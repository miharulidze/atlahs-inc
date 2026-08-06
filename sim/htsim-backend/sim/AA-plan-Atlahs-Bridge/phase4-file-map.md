# Phase 4 — ATLAHS/GOAL Bridge: File Scope Map

**Goal:** route GOAL-trace collectives onto the finished Layer-1 INC primitives, instead of
letting them stay decomposed into point-to-point send/recv.

**Worktree:** `atlahs-phase4` (branch `phase4-atlahs-bridge`, off HEAD `8f71e77`).
Self-contained: C++ side + generator submodule populated. Trace **data** (`atlahs_input/`)
is local/untracked — use it from the main worktree or `storage2.spcl.ethz.ch/traces/ai/`.

All paths below are relative to the worktree root. Line numbers verified against this HEAD.

```
GOAL .goal ──txt2bin──▶ binary ──Parser──▶ logsim-interface ──▶ atlahs_htsim_api ──▶ INC primitives
   (③ emit: Python generator)              (① scheduler/dispatch)  (① transport bridge)  (② Layer-1, done)
```

> ⚠️ **Edit `sim/htsim-backend/sim/lgs/`, NOT `sim/LogGOPSim/`.** htsim compiles the former
> (`logsim-interface.cpp:7` includes `lgs/Parser.hpp`). `sim/LogGOPSim/` is the upstream
> standalone toolchain and is not what htsim links.

---

## ① Consume side — C++, what you EDIT (the real work)

The op-type vocabulary is split across THREE files — a new collective op must be added to all:

| File | Role | Key locations |
|---|---|---|
| `sim/htsim-backend/sim/lgs/logsim.h` | `OP_*` integer op constants | `OP_SEND=1` … `OP_MSG=5` at **L87-91** — add `OP_ALLREDUCE`/`OP_REDUCE`/`OP_BCAST`/`OP_REDUCE_SCATTER` |
| `sim/htsim-backend/sim/lgs/txt2bin.cpp` | Text→binary GOAL compiler | `enum OpTypes` **L57** (SendOp L59 / RecvOp L60 / LoclOp L61); `process_item()` **L93**; the per-type `case` switch **L116-139** — add lexer keyword + enum + case |
| `sim/htsim-backend/sim/lgs/Parser.hpp` | Binary GOAL deserialization | `struct DeserializedNode` **L45** (Peer L49, Tag L51, offset L54) + `serialize()` **L181** — add `coll_type` / `group` / `root` fields and round-trip them |
| `sim/htsim-backend/sim/datacenter/logsim-interface.cpp` | Scheduler + op dispatch | the big `switch(elem.type)` **L562** (OP_LOCOP L563, OP_SEND L608, OP_RECV L664, OP_MSG L748); free-op switches **L473** & **L915**; `Send` call site L138 — add collective cases → call new API methods |
| `sim/htsim-backend/sim/datacenter/atlahs_htsim_api.h` / `.cpp` | Transport bridge | methods **h:L50-54** (`Send`/`Recv`/`Calc`/`Setup`/`EventFinished`) — add `Allreduce`/`Broadcast`/`Reduce`/`ReduceScatter` that allocate INC endpoints + install the FIB group |
| `sim/htsim-backend/sim/datacenter/atlahs_event.h` | Event structs (`SendEvent`/`RecvEvent`/`ComputeAtlahsEvent`/`EventOver`) | add collective-event variants carrying group/root/size |

---

## ② Target side — C++, READ to understand the dispatch destination

Finished Layer-1 primitives the API calls into, plus the `.cm` logic to mirror.
**Do not edit** (unless a small hook is needed) — this is what already works.

