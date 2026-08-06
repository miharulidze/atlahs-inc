#!/bin/bash
# M-B — Network-footprint / bandwidth-usage reduction (Khalilov SC24 Fig.2).
# Frozen thesis invocation: all 5 collectives on the two fabrics plotted in the thesis,
# with P=2..64 swept via the trace. The radix-32 1024-host sweep is an opt-in extension,
# not part of the tracked thesis matrix. Byte-ratio metric. Engine: pcm-sdk.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=atlahs-sim
DOCKER_USER=${ATLAHS_DOCKER_USER:-$(id -u):$(id -g)}
RUN_ID=${ATLAHS_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}
case "$RUN_ID" in *[!A-Za-z0-9._-]*|'') echo "invalid ATLAHS_RUN_ID: $RUN_ID" >&2; exit 2 ;; esac
RUN_REL=simulation-scripts/results/generated-runs/$RUN_ID/scaleup_coll_footprint
RUN_HOST=$REPO_ROOT/$RUN_REL
OUT=/workspace/$RUN_REL
TARGET=$REPO_ROOT/simulation-scripts/results/scaleup_coll_footprint/scaleup_coll_footprint.csv
[[ ! -e "$RUN_HOST" ]] || { echo "run archive already exists: $RUN_HOST" >&2; exit 1; }
mkdir -p "$RUN_HOST" "$(dirname "$TARGET")"

echo "[run_thesis] M-B footprint: two thesis fabrics, P=2..64, all collectives"
docker run --rm --user "$DOCKER_USER" -v "$REPO_ROOT":/workspace \
  -e FOOTPRINT_OUTPUT_DIR="$OUT" "$IMG" run scaleup_coll_footprint \
  --topos single_switch,fat3tier --max-p 64 \
  --collectives allgather,allreduce,reduce_scatter,bcast,reduce

SOURCE=$RUN_HOST/scaleup_coll_footprint.csv
[[ -s "$SOURCE" ]] || { echo "missing generated CSV: $SOURCE" >&2; exit 1; }
PUBLISH_TMP=$TARGET.tmp.$$
cp "$SOURCE" "$PUBLISH_TMP"
mv "$PUBLISH_TMP" "$TARGET"
echo "[run_thesis] archive -> $RUN_REL"
echo "[run_thesis] published -> simulation-scripts/results/scaleup_coll_footprint/scaleup_coll_footprint.csv"
