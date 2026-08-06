# intranode_linkspeed_sweep — INC vs baseline, trace-free Llama iteration

**Status: superseded.** This runner is retained for provenance and sensitivity studies.
Use `../ch5_accumulation/` for the canonical Chapter 5 experiment.

Reproduces the shape of the *"Tuning Intranode Link Speed"* figure as a purely
**synthetic** INC-vs-endpoint A/B (no captured trace, no GPU). For each
parallelism config we synthesize one Llama-2-7B training iteration with the
`simple_sim` analytical generator in two arms — **baseline** (the generator's
decomposed collectives) and **INC** (node-contained TP collectives as first-class
`coll` ops + a `.groups` sidecar) — compile to `.bin`, and sweep the scale-up
(intranode) link speed while timing one iteration on the pcm-sdk two-tier sim.

## What it measures

- **x**: intranode link speed, `-intranode_linkspeed` (Mbps = Gbps×1000), log₂.
- **y**: time / training iteration (s) = `makespan_ns / iters / 1e9`.
- Two PNGs, split by pipeline degree; each has 2 configs × {baseline solid,
  INC dashed} + the NVLink (3600 Gbps) reference line.

| Plot | Configs (TP·DP·PP) |
|------|--------------------|
| `..._pp1.png` | TP4·DP4·PP1, TP2·DP8·PP1 |
| `..._pp2.png` | TP4·DP2·PP2, TP2·DP4·PP2 |

All 16 GPUs. Layout is **multi-domain**, derived by the generator:
`gpus_per_node = TP`, `nodes = 16/TP`. TP collectives run intranode (the swept
link); DP/PP cross nodes on the fixed scale-out fabric. Only TP goes INC
(`INC_CONTEXTS=tp`), so DP/PP are the same fixed floor in both arms and the gap
isolates TP data movement.

## Model

Llama-2-7B-geometry **per-layer** dims (hidden 4096, FFN 11008, 32 MHA
heads, seq 4096, configurable micro-batch) so message sizes place the bandwidth→latency knee
in the plotted range, with a **shallow stack** (default 2 layers): iteration time
and the INC gap scale approximately linearly in depth. The case-study headline
uses a micro-batch of 32 per DP rank; the CLI default remains 1 so small
validation runs stay cheap. Gradient accumulation is not modeled by this knob.

The primary compute model is `COMPUTE_MODEL=h100_te`, an **optimized H100
FP8-math/BF16-storage per-operation roofline**. It uses the dense FP8 tensor-core ceiling
(1.979 PFLOP/s) for arithmetic, BF16/FP16 storage traffic (2 B/element) at the
3.35 TB/s HBM3 ceiling:

`Tcalc = Σi max(Fi / 1.979 PFLOP/s, 2·elementsi / 3.35 TB/s)`.

Every `calc` node keeps its own roofline duration, placement and dependencies;
one operation can never make another cheaper. The model deliberately does
**not** apply an end-to-end model-FLOPs utilisation (MFU) inside every kernel.
Communication, dependency stalls and overlap are already measured by the
simulator; using MFU as a per-kernel derating counted part of them twice.

`--compute_model h100` retains the old BF16 + 45%-MFU per-operation model only
for reproduction and sensitivity. The new model is an optimistic analytical
bound, not a claim of cycle-accurate GPU timing. The analytical IR does not
carry kernel execution-precision metadata, so `h100_te` applies the FP8 ceiling
to every arithmetic count and 2-byte storage to every element. Real kernels,
especially attention and optimizer kernels, can be slower; the two models are
best read as an optimized lower bound and a conservative sensitivity.

## Topology / the sweep knob

**Intranode (scale-up, swept):** a single-switch crossbar with exactly `TP` hosts
(must equal `-num_gpus_per_node`), **generated per swept rate** into the tmpdir
(`scaleup_single_switch_<TP>_<Gbps>Gbps.topo`, 50 ns links / 300 ns switch — the
thesis-standard latencies), so the fabric pipes and `-intranode_linkspeed` carry
the **same** speed at every point: consistent link speed in the network *and* at
the sender endpoints, no in-series ceiling. (The earlier harness pinned the topo
at 12800 Gbps and swept only the flag, leaving the pipes at a different — and
non-integer-ps/B — rate.)

