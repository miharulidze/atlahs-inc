# SP A/B: INC vs endpoint under sequence parallelism (2026-07-10; re-measured 2026-07-15, NIC-rate fix)

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
except the engine includes one new fix (the reduce_scatter chunking fix,
below) — plus, like every 2026-07-14 re-run, the NIC-rate invocation fix
(below):

* engine: pcm-sdk two-tier `htsim_flow_app_atlahs`, branch `wanja/inc-port`
  @ `4d4361b` (run-only; `2858452` + the reduce_scatter chunking fix)
* scale-out: 16-host `tree16.topo` (lossy composite); scale-up: per-node
  `scaleup_single_switch_{4,16}_3600Gbps.topo`, `-intranode_queue_type
  lossless_input`; INC arm adds `-groups` + `-reduce_compute_latency 100`
* **NIC-rate fix (all numbers below re-measured 2026-07-15, engine pcm-sdk
  run-only):** every run now passes `-intranode_linkspeed 4000000` on BOTH
  arms. The engine takes the per-GPU scale-up NIC injection rate from this
  flag (Mbps), NOT from the `.topo` file (which sets only fabric pipes);
  unset, it silently defaulted to COPY_ENG = 200,000 Mbps = 200 Gbps —
  `UecNIC::startSending` held the port 166 ns per 4,150 B frame =
  24.61 payload-B/ns = 5.0 % of the fabric's realised 492.3 B/ns — capping
  every p2p arm whose per-send block exceeds one frame (4,086 B payload).
  The ACK-less INC datapath bypasses the NIC pacer and was NEVER capped:
  both SP INC makespans reproduce byte-identically with the flag. With it
  the NIC paces at 9.22 ns/frame = 443.1 payload-B/ns (Mbps arithmetic; the
  fabric pipes' 2 ps/B quantisation realises 492.3 B/ns). Zero drops and
  zero lossless-headroom warnings in every re-run.
* exact command lines (incl. `-intranode_linkspeed 4000000`):
  `SPC3/run_summary.txt`, `SPC5/run_summary.txt`

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
| SP-C3 | TP4+SP/DP2/PP2 | 246,715,422 | 221,091,225 | **+10.39 %** | 60/60 (32+28) | 0 |
| SP-C5 | TP16+SP/DP1/PP1 | 1,286,317 | 153,252 | **+88.09 %** | 30/30 (16+14) | 0 |
| C3 anchor (plain TP) | TP4/DP2/PP2 | 219,352,385 | 229,541,927 | −0.52 % | 32/32 AR | 0 |
| C5 anchor (plain TP) | TP16/DP1/PP1 | 457,346 | 108,969 | +75.81 % | 16/16 AR | 0 |

(2026-07-14 re-measurement; the INC arms are byte-identical to the 2026-07-10
runs, all four baselines moved with the NIC-rate fix. Superseded 2026-07-10
capped-NIC gains for the record: SP-C3 14.15 %, SP-C5 93.68 %, plain C3
2.40 %, plain C5 93.46 %.)

* **The modern-regime headline (stronger post-fix): SP does not merely
  preserve the INC gain at C3 — it moves the config from negative to
  positive (−0.52 % plain → +10.39 % SP).** Under the placeholder compute
  model the fixed-NIC plain-C3 endpoint baseline (219.35 ms) now BEATS the
  plain INC arm (229.54 ms; see `../tp_share_sweep/README.md` — the INC
  arm's last collective completes at 148.8 ms of its 229.5 ms makespan, so
  the deficit sits in a ~81 ms coll-free tail whose schedule differs
  structurally from the baseline's; a schedule/congestion-structure effect
  under investigation, NOT the collective datapath — per-op INC durations
  are 3.5–9 µs. Under the calibrated H100 roofline compute model the same

> RESOLVED 2026-07-15 (see tp_share_sweep/schedule_sensitivity.md): no bug.
> The collective datapath is verified FASTER than recursive doubling even under a
> deliberate 20 ms member skew (coll +3.6 us vs RD +14.1 us after the straggler);
> both renderings pay a ~35-40x group-synchronisation amplification over their
> serial TP cost; and a nanosecond-scale dependency-preserving schedule
> perturbation moves BOTH arms' makespans by +-5-9% (16-28 ms) under BOTH compute
> models. Every single-schedule end-to-end delta at sub-percent TP share
> (-0.52% placebo, +8.81% H100, +-0.2% C1/C2/C4) is at or below that floor and is
> reported as UNRESOLVED, not as a gain or loss. Perturbation-ensemble means lean
> INC-positive (+2.5% placebo / +8.0% roofline, n=3-4). C5/SP-C5 (4.2x/8.4x) are
> far above the floor and stand.
> SP note: the SP-C3 +10.39% delta (14.1 ms) also sits near the floor and carries
> the same caveat; SP-C5 (8.4x) is unaffected.

  config is +8.81 %). SP-C3 is +10.39 %: SP moves the RS/AG re-rendering onto
  accelerated collectives, AND the SP INC arm (221.09 ms) is faster than the
  plain INC arm (229.54 ms) outright.
* At the pure-TP end (C5) the gain RISES under SP (+75.81 % → +88.09 %). The
  pre-fix "ratio preserved (93.46 % vs 93.68 %)" reading was an artifact of
  both baselines being NIC-capped: with the fixed NIC, the ring-decomposed
  RS+AG endpoint baseline (1.29 ms) costs 2.8x the plain-AR baseline
  (0.457 ms), while the INC arm pays only the 1.41x apex-fusion premium
  (153,252 ns vs 108,969 ns) — so the relative gain grows.
* SP-C3 vs plain-C3 remains cross-workload, NOT like-for-like: the SP trace
  has ~2x TP collectives sitting in the same PP-amplified critical path (see
  D2's C3 analysis), a slower endpoint baseline (235.2 ms vs 219.4 ms — 28
  additional ring-decomposed collectives (60 vs 32)), and less elementwise
  compute (seq-sharded norms). It says "SP moves MORE of the iteration onto
  TP collectives, which INC accelerates — enough to flip placeholder-compute
  C3's sign", not "SP makes INC better in a controlled sense".
* Cross-engine consistency: the SP-C5 INC trace run single-tier on the
  in-repo `htsim_uec` completes 16/16 AG + 14/14 RS, 0 drops, makespan
  153,168 ns — within 0.05 % of the pcm two-tier scale-up result (153,252 ns).
  (INC-arm-only, unaffected by the NIC-rate fix.)
* Zero dropped packets AND zero `LOSSLESS not working` headroom warnings
  everywhere in the 2026-07-14 re-runs. The 2026-07-10 INC arms' warnings
  (13,066 / 54,528 lines on scale-up last-hop queues) were themselves a
  capped-NIC drain artifact: hosts draining at the 200 Gbps NIC default
  could not keep up with the 3,600 Gbps fabric, so PFC held packets at the
  last hop. Packets were held, not dropped, and the INC makespans were
  unaffected (byte-identical with the flag).

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
and SP rows are DIFFERENT workloads with different baselines (1.29 ms vs
0.457 ms endpoint; different compute, different coll counts). The premium is a
statement about INC wall time on the two renderings of the same logical
layer, not a controlled single-variable experiment. The honest pairing is:
relative gain vs its own baseline (+75.81 % plain vs +88.09 % SP — HIGHER
under SP, because the fixed-NIC endpoint baseline pays 2.8x for the ring
RS+AG decomposition while INC pays only the 1.41x apex-fusion loss),
absolute INC time (+41 % under SP at C5). Both INC makespans are
byte-identical to the 2026-07-10 runs; the premium itself (1.41x) is
untouched by the NIC-rate fix.

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
   AR/AG/Reduce microrepros byte-identical (3541/2910/3541 ns). (The battery
   ran 2026-07-10 under the then-current capped-NIC invocation on both sides
   of the RS fix, so it remains a valid before==after check; its C3 baseline
   number is superseded by the 2026-07-14 fixed-NIC re-measurement above.)

## Scope caveats

* The renderer emits comm sizes from LOGICAL tensor shapes, so PP activation
  sends stay 1 MiB in the SP render (real Megatron-SP sends the S/tp
  seq-shard). Identical in both arms (A/B untouched); ~0.2 % of baseline
  send bytes; slightly pessimistic for SP in the plain-vs-SP framing.
* The AG sink expectation (`block_bytes x (|G|-1)`) meets mss-quantized
  overshoot (65 x 4086 per peer block), so AG completion can fire up to a
  couple of chunks early (~10 ns at 3.6 Tbps) — negligible, noted for
  completeness.
* Finite-PFC-headroom warnings: gone in the 2026-07-14 re-runs (0 lines on
  every arm) — the 2026-07-10 warnings were a capped-NIC drain artifact, not
  INC aggregation pressure (above). The unbounded-aggregation-state scope
  caveat stands in principle, but no headroom pressure is observed in any of
  these runs.

## Reproduce

Pipeline per config (scratch dir; submodule stays clean):
`PYTHONPATH=<submodule> SP_TP/SP_DP/SP_PP=... python3 -B -m
simple_sim.llama3_training_sp` (16 pkl graphs into `./llama3_graphs`) ->
`simple_sim2goal.py` twice (baseline; `EMIT_INC=1 INC_CONTEXTS=tp,tp_sp`) —
SP-C5 via `../tp_share_sweep/scripts/render_c5.py` — -> node-local groups
(`rank % gpn` after containment assert) -> `txt2bin` (coll-capable LogGOPSim
1.1) -> the run_summary command lines (`-intranode_linkspeed 4000000` is
MANDATORY on both arms — see the NIC-rate fix note in Setup; omitting it
silently reverts the p2p arm to the 200 Gbps NIC default). Same three
gotchas as `../tp_share_sweep/README.md`.
