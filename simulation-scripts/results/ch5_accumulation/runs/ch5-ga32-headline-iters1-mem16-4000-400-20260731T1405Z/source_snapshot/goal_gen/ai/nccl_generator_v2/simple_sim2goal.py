from __future__ import annotations

from typing import Dict, List, Optional, Type

from communication import CommOp, Communicator, CollDevice, CollAlgo
from nccl_primitives import GpuId

import pickle
from collections import defaultdict
import pathlib
from functools import reduce
from tqdm import tqdm

import os
from goal import EMIT_INC, GoalCalc, GoalCollective, GoalGraph, GoalGraphNode, GoalOp, GoalRank, node_contained
from simple_sim.extract import ExtractedGraph, topo_sort
from simple_sim.ir import CommOp as SimCommOp, ComputeOp as SimComputeOp, OpNode, Tensor, Token, Group
from simple_sim.ops_comm import AllReduceOp, AllGatherOp, ReduceScatterOp, SendOp, FillOp as RecvOp

context_dict = {
    "tp": 1,
    "zero1": 2,
    "pp": 3,
    "tp_sp": 4,
}

# --- Per-domain INC emit (scale-up collectives -> undecomposed `coll`) ---------
# EMIT_INC=1 (goal.EMIT_INC) makes NODE-CONTAINED collectives emit a first-class
# `coll` line instead of decomposing to send/recv; everything else stays
# decomposed. This produces the INC arm of the A/B; EMIT_INC unset reproduces
# the baseline byte-identically. Two layers:
#   * mechanism = node_contained(group_ranks, gpus_per_node) (goal.py): the
#     communicator must live entirely inside ONE scale-up domain (node). This
#     replaces the earlier context=="tp" proxy, which merely ASSUMED that TP
#     groups happen to be node-contained -- the layout property is now checked.
#   * policy = INC_CONTEXTS (env, comma-separated, default "tp"): which
#     parallelism contexts are allowed to go INC at all (INC is scoped to
#     tensor parallelism per the supervisor; widen via e.g.
#     INC_CONTEXTS=tp,zero1 without touching the containment mechanism).
INC_CONTEXTS = frozenset(c.strip() for c in os.environ.get("INC_CONTEXTS", "tp").split(",") if c.strip())

# SKELETON_CONTEXTS (env, comma-separated, default empty): comm ops in these
# contexts are emitted as dependency-preserving zero-cost `calc 0` placeholders
# instead of their real communication -- in BOTH arms (the decomposed baseline
# and the EMIT_INC coll path both pass through translate_comm_node). The
# skeleton run measures everything EXCEPT those ops; full - skeleton is the
# causal attribution of the makespan to them (the G72 realism-ladder method).
# With identical non-TP structure, the baseline-skeleton and INC-skeleton
# traces are equal by construction -- a consistency check the caller can make.
SKELETON_CONTEXTS = frozenset(c.strip() for c in os.environ.get("SKELETON_CONTEXTS", "").split(",") if c.strip())

# Compute-cost model for `calc` durations (see translate_compute_node).  The
# default "placeholder" preserves the original behaviour byte-for-byte.
#
# ``h100`` is the legacy per-op BF16 roofline.  It applies a whole-training MFU
# to every kernel and is retained only so old traces remain reproducible.
# ``h100_te`` is the model used by the AI case study: an optimized H100
# FP8-math/BF16-storage roofline.  It prices each analytical op independently
# at the hardware ceilings.  That makes it an optimized-compute lower bound without
# allowing one kernel to discount another or importing end-to-end stalls.
_COMPUTE_MODEL = os.environ.get("COMPUTE_MODEL", "placeholder").strip().lower()

# H100 SXM hardware ceilings.  NVIDIA quotes twice these tensor-core rates when
# structured sparsity is enabled; simple_sim does not model sparsity, hence the
# dense values below.  Communication bytes remain FP16/BF16 (2 B/element).
_H100_BF16_DENSE_FLOPS = 989e12
_H100_FP8_DENSE_FLOPS = 1.979e15
_H100_HBM_BW = 3.35e12
_H100_STORAGE_BYTES_PER_ELEMENT = 2
_COLL_KIND = {"AllReduceOp": "allreduce", "AllGatherOp": "allgather",
              "ReduceScatterOp": "reduce_scatter"}
