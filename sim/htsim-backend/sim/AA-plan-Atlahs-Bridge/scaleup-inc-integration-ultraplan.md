# Scale-up / scale-out INC integration — ultraplan (2026-06-24)

Status: **proposal, awaiting approval.** Companion to `scope-delta-2026-06-19-supervisor-meeting.md`
and `build-plan.md`. Resolves the §9.1 architecture-fork from the scope delta with a concrete,
staged plan grounded in two read-only code maps (the INC surface in `htsim_uec`; the stock
`htsim-backend` that `pcm-sdk` forks from).

---

## 0. Goal

Run the headline experiment — **per-domain P2P-vs-INC A/B on a non-decomposed TP AllReduce that
lives in the scale-up domain** — in **one** consistent two-tier (intra-node + inter-node)
simulator, by Aug 1. INC accelerates the scale-up arm; scale-out stays point-to-point.

## 1. The decisive finding: same base, lopsided deltas

Both forks descend from the same packet-level htsim:

- **Destination is packet-level** (`FatTreeSwitch::receivePacket`→`getNextHop`→queues/pipes, MTU
  configurable; `fat_tree_switch.cpp`, `network.h`, `queue.h`, `pipe.h`). Switch methods are
  `virtual` → INC can attach. It already carries the **same GOAL bridge** INC builds on
  (`logsim-interface`, `atlahs_htsim_api`, `lgs/`), with collectives decomposed to send/recv (the
  GAP we already closed in `htsim_uec`). It has **no INC and no two-tier**.
- **`htsim_uec` INC surface** (verified): switch `handle_mcast`/`handle_reduce`/`fanout_replicas`
  (`fat_tree_switch.cpp:169-453`), `INCFib`/`INCFibEntry` (`inc_fib.h`), packets
  `UecMcastPacket`/`UecReducePacket` (`uecpacket.h`), `UecCollectiveSink`/`UecCollectiveSrc`
  (`uec_collectives.{h,cpp}`), tree build + `set_up_mcast`/`addMcastPort`/`addHostPort`
  (`fat_tree_topology.cpp:602-871`, `fat_tree_switch.cpp:390-482`), GOAL launch
  (`logsim-interface.cpp:223-416`, `lgs/Goal.hpp`, `Parser.hpp`).

**Delta asymmetry (the whole basis of the plan):**

| Delta | Size | Transport coupling | Where it lives today |
|---|---|---|---|
| **INC** (mcast + aggregation) | large | **UEC-entangled** (packet types, `UecSrc` sources, lossless credits, entropy) | `htsim_uec` ✓ working, all 5 collectives |
| **Two-tier topology** (intra+inter) | small | **agnostic** (topology only) | `pcm-sdk` ✓ |

Porting INC → `pcm-sdk` means decoupling it from UEC onto PCM's transport (~1.3× the INC effort,
per the surface map) **plus** learning a private codebase **plus** Zhiyi coordination — on a hard
deadline. Porting two-tier → `htsim_uec` means a localized, transport-agnostic `FatTreeTopology`
extension where INC already runs end-to-end.

## 2. Decision

**PRIMARY (recommended) — Option B: add the two-tier topology to `htsim_uec`.** Bring the small,
clean, transport-agnostic delta to the fork where the big, entangled delta (INC) already works.
Keeps the A/B in one simulator we fully control, with **no private-access dependency on the
critical path**.

**SECONDARY / realism cross-check — Option A: port INC into `pcm-sdk`.** Only if access + time
allow; gives the CC-realistic, group-validated version. Treat as future-work / a robustness check,
not the gating result. (Its cost drops a lot *if* `pcm-sdk`'s transport turns out to be
UEC-with-pluggable-PCM-CC — see Q1 below — because INC's ACK-less sources bypass CC anyway.)

**Access still requested** (not blocking): read `pcm-sdk`'s two-domain topology so we can **lift**
it rather than rebuild (same base), and as the Option-A fallback.

## 3. The one genuinely new design problem: a tier-aware INC tree

Today `build_mcast_tree`/`set_up_mcast` build a group's tree across the single fat-tree. In the
two-tier model, a **scale-up** group (a TP group = the GPUs of one node) must have its
multicast/aggregation tree confined to that node's **intra-node** tier (the NVSwitch-analog,
"one switch layer"). Good news: this mostly *falls out* of correct topology composition — if the
intra-node tier is part of the topology graph the existing RPF tree-builder walks, then a group
whose members share one intra-node domain naturally gets a tree on that intra-node switch. The work
is (a) make the topology two-tier, (b) ensure `set_up_mcast`/`build_mcast_tree` operate over the
combined graph and pick the intra-node common ancestor for a scale-up group.

## 4. Staged plan (Option B)

**Stage 0 — decide + de-risk (this week).** Confirm Option B with supervisor; send Zhiyi the Q's
in §6; request `pcm-sdk` access (to lift topology / as Option-A fallback). Re-verify INC still runs
end-to-end on current HEAD (cheap).

