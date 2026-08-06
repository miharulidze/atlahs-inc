# Plan — In-Network Aggregation: Reduce + Allreduce (Phase 3)

**Status:** Allreduce AND rooted Reduce (single-MTU) IMPLEMENTED + validated
(§11). Decisions: Reduce+Allreduce; single-MTU
floor first (multi-MTU §9); timing/bytes only; Allreduce = apex-switch
turn-around (§4, no root host); build incast baseline (Q-A); mirror-bcast `.cm`
tokens (Q-B); defer `op_seq_id` (Q-C).
**Branch target:** `WIP-multicast-htsim-direct` (continues from the lossless work, commit 81b8f25)
**Scope:** the in-network aggregation primitive (Reduce) and Allreduce
(Reduce composed with the existing phase-2 multicast). Reduce-Scatter and
Allgather are out of scope. Multi-MTU chunking is deferred to a follow-up (§9).

---

## 1. Objective

Phase 2 made broadcast cheap by replicating *in the fabric* (one root emission
fans out along a pre-installed tree). Aggregation is the mirror image: instead
of every group member sending its contribution all the way to the root (an
incast that serialises `|G|` arrivals at the root NIC), each switch **combines
its children's contributions into one upward packet**, so the root receives a
single packet. This is the fan-**in** dual of phase-2 fan-**out**.

Per the agreed scoping answers:

- **Target:** *Reduce* (the aggregation primitive, many → one root) **+
  Allreduce** (Reduce up to the root, then reuse the phase-2 multicast to
  broadcast the result back down).
- **Message size:** *single-MTU floor first* — one packet per contribution, a
  per-operation fan-in barrier; multi-MTU chunking is a scoped follow-up (§9).
- **Reduction semantics:** *timing/bytes only* — model the fan-in barrier and
  the `k`→1 byte collapse (k inputs of size S → one size-S packet upward), with
  **no payload arithmetic**. Tests assert the *barrier* is correct (the switch
  waited for exactly the right child set) without computing values.

This mirrors the phase-2 cadence (single-MTU floor → message-size sweep) and
keeps the thesis narrative consistent. It also exercises the Task-1 lossless
machinery from the fan-in side (§5.4).

---

## 2. Reduce is the mirror of multicast

| | Multicast (phase 2) | Reduce (this plan) |
|---|---|---|
| Direction | 1-in / k-out (fan-out) | k-in / 1-out (fan-in) |
| Source(s) | the root host only | every group member |
| Sink(s) | every member | the root host only |
| Switch action | replicate to tree ports (`handle_mcast`) | **hold until all children arrive, then emit one packet toward root** (`handle_reduce`) |
| Tree | `INCFibEntry::tree_port_mask` | same mask; traversed toward `root_port_idx_or_neg1` |
| Completion gate | last leaf receives | last child arrives at each switch → root receives the single combined packet |

The per-group tree state (`inc_fib.h`) is reused unchanged; reduce only needs
the **toward-root port** (`root_port_idx_or_neg1`, already reserved for this in
phase 2) and, new, the **expected child count** per switch.

---

## 3. The fan-in barrier (core mechanism)

At each switch, for a given operation, the set of *downstream* tree ports
(= `tree_port_mask` minus the toward-root port) are the children that must
contribute. The switch keeps a small per-operation arrival record:

```
key   = (group_id, op_seq_id)                 // single-MTU: one barrier per op
state = { expected_children, arrived_count }  // expected fixed at setup time
```

- A `UEC_REDUCE` packet arrives from a child port → increment `arrived_count`
  and **absorb** (free) the packet.
- When `arrived_count < expected_children`: nothing is forwarded (the
  contribution is folded in — timing/bytes-only, so "folding" is just the
  count, no arithmetic).
