# AllGather (MPI_Allgather) — in-network, |G| concurrent broadcasts

Status: **implemented and validated** on `WIP-multicast-htsim-direct`, on
**both** driver paths (`.cm` connection-matrix and the GOAL `coll` binary
bridge). Completes the thesis collective set (Broadcast, Reduce,
Reduce-Scatter, Allreduce, **AllGather**).

## 1. What AllGather does

Each of the `P` group members holds a distinct block of `block_bytes`; after
AllGather **every member holds the concatenation of all `P` blocks**. There is
no arithmetic — it is pure data movement (the dual of Reduce-Scatter).

Example, `P = 4`, blocks `[a b c d]` (member `i` owns block `i`):

```
before:   m0:[a · · ·]  m1:[· b · ·]  m2:[· · c ·]  m3:[· · · d]
after:    every member: [a b c d]
```

Placement in the collective family:

| collective     | result location                                    |
|----------------|----------------------------------------------------|
| Reduce         | full reduced vector at **one** root                |
| Allreduce      | full reduced vector at **every** member            |
| Reduce-Scatter | reduced vector **partitioned**, member `i` ← block `i` |
| **AllGather**  | every member's block **replicated to every member** |

Key identity: **Allreduce = Reduce-Scatter + AllGather.** AllGather is the
bandwidth-optimal second half of a ring/recursive-doubling Allreduce and the
dominant collective in sharded-parameter gathers (FSDP/ZeRO all-gather of
weights before a forward pass). Reference [Khalilov 2024] lists `Allgather`
among the collectives that benefit from in-network **multicast** (it
replicates data — it does **not** reduce it, so it gets the multicast win, not
the aggregation win).

## 2. Design insight: AllGather = |G| concurrent broadcasts

AllGather has **no aggregation**. It is built entirely on the **multicast**
primitive, not the reduce primitive: every member multicasts its own block to
the whole group, simultaneously. `P` overlaid broadcast trees — one rooted at
each member — sharing one group tree.

This is the *dual* of Reduce-Scatter: RS fans **in** and combines `P`
contributions per chunk; AllGather fans **out** and replicates each member's
block to all. Where RS reuses the aggregation half of the data plane,
AllGather reuses the **broadcast** half.

The decisive property is that the multicast data plane is already an
**any-source** primitive (see §3): a broadcast is just "the one member that
happens to inject," so AllGather falls out as "every member injects" with no
new switch state.

## 3. Zero switch / FIB / packet changes

Confirmed against the code:

- `FatTreeSwitch::handle_mcast` (fat_tree_switch.cpp:182-184) is **stateless
  per-packet split-horizon RPF**: `egress_mask = tree_port_mask & ~ingress`
  over an undirected per-group port mask built by `build_mcast_tree`. It
  ignores any root and never consults `root_port_idx_or_neg1` (reduce-only).
  So `P` members injecting concurrently into one `group_id` are each
  RPF-flooded independently — no per-(group, op) switch state, no collision.
- **No self-delivery.** For a source-injected packet the ingress port is
  resolved from `pkt.from` → that host's downlink (fat_tree_switch.cpp:135-147)
  and excluded from the fanout. So member `m` never receives its own block
  back; it receives exactly the `P-1` **other** blocks = `(P-1)*block_bytes`.
  RPF gives this for free — no per-source pruning needed.
- `UecMcastPacket` already carries `from` (replica-preserved, uecpacket.h:295)
  and seeds `_pathid` from `(group_id ^ source_host_id)`, so the `P` concurrent
  sources naturally spread over distinct path hashes.
- The persistent per-`(host, group)` `UecCollectiveSink` and the group tree are
  built for **every** group by `FatTreeTopology::set_up_mcast`; AllGather
  reuses them untouched.

AllGather therefore adds **no new packet type, no new switch logic, and no new
FIB structure** — only driver-level composition of `P` multicast sources
sharing one `group_id` + one `op_flow_id`.

## 4. The one new concern: multi-source byte-count completion

AllGather is the **first collective whose sink sums `P-1` independent peer
streams into one byte counter** (under the shared `op_flow_id`). Broadcast and
Allreduce sinks each absorb a *single* logical stream; Reduce-Scatter sinks
await *one* aggregated block. This exposed a latent accounting bug, caught by
the adversarial review (§9):

