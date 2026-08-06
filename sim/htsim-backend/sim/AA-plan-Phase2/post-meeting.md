# Phase-Two Post-Meeting Adjustments

*Delta plan on top of the shipped phase-two implementation
(commits 2522e24 → b250901 on `WIP-multicast-htsim-direct`).
v4.md is the design of record; impl.md was the execution plan
through T1–T11. This document captures six follow-up items
raised in the supervisor meeting, scoped and ordered. Nothing
here changes the v4 design choices; everything is an addition
or a refinement. **No code yet — awaiting approval.***

---

## 0. Items at a Glance

| # | Item | Effort | Risk | Output |
|---|---|---|---|---|
| PT1 | Round-robin AGG/Core selection (replace group-id hashing) | ~30 min | low | code change in `build_mcast_tree` |
| PT2 | Runtime guard: error loudly when port radix > 128 | ~10 min | trivial | code change in `FatTreeSwitch::addPort` |
| PT3 | Plot/thesis label clarification: "16-host fat tree" not "16-node" | ~15 min | trivial | plot script + thesis text |
| PT4 | Multi-MTU sweep + plot (drop A1 single-packet assumption) | ~1.5 h | low | new sweep + figure + thesis subsection |
| PT5 | Group-layout sensitivity: local vs cross-pod for small \|G\| | ~3 h | low | hand-built `.cm`s + bar chart + TikZ topo figures + thesis subsection |
| PT6 | Link-crosses metric (alternative to completion time) | ~3–4 h | medium | simulator counter + sweep + figure + thesis subsection |

**Total estimate: 8–10 hours of focused work.** The order below
is suggested for staging commits cleanly; each task is
independently testable.

---

## PT1 — Round-Robin AGG / Core Selection

### What

Replace the current group-id-hashed convergence-point selection
with a true round-robin counter. The supervisor flagged this as
a clarity / load-balancing improvement: with sparse group
identifiers (e.g. user provides groups 0, 5, 17 in a `.cm`),
hashing leaves AGGs/Cores unevenly utilised. Round-robin gives
strict equal allocation.

### Where

`sim/htsim-backend/sim/datacenter/fat_tree_topology.cpp` —
`build_mcast_tree(uint32_t group_idx)` (around line 640) and
`set_up_mcast()` (around line 745).

Current logic:

```c
uint32_t podpos = group_idx % agg_switches_per_pod();        // line 640
uint32_t core_offset =
        (group_idx / agg_switches_per_pod()) % uplink_bundles; // line 678
uint32_t chosen_core = core_offset * agg_switches_per_pod()
                       + podpos;                              // line 680
```

### Change

1. `build_mcast_tree` gains a second parameter `uint32_t
   assignment_idx` (the round-robin counter for this group).
2. Inside the body, every `group_idx %` becomes
   `assignment_idx %`; every `group_idx /` becomes
   `assignment_idx /`.
