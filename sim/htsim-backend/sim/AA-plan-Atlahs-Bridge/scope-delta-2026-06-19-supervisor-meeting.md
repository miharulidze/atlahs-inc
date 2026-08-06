# Scope Delta — Supervisor Meeting 2026-06-19

Status: **direction change, recorded for approval.** Companion to `build-plan.md` (the
Phase-4 bridge plan) and `plan.md`. Source: meeting with the supervisor + Shukao (author of the
v2 NCCL→GOAL generator). Transcript: `~/Desktop/Bachelorarbeit Resources/Bachelor Thesis meeting - transcript.{txt,srt}`.

This document records how the meeting changes the **evaluation framing and target workload**. The
Layer-1 INC primitives and the Phase-4 bridge mechanics (`build-plan.md` §1–§3) are **unaffected** —
what changes is *which trace renderings we compare* and *in which network domain*.

---

## 1. The pivot: INC belongs in the scale-up domain, not scale-out

On the call I said *"I always got this wrong — I thought we were doing internode."* The correction:

- **Scale-out (inter-node) INC is infeasible for training.** At ~100k-GPU scale, constant link
  failures force re-establishing the multicast/aggregation trees — centralized state, impractical.
  Nobody deploys in-network computing in the scale-out fabric for training. (This is the
  MRC/SRv6 production story: lossy, multipath, no INC.)
- **Scale-up (intra-node, NVLink-scale, hundreds of GPUs, lossless) INC is feasible.** Model it as a
  *smaller subnetwork ≈ a single layer of switches* — not a multi-tier fat tree. Our current
  fat-tree config collapses to a single switch tier for the scale-up demonstration.

**Implication:** the headline result is *intra-domain* (scale-up), and the multi-tier fat-tree
moves from "the stage" to "the scale-out side / a generality check." The INC primitives themselves
don't change.

## 2. Headline experiment: per-domain P2P-vs-INC A/B

One workload, **two renderings**, compared one-to-one:

| | Scale-up domain | Scale-out domain | Result |
|---|---|---|---|
| **Trace A (baseline)** | point-to-point collectives | point-to-point | iteration time |
| **Trace B (INC)** | first-class `coll` ops → htsim multicast/aggregation | point-to-point | iteration time |

The speedup = A − B, attributable to INC in the scale-up domain. Run on **(a)** generated
TP-heavy traces and **(b)** the published ATLAHS traces ("more use cases ⇒ stronger argument").

