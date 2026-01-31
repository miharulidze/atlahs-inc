#!/bin/bash -l
#SBATCH --job-name="npkit-nccl228"
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --partition=normal
#SBATCH --account=a-g200
#SBATCH --mem=200G
#SBATCH --time=02:30:00
#SBATCH --output=npkit_nccl228.%j.out
#SBATCH --error=npkit_nccl228.%j.err

set -euo pipefail

# Resolve repo-relative paths (so this script works from a clean checkout).
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SCRIPT_ROOT=${SCRIPT_ROOT:-${SCRIPT_DIR}/nccl_228}

SCRATCH_BASE=${SCRATCH:-${HOME}/scratch}

# Where NCCL was built (matches the README default layout under $SCRATCH).
NCCL_BUILD_ROOT=${NCCL_BUILD_ROOT:-${SCRATCH_BASE}/nccl_228_npkit/nccl}
NCCL_LIB_DIR=${NCCL_LIB_DIR:-${NCCL_BUILD_ROOT}/build/lib}
DEFAULT_EVENT_HEADER=${DEFAULT_EVENT_HEADER:-${NCCL_BUILD_ROOT}/src/include/npkit/npkit_event.h}
RUN_BASE=${SCRATCH_BASE}/npkit_nccl228/job_${SLURM_JOB_ID}

PROTO_LIST=${PROTO_LIST:-"Simple"}   # set PROTO_LIST="Simple LL" to sweep both
START_SIZE=${START_SIZE:-1}
MAX_SIZE=${MAX_SIZE:-$((4 * 1024 * 1024))}
NUM_TRIALS=${NUM_TRIALS:-2}          # bump to 10 to mirror the original clariden sweep

mkdir -p "${RUN_BASE}"

echo "SCRIPT_ROOT        = ${SCRIPT_ROOT}"
echo "NCCL_LIB_DIR       = ${NCCL_LIB_DIR}"
echo "RUN_BASE           = ${RUN_BASE}"
echo "PROTO_LIST         = ${PROTO_LIST}"
echo "START_SIZE         = ${START_SIZE}"
echo "MAX_SIZE           = ${MAX_SIZE}"
echo "NUM_TRIALS         = ${NUM_TRIALS}"
echo "EVENT_HEADER       = ${NPKIT_EVENT_HEADER:-${DEFAULT_EVENT_HEADER}}"

export LD_LIBRARY_PATH=${NCCL_LIB_DIR}:${LD_LIBRARY_PATH:-}
export MPI_HOME=/usr/local/mpi
export NCCL_DEBUG=${NCCL_DEBUG:-WARN}
export NCCL_CUMEM_ENABLE=${NCCL_CUMEM_ENABLE:-0}
export NCCL_CUMEM_HOST_ENABLE=${NCCL_CUMEM_HOST_ENABLE:-0}

SRUN_COMMON_ARGS=(--mpi=pmi2)
if [ -n "${SRUN_EXTRA_ARGS:-}" ]; then
    # Intended for site-specific flags like: SRUN_EXTRA_ARGS="--environment=megatron"
    # shellcheck disable=SC2206
    SRUN_COMMON_ARGS+=(${SRUN_EXTRA_ARGS})
fi
if [ -n "${SLURM_JOB_ID:-}" ]; then
    SRUN_COMMON_ARGS+=(--ntasks="${SLURM_NTASKS:-1}")
