# Reproducing the thesis experiments

Every quantitative result in the thesis comes from three experiments, all run on the
pcm-sdk engine inside the `atlahs-sim` Docker image. This file gives one recipe per
experiment: the exact frozen command, its outputs, and the script that regenerates each
thesis asset. The committed CSVs under `results/` are the data of record — the thesis can
be re-derived from them without re-running any simulation (section 4).

## 0. One-time setup

```bash
# from the repo root
git submodule update --init --recursive sim/pcm-sdk_zhiyi goal_gen/ai/nccl_generator_v2
docker build -f simulation-scripts/Dockerfile -t atlahs-sim .
docker run --rm -v "$(pwd)":/workspace atlahs-sim build     # engine + coll txt2bin (one-time)
```

Both pinned repositories are public (since 2026-08-06): `sim/pcm-sdk_zhiyi` resolves to
`wstaempfli/pcm-sdk` (branch `wanja/inc-port`) and `goal_gen/ai/nccl_generator_v2` to
`wstaempfli/nccl_generator_v2` (branch `simple-sim-coll`) — no access grants needed.

## 1. Ch. 4 — collective completion-time A/B (`scaleup_coll_ab`)

```bash
simulation-scripts/experiments/scaleup_coll_ab/run_thesis.sh      # |G|=64, 9 sizes, 2 fabrics
simulation-scripts/experiments/scaleup_coll_ab/run_groupsweep.sh  # |G| in {2..64}, 2 sizes
```

- Output (tracked): `results/scaleup_coll_ab/scaleup_coll_ab.csv`,
  `results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv`.
- Thesis tables: `python3 simulation-scripts/_gen_rsag_tables.py` writes
  `tab_duality.tex`, `tab_bcast_validation.tex`, `tab_rsag_validation.tex`,
  `tab_ar_validation.tex` to `$RSAG_OUT` (default `~/CLionProjects/thesis-skeleton/figures`).
  Repo root is auto-detected; override with `ATLAHS_ROOT`.
- Thesis figures: `_gen_collective_plots.py` (run in the container) writes the
  regime/speedup PDFs to `results/scaleup_coll_ab/thesis_figs/`; copy manually to
  `thesis-skeleton/figures/matplotlib/`.

## 2. Ch. 4 — network footprint (`scaleup_coll_footprint`)

```bash
simulation-scripts/experiments/scaleup_coll_footprint/run_thesis.sh
```

- Output (tracked): `results/scaleup_coll_footprint/scaleup_coll_footprint.csv`.
- Figures: `_gen_collective_plots.py` (`fig_footprint`), same manual-copy step as above.

## 3. Ch. 5 — accumulation-corrected case study (`ch5_accumulation`)

Canonical run (data of record, tracked):
`results/ch5_accumulation/runs/ch5-ga32-headline-iters1-mem16-4000-400-20260731T1405Z`,
produced inside the container by:

```bash
python3 /workspace/simulation-scripts/experiments/ch5_accumulation/run.py \
  --tps 4,8,16 --accumulations 32 --points 4000:400 --iters 1 --jobs 1 --timeout 7200 \
  --run-id <fresh-run-id>
```

(hours; needs ≥16 GB for the TP16 cell). The run's `manifest.json` pins the simulator /
txt2bin / CC-config sha256s; `audits.json` verifies the emitted collective counts and bytes.

Thesis assets (figure + both tables + summary JSON) regenerate from the run alone:

```bash
docker run --rm -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
  /workspace/simulation-scripts/experiments/ch5_accumulation/plot.py \
  --headline-run /workspace/simulation-scripts/results/ch5_accumulation/runs/ch5-ga32-headline-iters1-mem16-4000-400-20260731T1405Z \
  --output-dir /workspace/simulation-scripts/results/ch5_accumulation/<new-dir>
```

Both `.tex` tables are byte-identical to the thesis copies (verified 2026-07-31); the
simulator-model TP-factor column is computed from eq:ar-speedup at 32 MiB, and the
collective-inventory table is built from `audits.json`.

## 4. Verification without re-running anything

```bash
python3 simulation-scripts/model_checks/verify_models.py   # exit 0 = all green
```

re-derives every Ch. 4 model claim from the committed CSVs, plus the Ch. 5 quoted
speedups, the simulator-model TP factors, and the hand-typed `tab:instantiation` /
`tab:ring-rtt` / PFC values.

## 5. Anchor numbers

| where | value |
|---|---|
| Ch. 5 end-to-end speedups (TP4/8/16) | 1.101 / 1.232 / 1.405 |
| simulator-model TP factors @ 32 MiB | 1.562 / 1.905 / 2.218 |
| ring-step λ (d = 1/2/3) | 808 / 2225 / 3642 ns |
| PFC pause/resume (crossbar; 3-tier) | 218/174; 389/311 (1×BDP = 268 / 439 packets) |

## 6. Supplementary (not thesis assets)

`scaleup_pfc_concurrent` (three-legged lossless proof), `scaleup_ar_bandwidth`
(apex vs composed RS+AG), `intranode_linkspeed_sweep` (superseded for Ch. 5 by
`ch5_accumulation`), `model_checks/` probe scripts, `isolation-plots/` (supervisor deck;
`build_deck.py`). Each has its own README. Scripts with container-absolute
`/workspace/...` paths (`_scratch_*`, `model_checks/fold_*_test.py`, `build_sim.py`)
are Docker-only by design.

The htsim fork (`sim/htsim-backend`) is the thesis's implementation artifact (Design
chapter); it feeds no quantitative thesis asset. Its INC unit tests run with
`make -C sim/htsim-backend/sim/tests check`; the bcast subprocess suite is
`sim/htsim-backend/sim/datacenter/connection_matrices/tests/test_bcast.py`.
