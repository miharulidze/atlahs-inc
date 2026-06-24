# INC-vs-ring AllReduce microbenchmark (scale-up tier)

The controlled form of the per-domain A/B Shuhao proposed (meeting 2026-06-19): one
logical AllReduce rendered two ways and run on a NVLink-class scale-up topology.

- **INC arm** — the TP AllReduce kept *whole*: one first-class `coll` op per rank;
  htsim does in-network reduce + multicast (`-allreduce_mode`-style apex offload).
- **ring arm** — the same AllReduce *decomposed* into a bandwidth-optimal ring of plain
  point-to-point flows: `2(N-1)` steps of `size/N` bytes each (the textbook
  `2(N-1)/N · size` per rank). Switches only forward; no INC.

Both arms are GOAL `.bin` schedules with identical framing (a single 1-unit calc tail so
the `Maximum finishing time at host` oracle == the collective time for both) — see
`make_allreduce_ab.cpp`. This is the same `Host N:` oracle Zhiyi's two-tier harness parses
(`parse_htsim_max_host_time`), so the result composes with Track A.

## How to reproduce

```sh
cd sim/htsim-backend/sim/datacenter
make htsim_uec
g++ -std=c++17 -I.. -I. make_allreduce_ab.cpp -o make_allreduce_ab
cd allreduce_ab
python3 run_allreduce_ab_sweep.py \
  --htsim ../htsim_uec --gen ../make_allreduce_ab \
  --topo ../topologies/scaleup_tree16_3600Gbps.topo --linkspeed 3600000 \
  --group-sizes 2,4,8,16 \
  --msg-sizes 4096,16384,65536,262144,1048576,4194304 \
  --reduce-compute 100 --out results.csv
/usr/bin/python3 plot_allreduce_ab.py --csv results.csv --out allreduce_ab
```

## Topology and the fairness model

- **Topology:** `topologies/scaleup_tree16_3600Gbps.topo` — the validations harness's
  intra-node tier (16 hosts, 2-tier, 3600 Gbps ≈ NVLink, 4 GPUs/leaf) with one change: a
  realistic `Switch_Latency_ns 300` (NVSwitch-class; the validations tier ships 0).
- **Per-hop latency is symmetric.** Both `Downlink_Latency_ns` (500 ns, link/wire) and
  `Switch_Latency_ns` (300 ns, switch processing) live in the topology and are incurred by
  **both** arms, every hop — the ring's p2p flows are FIB-routed through the switch pipe just
  like INC's. Verified: `Switch_Latency_ns = 1000` adds +1000 to INC (~2 hops) and +12000 to
  the ring (~12 serial hops × 1000) — symmetric per hop; the ring just makes more traversals.
  (The CLI `-switch_latency` is *ignored* on the `-topo` path — only the topo value counts —
  which is why setting it has no effect here; do **not** use it to model the shared cost.)
- **INC is charged for its extra work.** `-reduce_compute_latency 100` adds the in-switch
  aggregation ALU cost on the INC reduce path only (the ring does its reduces at endpoints,
  not modelled — standard α-β assumption). So INC is, if anything, *over*-charged: any
  measured speedup is a lower bound on that axis.
- Correctness oracle: every run completed with **0 drops** (24/24 cells clean).

## Results (completion ns; speedup = ring/INC)

(Config: `Downlink_Latency_ns 500` + `Switch_Latency_ns 300` in the topo — both arms;
`-reduce_compute_latency 100` — INC only.)

