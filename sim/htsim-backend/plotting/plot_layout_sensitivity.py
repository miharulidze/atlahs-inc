#!/usr/bin/env python3
"""Group-layout sensitivity: completion time for |G|=3 in two
layouts (local within a single pod vs. cross-pod across three
pods), in both baseline and mcast modes, for K=4/8/16 fat-trees.

Demonstrates that group placement, not group size, dominates
T_fabric for both phases.
"""

import argparse
import csv
import os
import sys

import matplotlib.pyplot as plt
import numpy as np


def load(path):
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                row["duration_ns"] = int(row["duration_ns"])
            except ValueError:
                continue
            rows.append(row)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    rows = load(args.csv)
    if not rows:
        sys.exit(f"no usable rows in {args.csv}")

    # Topologies (fat-tree sizes) on x-axis.
    nodes_set = sorted({int(r["n"]) for r in rows})
    layouts   = ("local", "crosspod")
    modes     = ("baseline", "mcast")

    fig, ax = plt.subplots(figsize=(8, 4.5))
    width = 0.18
    x = np.arange(len(nodes_set))

    # 4 bar groups per topology: local-baseline, local-mcast,
    # crosspod-baseline, crosspod-mcast.
    bar_specs = [
        ("local",    "baseline", "#88c"),
        ("local",    "mcast",    "#44a"),
        ("crosspod", "baseline", "#fa7"),
        ("crosspod", "mcast",    "#d62"),
    ]
    for i, (layout, mode, col) in enumerate(bar_specs):
        ys = []
        for n in nodes_set:
            cells = [r for r in rows
                     if int(r["n"]) == n
                     and r["layout"] == layout
                     and r["mode"] == mode]
            ys.append(cells[0]["duration_ns"] if cells else 0)
        offset = (i - 1.5) * width
        ax.bar(x + offset, ys, width=width,
               label=f"{layout} / {mode}", color=col,
               edgecolor="black", linewidth=0.4)

    ax.set_xticks(x)
    ax.set_xticklabels([f"{n}-host\n(K={int(round((n*4) ** (1/3)))})"
                        for n in nodes_set])
    ax.set_ylabel("Completion time (ns)")
    ax.set_title(r"Group-layout sensitivity: |G|=3, "
                 r"local vs cross-pod, K $\in$ {4, 8, 16}")
    ax.grid(True, axis="y", linestyle=":", linewidth=0.5,
            alpha=0.6)
    ax.legend(fontsize=8, ncol=2, loc="upper right")
    fig.tight_layout()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    for ext in ("pdf", "png"):
        f = f"{args.out}.{ext}"
        fig.savefig(f, dpi=150, bbox_inches="tight")
        print(f"wrote {f}")


if __name__ == "__main__":
    main()
