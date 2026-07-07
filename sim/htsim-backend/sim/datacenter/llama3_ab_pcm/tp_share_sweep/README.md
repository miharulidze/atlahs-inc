# D2: end-to-end INC gain vs TP communication share (2026-07-07)

**Question** (supervisor meeting 2026-07-06): *what must an application look
like to see the INC gain?* This sweep varies the llama3 workload's parallelism
mix at a fixed 16 ranks (tp x dp x pp = 16) and measures the end-to-end
per-iteration INC gain on the real two-tier simulator, against the workload's
TP communication share.

## Setup

Identical to the anchor run in `../results.md` (atlahs `b34e908`), per-config
`gpn` (= GPUs per node) swapped in:

* engine: pcm-sdk two-tier `htsim_flow_app_atlahs`, branch `wanja/inc-port`
  @ `2858452` (run-only)
* scale-out tier: 16-host `tree16.topo`, lossy composite, 100 Gbps
* scale-up tier: per-node single-switch NVLink-class crossbar
  `scaleup_single_switch_{gpn}_3600Gbps.topo`,
  `-intranode_queue_type lossless_input` (PFC)
* arms: decomposed baseline (`llama3.bin`) vs INC (`llama3_inc.bin`
  + `-groups llama3_inc_local.groups -reduce_compute_latency 100`)
* exact command lines: `C*/run_summary.txt`

Traces: generator submodule `nccl_generator_v2` @ `b1f7361`
(`simple-sim-coll`), simple_sim llama3 pipeline (2 layers, hidden 4096,
batch 1 x seq 128, 2 iterations; the per-rank TP AllReduce payload is
1 MiB = 128 x 4096 x fp16 in every config). Per config the
`tp_size/dp_size/pp_size` literals in `simple_sim/llama3_training.py` were
patched, both arms rendered (`EMIT_INC=1` -> `coll` + `.groups`; unset ->
decomposed baseline), and the file restored (`git checkout --`; submodule left
clean). Baseline TP AllReduce decomposition = recursive doubling (generator
default). TP groups are node-contained by construction (Megatron TP-fastest
rank order, node-major hosts, tp <= gpn) and the generator's node-containment
gate verified it; the emitted `.groups` (global ranks) were converted to the
engine's NODE-LOCAL host ids via `local = rank % gpn` after asserting
`rank // gpn` is constant per group (`scripts/run_sweep.py:convert_groups`).

**`tp_comm_share` definition:** from the INC arm's `.goal` text, summed over
all ranks: `coll bytes / (coll bytes + send bytes)` — the fraction of
communicated bytes that are TP-collective. (Each rank's AllReduce contribution
counts its payload S once; `send` lines carry the decomposed DP/PP traffic.
Byte shares are computed on the same trace for both arms since the baseline
differs only by decomposing the same colls.)

## Results

| config | tp/dp/pp | gpn | tp_comm_share | baseline (ns) | INC (ns) | gain | TP colls | drops |
|---|---|---:|---:|---:|---:|---:|---|---:|
| C1 | 2/8/1 | 4 | 0.0049 | 282,459,068 | 282,163,340 | 0.105 % | 128/128 | 0 |
| C2 | 4/4/1 | 4 | 0.0057 | 484,005,007 | 483,827,653 | 0.037 % | 64/64 | 0 |
| C3 | 4/2/2 | 4 | 0.0085 | 235,177,505 | 229,541,927 | **2.396 %** | 32/32 | 0 |
| C4 | 8/2/1 | 8 | 0.0085 | 323,893,617 | 322,881,231 | 0.313 % | 32/32 | 0 |
| C5 | 16/1/1 | 16 | 1.0000 | 1,665,136 | 108,969 | **93.456 %** | 16/16 | 0 |

* **Anchor consistency:** C3 = the `b34e908` (2026-07-04 first end-to-end result) configuration. The regenerated
  `.goal` files are BYTE-IDENTICAL to the pre-baked pair, the compiled `.bin`s
  are byte-identical to `../llama3{,_inc}.bin`, and the run reproduces the
  anchor exactly (235,177,505 / 229,541,927 ns = 2.40 %/iter, 32/32
  `ALLREDUCE_COMPLETE`, 0 drops).
* Every run: expected `ALLREDUCE_COMPLETE` count, INC < baseline, and **zero
  dropped packets** in both tiers and both arms.
