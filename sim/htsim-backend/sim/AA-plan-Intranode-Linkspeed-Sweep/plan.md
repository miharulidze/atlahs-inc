# AA-plan — Intranode Link-Speed Sweep experiment (INC vs baseline, trace-free)

Status: **IMPLEMENTED** (2026-07-21; approved + built). See §12 for the findings
that changed the sim invocation vs the proposal.
Owner: Wanja Stämpfli. Deliverable: a new `simulation-scripts/` experiment
reproducing a "Tuning Intranode Link Speed" figure for our INC datapath.

## 1. Goal

Reproduce the shape of Zhiyi's *"Tuning Intranode Link Speed"* figure, but as an
**INC-vs-baseline A/B on purely synthetic, trace-free workloads**:

- x-axis: intranode (scale-up) link speed, log-scaled — 100, 200, 400, 800,
  1600, 3200, 3600, 6400, 12800 Gbps.
- y-axis: time per training iteration (s).
- **Drop** the "Measured Real Workload (2.069 s)" horizontal line and the
  "Network Bandwidth Used in Trace Collection (200 Gbps)" vertical line.
- **Keep** the "NVLink Bandwidth (3600 Gbps)" vertical reference line.

Two synthetic arms per config — **INC** (`EMIT_INC=1`) and **baseline**
(`EMIT_INC=0`) — of a Llama-2-7B training iteration on 16 GPUs.

## 2. Why trace-free `simple_sim`, not Zhiyi's "synthetic"

Zhiyi's 3 "synthetic" curves came from `nccl_generator_v2/main.py`, which
*consumes a captured nsys trace* (`-i traces/<workload>/sqlite/`) and re-models
its NCCL collectives; TP/PP/DP are baked in at capture time. Those captures live
under `data/ai/…` and are **not in the repo**, so that path is not reproducible
here. `simple_sim` builds the Llama graph **analytically from config alone** — no
capture, no GPU — which is exactly the trace-free contract of `simulation-scripts`
and the only way to synthesize our four parallelism configs. Both paths support
`EMIT_INC`, so the INC/baseline A/B is unaffected by the choice.

## 3. The four configs, split into two plots by PP

All 16 GPUs. Layout is **multi-domain**: TP lives *inside* a scale-up domain (the
swept intranode link); DP/PP cross domains on a fixed scale-out fabric. This falls
out of the generator: `simple_sim2goal.derive_gpus_per_node()` returns the TP
degree (the INC-eligible group size), so `gpus_per_node = TP`, `nodes = 16/TP`.

| Plot | Config (TP·DP·PP) | gpus/node (=TP) | nodes (=16/TP) |
|------|-------------------|-----------------|----------------|
| **Plot 1 — PP=1** | (4, 4, 1) | 4 | 4 |
| **Plot 1 — PP=1** | (2, 8, 1) | 2 | 8 |
| **Plot 2 — PP=2** | (4, 2, 2) | 4 | 4 |
| **Plot 2 — PP=2** | (2, 4, 2) | 2 | 8 |

Each plot: 2 configs × {baseline solid, INC dashed} = **4 curves** + the NVLink
3600 Gbps vertical line. 8 goal files, 8 curves total across the two PNGs.

## 4. Model configuration (thesis-ready rationale)

Match Zhiyi's **Llama-2 7B** *per-layer* dimensions so the collective message
sizes — hence the bandwidth→latency knee — land in the plotted range, but use a
**shallow stack** for tractable sweeps:

| Param | Value | Source / reason |
|-------|-------|-----------------|
| hidden_size | 4096 | = Zhiyi 7B (already `simple_sim` default) |
| ffn_hidden | 11008 | = Zhiyi 7B (override the 14336 default) |
| heads / kv | 32 / 32 (MHA) | = Zhiyi 7B |
| seq_len | 4096 | = Zhiyi 7B — **must match**: sets the knee position |
| batch (micro) | 1 | = Zhiyi 7B |
| **num_layers** | **2** | reduced — see invariance argument |
| iterations | 2 | `simple_sim` default; metric divides it out |
| `COMPUTE_MODEL` | **h100** | explicit; sets a realistic compute floor |

