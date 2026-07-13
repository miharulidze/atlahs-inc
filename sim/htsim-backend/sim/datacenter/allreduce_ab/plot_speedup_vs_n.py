#!/usr/bin/env python3
"""[D3] Plot the INC-vs-ring-vs-recursive-doubling AllReduce A/B versus rank count N.

Reads results_vs_n_ext.csv: the single-switch scale-up crossbar swept in |G| = 2..72
(radix == |G|; 72 = the NVL72-realistic single-switch ceiling), PLUS the 256-GPU point
measured on the two-LEVEL scale-up fabric (the DGX H100 NVLink Switch System). The
multi-domain simulator runs every point. The two-level point is drawn with OPEN markers
past the single-switch ceiling to show INC's advantage still growing when the fabric
gains a level.

One row of panels per message size:
  (left)  completion vs N -- INC (flat O(1) single-switch apex; steps up once at the
          two-level point because its apex sits one switch level deeper), ring (O(N)
          serial hops), recursive-doubling (O(log N), power-of-two |G| only), and the
          analytic ideal-ring floor.
  (right) speedup vs N -- ring/INC (measured, carries the ring's CC cold-start confound),
          recursive-doubling/INC (the defensible latency headline), and ideal-ring/INC
          (CC-decontaminated).

Two pricing rules for the analytic references (measured arms are the engine's own output
and are NOT repriced):
  * REALISED wire rate 492.3 B/ns (the engine quantises per-byte time to 2 ps => 500 B/ns
    raw, x 4096/4160 MTU framing; == the ACK-less INC completion-vs-size slope), not the
    nominal 3600 Gbps -- this removes a ~9% bandwidth-term bias in the old plots.
  * each fabric's OWN one-way hop budget: 1300 ns on the single-switch crossbar
    (2x500 link + 300 switch), 2900 ns on the two-level fabric (4x500 + 3x300).
At large |G| the ideal-ring latency term is (N-1) serial hops, so ideal/INC BALLOONS
(~71 hops at |G|=72, ~255 at 256) -- that ratio is NOT the defensible latency headline;
the charged-INC-vs-recursive-doubling ratio is (recursive-doubling reduces at endpoints,
uncharged, on the same fabric).
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


# categorical arm palette (consistent, colour-blind-safe-ish)
C_INC = "#1b9e77"     # teal-green
C_RING = "#d95f02"    # orange
C_RDBL = "#7570b3"    # violet
C_IDEAL = "#444444"   # grey (analytic reference)


def fval(row, key):
    v = row.get(key, "")
    return float(v) if v not in ("", None) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results_vs_n_ext.csv")
    ap.add_argument("--out", default="speedup_vs_n")
    args = ap.parse_args()

    # split rows by message size, and within each size into single-switch crossbar
    # (connected series) vs the two-level fabric point (open markers, drawn separately)
    xbar = defaultdict(list)   # size -> [dict per N on the crossbar]
    tl = defaultdict(list)     # size -> [dict per N on the two-level fabric]
    with open(args.csv) as f:
        for row in csv.DictReader(f):
            if not row["inc_ns"] or not row["ring_ns"]:
                continue
            rec = {
                "N": int(row["group_size"]),
                "inc": fval(row, "inc_ns"),
                "ring": fval(row, "ring_ns"),
                "rdouble": fval(row, "rdouble_ns"),
                "ideal": fval(row, "ideal_ring_ns"),
                "sp_ring": fval(row, "speedup"),
                "sp_ideal": fval(row, "speedup_vs_ideal"),
                "sp_rdbl": fval(row, "speedup_rdouble"),
            }
            dest = tl if "twotier" in row.get("topo", "") else xbar
            dest[int(row["msg_bytes"])].append(rec)
    for d in (xbar, tl):
        for s in d:
            d[s].sort(key=lambda r: r["N"])

    sizes = sorted(xbar)
    nrows = len(sizes)
    fig, axes = plt.subplots(nrows, 2, figsize=(13.5, 4.9 * nrows), squeeze=False)

    def xs(recs, key):
        """(N, value) pairs where value is present (skips e.g. rdouble gap at |G|=72)."""
        return ([r["N"] for r in recs if r[key] is not None],
                [r[key] for r in recs if r[key] is not None])

    for i, s in enumerate(sizes):
        xr = xbar[s]
        tr = tl.get(s, [])
        axC, axS = axes[i][0], axes[i][1]
        ceiling = max(r["N"] for r in xr)           # single-switch ceiling (72)

        # ---------------- completion vs N ----------------
        nX, vX = xs(xr, "inc")
        axC.plot(nX, vX, "o-", color=C_INC, label="INC (in-network)")
        nX, vX = xs(xr, "ring")
        axC.plot(nX, vX, "s-", color=C_RING, label="ring (p2p, measured)")
        nX, vX = xs(xr, "rdouble")
        axC.plot(nX, vX, "^-", color=C_RDBL, label="recursive-doubling (measured)")
        nX, vX = xs(xr, "ideal")
        axC.plot(nX, vX, "d--", color=C_IDEAL, alpha=0.8, label="ideal ring (analytic)")

        # two-level fabric point(s): open markers + dotted bridge from the ceiling
        for r in tr:
            for key, col, mk in (("ring", C_RING, "s"), ("ideal", C_IDEAL, "d"),
                                 ("rdouble", C_RDBL, "^"), ("inc", C_INC, "o")):
                anchor = max((b for b in xr if b[key] is not None),
                             key=lambda b: b["N"], default=None)   # last measured point
                if anchor is not None:
                    axC.plot([anchor["N"], r["N"]], [anchor[key], r[key]], ":",
                             color=col, alpha=0.55, lw=1.3)
                axC.plot([r["N"]], [r[key]], mk, mfc="none", mec=col, ms=11, mew=2.0)
            axC.annotate("INC apex\n+1 level", xy=(r["N"], r["inc"]),
                         xytext=(-12, 26), textcoords="offset points", ha="right",
                         va="bottom", fontsize=7.5, color=C_INC,
                         arrowprops=dict(arrowstyle="->", color=C_INC, lw=1, alpha=0.8))

        axC.axvline(ceiling, color="k", lw=0.8, ls="--", alpha=0.5)
        axC.annotate(" single-switch crossbar\n ceiling (NVL72)", xy=(ceiling, 0.02),
                     xycoords=("data", "axes fraction"), fontsize=7, va="bottom",
                     ha="left", color="k", alpha=0.7)
        _finish_axis(axC, xr, tr, "completion time (ns)", f"Completion vs N  ({hb(s)}/rank)")
        axC.legend(fontsize=8, loc="upper left")

        # ---------------- speedup vs N ----------------
        nX, vX = xs(xr, "sp_rdbl")
        axS.plot(nX, vX, "^-", color=C_RDBL, label="recursive-doubling / INC  (headline)")
        nX, vX = xs(xr, "sp_ring")
        axS.plot(nX, vX, "s-", color=C_RING, alpha=0.85, label="ring / INC  (measured, CC-confounded)")
        nX, vX = xs(xr, "sp_ideal")
        axS.plot(nX, vX, "d--", color=C_IDEAL, label="ideal-ring / INC  (CC-decontaminated)")
        for r in tr:
            for key, col, mk in (("sp_ring", C_RING, "s"), ("sp_ideal", C_IDEAL, "d"),
                                 ("sp_rdbl", C_RDBL, "^")):
                anchor = max((b for b in xr if b[key] is not None),
                             key=lambda b: b["N"], default=None)   # last measured point
                if anchor is not None and r[key] is not None:
                    axS.plot([anchor["N"], r["N"]], [anchor[key], r[key]], ":",
                             color=col, alpha=0.55, lw=1.3)
                if r[key] is not None:
                    axS.plot([r["N"]], [r[key]], mk, mfc="none", mec=col, ms=11, mew=2.0)
            if r["sp_rdbl"] is not None:
                axS.annotate(f"{r['sp_rdbl']:.0f}x vs RD\n(two-level, uncharged)",
                             xy=(r["N"], r["sp_rdbl"]), xytext=(-12, -30),
                             textcoords="offset points", ha="right", va="top",
                             fontsize=7.5, color=C_RDBL,
                             arrowprops=dict(arrowstyle="->", color=C_RDBL, lw=1, alpha=0.8))
        axS.axhline(1.0, color="k", lw=0.8, ls=":")
        axS.axvline(ceiling, color="k", lw=0.8, ls="--", alpha=0.5)
        _finish_axis(axS, xr, tr, "speedup over INC  (x)", f"INC speedup vs N  ({hb(s)}/rank)")
        axS.legend(fontsize=8, loc="upper left")

    fig.suptitle("In-network vs point-to-point AllReduce scaling in |G| on the multi-domain "
                 "simulator\nsingle-switch scale-up crossbar (radix=|G|, 2..72) + 256-GPU "
                 "two-level fabric (open markers); 3600 Gbps/port, 500 ns link + 300 ns switch",
                 fontsize=10.5)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf ({nrows} size row(s): {[hb(s) for s in sizes]}; "
          f"two-level N: {sorted({r['N'] for s in tl for r in tl[s]})})")


def _finish_axis(ax, xr, tr, ylabel, title):
    Ns = sorted({r["N"] for r in xr} | {r["N"] for r in tr})
    # 72 sits right on top of 64 on a log2 axis; drop its numeric label (the point is
    # still plotted and is marked by the "single-switch crossbar ceiling (NVL72)" line).
    ticks = [n for n in Ns if n != 72]
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(n) for n in ticks], fontsize=8)
    ax.set_xlabel("rank count  |G| = N   (single-switch crossbar; open = two-level fabric)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, which="both", alpha=0.25)


if __name__ == "__main__":
    main()
