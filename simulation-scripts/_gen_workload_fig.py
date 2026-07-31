#!/usr/bin/env python3
"""Workload-illustration figure for the case-study chapter (user request
2026-07-30): what traffic does one training iteration actually produce?

Panel (a): rank-to-rank traffic matrix, MEASURED by parsing every `send` of
the generated baseline trace (TP16*DP4, b=32) and summing bytes per
(source, destination). Shows the two traffic classes and their locality:
TP rings inside each 16-GPU domain block, DP rings striping across domains.

Panel (b): structural timeline of one rank's iteration, drawn from the
dependency-graph structure (not simulated times): per layer, forward =
attention -> AllReduce -> MLP -> AllReduce; backward mirrored; gradient
synchronisation at the end. Widths are schematic.

Run inside the atlahs-sim container from /workspace/simulation-scripts.
"""
import os
import re
import subprocess
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib.patches import Rectangle

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import paths  # noqa: E402

RES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "results", "intranode_linkspeed_sweep")
TMP = "/tmp/workload_fig"
TP, DP, RANKS, BATCH = 16, 4, 64, 32


def build_trace():
    graphs = os.path.join(TMP, "graphs")
    goal_path = os.path.join(TMP, "base.goal")
    if os.path.isfile(goal_path):
        return goal_path
    os.makedirs(graphs, exist_ok=True)
    env = dict(os.environ, COMPUTE_MODEL="h100_te", INC_CONTEXTS="tp", EMIT_INC="0")
    subprocess.run([sys.executable, "-m", "simple_sim.llama3_training",
                    "--tp", str(TP), "--dp", str(DP), "--pp", "1",
                    "--num-layers", "2", "--seq-len", "4096", "--ffn", "11008",
                    "--hidden", "4096", "--heads", "32", "--kv-heads", "32",
                    "--batch", str(BATCH), "--iters", "1",
                    "--graphs-dir", graphs],
                   env=env, cwd=paths.GENERATOR_DIR, check=True,
                   capture_output=True)
    subprocess.run([sys.executable, "simple_sim2goal.py", "--graphs-dir",
                    graphs, "--out-goal", goal_path],
                   env=env, cwd=paths.GENERATOR_DIR, check=True,
                   capture_output=True)
    return goal_path


def traffic_matrix(goal_path):
    m = np.zeros((RANKS, RANKS))
    rank = None
    rank_re = re.compile(r"^rank (\d+)")
    send_re = re.compile(r": send (\d+)b to (\d+)")
    with open(goal_path) as f:
        for ln in f:
            r = rank_re.match(ln)
            if r:
                rank = int(r.group(1))
                continue
            s = send_re.search(ln)
            if s and rank is not None:
                m[rank, int(s.group(2))] += int(s.group(1))
    return m


def panel_a(ax, m):
    # pcolormesh (vector quads), NOT imshow: the imshow raster silently fails
    # to render in the thesis PDF toolchain (blank matrix in print). Masked
    # zero cells stay white; the sparse ring diagonals stay crisp vectors.
    mm = np.ma.masked_where(m <= 0, m) / 1e9
    edges = np.arange(RANKS + 1) - 0.5
    im = ax.pcolormesh(edges, edges, mm,
                       norm=LogNorm(vmin=mm.min(), vmax=mm.max()),
                       cmap="viridis")
    ax.invert_yaxis()
    ax.set_aspect("equal")
    for d in range(DP):  # outline each scale-up domain's 16x16 block
        ax.add_patch(Rectangle((d * TP - 0.5, d * TP - 0.5), TP, TP,
                               fill=False, ec="#999999", lw=0.9))
    ax.set_xlabel("destination rank", fontsize=12)
    ax.set_ylabel("source rank", fontsize=12)
    ax.set_title("(a)", fontsize=12, loc="left")
    ax.set_xticks([0, 15, 31, 47, 63])
    ax.set_yticks([0, 15, 31, 47, 63])
    ax.tick_params(labelsize=10)
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
    cbar.set_label("GB sent", fontsize=11)
    cbar.ax.tick_params(labelsize=10)


def panel_b(ax):
    """Structural timeline of one rank (widths schematic, order exact)."""
    comp_c, tp_c, dp_c = "#bdbdbd", "#1f77b4", "#8ecae6"
    segs = []
    for phase in ("fwd L1", "fwd L2"):
        segs += [(phase, 2.0, comp_c), (None, 0.7, tp_c),
                 (None, 2.0, comp_c), (None, 0.7, tp_c)]
    for phase in ("bwd L2", "bwd L1"):
        segs += [(phase, 3.4, comp_c), (None, 0.7, tp_c),
                 (None, 3.4, comp_c), (None, 0.7, tp_c)]
    segs += [("gradient\nsync", 4.4, dp_c)]
    # group labels spanning each phase (compute + its two ARs)
    x = 0.0
    starts = []
    for label, w, c in segs:
        ax.add_patch(Rectangle((x, 0.35), w, 0.5, fc=c, ec="white", lw=0.8))
        starts.append((x, w, label, c))
        x += w
    total = x
    # phase brackets underneath
    phase_spans = [("forward L1", 0, 5.4), ("forward L2", 5.4, 10.8),
                   ("backward L2", 10.8, 19.0), ("backward L1", 19.0, 27.2),
                   ("DP sync", 27.2, total)]
    for name, a, b_ in phase_spans:
        ax.annotate("", xy=(a + 0.08, 0.22), xytext=(b_ - 0.08, 0.22),
                    arrowprops=dict(arrowstyle="-", color="#555555", lw=0.9))
        ax.text((a + b_) / 2, 0.08, name, ha="center", va="center", fontsize=10)
    # legend
    handles = [Rectangle((0, 0), 1, 1, fc=comp_c),
               Rectangle((0, 0), 1, 1, fc=tp_c),
               Rectangle((0, 0), 1, 1, fc=dp_c)]
    ax.legend(handles,
              ["compute",
               "TP AllReduce (1.07 GB, inside the domain)",
               "DP gradient ring (across domains)"],
              loc="upper center", bbox_to_anchor=(0.5, 1.16), ncol=2,
              fontsize=10, frameon=False)
    ax.set_xlim(0, total)
    ax.set_ylim(0, 1.15)
    ax.axis("off")
    ax.text(-0.02, 0.55, "(b)", fontsize=12, ha="right", va="center",
            transform=ax.transAxes)


def main():
    goal_path = build_trace()
    m = traffic_matrix(goal_path)
    tp_bytes = sum(m[i, j] for i in range(RANKS) for j in range(RANKS)
                   if i // TP == j // TP)
    dp_bytes = m.sum() - tp_bytes
    print(f"trace totals: TP {tp_bytes/1e9:.1f} GB "
          f"({100*tp_bytes/m.sum():.0f}%), DP {dp_bytes/1e9:.1f} GB")
    fig = plt.figure(figsize=(7.0, 7.6))
    gs = fig.add_gridspec(2, 1, height_ratios=[2.5, 1.0], hspace=0.30)
    panel_a(fig.add_subplot(gs[0]), m)
    panel_b(fig.add_subplot(gs[1]))
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(RES, f"case_study_workload.{ext}"),
                    dpi=150 if ext == "png" else None, bbox_inches="tight")
    print("workload figure written")


if __name__ == "__main__":
    main()
