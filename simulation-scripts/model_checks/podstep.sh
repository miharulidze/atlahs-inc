#!/bin/bash
# Experiment: does the non-rooted ring baseline step when the group first crosses a
# pod boundary?  3-tier 256-host fabric, Podsize 16, contiguous rank->host, 4 KB
# (latency-dominated).  N=16 fits one pod exactly; N=17 spills into a second.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT=/workspace/simulation-scripts/results/_podstep
rm -rf "$REPO_ROOT/simulation-scripts/results/_podstep"
for N in 12 14 15 16 17 18 20 24; do
  echo "=== N=$N ==="
  docker run --rm -v "$REPO_ROOT":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" atlahs-sim \
    run scaleup_coll_ab --n "$N" --su-topo scaleup_3tier_256_4000Gbps.topo --sizes 85680
done
echo "PODSTEP DONE"
