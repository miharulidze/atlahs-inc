#!/bin/bash
# The floor is NOT the NIC gate (4000/8000/16000 Gbps give bit-identical times).
# Next: is it latency-derived or serialisation-derived?  Two twins of the crossbar with
# ONE parameter doubled each.  Latency x2 -> lambda goes 808.3 -> 1608.3; rate x2 -> tau_b
# and t_ser halve.  Whichever the floor follows identifies it.
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
for T in _probe_single_switch_64_lat2x _probe_single_switch_64_bw2x; do
  OUT=/workspace/simulation-scripts/results/_floorprobe2_$T
  rm -rf "$R/simulation-scripts/results/_floorprobe2_$T"
  echo "===== $T ====="
  docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" atlahs-sim \
    run scaleup_coll_ab --n 64 --su-topo "$T.topo" --sizes 262144,4194304 2>&1 | tail -4
done
echo FLOORPROBE2_DONE
