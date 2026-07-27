#!/usr/bin/env bash
# Follow-up to tierprobe.sh. What that one established, on ONE fixed fabric
# (scaleup_3tier_256_4000Gbps), so rates/latencies/switches are constant:
#   |G|=4  (d=1, one leaf) : RS and AG BOTH EXACT at 4 MB and 64 MB (-0.5 ns).
#   |G|=16 (d=2, one pod)  : RS +2.68% @4MB ; AG exact @4MB, +1.23% @64MB.
#   |G|=64 (d=3, 4 pods)   : RS +1.03% @4MB ; AG exact @4MB, +1.28% @64MB.
# So the deficit needs d >= 2 -- but it is NOT proportional to d: per MB, d=2 is
# 66.3 ns and d=3 only 26.9 ns, i.e. the SHALLOWER group is worse. That kills a
# per-tier cost. It also is not buffer occupancy: a 100x queue changed nothing.
# ARM C: is that null result real, or is -intranode_q inert on this path? Re-run
#   with an absurdly SMALL queue. If nothing moves, the flag does not bind here and
#   Arm B proved nothing.
# ARM D: separate DEPTH from GROUP SIZE. |G|=8 and 16 are both d=2; 32 and 64 are
#   both d=3. If the deficit tracks N within a depth, it is about how many blocks
#   share the binding link; if it tracks d, it is about the tree. 4 MB only: at
#   64 MB the |G|=16 run drops 30,143 packets and is not a clean point.
set -u
cd "$(dirname "$0")/../.."
T=scaleup_3tier_256_4000Gbps.topo
D="docker run --rm -v $PWD:/workspace"
R=/workspace/simulation-scripts/results

echo "===== ARM C: is -intranode_q inert? |G|=64, 4 MB, TINY queue ====="
$D -e SIM_EXTRA_FLAGS="-intranode_q 50000" -e SCALEUP_OUTPUT_DIR=$R/_tierprobe_tinyq \
   atlahs-sim run scaleup_coll_ab --n 64 --sizes 4194304 --su-topo "$T" \
   >simulation-scripts/results/_tierprobe_tinyq.log 2>&1
echo "  exit=$?"

for n in 8 32; do
  echo "===== ARM D: |G|=$n on $T, 4 MB ====="
  $D -e SCALEUP_OUTPUT_DIR=$R/_tierprobe_n$n \
     atlahs-sim run scaleup_coll_ab --n "$n" --sizes 4194304 --su-topo "$T" \
     >"simulation-scripts/results/_tierprobe_n$n.log" 2>&1
  echo "  exit=$?"
done
echo TIERPROBE2_DONE
