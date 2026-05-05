// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include "fat_tree_switch.h"
#include "routetable.h"
#include "fat_tree_topology.h"
#include "callback_pipe.h"
#include "queue_lossless.h"
#include "queue_lossless_output.h"
#include "uec_bcast.h"

#include <cassert>

unordered_map<BaseQueue*,uint32_t> FatTreeSwitch::_port_flow_counts;

FatTreeSwitch::FatTreeSwitch(EventList& eventlist, string s, switch_type t, uint32_t id,simtime_picosec delay, FatTreeTopology* ft): Switch(eventlist, s) {
    _id = id;
    _type = t;
    _pipe = new CallbackPipe(delay,eventlist, this);
    _uproutes = NULL;
    _ft = ft;
    _crt_route = 0;
    _hash_salt = random();
    _last_choice = eventlist.now();
    _fib = new RouteTable();
    _inc_fib = new INCFib();
}

FatTreeSwitch::~FatTreeSwitch() {
    delete _inc_fib;
    // _port_egress_routes own their Route* contents; release them.
    for (Route* r : _port_egress_routes) delete r;
}

// addPort override --- maintain the port-index reverse map so
// identify_ingress_port_idx() is O(1). Also guard the
// INCFibEntry std::bitset<128> width invariant: if the port
// count ever exceeded 128 we would silently truncate the
// multicast bitmap. Abort loudly instead.
int FatTreeSwitch::addPort(BaseQueue* q) {
    int idx = Switch::addPort(q);
    if (idx >= 128) {
        std::cerr
            << "FatTreeSwitch port count (" << (idx + 1)
            << ") exceeds INCFibEntry bitmap width (128). "
               "Widen std::bitset<128> in inc_fib.h or reduce "
               "switch radix.\n";
        std::abort();
    }
    _port_idx_by_queue[q] = static_cast<uint8_t>(idx);
    return idx;
}

void FatTreeSwitch::register_port_pipe(BaseQueue* q, Pipe* p) {
    _port_pipe_by_queue[q] = p;
}

void FatTreeSwitch::build_egress_route_cache() {
    _port_egress_routes.resize(_ports.size());
    for (size_t i = 0; i < _ports.size(); ++i) {
        BaseQueue* q = _ports[i];
        auto pit = _port_pipe_by_queue.find(q);
        if (pit == _port_pipe_by_queue.end()) {
            // No pipe registered for this port (e.g. host-side
            // queue whose pipe lives elsewhere). Leave the cached
            // route nullptr; multicast will never use this port
            // for fanout (only registered tree-member ports
            // appear in tree_port_mask).
            _port_egress_routes[i] = nullptr;
            continue;
        }
        if (q->getRemoteEndpoint() == nullptr) {
            _port_egress_routes[i] = nullptr;
            continue;
        }
        Route* r = new Route();
        r->push_back(q);
        r->push_back(pit->second);
        r->push_back(q->getRemoteEndpoint());
        // Invariant: every cached egress route is a 3-element
        // {queue, pipe, remote} chain. Asserting at install time
        // guards against the Fraschetti-PR-#1 failure mode where
        // multicast routes contained only the next-hop sink and
        // per-hop latency was understated.
        assert(r->size() == 3 &&
               "egress route must be {queue, pipe, remote_endpoint}");
        _port_egress_routes[i] = r;
    }
}

