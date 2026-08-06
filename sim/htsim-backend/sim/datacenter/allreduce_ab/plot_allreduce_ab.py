#!/usr/bin/env python3
"""Plot the INC-vs-ring AllReduce A/B message-size sweep on the single-switch scale-up
crossbar (results_crossbar_msgsweep.csv from run_allreduce_ab_sweep.py).

Re-anchored (2026-07-13) on the NVL72-realistic crossbar: the headline group is |G|=72
(radix-72 single-switch crossbar), with |G|=16 kept as the cross-engine agreement anchor
(INC @ 64 KiB = 1542 ns on the multi-domain simulator vs 1549 ns on the two-domain engine).
Both group sizes run on their own radix-==-|G| single-switch crossbar; the message-size
axes differ per group (|G|=72 needs 72-divisible payloads) and share a common log axis.

Two panels:
  (left)  completion time vs message size, INC (solid) and ring (dashed), one colour per |G|
  (right) INC speedup (ring/INC) vs message size, one line per |G|

The speedup separates into a latency regime (small messages: the ring pays 2(N-1) serial
dependency hops, INC one flat apex -- a purely algorithmic gap that widens with |G|) and a
bandwidth regime (large messages: INC's ACK-less sources stream at the realised line rate
while the decomposed ring runs over CC'd flows -- transport-sensitive; see README).
"""
import argparse
import csv
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def hb(n):
    n = int(n)
    if n >= 1000 * 1024:
        v = n / (1024 * 1024)
        return f"{v:.0f}MiB" if abs(v - round(v)) < 0.05 else f"{v:.1f}MiB"
    if n >= 1024:
        v = n / 1024
        return f"{v:.0f}KiB" if abs(v - round(v)) < 0.05 else f"{v:.1f}KiB"
    return f"{n}B"


# |G|=72 is the headline (NVL72-realistic); |G|=16 is the cross-engine anchor.
STYLE = {
    72: dict(color="#1b9e77", label="|G|=72  (NVL72-realistic headline)", lw=2.4, ms=7, z=3),
    16: dict(color="#7570b3", label="|G|=16  (cross-engine anchor)", lw=1.6, ms=6, z=2),
}
DEFAULT = dict(color="#888888", lw=1.4, ms=5, z=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results_crossbar_msgsweep.csv")
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

    fig, (axT, axS) = plt.subplots(1, 2, figsize=(13.5, 5.4))
    gs = sorted(by_g)
    all_sizes = sorted({r[0] for g in gs for r in by_g[g]})

    def sty(g, key):
        return STYLE.get(g, DEFAULT).get(key, DEFAULT.get(key))

    for g in gs:
        sizes = [r[0] for r in by_g[g]]
        inc = [r[1] for r in by_g[g]]
        ring = [r[2] for r in by_g[g]]
        spd = [r[3] for r in by_g[g]]
        c = sty(g, "color")
        lab = STYLE.get(g, {}).get("label", f"|G|={g}")
        axT.plot(sizes, inc, "o-", color=c, lw=sty(g, "lw"), ms=sty(g, "ms"),
                 zorder=sty(g, "z"), label=f"INC  {lab}")
        axT.plot(sizes, ring, "s--", color=c, alpha=0.65, lw=sty(g, "lw"),
                 ms=sty(g, "ms"), zorder=sty(g, "z"), label=f"ring {lab}")
        axS.plot(sizes, spd, "o-", color=c, lw=sty(g, "lw"), ms=sty(g, "ms"),
                 zorder=sty(g, "z"), label=lab)

    for ax in (axT, axS):
        ax.set_xscale("log", base=2)
        ax.set_xlabel("AllReduce size (bytes/rank)")
        ax.set_xticks(all_sizes)
        ax.set_xticklabels([hb(s) for s in all_sizes], rotation=45, fontsize=7)
        ax.grid(True, which="both", alpha=0.25)

    axT.set_yscale("log")
    axT.set_ylabel("completion time (ns)")
    axT.set_title("Completion: INC (solid) vs ring (dashed)")
    axT.legend(fontsize=7, ncol=2)

    axS.set_ylabel("INC speedup  (ring / INC)")
    axS.set_title("INC speedup vs message size")
    axS.axhline(1.0, color="k", lw=0.8, ls=":")
    axS.legend(title="group size", fontsize=8)

    fig.suptitle("In-network vs point-to-point ring AllReduce vs message size — "
                 "single-switch scale-up crossbar (radix=|G|)\non the multi-domain "
                 "simulator; 3600 Gbps/port, 500 ns link + 300 ns switch; INC charged "
                 "100 ns in-switch reduce",
                 fontsize=10.5)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf  (|G|: {gs})")


if __name__ == "__main__":
    main()