_inc_group_idx: Dict[str, int] = {}          # match_key -> group index (stable across ranks)
_inc_group_members: Dict[int, List[int]] = {}  # group index -> member global ranks
_inc_instance_count: Dict[str, int] = {}     # match_key -> per-rank occurrence counter (reset per rank)

# simple_sim has no explicit node model: the node width is implicit in the
# graph builders' cluster convention (simple_sim/llama3_training.py: "TP=4
# (tensor parallelism within a node)" with the Megatron TP-fastest-varying
# rank layout), so the TP degree IS the scale-up-domain width. Derived once
# per render; None (=> the INC predicate selects nothing) when underivable.
_gpus_per_node: Optional[int] = None


def derive_gpus_per_node(rank_nodes: Dict[int, List[OpNode]]) -> Optional[int]:
    """Derive the node width from the unique size of the INC-eligible groups.

    "INC-eligible" = the contexts named by INC_CONTEXTS (the same policy env
    that gates emission), so an SP render whose tensor-parallel comm is all
    context=="tp_sp" derives the width from those groups; the default
    INC_CONTEXTS="tp" keeps the original context=="tp" behavior. Either way
    the TP degree IS the scale-up-domain width (cluster convention)."""
    sizes = {node.group.size for nodes in rank_nodes.values() for node in nodes
             if isinstance(node, SimCommOp) and node.context in INC_CONTEXTS}
    if len(sizes) != 1:
        print(f"Warning: cannot derive gpus_per_node from TP group sizes {sorted(sizes)}; "
              f"EMIT_INC will select nothing")
        return None
    return sizes.pop()

device2goal_rank = {rank: rank for rank in range(4096)}  # was 32; lifted so >32-GPU traces don't KeyError in the ring builder (htsim tops out ~1000 hosts anyway)

def get_context(sim_node: SimCommOp) -> int:
    """Map simple_sim comm op context string to an integer for Goal IR."""
    # node_label = sim_node.label if sim_node.label else "unknown"
    # tag = ((hash(node_label) & 0xFFFF) % 1000) * 100 + context_dict.get(sim_node.context, 0)
    # return tag
    return context_dict.get(sim_node.context, 0)

