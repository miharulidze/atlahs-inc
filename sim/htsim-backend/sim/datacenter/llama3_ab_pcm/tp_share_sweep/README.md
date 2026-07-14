# D2: end-to-end INC gain vs TP communication share (2026-07-07; re-measured 2026-07-14)

**Question** (supervisor meeting 2026-07-06): *what must an application look
like to see the INC gain?* This sweep varies the llama3 workload's parallelism
mix at a fixed 16 ranks (tp x dp x pp = 16) and measures the end-to-end
per-iteration INC gain on the real two-tier simulator, against the workload's
TP communication share.

> **2026-07-14 measurement-artifact fix — all numbers below are re-measured.**
> The engine (pcm-sdk, run-only) takes the per-GPU scale-up NIC injection rate
> from `-intranode_linkspeed` (Mbps), NOT from the `.topo` file (which sets
> only the fabric pipes). The flag was never passed in the 2026-07-04 sweep,
> so it defaulted to COPY_ENG = 200,000 Mbps = 200 Gbps: `UecNIC::startSending`
> held the port 166 ns per 4,150 B frame = 24.61 payload-B/ns = 5.0 % of the
> fabric's realised 492.3 B/ns. Every p2p arm whose per-send block exceeded
> one frame (4,086 B payload) was silently NIC-capped; the ACK-less INC
> datapath bypasses the NIC pacer and was NEVER capped (all INC makespans
> reproduce byte-identically with the flag). All configs were re-run
> 2026-07-14 with `-intranode_linkspeed 3600000`; with it the NIC paces at
> 9.22 ns/frame = 443.1 payload-B/ns (Mbps arithmetic; the fabric pipes'
> 2 ps/B quantisation realises 492.3 B/ns). Zero drops and zero
> lossless-headroom warnings in every re-run — the old headroom warnings
> (C1's 5,788, C2's 76) were themselves a capped-NIC drain artifact: hosts
> draining at 200 Gbps couldn't keep up with the 3,600 Gbps fabric.

## Setup

Identical to the anchor run in `../results.md` (atlahs `b34e908`), per-config
`gpn` (= GPUs per node) swapped in:

* engine: pcm-sdk two-tier `htsim_flow_app_atlahs` (run-only), branch
  `wanja/inc-port`; re-measured 2026-07-14 with `-intranode_linkspeed 3600000`
  (previous 2026-07-04 numbers were NIC-capped at the 200 Gbps COPY_ENG
  default)
* scale-out tier: 16-host `tree16.topo`, lossy composite, 100 Gbps
* scale-up tier: per-node single-switch NVLink-class crossbar
  `scaleup_single_switch_{gpn}_3600Gbps.topo`,
  `-intranode_queue_type lossless_input` (PFC),
  `-intranode_linkspeed 3600000` (per-GPU NIC injection rate — see fix note)
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

## Results (re-measured 2026-07-14, `-intranode_linkspeed 3600000`)

| config | tp/dp/pp | gpn | tp_comm_share | baseline (ns) | INC (ns) | gain | TP colls | drops |
|---|---|---:|---:|---:|---:|---:|---|---:|
| C1 | 2/8/1 | 4 | 0.0049 | 281,993,617 | 282,300,153 | −0.109 % | 128/128 | 0 |
| C2 | 4/4/1 | 4 | 0.0057 | 483,889,232 | 483,844,306 | 0.009 % | 64/64 | 0 |
| C3 | 4/2/2 | 4 | 0.0085 | 219,352,385 | 229,541,927 | **−4.645 %** | 32/32 | 0 |
| C4 | 8/2/1 | 8 | 0.0085 | 323,443,331 | 322,881,231 | 0.174 % | 32/32 | 0 |
| C5 | 16/1/1 | 16 | 1.0000 | 457,346 | 108,969 | **76.174 %** | 16/16 | 0 |

For provenance, the superseded 2026-07-04 (NIC-capped) numbers:

| config | old baseline (ns) | old INC (ns) | old gain |
|---|---:|---:|---:|
| C1 | 282,459,068 | 282,163,340 | 0.105 % |
| C2 | 484,005,007 | 483,827,653 | 0.037 % |
| C3 | 235,177,505 | 229,541,927 | 2.396 % |
| C4 | 323,893,617 | 322,881,231 | 0.313 % |
| C5 | 1,665,136 | 108,969 | 93.456 % |

* **Anchor consistency (updated):** C3 = the `b34e908` (2026-07-04 first
  end-to-end result) configuration. The regenerated `.goal` files are
  BYTE-IDENTICAL to the pre-baked pair and the compiled `.bin`s are
  byte-identical to `../llama3{,_inc}.bin` — that reproduction chain stands.
  The anchor's *measured numbers* (235,177,505 / 229,541,927 = 2.40 %/iter)
  are superseded: the anchor baseline was NIC-capped. Determinism of the
  fixed setup is cross-checked by the compute-model placebo run
  (`../facevalidity/computemodel/`), which reproduces the C3 re-run
  byte-identically (219,352,385 / 229,541,927).
