# scaleup_ar_bandwidth — fused apex vs composed RS+AG (reduction bandwidth)

**Status: WIRED, NOT YET RUN.** The code is complete and runnable, but no results are
produced yet — reduction-bandwidth numbers wait on the in-flight `B_inc != B_ring`
bandwidth fix. (See "Bug independence" below: the *ratio* is common-mode and would survive
the fix, but per instruction we hold results until it lands.)

## What this measures

Reduction bandwidth `8·S / T` (Gbit/s) vs message size `S`, for **two in-network ways of
doing an AllReduce** — there is **no endpoint baseline** in this experiment:

| arm | `inc_kind` | structure | published analog |
|---|---|---|---|
| `apex` | `allreduce` | fused single-pass apex turn-around (reduce up, turn around in-switch, multicast down) | SHARP / NVLS |
| `composed` | `allreduce_rs_ag` | ReduceScatter then AllGather, two waves with a host resync between | NVLS-multimem / MSCCL++ |

This is the **bandwidth-dual** of the completion-time "apex-fusion premium" (1.93× op-level
/ 1.41× end-to-end) and the companion to the fork's apex-vs-reduce+bcast figure. The story:
**both decompositions pay a host round-trip between waves; only the fused apex turns around
in-switch and approaches wire speed.** Composed RS+AG is expected to land near half wire.

## Bug independence

Both arms are INC coll ops with the identical headerless analytic completion, so the pcm
`B_inc != B_ring` ~2% header artifact is **common-mode and cancels in the apex/composed
ratio**. The bug only distorts INC-vs-*endpoint* comparisons (the `scaleup_coll_ab` A/B),
not this INC-vs-INC structure comparison. The absolute "% of wire" annotation reads ~2%
optimistic until the fix lands; the apex-vs-composed ratio is exact regardless. Results are
held per instruction, not because the comparison is unsound.

## Parameters

| parameter | value |
|---|---|
| engine | pcm-sdk (`htsim_flow_app_atlahs`) |
| \|G\| = N | 64 (`--n`) |
| scale-up topo | `scaleup_single_switch_64_4000Gbps.topo` (`--su-topo`, one-hop crossbar) |
| scale-out topo | `tree16_bw200Gbps.topo` (carries no traffic under single-domain isolation) |
| sizes | 4 KiB … 256 MiB, 9-point log grid, all divisible by N (`--sizes`) |
| reduce_compute | 0 = charge-neither (`--reduce-compute`; >0 = sensitivity) |
| wire rate | pinned NIC `-intranode_linkspeed` = 4 000 000 Mbps (≈ 500 B/ns) |
| reps | 1 (deterministic pcm) |
| metric | `bandwidth_gbps = 8·msg_bytes / inc_ns`; `pct_wire` vs the pinned rate |

## Committed 3-point preview (from `scaleup_coll_ab`, N=64, drops=0)

Both arms already ran at 3 sizes as a byproduct of the INC-vs-endpoint A/B, giving a
validated sanity anchor (not a curve):

| size | apex (ns) | composed (ns) | composed/apex | apex % wire | composed % wire |
|---|---|---|---|---|---|
| 4 KiB | 1324 | 2641 | 1.99× | — | — |
| 4 MiB | 9832 | 19541 | 1.99× | — | — |
| 256 MiB | 546593 | 1084661 | 1.98× | ~98% | ~50% |

Composed ≈ 2× apex time at every size → composed ≈ half apex bandwidth. The dense 9-point
sweep here is the figure; these 3 points are the cross-check.

## Reproduce

```bash
# generate + compile both INC arms, NO sim (safe now):
python3 experiments/scaleup_ar_bandwidth/run.py --validate

# full sweep (RUN ONLY AFTER the B_inc!=B_ring fix lands):
python3 experiments/scaleup_ar_bandwidth/run.py --su-topo scaleup_single_switch_64_4000Gbps.topo

# plot (needs matplotlib; container):
python3 experiments/scaleup_ar_bandwidth/plot.py
```

Output CSV: `simulation-scripts/results/scaleup_ar_bandwidth/scaleup_ar_bandwidth.csv`.
Figure: `ar_bandwidth.pdf` (copy into `thesis-skeleton/figures/matplotlib/`).

## Citations

- Fused apex → **`graham2020sharp`** (SHARP/NVLS), with `sapio2021switchml`, `lao2021atp` as
  fused programmable-switch exemplars.
- Composed RS+AG → NVLS-multimem / MSCCL++ — **needs a new `mscclpp` bib entry** (absent from
  both bibs today). Not `scin` (unanalysed; leans fused).

Design context: `sim/htsim-backend/sim/AA-plan-Validation-Chapter/plan.md` (Experiment E1).