- When `arrived_count == expected_children` (last child): emit **one** fresh
  `UecReducePacket` out the toward-root port (to the parent switch, or — at the
  root switch — down the leaf route to the root host's `UecReduceSink`), then
  erase the barrier state.

`expected_children` per switch is computed at setup from the tree: for a leaf
TOR it is the number of local member hosts; for an interior switch it is the
number of downstream tree ports (child switches). This is the structural mirror
of `handle_mcast`'s egress fan-out, and reuses the same `_packets`-style
per-switch map keyed by operation instead of by packet pointer.

**Timing property (the point of the primitive):** the upward emission is gated
by the *last* (slowest) child, so reduce completion tracks the critical path
through the tree — `O(depth)` — rather than the `O(|G|)` serialised incast at
the root NIC. This is the fan-in analogue of phase-2's constant-in-`|G|`
broadcast result.

---

## 4. Allreduce = apex-switch turn-around (no root host)

Allreduce is semantically **root-agnostic and symmetric**: every member
contributes and every member receives the same result; there is no
distinguished participant. We therefore implement it the SHARP/in-network way —
**the reduction turns around at the tree apex**, not at a host.

The apex is the topmost switch of the group's tree (the chosen core for a
multi-pod group; the agg for a single-pod group; the TOR for a single-TOR
group) — the switch with no toward-root port. It is flagged by
`root_port_idx_or_neg1 == -1`. Mechanism:

- **Below the apex:** ordinary reduce fan-in (§3) — wait for all children, emit
  one `UEC_REDUCE` packet *up* the root port.
- **At the apex** (`root_port == -1`): the fan-in completes, and instead of
  forwarding up (there is nowhere up), the switch **turns the result around and
  multicasts it down** the same tree — i.e. it hands off to the existing
  `handle_mcast` fan-out (`UEC_MCAST` replicas out all tree ports). Every
  downstream switch then runs the unchanged phase-2 multicast machinery.

So Allreduce = reduce-up (new) until the apex, then phase-2 multicast-down
(reused verbatim). No root host, no host round-trip; the only new coupling is
`handle_reduce`→`handle_mcast` at the apex. This honours the symmetry: the
"root" is just the physical point where fan-in becomes fan-out.

`set_up_allreduce` installs the tree with `root_port` pointing toward the apex
at every switch and `root_port == -1` at the apex itself (the turn-around
signal). `set_up_reduce` (for the rooted Reduce primitive, §3) instead points
every switch's `root_port` toward the genuine root host, which has a real
semantic root. One collective type per group in this milestone (consistent with
deferring `op_seq_id`/concurrency, §10 Q-C); mixed Reduce+Allreduce on one group
would need op-keyed FIB entries — out of scope here.

**Planned comparison variant (later):** the alternative rooted composition
(Reduce to an arbitrary root host, then trigger-chained phase-2 broadcast from
that host — a free driver-level chain via `recv_done_trigger`/`TriggerRelay`)
will be added as a second Allreduce *mode* so the evaluation can plot
apex-turn-around vs. reduce+broadcast and quantify the host round-trip cost.

---

## 5. Components & implementation steps

Ordered so the tree is buildable/testable after each step.

### 5.1 Packet type — `UecReducePacket` (peer of `UecMcastPacket`)
- New enum `UEC_REDUCE` alongside `UEC_MCAST`.
- `UecReducePacket : public Packet` carrying `_group_id`, `_op_seq_id`,
  `_seqno`, wire size — a near-copy of `UecMcastPacket` (uecpacket.h:240+). A
  source-side `newpkt` factory; no `newpkt_replica` (reduce never replicates).
- `_ingressqueue = NULL` in the factory (same PacketDB-recycle hygiene as the
  Task-1 fix).

### 5.2 FIB — reduce fields in `INCFibEntry` (inc_fib.h)
- `root_port_idx_or_neg1` already exists; populate it in `set_up_reduce`.
- Add `int expected_children` (downstream tree-port count at this switch).

### 5.3 Topology — `set_up_reduce` (fat_tree_topology.cpp, peer of `set_up_mcast`)
- For each group + reduction root, build the reduce tree (reuse
  `build_mcast_tree`'s tree shape; compute `root_port` = the port stepping
  toward the root at each switch, and `expected_children`).
- Install per-switch `INCFibEntry` (or extend the existing one if the group
  already has a multicast entry — Allreduce shares the group).
- Create the root host's persistent `UecReduceSink`.

### 5.4 Switch — `handle_reduce` aggregation engine (fat_tree_switch.cpp)
- The §3 barrier. Mirror of `handle_mcast` (fat_tree_switch.cpp:155): a
  `UEC_REDUCE` arm in `FatTreeSwitch::receivePacket` dispatches to it.
- **Lossless (PFC) accounting (reuses Task 1):** an absorbed child packet never
  reaches an egress queue, so under `lossless_input` its ingress
  `LosslessInputQueue` charge must be released on absorption — call
  `release_bytes(pkt.size())` on `pkt.peek_ingress_queue()` (the exact dual of
  the multicast original-free release). The single emitted combined packet then
  picks up fresh ingress accounting normally as it climbs. This is why the
  refcount-release-once design generalises to fan-in, as argued in
  `AA-plan-Lossless/plan.md` §4.

