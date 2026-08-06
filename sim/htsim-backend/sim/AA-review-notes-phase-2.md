# Phase-2 review notes

Per-commit, per-file scratchpad. File paths are relative to
`sim/htsim-backend/`. Build files (Makefile, .gitignore), binaries
(.pdf, .png), generated data (results CSVs, generated manifests),
plan docs, and the thesis submodule pointer are omitted.


The lifecycle in three phases

Phase A — topology construction (set_up_mcast(), once per simulation):
- For each multicast group, build the tree.
- At every switch on the tree, install an INCFibEntry with tree_port_mask set.
- At each leaf TOR, for every member host:
  - Create a persistent UecMcastSink for that (host, group) pair.
  - Call addMcastPort(host, group, sink) to install a {queue, pipe, sink} route into the TOR's
    INCFibEntry.leaf_routes.

Phase B — driver setup per bcast op (main_uec.cpp):
- Look up the existing UecMcastSink via top->get_mcast_sink(m, dest) — no new sink, no new switch
  state.
- Call sink->register_op(op_flow_id, expected_bytes, barrier) so the sink knows how many bytes
  constitute "done" for this op.
- Create a fresh UecBcastSrcMcast for this op.

Phase C — runtime:
- Source emits UecMcastPackets onto srctotor.
- Every switch's handle_mcast does _inc_fib->lookup(group_id) → fanout via cached routes. (*)
- Each replica's leaf_routes route terminates at the right pre-existing sink, which matches flow_id
  against its _per_op map to update bytes-received.

(*) so for this  Every switch's handle_mcast does _inc_fib->lookup(group_id) → fanout via cached routes.
does it work like this: gets IncFibEntry, gets Bitset, from bitset iterate and create replica based on
if bit i is 1, if so call egressroutes[i] to get egress route of port i
---

## 2522e24  T1+T2: UEC_MCAST packet type and UecMcastPacket class

### sim/network.h
UEC_MCAST enum type

### sim/uecpacket.h

#### Packet Overhead
UecMcastPacket: I noticed that the header overhead (acksize) is still
64 but we add two new fields (group_id and op_seq_id) however because 
we can now drop other fields form the header (like ACKs/NACKs/credit 
fields) we can assume that the header overhead of 64 bytes is enough 
for the new packets

#### PathID
To ensure that each packet that takes the exact same path in the Topology uses the same path_id we use hashing. At the NIC we do p->_pathid = (group_id ^ source_host_id) * PATHID_SEED_MIX;
Which is the start path_id. We use the PATHID_SEED_MIX to ensure that the path uses the full 32 
bit range and not only the lower few bits.

At each hop we do the following:

p->_pathid = source._pathid * PATHID_HOP_MIX  + static_cast<uint32_t>(egress_port_idx) + 1u;

Where the HOP_MIX is 31 and the +1 ensures that even for egress port 0 we permute the pathid

This approach is called a polynomial rolling hash and has the following structure after d hops:

pathid = S · 31^d  +  (p₁+1) · 31^(d-1)  +  …  +  (p_d+1)         (mod 2^32)

So this is currently not really needed and is just a good practice choice, we notice that the 
path is dependent on the group, host pair because of the initial hash, however this could be 
generalised to be generic and only depend on the path taken, so for example if we have two 
different groups with a common destination host and common src host then paths would be 
identical which currently is not reflected in the pathid.

#### Packet Replica
When replicating a mcast packet, we take care of the following: we need to allocate a new packet
from the DB and we need to explicitly mention the egress port, the route provided corresponds 
already to the newly generated branch route. the egress port is simply used for the path_id 
creation.



### sim/uecpacket.cpp
Simply added initialization of packet Database

### sim/tests/main_uec_mcast_packet_test.cpp
so simple functionality tests of the new added packet class

---