**This upgrades `build-plan.md` §4.3:** `--emit-collectives` was a *global* toggle (whole trace
decomposed vs whole trace first-class). It must become **per-domain** — emit first-class `coll`
ops only for collectives whose communicator lives in the scale-up domain; decompose to p2p
everywhere else (the generator's built-in algorithm). Concretely: the per-comm emit decision in
`nccl_comm.py::to_goal` gains a domain predicate on the communicator's member set, not just a
collective-type check.

## 3. Target workload: tensor-parallel **non-decomposed** AllReduce

Verified live by Shukao reading the generator:

- **TP (Megatron row/column-linear) emits a full `AllReduce`, NOT decomposed** → exactly one
  first-class op INC accelerates, on the critical path, in the scale-up domain. All three line up.
- **DDP / data-parallel AllReduce *is* decomposed** into reduce-scatter + all-gather → never a
  single first-class op. (Consistent with the Llama N4 trace being FSDP/ZeRO-shaped: RS+AG
  dominate, AllReduce only for scalars.)

**This corrects `build-plan.md` §0/§5 Stage 3.** That plan named
`Llama7B_N4_GPU16…DP16` as "pure-DP ⇒ Allreduce-dominated" and made Allreduce the MVP target on
that trace. Both premises are now wrong: that trace is FSDP (decomposed), and DP AllReduce
decomposes regardless. **The clean INC demonstration is a TP trace**, where AllReduce stays whole
in the scale-up domain. The MVP/Stage-1 mechanics (all four collectives, end-to-end, already done)
are unaffected — only the *real-run trace selection* changes.

Need a **small TP / Llama-3 trace that runs on a laptop** in reasonable time for the demo.

## 4. New infrastructure dependency: two-htsim scale-up/scale-out co-simulation

Today the GOAL format folds intra-node/NVLink traffic into `calc` "calculation nodes" — the
scale-up domain is invisible as network. To run INC in scale-up we need:

- **"G"'s two-htsim extension** — two coupled htsim instances, one per domain — plus the GOAL
  variant that exposes scale-up as **real non-calc network nodes**. The supervisor is requesting
  access (wrote to "LSG" — name uncertain in the transcript) and will coordinate with G.
- **`simplesim` TP workload generator** (Shukao) — lives in a branch folder `simplesim`,
  currently undocumented; docs promised same day. This generates the TP/Llama-3 scenarios.

**De-risked by the supervisor:** if scale-up access doesn't arrive soon, **start with the existing
published traces** — do not block. So the critical path stays: finish the real-trace→GOAL mapping,
add per-domain emit control, run the A/B on existing traces; fold in the two-domain co-sim when
access lands.

## 5. What stays exactly as planned

- Layer-1 INC primitives (all five collectives) — done, untouched.
- The bridge binary format / lexer / runtime (`build-plan.md` §1–§3) — done, untouched.
- The map-collective-intent-regardless-of-NCCL-algo decision: at the v2 parser's algorithm choice,
  map each collective straight to our first-class GOAL primitive (`build-plan.md` §4.2). Confirmed
  correct by the supervisor on the call.
- Use the **v2** generator (ATLAHS mainline, Tommaso-maintained submodule) — confirm the link with
  Shukao.

---

## 6. Action items

**Mine (critical path):**
1. Finish the real NCCL trace → GOAL mapping (the emit side; the hand-written-GOAL path already works).
2. Make `--emit-collectives` **per-domain** (scale-up INC vs scale-out p2p); produce the two A/B traces.
3. Run the A/B (P2P vs INC in scale-up) on (a) a generated TP trace and (b) a published ATLAHS trace; report the speedup.
4. Send the supervisor a link + a few lines on how to run my pipeline on the base traces.
5. Confirm I'm on the v2 generator (link from Shukao).
6. Sanity-check that the fat-tree config reduces cleanly to a single switch-layer scale-up subnetwork.

**Waiting on (track as dependencies):**
- Shukao → v2-generator link + `simplesim` docs (same day).
- Supervisor → scale-up co-sim access via "LSG"; coordination with G on the two-htsim extension.
- Carl (Mattermost) → additional AI-motivated workloads.

## 7. Timeline

- **Aug 1 — HARD deadline:** official thesis to the department + Torsten + supervisor. Most likely
  the **ETH-library / CV-citable version** ⇒ must be ~95% done and reasonable, not a draft.
- **2-week post-deadline window:** OPTIONAL extra experiments/figures + scheduling the recorded
  15-min talk (Torsten watches the recording). Not a buffer for core work.
- **Target: fully done by end of July**, leaving only slides. Today is 2026-06-19 → ~6 weeks.

## 8. Open questions to confirm with the supervisor

- Exact identity behind "LSG" and the precise scale-up co-sim repo/branch + run instructions.
- Whether the Aug-1 department submission is definitively the ETH-library-published version (he was
  unsure on the call — but the safe move is to make Aug-1 complete regardless).
- Group placement for the scale-up domain in the two-htsim model (which ranks form the TP group on
  which switch tier) — feeds the `.groups` sidecar.

---

## 9. Infrastructure found (added 2026-06-22, from Zhiyi Hu's repo links)

The meeting's tooling exists across three pieces in the ATLAHS SC25 repo (`ZhiyiHu1999/atlahs`,
branch `validations_atlahs_v1.1`). Cast: "Sean" = Siyuan Shen (INC author); Zhiyi Hu owns the
two-domain sim; Tommaso Bonato maintains the repo; v2 generator = Shuhao (GH: Yanksi).

