# NPKit + NCCL 2.28 Reproduction Guide

Build NCCL **2.28.3** with NPKit enabled and produce the two summary JSONs used by ATLAHS/GOAL.

## Goal

Starting from a clean NCCL **v2.28.3** source tree, you should end up with:

- a NPKit-enabled `libnccl.so`
- `npkit_data_summary_Simple.json`
- `npkit_data_summary_LL.json`

## Prerequisites

- CUDA toolkit and MPI available (examples assume `/usr/local/cuda` and `/usr/local/mpi`)
- Slurm access to GPU nodes (adjust account/partition/container usage for your site)
- A scratch/work directory (examples use `$SCRATCH`)

This guide assumes:

- `SCRATCH`: base directory for NCCL builds and NPKit outputs
- `CUDA_HOME`: typically `/usr/local/cuda`
- `MPI_HOME`: typically `/usr/local/mpi`

## 1) Prepare NCCL 2.28.3 with NPKit

Clone NCCL and apply the forward-port patch.

```bash
git clone https://github.com/NVIDIA/nccl.git -b v2.28.3 $SCRATCH/nccl_228_npkit/nccl
```

```bash
cd $SCRATCH/nccl_228_npkit/nccl
```

Apply the NPKit forward-port patch shipped in this repo:

```bash
patch -p1 < <repo_root>/goal_gen/ai/nccl_versions/npkit/nccl_228/npkit_nccl228_patch.diff
```

Build with NPKit flags (adjust `NVCC_GENCODE` to your GPU architecture):

```bash
srun --nodes=1 --ntasks=1 --gpus-per-node=1 --time=01:00:00 --mpi=pmi2 --environment=megatron bash -lc '
  set -euo pipefail
  cd $SCRATCH/nccl_228_npkit/nccl
  make -j4 CUDA_HOME=/usr/local/cuda MPI_HOME=/usr/local/mpi \
    NVCC_GENCODE="-gencode=arch=compute_90,code=sm_90" \
    TRACING_FLAGS="-DENABLE_PROFILING -DENABLE_NPKIT -DENABLE_NPKIT_CPU_TIMESTAMP"
'
```

Quick sanity:

- `NVCC_GENCODE` must match your GPU.
- `TRACING_FLAGS` enables NPKit + CPU timestamps.

Outputs:

- Library: `$SCRATCH/nccl_228_npkit/nccl/build/lib/libnccl.so.2.28.3`
- Headers: `$SCRATCH/nccl_228_npkit/nccl/build/include`
- NPKit header: `$SCRATCH/nccl_228_npkit/nccl/src/include/npkit/npkit_event.h`

## 2) Build the NPKit microbenchmark

Build the tiny AllReduce benchmark against your NCCL build.

```bash
cd <repo_root>/goal_gen/ai/nccl_versions/npkit/nccl_228
```

```bash
MPI_HOME=/usr/local/mpi
NCCL_INC=$SCRATCH/nccl_228_npkit/nccl/build/include
NCCL_LIB=$SCRATCH/nccl_228_npkit/nccl/build/lib
CUDA_BIN=/usr/local/cuda/bin
```

Build the Simple binary:

```bash
${CUDA_BIN}/nvcc -I${MPI_HOME}/include -L${MPI_HOME}/lib -lmpi \
  -I${NCCL_INC} -L${NCCL_LIB} -lnccl \
  Simple/example_allreduce.cu -o Simple/example_allreduce
```

Build the LL binary:

```bash
${CUDA_BIN}/nvcc -I${MPI_HOME}/include -L${MPI_HOME}/lib -lmpi \
  -I${NCCL_INC} -L${NCCL_LIB} -lnccl \
  LL/example_allreduce.cu -o LL/example_allreduce
```

## 3) Run NPKit sweeps (Simple and LL)

Submit two jobs (one per protocol).

```bash
cd <repo_root>/goal_gen/ai/nccl_versions/npkit
```

```bash
sbatch --export=ALL,PROTO_LIST="Simple",START_SIZE=1,MAX_SIZE=$((4*1024*1024)),NUM_TRIALS=10 job_alps_npkit_nccl228.sh
```

```bash
sbatch --export=ALL,PROTO_LIST="LL",START_SIZE=1,MAX_SIZE=$((4*1024*1024)),NUM_TRIALS=10 job_alps_npkit_nccl228.sh
```

The job script sets `LD_LIBRARY_PATH` to your NCCL build and uses `NPKIT_EVENT_HEADER` (defaults to the header in the same NCCL tree).

Outputs go under:

- `${SCRATCH}/npkit_nccl228/job_<JOBID>/<PROTO>/`

## 4) Collect NPKit summaries

- Simple summary: `${SCRATCH}/npkit_nccl228/job_<JOBID>/Simple/npkit_data_summary_Simple.json`
- LL summary: `${SCRATCH}/npkit_nccl228/job_<JOBID>/LL/npkit_data_summary_LL.json`

## Outputs to expect

- NPKit summaries (what you usually need for GOAL/ATLAHS):
  - Simple: `${SCRATCH}/npkit_nccl228/job_<JOBID>/Simple/npkit_data_summary_Simple.json`
  - LL: `${SCRATCH}/npkit_nccl228/job_<JOBID>/LL/npkit_data_summary_LL.json`
- Per-run NPKit traces:
  - `${SCRATCH}/npkit_nccl228/job_<JOBID>/<PROTO>/results/.../npkit_trace/npkit_event_trace.json`

## Tips

- Adjust `NVCC_GENCODE` and Slurm resources for your hardware.
- If trace generation gets skipped, check that NPKit dumps exist and `NPKIT_EVENT_HEADER` points to the right `npkit_event.h`.
- If you reuse different NPKit summaries, swap `NPKIT_SIMPLE` / `NPKIT_LL` before running `get_traced_events.py`.
