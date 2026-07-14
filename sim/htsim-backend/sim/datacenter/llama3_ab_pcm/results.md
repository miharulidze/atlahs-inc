# llama3 two-tier A/B — first end-to-end result (2026-07-04, revised 2026-07-14)

> **REVISION 2026-07-14 — NIC injection-rate artifact fixed, both arms re-run.**
> The pcm-sdk engine (`htsim_flow_app_atlahs`, run-only, Zhiyi's repo; artifact
> verified in source 2026-07-13, fixed 2026-07-14) takes the per-GPU scale-up
> NIC injection rate from `-intranode_linkspeed` (Mbps), NOT from the `.topo`
> file (which sets only the fabric pipes). The flag was never passed, so it
> defaulted to `COPY_ENG` = 200,000 Mbps = 200 Gbps: `UecNIC::startSending`
> held the port 166 ns per 4,150 B frame = 24.61 payload-B/ns = 5.0 % of the
> fabric's realised 492.3 B/ns. Every p2p send block above one frame (4,086 B
> payload) — i.e. essentially the whole decomposed baseline — was silently
> NIC-capped; the ACK-less INC datapath bypasses the NIC pacer and was NEVER
> capped (the INC makespan below reproduces byte-identically with the flag).
> All numbers in this document are from the 2026-07-14 re-run with
> `-intranode_linkspeed 3600000` (NIC paces at 9.22 ns/frame = 443.1
> payload-B/ns, Mbps arithmetic; the fabric pipes' 2 ps/B quantisation
> realises 492.3 B/ns). Zero drops and zero lossless-headroom warnings in the
> re-run.

**Setup.** Pre-baked simple_sim llama3 pair (`llama3.goal` decomposed vs
`llama3_inc.goal` + `.groups`, generator `EMIT_INC` path): 16 ranks as 4 nodes
x 4 GPUs. Two-tier pcm-sdk simulator (run-only; original 2026-07-04 run on
branch `wanja/inc-port` @ `2858452`): scale-out = 16-host `tree16.topo` (lossy
composite), scale-up = per-node 4-host single-switch NVLink-class crossbar
(`scaleup_single_switch_4_3600Gbps.topo`, `-intranode_queue_type
lossless_input`), **`-intranode_linkspeed 3600000`** (per-GPU scale-up NIC
injection rate — REQUIRED, see revision note), `-reduce_compute_latency 100`
on the INC arm. Groups translated to node-local ids (`llama3_inc_local.groups`).
Compute model: placeholder (near-free) — the sign below is specific to it, see
interpretation.

| arm | makespan (ns) | collectives | drops |
|---|---:|---|---|
| decomposed baseline | 219 352 385 | (all p2p) | 0 |
| INC (first-class TP colls) | 229 541 927 (byte-identical to 2026-07-04) | 32/32 `ALLREDUCE_COMPLETE` | 0 |
| **delta** | **+10 189 542 (INC 4.6453 % slower/iteration under the placeholder compute model)** | | |

Superseded 2026-07-04 capped-NIC values, kept for provenance: baseline
235 177 505 / INC 229 541 927 / delta -5 635 578 (+2.3963 %, the old
"2.40 % faster/iteration" headline). That positive headline was measured
against a baseline whose p2p sends were NIC-capped at 200 Gbps; it does not
survive the fix on this anchor.

The 32 TP AllReduces (1 MiB, |G|=4, 8 instances x 4 groups) all complete in
their own node's scale-up domain — this run doubles as the validation of
multi-domain collective dispatch. INC makespan is identical on lossless and
composite (no drops/pauses in either), a consistency check.

**Interpretation (revised 2026-07-14).** With the NIC fix, under the
placeholder compute model the INC arm is 4.65 % SLOWER end-to-end on this
2-layer anchor. The slowdown is NOT the collective datapath: per-op INC
AllReduce durations are 3.5-9 us, and the INC arm's last collective completes
at 148.8 ms of its 229.5 ms makespan — the remaining ~81 ms collective-free
tail differs structurally from the baseline's schedule (a
schedule/congestion-structure effect; mechanism under investigation). The
end-to-end sign at this anchor is compute-model-sensitive: the same config
under the calibrated H100 roofline compute model gives +8.8133 %, the 8-layer
face-validity trace gives +9.1321 %, and the SP variant of this config (SP-C3)
gives +6.0086 % — the sign flip is specific to the near-free placeholder
compute model. The Amdahl framing of the 2026-07-04 write-up still holds
mechanically — only the TP AllReduce phase is accelerated, and the scale-out
(DP/PP) traffic is identical in both arms — but it no longer explains the
anchor result on its own. The collective-level microbenchmark speedups
(13.7x -> 3.6x vs the analytic ideal ring) are unaffected by the NIC artifact:
the INC datapath bypasses the NIC pacer and the ideal-ring reference is
analytic.

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

Reproduce: invocations above INCLUDING `-intranode_linkspeed 3600000` —
omitting it silently defaults the per-GPU scale-up NIC to `COPY_ENG` 200 Gbps
and reproduces the superseded 2026-07-04 baseline; traces in this dir;
sim = pcm-sdk (run-only), original run `wanja/inc-port` @ `2858452`
(2026-07-04), re-run 2026-07-14.
