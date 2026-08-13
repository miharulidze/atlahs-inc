#!/usr/bin/env python3
"""Plot the completion-time A/B (M-A): INC vs endpoint baseline, per collective.

Measured-only (no theory/model curves, per the 2026-07-23 thesis policy). Emits:
  * figure1_allreduce_with_tree_bine.pdf — Figure-1-style AllReduce speed-up at
    |G|=64 on the single-switch fabric, including the Tree and Bine endpoint arms
    when present in the CSV.
  * inc_speedup_overview.pdf            — speed-up vs message size, 4 headline lines, one
    panel per topology (single-switch | 3-tier).
  * inc_time_bcast_reduce__<topo>.pdf   — Broadcast & Reduce (rooted duals) overlaid, with a
    right-axis speed-up line.
  * inc_time_rs_ag__<topo>.pdf          — ReduceScatter & AllGather (duals) overlaid, ditto.
  * inc_time_<collective>__<topo>.pdf   — AllReduce / composed-AR completion time.

Overlapping duals are made distinguishable by encoding: COLOUR = arm (teal in-network /
blue endpoint), LINE+FILL = collective (solid+filled vs dashed+hollow). So even under
complete overlap the dashes reveal the solid underneath and the hollow markers ring the filled.

CSV: results/scaleup_coll_ab/scaleup_coll_ab.csv.

Run in the container (matplotlib):
  docker run --rm -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
      /workspace/simulation-scripts/experiments/scaleup_coll_ab/plot.py
"""
import csv
import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from common import paths  # noqa: E402

EXP = "scaleup_coll_ab"
OUTDIR = os.environ.get("SCALEUP_OUTPUT_DIR", paths.results_dir(EXP))
CSV = os.path.join(OUTDIR, f"{EXP}.csv")

COLORS = {"allreduce": "#1f77b4", "allreduce_rs_ag": "#17becf"}
INC_C, BASE_C, SPD_C = "#159588", "#4c78a8", "#b0682f"   # in-network / endpoint / speed-up

OVERVIEW_LINES = [
    ("allreduce",      "ring",    "#1f77b4", "o", "AllReduce (ring)"),
    ("allreduce",      "rdouble", "#e08a1e", "s", "AllReduce (rec.-doubling)"),
    ("reduce_scatter", "ring",    "#2ca02c", "^", "ReduceScatter"),
    ("allgather",      "ring",    "#d62728", "D", "AllGather"),
]
FIGURE1_LINES = [
    ("ring",    "#ff7f0e", "s", "vs. ring"),
    ("rdouble", "#2ca02c", "^", "vs. recursive doubling"),
    ("tree",    "#9467bd", "o", "vs. binomial tree"),
    ("bine",    "#d62728", "X", "vs. Bine butterfly"),
]
COMPLETION_TIME_LINES = [
    ("ring",    "#ff7f0e", "s", "Ring"),
    ("rdouble", "#2ca02c", "^", "Recursive doubling"),
    ("tree",    "#9467bd", "o", "Binomial tree"),
    ("bine",    "#d62728", "X", "Bine butterfly"),
]
INC_COMPLETION_LINE = ("#159588", "P", "In-network INC")
TOPO_TITLE = {"single_switch": "single-switch crossbar", "fat3tier": "256-host 3-tier fat-tree"}


def size_label(b):
    """Message size written out, e.g. 4KB / 256MB."""
    b = int(b)
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if b >= div:
            return f"{b / div:g}{unit}"
    return f"{b}B"


def topo_short(su_topo):
    if "single_switch" in su_topo:
        return "single_switch"
    if "3tier" in su_topo:
        return "fat3tier"
    if "radix32" in su_topo:
        return "radix32"
    return su_topo.replace(".topo", "")


def load():
    with open(CSV) as f:
        rows = [r for r in csv.DictReader(f)]
    for r in rows:
        r["_topo"] = topo_short(r.get("su_topo", ""))
    return rows


def fnum(r, k):
    try:
        return float(r[k])
    except (KeyError, ValueError, TypeError):
        return None


def _size_ticks(ax, xs_all):
    ax.set_xscale("log", base=2)
    ax.set_xticks(xs_all)
    ax.set_xticklabels([size_label(x) for x in xs_all], fontsize=8, rotation=45, ha="right")
    ax.minorticks_off()
    ax.set_xlabel("message size")


