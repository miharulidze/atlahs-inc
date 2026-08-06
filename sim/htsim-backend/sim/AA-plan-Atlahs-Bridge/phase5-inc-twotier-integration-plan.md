# Phase 5 — INC → pcm-sdk two-tier integration plan

_Generated 2026-06-28 from a 4-agent investigation (catalog our INC + measure HTSIM_spcl drift + map two-tier wiring → synthesis). Supersedes the high-level `phase5-inc-into-pcm-sdk-plan.md`. Now-known: txt2bin = upstream LogGOPSim 1.1; pcm-sdk two-tier baseline RUNS on the laptop (`-nodes 16 -num_gpus_per_node 4 -topo tree16 -intranode_topo tree16`)._

---

# INC → pcm-sdk Two-Tier Integration Plan

For: BSc thesis student, deadline ~Aug 1. You already built + validated INC single-tier in your own htsim fork; this plan integrates it into pcm-sdk's two-tier sim for the per-domain P2P-vs-INC A/B.

---

## 1. DIFFICULTY VERDICT

**Heavy reconciliation on the transport layer, near-clean on the switch layer, plus one genuinely-new subsystem (tier-awareness).** Don't let "both forks are the same UEC/LogGOPSim lineage" mislead you — that is true at the *grammar/op-code/lineage* level but NOT at the *transport-class* level. Your INC fork branched from an **older UEC ancestor**, before pcm-sdk's `UecNIC`/`UecSrcPort`/`UecPullPacer`/`UecRtsPacket` refactor and before the `FatTreeTopologyCfg`/`cfg()` config split. Concretely:

| Layer | Verdict | Why |
|---|---|---|
| **Switch INC** (`inc_fib.h`, `handle_mcast/handle_reduce/fanout_replicas`, dispatch guard) | **Near-clean transplant** | pcm-sdk's `fat_tree_switch.{h,cpp}` is *byte-identical to the uet-htsim base* (0 diff). `getNextHop`/`receivePacket`/`addHostPort` signatures match. Your dispatch is an additive early-return on `pkt.type()`. Only friction: ~26 `_ft->X(...)` call-sites → `_ft->cfg().X(...)`, and your `getNextHop` body predates `cfg()`. |
| **Transport INC** (`uec_collectives.{h,cpp}`, `UecMcastPacket`/`UecReducePacket`) | **Rewrite, not port** | Your sources/sinks inherit `UecSrc`/`UecSink` whose ctors, base classes, and ACK-less semantics **do not exist** in the target. Target `UecSink(TrafficLogger*, UecPullPacer*, UecNIC&, uint32_t)` has no no-arg ctor; `UecSrc` now `: …, UecTransportConnection`. Your defining mechanism — ACK-less, sink-less, line-rate `connect_collective()` sources that `emit_once()` and drop on receive — structurally conflicts with the target's **mandatory** RTS/`UecPullPacer` credit pacing. uec.h delta ≈1200 lines, uec.cpp ≈4300. |
| **Topology INC** (`set_up_mcast`, `build_mcast_tree`, `_collective_sinks`, egress cache) | **Port + `cfg()` rewiring** | Net-new in your fork, absent in target; but written against pre-`cfg()` API (`_ft->bundlesize`, static arrays). Target moved accessors into `FatTreeTopologyCfg`, made them `const`, de-static-ed arrays. |
| **txt2bin `coll` grammar** | **Small/mechanical** | One `Goal::Collective` method + 5 `OPTYPE_*` defines + 5 deserialize maps + one `coll` verb production. No binary-format change (reuses the 39-byte record). |
| **Tier-awareness** (group→`htsim_apis[node+1]`, global↔node-local rebasing, wrapper handle) | **GENUINELY NEW — the real research/integration work** | Does not exist in either fork. INC was designed scale-up-domain-only and single-tier; the two-domain dispatch + a concrete-`FatTreeTopology` handle through the `TopologyInterface`/`FatTreeTopologyWrapper` indirection + the global-rank vs node-local-rank rebasing are net-new. |

