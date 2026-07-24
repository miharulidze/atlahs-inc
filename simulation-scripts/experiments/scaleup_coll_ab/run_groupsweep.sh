#!/bin/bash
# X1 — Group-size sweep (constant-in-|G| demonstration).
# Reuses the scaleup_coll_ab harness, looping |G| over powers of two on the single-switch
# crossbar, at two fixed sizes (latency + bandwidth regime). All 5 collectives per |G|;
# group_size is a CSV column, so every run appends to one sweep CSV.
# Shows: Bcast/Reduce/AllReduce flat in |G| (constant) vs the O(N)/O(logN) baseline; and
# the (P-1)*block ingress-scaling of ReduceScatter/AllGather (the AG<->AR asymmetry).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
IMG=atlahs-sim
# NB: do NOT name this GROUPS -- that is a bash special variable (the user's group-id
# array; assignments to it are silently ignored), which on macOS made the loop run once
# at gid 20. Use a plain name.
GROUP_SIZES="2 4 8 16 32 64"
SIZES=4096,4194304          # 4 KiB (latency regime) + 4 MiB (bandwidth regime)
OUT=/workspace/simulation-scripts/results/scaleup_coll_ab_groupsweep   # container path

echo "[groupsweep] clearing old CSV for a fresh regeneration"
rm -f "$REPO_ROOT/simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv"

for n in $GROUP_SIZES; do
  echo "[groupsweep] |G|=$n  single-switch-64"
  docker run --rm -v "$REPO_ROOT":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" "$IMG" \
    run scaleup_coll_ab --n "$n" --su-topo scaleup_single_switch_64_3600Gbps.topo --sizes "$SIZES"
done

echo "[groupsweep] done -> simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv"
