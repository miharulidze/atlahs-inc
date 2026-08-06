#!/bin/bash
# X2 — PFC / lossless-backpressure validation: N concurrent disjoint AllReduces,
# trees PINNED to one core vs DISTRIBUTED round-robin, on the 256-host 3-tier fat-tree.
# REQUIRES a pcm binary rebuilt with the -mcast_pin flag:
#   docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim build
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=atlahs-sim
DOCKER_USER=${ATLAHS_DOCKER_USER:-$(id -u):$(id -g)}
RUN_ID=${ATLAHS_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}
case "$RUN_ID" in *[!A-Za-z0-9._-]*|'') echo "invalid ATLAHS_RUN_ID: $RUN_ID" >&2; exit 2 ;; esac
RUN_REL=simulation-scripts/results/generated-runs/$RUN_ID/scaleup_pfc_concurrent
RUN_HOST=$REPO_ROOT/$RUN_REL
OUT=/workspace/$RUN_REL
TARGET_DIR=$REPO_ROOT/simulation-scripts/results/scaleup_pfc_concurrent
[[ ! -e "$RUN_HOST" ]] || { echo "run archive already exists: $RUN_HOST" >&2; exit 1; }
mkdir -p "$RUN_HOST" "$TARGET_DIR"

echo "[run_thesis] X2 E2 stress: pinned vs distributed, N sweep x {64 KiB, 1 MiB}, 3-tier 256"
docker run --rm --user "$DOCKER_USER" -v "$REPO_ROOT":/workspace \
  -e SCALEUP_OUTPUT_DIR="$OUT" "$IMG" run scaleup_pfc_concurrent

echo "[run_thesis] X2 E1 census: corner cells, both fabrics"
docker run --rm --user "$DOCKER_USER" -v "$REPO_ROOT":/workspace \
  -e SCALEUP_OUTPUT_DIR="$OUT" "$IMG" run scaleup_pfc_concurrent --census

echo "[run_thesis] X2 E3 controls: mis-provisioned tripwire checks"
docker run --rm --user "$DOCKER_USER" -v "$REPO_ROOT":/workspace \
  -e SCALEUP_OUTPUT_DIR="$OUT" "$IMG" run scaleup_pfc_concurrent --controls

echo "[run_thesis] X2 E2b overdrive: NIC 2x fabric, gate ON"
docker run --rm --user "$DOCKER_USER" -v "$REPO_ROOT":/workspace \
  -e SCALEUP_OUTPUT_DIR="$OUT" "$IMG" run scaleup_pfc_concurrent --overdrive

for csv in scaleup_pfc_concurrent pfc_census pfc_controls pfc_overdrive; do
  SOURCE=$RUN_HOST/$csv.csv
  [[ -s "$SOURCE" ]] || { echo "missing generated CSV: $SOURCE" >&2; exit 1; }
done
for csv in scaleup_pfc_concurrent pfc_census pfc_controls pfc_overdrive; do
  SOURCE=$RUN_HOST/$csv.csv
  TARGET=$TARGET_DIR/$csv.csv
  PUBLISH_TMP=$TARGET.tmp.$$
  cp "$SOURCE" "$PUBLISH_TMP"
  mv "$PUBLISH_TMP" "$TARGET"
done
echo "[run_thesis] archive -> $RUN_REL"
echo "[run_thesis] published -> simulation-scripts/results/scaleup_pfc_concurrent/{scaleup_pfc_concurrent,pfc_census,pfc_controls,pfc_overdrive}.csv"
