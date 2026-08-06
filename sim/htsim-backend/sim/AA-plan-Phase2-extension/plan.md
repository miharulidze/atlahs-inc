# Phase-2 Extension Plan: Lossless Multicast via PFC

**Author:** Wanja Stäempfli
**Date:** 2026-05-11
**Branch target:** `WIP-multicast-htsim-direct` (post commit `cdfadf0`)
**Status:** Draft — awaiting approval before implementation
**Scope:** Additive `-bcast_mode mcast_lossless` mode for phase-2 multicast


## 0. TL;DR

Phase 2 currently assumes a lossless network implicitly: the ack-less broadcast
design (A2) requires every multicast replica to reach its sink, with no
retransmission. Today this holds vacuously — test workloads do not induce
buffer overflow, so no drops occur — but the assumption is not enforced by
the simulation.

This plan adds a third broadcast mode, `mcast_lossless`, that runs the
existing phase-2 multicast dispatch on a topology configured with
`LOSSLESS_INPUT` queues. The change is **additive**: `baseline` and `mcast`
modes remain byte-identical to their locked phase-2-v4 results
(`bcast_test1.cm`: mcast 3582 ns; baseline 4596 / 5272 ns).

Headline deliverables:

1. New `-bcast_mode mcast_lossless` CLI value, dispatched from `main_uec.cpp`.
2. Internal forcing of `queue_choice = LOSSLESS_INPUT` when the new mode is
   selected, regardless of `-queue_type` on the command line.
3. A new congestion-inducing test workload `bcast_congested.cm`.
4. Validation matrix: `mcast_lossless` matches `mcast` on uncongested cases;
   completes on congested cases where `mcast` drops or hangs.
5. Sweep + plot extensions: three-line per-topology overlay
   (`-` baseline, `--` mcast, `:` mcast_lossless).
6. Thesis §4.2 paragraph on PFC-based validation and a disclaimer on
   SHARP fidelity.

Effort estimate: **1 day** of focused work (revised down from 1–2 days
after diligence found the underlying plumbing more complete than expected).


## 1. Motivation

### 1.1 The implicit lossless assumption

The phase-2 mcast design depends on **(A2) ack-less broadcast**. The
`UecBcastSrcMcast` emits a stream of `UecMcastPacket`s; `UecMcastSink`
instances count `bytes_received` per registered op and fire a barrier when
`expected_bytes` is reached. There is no NACK, no retransmission, no
recovery mechanism. A dropped packet means a sink's `bytes_received`
never reaches `expected_bytes`, the barrier never fires, and the
simulation runs to event-list exhaustion without a `BCAST_COMPLETE`
line.

In the current test workloads — single bcast on `bcast_test1.cm`, isolated
sweep entries — the offered load never exceeds available link capacity
along the tree, so no buffer fills, so no drop occurs. The assumption
holds vacuously. But it is **not enforced**: the topology is configured
with `COMPOSITE` queues which would drop on overflow. Any future test
that introduces concurrent multicasts on shared links would expose this
gap.

### 1.2 What real INC fabrics do

Production in-network computing systems run on lossless fabrics:

- **InfiniBand (SHARP, NVIDIA Mellanox):** link-level credit-based flow
  control built into the IB transport. Each sender holds N credits per
  Virtual Lane; each packet consumes a credit; receivers return credits
  as buffer space frees. SHARP runs on a dedicated SL with reserved
  buffer credits, isolating it from unicast contention.
- **RoCEv2 / UEC (Ultra Ethernet):** PFC (Priority-based Flow Control,
  IEEE 802.1Qbb). Hysteresis thresholds on egress-queue ingress
  accounting trigger PAUSE frames upstream when buffers approach
  overflow. PFC has known pathologies (head-of-line blocking, deadlock,
  pause storms) that credit-based fabrics avoid by design.

The two mechanisms achieve the same end (zero packet loss under
normal operation) via different means and have different failure
modes. For a thesis targeting RoCEv2/UEC-style Ethernet INC, PFC is
the natural model. For a thesis claiming SHARP fidelity, IB
credit-based would be required — and htsim does not implement this.

### 1.3 What htsim's LOSSLESS_INPUT provides

The codebase has three lossless-queue classes:

- `LosslessQueue` — base class for PFC-aware queueing.
- `LosslessInputQueue` — pass-through accounting object; not a real
  FIFO buffer. Maintains ingress-side bookkeeping so the upstream
  egress can attribute traffic correctly.
