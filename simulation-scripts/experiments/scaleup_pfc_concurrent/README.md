# scaleup_pfc_concurrent — PFC / lossless-backpressure validation

**Status: WIRED. Requires a pcm rebuild (adds `-mcast_pin`).**

## What this validates

Losslessness is the correctness *precondition* for in-network aggregation — a dropped
packet corrupts a reduction irrecoverably. This experiment stresses it under adversarial
load and shows PFC working:

- **distributed** (default, `-mcast_pin` absent): the N groups' trees spread round-robin
  across core switches → independent → completion **flat in N**.
- **pinned** (`-mcast_pin 0`): every tree forced onto one aggregation position + core →
  the N collectives contend at one core → PFC backpressure **serialises** them →
  completion grows ~linearly in N. **Zero packet loss** in both arms.

This is the pcm rendering of the fork's retired Fig 5.12, and it also exercises the
post-fix real NIC-rate ingress datapath (M2) under concurrent multicast fan-out.

## Geometry (the crux)

Each group must **span multiple pods** so its tree reaches the *core* tier — pinning only
bites at the core; a single-pod group tops out at the agg tier. On the 256-host 3-tier
fat-tree (16 hosts/pod × 16 pods): group *g* = one host per pod at slot *g* across the
first `PODS_SPANNED` (=8) pods → `{p*16 + g}`. Disjoint by slot ⇒ **N ≤ 16** (hosts/pod),
also ≤ core count (16) so the distributed arm is genuinely flat. 8-pod groups keep the
pinned core the *sole* contended resource (clean attribution).

## Parameters

| parameter | value |
|---|---|
| engine | pcm-sdk (`htsim_flow_app_atlahs`, **rebuilt with `-mcast_pin`**) |
| topology | `scaleup_3tier_256_3600Gbps.topo` (multi-core; pinning meaningful) |
| group geometry | 8-host groups, one per pod over pods 0–7 |
| N sweep | 1, 2, 4, 8, 12, 16 concurrent disjoint groups |
| message | 64 KiB AllReduce (apex) per group |
| arms | distributed (`mcast_pin=-1`) vs pinned (`mcast_pin=0`) |
| metric | makespan = slowest group's completion; assert `drops == 0` |

## The `-mcast_pin` flag (submodule change)

The placement mechanism (`FatTreeTopology::set_mcast_pin_assignment`) already existed but
was dead code. Wired via a new CLI flag in
`sim/pcm-sdk_zhiyi/pcm/apps/htsim_atlahs/htsim_app_atlahs.cpp` (called before
`set_up_mcast()`), plumbed through `common/sim.py` (`run_sim(..., mcast_pin=)`). `-1` =
round-robin (default, distributed arm); `≥0` = pin all trees to that assignment index.

## Reproduce

```bash
docker run --rm -v $(pwd):/workspace atlahs-sim build          # REQUIRED: adds -mcast_pin
docker run --rm -v $(pwd):/workspace atlahs-sim run scaleup_pfc_concurrent --validate
experiments/scaleup_pfc_concurrent/run_thesis.sh               # full sweep
python3 experiments/scaleup_pfc_concurrent/plot.py             # slowest-vs-N, two arms
```

Output: `simulation-scripts/results/scaleup_pfc_concurrent/scaleup_pfc_concurrent.csv`.
Design context: `sim/htsim-backend/sim/AA-plan-Validation-Chapter/plan.md` §10 (X2).