uint8_t FatTreeSwitch::identify_ingress_port_idx(Packet& pkt) const {
    // The packet's _route was attached by the previous hop and
    // has the canonical shape {upstream_queue, pipe, this_switch}
    // for host-to-switch and switch-to-switch deliveries alike.
    // After traversal _nexthop == 3, so route[0] is the upstream
    // queue regardless of whether the upstream is a host or a
    // switch.
    assert(pkt.route() && pkt.route()->size() >= 1 &&
           "ingress identification requires a non-empty route");
    BaseQueue* upstream_queue =
            dynamic_cast<BaseQueue*>(pkt.route()->at(0));
    if (!upstream_queue) {
        assert(0 && "route[0] is not a BaseQueue");
        return 0;
    }
    // Two cases:
    //   (a) upstream_queue->getSwitch() != null --- the upstream
    //       is another switch. Our ingress port is the queue on
    //       *this* side of the link, whose remote endpoint is
    //       that upstream switch.
    //   (b) upstream_queue->getSwitch() == null --- the upstream
    //       is a host (no Switch wrapper). Use pkt.from as the
    //       host id; ingress port = host downlink for from on us.
    Switch* upstream_sw = upstream_queue->getSwitch();
    if (upstream_sw != nullptr) {
        for (size_t i = 0; i < _ports.size(); ++i) {
            PacketSink* mine_remote = _ports[i]->getRemoteEndpoint();
            if (mine_remote == upstream_sw) {
                return static_cast<uint8_t>(i);
            }
        }
        assert(0 &&
               "identify_ingress_port_idx: no port to upstream switch");
        return 0;
    } else {
        // Host-originated packet (TOR ingress). pkt.from is the
        // source host id; the matching port is this TOR's host
        // downlink for that host.
        int host = pkt.from;
        BaseQueue* host_q = _ft->queues_nlp_ns
                                    [_ft->HOST_POD_SWITCH(host)]
                                    [host][0];
        auto it = _port_idx_by_queue.find(host_q);
        assert(it != _port_idx_by_queue.end() &&
               "host downlink for pkt.from missing from port map");
        return it->second;
    }
}

// Phase-2 multicast fanout dispatch. RPF: replicate the arriving
// packet to every tree-member port except the ingress.
//
//   1. Look up the INCFibEntry by pkt.group_id().
//   2. On the first call (ingress phase), identify the ingress
//      port idx, compute egress_mask = tree_port_mask &
//      ~(1 << ingress), and for each set bit spawn a replica:
//        - leaf-TOR member port: route ends at UecMcastSink
//          via leaf_route_for(port_idx).
//        - interior tree port: route is the cached
//          {queue, pipe, remote} for that port.
//      Each replica is registered in _packets before being
//      sent through _pipe so the pipe-callback path classifies
//      it as egress (not a fresh ingress).
//      The original packet is freed; replicas are independent
//      allocations. Symmetric handling avoids the asymmetric
//      _direction-state hazard called out in v4 §3.4.
//   3. On the second call (egress, _packets contains the
//      replica's pointer), erase the marker and sendOn().
void FatTreeSwitch::handle_mcast(UecMcastPacket& pkt) {
    if (_packets.find(&pkt) == _packets.end()) {
        // Ingress: lookup, RPF dispatch, fanout.
        INCFibEntry* entry = _inc_fib->lookup(pkt.group_id());
        if (!entry) {
            // No state for this group at this switch. Drop
            // (set_up_mcast did not install — likely a bug;
            // assert in debug, drop in release).
            assert(0 && "handle_mcast: no INCFibEntry for group");
            pkt.free();
            return;
        }

        uint8_t ingress_idx = identify_ingress_port_idx(pkt);
        std::bitset<128> egress_mask = entry->tree_port_mask;
        egress_mask.reset(ingress_idx);

        for (size_t i = 0; i < 128; ++i) {
            if (!egress_mask.test(i)) continue;
            uint8_t port_idx = static_cast<uint8_t>(i);

            // Pick the route: leaf-TOR member port → end at sink;
            // interior port → cached {queue, pipe, remote}.
            Route* leaf = entry->leaf_route_for(port_idx);
            const Route& route = leaf ? *leaf
                                      : *_port_egress_routes[i];

            UecMcastPacket* r = UecMcastPacket::newpkt_replica(
                    pkt, route, port_idx);
            // Reset direction so the next switch can re-establish
            // it via Packet::set_direction without triggering the
            // DOWN→UP assert. (Qualifying NONE because
            // FatTreeSwitch::NONE shadows the global
            // packet_direction::NONE in this scope.)
            r->set_direction(::NONE);
            // Mark the replica as already-ingressed so the
            // pipe-callback hits the egress branch (sendOn).
            _packets[r] = true;
            _pipe->receivePacket(*r);
        }
        // Symmetric: free the original; replicas are fresh.
        pkt.free();
    } else {
        // Egress callback from _pipe: send the replica on its
        // attached route.
        _packets.erase(&pkt);
        pkt.sendOn();
    }
}

