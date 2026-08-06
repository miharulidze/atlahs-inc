# coll_ab_pcm — per-operation scale-up INC-vs-endpoint baselines

Isolated single-scale-up-domain A/B for **AllReduce, ReduceScatter, AllGather**
on the pcm-sdk two-tier simulator. Generator-faithful successor to
`../allreduce_ab_pcm` (whose ring arm was hand-rolled). Driver:
`run_coll_ab_sweep.py`. Design rationale: `../../AA-plan-Scaleup-Baselines/plan.md` (local, untracked).

## The two arms

- **INC arm** — one first-class `coll <kind>` op per rank + a `.groups` sidecar
  (in-network multicast + aggregation; ACK-less line-rate sources).
- **Baseline (endpoint) arm** — the **generator's own decomposition**, emitted by
  calling `nccl_generator_v2/communication.py` directly (`Communicator` +
  `{AllReduce,ReduceScatter,AllGather}.to_goal`). These are the exact NCCL
  Ring / Recursive-doubling step sequences the ATLAHS GOAL generator produces for
  real workloads (Demystifying-NCCL, Hu et al. HOTI'25, Tables V–VII). NOT a hand-roll.

Both arms run in ONE scale-up domain (all N ranks in node 0, `-nodes N
-num_gpus_per_node N`) so the whole collective executes intranode, no scale-out
flows. Both end in an identical 100 ns dependent `calc` tail (cancels in the A/B).

## Decisions (locked with user 2026-07-20)

- Baseline = generator decomposition (not hand-rolled).
- **Charge NEITHER arm for reduction compute** (`-reduce_compute_latency 0`; the
  synthetic decomposition emits no reduction `calc`). The A/B isolates data
  movement + step count. The ALU charge remains a sensitivity knob (`--reduce-compute`).
- AllReduce runs against **both** `ring` (2(N−1) steps; = RS+AG) and `rdouble`
  (2·log₂N steps, power-of-2 N only). RS/AG are ring-only (N−1 steps).

## Usage

```bash
# topology-independent: generate every arm, check step counts vs the paper, compile — NO sim
python3 run_coll_ab_sweep.py --validate --n 8

# result sweep (needs the chosen scale-up topology via --su-topo)
python3 run_coll_ab_sweep.py --collective reduce_scatter --n 8 --su-topo <scaleup.topo> --out rs.csv
python3 run_coll_ab_sweep.py --collective allreduce --algo rdouble --n 8 --su-topo <scaleup.topo>
```

Analytic ideal reference (quotable baseline): AllReduce 2(N−1)/N·S/rate + (N−1)·hop;
ReduceScatter / AllGather (N−1)/N·S/rate + (N−1)·hop. rate = 492.3 B/ns (realised
2 ps/B × 4096/4160 MTU framing), crossbar one-way hop 1300 ns. `speedup_vs_ideal =
ideal/inc` is the CC-decontaminated INC-vs-perfect-endpoint speedup.

## Status (2026-07-20)

Built and validated up to the topology-gated run:

- `--validate` PASSES at N=6 and N=8: step counts match the paper exactly
  (AR ring 2(N−1); AR rdouble 2·log₂N; RS/AG N−1); rdouble skips non-power-of-2 N;
  all arms compile with the coll-extended `txt2bin`.
- Smoke run (all four arms, N=8, 256 KiB, on the single-switch crossbar
  **placeholder** — NOT the committed topology): every arm runs, zero drops. INC
  reduce_scatter and allgather first-class datapaths both work (resolving the
  `make_coll_test`-allgather concern — that was a test-harness gap, not an engine
  one). Internal consistency: AR ring baseline = 2× RS/AG ring baseline (Ring AR =
  RS + AG), confirming the decomposition.

## Deferred (needs the scale-up-topology decision)

- The scale-up topology itself (single-switch crossbar vs NVL72-style tree vs the
  emerging NVLink5/UALink/SUE set). `--su-topo` defaults to the crossbar only as a
  validation placeholder.
- The full message-size × N result sweeps and plots for AR/RS/AG.
- Re-running the legacy `../allreduce_ab_pcm` AllReduce at `-reduce_compute_latency 0`
  for convention uniformity (superseded by this harness's AllReduce arm).
