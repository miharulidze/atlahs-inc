#!/usr/bin/env python3
"""Regression for global GOAL roots on nonzero scale-up domains.

Run inside the atlahs-sim container after building the simulator:
  docker run --rm -v <atlahs>:/workspace --entrypoint python3 atlahs-sim \
      /workspace/simulation-scripts/model_checks/rooted_domain_localization_test.py
"""
import gzip
import pathlib
import re
import sys
import tempfile

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from common import goal, paths, sim  # noqa: E402

NUM_RANKS = 8
GPUS_PER_DOMAIN = 4
SIZE = 65536
TAIL_NS = 100
SO_TOPO = pathlib.Path(paths.TOPO_FILES_PATH) / "tree16_bw200Gbps.topo"
SU_TOPO = pathlib.Path(paths.TOPO_FILES_PATH) / "scaleup_single_switch_4_12800Gbps.topo"


def write_goal(path, kind, members, root):
    lines = [f"num_ranks {NUM_RANKS}", ""]
    member_set = set(members)
    for rank in range(NUM_RANKS):
        lines.append(f"rank {rank} {{")
        if rank in member_set:
            lines.append(
                f"l1: coll {kind} {SIZE}b 0 0 {root} cpu 1 nic 1"
            )
            lines.append(f"l2: calc {TAIL_NS} cpu 0")
            lines.append("l2 requires l1")
        else:
            lines.append("l1: calc 1 cpu 0")
        lines.extend(("}", ""))
    path.write_text("\n".join(lines), encoding="utf-8")


def run_case(tmp, kind, domain, root):
    stem = tmp / f"{kind}_d{domain}_r{root}"
    members = range(domain * GPUS_PER_DOMAIN, (domain + 1) * GPUS_PER_DOMAIN)
    write_goal(stem.with_suffix(".goal"), kind, members, root)
    groups = stem.with_suffix(".groups")
    groups.write_text("0 1 2 3\n", encoding="utf-8")
    binary = stem.with_suffix(".bin")
    goal.compile_goal(str(stem.with_suffix(".goal")), str(binary))
    log = stem.with_suffix(".log.gz")
    makespan, drops, status, _, _ = sim.run_sim(
        str(binary), str(SO_TOPO), str(SU_TOPO),
        nodes=NUM_RANKS, gpus_per_node=GPUS_PER_DOMAIN,
        groups=str(groups), timeout=60, save_stdout=str(log),
    )
    with gzip.open(log, "rt", encoding="utf-8") as stream:
        output = stream.read()
    return makespan, drops, status, output


def completion_duration(kind, output, root):
    match = re.search(
        rf"^{kind.upper()}_COMPLETE .* root={root} .* duration_ns=(\d+)$",
        output, re.MULTILINE,
    )
    assert match, f"missing {kind} completion with global root {root}\n{output[-2000:]}"
    return int(match.group(1))


def main():
    goal.require_txt2bin()
    sim.require_simulator()
    with tempfile.TemporaryDirectory(prefix="rooted-domain-") as directory:
        tmp = pathlib.Path(directory)
        for kind in ("bcast", "reduce"):
            domain0 = run_case(tmp, kind, domain=0, root=2)
            domain1 = run_case(tmp, kind, domain=1, root=6)
            for result in (domain0, domain1):
                makespan, drops, status, _ = result
                assert status == "ok" and makespan is not None and drops == 0, result[:3]
            duration0 = completion_duration(kind, domain0[3], root=2)
            duration1 = completion_duration(kind, domain1[3], root=6)
            assert duration0 == duration1, (kind, duration0, duration1)
            assert domain0[0] == domain1[0], (kind, domain0[0], domain1[0])

        invalid = run_case(tmp, "bcast", domain=1, root=2)
        assert invalid[2] != "ok", invalid[:3]
        assert "global root 2 in scale-up domain 0" in invalid[3], invalid[3][-2000:]

    print("PASS: rooted Broadcast/Reduce localize global roots per scale-up domain")


if __name__ == "__main__":
    main()