def figure1_allreduce_with_tree_bine(rows, outdir):
    """Reproduce Figure 1 and add endpoint binomial-tree and Bine curves.

    This intentionally stays on the paper's 64-host single-switch geometry and
    trims the 256 MiB canonical extension, so its x range is the Figure-1 4 KiB
    through 64 MiB sweep.  It works with a subset run (for example
    ``--baseline-algos tree,bine``) as long as the result CSV also contains the
    saved ring and recursive-doubling reference rows.
    """
    sub = [r for r in rows if r["_topo"] == "single_switch"
           and r["collective"] == "allreduce"
           and fnum(r, "group_size") == 64
           and fnum(r, "msg_bytes") and int(r["msg_bytes"]) <= 67108864]
    if not sub:
        return
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    plotted = 0
    for algo, color, marker, label in FIGURE1_LINES:
        points = {int(r["msg_bytes"]): fnum(r, "speedup") for r in sub
                  if r["baseline_algo"] == algo and fnum(r, "speedup")}
        if not points:
            continue
        xs = sorted(points)
        ax.plot(xs, [points[x] for x in xs], marker=marker, ms=5, lw=1.8,
                color=color, label=label)
        plotted += 1
    if not plotted:
        plt.close(fig)
        return
    xs_all = sorted({int(r["msg_bytes"]) for r in sub})
    ax.axhline(1.0, color="#888", lw=1, ls=":")
    ax.set_yscale("log")
    _size_ticks(ax, xs_all)
    ax.set_ylabel("speed-up  (endpoint / in-network)")
    ax.set_title("AllReduce, |G| = 64, single-switch (pcm-sdk, measured)", fontsize=10)
    ax.grid(ls=":", alpha=0.5, which="both")
    ax.legend(fontsize=8, loc="upper right", framealpha=0.95)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(outdir, f"figure1_allreduce_with_tree_bine.{ext}"), dpi=150)
    print("wrote figure1_allreduce_with_tree_bine")
    plt.close(fig)


def allreduce_algorithm_speedup_by_fabric(rows, outdir):
    """Plot the Figure-1 metric for every measured 64-rank scale-up fabric.

    The original Figure-1 plot deliberately stays on the paper's single-switch
    fabric.  This companion keeps its metric and message sweep but keys the
    series by the exact topology filename, which makes an oversubscription
    sweep directly comparable without conflating it with the full-bisection
    three-tier result.
    """
    fabrics = sorted({r.get("su_topo", "") for r in rows
                      if r["collective"] == "allreduce"
                      and fnum(r, "group_size") == 64
                      and fnum(r, "msg_bytes")
                      and int(r["msg_bytes"]) <= 67108864})
    for fabric in fabrics:
        sub = [r for r in rows if r.get("su_topo", "") == fabric
               and r["collective"] == "allreduce"
               and fnum(r, "group_size") == 64
               and fnum(r, "msg_bytes")
               and int(r["msg_bytes"]) <= 67108864]
        if not sub:
            continue
        fig, ax = plt.subplots(figsize=(6.7, 4.1))
        plotted = 0
        for algo, color, marker, label in FIGURE1_LINES:
            points = {int(r["msg_bytes"]): fnum(r, "speedup") for r in sub
                      if r["baseline_algo"] == algo and fnum(r, "speedup")}
            if not points:
                continue
            xs = sorted(points)
            ax.plot(xs, [points[x] for x in xs], marker=marker, ms=5, lw=1.8,
                    color=color, label=label)
            plotted += 1
        if not plotted:
            plt.close(fig)
            continue
        xs_all = sorted({int(r["msg_bytes"]) for r in sub})
        ax.axhline(1.0, color="#888", lw=1, ls=":")
        ax.set_yscale("log")
        _size_ticks(ax, xs_all)
        ax.set_ylabel("speed-up  (endpoint / in-network)")
        ax.set_title(f"AllReduce, |G| = 64, {fabric.replace('.topo', '')} (pcm-sdk, measured)",
                     fontsize=9)
        ax.grid(ls=":", alpha=0.5, which="both")
        ax.legend(fontsize=8, loc="upper right", framealpha=0.95)
        fig.tight_layout()
        tag = fabric.replace(".topo", "")
        for ext in ("pdf", "png"):
            fig.savefig(os.path.join(outdir, f"allreduce_algorithm_speedup__{tag}.{ext}"), dpi=150)
        print(f"wrote allreduce_algorithm_speedup__{tag}")
        plt.close(fig)


