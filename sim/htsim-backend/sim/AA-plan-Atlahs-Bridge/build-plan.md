# Phase 4 — ATLAHS↔htsim Bridge: Build Plan (first-class collective GOAL ops)

Status: **proposal, awaiting approval.** Grounded by the `bridge-design-grounding` workflow
(8 agents, verified file:line). Companion to `plan.md` (strategic) and `phase4-file-map.md` (scope).

## 0. Goal & target

Drive htsim from a **real ATLAHS GOAL trace** with collectives expressed as **first-class ops**
(not decomposed to p2p), dispatched onto our INC primitives, and compare completion/iteration
time against the **decomposed-p2p rendering of the same trace**.

- **Primary trace:** `Llama7B_N4_GPU16_TP1_PP1_DP16_BS32` (pure-DP → **Allreduce-dominated**),
  `_1iter` variant for packet-level htsim runs. Source nsys reports on `storage2.spcl.ethz.ch/traces/ai/llama/`.
- Because TP1/PP1, the workload is essentially **Allreduce only** → the MVP targets Allreduce.

## 1. The new GOAL format

### 1.1 Text syntax (one new verb)
One verb `coll <kind>`, reusing the existing size/cpu/nic token states in the re2c grammar:
```
l5: coll allreduce 1048576b group 7 instance 4231 [root -1] [cpu 0] [nic 0]
kind := allreduce | reduce | bcast | reduce_scatter
```
Every participating rank emits its **own** `coll` line for the same collective instance (each
rank's `Communicator` lists all members). `-1` reuses the existing `UINT32_MAX` ANYSOURCE sentinel.

### 1.2 Binary encoding — **no record-width change** (the critical constraint)
The on-disk node record is a **fixed 39-byte stride**, with `SIZEOF_NODE_INFO` hard-coded in **8
sites** across writer + reader (`Goal.hpp:322,346`; `Parser.hpp:474,615,631,679,702,728`). Adding a
fixed field corrupts the in-mmap `dep_cnt` countdown **silently**. So we **overload the `Type`
byte + reuse existing fields** — zero stride math touched, `MAGIC_COOKIE` stays 4223, old/new
binaries stay compatible:

| Record field | Reused for | Notes |
|---|---|---|
| `Type` (char, +4) | collective **kind** | new `OPTYPE_ALLREDUCE=12`/`BCAST=10`/`REDUCE=11`/`REDUCE_SCATTER=13` (disjoint from OP_MSG=5) |
| `Size` (u64, +9) | total bytes | same path as `send` |
| `Tag`  (u32, +17) | **instance id** | shared across the |G| ranks of one collective instance ⇒ becomes the htsim `op_flow_id` (switch aggregation + sink op-state key) |
| `Peer` (u32, +5) | **group id** | → member-set lookup via the group table; `find_root_nodes` ignores Peer, so safe |

**Key insight (resolves the "3 scalars, 2 slots" problem):** kind lives in the `Type` byte, so
`Tag` and `Peer` are free for **instance-id** and **group-id** — exactly the two scalars the bridge
needs. For Allreduce/Reduce-Scatter `root` is implicit (-1) and `redop` is SUM (all INC primitives
are SUM-only today), so **no third scalar is needed for the MVP**. Rooted Reduce/Bcast (later
stages) carry `root` in the group/op table, not on the record.

### 1.3 Group membership — out-of-band (zero binary change)
The |G| host lists go in a **sidecar `.groups` file** the htsim CLI loads to populate
`top->groups` before `set_up_mcast()` (mirrors how `main_uec.cpp:855-856` gets groups from the
`.cm`). Keeps the `.goal` binary 100% format-stable. (Alternative: an in-trace group block +
`MAGIC_COOKIE` bump — rejected as more invasive.)

## 2. C++ pipeline changes (consume side)

Edit the **`sim/htsim-backend/sim/lgs/`** copies (what htsim compiles), and keep the canonical
`sim/LogGOPSim/txt2bin.re` in sync. **Verify-first:** the `lgs/txt2bin.cpp` banner shows a 2023
re2c-3.1 generation; confirm a fresh `re2c` of `.re` reproduces it before treating `.re` as a
drop-in — otherwise hand-patch the generated state machine in `lgs/`.

1. **`lgs/txt2bin.re` + `lgs/txt2bin.cpp`:** add `coll` keyword + a small state chain
   (`kind`, `group`, `instance`, optional `root`), reusing size/cpu/nic states; add `OpTypes`
   parse enum entries; add a `process_item` case calling a new `Goal::Collective(...)`.
2. **`lgs/Goal.hpp`:** add `Goal::Collective(kind, bytes, group, instance, root)` mirroring
   `Send` (L30-45), setting `Type=OPTYPE_*`, `Size=bytes`, `Tag=instance`, `Peer=group`.
   `serialize_mmap` needs **no change** (new Type flows through automatically).
3. **`lgs/Parser.hpp`:** add `#define OPTYPE_*` (10-13); in `GetExecutableNodes` (L663-665) map
   each `OPTYPE_*` → new runtime `OP_*` constant.
4. **`lgs/LogGOPSim.hpp`:** add `OP_ALLREDUCE` etc. after `OP_MSG=5`.

## 3. The bridge runtime (the hard part)

### 3.1 Pre-pass + group install (timing)
On the ATLAHS path, `start_lgs` enters the dispatch loop immediately and **never calls
`set_up_mcast`** (all INC wiring currently lives in the mutually-exclusive `.cm` branch). Add a
**one-time bring-up** between `Setup()` and `start_lgs()`: load the `.groups` sidecar →
`top->groups = &groups` → `top->set_up_mcast()` (builds per-switch `INCFib` + persistent
`UecCollectiveSink` per `(host,group)`). **Port** the flow-id/trigger seeds and the per-collective
launch recipe out of `main_uec.cpp:855-1456` into `AtlahsHtsimApi`.

### 3.2 Dispatch + instance matching (collapse |G| lines → 1 INC op)
Add an `OP_ALLREDUCE` case to the dispatch switch (`logsim-interface.cpp:562`). Per arriving
rank-node:
- `instance = node.Tag`, `group = node.Peer`. Look up `pending_op[instance]`.
- **First arrival:** allocate `op_flow_id = instance`; register the |G| sinks
  (`register_mcast_op`/`register_reduce_op` per kind) + a `BarrierTrigger(count)` + a new
  **`CompletionBridge` TriggerTarget**.
- **Every arrival:** create that rank's `UecReduceSrc`(group, flowid=op_flow_id),
  `connect_collective(srctotor, now)`; record the rank's `lgs_node` in `pending_op[instance]`;
  `sends_active++`.
- Each rank's source starts **when that rank reaches the op** (models stragglers correctly); the
  switch aggregates by `(group, op_flow_id)`; the network op completes only after the **last**
  rank emits.

### 3.3 Completion → DAG release
The `BarrierTrigger` fires `CompletionBridge`, which — for **each recorded rank-node** —
synthesizes `EventOver{event_type=SEND_EVENT_OVER, node=<that rank's lgs_node>}` and calls
`AtlahsHtsimApi::EventFinished` → `flow_over` (`logsim-interface.cpp:157`) → injects `OP_MSG` →
`MarkNodeAsDone(offset)` → releases that rank's dependents. **Accounting law:** `sends_active`
incremented once per rank arrival = number of `EventOver`s fired at completion (else the outer
loop hangs or exits early). `node` must never be null (segfault).

### 3.4 Rank→host mapping
GOAL/goal-rank space ≠ htsim host-id space: `host = goal_rank*number_nics + nic`
(`atlahs_htsim_api.h:121`). `number_nics` defaults to 1 (identity) — but apply the mapping
**consistently** when filling `top->groups`, registering sinks, building `srctotor`, and the
per-rank `EventOver`. MVP assumes single-NIC; guard/assert `number_nics==1` or implement fully.

## 4. Generator changes (emit side)

1. **`goal.py`:** add `GoalCollective(GoalOpAtom)` emitting one `coll …` line.
2. **`nccl_comm.py`:** override `CommOp.to_goal` (must return the `(GoalOp,int)` tuple); when
   `EMIT_COLLECTIVES` and `type(self)` is targeted, emit a `GoalCollective` with
   `instance = comm_identifier` (same across ranks for one op), `group = group_id`,
   `size = CollInfo.data_size`, else fall back to decomposition. `AllReduce` L250 first.
3. **`main.py`:** `--emit-collectives` flag; set `nccl_comm.EMIT_COLLECTIVES` **before** `Pool()`
   (or via `_init_worker` initargs — it's read in forked workers).
4. **Group table:** emit the `.groups` sidecar from each comm's `rank2gpu_id` membership.

This gives **two renderings of one nsys trace**: decomposed (`baseline`) and first-class (`INC`).

## 5. Staging (MVP-first; build in this order)

- **Stage 0 — verify-first (no code):** (a) does fresh `re2c` reproduce `lgs/txt2bin.cpp`?
  (b) `CollInfo.data_size` = total bytes? (c) rootless root sentinel in trace? (d) grep for any
  other `SIZEOF_NODE_INFO` consumer; (e) confirm GOAL path forces UEC protocol.
- **Stage 1 — MVP, no generator:** hand-write a 4-rank `.goal` with one `coll allreduce` per rank
  + a `.groups` file + a dependent `calc`. Build the full C++ chain (lexer→Goal→Parser→dispatch→
  `AtlahsHtsimApi::Allreduce`→INC primitives→CompletionBridge→dependents release). **Proves the
  whole pipeline + completion→DAG with zero generator work.**
- **Stage 2 — generator:** `--emit-collectives` for Allreduce; regenerate the Llama trace.
- **Stage 3 — real run + baseline compare:** decomposed vs INC on `Llama7B_N4_GPU16_1iter`,
  packet-level, under lossless. Completion time + the congestion-interaction analysis.
- **Stage 4 (later):** Bcast, then Reduce/Reduce-Scatter (rooted unicast descent + `addHostPort`).

## 6. Risk register (from the critic — all must be respected)

- **Silent binary corruption** if any fixed field is added → **slot-reuse only** (mitigated).
- **`sends_active` mismatch** → hang/early-exit. Increment-per-arrival = EventOvers-fired. (#1 runtime risk.)
- **Kind/sink mismatch** silently drops packets: `UEC_MCAST`→`register_mcast_op`,
  `UEC_REDUCE`→`register_reduce_op`; Reduce/RS descents also need `addHostPort`.
- **No dedup** → |G| lines launch |G| ops/groups. Instance-id keying (§3.2) is the collapse.
- **`set_up_mcast` timing**: once, after groups known, before any op (pre-pass).
- **Flow-id collision**: `op_flow_id = instance` must be unique per op-instance (comm_identifier is).
- **<2-member groups** skipped by `set_up_mcast` → guard.
- **Self-send demotion** (`host==target`) → a root that is its own leaf vanishes as OP_LOCOP; account for it.

## 7. Open decisions for the user (see chat)
1. MVP scope: Allreduce-only first vs all four.  → **DECIDED: all four.**
2. Baseline: decomposed NCCL-as-traced vs htsim unicast legs.  → **DECIDED: NCCL-as-traced (decomposed).**
3. Membership delivery: sidecar `.groups` vs in-trace block.  → **DECIDED: sidecar `.groups`.**

## 8. Stage 0 outcomes (verified 2026-06-13) & amendments

Workflow `we8cnivmq`, 6 checks: **5 confirmed, 1 refuted (V6, as predicted). No blockers → GO.**

- **V1 (re2c) — confirmed + key correction.** htsim does **not** compile `txt2bin.cpp`; `txt2bin` is a *standalone* `.goal`→`.bin` converter, and htsim consumes the **binary** via `Parser.hpp`. So `coll` is a **two-part** change: (1) the txt2bin **lexer** (text→.bin) and (2) the **binary consumer** (`Goal`/`Parser`/`logsim-interface`). re2c is NOT installed → `brew install re2c`; edit canonical `LogGOPSim/txt2bin.re`, regen `re2c -o .../lgs/txt2bin.cpp .../LogGOPSim/txt2bin.re`, commit both. **MVP shortcut:** build the test `.bin` programmatically via the `Goal::Collective` API + `SerializeSchedule`, decoupling Stage 1 from re2c; the lexer lands with the generator (Stage 2).
- **V2 (size/root) — confirmed.** `CollInfo.data_size` = total bytes, use as-is (no ×type_size, no chunk-sum). Root field is unsigned (=0 for rootless, never −1) → derive root=−1 from collective **type** (AllReduce/AllGather/ReduceScatter); carry root only for Reduce/Broadcast.
- **V3 (Type-overload safe) — confirmed.** 39-byte record unchanged; all striding in `Parser.hpp`; `GetExecutableNodes` (~:665) is the only OPTYPE→OP site. **Two cautions:** (a) `OP_*` is defined in BOTH `lgs/LogGOPSim.hpp` AND `lgs/logsim.h` — add to both; (b) `sim/LogGOPSim/` is a DIVERGENT copy (its `OP_MSG=4`) — scope edits to `sim/htsim-backend/` (only touch `LogGOPSim/txt2bin.re`).
- **V4 (UEC + pre-pass) — confirmed.** GOAL branch hardcodes UEC (`main_uec.cpp:1584`); `_topo`/`_eventlist` reachable + identical to the FIB topology; `set_up_mcast` no-ops unless `top->groups` set. **Add** a `-groups <file>` CLI (parse like `connection_matrix` `Grp`), then between `main_uec.cpp:1582` and `:1604`: `top->groups=&vec; top->set_up_mcast();` + `assert(protocol==SENDER_PROTOCOL)`.
- **V5 (completion adapter needed) — confirmed.** `*CompletionRecorder` is stdout-only; collective sources never set `_atlahs_api`/`lgs_node` and bypass the ACK path where `EventFinished` fires. **Build** a `TriggerTarget` added to each op's `BarrierTrigger` *alongside* the recorder (keep recorders for plots), threaded with `AtlahsHtsimApi*` + group + per-rank `graph_node_properties*`; on fire, emit one `EventOver{node=rank_node}` per rank → `EventFinished`. Wire at the 5 barrier sites.
- **V6 (instance id) — REFUTED, fix specified.** `comm_identifier = hash(...) % 1000` repeats + hash-collides + Python-salted (non-reproducible) → **cannot** be `op_flow_id`. BUT the tuple `(comm_num_id, comm_op_id, comm_seq_id)` is **already shared-across-ranks AND unique-per-instance** (`comm_seq_id` = per-(comm,op) cumcount, same k-th invocation on every rank). **Fix:** a deterministic global pass mapping each distinct tuple → a monotonic integer = `op_flow_id`; budget the tag width (≤5 digits as wired, or widen by shrinking the 4-digit `message_id` since `op_flow_id` now disambiguates).

**Build order (Stage 1):** binary-side foundation (OPTYPE/OP constants in *both* header pairs; `Goal::Collective`; Parser OPTYPE→OP) → dispatch case in `logsim-interface` → `AtlahsHtsimApi` launcher (per-collective, the `.cm` recipe ported) → completion adapter → groups pre-pass + `-groups` CLI → programmatic test `.bin` → run. Generator (Stage 2) brings the lexer + the V6 instance-id counter.

## 9. Stage 1 progress (2026-06-13)

**DONE & compile-verified:**
- **Binary foundation.** `OPTYPE_BCAST/REDUCE/ALLREDUCE/REDUCE_SCATTER` = 10–13 (`Parser.hpp`); OPTYPE→OP arms in `GetExecutableNodes`; `OP_*` mirrored in **both** `LogGOPSim.hpp` and `logsim.h`; `Goal::Collective(optype, group, size, instance, cpu, nic)` writer (Type=kind, Peer=group, Tag=instance, Size=bytes — 39-byte record unchanged). Verified: `g++ -fsyntax-only` probe resolves all constants through the real include chain.
- **Groups bring-up.** `-groups <file>` CLI (`main_uec.cpp` decl :99 / parse after `-goal` :506); `.groups` parser (lines `[Grp] h0 h1 …`, `#` comments); `set_up_mcast` pre-pass before `Setup()` on the GOAL branch + `assert(get_protocol()==SENDER_PROTOCOL)`. Verified: `make main_uec.o` → **0 errors** (36 pre-existing unused-var warnings only).
- **Toolchain.** `re2c` installed via brew (for the Stage-2 lexer).
- Note: clangd flags false positives on these big TUs (e.g. `OP_BCAST`/`UecReduceSink` "undeclared") — the real compiler is clean; trust `make`, not the IDE squiggles.

**NEXT — coupled runtime core (the #1-risk chunk: completion/`sends_active` accounting):**
1. `logsim-interface.cpp` dispatch: add `OP_ALLREDUCE` (+3) case to the `switch(elem.type)`; per arriving rank-node look up `pending_op[Tag]`, first-arrival installs the op (BarrierTrigger + sinks + completion adapter), every arrival creates that rank's source + records its `lgs_node` + `sends_active++`.
2. `AtlahsHtsimApi::Collective(kind, group, instance, size, …)` launcher — port the `.cm` per-kind recipe (`register_mcast_op` for UEC_MCAST bcast/allreduce-apex; `register_reduce_op`+`addHostPort` for UEC_REDUCE reduce/reduce-scatter descent).
3. Completion adapter: a `TriggerTarget` added to each op's `BarrierTrigger` (alongside the recorder); on fire, one `EventOver{node=rank_node}` per recorded rank → `EventFinished` → `flow_over`. `sends_active` decrements must equal increments.
4. Programmatic test `.bin` via `Goal::Collective` + a 4-rank `.groups` + run end-to-end.

Do (1)–(3) with FULL reads of `atlahs_htsim_api.cpp` (Send/Setup/members), the `logsim-interface.cpp` dispatch loop, and the `.cm` allreduce-apex branch (`main_uec.cpp` ~1038–1085) — the accounting must be exact or the outer loop hangs/exits early.
