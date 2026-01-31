# NPKit + NCCL 2.28 (ATLAHS on Alps) Runbook

## Directory layout

- `baselines/clariden/`: baseline summary JSONs copied from ATLAHS `clariden/`
- `src/nccl_npkit_v2.20.5-1/`: legacy reference implementation (blueprint)
- `src/nccl_228_npkit/nccl/`: NCCL `v2.28.3-1` + NPKit patch (git repo)
- `atlahs/`: ATLAHS checkout used to run the NPKit microbench + postprocess
- `npkit_nccl228/job_<JOBID>/`: per-job raw dumps/traces and per-proto summary JSONs
- `results/job_<JOBID>/`: exported per-job `npkit_data_summary_*.json`
- `scripts/`: build/run/postprocess/compare helpers
- `logs/`: sbatch stdout/err, postprocess logs, compare outputs


## Build

### 1) Build NPKit-enabled NCCL 2.28

- Script: `scripts/job_build_nccl228_npkit.sh`
- Output: `src/nccl_228_npkit/nccl/build/lib`

### 2) Build the NPKit microbench binaries

- Script: `scripts/job_build_npkit_examples_debug.sh`
- Output binaries (per proto):
  - `atlahs/goal_gen/ai/nccl_versions/npkit/nccl_228/Simple/example_allreduce`
  - `atlahs/goal_gen/ai/nccl_versions/npkit/nccl_228/LL/example_allreduce`

## Run (SLURM)

Primary runtime path is SLURM on `debug` (1 node, 4 GPUs, 4 ranks). 

### Submit a run (final configuration)

This uses one `srun` per trial and sweeps sizes inside the benchmark to avoid per-size launch overhead.

Example commands:

- Simple:
  - `sbatch --export=ALL,PROTO_LIST=Simple,START_SIZE=1,MAX_SIZE=4194304,NUM_TRIALS=10,ONE_SRUN_PER_TRIAL=1,SKIP_TRACE_GEN=1,NCCL_NCHANNELS=4,NCCL_MIN_NCHANNELS=4,NCCL_MAX_NCHANNELS=4 scripts/job_npkit_nccl228_debug.sh`
- LL:
  - `sbatch --export=ALL,PROTO_LIST=LL,START_SIZE=1,MAX_SIZE=4194304,NUM_TRIALS=10,ONE_SRUN_PER_TRIAL=1,SKIP_TRACE_GEN=1,NCCL_NCHANNELS=4,NCCL_MIN_NCHANNELS=4,NCCL_MAX_NCHANNELS=4 scripts/job_npkit_nccl228_debug.sh`

Raw outputs land in:
- `npkit_nccl228/job_<JOBID>/<Simple|LL>/...`

## Postprocess (login node)

Convert dumps → traces, compute statistics, and produce summary JSON:

- `scripts/process_npkit_job.sh <JOBID> Simple`
- `scripts/process_npkit_job.sh <JOBID> LL`

Export the summaries for comparisons:

- `cp npkit_nccl228/job_<JOBID>/Simple/npkit_data_summary_Simple.json results/job_<JOBID>/`
- `cp npkit_nccl228/job_<JOBID>/LL/npkit_data_summary_LL.json results/job_<JOBID>/`