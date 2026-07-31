#!/usr/bin/env python3
"""Case-study figures derived from recorded sweep CSVs -- no simulation.

Fig A is the chapter's result summary.  Paired stacked bars show absolute
baseline and in-network iteration time, modeled-compute/communication shares,
and the resulting speedup at the 4000/400-Gb/s reference point.

Fig B (scaling, REVIEW ONLY -- not included in the thesis): end-to-end
speedup vs scale-up domain width, grouped by operating point.

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
COMPUTE_MODEL = "h100_te"

SCALES = [  # (label, internode-sweep csv suffix, config tag, TP width)
    ("TP4·DP4\n(16 GPUs)", "_h100_te", "pp1_tp4_dp4_pp1", 4),
    ("TP8·DP4\n(32 GPUs)", "_g32_h100_te", "pp1_tp8_dp4_pp1", 8),
    ("TP16·DP4\n(64 GPUs)", "_g64_h100_te", "pp1_tp16_dp4_pp1", 16),
]
# operating points: (fixed intranode Gbps, swept so Gbps, label, bar color)
POINTS = [
    (4000, 400, "reference (4000 / 400 Gbps)", "#1f77b4"),
    (4000, 800, "faster scale-out (4000 / 800 Gbps)", "#5fa2d3"),
    (8000, 800, "faster two-tier point (8000 / 800 Gbps)", "#ff7f0e"),
]


def cell(su, suffix, cfg, so):
    """(baseline_s, inc_s, compute_s) for one measured cell, or None."""
    path = os.path.join(RES, f"sweep_internode_su{su}{suffix}.csv")
    if not os.path.isfile(path):
        return None
    rows = [r for r in csv.DictReader(open(path))
            if r["config"] == cfg and int(r["so_gbps"]) == so
            and r.get("compute_model") == COMPUTE_MODEL]
    by = {r["arm"]: r for r in rows if r["time_per_iter_s"]}
    if "baseline" not in by or "inc" not in by:
        return None
    return (float(by["baseline"]["time_per_iter_s"]),
            float(by["inc"]["time_per_iter_s"]),
            int(by["baseline"]["compute_ns_per_iter"]) / 1e9)


def fig_a():
    """Absolute outcome, time composition, and speedup in one plot."""
    fig, ax = plt.subplots(figsize=(8.6, 4.9))
    rows = []
    for label, suffix, cfg, tp in SCALES:
        c = cell(4000, suffix, cfg, 400)
        if c is None:
            print(f"fig A: missing cell for {cfg}, skipped")
            continue
        base_ms, inc_ms, comp_ms = (v * 1e3 for v in c)
        rows.append((tp, base_ms, inc_ms, comp_ms))

    compute_color = "#bdbdbd"
    communication_color = "#6baed6"
    bar_width = 0.72
    pair_offset = 0.43
    centers = [i * 2.35 for i in range(len(rows))]
    xticks, xlabels = [], []

    for i, ((tp, base_ms, inc_ms, comp_ms), center) in enumerate(zip(rows, centers)):
        pair = (("baseline", base_ms, center - pair_offset),
                ("in-network", inc_ms, center + pair_offset))
        for arm, total_ms, x in pair:
            comm_ms = total_ms - comp_ms
            compute_pct = 100 * comp_ms / total_ms
            comm_pct = 100 - compute_pct
            ax.bar(x, comp_ms, width=bar_width, color=compute_color,
                   edgecolor="white", linewidth=0.6,
                   label="modeled compute" if i == 0 and arm == "baseline" else None)
            ax.bar(x, comm_ms, bottom=comp_ms, width=bar_width,
                   color=communication_color, edgecolor="white", linewidth=0.6,
                   label="exposed communication" if i == 0 and arm == "baseline" else None)
            ax.text(x, comp_ms / 2, f"{compute_pct:.1f}%",
                    ha="center", va="center", fontsize=10)
            ax.text(x, comp_ms + comm_ms / 2, f"{comm_pct:.1f}%",
                    ha="center", va="center", fontsize=10, color="#102a43")
            ax.text(x, total_ms + 1.1, f"{total_ms:.1f} ms",
                    ha="center", va="bottom", fontsize=9.2)
            xticks.append(x)
            xlabels.append(f"TP{tp}\n{arm}")

        bracket_y = max(base_ms, inc_ms) + 8.0
        left, right = center - pair_offset, center + pair_offset
        ax.plot([left, left, right, right],
                [bracket_y - 1.2, bracket_y, bracket_y, bracket_y - 1.2],
                color="#333333", lw=1.0, clip_on=False)
        ax.text(center, bracket_y + 1.0, f"{base_ms / inc_ms:.3f}×",
                ha="center", va="bottom", fontsize=12, fontweight="bold")

    ax.set_xticks(xticks)
    ax.set_xticklabels(xlabels, fontsize=9.5)
    ax.set_ylabel("Iteration time (ms)", fontsize=12)
    ax.set_title("End-to-end outcome at 4000/400 Gb/s", fontsize=13)
    ax.set_ylim(0, max(r[1] for r in rows) * 1.20)
    ax.set_xlim(centers[0] - 1.05, centers[-1] + 1.05)
    ax.grid(True, axis="y", ls=":", alpha=0.45)
    ax.set_axisbelow(True)
    ax.legend(fontsize=9.5, loc="upper right", frameon=False)

    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(RES, f"case_study_result_summary.{ext}"),
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


def _sweep_rows(name, cfg):
    path = os.path.join(RES, f"{name}.csv")
    if not os.path.isfile(path):
        return []
    return [r for r in csv.DictReader(open(path))
            if r["config"] == cfg and r["time_per_iter_s"]
            and r.get("compute_model") == COMPUTE_MODEL]


def fig_c():
    """Consolidated intranode-sweep speedup: all three scales as lines on one
    axes (inter-node fixed at the 400-Gb/s reference rate)."""
    fig, ax = plt.subplots(figsize=(8, 5.2))
    colors = {4: "#1f77b4", 8: "#ff7f0e", 16: "#2ca02c"}
    for label, suffix, cfg, tp in SCALES:
        rows = _sweep_rows(f"sweep_ib400{suffix}", cfg)
        by = {}
        for r in rows:
            by.setdefault(int(r["intranode_linkspeed_gbps"]), {})[r["arm"]] = \
                float(r["time_per_iter_s"])
        pts = sorted((g, v["baseline"] / v["inc"]) for g, v in by.items()
                     if "baseline" in v and "inc" in v)
        if not pts:
            print(f"fig C: no rows for {cfg}, skipped")
            continue
        xs, ys = zip(*pts)
        ax.plot(xs, ys, marker="o", ms=6, color=colors[tp],
                label=label.replace("\n", " "))
        for x, y in pts:
            if x == 4000:
                ax.scatter(x, y, s=58, color=colors[tp], edgecolor="white",
                           linewidth=0.8, zorder=4)
                ax.annotate(f"{y:.3f}×", (x, y), xytext=(7, 2),
                            textcoords="offset points", fontsize=8,
                            color=colors[tp], fontweight="bold")
    ax.axhline(1.0, color="grey", ls=":", lw=1.2, label="no speedup (1.0×)")
    ax.axvline(4000, color="grey", ls="--", lw=1.2, alpha=0.8)
    ax.text(4000, 0.995, " operating point", rotation=90, va="bottom",
            ha="right", fontsize=9, color="#555555")
    ax.set_xscale("log", base=2)
    xs_all = sorted({int(float(x)) for l in ax.get_lines()
                     for x in l.get_xdata() if float(x) > 1})
    if xs_all:
        ax.set_xticks(xs_all)
        ax.set_xticklabels([str(x) for x in xs_all])
        ax.minorticks_off()
    ax.set_xlabel("Nominal scale-up link rate (Gb/s)", fontsize=13)
    ax.set_ylabel("End-to-end speedup  (baseline / in-network)", fontsize=13)
    ax.grid(True, which="both", ls=":", alpha=0.5)
    ax.legend(fontsize=10)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(RES, f"case_study_intranode_speedup.{ext}"),
                    dpi=150 if ext == "png" else None)
    plt.close(fig)
    print("fig C written")


def fig_d():
    """Consolidated internode-sweep speedup for every available SU rate."""
    fig, ax = plt.subplots(figsize=(8, 5.2))
    colors = {4: "#1f77b4", 8: "#ff7f0e", 16: "#2ca02c"}
    plotted_su = set()
    for su, ls in ((4000, "-"), (8000, "--")):
        for label, suffix, cfg, tp in SCALES:
            rows = _sweep_rows(f"sweep_internode_su{su}{suffix}", cfg)
            by = {}
            for r in rows:
                by.setdefault(int(r["so_gbps"]), {})[r["arm"]] = \
                    float(r["time_per_iter_s"])
            pts = sorted((g, v["baseline"] / v["inc"]) for g, v in by.items()
                         if "baseline" in v and "inc" in v)
            if not pts:
                print(f"fig D: no rows for su{su} {cfg}, skipped")
                continue
            xs, ys = zip(*pts)
            ax.plot(xs, ys, marker="o" if su == 4000 else "^", ms=5,
                    ls=ls, color=colors[tp],
                    label=f"{label.splitlines()[0]}, intranode {su} Gbps")
            plotted_su.add(su)
    ax.axhline(1.0, color="grey", ls=":", lw=1.2)
    ax.set_xscale("log", base=2)
    xs_all = sorted({int(float(x)) for l in ax.get_lines()
                     for x in l.get_xdata() if float(x) > 1})
    if xs_all:
        ax.set_xticks(xs_all)
        ax.set_xticklabels([str(x) for x in xs_all])
        ax.minorticks_off()
    ax.set_xlabel("Inter-node Link Speed (Gbps)", fontsize=13)
    ax.set_ylabel("End-to-end Speedup  (baseline / INC)", fontsize=13)
    if plotted_su == {4000}:
        subtitle = "three domain widths; intranode fixed at 4000 Gbps"
    else:
        subtitle = "solid = intranode 4000, dashed = 8000 Gbps"
    ax.set_title(f"INC Speedup vs Inter-node Link Speed\n({subtitle})", fontsize=13)
    ax.grid(True, which="both", ls=":", alpha=0.5)
    ax.legend(fontsize=8)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(RES, f"case_study_internode_speedup.{ext}"),
                    dpi=150 if ext == "png" else None)
    plt.close(fig)
    print("fig D written")


if __name__ == "__main__":
    fig_a()
    fig_b()
    fig_c()
    fig_d()
    sys.exit(0)
