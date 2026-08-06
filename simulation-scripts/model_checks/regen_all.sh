#!/bin/bash
# Full regeneration on the fixed datapath (AllGather completion + tail truncation).
# _podstep is NOT re-run: it reads reduce_scatter only, whose emitter was already
# block-capped and is unchanged by either fix.
set -euo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"
E=simulation-scripts/experiments
for s in $E/scaleup_coll_ab/run_thesis.sh $E/scaleup_coll_ab/run_groupsweep.sh \
         $E/scaleup_coll_footprint/run_thesis.sh $E/scaleup_ar_bandwidth/run_thesis.sh; do
  echo "########## $s"
  bash "$s"
done
echo "REGEN_ALL_DONE"