## 997c9c5  T3: INCFib + INCFibEntry per-switch FIB
virtual = "let derived classes replace this function, and make the replacement reachable through
base-class pointers." In T3, it's used twice — once on the destructor (correct cleanup through
FibEntry*) and once on getEgressPort (so the assert in INCFibEntry actually fires when ECMP code
mis-routes through the base interface).

Notes about FibEntry: 
One reason why htsim is so fast at Runtime is because the Routes are prebacked for each switch, you
don't need to do a Lookup. 
The cost field is used for ECMP/multipath so you can assign a cost to the FibEntries However
for our scenario this doesn't make sense (yet) because we only have one reduction Tree and thus one
INCFibEntry (What if we had more Reduction Trees?)
### sim/inc_fib.h
NOTE: The inheritance is not needed since we keep INCFibs in a separate Map Thus I removed
INheritance from FibEntry

Constructor sets out route to nullptr because a mcast entry does fanout and not single path
the Direction is defaulted to NONE because the Reduction Tree is undirected, at RT the RPF 
gives it direction based on which port a given packet arrives on (How?)

std::bitset<128> tree_port_mask; encodes the groups. Bit i set ↔ Switch::_ports[i]
is in this group's tree.

leaf_route_for() is a bit scuffed, Im not sure if this is the best way to handle it. 
We create an entry for each leaf ToR to host for each (host, group) pair 
### sim/routetable.h
We changed the class deletion method to virtual aswell as the getEgressPort() Notice that this
increases the execution time because virutal function calls take more dereferencing and cannot be 
inlined neither. However according to Claude this is noise compared to other packet scheduling ops 
and can therefore be accepted. Worth Knowing though.

### sim/tests/main_inc_fib_test.cpp
simple tests (ignored)

---

## 45c08fc  T4: UecCollectiveSrc / UecCollectiveSink base classes

### sim/uec_collective.h

#### SRC
htsim has two ways to start a flow:

- Time-scheduled: register with EventList::sourceIsPending(start_ns); the event loop fires
  doNextEvent() at the scheduled time.
- Trigger-started: register as a TriggerTarget; some other completion fires activate().
  
start hooks both delegate to emit_once()

receivePacket() is a no-op. ACK-less by construction.

No retransmission counter, no in-flight tracker, no sequence
number management — that all belongs to the unicast UEC source and is deliberately not used here.

#### SINK

persistent per-(host, group), incremental op registration via register_op, byte-count
completion check fires end_trigger once.
-> |G| sinks per group
OpState — per-operation bookkeeping
Four fields. The first two implement byte-count progress.
end_trigger is the htsim trigger
object that fires when the op completes — connects this op's completion to whatever depends on it
(driver completion log, downstream op start, etc). completed is the latch that prevents 
double-firing.

register_op — driver-side registration
A sink can be reused for several different operations thus it keeps a _per_op map where each OpState
is registered.


TODO: remove comment at line 118: For aggregating sinks (phase 3) this is also
// where partial-reduction values are combined.

This is not true we don't actually aggregate values

TODO: Fix the process_body method or simply remove the virtual overridden 
data_packet_size() method, there exists a size() method already in Packet class (redundant 
overcomplication; need cast etc)


### sim/tests/main_uec_collective_test.cpp
again simple tests

---

## 749fcf7  T5: UecBcastSrcMcast and UecMcastSink subclasses

### sim/uec.h
two friend lines. for the new src/sink types 

### sim/uec_bcast.h
TODO: move comment further down on line 89 to Recorder

TODO: here the process_body method should be rewritten 

### sim/uec_bcast.cpp

nside void UecBcastSrcMcast::emit_once(), does it make sense to always initialize new packets
with mss? what if we consider last packet? how is this handled in regular p2p traffic? (yes
P2P also does it, leave it for now)

static_cast<uint32_t>(this->from) exists because Packet::from is signed int while the factory wants
▎ uint32_t for the hash seed (problem lies inside packet base class types)

op_seq_id is redundant here but important in phase 3