- `LosslessOutputQueue` — actual buffer + PFC PAUSE generator.
  Extracts the ingress queue tag from each packet to know whom to
  PAUSE when its ingress-attributed accounting saturates.

The `queue_type` enum value `LOSSLESS_INPUT` is a known footgun: it
instantiates `LosslessOutputQueue`, not `LosslessInputQueue`. Both
classes are needed for PFC to function; the topology wires them up
correctly at construction time when `_qt == LOSSLESS_INPUT`. This
plan uses the enum value as-is.

### 1.4 What this extension does **not** do

To set expectations and bound scope:

- **Does not model SHARP.** SHARP runs on IB credit-based flow control,
  which is structurally different from PFC. PFC-based losslessness is
  consistent with RoCEv2/UEC, not IB. Thesis prose must be precise on
  this point.
- **Does not add retransmission.** The (A2) assumption is unchanged;
  the extension prevents drops, which is sufficient for ack-less mcast
  to complete.
- **Does not fix HOL blocking.** Multicast on PFC has intrinsic
  head-of-line-blocking pathologies (see §3.6). We document them and
  use them as a research observation, not as a problem to solve.
- **Does not change phase-2 baseline numbers.** All existing locked
  results remain byte-identical; the new mode is fully opt-in.


## 2. Why option B (additive) over option C (full refactor)

Three options were on the table. This subsection captures the rationale
for the choice.

### 2.1 Option A — Document only

Add a paragraph to §4.2 acknowledging the implicit lossless assumption
and noting that real fabrics enforce it via PFC or credits. No code
changes. **Rejected** because it leaves a footnote-level caveat in the
design chapter and reduces the thesis's rigor claim.

### 2.2 Option B — Additive parallel mode (chosen)

Add `mcast_lossless` as a third value of the `bcast_mode` enum,
preserving the existing two modes unchanged. New mode forces
`LOSSLESS_INPUT` queues at the topology level when selected.

**Pros:**

- Phase-2 locked results stand byte-identical.
- Validates the implicit assumption explicitly.
- Small, contained code change.
- Plumbing already exists (CLI parser supports `-queue_type lossless_input`;
  topology already wires PFC accounting correctly).
- Reopens phase 3's flexibility — reduce trees can use the same lossless
  underlay where contention is structural.

**Cons:**

- Doesn't claim SHARP fidelity (correctly).
- Cannot validate behavior change on uncongested workloads (no PAUSEs
  fire); requires a new congested workload to demonstrate value.
- Multicast + PFC has HOL blocking, which may produce unexpected
  slowdowns on adversarial workloads.

### 2.3 Option C — Full refactor to LOSSLESS_INPUT default

Replace `COMPOSITE` with `LOSSLESS_INPUT` as the phase-2 default.
**Rejected** because:

- Regression risk across all existing sweep CSVs and plots.
- Re-validation of §4.2 figures and prose.
- Marginal fidelity gain over option B (only changes the default).
- HOL blocking behavior would need full characterization before
  publication.


## 3. Design

### 3.1 The new mode value

Extend the `BcastMode` enum:

```cpp
enum BcastMode {
    BCAST_BASELINE,           // existing
    BCAST_MCAST,              // existing
    BCAST_MCAST_LOSSLESS,     // NEW
};
```

CLI parser accepts `-bcast_mode {baseline, mcast, mcast_lossless}`.

### 3.2 Dispatch path

`mcast_lossless` reuses the **exact** phase-2 mcast dispatch:

- Topology: `set_up_mcast()` builds `INCFibEntry`s and `_port_egress_routes`
  identically.
- Driver loop: creates `UecBcastSrcMcast` and `register_op` on existing
  sinks identically.
- Runtime: `handle_mcast()` does identical RPF + bitmap fanout.

The **only** difference is the queue class instantiated at every
inter-switch link and host downlink. `COMPOSITE` becomes
`LOSSLESS_INPUT`. This is achieved by forcing `queue_choice` at the
topology-construction site (§4.2).

### 3.3 No new packet fields

`UecMcastPacket` is unchanged. PFC accounting at LosslessOutputQueue
keys on the packet's `_ingress_queue` field (set by the upstream
queue's `completeService` method as the packet leaves). This is
standard htsim plumbing; multicast replicas inherit it via the
normal queue mechanics — see §3.5 for the analysis.

### 3.4 Source-side handling