**Depth-invariance argument (why 2 layers is legitimate):** a training iteration
is the same transformer block repeated L times. Per-layer TP-collective *size* is
`batch·seq·hidden·2B` (independent of L); both comm bytes and compute scale ∝ L.
So the curve is `L × (per-layer shape)` — a vertical stretch that leaves the knee
position and the INC/baseline ratio invariant. The INC/baseline ratio itself is
set by the **TP group size N** (ring moves ≈2(N-1)/N× bytes; INC ≈1×), not by
model size. Congestion at larger message sizes only *favours* INC, so
extrapolating shallow→deep is conservative. Thesis line: *"we hold per-layer
message size at 7B/seq-4096 scale so the transition is realistic, and use a
shallow stack since iteration time and the INC gap scale linearly in depth."*

`COMPUTE_MODEL=h100` cancels in the INC-vs-baseline *gap* but sets where both
curves flatten; default `placeholder` would give an unrealistic floor.

## 5. Components & data flow

```
run.py (Docker: `run intranode_linkspeed_sweep`)
  for cfg in CONFIGS (4):
    1. build per-device graphs  -> simple_sim.build_full_training(tp,dp,pp,cfg_dims)  -> tmp/<cfg>/graphs/*.pkl
    2. baseline goal  <- simple_sim2goal (EMIT_INC=0)  -> llama.goal
       INC goal + .groups <- simple_sim2goal (EMIT_INC=1, COMPUTE_MODEL=h100)
    3. compile both -> .bin  (common.goal.compile_goal / coll txt2bin)
    for speed in SWEEP (9):
      4. sim.run_sim(bin, so_topo, su_topo, nodes=16/tp, gpus_per_node=tp,
                     groups=<inc only>, intranode_linkspeed=speed*1000)  -> makespan_ns
      5. time_per_iter_s = makespan_ns / iterations / 1e9  -> CSV row
  6. plot: read CSV -> two PNGs (split by PP), matplotlib
```

Reuses the `common/` library verbatim: `paths` (env-overridable roots),
`goal.compile_goal` / `require_*`, `sim.run_sim` + `parse_makespan`,
`report.CsvAppender`. `COMPUTE_MODEL` and `EMIT_INC` are passed as env to the
`simple_sim2goal` subprocess (matching how they are read at import today).

CSV schema: `plot, config, tp, dp, pp, arm, intranode_linkspeed_mbps,`
`makespan_ns, iters, time_per_iter_s, drops, status, su_topo, so_topo,`
`compute_model, engine, command`.

## 6. Generator changes (the substantial part; `nccl_generator_v2` submodule)

Two localized, **backward-compatible** edits (defaults preserve current output
byte-for-byte):

1. `simple_sim/llama3_training.py` — `build_full_training(...)` currently
   hardcodes `tp_size/dp_size/pp_size` and the `Llama3Config` inside the body.
   Add them as keyword args (defaults = current values); parameterize `__main__`
   with argparse (`--tp --dp --pp --num-layers --seq-len --ffn --graphs-dir`),
   so a config can be built into a chosen output dir.
2. `simple_sim2goal.py` — `__main__` hardcodes `llama3_graphs/` in and
   `llama3.goal`/`llama3_inc.goal` out. Add argparse (`--graphs-dir`,
   `--out-goal`, `--groups`) defaulting to today's names.

These are committed on the generator's `simple-sim-coll` branch (the pinned
mirror). No htsim/pcm C++ changes.

## 7. Topology / the pipe-cap question (verify early)

The sweep is driven by `-intranode_linkspeed` (Mbps = Gbps×1000). Open question:
does the flag alone govern the intranode rate, or does the `.topo` fabric pipe
also cap it?
- **Plan A (preferred, simpler):** one intranode scale-up `.topo` provisioned at
  the *max* swept speed (12800 Gbps); sweep the flag downward. Since the flag sets
  the (slower) NIC/copy-engine rate, it governs whenever `flag ≤ topo`.
