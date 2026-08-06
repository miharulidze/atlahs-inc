#!/usr/bin/env bash
# Fast, non-simulating publication gates. Use --extended to include the
# supplementary and superseded runners retained for provenance.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)
IMAGE=${ATLAHS_SIM_IMAGE:-atlahs-sim}
DOCKER_BIN=${ATLAHS_DOCKER_BIN:-docker}
DOCKER_USER=${ATLAHS_DOCKER_USER:-$(id -u):$(id -g)}
EXTENDED=0
PFC_SMOKE=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --extended) EXTENDED=1 ;;
    --pfc-smoke) PFC_SMOKE=1 ;;
    *) echo "usage: $0 [--extended] [--pfc-smoke]" >&2; exit 2 ;;
  esac
  shift
done

echo "[artifact] checking committed-data and analytic claims"
python3 "$HERE/model_checks/audit_reference_data.py"
python3 "$HERE/model_checks/verify_ch5_snapshot.py"
python3 "$HERE/model_checks/verify_models.py"

run_validation() {
  echo "[artifact] validating $1"
  "$DOCKER_BIN" run --rm --user "$DOCKER_USER" \
    -v "$REPO_ROOT":/workspace \
    -e SIM_RESULTS_ROOT=/tmp/atlahs-artifact-validation \
    "$IMAGE" run "$@"
}

run_validation scaleup_coll_ab --validate
run_validation scaleup_coll_footprint --validate
run_validation scaleup_pfc_concurrent --validate
run_validation ch5_accumulation --validate --tps 4 --accumulations 32

run_packet_check() {
  echo "[artifact] packet regression $1"
  "$DOCKER_BIN" run --rm --user "$DOCKER_USER" \
    -v "$REPO_ROOT":/workspace -w /tmp --entrypoint python3 \
    "$IMAGE" "/workspace/simulation-scripts/model_checks/$1"
}

echo "[artifact] generator trace-contract regression"
"$DOCKER_BIN" run --rm --user "$DOCKER_USER" \
  -v "$REPO_ROOT":/workspace \
  -w /workspace/goal_gen/ai/nccl_generator_v2 --entrypoint python3 \
  "$IMAGE" test_trace_contract.py

run_packet_check collective_validation_test.py
run_packet_check rooted_domain_localization_test.py

echo "[artifact] packet smoke scaleup_coll_ab (partial 8/64 domain)"
"$DOCKER_BIN" run --rm --user "$DOCKER_USER" \
  -v "$REPO_ROOT":/workspace -e SCALEUP_OUTPUT_DIR=/tmp/scaleup-coll-ab-smoke \
  "$IMAGE" run scaleup_coll_ab --n 8 --sizes 4096 \
  --su-topo scaleup_single_switch_64_4000Gbps.topo --timeout 60

echo "[artifact] packet smoke scaleup_coll_footprint (partial 2/64 domain)"
"$DOCKER_BIN" run --rm --user "$DOCKER_USER" \
  -v "$REPO_ROOT":/workspace -e FOOTPRINT_OUTPUT_DIR=/tmp/scaleup-footprint-smoke \
  "$IMAGE" run scaleup_coll_footprint --topos single_switch \
  --collectives allgather --max-p 2 --size-mults 1 --timeout 60

if [[ $PFC_SMOKE -eq 1 ]]; then
  echo "[artifact] packet smoke scaleup_pfc_concurrent (partial 128/256 domain)"
  "$DOCKER_BIN" run --rm --user "$DOCKER_USER" \
    -v "$REPO_ROOT":/workspace -e SCALEUP_OUTPUT_DIR=/tmp/scaleup-pfc-smoke \
    "$IMAGE" run scaleup_pfc_concurrent --n-sweep 1 --timeout 60
fi

if [[ $EXTENDED -eq 1 ]]; then
  run_validation scaleup_ar_bandwidth --validate
  run_validation intranode_linkspeed_sweep --validate
fi

echo "[artifact] all requested gates passed"