`UecBcastSrcMcast` inherits from `UecSrc`. The source emits packets
into its NIC's egress queue (`queues_ns_nlp[src][tor][0]`). In
`LOSSLESS_INPUT` topology mode, this queue is a `LosslessOutputQueue`
paired with a `LosslessInputQueue` at the TOR side.

Backpressure propagation:

1. Inter-switch queue X saturates → PAUSE sent to its upstream's
   egress queue Y.
2. Y stops draining → its ingress-attributed buffer for Y's upstream
   fills → PAUSE sent further upstream.
3. Chain continues to the TOR's host-downlink queue (where the source
   NIC's traffic ingresses the network).
4. PAUSE arrives at source's NIC egress queue.
5. NIC queue stops draining.
6. Source's emission rate must respect the NIC queue's drain rate to
   avoid infinite queueing at the source.

**Open question on source pacing:** `UecBcastSrcMcast` inherits the
`UecSrc` emission loop. For ack-less mcast, cwnd is not replenished
by ACKs. The source either:

- (a) Emits at line rate (one packet per wire-time), in which case
  PAUSE on the NIC blocks emission via queue-acceptance check; or
- (b) Emits faster than line rate, in which case packets accumulate
  in the NIC queue under PAUSE, eventually triggering the
  LosslessOutputQueue's own PFC mechanism back to the source —
  which the source does not honor.

Phase-0 diligence (§5, task D5) determines which case applies.
Likely (a) — htsim source emission is rate-paced. If (b), a small
patch to the source's send loop to respect NIC-queue PAUSE state
is needed.

### 3.5 Replica ingress-tag propagation

When `handle_mcast()` spawns replicas via `UecMcastPacket::newpkt_replica`,
each replica is pushed into `_pipe->receivePacket(*r)` for switch
latency emulation. After the switch_delay elapses, `_pipe` calls
back into `S.receivePacket(*r)`, which dispatches to the egress
phase and calls `r->sendOn()` — advancing the replica to its
attached egress queue.

The egress queue is `LosslessOutputQueue` in `mcast_lossless` mode.
At enqueue time, it stamps its own identity onto the packet's
`_ingress_queue` field (so the next downstream switch can identify
"this packet came from queue Q"). At dequeue time, it accounts the
packet against the ingress queue stamped at packet creation time —
the **upstream egress** that fed this switch.

For multicast: all replicas at switch S have the same ingress
(the upstream egress queue that delivered the original packet).
This is **correct PFC semantics**: PAUSE backpressure should
propagate to whichever upstream caused the load, regardless of
fanout. The replica factory does **not** need to copy any ingress
tag — the standard queue mechanics handle it.

**Verification required (D2 in §5):** confirm that `_ingress_queue`
is set by the upstream queue's send path, not by the packet
factory. If by the factory, `newpkt_replica` needs to propagate it.

### 3.6 Multicast + PFC head-of-line blocking

A single packet fanning out to N egress queues at switch S, all
sharing one ingress, has a known PFC pathology:

> If any one of the N egress queues saturates and sends PAUSE back
> through S's accounting, the upstream is paused — throttling all
> traffic from that ingress, including the other (N-1) branches
> that may be progressing fine.

This is HOL blocking, intrinsic to PFC. Real fabrics mitigate by
isolating multicast onto a dedicated priority/VL, which has
independent PFC accounting. htsim's PFC implementation may or
may not support multi-priority; phase-0 diligence (D3) clarifies.

For this extension, we use **a single priority** and accept HOL
blocking. The thesis chapter §4.2 documents the behavior as a
research observation: PFC-based lossless multicast has these
trade-offs, motivating real deployments to use VL isolation.

### 3.7 Queue size and PFC headroom

PFC requires sufficient buffer **headroom** to absorb packets
in-flight between PAUSE issuance and the upstream actually
stopping. Conservatively: headroom ≥ 2 × MTU × hop-delay × link-rate.

For 100 Gbps, 4160-byte packets, 400 ns wire delay:
headroom ≈ 2 × 4160 × 400 ns × 100 Gbps / 8 ≈ ~10 KB per ingress port.

htsim's default queue sizes are typically BDP-sized
(~100 KB at 100 Gbps × 1 µs RTT), which is an order of magnitude
larger than the required headroom. **Default sizing is adequate.**

If validation reveals corner cases where headroom is insufficient,
sizes can be tuned per-link. Out of scope for the initial pass.