- **Plan B (reference-faithful fallback):** generate a per-speed intranode topo
  (TP hosts, bidirectional GB/s convention) for each sweep point, matching the
  flag — this is what `run_goal_workloads_exp.py` did (paired `tree16_bwXGbps.topo`
  + `-intranode_linkspeed X000`).

**Verification gate:** before running the full sweep, confirm against
`htsim_app_atlahs.cpp` (`-intranode_linkspeed` → `speedFromMbps`, how it's applied
to the copy engine vs the fabric pipe) and a 2-point smoke test (100 vs 12800 with
a fixed fast topo) that Plan A produces distinct, monotonic makespans. If the flag
does *not* govern independently, fall back to Plan B. Scale-out topo:
`tree16_bw200Gbps.topo` (≥ 8 hosts, carries only DP/PP, fixed).

## 8. Plot spec (match the reference visually)

matplotlib added to `simulation-scripts/Dockerfile` (`pip install matplotlib`).
Per PNG: log-x (`set_xscale('log', base=2)`) with the 9 speeds as explicit ticks;
y in seconds; one color per config; **solid = baseline, dashed = INC**; markers
(o / ^); `axvline(3600, ls='--')` labelled "NVLink Bandwidth (3600 Gbps)"; title
"Tuning Intranode Link Speed (PP=1)" / "(PP=2)"; y-label "Time / Training
Iteration (s)"; x-label "Intranode Link Speed (Gbps)"; legend. Output:
`results/intranode_linkspeed_sweep/{sweep.csv, intranode_linkspeed_pp1.png,
intranode_linkspeed_pp2.png}`.

## 9. Alternatives considered

- **Zhiyi's `main.py` synthetic path** — rejected: needs captured 7B traces not
  in the repo; breaks the trace-free contract.
- **Full 32-layer 7B** — rejected as default: ~16× heavier sweep for a purely
  vertical y-scale; offered as a one-shot "headline figure" option if wanted.
- **Single scale-up domain (all 16 in one node)** — rejected: user chose the
  realistic multi-domain layout (TP intranode, DP/PP scale-out).
- **CSV-only, plot on host** — rejected: user requires everything in Docker.
- **Hand-rolled per-iteration schedule in `common/goal.py`** — rejected: would
  reimplement `simple_sim`'s compute+PP+DP structure; the generator already does
  it and gives a faithful iteration.

## 10. Testing / validation

- `--validate` (no sim): build all 4 configs, both arms; assert
  `gpus_per_node == TP`, `nodes·gpus_per_node == 16`, INC `.groups` has `16/TP`
  groups of size `TP`; compile all 8 `.bin`.
- Pipe-cap smoke test (§7) before the full sweep.
- Determinism: a repeated point must reproduce makespan bit-identically (the
  discrete-event sim is deterministic).
- Sanity of shape: INC ≤ baseline at every speed; both monotonically decreasing;
  both flatten by ~3600–6400 Gbps (knee in range → validates the seq-4096 choice).
- Loud TODO-marked guardrails on all derived counts (per `.cm`/input convention).

## 11. File change list

- **new** `simulation-scripts/experiments/intranode_linkspeed_sweep/run.py`
- **new** `simulation-scripts/experiments/intranode_linkspeed_sweep/README.md`
- **edit** `simulation-scripts/Dockerfile` (+matplotlib) — rebuild image
- **edit** `goal_gen/ai/nccl_generator_v2/simple_sim/llama3_training.py` (submodule)
- **edit** `goal_gen/ai/nccl_generator_v2/simple_sim2goal.py` (submodule)
- **new** `simulation-scripts/topo_files/scaleup_single_switch_{4,2}_12800Gbps.topo`
  and `tree16.topo` (see §12)
