#!/bin/bash
# Re-gather the PNG figures of all isolation experiments into this folder.
# Run after regenerating plots (each experiment's plot.py writes into its results dir).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RESULTS="$HERE/../results"

find "$HERE" -maxdepth 1 -name '*.png' -delete
for d in scaleup_ar_bandwidth scaleup_coll_ab scaleup_coll_ab_groupsweep \
         scaleup_pfc_concurrent scaleup_coll_footprint; do
  find "$RESULTS/$d" -maxdepth 1 -name '*.png' -exec cp {} "$HERE/" \; 2>/dev/null || true
done
echo "collected $(find "$HERE" -maxdepth 1 -name '*.png' | wc -l | tr -d ' ') PNGs into $HERE"
