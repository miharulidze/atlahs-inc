#!/bin/bash
# Two remaining unknowns in the ring baseline's residual:
#   (i)  c ~ 2.11 ns per ROUND, left after removing the (2d-1)*H/B ACK serialisation.
#        Already known NOT to be per-packet (identical at 1 and 16 packets per round),
#        nor latency- or depth-derived.  Arm A doubles the MTU: 16 packets/round becomes
#        8 and t_ser doubles.  If c moves, it is frame-related after all.
#   (ii) the growth beyond the floor at shard >= 256 KiB (+0.5, +6.7, +31.2 ns/round at
#        b = 256 KiB, 1 MiB, 4 MiB).  The NIC send gate is exactly tau_b + one frame, so it
#        approaches the round period as b grows (15% of it at b=64 KiB, 91% at b=4 MiB).
#        Arm B quadruples the gate rate at those sizes: if the growth is the gate it must
#        shrink 4x; if it does not move, the gate is exonerated at large b too.
set -uo pipefail
R=/Users/wstaempfli/CLionProjects/atlahs
echo "########## ARM A: MTU 4160 -> 8320 at |G|=64, S = 256 KiB and 4 MiB ##########"
for MTU in 4160 8320; do
  OUT=/workspace/simulation-scripts/results/_floorprobe4_mtu$MTU
  rm -rf "$R/simulation-scripts/results/_floorprobe4_mtu$MTU"
  echo "===== mtu $MTU ====="
  docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" \
    -e SIM_EXTRA_FLAGS="-mtu $MTU" atlahs-sim \
    run scaleup_coll_ab --n 64 --su-topo scaleup_single_switch_64_4000Gbps.topo \
        --sizes 262144,4194304 2>&1 | grep -E "reduce_scatter|ok" | tail -3
done
echo "########## ARM B: NIC gate 4000 vs 16000 Gbps at LARGE shard ##########"
for RATE in 4000000 16000000; do
  OUT=/workspace/simulation-scripts/results/_floorprobe4_bigrate$RATE
  rm -rf "$R/simulation-scripts/results/_floorprobe4_bigrate$RATE"
  echo "===== gate $RATE ====="
  docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" \
    -e SIM_EXTRA_FLAGS="-intranode_linkspeed $RATE" atlahs-sim \
    run scaleup_coll_ab --n 64 --su-topo scaleup_single_switch_64_4000Gbps.topo \
        --sizes 16777216,67108864 2>&1 | grep -E "reduce_scatter|ok" | tail -3
done
echo FLOORPROBE4_DONE
