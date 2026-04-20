#!/usr/bin/env python3
"""Plot the broadcast-baseline sweep collected by run_bcast_sweep.py.

Renders completion time (ns) vs. group size, per topology size, with
box-plot-style summary statistics (Hoefler-style: median, quartiles,
min, max). Produces both PDF and PNG.

CSV columns expected (produced by run_bcast_sweep.py):
    nodes, group_size, seed, op_id, root, group_idx, size, legs,
    start_ns, complete_ns, duration_ns, matrix_path

Invoke:
    python3 plot_bcast_baseline.py \\
        --csv ../sim/htsim-backend/sim/datacenter/connection_matrices/bcast_sweep/results.csv \\
        --out bcast_baseline
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt


def load_results(path):
    """Return dict keyed by (nodes, group_size) -> list[duration_ns]."""
    buckets = defaultdict(list)
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (int(row["nodes"]), int(row["group_size"]))
            buckets[key].append(int(row["duration_ns"]))
    return buckets


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True)
    p.add_argument(
        "--out",
        required=True,
        help="Output base name (extensions .pdf/.png added)",
    )
    p.add_argument("--title", default="Broadcast baseline (ACK-less P2P)")
    args = p.parse_args()

    buckets = load_results(args.csv)
    if not buckets:
        sys.exit(f"no rows in {args.csv}")

    # Organise by topology size so we get one line per fat-tree.
    by_nodes = defaultdict(list)  # nodes -> list[(group_size, durations)]
    for (nodes, g), durs in buckets.items():
        by_nodes[nodes].append((g, durs))
    for nodes in by_nodes:
        by_nodes[nodes].sort(key=lambda t: t[0])

    fig, ax = plt.subplots(figsize=(8, 5))

    for nodes, series in sorted(by_nodes.items()):
        xs = [g for g, _ in series]
        medians = [sorted(d)[len(d) // 2] for _, d in series]
        mins = [min(d) for _, d in series]
        maxs = [max(d) for _, d in series]
        lower_err = [m - lo for m, lo in zip(medians, mins)]
        upper_err = [hi - m for m, hi in zip(medians, maxs)]
        ax.errorbar(
            xs,
            medians,
            yerr=[lower_err, upper_err],
            marker="o",
            capsize=3,
            label=f"{nodes}-node fat-tree",
            linewidth=1.5,
        )

    ax.set_xscale("log", base=2)
    ax.set_xlabel("Group size |G|")
    ax.set_ylabel("Broadcast completion time (ns)")
    ax.set_title(args.title)
    ax.grid(True, which="both", linestyle=":", alpha=0.5)
    ax.legend()
    plt.tight_layout()

    for ext in ("pdf", "png"):
        out = f"{args.out}.{ext}"
        plt.savefig(out)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
