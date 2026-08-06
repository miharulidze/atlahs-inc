# Design Rationale: First-Class Collective Nodes in the GOAL Trace

*Thesis-ready draft for the Design & Implementation chapter (ATLAHS-bridge section).
Markdown now; lift into `.tex` after review. Implementation anchors and suggested
`refs.bib` keys are at the end.*

---

## Representing a Collective in a Per-Rank Trace

A collective operation is a single logical event, yet the GOAL trace that drives
the simulator represents each one as $\lvert G \rvert$ separate nodes --- one in
every participating rank's schedule, all sharing an instance identifier. A
natural question is whether this is redundant: could a collective be a single
node that automatically establishes its dependencies on the other ranks'
work? The answer is no, and understanding why clarifies both the representation
chosen here and how it relates to comparable systems.

### The constraint: GOAL is a per-rank schedule model

GOAL (the trace language of LogGOPSim, which htsim consumes) models an
application as one *independent* dependency graph per rank. A schedule node is
owned by exactly one rank, and every dependency edge connects two nodes *within
the same rank's graph* --- edges are stored as rank-local node offsets, and the
execution engine only ever advances a rank's own graph when one of its nodes
completes. There is no global node namespace and no edge type that can reach
across into another rank's graph. Consequently, **all cross-rank coupling is a
runtime mechanism rather than a static graph edge.** In native GOAL that
mechanism is point-to-point message matching: a rank's send produces, after
network delivery, a message that is matched by tag against the destination
rank's receive, releasing a node in each of the two ranks' *own* graphs.

This is the same property that makes a collective expensive to express in native
GOAL: a collective has no first-class existence there. It survives only as the
union of every rank's explicit send/receive legs --- a ring all-reduce becomes
$O(\lvert G \rvert)$ legs *per rank*, i.e. $O(\lvert G \rvert^{2})$ atoms in
total, and one rank's send is meaningless without the peer's matching receive.
This is exactly what the ATLAHS NCCL generator emits today, because it is a
faithful replay decomposer with no notion of a collective node.

### The choice: a typed collective node per rank, with runtime fan-out

In-network computing cannot be expressed in the point-to-point model at all: "let
the switch replicate/aggregate this" is not representable as a set of unicast
send/receive pairs. The bridge therefore introduces a **first-class typed
collective node** (`BCAST`, `REDUCE`, `ALLREDUCE`, `REDUCE_SCATTER`,
`ALLGATHER`) into the trace. Respecting the per-rank model, the collective is
emitted as **one such node per participating rank**, all carrying the same
*instance* identifier (the group's shared `op_flow_id`); the node also carries
the group index, the root (for rooted operations), and the total size.

The cross-rank rendezvous --- the part the question imagines as "automatic
dependencies to the other group nodes" --- is supplied at *runtime*, not as graph
edges, because GOAL admits no other option. As each rank reaches its collective
node, the simulator records it under the shared instance id; the first arrival
installs a single barrier and the per-member network endpoints. When the
operation completes in the fabric, the barrier fires once and the completion is
re-fanned back out to every participant, releasing each rank's node in its own
graph so its downstream work becomes eligible. Within a rank, the collective
node's ordinary dependencies on *regular* nodes (its predecessors and successors
on that rank's timeline) are still expressed as ordinary intra-rank edges by the
generator --- it is only the *inter-rank* coupling that is necessarily runtime.

### Why not a single node?

Two reasons, one structural and one semantic. Structurally, a single shared node
is unrepresentable: there is no global node and no cross-rank edge in GOAL, so
"automatic dependencies to other group nodes" cannot be encoded. Semantically,
even if the format permitted it, each rank still requires its *own* node, for two
independent jobs: to anchor that rank's local dependency chain (its collective
must wait for that rank's prior work and gate that rank's subsequent work), and
to release that rank's specific dependents when the collective completes. The
per-rank node is the unit of local ordering; collapsing the $\lvert G \rvert$
nodes to one would lose the very edges that keep each rank's replay causally
correct.

### Relation to prior work

This design is not a workaround but the dominant pattern for operator-graph
collective modelling. Chakra Execution Traces and ASTRA-sim represent a
collective as a single *typed* node --- a `COMM_COLL_NODE` carrying a collective
type, a size, and a communicator group --- and, like this work, place **one such
node in each participating rank's graph** (Chakra emits a separate execution
trace file per rank). The fan-out to peers is not encoded as graph edges either;
it is synthesised at runtime by the simulator's collective-algorithm generator
together with group membership, and the operation has a shared completion event.
The representation chosen here is the direct GOAL/htsim analogue: a typed node
per rank, the instance id playing the role of the communicator group, and the
in-network barrier playing the role of the shared completion event.

