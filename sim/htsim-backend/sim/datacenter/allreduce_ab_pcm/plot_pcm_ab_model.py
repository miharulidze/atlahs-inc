#!/usr/bin/env python3
"""Measured-ring model validation at |G| = 72 (companion to plot_pcm_ab.py).

Left panel: the completion-time plot with an ANALYTIC line for the measured
ring — the per-step stop-and-wait model of the forensics section,

    T_ring(S) = 142 x (2,600 ns + (S/72) / 492.3 B/ns)

(142 = 2(N-1) serial steps; 2,600 ns = 2 x 1,300 ns data-plus-ACK round trip
per step; the block wire time at the realised payload rate). Every constant is
read from the configuration — nothing fitted. The line predicting the measured
markers IS the validation.

Right panel: the ratio of the model's two terms,

    wire term / ACK term = (S/72 / 492.3) / 2,600,

showing which term dominates per payload: the constant 142 x 2,600 ns =
369.2 us ACK floor below the crossover, the payload-linear wire term above it.
Crossover where the terms are equal: S = 72 x 2,600 x 492.3 B ~= 87.9 MiB/rank.
"""
import argparse
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

C_INC = "#1b9e77"     # teal-green (suite palette, ColorBrewer Dark2)
C_RING = "#d95f02"    # orange
C_IDEAL = "#444444"   # grey (analytic reference)
N_GROUP = 72
STEPS = 2 * (N_GROUP - 1)          # 142
ACK_STEP_NS = 2600.0               # 2 x 1,300 ns data + ACK legs per step
RATE_BNS = 492.3                   # realised payload wire rate


def ring_model_ns(s):
    return STEPS * (ACK_STEP_NS + (s / N_GROUP) / RATE_BNS)


def term_ratio(s):
    """wire term / ACK term of one step (equivalently of the total)."""
    return ((s / N_GROUP) / RATE_BNS) / ACK_STEP_NS


def hb(n):
    n = int(n)
    if n >= 1000 * 1024:
        v = n / (1024 * 1024)
        return f"{v:.0f} MiB" if abs(v - round(v)) < 0.05 else f"{v:.3g} MiB"
    if n >= 1024:
        v = n / 1024
        return f"{v:.0f} KiB" if abs(v - round(v)) < 0.05 else f"{v:.1f} KiB"
    return f"{n} B"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results_msgsweep_pcm.csv")
    ap.add_argument("--out", default="allreduce_ab_pcm_model")
    args = ap.parse_args()

    rows = []
    with open(args.csv) as f:
        for r in csv.DictReader(f):
            if int(r["group_size"]) != N_GROUP or not r["inc_ns"] or not r["ring_ns"]:
                continue
            rows.append((int(r["msg_bytes"]), int(r["inc_ns"]), int(r["ring_ns"]),
                         float(r["ideal_ring_ns"])))
    rows.sort()
    S = [r[0] for r in rows]
    inc = [r[1] for r in rows]
    ring = [r[2] for r in rows]
    ideal = [r[3] for r in rows]
    model = [ring_model_ns(s) for s in S]

    resid = [(m - r) / r * 100 for m, r in zip(model, ring)]
    worst = max(abs(x) for x in resid)
    print("model vs measured ring:")
    for s, r, m, e in zip(S, ring, model, resid):
        print(f"  {hb(s):>9}: measured {r:>10,} ns  model {m:>12,.0f} ns  {e:+.2f}%")
    print(f"  worst residual {worst:.2f}%")

    fig, (axT, axR) = plt.subplots(1, 2, figsize=(13, 5))

    # --- left: completion time; the ring MODEL line under the measured markers ---
    axT.plot(S, model, ":", color=C_RING, lw=1.8, alpha=0.95,
             label=r"ring model  $142\times(2600\,\mathrm{ns} + \frac{S/72}{492.3\,\mathrm{B/ns}})$")
    axT.plot(S, ring, "s", color=C_RING, ms=7, mfc="none", mew=1.8,
             label="measured ring (simulation)")
    axT.plot(S, inc, "o-", color=C_INC, lw=2.0, ms=5, label="INC (in-network, measured)")
    axT.plot(S, ideal, "d:", color=C_IDEAL, lw=1.6, ms=5, label="ideal ring (analytic)")
    axT.set_yscale("log")
    axT.set_ylabel("completion time (ns)")
    axT.set_title(f"Ring model vs simulation (worst residual {worst:.2f}%)")
    axT.legend(fontsize=8.5, loc="center left", bbox_to_anchor=(0.015, 0.36),
               framealpha=0.95)

    # --- right: which model term dominates ---
    ratio = [term_ratio(s) for s in S]
    crossover = N_GROUP * ACK_STEP_NS * RATE_BNS          # bytes where ratio = 1
    axR.plot(S, ratio, "o-", color=C_RING, lw=2.0, ms=6,
             label="wire term / ACK term")
    axR.axhline(1.0, color=C_IDEAL, lw=1.2, ls="--")
    axR.axvline(crossover, color=C_IDEAL, lw=1.0, ls=":", alpha=0.8)
    axR.annotate(f"terms equal at\n{hb(crossover)}/rank",
                 xy=(crossover, 1.0), xytext=(-90, -34),
                 textcoords="offset points", ha="center", fontsize=8.5,
                 color=C_IDEAL,
                 arrowprops=dict(arrowstyle="->", color=C_IDEAL, lw=1))
    axR.annotate("ACK floor dominates\n(completion flat at "
                 r"$142\times2600$ ns $=369\,\mu$s)",
                 xy=(0.04, 0.62), xycoords="axes fraction", fontsize=8.5,
                 color=C_IDEAL)
    axR.annotate("wire time dominates\n(completion payload-linear)",
                 xy=(0.60, 0.13), xycoords="axes fraction", fontsize=8.5,
                 color=C_IDEAL)
    axR.set_yscale("log")
    axR.set_ylabel("wire term / ACK term  (per step)")
    axR.set_title("Which ring-model term dominates")

    for ax in (axT, axR):
        ax.set_xscale("log", base=2)
        ax.set_xticks(S)
        ax.set_xticklabels([hb(s) for s in S], rotation=45, ha="right", fontsize=8)
        ax.set_xlabel("AllReduce payload (bytes/rank)")
        ax.grid(True, which="both", alpha=0.22)

    fig.suptitle(r"Measured-ring validation at $|G|=72$: the stop-and-wait step model "
                 "predicts the simulation\n"
                 "every constant read from the configuration (1,300 ns hop, 492.3 B/ns "
                 "realised rate) — nothing fitted",
                 fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf")


if __name__ == "__main__":
    main()
