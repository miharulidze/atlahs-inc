#!/usr/bin/env bash
# Rebuild the AI case-study result set with the primary h100_te compute model.
# Intended for an unattended/overnight machine. The harness starts each target
# CSV fresh at the experiment level; unrelated result files remain.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/../../.." && pwd)
IMAGE=${ATLAHS_SIM_IMAGE:-atlahs-sim}
DOCKER_BIN=${ATLAHS_DOCKER_BIN:-}
JOBS=${ATLAHS_SIM_JOBS:-4}
TIMEOUT=${ATLAHS_SIM_TIMEOUT:-7200}
LOG_DIR=${ATLAHS_SIM_LOG_DIR:-$REPO_ROOT/simulation-scripts/results/intranode_linkspeed_sweep/logs_h100_te}
RUN_SU8000=${ATLAHS_RUN_SU8000:-0}
if [[ -z "$DOCKER_BIN" ]]; then
  if command -v docker >/dev/null 2>&1; then
    DOCKER_BIN=$(command -v docker)
  elif [[ -x /usr/local/bin/docker ]]; then
    DOCKER_BIN=/usr/local/bin/docker
  else
    echo "docker not found; set ATLAHS_DOCKER_BIN" >&2
    exit 1
  fi
fi
mkdir -p "$LOG_DIR"

run_width() {
  local total=$1
  local tp=$2
  local label=$3
  shift 3
  "$DOCKER_BIN" run --rm -v "$REPO_ROOT":/workspace "$IMAGE" \
    run intranode_linkspeed_sweep \
    --total_gpus "$total" --tps "$tp" --pps 1 \
    --layers 2 --batch 32 --iters 2 --compute_model h100_te \
    --jobs "$JOBS" --timeout "$TIMEOUT" "$@" \
    2>&1 | tee "$LOG_DIR/$label.log"
}

# Primary scale-up sweep: 4000/400 is one point on every curve.
run_width 16 4  intranode_tp4  --mode intranode --internode_gbps 400
run_width 32 8  intranode_tp8  --mode intranode --internode_gbps 400
run_width 64 16 intranode_tp16 --mode intranode --internode_gbps 400

# Scale-out sensitivity with scale-up fixed at the 4000-Gbps main target.
run_width 16 4  internode_tp4_su4000  --mode internode --intranode_gbps 4000
run_width 32 8  internode_tp8_su4000  --mode internode --intranode_gbps 4000
run_width 64 16 internode_tp16_su4000 --mode internode --intranode_gbps 4000

# Optional second-generation sensitivity. It is not part of the main target.
if [[ "$RUN_SU8000" == 1 ]]; then
  run_width 16 4  internode_tp4_su8000  --mode internode --intranode_gbps 8000
  run_width 32 8  internode_tp8_su8000  --mode internode --intranode_gbps 8000
  run_width 64 16 internode_tp16_su8000 --mode internode --intranode_gbps 8000
fi

# Recompute the causal skeleton with the same h100_te traces, then regenerate
# the consolidated case-study figures from the fresh CSVs.
"$DOCKER_BIN" run --rm --entrypoint python3 -v "$REPO_ROOT":/workspace "$IMAGE" \
  /workspace/simulation-scripts/_run_skeleton_decomposition.py \
  2>&1 | tee "$LOG_DIR/skeleton.log"
"$DOCKER_BIN" run --rm --entrypoint python3 -v "$REPO_ROOT":/workspace "$IMAGE" \
  /workspace/simulation-scripts/_gen_case_study_figs.py \
  2>&1 | tee "$LOG_DIR/figures.log"

echo "h100_te case-study rerun complete; logs: $LOG_DIR"
