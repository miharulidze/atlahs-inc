# Reproducing the thesis experiments

Three canonical experiments produce the thesis headline data. A fourth suite validates
the lossless-backpressure assumption used by those experiments. The committed CSVs and
Chapter 5 run directory are the historical thesis data of record, so the existing figures
and tables can be regenerated without rerunning the multi-hour simulations.

They must not be combined with rows from a corrected backend. See
[`REFERENCE_DATA.md`](REFERENCE_DATA.md) for lineage and the release-rerun policy.

For the final public handoff, also complete
[`PUBLICATION_CHECKLIST.md`](PUBLICATION_CHECKLIST.md).

## 1. Prepare the public checkout

Clone recursively, or initialize the two experiment dependencies explicitly:

```bash
git submodule update --init --recursive \
  sim/pcm-sdk_zhiyi goal_gen/ai/nccl_generator_v2

docker build -t atlahs-sim simulation-scripts/
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace atlahs-sim build
```

The root commit and its submodule gitlinks define the artifact version. Before a public
handoff, both submodule commits must be reachable from their public remotes and the
release checkout must not contain unpublished source changes. Building may create or
overlay generated files inside nested source trees, so perform the clean-tree check
before the build.

Run the quick gates after building:

```bash
simulation-scripts/validate_artifact.sh
```

`--extended` also validates the supplementary and superseded runners. These gates
generate and compile traces but do not run the expensive simulation matrices.

## 2. Chapter 4: collective completion-time A/B

The frozen runners use group size 64, nine message sizes, and both the single-switch and
three-tier fabrics. The group-size companion sweeps groups from 2 through 64.

```bash
simulation-scripts/experiments/scaleup_coll_ab/run_thesis.sh
simulation-scripts/experiments/scaleup_coll_ab/run_groupsweep.sh
```

Data of record:

- `simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv`
- `simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv`

Regenerate the LaTeX tables into a repository-local ignored directory:

```bash
python3 simulation-scripts/_gen_rsag_tables.py
# Override when copying directly into a paper tree:
RSAG_OUT=/path/to/paper/figures python3 simulation-scripts/_gen_rsag_tables.py
```

Regenerate the measured plots:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
  /workspace/simulation-scripts/_gen_collective_plots.py
```

The PDFs are written to `simulation-scripts/results/scaleup_coll_ab/thesis_figs/`.

## 3. Chapter 4: network footprint

```bash
simulation-scripts/experiments/scaleup_coll_footprint/run_thesis.sh
```

The frozen thesis runner covers the two plotted fabrics and groups 2–64. The tracked
data of record is
`simulation-scripts/results/scaleup_coll_footprint/scaleup_coll_footprint.csv`. The same
collective plotter regenerates the thesis footprint figure; the experiment-local
`plot.py` regenerates the standalone diagnostic figures. The radix-32, 1024-host sweep
is an opt-in extension, not part of the tracked thesis matrix.

## 4. Chapter 5: accumulation-corrected case study

The canonical historical run is:

```text
simulation-scripts/results/ch5_accumulation/runs/
  ch5-ga32-headline-iters1-mem16-4000-400-20260731T1405Z/
```

A fresh run with the same workload definition is launched inside the container with:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace atlahs-sim run ch5_accumulation \
  --tps 4,8,16 --accumulations 32 --points 4000:400 \
  --iters 1 --jobs 1 --timeout 7200 --run-id <fresh-run-id>
```

Allow at least 16 GiB for the TP16 cell. Run IDs are immutable: an existing directory is
a hard error, and failed runs retain their diagnostic artifacts.

Regenerate the figure and both tables from a completed run alone:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
  /workspace/simulation-scripts/experiments/ch5_accumulation/plot.py \
  --headline-run /workspace/simulation-scripts/results/ch5_accumulation/runs/<run-id> \
  --output-dir /workspace/simulation-scripts/results/ch5_accumulation/runs/<run-id>/thesis-assets-new
```

The historical run records input and binary hashes, but its manifest could not record
Git revisions or the Docker image ID. All ten executed source inputs are preserved and
hash-verified in `source_snapshot/`; see the run's `PROVENANCE.md`. Retain this run as the
thesis data of record, but use a fresh run from the clean public release commit to capture
repository ancestry and container identity as well.

## 5. Flow-control release gate

The model is a one-class PFC-style lossless abstraction, not CBFC. Before publishing a
new backend revision, rerun the full validation suite into a fresh output directory:

```bash
PFC_OUT=/workspace/simulation-scripts/results/release-validation/<release-id>

docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace -e SCALEUP_OUTPUT_DIR="$PFC_OUT" \
  atlahs-sim run scaleup_pfc_concurrent
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace -e SCALEUP_OUTPUT_DIR="$PFC_OUT" \
  atlahs-sim run scaleup_pfc_concurrent --census
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace -e SCALEUP_OUTPUT_DIR="$PFC_OUT" \
  atlahs-sim run scaleup_pfc_concurrent --controls
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/workspace -e SCALEUP_OUTPUT_DIR="$PFC_OUT" \
  atlahs-sim run scaleup_pfc_concurrent --overdrive
```

Acceptance criteria are documented in the experiment README: valid census and overdrive
cells must complete with zero invariant-warning lines and remain within queue bounds;
the deliberately undersized egress control must make the tripwire fire. The stress arm
validates contention timing but is not required to emit pauses.

## 6. Verification from committed data

```bash
python3 simulation-scripts/model_checks/audit_reference_data.py
python3 simulation-scripts/model_checks/verify_models.py
```

The first check rejects appended duplicate experiment generations. The second re-derives
the Chapter 4 model claims, Chapter 5 quoted speedups and simulator-model factors, and the
hand-entered topology/PFC anchor values. Exit status zero means every current check passed.

Selected anchors:

| quantity | value |
|---|---|
| Chapter 5 end-to-end speedups, TP4/8/16 | 1.101 / 1.232 / 1.405 |
| simulator-model TP factors at 32 MiB | 1.562 / 1.905 / 2.218 |
| ring-step latency for depth 1/2/3 | 808 / 2225 / 3642 ns |
| PFC pause/resume, crossbar and three-tier | 218/174 and 389/311 packets |

## 7. Noncanonical material

- `scaleup_ar_bandwidth` is a supplementary structural comparison, not a thesis asset.
- `intranode_linkspeed_sweep` is retained for provenance and was superseded by
  `ch5_accumulation`.
- `model_checks/` contains the supported verifier plus historical derivation probes.
- `isolation-plots/` is a convenience collection, not an independent source of data.
- The retired standalone `sim/htsim-backend` development fork has been removed from the
  release tree. Its history remains in Git and it does not feed the quantitative artifact.