**Honest framing for the thesis:** the headline cost is NOT "moving files." It is (a) re-deriving `uec_collectives` against the modern paced `UecSrc`/`UecSink`, and (b) the net-new tier-awareness. The switch FIB/dispatch and the grammar are the easy, mechanical wins — do them first to build confidence.

A note worth raising with Zhiyi early: the ACK-less collision is the single biggest unknown. There are two escape hatches — (i) re-derive the collective sources to satisfy the pacer's interface minimally (register with a `UecPullPacer` but immediately grant unlimited credit so they still emit at line rate), or (ii) keep the collective packets on a path that bypasses the pacer entirely (they already subclass `Packet` directly, not `UecDataPacket`, so they never enter the data CWND machinery). Option (ii) is likely closer to your existing design and lower-risk; confirm with Zhiyi whether the sink's pacer binding can be made inert.

---

## 2. STEP-BY-STEP PLAN

All target paths under `…/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/HTSIM_spcl/htsim/sim/` (abbreviated `T/`) and LogGOPSim 1.1 under `scratchpad/LogGOPSim-1.1/`. Source-of-truth (port FROM) under `…/atlahs/sim/htsim-backend/sim/` (abbreviated `S/`). Makefile has **no header-dependency tracking → `make clean` after every `.h` edit.**

