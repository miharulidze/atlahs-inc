#!/usr/bin/env python3
"""Functional tests for the ACK-less broadcast path.

Each test case:
  - points at a .cm file in this directory
  - declares the expected simulator exit code
  - declares a list of regex patterns that MUST appear on stdout
  - optionally declares patterns that MUST NOT appear on stdout
  - optionally declares the exact expected number of BCAST_COMPLETE
    lines, and the list of leg counts in some order

Tests are meant to catch regressions in the broadcast code path --
interaction with .cm triggers, co-existence with P2P traffic, parser
guards, edge cases. They are NOT part of the thesis baseline
benchmark; they live here purely for program correctness.

Run from anywhere:

    python3 sim/htsim-backend/sim/datacenter/bcast_tests/run_tests.py

Or pass a custom binary path:

    python3 run_tests.py --htsim /path/to/htsim_uec

Exit code is 0 iff every test passes.
"""

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT_HINT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))  # sim/htsim-backend
DEFAULT_HTSIM = os.path.join(REPO_ROOT_HINT, "sim", "cmake-build-debug", "htsim_uec")

BCAST_RE = re.compile(r"BCAST_COMPLETE\s+.*?legs=(\d+)")


@dataclass
class TestCase:
    name: str
    cm: str
    nodes: int
    description: str
    expect_exit_code: int = 0
    expect_patterns: List[str] = field(default_factory=list)
    expect_not_patterns: List[str] = field(default_factory=list)
    expect_bcast_count: Optional[int] = None
    expect_legs_multiset: Optional[List[int]] = None  # order-insensitive


TESTS: List[TestCase] = [
    TestCase(
        name="T01 simple_bcast",
        cm="t01_simple_bcast.cm",
        nodes=16,
        description="single 4-member broadcast, 3 legs",
        expect_bcast_count=1,
        expect_legs_multiset=[3],
    ),
    TestCase(
        name="T02 parallel_bcasts",
        cm="t02_parallel_bcasts.cm",
        nodes=16,
        description="two disjoint broadcasts in parallel",
        expect_bcast_count=2,
        expect_legs_multiset=[6, 4],
    ),
    TestCase(
        name="T03 bcast_with_recv_trigger",
        cm="t03_bcast_with_recv_trigger.cm",
        nodes=16,
        description="bcast fires barrier -> TriggerRelay -> named trigger "
                    "-> two waiting P2P flows",
        expect_bcast_count=1,
        expect_legs_multiset=[6],
        expect_patterns=[
            # synthetic barrier fires (id 1, since max_triggerid()=1 from
            # the named oneshot trigger => barrier starts at 2).
            r"Trigger 2 fired, 2 targets",
            # and the named oneshot relays successfully to its targets
            r"Trigger 1 fired, 2 targets",
            # P2P follow-up endpoints were created
            r"uec_1_0",
            r"uec_14_15",
        ],
    ),
    TestCase(
        name="T04 bcast_plus_p2p",
        cm="t04_bcast_plus_p2p.cm",
        nodes=16,
        description="broadcast alongside two independent P2P flows",
        expect_bcast_count=1,
        expect_legs_multiset=[3],
        expect_patterns=[
            r"uec_2_10",
            r"uec_6_14",
        ],
    ),
    TestCase(
        name="T05 full_group_bcast",
        cm="t05_full_group_bcast.cm",
        nodes=16,
        description="|G| = N = 16, 15 legs",
        expect_bcast_count=1,
        expect_legs_multiset=[15],
    ),
    TestCase(
        name="T06 missing_id_rejected",
        cm="t06_missing_id_rejected.cm",
        nodes=16,
        description="parser must reject bcast matrix with a bare P2P line",
        expect_exit_code=1,
        expect_patterns=[
            r"missing an explicit `id`",
            r"start_bcast",
        ],
        expect_bcast_count=0,
    ),
    TestCase(
        name="T07 trigger_started_bcast",
        cm="t07_trigger_started_bcast.cm",
        nodes=16,
        description="trigger_bcast: P2P send_done_trigger activates "
                    "all broadcast legs together",
        expect_bcast_count=1,
        expect_legs_multiset=[6],
        expect_patterns=[
            # The named oneshot fires all |G|-1 = 6 leg sources.
            r"Trigger 1 fired, 6 targets",
            # P2P precursor flow shows up at setup.
            r"uec_5_10",
        ],
    ),
    TestCase(
        name="T08 bcast_chain",
        cm="t08_bcast_chain.cm",
        nodes=16,
        description="bcast1 recv_done_trigger -> trigger_bcast on "
                    "bcast2; two broadcasts cascade cleanly",
        expect_bcast_count=2,
        expect_legs_multiset=[3, 3],
        expect_patterns=[
            # Bcast 2's |G|-1 = 3 legs all start when the named trigger
            # fires.
            r"Trigger 1 fired, 3 targets",
        ],
    ),
    TestCase(
        name="T09 p2p_recv_done_trigger",
        cm="t09_p2p_recv_done_trigger.cm",
        nodes=16,
        description="P2P recv_done_trigger fires the named trigger on "
                    "sink-side completion, unblocking a waiting P2P flow",
        expect_bcast_count=0,
        expect_patterns=[
            # Named trigger must fire (the regression test): before the
            # fix, UecSink stored _end_trigger but never activated it,
            # so this line would be absent.
            r"Trigger 1 fired, 1 targets",
            r"uec_5_10",
            r"uec_2_3",
        ],
    ),
]


