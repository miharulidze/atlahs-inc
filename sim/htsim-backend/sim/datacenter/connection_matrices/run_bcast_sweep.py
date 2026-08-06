#!/usr/bin/env python3
"""Run htsim_uec over a sweep of broadcast .cm files and collect
`BCAST_COMPLETE` lines into a CSV.

Reads the manifest produced by gen_bcast_sweep.py. For each matrix and
each seed, runs htsim_uec, greps the BCAST_COMPLETE line(s), and writes
one CSV row per (matrix, seed, op).

CSV columns: nodes, group_size, seed, op_id, root, group_idx, size,
legs, start_ns, complete_ns, duration_ns, matrix_path

Invoke:
    python3 run_bcast_sweep.py \\
        --manifest bcast_sweep/manifest.txt \\
        --htsim ../../cmake-build-debug/htsim_uec \\
        --reps 10 --out bcast_sweep/results.csv
"""

import argparse
import csv
import os
import re
import subprocess
import sys

BCAST_RE = re.compile(
    r"BCAST_COMPLETE\s+"
    r"op_id=(?P<op_id>\d+)\s+"
    r"root=(?P<root>\d+)\s+"
    r"group=(?P<group>\d+)\s+"
    r"size=(?P<size>\d+)\s+"
    r"legs=(?P<legs>\d+)\s+"
    r"start_ns=(?P<start_ns>\d+)\s+"
    r"complete_ns=(?P<complete_ns>\d+)\s+"
    r"duration_ns=(?P<duration_ns>\d+)"
)

# Correctness oracle. This build does not emit a New/Rtx/ACK summary line;
# loss instead surfaces as per-queue drop messages on stdout/stderr. Under
# the ACK-less lossless broadcast model a *correct* run drops nothing, so
# "drops == 0 == clean / drops > 0 == suspect" is the analogue of the
# Rtx:0-vs-Rtx>useful health check used in the host-centric INC literature.
# Match the actual drop-EVENT strings (NOT the "FastDrop:" config echo).
DROP_RE = re.compile(
    r"drop arriving|drop last from queue|RTS dropped|Data dropped|"
    r"Ack dropped|dropped packet|Random Drop|Buffer Drop|"
    r"Dropping packet|^Dropped\b|LOSSLESS not working",
    re.IGNORECASE | re.MULTILINE,
)
# A lossless queue that should have dropped is a hard correctness failure,
# not mere congestion loss -- flag it separately.
LOSSLESS_VIOLATION_RE = re.compile(r"LOSSLESS not working", re.IGNORECASE)


def read_manifest(path):
    """Return list of (nodes, group_size, rep, payload_bytes, cm_path).

    Manifest columns are either:
        nodes group_size rep path                   (4-col, single-MTU)
        nodes group_size rep payload_bytes path     (5-col, multi-MTU)

    payload_bytes is None for 4-col manifests; the runner can then
    leave the size column empty in the output CSV.
    """
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) == 4:
                nodes, group_size, rep, cm_path = parts
                payload = None
            elif len(parts) == 5:
                nodes, group_size, rep, payload, cm_path = parts
                payload = int(payload)
            else:
                continue
            entries.append((int(nodes), int(group_size), int(rep),
                            payload, cm_path))
    return entries


