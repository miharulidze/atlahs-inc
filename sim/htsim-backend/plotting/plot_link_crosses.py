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
import math
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
    p.add_argument(
        "--theory",
        action="store_true",
        help="Overlay the first-principles unicast footprint bounds "
             "c*(|G|-1): a lower bound where every receiver is "
             "intra-rack and an upper bound where every receiver is "
             "cross-pod. Only meaningful for the phase-1 (baseline) "
             "footprint, whose total is a sum of independent paths.",
    )
    p.add_argument(
        "--theory-intra-rack",
        type=float,
        default=3.0,
        help="Per-receiver directional traversals on an intra-rack "
             "path (2 hops -> 2h-1 = 3). Lower-bound slope.",
    )
    p.add_argument(
        "--theory-cross-pod",
        type=float,
        default=11.0,
        help="Per-receiver directional traversals on a cross-pod "
             "path (6 hops -> 2h-1 = 11). Upper-bound slope.",
    )
    p.add_argument(
        "--theory-mcast",
        action="store_true",
        help="Overlay the occupancy model for the multicast "
             "footprint, 2*(|G| + E[ToRs] + E[pods]) - 1, computed "
             "per topology. Explains why a larger fat tree yields a "
             "larger footprint at fixed |G|: the same group occupies "
             "more distinct racks/pods, so the tree branches more.",
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

    # First-principles unicast footprint bounds. The phase-1 total is
    # a sum of (|G|-1) independent root->receiver paths, each costing
    # 2h-1 directional traversals: 3 intra-rack (h=2) up to 11
    # cross-pod (h=6). So the whole sweep is bounded by c*(|G|-1) for
    # c in {intra-rack, cross-pod}.
    if args.theory:
        gs = sorted({g for _, g, _ in buckets})
        g_lo, g_hi = max(gs[0], 2), gs[-1]
        n = 80
        xs_t = [2.0 ** (math.log2(g_lo) + i * (math.log2(g_hi)
                - math.log2(g_lo)) / (n - 1)) for i in range(n)]
        lo = [args.theory_intra_rack * (x - 1.0) for x in xs_t]
        hi = [args.theory_cross_pod * (x - 1.0) for x in xs_t]
        ax.fill_between(xs_t, lo, hi, color="black", alpha=0.06,
                        zorder=0)
        ax.plot(xs_t, lo, color="black", linestyle=":", linewidth=1.4,
                alpha=0.85, zorder=1,
                label=fr"Lower bound: all intra-rack "
                      fr"(${args.theory_intra_rack:.0f}(|G|{{-}}1)$)")
        ax.plot(xs_t, hi, color="black", linestyle="--", linewidth=1.4,
                alpha=0.85, zorder=1,
                label=fr"Upper bound: all cross-pod "
                      fr"(${args.theory_cross_pod:.0f}(|G|{{-}}1)$)")

    # Occupancy model for the multicast footprint. The distribution
    # tree's branching at each tier equals the number of distinct
    # racks/pods the members occupy, so the footprint is
    #   2*(|G| + E[ToRs] + E[pods]) - 1,
    # where E[units occupied] = U*(1 - C(N-s,|G|)/C(N,|G|)) for U
    # units of s host-slots each (balls-in-bins). A larger fat tree
    # has more racks/pods, so the same |G| collides less and spreads
    # over more distinct switches -> larger footprint at fixed |G|.
    if args.theory_mcast:
        topos_t = sorted({k[0] for k in buckets})
        for idx, N in enumerate(topos_t):
            K = int(round((4 * N) ** (1.0 / 3.0)))
            n_tor, s_tor = K * K // 2, K // 2     # ToRs, hosts/ToR
            n_pod, s_pod = K, N // K              # pods, hosts/pod
            gs = sorted({k[1] for k in buckets if k[0] == N})

            def e_occ(units, slots, G):
                if N - slots < G:
                    return float(units)
                return units * (1.0 - math.comb(N - slots, G)
                                / math.comb(N, G))

            xs_m, ys_m = [], []
            for G in gs:
                if G > N:
                    continue
                e_tor, e_pod = e_occ(n_tor, s_tor, G), e_occ(n_pod, s_pod, G)
                apex = e_pod if e_pod > 1.0001 else 1.0  # core vs agg apex
                xs_m.append(G)
                ys_m.append(2.0 * (G + e_tor + apex) - 1.0)
            ax.plot(xs_m, ys_m, color="black", linestyle=":",
                    linewidth=1.3, alpha=0.75, zorder=1,
                    label=(r"model $2(|G|+E[\mathrm{ToR}]"
                           r"+E[\mathrm{pod}])-1$") if idx == 0 else None)

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
