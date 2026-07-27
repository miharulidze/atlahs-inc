#!/usr/bin/env bash
# AllGather's three-tier deviation: exact to a 256 KiB shard, ~+0.82 tau_b at a 1 MiB
# shard, +1.07 at 4 MiB. Crossbar exact at every size. Reduce-Scatter is now exact
# everywhere, so this is the chapter's last unexplained residual.
# RULED OUT already: the generator segment SEG=512 KiB, which only chops the RING
# baseline chain (goal.py / communication.py) and never touches the INC arm -- so the
# "onset between a 512 KB and a 1 MB shard" in the text is just the gap in the size
# grid, not a threshold anyone imposed.
# ARM A: bisect the onset on a fine shard ladder at |G|=64, d=3 -- shards 256K, 384K,
#   512K, 768K, 1M. If it steps at exactly 512 KiB something IS keyed to that size; if
#   it ramps, it is a rate or occupancy effect.
# ARM B: the same shard sizes at |G|=16 (d=2, one pod) to separate shard size from depth.
set -u
cd "$(dirname "$0")/../.."
T=scaleup_3tier_256_4000Gbps.topo
D="docker run --rm -v $PWD:/workspace"
R=/workspace/simulation-scripts/results

# |G|=64: message = 64 * shard
$D -e SCALEUP_OUTPUT_DIR=$R/_agprobe2_n64 atlahs-sim run scaleup_coll_ab --n 64 \
   --sizes 16777216,25165824,33554432,50331648,67108864 --su-topo "$T" \
   >simulation-scripts/results/_agprobe2_n64.log 2>&1
echo "n64 exit=$?"
# |G|=16: message = 16 * shard, same shard ladder
$D -e SCALEUP_OUTPUT_DIR=$R/_agprobe2_n16 atlahs-sim run scaleup_coll_ab --n 16 \
   --sizes 4194304,6291456,8388608,12582912,16777216 --su-topo "$T" \
   >simulation-scripts/results/_agprobe2_n16.log 2>&1
echo "n16 exit=$?"
echo AGPROBE2_DONE
