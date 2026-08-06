# Thesis simulation artifact

This directory is the CPU-only reproducibility harness for the thesis experiments on
in-network collectives. It builds the two-tier pcm-sdk/htsim backend and the
collective-aware GOAL compiler, then runs synthetic, GOAL-driven experiments without a
GPU or the full ATLAHS tracing stack.

Start with [`REPRODUCING.md`](REPRODUCING.md) when reproducing a result or preparing a
paper artifact. Use [`PUBLICATION_CHECKLIST.md`](PUBLICATION_CHECKLIST.md) before cutting
the supervisor/workshop release. [`REFERENCE_DATA.md`](REFERENCE_DATA.md) records which
historical revision produced each tracked dataset and why a current release must be
rerun as one coherent result set.

## Supported scope

| experiment | status | purpose |
|---|---|---|
| `scaleup_coll_ab` | canonical thesis | Chapter 4 completion-time A/B: in-network collectives versus endpoint decompositions |
| `scaleup_coll_footprint` | canonical thesis | Chapter 4 byte-link footprint reduction |
| `ch5_accumulation` | canonical thesis | Chapter 5 accumulation-corrected training case study |
| `scaleup_pfc_concurrent` | validation | Lossless-backpressure census, stress cases, and negative controls |
| `scaleup_ar_bandwidth` | supplementary | Fused-apex versus composed ReduceScatter+AllGather comparison |
| `intranode_linkspeed_sweep` | superseded | Earlier case-study harness, retained for provenance; use `ch5_accumulation` instead |

The container's `list` command prints the same classification. The three canonical
experiments are the publication path; supplementary and superseded runners must not be
silently mixed into the headline result set.

## Model boundary

These are deterministic, synthetic-network simulations. The frozen experiment runners
select the exact topology, workload, collective decomposition, and simulator flags; the
presence of other topology files or runners does not make them part of the thesis
artifact.

The scale-up fabric uses a congestion-control-free, hop-by-hop, PFC-style lossless
backpressure abstraction. Pause thresholds include propagation headroom, and queue
bounds cover the configured worst-case fan-in. The validation suite checks that the
abstraction engages and remains within its per-queue bounds for the evaluated workloads.

This is not a standards-compliant PFC or CBFC implementation. It models one link-wide
traffic class, not per-VC credit state or a finite shared switch-memory pool. The results
therefore support the tested topologies and workloads; they do not establish arbitrary
mixed-workload deadlock freedom or hardware-faithful CBFC behavior. Replacing this model
with CBFC is future work because it would change queueing and potentially the measured
completion times.

## Setup

From the repository root:

```bash
git submodule update --init --recursive \
  sim/pcm-sdk_zhiyi goal_gen/ai/nccl_generator_v2

# The build context contains only this Dockerfile and entrypoint, not the full checkout.
docker build -t atlahs-sim simulation-scripts/

# Build the simulator and collective-aware txt2bin in the bind-mounted checkout.
# --user avoids root-owned outputs on native Linux hosts.
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace atlahs-sim build

docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace atlahs-sim list
```

The build overlays the patch directories into the nested simulator source trees before
compiling. Generated build products and those overlay copies may make submodule
worktrees appear dirty; do not confuse them with a new publication revision. Release
commits must be made before building and must record clean, public submodule gitlinks.

Run the lightweight artifact gates with:

```bash
simulation-scripts/validate_artifact.sh
```

This checks the analytic/data claims, generates and compiles the canonical and
flow-control-validation arms, and runs the packet-level collective/domain regression
suites. Pass `--extended` to include retained supplementary and superseded runners. Full
experiment matrices are separate because Chapter 5 is multi-hour and memory-intensive.
`--pfc-smoke` adds a packet-level PFC instrumentation run, but constructing its 256-host
fabric makes it substantially slower than the default gates.

## Results and provenance

Tracked files under `simulation-scripts/results/` are reference data used by the thesis.
They reproduce the thesis assets, but correctness fixes can change packet-level ECMP and
timing; do not mix historical and current rows. New runtime outputs are ignored by default.
Chapter 5 uses immutable
`runs/<run-id>/` directories and refuses to overwrite an existing run.

The historical Chapter 5 data-of-record run includes a complete ten-file source snapshot
and binary/source hashes, but it was executed in a checkout whose Git metadata and Docker
image ID were unavailable. See its adjacent `PROVENANCE.md` before claiming a byte-for-byte
runtime reconstruction. A publication release should retain the historical data and also
produce a fresh, fully pinned run from the clean public release commit.

## Layout

```text
simulation-scripts/
├── Dockerfile             CPU-only build/runtime image
├── entrypoint.sh          build | list | run <experiment>
├── validate_artifact.sh   fast publication gates
├── build_sim.py           simulator + collective compiler build
├── common/                GOAL, simulator, topology, and reporting helpers
├── experiments/           isolated experiment runners and frozen wrappers
├── model_checks/          analytic and committed-data verification
├── topo_files/            canonical, validation, and exploratory topologies
└── results/               tracked reference data plus ignored new runs
```

Paths resolve from the repository checkout and can be overridden with the environment
variables documented by each experiment. To add a development experiment, copy
`experiments/_template`; do not classify it as canonical until its command, inputs,
outputs, and validation gate are documented.