| File | Role |
|---|---|
| `sim/htsim-backend/sim/datacenter/main_uec.cpp` | **The `.cm` collective construction (~L916-1267) is the template** for what each new `AtlahsHtsimApi::Allreduce/…` must build. The `goal_filename` boot branch is ~L504 |
| `sim/htsim-backend/sim/inc_fib.h` | `INCFibEntry` + `INCFib` (L27-98) — group/tree state the API must install |
| `sim/htsim-backend/sim/datacenter/fat_tree_topology.cpp` | `set_up_mcast()` (~L797) — group/FIB install at setup. **GOAL path needs a pre-scan equivalent** (see Design Decision below) |
| `sim/htsim-backend/sim/datacenter/fat_tree_switch.cpp` | `handle_mcast` / `handle_reduce` (~L169-388) — switch dispatch (context only, no change) |
| `sim/htsim-backend/sim/uec_collectives.{h,cpp}` | `UecCollectiveSrc/Sink`, `UecBcastSrcMcast`, `UecReduceSrc`, `UecMcastSink` — base + subclasses, all merged into one file (was `uec_collective.h` + `uec_bcast.{h,cpp}`, renamed at `c066a4d`) — what the API allocates |
| `sim/htsim-backend/sim/uecpacket.h` | `UecMcastPacket` / `UecReducePacket` (~L220-443) |
| `sim/htsim-backend/sim/network.h` | `PacketSink` `lgs_*` fields (~L259-274) — existing trace↔endpoint binding hint |

---

## ③ Emit side — Python generator, what you EDIT (the small part)

`goal_gen/ai/nccl_generator_v2/` (submodule, populated). All INC-targeted collectives already
have classes; intent (type/root/size/communicator) is intact before decomposition.

| File | Role | Seam |
|---|---|---|
| `nccl_comm.py` | Collective classes + **the intercept point** | `CommOp.to_goal()` **L64** — branch here. Classes: `CollectiveOp` L204, `AllReduce` L250, `ReduceScatter` L535, `Reduce` L589, `Broadcast` L643 (targets); `AllGather` L469, `AllToAll` L76 (leave decomposed). Each carries `coll_info` (`root_rank`, `data_size`) + `communicator` |
| `goal.py` | GOAL AST nodes | add `GoalAllreduce/Broadcast/Reduce/ReduceScatter` mirroring `GoalCalc`/`GoalTraffic` (L101-144). Tag built L121 — uint32, digit widths ≤9; **don't smuggle group/root through the tag, use a real op type** |
| `gpu.py` | `construct_collective()` (L32-110) | builds the `CollectiveOp` from the trace — confirms type/root/size known pre-decomposition |
| `main.py` | CLI + tagging | `prepare_gpu_data` `context_label`/`comm_identifier` block; **add a `--emit-collectives` flag** → one trace yields BOTH decomposed (baseline) and first-class (INC) GOAL files = apples-to-apples comparison |

---

## Design decision to settle before coding

**FIB/group install timing.** The `.cm` path pre-installs all groups at setup via `set_up_mcast()`.
The GOAL path needs the equivalent:
- **(recommended) pre-scan** the trace for collective groups → pre-install FIB entries at setup (mirrors `.cm`).
- *vs.* lazy install on first op (only needed if groups are created/destroyed mid-trace).

**Group encoding:** communicator → member ranks, as a separate group table in the GOAL file
(like `.cm`'s `Grp`) is cleaner than inline member lists.

---

## Recommended order (MVP de-risks the C++ pipeline, not the generator)

1. **MVP (no generator):** hand-write a tiny `.goal` — one `allreduce` line + a dependent `calc` —
   and build the whole C++ chain (logsim.h → txt2bin → Parser → logsim-interface dispatch →
   `AtlahsHtsimApi::Allreduce` → existing INC primitives → completion releases the `calc`).
   Proves the hard part end-to-end with zero generator work; doubles as the completion→DAG check.
   Templates: `atlahs_input/minimal.goal`, `test.goal` (main worktree).
2. **Generator** (the easy 1-3 days): `--emit-collectives` branch in `nccl_comm.py` + AST in `goal.py`.
3. **Real trace:** `Llama7B_N4_GPU16_TP1_PP1_DP16` (pure-DP, allreduce-dominated) and its `_1iter`
   variant for tractable packet-level htsim runs. INC vs decomposed-p2p baseline.

Rough effort: ~2-3 weeks total, front-loaded onto the C++ pipeline via the MVP.

See `plan.md` (this dir) for the full bridge plan and Option A/B rationale.
