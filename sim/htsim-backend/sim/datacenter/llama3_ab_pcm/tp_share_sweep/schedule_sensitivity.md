# C3 −4.65% diagnosis: no bug — the delta is below the trace's schedule-noise floor (2026-07-15)

## Question

After the `-intranode_linkspeed` fix (2026-07-14, see `README.md`), the plain-C3
anchor under the placeholder compute model flipped to a **−4.65%** end-to-end
"gain" (baseline 219,352,385 ns vs INC 229,541,927 ns, +10.19 ms), even though
every offloaded collective individually completes in 3.5–9 µs against the
baseline's ≈30 µs recursive-doubling decompositions. Is the in-network arm's
slowdown a collective-datapath bug, a scheduler bug, or something else?

## Verdict

**No bug.** The collective datapath is verified faster than recursive doubling
even under 20 ms member skew (E6). The −4.65% is a single-schedule draw from a
trace whose makespan is **chaotic at the ±10–25 ms (±5–9%) level under
nanosecond-scale, dependency-preserving perturbations — in both arms and under
both compute models** (E7). Every A/B delta measured at sub-percent TP share
(−10.2 ms placebo, +21.6 ms H100 roofline, ±0.5 ms C1/C2/C4) is comparable to
or smaller than that sensitivity, so none of them is a resolvable point
estimate. The resolvable end-to-end results are the pure-TP configs (C5 4.2×,
SP-C5 8.4× — far above the floor); the per-collective results are unaffected.

## Experiment ladder

Engine: pcm-sdk `htsim_flow_app_atlahs` (run-only), anchor invocation with
`-intranode_linkspeed 3600000`; every run 32/32 (or expected) collective
completions where applicable and **zero drops**. Traces re-rendered from the
generator (both C3 arms byte-identical to the committed `C3/llama3{,_inc}.bin`).
Perturbations via `scripts/perturb_schedule.py` (a `calc (rank%4)·unit` node
inserted after each TP-op completion point, dependents redirected through it —
no bytes, kinds, or dependency shape changed).

| # | experiment | makespan (ns) | reading |
|---|---|---|---|
| E1 | INC arm (colls), unperturbed | 229,541,927 | anchor |
| E1 | baseline (RD), unperturbed | 219,352,385 | anchor |
| E2 | INC goal, colls → `calc 0` | 189,306,085 | TP-free skeleton of the INC rendering |
| E3 | INC goal, colls → `calc 5700` (clean coll cost, no sync) | 190,252,795 | per-op *cost* adds only +0.9 ms → not the amplifier |
| E4 | baseline goal, RD chains → `calc 0` (1,024 ops) | 184,714,262 | TP-free skeleton of the decomposed rendering |
| E5 | INC arm + 0/200/400/600 ns post-coll release stagger | 229,811,322 | simultaneous group release is NOT the mechanism |
| E6 | micro: 1 op, \|G\|=4, one member delayed 20 ms — coll | 20,003,641 | coll adds **3.6 µs** after the straggler |
| E6 | micro: same, recursive doubling | 20,014,084 | RD adds **14.1 µs** → coll is faster in isolation |
| E7 | placebo baseline: unpert / stag100 / stag200 / stag400 | 219,352,385 / 224,930,127 / 237,912,703 / 235,884,063 | spread **18.6 ms** |
| E7 | placebo INC: unpert / stag100 / stag200 / stag400 | 229,541,927 / 225,968,559 / 229,811,322 / 209,990,415 | spread **19.8 ms** |
| E7 | H100 baseline: unpert / stag100 / stag200 | 245,291,887 / 248,729,998 / 221,071,753 | spread **27.7 ms** |
| E7 | H100 INC: unpert / stag100 / stag200 | 223,673,529 / 209,167,274 / 224,754,578 | spread **15.6 ms** |

## What the ladder establishes

1. **Synchronisation, not per-op cost, is the schedule amplifier** (common to
   both arms). Relative to its own TP-free skeleton, executing the 32 TP ops
   costs +40.2 ms as collectives (E1−E2) and +34.6 ms as recursive doubling
   (E1−E4), i.e. ~35–40× the serial op cost, while the same ops without group
   synchronisation cost +0.9 ms (E3−E2). Group-max waiting at each TP site
   (member-arrival skew up to 24 ms, driven by the DP gradient transfers that
   dominate this placeholder-compute trace) re-times the huge G-gated
   scale-out DP phases behind it.
