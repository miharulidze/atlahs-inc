# SP A/B: INC vs endpoint under sequence parallelism (2026-07-10)

**Question.** Modern Megatron tensor parallelism is essentially always run
*with sequence parallelism* (SP), which re-expresses the TP AllReduce as
ReduceScatter + AllGather of identical total vector volume on the SAME TP
group. That was this eval's #1 external threat: our headline A/B measured the
*monolithic* TP AllReduce, whose in-switch apex fusion SP removes. Both RS and
AG are first-class INC collectives in this work, so INC still applies — this
experiment converts the threat into data by measuring (a) INC vs endpoint on
the SP-rendered workload (the modern-regime result) and (b) the apex-fusion
premium (plain-TP INC vs SP INC, carefully framed).

## Setup

Identical engine/topology/arms to `../tp_share_sweep/` (per-config `gpn`),
except the engine includes one new fix (below):

* engine: pcm-sdk two-tier `htsim_flow_app_atlahs`, branch `wanja/inc-port`
  @ `4d4361b` (run-only; `2858452` + the reduce_scatter chunking fix)
* scale-out: 16-host `tree16.topo` (lossy composite); scale-up: per-node
  `scaleup_single_switch_{4,16}_3600Gbps.topo`, `-intranode_queue_type
  lossless_input`; INC arm adds `-groups` + `-reduce_compute_latency 100`
* exact command lines: `SPC3/run_summary.txt`, `SPC5/run_summary.txt`

**SP traces.** New additive generator driver
`simple_sim/llama3_training_sp.py` (submodule `nccl_generator_v2`,
`simple-sim-coll`): the same llama3 model (2 layers, hidden 4096, batch 1 x
seq 128, 2 iterations, Megatron TP-fastest rank order) wired through the
existing SP primitives — `sp_column_linear` (AllGather of the seq-sharded
activation before the column-parallel matmul) and `sp_row_linear`
(ReduceScatter after the row-parallel matmul), context `"tp_sp"`. Attention
uses fused-QKV `sp_column_linear` + `sp_row_linear` for the output
projection; the SwiGLU MLP gathers the sequence ONCE and feeds gate+up from
the same gathered activation (1 AG + 1 RS per direction, as Megatron-SP).
Backward comm falls out of autograd exactly as in Megatron-SP (vjp of AG is
RS and vice versa). Activations, norms and residual adds stay sequence
sharded (their compute cost is divided by TP — a real SP effect the cost
model picks up). Render gate: `EMIT_INC=1 INC_CONTEXTS=tp,tp_sp` (the policy
env now also drives the node-width derivation; default `tp` is
behavior-preserving — plain-TP C3 re-renders BYTE-IDENTICAL bins to the
committed anchors). SP-C5 (dp=1) reuses `tp_share_sweep/scripts/render_c5.py`.

