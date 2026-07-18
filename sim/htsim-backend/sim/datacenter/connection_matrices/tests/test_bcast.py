#!/usr/bin/env python3
"""Functional-correctness tests for the broadcast baseline.

This is *not* the benchmark. No plots, no CSVs, no sweep. Each test
generates a small `.cm` on the fly, runs `htsim_uec` once, and asserts
on the textual output. Coverage:

  * basic bcast (no triggers)
  * bcast firing a `recv_done_trigger` that starts a P2P flow
  * long trigger chain: bcast -> P2P -> P2P
  * mixed traffic: bcast + unrelated P2P flow at the same time
  * multiple concurrent bcasts: two, three groups
  * bigger topology (128 nodes)
  * edge cases: |G|=2 (one leg), |G|=N (full fabric)
  * parser guards: missing `id` is rejected; undefined group index is rejected
  * barrier mechanics: the per-op BarrierTrigger counts down exactly |G|-1 times

Invoke from this directory:
    python3 test_bcast.py

or with an explicit binary:
    python3 test_bcast.py --htsim /path/to/htsim_uec

Exit code is zero iff all tests pass.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

# --------------------------------------------------------------------------
# setup
# --------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))

# Default binary: ../../cmake-build-debug/htsim_uec relative to this file.
DEFAULT_HTSIM = os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "cmake-build-debug", "htsim_uec")
)

BCAST_RE = re.compile(
    r"BCAST_COMPLETE\s+op_id=(\d+)\s+root=(\d+)\s+group=(\d+)\s+"
    r"size=(\d+)\s+legs=(\d+)\s+start_ns=(\d+)\s+"
    r"complete_ns=(\d+)\s+duration_ns=(\d+)"
)

TRIG_FIRED_RE = re.compile(r"Trigger\s+(\d+)\s+fired,\s+(\d+)\s+targets")

BARRIER_COUNTDOWN_RE = re.compile(
    r"Trigger\s+\d+\s+activated,\s+activations remaining:\s+(\d+)"
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def run_htsim(htsim, cm_text, nodes=16, timeout=30):
    """Write `cm_text` to a temp .cm and run `htsim_uec`. Returns
    (returncode, stdout, stderr)."""
    with tempfile.NamedTemporaryFile(
        suffix=".cm", mode="w", delete=False
    ) as f:
        f.write(cm_text)
        path = f.name
    try:
        proc = subprocess.run(
            [
                htsim,
                "-strat", "ecmp_host",
                "-tm", path,
                "-nodes", str(nodes),
                "-linkspeed", "100000",
                "-seed", "1",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def parse_bcasts(stdout):
    """Return list of dicts, one per BCAST_COMPLETE line."""
    out = []
    for m in BCAST_RE.finditer(stdout):
        out.append({
            "op_id":       int(m.group(1)),
            "root":        int(m.group(2)),
            "group_idx":   int(m.group(3)),
            "size":        int(m.group(4)),
            "legs":        int(m.group(5)),
            "start_ns":    int(m.group(6)),
            "complete_ns": int(m.group(7)),
            "duration_ns": int(m.group(8)),
        })
    return out


def parse_triggers_fired(stdout):
    """Return a set of trigger ids that fired."""
    return {int(m.group(1)) for m in TRIG_FIRED_RE.finditer(stdout)}


def parse_barrier_countdowns(stdout):
    """Return list of remaining counts shown by BarrierTrigger decrements,
    in the order they occur. With exactly one broadcast of |G|=k, expect
    [k-2, k-3, ..., 1] i.e. k-2 items; the k-1'th activation fires the
    barrier and does not emit a 'remaining' line."""
    return [int(m.group(1)) for m in BARRIER_COUNTDOWN_RE.finditer(stdout)]


def _format_tail(text, n=400):
    return text[-n:].rstrip() if text else "<empty>"


class Assert:
    def __init__(self, stdout="", stderr=""):
        self._stdout = stdout
        self._stderr = stderr

    def fail(self, msg):
        tail_out = _format_tail(self._stdout)
        tail_err = _format_tail(self._stderr)
        raise AssertionError(
            f"{msg}\n  stdout tail: {tail_out}\n  stderr tail: {tail_err}"
        )

    def is_true(self, cond, msg):
        if not cond:
            self.fail(msg)


# --------------------------------------------------------------------------
# test registry
# --------------------------------------------------------------------------

_TESTS = []

def test(description):
    def deco(fn):
        _TESTS.append((description, fn))
        return fn
    return deco


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

@test("basic bcast — single operation, no triggers")
def t_basic(htsim):
    cm = """\
