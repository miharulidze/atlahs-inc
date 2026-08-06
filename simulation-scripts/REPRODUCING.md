# Reproducing the thesis experiments

The three experiments below produce the thesis headline results. The committed CSVs
and Chapter 5 run preserve the historical thesis measurements, so the existing figures
and tables can be regenerated without rerunning the simulations.

The current backend contains later correctness fixes. Treat results produced by the
current code as a separate dataset rather than combining them with committed rows. See
[`REFERENCE_DATA.md`](REFERENCE_DATA.md) for details.

## 1. Build the simulator

From the repository root:

```bash
git submodule update --init --recursive \
  sim/pcm-sdk_zhiyi goal_gen/ai/nccl_generator_v2

docker build -t atlahs-sim simulation-scripts/
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace atlahs-sim build
```

The host needs Docker and enough free memory for the chosen experiment. The full
Chapter 5 TP16 run needs at least 16 GiB.

You can optionally verify the setup before launching a full matrix:

```bash
simulation-scripts/validate_artifact.sh
```

This is a quick trace, data, and packet-level regression check; it does not run the
full experiments.

## 2. Chapter 4: collective completion time

This experiment compares in-network collective operations with Ring and
Recursive-Doubling endpoint implementations. It covers group size 64 on a single
switch and a three-tier fabric; the companion sweep varies the group size from 2 to 64.

```bash
simulation-scripts/experiments/scaleup_coll_ab/run_thesis.sh
simulation-scripts/experiments/scaleup_coll_ab/run_groupsweep.sh
```

The result CSVs are:

- `simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv`
- `simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv`

Regenerate the LaTeX tables and plots with:

```bash
python3 simulation-scripts/_gen_rsag_tables.py

docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
  /workspace/simulation-scripts/_gen_collective_plots.py
```

The plots are written to
`simulation-scripts/results/scaleup_coll_ab/thesis_figs/`. Set `RSAG_OUT` to write the
tables to a different directory.

## 3. Chapter 4: network footprint

This experiment measures byte-link crossings for the in-network and endpoint
collective implementations on the two plotted fabrics, with group sizes from 2 to 64.

```bash
simulation-scripts/experiments/scaleup_coll_footprint/run_thesis.sh
```

The result is
`simulation-scripts/results/scaleup_coll_footprint/scaleup_coll_footprint.csv`. Running
the collective plot command from the previous section regenerates the thesis footprint
figure. The experiment-specific diagnostic plots can be generated with:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
  /workspace/simulation-scripts/experiments/scaleup_coll_footprint/plot.py
```

## 4. Chapter 5: training case study

The historical run used for the thesis is:

```text
simulation-scripts/results/ch5_accumulation/runs/
  ch5-ga32-headline-iters1-mem16-4000-400-20260731T1405Z/
```

Launch the same TP4/TP8/TP16 workload definition in a new immutable run directory:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace atlahs-sim run ch5_accumulation \
  --tps 4,8,16 --accumulations 32 --points 4000:400 \
  --iters 1 --jobs 1 --timeout 7200 --run-id <run-id>
```

An existing run ID is rejected. Failed runs retain their diagnostics.

Generate the figure and tables from a completed run with:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
  /workspace/simulation-scripts/experiments/ch5_accumulation/plot.py \
  --headline-run /workspace/simulation-scripts/results/ch5_accumulation/runs/<run-id> \
  --output-dir /workspace/simulation-scripts/results/ch5_accumulation/runs/<run-id>/thesis-assets
```

The historical run records the executed inputs and binary/source hashes in its source
snapshot. Its `PROVENANCE.md` describes the available metadata.