| # | Change | Files / symbols | Build + validate check | Commit msg |
|---|---|---|---|---|
| **A. txt2bin `coll` grammar onto LogGOPSim 1.1** ||||
| A1 | Add op-codes to runtime headers | `lgs/logsim.h`, `lgs/LogGOPSim.hpp`, `lgs/Parser.hpp`: `OP_BCAST=10…OP_ALLGATHER=14, OP_COLL_DONE=15`; `OPTYPE_BCAST=10…OPTYPE_ALLGATHER=14` (S/`Parser.hpp:27-31`) | `make` lgs builds; no behavior change yet | `lgs: add OP/OPTYPE coll op-codes` |
| A2 | Add `Goal::Collective(optype,group,size,instance,root,cpu,nic)` packing `Peer=(group&0xFFFF)|(root_field<<16)`, `Tag=instance`, `Size=bytes`, 2 asserts | `scratchpad/LogGOPSim-1.1/Goal.hpp` (copy S/`Goal.hpp`); otherwise byte-identical | a hand-written `coll …` .goal → txt2bin → readbin round-trips the 39-byte record with correct Peer/Tag | `lgs: Goal::Collective node packer` |
| A3 | Deserialize map `OPTYPE_*→gp.type=OP_*` in `GetExecutableNodes`; expose `Peer→target, Tag→tag, Size→size` (S/`Parser.hpp:675-679`) | `Parser.hpp` | unit: parse a coll node, assert `gp.type/target/tag/size` | `lgs: deserialize coll nodes` |
| A4 | Add the `coll <kind> <size>b <group> <instance> <root\|ANYSOURCE>` verb to the **`.re`** grammar (1.1 ships `txt2bin.re`, 788 lines) → `Item{coll_group,coll_instance,int coll_root}` defaulted `0,0,-1`; `OpTypes{Bcast/Reduce/Allreduce/ReduceScatter/Allgather}Op`; `process_item` cases call `schedule->Collective(...)`; re-run **re2c** | `txt2bin.re` → regenerated `txt2bin.cpp`. Work at `.re` level (state numbers re2c-assigned, differ from your generated `.cpp`) | `txt2bin` builds; a 4-rank `coll allreduce 1024b 0 0 ANYSOURCE` trace → bin → readback shows 4 OP_ALLREDUCE nodes | `txt2bin: coll verb (re2c)` |
| **B. Net-new INC files into HTSIM_spcl base htsim** ||||
| B1 | Drop in `inc_fib.h` verbatim (header-only, no base deps) | `T/datacenter/inc_fib.h` (copy S/) | compiles standalone (add to a tu that includes it) | `inc: INCFib/INCFibEntry` |
| B2 | Append `UEC_MCAST, UEC_REDUCE` to target `packet_type` enum; add the non-asserting `peek_ingress_queue()` and **generalize `_ingressqueue` from `LosslessInputQueue*` to `VirtualQueue*`** (S/`network.h` +14/−4) | `T/network.h` | `make clean && make`; enum tokens resolve; existing tests pass | `net: UEC_MCAST/REDUCE + VirtualQueue ingress` |
| B3 | Add the two lossless credit classes `McastFanoutCredit`, `ReduceFanInCredit` (both self-deleting `VirtualQueue` subclasses) + expose `LosslessInputQueue::release_bytes` (S/`queue_lossless_input.h` +68) | `T/queue_lossless_input.{h,cpp}` (target base is 0-diff from uet base, so graft cleanly) | `make clean && make` | `inc: lossless mcast/reduce credits` |
| **C. Reconcile shared-file edits (switch / topology / UEC / packets)** ||||
| C1 | **Re-derive collective packets** `UecMcastPacket`/`UecReducePacket` as `: Packet` (NOT `UecDataPacket`) against target `uecpacket.h`, each own `PacketDB`; keep `_seqno/_group_id/_op_seq_id`, `_descending`, `_reduce_root`; factories `newpkt/newpkt_replica/newpkt_combined/newpkt_downward` | `T/uecpacket.h` | `make clean && make`; a unit test allocs+frees a replica chain, asserts `_pathid` stability | `inc: mcast/reduce packets on new base` |
| C2 | **Re-derive `uec_collectives.{h,cpp}`** against modern `UecSrc`/`UecSink`. Decide pacer strategy with Zhiyi (see §1): preferred = collective packets bypass the `UecPullPacer`; sink binds a pacer/NIC but counts payload (`size−acksize`) and fires `end_trigger` as today. Re-implement `UecCollectiveSrc::emit_once`, `UecBcastSrcMcast`, `UecReduceSrc`, `UecCollectiveSink::{register_mcast_op,register_reduce_op,receivePacket}`, `CollectiveCompletionRecorder`, `TriggerRelay` | `T/uec_collectives.{h,cpp}`; friend decls in `T/uec.h` | `make clean && make`; **single-switch unit**: one `UecBcastSrcMcast` → sink fires `BCAST_COMPLETE` | `inc: collective src/sink on paced UEC` |
| C3 | Graft switch INC onto the (0-diff) target switch: re-paste `UEC_MCAST`/`UEC_REDUCE` early-return guards at top of `receivePacket`; add `_inc_fib`, `_reduce_pipe`, `_port_idx_by_queue`, `_port_egress_routes`, `_reduce_barriers`, static `_reduce_compute_latency`; add `addPort` override, `build_egress_route_cache`, `identify_ingress_port_idx`, `addMcastPort`, `handle_mcast`, `handle_reduce`, `fanout_replicas`. **Rewrite ~26 `_ft->X` → `_ft->cfg().X`** and adopt target's small-packet ECMP `getNextHop` (don't port your diverged body) | `T/datacenter/fat_tree_switch.{h,cpp}` | `make clean && make`; **single-switch multicast unit** (one switch, k leaves): k sinks all complete; descending-reduce falls through to base `getNextHop` | `inc: switch mcast/reduce dispatch` |
| C4 | Port topology INC with `cfg()` rewiring: `groups`, `set_up_mcast()`, `build_mcast_tree(group_idx,assign_idx)`, `_collective_sinks` + `get_collective_sink`, `set_mcast_pin_assignment`. Rewire pre-`cfg()` accessors (`bundlesize`, `HOST_POD_SWITCH`, de-static-ed arrays) to `cfg()`; switches are `vector<Switch*>` in target → `static_cast<FatTreeSwitch*>` | `T/datacenter/fat_tree_topology.{h,cpp}` | `make clean && make`; build a small fat-tree, `set_up_mcast` installs one `INCFibEntry` per on-tree switch + one sink per member (assert counts) | `inc: topology set_up_mcast + sinks` |
| **D. Tier-aware INC bring-up (NET-NEW)** ||||
| D1 | Expose a concrete handle through the indirection: add `FatTreeTopology* get_fat_tree_topology()` to `TopologyInterface`/`FatTreeTopologyWrapper` (wrapper already downcasts internally at `fat_tree_topology_wrapper.cpp:185/192`). Do NOT revive `AtlahsHtsimApi::_topo` | `T/datacenter/topology_interface.h:37`, `T/datacenter/fat_tree_topology_wrapper.{h,cpp}` | `make`; a probe in the app downcasts a `su_api`'s topo and reads `cfg()` radix | `topo: expose FatTreeTopology getter` |
| D2 | One-time INC bring-up per **scale-up** topology. Port S/`main_uec.cpp:1736-1756` (`.groups` parse → `top->groups=&goal_groups; top->set_up_mcast()`) into the app instance-construction block. **Because all `su_api`s share ONE intranode topology object** (app `:1125`), call `set_up_mcast` **once** on that shared FatTreeTopology, in **node-local id space** `[0,gpus_per_node)`. `goal_groups` must outlive the run | `pcm/apps/htsim_atlahs/htsim_app_atlahs.cpp:1092-1194` (before `start_lgs` `:1197`) | run app with `-nodes 16 -num_gpus_per_node 4 -topo tree16 -intranode_topo tree16` + a `.groups`; assert FIB built, no crash, baseline P2P still runs | `app: set_up_mcast on scale-up topo` |
| D3 | **Global↔node-local rebasing** (the key hazard): a `.groups` TP group listing global ranks `{4,5,6,7}` (node 1) must rebase to local `{0,1,2,3}` before `set_up_mcast`/sink-registry/route-building. Choose: (a) per-node `.groups` already in local ids + node→instance map, or (b) global→local rebase in coll launch keyed off `group_id→owning node`. Mirror the p2p remap `host % gpus_per_node` (`logsim-interface.cpp:140-141`) | rebasing helper near coll launch; `.groups` loader | unit: a global-id group rebases to the same local FIB keys the single shared topo expects; sinks/routes don't desync | `inc: global→node-local group rebase` |
| **E. Coll-op dispatch in patch logsim-interface** ||||
| E1 | Port the coll machinery FROM S/`logsim-interface.cpp`: `CollectiveCompletionAdapter` (`:184`), `make_coll_route` (`:195`), `push_coll_done` (`:206`), `launch_collective` (`:223`), `collective_complete` (`:415`), `CollOpState`, `_pending_collectives`, `_next_coll_barrier_id=1<<28`. Replace its `_topo->...` concrete calls with the new wrapper getter (D1) on the chosen instance | `T/logsim-interface.{h,cpp}` (re-graft onto the multi-domain `htsim_apis[]` loop, orthogonal to the two-tier delta) | `make clean && make` | `lgs: port collective launch/complete` |
| E2 | Add op-loop arms to the patch's big switch (`~:659`, currently default `printf("not implemented")` at `:535`): `OP_BCAST/REDUCE/ALLREDUCE/REDUCE_SCATTER/ALLGATHER → MarkNodeAsStarted; sends_active++; launch_collective(elem)`; `OP_COLL_DONE → MarkNodeAsDone` | `T/logsim-interface.cpp` op loop | a 4-rank coll trace dispatches without hitting the default arm | `lgs: coll op-loop arms` |
| E3 | **Coll-specific instance picker** (net-new; `get_routing_domain_src_dst` only does point pairs). A coll op carries a group, not a target pair → derive `node=⌊rank/gpus_per_node⌋`, launch on `htsim_apis[node+1]`, reach *that* instance's FatTreeTopology via D1, register sinks/sources in node-local space (D3). Guard: assert all group members share one node (TP intra-node) for now | `T/logsim-interface.cpp` near `:123` picker | a TP group `{4,5,6,7}` launches entirely on `htsim_apis[2]`; `*_COMPLETE` line prints; DAG releases via `OP_COLL_DONE` | `lgs: group→scale-up instance dispatch` |
| **F. Validate** ||||
| F1 | **Single scale-up domain INC** (Milestone, see §3): one node, `-num_gpus_per_node 4`, one TP AllReduce coll trace, INC arm vs P2P-rendered arm; compare `ALLREDUCE_COMPLETE duration_ns` | hand-written `.goal`+`.groups`; the same allreduce as chunked-ring p2p trace | INC completes; duration < P2P; matches the single-tier number from your fork (sanity) | `eval: single scale-up INC A/B` |
| F2 | **Full two-tier A/B**: 16-host, 4-node, scale-out P2P between nodes + scale-up INC TP AllReduce within each. Baseline arm = all-P2P (`-sender_cc_only`); INC arm = scale-up coll | real/synth two-tier trace | both arms run to completion; per-domain durations logged; A/B table produced | `eval: two-tier P2P-vs-INC A/B` |

