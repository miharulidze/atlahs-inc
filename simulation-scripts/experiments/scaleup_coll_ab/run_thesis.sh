#!/bin/bash
# M-A — Completion-time A/B (INC vs endpoint), the headline per-collective table.
# Frozen thesis invocation: |G|=64, all 5 collectives, single-switch + 3-tier fat-tree,
# 9-point size grid. Engine: pcm-sdk (via the atlahs-sim Docker image).
#
# Regenerates results/scaleup_coll_ab/scaleup_coll_ab.csv from scratch (both topologies
# land in one CSV, distinguished by the su_topo column). Run from anywhere.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=atlahs-sim
SIZES=4096,16384,65536,262144,1048576,4194304,16777216,67108864,268435456
OUT=/workspace/simulation-scripts/results/scaleup_coll_ab   # container path

run() { docker run --rm -v "$REPO_ROOT":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" "$IMG" run scaleup_coll_ab "$@"; }

echo "[run_thesis] clearing old CSV for a fresh regeneration"
rm -f "$REPO_ROOT/simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv"

echo "[run_thesis] M-A single-switch-64 (one-hop headline)"
run --n 64 --su-topo scaleup_single_switch_64_3600Gbps.topo --sizes "$SIZES"

echo "[run_thesis] M-A 3-tier fat-tree 256-host (multi-tier)"
run --n 64 --su-topo scaleup_3tier_256_3600Gbps.topo --sizes "$SIZES"

echo "[run_thesis] done -> simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv"
