# Reduce-Scatter (MPI_Reduce_scatter) — in-network, per-block roots

Status: **implemented and validated** on `WIP-multicast-htsim-direct`.
Completes the proposal's required collective set (Broadcast, Reduce,
Reduce-Scatter, Allreduce).

## 1. What Reduce-Scatter does

Reduce-Scatter is **Reduce + Scatter fused**. Every one of the `P` group
members contributes a vector of the same length; the vectors are
element-wise reduced (summed); the reduced vector is then **partitioned into
`P` contiguous blocks and block `i` is delivered to member `i`**. Each member
ends with `1/P` of the fully-reduced result.

Example, `P = 4`, vector = 4 blocks `[w x y z]`:

```
member m's input:  [w_m  x_m  y_m  z_m]

after reduce-scatter:
  member 0 gets  Σ_m w_m        member 1 gets  Σ_m x_m
  member 2 gets  Σ_m y_m        member 3 gets  Σ_m z_m
```

Placement in the collective family:

| collective    | result location                         |
|---------------|-----------------------------------------|
| Reduce        | full reduced vector at **one** root     |
| Allreduce     | full reduced vector at **every** member |
| Reduce-Scatter| reduced vector **partitioned**, member `i` ← block `i` |

Key identity: **Allreduce = Reduce-Scatter + Allgather.** Reduce-Scatter is
the bandwidth-optimal first half of a ring/recursive-halving Allreduce and a
core primitive in sharded-optimizer (FSDP/ZeRO) and tensor-parallel training.
Reference [2] (Hoefler/Khalilov, *Game Changer or Challenge*) lists
`Reduce_scatter` among the collectives that obtain ~2× core-INC bandwidth
savings (it reduces data).

## 2. Design insight: Reduce-Scatter = P concurrent rooted Reduces

The aggregation half is **identical** to rooted Reduce: every member sends its
contribution up the same per-group reduction tree, and the switches combine
per chunk at each level on the way to the apex. The only difference is the
**redistribution**: instead of the apex sending the whole aggregate to one
root (Reduce) or fanning it to all members (Allreduce), the apex sends **each
chunk to the member that owns the block that chunk belongs to**.

Because the existing switch fan-in barrier is keyed by `(flow_id, seqno)` —
i.e. each chunk position reduces in its own independent pipeline — and the
apex's rooted-Reduce path already reads the destination root **off the packet**
(`UecReducePacket::reduce_root`, delivered via the regular down-FIB), the data
plane already does everything Reduce-Scatter needs. The whole operation
reduces to: **stamp each chunk with the root host that owns its block.**

```
owner(byte_offset) = group[ byte_offset / block_bytes ]
block_bytes        = vector_size / P
```

All members run the identical mapping, so every contribution to a given chunk
agrees on that chunk's root; the combined packet inherits it
(`newpkt_combined` copies `_reduce_root`), and the apex unicasts it down to
that owner.

## 3. Zero switch / FIB changes

Confirmed against the code:

- `FatTreeSwitch::handle_reduce` apex branch: `if (root_port_idx < 0 &&
  pkt.reduce_root() >= 0)` → `newpkt_downward(pkt, pkt.reduce_root())` →
  regular down-FIB unicast. Per-chunk roots ⇒ per-block delivery, **no change**.
- `UecReducePacket::newpkt_combined` preserves `_reduce_root` up the tree, so
  the per-chunk root survives the climb to the apex.
- The reduction tree (`root_port_idx_or_neg1`, apex) is built for **every**
  group by `FatTreeTopology::set_up_mcast`; Reduce-Scatter reuses it untouched.
- Host-route delivery is keyed by `(addr, flow_id)` (`addHostRoute`), so the
  `P` per-member reduce sinks can share one `op_flow_id` even when members
  share a ToR — each resolves by destination host.

The `ReduceFanInCredit` PFC accounting is likewise reused unchanged: each
chunk's held ingress charges release when its single combined packet drains.

## 4. The one mechanism change: per-packet root assignment

`UecReduceSrc` gained a Reduce-Scatter mode (`set_reduce_scatter(owners,
block_bytes)`). In `emit_once`, Reduce/Allreduce keep the fixed `_reduce_root`
for every chunk; Reduce-Scatter computes `root = owners[_highest_sent /
block_bytes]` per chunk. `block_bytes` is a multiple of the MTU, so no chunk
straddles a block boundary.

