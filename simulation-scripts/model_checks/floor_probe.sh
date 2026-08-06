#!/bin/bash
# Is the 141 ns floor the NIC send gate?  logsim-interface.cpp:960-965 charges
#   nextgs = t + (ceil(size/4096)+1) * 4160 * htsim_G,   htsim_G = 1/B_nic
# i.e. ONE FRAME MORE than the message needs.  The fabric rate comes from the .topo and
# the NIC gate rate from -intranode_linkspeed, and our topo deliberately sets both to
# 4000 Gbps -- so they are confounded in every run so far.  Split them: keep the .topo at
# 4000 Gbps and move ONLY the NIC gate.  If the floor is the gate it scales with it; if it
# is fabric serialisation or latency it does not move at all.
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
for RATE in 2000000 4000000 8000000 16000000; do
  OUT=/workspace/simulation-scripts/results/_floorprobe_$RATE
  rm -rf "$R/simulation-scripts/results/_floorprobe_$RATE"
  echo "===== NIC gate rate $RATE (fabric stays 4000 Gbps) ====="
  docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" \
    -e SIM_EXTRA_FLAGS="-intranode_linkspeed $RATE" atlahs-sim \
    run scaleup_coll_ab --n 64 --su-topo scaleup_single_switch_64_4000Gbps.topo \
        --sizes 262144,4194304 2>&1 | grep -E "reduce_scatter|BASE|ok" | tail -6
done
echo FLOORPROBE_DONE
