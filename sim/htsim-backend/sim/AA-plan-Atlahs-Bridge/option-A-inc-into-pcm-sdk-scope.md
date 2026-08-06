# Option A — add INC to pcm-sdk's scale-up domains (scoped against the real code)

Date: 2026-06-26. Now that we have `ZhiyiHu1999/pcm-sdk` access, this scopes putting our
in-network-computing collectives into the two-tier sim, grounded in its source.

## Architecture (confirmed by reading the code)

pcm-sdk = a **base htsim** + a **patch layer**:
- **Base: `uet-htsim`** (the official Ultra Ethernet htsim, `github.com/ultraethernet/uet-htsim`,
  nested submodule) — and `HTSIM_spcl` (Zhiyi's variant). The two-tier `main_uec.cpp` includes
  `uet-htsim`'s `fat_tree_switch.h` / `fat_tree_topology.h`.
- **Patch: `HTSIM_spcl-patch/`** — the two-tier additions: `main_uec.cpp`,
  `logsim-interface.cpp` (the routing), `AtlahsHtsimApi`, `FatTreeTopologyWrapper`/`Cfg`.

**The two-tier scaffolding (the valuable, already-built part):**
- N+1 `AtlahsHtsimApi` instances in `htsim_apis[]` (`main_uec.cpp:806` builds them) — `[0]` =
  scale-out, `[1..N]` = one scale-up FatTreeTopology per node.
- Per-domain topologies in `topo[]`.
- **Routing by node-containment** (`logsim-interface.cpp:123`, `get_routing_domain_src_dst`):
  `node = ⌊rank / num_gpus_per_node⌋`; intra-node send → scale-up instance, cross-node →
  scale-out; index remapped to local-GPU inside a scale-up domain. **This is exactly our
  per-domain rule — confirmed, not inferred.**
- Two-tier CLI (`-topo`/`-intranode_topo`/`-num_gpus_per_node`) parsed in `main_uec.cpp`.

→ **Reuse this whole layer.** It is the part that would be painful to rebuild in our fork.

## INC-merge surface (the sizing)

`uet-htsim`'s `FatTreeSwitch` (`uet-htsim/htsim/sim/datacenter/fat_tree_switch.h:84`) has the
**same class + the same virtual hooks** our INC attaches to:
`virtual void receivePacket(Packet&)` (L101), `virtual Route* getNextHop(...)` (L102) —
identical to our fork. Base has **no INC** (verified: no `handle_mcast`/`handle_reduce`/`INCFib`).
Same UEC transport (`UecSrc`). So our INC is a **merge into the same htsim family**, not a rewrite.

Measured delta (our fork w/ INC vs the uet-htsim base):

| file | uet base | our fork | INC delta |
|---|---|---|---|
| `datacenter/fat_tree_switch.h`   | 155  | 276  | ~+120 (handle_mcast/reduce decls, _reduce_pipe, etc.) |
| `datacenter/fat_tree_switch.cpp` | 534  | 945  | ~+410 (handle_mcast/handle_reduce/fanout_replicas) |
| `datacenter/fat_tree_topology.cpp` | 1619 | 1630 | ~+11 (set_up_mcast hook; rest in our additions) |
| `inc_fib.h`, `uec_collectives.{h,cpp}` | absent | present | net-new add |
| `UecMcastPacket`/`UecReducePacket` (uecpacket.h) | absent | present | net-new add |

Caveat: ~100 lines of **base drift** between our fork's switch and uet-htsim's (beyond INC) to
reconcile — our fork is the same family but not bit-identical to this uet-htsim commit.

## Work (Option A)

1. **Port INC onto uet-htsim** (in pcm-sdk): apply our INC additions to its
   `FatTreeSwitch`/`FatTreeTopology`/UEC + add `inc_fib.h`, `uec_collectives.{h,cpp}`, the
   `UecMcast/ReducePacket` types; reconcile the ~100-line base drift.
2. **coll-op dispatch in the patch's `logsim-interface`**: route a first-class `coll` op to the
   scale-up instance (`htsim_apis[node+1]`) and trigger INC there. The send-level routing
   already exists (`get_routing_domain_src_dst`); extend it to coll ops + their group bring-up.
3. **Emit scale-up collectives as `coll`** in the generator (per-domain emit, T8) — the same
   change being built for our `htsim_uec`; reusable, gated by node-containment (= `context=="tp"`).

Plus: lossless/PFC on the scale-up instances, lossy on scale-out (the configured default).

## Estimate + verdict

**Medium, ~1 week of focused work** — consistent with the "within a week once I have access"
plan. The scaffolding (multi-instance + routing + flags) is reused; the same-family base makes
the INC merge a port, not a rewrite. Risk areas: (a) the ~100-line base drift (uet-htsim vs our
fork), (b) the `FatTreeTopologyWrapper` indirection, (c) coll-op routing in the patched
`logsim-interface`.

**Option A is now the cleaner path** (vs Option B = rebuild the scaffolding in our fork) — the
two-tier orchestration is non-trivial and already built, while INC is the localized, working
delta. This matches the original "get the code and add INC" instinct.

**Next concrete step:** a file-level diff of `uet-htsim` vs our fork's
`fat_tree_switch.{h,cpp}` / `fat_tree_topology.cpp` / `uec.*` / `uecpacket.h` to plan the merge
hunk-by-hunk and pin the base-drift reconciliation.