def allreduce_completion_time_by_fabric(rows, outdir):
    """AllReduce completion-time plot with the scale-out plot's visual grammar."""
    fabrics = sorted({r.get("su_topo", "") for r in rows
                      if r["collective"] == "allreduce"
                      and fnum(r, "group_size") == 64
                      and fnum(r, "msg_bytes")
                      and int(r["msg_bytes"]) <= 67108864})
    for fabric in fabrics:
        sub = [r for r in rows if r.get("su_topo", "") == fabric
               and r["collective"] == "allreduce"
               and fnum(r, "group_size") == 64
               and fnum(r, "msg_bytes")
               and int(r["msg_bytes"]) <= 67108864]
        if not sub:
            continue
        fig, ax = plt.subplots(figsize=(6.7, 4.2))
        for algo, color, marker, label in COMPLETION_TIME_LINES:
            points = {int(r["msg_bytes"]): fnum(r, "base_ns") for r in sub
                      if r["baseline_algo"] == algo and fnum(r, "base_ns")}
            if not points:
                continue
            xs = sorted(points)
            ax.plot(xs, [points[x] for x in xs], color=color, marker=marker,
                    ms=5, lw=1.8, label=label)
        inc = {int(r["msg_bytes"]): fnum(r, "inc_ns") for r in sub if fnum(r, "inc_ns")}
        if inc:
            color, marker, label = INC_COMPLETION_LINE
            xs = sorted(inc)
            ax.plot(xs, [inc[x] for x in xs], color=color, marker=marker,
                    ms=5, lw=1.8, label=label)
        ax.set_yscale("log")
        _size_ticks(ax, sorted({int(r["msg_bytes"]) for r in sub}))
        ax.set_ylabel("endpoint completion time (ns)")
        ax.set_title(f"AllReduce, |G|=64, {fabric.replace('.topo', '')}", fontsize=9)
        ax.grid(ls=":", alpha=0.5, which="both")
        ax.legend(fontsize=8)
        fig.tight_layout()
        tag = fabric.replace(".topo", "")
        for ext in ("pdf", "png"):
            fig.savefig(os.path.join(outdir, f"allreduce_completion_time__{tag}.{ext}"), dpi=150)
        print(f"wrote allreduce_completion_time__{tag}")
        plt.close(fig)


def speedup_overview(rows, outdir):
    topos = [t for t in ("single_switch", "fat3tier") if any(r["_topo"] == t for r in rows)]
    if not topos:
        return
    fig, axes = plt.subplots(1, len(topos), figsize=(6.2 * len(topos), 4.6),
                             squeeze=False, sharey=True)
    for j, topo in enumerate(topos):
        ax = axes[0][j]
        sub = [r for r in rows if r["_topo"] == topo]
        xs_all = sorted({int(r["msg_bytes"]) for r in sub if fnum(r, "msg_bytes")})
        for coll, algo, color, mk, lab in OVERVIEW_LINES:
            d = {int(r["msg_bytes"]): fnum(r, "speedup") for r in sub
                 if r["collective"] == coll and r["baseline_algo"] == algo and fnum(r, "speedup")}
            if not d:
                continue
            xs = sorted(d)
            ax.plot(xs, [d[x] for x in xs], marker=mk, color=color, lw=1.7, ms=5,
                    label=(lab if j == len(topos) - 1 else None))
        ax.axhline(1.0, ls=":", color="#888", lw=1,
                   label=("no speed-up (1×)" if j == len(topos) - 1 else None))
        ax.set_yscale("log")
        _size_ticks(ax, xs_all)
        if j == 0:
            ax.set_ylabel("speedup")
        ax.set_title(TOPO_TITLE.get(topo, topo), fontsize=11)
        ax.grid(ls=":", alpha=0.5, which="both")
        yt = [1, 2, 5, 10, 20, 50, 100, 200]
        ax.set_yticks(yt)
        ax.set_yticklabels([f"{v}×" for v in yt])
        ax.minorticks_off()
    gs = rows[0].get("group_size", "?")
    axes[0][-1].legend(fontsize=8.5, loc="upper right", framealpha=0.95)
    fig.suptitle(f"In-network vs endpoint speed-up   (|G|={gs}, pcm-sdk, measured)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(outdir, f"inc_speedup_overview.{ext}"), dpi=150)
    print("wrote inc_speedup_overview")
    plt.close(fig)


def merged_dual_plot(rows, topo, outdir, first, second, out_tag, title):
    """Overlay two dual collectives (first, second) — each a (kind, label, marker) — on one plot:
    completion time, 4 curves ({first,second} × {in-network, endpoint ring}). The two duals coincide,
    so each collective's markers are given a small multiplicative x-DODGE (first left, second right) to
    read as two distinct series. Colour = arm (teal in-network / blue endpoint), marker = collective."""
    (c1, l1, m1), (c2, l2, m2) = first, second
    sub = [r for r in rows if r["_topo"] == topo and r["collective"] in (c1, c2)]
    if not sub:
        return

    def series(coll, arm):
        key = "inc_ns" if arm == "inc" else "base_ns"
        return {int(r["msg_bytes"]): fnum(r, key) for r in sub
                if r["collective"] == coll and (arm == "inc" or r["baseline_algo"] == "ring")
                and fnum(r, key)}

    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    xs_all = sorted({int(r["msg_bytes"]) for r in sub if fnum(r, "msg_bytes")})
    D = 1.11  # multiplicative x-dodge (~0.15 octave; adjacent points are 2 octaves apart)
    # (collective, label, marker, arm, x-dodge). first -> left, second -> right.
    specs = [(c1, l1, m1, "inc",  1 / D), (c2, l2, m2, "inc",  D),
             (c1, l1, m1, "base", 1 / D), (c2, l2, m2, "base", D)]
    for coll, lab, mk, arm, dodge in specs:
        d = series(coll, arm)
        if not d:
            continue
        xs = sorted(d)
        color = INC_C if arm == "inc" else BASE_C
        armlab = "in-network" if arm == "inc" else "endpoint ring"
        ax.plot([x * dodge for x in xs], [d[x] for x in xs], color=color, marker=mk,
                lw=1.6, ms=6, mew=1.4, mfc=color, label=f"{lab} · {armlab}", zorder=3)
    ax.set_yscale("log")
    _size_ticks(ax, xs_all)   # ticks stay at the true sizes; the dodge is visual only
    ax.set_ylabel("completion time (ns)")
    ax.legend(fontsize=8, ncol=2, loc="upper left", framealpha=0.95)
    gs = sub[0].get("group_size", "?")
    ax.set_title(f"{title} — {topo}, |G|={gs} (pcm-sdk, measured)", fontsize=10)
    ax.grid(ls=":", alpha=0.5, which="both")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(outdir, f"inc_time_{out_tag}__{topo}.{ext}"), dpi=150)
    print(f"wrote inc_time_{out_tag}__{topo}")
    plt.close(fig)