`UecCollectiveSink::receivePacket` counted **wire** bytes
(`pkt.size() = payload + acksize`, 64 B/packet) against a `expected_bytes`
that every caller registers in **payload** units. For a single stream the
64 B/packet surplus is harmless (it can shift completion within one sub-packet).
But across `P-1` distinct peer streams the surplus accumulates and, once
`(P-2)·acksize ≥ MTU` (e.g. `P ≥ 34` at MTU 2048), crosses `expected_bytes`
**before the last peer's block arrives** — a premature, silently-wrong
completion in a timing simulator.

Fix (uec_collectives.h, `receivePacket`): **count payload, not wire size** —
`bytes_received += pkt.size() - acksize`. This makes the byte count match the
payload-unit `expected_bytes` exactly, and also tightens
Bcast/Reduce/Allreduce completion to the true last byte (their validated
small-message timings are unchanged — verified, §8).

## 5. Two driver paths

Both paths reduce AllGather to "`P` `UecBcastSrcMcast` sources sharing
`group_id` + `op_flow_id`, each emitting its `block_bytes` block; every
member's sink registered (via `register_mcast_op`) for `(P-1)*block_bytes`;
one `BarrierTrigger` of count `P`; `ReduceCompletionRecorder` label
`ALLGATHER`." Each source `from = m` so RPF excludes self-delivery.

**Convention** (mirrors Reduce-Scatter in both paths): the `.cm`/trace `size`
is the **full gathered vector**; `block_bytes = size / |G|` is the per-member
contribution and must be a multiple of the MTU. So AllGather at a given `size`
is directly comparable to Reduce-Scatter and Allreduce at the same `size`.

- **`.cm` path:** `start_allgather <time>` keyword (`is_allgather` flag);
  `main_uec.cpp` `if (crt->is_allgather)` branch (mirrors the `reduce_scatter`
  branch but with mcast sinks + `UecBcastSrcMcast` sources).
- **GOAL `coll` path:** `coll allgather <size>b <group> <instance> <root>` in
  the `txt2bin` re2c lexer (`AllgatherOp` → `OPTYPE_ALLGATHER = 14`),
  `GetExecutableNodes` → `OP_ALLGATHER = 14` (`OP_COLL_DONE` bumped to 15),
  dispatch case + `launch_collective` branch in `logsim-interface.cpp`. The
  same htsim sink/source machinery as the `.cm` path.

## 6. Completion semantics (and why completion proves correctness)

Each member's sink expects `(P-1)*block_bytes` under the shared `op_flow_id`;
all `P` sinks feed a `BarrierTrigger` of count `P`. The barrier — and thus the
`ALLGATHER_COMPLETE` line — fires only when **every** member has received the
`P-1` other blocks. A mis-routing (e.g. a member's block reaching only a
subset) would leave some sink short and produce no completion line. Completion
is therefore a correctness certificate, not just a liveness signal — and after
the §4 fix it fires on the true last byte, not early.

## 7. Files changed

| File | Change |
|------|--------|
| `datacenter/connection_matrix.h` | `bool is_allgather` on `struct connection` |
| `datacenter/connection_matrix.cpp` | init flag; parse `start_allgather <time>` |
| `datacenter/main_uec.cpp` | `is_allgather` driver branch (P mcast sinks + P-count barrier + P sources) |
| `uec_collectives.h` | sink counts **payload** not wire size (multi-source completion fix) |
| `LogGOPSim/txt2bin.re` | `AllgatherOp` enum + encoder + `allgather` keyword (regenerated `lgs/txt2bin.cpp` via re2c 4.5.1) |
| `lgs/Parser.hpp` | `OPTYPE_ALLGATHER = 14` + `GetExecutableNodes` arm |
| `lgs/logsim.h`, `lgs/LogGOPSim.hpp` | `OP_ALLGATHER = 14`; `OP_COLL_DONE` → 15 |
| `datacenter/logsim-interface.cpp` | dispatch case + `launch_collective` AllGather branch (label, block_bytes, mcast sinks, mcast sources) |
| `datacenter/connection_matrices/allgather_test.cm` | 4-member group `{0,4,8,12}`, size 16384 |
| `tests/main_uec_collective_test.cpp` | `allgather_accumulates_peer_blocks` unit test (guards the §4 fix) |

No new packet type, no new switch logic, no new FIB structure.

## 8. Validation

16-host K=4 fat tree, 100 Gbps, `ecmp_host`.