Nodes 16
Grp 0 1 2 3
Connections 1
0->0 id 1 start_bcast 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    bcs = parse_bcasts(out)
    a.is_true(len(bcs) == 1, f"expected 1 BCAST_COMPLETE, got {len(bcs)}")
    a.is_true(bcs[0]["legs"] == 3, f"expected 3 legs, got {bcs[0]['legs']}")
    a.is_true(bcs[0]["op_id"] == 1, f"expected op_id 1, got {bcs[0]['op_id']}")
    a.is_true(2000 <= bcs[0]["duration_ns"] <= 10000,
              f"duration_ns={bcs[0]['duration_ns']} ns out of plausible range")


@test("bcast fires recv_done_trigger, launching a P2P flow")
def t_bcast_recv_done(htsim):
    cm = """\
Nodes 16
Grp 0 1 2 3
Connections 2
Triggers 1
0->0 id 1 start_bcast 0 size 4096 recv_done_trigger 10
8->9 id 2 trigger 10 size 4096
trigger id 10 oneshot
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    a.is_true(len(parse_bcasts(out)) == 1, "expected 1 bcast completion")
    fired = parse_triggers_fired(out)
    a.is_true(10 in fired, f"expected trigger 10 to fire; fired={sorted(fired)}")


@test("chain: bcast -> trigger -> P2P -> trigger -> P2P")
def t_trigger_chain(htsim):
    cm = """\
Nodes 16
Grp 0 1 2 3
Connections 3
Triggers 2
0->0 id 1 start_bcast 0 size 4096 recv_done_trigger 10
4->5 id 2 trigger 10 size 4096 send_done_trigger 20
12->13 id 3 trigger 20 size 4096
trigger id 10 oneshot
trigger id 20 oneshot
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    a.is_true(len(parse_bcasts(out)) == 1, "expected 1 bcast completion")
    fired = parse_triggers_fired(out)
    a.is_true(10 in fired and 20 in fired,
              f"expected triggers 10 and 20 to fire; fired={sorted(fired)}")


@test("mixed traffic — bcast and independent P2P both run")
def t_mixed(htsim):
    cm = """\
Nodes 16
Grp 0 1 2 3
Connections 2
0->0 id 1 start_bcast 0 size 4096
8->9 id 2 start 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    a.is_true(len(parse_bcasts(out)) == 1, "expected 1 bcast completion")
    # Regular P2P flow name "uec_8_9" is echoed by main_uec on setup.
    a.is_true("uec_8_9" in out,
              "expected 'uec_8_9' line from regular P2P setup")


@test("two concurrent bcasts produce distinct op_ids")
def t_two_bcasts(htsim):
    cm = """\
Nodes 16
Grp 0 1 2 3
Grp 4 5 6 7
Connections 2
0->0 id 1 start_bcast 0 size 4096
0->1 id 2 start_bcast 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    bcs = parse_bcasts(out)
    a.is_true(len(bcs) == 2, f"expected 2 bcasts, got {len(bcs)}")
    ops = {b["op_id"] for b in bcs}
    a.is_true(ops == {1, 2}, f"expected op_ids {{1,2}}, got {sorted(ops)}")


@test("three concurrent bcasts, all complete")
def t_three_bcasts(htsim):
    cm = """\
Nodes 16
Grp 0 1 2 3
Grp 4 5 6 7
Grp 8 9 10 11
Connections 3
0->0 id 1 start_bcast 0 size 4096
0->1 id 2 start_bcast 0 size 4096
0->2 id 3 start_bcast 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    bcs = parse_bcasts(out)
    a.is_true(len(bcs) == 3, f"expected 3 bcasts, got {len(bcs)}")
    a.is_true({b["op_id"] for b in bcs} == {1, 2, 3},
              f"unexpected op_ids: {[b['op_id'] for b in bcs]}")


@test("minimum group size |G|=2 → exactly one leg")
def t_min_group(htsim):
    cm = """\
Nodes 16
Grp 0 1
Connections 1
0->0 id 1 start_bcast 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    bcs = parse_bcasts(out)
    a.is_true(len(bcs) == 1 and bcs[0]["legs"] == 1,
              f"expected 1 leg, got {bcs}")


@test("full-fabric group |G|=N completes")
def t_full_group(htsim):
    members = " ".join(str(i) for i in range(16))
    cm = f"""\
Nodes 16
Grp {members}
Connections 1
0->0 id 1 start_bcast 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    bcs = parse_bcasts(out)
    a.is_true(len(bcs) == 1 and bcs[0]["legs"] == 15,
              f"expected 15 legs, got {bcs}")


@test("bcast on the 128-node topology")
def t_128_node(htsim):
    cm = """\
