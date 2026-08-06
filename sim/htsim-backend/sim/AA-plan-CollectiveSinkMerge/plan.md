# Collective-sink merge: one persistent endpoint per (host, group)

Status: **implemented and validated** on `WIP-multicast-htsim-direct`.
Merges `UecMcastSink` + `UecReduceSink` into the now-concrete
`UecCollectiveSink` — one persistent sink per (host, group) with two
kind-specific per-operation state maps.

## 1. Motivation

The per-flow sink pattern is an inheritance from the ACK-based unicast
transport, where `UecSink` *must* be per-flow: it holds per-flow protocol
state (cumulative-ACK/seqno tracking, ACK/NACK generation, the pull/credit
channel, the `_src` back-pointer). The collective endpoints are ACK-less by
design, so none of that state exists; their entire per-op state is
`OpState{bytes_received, expected_bytes, end_trigger, completed}`.

Phase 2 already broke from the pattern for multicast — forced to, because
`set_up_mcast` bakes the sink pointer into the switches'
`INCFibEntry::leaf_routes`, so the mcast sink had to be persistent
per-(host, group). The reduce sink, however, kept the per-op allocation
(`new UecReduceSink` per operation in three driver branches) purely by
pattern inertia: nothing structural required it, since the reduce descent
rides the regular FIB via a per-op `addHostPort`. This asymmetry — mcast
sinks persistent, reduce sinks per-op — had no justification once stated.

## 2. Design

One concrete class, `UecCollectiveSink` (uec_collectives.h), owned by the
topology in `FatTreeTopology::_collective_sinks` (renamed from
`_mcast_sinks`), created in `set_up_mcast` for every member of every group:

```
UecCollectiveSink (host, group)          # THE collective endpoint
├── _op_state_mcast  : flow_id → OpState # bcast descents, Allreduce
│                                        #   turn-around descents (UEC_MCAST)
└── _op_state_reduce : flow_id → OpState # rooted-Reduce results,
                                         #   Reduce-Scatter blocks (UEC_REDUCE)
```

`receivePacket` dispatches by packet kind to the matching map, then by the
packet's flow id to the op's `OpState`; byte counting, complete-exactly-once,
and trigger firing are unchanged from the old shell. The
`accepts_packet_type` / `process_body` virtuals are gone (the two former
subclass implementations of `process_body` were literally identical).

**Two maps rather than one** preserve the former two-class semantics
exactly: a packet can only ever count against a registration of its own
kind. (Flow ids are globally unique per op, so the maps can never
legitimately share a key — the split is purely the kind filter, now
expressed as data instead of subclassing.)

Driver registration becomes uniform across all five collective branches:
`top->get_collective_sink(m, g)` + `register_mcast_op(...)` or
`register_reduce_op(...)`. The reduce/RS/AR-RB branches no longer allocate
sinks; the per-op `addHostPort(host, op_flow_id, sink)` registration stays
(host routes are keyed `(addr, flow_id)`) but now points at the shared
persistent sink.

## 3. Intentionally NOT changed (deferred stage 2)

Delivery mechanics are untouched: mcast descents terminate via the baked
`leaf_routes`; reduce descents terminate via the per-op host route. A
follow-up ("stage 2") could unify them: descending reduce results are
always addressed to group members, and every member's ToR already holds a
baked leaf route ending at this very sink — so the destination ToR could
deliver descending `UEC_REDUCE` via the FIB entry's leaf route, eliminating
the per-op `addHostPort` entirely. End state: switch state purely
per-group, endpoint state purely per-op. Deferred to land with the ATLAHS
bridge, where per-op switch-FIB growth over long traces starts to matter.

## 4. Implementation summary

| File | Change |
|------|--------|
| `uec_collectives.h` | `UecCollectiveSink` made concrete (two maps, kind dispatch); `UecMcastSink`, `UecReduceSink` deleted |
| `uec.h` | `friend class UecMcastSink/UecReduceSink` → `friend class UecCollectiveSink` (friendship grants `_nodename` access) |
| `fat_tree_topology.{h,cpp}` | `_mcast_sinks`/`get_mcast_sink` → `_collective_sinks`/`get_collective_sink`; creates `UecCollectiveSink` |
| `fat_tree_switch.{h,cpp}` | `addMcastPort` parameter type updated |
| `datacenter/main_uec.cpp` | five branches fetch the persistent sink + `register_{mcast,reduce}_op`; reduce branch gained the `group.size() < 2` guard (consistency with allreduce/RS — see §6) |
| `inc_fib.h` | comment |
| `tests/main_uec_collective_test.cpp` | rewritten for the concrete class; **new `kind_isolation` test** (4 tests) |
| `tests/main_uec_mcast_sink_test.cpp` | rewritten against the merged class (3 tests) |
| `connection_matrices/concurrent_collectives_test.cm` | **new**: 5 concurrent ops on ONE group |

No switch forwarding logic, packet formats, or FIB structures changed.

## 5. Validation

16-host K=4 fat tree, 100 Gbps. **Every regression number identical to
pre-merge** (behavior-neutral refactor):

| case | composite | lossless (PFC) |
|------|-----------|----------------|
| bcast baseline | 4596 / 5272 ns | — |
| bcast mcast | 3582 ns | 3582 ns |
| reduce | 3582 ns | 3582 ns |
| allreduce apex / reduce+bcast | 3582 / 7165 ns | 3582 ns |
| reduce-scatter multi-pod / same-ToR | 4596 / 3458 ns | 4596 / 3458 ns |

**New concurrent stress** (`concurrent_collectives_test.cm`): 2×allreduce +
reduce + reduce-scatter + bcast, all at t=0 on the SAME group — every
member's single sink holds live entries in BOTH maps at once. 5/5
completions, composite and lossless, no asserts/overflow; staggered
completion times (3582→5948 ns) reflect honest tree contention.

Unit tests: 4/4 collective (incl. new `kind_isolation`), 3/3 mcast-sink,
3/3 inc-fib, 6/6 mcast-packet.

## 6. Review findings (adversarial pass)

- **Fixed:** the rooted-Reduce branch lacked the `group.size() < 2` guard
  that allreduce/RS have. Not a regression (a 1-member-group reduce also
  crashed before, at `handle_reduce`'s no-FIB-entry assert, since
  `set_up_mcast` skips groups < 2), but post-merge the failure became a
  misleading setup assert. Guard added for consistency and a clear error.
- **Refuted:** OpState default-member-init concern (build is `-std=c++11`);
  hot-path map-pointer indirection (replaces two virtual calls — net win);
  setup-time `std::map` lookups (setup-only; replaces a heap allocation).
- **Noted, deferred:** completed `OpState` entries accumulate over very
  long traces (strictly better than the old per-op sink leak; erase-on-
  completion is a one-liner if bridge traces make it matter);
  `set_up_mcast`/`addMcastPort` names now serve all collectives (naming
  predates this change — it has fed the reduce ascent since phase 3; rename
  to `set_up_collectives` is a candidate cosmetic follow-up); the five
  driver branches repeat the fetch+assert+register idiom (matches the
  file's explicit per-branch wiring style).

## 7. Why this matters for the bridge

A GOAL trace creates a communicator once → one sink per (member, group);
thousands of trace-driven collective ops are then pure `register_*_op`
calls — no allocation churn, no per-op endpoint objects. The endpoint
lifecycle now matches the communicator lifecycle, which is the natural
shape for the ATLAHS/GOAL integration.