| case | path | duration |
|------|------|----------|
| AllGather, group `{0,4,8,12}`, size 16384 (block 4096) | `.cm` | 4258 ns |
| AllGather, `|G|=8`, size 32768 (block 4096) | `.cm` | 5272 ns |
| AllGather, `|G|=16`, size 65536 (block 4096) | `.cm` | 7299 ns |
| AllGather, `|G|=16`, size 65536 | GOAL `coll` | **7299 ns** (== `.cm`) |
| Reduce-Scatter (dual), size 16384 — context | `.cm` | 4596 ns |
| Allreduce — regression, unchanged | `.cm` | 3582 ns |
| Broadcast — regression, unchanged | `.cm` | 3582 ns |

- **Cross-path identity:** the GOAL `|G|=16` completion (7299 ns) is byte-for-
  byte identical to the `.cm` `|G|=16` completion — both drive the same htsim
  machinery, proving the two front-ends are consistent.
- **Dual comparison:** AllGather (7299 ns) beats its dual Reduce-Scatter
  (8651 ns at `|G|=16`, size 65536) — pure replication has no per-chunk fan-in
  barrier.
- **Scaling** `4258 → 5272 → 7299 ns` for `|G| = 4 → 8 → 16` reflects the
  `(P-1)·block` per-member **ingress** floor (§10).
- **Seed-invariant** across seeds 1/2/7.
- **No regression** to Bcast/Reduce-Scatter/Allreduce after the shared sink fix
  (3582/4596/3582 ns unchanged).
- Unit tests 5/5, including the multi-source completion guard.

## 9. Adversarial review

A multi-agent review (dimension finders + skeptical per-finding verifiers)
raised 14 findings; 7 confirmed real, collapsing to three distinct bugs +
one nit:

- **A — acksize over-count (in-contract, fixed):** the wire-vs-payload counting
  bug of §4. Bites valid MTU-aligned input at large `|G|`. Fixed in the shared
  sink; guarded by the strengthened unit test.
- **C — `size < |G|` (`block_bytes == 0`) fabric flood (fixed):** AllGather
  uniquely guarded `setFlowSize` on the (truncatable) `block_bytes` rather than
  on `size`, so a degenerate `size < |G|` left the source's default flow size
  in place → a 934-MTU-per-source flood + instant false completion. Fixed by
  setting the flow size unconditionally (`setFlowSize(0)` emits nothing) on
  both paths. Verified: `size = 2, |G| = 4` now yields 0 completions and no
  flood.
- **B — sub-MTU block (out of contract):** a `block_bytes < MTU` block
  over-sends a full MTU and completes early. This violates the documented
  "block a multiple of MTU" precondition; per house style (and identically to
  Reduce-Scatter) the precondition is documented, not defensively enforced.
- **D — GOAL `|G| < 2` guard (nit, pre-existing):** the GOAL path relies on a
  debug-only `assert` for sub-2-member groups where the `.cm` path has an
  explicit `cerr`+`exit`. Pre-existing and shared by all GOAL collectives; left
  as future hardening.

## 10. Performance: the ingress-bound crossover

AllGather is the collective where INC does **not** beat endpoint algorithms on
bulk bandwidth. Every member must **ingest** `(P-1)·block` bytes through its
one NIC — an irreducible floor that ring/recursive-doubling already saturate.
INC multicast reduces NIC **egress** to one block (vs `(P-1)·block` for ring),
but egress was not the bottleneck, so large-message completion **ties** ring.
INC wins on (a) **small-message latency** (one up-down traversal vs `P-1`
serial ring steps), (b) **fabric footprint** (each block crosses each link
once — bandwidth-optimal — vs ring's repeated spine traversals), and
(c) freeing host egress. This is the crossover the thesis maps: INC AllGather
is latency- and footprint-optimal but ingress-bandwidth-tied — the opposite of
Allreduce, where reduction shrinks the data and INC genuinely halves traffic.

## 11. Limitations / future work

- **Uneven blocks** (`size` not divisible by `|G|`, MPI `recvcounts`) are not
  modelled; equal MTU-aligned blocks are assumed (same scope as Reduce-Scatter).
- **Sub-MTU blocks** (Bug B) and **GOAL `|G|<2`** (Bug D) are documented
  preconditions, not enforced (house style: trust inputs). A future `block %
  MTU` validation at dispatch would turn out-of-contract inputs into loud
  rejections.
- **RS + AllGather Allreduce:** with both halves now in-network, a
  Reduce-Scatter → AllGather decomposition gives a bandwidth-optimal Allreduce
  to compare against the existing apex turn-around variant.
