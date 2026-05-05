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
    """Run htsim_uec once and return (matches, link_crosses_total).

    matches  = list of BCAST_COMPLETE field-dicts.
    link_crosses_total = total directional pipe traversals reported
        by the simulator's PT6 instrumentation, or None if not
        captured.
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
        return ([], None)

    if proc.returncode != 0:
        sys.stderr.write(
            f"[fail rc={proc.returncode}] {cm_path} seed={seed}\n"
            f"  stderr tail: {proc.stderr[-300:]}\n"
        )
        if lc_path:
            try: os.unlink(lc_path)
            except OSError: pass
        return ([], None)

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

    return (matches, lc_total)


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
    for (nodes, group_size, rep, payload_bytes, cm_path) in entries:
        for mode in modes:
            hits, lc_total = run_one(
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
                    "matrix_path": cm_path,
                }
                if args.link_crosses:
                    row["link_crosses"] = lc_total if lc_total is not None else ""
                rows.append(row)
            print(
                f"n={nodes} g={group_size} rep={rep} mode={mode}  "
                f"-> {len(hits)} op(s)"
                + (f"  [lc={lc_total}]" if args.link_crosses else "")
            )

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