* C1-INC and C2-INC log `LOSSLESS not working! I should have dropped this
  packet` warnings (5,788 and 76 lines) on scale-up last-hop queues
  (`LS0->DST2/3` — C1's second per-node group). No packet is dropped (the
  lossless queue holds it); the warning flags that concurrent ACK-less
  line-rate collectives (C1: TWO groups per scale-up domain) plus converging
  decomposed DP traffic exceed the modeled PFC headroom before pause
  propagates. This is the finite-headroom scope caveat already documented for
  the anchor (aggregation state modeled as unbounded); completion counts and
  the A/B remain valid, but C1's INC makespan should be read as optimistic by
  up to the held-bytes' drain time.

## The finding: byte share alone does not predict the gain

Gain is **not monotone in `tp_comm_share`** (C1 > C2; C3 = 8x C4 at identical
share). The scatter is structural, not noise:

| config | naive serial TP saving* | measured delta | ratio |
|---|---:|---:|---:|
| C1 | 0.35 ms | 0.30 ms | 0.86 |
| C2 | 0.69 ms | 0.18 ms | 0.26 |
| C3 | 0.35 ms | 5.64 ms | 16.3 |
| C4 | 0.87 ms | 1.01 ms | 1.17 |
| C5 | 0.96 ms | 1.56 ms | 1.62 |

\* per-rank TP-coll count x (rdouble − INC) 1 MiB collective time at |G|=tp
from the microbench regime (`../../allreduce_ab/results_vs_n.csv`, htsim_uec
engine — indicative scale only).

* **C1, C4 (ratio ~ 1):** TP savings land ~1:1 on the critical path; the gain
  is just Amdahl-diluted by the DP-dominated iteration.
* **C2 (ratio 0.26):** the longest iteration (DP=4 ZeRO-1 across 4 nodes,
  2 full layers of gradients) hides most TP savings off the critical path.
* **C3 (ratio 16, ABOVE the byte-share Amdahl ceiling):** with PP=2 the TP
  AllReduce latency sits inside the pipeline dependency chain, so each saved
  collective also shrinks downstream bubbles — the saving is *amplified*, and
  the measured 2.40 % exceeds the 0.85 % byte-share ceiling by ~3x.
* **C5 (share = 1):** the pure-TP end: gain = 1 − 1/S with measured S = 15.3
  — the same-engine bandwidth-regime speedup that anchors the Amdahl
  reference curve in the plot.

**Characterization ("what must an application look like"):** the application
must spend its *critical path* in node-contained TP collectives. Byte share
under-weights TP traffic by the tier bandwidth ratio (scale-up bytes are ~36x
cheaper than scale-out bytes at 3600 vs 100 Gbps), so DP/PP-heavy 16-rank
llama3 mixes sit at share < 1 % and see < 0.5 %/iter; pipeline-parallel
configs (C3) see several x more than their byte share suggests; and TP-only
scale-up workloads (C5, the deployment INC targets per the scale-up pivot)
approach the full collective-level speedup.

## Reproduce

```
scripts/run_sweep.py      # groups conversion + txt2bin + both arms + metrics
scripts/set_config.py     # patches tp/dp/pp literals (restored afterwards)
scripts/render_c5.py      # C5-only: maps size-1-group (dp=1) comm ops to calc 0
plot_gain_vs_tp_share.py  # this plot
```

Pipeline per config: patch literals -> `python -m simple_sim.llama3_training`
(16 pkl graphs) -> `simple_sim2goal.py` twice (baseline / `EMIT_INC=1`) ->
convert groups -> `txt2bin -i X.goal -o X.bin` (coll-capable LogGOPSim 1.1) ->
run both arms. Two generation gotchas: (1) CPython validates `.pyc` by
size+mtime-seconds — the patched literals keep the file size constant, so a
sub-second generate cycle can silently reuse the previous config's bytecode
(use `-B` + delete the stale `.pyc`); (2) `dp_size=1` makes the ZeRO-1
collectives decompose to zero ops and crashes `generate_lines` — C5 is
rendered by `scripts/render_c5.py`, which replaces size-1-group comm ops with
a dependency-preserving `calc 0` in BOTH arms (never fires for TP ops).

Inputs/outputs per config in `C1/..C5/` (bins, global + node-local groups,
run summaries with exact invocations). Full stdout/stderr in the session
scratchpad (`tp_sweep/C*/{baseline,inc}.{out,err}`).