2. **The coll-vs-RD residual is inside the noise.** Raw INC−baseline = +10.2 ms
   decomposes as +4.6 ms skeleton difference (E2−E4: the two TP-free DAGs
   differ by node microstructure alone) + ~4.7 ms sync-cost difference — both
   smaller than the measured single-arm perturbation spreads (15.6–27.7 ms).
3. **The collective implementation is sound**: E5 rules out release timing,
   E6 rules out any op-level defect (the coll beats RD under identical skew),
   and all runs complete with zero drops and full collective counts.
4. **Ensemble means lean positive but are not conclusive at this n**: placebo
   INC 223.8 ± 9.4 vs baseline 229.5 ± 8.9 (≈+2.5%); H100 INC 219.2 ± 8.7 vs
   baseline 238.4 ± 15.1 (≈+8.0%); n = 3–4 per arm (mean ± sample std, ms).

## Implications for quoted numbers

- **Do not quote** single-schedule end-to-end deltas for C1–C4 (either sign,
  either compute model) as point estimates; state them with the ±5–9%
  schedule-sensitivity floor, or quote perturbation-ensemble means ± spread.
- SP-C3's +6.01% (14.1 ms delta) sits near the floor — quote with the caveat.
- C5 (76.17% = 1−1/S exact), SP-C5 (88.14%), and every per-collective /
  microbenchmark number are far above the floor and stand.
- The 2-layer, 16-rank anchor with ~99% of its iteration in G-gated scale-out
  DP traffic is a *stress case* for end-to-end A/Bs, not a representative
  workload; deeper renders (8-layer face validity) and calibrated compute
  (H100 roofline) shrink but do not eliminate the sensitivity.

## Reproduction

```bash
# re-render C3 (generator defaults are the C3 literals), compile, perturb, run:
python3 simple_sim2goal.py                     # baseline arm (llama3.goal)
EMIT_INC=1 INC_CONTEXTS=tp python3 simple_sim2goal.py   # INC arm
python3 scripts/perturb_schedule.py llama3.goal pb200.goal recv 200
python3 scripts/perturb_schedule.py llama3_inc.goal pi200.goal coll 200
txt2bin -i pb200.goal -o pb200.bin   # coll-extended LogGOPSim 1.1 txt2bin
htsim_flow_app_atlahs -goal pb200.bin -nodes 16 -num_gpus_per_node 4 \
  -topo tree16.topo -intranode_topo scaleup_single_switch_4_3600Gbps.topo \
  -intranode_linkspeed 3600000 -end 100000000 -sender_cc_only \
  -intranode_queue_type lossless_input          # (+ -groups/-reduce_compute_latency 100 for INC)
```

## Addendum (2026-07-15, NIC pinned to the pipes' realised rate)

All arms re-measured with `-intranode_linkspeed 4000000` (NIC frame time 8.30 ns
= the pipes' 2 ps/B quantisation; one 492.3 payload-B/ns wire everywhere; the
2026-07-14 numbers above used 3600000 = 443 B/ns NIC pacing). The floor
conclusion is REINFORCED — the ~10% NIC-pacing change acted as one more
perturbation sample:

| quantity | @443 B/ns NIC | @pinned 492.3 | reading |
|---|---|---|---|
| placebo C3 baseline | 219,352,385 | 228,360,322 | +9.0 ms from a FASTER NIC |
| placebo C3 A/B | −4.65% | −0.52% | both sub-floor |
| H100 C3 A/B | +8.81% | −5.22% | sign flip across two NIC pacings |
| SP-C3 A/B | +6.01% | +10.39% | baseline swung 235.2→246.7 ms |
| C5 (pure TP) | 76.17% (S=4.20) | 75.81% (S=4.13) | 1−1/S exact both times |
| INC arms (all) | — | byte-identical | bypass verified again |

Perturbation spot-check at the pinned rate: placebo baseline
{228.36, 219.67, 216.82} ms (unpert/stag200/stag400), INC
{229.54, 229.81, 209.99} ms — spreads 11.5/19.8 ms, floor intact. Ensemble
means at n=3 are themselves unstable to within a few per cent (they lean
positive at 443-pacing, negative at the pinned rate) — consistent with, and
further evidence for, reporting sub-percent-share deltas as unresolved.
