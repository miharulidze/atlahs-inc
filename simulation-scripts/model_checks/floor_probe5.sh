#!/bin/bash
# The ring residual has a SECOND term on top of the flat (N-1)*[2d*H/B + 2 ns] floor,
# appearing once the shard passes 64 KiB: +0.5, +6.7, +31.2 ns per round at shards of
# 256 KiB, 1 MiB, 4 MiB.  Already excluded: the NIC send gate (probe 4B, bit-identical at
# 4x the gate rate) and GOAL-level chunking (goal.py chunks Broadcast/Reduce only).
#
# Two remaining structural suspects, both of which would bite only once a message is large
# enough to fill something:
#   arm CC   -intranode_cc none.  The scale-up domain runs DCTCP by DEFAULT
#            (htsim_app_atlahs.cpp:189) and the harness never overrides it, so a
#            congestion window is live on the ring baseline.  A window cap throttles long
#            messages and not short ones -- exactly the observed shape.
#   arm Q    -intranode_q 8000000.  If instead the receiver's queue fills and PFC pauses
#            the sender, more buffer removes it.
# Whichever collapses the excess back to the flat 2.24 ns/round identifies the term.
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
run () {  # $1 = tag, $2... = extra flags
  local tag=$1; shift
  local OUT=/workspace/simulation-scripts/results/_floorprobe5_$tag
  rm -rf "$R/simulation-scripts/results/_floorprobe5_$tag"
  echo "===== $tag  ($*) ====="
  docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" \
    -e SIM_EXTRA_FLAGS="$*" atlahs-sim \
    run scaleup_coll_ab --n 64 --su-topo scaleup_single_switch_64_4000Gbps.topo \
        --sizes 4194304,16777216,67108864 2>&1 | grep -E "reduce_scatter|ok" | tail -4
}
run base
run ccnone -intranode_cc none
run bigq   -intranode_q 8000000
echo FLOORPROBE5_DONE
