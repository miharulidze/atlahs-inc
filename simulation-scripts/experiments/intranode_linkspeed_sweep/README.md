# intranode_linkspeed_sweep — INC vs baseline, trace-free Llama iteration

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

Zhiyi-matched Llama-2 7B **per-layer** dims (hidden 4096, FFN 11008, 32 MHA
heads, seq 4096, micro-batch 1) so message sizes place the bandwidth→latency knee
in the plotted range, with a **shallow stack** (default 2 layers): iteration time
and the INC gap scale ~linearly in depth, so the trend extrapolates to full 7B
(a conservative bound — larger messages only favour INC via congestion).
`COMPUTE_MODEL=h100` sets a realistic compute floor (cancels in the INC gap).

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
wire — the default grid is `100, 200, 400, 800, 1600, 2000, 4000, 8000`
(80…1 ps/B). 3200/3600/6400/12800 silently quantise and are rejected loudly;
the NVLink 3600 Gbps line on the plots is a reference line, not a simulated
point.

**Scale-out (DP/PP, fixed per run, selectable via `--internode_gbps`):**
`tree16_nonblocking_{100,200}Gbps.topo` — 16 hosts on one **non-blocking** switch.
The earlier 2:1-oversubscribed `tree16` penalised TP4 (its DP ring lands
one-per-rack → 100 % cross-rack through the squeezed uplinks) and added ECMP
routing noise; non-blocking removes both confounds without changing link speed
(structure, not speed). `--internode_gbps 100` (default) or `200` sets the
inter-node bandwidth; at 200 the fabric pipe matches the scale-out NIC exactly
(`-linkspeed 200000`), so there is no NIC/fabric mismatch, at 100 the pipe is the
binding constraint. Outputs are suffixed `_ib<N>` so both variants coexist.

**Congestion control (per-tier).** The two tiers model different fabrics, so they
run different CC (see `AA-plan-Intranode-CC-Bypass`):

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

`-nodes` = TOTAL GPUs (16); `-num_gpus_per_node` = TP; INC `.groups` are node-local.

## Compute share (annotated on every plot)

The generator's `calc` ops carry H100-roofline durations that depend only on the
model math and the parallelism split — **not** on link speeds and **not** on the
arm (both arms share the same calc ops). So per-rank compute per iteration is one
fixed number per config, parsed from the generated `.goal` (`compute_ns_per_iter`
= max rank, `_mean` also recorded) and annotated on each plot as the fixed
compute time + its share range over the plotted makespans. The compute model is
a coarse roofline (not representative in absolute terms); the share is reported
to size the communication-dominance of the workload, ~5–10 % at these configs.

## Internode sweep mode (`--mode internode`)

The inverse experiment: intranode FIXED (`--intranode_gbps`, default 4000),
inter-node link speed swept (`--so_speeds`, default `100,200,400,800,1600`; 800 ≈
current-gen per-GPU scale-out NIC, 1600 next-gen; all must divide 8000). Both the
generated scale-out topo pipes AND the scale-out NIC (`-linkspeed`) carry the
swept rate — the same network/endpoint consistency rule as the intranode mode.
Expectation: a faster scale-out fabric shrinks the fixed DP/PP floor, the TP
collective becomes a larger share of the critical path, and INC's speedup RISES.
Outputs: `sweep_internode_su<N>.csv`, `internode_linkspeed_{time,speedup}_pp1_su<N>.png`.

## Run (Docker)

```bash
docker build -f simulation-scripts/Dockerfile -t atlahs-sim .
docker run --rm -v "$(pwd)":/workspace atlahs-sim build            # one-time
docker run --rm -v "$(pwd)":/workspace atlahs-sim run intranode_linkspeed_sweep --validate
docker run --rm -v "$(pwd)":/workspace atlahs-sim run intranode_linkspeed_sweep                      # inter-node 100 Gbps (default)
docker run --rm -v "$(pwd)":/workspace atlahs-sim run intranode_linkspeed_sweep --internode_gbps 200 # inter-node 200 Gbps
```

Useful flags: `--internode_gbps {100,200}` (scale-out fabric bandwidth; default
100), `--speeds 100,2000,8000` (subset; every value must divide 8000 — integer
ps/B), `--layers N`, `--iters N`, `--no-plot`,
`--only-plot` (re-render PNGs from an existing `sweep_ib<N>.csv` — pair with the
matching `--internode_gbps`), `--speedup` (plot INC speedup = baseline/INC vs
intranode speed for the PP=1 configs at `--internode_gbps`, two lines TP4·DP4 /
TP2·DP8; reads that `sweep_ib<N>.csv`, no sim), `--validate` (build + check the
16-GPU/INC-group layout, no sim).

## Output (`results/intranode_linkspeed_sweep/`)

- `sweep_ib<N>.csv` (N = inter-node Gbps) — one row per (config, speed, arm);
  columns include `time_per_iter_s`, `makespan_ns`, `drops`, `status`,
  `intranode_linkspeed_mbps`, `so_gbps`, `compute_model`, and the full simulator
  `command`.
- `intranode_linkspeed_pp1_ib<N>.png`, `intranode_linkspeed_pp2_ib<N>.png` — one
  pair per `--internode_gbps` value (e.g. `_ib100`, `_ib200`).
- `intranode_linkspeed_speedup_pp1_ib<N>.png` (via `--speedup --internode_gbps N`)
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
- Caveat (the honest framing): the makespan is ~90 % DP/PP communication over the
  scale-out fabric (compute is ~5–10 %), so the intranode sweep — and hence the INC
  gap — is a MODEST slice of the iteration. This is a "how much INC survives when DP
  is in play" embedding, not an isolated-collective benefit (that's `scaleup_coll_ab`).
  Any residual baseline wiggle is ECMP routing/schedule sensitivity (a few %), not
  physical bandwidth-dependence.
