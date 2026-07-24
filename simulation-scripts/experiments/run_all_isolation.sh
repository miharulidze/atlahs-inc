#!/bin/bash
# Orchestrator — runs the full isolation experiment matrix in priority order, all on the
# pcm-sdk engine via the atlahs-sim Docker image (single-engine, coherent story).
#
# Priority (cheapest/most-verified first): E1 -> M-A time A/B -> X1 group sweep -> M-B footprint.
# Each step is a frozen per-experiment run_thesis.sh; re-running regenerates that step's CSV.
#
# Prereqs: `docker run --rm -v $(pwd):/workspace atlahs-sim build` has produced the pcm binary
# + coll txt2bin, and the atlahs-sim image is built (see simulation-scripts/Dockerfile).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==== [1/4] M-C E1: AllReduce apex vs composed RS+AG (reduction bandwidth) ===="
"$HERE/scaleup_ar_bandwidth/run_thesis.sh"

echo "==== [2/4] M-A: completion-time A/B, all 5 collectives, single-switch + 3-tier ===="
"$HERE/scaleup_coll_ab/run_thesis.sh"

echo "==== [3/4] X1: group-size sweep (constant-in-|G|), single-switch ===="
"$HERE/scaleup_coll_ab/run_groupsweep.sh"

echo "==== [4/5] M-B: network-footprint reduction, all topologies (Khalilov) ===="
"$HERE/scaleup_coll_footprint/run_thesis.sh"

echo "==== [5/5] X2: PFC/lossless backpressure, pinned vs distributed ===="
"$HERE/scaleup_pfc_concurrent/run_thesis.sh"

echo "==== isolation matrix complete ===="
echo "  (E1 = apex vs RS+AG; reduce_bcast retired.)"
