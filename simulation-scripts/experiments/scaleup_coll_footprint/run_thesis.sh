#!/bin/bash
# M-B — Network-footprint / bandwidth-usage reduction (Khalilov SC24 Fig.2).
# Frozen thesis invocation: all 5 collectives across all three topology classes
# (single-switch, 3-tier fat-tree, radix-32 1024-host paper topology); P swept via the
# trace. Byte-ratio metric, size-independent. Engine: pcm-sdk.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=atlahs-sim

echo "[run_thesis] clearing old CSV for a fresh regeneration"
rm -f "$REPO_ROOT/simulation-scripts/results/scaleup_coll_footprint/scaleup_coll_footprint.csv"

echo "[run_thesis] M-B footprint: all topologies x all collectives (defaults)"
docker run --rm -v "$REPO_ROOT":/workspace "$IMG" run scaleup_coll_footprint \
  --topos single_switch,fat3tier,paper_r32 \
  --collectives allgather,allreduce,reduce_scatter,bcast,reduce

echo "[run_thesis] done -> simulation-scripts/results/scaleup_coll_footprint/scaleup_coll_footprint.csv"