**Exact rates only.** htsim stores a link's rate as integer **picoseconds per
byte** (ps/B = 8000/Gbps), so swept rates must divide 8000 to be exact on the
wire — the default grid is `400, 800, 1600, 2000, 4000, 8000`
(20…1 ps/B). 3200/3600/6400/12800 silently quantise and are rejected loudly;
the NVLink 3600 Gbps line on the plots is a reference line, not a simulated
point.

**Scale-out (DP/PP, fixed per run, selected via `--internode_gbps`):** a
generated `tree<N>_nonblocking_<rate>Gbps.topo` with all hosts on one
**non-blocking** switch.
The earlier 2:1-oversubscribed `tree16` penalised TP4 (its DP ring lands
one-per-rack → 100 % cross-rack through the squeezed uplinks) and added ECMP
routing noise; non-blocking removes both confounds without changing link speed
(structure, not speed). The default and main operating point is **400 Gbps**;
the fabric pipe and scale-out NIC always carry the same selected rate. Outputs
are suffixed `_ib<N>` so variants coexist.

**Congestion control (per-tier).** The two tiers model different fabrics, so they
run different congestion-control treatments:

- **Scale-out (DP/PP)** keeps **DCTCP** via PCM (`-pcm_enable` +
  `pcm_cc_config_all_uec_dctcp_v2.json`, loading `libuec_dctcp_v2.so` from
  `pcm/build/lib` via `LD_LIBRARY_PATH`). This fabric is lossy; without sender
  pacing the decomposed baseline's ~33 MB collectives overflow the queue and the
  makespan turns buffer-dependent (an artifact). DCTCP → 0 lossless violations / 0
  retransmits.
- **Intranode (scale-up)** is **CC-FREE** (`-intranode_cc none`): it models NVLink,
  which has no end-to-end congestion control — only NIC line-rate serialization +
  lossless PFC. PFC thresholds and the intranode queue are **derived per generated
  fabric** by `sim.pfc_config()` (XOFF = 1 BDP at the fabric's own rate minus the
  rate-derived pause-propagation headroom, XON = 0.8×XOFF, queue = radix×BDP — the
  same shared-buffer provisioning rule as the isolation experiments, thesis
  Table 4.1), recorded per row in `pfc_high`/`pfc_low`/`intranode_q`.

**Fair INC endpoint.** The INC `coll` arm is paced through the **same intranode
NIC** as the decomposed baseline (line-rate serialization, still ACK-less — no
CWND/RTO/pull-pacer), so both arms pay identical per-GPU injection cost and the
A/B isolates the in-switch fan-out/reduce saving. (Previously the INC arm dumped
straight onto the fabric pipe, so its makespan was byte-identical at every speed —
an unfair, misleading comparison.)

`-nodes` = total GPUs; `-num_gpus_per_node` = TP; INC `.groups` are node-local.

## Compute demand (annotated on every plot)

The generator's `calc` ops depend only on model math and parallelism — **not**
on link speeds and **not** on the arm. Per-rank calc demand is parsed from the
generated `.goal` (`compute_ns_per_iter` = max rank; `_mean` is also recorded)
and annotated as `sum(calc) / makespan`. For the PP=1 case-study graph the calc
nodes form the serial compute chain, so this ratio is the modeled compute share;
for an arbitrary graph with parallel branches it is only a demand ratio.

At the main **4000/400 Gbps** point with `--batch 32`, the revised model gives:

| TP width | calc / iter | baseline share | INC share |
|---:|---:|---:|---:|
| 4  | 68.5 ms | 71.2% | 77.3% |
| 8  | 43.5 ms | 57.9% | 68.8% |
| 16 | 31.5 ms | 48.4% | 62.0% |

The old model gave 234.3/129.6/77.2 ms and as much as 92.7% at this same
point. The revised percentages are simulator outputs, not targets baked into
the formula. TP4 INC remains compute-heavy even at the hardware ceiling; a
lower value there would require a different workload or measured overlap, not
another arbitrary efficiency constant.

## Internode sweep mode (`--mode internode`)