Three points of contrast are worth stating explicitly. First, against *native
GOAL*, the typed node is what makes in-network execution expressible at all, and
it reduces a ring collective from $O(\lvert G \rvert^{2})$ point-to-point atoms
to $\lvert G \rvert$ nodes. Second, a *truly single, global* collective node ---
one node for the whole operation --- is a different model again: it requires a
global, cross-rank dependency graph and a participant-aware scheduler, which
neither GOAL nor ASTRA-sim provides. Third, even mature systems keep the
abstraction shallow where convenient: ASTRA-sim, for instance, does not
algorithmically expand a broadcast but replays its recorded duration, so a typed
collective node does not by itself imply full algorithmic fidelity.

| Model | Unit of representation | Cross-rank coupling | First-class collective |
|---|---|---|---|
| GOAL native (LogGOPSim) | per-rank send/recv/calc atoms | tag-matched send/recv | No --- emergent union of P2P |
| **This work (`coll` node)** | **one typed node per rank** | **runtime barrier** | **Yes** |
| Chakra ET / ASTRA-sim | one typed node per rank (`COMM_COLL_NODE`) | runtime generator + comm group | Yes |
| Global operator-DAG | one node for the whole op | scheduler-enforced rendezvous | Yes (global) |

### Cost

The cost of the chosen representation is honest but bounded. On disk, a
collective occupies $\lvert G \rvert$ fixed-width records of which the type,
group, root, instance, and size fields are identical; only the owning rank and
the node's local offset differ. This duplication is largely intrinsic to a
per-rank serialization, in which each rank's schedule is an independently
executable graph and must carry its own node to hold its local edges. The larger
cost is the consumer-side scatter-gather that collapses the $\lvert G \rvert$
arrivals into one barrier and re-expands one completion into $\lvert G \rvert$
per-rank releases; part of even this is intrinsic, since releasing each rank's
dependents by its local offset is unavoidable in a per-rank model. Set against
the $O(\lvert G \rvert^{2})$ point-to-point alternative --- and against the
impossibility of expressing in-network execution without it --- the typed
per-rank node is the minimal faithful representation the model admits.

---

## Implementation anchors (htsim-side, not for the thesis prose)

- **Per-rank GOAL model:** node owned by one `host`; rank-local dep edges
  (`Node.DependOnMe`/`StartDependOnMe`, offsets); engine advances only
  `parser.schedules[host]` on completion. `lgs/Goal.hpp`, `lgs/Parser.hpp`
  (`addDependency` ~131, `MarkNodeAsDone` ~705), `datacenter/logsim-interface.cpp`.
- **Typed collective node:** `OPTYPE_BCAST..ALLGATHER` (10..14); `Peer` packs
  `group | root<<16`; `Tag` = instance/`op_flow_id`. `lgs/Goal.hpp` (`Collective`
  ~84), `lgs/txt2bin.cpp` (item fields ~47).
- **Runtime rendezvous:** `launch_collective` if-first dedup + `_pending_collectives`
  + `CollOpState{expected_members, arrived_count, rank_nodes}`;
  `CollectiveCompletionAdapter` on the `BarrierTrigger`; `collective_complete` →
  `push_coll_done` → `OP_COLL_DONE` per participant.
  `datacenter/logsim-interface.cpp:223-416`, `:184-221`.
- **Generator (no `coll` token):** `nccl_generator_v2` emits decomposed
  send/recv/reduce chains; `coll` is the thesis-side htsim/`txt2bin` addition.
  `goal_gen/ai/nccl_generator_v2/nccl_comm.py:229`.
- **Precedent:** Chakra `COMM_COLL_NODE` + per-NPU `.et` files; ASTRA-sim
  `generate_all_reduce/...` + `comm_group`; per-rank `ctrl_deps`/`data_deps`.
  `apps/ai/astra-sim` (`workload/Workload.cc:29,259-341`),
  `extern/graph_frontend/chakra/schema/protobuf/et_def.proto`.

## Suggested `refs.bib` keys (add before citing)

- `sridharan2023chakra` --- Sridharan et al., *Chakra: Advancing Performance
  Benchmarking and Co-design using Standardized Execution Traces*, arXiv:2305.14516, 2023.
- `rashidi2020astrasim` --- Rashidi et al., *ASTRA-SIM: Enabling SW/HW Co-Design
  Exploration for Distributed DL Training Platforms*, ISPASS 2020. (Optionally
  `won2023astrasim2` for ASTRA-sim 2.0, ISPASS 2023.)
- `hoefler2010loggopsim` --- Hoefler, Schneider, Lumsdaine, *LogGOPSim --
  Simulating Large-Scale Applications in the LogGOPS Model*, LSAP 2010 (the GOAL
  trace language). Already-present ATLAHS cite remains `shen2025atlahs`.
