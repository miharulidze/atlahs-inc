# Phase-Two Implementation Plan

*Production-ready execution plan for switch-level multicast in the
ATLAHS / htsim UEC backend. Design rationale and alternatives are in
`v4.md` — this document is the executable distillation.*

**Branch**: `WIP-multicast-htsim-direct`
**Estimate**: ~40–50 hours (within proposal weeks 4–8 budget)
**Headline outcome**: `-bcast_mode mcast` produces a constant-in-\|G\|
broadcast completion-time curve; the existing baseline path is
bit-identical to pre-phase-two output.

---

## 0. Pre-Flight Checks

Before starting implementation, verify:

1. `git status` is clean (no uncommitted edits to phase-1 code).
2. Current branch is `WIP-multicast-htsim-direct`.
3. Phase-1 baseline tests pass: run
   `sim/htsim-backend/sim/datacenter/connection_matrices/tests/test_bcast.py`.
4. `htsim_uec` builds clean: `make -C sim/htsim-backend/sim/datacenter htsim_uec`.
5. v4 plan is approved (user has confirmed the design choices —
   §3.1 (β), §3.2 (i') new packet, §3.3 (q) bitmap FIB, §3.4 (v)
   distinct copies, §3.5 (z) new sink, §3.6 (m')/RPF, §3.7 collective
   base classes).

If any check fails, **stop and surface it** before starting Task 1.

---

## 1. Task Order and Dependencies

```
T1: UEC_MCAST enum value     ───┐
T2: UecMcastPacket class     ───┤
T3: INCFib / INCFibEntry     ───┤
T4: UecCollectiveSrc/Sink    ───┼─→ T6: FatTreeSwitch ingress
T5: UecBcastSrcMcast +       ───┘     + INC fib member + addMcastPort
    UecMcastSink                    ↓
                                   T7: handle_mcast dispatch
                                    ↓
                              T8: build_mcast_tree
                                    ↓
                              T9: set_up_mcast body
                                    ↓
                              T10: Driver integration
                                    ↓
                              T11: Benchmark + plotting
                                    ↓
                              T12: Regression + sweep
```

Tasks T1–T5 are independent and can be implemented in any order
or in parallel. T6 depends on the FIB class (T3). T7 depends on
T2, T3, T5, T6. T8 onward is sequential.

Each task includes a definition-of-done. **Do not start the next
task until the current task's tests pass.**

---

## 2. Task Details

### T1 — Add `UEC_MCAST` to `packet_type` enum

**Files**: `sim/htsim-backend/sim/network.h`

**Change**: locate the `packet_type` enum (search for `UEC` / `UECACK`).
Add `UEC_MCAST` as a new value. Order it after the existing UEC
variants for readability.

If a parallel `packet_type_str` table exists (search the file for
type-to-string), add a `"UEC_MCAST"` entry to keep tracing output
readable.

**Definition of done**: `make -C sim/htsim-backend/sim/datacenter
htsim_uec` builds clean. No behavioural change.

---

### T2 — Implement `UecMcastPacket`

**Files**: `sim/htsim-backend/sim/uecpacket.h`, `uecpacket.cpp`

**New class** (in `uecpacket.h`, alongside `UecPacket` / `UecAck` /
`UecNack`):

```c
class UecMcastPacket : public Packet {
public:
    typedef uint64_t seq_t;

    seq_t    _seqno;
    uint32_t _group_id;
    uint32_t _op_seq_id;       // phase-2: always 0; phase-3 uses

    static constexpr int      acksize          = 64;
    static constexpr uint32_t PATHID_SEED_MIX  = 2654435761u;
    static constexpr uint32_t PATHID_HOP_MIX   = 31u;

    UecMcastPacket() : Packet() {}
    virtual ~UecMcastPacket() {}

    // Source-side factory: seeded path-hash from group + source.
    // Wire size is data + header overhead (acksize), matching
    // UecPacket convention. _id is the last data byte.
    inline static UecMcastPacket* newpkt(
            PacketFlow& flow, const Route& route,
            seq_t seqno, int size,
            uint32_t group_id, uint32_t source_host_id,
            uint32_t op_seq_id = 0) {
        UecMcastPacket* p = _packetdb.allocPacket();
        p->set_route(flow, route, size + acksize, seqno + size - 1);
        p->_type      = UEC_MCAST;
        p->_is_header = false;
        p->_bounced   = false;
        p->_seqno     = seqno;
        p->_group_id  = group_id;
        p->_op_seq_id = op_seq_id;
        p->_pathid    = (group_id ^ source_host_id) * PATHID_SEED_MIX;
        p->_direction = NONE;
        return p;
    }

    // Switch-side factory: extends path-hash with chosen egress.
    inline static UecMcastPacket* newpkt_replica(
            UecMcastPacket& source,
            const Route& branch_route,
            uint8_t egress_port_idx) {
        UecMcastPacket* p = _packetdb.allocPacket();
        p->set_route(source.flow(), branch_route,
                     source.size(), source.id());
        p->_type      = UEC_MCAST;
        p->_is_header = false;
        p->_bounced   = false;
        p->_seqno     = source._seqno;
        p->_group_id  = source._group_id;
        p->_op_seq_id = source._op_seq_id;
        p->_pathid    = source._pathid * PATHID_HOP_MIX
                        + static_cast<uint32_t>(egress_port_idx) + 1u;
        p->_direction = NONE;
        p->from = source.from;
        p->to   = source.to;
        p->tag  = source.tag;
        return p;
    }

    void free() { _packetdb.freePacket(this); }

    inline uint32_t group_id()   const { return _group_id; }
    inline seq_t    seqno()      const { return _seqno; }
    inline uint32_t op_seq_id()  const { return _op_seq_id; }

    virtual int data_packet_size() const { return _size; }
    virtual PktPriority priority() const { return Packet::PRIO_LO; }

private:
    static PacketDB<UecMcastPacket> _packetdb;
};
```

In `uecpacket.cpp`: add the static `PacketDB` definition:
```c
PacketDB<UecMcastPacket> UecMcastPacket::_packetdb;
```

**Tests** (`sim/tests/uec_mcast_packet_test.cpp` — new C++ test file):

1. `alloc_free_roundtrip`: allocate via `newpkt`, set fields, call
   `free`, allocate again — should reuse the freelist slot
   (refcount returns to 0 then 1).
2. `pathid_source_seed`: alloc with `(group_id=5, source_host=42)`;
   assert `_pathid == (5 ^ 42) * 2654435761u`.
3. `pathid_replica_chain`: source pathid = `S`; spawn replica with
   `egress_port_idx=7`; assert replica pathid = `S * 31 + 7 + 1`.
4. `pathid_two_hop_match`: hand-build two replicas via
   `newpkt_replica` chain (port 5 then port 2); assert leaf pathid
   matches manually computed `((seed * 31 + 5 + 1) * 31 + 2 + 1)`.
5. `pathid_cross_op_stability`: emit two source packets with same
   `(group_id, source_host_id)`; trace each through identical
   two-hop fanout (same egress ports); assert leaf replicas have
   identical `_pathid` — **the §3.2.1 headline property**.
6. `wire_size_includes_acksize`: alloc a packet with `size=4096`
   via `newpkt(...)`; assert `pkt.size() == 4096 + acksize`
   (= 4160). This guards the UecPacket-convention parity: queue
   serialisation latency is computed from `_size` and must
   include the 64-byte header overhead. Spawn a replica via
   `newpkt_replica`; assert it inherits the same wire size.

If `sim/tests/` has no existing test infrastructure, write the
tests as a single executable that returns 0 on success; reference
it from a small `tests/Makefile` target.

**Definition of done**: build clean; all 6 tests pass.

---

### T3 — Implement `INCFib` and `INCFibEntry`

**Files**: `sim/htsim-backend/sim/inc_fib.h`, `inc_fib.cpp` (new
files; do not pollute `routetable.{h,cpp}`).

**Headers required**: `<bitset>`, `<vector>`, `<utility>`,
`<unordered_map>`, `<optional>`, `"routetable.h"` (for `FibEntry`),
`"queue.h"` (for `BaseQueue`).

```c
class INCFibEntry : public FibEntry {
public:
    INCFibEntry() : FibEntry(/*out=*/nullptr, /*cost=*/0,
                             /*direction=*/NONE) {}

    std::bitset<128> tree_port_mask;

    // Phase-2: leaf-TOR pre-baked routes ending at UecMcastSink.
    // Sparse — keyed by port_idx; one entry per local member host.
    std::vector<std::pair<uint8_t, Route*>> leaf_routes;

    // Phase-3: port leading toward the reduction root for this
    // (group, reduce-root) pair. nullopt in phase-2 install.
    std::optional<uint8_t> root_port_idx;

    // Defensive: ECMP code paths must never reach an INCFibEntry.
    Route* getEgressPort() {
        assert(0 && "INCFibEntry has fanout/aggregation semantics");
        return nullptr;
    }

    // O(N) lookup; N ≤ host fanout per leaf TOR (~K/2). Acceptable.
    Route* leaf_route_for(uint8_t port_idx) const {
        for (auto& [idx, r] : leaf_routes)
            if (idx == port_idx) return r;
        return nullptr;
    }
};

class INCFib {
public:
    INCFibEntry* lookup(uint32_t group_id) const {
        auto it = _entries.find(group_id);
        return it == _entries.end() ? nullptr : it->second;
    }

    void install(uint32_t group_id, INCFibEntry* entry) {
        _entries[group_id] = entry;
    }

private:
    std::unordered_map<uint32_t, INCFibEntry*> _entries;
};
```

**Note on `FibEntry` base class**: `FibEntry` is non-virtual today
(`routetable.h:15`). For `dynamic_cast<INCFibEntry*>` to work in
T7's dispatch, `FibEntry` needs at least one virtual member.
Easiest: declare `virtual ~FibEntry() = default;` in `routetable.h`
before T3. This is a one-line change with no behavioural impact;
verify the build still succeeds after.

**Tests** (`sim/tests/inc_fib_test.cpp`):

1. `install_lookup`: install an entry under group_id=42; lookup
   returns the same pointer; lookup of group_id=43 returns nullptr.
2. `bitmask_set_query`: set bits 5, 17, 99 in `tree_port_mask`;
   assert `count() == 3` and `test(5/17/99)` true.
3. `leaf_route_lookup`: install two leaf routes at indices 3 and 8;
   `leaf_route_for(3)` and `leaf_route_for(8)` return the right
   pointers; `leaf_route_for(5)` returns nullptr.

**Definition of done**: build clean; all 3 tests pass.

---

### T4 — `UecCollectiveSrc` and `UecCollectiveSink` base classes

**Files**: `sim/htsim-backend/sim/uec_collective.h`,
`uec_collective.cpp` (new).

```c
// Operation-agnostic collective source. Phase-2: UecBcastSrcMcast.
// Phase-3: UecReduceSrc, etc.
class UecCollectiveSrc : public UecSrc {
public:
    UecCollectiveSrc(UecLogger* l, TrafficLogger* pl,
                     EventList& el, uint64_t rtt, uint64_t bdp,
                     uint64_t qd, int hops)
        : UecSrc(l, pl, el, rtt, bdp, qd, hops) {}

    void doNextEvent() override { emit_once(); }
    void activate()    override { emit_once(); }
    void receivePacket(Packet& pkt) override { pkt.free(); }

    void set_group_id(uint32_t g) { _group_id = g; }
    uint32_t group_id() const { return _group_id; }

protected:
    virtual void emit_once() = 0;

    void mark_sent() {
        if (_sent_once) return;
        _sent_once       = true;
        _flow_start_time = eventlist().now();
    }

    uint32_t _group_id  = 0;
    bool     _sent_once = false;
};

// Operation-agnostic collective sink. Phase-2: UecMcastSink.
// Phase-3: UecReduceSink, etc.
class UecCollectiveSink : public UecSink {
public:
    UecCollectiveSink(int host_addr, uint32_t group_id)
        : UecSink(), _host_addr(host_addr), _group_id(group_id) {}

    struct OpState {
        uint64_t bytes_received = 0;
        uint64_t expected_bytes = 0;
        Trigger* end_trigger    = nullptr;
        bool     completed      = false;
    };

    void register_op(uint32_t op_flow_id,
                     uint64_t expected_bytes,
                     Trigger* end_trigger) {
        _per_op[op_flow_id] = {0, expected_bytes, end_trigger,
                               false};
    }

    void receivePacket(Packet& pkt) override {
        if (!accepts_packet_type(pkt))     { pkt.free(); return; }
        if (pkt.header_only())             { pkt.free(); return; }

        auto it = _per_op.find(pkt.flow_id());
        if (it == _per_op.end())           { pkt.free(); return; }
        OpState& s = it->second;

        process_body(pkt, s);   // subclass: updates s, calls free

        if (!s.completed && s.bytes_received >= s.expected_bytes) {
            s.completed = true;
            if (s.end_trigger) s.end_trigger->activate();
        }
    }

    int host_addr() const { return _host_addr; }
    uint32_t group_id() const { return _group_id; }

protected:
    virtual bool accepts_packet_type(const Packet& pkt) const = 0;
    virtual void process_body(Packet& pkt, OpState& s) = 0;

    int      _host_addr;
    uint32_t _group_id;
    std::unordered_map<uint32_t, OpState> _per_op;
};
```

**Tests** (`sim/tests/uec_collective_test.cpp`): use a stub
subclass (`accepts_packet_type` returns true; `process_body` adds
size and frees) and verify:

1. `register_op_then_complete`: register one op with
   expected_bytes=N; deliver one fake-packet of size=N; assert
   trigger fires exactly once.
2. `multi_op_isolation`: register two ops on the same sink;
   complete one; assert the other doesn't fire.
3. `unknown_op_drop`: deliver a packet with unregistered flow_id;
   assert no trigger fires and the packet is freed.

**Definition of done**: build clean; all 3 tests pass.

---

### T5 — `UecBcastSrcMcast` and `UecMcastSink` subclasses

**Files**: `sim/htsim-backend/sim/uec_bcast.h`, `uec_bcast.cpp`
(extend existing).

```c
// In uec_bcast.h (alongside UecBcastSrc/UecBcastSink):
class UecBcastSrcMcast : public UecCollectiveSrc {
public:
    UecBcastSrcMcast(UecLogger* l, TrafficLogger* pl, EventList& el,
                     uint64_t rtt, uint64_t bdp, uint64_t qd, int hops)
        : UecCollectiveSrc(l, pl, el, rtt, bdp, qd, hops) {}

protected:
    void emit_once() override;
};

class UecMcastSink : public UecCollectiveSink {
public:
    UecMcastSink(int host, uint32_t group)
        : UecCollectiveSink(host, group) {
        _nodename = "uec_mcast_sink_h" + std::to_string(host)
                    + "_g" + std::to_string(group);
    }

protected:
    bool accepts_packet_type(const Packet& pkt) const override {
        return pkt.type() == UEC_MCAST;
    }
    void process_body(Packet& pkt, OpState& s) override {
        UecMcastPacket& mp = static_cast<UecMcastPacket&>(pkt);
        s.bytes_received += mp.data_packet_size();
        mp.free();
    }
};
```

`UecBcastSrcMcast::emit_once` (in `uec_bcast.cpp`):
```c
void UecBcastSrcMcast::emit_once() {
    if (_sent_once) return;
    mark_sent();
    UecMcastPacket* p = UecMcastPacket::newpkt(
        _flow, *_route,
        _highest_sent + 1, _mss,
        _group_id,
        /*source_host_id=*/static_cast<uint32_t>(this->from),
        /*op_seq_id=*/0);
    p->from = this->from;
    p->to   = this->to;
    p->tag  = this->tag;
    p->timestamp_sent = eventlist().now();
    _highest_sent  += _mss;
    _packets_sent  += _mss;
    p->sendOn();
}
```

**Tests** (`sim/tests/uec_mcast_sink_test.cpp`):

1. `single_op_completion`: register one op (expected=1500); deliver
   one `UecMcastPacket` of size 1500; assert trigger fires.
2. `wrong_packet_type_dropped`: deliver a `UecPacket` (wrong type)
   to the sink; assert no completion and packet is freed.
3. `multi_op_concurrent`: register ops 7 and 9 on same sink;
   deliver size-1500 packet on flow 7; assert op-7 trigger fires
   but op-9's doesn't.

**Definition of done**: build clean; all 3 tests pass.

---

### T6 — `FatTreeSwitch` ingress identification + INC FIB member

**Files**: `sim/htsim-backend/sim/datacenter/fat_tree_switch.h`,
`fat_tree_switch.cpp` (extend).

**Add members**:
```c
INCFib*                                _inc_fib;
std::unordered_map<BaseQueue*, uint8_t> _port_idx_by_queue;
std::vector<Route*>                    _port_egress_routes;
```

**Add `addPort` override or post-init hook** so that whenever a
queue is added to `_ports`, `_port_idx_by_queue` and
`_port_egress_routes` are kept in sync. Simplest: override
`addPort`:
```c
int FatTreeSwitch::addPort(BaseQueue* q) {
    int idx = Switch::addPort(q);
    _port_idx_by_queue[q] = static_cast<uint8_t>(idx);
    return idx;
}
```

**Populate `_port_egress_routes` lazily or in a one-shot**
`build_egress_route_cache()` called by `FatTreeTopology` after
all `addPort` calls complete:
```c
void FatTreeSwitch::build_egress_route_cache() {
    _port_egress_routes.resize(_ports.size());
    for (size_t i = 0; i < _ports.size(); ++i) {
        Route* r = new Route();
        r->push_back(_ports[i]);                      // egress queue
        r->push_back(get_pipe_for_port(_ports[i]));   // pipe
        r->push_back(_ports[i]->getRemoteEndpoint()); // next-hop
        // Invariant: every cached route is a 3-tuple
        // {queue, pipe, remote_endpoint}. Asserting this catches
        // the Fraschetti-PR-#1 failure mode where INC routes
        // contained only `{remote_endpoint}` and per-hop latency
        // was understated by ~500 ns.
        assert(r->size() == 3 &&
               "egress route must be {queue, pipe, remote}");
        _port_egress_routes[i] = r;
    }
}
```

`get_pipe_for_port`: the topology builds queue+pipe pairs in a known
order; the cleanest implementation is to record the pipe alongside
the queue when `addPort` is called. Add a parallel
`std::unordered_map<BaseQueue*, Pipe*> _port_pipe_by_queue` and a
small `addPortWithPipe(BaseQueue* q, Pipe* p)` helper called from
`fat_tree_topology.cpp:798/859/860/935/936`.

**Add `identify_ingress_port_idx`**:
```c
uint8_t FatTreeSwitch::identify_ingress_port_idx(Packet& pkt) {
    // Walk back to the upstream queue: the route element just
    // before this switch (nexthop already advanced past it).
    assert(pkt.nexthop() >= 2);
    PacketSink* upstream = pkt.route()->at(pkt.nexthop() - 2);
    Switch* upstream_switch = nullptr;
    if (auto* q = dynamic_cast<BaseQueue*>(upstream))
        upstream_switch = q->getSwitch();
    // Find the port whose remote endpoint is that upstream switch.
    for (size_t i = 0; i < _ports.size(); ++i) {
        if (_ports[i]->getRemoteEndpoint() == upstream_switch ||
            _ports[i]->getRemoteEndpoint() == upstream) {
            return static_cast<uint8_t>(i);
        }
    }
    assert(0 && "ingress port not found in _ports");
    return 0;
}
```

**Add `addMcastPort`**:
```c
void FatTreeSwitch::addMcastPort(int host_addr, uint32_t group_id,
                                 UecMcastSink* sink);
```
Implementation in `fat_tree_switch.cpp`:
```c
void FatTreeSwitch::addMcastPort(int host_addr, uint32_t group_id,
                                 UecMcastSink* sink) {
    INCFibEntry* entry = _inc_fib->lookup(group_id);
    assert(entry && "INCFib entry must exist before addMcastPort");

    // Find the port idx for the host downlink to host_addr.
    BaseQueue* host_q =
        _ft->queues_nlp_ns[_ft->HOST_POD_SWITCH(host_addr)]
                          [host_addr][0];
    auto it = _port_idx_by_queue.find(host_q);
    assert(it != _port_idx_by_queue.end());
    uint8_t port_idx = it->second;

    Route* r = new Route();
    r->push_back(host_q);
    r->push_back(_ft->pipes_nlp_ns[_ft->HOST_POD_SWITCH(host_addr)]
                                  [host_addr][0]);
    r->push_back(sink);

    entry->leaf_routes.emplace_back(port_idx, r);
    assert(entry->tree_port_mask.test(port_idx) &&
           "addMcastPort: port not in tree_port_mask");
}
```

Construct `_inc_fib = new INCFib();` in the constructor.

**Tests** (`sim/tests/fat_tree_switch_ingress_test.cpp`):

1. `port_idx_after_addport`: add 3 ports to a stub switch; verify
   `_port_idx_by_queue` returns 0/1/2 and `getPort(idx)` round-trips.
2. `identify_ingress_walks_back`: hand-build a 3-element route with
   a known upstream queue; inject a packet with `_nexthop=2`;
   verify `identify_ingress_port_idx` returns the expected index.

**Definition of done**: build clean; all 2 tests pass; phase-1
baseline regression still passes.

---

### T7 — `handle_mcast` and `receivePacket` dispatch wiring

**Files**: `sim/htsim-backend/sim/datacenter/fat_tree_switch.cpp`
(extend `receivePacket`; add `handle_mcast`).

In `receivePacket`, add a third top-level branch **after** the
existing `ETH_PAUSE` arm and **before** the existing
`_packets.find` ingress/egress logic:

```c
if (pkt.type() == UEC_MCAST) {
    handle_mcast(static_cast<UecMcastPacket&>(pkt));
    return;
}
```

`handle_mcast` implementation:
```c
void FatTreeSwitch::handle_mcast(UecMcastPacket& pkt) {
    INCFibEntry* entry = _inc_fib->lookup(pkt.group_id());
    if (!entry) {
        // Should not happen if set_up_mcast ran for this group.
        cerr << "No INCFib entry for group " << pkt.group_id()
             << " at switch " << _id << "\n";
        pkt.free();
        return;
    }

    if (_packets.find(&pkt) == _packets.end()) {
        // Ingress: fanout dispatch.
        uint8_t ingress_idx = identify_ingress_port_idx(pkt);
        std::bitset<128> egress_mask = entry->tree_port_mask;
        egress_mask.reset(ingress_idx);

        for (size_t i = 0; i < 128; ++i) {
            if (!egress_mask.test(i)) continue;
            Route* leaf = entry->leaf_route_for(static_cast<uint8_t>(i));
            const Route& route = leaf
                ? *leaf
                : *_port_egress_routes[i];

            UecMcastPacket* replica = UecMcastPacket::newpkt_replica(
                pkt, route, static_cast<uint8_t>(i));
            replica->set_direction(NONE);
            _packets[replica] = true;
            _pipe->receivePacket(*replica);
        }
        pkt.free();   // symmetric — original is freed; all egress
                      // copies are fresh allocations
    } else {
        // Egress callback from _pipe.
        _packets.erase(&pkt);
        pkt.sendOn();
    }
}
```

**Tests** (`sim/tests/fat_tree_switch_mcast_test.cpp`): hand-build a
minimal switch + INCFibEntry pair (no full topology). Use stub
queues + pipes that record received packets.

1. `interior_fanout`: 3 tree ports {1, 4, 7}; packet enters on
   port 1; assert exactly 2 replicas emerge on egress queues at
   ports 4 and 7; assert no replica on port 1.
2. `leaf_tor_fanout_to_sink`: 2 tree ports {1 (interior), 5 (leaf,
   has leaf_route to a stub sink)}; packet enters on port 1;
   assert 1 replica reaches the stub sink.
3. `original_freed`: verify original packet's PacketDB refcount
   returned to 0 after dispatch (use a hand-built tracker).

**Definition of done**: build clean; all 3 tests pass; phase-1
baseline regression still passes.

---

### T8 — Tree construction `build_mcast_tree`

**Files**: `sim/htsim-backend/sim/datacenter/fat_tree_topology.h`,
`fat_tree_topology.cpp` (extend).

Add a helper that returns, per switch on the tree, the set of
tree-member port indices and the list of local member host
addresses.

```c
struct McastTreeNode {
    Switch*               switch_ptr;
    std::vector<uint8_t>  tree_port_indices;
    std::vector<int>      local_member_hosts;   // leaf-TOR only
};

std::vector<McastTreeNode>
FatTreeTopology::build_mcast_tree(uint32_t group_idx);
```

**Algorithm** (root-agnostic, deterministic per §3.6):

```
const std::vector<int32_t>& members = (*groups)[group_idx];
std::set<int> member_tors, member_pods;
std::map<int, std::vector<int>> hosts_per_tor;

for (int h : members) {
    int tor = HOST_POD_SWITCH(h);
    int pod = HOST_POD(h);
    member_tors.insert(tor);
    member_pods.insert(pod);
    hosts_per_tor[tor].push_back(h);
}

uint32_t podpos = group_idx % agg_switches_per_pod();
bool multi_pod  = member_pods.size() > 1;

// Per-TOR entries:
for (int tor : member_tors) {
    int pod = (members are in this tor's pod);
    int agg = MIN_POD_AGG_SWITCH(pod) + podpos;
    McastTreeNode node;
    node.switch_ptr = switches_lp[tor];
    for (int h : hosts_per_tor[tor]) {
        node.local_member_hosts.push_back(h);
        node.tree_port_indices.push_back(
            port_idx_for_host_downlink(switches_lp[tor], tor, h));
    }
    if (member_tors.size() > 1 || multi_pod) {
        node.tree_port_indices.push_back(
            port_idx_for_uplink(switches_lp[tor], tor, agg));
    }
    result.push_back(node);
}

// Per-AGG entries:
for (int pod : member_pods) {
    int agg = MIN_POD_AGG_SWITCH(pod) + podpos;
    McastTreeNode node;
    node.switch_ptr = switches_up[agg];
    for (int tor : member_tors_in_pod(pod)) {
        node.tree_port_indices.push_back(
            port_idx_for_downlink(switches_up[agg], agg, tor));
    }
    if (multi_pod) {
        // Compute chosen Core for this group:
        uint32_t uplink_bundles = radix_up(AGG_TIER) / bundlesize(CORE_TIER);
        uint32_t core_offset = (group_idx / agg_switches_per_pod()) % uplink_bundles;
        uint32_t chosen_core = core_offset * agg_switches_per_pod() + podpos;
        node.tree_port_indices.push_back(
            port_idx_for_uplink_to_core(switches_up[agg], agg, chosen_core));
    }
    result.push_back(node);
}

// Per-Core entry (if multi-pod):
if (multi_pod) {
    uint32_t uplink_bundles = radix_up(AGG_TIER) / bundlesize(CORE_TIER);
    uint32_t core_offset = (group_idx / agg_switches_per_pod()) % uplink_bundles;
    uint32_t chosen_core = core_offset * agg_switches_per_pod() + podpos;
    McastTreeNode node;
    node.switch_ptr = switches_c[chosen_core];
    for (int pod : member_pods) {
        int agg = MIN_POD_AGG_SWITCH(pod) + podpos;
        node.tree_port_indices.push_back(
            port_idx_for_downlink_to_agg(switches_c[chosen_core],
                                         chosen_core, agg));
    }
    result.push_back(node);
}
```

`port_idx_for_*` helpers: walk `switches_lp[tor]->_port_idx_by_queue`
to find the port idx for the given queue (e.g.
`queues_nlp_ns[tor][host][0]` for a host downlink). Same pattern at
AGG and Core tiers using `queues_*_*` arrays.

**Edge cases**:
- All members on one TOR: single TOR entry, no AGG, no Core.
- Single pod, multiple TORs: TOR entries + one AGG entry, no Core.
- 2-tier topology: skip the multi-pod / Core branch entirely.

**Tests** (`sim/tests/build_mcast_tree_test.cpp`):

1. `intra_tor_group`: K=4 fat-tree, group of 3 hosts all on
   TOR 0; assert tree has 1 node (TOR 0) with 3 host-port
   indices, no uplinks.
2. `intra_pod_group`: K=4, group spanning 2 TORs in pod 0; assert
   2 TOR nodes (each with host downlink + uplink) + 1 AGG node.
3. `cross_pod_group`: K=4, group spanning 2 pods; assert TOR
   nodes + 2 AGG nodes + 1 Core node.
4. `root_agnostic_property`: build the tree for group_idx=5;
   build it again; assert structural identity (same switches,
   same port sets) — the algorithm is deterministic.
5. `acyclicity`: for the cross-pod result, verify the union of
   per-switch port sets, viewed as an undirected graph, has no
   cycle (use a small union-find).

**Definition of done**: build clean; all 5 tests pass.

---

### T9 — `set_up_mcast` body

**Files**: `sim/htsim-backend/sim/datacenter/fat_tree_topology.cpp`
(replace stub at line 596).

```c
void FatTreeTopology::set_up_mcast() {
    if (groups == nullptr) return;

    // First: build _port_egress_routes for every switch, now that
    // the topology is fully constructed.
    for (auto* sw : switches_lp) static_cast<FatTreeSwitch*>(sw)->build_egress_route_cache();
    for (auto* sw : switches_up) static_cast<FatTreeSwitch*>(sw)->build_egress_route_cache();
    for (auto* sw : switches_c)  static_cast<FatTreeSwitch*>(sw)->build_egress_route_cache();

    // For each group, build the tree, install entries, create sinks.
    for (uint32_t g = 0; g < groups->size(); ++g) {
        const auto& members = (*groups)[g];
        if (members.size() < 2) continue;

        auto tree = build_mcast_tree(g);

        // Install one INCFibEntry per switch on the tree.
        std::unordered_map<Switch*, INCFibEntry*> entry_by_switch;
        for (auto& node : tree) {
            INCFibEntry* entry = new INCFibEntry();
            for (uint8_t idx : node.tree_port_indices) {
                entry->tree_port_mask.set(idx);
            }
            static_cast<FatTreeSwitch*>(node.switch_ptr)
                ->_inc_fib->install(g, entry);
            entry_by_switch[node.switch_ptr] = entry;
        }

        // Per member: create a UecMcastSink, register at the
        // host's leaf TOR via addMcastPort.
        for (int h : members) {
            UecMcastSink* sink = new UecMcastSink(h, g);
            int tor_id = HOST_POD_SWITCH(h);
            FatTreeSwitch* tor =
                static_cast<FatTreeSwitch*>(switches_lp[tor_id]);
            tor->addMcastPort(h, g, sink);

            // Stash for driver lookup later:
            _mcast_sinks[std::make_pair(h, g)] = sink;
        }

        // Invariant: every leaf-TOR member port has a leaf_route.
        for (auto& node : tree) {
            if (node.local_member_hosts.empty()) continue;
            INCFibEntry* entry = entry_by_switch[node.switch_ptr];
            for (uint8_t port_idx : node.tree_port_indices) {
                if (!is_host_downlink(static_cast<FatTreeSwitch*>(node.switch_ptr),
                                       port_idx)) continue;
                assert(entry->leaf_route_for(port_idx) != nullptr &&
                       "leaf-TOR member port without route");
            }
        }
    }
}
```

Add to `fat_tree_topology.h`:
```c
std::map<std::pair<int, uint32_t>, UecMcastSink*> _mcast_sinks;

UecMcastSink* get_mcast_sink(int host, uint32_t group_id) {
    auto it = _mcast_sinks.find({host, group_id});
    return it == _mcast_sinks.end() ? nullptr : it->second;
}
```

**Tests**: integration via T10's driver path. No isolated test for
T9 alone (it's small glue).

**Definition of done**: build clean; T10's end-to-end test passes.

---

### T10 — Driver integration (`-bcast_mode mcast`)

**Files**: `sim/htsim-backend/sim/datacenter/main_uec.cpp`
(extend the `is_bcast` branch around line 831).

**Add CLI flag** parsing near the other `-` flag handlers:
```c
enum BcastMode { BCAST_BASELINE, BCAST_MCAST };
BcastMode bcast_mode = BCAST_BASELINE;
// In the argv loop:
} else if (!strcmp(argv[i], "-bcast_mode")) {
    i++;
    if      (!strcmp(argv[i], "baseline")) bcast_mode = BCAST_BASELINE;
    else if (!strcmp(argv[i], "mcast"))    bcast_mode = BCAST_MCAST;
    else { cerr << "unknown -bcast_mode\n"; exit(1); }
}
```

**Refactor the `is_bcast` block** to dispatch on mode. Keep the
existing baseline path **byte-identical** under
`bcast_mode == BCAST_BASELINE`. Add the new mcast branch:

```c
if (crt->is_bcast) {
    // ... existing root/group/leg_count validation ...

    BarrierTrigger* barrier = new BarrierTrigger(
        eventlist, ++next_bcast_barrier_id, leg_count);
    barrier->add_target(*new BcastCompletionRecorder(
        eventlist, crt->flowid, root, dest,
        crt->size, leg_count, crt->start));
    if (crt->recv_done_trigger) {
        Trigger* downstream = conns->getTrigger(
            crt->recv_done_trigger, eventlist);
        barrier->add_target(*new TriggerRelay(downstream));
    }

    if (bcast_mode == BCAST_BASELINE) {
        // ... existing |G|-1 leg synthesis loop (unchanged) ...
    } else {  // BCAST_MCAST
        uint32_t op_flow_id = crt->flowid
            ? crt->flowid : ++next_bcast_leg_flow_id;

        // Register per-op expectations on each member's
        // persistent UecMcastSink (created by set_up_mcast).
        for (int32_t m : group) {
            if (m == root) continue;
            UecMcastSink* sink = top->get_mcast_sink(m, dest);
            assert(sink && "set_up_mcast did not create sink for "
                           "(host, group)");
            sink->register_op(op_flow_id, crt->size, barrier);
        }

        // Allocate the source.
        UecBcastSrcMcast* bs = new UecBcastSrcMcast(
            NULL, NULL, eventlist,
            base_rtt_max_hops, bdp_local, 100, 6);
        bs->setNumberEntropies(256);
        bs->set_group_id(dest);
        bs->set_flowid(op_flow_id);
        if (crt->size > 0) bs->setFlowSize(crt->size);
        bs->from = root;
        bs->to   = -1;             // mcast — no single dest
        bs->set_paths(number_entropies);

        if (crt->trigger) {
            Trigger* trig = conns->getTrigger(
                crt->trigger, eventlist);
            trig->add_target(*bs);
        }

        // Build srctotor route exactly as in baseline.
        Route* srctotor = new Route();
        if (top != NULL) {
            srctotor->push_back(top->queues_ns_nlp[root][top->HOST_POD_SWITCH(root)][0]);
            srctotor->push_back(top->pipes_ns_nlp[root][top->HOST_POD_SWITCH(root)][0]);
            srctotor->push_back(top->queues_ns_nlp[root][top->HOST_POD_SWITCH(root)][0]->getRemoteEndpoint());
        }

        // Connect: routeback unused on mcast (ack-less); use a
        // dummy sink to satisfy the connect() signature.
        UecSink dummy_sink;
        bs->connect(srctotor, /*routeback=*/nullptr,
                    dummy_sink, crt->start);

        bs->setName("uec_bcast_mcast_op" + ntoa_uec(op_flow_id)
                    + "_root" + ntoa_uec(root)
                    + "_g" + ntoa_uec(dest));
        logfile.writeName(*bs);
    }

    continue;
}
```

**End-to-end test**: re-run an existing `.cm` file
(`bcast_test1.cm` or `t01_simple_bcast.cm`) with both modes:

- `htsim_uec ... -bcast_mode baseline` → output unchanged from
  pre-phase-two reference.
- `htsim_uec ... -bcast_mode mcast` → output produces a
  `BCAST_COMPLETE ... duration_ns=...` line; duration is
  approximately `2 × T_link_host_to_TOR + T_path_root_to_farthest_leaf`
  (no |G|-1 serialisation).

**Definition of done**: both runs succeed; mcast completion time
is **roughly constant** as |G| varies (≤ 2× variation across a
sweep, vs. linear under baseline).

---

### T11 — Benchmark and plotting extension

**Files**:
- `sim/htsim-backend/sim/datacenter/connection_matrices/bcast_sweep/run_bcast_sweep.py`
- `sim/htsim-backend/plotting/plot_bcast_baseline.py`

**`run_bcast_sweep.py`**: add a `--mode {baseline,mcast,both}`
argument (default `both`). When `both`, run each combination of
(topology, group_size) twice — once per mode — and tag each row
in the output CSV with a `mode` column.

**`plot_bcast_baseline.py`**: add reading of the `mode` column;
draw one line per (topology, mode) pair. Distinguish mcast from
baseline with line style (e.g., dashed for mcast); annotate the
plot legend.

Update `gen_bcast_sweep.py` if any per-mode metadata needs to be
emitted into the generated `.cm` files (likely none — the same
.cm runs in both modes via the CLI flag).

**Definition of done**: full sweep runs in both modes; plot
shows linear baseline curve and approximately constant mcast
curve; PDF + PNG outputs produced.

---

### T12 — Regression and acceptance

Run, in order, with all results recorded:

1. **Phase-1 baseline regression**:
   ```
   cd sim/htsim-backend/sim/datacenter
   ./htsim_uec -tm connection_matrices/bcast_test1.cm -bcast_mode baseline
   ```
   Output must be **bit-identical** to the pre-phase-two reference
   (compare against a saved baseline file).

2. **Phase-2 mcast smoke test**:
   ```
   ./htsim_uec -tm connection_matrices/bcast_test1.cm -bcast_mode mcast
   ```
   Output produces a `BCAST_COMPLETE ...` line; manually verify
   the duration is consistent with `O(max-leaf-path-latency)` not
   `O(|G| × t_ser)`.

3. **Functional tests in mcast mode**:
   ```
   cd connection_matrices/tests
   ./run_tests.py --mode mcast    # if run_tests.py supports it
   ```
   All `t01..t08` should produce sane completion times in mcast
   mode (slightly different from baseline; document expected
   ratios).

4. **Full sweep**:
   ```
   cd connection_matrices/bcast_sweep
   ./run_bcast_sweep.py --mode both
   cd ../../../../plotting
   ./plot_bcast_baseline.py results.csv
   ```
   Resulting plot: `bcast_baseline.pdf` shows linear baseline +
   approximately constant mcast for all three fat-tree sizes.

5. **v4 cross-op pathid stability** (manual check): pick two
   `.cm` operations on the same group with the same root; in a
   trace-enabled run, verify that replicas traversing the same
   physical path on both ops have identical `_pathid` values.

**Definition of phase-2 done**: all five checks pass; the v4 plan's
§6 expected outcome is observed in the data; no phase-1 regression.

---

## 3. Files Touched (Summary)

**New files**:
- `sim/htsim-backend/sim/inc_fib.{h,cpp}` (T3)
- `sim/htsim-backend/sim/uec_collective.{h,cpp}` (T4)
- `sim/tests/uec_mcast_packet_test.cpp` (T2)
- `sim/tests/inc_fib_test.cpp` (T3)
- `sim/tests/uec_collective_test.cpp` (T4)
- `sim/tests/uec_mcast_sink_test.cpp` (T5)
- `sim/tests/fat_tree_switch_ingress_test.cpp` (T6)
- `sim/tests/fat_tree_switch_mcast_test.cpp` (T7)
- `sim/tests/build_mcast_tree_test.cpp` (T8)

**Modified files**:
- `sim/htsim-backend/sim/network.h` (T1: add UEC_MCAST enum)
- `sim/htsim-backend/sim/uecpacket.h`, `uecpacket.cpp` (T2)
- `sim/htsim-backend/sim/routetable.h` (T3: virtual dtor on FibEntry)
- `sim/htsim-backend/sim/uec_bcast.h`, `uec_bcast.cpp` (T5)
- `sim/htsim-backend/sim/datacenter/fat_tree_switch.h`, `fat_tree_switch.cpp` (T6, T7)
- `sim/htsim-backend/sim/datacenter/fat_tree_topology.h`, `fat_tree_topology.cpp` (T8, T9)
- `sim/htsim-backend/sim/datacenter/main_uec.cpp` (T10)
- `sim/htsim-backend/sim/datacenter/connection_matrices/bcast_sweep/run_bcast_sweep.py` (T11)
- `sim/htsim-backend/plotting/plot_bcast_baseline.py` (T11)
- `sim/htsim-backend/sim/datacenter/Makefile` (linking new .cpp files)

---

## 4. Notes for the Executing Claude

- **Read v4 (§3) for design rationale** if any task feels
  under-specified. This document tightens the v4 plan; it does
  not replace its reasoning.
- **Do not skip tests**. Each task's tests are the gate to the
  next task; running them is the only way to catch a wrong
  assumption early. If a test fails, debug *that test*, not the
  next task.
- **Keep the baseline path byte-identical**. T10's regression
  check requires the existing `bcast_test1.cm` baseline output
  to match pre-phase-two output bit-for-bit. Any divergence
  indicates an unintended change to the existing code path.
- **Symmetric replica handling** (T7): always free the original
  packet and spawn N fresh replicas. Do not optimise by
  reusing the original for one branch — the asymmetry causes
  subtle `_direction`-state bugs that are hard to diagnose.
- **`_packets[replica] = true` BEFORE `_pipe->receivePacket`**:
  the pipe callback re-enters `receivePacket` on the same packet
  pointer; without the marker, the switch mistakes the egress
  callback for a fresh ingress and loops or misroutes.
- **C++ casts** per house style: `static_cast<T>(x)` not `(T)x`.
- **Trust `.cm` inputs**: malformed connection matrices are
  allowed to crash; do not add defensive bounds checks.
- **Commits**: one per task at minimum, with a clear message
  scoped to that task. Run the relevant test(s) before each
  commit.

---

## 5. Done Criteria

Phase two is complete when:

- ✅ All 12 tasks closed with passing tests.
- ✅ Phase-1 baseline regression bit-identical.
- ✅ `-bcast_mode mcast` produces approximately constant
  completion-time curve across the sweep.
- ✅ Full sweep CSV + plot generated, both modes overlaid.
- ✅ Same-group-different-root operations share FIB state and
  produce same `_pathid` for matching paths.
- ✅ Thesis section in `thesis/Design and Implementation.tex`
  reflects the implemented design (see separate thesis commit).

Implementation can then move to phase three (aggregation) per
v4 §10.
