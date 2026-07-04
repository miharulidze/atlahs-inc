# INC-vs-ring AllReduce A/B — pcm-sdk two-tier engine (scale-up tier)

The pcm-sdk counterpart of `../allreduce_ab/` (the htsim_uec-fork microbench):
the same two renderings of one logical AllReduce, run on the **pcm-sdk two-tier
simulator** after the INC port (branch `wanja/inc-port`, through commit
`c6aa679`), on the **single-switch NVLink-class scale-up topology**.

First results 2026-07-01; corrected diagnosis + warm-ring + analytic ideal-ring
reference added 2026-07-04. **Quote the `speedup_vs_ideal` column (red curve in
the figure), not the GOAL-ring speedup — see "How to read this".**

## Arms

| arm | rendering | transport |
|---|---|---|
| INC | one `coll allreduce` per rank (switch reduces + multicasts) | ACK-less line-rate collective sources; `-reduce_compute_latency 100` (INC-only ALU charge, conservative) |
| ring | chunked bandwidth-optimal p2p ring: 2(N−1)=30 steps of size/N, `send_t requires recv_{t−1}` (mirrors `make_allreduce_ab.cpp`) | pcm-sdk's paced/pull (receiver-driven) UEC |

Both arms: **same fabric** — `-intranode_queue_type lossless_input` (PFC) on the
scale-up tier, single scale-up domain (`-nodes 16 -num_gpus_per_node 16`), and an
identical 100 ns calc tail that depends on the collective, so

```
collective time = "Maximum finishing time at host 0" − 100 ns
```

(for the INC arm this equals `ALLREDUCE_COMPLETE duration_ns`; asserted per run).
Traces are emitted as `.goal` text and compiled with the coll-extended
LogGOPSim 1.1 `txt2bin` (`tools/loggopsim-coll`) — the same pipeline a
generator-produced trace takes.

## Results (|G| = 16, single-switch @ 3600 Gbps, lossless_input, 0 drops)

| size | INC (ns) | GOAL ring (ns) | warm ring (ns) | ideal ring (ns, analytic) | vs GOAL ring | **vs ideal ring** |
|---:|---:|---:|---:|---:|---:|---:|
| 4 KiB | 1424 | 78046 | 78046 | 19517 | 54.8× | **13.7×** |
| 16 KiB | 1449 | 78138 | 78138 | 19568 | 53.9× | **13.5×** |
| 64 KiB | 1549 | 82996 | 82897 | 19773 | 53.6× | **12.8×** |
| 256 KiB | 1947 | 97940 | 97940 | 20592 | 50.3× | **10.6×** |
| 1 MiB | 3541 | 157934 | 157870 | 23869 | 44.6× | **6.7×** |
| 4 MiB | 9932 | 397964 | 397889 | 36976 | 40.1× | **3.7×** |

Reproduce: `python3 run_pcm_ab_sweep.py --out results.csv` then
`python3 plot_pcm_ab.py` (figure: `allreduce_ab_pcm.{png,pdf}`). The warm-ring
column re-runs the ring arm with `-conn_reuse` (persistent NCCL-like
connections, commit `11c4216`); the ideal-ring column is analytic (below).

## How to read this (the honest framing)

**The GOAL-ring speedup level (40–55×) is inflated by a completion-semantic
artifact — quote the vs-ideal-ring column instead.** Diagnosis (established
empirically, 2026-07-04):

1. **It is NOT connection cold-start.** We implemented warm persistent
   connections (`-conn_reuse`: one `UecSrc`+`UecPdcSes` per pair, each Send a
   `UecMsg` on the warm session) and re-ran the sweep: **warm == cold within
   <100 ns at every size** (e.g. 64 KiB 82897 vs 82996). Per-flow setup and CC
   ramp are not the floor.
2. **It is the bridge's sender-ACK completion semantic.** The GOAL bridge
   completes a send (and its matched recv) when the *sender* has the ACK, so
   every step of the 2(N−1)-step serial dependency chain pays **data one-way +
   ACK one-way** ≈ 500+300+500 ns × 2 ≈ **2.6 µs** — exactly the measured
   4 KiB floor (78046/30 ≈ 2602 ns/step). Canonical LogGOPSim completes sends
   locally and recvs at arrival; changing the bridge to delivery-time
   completion would be an engine-wide modeling change (affects all existing
   pcm-sdk results) — flagged, not made unilaterally.
3. **The remaining per-step data one-way is real physics for a
   message-granularity ring, but real NCCL pipelines chunk slices within
   steps.** The fair reference is therefore the **analytic ideal ring**
   `T = 2(N−1)/N · S/BW + (N−1) · hop_oneway` (hop_oneway = 1.3 µs from the
   .topo: 500 ns link + 300 ns switch + 500 ns link) — a perfectly pipelined,
   zero-protocol-overhead ring. INC-vs-ideal is **conservative** for INC (a
   real endpoint ring cannot beat it).

**The quotable spread: INC ≈ 13.7× → 3.7× vs an ideal ring** (latency regime →
bandwidth regime), decreasing toward the asymptotic 2(N−1)/N ≈ 1.9× traffic
factor as serialization dominates. Consistent with the htsim_uec-fork
microbench (whose measured span started at 3.7×) and with the
INC-crossover literature (INC wins big at small/latency-bound sizes; the
advantage narrows in the bandwidth regime).

**What the measured table IS evidence for:** the INC datapath works end-to-end
on pcm-sdk (reduce-ascent → apex → mcast-descent, completion, DAG release);
INC completion is flat-then-linear exactly as on the htsim_uec fork
(latency-bound ≤64 KiB at ~1.4–1.5 µs); PFC is timing-neutral for these
uncongested runs (64 KiB ring: 82997 composite vs 82996 lossless).

## Scope

Single scale-up domain (all 16 ranks in node 0). Multi-domain per-node coll
dispatch exists but is not yet validated. `-reduce_compute_latency 100` charges
the in-switch ALU on the INC arm only (matches the fork microbench's fairness
model).