---

## 3. MINIMAL FIRST MILESTONE (de-risk early)

**One TP AllReduce in a single scale-up domain inside pcm-sdk, vs its P2P (chunked-ring) rendering — both in pcm-sdk's two-tier sim with `-nodes 1 -num_gpus_per_node 4`.**

Why this is the right floor:
- It exercises the **entire risky spine** — re-derived `uec_collectives` on the paced UEC (C2), switch dispatch (C3), `set_up_mcast` on the shared scale-up topo (D2), the wrapper handle (D1), and the coll op-loop + instance picker (E2/E3) — **without** yet needing global↔local rebasing across multiple nodes (D3 trivial: node 0, local==global) or the scale-out domain.
- It produces a **directly comparable number**: the `ALLREDUCE_COMPLETE duration_ns` line you already grep, runnable against your single-tier fork's number for that same group as a cross-check that the transplant preserved semantics.
- It is the smallest thing that answers the thesis's headline question (per-domain INC-vs-P2P) end-to-end in the target sim.

Build order to reach it fastest: **A (grammar) → B1/B2 (enum+FIB) → C1/C2 single-switch unit → C3 single-switch multicast unit → D1+D2 (one shared topo, local space) → E1/E2/E3 → F1.** Defer C4's full multi-pod tree, D3 rebasing, B3 lossless credits, and the scale-out domain until F1 is green.

