#!/bin/bash
# Experiment: does the non-rooted ring baseline step when the group first crosses a
# pod boundary?  3-tier 256-host fabric, podsize 16, contiguous rank->host.
# N=16 fits one pod exactly; N=17 spills into a second.
#
# S = 171,360 B, chosen so that (a) it divides evenly by every group size below -- the
# harness skips any size not divisible by N -- and (b) the smallest shard, 171,360/24 =
# 7,140 B, still clears one MSS, which the chapter requires of every sharded collective.
# The previous 85,680 B failed (b) at N=24 (3,570 B).  Still overwhelmingly
# latency-bound: tau_b runs 14.5..29.1 ns against lambda(3) = 3,641.6 ns.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT=/workspace/simulation-scripts/results/_podstep
rm -rf "$REPO_ROOT/simulation-scripts/results/_podstep"
for N in 12 14 15 16 17 18 20 24; do
  echo "=== N=$N ==="
  docker run --rm -v "$REPO_ROOT":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" atlahs-sim \
    run scaleup_coll_ab --n "$N" --su-topo scaleup_3tier_256_4000Gbps.topo --sizes 171360
done
echo "PODSTEP DONE"