void FatTreeSwitch::addMcastPort(int host_addr, uint32_t group_id,
                                 UecMcastSink* sink) {
    INCFibEntry* entry = _inc_fib->lookup(group_id);
    assert(entry &&
           "INCFib entry must be installed before addMcastPort");

    // Find this leaf-TOR's host downlink queue for host_addr,
    // then look up its port index.
    BaseQueue* host_q = _ft->queues_nlp_ns
                                [_ft->HOST_POD_SWITCH(host_addr)]
                                [host_addr][0];
    auto it = _port_idx_by_queue.find(host_q);
    assert(it != _port_idx_by_queue.end() &&
           "host downlink queue not found in this switch's port map");
    uint8_t port_idx = it->second;

    Route* r = new Route();
    r->push_back(host_q);
    r->push_back(_ft->pipes_nlp_ns
                         [_ft->HOST_POD_SWITCH(host_addr)]
                         [host_addr][0]);
    r->push_back(sink);
    entry->leaf_routes.emplace_back(port_idx, r);

    // The bit for this port must already be set: tree
    // construction ran first (build_mcast_tree) and set the
    // mask; addMcastPort only attaches the sink-route to it.
    assert(entry->tree_port_mask.test(port_idx) &&
           "addMcastPort: tree_port_mask bit not set for this port");
}

void FatTreeSwitch::receivePacket(Packet& pkt){
    if (pkt.type()==ETH_PAUSE){
        EthPausePacket* p = (EthPausePacket*)&pkt;
        //I must be in lossless mode!
        //find the egress queue that should process this, and pass it over for processing.
        for (size_t i = 0;i < _ports.size();i++){
            LosslessQueue* q = (LosslessQueue*)_ports.at(i);
            if (q->getRemoteEndpoint() && ((Switch*)q->getRemoteEndpoint())->getID() == p->senderID()){
                q->receivePacket(pkt);
                break;
            }
        }

        return;
    }

    // Phase-2 multicast dispatch: a UEC_MCAST packet bypasses the
    // unicast _fib pipeline and is routed via the per-switch
    // INCFib by group_id. Mirrors the ETH_PAUSE arm above.
    if (pkt.type() == UEC_MCAST) {
        handle_mcast(static_cast<UecMcastPacket&>(pkt));
        return;
    }

    if (_packets.find(&pkt)==_packets.end()){
        //ingress pipeline processing.

        _packets[&pkt] = true;

        const Route * nh = getNextHop(pkt,NULL);
        //set next hop which is peer switch.
        pkt.set_route(*nh);

        //emulate the switching latency between ingress and packet arriving at the egress queue.
        _pipe->receivePacket(pkt); 
    }
    else {
        _packets.erase(&pkt);
        
        //egress queue processing.
        //cout << "Switch type " << _type <<  " id " << _id << " pkt dst " << pkt.dst() << " dir " << pkt.get_direction() << endl;
        pkt.sendOn();
    }
};

void FatTreeSwitch::addHostPort(int addr, int flowid, PacketSink* transport){
    Route* rt = new Route();
    rt->push_back(_ft->queues_nlp_ns[_ft->HOST_POD_SWITCH(addr)][addr][0]);
    rt->push_back(_ft->pipes_nlp_ns[_ft->HOST_POD_SWITCH(addr)][addr][0]);
    rt->push_back(transport);
    _fib->addHostRoute(addr,rt,flowid);
}

uint32_t mhash(uint32_t x) {
    x = ((x >> 16) ^ x) * 0x45d9f3b;
    x = ((x >> 16) ^ x) * 0x45d9f3b;
    x = (x >> 16) ^ x;
    return x;
}

uint32_t FatTreeSwitch::adaptive_route_p2c(vector<FibEntry*>* ecmp_set, int8_t (*cmp)(FibEntry*,FibEntry*)){
    uint32_t choice = 0, min = UINT32_MAX;
    uint32_t start, i = 0;
    static const uint16_t nr_choices = 2;
    
    do {
        start = random()%ecmp_set->size();

        Route * r= (*ecmp_set)[start]->getEgressPort();
        assert(r && r->size()>1);
        BaseQueue* q = (BaseQueue*)(r->at(0));
        assert(q);
        if (q->queuesize()<min){
            choice = start;
            min = q->queuesize();
        }
        i++;
    } while (i<nr_choices);
    return choice;
}

