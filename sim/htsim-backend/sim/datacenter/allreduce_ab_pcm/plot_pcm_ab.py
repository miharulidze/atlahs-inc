#!/usr/bin/env python3
"""Plot the multi-domain (pcm-sdk) INC-vs-ring AllReduce A/B vs message size.

Re-anchored 2026-07-13 on the NVL72-realistic radix-72 crossbar: |G|=72 is the headline,
|G|=16 kept as the cross-engine anchor (INC @ 64 KiB = 1549 ns here vs 1542 ns on the
htsim fork). Reads results_msgsweep_pcm.csv. Three curves per group: INC (solid), the
measured GOAL ring (dashed; its level carries the per-step sender-ACK completion + CC
confound, see the forensics section), and the analytic ideal ring (dotted; priced at the
realised 492.3 B/ns) -- INC-vs-ideal is the honest, quotable spread. The warm-connection
ring forensics arm is discussed at |G|=16 in the text and is omitted from this figure.
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


# |G|=72 headline (NVL72-realistic); |G|=16 cross-engine anchor.
STYLE = {
    72: dict(color="#1b9e77", label="|G|=72  (NVL72-realistic headline)", lw=2.4, ms=7, z=3),
    16: dict(color="#7570b3", label="|G|=16  (cross-engine anchor)", lw=1.6, ms=6, z=2),
}
DEFAULT = dict(color="#888888", lw=1.4, ms=5, z=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results_msgsweep_pcm.csv")
    ap.add_argument("--out", default="allreduce_ab_pcm")
    args = ap.parse_args()

    by_g = defaultdict(list)
    with open(args.csv) as f:
        for row in csv.DictReader(f):
            if not row["inc_ns"] or not row["ring_ns"]:
                continue
            by_g[int(row["group_size"])].append((
                int(row["msg_bytes"]), int(row["inc_ns"]), int(row["ring_ns"]),
                float(row["speedup"]),
                float(row["ideal_ring_ns"]) if row.get("ideal_ring_ns") else None,
                float(row["speedup_vs_ideal"]) if row.get("speedup_vs_ideal") else None))
    for g in by_g:
        by_g[g].sort()

    gs = sorted(by_g)
    all_sizes = sorted({r[0] for g in gs for r in by_g[g]})

    def sty(g, k):
        return STYLE.get(g, DEFAULT).get(k, DEFAULT.get(k))

    fig, (axT, axS) = plt.subplots(1, 2, figsize=(13.5, 5.4))
    for g in gs:
        rows = by_g[g]
        sizes = [r[0] for r in rows]
        inc = [r[1] for r in rows]
        ring = [r[2] for r in rows]
        spd = [r[3] for r in rows]
        ideal = [r[4] for r in rows]
        spd_i = [r[5] for r in rows]
        c = sty(g, "color")
        lab = STYLE.get(g, {}).get("label", f"|G|={g}")
        axT.plot(sizes, inc, "o-", color=c, lw=sty(g, "lw"), ms=sty(g, "ms"),
                 zorder=sty(g, "z"), label=f"INC  {lab}")
        axT.plot(sizes, ring, "s--", color=c, alpha=0.6, lw=sty(g, "lw"),
                 ms=sty(g, "ms"), zorder=sty(g, "z"), label=f"GOAL ring  {lab}")
        if all(v is not None for v in ideal):
            axT.plot(sizes, ideal, ":", color=c, lw=sty(g, "lw"), alpha=0.9,
                     zorder=sty(g, "z"), label=f"ideal ring  {lab}")
        axS.plot(sizes, spd, "s--", color=c, alpha=0.5, lw=sty(g, "lw"),
                 ms=sty(g, "ms"), zorder=sty(g, "z"), label=f"vs GOAL ring  {lab}")
        if all(v is not None for v in spd_i):
            axS.plot(sizes, spd_i, "o-", color=c, lw=sty(g, "lw"), ms=sty(g, "ms"),
                     zorder=sty(g, "z"), label=f"vs ideal ring  {lab} (quotable)")

    for ax in (axT, axS):
        ax.set_xscale("log", base=2)
        ax.set_xlabel("AllReduce size (bytes/rank)")
        ax.set_xticks(all_sizes)
        ax.set_xticklabels([hb(s) for s in all_sizes], rotation=45, fontsize=7)
        ax.grid(True, which="both", alpha=0.25)

    axT.set_yscale("log")
    axT.set_ylabel("completion time (ns)")
    axT.set_title("Completion: INC (solid) vs GOAL ring (dashed) vs ideal ring (dotted)")
    axT.legend(fontsize=6.5, ncol=2)

    axS.set_yscale("log")
    axS.set_ylabel("INC speedup  (x)")
    axS.set_title("INC speedup vs message size (quotable = vs ideal ring)")
    axS.axhline(1.0, color="k", lw=0.8, ls=":")
    axS.legend(fontsize=7)

    fig.suptitle("In-network vs point-to-point ring AllReduce vs message size on the "
                 "multi-domain simulator\nsingle-switch scale-up crossbar (radix=|G|), "
                 "lossless_input PFC, 3600 Gbps/port; INC charged 100 ns in-switch reduce; "
                 "analytic ideal ring at the realised 492.3 B/ns",
                 fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf  (|G|: {gs})")


if __name__ == "__main__":
    main()