def translate_comm_node(sim_node: SimCommOp, communicators: Dict[str, Communicator], device2goal_rank, *, cpu: int = 0) -> Optional[CommOp]:
    """Translate a simple_sim CommOp into an nccl_comm CommOp.

    Returns ``None`` when the translation is not yet implemented – the
    caller falls back to a zero-cost ``GoalCalc`` placeholder.

    TODO: implement per-collective-type mapping once the nccl_comm
    parameter construction is figured out.
    """
    # Skeleton mode: ops in SKELETON_CONTEXTS become zero-cost placeholders
    # (the None fallback emits a dependency-preserving `calc 0`).
    if sim_node.context in SKELETON_CONTEXTS:
        return None
    comm = communicators.get(sim_node.group.match_key)
    if comm is None:
        print(f"Warning: no communicator found for group {sim_node.group}, cannot translate {sim_node}")
        return None
    tensor_size = sim_node.inputs[0].shape
    n_elements = reduce(lambda x, y: x * y, tensor_size, 1) * 2
    # ZeRO-1 gradient/parameter collectives operate on the PHYSICAL per-TP-rank
    # shard, but the IR keeps logical shapes on tensors (sharding lives in
    # ShardSpec metadata) -- the logical product overstates every zero1 payload
    # by exactly the TP factor (inherited from the original translator,
    # 0c7d301). tp_group is the reliable divisor: the RS input still carries
    # the TP ShardSpec but the AG input's spec is overwritten by
    # reduce_scatter's tensor_replace, so physical_shape() would be wrong
    # there. ZERO1_LOGICAL_BYTES=1 restores the historical behaviour.
    if (sim_node.context == "zero1"
            and getattr(sim_node.inputs[0], "tp_group", None) is not None
            and os.environ.get("ZERO1_LOGICAL_BYTES") != "1"):
        n_elements //= sim_node.inputs[0].tp_group.size
    context = get_context(sim_node)
    # Scale-up collective -> emit one undecomposed `coll` op (INC arm). The
    # group must be node-contained (checked via node_contained -- no longer the
    # old context=="tp" proxy that merely assumed it) AND pass the INC_CONTEXTS
    # policy filter. The k-th collective on a group gets the same within-group
    # instance on every member (per-rank counter, reset per rank).
    # htsim keys its per-op barrier by op_flow_id ALONE, so op_flow_id must be
    # GLOBALLY UNIQUE across groups (else groups collide and the 2nd never sets up
    # -> hang). Fold the (stable) group index into it: (gi<<16)|within_group_inst.
    kind = _COLL_KIND.get(type(sim_node).__name__)
    if (EMIT_INC and kind is not None
            and sim_node.context in INC_CONTEXTS
            and _gpus_per_node is not None
            and node_contained([r for r in comm.rank2device_id if r is not None], _gpus_per_node)):
        mk = sim_node.group.match_key
        if mk not in _inc_group_idx:
            gi = len(_inc_group_idx)
            _inc_group_idx[mk] = gi
            _inc_group_members[gi] = list(comm.rank2device_id)
        gi = _inc_group_idx[mk]
        inst = _inc_instance_count.get(mk, 0)
        _inc_instance_count[mk] = inst + 1
        op_flow_id = (gi << 16) | inst   # unique across groups, stable across members
        return GoalCollective(kind, group=gi, size=n_elements, instance=op_flow_id,
                              root=-1, nic=0, cpu=cpu)
    if isinstance(sim_node, AllReduceOp):
        # SIM_AR_ALGO (env, default "ring"): the decomposed AllReduce schedule.
        # NCCL's large-message allreduce is the ring; the original hardcoded
        # RECURSIVE_DOUBLING (Shuhao, 0c7d301) was a convenient bandwidth-
        # optimal stand-in, not an NCCL-fidelity choice (NCCL ships no RD
        # allreduce). Both move 2(N-1)/N * S per rank; "rdouble" is kept as
        # the sensitivity knob.
        _ar_algo = {"ring": CollAlgo.RING,
                    "rdouble": CollAlgo.RECURSIVE_DOUBLING}[
            os.environ.get("SIM_AR_ALGO", "ring").strip().lower()]
        op = comm.allreduce(size=n_elements, context=context, algo=_ar_algo)
    elif isinstance(sim_node, AllGatherOp):
        op = comm.allgather(size=n_elements, context=context, algo=CollAlgo.RING)
    elif isinstance(sim_node, ReduceScatterOp):
        op = comm.reducescatter(size=n_elements, context=context, algo=CollAlgo.RING)
    elif isinstance(sim_node, SendOp):
        op = comm.send(size=n_elements, context=context, dst_rank=sim_node.dst)
    elif isinstance(sim_node, RecvOp):
        op = comm.recv(size=n_elements, context=context, src_rank=sim_node.src)
    else:
        print(f"Warning: unrecognized comm op type {type(sim_node)}, cannot translate {sim_node}")
        return None
    return op.to_goal(device2goal_rank, cpu, nic=0)


# def get_cost(cost_meta: dict) -> int:
#     """Estimate wall-clock cost (in ns) of a compute op via roofline model.

#     TODO: implement a proper roofline analysis that considers hardware
#     peak FLOPS and memory bandwidth.  For now, just returns the FLOP
#     count as a placeholder duration.
#     """
#     return cost_meta["flops"]

def _roofline_ns(cost: dict, *, peak_flops: float, hbm_bw: float,
                 bytes_per_element: int, compute_efficiency: float = 1.0,
                 memory_efficiency: float = 1.0) -> float:
    """Return a first-order roofline duration in nanoseconds.

    ``simple_sim`` reports memory traffic in elements, not bytes.  Keeping the
    conversion here makes the storage precision explicit and prevents the
    formula from silently mixing the two units.
    """
    flops = cost.get("flops", 0)
    elements = cost.get("mem_read", 0) + cost.get("mem_write", 0)
    t_compute_ns = flops / (peak_flops * compute_efficiency) * 1e9
    t_memory_ns = (elements * bytes_per_element) / (hbm_bw * memory_efficiency) * 1e9
    return max(t_compute_ns, t_memory_ns)