### 5.5 Endpoints — `UecReduceSrc` / `UecReduceSink` (uec_bcast.h / uec_collective.h)
- `UecReduceSrc : UecCollectiveSrc` — one per group member; `emit_once()` sends
  one `UecReducePacket` toward the root (route to the local TOR, then FIB-routed
  up by `group_id`). ACK-less, single-shot — same shape as `UecBcastSrcMcast`.
- `UecReduceSink : UecCollectiveSink` — one at the root host; accepts
  `UEC_REDUCE`, counts the single combined arrival, fires the completion
  trigger. Reuses the `UecCollectiveSink` per-op state map.

### 5.6 `.cm` + driver (connection_matrix.cpp, main_uec.cpp)
- Parser: `start_reduce` / `start_allreduce` tokens and `trigger_reduce`
  variants, mirroring `start_bcast` (connection_matrix.cpp:761). Add `is_reduce`
  / `is_allreduce` flags to `struct connection`. Root encoded `ROOT->GRP` as for
  bcast.
- Driver: a reduce branch peer to the `is_bcast` block (main_uec.cpp:887). For
  each member create a `UecReduceSrc`; wire the root `UecReduceSink`'s completion
  to a `ReduceCompletionRecorder` (peer of `BcastCompletionRecorder`). For
  Allreduce, chain the reduce-complete trigger into the existing mcast-emit path.
- A `-coll_mode` selector (or extend `-bcast_mode`) choosing the **baseline**
  (non-INC incast: all members unicast to the root, root NIC serialises `|G|`
  arrivals — the dual of the phase-1 bcast baseline) vs the **inc** (in-network)
  path, so the evaluation has an apples-to-apples comparison. (See open Q-A.)

### 5.7 Tests
- **Barrier correctness:** assert each switch emitted upward exactly once, only
  after all expected children arrived (instrument the arrival record); verify on
  a few topologies/group shapes.
- **Completion:** reduce completion gated by tree critical path; in-network vs
  incast baseline speedup (mirror of the phase-2 table).
- **Lossless:** reduce under `lossless_input`, zero drops, and end-of-sim
  ingress `_queuesize == 0` (no leak — validates the absorption release of §5.4).
- Reuse `-mcast_pin_core` to congest a single core with parallel reduces.

---

## 6. Design decisions & alternatives

| Decision | Chosen | Alternative(s) rejected |
|---|---|---|
| Reduction modelling | Timing/bytes only; barrier asserted in tests | Compute real reductions — pure cost, zero timing impact, not htsim style |
| Message size | Single-MTU floor first | Multi-MTU now — more state (per-chunk barrier, eviction, OOO); deferred to §9 |
| Held-packet PFC accounting | Release-on-absorb (reuse Task-1 `release_bytes`) | Hold-and-refcount — unnecessary at single-MTU (a child packet is either absorbed or, as the combined packet, re-enters accounting fresh) |
| Allreduce | Reduce-up + reuse phase-2 mcast-down, trigger-chained | A bespoke bidirectional engine — needless; composition is free |
| Tree state | Reuse `INCFibEntry` (+ `expected_children`) | A separate reduce FIB — duplicates phase-2 |

---

## 7. PFC / lossless interaction (single-MTU)

At single-MTU a switch holds at most `expected_children - 1` packets briefly
until the last child arrives, then absorbs all and emits one. Occupancy is
small, but the **absorption release** (§5.4) is mandatory under `lossless_input`
or the ingress port leaks charge and latches PAUSED — the same failure class as
the multicast original-free bug, fixed the same way. This is validated by the
end-of-sim zero-occupancy check (§5.7). The richer backpressure story (a reduce
switch holding a *stream* of early chunks while waiting for a straggler child)
appears at multi-MTU (§9), where it meets the Task-1 work head-on.

---

## 8. Validation plan (summary)

1. Build `htsim_uec`; no new warnings.
2. Single-MTU Reduce on small fat trees: correct single arrival at root,
   barrier-correct, completion gated by tree depth.
3. In-network vs incast-baseline reduce: speedup table (mirror tab:phase2-numbers).
4. Allreduce end-to-end: reduce-complete triggers mcast-down; root and all
   members observe completion.
