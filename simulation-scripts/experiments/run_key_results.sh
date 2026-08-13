#!/usr/bin/env bash
# Regenerate the three completion-time figures in key_results (local, no Docker).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
OUT_DIR="${KEY_RESULTS_OUTPUT_DIR:-$REPO_ROOT/simulation-scripts/results/key_figures_rerun}"
SIZES="4096,16384,65536,262144,1048576,4194304,16777216,67108864"

cd "$REPO_ROOT"
mkdir -p "$OUT_DIR"

SCALEUP_OUTPUT_DIR="$OUT_DIR" "$PYTHON_BIN" simulation-scripts/experiments/scaleup_coll_ab/run.py \
  --n 64 --sizes "$SIZES" \
  --su-topo scaleup_single_switch_64_4000Gbps.topo --so-topo tree64_8.topo \
  --tmpdir /tmp/key_results_normal

SCALEUP_OUTPUT_DIR="$OUT_DIR" "$PYTHON_BIN" simulation-scripts/experiments/scaleup_coll_ab/run.py \
  --n 64 --sizes "$SIZES" \
  --su-topo scaleup_3tier_64_oversub4_4000Gbps.topo --so-topo tree64_8.topo \
  --tmpdir /tmp/key_results_oversub

SCALEUP_OUTPUT_DIR="$OUT_DIR" "$PYTHON_BIN" simulation-scripts/experiments/scaleup_coll_ab/plot.py

SCALEOUT_OUTPUT_DIR="$OUT_DIR" "$PYTHON_BIN" simulation-scripts/experiments/scaleout_allreduce/run.py \
  --tmpdir /tmp/key_results_scaleout
SCALEOUT_OUTPUT_DIR="$OUT_DIR" "$PYTHON_BIN" simulation-scripts/experiments/scaleout_allreduce/plot.py

cp "$OUT_DIR/allreduce_completion_time__scaleup_single_switch_64_4000Gbps.png" \
   key_results/normal_scaleup_allreduce_completion_time.png
cp "$OUT_DIR/allreduce_completion_time__scaleup_3tier_64_oversub4_4000Gbps.png" \
   key_results/oversubscribed_scaleup_allreduce_completion_time.png
cp "$OUT_DIR/scaleout_allreduce_completion_time.png" \
   key_results/scaleout_allreduce_completion_time.png

echo "wrote key_results/{normal_scaleup,oversubscribed_scaleup,scaleout}_allreduce_completion_time.png"
