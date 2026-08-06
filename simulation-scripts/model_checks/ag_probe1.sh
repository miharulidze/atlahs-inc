#!/bin/bash
# In-network AllGather: Eq. (4.8) is exact to under a nanosecond everywhere EXCEPT the two
# largest three-tier sizes, where the measurement runs above it by 0.82 and 1.07 block
# times (64 MB: +1,745 ns; 256 MB: +9,101 ns).  It is not the shell hand-over -- s=1
# already binds at 16 MB, where the model is exact to 0.6 ns.
#
# Get the shape before guessing at a mechanism, as for the ring floor: sweep the shard
# finely across the onset on the three-tier fabric, and run the SAME shards on the crossbar
# as a control (there the model is exact at every size, so whatever this is needs a
# multi-tier fabric).
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
SIZES=16777216,33554432,67108864,134217728,268435456
for TOPO in scaleup_3tier_256_4000Gbps scaleup_single_switch_64_4000Gbps; do
  OUT=/workspace/simulation-scripts/results/_agprobe1_$TOPO
  rm -rf "$R/simulation-scripts/results/_agprobe1_$TOPO"
  echo "===== $TOPO  shards 256 KiB .. 4 MiB ====="
  docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" atlahs-sim \
    run scaleup_coll_ab --n 64 --su-topo "$TOPO.topo" --sizes "$SIZES" 2>&1 | tail -2
done
echo AGPROBE1_DONE