## 4. Implementation phases

Five short phases. Each is independently committable and validates
incrementally.

### 4.1 Phase 0 — Diligence (read-only investigation)

**No code changes.** Six tasks, ~2 hours total. Goal: confirm the
assumptions in §3 hold against the current codebase before any
edits land. Each task has a question, file to read, and pass/fail
criterion.

| ID | Question | File / Symbol | Pass criterion |
|----|----------|---------------|----------------|
| D1 | Does `-queue_type lossless_input` route to `queue_choice = LOSSLESS_INPUT`? | `main_uec.cpp:555–565` | Yes (already confirmed) |
| D2 | What sets `pkt._ingress_queue`? Is it factory or queue's send path? | `queue.cpp`, `queue_lossless_output.cpp`, `uecpacket.h` | Queue's send/dequeue path stamps it. |
| D3 | Does htsim's PFC implementation support multiple priorities, or a single accounting plane? | `queue_lossless.cpp`, `queue_lossless_output.cpp` | Note current support level; single OK for v1 |
| D4 | Are `LosslessInputQueue` objects automatically paired with every queue when `_qt == LOSSLESS_INPUT`? | `fat_tree_topology.cpp:1047–1117` | Yes (already confirmed) |
| D5 | Is `UecBcastSrcMcast` emission rate-paced by NIC drain, or by an independent timer? | `uec.cpp`, `uec_bcast.h`, `uec_bcast.cpp` | Pacing respects queue acceptance. |
| D6 | Does `ETH_PAUSE` handling in `FatTreeSwitch::receivePacket` fire before the `UEC_MCAST` branch? | `fat_tree_switch.cpp:247–261` | Yes (already confirmed: ETH_PAUSE at 247, UEC_MCAST at 258) |

**Blockers identified during diligence trigger Phase 1 scope changes
before any code lands.**

### 4.2 Phase 1 — Enum + CLI + topology conditional

**Files touched:** `main_uec.cpp` only.

**Code change A:** Extend `BcastMode` enum (or whatever the existing
declaration is — check name).

```cpp
enum BcastMode {
    BCAST_BASELINE      = 0,
    BCAST_MCAST         = 1,
    BCAST_MCAST_LOSSLESS = 2,   // NEW
};
```

**Code change B:** Extend `-bcast_mode` parser.

```cpp
} else if (!strcmp(argv[i], "-bcast_mode")) {
    const char* m = argv[++i];
    if      (!strcmp(m, "baseline"))       bcast_mode = BCAST_BASELINE;
    else if (!strcmp(m, "mcast"))          bcast_mode = BCAST_MCAST;
    else if (!strcmp(m, "mcast_lossless")) bcast_mode = BCAST_MCAST_LOSSLESS;  // NEW
    else { cerr << "unknown bcast_mode " << m << endl; exit(1); }
}
```

**Code change C:** Force `queue_choice = LOSSLESS_INPUT` before the
topology constructor when `mcast_lossless` is set.

```cpp
// After arg-parse, before topology construction:
if (bcast_mode == BCAST_MCAST_LOSSLESS) {
    if (queue_choice != LOSSLESS_INPUT && queue_choice != COMPOSITE) {
        cerr << "-bcast_mode mcast_lossless requires -queue_type "
             << "lossless_input or composite (default); overriding."
             << endl;
    }
    queue_choice = LOSSLESS_INPUT;
    UecSrc::set_queue_type("lossless_input");
}
```

**Code change D:** Driver dispatch in the per-connection loop. The
`mcast_lossless` branch is **identical** to the `mcast` branch
(same `UecBcastSrcMcast`, same `register_op`, same routes). To
avoid duplication, the condition becomes:

```cpp
if (bcast_mode == BCAST_MCAST ||
    bcast_mode == BCAST_MCAST_LOSSLESS) {
    /* existing mcast branch — no changes inside */
}
```

**Validation:** rebuild, run `bcast_test1.cm` with each of the
three modes. Confirm:

- `baseline`: 4596 / 5272 ns (locked).
- `mcast`: 3582 ns (locked).
- `mcast_lossless`: 3582 ns (matches mcast, no congestion).

If `mcast_lossless` differs from `mcast` on this workload, something
is wrong (PFC accounting overhead leaking into the no-PAUSE path,
or queue construction not actually different).

### 4.3 Phase 2 — Replica ingress-tag verification (conditional)

