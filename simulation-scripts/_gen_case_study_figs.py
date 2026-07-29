#!/usr/bin/env python3
"""Case-study chapter figures A + B (AA-plan-Case-Study-Chapter, approved
2026-07-29). Derived purely from the recorded sweep CSVs -- no simulation.

Fig A (anatomy): per scale, baseline-vs-INC stacked bars splitting the
iteration into the fixed per-rank compute floor (measured from the trace) and
the communication+wait residual (makespan - compute), at the realistic
H100-class operating point (intranode 4000, inter-node 400 Gbps).

Fig B (scaling): end-to-end speedup vs scale-up domain width, grouped by
operating point: (4000,400) H100-class, (4000,800), and (8000,800) GB200-class
where measured. Missing cells are skipped with a note.

Outputs PNG (review) + PDF (thesis-grade) side by side into
results/intranode_linkspeed_sweep/.
"""
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "results", "intranode_linkspeed_sweep")

SCALES = [  # (label, internode-sweep csv suffix, config tag, TP width)
    ("TP4·DP4\n(16 GPUs)", "", "pp1_tp4_dp4_pp1", 4),
    ("TP8·DP4\n(32 GPUs)", "_g32", "pp1_tp8_dp4_pp1", 8),
    ("TP16·DP4\n(64 GPUs)", "_g64", "pp1_tp16_dp4_pp1", 16),
]
# operating points: (fixed intranode Gbps, swept so Gbps, label, bar color)
POINTS = [
    (4000, 400, "H100-class (4000 / 400 Gbps)", "#1f77b4"),
    (4000, 800, "faster NIC (4000 / 800 Gbps)", "#5fa2d3"),
    (8000, 800, "GB200-class (8000 / 800 Gbps)", "#ff7f0e"),
]


def cell(su, suffix, cfg, so):
    """(baseline_s, inc_s, compute_s) for one measured cell, or None."""
    path = os.path.join(RES, f"sweep_internode_su{su}{suffix}.csv")
    if not os.path.isfile(path):
        return None
    rows = [r for r in csv.DictReader(open(path))
            if r["config"] == cfg and int(r["so_gbps"]) == so]
    by = {r["arm"]: r for r in rows if r["time_per_iter_s"]}
    if "baseline" not in by or "inc" not in by:
        return None
    return (float(by["baseline"]["time_per_iter_s"]),
            float(by["inc"]["time_per_iter_s"]),
            int(by["baseline"]["compute_ns_per_iter"]) / 1e9)


def fig_a():
    """Anatomy bars at the H100-class point."""
    fig, ax = plt.subplots(figsize=(8, 5.2))
    width, gap = 0.34, 0.06
    xs, labels = [], []
    for i, (label, suffix, cfg, _tp) in enumerate(SCALES):
        c = cell(4000, suffix, cfg, 400)
        if c is None:
            print(f"fig A: missing cell for {cfg}, skipped")
            continue
        base_s, inc_s, comp_s = (v * 1e3 for v in c)  # -> ms
        for k, (t, xoff) in enumerate((("baseline", -width / 2 - gap / 2),
                                       ("INC", width / 2 + gap / 2))):
            total = base_s if t == "baseline" else inc_s
            x = i + xoff
            ax.bar(x, comp_s, width, color="#bdbdbd",
                   label="compute (fixed)" if i == 0 and k == 0 else None)
            ax.bar(x, total - comp_s, width, bottom=comp_s,
                   color="#1f77b4" if t == "baseline" else "#ff7f0e",
                   label=(f"{t}: communication + wait"
                          if i == 0 else None))
            ax.text(x, total + 0.6, f"{total:.1f}", ha="center", fontsize=9)
        xs.append(i)
        labels.append(label)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_ylabel("Time / Training Iteration (ms)", fontsize=13)
    ax.set_title("Anatomy of a Training Iteration — H100-class operating point\n"
                 "(intranode 4000 Gbps, inter-node 400 Gbps)", fontsize=13)
    ax.grid(True, axis="y", ls=":", alpha=0.5)
    # headroom so the legend never covers a bar's value label
    ax.set_ylim(0, ax.get_ylim()[1] * 1.28)
    ax.legend(fontsize=10, loc="upper right")
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(RES, f"case_study_anatomy.{ext}"),
                    dpi=150 if ext == "png" else None)
    plt.close(fig)
    print("fig A written")


def fig_b():
    """Scaling bars: speedup vs domain width, grouped by operating point."""
    fig, ax = plt.subplots(figsize=(8, 5.2))
    n_pts = len(POINTS)
    width = 0.8 / n_pts
    plotted = 0
    for j, (su, so, plabel, color) in enumerate(POINTS):
        xs, ys = [], []
        for i, (_label, suffix, cfg, _tp) in enumerate(SCALES):
            c = cell(su, suffix, cfg, so)
            if c is None:
                print(f"fig B: missing cell su{su}/so{so} for {cfg}, skipped")
                continue
            xs.append(i + (j - (n_pts - 1) / 2) * width)
            ys.append(c[0] / c[1])
        if not xs:
            continue
        ax.bar(xs, ys, width * 0.92, color=color, label=plabel)
        for x, y in zip(xs, ys):
            ax.text(x, y + 0.002, f"{y:.3f}", ha="center", fontsize=8)
        plotted += 1
    ax.axhline(1.0, color="grey", ls=":", lw=1.2)
    ax.set_xticks(range(len(SCALES)))
    ax.set_xticklabels([s[0] for s in SCALES], fontsize=11)
    ax.set_ylabel("End-to-end Speedup  (baseline / INC)", fontsize=13)
    ax.set_title("INC Speedup vs Scale-up Domain Width, by Operating Point",
                 fontsize=13)
    ax.set_ylim(bottom=0.98)
    ax.grid(True, axis="y", ls=":", alpha=0.5)
    ax.legend(fontsize=10, loc="upper left")
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(RES, f"case_study_scaling.{ext}"),
                    dpi=150 if ext == "png" else None)
    plt.close(fig)
    print(f"fig B written ({plotted} operating points)")


if __name__ == "__main__":
    fig_a()
    fig_b()
    sys.exit(0)