# ---------------------------------------------------------------- runner

def run_one(htsim: str, test: TestCase, timeout_s: int) -> tuple:
    """Run htsim_uec on `test`; return (ok, stdout, reasons)."""
    cm_path = os.path.join(HERE, test.cm)
    cmd = [
        htsim,
        "-strat", "ecmp_host",
        "-tm", cm_path,
        "-nodes", str(test.nodes),
        "-linkspeed", "100000",
        "-seed", "1",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return False, "", [f"timed out after {timeout_s}s"]

    reasons = []
    if proc.returncode != test.expect_exit_code:
        reasons.append(
            f"exit code: expected {test.expect_exit_code}, "
            f"got {proc.returncode}"
        )

    stdout = proc.stdout + proc.stderr

    for pat in test.expect_patterns:
        if not re.search(pat, stdout):
            reasons.append(f"expected pattern not found: {pat!r}")
    for pat in test.expect_not_patterns:
        if re.search(pat, stdout):
            reasons.append(f"forbidden pattern present: {pat!r}")

    bcasts = BCAST_RE.findall(stdout)
    bcast_count = len(bcasts)
    if test.expect_bcast_count is not None:
        if bcast_count != test.expect_bcast_count:
            reasons.append(
                f"BCAST_COMPLETE count: expected "
                f"{test.expect_bcast_count}, got {bcast_count}"
            )

    if test.expect_legs_multiset is not None:
        got = sorted(int(x) for x in bcasts)
        want = sorted(test.expect_legs_multiset)
        if got != want:
            reasons.append(
                f"legs multiset: expected {want}, got {got}"
            )

    return (not reasons), stdout, reasons


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--htsim",
        default=DEFAULT_HTSIM,
        help=f"Path to htsim_uec binary (default: {DEFAULT_HTSIM})",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Per-test timeout in seconds",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="On failure, print the offending simulator stdout+stderr",
    )
    p.add_argument(
        "--filter",
        default=None,
        help="Only run tests whose name contains this substring",
    )
    args = p.parse_args()

    if not os.path.isfile(args.htsim) or not os.access(args.htsim, os.X_OK):
        sys.exit(
            f"htsim binary not found or not executable: {args.htsim}\n"
            "Pass --htsim <path> or build htsim_uec first."
        )

    selected = [
        t for t in TESTS
        if args.filter is None or args.filter in t.name
    ]
    if not selected:
        sys.exit("no tests matched --filter")

    pad = max(len(t.name) for t in selected)
    failures = 0
    for t in selected:
        ok, stdout, reasons = run_one(args.htsim, t, args.timeout)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {t.name:<{pad}}  {t.description}")
        if not ok:
            failures += 1
            for r in reasons:
                print(f"         - {r}")
            if args.verbose:
                print("----- simulator output -----")
                print(stdout)
                print("----- end -----")

    total = len(selected)
    passed = total - failures
    print(f"\n{passed}/{total} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
