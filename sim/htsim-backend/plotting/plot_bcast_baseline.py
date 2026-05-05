#!/usr/bin/env python3
"""Plot the broadcast-baseline sweep collected by run_bcast_sweep.py.

Renders completion time (ns) vs. group size, per topology size, with
min / median / max error bars (Hoefler-style non-parametric summary).
Optionally overlays the first-principles theoretical model
``T(|G|) = T_fabric + (|G|-1) * t_ser`` for direct comparison.
Produces PDF and PNG.

CSV columns expected (produced by run_bcast_sweep.py):
    nodes, group_size, rep, op_id, root, group_idx, size, legs,
    start_ns, complete_ns, duration_ns, matrix_path

Invoke from sim/htsim-backend/plotting:
    python3 plot_bcast_baseline.py \\
        --csv ../sim/datacenter/connection_matrices/bcast_sweep/results.csv \\
        --out bcast_baseline --theory          # linear-y, log-x
    python3 plot_bcast_baseline.py \\
        --csv ... --out bcast_baseline_loglog --loglog --theory
"""

import argparse
import csv
import math
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt


def load_results(path):
    """Return dict keyed by (nodes, group_size, mode) -> list[duration_ns].

    Pre-phase-2 CSVs lack a 'mode' column; rows without one are
    bucketed under mode="baseline" for backwards compatibility.
    """
    buckets = defaultdict(list)
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            mode = row.get("mode", "baseline") or "baseline"
            key = (int(row["nodes"]), int(row["group_size"]), mode)
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
    p.add_argument(
        "--loglog",
        action="store_true",
        help="Log-scale both axes (straight line => linear-in-|G|)",
    )
    p.add_argument(
        "--offset-topologies",
        action="store_true",
        default=True,
        help="Apply a tiny multiplicative x-offset per topology so the "
        "three curves don't stack on top of each other when they "
        "produce identical durations (default: on)",
    )
    p.add_argument(
        "--no-offset-topologies",
        action="store_false",
        dest="offset_topologies",
    )
    p.add_argument(
        "--theory",
        action="store_true",
        help="Overlay the first-principles theoretical curve "
             "T(|G|) = T_fabric + (|G|-1)*t_ser.",
    )
    p.add_argument(
        "--mmm",
        action="store_true",
        help="Render min/max as a translucent band per topology "
             "with the median as a solid line on top, instead of "
             "median+errorbar. Makes the run-to-run spread (which "
             "is only visible at small |G|) easier to read.",
    )
    p.add_argument(
        "--theory-tser-ns",
        type=float,
        default=168.96,
        help="Per-leg serialisation cost t_ser in ns. Default 168.96 = "
             "one MSS-sized packet (2048+64 = 2112 B at 100 Gbps).",
    )
    p.add_argument(
        "--theory-tfabric-min-ns",
        type=float,
        default=968.96,
        help="Lower-bound T_fabric (intra-rack) in ns. "
             "Default 968.96 = 1 queue drain (168.96 ns) + 2 pipe "
             "propagations (400 ns) for the 2-hop intra-rack path.",
    )
    p.add_argument(
        "--theory-tfabric-max-ns",
        type=float,
        default=3244.80,
        help="Upper-bound T_fabric (cross-pod) in ns. "
             "Default 3244.80 = 5 queue drains (168.96 ns each) + "
             "6 pipe propagations (400 ns each) for the 6-hop "
             "cross-pod path.",
    )
    p.add_argument(
        "--theory-samples",
        type=int,
        default=80,
        help="Number of log-spaced sample points for the theoretical "
             "curves (smoother on log axes when larger).",
    )
    args = p.parse_args()

    buckets = load_results(args.csv)
    if not buckets:
        sys.exit(f"no rows in {args.csv}")

    # Organise by (topology size, mode) so we get one line per
    # (fat-tree, mode) pair. Phase-1 CSVs (no mode column) end up
    # under ("baseline",) only via the load_results back-compat
    # default.
    by_topo_mode = defaultdict(list)
    # (nodes, mode) -> list[(group_size, durations)]
    for (nodes, g, mode), durs in buckets.items():
        by_topo_mode[(nodes, mode)].append((g, durs))
    for k in by_topo_mode:
        by_topo_mode[k].sort(key=lambda t: t[0])
    # Legacy alias used by the per-topology offset map below.
    by_nodes = defaultdict(list)
    for (nodes, mode), series in by_topo_mode.items():
        by_nodes[nodes] = series  # any one series is enough for x-axis

    fig, ax = plt.subplots(figsize=(8, 5))

    # Spread the three topologies apart on the x-axis by a tiny
    # multiplicative factor so identical points become visually
    # distinguishable. Pure cosmetics; the absolute offset (a few %)
    # is well below the gap between adjacent powers of two.
    n_topos = len(by_nodes)
    offsets = {}
    if args.offset_topologies and n_topos > 1:
        for i, nodes in enumerate(sorted(by_nodes)):
            # center the set of topologies on 1.0
            offsets[nodes] = 1.0 + 0.04 * (i - (n_topos - 1) / 2)
    else:
        for nodes in by_nodes:
            offsets[nodes] = 1.0

    # Mode → linestyle map. Baseline gets a solid line (the
    # historical default); mcast gets dashed so the two are
    # immediately distinguishable on the same axis.
    mode_style = {"baseline": "-", "mcast": "--"}

    for (nodes, mode), series in sorted(by_topo_mode.items()):
        xs = [g * offsets[nodes] for g, _ in series]
        medians = [sorted(d)[len(d) // 2] for _, d in series]
        mins = [min(d) for _, d in series]
        maxs = [max(d) for _, d in series]
        ls = mode_style.get(mode, "-")
        label_suffix = "" if mode == "baseline" else f" [{mode}]"
        if args.mmm:
            line, = ax.plot(
                xs,
                medians,
                marker="o",
                markersize=5,
                linewidth=1.5,
                linestyle=ls,
                label=f"{nodes}-host fat-tree (median){label_suffix}",
                zorder=3,
            )
            color = line.get_color()
            ax.fill_between(
                xs,
                mins,
                maxs,
                color=color,
                alpha=0.22,
                edgecolor="none",
                zorder=1,
            )
            ax.plot(
                xs, mins,
                color=color, linestyle=":", linewidth=0.9,
                alpha=0.7, zorder=2,
            )
            ax.plot(
                xs, maxs,
                color=color, linestyle=":", linewidth=0.9,
                alpha=0.7, zorder=2,
            )
        else:
            lower_err = [m - lo for m, lo in zip(medians, mins)]
            upper_err = [hi - m for m, hi in zip(medians, maxs)]
            ax.errorbar(
                xs,
                medians,
                yerr=[lower_err, upper_err],
                marker="o",
                capsize=3,
                markersize=5,
                linestyle=ls,
                label=f"{nodes}-host fat-tree{label_suffix}",
                linewidth=1.3,
                alpha=0.85,
                zorder=3,
            )

    # Overlay theoretical lower and upper bounds. The lower bound
    # uses intra-rack T_fabric (the shortest fabric path), the upper
    # bound uses cross-pod T_fabric (the longest). Both share the
    # same per-leg slope t_ser.
    if args.theory:
        g_min = min(g for (_, g), _ in buckets.items())
        g_max = max(g for (_, g), _ in buckets.items())
        if g_min < 1:
            g_min = 1
        log_lo = math.log2(g_min)
        log_hi = math.log2(g_max)
        n_samples = max(args.theory_samples, 2)
        xs_t = [
            2.0 ** (log_lo + i * (log_hi - log_lo) / (n_samples - 1))
            for i in range(n_samples)
        ]
        ys_lo = [
            args.theory_tfabric_min_ns + (x - 1.0) * args.theory_tser_ns
            for x in xs_t
        ]
        ys_hi = [
            args.theory_tfabric_max_ns + (x - 1.0) * args.theory_tser_ns
            for x in xs_t
        ]
        ax.fill_between(
            xs_t, ys_lo, ys_hi,
            color="black", alpha=0.06, zorder=0,
        )
        ax.plot(
            xs_t, ys_lo,
            color="black", linestyle=":", linewidth=1.4,
            label=(
                fr"Lower bound: $T_{{\mathrm{{fab}}}}^{{\mathrm{{(intra\text{{-}}rack)}}}}"
                fr"={args.theory_tfabric_min_ns:.2f}\,$ns"
            ),
            zorder=1, alpha=0.85,
        )
        ax.plot(
            xs_t, ys_hi,
            color="black", linestyle="--", linewidth=1.4,
            label=(
                fr"Upper bound: $T_{{\mathrm{{fab}}}}^{{\mathrm{{(cross\text{{-}}pod)}}}}"
                fr"={args.theory_tfabric_max_ns:.2f}\,$ns"
            ),
            zorder=1, alpha=0.85,
        )

    ax.set_xscale("log", base=2)
    ax.set_xlabel(r"Group size $|G|$")
    if args.loglog:
        ax.set_yscale("log", base=10)
        ax.set_ylabel("Broadcast completion time (ns, log scale)")
    else:
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