Nodes 128
Grp 0 1 2 3 4 5 6 7
Connections 1
0->0 id 1 start_bcast 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm, nodes=128, timeout=60)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    bcs = parse_bcasts(out)
    a.is_true(len(bcs) == 1 and bcs[0]["legs"] == 7,
              f"expected 7 legs, got {bcs}")


@test("parser rejects bcast matrix with a missing id on any connection")
def t_missing_id(htsim):
    cm = """\
Nodes 16
Grp 0 1 2 3
Connections 2
0->0 id 1 start_bcast 0 size 4096
8->9 start 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc != 0, "expected nonzero rc when an id is missing")
    combined = out + err
    a.is_true("missing an explicit `id`" in combined,
              "expected the 'missing id' diagnostic from the parser guard")


@test("driver rejects bcast with an undefined group index")
def t_undefined_group(htsim):
    cm = """\
Nodes 16
Grp 0 1 2 3
Connections 1
0->7 id 1 start_bcast 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc != 0, "expected nonzero rc when group index is out of range")
    combined = out + err
    a.is_true("undefined group index" in combined,
              "expected 'undefined group index' diagnostic")


@test("barrier counts down exactly |G|-1 times")
def t_barrier_count(htsim):
    # |G|=5  =>  legs=4  =>  the barrier is activated 4 times total.
    # The 4th activation fires the barrier; the first 3 emit
    # "activations remaining: {3, 2, 1}".
    cm = """\
Nodes 16
Grp 0 1 2 3 4
Connections 1
0->0 id 1 start_bcast 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    countdown = parse_barrier_countdowns(out)
    a.is_true(countdown == [3, 2, 1],
              f"expected countdown [3,2,1], got {countdown}")


@test("bcast op_id shows through into BCAST_COMPLETE")
def t_op_id_preserved(htsim):
    cm = """\
Nodes 16
Grp 0 1 2 3
Connections 1
0->0 id 42 start_bcast 0 size 4096
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    bcs = parse_bcasts(out)
    a.is_true(len(bcs) == 1 and bcs[0]["op_id"] == 42,
              f"expected op_id=42, got {bcs}")


@test("user trigger ids and synthesised barrier ids do not collide")
def t_no_trigger_id_collision(htsim):
    # User declares triggers 1 and 2 explicitly; our synthesised
    # barriers should receive ids above max_triggerid() = 2.
    cm = """\
Nodes 16
Grp 0 1 2 3
Connections 2
Triggers 2
0->0 id 1 start_bcast 0 size 4096 recv_done_trigger 1
4->5 id 2 trigger 1 size 4096 send_done_trigger 2
trigger id 1 oneshot
trigger id 2 oneshot
"""
    rc, out, err = run_htsim(htsim, cm)
    a = Assert(out, err)
    a.is_true(rc == 0, f"rc={rc}")
    # Parse every "Trigger N fired ..." line we see.  Both user triggers
    # (1, 2) should fire, plus the synthetic barrier (id >= 3).
    fired = parse_triggers_fired(out)
    a.is_true(1 in fired and 2 in fired,
              f"expected user triggers 1,2 to fire; fired={sorted(fired)}")
    synthetic = {t for t in fired if t > 2}
    a.is_true(len(synthetic) >= 1,
              f"expected a synthetic barrier (id>2) to fire; fired={sorted(fired)}")


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--htsim", default=DEFAULT_HTSIM,
                   help=f"Path to htsim_uec (default: {DEFAULT_HTSIM})")
    p.add_argument("--filter", default="",
                   help="Only run tests whose description contains this substring")
    args = p.parse_args()

    if not os.path.isfile(args.htsim):
        sys.exit(f"htsim binary not found: {args.htsim}")
    if not os.access(args.htsim, os.X_OK):
        sys.exit(f"htsim binary not executable: {args.htsim}")

    to_run = [(desc, fn) for desc, fn in _TESTS if args.filter in desc]
    if not to_run:
        sys.exit(f"no tests matched filter: {args.filter!r}")

    print(f"Running {len(to_run)} test(s) against:")
    print(f"    {args.htsim}\n")

    failed = 0
    for desc, fn in to_run:
        try:
            fn(args.htsim)
        except AssertionError as e:
            print(f"  FAIL  {desc}")
            for line in str(e).splitlines():
                print(f"        {line}")
            failed += 1
            continue
        except subprocess.TimeoutExpired as e:
            print(f"  FAIL  {desc}   (timed out after {e.timeout}s)")
            failed += 1
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  ERR   {desc}   ({type(e).__name__}: {e})")
            failed += 1
            continue
        print(f"  PASS  {desc}")

    total = len(to_run)
    print(f"\n{total - failed}/{total} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