def run_one(htsim, cm_path, nodes, seed, linkspeed, timeout_s, mode,
            capture_link_crosses=False):
    """Run htsim_uec once and return (matches, link_crosses_total, diag).

    matches  = list of BCAST_COMPLETE field-dicts.
    link_crosses_total = total directional pipe traversals reported
        by the simulator's PT6 instrumentation, or None if not
        captured.
    diag = correctness-oracle dict {status, drops, lossless_violation}:
        status in {"ok","fail","timeout"}; drops = count of drop-event
        lines on stdout+stderr; lossless_violation = a lossless queue
        reported it should have dropped (hard correctness failure).
    """
    import tempfile
    lc_path = None
    cmd = [
        htsim,
        "-strat", "ecmp_host",
        "-tm", cm_path,
        "-nodes", str(nodes),
        "-linkspeed", str(linkspeed),
        "-seed", str(seed),
        "-bcast_mode", mode,
    ]
    if capture_link_crosses:
        lc_fd, lc_path = tempfile.mkstemp(prefix="lc_", suffix=".csv")
        os.close(lc_fd)
        cmd += ["-link_crosses_csv", lc_path]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            f"[timeout] {cm_path} seed={seed} after {timeout_s}s\n"
        )
        if lc_path:
            try: os.unlink(lc_path)
            except OSError: pass
        return ([], None,
                {"status": "timeout", "drops": 0,
                 "lossless_violation": False})

    if proc.returncode != 0:
        sys.stderr.write(
            f"[fail rc={proc.returncode}] {cm_path} seed={seed}\n"
            f"  stderr tail: {proc.stderr[-300:]}\n"
        )
        if lc_path:
            try: os.unlink(lc_path)
            except OSError: pass
        return ([], None,
                {"status": "fail", "drops": 0,
                 "lossless_violation": False})

    # Correctness oracle: scan the combined output for drop events.
    combined = proc.stdout + "\n" + proc.stderr
    drops = len(DROP_RE.findall(combined))
    diag = {
        "status": "ok",
        "drops": drops,
        "lossless_violation": bool(LOSSLESS_VIOLATION_RE.search(combined)),
    }
    if drops:
        sys.stderr.write(
            f"[drops={drops}{' LOSSLESS!' if diag['lossless_violation'] else ''}]"
            f" {cm_path} seed={seed} mode={mode}\n"
        )

    matches = [m.groupdict() for m in BCAST_RE.finditer(proc.stdout)]
    if not matches:
        sys.stderr.write(
            f"[no BCAST_COMPLETE] {cm_path} seed={seed}\n"
        )

    lc_total = None
    if lc_path:
        try:
            with open(lc_path) as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            if len(lines) >= 2:
                lc_total = int(lines[1])
        except (OSError, ValueError):
            pass
        try: os.unlink(lc_path)
        except OSError: pass

    return (matches, lc_total, diag)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True)
    p.add_argument("--htsim", required=True, help="Path to htsim_uec")
    p.add_argument(
        "--seed",
        type=int,
        default=1,
        help="Simulator seed passed via -seed (fixed; variance comes "
             "from the generator's multiple random-group matrices)",
    )
    p.add_argument(
        "--linkspeed",
        type=int,
        default=100000,
        help="Link speed in Mbps (htsim -linkspeed arg)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Per-run timeout in seconds",
    )
    p.add_argument(
        "--mode",
        choices=["baseline", "mcast", "both"],
        default="both",
        help="Which bcast_mode(s) to sweep. "
             "'both' produces one CSV row per (matrix, mode).",
    )
    p.add_argument(
        "--link-crosses",
        action="store_true",
        help="Capture per-run total link-crosses (PT6 metric) "
             "via -link_crosses_csv and add a 'link_crosses' "
             "column to the output CSV.",
    )
    p.add_argument("--out", required=True, help="Output CSV path")
    args = p.parse_args()

    if not os.path.isfile(args.htsim) or not os.access(args.htsim, os.X_OK):
        sys.exit(f"htsim binary not executable: {args.htsim}")

    entries = read_manifest(args.manifest)
    if not entries:
        sys.exit(f"manifest empty: {args.manifest}")

    modes = (["baseline", "mcast"] if args.mode == "both"
             else [args.mode])
    rows = []
    # Correctness-oracle tally (broadcast analogue of a New/Rtx/ACK health
    # check): every run should complete with zero drops.
    health = {"runs": 0, "clean": 0, "with_drops": 0,
              "lossless_violations": 0, "no_completion": 0,
              "failed_or_timeout": 0}
    for (nodes, group_size, rep, payload_bytes, cm_path) in entries:
        for mode in modes:
            hits, lc_total, diag = run_one(
                args.htsim, cm_path, nodes, args.seed,
                args.linkspeed, args.timeout, mode,
                capture_link_crosses=args.link_crosses,
            )
            for h in hits:
                row = {
                    "nodes": nodes,
                    "group_size": group_size,
                    "rep": rep,
                    "mode": mode,
                    "payload_bytes": payload_bytes if payload_bytes is not None else "",
                    "op_id": int(h["op_id"]),
                    "root": int(h["root"]),
                    "group_idx": int(h["group"]),
                    "size": int(h["size"]),
                    "legs": int(h["legs"]),
                    "start_ns": int(h["start_ns"]),
                    "complete_ns": int(h["complete_ns"]),
                    "duration_ns": int(h["duration_ns"]),
                    "drops": diag["drops"],
                    "matrix_path": cm_path,
                }
                if args.link_crosses:
                    row["link_crosses"] = lc_total if lc_total is not None else ""
                rows.append(row)

            # Tally run health.
            health["runs"] += 1
            if diag["status"] in ("fail", "timeout"):
                health["failed_or_timeout"] += 1
                flag = f"[{diag['status'].upper()}]"
            elif not hits:
                health["no_completion"] += 1
                flag = "[NO-COMPLETE]"
            elif diag["drops"] == 0:
                health["clean"] += 1
                flag = "[OK]"
            else:
                health["with_drops"] += 1
                flag = f"[WARN drops={diag['drops']}]"
            if diag["lossless_violation"]:
                health["lossless_violations"] += 1
                flag += " [LOSSLESS-VIOLATION]"

            print(
                f"n={nodes} g={group_size} rep={rep} mode={mode}  "
                f"-> {len(hits)} op(s)  {flag}"
                + (f"  [lc={lc_total}]" if args.link_crosses else "")
            )

    # One-glance correctness oracle: are the timings from loss-free runs?
    print("\n=== correctness oracle (drops == 0 == clean) ===")
    print(f"  runs:                {health['runs']}")
    print(f"  clean (0 drops):     {health['clean']}")
    print(f"  with drops:          {health['with_drops']}")
    print(f"  lossless violations: {health['lossless_violations']}")
    print(f"  no BCAST_COMPLETE:   {health['no_completion']}")
    print(f"  failed/timeout:      {health['failed_or_timeout']}")
    unclean = (health["with_drops"] + health["lossless_violations"]
               + health["no_completion"] + health["failed_or_timeout"])
    print("  STATUS: "
          + ("OK -- all runs clean and complete"
             if unclean == 0 else
             "NOT CLEAN -- investigate before trusting timings"))

    if not rows:
        sys.exit("no results collected")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
