// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#ifndef _FATTREESWITCH_H
#define _FATTREESWITCH_H

#include "switch.h"
#include "callback_pipe.h"
#include "inc_fib.h"
#include <cstdint>
#include <unordered_map>
#include <vector>

class FatTreeTopology;
class UecMcastSink;
class UecMcastPacket;
class UecReducePacket;
class VirtualQueue;
class LosslessInputQueue;
class Pipe;

/*
 * Copyright (C) 2013-2014 Universita` di Pisa. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *   1. Redistributions of source code must retain the above copyright
 *      notice, this list of conditions and the following disclaimer.
 *   2. Redistributions in binary form must reproduce the above copyright
 *      notice, this list of conditions and the following disclaimer in the
 *      documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 * OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
 * OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 */

/*
 * Shamelessly copied from FreeBSD
 */

/* ----- FreeBSD if_bridge hash function ------- */

/*
 * The following hash function is adapted from "Hash Functions" by Bob Jenkins
 * ("Algorithm Alley", Dr. Dobbs Journal, September 1997).
 *
 * http://www.burtleburtle.net/bob/hash/spooky.html
 */

#define MIX(a, b, c)                            \
    do {                                        \
        a -= b; a -= c; a ^= (c >> 13);         \
        b -= c; b -= a; b ^= (a << 8);          \
        c -= a; c -= b; c ^= (b >> 13);         \
        a -= b; a -= c; a ^= (c >> 12);         \
        b -= c; b -= a; b ^= (a << 16);         \
        c -= a; c -= b; c ^= (b >> 5);          \
        a -= b; a -= c; a ^= (c >> 3);          \
        b -= c; b -= a; b ^= (a << 10);         \
        c -= a; c -= b; c ^= (b >> 15);         \
    } while (/*CONSTCOND*/0)

static inline uint32_t freeBSDHash(uint32_t target1, uint32_t target2 = 0, uint32_t target3 = 0)
{
    uint32_t a = 0x9e3779b9, b = 0x9e3779b9, c = 0; // hask key
        
    b += target3;
    c += target2;
    a += target1;        
    MIX(a, b, c);
    return c;
}

#undef MIX

class FlowletInfo {
public:
    uint32_t _egress;
    simtime_picosec _last;

    FlowletInfo(uint32_t egress,simtime_picosec lasttime) {_egress = egress; _last = lasttime;};

};

class FatTreeSwitch : public Switch {
public:
    enum switch_type {
        NONE = 0, TOR = 1, AGG = 2, CORE = 3
    };

    enum routing_strategy {
        NIX = 0, ECMP = 1, ADAPTIVE_ROUTING = 2, ECMP_ADAPTIVE = 3, RR = 4, RR_ECMP = 5
    };

    enum sticky_choices {
        PER_PACKET = 0, PER_FLOWLET = 1
    };

    FatTreeSwitch(EventList& eventlist, string s, switch_type t, uint32_t id,simtime_picosec switch_delay, FatTreeTopology* ft);
    ~FatTreeSwitch();

    virtual void receivePacket(Packet& pkt);
    virtual Route* getNextHop(Packet& pkt, BaseQueue* ingress_port);
    virtual uint32_t getType() {return _type;}

    // Phase-2: addPort override that maintains the
    // _port_idx_by_queue map so identify_ingress_port_idx() can
    // resolve the upstream queue → port index in O(1).
    int addPort(BaseQueue* q) override;

    // Phase-2: build the per-port pre-baked egress route cache
    // {queue, pipe, remote_endpoint}. Called once after the
    // topology has finished wiring queues+pipes. The caller
    // (FatTreeTopology::set_up_mcast) builds a queue→pipe index
    // from the topology arrays and passes it in by const-ref;
    // the map is setup-time-only and not retained.
    void build_egress_route_cache(
            const std::unordered_map<BaseQueue*, Pipe*>& q2p);

    // Phase-2: identify the ingress port index for an incoming
    // multicast packet by walking back to the upstream queue
    // (route element nexthop-2) and matching against this
    // switch's _ports.
    uint8_t identify_ingress_port_idx(Packet& pkt) const;

    // Phase-2: register a (host, group, sink) leaf-TOR member
    // with this switch's INC FIB. Builds a 3-element route to the
    // sink via the host downlink and stashes it in the group's
    // INCFibEntry.leaf_routes. The bit for the host downlink port
    // must already be set in tree_port_mask (asserts on mismatch).
    void addMcastPort(int host_addr, uint32_t group_id,
                      UecMcastSink* sink);

    INCFib* inc_fib() const { return _inc_fib; }
    const std::vector<Route*>& port_egress_routes() const {
        return _port_egress_routes;
    }

    // O(1) reverse lookup of a port index from its queue. Returns
    // -1 if the queue is not one of this switch's ports.
    int port_idx_for(BaseQueue* q) const {
        auto it = _port_idx_by_queue.find(q);
        return it == _port_idx_by_queue.end()
                ? -1 : static_cast<int>(it->second);
    }

