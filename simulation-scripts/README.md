# simulation-scripts — CPU-only, device-agnostic simulation reproducibility

A self-contained Docker environment for reproducing the **simulation** results of
this thesis on any machine, without a GPU. It builds the pcm-sdk two-tier htsim
simulator (our INC datapath) and the coll-extended LogGOPSim `txt2bin`, and runs
independent, GOAL-driven experiments — nothing else.

## Why this exists (vs the top-level Dockerfile)

The repo's top-level `Dockerfile` is Zhiyi's **full ATLAHS pipeline**: it provisions
the real-workload TRACING/GENERATION toolchain (NVIDIA pytorch base, deepspeed,
flash-attn, Chakra, …) to *capture* GOAL traces from real GPU training. That build
needs a GPU box's RAM (the flash-attn compile OOMs an 8 GB laptop VM) and is
irrelevant to simulation. This folder keeps that environment untouched and adds a
**simulation-only** one: a light Linux base + the C/C++ build toolchain, which
builds anywhere Docker runs and reproduces our results deterministically.

Simulation is decoupled from real experiments: every experiment here synthesizes
or consumes GOAL traces, compiles them to the canonical `.bin`, and executes them
on the pcm-sdk backend (`htsim_flow_app_atlahs`). No torch, no GPU.

## Layout

```
simulation-scripts/
├── Dockerfile          light base (ubuntu:24.04) + build toolchain (cmake, g++, re2c, …)
├── entrypoint.sh       build | list | run <exp> [args]   (baked into the image — rebuild after editing)
├── build_sim.py        builds the pcm binary (submodule) + coll-txt2bin; no GPU deps
├── common/             shared library: paths (env-overridable), GOAL writers +
│                       txt2bin compile, simulator invocation + makespan/drop
│                       parsing, CSV/report helpers
├── experiments/        one directory per independent experiment
│   ├── scaleup_coll_ab/   the scale-up INC-vs-endpoint collective A/B (see its README)
│   └── _template/         copy-me skeleton; underscore ⇒ hidden from dispatch
├── topo_files/         .topo inputs (scale-up + scale-out; see its README)
└── results/<exp>/      experiment outputs (CSV + logs); gitignored, created at runtime
```

Reused (not duplicated): the `sim/pcm-sdk_zhiyi` submodule (the engine), the
pure-python NCCL→GOAL generator (`goal_gen/ai/nccl_generator_v2`), and the coll
grammar patch (`tools/loggopsim-coll/coll.patch`).

## Prerequisite (host)

Materialize the simulator submodule once:

```bash
git submodule update --init --recursive sim/pcm-sdk_zhiyi
```

## Reproduce

```bash
# from the repo root
docker build -f simulation-scripts/Dockerfile -t atlahs-sim .

# build the simulator + coll-txt2bin (CPU-only; ~one-time)
docker run --rm -v "$(pwd)":/workspace atlahs-sim build

# what can I run?
docker run --rm -v "$(pwd)":/workspace atlahs-sim list

# topology-independent check (no simulator run): step counts vs the NCCL paper + compile
docker run --rm -v "$(pwd)":/workspace atlahs-sim run scaleup_coll_ab --validate

# the A/B sweep — AllReduce (ring + recursive-doubling), ReduceScatter, AllGather
docker run --rm -v "$(pwd)":/workspace atlahs-sim run scaleup_coll_ab --n 8 --su-topo <topo>
```

Results land in `simulation-scripts/results/<exp>/` (bind-mounted back to the
host). Experiments also run locally without Docker once the binaries are built
in-tree — paths resolve relative to the repo checkout, each overridable by env
var (see `common/paths.py`).

## Adding an experiment

```bash
cp -r simulation-scripts/experiments/_template simulation-scripts/experiments/<name>
```

then edit `run.py` (GOAL synthesis → `goal.compile_goal` → `sim.run_sim` →
`report.CsvAppender`) and the README. `run <name>` and `list` pick it up
automatically — no dispatch registration. Conventions: argparse CLI; results via
`paths.results_dir("<name>")`; topologies via `paths.topo(basename)`; keep loud
TODO-marked guardrails on parsed inputs during development. Details in
`experiments/_template/README.md`.

## Status / notes

- The scale-up **topology** is still a deferred decision (crossbar vs NVL72-style
  tree vs the emerging NVLink5/UALink/SUE set); `--su-topo` takes a placeholder
  until it's chosen. `--validate` needs no topology.
- Different base image than Zhiyi's full pipeline → confirm the sim produces
  identical numbers to a known run (the discrete-event sim is deterministic;
  verified bit-identical macOS-local vs Docker-Linux 2026-07-20).
- Design rationale: `sim/htsim-backend/sim/AA-plan-Scaleup-Baselines/plan.md` and
  `sim/htsim-backend/sim/AA-plan-Sim-Scripts-Restructure/plan.md` (local).
