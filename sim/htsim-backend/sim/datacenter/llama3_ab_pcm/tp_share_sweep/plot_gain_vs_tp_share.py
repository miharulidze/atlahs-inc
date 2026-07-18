#!/usr/bin/env python3
"""[D2] End-to-end INC gain vs the workload's TP communication share.

Reads results.csv (5 llama3 parallelism configs, 16 ranks each, two-tier
pcm-sdk A/B) and plots measured gain% against tp_comm_share = the fraction of
communicated bytes in the INC arm's .goal that are TP-collective
(coll bytes / (coll + send bytes), all ranks summed).

Reference curves:
  * Amdahl ceiling  gain = 100 * share            (S -> infinity)
  * Amdahl          gain = 100 * share * (1-1/S)  with S the MEASURED pure-TP
    end-to-end speedup of this very sweep (C5, share = 1; 4.20 since the
    2026-07-15 re-measurement) -- the same engine's own speedup, so the curve
    passes through C5.

Re-measured 2026-07-15 with -intranode_linkspeed 4000000 (NIC pinned to the pipes' realised rate) (the 2026-07-04
numbers were NIC-capped at the 200 Gbps COPY_ENG default, which inflated every
p2p baseline). Post-fix headline: under the placeholder compute model the
realistic mixed-parallelism configs (C1/C2/C4, share < 1%) are within noise of
zero, and C3 (PP=2) is NEGATIVE (-4.65%; open finding, mechanism under
investigation -- +8.81% under the calibrated H100 roofline compute model).
Only the pure-TP end (C5) retains a large placeholder-compute gain. The y-axis
is symlog so the negative points are visible.
"""
import argparse
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# categorical arm palette (house style, cf. allreduce_ab/plot_speedup_vs_n.py)
C_INC = "#1b9e77"     # teal-green (measured INC gain)
C_IDEAL = "#444444"   # grey (analytic reference)

S_MEASURED = None     # filled from the share==1 row (C5) if present


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results.csv")
    ap.add_argument("--out", default="gain_vs_tp_share")
    args = ap.parse_args()

    rows = []
    with open(args.csv) as f:
        for r in csv.DictReader(f):
            rows.append({
                "cfg": r["config"],
                "label": f'{r["config"]}: TP{r["tp"]}/DP{r["dp"]}/PP{r["pp"]}',
                "share": float(r["tp_comm_share"]),
                "gain": float(r["gain_pct"]),
            })
    rows.sort(key=lambda d: d["share"])

    # measured pure-TP speedup anchors the Amdahl reference curve
    s_meas = None
    for d in rows:
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

    ax.plot([d["share"] for d in rows], [d["gain"] for d in rows], "o",
            color=C_INC, ms=8, zorder=3, label="measured end-to-end gain")

    off = {"C1": (-8, 6), "C2": (8, -12), "C3": (-8, -3), "C4": (8, -12),
           "C5": (-10, -16)}
    ha = {"C5": "right", "C1": "right", "C3": "right"}
    for d in rows:
        ax.annotate(d["label"], (d["share"], d["gain"]),
                    textcoords="offset points",
                    xytext=off.get(d["cfg"], (8, 2)),
                    ha=ha.get(d["cfg"], "left"), fontsize=8)

    # post-fix callouts (2026-07-15): all sub-percent-share deltas sit below the
    # measured +-5-9% single-schedule sensitivity floor (schedule_sensitivity.md)
    c3 = next(d for d in rows if d["cfg"] == "C3")
    ax.annotate("sub-percent TP share (C1-C4):\nbelow the +-5-9% schedule-noise\nfloor, either sign unresolved\n(schedule_sensitivity.md)",
                (c3["share"], c3["gain"]), textcoords="offset points",
                xytext=(12, 2), fontsize=7.5, color="#7570b3")

    ax.axhline(0, color="#888888", lw=0.8, alpha=0.6)
    ax.set_xscale("log")
    # symlog: C1/C3 gains are NEGATIVE since the NIC-cap fix
    ax.set_yscale("symlog", linthresh=0.1)
    ax.set_xlabel("TP communication share of bytes  "
                  "(coll / (coll + send), INC-arm .goal)")
    ax.set_ylabel("end-to-end INC gain per iteration  (%)")
    ax.set_title("llama3 16-rank two-tier A/B: end-to-end INC gain vs TP share\n"
                 "scale-out tree16 (100 Gbps, lossy) + per-node single-switch "
                 "scale-up (3600 Gbps, lossless PFC)\n"
                 "re-measured 2026-07-15 with -intranode_linkspeed 4000000 (NIC pinned to the pipes' realised rate) "
                 "(placeholder compute model)", fontsize=9)
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8, loc="upper left")

    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf")


if __name__ == "__main__":
    main()