def translate_compute_node(compute_node: SimComputeOp, *, cpu: int = 0) -> GoalOp:
    cost = compute_node.get_cost_meta()
    flops = cost.get("flops", 0)
    mem = cost.get("mem_read", 0) + cost.get("mem_write", 0)

    # NB the simulator consumes `calc N` as NANOSECONDS (logsim-interface OP_LOCOP
    # adds N straight onto the ns clock), so every branch here yields ns.
    if _COMPUTE_MODEL == "h100":
        # LEGACY / REPRODUCTION ONLY.  MFU is an end-to-end training metric and
        # therefore also contains communication and stalls that this simulator
        # models explicitly.  Applying it here double-counts those costs, which
        # is why the AI case study now selects ``h100_te`` instead.
        duration_ns = _roofline_ns(
            cost,
            peak_flops=_H100_BF16_DENSE_FLOPS,
            hbm_bw=_H100_HBM_BW,
            bytes_per_element=_H100_STORAGE_BYTES_PER_ELEMENT,
            compute_efficiency=0.45,
            memory_efficiency=0.70,
        )
        return GoalCalc(int(round(duration_ns)), cpu=cpu)

    if _COMPUTE_MODEL == "h100_te":
        duration_ns = _roofline_ns(
            cost,
            peak_flops=_H100_FP8_DENSE_FLOPS,
            hbm_bw=_H100_HBM_BW,
            bytes_per_element=_H100_STORAGE_BYTES_PER_ELEMENT,
        )
        return GoalCalc(int(round(duration_ns)), cpu=cpu)
    
    # Legacy dimensionless placeholder.  These values are passed directly to
    # ``calc`` as nanoseconds; keep the historical divisors byte-for-byte rather
    # than assigning them misleading physical throughput units.
    compute_units = flops // 1e6
    memory_units = mem // 1e6

    duration = max(compute_units, memory_units)
    return GoalCalc(int(duration), cpu=cpu)

def extract_communicators(rank_nodes: Dict[int, List[OpNode]]) -> Dict[str, Communicator]:
    participating_ranks = defaultdict(dict)
    for rank, nodes in rank_nodes.items():
        for node in nodes:
            if isinstance(node, SimCommOp):
                group = node.group
                participating_ranks[group.match_key][group.self_rank] = rank
    for match_key, rank_map in participating_ranks.items():
        participating_ranks[match_key] = [rank_map[i] for i in range(len(rank_map))]
    communicators = {}
    for match_key, rank_map in participating_ranks.items():
        communicators[match_key] = Communicator(rank_map, match_key)
    return communicators


def get_graphs(graphs_dir: pathlib.Path):
    graph_paths = sorted(graphs_dir.glob("*.pkl"))
    graphs = {}
    for path in graph_paths:
        # The rank is the filename STEM, not the alphabetical position: the
        # graph writers pad device ids to two digits (f"{device_id:02d}.pkl"),
        # so at >= 100 ranks "100.pkl" sorts between "10.pkl" and "11.pkl" and
        # the old enumerate() scrambled every rank id above 99 -- TP groups
        # then looked node-spanning and EMIT_INC (correctly) selected nothing.
        with open(path, 'rb') as f:
            # pickle is safe here: these .pkl graphs are this pipeline's own
            # artifacts, written moments earlier by simple_sim's graph builders
            # into a caller-chosen directory -- never untrusted input.
            graph = pickle.load(f)
            # return the graph as a list of topologically sorted nodes
            graphs[int(path.stem)] = topo_sort(graph.nodes)
    return graphs
    
