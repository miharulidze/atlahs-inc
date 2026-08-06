# Plan — Lossless Network Support for Broadcast / Multicast

**Status:** IMPLEMENTED + validated (Task 1). See §10 for results.
**Branch target:** `WIP-multicast-htsim-direct`
**Scope of this document:** Task 1 only (lossless network). Aggregation (Task 2 /
Phase 3) is referenced for forward-compatibility but is *not* designed here.

---

## 1. Objective

Until now every broadcast/multicast experiment has run under the phase-one
modelling assumption *"the network is lossless / switches have infinite
buffers"* (`uec_bcast.h:9-17`, `AA-plan-Baseline.md` assumption 3). That
assumption has never actually been *exercised*: the driver always builds
`COMPOSITE` queues with a ~100 MB default size so congestion can't occur.

This task replaces the *assumption* with a *mechanism*: run the experiments on
a genuinely lossless fabric using Priority Flow Control (PFC) with finite
buffers, and confirm the collectives still complete with **zero packet loss**.

Per the agreed answers to the scoping questions:

- **Goal:** *both* — (a) validate feasibility (zero-drop completion under finite
  buffers + PFC) and (b) characterise PFC dynamics (PAUSE propagation, HOL
  blocking, completion-time impact) for bcast vs mcast.
- **Network scope:** *both* the Phase-1 unicast baseline (`|G|-1` legs) and the
  Phase-2 multicast path run on the lossless fabric, so the comparison is
  apples-to-apples under one network model.
- **Multicast PFC accounting:** *refcount release-once* (decision recorded in
  §4–§5, alternatives in §6).

---

## 2. How the existing lossless model works (htsim PFC)

PFC in htsim is hop-by-hop backpressure built from a *pair* of queue objects
per directional link:

- **`LosslessOutputQueue`** (`queue_lossless_output.{h,cpp}`) — the real link
  queue. Holds packets, drains at link rate, and honours PAUSE via
  `_state_send` (`PAUSED` / `READY` / `PAUSE_RECEIVED`).
- **`LosslessInputQueue`** (`queue_lossless_input.{h,cpp}`) — a *virtual* queue
  sitting at the **downstream switch's ingress port**. It is a `VirtualQueue`
  (`network.h:74`). It tracks *bytes that entered this switch at this port but
  have not yet drained out of the switch's egress*.

Per-switch wiring already exists in `FatTreeTopology::init_network`
(`fat_tree_topology.cpp:1052-1199`): for `LOSSLESS_INPUT` / `LOSSLESS_INPUT_ECN`
every link gets a `LosslessOutputQueue` and every switch ingress gets a
`LosslessInputQueue` whose `peer` is the upstream output queue and whose
`_switch` is the local switch. NDP uses this today.

### The accounting contract (1-in / 1-out)

Trace one packet through switch `S`:

1. Arrives at `S`'s ingress `LosslessInputQueue` (`IQ_in`).
   `IQ_in._queuesize += size`. It records `pkt.set_ingress_queue(IQ_in)` and
   calls `S->receivePacket(pkt)` (`queue_lossless_input.cpp:66-92`).
2. `S` routes it; eventually `pkt.sendOn()` puts it on its egress route, whose
   first hop is `S`'s egress `LosslessOutputQueue` (`OQ_out`).
3. `OQ_out.receivePacket` reads `prev = pkt.get_ingress_queue()` (= `IQ_in`),
   pushes `prev` into its `_vq` list, and enqueues
   (`queue_lossless_output.cpp:31-101`).
4. When `OQ_out` finishes transmitting the packet, `completeService` pops the
   paired `prev` and calls `prev->completedService(pkt)`
   (`queue_lossless_output.cpp:151`), which does `IQ_in._queuesize -= size`
   (`queue_lossless_input.cpp:95-104`).

