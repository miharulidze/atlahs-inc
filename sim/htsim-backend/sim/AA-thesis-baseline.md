# Baseline for ACK-less P2P Emulation of Broadcast in htsim

*Phase-one design record. Intended as raw material for a thesis section;
keep tone and structure close to what the final chapter will need.*

## 1. Context and Goal

The ATLAHS network simulator embeds [htsim](https://github.com/Broadcom/csg-htsim)
as its flow-level backend. htsim's UEC transport is a reliable, congestion-
controlled unicast stack modelled on the Ultra Ethernet Consortium's draft
specification. We would like to extend ATLAHS with a faithful model of
collective operations — starting with `Broadcast` — so that simulated
workloads can exercise collective-aware network features (most notably
in-network multicast replication).

Before evaluating any in-network mechanism, we need a **baseline**: the
cost of performing the same collective using only ordinary point-to-point
(P2P) unicast traffic. A broadcast from a root to a group of size \|G\|
is emulated by \|G\|-1 independent unicast flows, and the collective's
completion time is the time at which the slowest of those legs delivers
its last byte. This baseline is important for two reasons:

1. It provides the reference curve — collective completion time as a
   function of group size — that later phases must beat to justify
   in-network hardware support.
2. It exercises the setup and completion-detection plumbing that phase
   two (switch-level replication) will reuse unchanged.

## 2. Assumptions

To keep phase one tractable and decoupled from congestion-control
pathologies, we adopt the following assumptions, taken from
`AA-plan-Baseline.md`:

1. **Single-packet payloads.** Each emulated message fits inside one MTU.
   The sender emits exactly one data packet per destination.
2. **No packet loss or corruption.** Therefore no acknowledgements are
   needed for reliability.
3. **Infinite switch buffers.** Phase one ignores queue-induced loss
   entirely; the relevant contention is link serialisation, not buffer
   capacity.

Assumption (2) is the load-bearing one architecturally. It means the
transport does not need to track in-flight packets, arm retransmission
timers, or drive a congestion window from ACK feedback. It also means
an ACK, if generated, carries no useful information to either endpoint.

## 3. Baseline htsim/UEC P2P Architecture

This section records the state of the code at commit `d212e8d` so that
subsequent phases can cite specific mechanisms rather than paraphrases.

### 3.1 Traffic-matrix parsing and flow setup

`ConnectionMatrix::load` in `sim/htsim-backend/sim/datacenter/connection_matrix.cpp`
parses a `.cm` file into a vector of `connection` records
(`connection_matrix.h:16`). Each record carries source, destination,
payload size, optional flow id, optional triggers, start time, and
— added originally in `d212e8d` under the name `is_mcast` and renamed to
`is_bcast` once the collective/network-layer naming split was adopted —
a flag set when the start token is `start_bcast`
(`connection_matrix.cpp:761`). The loader also recognises `Grp`
(member-group) header lines and accumulates group membership into
`ConnectionMatrix::groups` (`connection_matrix.cpp:713-720`). `Grp` is
deliberately neutral: the same group concept is reusable for later
collectives (Scatter, Gather, Reduce) rather than being broadcast-
specific.

`main_uec.cpp:752-900` consumes the matrix. For each connection it
allocates a `UecSrc` plus a `UecSink`, constructs two three-hop
`Route`s (host → ToR queue → ToR pipe → peer-ToR endpoint) for the two
directions, and calls

```cpp
uecSrc->connect(srctotor, dsttotor, *uecSnk, crt->start);
```

which stores the sink pointer, invokes `_sink->connect(src, routeback)`
to give the sink its return route, and schedules
`sourceIsPending(src, start_time)` on the event list (`uec.cpp:626-642`).
Both endpoints are then registered on their local ToR switches via
`FatTreeSwitch::addHostPort(host, flow_id, endpoint)`
(`main_uec.cpp:886-888`), which is how a ToR demultiplexes an incoming
packet to the correct endpoint object.

### 3.2 The UEC transport: ACK-driven send loop

When `sourceIsPending` fires, `UecSrc::doNextEvent` calls `startflow`
(`uec.cpp:644-653`), which calls `send_packets` (`uec.cpp:688`). The
send loop is gated by the congestion window:

```cpp
while (get_unacked() + _mss <= c && _highest_sent < _flow_size) {
    UecPacket *p = UecPacket::newpkt(...);
    p->sendOn();
    _sent_packets.push_back(SentPacket(...));
    if (_rtx_timeout == timeInf) update_rtx_time();
}
```

The first packet arms an RTO. The source then waits for
`UECACK`/`UECNACK` on its return route. `UecSink::receivePacket`
(`uec.cpp:953`) processes each data packet and **unconditionally**
synthesises an ACK:

```cpp
send_ack(ts, marked, seqno, ackno, _paths.at(crt_path), NULL, path_id);
```

with a `UecNack` substituted when the arrival is a trimmed header-only
packet. `UecSrc::receivePacket` (`uec.cpp:535`) dispatches `UECACK` into
`processAck` (`uec.cpp:422`), which updates the window, clears entries
in `_sent_packets`, and — only when the cumulative ack covers the flow
and `_sent_packets` is empty — signals completion via
`_end_trigger->activate()` and posts an `EventOver` to ATLAHS
(`uec.cpp:463-498`).

### 3.3 Completion detection lives inside `processAck`

Three observations matter for the rest of the document:

- **Completion semantics are ACK-coupled.** The only path to
  `_flow_finished = true` is inside `processAck`, and the only hook
  that fires the simulation-level trigger is inside that same branch.
- **The send loop is CWND-gated.** Without ACKs, `get_unacked()` never
  decreases; after one bandwidth-delay product of outstanding data the
  source stalls.
- **An RTO is always armed.** `_rtx_timeout` starts from the first
  packet. Without ACKs, it will expire and trigger retransmissions of
  packets the receiver has already accepted.

### 3.4 Pre-existing collective scaffolding and the naming split

Commit `d212e8d` added parser-side plumbing but no data-path behaviour.
The fields were originally spelled with the prefix `mcast`, which we
subsequently split along a meaningful boundary:

- **Collective operation (what the user writes)** — renamed to `bcast`.
  The MPI operation being emulated is `MPI_Bcast`. Hence the `.cm`
  keyword `start_bcast`, the `connection.is_bcast` flag, and the
  transport classes `UecBcastSrc`/`UecBcastSink` (introduced below).
- **Network-layer mechanism (reserved for phase two)** — retained as
  `mcast`. `FatTreeTopology::set_up_mcast()`
  (`fat_tree_topology.cpp:596`) is a stub for switch-level multicast
  replication and will be implemented in phase two. Multicast here is
  the network primitive, reusable by other one-to-many collectives
  (Scatter, some Reduces); broadcast is the specific MPI collective.

Other d212e8d artefacts:
- `Grp` tokens (member-groups, kept as-is — reusable across
  collectives).
- A boolean `UecSrc::_is_mcast` with an unused setter — removed as
  part of this phase, since the new subclass makes the flag redundant.
- In `main_uec.cpp` the original mcast branch named a `UecSrc` and then
  `continue`d, leaving it unconnected, unscheduled, and never
  transmitting. Discarded.

The parser-side group and triggers machinery is reused unchanged. The
original data-path scaffolding is discarded.

## 4. Why Unicast P2P Cannot Directly Emulate Collectives

Three concrete incompatibilities follow from §3:

1. **Unsolicited ACK traffic.** The sink generates one ACK per data
   packet regardless of intent. For a broadcast of size \|G\| to a root
   we would incur 2·(\|G\|-1) packet transmissions instead of \|G\|-1,
   and the return-path bandwidth measured in the baseline would be
   dominated by bookkeeping that a real multicast path does not emit.
2. **Completion depends on ACK arrival.** Without acknowledgements the
   source never reaches `_flow_finished`, never fires `_end_trigger`,
   and never posts `EventOver`. The barrier that decides "broadcast
   complete" therefore has no input.
3. **Congestion control and retransmission are meaningless here.** The
   CWND gate and the RTO are correctness mechanisms for a lossy, shared
   network. Under assumption (2) they merely stall the source and
   inject spurious retransmits.

Reusing `UecSrc`/`UecSink` unmodified is thus not viable.

## 5. Design Space

Four candidate architectures were considered.

**A. Dedicated collective endpoint classes.** Introduce
`UecBcastSrc`/`UecBcastSink`, new classes that share routing and
entropy plumbing with the UEC pair but override the data-path methods:
the sink counts bytes and raises a local completion signal without
emitting ACKs; the source bypasses CWND and RTO, emits the required
packets, and raises completion from a self-scheduled event at
last-byte-serialised time.

- *Pros:* keeps production UEC untouched; presents a clean override
  seam for phase two; separate counters ease benchmarking.
- *Cons:* duplicates some connection setup unless implemented via
  inheritance; needs an alternative completion signal.

**B. Add an ack-less mode to `UecSrc`/`UecSink`.** Piggy-back on the
existing `_is_mcast` (now `_is_bcast`) flag, gate `send_ack`,
short-circuit `_rto`, move completion out of `processAck`.

- *Pros:* smallest diff; reuses all existing wiring.
- *Cons:* fuses two reliability models into one class; every future
  change to UEC congestion control must now reason about the ack-less
  path; stale CWND/RTO state is easy to misuse. This cost persists
  beyond the thesis.

**C. Per-packet ack-suppression flag.** A bit on the packet that the
sink checks before calling `send_ack`.

- *Pros:* trivial sink-side change.
- *Cons:* fixes the sink only. The source still stalls under CWND and
  still arms an RTO, so the approach collapses into B plus further
  hacks.

**D. Decompose one `mc` operation into \|G\|-1 P2P legs bound to a
`BarrierTrigger`.** Orthogonal to A/B/C — this is a setup-time concern
rather than a transport concern. The parser already supports
`trigger … barrier` and `recv_done_trigger`; we use them: each leg
fires its sink's local trigger on last-byte-received, and the barrier
(count = \|G\|-1) fires the collective-complete trigger.

- *Pros:* completion semantics match the physical intuition
  ("collective is done when the last sink has received"); reuses
  existing trigger machinery; phase two swaps \|G\|-1 senders for a
  single sender without disturbing the completion path.
- *Cons:* `main_uec.cpp` must synthesise \|G\|-1 connection objects
  from a single `mc ROOT → GRP` line.

## 6. Chosen Architecture: A Combined with D

Phase one uses **A for the transport and D for the decomposition**.
Option B is rejected because the long-term maintenance cost of an
ack-less mode inside the production UEC class is not justified by the
code saved. Option C is rejected because it solves only half the
problem. Option D is adopted because the resulting completion topology
— many sinks, one barrier — is what we will continue to measure in
phase two after replacing the \|G\|-1 senders with in-network
replication. Keeping the benchmark harness fixed across phases is
essential for the thesis comparison.

The pre-existing flag `UecSrc::_is_mcast` is removed: the new subclass
makes the flag redundant and avoids the trap of a stale boolean that
future code might read.

### 6.1 Naming convention

- *Collective layer* uses `bcast` (the MPI operation). This covers the
  parser keyword (`start_bcast`), the connection-level flag
  (`connection.is_bcast`), and the transport subclasses
  (`UecBcastSrc`/`UecBcastSink`), as well as the new translation unit
  `uec_bcast.{h,cpp}`.
- *Network layer* keeps `mcast` (the network primitive).
  `FatTreeTopology::set_up_mcast()` stays as-is; phase two will fill it
  in with switch-level multicast FIB and replication logic, which is
  the mechanism on top of which broadcast (and potentially other one-
  to-many collectives) run. Conflating the two would prevent future
  reuse of the phase-two fabric support.

## 7. Phase-One Implementation Commitments

The following decisions have been made and will be recorded in the
implementation:

- **Completion metric.** Collective-complete = last byte received at
  the farthest sink.
- **Transport classes.** `UecBcastSrc : public UecSrc` and
  `UecBcastSink : public UecSink`, in the new translation unit
  `uec_bcast.{h,cpp}`. `UecBcastSrc::bcast_send_once` emits one packet
  per destination without CWND gating and without arming an RTO;
  `receivePacket` on the source is a no-op for `UECACK`/`UECNACK`.
  `UecBcastSink::receivePacket` counts received bytes and fires
  `_end_trigger` on the last byte; it does not call `send_ack` or
  `send_nack`.
- **Leg synthesis.** `main_uec.cpp`, on encountering a `connection`
  with `is_bcast`, reads `conns->groups[crt->dst]`, treats
  `crt->src`-th member of that group as the root, and synthesises one
  `UecBcastSrc`/`UecBcastSink` pair per other group member. Each pair
  is wired through the normal ECMP route-construction path used by
  regular UEC connections and registered on the correct ToRs via
  `addHostPort`.
- **Flow identity.** Distinct `flow_id` per leg, synthesised starting
  above `ConnectionMatrix::max_flowid()`. Traces and per-flow logs
  remain legible. To make the upper bound tight, the parser rejects
  any matrix that uses `start_bcast` but leaves a connection without
  an explicit `id`; that prevents implicit `PacketFlow` defaults from
  colliding with synthesised leg ids on a shared `(host, flow_id)`
  ToR FIB entry.
- **Entropy pool.** Same ECMP entropy pool as regular UEC traffic; no
  reserved subset.
- **Completion orchestration.** A fresh `BarrierTrigger` with
  `count = |G| - 1` is allocated per `bcast` operation;
  `UecBcastSink::_end_trigger` points at this barrier for every leg;
  the barrier forwards to the user-specified `recv_done_trigger` (or
  equivalent) so that downstream operations in the `.cm` chain begin
  exactly when the broadcast is complete.
- **Topology side.** `FatTreeTopology::set_up_mcast()` remains a stub.
  Switch-level replication is phase-two work and keeps the `mcast`
  name because it is the network primitive, not the collective.

## 8. Relationship to Phase Two

Phase two will introduce switch-resident multicast tables so that a
single `UecBcastSrc` emits one data packet and the network fabric
replicates it toward the \|G\|-1 receivers. The completion mechanism
described above — \|G\|-1 `UecBcastSink` instances behind one
`BarrierTrigger` — is unchanged. What changes is solely the number of
*sending* endpoints (one instead of \|G\|-1) and the ToR-level routing
decision. The x-axis of the benchmark plot (group size) and the y-axis
(collective completion time) remain directly comparable across phases.

## 9. Benchmark Plan and Results

### 9.1 Plan

Phase-one evaluation follows the guidance in
`hoefler_scientific_benchmarking.md`:

1. Report broadcast completion time as a function of group size on a
   fixed `FatTreeTopology`, starting with the hand-written
   `bcast_test1.cm` (group of size 7, 16-node fabric) as a
   plausibility check.
2. Sweep topology size and group size in generated matrices. Expect
   approximately linear scaling of completion time with group size —
   the signature of naive unicast emulation.
3. Use the same driver and parser invocation for every run so that
   phase-two plots overlay directly.

### 9.2 Measurement harness

Completion times are emitted by `CollectiveCompletionRecorder`, attached as
a `TriggerTarget` to the per-operation `BarrierTrigger`. When the
barrier fires (last leg reports last-byte-received), the recorder
prints one machine-parseable line to stdout:

```
BCAST_COMPLETE op_id=N root=R group=G size=B legs=K
               start_ns=t0 complete_ns=t1 duration_ns=dt
```

The sweep tooling lives in
`sim/htsim-backend/sim/datacenter/connection_matrices/`:

- `gen_bcast_sweep.py` — generates one `.cm` per
  `(nodes, group_size, rep)`, each with a fresh random group.
- `run_bcast_sweep.py` — runs `htsim_uec` per matrix, parses the
  `BCAST_COMPLETE` line, emits a CSV (`bcast_sweep/results.csv`).
- `sim/htsim-backend/plotting/plot_bcast_baseline.py` — reads the CSV
  and plots completion time vs. group size with min/median/max error
  bars.

Variance across reps comes from random group *membership*, not from
the simulator RNG: under `ECMP_FIB` the path selection is
deterministic given a fixed flow order, so changing `-seed` has no
effect on completion time. Membership variance is the meaningful
dimension under these phase-one assumptions.

### 9.3 Results

Sweep parameters: three topologies (16-node `K=4`, 128-node `K=8`,
1024-node `K=16` fat-trees), group sizes in
{2, 4, 8, 16, 32, 64, 128, 256, 512, 1024} capped at the topology,
ten random-group reps per point, 4096-byte payload (single-MTU per
assumption 1), 100 Gbps link speed.

Summary (duration in ns; legs = \|G\| - 1):

| nodes | \|G\| | legs | min    | median | max    |
|-------|-------|------|--------|--------|--------|
| 16    | 2     | 1    | 3582   | 3582   | 3582   |
| 16    | 4     | 3    | 4258   | 4258   | 4258   |
| 16    | 8     | 7    | 5610   | 5610   | 5610   |
| 16    | 16    | 15   | 8313   | 8313   | 8313   |
| 128   | 2     | 1    | 2444   | 3582   | 3582   |
| 128   | 4     | 3    | 4258   | 4258   | 4258   |
| 128   | 8     | 7    | 5610   | 5610   | 5610   |
| 128   | 16    | 15   | 8313   | 8313   | 8313   |
| 128   | 32    | 31   | 13720  | 13720  | 13720  |
| 128   | 64    | 63   | 24533  | 24533  | 24533  |
| 128   | 128   | 127  | 46160  | 46160  | 46160  |
| 1024  | 2     | 1    | 2444   | 3582   | 3582   |
| 1024  | 4     | 3    | 4258   | 4258   | 4258   |
| 1024  | 8     | 7    | 5610   | 5610   | 5610   |
| 1024  | 16    | 15   | 8313   | 8313   | 8313   |
| 1024  | 32    | 31   | 13720  | 13720  | 13720  |
| 1024  | 64    | 63   | 24533  | 24533  | 24533  |
| 1024  | 128   | 127  | 46160  | 46160  | 46160  |
| 1024  | 256   | 255  | 89414  | 89414  | 89414  |
| 1024  | 512   | 511  | 175921 | 175921 | 175921 |
| 1024  | 1024  | 1023 | 348936 | 348936 | 348936 |

Plot: `sim/htsim-backend/plotting/bcast_baseline.{pdf,png}`.

Three observations:

1. **Linear scaling in leg count.** Incremental time per added leg is
   (348936 − 2444) / 1022 ≈ 339 ns across the full 1024-node range,
   which matches the serialisation delay of one 4096-byte packet at
   100 Gbps (327 ns). The root's uplink is the bottleneck: each of
   the \|G\|-1 legs must be pushed out sequentially.
2. **Topology size is invisible.** The 16-, 128-, and 1024-node
   curves overlap exactly wherever they share an x-axis value.
   Completion time depends on \|G\|, not on where \|G\| lives in the
   fabric. Fat-tree path diversity does not help because the
   bottleneck is at the root's egress, not inside the fabric.
3. **Group-membership variance is negligible.** Across ten random
   groups, min = median = max at every point except the single
   outlier at (128, \|G\|=2) and (1024, \|G\|=2), where an
   intra-rack pairing shaves ~1.1 µs off the cross-pod baseline. This
   isolated low-variance outlier is the cleanest way to see that
   fabric-path effects exist; they are otherwise masked by root-link
   serialisation.

Together these results give the reference curve phase two must beat:
a switch-level multicast mechanism should keep broadcast completion
time approximately constant across group sizes, because a single
packet emitted at the root is replicated inside the fabric instead of
being serialised into \|G\|-1 separate packets. The x-axis (group
size) and y-axis (completion time in ns) are chosen so phase-two
measurements overlay directly on the same plot.

### 9.4 Reproducing

From `sim/htsim-backend/sim/datacenter/connection_matrices/`:

```bash
# from sim/htsim-backend/sim/datacenter/connection_matrices
python3 gen_bcast_sweep.py --out bcast_sweep --nodes 16 128 1024 \
        --group-sizes 2 4 8 16 32 64 128 256 512 1024 --reps 10
python3 run_bcast_sweep.py --manifest bcast_sweep/manifest.txt \
        --htsim ../../cmake-build-debug/htsim_uec \
        --out bcast_sweep/results.csv

# from sim/htsim-backend/plotting
python3 plot_bcast_baseline.py \
        --csv ../sim/datacenter/connection_matrices/bcast_sweep/results.csv \
        --out bcast_baseline --theory
python3 plot_bcast_baseline.py \
        --csv ../sim/datacenter/connection_matrices/bcast_sweep/results.csv \
        --out bcast_baseline_loglog --loglog --theory
```

`--theory` overlays the first-principles model
`T(|G|) = T_fabric + (|G|-1)·t_ser` with default
`T_fabric = 4396.8 ns` (cross-pod K=16 upper bound) and
`t_ser = 332.8 ns` (4160 B at 100 Gbps); both can be overridden via
`--theory-tser-ns` and `--theory-tfabric-ns`.
