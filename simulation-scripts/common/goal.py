"""GOAL synthesis + compilation helpers shared by the experiments.

Two writers, matching the AA-plan-Scaleup-Baselines A/B design:
  * gen_baseline_goal — endpoint arm via the GENERATOR's OWN decomposition
    (goal_gen/ai/nccl_generator_v2/communication.py; Ring / Recursive-doubling
    per Demystifying-NCCL Tables V-VII). NOT hand-rolled.
  * gen_inc_goal — INC arm: one first-class `coll <kind>` op per rank + a .groups
    sidecar; rooted (bcast/reduce) or rootless (allreduce/reduce_scatter/allgather).

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
_REQ_COMM = ("Communicator", "CollDevice", "AllReduce", "ReduceScatter", "AllGather",
             "Broadcast", "Reduce", "CollAlgo")
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
    """kind -> generator collective class (the endpoint decomposition). Includes the two
    rooted collectives bcast/reduce, whose rooted pipelined-ring decomposition drives the
    Broadcast/Reduce baseline arms; their comm-local `root` is supplied by gen_baseline_goal
    (default 0). Rootless kinds (allreduce/reduce_scatter/allgather) take no root."""
    communication, gen_goal = _generator()
    classes = {"allreduce": communication.AllReduce,
               "reduce_scatter": communication.ReduceScatter,
               "allgather": communication.AllGather,
               "bcast": communication.Broadcast,
               "reduce": communication.Reduce}
    for k in classes:
        # TODO guardrail: every baseline kind must be a known coll-grammar kind.
        assert k in gen_goal.GoalCollective.KINDS, k
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


def gen_baseline_goal(path, n, size, collective, algo_name, tail_ns, root=0):
    """Endpoint baseline via the generator's OWN decomposition. Flush-left labels
    (txt2bin rejects indented `l0:`); no reduction calc (charge-neither); each
    rank ends in a dependent `calc tail_ns` so both arms share the same tail.
    `root` (comm-local rank) is used only by the rooted collectives (bcast/reduce);
    it is ignored for the rootless AllReduce/ReduceScatter/AllGather."""
    communication, gen_goal = _generator()
    _reset_goal_state()
    devices = [communication.CollDevice(("gpu", i)) for i in range(n)]
    comm = communication.Communicator(devices, "scaleup_ab")
    device2goal_rank = {d: i for i, d in enumerate(devices)}
    kwargs = dict(size=size, context=0, algo=algo(algo_name))
    if collective in ("bcast", "reduce"):   # rooted collectives take a comm-local root
        kwargs["root"] = root
    op = coll_classes()[collective](comm, **kwargs)
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


def gen_inc_goal(path, groups_path, n, size, kind, tail_ns, root=-1):
    """INC arm: one first-class `coll` op per rank (+ .groups sidecar); rooted for
    bcast/reduce (root = a GLOBAL goal rank >= 0), rootless (root = -1) otherwise.

    `kind` is a coll-grammar kind (bcast/reduce/reduce_scatter/allgather/allreduce),
    OR the pseudo-kind "allreduce_rs_ag": an NVLS-style AllReduce COMPOSED from a
    ReduceScatter followed by a dependent AllGather, emitted as two coll ops (same
    group, distinct op_flow_id instances 0 and 1). This is a trace-gen-time
    decomposition -- the engine runs it as a standalone RS then a standalone AG,
    with no engine changes; the apex `allreduce` primitive is left untouched.

    coll-line grammar (generator goal.py:213): `coll <kind> <size>b <group>
    <instance> <root> cpu <c> nic <n>`; group 0 indexes the single .groups line,
    root -1 = rootless (ANYSOURCE), a rooted kind carries the root's global goal rank,
    instance = op_flow_id (shared across a collective's ranks)."""
    _, gen_goal = _generator()
    KINDS = gen_goal.GoalCollective.KINDS
    # TODO guardrail: refuse kinds the coll grammar / INC datapath doesn't know.
    if kind == "allreduce_rs_ag":
        for k in ("reduce_scatter", "allgather"):
            assert k in KINDS and not KINDS[k], f"composite AR needs rootless {k}"
        body = [f"l1: coll reduce_scatter {size}b 0 0 -1 cpu 1 nic 1",
                f"l2: coll allgather {size}b 0 1 -1 cpu 1 nic 1",
                f"l3: calc {tail_ns} cpu 0",
                "l2 requires l1",   # AllGather waits for the ReduceScatter to finish
                "l3 requires l2"]   # tail calc waits for the AllGather
    else:
        assert kind in KINDS, f"unknown coll kind {kind!r}"
        if KINDS[kind]:                       # rooted (bcast/reduce): root is a GLOBAL goal rank
            assert 0 <= root < n, (f"rooted kind {kind!r} root must be a group member in "
                                   f"[0, {n}) (global goal rank); got {root}")
            root_field = root
        else:                                 # rootless: -1 == ANYSOURCE
            assert root == -1, f"rootless kind {kind!r} needs root=-1; got {root}"
            root_field = -1
        body = [f"l1: coll {kind} {size}b 0 0 {root_field} cpu 1 nic 1",
                f"l2: calc {tail_ns} cpu 0",
                "l2 requires l1"]
    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for r in range(n):
            f.write(f"rank {r} {{\n")
            for ln in body:
                f.write(ln + "\n")
            f.write("}\n\n")
    with open(groups_path, "w") as f:
        f.write(" ".join(str(i) for i in range(n)) + "\n")


def expected_steps(collective, n, algo_name):
    """Busiest rank's send-op count (== count_steps). Rootless ring = n-1, rdouble =
    2*log2(n) or 2*(n-1) per Demystifying-NCCL Tables V-VII. The rooted pipelined-ring
    Broadcast/Reduce split the message into n chunks streamed along the chain, so the
    root (bcast) / tail (reduce) -- the busiest sender -- emits all n."""
    if collective == "allreduce":
        return 2 * (n.bit_length() - 1) if algo_name == "rdouble" else 2 * (n - 1)
    if collective in ("bcast", "reduce"):
        return n
    return n - 1


def count_steps(goal_path):
    """Busiest rank's send-op count in a .goal text file. Every rank sends the same
    number for the symmetric collectives (so this equals the old 'rank 0 sends'); for
    the rooted Broadcast/Reduce the root is NOT the busiest sender, so take the max
    over all ranks."""
    per_rank = {}
    cur = None
    with open(goal_path) as f:
        for line in f:
            s = line.strip()
            m = re.match(r"rank (\d+)\s*\{", s)
            if m:
                cur = int(m.group(1))
                per_rank.setdefault(cur, 0)
            elif s == "}":
                cur = None
            elif cur is not None and re.search(r":\s*send ", s):
                per_rank[cur] += 1
    return max(per_rank.values()) if per_rank else 0


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