| \|G\| | 4 KiB | 64 KiB | 256 KiB | 1 MiB | 4 MiB |
|---|---|---|---|---|---|
| 2  | 1417 / 5234  (3.7×)  | 1542 / 5351  (3.5×)  | 1941 / 8366  (4.3×)  | 3539 / 25139  (7.1×)  | 9929 / 92277  (9.3×)  |
| 4  | 1417 / 15702 (11.1×) | 1542 / 15852 (10.3×) | 1941 / 16882 (8.7×)  | 3539 / 57222  (16.2×) | 9929 / 218572 (22.0×) |
| 8  | 3134 / 49505 (15.8×) | 3259 / 49621 (15.2×) | 3658 / 50320 (13.8×) | 5255 / 77475  (14.7×) | 11645 / 285947 (24.6×)|
| 16 | 3134 / 104243 (33.3×)| 3259 / 104243 (32.0×)| 3658 / 104991 (28.7×)| 5255 / 108125 (20.6×) | 11645 / 320026 (27.5×)|

See `allreduce_ab.png`. INC is near-flat across *size* at small messages (a latency floor),
then grows once bandwidth-bound; across `|G|` it is *stepwise*, not flat — it doubles
(1417 → 3134 ns) at the |G|=4→8 boundary, where the group stops fitting under one leaf and
the reduce apex moves up to the root tier. The ring is far higher throughout and grows with
`|G|` (more serial hops).

## How to read the speedup (and its threats to validity)

The speedup has **two regimes** — the contribution is mapping them, not the headline
multiple (cf. the crossover literature):

1. **Latency regime (small messages, left of the plot).** The ring pays `2(N-1)` *serial*
   dependency hops (each step forwards what the previous received) at a per-hop RTT that
   itself grows with tree depth; INC pays only its apex tree-depth (≈ 2 hops within a leaf
   tier, doubling when the group spans up to the root). So the gap is `O(N)` (ring) vs
   `O(log N)`/`O(depth)` (INC) — **algorithmic and transport-independent**: at 4 KiB almost
   no bytes move (verified: ring identical at 64 B and 4 KiB), so it is all serial-hop
   latency and would survive a line-rate ring. The measured small-message speedup is **not**
   `(N-1)` — it is ≈ `2(N-1)·(RTT_step/RTT_apex)`, already 3.7× at |G|=2 (where the ring's
   2 serial full-RTT steps beat INC's single sub-RTT turn-around) and ~33× at |G|=16. Read
   the measured curve, not a closed form.

2. **Bandwidth regime (large messages, right of the plot).** Here a *transport confound*
   enters: INC's reduce sources are **ACK-less** and stream at line rate, while the
   decomposed ring runs over **congestion-controlled UEC** flows that are RTT-bound (each
   ring step is a fresh CC'd flow; verified: INC streams at ~97% of line rate while the ring
   reaches only ~10%, so a fair line-rate ring at \|G\|=2/4 MiB would be ≈ INC ≈ `S/B`, i.e.
   ~1×, not 9.3×). This is **realistic** — a real decomposed AllReduce
   *does* run over CC'd RDMA, and bypassing CC is a genuine benefit of in-network offload —
   but the *magnitude* is sensitive to CC tuning, so the large-message multiples should be
   read as "INC offload vs CC'd endpoint p2p", not as a pure topology result.

**Bottom line for the thesis:** INC's advantage is real and largest where AllReduce latency
matters (small/medium messages on larger groups — exactly the TP-AllReduce regime), driven
by the ring's `O(N)` serial-hop structure. In the bandwidth regime the comparison is
transport-sensitive; a CC-matched line-rate ring would narrow the gap toward the ~2×
in-network ceiling. Mapping that crossover is the result — not "INC always wins by ~33×".

*Verified (adversarial review):* the ring arm is a correct, byte-fair, bandwidth-optimal
pipelined ring; `-reduce_compute_latency` is a no-op at 0 and charges once per aggregating
switch (not per replica); both arms are seed-invariant and complete with 0 drops. One
under-modelled point worth noting: the ring's per-step CC *cold-start* (a fresh flow each
step) is part of why it sits at ~10% of line rate — a real ring reuses a warm connection, so
even a CC'd ring would beat this; the large-message multiple is thus an upper bound on the
transport-driven part.