The inverse experiment: intranode FIXED (`--intranode_gbps`, default 4000),
inter-node link speed swept (`--so_speeds`, default `100,200,400,800,1600`; 800 ≈
current-gen per-GPU scale-out NIC, 1600 next-gen; all must divide 8000). Both the
generated scale-out topo pipes AND the scale-out NIC (`-linkspeed`) carry the
swept rate — the same network/endpoint consistency rule as the intranode mode.
Expectation: a faster scale-out fabric shrinks the fixed DP/PP floor, the TP
collective becomes a larger share of the critical path, and INC's speedup RISES.
Outputs carry the model tag, for example `sweep_internode_su4000_h100_te.csv`
and `internode_linkspeed_{time,speedup}_pp1_su4000_h100_te.png`.

## Run (Docker)

```bash
docker build -t atlahs-sim simulation-scripts/
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim build
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim run intranode_linkspeed_sweep --validate
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim run intranode_linkspeed_sweep --batch 32
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim run intranode_linkspeed_sweep --batch 32 --speeds 4000
```

For the unattended thesis matrix (TP4/8/16 scale-up sweep, scale-out
sensitivity at fixed 4000 Gbps, skeleton, consolidated figures):

```bash
ATLAHS_SIM_JOBS=4 simulation-scripts/experiments/intranode_linkspeed_sweep/run_case_study.sh
```

Set `ATLAHS_RUN_SU8000=1` to add the non-headline 8000-Gbps sensitivity.

Useful flags: `--internode_gbps N` (scale-out fabric bandwidth; default 400),
`--speeds 400,2000,8000` (subset; every value must divide 8000 — integer
ps/B), `--total_gpus N --tps T1,T2` (experiment scale: N endpoints split
TP×DP×PP with gpus/node = TP; e.g. `--total_gpus 32 --tps 8` = 4 nodes × 8 GPUs,
TP8·DP4·PP1 + TP8·DP2·PP2; non-16 scales suffix every output with `_g<N>` and
generate the scale-out topo at N hosts), `--layers N`, `--iters N`,
`--compute_model {h100_te,h100}` (every output carries its model suffix, so
neither can overwrite the other or historical unsuffixed results), `--no-plot`,
`--only-plot` (re-render PNGs from an existing model-tagged sweep CSV — pair with the
matching `--internode_gbps`), `--speedup` (plot INC speedup = baseline/INC vs
intranode speed for the PP=1 configs at `--internode_gbps`, two lines TP4·DP4 /
TP2·DP8; no sim), `--validate` (build + check the
16-GPU/INC-group layout, no sim).

## Output (`results/intranode_linkspeed_sweep/`)

- `sweep_ib<N>_<model>.csv` (N = inter-node Gbps) — one row per
  (config, speed, arm);
  columns include `time_per_iter_s`, `makespan_ns`, `drops`, `status`,
  `intranode_linkspeed_mbps`, `so_gbps`, `compute_model`, and the full simulator
  `command`.
- `intranode_linkspeed_pp1_ib<N>_<model>.png` — one per bandwidth, scale and model.
- `intranode_linkspeed_speedup_pp1_ib<N>_<model>.png` (via `--speedup --internode_gbps N`)
  — INC speedup (baseline/INC) vs intranode speed, PP=1, two lines (TP4·DP4,
  TP2·DP8) at that inter-node bandwidth.

## Notes

- Depends on the `simple_sim` generator (`goal_gen/ai/nccl_generator_v2`), driven
  via CLI with `EMIT_INC=0/1`; the generator's core graph-build needs only
  numpy/scipy/tqdm (its viz/aggregate stack is guarded out in
  `simple_sim/__init__.py`), all in the sim image.
- Sanity checks worth eyeballing: `drops == 0` (scale-out DCTCP + intranode
  lossless PFC both holding); INC ≤ baseline at every speed; **both** arms slope
  down with intranode link speed (the INC coll is now NIC-paced, so it is no
  longer link-speed-insensitive — the fair A/B), converging toward a common floor
  at high speed where the intranode link stops being the bottleneck and the fixed
  scale-out DP/PP floor dominates.
- Caveat (the honest framing): `h100_te` is the optimized-compute bound of a
  synthetic two-layer proxy. Keep `h100` as the conservative sensitivity and do
  not describe either as measured GPU time. This is a "how much INC survives in
  a training dependency graph" embedding, not an isolated-collective benefit
  (that is `scaleup_coll_ab`).