---

## 4. EFFORT, RISKS, WHERE ZHIYI HELPS

### Rough effort (person-days; assumes you know your own INC code cold)
| Step group | Days | Notes |
|---|---|---|
| A (grammar) | 1.5 | Mechanical; re2c regen is the only fiddly part |
| B (net-new files + enum/credits) | 1.5 | Near-clean; FIB header drops in |
| C1 packets | 1.5 | Re-derive against `UecBasePacket` tree |
| **C2 uec_collectives** | **4–6** | **The blocker.** ACK-less vs paced reconciliation; budget a spike day with Zhiyi up front |
| C3 switch dispatch | 2 | Clean graft + `cfg()` rewiring of ~26 sites + `getNextHop` body swap |
| C4 topology | 2 | `cfg()` rewiring, `vector<Switch*>` casts |
| D1 wrapper handle | 0.5 | Small, but design-sensitive |
| D2 bring-up | 1 | Shared-topo subtlety |
| **D3 rebasing** | **2** | Net-new; the desync-hang failure class |
| E (dispatch) | 2.5 | Orthogonal re-graft onto multi-domain loop |
| F1 milestone validate | 1 | |
| F2 two-tier A/B | 2 | |
| **Total** | **~22–24 pd** | ≈4.5–5 calendar weeks solo — **tight but feasible for Aug 1** if C2 doesn't blow up |