### 9.1 The two-domain simulator (the meeting's "two htsims") — PRIVATE, no INC
`htsim_flow_app_atlahs`, built from **private** repo `ZhiyiHu1999/pcm-sdk` (branch
`feature/support_for_dragonfly_slimfly`). Driven by `scripts/run_goal_workloads_exp.py`:
`-topo` (scale-out, `tree1024`@200 G) + `-intranode_topo` (scale-up, `tree16`@3600 G ≈ NVLink) +
`-num_gpus_per_node 4`; **flow-level**; pluggable congestion control (cubic/dctcp/dcqcn/swift/
strack/nscc/smartt/uec_dctcp). Run via Docker `run -g <dir>`. It runs **plain point-to-point +
CC** — i.e. it is the **baseline (Trace A)** machine; it has **no in-network multicast/aggregation**.
The in-repo stock `htsim-backend` also has no INC. **INC exists only in our `htsim_uec` fork.**
`run_goal_workloads_exp.py:91-94` has a commented-out `VT_OPT_CASES` (`vt_no_opt` vs `vt_opt`) —
an optimized-vs-baseline A/B slot our INC rendering fits.

**→ STILL-OPEN ARCHITECTURE FORK (confirm with Zhiyi):** the A/B must be ONE simulator. Either
(1) port our INC into `pcm-sdk` (private, flow-level — big lift), or (2) add a two-domain topology
to our `htsim_uec` (we control it; INC already works) and use Zhiyi's stack only to generate
workloads. Cross-simulator comparison is a timing-model confound — avoid.

### 9.2 The synthetic generator (`simple_sim` branch) — PUBLIC, and well-shaped for us
`simple_sim` branch of `Yanksi/nccl_generator_v2` (author Shuhao). A parameterized **Llama-3
training** model with explicit `Group("tp"/"dp"/"pp")` parallelism, Megatron MLP, ZeRO-1, pipeline.
Pipeline: `llama3_training.py` (set `tp_size`/`dp_size`/`pp_size` + `Llama3Config`) → per-device IR
graphs pickled to `llama3_graphs/*.pkl` → `simple_sim2goal.py` (`extract_communicators` +
`translate_comm_node`) → text GOAL → `txt2bin` (our `coll` verb, done) → `.bin`.

- **TP AllReduce is kept WHOLE** (`tp_megatron.py:28`, plain TP without sequence-parallelism;
  op = `AllReduceOp`, size = full output-tensor bytes). With SP it decomposes to AllGather +
  ReduceScatter — so **use plain TP (no SP)** to get the non-decomposed AllReduce. This is exactly
  the headline INC target, available first-class.
- **DP/ZeRO-1** emits ReduceScatter + AllGather (decomposed, `zero1.py:54,67`); **PP** is pure p2p
  send/recv. Matches the meeting: only TP gives a whole AllReduce.
- **Per-domain emit = ONE branch in `simple_sim2goal.py::translate_comm_node` (lines 34-63):** each
  collective carries `context` ∈ {tp:1, zero1:2, pp:3}. If `context=="tp"` (scale-up) → emit our
  first-class `coll` op (member ranks from `extract_communicators`); else → `op.to_goal()`
  decompose. That yields the two A/B renderings. Collectives stay first-class in the IR right up to
  `to_goal()`, so the hook is clean.
- **`build_full_training` (`llama3_training.py:211-361`)** is the config surface; set
  `tp_size=2, dp_size=8, pp_size=1` for the `TP2_PP1_DP8` job. Megatron layout = TP fastest-varying,
  so a TP group of consecutive ranks = intra-node = scale-up.
- **Caveats:** `translate_comm_node` is **WIP** (a TODO; unhandled ops return `None` → zero-cost
  `GoalCalc` placeholder) — must be finished so every comm op translates. Scale-up vs scale-out is
  **not explicitly tagged** in the IR; downstream infers it from group + `num_gpus_per_node` +
  `context`. Output is text GOAL → needs `txt2bin`.
- Related branches to check before building: **`tb/intermediate-collective-goal`** (Tommaso — likely
  first-class collective GOAL emit already in progress; may reduce our work) and `zhiyi/tests`.

**Net:** the generator side (per-domain first-class emit of a whole TP AllReduce) is **public and
well-abstracted** — a localized branch + finishing a WIP translator, not a rewrite. The remaining
gate is the **simulator** decision in §9.1.
