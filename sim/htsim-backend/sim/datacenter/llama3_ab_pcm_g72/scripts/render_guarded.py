#!/usr/bin/env python3
"""G72 guarded renderer: simple_sim2goal.py's __main__ with the size-1-communicator
guard (render_c5.py precedent), applied for EVERY config uniformly.

With dp_size=1 the ZeRO-1/DP collectives run on a 1-member communicator; their
send/recv decomposition is empty, and goal.py's generate_lines() raises
StopIteration on an op with no atoms. A 1-member collective is semantically a
no-op, so this driver replaces any comm op whose group has size 1 with
GoalCalc(0) -- dependency-preserving, applied identically to BOTH arms (the
guard only ever fires for non-TP ops, so the A/B contrast is untouched).
Everything else is verbatim simple_sim2goal.py __main__ (submodule stays clean).
"""
import pathlib
import sys

SUB = "/Users/wstaempfli/CLionProjects/atlahs/goal_gen/ai/nccl_generator_v2"
sys.path.insert(0, SUB)

import simple_sim2goal as s2g
from communication import CollDevice
from goal import EMIT_INC, GoalCalc
from simple_sim.ir import CommOp as SimCommOp
from tqdm import tqdm

_orig_translate = s2g.translate_comm_node


def translate_guarded(sim_node, communicators, device2goal_rank, *, cpu=0):
    if isinstance(sim_node, SimCommOp) and sim_node.group.size == 1:
        return GoalCalc(0, cpu=cpu)  # 1-member collective == no-op
    return _orig_translate(sim_node, communicators, device2goal_rank, cpu=cpu)


s2g.translate_comm_node = translate_guarded

# G72: force RING for AllReduce. simple_sim2goal hardcodes RECURSIVE_DOUBLING,
# which asserts a power-of-two communicator -- impossible at |G| = 72 (the same
# constraint that excludes RD from the collective-level N=72 row). Ring is the
# canonical endpoint AllReduce at arbitrary group size; applied to EVERY
# AllReduce in the suite (DP groups included) for one uniform baseline algo.
from communication import Communicator as _Comm, CollAlgo as _CA
_orig_allreduce = _Comm.allreduce
def _allreduce_ring(self, size, context, algo=_CA.RING, device_id=None):
    return _orig_allreduce(self, size, context, algo=_CA.RING, device_id=device_id)
_Comm.allreduce = _allreduce_ring

# G72: the module-level device->goal-rank identity map is hardcoded to
# range(32) (simple_sim2goal.py:76) -- KeyError beyond 32 devices. Rebind the
# module global (read at call time) to cover any rank count in this suite.
s2g.device2goal_rank = {rank: rank for rank in range(4096)}

# ---- verbatim simple_sim2goal.py __main__ (module-qualified) ----------------
graphs = s2g.get_graphs(pathlib.Path("llama3_graphs"))
communicators = s2g.extract_communicators(graphs)
if EMIT_INC:
    s2g._gpus_per_node = s2g.derive_gpus_per_node(graphs)
out_goal = "llama3_inc.goal" if EMIT_INC else "llama3.goal"
with open(out_goal, "w") as f:
    f.write(f"num_ranks {len(graphs)}\n")
    for rank, nodes in tqdm(graphs.items(), desc="Translating to Goal IR"):
        with CollDevice(rank):
            goal_graph = s2g.simple_ir2goal_ir(nodes, communicators, cpu=0, nic=0)
            f.write(f"rank {rank} {{\n")
            for line in goal_graph.generate_lines():
                f.write(f"{line}\n")
            f.write("}\n")
if EMIT_INC:
    groups_path = out_goal.rsplit(".", 1)[0] + ".groups"
    with open(groups_path, "w") as gf:
        for gi in range(len(s2g._inc_group_members)):
            members = sorted({r for r in s2g._inc_group_members[gi] if r is not None})
            gf.write(" ".join(str(r) for r in members) + "\n")
    print(f"INC emit: {len(s2g._inc_group_members)} scale-up group(s) -> {groups_path}; goal -> {out_goal}")