Whats exactly FlowId and why not use this field for op_seq_id? 

                          +-------------------+
                          |  UecMcastPacket   |  ← the packet object
                          |-------------------|
                          |  _seqno           |
                          |  _group_id        |  ← in the packet body
                          |  _op_seq_id       |  ← in the packet body
                          |  _pathid          |
                          |  _size            |
                          |  _flow*  ─────────┼──┐
                          +-------------------+  │
                                                 ▼
                          +-------------------+
                          |   PacketFlow      |  ← a separate, shared object
                          |-------------------|
                          |  _flow_id         |  ← pkt.flow_id() reads this
                          |  ...              |
                          +-------------------+

PacketFlow is htsim's transport-layer object. It's where unicast UEC tracks per-connection state —
cwnd, retransmit timers, ACK windows, etc. Many packets in a unicast flow share one PacketFlow object
that lives for the duration of the flow.


### sim/tests/main_uec_mcast_sink_test.cpp
blabla

---

## 0943dae  T6: FatTreeSwitch INC FIB + ingress identification

The general difference between the P2P routing and routing based on Reduction Trees is that 
the _fib cannot be constructed the same way. Because the routing decision is not just "route up"
and pick among uproutes according to routing protocol. It's clearly defined how we route up 
given a group (+ ingress idx)

FatTreeSwitch  (existing class, T6 grafts on:)
├── _inc_fib                    INCFib*  (one per switch)
│     └── per-group INCFibEntry  (installed by T8+T9 set_up_mcast)
│
├── _port_idx_by_queue          BaseQueue* → uint8_t   (reverse map: 'which port is this?')
├── _port_pipe_by_queue         BaseQueue* → Pipe*     (queue ↔ paired pipe pairing)
├── _port_egress_routes         vector<Route*>          (pre-baked {q,p,remote} per port)
│
└── methods to populate, query, and bridge route ↔ FIB

General issue: We know that the addHostPorts are populated lazily i.e at Runtime when we build 
the srces and sinks. Therefore I don't think we can have port_idx_by_queue ? nor pre baked egress
routes? 

Solution: We precompute all paths at initialization 

### sim/datacenter/fat_tree_switch.h
We identify the ingress port by taking the packet, fetching the upstream queue (queue, that 
sits at the previous switch or host) and fetching the queue owner (queue contains its owner)
Then you iterate over the ports of the switch and fetch remote endpoint, checking for matches 
with the upstream switch (if queue belonged to a switch) (O(128) for each packet)


     "What do I do with a packet for group X?"     ─►  _inc_fib
     "Which port did this packet come in on?"      ─►  _port_idx_by_queue
     "Which pipe pairs with this queue?"           ─►  _port_pipe_by_queue   (setup-only)
     "How do I send a replica out port i?"         ─►  _port_egress_routes

The Sinks are created at initilaization time and then are distributed back in the uec_main
whenever we encounter a new Bcast operation. This allows us to install the leaf routes at 
the switches during setup and not during Traffic traversal. Notice that the src is newly created
in the uec_main but since we never send back to it, we don't have to add it to the switch leaf 
routes

### sim/datacenter/fat_tree_switch.cpp*
*
Destructor method created, IMO not needed because switches never get deleted, but good practice?


---

## af0c475  T7: handle_mcast RPF dispatch + receivePacket wiring

### sim/datacenter/fat_tree_switch.h
mcast handler branching logic implemented, bitmask iteration and packet replication


### sim/datacenter/fat_tree_switch.cpp
mcast handler branching logic implemented, bitmask iteration and packet replication


---

## 865e1e5  T8+T9: tree construction + set_up_mcast body
TODO: ADd a visualization of the McastTree that is built in the Thesis

NOTE: This probably needs the most reengineering when addding LOSSLESS_INPUT Queue type
### sim/datacenter/fat_tree_topology.h
defines McastTreeNode struct

### sim/datacenter/fat_tree_topology.cpp
Reasons why we have the pipe_by_queue map per switch and not just one big table:
Build-time locality: per-switch maps are smaller and L1-friendlier inside build_egress_route_cache.
- Symmetry: matches _port_idx_by_queue, which is naturally per-switch.
- Subtype independence: switch doesn't bind to a specific topology's array shape.

