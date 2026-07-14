# INC-vs-ring AllReduce A/B — pcm-sdk two-tier engine (scale-up tier)

The pcm-sdk counterpart of `../allreduce_ab/` (the htsim_uec-fork microbench):
the same two renderings of one logical AllReduce, run on the **pcm-sdk two-tier
simulator** after the INC port (branch `wanja/inc-port`, through commit
`c6aa679`), on the **single-switch NVLink-class scale-up topology**.

First results 2026-07-01; corrected diagnosis + warm-ring + analytic ideal-ring
reference added 2026-07-04. **A second measurement artifact — the engine's
NIC-injection default, verified in pcm-sdk source 2026-07-13, fixed 2026-07-14 —
silently capped every ring row from 64 KiB up; all rows below are the 2026-07-14
re-run on pcm-sdk (run-only) with `-intranode_linkspeed 3600000` (see point 3 of
"How to read this"). All INC rows reproduce byte-identically.** Quote the
`speedup_vs_ideal` column (red curve in the figure), not the GOAL-ring speedup —
see "How to read this".

## Arms

| arm | rendering | transport |
|---|---|---|
| INC | one `coll allreduce` per rank (switch reduces + multicasts) | ACK-less line-rate collective sources; `-reduce_compute_latency 100` (INC-only ALU charge, conservative) |
| ring | chunked bandwidth-optimal p2p ring: 2(N−1)=30 steps of size/N, `send_t requires recv_{t−1}` (mirrors `make_allreduce_ab.cpp`) | pcm-sdk's paced/pull (receiver-driven) UEC |

Both arms: **same fabric** — `-intranode_queue_type lossless_input` (PFC) on the
scale-up tier, single scale-up domain (`-nodes 16 -num_gpus_per_node 16`),
`-intranode_linkspeed 3600000` (the per-GPU scale-up NIC injection rate in Mbps —
**must be passed explicitly**, the `.topo` file does NOT set it; see point 3
below), and an identical 100 ns calc tail that depends on the collective, so

```
collective time = "Maximum finishing time at host 0" − 100 ns
```

(for the INC arm this equals `ALLREDUCE_COMPLETE duration_ns`; asserted per run).
Traces are emitted as `.goal` text and compiled with the coll-extended
LogGOPSim 1.1 `txt2bin` (`tools/loggopsim-coll`) — the same pipeline a
generator-produced trace takes.

## Results (|G| = 16, single-switch @ 3600 Gbps, lossless_input, 0 drops)

Re-run 2026-07-14 on pcm-sdk (run-only) with `-intranode_linkspeed 3600000`;
zero drops and zero lossless-headroom warnings in every run.

| size | INC (ns) | GOAL ring (ns) | warm ring (ns) | ideal ring (ns, analytic) | vs GOAL ring | **vs ideal ring** |
|---:|---:|---:|---:|---:|---:|---:|
| 4 KiB | 1424 | 78046 | 78046 | 19517 | 54.8× | **13.7×** |
| 16 KiB | 1449 | 78138 | 78138 | 19568 | 53.9× | **13.5×** |
| 64 KiB | 1549 | 78510 | 78510 | 19750 | 50.7× | **12.8×** |
| 256 KiB | 1947 | 79341 | 79341 | 20498 | 40.8× | **10.5×** |
| 1 MiB | 3541 | 82669 | 82669 | 23494 | 23.3× | **6.6×** |
| 4 MiB | 9932 | 95977 | 95977 | 35475 | 9.7× | **3.6×** |

Reproduce: `python3 run_pcm_ab_sweep.py --out results.csv` then
`python3 plot_pcm_ab.py` (figure: `allreduce_ab_pcm.{png,pdf}`). The sweep
driver now always passes `-intranode_linkspeed` (default 3600000) — never rely
on the engine default (point 3 below). The warm-ring column re-runs the ring
arm with `-conn_reuse` (persistent NCCL-like connections, commit `11c4216`);
the ideal-ring column is analytic (below).

## How to read this (the honest framing)

**The GOAL-ring speedup level is inflated — quote the vs-ideal-ring column
instead.** TWO measurement artifacts are involved: the bridge's sender-ACK
completion semantic (point 2 — still present by design, it inflates every row
of the post-fix table above) and the engine's NIC-injection default (point 3 —
a genuine bug that additionally capped the pre-fix ring rows from 64 KiB up;
fixed 2026-07-14, so the table above no longer contains it). Diagnosis
(points 1–2 established empirically 2026-07-04; point 3 verified in pcm-sdk
source 2026-07-13, fixed and re-run 2026-07-14):