3. `set_up_mcast` maintains a counter that increments each time
   it processes a group with `members.size() >= 2` (skipping
   singletons, which don't get trees), and passes it into
   `build_mcast_tree`.

```c
// In set_up_mcast(), after the egress-route-cache build:
uint32_t rr_counter = 0;
for (uint32_t g = 0; g < groups->size(); ++g) {
    const auto& members = (*groups)[g];
    if (members.size() < 2) continue;

    auto tree = build_mcast_tree(g, rr_counter);
    rr_counter++;
    /* ... install entries, create sinks, etc. unchanged ... */
}
```

### Tests

- **Functional**: existing T01 / T05 baseline-mode regression
  must remain byte-identical (round-robin only affects mcast
  setup; baseline mode does not consult `build_mcast_tree`).
- **Mcast smoke**: `t01_simple_bcast.cm` and
  `t05_full_group_bcast.cm` under `-bcast_mode mcast` produce
  identical `BCAST_COMPLETE` output to the current
  implementation **for these test cases**, because
  `t01`/`t05`'s only group has `group_idx == 0` and
  `assignment_idx == 0` — so the chosen AGG/Core is the same.
- **Cross-validation test**: a hand-built `.cm` with two groups
  declared in non-sequential order (e.g., `Grp 0 1 2 3` then
  `Grp 4 5 6 7`) should now allocate the second group to a
  different AGG even when `group_idx % agg_per_pod` would
  collide. Add as `connection_matrices/tests/round_robin_tree.cm`.
- **Unit test (optional)**: build_mcast_tree called twice with
  same group but different `assignment_idx` should produce
  different convergence points. Single test in
  `tests/main_round_robin_test.cpp`.

### Acceptance

- Build clean.
- Phase-1 baseline regression byte-identical.
- Round-robin allocation visible in the new test (different
  AGGs for two distinct groups even when group_id mod K
  collides).

---

## PT2 — Runtime Guard for Port Radix > 128

### What

Our INC FIB representation uses `std::bitset<128>` for the per-
switch tree-port mask. The fat-tree topology currently asserts
`< 96` on TOR ports and `< 64` on AGG/CORE ports, well within
the 128-bit budget. If anyone ever raises those bounds without
also widening the bitmap, multicast would silently misroute
(bits beyond 127 would be dropped or alias).

A second-line defence at `FatTreeSwitch::addPort` makes the
constraint explicit and visible at the layer that actually
relies on it.

### Where

`sim/htsim-backend/sim/datacenter/fat_tree_switch.cpp` —
`FatTreeSwitch::addPort` (the override added in T6).

### Change

```c
int FatTreeSwitch::addPort(BaseQueue* q) {
    int idx = Switch::addPort(q);
    if (idx >= 128) {
        std::cerr
            << "FatTreeSwitch port count (" << (idx + 1)
            << ") exceeds INCFibEntry bitmap width (128). "
               "Widen std::bitset<128> in inc_fib.h or reduce "
               "switch radix.\n";
        std::abort();
    }
    _port_idx_by_queue[q] = static_cast<uint8_t>(idx);
    return idx;
}
```

### Tests

- **Functional**: existing fat-tree topologies (K=4, K=8, K=16)
  all stay well below 128. No behavioural change expected.
- **Negative test (optional)**: a synthetic stub that adds 200
  ports to a `FatTreeSwitch` should abort with the diagnostic.
  Could be a small standalone unit test
  (`tests/main_radix_overflow_test.cpp`) that uses
  `subprocess.run` to verify the abort message.

### Acceptance

- Phase-1 + phase-2 sweeps unchanged (all current configurations
  stay below 128 ports).
- The abort message is the only visible new behaviour — and only
  for pathological configurations.

---

## PT3 — Label Clarification ("16-host" not "16-node")

### What

In htsim's terminology, `Nodes 16` in a `.cm` and
`-nodes 16` on the CLI refer to **host** count. Switches are
not counted in this number. Our plot legends say "16-node fat
tree", which is technically correct but ambiguous: a reader
could (reasonably) infer that "16-node" means "16 entities in
the topology including switches", which it does not.

The supervisor wants this disambiguated.

For a K=4 fat-tree:
- 16 hosts
- 8 ToR switches (4 pods × 2 ToR/pod)
- 8 AGG switches (4 pods × 2 AGG/pod)
- 4 CORE switches
- = 16 hosts + 20 switches = 36 entities, 16 of which are
  end-hosts.

### Where

1. `sim/htsim-backend/plotting/plot_bcast_baseline.py` — change
   the per-line label format from `f"{nodes}-node fat-tree …"`
   to `f"{nodes}-host fat-tree …"` (and ensure the K-value
   appears, e.g., `f"K={k}, {nodes}-host fat-tree …"`).
2. `thesis/Validation and Evaluation.tex` — chapter §5 prose
   currently refers to "16/128/1024-node fat-trees" in
   §sec:eval-bcast-baseline. Replace with "16/128/1024-host
   fat-trees" or "K=4/8/16 fat-trees (16/128/1024 hosts)".
3. `thesis/Design and Implementation.tex` §sec:phase2-mcast and
   §sec:bcast-baseline — same substitutions.

### Note on K

In a K-port fat-tree:
- pods = K
- TORs per pod = K/2
- hosts per pod = (K/2)² = K²/4
- total hosts = K × K²/4 = K³/4

So K=4 → 16 hosts, K=8 → 128 hosts, K=16 → 1024 hosts. The
existing labels coincide with K³/4 powers of two by chance.

### Tests

- Visual inspection of regenerated plots and the recompiled
  thesis chapter.

### Acceptance

- Plots regenerated with the new label.
- Thesis prose disambiguated (consistent terminology
  throughout).

---

## PT4 — Multi-MTU Sweep and Plot

### What

Phase-two evaluation is currently bounded by **(A1)** — single-
packet payloads. Drop that assumption for an additional sweep
that varies message size, validating the per-packet
serialisation model and showing the speedup factor across
realistic message sizes (e.g., 4 KB → 1 MB).

The phase-1 source (`UecBcastSrc::bcast_send_once`) already
loops `while (_highest_sent < _flow_size)` and emits packets
back-to-back, so does the phase-2 source
(`UecBcastSrcMcast::emit_once`). No code change needed in the
endpoints; only new `.cm` files.

### Expected behaviour

For a message of `M` MTUs:

- **Baseline**: total time =
  $T_{\text{fabric}} + (\lvert G \rvert{-}1) \cdot M \cdot t_{\text{ser}}$.
  Linear in both \|G\| and M.
- **Mcast**: total time =
  $T_{\text{fabric}} + M \cdot t_{\text{ser}}$.
  Linear in M, constant in \|G\|.

So mcast still beats baseline by a factor of \|G\| at any
message size. The plot should show two parallel slopes (mcast
below baseline) on a linear-y, log-x axis.

### Where

1. **Generator**: extend
   `sim/htsim-backend/sim/datacenter/connection_matrices/gen_bcast_sweep.py`
   (or a new sibling generator) to emit `.cm` files with varied
   `size` field. Suggested grid:
   - **Topology**: 16, 128, 1024 hosts.
   - **Group sizes**: 4, 16, 64, 256 (subset that's representative).
   - **Message sizes**: 4 KB, 16 KB, 64 KB, 256 KB, 1 MB, 4 MB.
   - **Reps**: 5 per cell.
   - = 3 × 4 × 6 × 5 = 360 `.cm` files; 720 runs (both modes).
2. **Manifest + runner**: extend
   `connection_matrices/run_bcast_sweep.py` to capture the
   message size in the CSV (it already runs both modes).
3. **Plotter**: new script (or `--mode-axis msg_size` argument
   to `plot_bcast_baseline.py`) that plots completion time vs
   message size, one line per (topology, mode, group-size)
   triple — or facet by topology to keep it readable.

### Tests

- Smoke: a single 1024-host, \|G\|=64, 1-MiB run in mcast mode
  should not crash and should produce a `BCAST_COMPLETE` line
  with `duration_ns` proportional to 1 MiB / link bandwidth +
  fabric latency.
- Sweep run-time budget: 720 runs at varying scales, large
  ones taking up to ~20 s. Estimate ~2–4 hours total wall
  clock. Should run in background.

### Acceptance

- New sweep CSV (`results_phase2_mtu.csv`) committed.
- New plot (`bcast_phase2_mtu.{pdf,png}`) committed.
- Thesis §5.3 gains a subsection §5.3.X "Sensitivity to Message
  Size" presenting the figure and discussing the slope match.

---

## PT5 — Group-Layout Sensitivity (Local vs Cross-Pod, Small \|G\|)

### What

For small group sizes (\|G\| = 3 is a clean choice), the
**spatial layout** of the group within the topology has a
large multiplicative effect on $T_{\text{fabric}}$, much
larger than \|G\| does for the multicast curve. Two extreme
cases:

- **Local**: all members on one ToR → tree depth 2 hops
  (host → ToR → host). $T_{\text{fabric}} \approx 800$ ns.
- **Cross-pod**: 3 members in 3 different pods → tree depth
  6 hops (host → ToR → AGG → CORE → AGG → ToR → host).
  $T_{\text{fabric}} \approx 2400$ ns + per-switch latency.

Both for baseline and for mcast, the local case is dramatically
faster, but the **gap between baseline and mcast is unchanged**
because both pay the same fabric-traversal cost. The data point
is: group placement matters; mcast does not eliminate fabric
latency, it eliminates root-NIC serialisation.

### Where

1. **Hand-built `.cm` files** in
   `connection_matrices/layout_tests/`:
   - `g3_local_n16.cm`: hosts 0, 1, 2 (all on TOR 0, pod 0).
   - `g3_crosspod_n16.cm`: hosts 0, 8, 12 (pod 0, pod 2, pod 3).
   - `g3_local_n128.cm`: hosts 0, 1, 2 (all on one TOR).
   - `g3_crosspod_n128.cm`: hosts 0, 64, 96 (different pods).
   - `g3_local_n1024.cm`: hosts 0, 1, 2.
   - `g3_crosspod_n1024.cm`: hosts 0, 256, 768.
   (Six `.cm`s; one local + one cross-pod per topology.)
2. **Sweep script**: small Python wrapper that runs each `.cm`
   in both modes, captures `duration_ns`, writes a tiny CSV.
3. **Plot**: small bar chart, four bars per topology (local-
   baseline, local-mcast, crosspod-baseline, crosspod-mcast).
   Or table form in the thesis prose.
4. **TikZ topology figures**: two per (topology, layout) — one
   schematic showing the K=4 fat-tree with the 3 group members
   highlighted in their pod positions for local; another for
   cross-pod. K=4 is the only one worth drawing in detail
   (the structure repeats at larger K). At K=8/K=16 we'd cite
   "same structure scaled up" rather than draw 1024 hosts.
5. **Thesis subsection** in §5.3, e.g., §5.3.X "Group-Layout
   Sensitivity":
   - Show one TikZ figure per layout (K=4, local + cross-pod).
   - Bar chart or table of the 4 measurements.
   - Brief discussion: cross-topology validation (numbers are
     consistent: 6-hop diameter is the same for K=4/8/16
     3-tier fat-trees).
   - Statistical-likelihood sentence: as \|G\| or N grows,
     a uniformly-random group is increasingly unlikely to
     fit in a single pod. Probability:
     $P(\text{single-pod}) =
     \binom{P}{|G|} / \binom{N}{|G|}$
     where P is the pod size. For K=4, N=16, P=4: P(local
     |G|=3) = $\binom{4}{3}/\binom{16}{3} = 4/560 \approx 0.7\%$.
     Drops fast for larger N.
6. **Cross-topology validation** (the "only do this if
   necessary" item): the existing main sweep already gives us
   the data — every cell's median already shows that
   $T_{\text{fabric}}$ is identical across K=4/8/16 for the
   same \|G\| because diameter is fixed. We can mention this
   inline rather than producing a dedicated plot. Recommend:
   skip the dedicated cross-topology layout figure and cite
   the existing main sweep for the "depth-invariance"
   property.

### Tests

- Run each of the six `.cm` files in both modes; verify
  `BCAST_COMPLETE` lines and check that mcast-cross-pod >>
  mcast-local (the gap should be roughly 4× the fabric
  difference for a K=4 fat-tree).

### Acceptance

- Six `.cm` files checked in.
- Bar-chart figure committed.
- One or two TikZ figures (local + cross-pod, K=4 only) added
  to thesis figures.
- Thesis subsection committed with the figure refs and the
  statistical-likelihood discussion.

---

## PT6 — Link-Crosses Metric

### What

Completion time is one metric. Another is **how much of the
fabric you used** to transmit the broadcast — i.e., total
packet-link traversals across all links. This is closely
related to throughput and contention: a baseline broadcast
with \|G\|−1 unicast legs over a 6-hop path consumes
$\approx 6 \cdot (\lvert G \rvert{-}1)$ link-crosses; the same
broadcast under mcast consumes $\le$ #(tree edges), which is
much smaller.

The supervisor wants this as a complementary plot.

### Expected behaviour

For a cross-pod \|G\|=16 group on K=4:
- **Baseline**: 15 legs × 6 hops = 90 link-crosses.
- **Mcast**: tree spans 4 ToRs + 4 AGGs + 1 Core + 16
  host-downlinks + 4 ToR↔AGG + 4 AGG↔Core links =
  ≈ 24 link-crosses.
- Ratio ≈ 3.7×.

Smaller than the completion-time speedup (because completion
time is bottlenecked by serial root emission, not by link
bandwidth) but a different and meaningful axis. It shows
mcast also reduces network-wide load.

### Where

1. **Simulator instrumentation**: add a per-packet counter at
   each pipe (or queue). Two paths:
   - **(a) Modify Pipe**: increment a static class-level
     counter on every `receivePacket`. Sum at end of
     simulation. Simple but global.
   - **(b) Per-link counter**: each pipe holds its own
     counter; query at end. More granular; lets us emit a
     per-link CSV for heatmap-style plots later.

   Recommend **(b)** — incremental cost is small and the
   output is more useful.

2. **CSV emission**: new CLI flag
   `-link_crosses_csv <path>` on `htsim_uec`. At simulation
   end (after the BarrierTrigger fires and the recorder
   logs), iterate every pipe in the topology, write
   `(switch_from, port_idx, switch_to, n_packets)` rows.

3. **Sweep script**: extend `run_bcast_sweep.py` to capture
   link-cross totals. Each run gets a single
   `total_link_crosses` field in the CSV (no per-link
   detail in the headline plot).

4. **Plot**: total link-crosses vs \|G\|, one line per
   (topology, mode). Should show baseline scaling
   approximately linearly with \|G\|·diameter and mcast
   scaling approximately linearly with \|G\| (one tree edge
   per added member, roughly).

5. **Thesis subsection**: §5.3.Y "Network Footprint" with
   the figure and a discussion of how mcast reduces total
   link load by O(diameter)× compared to baseline.

### Implementation sketch

```c
// pipe.h
class Pipe {
    /* ... existing members ... */
    uint64_t _packet_count = 0;
    uint64_t packet_count() const { return _packet_count; }
};

// pipe.cpp Pipe::receivePacket — add at top:
_packet_count++;

// fat_tree_topology.h: add accessor
void emit_link_crosses_csv(const std::string& path) const;

// fat_tree_topology.cpp: walk pipes_*_*, emit rows
```

### Tests

- Sanity: small `.cm` (say `t01_simple_bcast.cm`), known
  baseline link-crosses ≈ 18 (3 legs × 6 hops). Verify
  the CSV reports a number close to that.
- Mcast on the same `.cm`: should report fewer link-crosses
  than baseline.

### Acceptance

- Simulator emits the CSV.
- New plot in `plotting/`.
- New thesis subsection §5.3.Y.

---

## Suggested Execution Order

```
PT2 (radix guard)     ─┐  trivial; commit first
PT3 (labels)          ─┼─ both are documentation/safety,
                      ─┘  can ship in one commit
PT1 (round-robin)     ──  small code change, isolated
PT4 (multi-MTU sweep) ──  generator + sweep + plot, runs in
                          background while thesis is touched
PT5 (group layout)    ──  hand-crafted `.cm`s + TikZ + plot
PT6 (link-crosses)    ──  simulator instrumentation + sweep
                          + plot — heaviest item, last
```

PT2 + PT3 can be one commit (~30 min combined).
PT1 is a separate commit.
PT4, PT5, PT6 are each their own commit cluster (code +
sweep results + plot + thesis).

---

## Open Questions for the User Before Starting

1. **PT1 — "Round-robin roots"**: I interpreted this as
   round-robin assignment of the per-pod **AGG** and the
   multi-pod **CORE** convergence points (the points where
   the tree turns / pivots). Is that the right
   interpretation? Or did the supervisor mean something
   else by "roots" (e.g., source roots — but those are
   user-specified per `start_bcast`, not picked by the
   topology)?

2. **PT5 — cross-topology layout figure**: I'm planning to
   skip the per-K=8/K=16 layout figures because the
   existing main sweep already shows that cross-pod
   completion time is invariant across K (3-tier fat-tree
   diameter is fixed). The thesis prose can cite the
   main sweep instead of drawing redundant figures. OK?

3. **PT6 — link-cross granularity**: total link-crosses per
   simulation is the headline metric. Per-link
   distribution (a heatmap or a CCDF) is *available* given
   the per-pipe counter, but I'm not planning to produce a
   per-link figure unless asked. Confirm headline metric
   only?

4. **Priority order**: anything in PT1–PT6 you want
   bumped/dropped given limited time, or is the order
   above acceptable?

---

## What This Plan Explicitly Does NOT Touch

- The v4 design (no §3.x changes).
- Phase-1 baseline path (any change must remain byte-
  identical under `-bcast_mode baseline`).
- Phase-3 work (Reduce, Allreduce, Allgather, aggregation).
- The `UecMcastPacket` / `INCFib` / `UecCollectiveSrc`
  shapes (additive only).
- Thesis chapters 1–3 and §5.1–§5.2 (only §5.3 grows new
  subsections).
