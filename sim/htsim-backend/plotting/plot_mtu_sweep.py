#!/usr/bin/env python3
"""Plot the multi-MTU broadcast sweep.

X-axis: payload bytes (log scale).
Y-axis: completion time (ns, log scale).
One line per (topology, group_size, mode) triple, faceted by
topology so the chart stays readable.

CSV columns expected (produced by run_bcast_sweep.py with a
multi-MTU manifest):
    nodes, group_size, rep, mode, payload_bytes, op_id, root,
    group_idx, size, legs, start_ns, complete_ns, duration_ns,
    matrix_path
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt


def load(path):
    by = defaultdict(list)  # (nodes, g, mode, payload) -> list[duration]
    with open(path) as f:
        for row in csv.DictReader(f):
            payload = int(row["payload_bytes"]) if row.get("payload_bytes") \
                      else 4096
            key = (int(row["nodes"]), int(row["group_size"]),
                   row["mode"], payload)
            by[key].append(int(row["duration_ns"]))
    return by


def med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True)
    p.add_argument("--out", required=True,
                   help="Output base name (.pdf and .png appended)")
    p.add_argument("--title",
                   default="Broadcast completion time vs message size")
    p.add_argument(
        "--modes",
        nargs="+",
        default=["baseline", "mcast"],
        help="Which bcast modes to plot (e.g. 'baseline', 'mcast', "
             "or both). A single mode produces a standalone per-phase "
             "message-size figure; both produces the overlay.",
    )
    p.add_argument(
        "--group-size", type=int, default=None,
        help="Restrict to a single |G| (required for "
             "--overlay-topologies).",
    )
    p.add_argument(
        "--overlay-topologies", action="store_true",
        help="Single panel, one line per topology at the fixed "
             "--group-size, to show topology-size invariance. Applies "
             "a small geometric y-offset per topology so the otherwise "
             "coincident lines are distinguishable.",
    )
    p.add_argument(
        "--y-offset", type=float, default=1.15,
        help="Geometric y-offset factor between adjacent topologies "
             "in --overlay-topologies mode (purely cosmetic).",
    )
    args = p.parse_args()

    buckets = load(args.csv)
    if not buckets:
        sys.exit(f"no rows in {args.csv}")

    # Topology-invariance view: one |G|, one line per topology, with a
    # geometric y-offset because the medians are identical and would
    # otherwise sit exactly on top of one another.
    if args.overlay_topologies:
        g = args.group_size
        if g is None:
            sys.exit("--overlay-topologies requires --group-size")
        nodes_set = sorted({k[0] for k in buckets if k[1] == g})
        fig, ax = plt.subplots(figsize=(8, 5))
        nt = len(nodes_set)
        for i, nodes in enumerate(nodes_set):
            off = args.y_offset ** (i - (nt - 1) / 2.0)
            for mode in args.modes:
                payloads = sorted({k[3] for k in buckets if k[0] == nodes
                                   and k[1] == g and k[2] == mode})
                if not payloads:
                    continue
                ys = [med(buckets[(nodes, g, mode, pl)]) * off
                      for pl in payloads]
                ls = "-" if mode == "baseline" else "--"
                K = int(round((nodes * 4) ** (1.0 / 3.0)))
                ax.plot(payloads, ys, marker="o", markersize=4,
                        linewidth=1.4, linestyle=ls,
                        label=f"{nodes}-host fat tree (K={K})")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("Message size (bytes)")
        ax.set_ylabel("Completion time (ns)")
        ax.set_title(args.title)
        ax.grid(True, which="both", linestyle=":", linewidth=0.5,
                alpha=0.6)
        ax.legend(fontsize=8, loc="upper left")
        fig.tight_layout()
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        for ext in ("pdf", "png"):
            f = f"{args.out}.{ext}"
            fig.savefig(f, dpi=150, bbox_inches="tight")
            print(f"wrote {f}")
        return

    # Organise by topology, then series (group, mode).
    nodes_set = sorted({k[0] for k in buckets})
    n_topos = len(nodes_set)
    fig, axes = plt.subplots(1, n_topos, figsize=(4.2 * n_topos, 4.5),
                             sharey=True)
    if n_topos == 1:
        axes = [axes]

    color_map = {}  # group_size -> colour
    for ax, nodes in zip(axes, nodes_set):
        # Series for this topology.
        groups = sorted({k[1] for k in buckets if k[0] == nodes})
        for g in groups:
            for mode in args.modes:
                payloads = sorted({k[3] for k in buckets
                                   if k[0] == nodes
                                   and k[1] == g
                                   and k[2] == mode})
                if not payloads:
                    continue
                xs = payloads
                ys = []
                for p in payloads:
                    durs = buckets[(nodes, g, mode, p)]
                    durs.sort()
                    ys.append(durs[len(durs) // 2])
                ls = "-" if mode == "baseline" else "--"
                # When only one mode is plotted the linestyle alone is
                # ambiguous, so drop the mode tag from the label.
                lbl = f"|G|={g}" if len(args.modes) == 1 \
                    else f"|G|={g} {mode}"
                if g not in color_map:
                    color_map[g] = None  # let matplotlib pick
                line, = ax.plot(xs, ys, marker="o", markersize=4,
                                linewidth=1.4, linestyle=ls,
                                color=color_map[g],
                                label=lbl)
                color_map[g] = line.get_color()
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("Message size (bytes)")
        ax.set_title(f"{nodes}-host fat tree (K={int(round((nodes*4) ** (1/3)))})")
        ax.grid(True, which="both", linestyle=":", linewidth=0.5,
                alpha=0.6)
        ax.legend(fontsize=7, loc="upper left")
    axes[0].set_ylabel("Completion time (ns)")
    fig.suptitle(args.title)
    fig.tight_layout()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    for ext in ("pdf", "png"):
        f = f"{args.out}.{ext}"
        fig.savefig(f, dpi=150, bbox_inches="tight")
        print(f"wrote {f}")


if __name__ == "__main__":
    main()