fi

	for PROTO in ${PROTO_LIST}; do
	    BIN=${SCRIPT_ROOT}/${PROTO}/example_allreduce
	    WORK_RUN=${RUN_BASE}/${PROTO}
	    RESULTS_DIR=${WORK_RUN}/results
	    mkdir -p "${RESULTS_DIR}"

	    echo "Binary             = ${BIN}"
	    echo "===== Running proto ${PROTO} into ${WORK_RUN} ====="

	    # Pre-compute the size sweep list once so we can optionally amortize srun
	    # startup overhead by running all sizes in a single srun per trial.
	    SIZES=()
	    size=${START_SIZE}
	    while [ ${size} -le ${MAX_SIZE} ]; do
	        SIZES+=(${size})
	        size=$((size * 2))
	    done

	    for trial in $(seq 0 $((NUM_TRIALS - 1))); do
	        iteration_dir="${RESULTS_DIR}/${trial}"
	        mkdir -p "${iteration_dir}"

	        if [ -n "${ONE_SRUN_PER_TRIAL:-}" ]; then
	            # Run a single MPI/NCCL process group per trial and sweep sizes inside
	            # the benchmark binary to avoid per-size srun startup overhead.
	            npkit_run_dir="${iteration_dir}/npkit_run"
	            npkit_dump_dir="${npkit_run_dir}/npkit_dump"
	            npkit_trace_dir="${npkit_run_dir}/npkit_trace"
	            npkit_result_dir="${npkit_run_dir}/npkit_result"

	            mkdir -p "${npkit_dump_dir}" "${npkit_trace_dir}" "${npkit_result_dir}"

	            echo "[${PROTO}] trial ${trial} -> one srun, sweeping sizes ${START_SIZE}..${MAX_SIZE} (x2)"
	            srun "${SRUN_COMMON_ARGS[@]}" bash -lc "
	                set -euo pipefail
	                cd ${SCRIPT_ROOT}
	                export LD_LIBRARY_PATH=${NCCL_LIB_DIR}:\${LD_LIBRARY_PATH:-}
	                export NCCL_ALGO=Ring
	                export NCCL_PROTO=${PROTO}
	                export NCCL_CUMEM_ENABLE=${NCCL_CUMEM_ENABLE}
	                export NCCL_CUMEM_HOST_ENABLE=${NCCL_CUMEM_HOST_ENABLE}
	                export NPKIT_DUMP_DIR=${npkit_dump_dir}
	                ${BIN} --sweep ${START_SIZE} ${MAX_SIZE} | tee ${npkit_result_dir}/log.txt
	            "

	            EVENT_HEADER=${NPKIT_EVENT_HEADER:-${DEFAULT_EVENT_HEADER}}
	            if [ -n "${SKIP_TRACE_GEN:-}" ]; then
	                echo "Skip trace generation for trial ${trial} (SKIP_TRACE_GEN is set)"
	            elif [ -f "${EVENT_HEADER}" ] && compgen -G "${npkit_dump_dir}/*" > /dev/null; then
	                python3 ${SCRIPT_ROOT}/${PROTO}/npkit_dependency_trace_generator.py \
	                    --npkit_dump_dir=${npkit_dump_dir} \
	                    --npkit_event_header_path=${EVENT_HEADER} \
	                    --output_dir=${npkit_trace_dir}
	            else
	                echo "Skip trace generation for trial ${trial} (missing NPKit dumps or event header)" | tee "${npkit_result_dir}/npkit_trace_skipped.txt"
	            fi
	        else
	            size=${START_SIZE}
	            while [ ${size} -le ${MAX_SIZE} ]; do
	                size_dir="${iteration_dir}/${size}"
	                npkit_run_dir="${size_dir}/npkit_run"
	                npkit_dump_dir="${npkit_run_dir}/npkit_dump"
	                npkit_trace_dir="${npkit_run_dir}/npkit_trace"
	                npkit_result_dir="${npkit_run_dir}/npkit_result"

	                mkdir -p "${npkit_dump_dir}" "${npkit_trace_dir}" "${npkit_result_dir}"

	                echo "[${PROTO}] trial ${trial} size ${size} -> ${npkit_run_dir}"

	                srun "${SRUN_COMMON_ARGS[@]}" bash -lc "
	                    set -euo pipefail
	                    cd ${SCRIPT_ROOT}
	                    export LD_LIBRARY_PATH=${NCCL_LIB_DIR}:\${LD_LIBRARY_PATH:-}
	                    export NCCL_ALGO=Ring
	                    export NCCL_PROTO=${PROTO}
	                    export NCCL_CUMEM_ENABLE=${NCCL_CUMEM_ENABLE}
	                    export NCCL_CUMEM_HOST_ENABLE=${NCCL_CUMEM_HOST_ENABLE}
	                    export NPKIT_DUMP_DIR=${npkit_dump_dir}
	                    ${BIN} ${size} | tee ${npkit_result_dir}/log.txt
	                "

	                EVENT_HEADER=${NPKIT_EVENT_HEADER:-${DEFAULT_EVENT_HEADER}}
	                if [ -n "${SKIP_TRACE_GEN:-}" ]; then
	                    echo "Skip trace generation for trial ${trial} size ${size} (SKIP_TRACE_GEN is set)"
	                elif [ -f "${EVENT_HEADER}" ] && compgen -G "${npkit_dump_dir}/*" > /dev/null; then
	                    python3 ${SCRIPT_ROOT}/${PROTO}/npkit_dependency_trace_generator.py \
	                        --npkit_dump_dir=${npkit_dump_dir} \
	                        --npkit_event_header_path=${EVENT_HEADER} \
	                        --output_dir=${npkit_trace_dir}

	                else
	                    echo "Skip trace generation for trial ${trial} size ${size} (missing NPKit dumps or event header)" | tee "${npkit_result_dir}/npkit_trace_skipped.txt"
	                fi

	                size=$((size * 2))
	            done
	        fi
	    done

    first_trace=$(find "${RESULTS_DIR}" -name npkit_event_trace.json -print -quit || true)
    if [ -n "${first_trace}" ]; then
        (
            cd "${WORK_RUN}"
            python3 ${SCRIPT_ROOT}/${PROTO}/get_npkit_statistics.py
            python3 ${SCRIPT_ROOT}/${PROTO}/summary.py
        )
        SUMMARY_FILE=${WORK_RUN}/npkit_data_summary_${PROTO}.json
        export SUMMARY_FILE

        if [ -s "${SUMMARY_FILE}" ]; then
            python3 - <<'PY'
import json, os
path = os.environ["SUMMARY_FILE"]
with open(path) as f:
    data = json.load(f)
print(f'Validated summary: {path} (size {os.path.getsize(path)} bytes)')
if not data:
    print('Summary JSON is empty.')
    sys.exit(0)
first_keys = list(data.keys())[:5]
sizes = set()
for sizes_map in data.values():
    sizes.update(int(s) for s in sizes_map.keys())
print('Sample event keys:', first_keys)
if sizes:
    print(f'Size buckets: {len(sizes)} (min {min(sizes)}, max {max(sizes)})')
PY
        else
            echo "Summary file ${SUMMARY_FILE} missing or empty."
        fi
    else
        echo "No npkit_event_trace.json produced for ${PROTO}; skipping aggregation."
    fi

    echo "RESULTS (${PROTO}):"
    echo "  Scratch run root : ${WORK_RUN}"
    echo "  Summary JSON     : ${WORK_RUN}/npkit_data_summary_${PROTO}.json"
    echo "  Per-run traces   : ${RESULTS_DIR}"
done