set_up_mcast():
first populate the pipe per queues for ALL switches (if we only have one group then this is 
quite a bit overhead) -> make a plot 
precompute all egress routes based on the Reduction Trees created, for all switches 
per group tree construction + FIB install on all switches




COMMENT: ❯ okay so I tihnk our implementation is generally good. what i noticed is that the way we route traffic
ofc is really different from before. All this precomputing at initialization time of the topology
really is new. I wonder a) how much that drags the total time and b) what the alternatives were that
we could have went with that potentially would have less friction. this is jiust a question so do not
make any changes


(a) How much does the precomputation cost?

The setup work scales roughly linearly in topology size and group count. Order-of-magnitude breakdown
for a representative 1024-host K=8 fat tree with 100 groups of |G|=16:

Phase: FatTreeTopology ctor (always paid, even baseline)
Work: allocate queues, pipes, switches, wire them
Approx cost: dominant — milliseconds
────────────────────────────────────────
Phase: populate_port_pipes
Work: walk all queue/pipe arrays, hash insert into each switch
Approx cost: ~10K inserts → ~100 µs
────────────────────────────────────────
Phase: build_egress_route_cache × all switches
Work: ~80 switches × ~96 ports × Route allocation
Approx cost: ~8K small allocations → ~1 ms
────────────────────────────────────────
Phase: build_mcast_tree × 100 groups
Work: per group: walk members, compute tree, return descriptor
Approx cost: 100 × <100 µs → ~10 ms
────────────────────────────────────────
Phase: FIB install + sink creation × 100 groups
Work: ~5–10 INCFibEntries + ~16 sinks per group
Approx cost: ~10 ms

Total mcast-specific setup: tens of ms. Memory: a few MB across all switches.

For comparison, the simulation event loop for a 100-bcast run processes millions of events (packets ×
hops × switches) and takes seconds to minutes of wall clock to simulate microseconds of network time.
The setup is noise in the wall-clock budget — a few ms before a multi-second simulation.

The setup cost only becomes visible at huge scale: a 10K-host fat tree with 1000+ groups would push
setup into seconds. Still amortized away if simulation time exceeds it, but starts to be a perceptible
delay for quick iteration.

The memory picture is more interesting. The biggest growers:
- _port_egress_routes per switch: K ports × 3-element Route × ~32 bytes ≈ ~10 KB per switch.
- Persistent UecMcastSink per (host, group) on member TORs.
- INCFibEntry per (switch, group) on the tree.

At 1024 hosts × 100 groups, you're looking at tens of MB of FIB-related state alive for the whole
simulation. Phase 2 isn't tight on this — _port_pipe_by_queue for instance is never read after setup
but still occupies memory forever. That's the closest thing to a real cost issue.
### sim/datacenter/fat_tree_switch.cpp
uint32_t uplink_bundles = radix_up(AGG_TIER) / bundlesize(CORE_TIER);
uint32_t core_offset    = (assignment_idx / agg_switches_per_pod()) % uplink_bundles;
uint32_t chosen_core    = core_offset * agg_switches_per_pod() + podpos;

1. uplink_bundles — how many distinct cores a single AGG can reach (radix_up(AGG) / bundlesize(CORE),
   as you just asked).
2. core_offset — picks a "row" in the (AGG-slot × CORE-bundle) matrix, derived from the round-robin
   counter:
   core_offset = (assignment_idx / agg_switches_per_pod) % uplink_bundles
2. Inner division advances core_offset by 1 every agg_switches_per_pod groups; outer modulo wraps it
   through the uplink_bundles available cores.
3. chosen_core — the final global CORE ID:
   chosen_core = core_offset * agg_switches_per_pod + podpos


Setup:
- agg_switches_per_pod = 2
- radix_up(AGG_TIER) = 2
- bundlesize(CORE_TIER) = 1
- uplink_bundles = 2
- Cores: 0, 1, 2, 3