**Per-layer collective pattern (validated on the graphs, labels in topo
order):** forward `AG(qkv) -> RS(o_proj) -> AG(mlp) -> RS(down)`; backward
mirrored (`AG(bwd down) -> RS(bwd mlp) -> AG(bwd o_proj) -> RS(bwd qkv)`).
Every coll is 1,048,576 b — the same S as the plain-TP AllReduce it replaces
(AG: S = gathered output, each member multicasts S/|G|; RS: S = each member's
partial-sum input, each receives S/|G|). One structural asymmetry: the
first pipeline stage's layer-0 backward `RS(bwd qkv)` does not exist because
the simplified driver's input embedding has no parameter (`requires_grad ==
False` input), so per-iteration coll counts are 14 (stage-0 groups) / 16
(stage-1 groups) instead of a uniform 16.

## Results

| config | parallelism | baseline (ns) | INC (ns) | gain | colls (AG+RS) | drops |
|---|---|---:|---:|---:|---|---:|
| SP-C3 | TP4+SP/DP2/PP2 | 257,540,167 | 221,091,225 | **14.15 %** | 60/60 (32+28) | 0 |
| SP-C5 | TP16+SP/DP1/PP1 | 2,423,650 | 153,252 | **93.68 %** | 30/30 (16+14) | 0 |
| C3 anchor (plain TP) | TP4/DP2/PP2 | 235,177,505 | 229,541,927 | 2.40 % | 32/32 AR | 0 |
| C5 anchor (plain TP) | TP16/DP1/PP1 | 1,665,136 | 108,969 | 93.46 % | 16/16 AR | 0 |

* **The modern-regime headline: INC's end-to-end gain SURVIVES SP.** At the
  pure-TP end (C5) the relative gain is essentially unchanged (93.68 % vs
  93.46 %): the endpoint baseline also has to run 2x collectives, so the
  ratio is preserved even though apex fusion is lost.
* SP-C3's larger gain (14.15 % vs plain 2.40 %) is NOT a like-for-like
  improvement: the SP trace has ~2x TP collectives sitting in the same
  PP-amplified critical path (see D2's C3 analysis), a slower endpoint
  baseline (257.5 ms vs 235.2 ms — 28 additional ring-decomposed collectives (60 vs 32)), and
  less elementwise compute (seq-sharded norms). It says "SP moves MORE of the
  iteration onto TP collectives, which INC accelerates", not "SP makes INC
  2x better".
* Cross-engine consistency: the SP-C5 INC trace run single-tier on the
  in-repo `htsim_uec` completes 16/16 AG + 14/14 RS, 0 drops, makespan
  153,168 ns — within 0.05 % of the pcm two-tier scale-up result (153,252 ns).
* Zero dropped packets everywhere. The INC arms log `LOSSLESS not working`
  headroom warnings on scale-up last-hop queues (13,066 / 54,528 lines;
  packets HELD, not dropped) — the known finite-PFC-headroom scope caveat
  (see `../tp_share_sweep/README.md`, C1/C2): back-to-back ACK-less AG and RS
  bursts on the same group exceed the modeled pause headroom, so INC
  makespans read optimistic by up to the held-bytes drain time.

## The apex-fusion premium (plain-TP INC vs SP INC)

At the pure-TP config (C5, cleanest case — TP collectives are the whole
workload): SP INC 153,252 ns vs plain INC 108,969 ns = **1.41x — losing the
in-switch apex fusion of the monolithic AllReduce costs INC about 41 %** in
TP-collective wall time. Mechanically: the fused AllReduce ascends and fans
back down once (16 coll ops/iteration pair -> here 16 total), while RS+AG
must complete the reduction, re-synchronize, and re-ascend the scattered
blocks (30 coll ops, two barrier waves per layer instead of one, plus S/|G|
extra wire bytes per collective pair).

**Framing caveat (do not read as a clean same-baseline delta):** the plain
and SP rows are DIFFERENT workloads with different baselines (2.42 ms vs
1.67 ms endpoint; different compute, different coll counts). The premium is a
statement about INC wall time on the two renderings of the same logical
layer, not a controlled single-variable experiment. The honest pairing is:
relative gain vs its own baseline (93.5 % vs 93.7 %, ~unchanged), absolute
INC time (+41 % under SP at C5).

## Byte accounting (validation bar)

Summed over all ranks from the `.goal` texts (`coll` line sizes):

| config | coll lines | coll bytes | vs plain anchor |
|---|---:|---:|---|
| SP-C3 | 240 (60 ops x |G|=4) | 251,658,240 | 1.875x of 134,217,728 (32 ops) |
| SP-C5 | 480 (30 ops x |G|=16) | 503,316,480 | 1.875x of 268,435,456 (16 ops) |

Exactly 2x per surviving collective (each 1 MiB AllReduce -> one 1 MiB RS +
one 1 MiB AG), 1.875x overall because the 2 (resp. 4) stage-0 layer-0
backward RS ops don't exist (embedding simplification above). Decomposed-arm
TP send bytes: SP-C3 188,743,680 (ring RS/AG at |G|=4: 240 x 0.75 MiB) vs
plain-C3 201,326,592 (recursive-doubling AR: 32 ops x 4 ranks x 1.5 MiB,
2(N-1)/N = 1.5 at N=4); SP-C5 471,859,200 vs plain-C5 503,316,480 — the SP
re-expression carries the SAME per-rank volume (2(N-1)/N x S per
AR-equivalent, bandwidth-optimal in both renderings), modulo the missing
bwd ops.

## Two defects found en route (both would have silently skewed the A/B)

1. **Trace: iteration boundary not enforced on the first PP stage**
   (generator; fixed in `llama3_training_sp.py`). The simplified driver has
   no embedding parameter, so iteration i+1's input activation has NO
   dependency on iteration i. Plain-TP masked this structurally (its first
   collective sits behind the qkv matmul, which consumes the updated w_qkv);
   in SP the block's FIRST op is the activation AllGather, whose only
   ancestors are input+norm — every iteration's leading AG was free at trace
   start and launched at t=0 (observed on BOTH engines: `start_ns=2`). Fix:
   `first_stage_input_fn` gates the input on a current-iteration parameter
   via `wait_for` (embedding-dependency proxy) — restoring exactly the
   plain-TP iteration boundary (post-fix: iter-1's first AG starts at
   76,589 ns on the C5 smoke, after iter-0 completes).
2. **Engine: reduce_scatter hang on the pcm port** (fixed on `wanja/inc-port`
   @ `4d4361b`). The RS emit path assumed a chunk never straddles two owner
   blocks — true in the origin engine (`_mss=4096` divides power-of-two block
   sizes), false on pcm-sdk's UEC base (`_mss=4086` = MTU minus headers). The
   straddling chunk is attributed whole to the earlier block, so every
   non-first owner's sink receives less than its registered `block_bytes`
   (64x4086 = 261,504 < 262,144 at 1 MiB, |G|=4) and the fan-in barrier never
   fires. AR/AG/rooted-Reduce only ever OVERSHOOT their expectations (`>=`
   tolerates), so reduce_scatter was the sole hanging kind — exactly the
   never-exercised dispatch path flagged going in. Fix: cap each RS chunk at
   its block boundary (per-owner payload sums to exactly `block_bytes`; same
   wire bytes). Isolated by the staged method: Stage B ran the SAME trace
   clean on the in-repo engine first, so Stage C's hang was provably
   engine-side; a 4-member 1 MiB single-coll repro + `INC_STALL_DBG` (new
   `[DESC-DBG]`/`[SINK-RX-DBG]` probes) pinned the byte arithmetic.
   Exact-regression battery before==after: single-tier incast 1309006791;
   two-tier incast 1047079590; plain-TP C3 A/B 235177505/229541927 (32/32);
   AR/AG/Reduce microrepros byte-identical (3541/2910/3541 ns).

## Scope caveats

* The renderer emits comm sizes from LOGICAL tensor shapes, so PP activation
  sends stay 1 MiB in the SP render (real Megatron-SP sends the S/tp
  seq-shard). Identical in both arms (A/B untouched); ~0.2 % of baseline
  send bytes; slightly pessimistic for SP in the plain-vs-SP framing.
* The AG sink expectation (`block_bytes x (|G|-1)`) meets mss-quantized
  overshoot (65 x 4086 per peer block), so AG completion can fire up to a
  couple of chunks early (~10 ns at 3.6 Tbps) — negligible, noted for
  completeness.
* Finite-PFC-headroom warnings on the INC arms (above): INC makespans are
  optimistic bounds under the unbounded-aggregation-state scope.

## Reproduce

Pipeline per config (scratch dir; submodule stays clean):
`PYTHONPATH=<submodule> SP_TP/SP_DP/SP_PP=... python3 -B -m
simple_sim.llama3_training_sp` (16 pkl graphs into `./llama3_graphs`) ->
`simple_sim2goal.py` twice (baseline; `EMIT_INC=1 INC_CONTEXTS=tp,tp_sp`) —
SP-C5 via `../tp_share_sweep/scripts/render_c5.py` — -> node-local groups
(`rank % gpn` after containment assert) -> `txt2bin` (coll-capable LogGOPSim
1.1) -> the run_summary command lines. Same three gotchas as
`../tp_share_sweep/README.md`.