def per_collective_time(rows, topo, coll, outdir):
    sub = [r for r in rows if r["_topo"] == topo and r["collective"] == coll]
    if not sub:
        return
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    inc = {}
    base = defaultdict(dict)
    for r in sub:
        s = fnum(r, "msg_bytes")
        if not s:
            continue
        if fnum(r, "inc_ns"):
            inc[s] = fnum(r, "inc_ns")
        if fnum(r, "base_ns"):
            base[r["baseline_algo"]][s] = fnum(r, "base_ns")
    if inc:
        xs = sorted(inc)
        ax.plot(xs, [inc[x] for x in xs], marker="o", ms=5, lw=1.8,
                color=COLORS.get(coll, "#1f77b4"), label="in-network")
    for algo, d in sorted(base.items()):
        xs = sorted(d)
        ax.plot(xs, [d[x] for x in xs], marker="s", ms=4, lw=1.3, ls="--", label=f"endpoint {algo}")
    ax.set_yscale("log")
    _size_ticks(ax, sorted(set(inc) | {x for d in base.values() for x in d}))
    ax.set_ylabel("completion time (ns)")
    gs = sub[0].get("group_size", "?")
    ax.set_title(f"{coll} — {topo}, |G|={gs} (pcm-sdk, measured)", fontsize=10)
    ax.grid(ls=":", alpha=0.5, which="both")
    ax.legend(fontsize=8)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(outdir, f"inc_time_{coll}__{topo}.{ext}"), dpi=150)
    print(f"wrote inc_time_{coll}__{topo}")
    plt.close(fig)


def main():
    if not os.path.exists(CSV):
        sys.exit(f"no results CSV at {CSV} -- run the experiment first")
    rows = load()
    if not rows:
        sys.exit(f"no usable rows in {CSV}")
    topos = sorted({r["_topo"] for r in rows})
    colls = sorted({r["collective"] for r in rows})
    figure1_allreduce_with_tree_bine(rows, OUTDIR)
    allreduce_algorithm_speedup_by_fabric(rows, OUTDIR)
    allreduce_completion_time_by_fabric(rows, OUTDIR)
    speedup_overview(rows, OUTDIR)
    for topo in topos:                     # individual per-collective completion-time plots
        for coll in colls:
            per_collective_time(rows, topo, coll, OUTDIR)


if __name__ == "__main__":
    main()