def simple_ir2goal_ir(
    nodes: List[OpNode],
    communicators: Dict[str, Communicator],
    *,
    cpu: int = 0,
    nic: int = 0,
) -> GoalGraph:
    """Translate a simple_sim :class:`ExtractedGraph` into a :class:`GoalGraph`.

    Compute nodes become ``GoalCalc(get_cost(...))``.
    Communication nodes are translated via ``get_comm_op`` → Goal IR.
    When ``get_comm_op`` returns ``None`` (not yet implemented),
    a zero-cost ``GoalCalc`` placeholder is emitted instead.
    """

    # Per-rank reset of the INC instance counter: each rank visits the same
    # collectives in the same order, so the k-th collective on a group gets the
    # same instance id on every member rank (cross-rank match key for htsim).
    _inc_instance_count.clear()

    # Map simple_sim node id → GoalGraphNode
    node_map: Dict[int, GoalGraphNode] = {}
    graph_nodes: List[GoalGraphNode] = []

    for node in nodes:
        # --- collect predecessor GoalGraphNodes -----------------------
        predecessors = _get_predecessors(node, node_map)

        # --- translate to GoalOp -------------------------------------
        if isinstance(node, SimCommOp):
            goal_op = translate_comm_node(node, communicators, device2goal_rank, cpu=cpu)
            if goal_op is None:
                # The documented fallback (untranslated or SKELETON_CONTEXTS
                # comm op): a dependency-preserving zero-cost placeholder.
                goal_op = GoalCalc(0, cpu=cpu)
        else:
            goal_op = translate_compute_node(node, cpu=cpu)

        gnode = GoalGraphNode(goal_op, predecessors)
        node_map[node.id] = gnode
        graph_nodes.append(gnode)

    return GoalGraph(graph_nodes, cpu=cpu)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_predecessors(
    node: OpNode,
    node_map: Dict[int, GoalGraphNode],
) -> List[GoalGraphNode]:
    """Return deduplicated predecessor ``GoalGraphNode``s for *node*.

    A predecessor is any node in *node_map* that produces a
    ``Tensor`` or ``Token`` consumed by *node*.
    """
    seen: set[int] = set()  # id(GoalGraphNode) for dedup
    preds: List[GoalGraphNode] = []
    for inp in node.inputs:
        producer: Optional[OpNode] = None
        if isinstance(inp, Tensor):
            producer = inp.producer
        elif isinstance(inp, Token):
            producer = inp.producer
        if producer is not None and producer.id in node_map:
            gnode = node_map[producer.id]
            if id(gnode) not in seen:
                seen.add(id(gnode))
                preds.append(gnode)
    return preds

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Translate simple_sim per-device graphs -> a single GOAL trace. "
                    "EMIT_INC=1 (env) emits node-contained collectives as first-class "
                    "`coll` ops + a .groups sidecar; unset reproduces the decomposed baseline.")
    ap.add_argument("--graphs-dir", default="llama3_graphs",
                    help="directory of per-device NN.pkl graphs (from llama3_training.py)")
    ap.add_argument("--out-goal", default=None,
                    help="output .goal path; default llama3_inc.goal (EMIT_INC) / llama3.goal")
    ap.add_argument("--groups", default=None,
                    help="output .groups path (INC only); default <out-goal>.groups")
    args = ap.parse_args()

    graphs = get_graphs(pathlib.Path(args.graphs_dir))
    communicators = extract_communicators(graphs)
    if EMIT_INC:
        _gpus_per_node = derive_gpus_per_node(graphs)
    # print(f"Extracted communicators: {communicators}")
    out_goal = args.out_goal or ("llama3_inc.goal" if EMIT_INC else "llama3.goal")
    with open(out_goal, "w") as f:
        f.write(f"num_ranks {len(graphs)}\n")
        # Rank blocks MUST be emitted in ascending numeric order: LogGOPSim's
        # txt2bin serializes ranks sequentially through a jumptable (rank r
        # chains on rank r-1's end offset and the parser stops at the block
        # labeled num_ranks-1), so an out-of-order goal file compiles into a
        # corrupt .bin or segfaults. dict insertion order follows the
        # alphabetical graph filenames, which diverges from numeric order at
        # >= 100 ranks ("100.pkl" sorts before "11.pkl").
        for rank in tqdm(sorted(graphs), desc="Translating to Goal IR"):
            nodes = graphs[rank]
            with CollDevice(rank):
                goal_graph = simple_ir2goal_ir(nodes, communicators, cpu=0, nic=0)
                f.write(f"rank {rank} {{\n")
                for line in goal_graph.generate_lines():
                    f.write(f"{line}\n")
                f.write("}\n")
    if EMIT_INC:
        groups_path = args.groups or (out_goal.rsplit(".", 1)[0] + ".groups")
        with open(groups_path, "w") as gf:
            for gi in range(len(_inc_group_members)):
                members = sorted({r for r in _inc_group_members[gi] if r is not None})
                gf.write(" ".join(str(r) for r in members) + "\n")
        print(f"INC emit: {len(_inc_group_members)} scale-up group(s) -> {groups_path}; goal -> {out_goal}")
    # for rank, nodes in graphs.items():
    #     with CollDevice(rank) as coll_device:
    #         goal_graph = simple_ir2goal_ir(nodes, communicators, cpu=0, nic=0)
    #         print(f"Goal graph for rank {rank} with {len(goal_graph.nodes)} nodes:")
    #         with open(goal_path / f"{rank:02d}.goal", "w") as f:
    #             for line in goal_graph.generate_lines():
    #                 f.write(line + "\n")
    # sample_graph_path = "graphs/llama3_training_device0.pkl"
    # with open(sample_graph_path, 'rb') as f:
    #     simple_graph = pickle.load(f)
    #     print(f"Loaded simple graph from {sample_graph_path} with {len(simple_graph.nodes)} nodes")
    # goal_graph = simple_ir2goal_ir(simple_graph, self_rank=0, cpu=0)
    # print(goal_graph)
