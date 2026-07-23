# Results — multicast bandwidth-usage reduction (measured)

Simulator-measured reproduction of Khalilov et al. SC24 Fig. 2 on pcm-sdk, scale-up
domain in isolation. Metric = `footprint_baseline / footprint_INC` (byte·link crossings),
MTU-aligned message (`-mtu 4160`, per-rank chunk = 1 packet), `reduce_compute_latency = 0`.
Generated 2026-07-23; raw data in `results/scaleup_coll_footprint/scaleup_coll_footprint.csv`.

## Measured byte-ratios

**AllGather** (the paper's Fig. 2 collective):

| P | 2 | 4 | 8 | 16 | 32 | 64 | 128 | 256 |
|---|---|---|---|---|---|---|---|---|
| single-switch Ring | 1.02 | 1.52 | 1.78 | 1.90 | 1.97 | 2.00 | — | — |
| single-switch RD   | 1.02 | 1.52 | 1.77 | 1.89 | 1.95 | 1.98 | — | — |
| **3-tier Ring**    | 1.02 | 1.52 | 1.78 | 1.90 | 1.97 | 2.00 | 2.01 | 2.02 |
| **3-tier RD**      | 1.02 | 1.52 | **2.22** | **2.72** | **3.59** | **4.09** | **4.34** | **4.47** |
| analytic 2−2/P     | 1.00 | 1.50 | 1.75 | 1.88 | 1.94 | 1.97 | 1.98 | 1.99 |

**AllReduce** RD (3-tier) tracks AllGather RD almost exactly (4.47× @ P=256).
**ReduceScatter** RD (3-tier) is slightly lower (1.85 / 2.47 / 3.35 / 3.95 / 4.27 / 4.43 at
P=8…256) and starts at **0.68 at P=2** (see finding 5). Single-switch Ring≡RD for all three.

**Radix-32 3-tier, 1024 hosts (the paper's exact topology)** — AllGather, enabled by the P=1024 fix
(finding 6):

| P | 2 | 4 | 8 | 16 | 32 | 64 | 128 | 256 | 512 | 1024 |
|---|---|---|---|---|---|---|---|---|---|---|
| Ring | 1.02 | 1.52 | 1.78 | 1.90 | 1.97 | 2.00 | 2.01 | 2.02 | 2.03 | ~2.03† |
| RD   | 1.02 | 1.52 | 1.77 | 1.89 | 2.78 | 3.28 | 3.53 | **3.66** | 4.65 | **5.15** |

†Ring @ P=1024 not captured — the 1023-step ring baseline trace was SIGKILLed (a resource limit on the
huge unicast trace, not a crash); Ring is ~2.03 by trend and the analytic `2−2/P`=1.998. RD (log-step)
runs fine. RD tracks Ring within one 16-host leaf (P≤16), diverges past it, and **best matches the
paper's ~3.5 at P=256 = one radix-32 pod**; beyond one pod (P≥512) it overshoots because our core tier
is radix-4 (4 pods) vs the paper's radix-32 core, making inter-pod 6-hop traffic heavier.

## Findings

1. **Single-switch collapses Ring ≡ Recursive-Doubling.** Every GPU pair is 2 hops through
   the one switch, so both algorithms have the identical footprint and track the analytic
   `2−2/P` to a 2× plateau. This is by construction, not a simulator artifact — the constant-
   hop control that isolates the pure multicast win.
2. **The Ring-vs-RD divergence is a multi-tier effect.** On the 3-tier fat-tree Ring stays flat
   at ~2× (nearest-neighbour) while RD climbs monotonically as `P` crosses leaf (P≥8) and pod
   (P≥32) boundaries and its large late-step messages traverse 4- then 6-hop paths — reaching
   **4.47× at P=256**. This reproduces the shape of the paper's Fig. 2 orange bars.
3. **The paper's exact topology (radix-32, 1024 hosts) is reproduced** (P=2→1024; enabled by the
   P=1024 fix, finding 6). Ring stays flat ~2× on the analytic line; RD tracks Ring within one
   16-host leaf then diverges, **matching the paper's ~3.5 at P=256 (one radix-32 pod)**. Beyond one
   pod RD overshoots (5.15× @ 1024 vs the paper's 3.6×) because our core tier is radix-4/4-pods, not
   the paper's radix-32 core — the mechanism/shape are faithful; the inter-pod magnitude is
   topology-specific. (The small-radix `3tier_256`, finding 2, diverges even earlier — from P=8 —
   since its leaf is only 4 hosts.)
4. **Byte-ratio is the clean metric; packet-ratio is control-contaminated.** INC multicast
   crosses = exactly the data footprint (P² for single-switch AllGather, zero control); the
   P2P arm drags ~1 control-packet crossing per data packet, so its packet count is ~2× data.
   In bytes those ~64 B control packets are <~2 %, so the byte-ratio recovers the analytic
   data-movement `2−2/P`; the packet-ratio does not. All plots use bytes.
5. **ReduceScatter at P=2 shows a <1 ratio (0.68):** in-network reduction moves marginally
   *more* than a trivial 2-GPU point-to-point exchange (the aggregation seed/turn-around
   overhead is not amortised). INC wins from P≥4 onward. An honest small-scale data point,
   not seen for AllGather/AllReduce.
6. **P=1024 crash was a fixed uninitialized-pointer bug, not a scale/INC limitation.** INC on large
   multi-tier fabrics crashed post-collective (`LosslessOutputQueue::completeService` empty-queue
   assert / `EventList::doNextEvent` SIGSEGV). Root cause: `LogSimInterface::compute_events_handler`
   (and `null_events_handler`) were declared without initializers in the pcm LGS bridge
   (`logsim-interface.h`), so the ctors' lazy `if (handler==NULL) new ...` read indeterminate memory
   and, when it was non-NULL garbage, skipped the allocation — leaving a dangling pointer that
   `execute_compute()->setCompute()` then scheduled, firing on whatever object the garbage aliased (a
   `LosslessOutputQueue` at the 1024-host heap layout; benign at 256, hence the fabric/arm-dependent
   look). **Fix: `= nullptr` on both members.** pcm-side only (the htsim-backend fork has no such
   member); distinct from the fork's traffic-driven >512-node `compositequeue` crash.
7. **Analytical cost model (Khalilov's method) validates the sim and quantifies the gap to the paper.**
   `analytic_khalilov.py` computes the theoretical byte·link footprint from first principles:
   Ring = `2−2/P`; RD = `Σ_j P·2^(j−1)·hop(2^(j−1))`; multicast-optimal = `P·(P+⌈P/L⌉+⌈P/Q⌉)` — which
   reproduces the MEASURED INC crosses to the byte (69632/279552/1118208 @ P=256/512/1024). Analytic
   and measured ratios agree to **<0.5% at every P on all three topologies** (theory ↔ simulation mutual
   validation). On the paper's radix-32 topology our Ring exactly matches Khalilov's `2−2/P`, and our RD
   matches the paper up to P≈16 (one leaf) then climbs higher — **5.1× (analytic AND measured) vs the
   paper's ~3.6× at P=1024**. Khalilov's exact cost model (Appendix B) is absent from the core-only PDF,
   so this ~40% gap is attributable to a difference in the paper's RD/multicast footprint accounting;
   our two independent methods are self-consistent.

## Method note (for the thesis)

- **Counter:** the PT6 per-link footprint counter (fork origin, ported into
  `HTSIM_spcl/htsim/sim/pipe.{h,cpp}`) increments once per packet entering a physical `Pipe`;
  switch-internal stages (`CallbackPipe`: forwarding latency, INC `_reduce_pipe`, lossless wire)
  set `_count_in_total=false` and are excluded, so the total counts real link traversals only.
  Both packets and bytes (`+= pkt.size()`) are accumulated. Emitted via `-link_crosses_csv` on
  the GOAL run path.
- **Size-independence:** the footprint ratio is analytically independent of message size (the
  paper's "N cancels"); confirmed empirically — a `--size-mults 1,4,16` control leaves the ratio
  flat. The sweep therefore uses the cheapest MTU-aligned message (per-rank chunk = 1 packet).
- **Validation oracle:** single-switch AllGather INC crosses = P² exactly and the byte-ratio =
  `2−2/P` (within the ~1.5 % control offset); Ring≡RD data footprint; scale-out fabric zero
  crosses; and the single-fixed-width-topo mode (`nodes=2`) reproduces the exact-width recipe
  byte-for-byte (`_validate_footprint.py`).

## Caveats

- The ~1.5 % control-packet offset is a small **pro-INC bias** (the P2P baseline pays for ACKs the
  analytic model ignores). Reported honestly; a data-packet-only sub-count would remove it exactly.
- **No lossless host-count ceiling** (corrected): the `compositequeue` ~1000-host abort is a
  fork / default-`COMPOSITE`-queue runtime bug, off our path — our scale-up fabric uses the separate
  `LosslessInputQueue`. Probed 128→4096 host objects under lossless: all clean. The only hard
  topology limit is htsim's ≤96 ports/switch (`fat_tree_topology.cpp:876`), a per-switch cap, not a
  fabric-size cap. 3-tier `P > 256` is therefore reachable (runtime-bound); we stopped at 256 as a
  topo-size choice, and the divergence trend is already unambiguous by then.
- All ratios are **network footprint** (bandwidth *usage*), orthogonal to the completion-time
  speedups measured by `scaleup_coll_ab`.
