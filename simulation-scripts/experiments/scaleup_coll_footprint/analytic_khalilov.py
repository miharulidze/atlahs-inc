#!/usr/bin/env python3
"""Analytical cost model matching Khalilov et al. SC24 Fig. 2 — the *theoretical*
byte·link-footprint reduction with multicast — computed from first principles for a
k-ary fat-tree, and overlaid on our simulator-measured ratios.

This is the analytical counterpart of the measured `scaleup_coll_footprint` sweep:
Khalilov's Fig. 2 is itself a "theoretical cost model" (its closed forms live in the
paper's Appendix B, absent from the core-only PDF), so this reconstructs the same
method — total data movement = Σ (message_bytes × hop_count), ratio = baseline / INC.

MODEL (AllGather; ranks placed contiguously; L = hosts/leaf, Q = hosts/pod = L²):
  hop(δ) = 2 (same leaf, δ<L) | 4 (same pod, δ<Q) | 6 (inter-pod, 3-tier).
  Ring:              footprint = (P−1)·Σ_r hop(r,(r+1) mod P)             → ratio 2−2/P.
  Recursive-Doubling footprint = Σ_{j=1..log2 P} P·2^(j−1)·hop(2^(j−1)).
  Multicast-optimal  footprint = P·(P + ⌈P/L⌉ + [⌈P/Q⌉ if P>Q]).
The multicast-optimal formula reproduces the MEASURED INC crosses to the byte
(69632 / 279552 / 1118208 at P=256/512/1024 on radix-32), and RD matches the measured
byte-ratio to <1% — so the analytical model and the simulator validate each other.

Standalone (no simulator). Emits analytic_khalilov.png + a printed table.
"""
import csv
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from common import paths  # noqa: E402

BIG = 1 << 30  # single switch: everything within one "leaf" → all pairs 2 hops
# (label, L=hosts/leaf, Q=hosts/pod, Pmax) — match the minted topologies.
TOPOS = [
    ("single_switch", "Single switch",              BIG, BIG, 64),
    ("fat3tier",      "3-tier fat-tree (4×4×N)",    4,   16,  256),
    ("paper_r32",     "radix-32 3-tier (paper)",    16,  256, 1024),
]

# Khalilov Fig. 2 Recursive-Doubling values, read off the published plot (approximate,
# ±0.2 in the middle); Ring is the exact 2−2/P the paper states.
KHALILOV_RD = {2: 1.00, 4: 1.50, 8: 1.75, 16: 1.90, 32: 2.05, 64: 2.30,
               128: 2.60, 256: 2.90, 512: 3.25, 1024: 3.60}


def hop(delta, L, Q):
    if delta < L:
        return 2
    if delta < Q:
        return 4
    return 6


def ratios(P, L, Q):
    fp_ring = (P - 1) * sum(hop(1 if (r // L) == ((r + 1) % P // L) else
                                (L if (r // Q) == ((r + 1) % P // Q) else Q),
                                L, Q) for r in range(P))
    fp_rd = sum(P * (1 << (j - 1)) * hop(1 << (j - 1), L, Q)
                for j in range(1, P.bit_length()))
    fp_mc = P * (P + (math.ceil(P / L) if P > L else 0)
                 + (math.ceil(P / Q) if P > Q else 0))
    return fp_ring / fp_mc, fp_rd / fp_mc


def load_measured():
    """{(cls, algo): {P: ratio_bytes}} from the measured AllGather CSV."""
    csv_path = os.path.join(paths.results_dir("scaleup_coll_footprint"),
                            "scaleup_coll_footprint.csv")
    out = {}
    if not os.path.exists(csv_path):
        return out
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            if r["collective"] != "allgather" or r.get("size_mult") not in (None, "1"):
                continue
            if not r["ratio_bytes"]:
                continue
            out.setdefault((r["topology_class"], r["baseline_algo"]), {})[
                int(r["group_size"])] = float(r["ratio_bytes"])
    return out


def main():
    measured = load_measured()
    print(f"{'topo':>14} {'P':>5} {'ring(an)':>9} {'RD(an)':>8} "
          f"{'ring(meas)':>11} {'RD(meas)':>9} {'RD(Khalilov)':>13}")
    fig, axes = plt.subplots(1, len(TOPOS), figsize=(5.4 * len(TOPOS), 4.0), squeeze=False)
    for j, (cls, title, L, Q, pmax) in enumerate(TOPOS):
        Ps = [p for p in (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024) if p <= pmax]
        an_ring, an_rd = zip(*(ratios(p, L, Q) for p in Ps))
        ax = axes[0][j]
        ax.plot(Ps, an_ring, "-o", color="#2e7d32", ms=4, label="Ring (analytic)")
        ax.plot(Ps, an_rd, "-o", color="#e08a1e", ms=4, label="Recursive Doubling (analytic)")
        mr = measured.get((cls, "ring"), {})
        md = measured.get((cls, "rdouble"), {})
        if mr:
            ax.scatter([p for p in Ps if p in mr], [mr[p] for p in Ps if p in mr],
                       marker="x", color="#1b5e20", s=45, zorder=5, label="Ring (measured)")
        if md:
            ax.scatter([p for p in Ps if p in md], [md[p] for p in Ps if p in md],
                       marker="x", color="#b35900", s=45, zorder=5, label="RD (measured)")
        if cls == "paper_r32":
            kp = [p for p in Ps if p in KHALILOV_RD]
            ax.plot(kp, [KHALILOV_RD[p] for p in kp], "--s", color="#555", ms=4,
                    label="RD (Khalilov Fig. 2)")
            ax.plot(kp, [2 - 2 / p for p in kp], "--", color="#999",
                    label="Ring = 2−2/P (Khalilov)")
        ax.set_xscale("log", base=2)
        ax.set_xticks(Ps); ax.set_xticklabels(Ps)
        ax.set_title(title, fontsize=9)
        ax.set_xlabel("Number of participating GPUs", fontsize=8)
        if j == 0:
            ax.set_ylabel("Relative bandwidth usage\nreduction with multicast (×)", fontsize=8)
        ax.grid(True, ls=":", alpha=0.5)
        ax.legend(fontsize=6.5, loc="upper left")
        for p, rr, rd in zip(Ps, an_ring, an_rd):
            print(f"{cls:>14} {p:>5} {rr:>9.3f} {rd:>8.3f} "
                  f"{str(round(mr.get(p), 3)) if p in mr else '-':>11} "
                  f"{str(round(md.get(p), 3)) if p in md else '-':>9} "
                  f"{str(KHALILOV_RD.get(p, '-')):>13}")
    fig.suptitle("Analytical cost model (Khalilov SC24 Fig. 2 method) vs measured footprint",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out = os.path.join(paths.results_dir("scaleup_coll_footprint"), "analytic_khalilov.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=150)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
