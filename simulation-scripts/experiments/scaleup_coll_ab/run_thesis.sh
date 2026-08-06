#!/bin/bash
# M-A — Completion-time A/B (INC vs endpoint), the headline per-collective table.
# Frozen thesis invocation: |G|=64, all 5 collectives, single-switch + 3-tier fat-tree,
# 9-point size grid. Engine: pcm-sdk (via the atlahs-sim Docker image).
#
# Builds a fresh CSV in an ignored run archive, then atomically refreshes the tracked
# reference only after both topologies complete. Run from anywhere.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=atlahs-sim
DOCKER_USER=${ATLAHS_DOCKER_USER:-$(id -u):$(id -g)}
SIZES=4096,16384,65536,262144,1048576,4194304,16777216,67108864,268435456
RUN_ID=${ATLAHS_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}
case "$RUN_ID" in *[!A-Za-z0-9._-]*|'') echo "invalid ATLAHS_RUN_ID: $RUN_ID" >&2; exit 2 ;; esac
RUN_REL=simulation-scripts/results/generated-runs/$RUN_ID/scaleup_coll_ab
RUN_HOST=$REPO_ROOT/$RUN_REL
OUT=/workspace/$RUN_REL
TARGET=$REPO_ROOT/simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv
[[ ! -e "$RUN_HOST" ]] || { echo "run archive already exists: $RUN_HOST" >&2; exit 1; }
mkdir -p "$RUN_HOST" "$(dirname "$TARGET")"

run() { docker run --rm --user "$DOCKER_USER" -v "$REPO_ROOT":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" "$IMG" run scaleup_coll_ab "$@"; }

echo "[run_thesis] M-A single-switch-64 (one-hop headline)"
run --n 64 --su-topo scaleup_single_switch_64_4000Gbps.topo --sizes "$SIZES"

echo "[run_thesis] M-A 3-tier fat-tree 256-host (multi-tier)"
run --n 64 --su-topo scaleup_3tier_256_4000Gbps.topo --sizes "$SIZES"

SOURCE=$RUN_HOST/scaleup_coll_ab.csv
[[ -s "$SOURCE" ]] || { echo "missing generated CSV: $SOURCE" >&2; exit 1; }
PUBLISH_TMP=$TARGET.tmp.$$
cp "$SOURCE" "$PUBLISH_TMP"
mv "$PUBLISH_TMP" "$TARGET"
echo "[run_thesis] archive -> $RUN_REL"
echo "[run_thesis] published -> simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv"