- **edit** `goal_gen/ai/nccl_generator_v2/simple_sim/__init__.py` (guard viz/aggregate
  re-exports so the core graph-builder imports without matplotlib/pandas/dask)

## 12. Implementation findings (resolved during build, 2026-07-21)

Four things the build settled, all verified against the proven `llama3_ab_pcm`
runner and the reference `scripts/run_goal_workloads_exp.py`:

1. **§7 pipe-cap: Plan A confirmed** — `-intranode_linkspeed` (NIC/copy-engine)
   and the `.topo` fabric pipe are in-series limiters (effective = min), so ONE
   intranode topo pinned at the sweep max (12800 Gbps) + sweeping the flag governs
   at every point. But the intranode topo must be a **single-switch crossbar with
   exactly `gpus_per_node` hosts** (`scaleup_single_switch_<tp>_12800Gbps.topo`),
   NOT a 16-host tree — the sim indexes a per-domain vector of that size.
2. **`-nodes` = TOTAL GPUs (16), `-num_gpus_per_node` = TP.** In this sim `-nodes`
   is the total endpoint count and `-num_gpus_per_node` the scale-up-domain width;
   the scale-out topo (`tree16.topo`, 16 hosts) must match `-nodes`. Passing
   `-nodes = domains` aborted with `out_of_range __n(7) >= size(4)`.
3. **INC `.groups` must be NODE-LOCAL** (`rank % gpn`), not the generator's global
   ranks — mirroring the reference runner's `convert_groups()`.
4. **Congestion control is REQUIRED at seq 4096** (NOT needed by `llama3_ab_pcm` at
   seq 128). The decomposed baseline's ~33 MB collectives overflow the lossless
   queue; without sender pacing the makespan is buffer-dependent (0.2 s → 398 s →
   1992 s as `-q` grows) — an artifact. The reference's `-pcm_enable` + DCTCP CC
   config paces injection → 0 lossless warnings, 0 retransmits, stable makespan.
   The `_v2` config loads `libuec_dctcp_v2.so` (prebuilt in `pcm/build/lib`;
   the plain `libuec_dctcp.so` is absent), added to `LD_LIBRARY_PATH` at run time.
   So run.py uses its own CC-aware sim call (the shared `common/sim.run_sim` stays
   the minimal single-domain helper `scaleup_coll_ab` needs).

