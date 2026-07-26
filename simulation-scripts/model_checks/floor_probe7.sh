#!/bin/bash
# Probe 6 gave the growth an exact law.  Excess over the flat 2.243 ns/round is
#     0.0320 ns per frame beyond 48 frames per round,   zero below it
# fitting all ten shard sizes: 64 fr -> 0.513, 128 -> 2.572, 256 -> 6.660, 512 -> 14.851,
# 1024 -> 31.233, and 0.000 at 2/4/8/16/32 frames.  Marginal cost constant to 0.5%.
#
# 48 frames is 199,680 B, and uec.cpp:539 sets _maxwnd = 50 * _mtu = 208,000 B -- the
# sender's window, two frames above the measured knee.  So the growth looks like the
# transfer going WINDOW-LIMITED: past ~50 MTUs in flight each further frame waits on a
# credit.  Causal test: -cwnd raises the window (uec.cpp:191-193 takes it when nonzero).
# If the growth collapses, the window is confirmed; if it does not, _maxwnd is clamping the
# knob and that itself is worth knowing.
set -uo pipefail
R=/Users/wstaempfli/CLionProjects/atlahs
for CW in 0 2000000; do
  TAG=$([ "$CW" = 0 ] && echo default || echo bigcwnd)
  OUT=/workspace/simulation-scripts/results/_floorprobe7_$TAG
  rm -rf "$R/simulation-scripts/results/_floorprobe7_$TAG"
  FLAGS=$([ "$CW" = 0 ] && echo "" || echo "-cwnd $CW")
  echo "===== $TAG ($FLAGS) ====="
  docker run --rm -v "$R":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" \
    -e SIM_EXTRA_FLAGS="$FLAGS" atlahs-sim \
    run scaleup_coll_ab --n 64 --su-topo scaleup_single_switch_64_4000Gbps.topo \
        --sizes 1048576,67108864 2>&1 | tail -2
done
echo FLOORPROBE7_DONE
