# Thesis experiment suite

This directory contains the CPU-only simulation setup used for the thesis experiments
on in-network collectives. It builds the PCM/HTSim backend and the collective-aware
GOAL compiler, then runs the experiments without requiring GPUs or the full ATLAHS
tracing stack.

Start with [`REPRODUCING.md`](REPRODUCING.md) for the commands. See
[`REFERENCE_DATA.md`](REFERENCE_DATA.md) before comparing a new run with the committed
thesis results.

## Experiments

| experiment | thesis result |
|---|---|
| `scaleup_coll_ab` | Chapter 4 completion time: in-network collectives versus endpoint implementations |
| `scaleup_coll_footprint` | Chapter 4 reduction in network byte-link footprint |
| `ch5_accumulation` | Chapter 5 accumulation-corrected training case study |

`scaleup_pfc_concurrent` is an optional robustness test for the simulator's lossless
backpressure model; it is not a performance result.

## Model scope

The experiments use deterministic synthetic workloads and networks. Their runners fix
the topology, workload, collective implementation, and simulator flags.

The scale-up network uses a one-class, hop-by-hop, PFC-style lossless-backpressure
abstraction. It is not a standards-compliant PFC or CBFC implementation and does not
model per-virtual-channel credits or a finite shared switch-memory pool. The results
therefore apply to the evaluated topologies and workloads. Replacing this abstraction
with CBFC could change queueing and completion times.

## Build

From the repository root:

```bash
git submodule update --init --recursive \
  sim/pcm-sdk_zhiyi goal_gen/ai/nccl_generator_v2

docker build -t atlahs-sim simulation-scripts/
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace atlahs-sim build
```

The build overlays the PCM patch directories into its nested simulator sources. Those
generated copies and build products may make nested submodules appear modified.

To list the available runners:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace atlahs-sim list
```

As an optional quick check of the setup:

```bash
simulation-scripts/validate_artifact.sh
```

This generates and compiles representative traces, checks the committed data, and runs
the packet-level collective regression tests. It does not run the full experiment
matrices.

## Results

Committed files under `simulation-scripts/results/` are the reference results used to
regenerate the existing figures and tables. The frozen wrappers stage complete runs under
`results/generated-runs/<run-id>/` before updating their result CSV.

Chapter 5 writes each invocation to a separate immutable `runs/<run-id>/` directory.
Its canonical run includes the executed source inputs and hashes; details are in the
adjacent `PROVENANCE.md`.

## Directory layout

```text
simulation-scripts/
├── Dockerfile             CPU-only build/runtime image
├── entrypoint.sh          build | list | run <experiment>
├── validate_artifact.sh   optional quick validation
├── build_sim.py           simulator and collective compiler build
├── common/                shared trace, simulator, topology, and reporting code
├── experiments/           experiment runners and frozen wrappers
├── model_checks/          automated consistency checks
├── topo_files/            topology definitions
└── results/               committed reference data and generated runs
```
