#!/bin/bash
# sim.py now passes -intranode_cc none. Before trusting that against the committed CSVs,
# check it is numerically neutral for EVERY collective on BOTH fabrics -- probe 5 only
# covered reduce_scatter/ring on the crossbar. If anything moves, the chapter's data needs
# regenerating; if nothing moves, the flag is documentation and the CSVs stand.
set -uo pipefail
R=/Users/wstaempfli/CLionProjects/atlahs
for TOPO in scaleup_single_switch_64_4000Gbps scaleup_3tier_256_4000Gbps; do
  OUT=/workspace/simulation-scripts/results/_ccneutral_$TOPO
  rm -rf "$R/simulation-scripts/results/_ccneutral_$TOPO"
  echo "===== $TOPO (sim.py as committed, i.e. WITH -intranode_cc none) ====="
  docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" atlahs-sim \
    run scaleup_coll_ab --n 64 --su-topo "$TOPO.topo" \
        --sizes 262144,4194304,67108864 2>&1 | tail -2
done
echo CCNEUTRAL_DONE
