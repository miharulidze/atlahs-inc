#!/bin/bash
# X2 — PFC / lossless-backpressure validation: N concurrent disjoint AllReduces,
# trees PINNED to one core vs DISTRIBUTED round-robin, on the 256-host 3-tier fat-tree.
# REQUIRES a pcm binary rebuilt with the -mcast_pin flag:
#   docker run --rm -v $(pwd):/workspace atlahs-sim build
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=atlahs-sim

echo "[run_thesis] clearing old CSV for a fresh regeneration"
rm -f "$REPO_ROOT/simulation-scripts/results/scaleup_pfc_concurrent/scaleup_pfc_concurrent.csv"

echo "[run_thesis] X2 PFC concurrent: pinned vs distributed, N sweep, 3-tier 256"
docker run --rm -v "$REPO_ROOT":/workspace "$IMG" run scaleup_pfc_concurrent

echo "[run_thesis] done -> simulation-scripts/results/scaleup_pfc_concurrent/scaleup_pfc_concurrent.csv"
