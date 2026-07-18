#!/usr/bin/env python3
"""[SP A/B] Baseline-vs-INC iteration time, plain-TP and SP renderings.

One bar group per config (C3, SP-C3, C5, SP-C5) from ./results.csv: endpoint
baseline vs INC iteration time (log y, ns), with the per-pair gain annotated.
This is the within-rendering A/B at a glance: each pair is a controlled
experiment (same trace, same engine, same topology; only the TP collectives
move into the network).

Numbers are the 2026-07-15 re-measurement with -intranode_linkspeed 4000000 (NIC pinned to the pipes' realised rate)
(NIC-rate fix; INC arms byte-identical, all baselines moved -- cf. README.md).

Framing (cf. README.md): comparisons ACROSS a plain/SP pair of groups are
cross-workload -- the SP rendering has ~2x TP collectives, a different
endpoint baseline and less elementwise compute -- so the -0.52 % -> +10.39 %
flip at C3 is NOT a like-for-like INC improvement. The honest per-regime
statements: at C3 (placeholder compute model) plain-TP INC is NEGATIVE
(-0.52 %, sub-floor) and the SP re-rendering reads +10.39 %; at C5 the
gain rises under SP (+76.17 -> +88.14 %).
"""
import argparse
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# categorical arm palette (house style, cf. allreduce_ab/plot_speedup_vs_n.py)
C_INC = "#1b9e77"     # teal-green (INC arm)
C_BASE = "#d95f02"    # orange (endpoint/p2p baseline arm)

ORDER = ["C3-anchor", "SP-C3", "C5-anchor", "SP-C5"]
NICE = {
    "C3-anchor": "C3 (plain TP)\nTP4/DP2/PP2\n32 AllReduce",
    "SP-C3": "SP-C3\nTP4+SP/DP2/PP2\n32 AG + 28 RS",
    "C5-anchor": "C5 (plain TP)\nTP16/DP1/PP1\n16 AllReduce",
    "SP-C5": "SP-C5\nTP16+SP/DP1/PP1\n16 AG + 14 RS",
}


def ht(ns):
    """human-readable time from ns"""
    if ns >= 1e6:
        return f"{ns / 1e6:.2f} ms"
    return f"{ns / 1e3:.1f} µs"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results.csv")
    ap.add_argument("--out", default="sp_ab_bars")
    args = ap.parse_args()

    rows = {}
    with open(args.csv) as f:
        for r in csv.DictReader(f):
            rows[r["config"]] = {
                "base": float(r["baseline_ns"]),
                "inc": float(r["inc_ns"]),
                "gain": float(r["gain_pct"]),
            }
    data = [rows[c] for c in ORDER]

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    x = np.arange(len(ORDER), dtype=float)
    # extra separation between the C3-regime and C5-regime pairs
    x[2:] += 0.35
    w = 0.36

    bars_b = ax.bar(x - w / 2 - 0.015, [d["base"] for d in data], w,
                    color=C_BASE, label="endpoint baseline (decomposed p2p)")
    bars_i = ax.bar(x + w / 2 + 0.015, [d["inc"] for d in data], w,
                    color=C_INC, label="INC (in-network collectives)")

    # value labels on the bars, gain above each pair
    for xi, d, bb, bi in zip(x, data, bars_b, bars_i):
        ax.annotate(ht(d["base"]), (bb.get_x() + bb.get_width() / 2, d["base"]),
                    textcoords="offset points", xytext=(-4, 3),
                    ha="center", va="bottom", fontsize=7)
        ax.annotate(ht(d["inc"]), (bi.get_x() + bi.get_width() / 2, d["inc"]),
                    textcoords="offset points", xytext=(4, 3),
                    ha="center", va="bottom", fontsize=7)
        # signed INC gain (positive = INC faster); anchor above the taller bar
        gain_col = C_INC if d["gain"] >= 0 else "#b2182b"
        ax.annotate(f"{d['gain']:+.2f} %", (xi, max(d["base"], d["inc"])),
                    textcoords="offset points", xytext=(0, 16),
                    ha="center", va="bottom", fontsize=9.5,
                    fontweight="bold", color=gain_col)

    ax.set_yscale("log")
    ax.set_ylim(4e4, 1.2e9)
    ax.set_xticks(x)
    ax.set_xticklabels([NICE[c] for c in ORDER], fontsize=8)
    ax.set_ylabel("iteration time  (ns, log)")
    ax.set_title("llama3 16-rank two-tier A/B: endpoint vs INC per rendering\n"
                 "scale-out tree16 (100 Gbps, lossy) + per-node single-switch "
                 "scale-up (3600 Gbps, lossless PFC)", fontsize=10)
    ax.grid(True, axis="y", which="both", alpha=0.25)
    ax.set_axisbelow(True)
    ax.legend(fontsize=8, loc="upper right")

    ax.annotate("each pair = controlled within-rendering A/B; plain → SP is a "
                "workload change (2× TP collectives,\ndifferent baseline and "
                "compute) — the −0.52 → +10.39 % swing at C3 is not "
                "like-for-like (placeholder compute model)",
                xy=(0.5, -0.24), xycoords="axes fraction",
                ha="center", va="top", fontsize=7.5, color="#555555")

    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf")


if __name__ == "__main__":
    main()