The slot-to-core compatibility:

┌────────┬─────────────────────────────────────────┐
│ podpos │            Compatible cores             │
├────────┼─────────────────────────────────────────┤
│ 0      │ {0, 2} (i.e. cores core_offset * 2 + 0) │
├────────┼─────────────────────────────────────────┤
│ 1      │ {1, 3} (i.e. cores core_offset * 2 + 1) │
└────────┴─────────────────────────────────────────┘

Now trace chosen_core as assignment_idx increments:

┌────────────────┬─────────────────┬──────────────────────────┬─────────────┐
│ assignment_idx │ podpos (=idx%2) │ core_offset (=(idx/2)%2) │ chosen_core │
├────────────────┼─────────────────┼──────────────────────────┼─────────────┤
│ 0              │ 0               │ 0                        │ 0           │
├────────────────┼─────────────────┼──────────────────────────┼─────────────┤
│ 1              │ 1               │ 0                        │ 1           │
├────────────────┼─────────────────┼──────────────────────────┼─────────────┤
│ 2              │ 0               │ 1                        │ 2           │
├────────────────┼─────────────────┼──────────────────────────┼─────────────┤
│ 3              │ 1               │ 1                        │ 3           │
├────────────────┼─────────────────┼──────────────────────────┼─────────────┤
│ 4              │ 0               │ 0                        │ 0 (wraps)   │
└────────────────┴─────────────────┴──────────────────────────┴─────────────┘

---

## 87faf0d  T10: -bcast_mode driver dispatch + ingress identification
Bug fix: identify_ingress_port_idx had been walking back to
route[nexthop-2] which is the pipe (not a queue) for the
canonical 3-element route shape. Rewrote to inspect route[0]
directly (the upstream queue). Two cases:
  - upstream queue's getSwitch() != null → match this switch's
    port whose remote endpoint is that upstream switch.
  - getSwitch() == null (host-originated) → use pkt.from to
    find the host downlink port on this TOR.

### sim/datacenter/fat_tree_switch.cpp


### sim/datacenter/main_uec.cpp
For the branching logic at the bottom: 
TODO: rethink the UecSink dummy_mcast_sink. can't we find a better way to handle this? 
Like overriding the connection function for the subclass? 

---

## 139d564  T11: sweep + plot scripts grow --mode and per-mode line

### sim/datacenter/connection_matrices/run_bcast_sweep.py
plots bla bla doesn't really matter

### plotting/plot_bcast_baseline.py
plots bla bla doesn't really matter


---

## d2f3bf1  Post-meeting batch 1: PT1+PT2+PT3+PT6 instrumentation

### sim/datacenter/fat_tree_topology.h


### sim/datacenter/fat_tree_topology.cpp


### sim/datacenter/fat_tree_switch.cpp


### sim/pipe.h


### sim/pipe.cpp


### sim/datacenter/main_uec.cpp


### sim/datacenter/connection_matrices/run_bcast_sweep.py


### plotting/plot_bcast_baseline.py


---

## 438cad2  Post-meeting batch 2: PT4+PT5+PT6 sweeps, plots, scripts

### sim/datacenter/connection_matrices/gen_bcast_sweep.py


### sim/datacenter/connection_matrices/run_bcast_sweep.py


### plotting/plot_mtu_sweep.py


### plotting/plot_layout_sensitivity.py


### plotting/plot_link_crosses.py


### sim/datacenter/connection_matrices/layout_tests/g3_local_n16.cm


### sim/datacenter/connection_matrices/layout_tests/g3_local_n128.cm


### sim/datacenter/connection_matrices/layout_tests/g3_local_n1024.cm


### sim/datacenter/connection_matrices/layout_tests/g3_crosspod_n16.cm


### sim/datacenter/connection_matrices/layout_tests/g3_crosspod_n128.cm


### sim/datacenter/connection_matrices/layout_tests/g3_crosspod_n1024.cm
