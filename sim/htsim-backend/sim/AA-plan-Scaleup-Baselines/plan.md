# AA-plan — Per-operation scale-up baselines for the INC collectives

Status: APPROVED + buildable-now scope BUILT & VALIDATED 2026-07-20 (uncommitted; user:
build on umbrella-integration, don't push/commit yet). Harness: datacenter/coll_ab_pcm/
run_coll_ab_sweep.py (+README). `--validate` PASSES (step counts match paper at N=6,8; all
arms compile); smoke run of all 4 arms clean on the crossbar placeholder (INC RS/AG datapaths
confirmed working; AR ring baseline = 2× RS/AG, confirming Ring AR = RS+AG). Remaining =
topology decision + result sweeps. Local-only doc (AA* gitignored).
Scope: establish, in isolation within a single scale-up domain, an INC-vs-endpoint A/B for
each of AllReduce (AR), ReduceScatter (RS), AllGather (AG). Build everything up to — but not
including — the topology-gated sweep run (scale-up topology still to be chosen).

## PIVOT 2026-07-20 — Docker-model restructure (supersedes the datacenter/ layout below)

User rulings this session, in order: (1) the old `*_ab_pcm` experiments under
htsim-backend/datacenter/ (INCLUDING the coll_ab_pcm just built) STAY as backup, are
NOT correct, and are NOT a reference — wrong engine location + wrong template
(hand-rolled off run_pcm_ab_sweep.py). (2) Redo results oriented on ZHIYI'S scripts/
harness (run_goal_workloads_exp.py conventions). (3) Adopt the DOCKER execution model
for device-agnostic reproducibility.

So the scale-up baseline becomes a scripts/-style, Docker-driven experiment. The
GENERATION LOGIC already built + validated (communication.py-driven baseline, INC-arm
emission, per-collective analytic refs, step-count check — all correct) is RE-HOMED into
the new structure, not discarded. Concrete file plan (grounded in the merged scripts/,
build.py, Dockerfile, tools/loggopsim-coll):

- **build.py + `build_loggopsim_coll()`** — the container currently builds only Zhiyi's
  STOCK txt2bin (sim/LogGOPSim); our `coll` .goal needs the coll-extended txt2bin. Add a
  builder that fetches LogGOPSim-1.1, applies tools/loggopsim-coll/coll.patch, re2c+g++
  (deps already in the Dockerfile), installs to a known /workspace path; wire into
  build_apps(-r). pcm INC binary + generator env already built (submodule pins = our branches).
- **scripts/run_scaleup_coll_ab_exp.py** — styled after run_goal_workloads_exp.py:
  /workspace path constants (EXEC_PATH, COLL_TXT2BIN, TOPO_FILES_PATH, OUTPUT_DIR =
  data/validation/scaleup_coll), a CASES structure (AR{ring,rdouble} / RS{ring} /
  AG{ring} × sizes × N × scale-up topo), generation via the generator + coll-txt2bin,
  the htsim_flow_app_atlahs invocation (his flags + our -groups / -reduce_compute_latency 0),
  max_host_time metric, CSV to data/ (his columns + A/B + analytic ideal).
- **scripts/topo_files/** — scale-up topos live here (Zhiyi's convention), not htsim-backend.
- **entrypoint.sh** — add a `run -s` dispatch → run_scaleup_coll_ab_exp.py.
- Reproduce: `docker build -t atlahs .` → `run build -r` (pcm INC binary + coll-txt2bin +
  generator) → `run -s` (scale-up baselines → data/).

Sub-decisions RESOLVED (user): (A) download LogGOPSim-1.1 during build; (B) extend Zhiyi's
entrypoint.sh + build.py in place.

**BUILT + validated 2026-07-20 (uncommitted on umbrella-integration):**
- `scripts/build.py`: `build_loggopsim_coll()` (fetch LogGOPSim-1.1 → apply coll.patch → re2c+g++;
  idempotent; wired into build_apps(-r)). Recipe validated end-to-end locally (curl/patch/re2c/g++
  all work; the built txt2bin compiles a coll .goal).
- `scripts/run_scaleup_coll_ab_exp.py`: scripts/-style experiment (/workspace path constants,
  env-overridable for local dev; COLL_CASES; generator-driven baseline + INC arm; max_host_time
  metric with a >16-rank fallback; CSV to data/validation/scaleup_coll). `--validate` PASSES
  locally at N=6,8 (step counts match paper, all arms compile).
- `entrypoint.sh`: `run -s` dispatch (arg passthrough) + usage line. bash -n clean; py_compile clean.
Note: our generator (communication.py/goal.py) is pure-stdlib → no build step needed (build.py's
build_nccl_generator_v2 targets the _zhiyi variant, irrelevant to us).

**FINAL RESTRUCTURE + DOCKER PROOF 2026-07-20 (user: sim-only folder "simulation-scripts"):**
The scripts/ edits above were a stepping stone; superseded by a self-contained SIM-ONLY artifact.
- REVERTED scripts/build.py + top-level entrypoint.sh to pristine (Zhiyi's files untouched);
  MOVED run_scaleup_coll_ab_exp.py into simulation-scripts/.
- NEW `simulation-scripts/`: Dockerfile (ubuntu:24.04 + CPU toolchain, NO GPU/torch/flash-attn),
  entrypoint.sh (build|run), build_sim.py (pcm binary + coll-txt2bin), run_scaleup_coll_ab_exp.py,
  README.md. Reuses (not duplicates) the pcm-sdk submodule + generator + tools/loggopsim-coll.
- **PROVEN end-to-end in Docker on this Mac (8 GB arm64 VM):** image builds (1.09 GB); `build`
  produces Linux-ELF pcm binary + coll-txt2bin; `run --validate` PASSES (step counts match paper);
  `run` (full sweep) runs the sim and produces numbers IDENTICAL to the local run (INC 1847/1847/
  1841/1781; base 37481/16595/18740/18740; AR ring = 2× RS/AG) → light base doesn't perturb the
  deterministic sim = device-agnostic reproducibility confirmed. data/ is gitignored (smoke CSV safe).
Prereq: `git submodule update --init --recursive sim/pcm-sdk_zhiyi` (done). Remaining = scale-up
TOPOLOGY choice + the committed result sweep (placeholder crossbar used for the smoke only).

**ANALYTIC IDEAL REFERENCE REMOVED 2026-07-20 (user):** dropped `ideal_ns` + IDEAL_RATE_BNS/
IDEAL_HOP_ONEWAY_NS + the `ideal_ns`/`speedup_vs_ideal` CSV columns entirely. It was an old-microbench
cost-model formula and must NOT be mixed into the measurement CSV. The experiment now records only
MEASURED quantities: inc_ns, base_ns, speedup=base/inc (both arms simulated). The design/buildable-now
mentions of "per-op analytic references" above are SUPERSEDED by this removal.

The design decisions (Q1 generator decomposition / Q2 charge-neither / Q3 both AR algos) are
UNCHANGED and carry over verbatim.

---

## Context & grounding

- The endpoint baseline must be the **generator's own decomposition**, not a hand-roll — i.e.
  the NCCL Ring / recursive algorithms the ATLAHS GOAL generator emits. These are exactly the
  step sequences specified in *Demystifying NCCL* (Hu, Shen, Bonato, Hoefler, HOTI'25), Tables
  V–X, which §VI of that paper states drove ATLAHS's GOAL schedule generation. So the baseline
  arm = what the generator produces; the paper is its spec.
- Ring step structure (k = group size): Ring AR = 2(k−1) steps = ReduceScatter phase +
  AllGather phase; Ring RS = k−1 steps; Ring AG = k−1 steps; each step moves an S/k chunk.
  Recursive AR = log2(k) halving (reduce-scatter) + log2(k) doubling (all-gather), power-of-two k.
- Scale-up isolation is enabled by ATLAHS v2 (Zhiyi thesis §3.3.3): intranode events are now
  simulated by the same packet engine as scale-out, not folded into a calc. Placing all k ranks
  in ONE scale-up domain runs the whole decomposition intranode, no scale-out flows. A single
  domain is also free of the shared-intranode-topology aliasing that clouds the multi-domain runs.
- Bcast/Reduce are excluded: the generator's high-level path (Megatron TP→AR, ZeRO-1 DP→RS+AG,
  PP→P2P) never emits standalone Bcast/Reduce, so there is no decomposition baseline for them.
- Prior art reused: `allreduce_ab_pcm/` already isolates a single AllReduce in one scale-up
  domain (all N ranks in node 0, single-switch crossbar), sweeping msg size and N∈{2..288}, with
  an analytic ideal-ring reference. RS/AG have NO standalone microbench today (only embedded in
  the SP llama3 traces). So the concrete gap = standalone RS and AG, plus AR re-run under the new
  charge convention.

## Decisions (locked with user 2026-07-20)

- **Q1 Baseline source = generator decomposition.** Drive the generator's own routines
  (`communication.py` synthetic path: `AllReduce/ReduceScatter/AllGather._to_goal`,
  `_ring_steps`/`_recursive_steps`) rather than hand-emitting. Preferred realisation: a minimal
  single-collective `simple_sim` graph run through `simple_sim2goal.py`, so BOTH arms come from
  the real `EMIT_INC` path (identical to the llama3 A/B, just a trivial one-op graph). Fallback:
  call `communication.py` collective `_to_goal` directly if the one-op graph is awkward. (Verify
  exact construction API at build time.)
- **Q2 Reduction compute = charge neither arm.** INC arm runs `-reduce_compute_latency 0`;
  the synthetic decomposition already emits no reduction `calc`. Both arms treat reduction
  arithmetic as free (same total work either way); the A/B isolates data movement + step count.
  Consequences: (a) the ALU charge stays a one-line **sensitivity knob** for a later "switch ALU
  = X ns" study; (b) the existing AR microbench (charged 100 ns) must be **re-run at 0** for
  convention uniformity — this re-run is part of the gated run phase, not buildable-now.
- **Q3 AllReduce algorithm = both.** AR baseline runs against BOTH Ring and Recursive-doubling
  (rdouble = generator synthetic default + llama3-16 anchor; ring = bandwidth-optimal + composes
  as RS+AG). RS and AG are ring-only (generator default). rdouble is power-of-two N only.

## Design

A/B mechanism (unchanged, reused): the `EMIT_INC` flag. INC arm → one `coll <kind> <S>b <grp>
<inst> <root> cpu c nic n` + `.groups` sidecar; baseline arm (`EMIT_INC=0`) → generator
decomposition. Same logical graph, one flag.

Isolation: all N ranks in node 0 of a single scale-up domain; one logical collective; no
scale-out flows.

Analytic references (quotable baseline, per collective; rate = realised 492.3 B/ns, hop = 1300 ns):
- AR: 2(N−1)/N · S/rate + (N−1)·hop   [exists in allreduce_ab_pcm]
- RS: (N−1)/N · S/rate + (N−1)·hop    [NEW]
- AG: (N−1)/N · S/rate + (N−1)·hop    [NEW]

Engine invocation (pcm-sdk `htsim_flow_app_atlahs`, from the existing harness): `-nodes N
-num_gpus_per_node N -intranode_topo <scale-up .topo> -intranode_linkspeed 4000000` (MANDATORY —
sets per-GPU scale-up NIC rate; omitting it silently caps p2p arms at 200 Gbps, the 2026-07-14
artifact) `-intranode_queue_type lossless_input -sender_cc_only -end <t>`; INC arm adds `-groups
<file> -reduce_compute_latency 0`.

Metric: `Maximum finishing time at host N` (makespan); collective time = makespan − 100 ns tail;
INC arm cross-checked vs `ALLREDUCE_COMPLETE ... duration_ns`. Speedups: `baseline_ns/inc_ns` and
quotable `ideal_ns/inc_ns`. Drop/lossless-warning counter as correctness oracle.

Harness structure (recommended): generalise `run_pcm_ab_sweep.py` into a collective-parametrised
sweep — `--collective {allreduce,reduce_scatter,allgather} --algo {ring,rdouble}` — writing
per-op CSVs (`reducescatter_ab_pcm/`, `allgather_ab_pcm/`, existing `allreduce_ab_pcm/`).
Alternative: three mirror dirs with copy-paste harnesses (more consistent with the current
layout, less DRY). CSV schema mirrors the existing `results_msgsweep_pcm.csv` columns, plus a
`baseline_algo` column (ring/rdouble) and `reduce_compute_ns` (=0 here).

## Buildable now (topology-independent)

1. Single-collective generator driver: emit both arms (INC coll + `.groups`; baseline
   decomposition) via the `EMIT_INC` path for AR{ring,rdouble}, RS{ring}, AG{ring}.
2. Per-op analytic references (add RS/AG formulas).
3. Collective-parametrised sweep harness (generation → compile → invoke → parse → CSV), with
   `-reduce_compute_latency 0` and the mandatory `-intranode_linkspeed`.
4. Coll-extended `txt2bin` compile pipeline (already vendored).
5. Validation on the EXISTING single-switch crossbar as a THROWAWAY placeholder: confirm
   .goal/.bin/.groups well-formedness, that decomposition step counts match the paper
   (AR ring 2(N−1); AR rdouble 2·log2 N; RS/AG N−1), one clean sim per op (zero drops, INC
   completion matches). This validates the build; it is NOT the result-producing sweep and NOT
   a topology commitment.

## Topology-gated (deferred — needs the scale-up topology decision)

- The scale-up topology itself: single-switch crossbar vs hierarchical fat-tree (NVL72-style,
  which §3.3.3 says the scale-up domain is bound to) vs the emerging NVLink5/UALink/SUE .topo set.
- The full message-size × N sweep and result CSVs/plots for AR/RS/AG.
- The AR re-run at `-reduce_compute_latency 0` for convention uniformity.

## Open questions / risks

- One-op `simple_sim` graph construction: need to confirm the minimal IR to get a single AR/RS/AG
  through `simple_sim2goal` with the INC gate satisfied (context∈INC_CONTEXTS, gpus_per_node=N,
  node_contained). Fallback = direct `communication.py` call. Resolve at build start.
- rdouble is power-of-two N only → AR-rdouble sweep restricted to N∈{2,4,8,16,32,64,128,256};
  ring/RS/AG can also cover N∈{72,144,288}.
- Branch: harness code lands on the work branch (WIP-multicast-htsim-direct), not umbrella-integration;
  any generator tweak goes on nccl_generator_v2 `simple-sim-coll`.