### Top risks (ranked)
1. **ACK-less vs mandatory `UecPullPacer` (C2).** Highest. If the paced sink/NIC cannot be made inert for collective packets, the rewrite balloons. **Resolve in week 1 with a spike**, not at the end. Fallback: keep collective packets fully off the `UecDataPacket`/CWND/pacer path (they already subclass `Packet`).
2. **Global↔node-local rebasing desync (D3).** Same failure class your single-tier comment (S/`logsim-interface.cpp:234-239`) warns about — wrong keying → silent hang, not a crash. Build the rebasing unit test before wiring the full launch.
3. **Shared single intranode topology (D2).** All `su_api`s point at ONE topology object → one FIB in node-local space reused by every scale-up instance. Building per-instance FIBs would be wrong. Verify the sharing assumption (app `:1125`) holds in your checkout before D2.
4. **`cfg()` refactor breadth (C3/C4).** ~26 switch call-sites + topology accessors now `const` + de-static-ed arrays. Mechanical but a compile-error slog; do it in one focused pass.
5. **re2c state-number drift (A4).** Don't transplant your generated `.cpp`'s `s_31/s_34` states — they won't match 1.1's numbering. Work at the `.re` level and regenerate.
6. **`make clean` discipline.** No header-dep tracking → stale-object false greens after `.h` edits.

### Where Zhiyi's pairing genuinely helps vs solo
**Zhiyi (pair / ask):**
- **C2 pacer strategy** — the one decision that gates the whole transport rewrite; he owns the modern UEC/`UecPullPacer`/NIC design and knows whether the sink's pacer binding can be made inert. *Highest-leverage hour you can spend.*
- **D1/D2 indirection** — he designed `TopologyInterface`/`FatTreeTopologyWrapper`/the N+1 instance construction and the shared-intranode-topo assumption; confirm the wrapper getter is the sanctioned route (vs reviving `_topo`) and that the single-shared-topo invariant holds.
- **The earlier two-tier null-deref** (`logsim-interface.cpp:1016`, `htsim_apis[]` unset) and the **.bin-format incompatibility** you escalated — confirm those are resolved in the checkout you're integrating against, or E2/E3 will trip them.

**Solo (proceed without him):**
- All of **A** (grammar — self-contained, proven in your tree).
- **B1/B2/B3, C1, C3, C4** — net-new INC + mechanical `cfg()` rewiring; you wrote this code and the switch is 0-diff from base.
- **D3 rebasing, E2** op-loop arms, **F1/F2** validation — straightforward once C2/D1 are unblocked.

---

### Key file anchors (verbatim, for the student)
- **Port FROM (your fork):** `/Users/wstaempfli/CLionProjects/atlahs/sim/htsim-backend/sim/{inc_fib.h, uec_collectives.{h,cpp}, uecpacket.h, queue_lossless_input.h}`, `…/datacenter/{fat_tree_switch.{h,cpp}, fat_tree_topology.{h,cpp}, logsim-interface.cpp:184-430, main_uec.cpp:1736-1756}`, `…/lgs/{Goal.hpp, Parser.hpp:27-31,675-679}`.
- **Port INTO (target):** `/Users/wstaempfli/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/HTSIM_spcl/htsim/sim/{uec.{h,cpp}, uecpacket.h, network.h, queue_lossless_input.{h,cpp}, logsim-interface.cpp}`, `…/datacenter/{fat_tree_switch.{h,cpp}, fat_tree_topology.{h,cpp}, topology_interface.h:37, fat_tree_topology_wrapper.{h,cpp}:185/192}`, `…/pcm/apps/htsim_atlahs/htsim_app_atlahs.cpp:1092-1197`.
- **Grammar:** `scratchpad/LogGOPSim-1.1/{Goal.hpp, Parser.hpp, txt2bin.re}` (re-run re2c).
- **Patch op-loop anchors:** picker `logsim-interface.cpp:123`, `send_event:149`, op loop `:558`, `OP_SEND:661`, default-no-coll-arm `:535`, big switch `~:659`.