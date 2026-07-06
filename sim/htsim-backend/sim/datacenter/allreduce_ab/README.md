# INC-vs-ring AllReduce microbenchmark (scale-up tier)

The controlled form of the per-domain A/B Shuhao proposed (meeting 2026-06-19): one
logical AllReduce rendered two ways and run on a NVLink-class scale-up topology.

- **INC arm** — the TP AllReduce kept *whole*: one first-class `coll` op per rank;
  htsim does in-network reduce + multicast (`-allreduce_mode`-style apex offload).
- **ring arm** — the same AllReduce *decomposed* into a bandwidth-optimal ring of plain
  point-to-point flows: `2(N-1)` steps of `size/N` bytes each (the textbook
  `2(N-1)/N · size` per rank). Switches only forward; no INC.
- **recursive-doubling (`rdouble`) arm** [D4] — the latency-optimal recursive
  halving/doubling AllReduce (the algorithm NCCL Tree / the `simple_sim` generator's
  `RECURSIVE_DOUBLING` use): `2·log2(N)` steps, bandwidth-optimal (`2(N-1)/N · size` per
  rank), power-of-two `|G|` only. Wired into the sweep as a third measured baseline so it
  emits **ring / rdouble / INC / ideal-ring side by side**.

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

# (a) original size-sweep on the fixed 2-hop tree16 (INC vs ring only):
python3 run_allreduce_ab_sweep.py \
  --htsim ../htsim_uec --gen ../make_allreduce_ab \
  --topo ../topologies/scaleup_tree16_3600Gbps.topo --linkspeed 3600000 \
  --group-sizes 2,4,8,16 \
  --msg-sizes 4096,16384,65536,262144,1048576,4194304 \
  --reduce-compute 100 --out results.csv
/usr/bin/python3 plot_allreduce_ab.py --csv results.csv --out allreduce_ab

# (b) [D3] speedup-vs-N sweep on per-N single-switch scale-up crossbars (radix == |G|),
#     emitting ring / rdouble / INC / analytic ideal-ring side by side:
python3 run_allreduce_ab_sweep.py \
  --htsim ../htsim_uec --gen ../make_allreduce_ab \
  --topo-template ../topologies/scaleup_single_switch_{n}_3600Gbps.topo \
  --linkspeed 3600000 --group-sizes 2,4,8,16,32,64 \
  --msg-sizes 65536,1048576 --reduce-compute 100 --out results_vs_n.csv
/usr/bin/python3 plot_speedup_vs_n.py --csv results_vs_n.csv --out speedup_vs_n
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

## Scaling vs rank count N — supervisor directives (2026-07-06 meeting)

The `results_vs_n.csv` / `speedup_vs_n.png` sweep answers **D3** ("where does the
40×→~2× speedup come from") on the D5-scoped **single-switch scale-up** fabric: each `|G|`
runs on `scaleup_single_switch_{2,4,8,16,32,64}_3600Gbps.topo`, a non-blocking crossbar
whose radix == `|G|` (every pair one hop apart). This holds the fabric constant so the only
variable is the collective's *structure*.

Measured (65 KiB/rank, `--reduce-compute 100`): INC is **flat** at 1542 ns from `|G|=2` to
`|G|=64` (the switch aggregates all N inputs in one apex pass, so completion is O(1) in N);
the ring grows **O(N)** (5351→15852→36754→78507→162248→329729 ns — one added serial
dependency hop per rank) and recursive-doubling grows **O(log N)**
(5351→10634→15885→21119→26353→31586 ns).

### New computed / measured columns

| column | directive | meaning |
|---|---|---|
| `rdouble_ns` | D4 | measured recursive halving/doubling baseline (power-of-two `|G|`) |
| `ideal_ring_ns` | D3 | **computed** analytic ring floor `2(N-1)/N · 8S/B + (N-1)·hop_oneway` (`B=3600e9` bps, `hop_oneway=1300` ns); not simulated |
| `speedup_vs_ideal` | D3 | `ideal_ring_ns / inc_ns` — the **CC-decontaminated** INC-vs-*perfect*-ring speedup (removes the measured ring's congestion-control cold-start confound) |
| `inc_synced_ns` | D1 | `inc_ns + --inc-sync-rtt-ns` — the Khalilov 3-phase app-sync charge (ring barrier → ACK-less mcast/aggr → neighbor "I got everything" scan) |
| `speedup_synced` | D1 | `ring_ns / inc_synced_ns` — INC-pessimistic headline |

**D1 fairness.** INC completes ACK-less while the ring/rdouble baselines complete at
ACK-return (~1 RTT optimistic for INC). Rather than strip baseline ACKs, we charge INC an
analytic app-sync cost. `--inc-sync-rtt-ns` defaults to **2600 ns** (= +1 RTT =
`2·hop_oneway`, the INC-pessimistic headline); pass **1300** for the 1/2-RTT one-way
sensitivity lower bound. It is pure post-processing over `inc_ns` (no in-sim op). Even at
+1 RTT the advantage is robust: e.g. `|G|=64`, 65 KiB → `speedup_synced` still 79.6×.

### Ideal-ring bug-oracle

The analytic `ideal_ring_ns` is the **floor for the ring arm**: a *measured*, CC'd,
cold-starting ring can never beat the perfect line-rate zero-CC ring. The sweep asserts this
(`ring_below_ideal` counter) — **0/12 violations**, so the ring arm is not accidentally
faster than physics allows. INC, by contrast, legitimately **beats** the ideal-ring in
11/12 cells (`inc_beats_ideal`), because the ideal-ring's `(N-1)`-serial-hop latency term is
exactly what INC's O(1) single-switch apex is designed to eliminate — this is the D3
*algorithmic* result, not a bug. (The lone exception is `|G|=2`, 65 KiB, where a 2-rank ring
is a single exchange each way and INC's apex+ALU overhead makes `speedup_vs_ideal` = 0.94.)

### Reading the D3 decomposition

At `|G|=64`, 65 KiB the raw `ring/INC` speedup is **213×**, but `ideal_ring/INC` is **53×**:
the ~4× gap between them is the **transport confound** (the measured ring runs its
`2(N-1)` steps as fresh CC'd UEC flows at a fraction of line rate; the analytic ideal ring
does not). The 53× that survives is the **algorithmic + bandwidth** win — the ring's O(N)
serial latency (`(N-1)·hop_oneway`) against INC's flat apex. Recursive-doubling closes most
of the *latency* gap (its `rdouble/INC` plateaus ~20× — O(log N)), which is why it is the
fair latency-regime baseline to cite alongside the ring.
