#!/usr/bin/env python3
"""[D3] Plot the INC-vs-ring-vs-rdouble AllReduce A/B *versus rank count N*.

Reads results_vs_n.csv (run_allreduce_ab_sweep.py with --group-sizes 2,4,8,16,32,64).
One row of panels per message size:
  (left)  completion time vs N -- INC (flat, O(1) single-switch apex), ring (O(N) serial
          hops), rdouble (O(log N)), and the analytic ideal-ring floor (2(N-1)/N model).
  (right) speedup vs N -- ring/INC (measured, includes the ring's CC cold-start confound),
          ideal-ring/INC (CC-decontaminated: INC vs a perfect line-rate ring), and
          rdouble/INC. The gap between ring/INC and ideal/INC is the CC confound; the
          growth of ideal/INC with N is the pure algorithmic O(N)->O(1) win.

This isolates the D3 question "where does the speedup come from": the ring's O(N) serial
dependency structure (latency regime) plus the ACK-less line-rate transport (bandwidth
regime, visible as the ring/INC vs ideal/INC gap).
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
    ap.add_argument("--csv", default="results_vs_n.csv")
    ap.add_argument("--out", default="speedup_vs_n")
    args = ap.parse_args()

    by_size = defaultdict(list)  # size -> list of dict-per-N
    with open(args.csv) as f:
        for row in csv.DictReader(f):
            if not row["inc_ns"] or not row["ring_ns"]:
                continue
            by_size[int(row["msg_bytes"])].append({
                "N": int(row["group_size"]),
                "inc": fval(row, "inc_ns"),
                "ring": fval(row, "ring_ns"),
                "rdouble": fval(row, "rdouble_ns"),
                "ideal": fval(row, "ideal_ring_ns"),
                "sp_ring": fval(row, "speedup"),
                "sp_ideal": fval(row, "speedup_vs_ideal"),
                "sp_rdbl": fval(row, "speedup_rdouble"),
            })
    for s in by_size:
        by_size[s].sort(key=lambda d: d["N"])

    sizes = sorted(by_size)
    nrows = len(sizes)
    fig, axes = plt.subplots(nrows, 2, figsize=(13, 4.8 * nrows), squeeze=False)

    for i, s in enumerate(sizes):
        rows = by_size[s]
        Ns = [d["N"] for d in rows]
        axC, axS = axes[i][0], axes[i][1]

        # --- completion vs N ---
        axC.plot(Ns, [d["inc"] for d in rows], "o-", color=C_INC, label="INC (in-network)")
        axC.plot(Ns, [d["ring"] for d in rows], "s-", color=C_RING, label="ring (p2p, measured)")
        if all(d["rdouble"] for d in rows):
            axC.plot(Ns, [d["rdouble"] for d in rows], "^-", color=C_RDBL,
                     label="recursive-doubling (measured)")
        axC.plot(Ns, [d["ideal"] for d in rows], "d--", color=C_IDEAL, alpha=0.8,
                 label="ideal ring (analytic 2(N-1)/N)")
        axC.set_xscale("log", base=2)
        axC.set_yscale("log")
        axC.set_xticks(Ns)
        axC.set_xticklabels([str(n) for n in Ns])
        axC.set_xlabel("rank count  |G| = N")
        axC.set_ylabel("completion time (ns)")
        axC.set_title(f"Completion vs N  ({hb(s)}/rank)")
        axC.grid(True, which="both", alpha=0.25)
        axC.legend(fontsize=8)

        # --- speedup vs N ---
        axS.plot(Ns, [d["sp_ring"] for d in rows], "s-", color=C_RING,
                 label="ring / INC (measured)")
        axS.plot(Ns, [d["sp_ideal"] for d in rows], "d--", color=C_IDEAL,
                 label="ideal-ring / INC (CC-decontaminated)")
        if all(d["sp_rdbl"] for d in rows):
            axS.plot(Ns, [d["sp_rdbl"] for d in rows], "^-", color=C_RDBL,
                     label="recursive-doubling / INC")
        axS.axhline(1.0, color="k", lw=0.8, ls=":")
        axS.set_xscale("log", base=2)
        axS.set_yscale("log")
        axS.set_xticks(Ns)
        axS.set_xticklabels([str(n) for n in Ns])
        axS.set_xlabel("rank count  |G| = N")
        axS.set_ylabel("speedup over INC  (x)")
        axS.set_title(f"INC speedup vs N  ({hb(s)}/rank)")
        axS.grid(True, which="both", alpha=0.25)
        axS.legend(fontsize=8)

    fig.suptitle("In-network vs point-to-point AllReduce scaling in |G| — single-switch "
                 "scale-up crossbar (radix=|G|, 3600 Gbps, 500 ns link + 300 ns switch)",
                 fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf ({nrows} size row(s): {[hb(s) for s in sizes]})")


if __name__ == "__main__":
    main()
