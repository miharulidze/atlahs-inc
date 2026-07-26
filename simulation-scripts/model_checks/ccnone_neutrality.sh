#!/bin/bash
# sim.py now passes -intranode_cc none. Is that numerically neutral against the committed
# chapter CSVs?  The FULL sweep this time: all nine sizes on both fabrics, every collective
# and both AllReduce baselines -- 126 rows, matching results/scaleup_coll_ab exactly.
#
# An earlier, narrower version of this script covered only 256 KB / 4 MB / 64 MB and
# reported "41 of 42 rows identical, one moves". That was not enough to conclude anything
# about the sweep: 256 MB in particular was untested and is where a window or contention
# effect is largest. Hence the full nine.
#
# The committed CSV IS the without-flag arm, so this run doubles as the regeneration: if it
# differs, results/scaleup_coll_ab can be replaced by it wholesale.
set -uo pipefail
R=/Users/wstaempfli/CLionProjects/atlahs
SIZES=4096,16384,65536,262144,1048576,4194304,16777216,67108864,268435456
for TOPO in scaleup_single_switch_64_4000Gbps scaleup_3tier_256_4000Gbps; do
  OUT=/workspace/simulation-scripts/results/_ccneutral_$TOPO
  rm -rf "$R/simulation-scripts/results/_ccneutral_$TOPO"
  echo "===== $TOPO, all nine sizes ====="
  docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" atlahs-sim \
    run scaleup_coll_ab --n 64 --su-topo "$TOPO.topo" --sizes "$SIZES" 2>&1 | tail -2
done
echo CCNEUTRAL_DONE