    // Phase-2 multicast dispatch: RPF fanout. Reads
    // pkt.group_id(), looks up the INCFibEntry, computes
    // egress_mask = tree_port_mask & ~(1 << ingress), spawns
    // one replica per set bit via UecMcastPacket::newpkt_replica
    // (using cached egress routes or leaf-route per port), and
    // sends each replica through _pipe to absorb switch
    // latency. Original packet is freed for symmetric handling.
    void handle_mcast(UecMcastPacket& pkt);

    // Phase-3 in-network aggregation (Reduce / Allreduce). Fan-in dual
    // of handle_mcast: collect one UEC_REDUCE contribution per downstream
    // tree port (the per-operation barrier), then on the last arrival
    // either forward one combined packet toward the root (root_port set)
    // or, at the apex (root_port == -1), turn around and multicast the
    // result back down the tree via fanout_replicas (Allreduce).
    void handle_reduce(UecReducePacket& pkt);

    // Shared fan-out: spawn one UecMcastPacket replica per set bit of
    // egress_mask, route via leaf-route or cached egress route, register
    // in _packets, and send through _pipe. Under lossless, a shared
    // McastFanoutCredit releases the ingress charge after the last
    // replica drains (ingress_iq == nullptr for switch-originated apex
    // fanout, where there is no ingress charge). Used by handle_mcast and
    // the Allreduce apex turn-around.
    void fanout_replicas(INCFibEntry* entry,
                         const std::bitset<128>& egress_mask,
                         UecMcastPacket& templ,
                         VirtualQueue* ingress_iq, bool lossless,
                         VirtualQueue* prev_override = nullptr);

    uint32_t adaptive_route(vector<FibEntry*>* ecmp_set, int8_t (*cmp)(FibEntry*,FibEntry*));
    uint32_t replace_worst_choice(vector<FibEntry*>* ecmp_set, int8_t (*cmp)(FibEntry*,FibEntry*),uint32_t my_choice);
    uint32_t adaptive_route_p2c(vector<FibEntry*>* ecmp_set, int8_t (*cmp)(FibEntry*,FibEntry*));

    static int8_t compare_flow_count(FibEntry* l, FibEntry* r);
    static int8_t compare_pause(FibEntry* l, FibEntry* r);
    static int8_t compare_bandwidth(FibEntry* l, FibEntry* r);
    static int8_t compare_queuesize(FibEntry* l, FibEntry* r);
    static int8_t compare_pqb(FibEntry* l, FibEntry* r);//compare pause,queue, bw.
    static int8_t compare_pq(FibEntry* l, FibEntry* r);//compare pause, queue
    static int8_t compare_pb(FibEntry* l, FibEntry* r);//compare pause, bandwidth
    static int8_t compare_qb(FibEntry* l, FibEntry* r);//compare pause, bandwidth

    static int8_t (*fn)(FibEntry*,FibEntry*);

    virtual void addHostPort(int addr, int flowid, PacketSink* transport);

    virtual void permute_paths(vector<FibEntry*>* uproutes);

    static void set_strategy(routing_strategy s) { assert (_strategy==NIX); _strategy = s; }
    static void set_ar_fraction(uint16_t f) { assert(f>=1);_ar_fraction = f;} 

    static routing_strategy _strategy;
    static uint16_t _ar_fraction;
    static uint16_t _ar_sticky;
    static simtime_picosec _sticky_delta;
    static double _ecn_threshold_fraction;
    static double _speculative_threshold_fraction;
private:
    switch_type _type;
    Pipe* _pipe;
    FatTreeTopology* _ft;
    
    //CAREFUL: can't always have a single FIB for all up destinations when there are failures!
    vector<FibEntry*>* _uproutes;

    unordered_map<uint32_t,FlowletInfo*> _flowlet_maps;

    static unordered_map<BaseQueue*,uint32_t> _port_flow_counts;

    uint32_t _crt_route;
    uint32_t _hash_salt;
    simtime_picosec _last_choice;

    unordered_map<Packet*,bool> _packets;

    // Phase-3 reduce fan-in barriers. Keyed by (flow_id<<32 | chunk seqno):
    // `arrived` counts contributions received for that chunk; when it reaches
    // the entry's expected_children() the switch emits/turns-around and the key
    // is erased. Under lossless, `charges` holds each arrived child's ingress
    // queue + bytes so the credit can release them once the result drains
    // (the children's charges are NOT released on arrival -- that is what
    // backpressures them; see handle_reduce).
    struct ReduceBarrier {
        int arrived = 0;
        std::vector<std::pair<LosslessInputQueue*, mem_b>> charges;
    };
    unordered_map<uint64_t, ReduceBarrier> _reduce_barriers;

    // Phase-2 INC state. _inc_fib holds per-group INCFibEntry
    // instances (multicast trees that traverse this switch).
    // _port_idx_by_queue caches the inverse of Switch::_ports for
    // fast ingress identification. _port_egress_routes is the
    // pre-baked {queue, pipe, remote_endpoint} per port, populated
    // by build_egress_route_cache(). The queue→pipe index needed
    // to build that cache is owned by FatTreeTopology::set_up_mcast
    // (setup-time-only) and passed in by const-ref; no member.
    INCFib* _inc_fib;
    std::unordered_map<BaseQueue*, uint8_t> _port_idx_by_queue;
    std::vector<Route*>                     _port_egress_routes;
};

#endif
    
