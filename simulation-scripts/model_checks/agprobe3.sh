#!/usr/bin/env bash
# AllGather's >=64 MB three-tier deviation: CAUSE FOUND = PFC.
#   |G|=64, S=64MB, 1 MiB block: baseline finish 136,113 vs model 134,593 (+0.71 tau_b).
#   -lossless_high_pfc 300 -> 134,701 (+0.05). -intranode_q 100000 -> no change at all.
#   Falsification: the EXACT 512 KiB-block point (+0.10 tau_b at the default high_pfc=100)
#   goes +2.16 / +5.58 / +39.9 / +139 tau_b at high_pfc 64 / 48 / 32 / 16.
# The default PFC high threshold is 100 packets = 406 KiB, below one block once the shard
# passes ~512 KiB, so a member's ingress backlog pauses the upstream mid-block and the
# pause blocks the OTHER hosts' traffic on that link too.
# THIS PROBE: does raising the threshold perturb anything else? If only INC AllGather
# moves, the deviation is a fabric-configuration artefact and the model was right all
# along. If the ring baselines move too, the whole result set is PFC-limited and the
# threshold is a parameter the chapter has to declare.
set -u
cd "$(dirname "$0")/../.."
D="docker run --rm -v $PWD:/workspace"
R=/workspace/simulation-scripts/results
S=16777216,67108864,268435456
for T in scaleup_single_switch_64_4000Gbps.topo scaleup_3tier_256_4000Gbps.topo; do
  tag=$(echo "$T" | sed 's/scaleup_//;s/_4000Gbps.topo//')
  $D -e SIM_EXTRA_FLAGS="-lossless_high_pfc 300 -lossless_low_pfc 240" \
     -e SCALEUP_OUTPUT_DIR=$R/_agpfc300 atlahs-sim run scaleup_coll_ab \
     --n 64 --sizes "$S" --su-topo "$T" \
     >"simulation-scripts/results/_agpfc300_$tag.log" 2>&1
  echo "$tag exit=$?"
done
echo AGPROBE3_DONE
