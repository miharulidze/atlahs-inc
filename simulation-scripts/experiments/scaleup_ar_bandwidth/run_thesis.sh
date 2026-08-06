#!/bin/bash
# M-C (E1) — AllReduce reduction bandwidth: fused apex vs composed RS-then-AG.
# Frozen thesis invocation: |G|=64, single-switch crossbar, 9-point size grid.
# Both arms are IN-NETWORK (no endpoint baseline), so the apex/composed ratio is
# bug-common-mode. Metric: 8*S/T (Gbit/s), % of wire. Engine: pcm-sdk.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=atlahs-sim
DOCKER_USER=${ATLAHS_DOCKER_USER:-$(id -u):$(id -g)}
RUN_ID=${ATLAHS_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}
case "$RUN_ID" in *[!A-Za-z0-9._-]*|'') echo "invalid ATLAHS_RUN_ID: $RUN_ID" >&2; exit 2 ;; esac
RUN_REL=simulation-scripts/results/generated-runs/$RUN_ID/scaleup_ar_bandwidth
RUN_HOST=$REPO_ROOT/$RUN_REL
OUT=/workspace/$RUN_REL
TARGET=$REPO_ROOT/simulation-scripts/results/scaleup_ar_bandwidth/scaleup_ar_bandwidth.csv
[[ ! -e "$RUN_HOST" ]] || { echo "run archive already exists: $RUN_HOST" >&2; exit 1; }
mkdir -p "$RUN_HOST" "$(dirname "$TARGET")"

echo "[run_thesis] M-C E1: apex vs composed RS+AG, |G|=64 single-switch"
docker run --rm --user "$DOCKER_USER" -v "$REPO_ROOT":/workspace \
  -e SCALEUP_OUTPUT_DIR="$OUT" "$IMG" run scaleup_ar_bandwidth \
  --n 64 --su-topo scaleup_single_switch_64_4000Gbps.topo

SOURCE=$RUN_HOST/scaleup_ar_bandwidth.csv
[[ -s "$SOURCE" ]] || { echo "missing generated CSV: $SOURCE" >&2; exit 1; }
PUBLISH_TMP=$TARGET.tmp.$$
cp "$SOURCE" "$PUBLISH_TMP"
mv "$PUBLISH_TMP" "$TARGET"
echo "[run_thesis] archive -> $RUN_REL"
echo "[run_thesis] published -> simulation-scripts/results/scaleup_ar_bandwidth/scaleup_ar_bandwidth.csv"
echo "[run_thesis] plot: docker run --rm --user \$(id -u):\$(id -g) -v \$(pwd):/workspace --entrypoint python3 $IMG \\"
echo "               /workspace/simulation-scripts/experiments/scaleup_ar_bandwidth/plot.py"
