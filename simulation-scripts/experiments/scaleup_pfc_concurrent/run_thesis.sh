#!/bin/bash
# X2 — PFC / lossless-backpressure validation: N concurrent disjoint AllReduces,
# trees PINNED to one core vs DISTRIBUTED round-robin, on the 256-host 3-tier fat-tree.
# REQUIRES a pcm binary rebuilt with the -mcast_pin flag:
#   docker run --rm -v $(pwd):/workspace atlahs-sim build
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=atlahs-sim

echo "[run_thesis] clearing old CSVs for a fresh regeneration"
rm -f "$REPO_ROOT/simulation-scripts/results/scaleup_pfc_concurrent/scaleup_pfc_concurrent.csv" \
      "$REPO_ROOT/simulation-scripts/results/scaleup_pfc_concurrent/pfc_census.csv" \
      "$REPO_ROOT/simulation-scripts/results/scaleup_pfc_concurrent/pfc_controls.csv" \
      "$REPO_ROOT/simulation-scripts/results/scaleup_pfc_concurrent/pfc_overdrive.csv"

echo "[run_thesis] X2 E2 stress: pinned vs distributed, N sweep x {64 KiB, 1 MiB}, 3-tier 256"
docker run --rm -v "$REPO_ROOT":/workspace "$IMG" run scaleup_pfc_concurrent

echo "[run_thesis] X2 E1 census: corner cells, both fabrics"
docker run --rm -v "$REPO_ROOT":/workspace "$IMG" run scaleup_pfc_concurrent --census

echo "[run_thesis] X2 E3 controls: mis-provisioned tripwire checks"
docker run --rm -v "$REPO_ROOT":/workspace "$IMG" run scaleup_pfc_concurrent --controls

echo "[run_thesis] X2 E2b overdrive: NIC 2x fabric, gate ON"
docker run --rm -v "$REPO_ROOT":/workspace "$IMG" run scaleup_pfc_concurrent --overdrive

echo "[run_thesis] done -> simulation-scripts/results/scaleup_pfc_concurrent/{scaleup_pfc_concurrent,pfc_census,pfc_controls,pfc_overdrive}.csv"
