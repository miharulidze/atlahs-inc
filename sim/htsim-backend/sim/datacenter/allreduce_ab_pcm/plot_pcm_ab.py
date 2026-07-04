#!/usr/bin/env python3
"""Plot the pcm-sdk INC-vs-ring AllReduce A/B (results.csv from run_pcm_ab_sweep.py).

Same figure family as ../allreduce_ab/plot_allreduce_ab.py (the htsim_uec fork
microbench): (left) log-log completion time vs message size; (right) INC speedup.

Three ring references (see README for the diagnosis):
  - GOAL ring (measured): 2(N-1) sequential steps; each step pays data one-way
    + ACK one-way because the bridge completes a send at sender-ACK. This is
    the sim's completion semantic, not connection setup: warm connections
    (-conn_reuse) match cold within <100 ns at every size.
  - warm ring (measured): same, over persistent connections -- overlaps cold,
    the visual proof that per-flow setup is NOT the floor.
  - ideal ring (analytic): 2(N-1)/N * S/BW + (N-1)*hop_oneway -- a perfectly
    pipelined NCCL-style ring. INC-vs-ideal is the honest, quotable spread.
"""
import argparse
import csv
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def hb(n):
    n = int(n)
    if n >= 1024 * 1024:
        return f"{n // (1024*1024)}MiB"
    if n >= 1024:
        return f"{n // 1024}KiB"
    return f"{n}B"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results.csv")
    ap.add_argument("--out", default="allreduce_ab_pcm")
    args = ap.parse_args()

    by_g = defaultdict(list)
    with open(args.csv) as f:
        for row in csv.DictReader(f):
            if not row["inc_ns"] or not row["ring_ns"]:
                continue
            by_g[int(row["group_size"])].append((
                int(row["msg_bytes"]), int(row["inc_ns"]),
                int(row["ring_ns"]), float(row["speedup"]),
                int(row["ring_warm_ns"]) if row.get("ring_warm_ns") else None,
                int(row["ring_ideal_ns"]) if row.get("ring_ideal_ns") else None,
                float(row["speedup_vs_ideal"]) if row.get("speedup_vs_ideal") else None))
    for g in by_g:
        by_g[g].sort()

    fig, (axT, axS) = plt.subplots(1, 2, figsize=(13, 5.2))
    cmap = plt.cm.viridis
    gs = sorted(by_g)
    colors = {g: cmap(i / max(1, len(gs) - 1)) for i, g in enumerate(gs)}

    for g in gs:
        rows = by_g[g]
        sizes = [r[0] for r in rows]
        inc = [r[1] for r in rows]
        ring = [r[2] for r in rows]
        spd = [r[3] for r in rows]
        warm = [r[4] for r in rows]
        ideal = [r[5] for r in rows]
        spd_i = [r[6] for r in rows]
        c = colors[g]
        axT.plot(sizes, inc, "o-", color=c, label=f"INC  |G|={g}")
        axT.plot(sizes, ring, "s--", color=c, alpha=0.7,
                 label=f"GOAL ring |G|={g} (sender-ACK completion)")
        if all(w is not None for w in warm):
            axT.plot(sizes, warm, "x", color=c, ms=8, alpha=0.9,
                     label=f"warm ring |G|={g} (-conn_reuse; == cold)")
        if all(v is not None for v in ideal):
            axT.plot(sizes, ideal, ":", color="tab:red", lw=1.8,
                     label=f"ideal ring (analytic, pipelined)")
        axS.plot(sizes, spd, "s--", color=c, alpha=0.7,
                 label=f"vs GOAL ring |G|={g}")
        if all(v is not None for v in spd_i):
            axS.plot(sizes, spd_i, "o-", color="tab:red",
                     label=f"vs IDEAL ring |G|={g} (quotable)")

    axT.set_xscale("log", base=2)
    axT.set_yscale("log")
    axT.set_xlabel("AllReduce size (bytes/rank)")
    axT.set_ylabel("completion time (ns)")
    axT.set_title("Completion: INC vs ring (measured + analytic ideal)")
    axT.grid(True, which="both", alpha=0.25)
    axT.legend(fontsize=7)
    axT.set_xticks(sizes)
    axT.set_xticklabels([hb(s) for s in sizes], rotation=45, fontsize=7)

    axS.set_xscale("log", base=2)
    axS.set_xlabel("AllReduce size (bytes/rank)")
    axS.set_ylabel("INC speedup")
    axS.set_title("INC speedup vs message size\n"
                  "(GOAL-ring level inflated by per-step ACK-leg completion "
                  "semantic; red = vs analytic ideal ring — see README)",
                  fontsize=9)
    axS.axhline(1.0, color="k", lw=0.8, ls=":")
    axS.grid(True, which="both", alpha=0.25)
    axS.legend(fontsize=8)
    axS.set_xticks(sizes)
    axS.set_xticklabels([hb(s) for s in sizes], rotation=45, fontsize=7)

    fig.suptitle("In-network vs point-to-point ring AllReduce — pcm-sdk two-tier "
                 "engine, scale-up tier (single-switch NVLink-class @ 3600 Gbps, "
                 "lossless_input PFC; reduce_compute=100 ns INC-only)",
                 fontsize=10.5)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf")


if __name__ == "__main__":
    main()