`IQ_in` sends a **PAUSE** to its upstream peer when `_queuesize` crosses
`_high_threshold`, and **RESUME** when it falls below `_low_threshold`
(`queue_lossless_input.cpp:70-73, 100-103`). This is the entire losslessness
guarantee: an output queue is paused *before* it can overflow.

The contract is strictly **one charge on ingress, one release on egress**. It
assumes a packet enters one port and leaves one port.

---

## 3. What is broken today

### 3.1 The driver cannot turn lossless on

- `main_uec.cpp:781` hardcodes `queue_type qt = COMPOSITE;` and passes *that* to
  the topology (`:786, :789, :1153, :1156`). The `queue_choice = LOSSLESS_INPUT`
  set by `-queue_type lossless_input` (`:564`) is parsed and then **ignored**.
- `LosslessInputQueue::_high_threshold` / `_low_threshold` are static and
  default to `0` (`queue_lossless_input.cpp:8-9`). Even if `qt` were wired, the
  `LosslessInputQueue` constructor's `assert(_high_threshold>0)`
  (`queue_lossless_input.cpp:45`) would fire at `init_network` time. The
  `-pfc_low` / `-pfc_high` flags are parsed (`main_uec.cpp:349-353`) but never
  used. (`main_ndp.cpp:390-391` shows the threshold-setup pattern we'll mirror.)

So no bcast/mcast run has ever used PFC. **Phase-1 unicast** will work as soon
as these two gaps are fixed (it is strictly 1-in/1-out). **Phase-2 multicast**
will not — see below.

### 3.2 Multicast breaks the 1-in / 1-out contract

`FatTreeSwitch::handle_mcast` (`fat_tree_switch.cpp:155-203`) turns one ingress
packet into `k` fresh replicas via `UecMcastPacket::newpkt_replica`
(`uecpacket.h:270`) and **frees the original** (`:196`). Under PFC this fails
two ways:

1. **Ingress byte leak.** The original charged `IQ_in += size` on arrival, but
   it is freed before reaching any `OQ_out`, so `IQ_in.completedService` is
   never called for it. `IQ_in._queuesize` never returns to 0 → the ingress
   port latches **PAUSED forever**.
2. **Crash on replica egress.** `newpkt_replica` does not copy `_ingressqueue`
   (it is a fresh allocation, default `NULL`). When a replica reaches
   `OQ_out.receivePacket`, `pkt.get_ingress_queue()` asserts non-null
   (`network.h:178`) / `assert(prev != NULL)` (`queue_lossless_output.cpp:77`)
   → abort.

This is the crux of the task: **PFC backpressure accounting must be defined for
1-in / k-out fan-out.**

### 3.3 Invariant that any fix must preserve

The ingress charge **must persist while any replica is still buffered**. If a
single egress branch is congested (its downstream paused it), replicas pile up
in that egress queue; the only lever that stops *more* multicast packets from
arriving and overflowing it is `IQ_in` staying occupied so it PAUSEs the
upstream source. Any accounting scheme that releases the ingress charge before
the fan-out has fully drained can overflow an egress queue → not lossless.

---

## 4. Chosen design — refcount release-once

Model the switch as a store-and-forward shared buffer: a multicast packet is
stored **once** and read out `k` times. Its ingress-port occupancy is therefore
**one** buffered copy, **held until the last replica has been transmitted**,
then released once.

Mechanism:

- Generalise `Packet::_ingressqueue` from `LosslessInputQueue*` to
  `VirtualQueue*` (both real input queues and the new credit object are
  `VirtualQueue`s; the egress queue already `dynamic_cast`s to `VirtualQueue*`,
  so this is type-system-only churn).
- Introduce `McastFanoutCredit : public VirtualQueue`, a small heap object
  holding `{ LosslessInputQueue* iq; mem_b size; int pending; }`.
- In `handle_mcast`, when the ingress packet carries an input queue (lossless
  mode): create one credit with `pending = k`, point **every replica's**
  `ingress_queue` at the credit, then free the original (its `+size` charge
  stays on `IQ_in`, to be released by the credit).
- Each replica drains from its egress `OQ_out` exactly as a normal packet does;
  `OQ_out.completeService` calls `credit->completedService(pkt)`. The credit
  decrements `pending`; on reaching 0 it releases the original's `size` from
  `IQ_in` **once** and self-deletes.

Net effect: `IQ_in` is charged `size` once (original arrival) and released
`size` once (last replica drained). Occupancy = exactly one buffered copy,
held across the whole fan-out — faithful, and §3.3-correct because the charge
persists until every branch has drained.

Why this over the cheaper "fan-out charge" alternative: see §6. Short version —
they are observationally identical at single-MTU (current Phase-2), but
refcount-once does not over-count ingress occupancy under load (matters for the
PFC-dynamics goal) and is the model Phase-3 aggregation extends rather than
replaces (fan-in "hold until all children arrive" is the same
release-when-truly-done shape).

---

## 5. Implementation plan

Ordered so the tree is buildable and testable after each step.

### Step 1 — Make the driver able to select lossless (`main_uec.cpp`)
- Replace `queue_type qt = COMPOSITE;` (`:781`) with `qt = queue_choice;` so
  `-queue_type lossless_input` reaches the topology.
- After arg parsing, set the static thresholds (mirror `main_ndp.cpp:390-391`):
  ```
  LosslessInputQueue::_high_threshold = Packet::data_packet_size() * pfc_high;
  LosslessInputQueue::_low_threshold  = Packet::data_packet_size() * pfc_low;
  ```
  with sensible non-zero defaults when the flags are unset (proposal: high = 100
  pkts, low = 80 pkts; both ≪ the default 100 MB buffer, expressed in packets to
  match NDP's convention). Expose `-pfc_high` / `-pfc_low` (already parsed) and
  document the buffer (`-q`) vs threshold relationship.
- *Result:* Phase-1 unicast baseline runs losslessly. Testable immediately.

### Step 2 — Generalise the ingress-queue pointer (`network.h`)
- Change `_ingressqueue` (`:225`) and the `set_/get_/clear_ingress_queue`
  accessors (`:177-179`) from `LosslessInputQueue*` to `VirtualQueue*`.
- Add a non-asserting `peek_ingress_queue()` returning the raw pointer (may be
  `NULL`), needed by `handle_mcast` because COMPOSITE-mode packets carry no
  ingress queue.
- Simplify `LosslessOutputQueue::receivePacket(Packet&)`
  (`queue_lossless_output.cpp:31-40`): `prev` is now already a `VirtualQueue*`,
  drop the `dynamic_cast`.

### Step 3 — Refcount credit object (`queue_lossless_input.{h,cpp}`)
- Add `LosslessInputQueue::release_bytes(mem_b)`: the `_queuesize -= bytes` +
  RESUME-below-low-threshold logic, factored out of `completedService` (which
  then calls `release_bytes(pkt.size())`).
- Add `class McastFanoutCredit : public VirtualQueue` with
  `completedService(Packet&)` that does `if (--_pending == 0) {
  _iq->release_bytes(_size); delete this; }`.

### Step 4 — Fan-out accounting in the switch (`fat_tree_switch.cpp` / `uecpacket.h`)
- In `handle_mcast` ingress branch: compute `k = egress_mask.count()`. Read
  `VirtualQueue* iq = pkt.peek_ingress_queue()`.
  - If `iq` (lossless mode):
    - `k == 0` edge: release the charge immediately
      (`static_cast<LosslessInputQueue*>(iq)->release_bytes(pkt.size())`) — no
      replicas to carry it. (Degenerate; guarded for safety.)
    - else: `auto* credit = new McastFanoutCredit(iq, pkt.size(), k);` and for
      each replica `r->set_ingress_queue(credit)`.
  - If `iq == NULL` (COMPOSITE/non-lossless): unchanged behaviour.
- `newpkt_replica` (`uecpacket.h:270`): explicitly reset `p->_ingressqueue =
  NULL` so the `set_ingress_queue` assert (`assert(!_ingressqueue)`) holds for
  recycled packets from `PacketDB`.

### Step 5 — Source-side note (no code unless needed)
Host NIC send queues are `FairPriorityQueue` (`alloc_src_queue`,
`fat_tree_topology.cpp:885-896`) and do **not** honour PAUSE. This is acceptable
for Task 1 because every collective source emits a **single MTU** per leg/op
(`emit_once`, `UecBcastSrc::bcast_send_once`) — there is no sustained host rate
to pause. Documented as a known limitation; revisit (lossless host queue) only
when multi-MTU messages arrive (Phase 3). **Open question Q-A below.**

---

## 6. Alternatives considered

| Option | Mechanism | Pros | Cons | Verdict |
|---|---|---|---|---|
| **Refcount release-once** *(chosen)* | charge `size` once; hold until last replica drains; release once | Physically faithful (ingress PG = 1 buffered copy); no over-count under load; generalises to Phase-3 fan-in hold-and-combine; preserves §3.3 | Needs `VirtualQueue*` generalisation + a credit object + one egress-path branch | **Chosen** |
| **Fan-out charge (charge k)** | charge `k·size` on replication; each replica drain releases one `size` | ~10-line diff in `handle_mcast`, type-compatible, preserves §3.3 | Over-counts ingress occupancy `k×` → PFC trips early under load (corrupts the PFC-dynamics measurement); replication-specific, thrown away at Phase 3 | Rejected — undermines the stated dynamics goal; identical to chosen at single-MTU anyway |
| **Cut-through release + dummy ingress queue** | release ingress charge at replication; give replicas a no-op `VirtualQueue` | Smallest conceptual change | **Breaks losslessness** (§3.3): switch never PAUSEs its upstream on egress-branch congestion → egress `OQ_out` can overflow | Rejected (incorrect) |

At single-MTU (current Phase-2: one packet per op) refcount-once and fan-out
charge are observationally identical — PAUSE essentially never fires for a lone
MTU. They diverge only under accumulated occupancy (multi-MTU messages or many
concurrent ops converging on a port), which is exactly the Phase-3 / dynamics
regime that motivated choosing the faithful model now.

---

## 7. Validation plan

1. **Build** the `htsim_uec` target; no warnings introduced.
2. **Regression / feasibility (single-MTU):** re-run the existing bcast tests
   (`datacenter/connection_matrices/...`, baseline + mcast) with
   `-queue_type lossless_input` and modest `-q` / thresholds. Assert:
   - zero `"LOSSLESS not working! I should have dropped this packet"` lines
     (`queue_lossless_*.cpp`);
   - identical `BCAST_COMPLETE` lines (correct delivery) and completion times
     within noise of the COMPOSITE infinite-buffer runs (PAUSE should not fire
     at single-MTU);
   - end-of-sim sanity: every `LosslessInputQueue._queuesize == 0` (no leak —
     directly validates the refcount).
3. **PFC-dynamics stress (serves the "study dynamics" goal):** shrink buffer +
   thresholds and build a convergent pattern (many groups / large `|G|` whose
   trees overlap on one port) so PAUSE actually fires. Confirm: still zero
   drops, and completion time rises measurably (HOL/backpressure observable).
   This becomes an evaluation subsection.
4. Compare lossless vs infinite-buffer footprint/completion plots to quantify
   the cost of real backpressure.

---

## 8. Thesis documentation (document-alongside)

A Design/Background subsection covering: the htsim PFC pair-queue model and its
1-in/1-out contract; the multicast fan-out problem (§3.2–§3.3); and the
refcount-release-once resolution with its physical justification (ingress PG
occupancy = one shared buffered copy held until full fan-out), plus the
forward-compat argument to Phase-3 aggregation fan-in. Produced as the step is
implemented, not after.

---

## 9. Open questions for you

- **Q-A (source-side PAUSE):** OK to leave host NIC queues non-lossless for
  Task 1 (justified by single-MTU emission, §5 Step 5), and revisit when
  multi-MTU lands? Or wire a lossless host queue now?
- **Q-B (default thresholds):** Accept the proposed defaults (high = 100 pkts,
  low = 80 pkts, buffer via `-q`) or do you want specific values / a
  BDP-derived default?
- **Q-C (message-size sweep):** Does your existing message-size / MTU sweep emit
  payloads **larger than one MTU** (i.e. multiple packets per op)? If so those
  runs already leave the single-MTU regime and become a natural dynamics
  testbed — I can confirm by reading the emit path before we start.

---

## 10. Implementation result & validation (Task 1 complete)

### Files changed
- `network.h` — `_ingressqueue` generalised `LosslessInputQueue*` → `VirtualQueue*`;
  added non-asserting `peek_ingress_queue()`.
- `queue_lossless_input.{h,cpp}` — factored `release_bytes(mem_b)` out of
  `completedService`; added `McastFanoutCredit : VirtualQueue` (refcount-once).
- `queue_lossless_output.cpp` — drop the now-redundant `dynamic_cast` on the
  generalised ingress pointer.
- `uecpacket.h` — both `UecMcastPacket` factories null `_ingressqueue` (PacketDB
  recycles packets; keeps `set_ingress_queue`'s `assert(!_ingressqueue)` valid).
- `datacenter/fat_tree_switch.cpp` — `handle_mcast` creates one
  `McastFanoutCredit` (pending = k egress ports) and points every replica's
  ingress queue at it; `k==0` releases immediately. `identify_ingress_port_idx`
  extended for the lossless wiring (a port's remote endpoint is the downstream
  switch's `LosslessInputQueue`; match via `riq->getSwitch()`).
- `datacenter/main_uec.cpp` — `qt = queue_choice` (was hardcoded COMPOSITE);
  set `LosslessInputQueue::_high/_low_threshold` from `-pfc_high`/`-pfc_low`
  (defaults 100/80 pkts) when a lossless queue type is selected.

### Validation (bcast_test1.cm, 16 nodes, 100 Gbps)
| mode | queue | result |
|---|---|---|
| baseline | composite | 4596 / 5272 ns |
| baseline | lossless_input | **4596 / 5272 ns (identical)** |
| mcast | composite | 3582 / 3582 ns |
| mcast | lossless_input | **3582 / 3582 ns (identical)** |

Identical timing confirms PFC does not perturb when buffers aren't stressed.

### Stress (the real refcount test)
- 1 MB multicast, |G|=16, tiny thresholds (high=3/low=1 pkt → constant
  PAUSE/RESUME): completes, **0 drops, 0 asserts**. Refcount survives sustained
  pausing without leaking (a leak would latch the port PAUSED and stall).
- **4 concurrent convergent broadcasts, 512 KB each, tiny thresholds:**
  lossless completes **all 4, 0 drops, 0 asserts**; COMPOSITE (lossy) completes
  **none** — partial legs only. This is the empirical case for the task: the
  lossless fabric correctly carries exactly the convergent workload where the
  ACK-less collective on a lossy fabric silently fails to complete (dropped
  packets are never recovered).

### Known limitations / open items
- Host NIC send queues remain non-lossless (Q-A deferred): justified while
  sources emit at most a single MTU burst per leg; revisit for sustained
  multi-MTU host rates.
- COMPOSITE multicast under heavy convergent load not completing is pre-existing
  lossy behaviour (the COMPOSITE path is untouched — `handle_mcast`'s credit
  logic is a no-op when there is no ingress queue); characterising its drop
  mechanism is out of scope for Task 1.
