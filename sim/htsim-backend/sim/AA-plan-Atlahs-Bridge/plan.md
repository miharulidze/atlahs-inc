# Plan — ATLAHS/GOAL bridge to the in-network collectives

**Status:** proposal, awaiting approval / direction
**Branch target:** `WIP-multicast-htsim-direct`
**Goal:** drive htsim from ATLAHS GOAL traces (real AI-model workloads) such
that collective operations are executed on our in-network primitives
(broadcast / reduce / allreduce), not as point-to-point — so we can measure
the INC benefit on real traces (the SHARP-style application evaluation).

---

## 1. What already exists (verified)

There is a **mature GOAL path** in htsim, separate from the `.cm` path:

- `main_uec.cpp` has a `goal_filename` branch (the `-lgs_*` flags) that, instead
  of building a `ConnectionMatrix`, constructs a `FatTreeTopology` + an
  `AtlahsHtsimApi` + a `LogSimInterface` and calls `start_lgs()` to drive the
  whole simulation. The two input paths (`.cm` vs GOAL) are mutually exclusive.
- `lgs/` is LogGOPSim: `Parser.hpp` deserialises the binary GOAL DAG; node types
  are **only `OPTYPE_SEND`(1) / `OPTYPE_RECV`(2) / `OPTYPE_CALC`(3)**
  (`lgs/Parser.hpp:20-22`). Dependencies are a DAG (`DependOnMe` /
  `StartDependOnMe`); compute is a delay (`CALC`, size = ns).
- `logsim-interface.{h,cpp}` is the scheduler: an active queue ordered by time,
  MPI-style send/recv matching (unexpected + receive queues), CPU/NIC resource
  modelling, and it bounces control between GOAL scheduling and the htsim event
  loop (`htsim_simulate_until`). Compute uses `ComputeEvent`/`NullEvent`.
- `atlahs_htsim_api.{h,cpp}` is the transport bridge: `Send()` creates a fresh
  per-flow `UecSrc`/`EqdsSrc`/`NdpSrc` + sink and registers a host route
  (`addHostPort`). `Recv`/`Calc` are largely no-ops (handled in logsim).

**The gap (verified):** GOAL has no collective vocabulary. LogGOPSim/ATLAHS
**decomposes collectives into point-to-point** send/recv trees, so an allreduce
arrives as dozens of `OP_SEND`/`OP_RECV` ops and `Send()` makes ordinary
unicast flows. The in-network handlers (`handle_mcast`, `handle_reduce`) and the
`.cm` collective flags (`is_reduce`, `is_allreduce`) are **never exercised by
the GOAL path**. So today, running a real AI trace shows host-based collectives,
not INC.

---

## 2. The central decision

How does a GOAL allreduce reach our in-network primitives?

### Option A — first-class collective ops in GOAL  *(recommended)*
Add collective op types to the GOAL format (`OPTYPE_ALLREDUCE`, `OPTYPE_REDUCE`,
`OPTYPE_BCAST`), have the ATLAHS trace generator **emit them instead of
decomposing** (this is the "adjusting GOAL files" you mentioned), recognise them
in `logsim-interface`, and route them through new
`AtlahsHtsimApi::Allreduce/Reduce/Broadcast()` methods onto `set_up_mcast` +
`handle_mcast`/`handle_reduce`.

- **Pros:** clean, explicit, exact; reuses the proven `.cm` collective path;
  group membership + op kind come straight from the trace.
- **Cons:** requires the ATLAHS generator to preserve collective intent (a
  change on the trace-production side, outside htsim).
- **Hard dependency / open question:** *can ATLAHS emit collectives as
  first-class GOAL ops?* If the generator already has the collective at the API
  level (NCCL/MPI allreduce in the captured AI trace) before decomposing, then
  yes — we keep it. This is the make-or-break feasibility question (see §5).

### Option B — pattern-match decomposed point-to-point back into collectives
Detect, in `logsim-interface`, that a set of sends/recvs forms a known collective
algorithm (ring / recursive-doubling allreduce) and replace them with one
in-network collective.

- **Pros:** no generator change; works on existing traces.
- **Cons:** fragile and algorithm-specific (ring vs tree vs halving-doubling all
  look different); reconstructing group membership + dependency edges from the
  decomposed ops is error-prone; brittle as new patterns appear.
