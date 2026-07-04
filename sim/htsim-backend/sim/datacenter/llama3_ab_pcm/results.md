# llama3 two-tier A/B — first end-to-end result (2026-07-04)

**Setup.** Pre-baked simple_sim llama3 pair (`llama3.goal` decomposed vs
`llama3_inc.goal` + `.groups`, generator `EMIT_INC` path): 16 ranks as 4 nodes
x 4 GPUs. Two-tier pcm-sdk simulator (branch `wanja/inc-port` @ `2858452`):
scale-out = 16-host `tree16.topo` (lossy composite), scale-up = per-node
4-host single-switch NVLink-class crossbar (`scaleup_single_switch_4_3600Gbps
.topo`, `-intranode_queue_type lossless_input`), `-reduce_compute_latency 100`
on the INC arm. Groups translated to node-local ids (`llama3_inc_local.groups`).

| arm | makespan (ns) | collectives | drops |
|---|---:|---|---|
| decomposed baseline | 235 177 505 | (all p2p) | 0 |
| INC (first-class TP colls) | 229 541 927 | 32/32 `ALLREDUCE_COMPLETE` | 0 |
| **delta** | **-5 635 578 (2.40 % faster/iteration)** | | |

The 32 TP AllReduces (1 MiB, |G|=4, 8 instances x 4 groups) all complete in
their own node's scale-up domain — this run doubles as the validation of
multi-domain collective dispatch. INC makespan is identical on lossless and
composite (no drops/pauses in either), a consistency check.

**Why the delta is modest (and honest):** only the TP AllReduce phase is
accelerated; the scale-out (DP/PP) traffic dominates the 0.235 s iteration and
is identical in both arms (Amdahl). This is the application-level statement
that complements the microbenchmark speedups (13.7x -> 3.7x vs the analytic
ideal ring at the collective level).

**Two engine bugs found and fixed en route** (pcm-sdk `2858452`), both
specific to *staggered* collective-member arrival — real workloads stagger,
microbenchmarks did not:
1. *Scheduler clock freeze*: a partially-arrived collective holds
   `sends_active > 0` with an idle event list; the scheduler's catch-up to
   future-timed queue entries was unconditionally suppressed in that state, so
   the clock froze and the awaited members could never arrive.
2. *PFC charge-hold deadlock*: the reduce fan-in barrier held each
   contribution's lossless ingress charge until the combined result drained;
   one early member's full message of held charges pushed the ingress queue
   past its PFC pause threshold, and the stragglers the barrier was waiting
   for could never deliver. Fixed by releasing ingress bytes at fold time
   (aggregation state modeled as unbounded, per the thesis scope; finite
   aggregation buffers = future work). This deadlock is itself a finding about
   in-network aggregation under link-level flow control with finite headroom.

Reproduce: invocations above; traces in this dir; sim = pcm-sdk
`wanja/inc-port` @ `2858452`.
