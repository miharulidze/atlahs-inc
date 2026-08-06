#!/usr/bin/env python3
"""Positive and fail-fast regressions for first-class INC collective validation.

Run inside the atlahs-sim container after building the simulator:
  docker run --rm -v <atlahs>:/workspace --entrypoint python3 atlahs-sim \
      /workspace/simulation-scripts/model_checks/collective_validation_test.py
"""
import gzip
import pathlib
import re
import subprocess
import sys
import tempfile

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from common import goal, paths, sim  # noqa: E402

GPUS_PER_DOMAIN = 4
SIZE = 65536
TAIL_NS = 100
MAX_MANUAL_FLOW_ID = 999_999_999
SO_TOPO = pathlib.Path(paths.TOPO_FILES_PATH) / "tree16_bw200Gbps.topo"
SU_TOPO = pathlib.Path(paths.TOPO_FILES_PATH) / "scaleup_single_switch_4_12800Gbps.topo"
SU_TOPO_2 = pathlib.Path(paths.TOPO_FILES_PATH) / "scaleup_single_switch_2_12800Gbps.topo"


def coll(kind, size=SIZE, group=0, instance=0, root=-1):
    return kind, size, group, instance, root


def block_for_ops(ops):
    if not ops:
        return ["l1: calc 1 cpu 0"]
    lines = []
    for label, (kind, size, group, instance, root) in enumerate(ops, start=1):
        lines.append(
            f"l{label}: coll {kind} {size}b {group} {instance} {root} cpu 1 nic 1"
        )
    tail = len(ops) + 1
    lines.append(f"l{tail}: calc {TAIL_NS} cpu 0")
    lines.extend(f"l{tail} requires l{label}" for label in range(1, tail))
    return lines


def write_goal(path, blocks, num_ranks):
    lines = [f"num_ranks {num_ranks}", ""]
    for rank in range(num_ranks):
        lines.append(f"rank {rank} {{")
        lines.extend(blocks.get(rank, ["l1: calc 1 cpu 0"]))
        lines.extend(("}", ""))
    path.write_text("\n".join(lines), encoding="utf-8")


def run_case(tmp, name, ops_by_rank=None, groups=("0 1 2 3",),
             num_ranks=4, blocks=None, su_topo=SU_TOPO):
    stem = tmp / name
    if blocks is None:
        blocks = {
            rank: block_for_ops(ops)
            for rank, ops in (ops_by_rank or {}).items()
        }
    write_goal(stem.with_suffix(".goal"), blocks, num_ranks)
    groups_path = stem.with_suffix(".groups")
    groups_path.write_text("\n".join(groups) + "\n", encoding="utf-8")
    binary = stem.with_suffix(".bin")
    goal.compile_goal(str(stem.with_suffix(".goal")), str(binary))
    log = stem.with_suffix(".log.gz")
    result = sim.run_sim(
        str(binary), str(SO_TOPO), str(su_topo),
        nodes=num_ranks, gpus_per_node=GPUS_PER_DOMAIN,
        groups=str(groups_path), timeout=15, save_stdout=str(log),
    )
    output = ""
    if log.exists():
        with gzip.open(log, "rt", encoding="utf-8") as stream:
            output = stream.read()
    return result[:3], output


def all_ranks(op, num_ranks=4):
    return {rank: [op] for rank in range(num_ranks)}


def expect_ok(result, output, completion, participant_ranks=range(4), count=1):
    makespan, drops, status = result
    assert status == "ok" and makespan is not None and drops == 0, result
    records = re.findall(
        rf"^{re.escape(completion)} .* complete_ns=(\d+) duration_ns=\d+$",
        output, re.MULTILINE,
    )
    assert len(records) == count, (completion, len(records), output[-3000:])
    latest_completion = max(int(value) for value in records)
    host_times = {
        int(host): int(finish)
        for host, finish in re.findall(r"^Host (\d+): (\d+)$", output, re.MULTILINE)
    }
    for rank in participant_ranks:
        assert host_times.get(rank, -1) >= latest_completion + TAIL_NS, (
            rank, host_times, latest_completion
        )
    assert makespan == max(host_times.values()), (makespan, host_times)


def expect_failure(result, output, diagnostic):
    assert result[2] != "timeout", (diagnostic, result)
    assert result[2].startswith("rc="), (diagnostic, result)
    assert diagnostic in output, (diagnostic, output[-3000:])


def reject(tmp, name, diagnostic, ops_by_rank=None, groups=("0 1 2 3",),
           num_ranks=4, blocks=None, su_topo=SU_TOPO):
    result, output = run_case(
        tmp, name, ops_by_rank=ops_by_rank, groups=groups,
        num_ranks=num_ranks, blocks=blocks, su_topo=su_topo,
    )
    expect_failure(result, output, diagnostic)


