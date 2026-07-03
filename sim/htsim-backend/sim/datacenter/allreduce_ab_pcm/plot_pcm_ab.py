#!/usr/bin/env python3
"""Plot the pcm-sdk INC-vs-ring AllReduce A/B (results.csv from run_pcm_ab_sweep.py).

Same figure family as ../allreduce_ab/plot_allreduce_ab.py (the htsim_uec fork
microbench): (left) log-log completion time vs message size, both arms;
(right) INC speedup vs message size.

Read the magnitude with the README's caveat: the ring arm cold-starts 2(N-1)
sequential paced-UEC flows (~2.6 us/step CC-startup floor), so the speedup level
is CC-confounded; the DECREASING trend with size is the fingerprint of that
cold-start dominance (contrast the htsim_uec fork, where speedup GROWS 3.7x->33x).
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
                int(row["ring_ns"]), float(row["speedup"])))
    for g in by_g:
        by_g[g].sort()

    fig, (axT, axS) = plt.subplots(1, 2, figsize=(13, 5.2))
    cmap = plt.cm.viridis
    gs = sorted(by_g)
    colors = {g: cmap(i / max(1, len(gs) - 1)) for i, g in enumerate(gs)}

    for g in gs:
        sizes = [r[0] for r in by_g[g]]
        inc = [r[1] for r in by_g[g]]
        ring = [r[2] for r in by_g[g]]
        spd = [r[3] for r in by_g[g]]
        c = colors[g]
        axT.plot(sizes, inc, "o-", color=c, label=f"INC  |G|={g}")
        axT.plot(sizes, ring, "s--", color=c, alpha=0.7, label=f"ring |G|={g}")
        axS.plot(sizes, spd, "o-", color=c, label=f"|G|={g}")

    axT.set_xscale("log", base=2)
    axT.set_yscale("log")
    axT.set_xlabel("AllReduce size (bytes/rank)")
    axT.set_ylabel("completion time (ns)")
    axT.set_title("Completion: INC (solid) vs ring (dashed)")
    axT.grid(True, which="both", alpha=0.25)
    axT.legend(fontsize=7, ncol=2)
    axT.set_xticks(sizes)
    axT.set_xticklabels([hb(s) for s in sizes], rotation=45, fontsize=7)

    axS.set_xscale("log", base=2)
    axS.set_xlabel("AllReduce size (bytes/rank)")
    axS.set_ylabel("INC speedup  (ring / INC)")
    axS.set_title("INC speedup vs message size\n"
                  "(level is CC-confounded; decreasing trend = ring cold-start "
                  "dominance — see README)", fontsize=9)
    axS.axhline(1.0, color="k", lw=0.8, ls=":")
    axS.grid(True, which="both", alpha=0.25)
    axS.legend(title="group size", fontsize=8)
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