**Depends on D2 outcome.**

- **Case A (queue stamps `_ingress_queue` at send-path):** no
  code change needed. The replica naturally picks up the correct
  tag at its egress queue's send. Skip to phase 3.
- **Case B (factory stamps `_ingress_queue`):** edit
  `UecMcastPacket::newpkt_replica` to clear the inherited tag so
  the egress queue can stamp afresh:

```cpp
inline static UecMcastPacket *newpkt_replica(UecMcastPacket &source,
                                             const Route &branch_route,
                                             uint8_t egress_port_idx) {
    UecMcastPacket *p = _packetdb.allocPacket();
    p->set_route(source.flow(), branch_route, source.size(), source.id());
    // ... existing fields ...
    p->_ingress_queue = nullptr;   // NEW: let the egress queue
                                   // stamp at its own send-path.
    return p;
}
```

**Validation:** rerun `bcast_test1.cm` with `mcast_lossless`; confirm
unchanged 3582 ns. If a regression appears, investigate the tagging
path more carefully.

### 4.4 Phase 3 — Congested test workload

**File created:** `sim/datacenter/connection_matrices/bcast_congested.cm`.

**Design intent:** four concurrent multicasts on a small fat tree
whose trees share root-TOR uplinks, creating ~4× oversubscription on
the shared link.

**Topology:** 16-host K=4 fat-tree (matches `bcast_test1.cm`).

**Group structure:**

- Group 0: hosts {0, 4, 8, 12}    (one per pod, all leaf TORs)
- Group 1: hosts {1, 5, 9, 13}
- Group 2: hosts {2, 6, 10, 14}
- Group 3: hosts {3, 7, 11, 15}

**Sources:** {0, 1, 2, 3} all sit on TOR_0 (pod 0). Each is the
broadcast root of its respective group. All four sources start at
t=0 with `size=65536` bytes (16 packets each).

**Concurrent emission produces:**

- TOR_0 receives 4 simultaneous multicast packets.
- Each fans out: 1 local-pod replica (to one of {4,5,6,7}-pod members)
  + 1 cross-pod replica via AGG.