5. Lossless: zero drops + zero ingress leak under `lossless_input`; parallel
   reduces pinned to one core via `-mcast_pin_core`.
6. Thesis: document-alongside — Design §sec:inc-primitives (currently a stub)
   becomes the reduce/aggregation engine; an evaluation subsection peer to
   §sec:eval-phase2.

---

## 9. Multi-MTU chunking (phase 3b) — Step A DONE, Step B remaining

**Step A (per-chunk barrier) — DONE.** The fan-in barrier is keyed by
`(flow_id, seqno)` so each chunk aggregates independently and pipelines up the
tree; sinks complete by byte count (no sink change). Validated: reduce +
allreduce at 262 KB (~128 chunks), composite + lossless-uncongested, and
**concurrent multi-MTU ops** all complete, 0 drops. A recycle bug surfaced and
was fixed: `UecReducePacket::newpkt`/`newpkt_combined` must reset `_descending`
(a freed descending packet recycled into an ascent packet otherwise kept
`_descending=true` and got mis-routed as a unicast by its stale `dst` — only
triggered by concurrent multi-MTU, found via pointer tracing).

**Step B (lossless backpressure for switch-originated streams) — DONE.**
`ReduceFanInCredit` (queue_lossless_input.h), the fan-in dual of
`McastFanoutCredit`: the barrier now HOLDS each arrived child's ingress charge
(queue + bytes) instead of releasing it on arrival, and attaches them to a
credit on the emitted result. The credit releases all held charges once the
result has drained from the egress --- 1 drain for a combined-up / rooted-Reduce
unicast, k drains (shared credit) for the Allreduce apex fan-out. While the
result is stuck behind a paused uplink the charges stay held, so the switch's
ingress stays occupied and pauses the children: backpressure propagates and no
egress queue overflows. Validated: 2-op multi-MTU reduce / allreduce-apex /
reduce+bcast and 4-op bcast, all pinned to one core under lossless, complete
with **0 buffer-overflow warnings** (was 588). Composite path unchanged (no
charges, no credit); the per-chunk credit alloc in lossless mode mirrors the
existing multicast credit. Multi-MTU aggregation is now fully lossless.

### Original sketch:

Realistic tensor sizes make each reduce a *stream* of chunks. The barrier key
becomes `(group_id, op_seq_id, chunk_seqno)`; switches hold per-chunk state,
handle out-of-order chunk arrival across children, pipeline chunks up the tree,
and evict completed-chunk state. This is where a reduce switch buffers early
chunks under PFC backpressure while a straggler child catches up — the fan-in
counterpart of the Task-1 multicast accounting, and the regime where lossless
dynamics for aggregation are actually interesting. Scoped as a separate step on
top of the proven single-MTU engine.

---

## 10. Open questions

- **Q-A (baseline):** build the non-INC **incast baseline** reduce now (all
  members unicast to root; root NIC serialises `|G|` arrivals) so the evaluation
  has the in-network-vs-baseline contrast, as phase-2 had baseline-vs-mcast? Or
  land the in-network reduce alone first and add the baseline later?
- **Q-B (`.cm` tokens / mode flag):** OK to mirror bcast — `start_reduce` /
  `start_allreduce`, `ROOT->GRP` root encoding, and a `-coll_mode`-style selector
  — or do you have a preferred convention?
- **Q-C (concurrent ops):** is `op_seq_id` disambiguation of *concurrent reduces
  on the same group* needed in this milestone, or can the single-MTU floor
  assume one in-flight reduce per group (add `op_seq_id` keying with multi-MTU)?
- **Q-D (Allreduce root):** the reduce root and the subsequent broadcast root are
  the same host — confirm that's the intended Allreduce semantics for the
  evaluation (vs. a rooted-then-rebroadcast-from-a-different-node variant).

---

## 11. Implementation result — Allreduce (single-MTU), validated

### Done
- `UecReducePacket` (`UEC_REDUCE`) + `UecReduceSrc`/`UecReduceSink` +
  `ReduceCompletionRecorder` (uecpacket.*, uec_bcast.*, uec.h friends).
