"""GOAL synthesis + compilation helpers shared by the experiments.

Two writers, matching the AA-plan-Scaleup-Baselines A/B design:
  * gen_baseline_goal — endpoint arm via the GENERATOR's OWN decomposition
    (goal_gen/ai/nccl_generator_v2/communication.py; Ring / Recursive-doubling
    per Demystifying-NCCL Tables V-VII). NOT hand-rolled.
  * gen_inc_goal — INC arm: one rootless first-class `coll <kind>` op per rank
    + a .groups sidecar.

The generator import is lazy so `common` stays importable without the
generator materialized; the first writer call fails loudly if it is missing.
"""
import os
import re
import subprocess
import sys

from common import paths

_GEN = None  # (communication, goal) module pair, cached after first import

# Exact symbols the writers below rely on. Validated at import so a wrong-version
# generator (module imports but is missing a symbol — the known submodule-
# provenance landmine) fails with the actionable message, not a raw AttributeError
# deep in a writer call.
_REQ_COMM = ("Communicator", "CollDevice", "AllReduce", "ReduceScatter", "AllGather", "CollAlgo")
_REQ_GOAL = ("GoalOpAtom", "GoalSend", "GoalRecv", "GoalCalc", "GoalCollective")


def _generator():
    global _GEN
    if _GEN is None:
        # Force index 0 (matching the original's unconditional insert): GENERATOR_DIR
        # must win over any earlier sys.path entry that also ships a goal/communication.
        sys.path.insert(0, paths.GENERATOR_DIR)
        try:
            import communication
            import goal as gen_goal  # the generator's goal.py, not this module
            missing = ([f"communication.{n}" for n in _REQ_COMM if not hasattr(communication, n)]
                       + [f"goal.{n}" for n in _REQ_GOAL if not hasattr(gen_goal, n)])
            if missing:
                raise ImportError("missing symbols: " + ", ".join(missing))
        except ImportError as e:
            sys.exit(f"cannot import the generator decomposition from {paths.GENERATOR_DIR}: {e}\n"
                     "Set GENERATOR_DIR (default <workspace>/goal_gen/ai/nccl_generator_v2).")
        _GEN = (communication, gen_goal)
    return _GEN


def require_generator():
    """Loud preflight: the NCCL->GOAL generator must import with every symbol the
    writers use. Call before touching the filesystem so a broken GENERATOR_DIR
    fails cleanly instead of leaving a header-only CSV behind."""
    _generator()


def coll_classes():
    """kind -> generator collective class, for the kinds the INC datapath supports."""
    communication, gen_goal = _generator()
    classes = {"allreduce": communication.AllReduce,
               "reduce_scatter": communication.ReduceScatter,
               "allgather": communication.AllGather}
    for k in classes:
        # TODO guardrail: kinds must be first-class, argument-free GOAL collectives.
        assert k in gen_goal.GoalCollective.KINDS and not gen_goal.GoalCollective.KINDS[k], k
    return classes


def algo(name):
    """Baseline algorithm name ('ring' | 'rdouble') -> generator CollAlgo."""
    communication, _ = _generator()
    return {"ring": communication.CollAlgo.RING,
            "rdouble": communication.CollAlgo.RECURSIVE_DOUBLING}[name]


def _reset_goal_state():
    _, gen_goal = _generator()
    gen_goal.GoalOpAtom.task_id_for_rank.clear()
    gen_goal.GoalSend.send_message_id.clear()
    gen_goal.GoalRecv.recv_message_id.clear()


def gen_baseline_goal(path, n, size, collective, algo_name, tail_ns):
    """Endpoint baseline via the generator's OWN decomposition. Flush-left labels
    (txt2bin rejects indented `l0:`); no reduction calc (charge-neither); each
    rank ends in a dependent `calc tail_ns` so both arms share the same tail."""
    communication, gen_goal = _generator()
    _reset_goal_state()
    devices = [communication.CollDevice(("gpu", i)) for i in range(n)]
    comm = communication.Communicator(devices, "scaleup_ab")
    device2goal_rank = {d: i for i, d in enumerate(devices)}
    op = coll_classes()[collective](comm, size=size, context=0, algo=algo(algo_name))
    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for device, rank in device2goal_rank.items():
            with device:
                coll_goal = op.to_goal(device2goal_rank, starting_cpu_id=0, nic=0)
                coll_lines = list(coll_goal.generate_lines())
                end_id = coll_goal.get_end_id()
                tail = gen_goal.GoalCalc(tail_ns, self_rank=rank, cpu=0)
                tail_lines = list(tail.generate_lines())
                tail_start = tail.get_start_id()
            f.write(f"rank {rank} {{\n")
            for ln in coll_lines:
                f.write(f"{ln}\n")
            for ln in tail_lines:
                f.write(f"{ln}\n")
            f.write(f"l{tail_start} requires l{end_id}\n")
            f.write("}\n\n")


def gen_inc_goal(path, groups_path, n, size, kind, tail_ns):
    """INC arm: one rootless first-class `coll <kind>` per rank + .groups sidecar."""
    _, gen_goal = _generator()
    # TODO guardrail: refuse kinds the coll grammar / INC datapath doesn't know.
    assert kind in gen_goal.GoalCollective.KINDS and not gen_goal.GoalCollective.KINDS[kind]
    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for r in range(n):
            f.write(f"rank {r} {{\n")
            f.write(f"l1: coll {kind} {size}b 0 0 -1 cpu 1 nic 1\n")
            f.write(f"l2: calc {tail_ns} cpu 0\n")
            f.write("l2 requires l1\n")
            f.write("}\n\n")
    with open(groups_path, "w") as f:
        f.write(" ".join(str(i) for i in range(n)) + "\n")


def expected_steps(collective, n, algo_name):
    """Parallel send/recv rounds per rank (Demystifying-NCCL Tables V-VII)."""
    if collective == "allreduce":
        return 2 * (n.bit_length() - 1) if algo_name == "rdouble" else 2 * (n - 1)
    return n - 1


def count_steps(goal_path):
    """Count rank 0's send ops in a .goal text file (= its send/recv rounds)."""
    sends, in_rank0 = 0, False
    with open(goal_path) as f:
        for line in f:
            s = line.strip()
            if s.startswith("rank 0 {"):
                in_rank0 = True
            elif in_rank0 and s == "}":
                break
            elif in_rank0 and re.search(r":\s*send ", s):
                sends += 1
    return sends


def compile_goal(goal_path, binout):
    """Compile .goal text to the canonical LogGOPSim .bin via the coll txt2bin."""
    r = subprocess.run([paths.COLL_TXT2BIN, "-i", goal_path, "-o", binout],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"txt2bin failed on {goal_path}: {r.stderr[-300:]}")


def require_txt2bin():
    """Loud preflight: the coll-extended txt2bin must be built and executable."""
    if not (os.path.isfile(paths.COLL_TXT2BIN) and os.access(paths.COLL_TXT2BIN, os.X_OK)):
        sys.exit(f"coll txt2bin not executable: {paths.COLL_TXT2BIN}\n"
                 "(build with `build` in the container, or set COLL_TXT2BIN locally)")
