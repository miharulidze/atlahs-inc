# AA-plan — Design & Implementation chapter (Blue-Phase sections)

**Status:** approved in conversation 2026-07-22; structure locked, drafting of the `.tex` is the follow-up.
**Scope:** the *blue-phase* sections of the thesis skeleton's `Design and Implementation.tex` only —
`\section{New Primitives in HTSIM}` and `\section{INC-Based Collective Operations}`. The
yellow/green sections (GOAL toolchain, trace generation, multi-domain backend) are out of scope for this pass.
**Does not** modify `.tex` yet — this is the structural proposal.

## 0. Context

Two Design chapters exist today:

- **Full manuscript** (`~/CLionProjects/thesis/Design and Implementation.tex`, ~12.8k words) —
  organised *by mechanism*, ~2× the BSc length norm. Source of reusable prose.
- **Skeleton** (`~/CLionProjects/thesis-skeleton/Design and Implementation.tex`) —
  organised *by layer* (primitives → collectives) and *base-vs-composed*. This is the trim target and
  the outline this plan refines.

The skeleton's two-layer split is retained: it mirrors the actual architecture — **two switch
primitives → six collectives** — and reads better than the manuscript's mechanism-lumping. The job is
to fill it precisely, fix accuracy gaps, and keep it *shorter* than the current bulleted skeleton
(prose → tables, name each mechanism once, forward-reference results to Evaluation).

## 1. Guiding constraints

1. **Trim-positive.** Every change replaces prose with a table or a single named concept. No new
   long subsections. Results (e.g. the constant-in-|G| makespan) are forward-referenced to Evaluation,
   not restated here.
2. **Design ≠ Evaluation scope.** The Design chapter describes the *general* FatTree-based,
   **multi-tier-capable** INC system. The single non-blocking crossbar is an **evaluation-methodology
   choice**, documented in Validation & Evaluation — *not* a limitation of the design or the code.
3. **No rejected alternatives in the thesis body.** Design decisions are presented rationale-only.
   Alternatives-considered live in the design-wiki ADRs, not in the chapter.
4. **Naming discipline.** `bcast` = the MPI-level collective; `mcast` = the network-layer multicast
   mechanism. "X-host fat tree", never "X-node".
5. **Formal consistency** (see §7).

## 2. Proposed structure

```
\section{New Primitives in HTSIM}
  \subsection{Motivation & Design Choices}
      - Scope/validity statement (timing + link-footprint model, no payload arithmetic;
        lossless as a feasibility precondition; FatTree scale-up abstraction).
      - Design-decisions TABLE (rationale-only) — see §5.1.
  \subsection{Switch-based Multicast and Aggregation}
      Construction time:  group parse -> per-group any-source RPF tree
                          -> round-robin core placement -> per-switch INCFib install.
      Run time:           multicast fan-out (RPF self-exclusion);
                          aggregation fan-in (buffer-until-all-children, single root-port
                          emit, descending-tag routing); AllReduce apex turn-around;
                          refcount-release-once PFC accounting (name the mechanism);
                          ACK-less barrier-gated completion.
      [figures: adapt appendix-lossless-cases TikZ — see §6]

\section{INC-Based Collective Operations}
  - lead-in: collective -> primitive map TABLE — see §5.2.
  \subsection{Base Collectives}                 Broadcast, Reduce, AllReduce (apex turn-around)
  \subsection{Composed Collectives}             AllGather, ReduceScatter, AllReduce (composed)
                                                 [composed-AllReduce = PENDING RS∘AG, see §8]
  \subsection{Standalone experiment harness}    the .cm driver, one short paragraph
  \subsection{Correctness by structural invariants}   was "Testing", reframed
```

## 3. Per-section content

### 3.1 Motivation & Design Choices
- **Scope/validity statement (new, up front).** State plainly what the simulator models: **network
  timing and per-link footprint, not the numerical reduction**. Reduce packets carry no operands; the
  switch performs no arithmetic; it models the fan-in barrier and the *k*→1 byte collapse. This
  defines what is measured and honestly bounds the correctness claims.