- `INCFibEntry.expected_children`; `set_up_mcast` now also fills the reduce
  ascent (`root_port = node uplink`, `expected_children = tree_ports − uplink?`)
  — one FIB entry serves multicast, the reduce ascent, AND the Allreduce
  descent (the descent tree == the group's multicast tree).
- `McastTreeNode.uplink_port_idx_or_neg1` (−1 ⇒ apex).
- Switch engine: `handle_reduce` fan-in barrier (per-`(group<<32|op)` counter);
  refactored `handle_mcast`'s fan-out into shared `fanout_replicas`. Apex
  (`root_port == −1`) turns around into a downward multicast; non-apex forwards
  one combined packet up. PFC: absorbed contributions release their ingress
  charge on arrival (fan-in dual of the mcast free); switch-originated combined
  / apex packets carry a no-op `McastFanoutCredit{iq=nullptr}` so the egress
  queue has a non-null prev to pair (and `McastFanoutCredit` skips release when
  `iq` is null).
- `.cm` `start_allreduce` (+ `is_reduce`/`is_allreduce` flags); driver
  `is_allreduce` branch (every member is a `UecReduceSrc` ascent source and a
  `UecMcastSink` descent sink; BarrierTrigger over all `|G|` members fires the
  recorder). Root index ignored (symmetric).

### Validated (16-host k=4 fat tree, single-MTU = 2048 B)
- Allreduce completes for |G|=4 multi-pod (3582 ns), |G|=16 (3582 ns —
  **constant in |G|**, the in-network benefit), |G|=4 single-pod (2444 ns,
  apex=agg, fewer hops). 0 drops/asserts, composite and lossless identical.
- No regression: bcast baseline + mcast still pass (composite + lossless).
- Single-core hotspot (`-mcast_pin_core 0`), 4 concurrent single-MTU
  allreduces, lossless, conservative threshold: **4/4 complete, 0 drops**.

### Known gap — multi-MTU (the §9 follow-up, scoped out of this milestone)
Two issues, both inherent to multi-MTU and explicitly deferred:
1. **Barrier semantics.** `handle_reduce` counts total arrivals vs
   `expected_children`; correct only when each member sends exactly one packet.
   Multi-MTU needs a per-`(group, op, chunk_seqno)` barrier.
2. **Switch-origination backpressure.** Combined/apex packets are
   switch-originated with a no-op credit, so under congestion a *stream* of them
   can overrun a downstream-paused egress queue (observed: 262 KB messages,
   pinned core → drops at every threshold — buffer-bound, not threshold-bound).
   Multi-MTU must make switch emission honour the egress pause state (queue the
   combined packet rather than emit into a paused queue). This is the fan-in
   counterpart of the Task-1 lossless work and the substance of §9.

### Rooted Reduce — DONE (FIB down-routing; gdesc approach replaced)
Same ascent as Allreduce; the apex delivers to the single root R as an
**ordinary unicast down the regular FIB** — down-routing in a fat tree is a
deterministic single path (`getNextHop` by `dst`, CORE/AGG/TOR all handled),
so no descent tree is needed. `handle_reduce`'s apex (entry flagged
`reduce_root_or_neg1 = R`) builds one `UecReducePacket::newpkt_downward` (dst=R,
flow=op, `_descending=true`) and routes it via `getNextHop`; transit switches
see the descending flag and fall through to normal routing instead of
re-aggregating. The driver registers R's `UecReduceSink` as a host route
(`addHostPort(R, op, sink)`), so `getHostRoute` resolves it.

Chosen over the earlier synthetic single-member descent group (`gdesc` +
`install_reduce_descent`): the FIB approach is **net −27 lines**, removes the
fragile CORE/AGG/TOR tier-casing, and is **allocation-free per hop** (forwards
one packet vs `gdesc`'s per-hop `newpkt_replica`) — efficiency being a core
htsim property. Matches SHARP/real systems: aggregate up, deliver down the
target's path. Validated at exact parity with the gdesc version: constant in
|G| (3582 ns |G|=4..16), roots in any pod (hosts 0/2/7/12), single-pod 2444 ns,
0 drops composite + lossless, 4 concurrent single-MTU ops drop-free pinned to
one core. Allreduce keeps its multicast descent (delivers to all). Multi-MTU
still deferred (§9).

### Efficiency note
Switch-originated lossless packets (apex fan-out, combined-up) reuse a shared
zero-allocation `NoOpVirtualQueue` sentinel rather than allocating a credit
per packet; `McastFanoutCredit` (which refcounts) is used only where a real
ingress charge must be released. Keeps the hot path allocation-free, which
matters once multi-MTU turns these into streams.