- **Verdict:** a fallback only if A is impossible.

**Recommendation: Option A.** It is the faithful design and matches your
"potentially adjusting GOAL files." B is a brittle workaround.

---

## 3. Proposed design (Option A)

1. **GOAL format + parser (`lgs/`).** Add `OPTYPE_ALLREDUCE/REDUCE/BCAST` and the
   fields a collective needs that p2p ops lack: a **group id / member list** and
   (for reduce) a **root**. Extend `Parser.hpp` to deserialise them and
   `txt2bin` for the text form. Keep SEND/RECV/CALC untouched (backward
   compatible).
2. **Scheduler (`logsim-interface.cpp`).** Add cases in the op dispatch for the
   collective types. A collective op is a DAG node like any other: it becomes
   *ready* when its dependencies clear, and it must **signal completion** (to
   release dependents) when the in-network collective finishes. Wire that
   completion to the collective's `BarrierTrigger`/recorder (the same mechanism
   the `.cm` path already uses), then decrement the DAG dependents — this is the
   one genuinely new bit of scheduler logic.
3. **Transport bridge (`atlahs_htsim_api`).** Add `Allreduce/Reduce/Broadcast()`
   to `AtlahsApi` + `AtlahsHtsimApi`, constructing the same sources/sinks the
   `.cm` branch builds (`UecReduceSrc` per member, `UecMcastSink`s, apex
   turn-around / FIB-routed Reduce) and the `-allreduce_mode` choice.
4. **Group setup.** The `.cm` path calls `top->set_up_mcast()` once after loading
   all groups. The GOAL path must learn the groups up front (from the trace's
   collective ops) and call `set_up_mcast()` before simulation — a setup pass
   over the parsed DAG to collect group memberships.
5. **Reuse unchanged:** topology, event loop, compute/CALC modelling,
   send/recv matching for the non-collective traffic, lossless/PFC, all
   in-network switch logic.

---

## 4. Where the finite-resource question returns
Real traces run **many** collectives across iterations, often overlapping — this
is the first workload that actually exercises switch-resource contention. So the
finite-aggregation-buffer / concurrent-tree-cap modelling (deferred earlier) gets
its motivating workload here. Recommendation stands: build the bridge first,
observe whether contention is significant on real traces, then add the resource
model with trace-informed parameters.

---

## 5. Open questions (need answers before coding)
- **Q1 (feasibility of A):** Does the ATLAHS GOAL generator have collective
  intent available (does the captured AI trace carry NCCL/MPI allreduce calls),
  and can it emit collective ops rather than decomposing? *This decides A vs B.*
  Where does the generator live (Python side of ATLAHS?), and can we modify it?
- **Q2 (group/root encoding):** How should a collective op carry its member set
  and root in the GOAL format — inline member list, or a separate group table
  (like the `.cm` `Grp` lines)?
- **Q3 (completion → DAG):** Confirm the cleanest way to make an in-network
  collective's completion release its GOAL dependents (reuse the
  `BarrierTrigger`, then call into the parser's `MarkNodeAsDone`/free-dependents
  path).
- **Q4 (scope of first cut):** Start with **Allreduce only** (the ML-dominant
  op, and the one we've most validated), on a small hand-written GOAL trace,
  before wiring the full ATLAHS generator + real traces?

---

## 6. Phasing
- **Phase 1:** GOAL op type + parser + a *hand-written* GOAL trace with one
  allreduce → dispatch → `AtlahsHtsimApi::Allreduce` → completion releases a
  dependent CALC. Proves the path end-to-end without touching the generator.
- **Phase 2:** group-setup pass + multiple/overlapping collectives; validate
  against the `.cm` results (same allreduce should give the same completion).
- **Phase 3:** wire the ATLAHS generator to emit collective ops (Q1); run a real
  AI-model trace; compare INC vs host-based (decomposed) end-to-end — the
  headline application result.
- **Phase 4 (optional):** finite-resource contention model (the deferred item),
  now motivated by real-trace contention.

**Recommendation:** answer Q1 (it gates A vs B and the whole effort), then do
Phase 1 as the proof-of-path. Phase 1 is contained and low-risk; the generator
work (Phase 3) is where the real-trace payoff is.