- **FatTree as the scale-up abstraction.** ATLAHS v2 (`hu2026atlahsv2`) binds the scale-up domain to a
  FatTree; a switch-based, multi-tier design generalises from a single-node crossbar to rack-scale
  (NVL72) and 1024/4096-GPU domains. Most INC in scale-up is single-switch *today*; the design targets
  future multi-tier fidelity. (The specific single-crossbar instantiation is an Evaluation choice.)
- **Lossless transport as a precondition.** In-network aggregation is non-idempotent — a dropped-and-
  retransmitted contribution double-counts, and a lost partial result has no cheap end-to-end recovery
  — so INC presupposes a lossless fabric; no switch retransmissions. Substrate-agnostic: SHARP on
  InfiniBand (credit-based), NVLS on NVLink/NVSwitch, EPIC on standard Ethernet, UALink memory-semantic
  load/store, SUE lossless Ethernet. Losslessness is a **feasibility condition, not an optimality claim**.
- Present these as the **rationale-only decisions table** (§5.1). Reuse the manuscript's
  `\section{Simulation Environment}` numbered-decisions prose, compressed.

### 3.2 Switch-based Multicast and Aggregation
- **Construction time.** Group file parsed → one **any-source (root-agnostic) reverse-path-forwarding
  tree per group**; trees fairly distributed across core switches round-robin to load-balance; each
  participating switch gets one `INCFibEntry` (the port mask + toward-root port) installed. Stress that
  one tree per group (not per-source) collapses O(n²) directed trees to O(n) FIB state.
- **Run time — multicast (fan-out).** A multicast packet is replicated on every port leading to group
  members *except the incoming port* (RPF self-exclusion). Lossless fan-out: the switch input queue
  grants credit back to the remote endpoint only once **all** replicas have drained on the egress side.
- **Run time — aggregation (fan-in).** Reduce packets are buffered at the switch until every child has
  arrived (per-chunk fan-in barrier), then a single combined packet is emitted on the one toward-root
  port; at the root port it is tagged **descending** so downstream switches route it by the regular
  (deterministic) down-FIB instead of re-aggregating. Fan-out is fan-in reversed.
- **AllReduce apex turn-around.** At the apex switch the aggregate is turned around in-network and
  multicast back down the same tree — no host round-trip.
- **Refcount-release-once PFC accounting (name it).** A store-and-forward switch stores a multicast
  packet *once* and reads it out *k* times, so ingress occupancy is one buffered copy, not *k*. Two
  small `VirtualQueue` tokens implement this with **no change to the stock lossless queue**:
  `McastFanoutCredit` holds the single ingress charge until the last replica drains (fan-out);
  `ReduceFanInCredit` holds the children's charges until the combined result commits (fan-in). Holding
  the children's charges *is* what back-pressures them when the uplink blocks.
- **ACK-less, barrier-gated completion.** Collective flows carry no per-flow protocol state and need no
  retransmission on a lossless fabric; sinks emit no ACK/NACK. One persistent sink per (host, group)
  counts payload bytes and fires a barrier once all expected bytes arrive, bypassing the send/recv ACK
  path. Which endpoints fire the barrier varies by collective (root only for Reduce; all members for
  AllReduce/RS/AG; the *n*−1 receivers for Broadcast).

### 3.3 Base Collectives
Formal definitions with worked examples (§7 notation). For a communicator `G`, `|G| = n`, members
`r_1,…,r_n`, messages `{M_1,…,M_n}` of equal size `k`:
- **Broadcast(r, G, M).** Rooted. The scheduler emits `⌈k/MSS⌉` multicast packets from root `r`; the
  switches RPF-fan them over the group tree. (Switch-multicast is the standard and only path — see §7,
  fix ③.)
- **Reduce(r, G, {M_1,…,M_n}).** The scheduler emits `⌈k/MSS⌉` reduce packets on every member; switches
  aggregate up the tree; the apex delivers the result to root `r` via the regular unicast down-FIB.
- **AllReduce (apex turn-around).** Symmetric, rootless. Every member emits `⌈k/MSS⌉` reduce packets;
  the apex turns the aggregate around into a downward multicast to all members. Mention *briefly* that
  a negative root sentinel selects turn-around vs rooted delivery (§7, fix ④ — one line, no more).

