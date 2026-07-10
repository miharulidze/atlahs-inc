#!/usr/bin/env python3
"""[D2 + SP] End-to-end INC gain vs TP communication share, WITH the SP points.

Extends ../tp_share_sweep/plot_gain_vs_tp_share.py (which stays untouched):
the same 5-config plain-TP scatter and Amdahl reference curves, plus the two
sequence-parallel re-renderings (SP-C3, SP-C5) from ./results.csv as a second
marker series, with arrows marking the C3->SP-C3 and C5->SP-C5 shifts.

Visual message: SP does not remove the INC opportunity -- it MOVES workloads
up the TP-share axis (the same layers now emit ~2x TP collectives, all of
which INC accelerates) and INC keeps paying. At the pure-TP end (C5,
share = 1) the relative gain is preserved almost exactly (93.5 -> 93.7 %)
even though the in-switch apex fusion of the monolithic AllReduce is lost:
the endpoint baseline must run the doubled collective count too, so the
RATIO survives the re-rendering.

Framing (cf. README.md): each point is a controlled within-rendering A/B;
the C3 -> SP-C3 arrow is a WORKLOAD change (2x TP collectives on the same
PP-amplified critical path, slower endpoint baseline, seq-sharded compute),
not a single-variable delta.
"""
import argparse
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# categorical arm palette (house style, cf. ../tp_share_sweep/, allreduce_ab/)
C_INC = "#1b9e77"     # teal-green (measured INC gain, plain-TP rendering)
C_SP = "#e7298a"      # magenta (measured INC gain, SP rendering)
C_IDEAL = "#444444"   # grey (analytic reference)


def read_plain(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({
                "cfg": r["config"],
                "label": f'{r["config"]}: TP{r["tp"]}/DP{r["dp"]}/PP{r["pp"]}',
                "share": float(r["tp_comm_share"]),
                "gain": float(r["gain_pct"]),
            })
    rows.sort(key=lambda d: d["share"])
    return rows


def read_sp(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            if not r["config"].startswith("SP-"):
                continue  # anchor rows duplicate the plain sweep
            rows.append({
                "cfg": r["config"],
                "label": f'{r["config"]}: {r["parallelism"]}',
                "share": float(r["tp_comm_share"]),
                "gain": float(r["gain_pct"]),
            })
    rows.sort(key=lambda d: d["share"])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plain-csv", default="../tp_share_sweep/results.csv")
    ap.add_argument("--sp-csv", default="results.csv")
    ap.add_argument("--out", default="gain_vs_tp_share_with_sp")
    args = ap.parse_args()

    plain = read_plain(args.plain_csv)
    sp = read_sp(args.sp_csv)

    # measured pure-TP speedup anchors the Amdahl reference curve (as in D2)
    s_meas = None
    for d in plain:
        if d["share"] == 1.0:
            s_meas = 1.0 / (1.0 - d["gain"] / 100.0)

    fig, ax = plt.subplots(figsize=(7.2, 5.2))

    shares = np.logspace(np.log10(2e-3), 0, 200)
    ax.plot(shares, 100 * shares, "--", color=C_IDEAL, alpha=0.8, lw=1.2,
            label="Amdahl ceiling  gain = share  (S→∞)")
    if s_meas:
        ax.plot(shares, 100 * shares * (1 - 1 / s_meas), ":", color=C_IDEAL,
                lw=1.2,
                label=f"Amdahl  gain = share·(1−1/S),  "
                      f"S = {s_meas:.1f} (measured, C5)")

    ax.plot([d["share"] for d in plain], [d["gain"] for d in plain], "o",
            color=C_INC, ms=8, zorder=3,
            label="measured gain, plain-TP rendering (AllReduce)")
    ax.plot([d["share"] for d in sp], [d["gain"] for d in sp], "s",
            color=C_SP, ms=8, zorder=4, mfc="none", mew=1.8,
            label="measured gain, SP rendering (ReduceScatter+AllGather)")

    off = {"C1": (-8, 6), "C2": (8, -12), "C3": (-4, -14), "C4": (8, -12),
           "C5": (-12, -11), "SP-C3": (9, -3), "SP-C5": (-12, 3)}
    ha = {"C5": "right", "C1": "right", "SP-C5": "right", "C3": "right"}
    for d in plain + sp:
        ax.annotate(d["label"], (d["share"], d["gain"]),
                    textcoords="offset points",
                    xytext=off.get(d["cfg"], (8, 2)),
                    ha=ha.get(d["cfg"], "left"), fontsize=8,
                    color=C_SP if d["cfg"].startswith("SP-") else "black")

    # --- the two rendering-shift arrows ---
    c3 = next(d for d in plain if d["cfg"] == "C3")
    spc3 = next(d for d in sp if d["cfg"] == "SP-C3")
    ax.annotate("", xy=(spc3["share"] * 0.93, spc3["gain"] * 0.90),
                xytext=(c3["share"] * 1.07, c3["gain"] * 1.12),
                arrowprops=dict(arrowstyle="-|>", color=C_SP, lw=1.4,
                                shrinkA=0, shrinkB=0,
                                connectionstyle="arc3,rad=-0.15"))
    ax.annotate("SP re-rendering: ~2×\n"
                "TP collectives on the\n"
                "same PP-amplified\n"
                "critical path (2.40 →\n"
                "14.15 %, not like-for-like)",
                (0.0022, 11.0),
                ha="left", va="center", fontsize=7.5, color=C_SP)

    c5 = next(d for d in plain if d["cfg"] == "C5")
    spc5 = next(d for d in sp if d["cfg"] == "SP-C5")
    ax.annotate("C5 → SP-C5: share = 1 unchanged,\n"
                "gain preserved (93.46 → 93.68 %)\n"
                "despite losing apex fusion",
                xy=(spc5["share"], spc5["gain"] * 0.80),
                xytext=(spc5["share"] * 0.95, 17),
                ha="right", va="top", fontsize=7.5, color=C_SP,
                arrowprops=dict(arrowstyle="-|>", color=C_SP, lw=1.4,
                                shrinkB=2, connectionstyle="arc3,rad=-0.25"))

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("TP communication share of bytes  "
                  "(coll / (coll + send), INC-arm .goal)")
    ax.set_ylabel("end-to-end INC gain per iteration  (%)")
    ax.set_title("llama3 16-rank two-tier A/B: INC gain vs TP share, "
                 "plain-TP vs SP rendering\n"
                 "scale-out tree16 (100 Gbps, lossy) + per-node single-switch "
                 "scale-up (3600 Gbps, lossless PFC)", fontsize=10)
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8, loc="upper left")

    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf")


if __name__ == "__main__":
    main()