- Wait — this isn't right. Hosts {0,1,2,3} are on TOR_0, hosts {4,5,6,7}
  are on TOR_1 (pod 1 different pod from pod 0's hosts). Need to
  re-check the K=4 layout. With K=4: 2 hosts per TOR, 2 TORs per pod,
  4 pods, 16 hosts total. So host_id `/` 2 gives TOR, host_id `/` 4
  gives pod. Hosts {0,1} on TOR_0/pod_0; {2,3} on TOR_1/pod_0;
  {4,5} on TOR_2/pod_1; etc.
- Re-design: put all four sources on hosts {0, 2, 4, 6} so each is
  on a distinct TOR but the groups' trees converge on shared AGG/CORE
  uplinks.

**Revised .cm content:**

```
Nodes 16
Connections 4

# Group 0: leaf hosts spread across pods, root on TOR_0
0 bcast 0 size 65536 start 0
2 bcast 1 size 65536 start 0
4 bcast 2 size 65536 start 0
6 bcast 3 size 65536 start 0

Groups 4
0 -> 0 1 8 9          # group 0: 0 (root) + 1 same-TOR + 8,9 in pod 2
1 -> 2 3 10 11        # group 1: 2 (root) + 3 same-TOR + 10,11 in pod 2
2 -> 4 5 12 13        # group 2: 4 (root) + 5 same-TOR + 12,13 in pod 3
3 -> 6 7 14 15        # group 3: 6 (root) + 7 same-TOR + 14,15 in pod 3
```

The actual `.cm` syntax may differ; the file should be authored to
produce four simultaneous broadcasts whose trees share at least one
upstream link.

**Expected behavior per mode:**

| Mode | Outcome |
|------|---------|
| `baseline` | Completes. Each group's |G|-1 unicasts are independent and rate-limited by source NIC; queue buildup at shared AGG uplink causes some queueing delay but no drops in COMPOSITE (assuming default BDP sizes are large enough). |
| `mcast` | Drops likely. 4 concurrent multicasts share root-pod AGG uplinks; offered rate 4× link speed → queue fills → drops → at least one sink's `bytes_received < expected_bytes` → `BCAST_COMPLETE` never fires for that group. |
| `mcast_lossless` | Completes, slower than uncongested. PAUSE backpressure throttles sources serially. Completion time roughly 4× the uncongested time. |

**If `mcast` actually completes** (because default queue sizes
absorb the burst): increase `size` parameter until the
oversubscription window exceeds buffer capacity. The point of the
test is to demonstrate the difference; tune the workload until it
shows.

### 4.5 Phase 4 — Validation matrix

Run all three modes on:

1. `bcast_test1.cm` (uncongested) — expect identical results
   for `mcast` and `mcast_lossless`; `baseline` unchanged.
2. `bcast_congested.cm` (congested) — expect `mcast` to fail
   (no BCAST_COMPLETE within timeout); `mcast_lossless` to
   complete (slower); `baseline` to complete (also slower,
   different mechanism).
3. Subset of `mtu_sweep/` (10 random matrices across nodes ∈
   {16, 128, 1024} × group_size ∈ {4, 16, 64}): all three
   modes complete; `mcast_lossless` within 10% of `mcast`.

Each run captured in CSV with the `mode` column from T11's
sweep extensions. Comparison plot generated.

### 4.6 Phase 5 — Sweep + plot extensions

`run_bcast_sweep.py`:

```python
p.add_argument(
    "--mode",
    choices=["baseline", "mcast", "mcast_lossless", "all"],
    default="all",   # was "both"
)

modes = (["baseline", "mcast", "mcast_lossless"] if args.mode == "all"
         else [args.mode])
```

`plot_bcast_baseline.py`:

```python
mode_style = {
    "baseline":       "-",
    "mcast":          "--",
    "mcast_lossless": ":",
}
```

Three lines per topology size in the overlay plot.
Backward-compatible: existing phase-1 CSVs without a `mode` column
still render under the default `"baseline"`.

### 4.7 Phase 6 — Thesis prose

**§4.2 additions:**

1. **One paragraph** (placed at the end of the existing phase-2
   description) introducing `mcast_lossless`:

   > Phase 2's ack-less broadcast assumes a lossless fabric: a
   > dropped packet would prevent a sink from completing, as no
   > retransmission mechanism exists. To validate this assumption
   > explicitly, we introduce a third mode, `mcast_lossless`, which
   > runs the identical phase-2 dispatch on a topology configured
   > with `LOSSLESS_INPUT` queues. These queues implement Priority
   > Flow Control (PFC, IEEE 802.1Qbb), generating PAUSE frames
   > when ingress-attributed buffers approach overflow. This models
   > the canonical Ethernet lossless mechanism used in RoCEv2 and
   > UEC deployments. We note that InfiniBand-based fabrics
   > (e.g., for SHARP) use a structurally different credit-based
   > scheme; modeling that is left for future work.

2. **Figure**: extend the existing per-topology overlay plot to
   include the `mcast_lossless` line (third linestyle). Caption
   notes that the new line overlaps `mcast` on uncongested
   workloads, demonstrating that lossless-mode overhead is
   negligible in the absence of congestion.

3. **Figure**: a new plot from `bcast_congested.cm` showing
   completion time vs. simulation timeout for the three modes,
   demonstrating that `mcast` fails to complete while
   `mcast_lossless` does.

4. **One sentence** acknowledging the HOL-blocking observation:

   > Under sustained congestion, multicast on a single PFC priority
   > exhibits classical head-of-line blocking: a slow consumer on
   > one branch of the tree throttles the entire ingress at the
   > root TOR, slowing other branches that would otherwise progress.
   > Production deployments mitigate this via per-priority isolation;
   > we use a single priority and document the effect.


## 5. Risks and mitigations

| ID | Risk | Likelihood | Severity | Mitigation |
|----|------|------------|----------|------------|
| R1 | Phase-2 `mcast` or `baseline` regression | low | high | Byte-identical regression check on `bcast_test1.cm` after each phase commit. |
| R2 | Source backpressure inadequate (D5 finds case B) | medium | medium | Patch source emission to honor NIC-queue acceptance; ~1 hour of work. |
| R3 | `_ingress_queue` stamping breaks for multicast replicas (D2 finds factory-stamped) | low | medium | Phase 2 patches `newpkt_replica`; ~30 minutes of work. |
| R4 | `bcast_congested.cm` doesn't actually congest under default queue sizes | high | low | Iterate workload sizing until oversubscription window exceeds buffer capacity. |
| R5 | HOL blocking causes `mcast_lossless` to stall on congested workload (livelock) | medium | medium | Tune workload to be congested but not pathological; add timeout to sweep. |
| R6 | `LOSSLESS_INPUT` enum footgun creates wrong queue class | very low | high | Phase-1 includes runtime assertion on queue type at construction; loud abort. |
| R7 | Buffer sizing inadequate for PFC headroom | low | low | Default BDP-sized buffers exceed required headroom by 10×; no action expected. |


## 6. Open questions

Documented for transparency; not blockers for v1 of the extension.

1. **Multi-priority support.** Does `LosslessOutputQueue` track
   independent accounting per priority? If yes, future work could
   put multicast on a dedicated priority to eliminate HOL blocking,
   matching production deployments more closely.

2. **VL isolation emulation.** SHARP's fidelity story would require
   modeling per-VL credit accounting. Out of scope for option B;
   open as a phase-3 or future-thesis topic.

3. **Coordination with phase 3 (reduce).** When reduce arrives, the
   convergence-point AGG/CORE switches do **input merging** — they
   wait for replicas from multiple children before forwarding upward.
   This creates structural contention that lossless mode addresses
   naturally; the phase-3 plan should explicitly choose
   `mcast_lossless`-equivalent (`reduce_lossless`?) as the default
   for reduce-direction ops.

4. **Drop-detection in `mcast` mode.** Currently `mcast` silently
   hangs on drops. A phase-3-or-later enhancement could add NACK-based
   retransmission, which would obsolete the lossless mode's safety
   role but not its fidelity role.


## 7. Effort and schedule

| Phase | Estimated hours | Cumulative |
|-------|-----------------|------------|
| 0. Diligence | 2 | 2 |
| 1. Enum + CLI + topology conditional | 2 | 4 |
| 2. Replica ingress-tag verification | 1 (conditional) | 5 |
| 3. Congested test workload | 2 | 7 |
| 4. Validation matrix | 1.5 | 8.5 |
| 5. Sweep + plot extensions | 1.5 | 10 |
| 6. Thesis prose | 1 | 11 |
| **Total** | **~11 hours** | **~1.5 days** |

Buffer for risk realization (especially R2 source backpressure
patching and R4 workload tuning): +3 hours.

**Realistic delivery: 1.5–2 days of focused work.**


## 8. Validation summary (for thesis defense)

A reviewer asking "did you validate phase 2's lossless assumption?"
should be answerable with one paragraph and one figure:

> Phase 2 assumes a lossless fabric implicitly via the ack-less
> design. To validate this, we added a third broadcast mode,
> `mcast_lossless`, identical to `mcast` except that the topology
> uses `LOSSLESS_INPUT` queues implementing PFC. On uncongested
> workloads (Figure X), `mcast_lossless` matches `mcast`
> completion times within measurement noise, demonstrating that
> lossless-mode overhead is negligible when no buffer overflow
> occurs. On a deliberately congestion-inducing workload
> (Figure Y), `mcast_lossless` completes successfully while
> `mcast` fails to complete due to silent packet loss. This
> confirms that the implicit lossless assumption is what enables
> the ack-less design and that real INC fabrics with PFC do
> satisfy this assumption under normal operation.


## 9. References

- Phase-2 v4 plan: `sim/AA-plan-Phase2/v4.md` (the design this extends).
- Phase-2 impl plan: `sim/AA-plan-Phase2/impl.md` (T1–T12 task breakdown).
- Phase-2 post-meeting follow-ups: `sim/AA-plan-Phase2/post-meeting.md`.
- htsim queueing classes:
  `sim/queue_lossless.cpp`, `sim/queue_lossless_input.cpp`, `sim/queue_lossless_output.cpp`.
- IEEE 802.1Qbb — Priority-based Flow Control (PFC) specification.
- NVIDIA SHARP papers: Graham et al. 2016 (HotI), Bloch et al. 2019 (DCS-IT).
- claude-mem observations: 170–177 (LosslessQueue family), 181–184 (Fraschetti PR analysis), 409 (linkspeed/BDP guard).


## 10. Approval

- [ ] Author (Wanja) reviewed and agrees with scope.
- [ ] Supervisor reviewed (if applicable for mid-thesis additions).
- [ ] Implementation begins on a clean working tree from `cdfadf0`.
- [ ] Diligence phase complete and case A/B noted for replica ingress-tag.
- [ ] Phase-2 regression baseline saved (`bcast_test1.cm` outputs for all
      three modes) before first behavior-changing commit.
