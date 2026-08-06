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
DOCKER_USER=${ATLAHS_DOCKER_USER:-$(id -u):$(id -g)}
# NB: do NOT name this GROUPS -- that is a bash special variable (the user's group-id
# array; assignments to it are silently ignored), which on macOS made the loop run once
# at gid 20. Use a plain name.
GROUP_SIZES="2 4 8 16 32 64"
SIZES=4096,4194304          # 4 KiB (latency regime) + 4 MiB (bandwidth regime)
RUN_ID=${ATLAHS_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}
case "$RUN_ID" in *[!A-Za-z0-9._-]*|'') echo "invalid ATLAHS_RUN_ID: $RUN_ID" >&2; exit 2 ;; esac
RUN_REL=simulation-scripts/results/generated-runs/$RUN_ID/scaleup_coll_ab_groupsweep
RUN_HOST=$REPO_ROOT/$RUN_REL
OUT=/workspace/$RUN_REL
TARGET=$REPO_ROOT/simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv
[[ ! -e "$RUN_HOST" ]] || { echo "run archive already exists: $RUN_HOST" >&2; exit 1; }
mkdir -p "$RUN_HOST" "$(dirname "$TARGET")"

for n in $GROUP_SIZES; do
  echo "[groupsweep] |G|=$n  single-switch-64"
  docker run --rm --user "$DOCKER_USER" -v "$REPO_ROOT":/workspace -e SCALEUP_OUTPUT_DIR="$OUT" "$IMG" \
    run scaleup_coll_ab --n "$n" --su-topo scaleup_single_switch_64_4000Gbps.topo --sizes "$SIZES"
done

SOURCE=$RUN_HOST/scaleup_coll_ab.csv
[[ -s "$SOURCE" ]] || { echo "missing generated CSV: $SOURCE" >&2; exit 1; }
PUBLISH_TMP=$TARGET.tmp.$$
cp "$SOURCE" "$PUBLISH_TMP"
mv "$PUBLISH_TMP" "$TARGET"
echo "[groupsweep] archive -> $RUN_REL"
echo "[groupsweep] published -> simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv"