1. **It is NOT connection cold-start.** We implemented warm persistent
   connections (`-conn_reuse`: one `UecSrc`+`UecPdcSes` per pair, each Send a
   `UecMsg` on the warm session) and re-ran the sweep: **warm == cold
   byte-identical at every size** (e.g. 64 KiB 78510 == 78510). Pre-fix the
   match was only "within <100 ns" (82897 vs 82996 at 64 KiB); with the
   NIC-injection fix (point 3) it is exact. Per-flow setup and CC ramp are not
   the floor.
2. **It is the bridge's sender-ACK completion semantic (the small-message
   floor).** The GOAL bridge completes a send (and its matched recv) when the
   *sender* has the ACK, so every step of the 2(N−1)-step serial dependency
   chain pays **data one-way + ACK one-way** ≈ 500+300+500 ns × 2 ≈ **2.6 µs**
   — exactly the measured 4 KiB floor (78046/30 ≈ 2602 ns/step). Canonical
   LogGOPSim completes sends locally and recvs at arrival; changing the bridge
   to delivery-time completion would be an engine-wide modeling change
   (affects all existing pcm-sdk results) — flagged, not made unilaterally.
3. **It WAS also the engine's NIC-injection default (the second artifact,
   fixed 2026-07-14).** The pcm-sdk engine (`htsim_flow_app_atlahs`, run-only)
   takes the per-GPU scale-up NIC injection rate from `-intranode_linkspeed`
   (Mbps), NOT from the `.topo` file (which sets only the fabric pipes). The
   flag was never passed, so it defaulted to COPY_ENG = 200,000 Mbps
   = 200 Gbps: `UecNIC::startSending` held the port 166 ns per 4,150 B frame
   = 24.61 payload-B/ns = **5.0 % of the fabric's realised 492.3 B/ns**. Every
   p2p send whose block exceeded one frame (4,086 B payload) was silently
   NIC-capped — here, every ring row from 64 KiB up (per-step block
   S/16 > 4,086 B). The ACK-less INC datapath bypasses the NIC pacer and was
   **never** capped: all INC rows reproduce byte-identically with the flag.
   With `-intranode_linkspeed 3600000` the NIC paces at 9.22 ns/frame
   = 443.1 payload-B/ns (Mbps arithmetic; the fabric pipes' 2 ps/B
   quantisation realises 492.3 B/ns), and e.g. the 4 MiB ring falls
   397964 → 95977 ns. Zero drops and zero lossless-headroom warnings in every
   re-run.
4. **The remaining per-step data one-way is real physics for a
   message-granularity ring, but real NCCL pipelines chunk slices within
   steps.** The fair reference is therefore the **analytic ideal ring**
   `T = 2(N−1)/N · S/BW + (N−1) · hop_oneway` (hop_oneway = 1.3 µs from the
   .topo: 500 ns link + 300 ns switch + 500 ns link) — a perfectly pipelined,
   zero-protocol-overhead ring. INC-vs-ideal is **conservative** for INC (a
   real endpoint ring cannot beat it).

**The quotable spread: INC ≈ 13.7× → 3.6× vs an ideal ring** (latency regime →
bandwidth regime), decreasing toward the asymptotic 2(N−1)/N ≈ 1.9× traffic
factor as serialization dominates. Consistent with the htsim_uec-fork
microbench (whose measured span started at 3.7×) and with the
INC-crossover literature (INC wins big at small/latency-bound sizes; the
advantage narrows in the bandwidth regime). Note the NIC fix moved the
*measured* GOAL-ring column in the same direction: its speedup now decays
54.8× → 9.7× across the sweep instead of the pre-fix, cap-flattened 55× → 40×.

**What the measured table IS evidence for:** the INC datapath works end-to-end
on pcm-sdk (reduce-ascent → apex → mcast-descent, completion, DAG release);
INC completion is flat-then-linear exactly as on the htsim_uec fork
(latency-bound ≤64 KiB at ~1.4–1.5 µs); PFC was timing-neutral in the
2026-07-04 control (64 KiB ring: 82997 composite vs 82996 lossless — both arms
of that pair ran under the pre-fix capped NIC, so the control is internally
consistent but has not been repeated at the fixed injection rate; every
2026-07-14 re-run reports zero drops and zero lossless-headroom warnings).

## Scope

Single scale-up domain (all 16 ranks in node 0). Multi-domain per-node coll
dispatch exists but is not yet validated. `-reduce_compute_latency 100` charges
the in-switch ALU on the INC arm only (matches the fork microbench's fairness
model).
