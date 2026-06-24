#!/usr/bin/env python3
"""Plot the INC-vs-ring AllReduce A/B sweep (results.csv from run_allreduce_ab_sweep.py).

Two panels:
  (left)  completion time vs message size, INC and ring, one colour per |G| (log-log)
  (right) INC speedup (ring/INC) vs message size, one line per |G|

The speedup separates into a latency regime (small messages: the ring pays 2(N-1)
serial dependency hops, INC ~2 — a purely algorithmic gap) and a bandwidth regime
(large messages: INC's ACK-less sources stream at line rate while the decomposed ring
runs over CC'd UEC flows — a transport-sensitive gap; see README).
"""
import argparse
import csv
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def human(n):
    n = int(n)
    for unit in ("B", "KiB", "MiB"):
        if n < 1024 or unit == "MiB":
            return f"{n}{unit}" if unit == "B" else f"{n//1 if n%1 else n}{unit}"
        n //= 1024
    return f"{n}"


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
    ap.add_argument("--out", default="allreduce_ab")
    args = ap.parse_args()

    by_g = defaultdict(list)  # |G| -> list of (size, inc_ns, ring_ns, speedup)
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
    axS.set_title("INC speedup vs message size")
    axS.axhline(1.0, color="k", lw=0.8, ls=":")
    axS.grid(True, which="both", alpha=0.25)
    axS.legend(title="group size", fontsize=8)
    axS.set_xticks(sizes)
    axS.set_xticklabels([hb(s) for s in sizes], rotation=45, fontsize=7)

    fig.suptitle("In-network vs point-to-point ring AllReduce — scale-up tier "
                 "(NVLink-class tree16 @ 3600 Gbps, 500 ns/hop)", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf")


if __name__ == "__main__":
    main()