## 5. Modeling decision (single-packet-payload scope)

Reduce-Scatter is the one collective in tension with the proposal's
"single-packet payload" focus: scattering a one-packet buffer over `P > 1`
members would yield sub-packet slivers. We resolve it the natural way — the
**aggregation unit is one packet (one `seqno`); a block is `vector_size / P`
packets (≥ 1)** — so the vector is `P × block_size` and reuses the already-
shipped multi-MTU reduction. The minimal "single packet per block" case is
just `vector_size = P × MTU`. We assume the vector is divisible by `P` and the
block a multiple of the MTU (per house style, malformed `.cm` is not
defensively checked).

## 6. Completion semantics (and why completion proves correctness)

Each member registers a `UecReduceSink` expecting `block_bytes`, all feeding a
`BarrierTrigger` of count `P`; the barrier fires `ReduceCompletionRecorder`
(label `REDUCE_SCATTER`). Because exactly `vector_size = P × block_bytes` bytes
are delivered downward and each of the `P` sinks needs `≥ block_bytes` to
complete, **all-`P`-complete is only achievable if each member received exactly
its own block**. A mis-routing that sent everything to one root would leave the
others stuck at 0 and produce no completion line. Completion is therefore a
correctness certificate, not just a liveness signal.

## 7. Files changed

| File | Change |
|------|--------|
| `connection_matrix.h` | `bool is_reduce_scatter` on `struct connection` |
| `connection_matrix.cpp` | init flag; parse `start_reduce_scatter <time>` |
| `uec_bcast.h` | `UecReduceSrc::set_reduce_scatter(owners, block_bytes)` + members |
| `uec_bcast.cpp` | `UecReduceSrc::emit_once` per-chunk root for RS mode |
| `datacenter/main_uec.cpp` | `is_reduce_scatter` driver branch (P sinks + P-count barrier + per-packet roots) |
| `datacenter/connection_matrices/reduce_scatter_test.cm` | multi-pod group `{0,4,8,12}` |
| `datacenter/connection_matrices/reduce_scatter_samerack.cm` | intra-pod group `{0,1,2,3}` (same-ToR delivery) |

No new packet type, no new switch logic, no new FIB structure.

## 8. Validation

16-host K=4 fat tree, 100 Gbps, vector 16384 B over a 4-member group
(block = 4096 B).

| case | queue | MTU | duration |
|------|-------|-----|----------|
| RS, group `{0,4,8,12}` (multi-pod) | composite | 2048 | 4596 ns |
| RS, group `{0,4,8,12}` | lossless_input (PFC) | 2048 | 4596 ns |
| RS, group `{0,4,8,12}` | composite | 4096 | 5395 ns |
| RS, group `{0,1,2,3}` (intra-pod, same ToR) | composite | 2048 | 3458 ns |
| RS, group `{0,1,2,3}` | lossless_input | 2048 | 3458 ns |
| Reduce (1 block) — context | composite | 2048 | 3582 ns |
| Allreduce — regression, unchanged | composite | 2048 | 3582 ns |

- Multi-packet blocks (MTU 2048 ⇒ 2 packets/block) and single-packet blocks
  (MTU 4096) both correct.
- Seed-invariant (4596 ns across seeds 1/7/42): the reduction tree is pinned to
  a single apex, so ECMP entropy doesn't change the structure.
- Same-ToR group confirms `(addr, flow_id)` sink resolution.
- Lossless matches lossy (no congestion): the per-block `ReduceFanInCredit`
  path is correct, no asserts.
- No regression to Reduce/Allreduce.

## 9. Limitations / future work

- **Uneven blocks** (vector not divisible by `P`, i.e. MPI `recvcounts`) are
  not modelled; we assume equal blocks. Straightforward extension: per-block
  size table instead of a single `block_bytes`.
- **Allgather** (the other half of Allreduce) is not in the required set but is
  cheap given the multicast primitive; `Reduce-Scatter + Allgather` would give
  a bandwidth-optimal Allreduce decomposition to compare against the current
  apex turn-around.
- Bandwidth footprint vs Allreduce: RS delivers `vector_size` total downward
  (one block per member) versus Allreduce's `P × vector_size` (full vector to
  all) — the `1/P` downstream cost that makes RS the bandwidth-optimal half.