### 3.4 Composed Collectives
Hierarchical: composed collectives are **driver-level orchestration** of the same two primitive
classes — barriers, trigger-chaining, per-chunk root stamping — adding **zero new switch/FIB/packet
state**. (This is a result to sell, not an implementation footnote.)
- **AllGather(G, {M_1,…,M_n}).** `n` concurrent Broadcasts `Broadcast(r_i, G, M_i)` sharing one tree;
  RPF self-exclusion is exactly why a member never receives its own block back.
- **ReduceScatter(G, {M_1,…,M_n}).** `n` concurrent rooted Reduces over one tree; the message is split
  into `n` equal chunks `C_i` and `r_i` is the root of `Reduce(r_i, G, C_i)`. The *only* difference from
  Reduce is per-chunk root stamping.
- **AllReduce (composed).** **PENDING — RS∘AG, user to wire and report (§8).** Until then the
  documented composed variant is Reduce∘Broadcast (rooted Reduce trigger-chained to a Broadcast from
  the root), which serves as the host-round-trip comparison baseline for the apex turn-around. NVLS's
  RS∘AG decomposition is cited as motivation / related work, not conflated with this.

### 3.5 Standalone experiment harness (the `.cm` driver)
One short paragraph: the connection-matrix path was the standalone bring-up/validation harness used to
develop and exercise the primitives directly. Forward-reference the GOAL-driven path (yellow phase) as
*the* production driver.

### 3.6 Correctness by structural invariants (was "Testing")
Because the switch does no arithmetic, correctness is established **structurally**, not by value tests:
barrier completion, machine-parseable `*_COMPLETE` op-count tokens, dependent-node release, and
zero-drop under PFC. This bridges into Validation & Evaluation.

## 4. Reuse map (compress, don't transplant)

| New subsection | Reuse from full manuscript |
|---|---|
| Motivation & Design Choices | `\section{Simulation Environment}` (numbered decisions) |
| Multicast/Aggregation — construction | `\subsection{Tree Construction}` |
| Multicast/Aggregation — runtime | `\subsection{Packet Format and Switch Datapath}` + `\subsection{The fan-in barrier}` |
| Base — AllReduce | `\subsection{Allreduce: apex turn-around}` |
| Composed | `\subsection{Rooted Reduce, Reduce-Scatter, and AllGather}` |
| Correctness by invariants | `\subsection{Validation and Testing}` |

Length caution: the manuscript prose re-imports the 2× problem — compress, don't paste. The
standalone `\section{Broadcast Baseline}` and `\section{Lossless Operation under PFC}` do **not** get
their own homes: broadcast-baseline is dropped (fix ③); lossless folds into Design Choices + the
runtime bullets.

## 5. Tables (fully specified)

### 5.1 Design decisions (rationale-only, no rejected alternatives)

| Decision | Rationale |
|---|---|
| Lossless fabric (PFC) | Aggregation is non-idempotent — a dropped/retransmitted contribution double-counts and a lost partial has no cheap recovery — so INC presupposes losslessness; a feasibility precondition, not an optimality claim. Matches every emerging scale-up fabric (NVLink credit FC, UALink memory-semantic, OISA multi-level FC, SUE lossless Ethernet). |
| ACK-less, barrier-gated completion | Collective flows carry no per-flow protocol state and need no RTX on a lossless fabric; a persistent per-(host,group) sink counts payload bytes and fires a barrier when all expected bytes arrive, bypassing the send/recv ACK path. |
| Timing + footprint, no payload arithmetic | The reduction value is immaterial to network timing/footprint; the switch models the fan-in barrier and the *k*→1 byte collapse without operands. Correctness is proven structurally, not by value tests. |
| One any-source RPF tree per group | A root-agnostic reverse-path-forwarding tree serves multicast, reduce ascent and AllReduce descent for any root, collapsing O(n²) per-source directed trees to O(n) per-switch FIB state and enabling zero-state composition. |
| AllReduce apex turn-around | Reducing up and multicasting the result back down from the apex completes in-network with no host round-trip; each link carries each byte once per direction. |
| FatTree scale-up abstraction (multi-tier capable) | ATLAHS v2 binds the scale-up domain to a FatTree; a switch-based multi-tier design generalises from a single node to rack-scale (NVL72) and 1024/4096-GPU domains without datapath changes. |