**Stage 1 — two-tier topology in `htsim_uec` (the core build).**
- Extend `FatTreeTopology` to compose an **intra-node** topology (small, NVLink-class BW, e.g.
  `tree16 @ 3600 Gbps`) with the **inter-node** topology (`tree1024 @ 200 Gbps`). Add
  `-intranode_topo` + `-num_gpus_per_node` CLI, **mirroring pcm-sdk's interface** so the same
  `.topo` files / workloads are reusable. (Stock has none of this — `fat_tree_topology.{h,cpp}`,
  `main_uec.cpp` flag parsing are the edit sites.)
- Host→endpoint mapping: each node expands into `num_gpus_per_node` GPU-endpoints behind an
  intra-node switch; the node attaches to the inter-node fabric. Routing: an intra-node hop
  precedes inter-node egress (`HOST_POD_SWITCH`-equivalent gains a tier).
- Make `set_up_mcast`/`build_mcast_tree` tier-aware (§3) so a scale-up group's tree lands in the
  intra-node tier.
- **Validate:** existing INC tests (Bcast/Reduce/Allreduce/RS/Allgather) run on an intra-node
  domain (e.g. 8–16 GPUs) — INC AllReduce completes within the scale-up tier; scale-out p2p flows
  still complete (the `bf7e7ab` entropy fix path).

**Stage 2 — per-domain emit from the generator (`simple_sim`).**
- In `simple_sim2goal.py::translate_comm_node`, branch on `context`: **`tp` (scale-up) → emit our
  first-class `coll` op**; `zero1`/`pp` (scale-out) → `op.to_goal()` decompose. Finish the WIP
  translator (its `None`→placeholder fallbacks). Output two renderings of one model: **baseline**
  (all decomposed) and **INC** (TP as `coll`).
- Config a small **plain-TP** Llama-3 trace (no sequence-parallelism, so AllReduce stays whole):
  `build_full_training`, `tp_size=…/pp=1/dp=…`, laptop-runnable. Text GOAL → `txt2bin` (`coll`
  verb, done) → `.bin` + `.groups`.
- `.groups`: each TP group's members = the GPUs of one node (scale-up domain).

**Stage 3 — the headline A/B run.**
- Run baseline (`.bin` all-p2p) and INC (`.bin` TP-as-`coll`) in two-tier `htsim_uec`, **same topo
  + CC**, measure collective + iteration time. Report speedup; sweep intra-node BW and TP-group
  size → **map the crossover** (per [[reference_inc_crossover]], the contribution is the map, not
  "INC always wins"). Add the published ATLAHS traces for breadth.

**Stage 4 — optional realism (Option A).** If access + time: port INC into `pcm-sdk` using the
surface map for the CC-realistic A/B, or run the baseline arm there as a cross-check (note the
CC confound).

## 5. Risk register & access-gated unknowns

- **Tier-aware tree (§3)** — the real new design; de-risk first with a tiny hand-built two-tier
  topology + one INC AllReduce in the intra-node tier before wiring the generator.
- **pcm-sdk transport identity** (Q1) — determines Option-A cost; doesn't block Option B.
- **Two-tier composition semantics** (Q2) — `num_gpus_per_node` vs `tree16` host-count needs
  pinning; lifting pcm-sdk's code removes the guesswork.
- **Generator `translate_comm_node` is WIP** — finishing it is on the Stage-2 path.
- **Group placement** — TP group must map to same-node GPUs in the scale-up tier (the `.groups`
  sidecar); decouples placement from the trace so it can be swept.
- **CC realism** — Option B's scale-out arm uses UEC, not pcm-sdk's CC sweep; fine for a single
  consistent A/B (both arms share config); pcm-sdk is the cross-check, not the core.

## 6. Questions for Zhiyi (the unblockers)

1. **Transport:** does `pcm-sdk` use UEC with pluggable PCM congestion control, or a different
   transport? (Sets the cost of Option A; `uec_dctcp.json` hints UEC-family.)
2. **Two-tier:** how do `-intranode_topo` + `-num_gpus_per_node` compose the tiers exactly (does a
   node expand into N GPU-endpoints behind an intra-node switch?), and can I read/lift that
   `FatTreeTopology` code (same base)?
3. **Generator:** where do the synthetic TP generation + per-domain emit live (`simple_sim` /
   `zhiyi/tests`), and where are the `goal_workloads/*.bin`?
4. **INC already there?** any NVLS/in-network hook in `pcm-sdk`, or is INC entirely my addition?

## 7. Timeline (today 2026-06-24 → Aug 1)

- Wk1: Stage 0 + Stage 1 spike (hand-built two-tier topo + one INC AllReduce in intra-node tier).
- Wk2–3: Stage 1 complete (general two-tier + tier-aware `set_up_mcast`) + Stage 2 generator emit.
- Wk4: Stage 3 A/B + crossover sweep; write up.
- Buffer/realism: Stage 4 if pcm-sdk access lands; else cross-check deferred to future work.
