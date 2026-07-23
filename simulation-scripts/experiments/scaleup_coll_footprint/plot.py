#!/usr/bin/env python3
"""Plot the multicast bandwidth-usage-reduction figures from the footprint CSV.

Paper-style (Khalilov SC24 Fig. 2) grouped bars: relative bandwidth-usage reduction
with multicast = footprint_baseline / footprint_INC (y, byte-ratio) vs number of
participating GPUs P (x), Ring (green) vs Recursive Doubling (orange), with the
analytic 2-2/P reference line for AllGather.

Emits:
  footprint_allgather.png        — the direct Fig. 2 reproduction (single-switch | 3-tier)
  footprint_all_collectives.png  — AllGather / AllReduce / ReduceScatter x both topologies

Run in the container (matplotlib lives there):
  docker run --rm -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
      /workspace/simulation-scripts/experiments/scaleup_coll_footprint/plot.py
"""
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from common import paths  # noqa: E402

EXP = "scaleup_coll_footprint"
OUTDIR = os.environ.get("FOOTPRINT_OUTPUT_DIR", paths.results_dir(EXP))
CSV = os.path.join(OUTDIR, f"{EXP}.csv")

RING_C, RD_C = "#4c9a2a", "#e08a1e"   # green / orange (paper palette)
ANA_C = "#333333"
TOPO_LABEL = {"single_switch": "Single switch (all pairs 2 hops)",
              "fat3tier": "3-tier fat-tree (2/4/6 hops)",
              "paper_r32": "radix-32 3-tier, 1024 hosts (paper topology)"}
COLL_LABEL = {"allgather": "AllGather", "allreduce": "AllReduce",
              "reduce_scatter": "ReduceScatter"}
YLABEL = "Relative bandwidth usage\nreduction with multicast (×)"
XLABEL = "Number of participating GPUs"


def load():
    rows = []
    with open(CSV) as f:
        for r in csv.DictReader(f):
            if r.get("size_mult") not in (None, "1"):
                continue
            if not r.get("ratio_bytes"):
                continue
            rows.append(r)
    return rows


def series(rows, cls, coll, algo):
    return {int(r["group_size"]): float(r["ratio_bytes"]) for r in rows
            if r["topology_class"] == cls and r["collective"] == coll
            and r["baseline_algo"] == algo}


def panel(ax, rows, cls, coll, show_ylabel, show_legend):
    ring = series(rows, cls, coll, "ring")
    rd = series(rows, cls, coll, "rdouble")
    Ps = sorted(set(ring) | set(rd))
    if not Ps:
        ax.set_visible(False)
        return
    x = np.arange(len(Ps))
    w = 0.38
    ax.bar(x - w / 2, [ring.get(p, np.nan) for p in Ps], w, label="Ring", color=RING_C)
    ax.bar(x + w / 2, [rd.get(p, np.nan) for p in Ps], w, label="Recursive Doubling", color=RD_C)
    if coll == "allgather":  # analytic ring reference (2 - 2/P)
        ax.plot(x, [2 - 2 / p for p in Ps], "--o", color=ANA_C, ms=3, lw=1,
                label="Analytic 2−2/P")
    ax.set_xticks(x)
    ax.set_xticklabels(Ps)
    ax.set_title(f"{COLL_LABEL.get(coll, coll)} — {TOPO_LABEL.get(cls, cls)}", fontsize=9)
    ax.set_xlabel(XLABEL, fontsize=8)
    if show_ylabel:
        ax.set_ylabel(YLABEL, fontsize=8)
    ax.grid(axis="y", ls=":", alpha=0.5)
    ax.set_ylim(0, max(3.8, max([v for v in list(ring.values()) + list(rd.values())] + [0]) * 1.15))
    if show_legend:
        ax.legend(fontsize=7, loc="upper left")


def fig_allgather(rows):
    classes = [c for c in ("single_switch", "fat3tier", "paper_r32")
               if any(r["topology_class"] == c and r["collective"] == "allgather" for r in rows)]
    if not classes:
        return
    fig, axes = plt.subplots(1, len(classes), figsize=(5.2 * len(classes), 3.6), squeeze=False)
    for j, cls in enumerate(classes):
        panel(axes[0][j], rows, cls, "allgather", show_ylabel=(j == 0), show_legend=True)
    fig.suptitle("Multicast AllGather bandwidth-usage reduction (measured, pcm-sdk)", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out = os.path.join(OUTDIR, "footprint_allgather.png")
    fig.savefig(out, dpi=150)
    print("wrote", out)


def fig_all(rows):
    colls = [c for c in ("allgather", "allreduce", "reduce_scatter")
             if any(r["collective"] == c for r in rows)]
    classes = ["single_switch", "fat3tier"]
    fig, axes = plt.subplots(len(colls), len(classes),
                             figsize=(5.2 * len(classes), 3.2 * len(colls)), squeeze=False)
    for i, coll in enumerate(colls):
        for j, cls in enumerate(classes):
            panel(axes[i][j], rows, cls, coll, show_ylabel=(j == 0),
                  show_legend=(i == 0 and j == 0))
    fig.suptitle("Multicast bandwidth-usage reduction by collective and topology (measured, pcm-sdk)",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = os.path.join(OUTDIR, "footprint_all_collectives.png")
    fig.savefig(out, dpi=150)
    print("wrote", out)


def main():
    if not os.path.exists(CSV):
        sys.exit(f"no results CSV at {CSV} — run the experiment first")
    rows = load()
    if not rows:
        sys.exit(f"no usable rows in {CSV}")
    fig_allgather(rows)
    fig_all(rows)


if __name__ == "__main__":
    main()
