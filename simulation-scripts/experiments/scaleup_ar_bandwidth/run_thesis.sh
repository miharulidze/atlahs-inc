#!/bin/bash
# M-C (E1) — AllReduce reduction bandwidth: fused apex vs composed RS-then-AG.
# Frozen thesis invocation: |G|=64, single-switch crossbar, 9-point size grid.
# Both arms are IN-NETWORK (no endpoint baseline), so the apex/composed ratio is
# bug-common-mode. Metric: 8*S/T (Gbit/s), % of wire. Engine: pcm-sdk.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=atlahs-sim

echo "[run_thesis] clearing old CSV for a fresh regeneration"
rm -f "$REPO_ROOT/simulation-scripts/results/scaleup_ar_bandwidth/scaleup_ar_bandwidth.csv"

echo "[run_thesis] M-C E1: apex vs composed RS+AG, |G|=64 single-switch"
docker run --rm -v "$REPO_ROOT":/workspace "$IMG" run scaleup_ar_bandwidth \
  --n 64 --su-topo scaleup_single_switch_64_3600Gbps.topo

echo "[run_thesis] done -> simulation-scripts/results/scaleup_ar_bandwidth/scaleup_ar_bandwidth.csv"
echo "[run_thesis] plot: docker run --rm -v \$(pwd):/workspace --entrypoint python3 $IMG \\"
echo "               /workspace/simulation-scripts/experiments/scaleup_ar_bandwidth/plot.py"
