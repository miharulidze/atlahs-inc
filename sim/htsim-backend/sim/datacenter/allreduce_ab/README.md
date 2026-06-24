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
  intra-node tier *reused verbatim* (16 hosts, 2-tier, 3600 Gbps ≈ NVLink, 4 GPUs/leaf).
- **Per-hop latency is symmetric.** It comes from the `.topo` `Downlink_Latency_ns` (500
  ns), which lives in the link `Pipe`s that **both** arms traverse. We do **not** use
  `-switch_latency`: plain p2p flows are source-routed and bypass the switch's processing
  pipe, so `-switch_latency` would reach the INC arm only (verified: ring is invariant to
  it; INC is not). Folding the per-hop cost into the link latency keeps it fair.
- **INC is charged for its extra work.** `-reduce_compute_latency 100` adds the in-switch
  aggregation ALU cost on the INC reduce path only (the ring does its reduces at endpoints,
  not modelled — standard α-β assumption). So INC is, if anything, *over*-charged: any
  measured speedup is a lower bound on that axis.
- Correctness oracle: every run completed with **0 drops** (24/24 cells clean).

## Results (completion ns; speedup = ring/INC)

| \|G\| | 4 KiB | 64 KiB | 256 KiB | 1 MiB | 4 MiB |
|---|---|---|---|---|---|
| 2  | 1117 / 4034  (3.6×)   | 1242 / 4151  (3.3×)   | 1641 / 7766  (4.7×)   | 3239 / 24540  (7.6×)  | 9629 / 91682  (9.5×)  |
| 4  | 1117 / 12102 (10.8×)  | 1242 / 12252 (9.9×)   | 1641 / 16282 (9.9×)   | 3239 / 56622  (17.5×) | 9629 / 217976 (22.6×) |
| 8  | 2234 / 36636 (16.4×)  | 2359 / 36920 (15.7×)  | 2758 / 37189 (13.5×)  | 4355 / 75679  (17.4×) | 10745 / 284152 (26.5×)|
| 16 | 2234 / 77305 (34.6×)  | 2359 / 77305 (32.8×)  | 2758 / 78225 (28.4×)  | 4355 / 86171  (19.8×) | 10745 / 318231 (29.6×)|

See `allreduce_ab.png`. INC completion is near-flat across size at small messages (a
latency floor) and grows ~linearly only once bandwidth-bound; the ring is far higher
throughout and grows with `|G|`.

## How to read the speedup (and its threats to validity)

The speedup has **two regimes** — the contribution is mapping them, not the headline
multiple (cf. the crossover literature):

1. **Latency regime (small messages, left of the plot).** The ring pays `2(N-1)` *serial*
   dependency hops (each step forwards what the previous received); INC pays ~2 (one
   reduce-up + one multicast-down). This `O(N)` vs `O(1)` gap is **purely algorithmic** and
   transport-independent — at 4 KiB almost no bytes move, so the gap is all serial-hop
   latency. This is the clean, defensible part of the result and explains why the speedup
   scales with `|G|` (≈ `(N-1)` at the floor).

2. **Bandwidth regime (large messages, right of the plot).** Here a *transport confound*
   enters: INC's reduce sources are **ACK-less** and stream at line rate, while the
   decomposed ring runs over **congestion-controlled UEC** flows that are RTT-bound (each
   ring step is a fresh CC'd flow; verified: a fair line-rate ring at \|G\|=2/4 MiB would be
   ≈ INC ≈ `S/B`, i.e. ~1×, not 9.5×). This is **realistic** — a real decomposed AllReduce
   *does* run over CC'd RDMA, and bypassing CC is a genuine benefit of in-network offload —
   but the *magnitude* is sensitive to CC tuning, so the large-message multiples should be
   read as "INC offload vs CC'd endpoint p2p", not as a pure topology result.

**Bottom line for the thesis:** INC's advantage is real and largest where AllReduce latency
matters (small/medium messages on larger groups — exactly the TP-AllReduce regime), driven
by the ring's `O(N)` serial-hop structure. In the bandwidth regime the comparison is
transport-sensitive; a CC-matched line-rate ring would narrow the gap toward the ~2×
in-network ceiling. Mapping that crossover is the result — not "INC always wins by 30×".
