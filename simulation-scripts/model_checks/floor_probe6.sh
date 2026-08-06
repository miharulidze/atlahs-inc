#!/bin/bash
# Probe 5 eliminated congestion control AND buffer size: -intranode_cc none and
# -intranode_q 8000000 both give BIT-IDENTICAL times at 4/16/64 MB.  (And on inspection
# the CC suspicion was misfounded twice over -- see model_checks/README.md.)
#
# So the >=64 KiB-shard growth is something else.  Get its functional form instead of
# guessing at mechanisms: sweep the shard finely at fixed N=64 and fixed round count, so
# the only thing moving is bytes per round.  Shards 8 KiB .. 4 MiB in powers of two.
# Known so far, as excess over the flat 2.243 ns/round:
#   shard   64 KiB  256 KiB   1 MiB   4 MiB
#   excess    0.00    +0.51   +6.66  +31.20
# Flat, then a knee, then near-linear.  The intermediate points will say where the knee is
# and whether the growth is linear in bytes, in frames, or something with a threshold.
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
SIZES=$(python3 -c "print(','.join(str(64*(8192<<k)) for k in range(0,10)))")
OUT=/workspace/simulation-scripts/results/_floorprobe6
rm -rf "$R/simulation-scripts/results/_floorprobe6"
echo "shards 8 KiB..4 MiB  ->  S = $SIZES"
docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" atlahs-sim \
  run scaleup_coll_ab --n 64 --su-topo scaleup_single_switch_64_4000Gbps.topo \
      --sizes "$SIZES" 2>&1 | tail -3
echo FLOORPROBE6_DONE
