#!/bin/bash
# The floor decomposes as (N-1) * [ c + (2d-1)*64/B ].  The second term is the ACK
# serialisation lambda(d) explicitly drops, and it accounts for the whole depth
# dependence (32.3 ns predicted vs 32.7 observed between d=1 and d=3).  This probe tests
# whether the remainder, c ~ 2.12 ns, is really PER ROUND: sweep N at FIXED shard size
# b = 65,536 B, so every run has the same per-round work and only the round count moves.
# If c is per-round the floor must come out as (N-1)*2.243 ns at d=1:
#   N= 4 ->   6.7    N= 8 ->  15.7    N=16 ->  33.6    N=32 ->  69.5    N=64 -> 141.3
set -uo pipefail
R=/Users/wstaempfli/CLionProjects/atlahs
for N in 4 8 16 32 64; do
  S=$((N * 65536))
  OUT=/workspace/simulation-scripts/results/_floorprobe3_n$N
  rm -rf "$R/simulation-scripts/results/_floorprobe3_n$N"
  echo "===== N=$N  S=$S  (shard 65,536 B) ====="
  docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" atlahs-sim \
    run scaleup_coll_ab --n "$N" --su-topo scaleup_single_switch_64_4000Gbps.topo \
        --sizes "$S" 2>&1 | grep -E "reduce_scatter|ok" | tail -3
done
echo FLOORPROBE3_DONE
