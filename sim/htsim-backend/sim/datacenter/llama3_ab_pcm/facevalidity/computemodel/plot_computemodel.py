#!/usr/bin/env python3
"""Robustness of the C3 INC headline to the compute-cost model.

Committed C3 anchor config (default Llama3Config: 2 layers, seq 128, hidden
4096; TP4/DP2/PP2, 16 ranks, plain Megatron TP), two-tier pcm engine, run TWICE
-- identical EXCEPT the simple_sim2goal compute-cost model that sets `calc`
(compute) durations:

  * PLACEBO (COMPUTE_MODEL unset): the original placeholder -- compute ~1 PFLOP/s
    (accidentally ~H100), memory ~1 PB/s (so memory-bound ops are ~free).
    Reproduces the 2026-07-14 re-measured C3 sweep row byte-for-byte
    (219,352,385 / 229,541,927 ns, -4.6453%, 32 colls, 0 drops).
  * H100 (COMPUTE_MODEL=h100): explicit roofline, compute 989e12 x MFU 0.45,
    memory 3.35e12 B/s x 0.70, 2 B/elem, duration = max(compute, memory) ns.

RE-MEASURED 2026-07-14 (NIC injection-rate fix): all four arms re-run on the
pcm-sdk engine (run-only) with -intranode_linkspeed 3600000 -- the flag was
previously never passed, so both p2p baselines were silently NIC-capped at the
COPY_ENG default 200 Gbps; the ACK-less INC datapath was never capped (both
INC makespans byte-identical with the flag).

Finding (rewritten at the fixed NIC): the compute model is DECISION-RELEVANT,
not a robustness footnote.  Under the placeholder model the fixed-NIC baseline
(219.35M ns) is FASTER than the INC arm (229.54M ns) -> gain -4.6453% -- not
the collective datapath (per-op INC durations 3.5-9 us; the INC arm's ~81 ms
coll-free tail differs structurally from the baseline's schedule; mechanism
under investigation).  Under the calibrated H100 roofline the block serializes
(norm->attn->AR->norm->mlp->AR), each TP AllReduce sits on the serial critical
path, and INC saves 21.62M ns -> gain +8.8133%.  Shift = +13.4586 pp, still UP
and larger than the capped-NIC +5.8756 pp, and now a SIGN change.  Honest
note: the h100 baseline got 1.45 ms SLOWER with the faster NIC (243.84M ->
245.29M ns) -- a congestion-structure effect on the shared tiers.

/usr/bin/python3 (matplotlib 3.9.4).
"""
import csv
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent

C_BASE = "#7a7a7a"   # grey: decomposed baseline
C_INC = "#1b9e77"    # teal-green: INC
C_H100 = "#d95f02"   # orange: H100-model accent


def load():
    d = {}
    with open(HERE / "results.csv") as f:
        for r in csv.DictReader(f):
            d[(r["model"], r["arm"])] = {
                "ms": int(r["makespan_ns"]),
                "calc_iter": int(r["calc_ns_per_iter"]),
            }
    return d


def main():
    d = load()
    pb, pi = d[("placebo", "baseline")]["ms"], d[("placebo", "inc")]["ms"]
    hb, hi = d[("h100", "baseline")]["ms"], d[("h100", "inc")]["ms"]
    calc_p = d[("placebo", "baseline")]["calc_iter"]
    calc_h = d[("h100", "baseline")]["calc_iter"]
    gp, gh = (1 - pi / pb) * 100, (1 - hi / hb) * 100

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.9))

    # ---- panel 1: makespans (grouped) ----
    x = np.array([0, 1.0])
    w = 0.38
    base = np.array([pb, hb]) / 1e6
    inc = np.array([pi, hi]) / 1e6
    ax1.bar(x - w / 2, base, w, color=C_BASE, label="decomposed baseline")
    ax1.bar(x + w / 2, inc, w, color=C_INC, label="INC (TP AllReduce)")
    for xi, (b, i, g) in enumerate(zip(base, inc, [gp, gh])):
        ax1.annotate(f"gain {g:+.2f}%\n(saving {b-i:+.1f} ms)",
                     (xi, max(b, i)), textcoords="offset points",
                     xytext=(0, 8), ha="center", fontsize=9,
                     color=C_H100 if xi == 1 else "#333", fontweight="bold")
    ax1.set_xticks(x)
    ax1.set_xticklabels(["PLACEBO\n(orig; ~1 PFLOP/s, mem ~free)",
                         "H100\n(989e12*0.45 / 3.35e12*0.70)"], fontsize=9)
    ax1.set_ylabel("iteration makespan  (10^6 ns)")
    ax1.set_ylim(0, max(base.max(), inc.max()) * 1.18)
    ax1.set_title("x19.6 more compute -> +11.8% baseline makespan;\n"
                  "INC < baseline under H100 only -- placebo flips negative",
                  fontsize=9.5)
    ax1.legend(fontsize=8, loc="lower right")
    ax1.grid(True, axis="y", alpha=0.25)

    # ---- panel 2: the gain shift (crosses zero at the fixed NIC) ----
    gx = np.array([0, 1.0])
    ax2.bar(gx, [gp, gh], 0.5, color=[C_BASE, C_H100])
    ax2.axhline(0, color="#666666", lw=0.9)
    for xi, g in zip(gx, [gp, gh]):
        ax2.annotate(f"{g:+.2f}%", (xi, g), textcoords="offset points",
                     xytext=(0, 6 if g >= 0 else -18), ha="center",
                     fontsize=12, fontweight="bold",
                     color=C_H100 if xi == 1 else "#333")
    ax2.set_xticks(gx)
    ax2.set_xticklabels(["PLACEBO", "H100"], fontsize=10)
    ax2.set_ylabel("end-to-end INC gain per iteration  (%)")
    ax2.set_ylim(gp * 1.55, gh * 1.55)
    ax2.set_title("The compute model now flips the SIGN of the gain:\n"
                  "-4.65% (placebo) -> +8.81% (H100), shift +13.46 pp",
                  fontsize=9.5)
    ax2.grid(True, axis="y", alpha=0.25)
    ax2.annotate(
        f"compute denominator: total calc/iter\n"
        f"  {calc_p/1e6:.2f}M -> {calc_h/1e6:.2f}M ns  "
        f"(x{calc_h/calc_p:.1f})\n"
        f"placebo: INC arm 10.19M ns SLOWER\n"
        f"  end-to-end -- schedule/congestion-\n"
        f"  structure effect, NOT the collective\n"
        f"  datapath (per-op INC 3.5-9 us;\n"
        f"  mechanism under investigation)\n"
        f"H100: serialized block exposes each\n"
        f"  TP AllReduce on the critical path\n"
        f"  -> INC saves 21.62M ns",
        (0.03, 0.97), xycoords="axes fraction", ha="left", va="top",
        fontsize=7.6, color="#333",
        bbox=dict(boxstyle="round,pad=0.4", fc="#f3f3f3", ec="#bbb"))

    fig.suptitle("Compute-model sensitivity of the C3 INC result "
                 "(2-layer/seq-128 anchor, TP4/DP2/PP2, 16 ranks, two-tier "
                 "pcm)\nre-measured 2026-07-14, pcm-sdk (run-only), "
                 "-intranode_linkspeed 3600000",
                 fontsize=10.5, y=1.04)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(HERE / f"computemodel_robustness.{ext}", dpi=140,
                    bbox_inches="tight")
    print("wrote computemodel_robustness.png / .pdf")


if __name__ == "__main__":
    main()