uint32_t FatTreeSwitch::adaptive_route(vector<FibEntry*>* ecmp_set, int8_t (*cmp)(FibEntry*,FibEntry*)){
    //cout << "adaptive_route" << endl;
    uint32_t choice = 0;

    uint32_t best_choices[256];
    uint32_t best_choices_count = 0;
  
    FibEntry* min = (*ecmp_set)[choice];
    best_choices[best_choices_count++] = choice;

    for (uint32_t i = 1; i< ecmp_set->size(); i++){
        int8_t c = cmp(min,(*ecmp_set)[i]);

        if (c < 0){
            choice = i;
            min = (*ecmp_set)[choice];
            best_choices_count = 0;
            best_choices[best_choices_count++] = choice;
        }
        else if (c==0){
            assert(best_choices_count<255);
            best_choices[best_choices_count++] = i;
        }        
    }

    assert (best_choices_count>=1);
    uint32_t choiceindex = random()%best_choices_count;
    choice = best_choices[choiceindex];
    //cout << "ECMP set choices " << ecmp_set->size() << " Choice count " << best_choices_count << " chosen entry " << choiceindex << " chosen path " << choice << " ";

    if (cmp==compare_flow_count){
        //for (uint32_t i = 0; i<best_choices_count;i++)
          //  cout << "pathcnt " << best_choices[i] << "="<< _port_flow_counts[(BaseQueue*)( (*ecmp_set)[best_choices[i]]->getEgressPort()->at(0))]<< " ";
        
        _port_flow_counts[(BaseQueue*)((*ecmp_set)[choice]->getEgressPort()->at(0))]++;
    }

    return choice;
}

uint32_t FatTreeSwitch::replace_worst_choice(vector<FibEntry*>* ecmp_set, int8_t (*cmp)(FibEntry*,FibEntry*),uint32_t my_choice){
    uint32_t best_choice = 0;
    uint32_t worst_choice = 0;

    uint32_t best_choices[256];
    uint32_t best_choices_count = 0;

    FibEntry* min = (*ecmp_set)[best_choice];
    FibEntry* max = (*ecmp_set)[worst_choice];
    best_choices[best_choices_count++] = best_choice;

    for (uint32_t i = 1; i< ecmp_set->size(); i++){
        int8_t c = cmp(min,(*ecmp_set)[i]);

        if (c < 0){
            best_choice = i;
            min = (*ecmp_set)[best_choice];
            best_choices_count = 0;
            best_choices[best_choices_count++] = best_choice;
        }
        else if (c==0){
            assert(best_choices_count<256);
            best_choices[best_choices_count++] = i;
        }        

        if (cmp(max,(*ecmp_set)[i])>0){
            worst_choice = i;
            max = (*ecmp_set)[worst_choice];
        }
    }

    //might need to play with different alternatives here, compare to worst rather than just to worst index.
    int8_t r = cmp((*ecmp_set)[my_choice],(*ecmp_set)[worst_choice]);
    assert(r>=0);

    if (r==0){
        assert (best_choices_count>=1);
        return best_choices[random()%best_choices_count];
    }
    else return my_choice;
}


int8_t FatTreeSwitch::compare_pause(FibEntry* left, FibEntry* right){
    Route * r1= left->getEgressPort();
    assert(r1 && r1->size()>1);
    LosslessOutputQueue* q1 = dynamic_cast<LosslessOutputQueue*>(r1->at(0));
    Route * r2= right->getEgressPort();
    assert(r2 && r2->size()>1);
    LosslessOutputQueue* q2 = dynamic_cast<LosslessOutputQueue*>(r2->at(0));

    if (!q1->is_paused()&&q2->is_paused())
        return 1;
    else if (q1->is_paused()&&!q2->is_paused())
        return -1;
    else 
        return 0;
}

int8_t FatTreeSwitch::compare_flow_count(FibEntry* left, FibEntry* right){
    Route * r1= left->getEgressPort();
    assert(r1 && r1->size()>1);
    BaseQueue* q1 = (BaseQueue*)(r1->at(0));
    Route * r2= right->getEgressPort();
    assert(r2 && r2->size()>1);
    BaseQueue* q2 = (BaseQueue*)(r2->at(0));

    if (_port_flow_counts.find(q1)==_port_flow_counts.end())
        _port_flow_counts[q1] = 0;

    if (_port_flow_counts.find(q2)==_port_flow_counts.end())
        _port_flow_counts[q2] = 0;

    //cout << "CMP q1 " << q1 << "=" << _port_flow_counts[q1] << " q2 " << q2 << "=" << _port_flow_counts[q2] << endl; 

    if (_port_flow_counts[q1] < _port_flow_counts[q2])
        return 1;
    else if (_port_flow_counts[q1] > _port_flow_counts[q2] )
        return -1;
    else 
        return 0;
}