* C3/C4/C5 INC makespans are byte-identical to the 2026-07-04 runs (the
  ACK-less INC datapath bypasses the NIC pacer and was never capped);
  C1/C2 INC makespans shifted < 0.05 % via their NIC-paced intranode DP
  sends.
* Every run: expected `ALLREDUCE_COMPLETE` count, **zero dropped packets**
  and **zero lossless-headroom warnings** in both tiers and both arms.
  The 2026-07-04 C1/C2-INC `LOSSLESS not working!` warnings are gone with
  the flag — they were the capped-NIC drain artifact, not a PFC-headroom
  property of the INC datapath. Note that "INC < baseline" no longer holds
  for C1 and C3 (see below).

## Result after the fix: placeholder-compute gains collapse at realistic TP shares

The 2026-07-04 finding here ("gain is critical-path-structural, not
byte-share", with C3 sitting ~3x ABOVE the byte-share Amdahl ceiling via
pipeline amplification, and a naive-serial-saving ratio table) was computed
against NIC-capped baselines and is **withdrawn**. What the fixed
measurement shows, under the placeholder (near-free) compute model:

* **C1/C2/C4 (realistic mixed-parallelism shares, < 1 %):** gains of
  −0.109 % / 0.009 % / 0.174 % — within noise of zero. The capped baselines
  had inflated every p2p arm; with the cap removed the decomposed DP/PP
  traffic no longer hands the INC arm a spurious edge.
* **C3 (PP=2): the gain flips NEGATIVE (−4.645 %).** The fixed-NIC plain-C3
  baseline (219,352,385 ns) is FASTER than the INC arm (229,541,927 ns,
  unchanged). This is an OPEN FINDING: the INC arm's last collective
  completes at 148.8 ms of its 229.5 ms makespan, and the ~81 ms
  collective-free tail differs structurally from the baseline's schedule —
  a schedule/congestion-structure effect, NOT the collective datapath
  (per-op INC durations are 3.5–9 us). Mechanism under investigation.
  The sign flip is specific to the placeholder compute model: the same
  config under the calibrated H100 roofline gives **+8.813 %**
  (`../facevalidity/computemodel/`).
* **C5 (share = 1, the pure-TP end):** gain 76.174 % = 1 − 1/S with
  per-collective speedup S = 457,346 / 108,969 = **4.20** (the old S = 15.3
  was a capped-baseline artifact — the pure-TP decomposed sends were the
  most heavily capped traffic in the sweep). C5 remains the deployment
  regime the scale-up pivot targets, and its gain remains large, but the
  Amdahl reference curve in the plot is now anchored at S = 4.20.

**Where the end-to-end signal survives** (all re-measured 2026-07-14 with
the flag):

* calibrated H100 roofline compute model, plain C3: **+8.813 %**
  (`../facevalidity/computemodel/`);
* 8-layer face-validity trace: **+9.132 %** (`../facevalidity/`);
* sequence parallelism rescues C3 even under placeholder compute:
  SPC3 **+6.009 %** vs plain-C3 −4.645 % — SP moves RS/AG onto accelerated
  collectives AND the SP INC arm (221.09 ms) is faster than the plain INC
  arm (229.54 ms) (`../sp_ab/`).

**Characterization ("what must an application look like"), revised:** byte
share still under-weights TP traffic by the tier bandwidth ratio (scale-up
bytes are ~36x cheaper than scale-out bytes at 3600 vs 100 Gbps), so
realistic 16-rank llama3 mixes sit at share < 1 %. Under the placeholder
compute model, none of those mixed configs shows a gain outside noise, and
C3 is negative. The end-to-end INC gain at realistic shares is carried by
(a) realistic compute cost (H100 roofline, +8.8 %), (b) deeper models
(8-layer, +9.1 %), and (c) TP-collective-dense schedules (SP, +6.0 %;
pure-TP C5, +76.2 %) — not by the byte share itself.

## Reproduce

```
scripts/run_sweep.py        # groups conversion + txt2bin + both arms + metrics
                            # (passes -intranode_linkspeed explicitly since the fix)
scripts/rerun_from_bins.py  # 2026-07-14 re-measurement from the committed C*/ bins
                            # (traces unchanged; only measured columns replaced)
scripts/set_config.py       # patches tp/dp/pp literals (restored afterwards)
scripts/render_c5.py        # C5-only: maps size-1-group (dp=1) comm ops to calc 0
plot_gain_vs_tp_share.py    # this plot
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
Run gotcha (the 2026-07-14 fix): **always pass `-intranode_linkspeed`
explicitly** — the `.topo` speed sets only the fabric pipes, and the per-GPU
NIC injection rate otherwise defaults to 200 Gbps COPY_ENG.

Inputs/outputs per config in `C1/..C5/` (bins, global + node-local groups,
run summaries with exact invocations). `results.csv` carries the
`intranode_linkspeed_mbps` column since the re-measurement. Full
stdout/stderr in the rerun scratch (`tp_sweep_nicfix/C*/{baseline,inc}.{out,err}`).