### 5.2 Collective → primitive map

| Collective | Category | Primitive(s) | How it's built | Adds switch state? |
|---|---|---|---|---|
| Broadcast | base | multicast fan-out | root emits `⌈k/MSS⌉` mcast packets over the group's RPF tree | no — reuses the tree |
| Reduce | base | aggregation fan-in | `n` members emit up; apex delivers to root `r` via the unicast down-FIB | **yes** — per-chunk fan-in barrier (+ optional reduce-compute) |
| AllReduce | base | fan-in + fan-out | reduce up; apex turns around → multicast down | no beyond Reduce's |
| AllGather | composed | multicast fan-out (×n) | `n` Broadcasts on one shared tree; RPF self-exclusion | no |
| ReduceScatter | composed | aggregation fan-in (×n) | `n` rooted Reduces on one tree; per-chunk root stamping | no |
| AllReduce (composed) | composed | fan-in + fan-out | *pending RS∘AG*; baseline Reduce∘Broadcast via root `r` | no |

**Punchline (state under the table):** *only aggregation introduces switch state (the fan-in barrier);
multicast and every composition reuse existing state.*

## 6. Figures

- **Scenario visuals (Multicast/Aggregation runtime).** Adapt the four `appendix-lossless-cases.tex`
  TikZ diagrams — baseline one-charge/one-release, multicast fan-out credit, rooted-reduce fan-in,
  AllReduce apex fan-in+fan-out — to illustrate the refcount-release-once cases inline.
- **Standalone walkthrough (independent float).** An end-to-end AllReduce apex turn-around
  (fan-in → apex → fan-out) with credit hold/release annotations, based on `fig:lossless-allreduce`.
  Keep it a free-floating figure (no tight in-text coupling) so it can be relocated.
- Optional layering diagram (2 primitives → 3 base → 3 composed) if space allows; low priority.

## 7. Accuracy fixes (checklist)

1. **Composed AllReduce ≠ RS∘AG in the current code** — it is Reduce∘Broadcast. RS∘AG is being wired
   (§8); NVLS RS∘AG is cited as motivation/related work only. *[agreed]*
2. **Base/composed is a driver-composition distinction, not a class hierarchy** — two source classes
   (multicast, aggregation) + one unified sink; AllGather reuses Broadcast's class, ReduceScatter
   reuses Reduce's. Sell the zero-added-state result. *[agreed]*
3. **Broadcast = switch-multicast as standard.** The endpoint unicast bcast is a vestigial artifact,
   dropped from the thesis; removal from the default code path is scheduled in a separate session. *[agreed]*
4. **AllReduce root sentinel** — mention at a high level only, one line. *[agreed]*
5. **Formal notation consistency** — `k` = message size throughout (not `M`); messages `{M_1,…,M_n}`
   each of size `k`; `⌈k/MSS⌉` for packet counts; `AllReduce(G, {M_1,…,M_n})` (every member contributes);
   remove the stray `x` after the composed `\end{itemize}`. *[agreed]*
6. **Single non-blocking crossbar → Evaluation scope**, not a Design limitation; the code supports
   general multi-tier fat-trees. *[agreed]*

## 8. Open items

- **Composed AllReduce = RS∘AG** — user is wiring it now and will report the implementation; slot the
  details into §3.4 when available (a one-paragraph edit; structure unchanged).
- **Broadcast default → mcast** — code change scheduled in a separate session; once landed the endpoint
  bcast baseline is fully removable from the narrative.
- **Related-work placement** — NVLS/SHARP/EPIC/MSCCL++ cited as motivation/related, kept out of the
  mechanism prose.

## 9. Next step

On approval of this structure, draft the revised `Design and Implementation.tex` (blue-phase sections),
with the composed-AllReduce subsection carrying the RS∘AG stub until the user reports the wiring.
