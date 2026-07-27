#!/usr/bin/env bash
# Why are the INC models exact on the crossbar but ~1.3% optimistic on the three-tier
# fabric at large S?  Established from the committed CSVs:
#   * Reduce-Scatter, 3tier: residual settles on a CONSTANT 27.74 ns/MB (1.32% of the
#     serialisation time) from 1 MB up.  A rate deficit, not a byte-count error --
#     solving for an implied block count gives 64.83, not an integer.
#   * AllGather,      3tier: EXACT to 16 MB, then 36.4 ns/MB.  A threshold, not a rate.
#   * Both exact on the single switch at every size to 256 MB.  0 drops, except
#     RS@256MB which has 637,571 -- that row is not a valid data point.
#   * All tiers are 4000 Gbps in the .topo, so it is not a rate mismatch there.
#   * A 2-tier probe at N=16, which also binds on a SHARED leaf uplink with kappa=N,
#     is exact to +1.3 ns -- so a shared binding link alone is not the cause.
# ARM A isolates DEPTH on one fixed fabric: on scaleup_3tier_256, |G|=4 stays inside a
#   leaf (d=1), |G|=16 inside a pod (d=2), |G|=64 spans four pods (d=3). Same topology
#   file, same rates, only the depth of the group's tree changes.
# ARM B tests the buffer hypothesis behind AllGather's threshold: rerun |G|=64 with a
#   queue two orders larger. If the deficit vanishes it is PFC occupancy, not the model.
# Runs in Docker: txt2bin and the simulator are Linux ARM64, not host binaries.
set -u
cd "$(dirname "$0")/../.."          # repo root, bind-mounted at /workspace
T=scaleup_3tier_256_4000Gbps.topo
S=4194304,67108864
D="docker run --rm -v $PWD:/workspace"

for n in 4 16 64; do
  echo "===== ARM A: |G|=$n on $T ====="
  $D -e SCALEUP_OUTPUT_DIR=/workspace/simulation-scripts/results/_tierprobe_n$n \
     atlahs-sim run scaleup_coll_ab --n "$n" --sizes "$S" --su-topo "$T" \
     >"simulation-scripts/results/_tierprobe_n$n.log" 2>&1
  echo "  exit=$?"
done

echo "===== ARM B: |G|=64, queue x100 ====="
$D -e SIM_EXTRA_FLAGS="-intranode_q 400000000" \
   -e SCALEUP_OUTPUT_DIR=/workspace/simulation-scripts/results/_tierprobe_bigq \
   atlahs-sim run scaleup_coll_ab --n 64 --sizes "$S" --su-topo "$T" \
   >simulation-scripts/results/_tierprobe_bigq.log 2>&1
echo "  exit=$?"
echo TIERPROBE_DONE