int8_t FatTreeSwitch::compare_queuesize(FibEntry* left, FibEntry* right){
    Route * r1= left->getEgressPort();
    assert(r1 && r1->size()>1);
    BaseQueue* q1 = dynamic_cast<BaseQueue*>(r1->at(0));
    Route * r2= right->getEgressPort();
    assert(r2 && r2->size()>1);
    BaseQueue* q2 = dynamic_cast<BaseQueue*>(r2->at(0));

    if (q1->quantized_queuesize() < q2->quantized_queuesize())
        return 1;
    else if (q1->quantized_queuesize() > q2->quantized_queuesize())
        return -1;
    else 
        return 0;
}

int8_t FatTreeSwitch::compare_bandwidth(FibEntry* left, FibEntry* right){
    Route * r1= left->getEgressPort();
    assert(r1 && r1->size()>1);
    BaseQueue* q1 = dynamic_cast<BaseQueue*>(r1->at(0));
    Route * r2= right->getEgressPort();
    assert(r2 && r2->size()>1);
    BaseQueue* q2 = dynamic_cast<BaseQueue*>(r2->at(0));

    if (q1->quantized_utilization() < q2->quantized_utilization())
        return 1;
    else if (q1->quantized_utilization() > q2->quantized_utilization())
        return -1;
    else 
        return 0;

    /*if (q1->average_utilization() < q2->average_utilization())
        return 1;
    else if (q1->average_utilization() > q2->average_utilization())
        return -1;
    else 
        return 0;        */
}

int8_t FatTreeSwitch::compare_pqb(FibEntry* left, FibEntry* right){
    //compare pause, queuesize, bandwidth.
    int8_t p = compare_pause(left, right);

    if (p!=0)
        return p;
    
    p = compare_queuesize(left,right);

    if (p!=0)
        return p;

    return compare_bandwidth(left,right);
}

int8_t FatTreeSwitch::compare_pq(FibEntry* left, FibEntry* right){
    //compare pause, queuesize, bandwidth.
    int8_t p = compare_pause(left, right);

    if (p!=0)
        return p;
    
    return compare_queuesize(left,right);
}

int8_t FatTreeSwitch::compare_qb(FibEntry* left, FibEntry* right){
    //compare pause, queuesize, bandwidth.
    int8_t p = compare_queuesize(left, right);

    if (p!=0)
        return p;
    
    return compare_bandwidth(left,right);
}

int8_t FatTreeSwitch::compare_pb(FibEntry* left, FibEntry* right){
    //compare pause, queuesize, bandwidth.
    int8_t p = compare_pause(left, right);

    if (p!=0)
        return p;
    
    return compare_bandwidth(left,right);
}

void FatTreeSwitch::permute_paths(vector<FibEntry *>* uproutes) {
    int len = uproutes->size();
    for (int i = 0; i < len; i++) {
        int ix = random() % (len - i);
        FibEntry* tmppath = (*uproutes)[ix];
        (*uproutes)[ix] = (*uproutes)[len-1-i];
        (*uproutes)[len-1-i] = tmppath;
    }
}

FatTreeSwitch::routing_strategy FatTreeSwitch::_strategy = FatTreeSwitch::NIX;
uint16_t FatTreeSwitch::_ar_fraction = 0;
uint16_t FatTreeSwitch::_ar_sticky = FatTreeSwitch::PER_PACKET;
simtime_picosec FatTreeSwitch::_sticky_delta = timeFromUs((uint32_t)10);
double FatTreeSwitch::_ecn_threshold_fraction = 1.0;
double FatTreeSwitch::_speculative_threshold_fraction = 0.2;
int8_t (*FatTreeSwitch::fn)(FibEntry*,FibEntry*)= &FatTreeSwitch::compare_queuesize;

