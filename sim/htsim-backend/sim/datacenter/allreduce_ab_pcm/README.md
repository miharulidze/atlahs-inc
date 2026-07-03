# INC-vs-ring AllReduce A/B — pcm-sdk two-tier engine (scale-up tier)

The pcm-sdk counterpart of `../allreduce_ab/` (the htsim_uec-fork microbench):
the same two renderings of one logical AllReduce, run on the **pcm-sdk two-tier
simulator** after the INC port (branch `wanja/inc-port`, through commit
`c6aa679`), on the **single-switch NVLink-class scale-up topology**.

First results produced 2026-07-01. **Read the speedup with the caveat below —
the magnitude is CC-confounded and is not a quotable INC advantage.**

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

| size | INC (ns) | ring (ns) | speedup |
|---:|---:|---:|---:|
| 4 KiB | 1424 | 78046 | 54.8× |
| 16 KiB | 1449 | 78138 | 53.9× |
| 64 KiB | 1549 | 82996 | 53.6× |
| 256 KiB | 1947 | 97940 | 50.3× |
| 1 MiB | 3541 | 157934 | 44.6× |
| 4 MiB | 9932 | 397964 | 40.1× |

Reproduce: `python3 run_pcm_ab_sweep.py --out results.csv` then
`python3 plot_pcm_ab.py` (figure: `allreduce_ab_pcm.{png,pdf}`).

## How to read this (the honest framing)

**The speedup level is CC-confounded — do not quote 40–55× as INC's advantage.**
The ring arm issues its 30 steps as *sequential, cold-started* flows through
pcm-sdk's receiver-driven UEC: every step pays the pull/credit handshake and
window ramp before moving bytes, a ~2.6 µs/step floor (78046/30 ≈ 2602 ns at
4 KiB) that is nearly independent of chunk size. A real ring (NCCL/MPI) connects
its neighbors once, keeps the connections warm, and pipelines chunks — it pays
the CC ramp ~once, not 30×. Most of the ring's makespan here is connection
setup, not data movement.

**The fingerprint:** the speedup *decreases* with size (54.8× → 40.1×), the
opposite of the htsim_uec-fork microbench (`../allreduce_ab/`: 3.7× → 33×,
*growing*). A setup-bound baseline barely grows with data, so as INC's own cost
rises with size the ratio falls. Cross-engine consistency check: the 64 KiB ring
on the lossy COMPOSITE queue measured 82997 ns vs 82996 ns here on
lossless_input — PFC is irrelevant to this uncongested sequential ring, as
expected.

**What this table IS evidence for:** the INC datapath works end-to-end on
pcm-sdk (reduce-ascent → apex → mcast-descent, completion, DAG release), INC
completion is flat-then-linear exactly as on the htsim_uec fork (latency-bound
≤64 KiB at ~1.4–1.5 µs, bandwidth/chunking-bound above), and INC wins in the
latency regime for algorithmic reasons (apex depth 1 vs 2(N−1) serial hops).

**Toward a quotable number:** the ring baseline needs warm/persistent neighbor
connections (pay the CC ramp once) and/or pipelined chunks — a fair-ring
generator change, tracked as the #1 eval-methodology item.

## Scope

Single scale-up domain (all 16 ranks in node 0). Multi-domain per-node coll
dispatch exists but is not yet validated. `-reduce_compute_latency 100` charges
the in-switch ALU on the INC arm only (matches the fork microbench's fairness
model).
