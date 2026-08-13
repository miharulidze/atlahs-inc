"""GOAL synthesis + compilation helpers shared by the experiments.

Two writers, matching the frozen scale-up-baseline A/B design:
  * gen_baseline_goal — endpoint arm via the GENERATOR's OWN decomposition
    (goal_gen/ai/nccl_generator_v2/communication.py; Ring / Recursive-doubling
    per Demystifying-NCCL Tables V-VII).  The AllReduce ``tree`` and ``bine``
    arms are explicit exceptions: the pinned generator exposes a TREE enum but
    does not implement it in communication.py, so this module emits the
    textbook binomial Reduce + Broadcast and Bine-butterfly schedules directly.
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
    """Generator-backed baseline name ('ring' | 'rdouble') -> CollAlgo."""
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
    if algo_name == "tree":
        if collective != "allreduce":
            raise ValueError("the binomial-tree endpoint writer supports AllReduce only")
        gen_binomial_tree_allreduce_goal(path, n, size, tail_ns)
        return
    if algo_name == "bine":
        if collective != "allreduce":
            raise ValueError("the Bine endpoint writer supports AllReduce only")
        gen_bine_allreduce_goal(path, n, size, tail_ns)
        return

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


def gen_binomial_tree_allreduce_goal(path, n, size, tail_ns):
    """Emit a traffic-only, full-buffer binomial-tree AllReduce endpoint schedule.

    The reduction is a binomial fan-in rooted at rank 0, followed by the reverse
    binomial broadcast.  Every tree edge carries the full ``size`` buffer once in
    each direction, so this deliberately realizes the tree row in Table I rather
    than the bandwidth-optimal recursive-halving/doubling decomposition.  As with
    the other endpoint arms, reduction arithmetic is not charged; ``tail_ns`` is
    the identical final dependent calculation used in the A/B harness.

    Only power-of-two communicators are accepted.  This keeps every rank's
    round/partner relationship symmetric with the paper's 2 log2(N) step model
    and with the recursive-doubling baseline's existing precondition.
    """
    if n < 2 or n & (n - 1):
        raise ValueError(f"binomial-tree AllReduce needs a power-of-two N >= 2; got {n}")
    if size <= 0:
        raise ValueError(f"binomial-tree AllReduce needs a positive size; got {size}")

    stages = n.bit_length() - 1

    def tag(stage, phase):
        # Match the generator's 6-digit message-id + 3-digit context tag layout.
        # Each source/destination pair appears once per phase, so stage is enough
        # to distinguish these direct point-to-point operations.
        return f"{stage:06d}{phase:03d}"

    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for rank in range(n):
            actions = []

            # Binomial reduce: at stage i the lower member of each 2^(i+1)
            # block receives the full partial result from its upper partner.
            for stage in range(stages):
                mask = 1 << stage
                residue = rank % (2 * mask)
                if residue == 0:
                    actions.append(("recv", rank + mask, stage, 0))
                elif residue == mask:
                    actions.append(("send", rank - mask, stage, 0))

            # Reverse the same tree for broadcast.  A rank which sent during the
            # reduce waits here until its parent has received the completed result.
            for stage in range(stages - 1, -1, -1):
                mask = 1 << stage
                residue = rank % (2 * mask)
                if residue == 0:
                    actions.append(("send", rank + mask, stage, 1))
                elif residue == mask:
                    actions.append(("recv", rank - mask, stage, 1))

            f.write(f"rank {rank} {{\n")
            previous = None
            label = 1
            for op, peer, stage, phase in actions:
                direction = f"to {peer}" if op == "send" else f"from {peer}"
                f.write(f"l{label}: {op} {size}b {direction} tag {tag(stage, phase)} cpu 0 nic 0\n")
                if previous is not None:
                    f.write(f"l{label} requires l{previous}\n")
                previous = label
                label += 1
            f.write(f"l{label}: calc {tail_ns} cpu 0\n")
            if previous is not None:
                f.write(f"l{label} requires l{previous}\n")
            f.write("}\n\n")


def _bine_partner(rank, stage, n):
    """Bine's negabinary butterfly partner π(rank, stage).

    The published Bine implementation uses the signed offsets
    1, -1, 3, -5, 11, ... .  Their closed form is
    ``rho(stage) = (1 - (-2) ** (stage + 1)) / 3``.  Applying rho to even
    ranks and -rho to odd ranks makes every stage a perfect matching.
    """
    rho = (1 - (-2) ** (stage + 1)) // 3
    return (rank + rho if rank % 2 == 0 else rank - rho) % n


def gen_bine_allreduce_goal(path, n, size, tail_ns):
    """Emit Bine's bandwidth-oriented AllReduce butterfly.

    Bine replaces the XOR partners of recursive halving/doubling with its
    negabinary ``π`` partners, which preserves locality under a suitable rank
    placement.  The data movement is otherwise the same bandwidth-optimal
    ReduceScatter + AllGather: S/2, S/4, ..., S/N bytes, then the reverse.
    GOAL models dependencies and byte volumes, not buffer offsets, so its trace
    exactly captures the packet-level schedule relevant to this simulator.

    This follows ``allreduce_bine_bdw_remap`` in HLC-Lab/pico.  It is called a
    Bine butterfly in that implementation (rather than a rooted full-buffer
    tree), but is the Bine family member appropriate for AllReduce.
    """
    if n < 2 or n & (n - 1):
        raise ValueError(f"Bine AllReduce needs a power-of-two N >= 2; got {n}")
    if size <= 0 or size % n:
        raise ValueError(f"Bine AllReduce size must be positive and divisible by N={n}; got {size}")

    stages = n.bit_length() - 1

    def tag(stage, phase):
        return f"{stage:06d}{phase:03d}"

    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for rank in range(n):
            f.write(f"rank {rank} {{\n")
            previous = None
            label = 0
            # The reduce-scatter and allgather rounds are explicit send/recv
            # parallel pairs, followed by a join, just like the generator's
            # GoalParallel rendering for recursive doubling.
            rounds = [(stage, 0, size >> (stage + 1)) for stage in range(stages)]
            rounds += [(stage, 1, size >> (stage + 1))
                       for stage in range(stages - 1, -1, -1)]
            for stage, phase, chunk in rounds:
                peer = _bine_partner(rank, stage, n)
                gate, recv, send, join = label, label + 1, label + 2, label + 3
                f.write(f"l{gate}: calc 0 cpu 0\n")
                if previous is not None:
                    f.write(f"l{gate} requires l{previous}\n")
                f.write(f"l{recv}: recv {chunk}b from {peer} tag {tag(stage, phase)} cpu 0 nic 0\n")
                f.write(f"l{recv} requires l{gate}\n")
                f.write(f"l{send}: send {chunk}b to {peer} tag {tag(stage, phase)} cpu 0 nic 0\n")
                f.write(f"l{send} requires l{gate}\n")
                f.write(f"l{join}: calc 0 cpu 0\n")
                f.write(f"l{join} requires l{recv}\n")
                f.write(f"l{join} requires l{send}\n")
                previous, label = join, label + 4
            f.write(f"l{label}: calc {tail_ns} cpu 0\n")
            f.write(f"l{label} requires l{previous}\n")
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


def gen_multigroup_inc_goal(path, groups_path, num_ranks, groups, size, kind, tail_ns):
    """PFC/backpressure trace: N DISJOINT concurrent INC collectives, one per group.

    `groups` is a list of member-rank lists (one per group); the groups must be disjoint.
    Each group g runs an independent rootless `coll <kind>` op (group index g into the
    .groups sidecar; op_flow_id g so the N collectives stay independent/concurrent). Ranks
    in no group emit a lone tail `calc` (idle filler keeping the rank space dense over
    0..num_ranks-1; a filler finishes ~tail_ns, far below any collective, so the makespan
    stays = the slowest group). Used by scaleup_pfc_concurrent to show PFC serialisation
    when all trees are pinned to one core (-mcast_pin 0) vs. spread round-robin.

    Rootless kinds only (allreduce/reduce_scatter/allgather): the disjoint-group core-
    contention story is about the multicast/aggregation fan-out, not a rooted delivery."""
    _, gen_goal = _generator()
    KINDS = gen_goal.GoalCollective.KINDS
    assert kind in KINDS and not KINDS[kind], (
        f"gen_multigroup_inc_goal needs a rootless coll kind; got {kind!r}")
    rank_group = {}
    for gi, members in enumerate(groups):
        for r in members:
            assert 0 <= r < num_ranks, f"group {gi} member {r} out of range [0,{num_ranks})"
            assert r not in rank_group, (f"groups must be DISJOINT (rank {r} in groups "
                                         f"{rank_group[r]} and {gi})")
            rank_group[r] = gi
    with open(path, "w") as f:
        f.write(f"num_ranks {num_ranks}\n\n")
        for r in range(num_ranks):
            f.write(f"rank {r} {{\n")
            gi = rank_group.get(r)
            if gi is not None:
                f.write(f"l1: coll {kind} {size}b {gi} {gi} -1 cpu 1 nic 1\n")
                f.write(f"l2: calc {tail_ns} cpu 0\n")
                f.write("l2 requires l1\n")
            else:
                f.write(f"l1: calc {tail_ns} cpu 0\n")   # idle filler (non-empty rank block)
            f.write("}\n\n")
    with open(groups_path, "w") as f:
        for members in groups:
            f.write(" ".join(str(r) for r in members) + "\n")


def gen_hierarchical_inc_allreduce_goal(path, groups_path, n, gpus_per_node, size,
                                        global_algo, tail_ns):
    """Emit a semantically complete multi-domain hierarchical AllReduce.

    The INC datapath deliberately accepts *node-local* groups only: a single
    global ``coll allreduce`` cannot cross scale-up domains.  This composition
    uses its supported primitives where they apply instead:

      1. local INC ReduceScatter across each scale-up domain;
      2. an endpoint Bine/RD AllReduce across domains for every local-rank
         lane, in parallel; and
      3. local INC AllGather across each scale-up domain.

    The local group sidecar is installed identically on every domain.  Each
    domain receives unique ``op_flow_id`` values because the simulator's
    collective state is trace-global even though the FIB/sinks are per domain.
    ``global_algo`` is currently ``rdouble`` or Bine's negabinary butterfly.
    """
    if n < 2 or n & (n - 1):
        raise ValueError(f"hierarchical INC AllReduce needs power-of-two N >= 2; got {n}")
    if gpus_per_node < 2 or gpus_per_node & (gpus_per_node - 1):
        raise ValueError(f"gpus_per_node must be a power of two >= 2; got {gpus_per_node}")
    if n % gpus_per_node:
        raise ValueError(f"N={n} must be divisible by gpus_per_node={gpus_per_node}")
    if size <= 0 or size % n:
        raise ValueError(f"size must be positive and divisible by N={n}; got {size}")
    if global_algo not in ("rdouble", "bine"):
        raise ValueError(f"unsupported hierarchical global algorithm {global_algo!r}")

    domains = n // gpus_per_node
    stages = domains.bit_length() - 1
    lane_bytes = size // gpus_per_node

    def peer_index(index, stage):
        return (index ^ (1 << stage) if global_algo == "rdouble"
                else _bine_partner(index, stage, domains))

    def tag(stage, phase):
        # Keep P2P tags distinct from the small coll-op IDs and stable between
        # matching lane send/recv pairs.
        return f"9{stage:05d}{phase:03d}"

    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for rank in range(n):
            domain, local_rank = divmod(rank, gpus_per_node)
            local_rs_id = domain
            local_ag_id = domains + domain
            label = 1
            f.write(f"rank {rank} {{\n")
            # Keep this trace's declared LogGOPS NIC count at one.  A ``nic 1``
            # coll token makes the legacy runtime remap the later scale-out
            # lane P2P ranks as ``rank * 2 + nic`` and sends them beyond the
            # 64-endpoint topology; the INC packet path has its own real NIC.
            f.write(f"l{label}: coll reduce_scatter {size}b 0 {local_rs_id} -1 cpu 1 nic 0\n")
            previous = label
            label += 1

            # Lane-parallel global bandwidth-optimal ReduceScatter + AllGather:
            # rank local_rank in every domain carries the same S/gpus_per_node
            # shard, so all GPUs keep using their scale-out NICs.
            rounds = [(stage, 0, lane_bytes >> (stage + 1)) for stage in range(stages)]
            rounds += [(stage, 1, lane_bytes >> (stage + 1))
                       for stage in range(stages - 1, -1, -1)]
            for stage, phase, chunk in rounds:
                peer = peer_index(domain, stage) * gpus_per_node + local_rank
                gate, recv, send, join = label, label + 1, label + 2, label + 3
                f.write(f"l{gate}: calc 0 cpu 0\n")
                f.write(f"l{gate} requires l{previous}\n")
                f.write(f"l{recv}: recv {chunk}b from {peer} tag {tag(stage, phase)} cpu 0 nic 0\n")
                f.write(f"l{recv} requires l{gate}\n")
                f.write(f"l{send}: send {chunk}b to {peer} tag {tag(stage, phase)} cpu 0 nic 0\n")
                f.write(f"l{send} requires l{gate}\n")
                f.write(f"l{join}: calc 0 cpu 0\n")
                f.write(f"l{join} requires l{recv}\n")
                f.write(f"l{join} requires l{send}\n")
                previous, label = join, label + 4

            f.write(f"l{label}: coll allgather {size}b 0 {local_ag_id} -1 cpu 1 nic 0\n")
            f.write(f"l{label} requires l{previous}\n")
            f.write(f"l{label + 1}: calc {tail_ns} cpu 0\n")
            f.write(f"l{label + 1} requires l{label}\n")
            f.write("}\n\n")
    with open(groups_path, "w") as f:
        f.write(" ".join(str(rank) for rank in range(gpus_per_node)) + "\n")


def gen_naive_hierarchical_inc_allreduce_goal(path, groups_path, n, gpus_per_node, size,
                                              global_algo, tail_ns):
    """Emit the direct two-level composition of the existing local INC primitive.

    First run the existing ``coll allreduce`` independently in every scale-up
    domain.  Then every matching local-rank lane performs a *full-buffer*
    endpoint AllReduce across scale-out domains.  The composition is correct,
    but intentionally naive: all ``gpus_per_node`` lanes ship the entire S-byte
    result over scale-out.  In contrast,
    :func:`gen_hierarchical_inc_allreduce_goal` uses local ReduceScatter and
    AllGather so every lane ships only S/gpus_per_node.

    This is the closest valid multi-domain version of the pre-existing remote
    local INC path.  A literal 64-rank ``coll allreduce`` is invalid because
    the PCM INC groups and FIBs are deliberately node-local.
    """
    if n < 2 or n & (n - 1):
        raise ValueError(f"naive hierarchical INC AllReduce needs power-of-two N >= 2; got {n}")
    if gpus_per_node < 2 or gpus_per_node & (gpus_per_node - 1):
        raise ValueError(f"gpus_per_node must be a power of two >= 2; got {gpus_per_node}")
    if n % gpus_per_node:
        raise ValueError(f"N={n} must be divisible by gpus_per_node={gpus_per_node}")
    if size <= 0 or size % (n // gpus_per_node):
        raise ValueError(f"size must be positive and divisible by domain count; got {size}")
    if global_algo not in ("rdouble", "bine"):
        raise ValueError(f"unsupported naive global algorithm {global_algo!r}")

    domains = n // gpus_per_node
    stages = domains.bit_length() - 1

    def peer_index(index, stage):
        return (index ^ (1 << stage) if global_algo == "rdouble"
                else _bine_partner(index, stage, domains))

    def tag(stage, phase):
        return f"8{stage:05d}{phase:03d}"

    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for rank in range(n):
            domain, local_rank = divmod(rank, gpus_per_node)
            local_ar_id = domain
            label = 1
            f.write(f"rank {rank} {{\n")
            # nic 0 preserves the trace's one-NIC P2P rank mapping; the INC
            # implementation routes through its own scale-up NIC object.
            f.write(f"l{label}: coll allreduce {size}b 0 {local_ar_id} -1 cpu 1 nic 0\n")
            previous, label = label, label + 1
            rounds = [(stage, 0, size >> (stage + 1)) for stage in range(stages)]
            rounds += [(stage, 1, size >> (stage + 1))
                       for stage in range(stages - 1, -1, -1)]
            for stage, phase, chunk in rounds:
                peer = peer_index(domain, stage) * gpus_per_node + local_rank
                gate, recv, send, join = label, label + 1, label + 2, label + 3
                f.write(f"l{gate}: calc 0 cpu 0\n")
                f.write(f"l{gate} requires l{previous}\n")
                f.write(f"l{recv}: recv {chunk}b from {peer} tag {tag(stage, phase)} cpu 0 nic 0\n")
                f.write(f"l{recv} requires l{gate}\n")
                f.write(f"l{send}: send {chunk}b to {peer} tag {tag(stage, phase)} cpu 0 nic 0\n")
                f.write(f"l{send} requires l{gate}\n")
                f.write(f"l{join}: calc 0 cpu 0\n")
                f.write(f"l{join} requires l{recv}\n")
                f.write(f"l{join} requires l{send}\n")
                previous, label = join, label + 4
            f.write(f"l{label}: calc {tail_ns} cpu 0\n")
            f.write(f"l{label} requires l{previous}\n")
            f.write("}\n\n")
    with open(groups_path, "w") as f:
        f.write(" ".join(str(rank) for rank in range(gpus_per_node)) + "\n")


def expected_steps(collective, n, algo_name):
    """Busiest rank's endpoint-step count.  ``count_steps`` measures sends, which
    is equal to this quantity for the symmetric Ring/recursive-doubling schedules;
    the binomial tree instead uses ``count_transfer_steps`` because its root
    receives during fan-in and sends during fan-out.  Rootless ring = n-1;
    recursive doubling, the Bine butterfly, and the full-buffer binomial tree =
    2*log2(n) for AllReduce. The rooted pipelined-ring
    Broadcast/Reduce chop the message into K = max(n, size//SEG_BYTES) chunks streamed
    along the chain (communication.py), so the busiest sender -- root (bcast) / tail
    (reduce) -- emits all K. That count is SIZE-dependent, so the n returned here is only
    the small-message case (K == n); callers compile-check those two instead of
    comparing counts (see experiments/scaleup_coll_ab/run.py)."""
    if collective == "allreduce":
        return 2 * (n.bit_length() - 1) if algo_name in ("rdouble", "tree", "bine") else 2 * (n - 1)
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


def count_transfer_steps(goal_path):
    """Busiest rank's send-or-recv count.

    This is the dependent-transfer depth of the direct binomial-tree AllReduce:
    the root receives log2(N) reduction messages and then sends log2(N) broadcast
    messages.  It intentionally differs from ``count_steps`` (send-only), whose
    historical definition is retained for validating the generator schedules.
    """
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
            elif cur is not None and re.search(r":\s*(send|recv) ", s):
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