Route* FatTreeSwitch::getNextHop(Packet& pkt, BaseQueue* ingress_port){
    vector<FibEntry*> * available_hops = _fib->getRoutes(pkt.dst());

    if (available_hops){
        //implement a form of ECMP hashing; might need to revisit based on measured performance.
        uint32_t ecmp_choice = 0;
        if (available_hops->size()>1)
            switch(_strategy){
            case NIX:
                abort();
            case ECMP:
                ecmp_choice = freeBSDHash(pkt.flow_id(),pkt.pathid(),_hash_salt) % available_hops->size();
                break;
            case ADAPTIVE_ROUTING:
                if (_ar_sticky==FatTreeSwitch::PER_PACKET){
                    ecmp_choice = adaptive_route(available_hops,fn); 
                } 
                else if (_ar_sticky==FatTreeSwitch::PER_FLOWLET){     
                    if (_flowlet_maps.find(pkt.flow_id())!=_flowlet_maps.end()){
                        FlowletInfo* f = _flowlet_maps[pkt.flow_id()];
                        
                        // only reroute an existing flow if its inter packet time is larger than _sticky_delta and
                        // and
                        // 50% chance happens. 
                        // and (commented out) if the switch has not taken any other placement decision that we've not seen the effects of.
                        if (eventlist().now() - f->_last > _sticky_delta && /*eventlist().now() - _last_choice > _pipe->delay() + BaseQueue::_update_period  &&*/ random()%2==0){ 
                            //cout << "AR 1 " << timeAsUs(eventlist().now()) << endl;
                            uint32_t new_route = adaptive_route(available_hops,fn); 
                            if (fn(available_hops->at(f->_egress),available_hops->at(new_route)) < 0){
                                f->_egress = new_route;
                                _last_choice = eventlist().now();
                                //cout << "Switch " << _type << ":" << _id << " choosing new path "<<  f->_egress << " for " << pkt.flow_id() << " at " << timeAsUs(eventlist().now()) << " last is " << timeAsUs(f->_last) << endl;
                            }
                        }
                        ecmp_choice = f->_egress;

                        f->_last = eventlist().now();
                    }
                    else {
                        //cout << "AR 2 " << timeAsUs(eventlist().now()) << endl;
                        ecmp_choice = adaptive_route(available_hops,fn); 
                        _last_choice = eventlist().now();

                        _flowlet_maps[pkt.flow_id()] = new FlowletInfo(ecmp_choice,eventlist().now());
                    }
                }

                break;
            case ECMP_ADAPTIVE:
                ecmp_choice = freeBSDHash(pkt.flow_id(),pkt.pathid(),_hash_salt) % available_hops->size();
                if (random()%100 < 50)
                    ecmp_choice = replace_worst_choice(available_hops,fn, ecmp_choice);
                break;
            case RR:
                if (_crt_route>=5 * available_hops->size()){
                    _crt_route = 0;
                    permute_paths(available_hops);
                }
                ecmp_choice = _crt_route % available_hops->size();
                _crt_route ++;
                break;
            case RR_ECMP:
                if (_type == TOR){
                    if (_crt_route>=5 * available_hops->size()){
                        _crt_route = 0;
                        permute_paths(available_hops);
                    }
                    ecmp_choice = _crt_route % available_hops->size();
                    _crt_route ++;
                }
                else ecmp_choice = freeBSDHash(pkt.flow_id(),pkt.pathid(),_hash_salt) % available_hops->size();
                
                break;
            }
        
        FibEntry* e = (*available_hops)[ecmp_choice];
        pkt.set_direction(e->getDirection());
        
        return e->getEgressPort();
    }

    //no route table entries for this destination. Add them to FIB or fail. 
    if (_type == TOR){
        if ( _ft->HOST_POD_SWITCH(pkt.dst()) == _id) { 
            //this host is directly connected!
            HostFibEntry* fe = _fib->getHostRoute(pkt.dst(),pkt.flow_id());
            assert(fe);
            pkt.set_direction(DOWN);
            return fe->getEgressPort();
        } else {
            //route packet up!
            if (_uproutes)
                _fib->setRoutes(pkt.dst(),_uproutes);
            else {
                uint32_t podid,agg_min,agg_max;

                if (_ft->get_tiers()==3) {
                    podid = _id / _ft->tor_switches_per_pod();
                    agg_min = _ft->MIN_POD_AGG_SWITCH(podid);
                    agg_max = _ft->MAX_POD_AGG_SWITCH(podid);
                }
                else {
                    agg_min = 0;
                    agg_max = _ft->getNAGG()-1;
                }

                for (uint32_t k=agg_min; k<=agg_max;k++){
                    for (uint32_t b = 0; b < _ft->bundlesize(AGG_TIER); b++) {
                        Route * r = new Route();
                        r->push_back(_ft->queues_nlp_nup[_id][k][b]);
                        assert(((BaseQueue*)r->at(0))->getSwitch() == this);

                        r->push_back(_ft->pipes_nlp_nup[_id][k][b]);
                        r->push_back(_ft->queues_nlp_nup[_id][k][b]->getRemoteEndpoint());
                        _fib->addRoute(pkt.dst(),r,1,UP);
                    }

                    /*
                      FatTreeSwitch* next = (FatTreeSwitch*)_ft->queues_nlp_nup[_id][k]->getRemoteEndpoint();
                      assert (next->getType()==AGG && next->getID() == k);
                    */
                }
                _uproutes = _fib->getRoutes(pkt.dst());
                permute_paths(_uproutes);
            }
        }
    } else if (_type == AGG) {
        if ( _ft->get_tiers()==2 || _ft->HOST_POD(pkt.dst()) == _ft->AGG_SWITCH_POD_ID(_id)) {
            //must go down!
            //target NLP id is 2 * pkt.dst()/K
            uint32_t target_tor = _ft->HOST_POD_SWITCH(pkt.dst());
            for (uint32_t b = 0; b < _ft->bundlesize(AGG_TIER); b++) {
                Route * r = new Route();
                r->push_back(_ft->queues_nup_nlp[_id][target_tor][b]);
                assert(((BaseQueue*)r->at(0))->getSwitch() == this);

                r->push_back(_ft->pipes_nup_nlp[_id][target_tor][b]);          
                r->push_back(_ft->queues_nup_nlp[_id][target_tor][b]->getRemoteEndpoint());

                _fib->addRoute(pkt.dst(),r,1, DOWN);
            }
        } else {
            //go up!
            if (_uproutes)
                _fib->setRoutes(pkt.dst(),_uproutes);
            else {
                uint32_t podpos = _id % _ft->agg_switches_per_pod();
                uint32_t uplink_bundles = _ft->radix_up(AGG_TIER) / _ft->bundlesize(CORE_TIER);
                for (uint32_t l = 0; l <  uplink_bundles ; l++) {
                    uint32_t core = l * _ft->agg_switches_per_pod() + podpos;
                    for (uint32_t b = 0; b < _ft->bundlesize(CORE_TIER); b++) {
                        Route *r = new Route();
                        r->push_back(_ft->queues_nup_nc[_id][core][b]);
                        assert(((BaseQueue*)r->at(0))->getSwitch() == this);

                        r->push_back(_ft->pipes_nup_nc[_id][core][b]);
                        r->push_back(_ft->queues_nup_nc[_id][core][b]->getRemoteEndpoint());

                        /*
                          FatTreeSwitch* next = (FatTreeSwitch*)_ft->queues_nup_nc[_id][k]->getRemoteEndpoint();
                          assert (next->getType()==CORE && next->getID() == k);
                        */
                    
                        _fib->addRoute(pkt.dst(),r,1,UP);

                        //cout << "AGG switch " << _id << " adding route to " << pkt.dst() << " via CORE " << k << " bundle_id " << b << endl;
                    }
                }
                //_uproutes = _fib->getRoutes(pkt.dst());
                permute_paths(_fib->getRoutes(pkt.dst()));
            }
        }
    } else if (_type == CORE) {
        uint32_t nup = _ft->MIN_POD_AGG_SWITCH(_ft->HOST_POD(pkt.dst())) + (_id % _ft->agg_switches_per_pod());
        for (uint32_t b = 0; b < _ft->bundlesize(CORE_TIER); b++) {
            Route *r = new Route();
            //cout << "CORE switch " << _id << " adding route to " << pkt.dst() << " via AGG " << nup << endl;

            assert (_ft->queues_nc_nup[_id][nup][b]);
            r->push_back(_ft->queues_nc_nup[_id][nup][b]);
            assert(((BaseQueue*)r->at(0))->getSwitch() == this);

            assert (_ft->pipes_nc_nup[_id][nup][b]);
            r->push_back(_ft->pipes_nc_nup[_id][nup][b]);

            r->push_back(_ft->queues_nc_nup[_id][nup][b]->getRemoteEndpoint());
            _fib->addRoute(pkt.dst(),r,1,DOWN);
        }
    }
    else {
        cerr << "Route lookup on switch with no proper type: " << _type << endl;
        abort();
    }
    assert(_fib->getRoutes(pkt.dst()));

    //FIB has been filled in; return choice. 
    return getNextHop(pkt, ingress_port);
};