Observed shape (2-point smoke, all 4 configs): INC ≤ baseline everywhere; baseline
decreases with intranode bandwidth; INC is ~flat (the in-network coll is
link-speed-insensitive), so baseline converges *down* toward INC as the intranode
link speeds up — the intended INC value story. A residual gap remains even at
12800 Gbps (INC's lower step count). ~15.6 s/sim.

> **SUPERSEDED (2026-07-22, see §13):** the "INC is ~flat" observation was an
> *artifact of an unfair endpoint model* — the INC arm dumped straight onto the
> fabric pipe, bypassing the NIC, so its makespan was byte-identical at every
> speed. It is now NIC-paced (fair), and **both** arms slope with link speed.
> Finding #4 (CC everywhere) is likewise revised: the intranode is now CC-free.

## 13. CC-bypass + fair INC endpoint follow-up (2026-07-22)

Implemented per `AA-plan-Intranode-CC-Bypass`. Two coupled corrections to the
sweep's fidelity, both landed in the pcm-sdk sim + wired into `run_one_sim`:

1. **Per-tier CC (revises finding #4).** DCTCP is NOT physical on NVLink, which is
   a memory-semantic credit-based fabric with no end-to-end CC. The scale-up tier
   now runs **CC-free** (`-intranode_cc none`: plain `UecSrc`, native `CONSTANT`
   no-op window; only NIC line-rate serialization + lossless PFC bound the queue),
   while the **scale-out** tier keeps DCTCP (`-pcm_enable`) because it is lossy and
   would RTO-collapse without it. New CLI flags: `-intranode_cc <dctcp|none>`,
   `-lossless_high_pfc`/`-lossless_low_pfc` (PFC thresholds, packets). With no
   window loop on the intranode, the PFC pause threshold must sit below the queue
   (`high=1500 pkt`≈6.1 MB ≈2.4× the 12800 Gbps BDP, in a 20 MB queue) so PAUSE
   fires before overflow. Task-0 de-risking spike + the sweep confirm 0 lossless
   violations / 0 retransmits at every speed.

2. **Fair INC endpoint (the real fix for the "flat INC" artifact).** The INC
   `coll` sources now **serialize through the same intranode NIC** as the
   decomposed baseline (`uec_collectives.{h,cpp}`: `emit_once`→`emit_chunk` + a
   base `pump()` that re-arms via `sourceIsPending` at `_coll_nic.linkspeed()` ==
   `-intranode_linkspeed`). Still ACK-less (no CWND/RTO/pull-pacer) — the in-switch
   fan-out/reduce saving is untouched — but the per-GPU injection cost is now
   identical to the baseline, so the A/B is fair and NVLink-faithful.

**Corrected full sweep (2026-07-22, all 4 configs × 9 speeds × 2 arms = 72 runs;
0 drops, 0 lossless violations, all `ok`):** the INC arm now *moves* with link
speed (was byte-identical before).

- **PP=1 (headline, publication-clean):** both configs monotone-decreasing, INC ≤
  baseline everywhere, converging to a fixed scale-out DP/PP floor at high speed.
  `TP4·DP4·PP1` s/iter: baseline 0.1258→0.1048, INC 0.1177→0.1043; INC lead 6.4%
  @100 → ~0.5% @8000. `TP2·DP8·PP1`: baseline 0.1400→0.1258, INC 0.1389→0.1251, INC
  lead ~0.8% → ~0.6% (smaller TP group = smaller intranode share = smaller gap).
- **PP=2 (parked, see note):** correct (INC ≤ baseline 16/18 points, 0 drops) but
  jagged — the documented pipeline schedule-noise floor (±5–9%). `TP4·DP2·PP2` INC
  crosses *above* baseline at 800 (+2.3%) and 3200 Gbps (+4.4%) — ECMP/PP event-
  ordering noise, NOT the INC path (same INC code is clean under PP=1). A seed
  ensemble would smooth it; **deferred by the user 2026-07-22 (focus PP=1 first,
  revisit later).**

Both PNGs at `simulation-scripts/results/intranode_linkspeed_sweep/`.

## 14. Rework on the fixed engine (2026-07-28): exact rates, per-rate topos, derived PFC

The 2026-07-22 results predated the real INC receive datapath, the 07-26 INC
correctness fixes, the derived-PFC standard, and the 500→50 ns latency fix to
this sweep's own topo files; they are retired (the old CSVs were overwritten by
the rerun). Four changes landed together:

1. **Per-domain intranode fabrics (engine, pcm-sdk `05cdb40`).** All N scale-up
   domains had shared ONE intranode `FatTreeTopology` — every domain's ranks
   aliased onto the same queues and INC FIB/sinks, i.e. false congestion +
   collective cross-talk on every multi-domain run (this sweep included).
   Domain i's api now owns `intranode_topos[i]`. The su_api creation loop
   deliberately stays at `no_of_nodes`: shrinking it re-dealt the global
   object/flow-id stream and moved 3-tier ECMP baselines by a few ns; with the
   count preserved, single-domain isolation runs are **bit-identical** (28/28
   coll_ab cells on both canonical fabrics), so no isolation dataset moved.
2. **Exact link rates only.** htsim stores link rate as integer **ps/B**
   (= 8000/Gbps). The old grid's 3200/3600/6400 (and the 12800 topo pin)
   silently quantised. New grid: 100, 200, 400, 800, 1600, 2000, 4000, 8000
   (80…1 ps/B, all exact); non-divisors of 8000 are rejected loudly. NVLink
   3600 remains a reference line, not a simulated point.
3. **The topo carries the swept rate.** The intranode .topo is generated per
   (TP, rate) — 50 ns links / 300 ns switch, the thesis-standard latencies — so
   the fabric pipes and `-intranode_linkspeed` agree at every point (consistent
   link speed in the network AND at the sender endpoints; previously the pipes
   sat at a pinned 12800 while only the endpoint flag swept).
4. **Standard-config alignment.** `-mtu 4160` (MSS 4096) and PFC via
   `sim.pfc_config()` — XOFF = 1 BDP at the fabric's own rate minus the
   rate-derived pause headroom (generalised from the constant 50 frames, which
   it reproduces exactly at 4000 Gbps), queue = radix × BDP — instead of the
   hand-picked 1500/1200/20 MB. Per-row `pfc_high/pfc_low/intranode_q` +
   `engine` (commit hash, `-dirty`-flagged) columns record the provenance.

**Rerun (128 runs = 4 configs × 8 speeds × 2 arms × {ib100, ib200}; engine
`pcm-sdk@05cdb40`): 0 drops, all ok, INC ≤ baseline at every one of the 64
config-speed points — including PP=2, whose two above-baseline crossings under
the old engine are gone.** PP=1 INC leads: TP4·DP4 8.4% @100 → 0.5% @8000
(ib100) and 38.6% @100 → 4.3% @8000 (ib200); TP2·DP8 1.7% → 0.3% (ib100),
9.5% → 5.1% (ib200). The inter-node-bandwidth finding (§13) survives and
strengthens: a faster scale-out fabric grows INC's advantage at every intranode
speed. CSVs + PNGs at `simulation-scripts/results/intranode_linkspeed_sweep/`.

## 15. Compute-share annotation + the internode sweep (2026-07-29)

**Compute share.** The generator's `calc` ops carry H100-roofline durations fixed
by the model math and the parallelism split — independent of link speeds and
identical in both arms. Per-rank compute per iteration is therefore one number
per config, parsed from the generated `.goal` (`compute_ns_per_iter` = max rank;
mean also recorded): TP4·DP4·PP1 = 7.91 ms, TP2·DP8·PP1 = 14.15 ms (TP2 ranks do
~2× the sharded math), PP2 variants ≈ half with visible stage imbalance
(max > mean). Every plot now carries the fixed compute time + its share range
over the plotted makespans; the intranode sweep shares (6–11 % at ib100) match
the long-standing "compute is ~5–10 %" characterisation.

**Internode sweep (`--mode internode`, intranode fixed 4000 Gbps).** The inverse
experiment: sweep the scale-out rate {100, 200, 400, 800, 1600} Gbps (exact
ps/B; 800 ≈ current-gen per-GPU NIC), with the generated scale-out topo pipes
AND the scale-out NIC (`-linkspeed`) at the swept rate. 40 runs on
`pcm-sdk@05cdb40`: 0 drops, INC ≤ baseline everywhere.

**Finding: the speedup rises, then compute caps it.** TP4·DP4 climbs 1.026 →
1.065 (100 → 400 Gbps) and then PLATEAUS at ~1.064; TP2·DP8 peaks at 1.049
(200 Gbps) and falls to 1.019 at 1600. The mechanism is on the time plot: as
the scale-out fabric speeds up, the iteration collapses toward the fixed
compute floor (compute share grows 7 % → 59 % for TP4, 10 % → 67 % for TP2),
and Amdahl caps what any communication optimisation can contribute. The
"faster scale-out grows INC's advantage" trend from §14 holds while
communication dominates and saturates once compute takes over — TP2 saturates
earlier because its per-rank compute is 2× TP4's. Outputs:
`sweep_internode_su4000.csv`, `internode_linkspeed_{time,speedup}_pp1_su4000.png`.

## 16. Scaling to 32 GPUs = 4 nodes × 8 (2026-07-29)

Harness gained `--total_gpus / --tps` (umbrella c1a2724; defaults reproduce the
16-GPU table; non-16 scales generate the scale-out topo at N hosts and suffix
every output `_g<N>`). 32-GPU configs: TP8·DP4·PP1 (headline) + TP8·DP2·PP2 —
same 4 domains and DP-ring length as the 16-GPU TP4·DP4, double the scale-up
domain width, so the comparison isolates TP-group scaling.

**Result: doubling the scale-up domain roughly doubles INC's leverage.**
All runs on `pcm-sdk@05cdb40`, 0 drops, 0 lossless warnings:
- Intranode sweep, TP8·DP4 INC lead: **63% @100 Gbps (ib200)** vs 38.6% at
  TP4/16; ib100 26%→0.9% over the sweep (vs 8.4%→0.5%).
- Internode sweep (intranode 4000): speedup climbs to **1.12× @1600 Gbps and is
  still rising** where TP4/16 plateaued at 1.064×. Cause is visible in the
  compute annotation: TP8's per-rank compute is 4.64 ms/iter (vs 7.91), so its
  compute share at 1600 Gbps is only ~41–46% vs TP4's 59% — the Amdahl ceiling
  sits further out. (The 200 Gbps point dips non-monotonically — single-schedule
  sensitivity, worth an ensemble if the figure is published.)

**OPEN ENGINE BUG found by the g32 sweep (1 cell of 84): TP8·DP2·PP2, intranode
1600, ib100, INC arm — goal-loop wedge at t=0.** Reproduced 5×: two sweep
timeouts (1800 s / 3600 s solo) + three plain reruns (900 s each); marked
`timeout` in sweep_ib100_g32.csv. Diagnosis (results/_stall_debug2.log): at
virtual t=0 the first 32 sends sit in aq and none dispatches (10+ s of wall at
now_ns=0, ev_pending=1); the wedged loop then runaway-advances the clock past
-end (now_ns=3.57e12 > end=1e12) with sends_active=80 pinned on node0↔node1
PP-boundary scale-out sends, ev_pending=0, compute_started wrapping ±2^31 —
never terminating. The wedge is TIE-ORDER-DEPENDENT: the old INC_STALL_DBG dump
pop/push-reshuffled equal-time aq entries and thereby UNSTUCK it (completed,
0.0674 s/iter, in-family — the heisenbug); the dump is now read-only (submodule
4975fb9). Same family as the July `have_more` stall: the aq drain has an order-
sensitive dispatch condition at equal timestamps. The INC datapath is not
implicated (no collective ever starts; the same cell at ib200 and at every other
intranode rate completes). Fix = goal-loop dispatch made tie-order-invariant —
Zhiyi-coordination territory, deferred; the cell is excluded and PP=2 is
non-headline by standing decision.

## 17. TP16 (64 GPUs) + the TP64/256 feasibility probe (2026-07-29)

**TP16·DP4·PP1 (4 nodes × 16, PP=1 only per user): the scaling series completes.**
All runs clean (0 drops, engine `pcm-sdk@4975fb9`), compute 3.00 ms/iter/rank:
- Intranode sweep INC lead @100 Gbps: **16.2% (ib100) / 78% (ib200)**; @8000:
  1.9% / 4.5%. Series @100-ib200: TP4 38.6% → TP8 63% → TP16 78%.
- Internode sweep (intranode 4000): speedup **monotone 1.043 → 1.156 @1600 Gbps,
  no saturation in range** (compute share only 2.7–34%). Series @1600:
  TP4 1.065 (plateaued) → TP8 1.119 → TP16 1.156.
Files: `*_g64.{csv,png}`.

**TP64 (4 × 64, 256 GPUs): generator + topology now work; wall-clock does not.**
Three layers fixed/learned on the way:
1. Generator rank-id fix (`093e9c5`): 2-digit-padded graph filenames scrambled
   rank ids >= 100 via alphabetical enumerate; key by int(stem).
2. Generator ordered-emission fix (`653743d`): txt2bin serializes ranks
   SEQUENTIALLY through a jumptable (rank r chains on r-1's end offset), so
   alphabetically-ordered rank blocks compile into corrupt .bins or segfault
   (SEGV in Graph::serialize_mmap via a never-written jumptable slot); emit
   sorted. Both fixes byte-identical <= 99 ranks. TP64 also needs 64-head
   dims (TP shards attention by head; used Llama-70B-class hidden 8192 /
   ffn 28672 / heads 64) -- 4 clean groups of 64.
3. Scale-out topo >96 hosts: pcm's fat-tree parser builds aggregation PER POD;
   the working >96-host pattern is the tree1024 one -- SINGLE pod
   (Podsize == Nodes), Tier0 Radix_Down=hosts/ToR & Radix_Up=spines, Tier1
   Radix_Down=#ToRs (256 = 16 ToRs x 16 hosts, 16 spines, radix-32).
   Podsize < Nodes trips "Did you miss specifying a Tier 1 bundle size".
**Verdict: a single TP64 cell is wall-infeasible as configured** -- the INC arm
reached ~12 ms of ~135 ms virtual in 45 min (~9%, extrapolates to ~8 h/cell;
sim kept advancing -- DCTCP per-flow VM setup + 1 us scheduler polling dominate
at 256 ranks x 64 DP rings). Options if ever needed: coarser -pcm_sched_poll_delay,
iters=1, a 2-3 point grid overnight, or profiling the pcm scheduler. The
4 -> 8 -> 16 series carries the scaling argument without it.

## 18. The batch pivot: RTO artifact, b32 canonical, cross-machine parallel harness (2026-07-29 evening)

**Why Zhiyi's iterations are seconds and ours were ms:** his validation trace is
full-depth Llama-7B (32 layers) at BS32 from real captures; ours was a 2-layer,
micro-batch-1 roofline synthetic. Deliberate (depth-linear A/B), but batch turned
out to be load-bearing, not cosmetic:

**Traffic composition (measured, _scratch_composition.py):** TP activations scale
with micro-batch, DP gradients do NOT → TP share of comm bytes = 14% (b1) → 40%
(b4) → 57% (b8) → ~84% (b32). Micro-batch b is the network-level proxy for
gradient accumulation (b sequences per DP rank per optimizer step) — b32 is
squarely realistic and echoes Zhiyi's BS32.

**RTO-storm artifact found via the b≥2 probes (umbrella 1cd8479):** the fixed
scale-out `-q 1000000` (1 MB, tuned for 100G) is ~20 µs at 400G; re-timed DP
incast overflowed it and each loss stalled behind the driver's queue-derived
~1 s min RTO. b2 TP4 cell: 9.0 s/2iter with Rtx=594 @1 MB vs 84 ms with Rtx=0
@4 MB (4 MB == 16 MB byte-identical). These losses print NO line the drop regex
matches — only the end-of-run Rtx counter shows them (now folded into the CSV
drops tripwire). Also fixed: intranode mode pinned the scale-out NIC at 200G
regardless of fabric rate (violating network==endpoint); NIC now always equals
fabric. -q now scales at 10 KB/Gbps. b1 regression: byte-identical.

**b32 canonical (user-approved; saturation measured b1→1.065, b8→1.238,
b16→1.293, b32→1.323 at TP4 — TP16 NOT yet saturated).** Headline series at the
H100-class point (4000/400), 0 rtx: **TP4 1.323× / TP8 1.648× / TP16 2.155×** —
INC more than halves the TP16 iteration (235→109 ms); the INC arm is ~88%
compute (communication leaves the critical path) vs the baseline's 59% comm.

**Parallel + distributed harness:** `--jobs N` runs sweep cells concurrently
(thread pool over single-threaded htsim subprocesses; CSV order and values
byte-identical to sequential). Second MacBook enlisted over SSH (12 cores;
~/atlahs-sim-work rsync'd — NOT ~/Desktop, which macOS TCC blocks over SSH;
stale cmake build/ dirs must be rm'd after rsync or FetchContent's gitupdate
fails). Cross-machine determinism verified byte-identical. Split: local =
intranode sweeps, remote = internode su4000 g32/g64 + su8000 ×3.