def main():
    goal.require_txt2bin()
    sim.require_simulator()
    with tempfile.TemporaryDirectory(prefix="collective-validation-") as directory:
        tmp = pathlib.Path(directory)

        valid = (
            ("bcast", 0, "BCAST_COMPLETE"),
            ("reduce", 0, "REDUCE_COMPLETE"),
            ("allreduce", -1, "ALLREDUCE_COMPLETE"),
            ("reduce_scatter", -1, "REDUCE_SCATTER_COMPLETE"),
            ("allgather", -1, "ALLGATHER_COMPLETE"),
        )
        for kind, root, completion in valid:
            result, output = run_case(
                tmp, f"valid_{kind}", all_ranks(coll(kind, root=root))
            )
            expect_ok(result, output, completion)

        # A fixed-width fabric may be only partially populated. This is the
        # canonical group/footprint-sweep geometry (P active ranks on a wider
        # topology), and it still constitutes exactly one scale-up domain.
        result, output = run_case(
            tmp,
            "valid_partial_domain",
            all_ranks(coll("allreduce"), num_ranks=2),
            groups=("0 1",),
            num_ranks=2,
        )
        expect_ok(
            result, output, "ALLREDUCE_COMPLETE", participant_ranks=(0, 1)
        )

        # Future-timed arrivals must not be mistaken for stalled operations.
        delayed_ar = {rank: block_for_ops([coll("allreduce")]) for rank in range(4)}
        delayed_ar[3] = [
            "l1: calc 1000 cpu 0",
            f"l2: coll allreduce {SIZE}b 0 0 -1 cpu 1 nic 1",
            "l2 requires l1",
            f"l3: calc {TAIL_NS} cpu 0",
            "l3 requires l2",
        ]
        result, output = run_case(tmp, "valid_delayed_allreduce", blocks=delayed_ar)
        expect_ok(result, output, "ALLREDUCE_COMPLETE")

        delayed_bcast = {rank: block_for_ops([coll("bcast", root=0)]) for rank in range(4)}
        delayed_bcast[3] = [
            "l1: calc 5000 cpu 0",
            f"l2: coll bcast {SIZE}b 0 0 0 cpu 1 nic 1",
            "l2 requires l1",
            f"l3: calc {TAIL_NS} cpu 0",
            "l3 requires l2",
        ]
        result, output = run_case(
            tmp, "valid_delayed_bcast", blocks=delayed_bcast
        )
        expect_ok(result, output, "BCAST_COMPLETE")

        sequential = {}
        for rank in range(4):
            sequential[rank] = [
                f"l1: coll allreduce {SIZE}b 0 10 -1 cpu 1 nic 1",
                f"l2: coll allreduce {SIZE}b 0 11 -1 cpu 1 nic 1",
                "l2 requires l1",
                f"l3: calc {TAIL_NS} cpu 0",
                "l3 requires l2",
            ]
        result, output = run_case(tmp, "valid_sequential_ids", blocks=sequential)
        expect_ok(result, output, "ALLREDUCE_COMPLETE", count=2)
        assert "ALLREDUCE_COMPLETE op_id=10 " in output
        assert "ALLREDUCE_COMPLETE op_id=11 " in output

        subgroup = {
            2: [coll("allreduce", group=1, instance=65536)],
            3: [coll("allreduce", group=1, instance=65536)],
        }
        result, output = run_case(
            tmp, "valid_nonzero_subgroup", subgroup,
            groups=("0 1", "2 3"),
        )
        expect_ok(result, output, "ALLREDUCE_COMPLETE", participant_ranks=(2, 3))

        result, output = run_case(
            tmp, "valid_max_manual_flow_id",
            all_ranks(coll("allreduce", instance=MAX_MANUAL_FLOW_ID)),
        )
        expect_ok(result, output, "ALLREDUCE_COMPLETE")
        assert "ALLREDUCE_COMPLETE op_id=999999999 " in output

        for label, instance in (
            ("dynamic_namespace", MAX_MANUAL_FLOW_ID + 1),
            ("uint64", 1 << 80),
        ):
            overflow_goal = tmp / f"overflow_{label}_id.goal"
            write_goal(
                overflow_goal,
                {rank: block_for_ops([coll("allreduce", instance=instance)])
                 for rank in range(4)},
                4,
            )
            compiled = subprocess.run(
                [paths.COLL_TXT2BIN, "-i", str(overflow_goal), "-o",
                 str(tmp / f"overflow_{label}_id.bin")],
                text=True, capture_output=True,
            )
            assert compiled.returncode != 0, compiled
            assert "Collective op_flow_id exceeds simulator-safe range" in compiled.stderr, (
                compiled.stderr
            )

        reject(
            tmp, "group_oob", "references group 1",
            all_ranks(coll("allreduce", group=1)),
        )
        reject(
            tmp, "not_member", "is not a member of group 0",
            {3: [coll("allreduce")]}, groups=("0 1 2",),
        )
        duplicate = all_ranks(coll("allreduce"))
        duplicate[0] = [coll("allreduce"), coll("allreduce")]
        reject(
            tmp, "duplicate_arrival", "duplicate collective arrival", duplicate,
        )
        reused_blocks = {}
        for rank in range(4):
            reused_blocks[rank] = [
                f"l1: coll allreduce {SIZE}b 0 0 -1 cpu 1 nic 1",
                f"l2: coll allreduce {SIZE}b 0 0 -1 cpu 1 nic 1",
                "l2 requires l1",
                f"l3: calc {TAIL_NS} cpu 0",
                "l3 requires l2",
            ]
        reject(
            tmp, "reused_after_completion",
            "reused after its prior operation completed", blocks=reused_blocks,
        )

        mismatch_kind = all_ranks(coll("allreduce"))
        mismatch_kind[3] = [coll("allgather")]
        reject(tmp, "mismatch_kind", "signature mismatch", mismatch_kind)
        mismatch_size = all_ranks(coll("allreduce"))
        mismatch_size[3] = [coll("allreduce", size=SIZE + 4)]
        reject(tmp, "mismatch_size", "signature mismatch", mismatch_size)
        mismatch_root = all_ranks(coll("bcast", root=0))
        mismatch_root[3] = [coll("bcast", root=1)]
        reject(tmp, "mismatch_root", "signature mismatch", mismatch_root)
        mismatch_group = all_ranks(coll("allreduce", group=0))
        mismatch_group[3] = [coll("allreduce", group=1)]
        reject(
            tmp, "mismatch_group", "signature mismatch", mismatch_group,
            groups=("0 1 2 3", "0 1 2 3"),
        )

        domain_collision = {
            0: [coll("allreduce")],
            4: [coll("allreduce")],
        }
        reject(
            tmp, "mismatch_domain", "signature mismatch", domain_collision,
            num_ranks=8,
        )
        reject(
            tmp, "rooted_without_root", "requires a global root",
            {0: [coll("bcast", root=-1)]},
        )
        reject(
            tmp, "rootless_with_root", "requires root=-1",
            {0: [coll("allreduce", root=0)]},
        )
        reject(
            tmp, "zero_size", "zero-byte INC collectives are unsupported",
            {0: [coll("allreduce", size=0)]},
        )
        reject(
            tmp, "root_not_member", "is not a member of group 0",
            {0: [coll("bcast", root=3)]}, groups=("0 1 2",),
        )
        for kind in ("reduce_scatter", "allgather"):
            reject(
                tmp, f"indivisible_{kind}", "is not divisible",
                {0: [coll(kind, size=SIZE + 1)]},
            )

        reject(
            tmp, "singleton_group", "at least two are required",
            {0: [coll("allreduce")]}, groups=("0",),
        )
        reject(
            tmp, "duplicate_group_member", "contains duplicate node-local host 1",
            {0: [coll("allreduce")]}, groups=("0 1 1 2",),
        )
        reject(
            tmp, "out_of_range_group_member", "outside node-local host range [0,4)",
            {0: [coll("allreduce")]}, groups=("0 1 2 4",),
        )
        reject(
            tmp, "malformed_group_token", "malformed integer token 'bad'",
            {0: [coll("allreduce")]}, groups=("0 1 bad 2",),
        )
        reject(
            tmp, "overflow_group_token", "malformed integer token",
            {0: [coll("allreduce")]},
            groups=("0 1 999999999999999999999999",),
        )
        reject(
            tmp, "empty_group_line", "empty group in -groups line 1",
            {0: [coll("allreduce")]}, groups=("",),
        )
        reject(
            tmp, "topology_domain_mismatch", "scale-up topology contains 2 hosts",
            all_ranks(coll("allreduce")), su_topo=SU_TOPO_2,
        )

        reject(
            tmp, "incomplete_bcast", "arrived=1/4",
            {0: [coll("bcast", root=0)]},
        )
        incomplete_ar = {rank: [coll("allreduce")] for rank in range(3)}
        reject(
            tmp, "incomplete_allreduce", "arrived=3/4", incomplete_ar,
        )

    print(
        "PASS: collective validation accepts valid, partial-domain, and delayed "
        "operations and rejects malformed or incomplete traces"
    )


if __name__ == "__main__":
    main()
