#!/usr/bin/env python3
"""Plot total link-crosses (PT6) as a function of group size,
one line per (topology, mode). Captures network-footprint
contrast between phase-1 ACK-less unicast and phase-2 switch-
level multicast.

CSV columns expected (produced by run_bcast_sweep.py
--link-crosses):
    nodes, group_size, rep, mode, payload_bytes, op_id, ...,
    link_crosses
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt


def load(path):
    by = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                lc = int(row["link_crosses"])
            except (KeyError, ValueError):
                continue
            key = (int(row["nodes"]), int(row["group_size"]),
                   row["mode"])
            by[key].append(lc)
    return by


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--title",
                   default="Total link traversals: phase-1 vs phase-2")
    p.add_argument(
        "--modes",
        nargs="+",
        default=None,
        help="Restrict to these bcast modes (e.g. 'baseline' or "
             "'mcast'). Default: every mode in the CSV. Use a single "
             "mode for a standalone per-phase footprint figure.",
    )
    p.add_argument(
        "--nodes",
        nargs="+",
        type=int,
        default=None,
        help="Restrict to these topology sizes (e.g. 1024). Default: "
             "every size in the CSV. Use a single size for an overlay "
             "comparison figure where the topologies behave alike.",
    )
    p.add_argument(
        "--mmm",
        action="store_true",
        help="Render min/max as error bars (median marker with a "
             "min/max whisker) per series, instead of a bare median "
             "line. Use for standalone per-phase figures where the "
             "footprint genuinely varies with the group layout.",
    )
    p.add_argument(
        "--offset-topologies",
        action="store_true",
        default=True,
        help="Apply a tiny multiplicative x-offset per topology so "
             "that error bars sharing identical min/median/max (e.g. "
             "at |G|=2) don't stack and occlude each other (default: "
             "on).",
    )
    p.add_argument(
        "--no-offset-topologies",
        action="store_false",
        dest="offset_topologies",
    )
    args = p.parse_args()

    buckets = load(args.csv)
    if not buckets:
        sys.exit(f"no link_crosses rows in {args.csv}")

    if args.modes:
        keep = set(args.modes)
        buckets = {k: v for k, v in buckets.items() if k[2] in keep}
        if not buckets:
            sys.exit(f"no link_crosses rows for modes {sorted(keep)}")

    if args.nodes:
        keepn = set(args.nodes)
        buckets = {k: v for k, v in buckets.items() if k[0] in keepn}
        if not buckets:
            sys.exit(f"no link_crosses rows for nodes {sorted(keepn)}")

    fig, ax = plt.subplots(figsize=(8, 5))
    mode_style = {"baseline": "-", "mcast": "--"}

    by_topo = defaultdict(list)  # (nodes, mode) -> [(g, lcs)]
    for (nodes, g, mode), lcs in buckets.items():
        by_topo[(nodes, mode)].append((g, lcs))
    for k in by_topo:
        by_topo[k].sort(key=lambda t: t[0])

    # Spread the topologies apart by a tiny multiplicative x-offset so
    # that when several share identical min/median/max (e.g. at
    # |G|=2, where every topology yields 3/11/11) their error bars do
    # not stack and occlude one another. Pure cosmetics on the log-x
    # axis; the offset is a few percent, well below the gap between
    # adjacent group sizes. Matches plot_bcast_baseline.py.
    topos = sorted({nodes for nodes, _ in by_topo})
    n_topos = len(topos)
    offsets = {}
    if args.offset_topologies and n_topos > 1:
        for i, nodes in enumerate(topos):
            offsets[nodes] = 1.0 + 0.04 * (i - (n_topos - 1) / 2)
    else:
        for nodes in topos:
            offsets[nodes] = 1.0

    for (nodes, mode), series in sorted(by_topo.items()):
        xs = [g * offsets[nodes] for g, _ in series]
        medians = [sorted(d)[len(d) // 2] for _, d in series]
        ls = mode_style.get(mode, "-")
        suffix = "" if mode == "baseline" else " [mcast]"
        if args.mmm:
            mins = [min(d) for _, d in series]
            maxs = [max(d) for _, d in series]
            lower = [m - lo for m, lo in zip(medians, mins)]
            upper = [hi - m for m, hi in zip(medians, maxs)]
            ax.errorbar(xs, medians, yerr=[lower, upper], marker="o",
                        capsize=3, markersize=5, linestyle=ls,
                        linewidth=1.3, alpha=0.85,
                        label=f"{nodes}-host fat-tree{suffix}")
        else:
            ax.plot(xs, medians, marker="o", markersize=5,
                    linewidth=1.4, linestyle=ls,
                    label=f"{nodes}-host fat-tree{suffix}")

    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlabel("Group size |G|")
    ax.set_ylabel("Total directional link traversals (count)")
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


if __name__ == "__main__":
    main()
