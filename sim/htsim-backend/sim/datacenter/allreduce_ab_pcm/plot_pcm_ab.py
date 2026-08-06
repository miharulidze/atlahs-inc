#!/usr/bin/env python3
"""Message-size AllReduce A/B on the multi-domain (pcm-sdk) simulator, |G|=72 only.

The NVL72-realistic radix-72 crossbar swept 4.5 KiB -> 288 MiB (the SHARP hardware
range) as a convergence check.
  Left  : completion time vs payload -- INC (solid), measured ring (dashed), analytic
          ideal ring (dotted; priced at the realised 492.3 B/ns).
  Right : the quotable INC-vs-ideal-ring speedup, converging onto the horizontal
          2(N-1)/N = 1.972x traffic bound.
The |G|=16 cross-engine anchor is tabulated, not plotted (see the table).
True numeric log-log payload axis; single human-readable ticks at the sampled sizes.
"""
import argparse
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

C_INC = "#1b9e77"     # teal-green
C_RING = "#d95f02"    # orange
C_IDEAL = "#444444"   # grey (analytic reference)
N_GROUP = 72
TRAFFIC_BOUND = 2.0 * (N_GROUP - 1) / N_GROUP   # 1.9722
T_PKT_NS = 8.32       # one full 4,160 B frame at the realised 2 ps/B


def inc_bound_ns(s):
    """Analytic in-network floor (eq:inc-cost): one uplink serialisation of the
    payload + one-way hop (1,300) + charged reduce (100) + last chunk's descent.
    Validated against the measured arm to <= 17 ns across 4.5 KiB - 288 MiB."""
    w = -(-s // 4096) * T_PKT_NS
    return w + 1300 + 100 + T_PKT_NS


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
    ap.add_argument("--out", default="allreduce_ab_pcm")
    args = ap.parse_args()

    rows = []
    with open(args.csv) as f:
        for r in csv.DictReader(f):
            if int(r["group_size"]) != N_GROUP or not r["inc_ns"] or not r["ring_ns"]:
                continue
            rows.append((int(r["msg_bytes"]), int(r["inc_ns"]), int(r["ring_ns"]),
                         float(r["ideal_ring_ns"]), float(r["speedup_vs_ideal"])))
    rows.sort()
    S = [r[0] for r in rows]
    inc = [r[1] for r in rows]
    ring = [r[2] for r in rows]
    ideal = [r[3] for r in rows]
    sp_ideal = [r[4] for r in rows]

    fig, (axT, axS) = plt.subplots(1, 2, figsize=(13, 5))

    # --- left: completion time, measured arms + both analytic floors ---
    axT.plot(S, [inc_bound_ns(s) for s in S], ":", color=C_INC, lw=1.6,
             alpha=0.85, label="INC bound  $S/r + 1\\,\\mathrm{hop} + 100$ ns (analytic)")
    axT.plot(S, inc, "o-", color=C_INC, lw=2.2, ms=6, label="INC (in-network, measured)")
    axT.plot(S, ring, "s--", color=C_RING, lw=2.0, ms=6, alpha=0.9, label="measured ring")
    axT.plot(S, ideal, "d:", color=C_IDEAL, lw=2.0, ms=6, label="ideal ring (analytic)")
    axT.set_yscale("log")
    axT.set_ylabel("completion time (ns)")
    axT.set_title("Completion time vs payload")
    axT.legend(fontsize=8.5, loc="lower right")

    # --- right: quotable INC-vs-ideal-ring speedup + traffic-bound asymptote ---
    axS.plot(S, sp_ideal, "o-", color=C_INC, lw=2.2, ms=6, label="INC vs ideal ring")
    axS.axhline(TRAFFIC_BOUND, color="k", lw=1.3, ls="--",
                label=rf"traffic bound  $2(N{{-}}1)/N = {TRAFFIC_BOUND:.3f}\times$")
    axS.set_yscale("log")
    axS.set_ylabel(r"INC speedup vs ideal ring ($\times$)")
    axS.set_title("Convergence to the traffic bound")
    axS.legend(fontsize=9, loc="upper right")

    for ax in (axT, axS):
        ax.set_xscale("log", base=2)
        ax.set_xticks(S)
        ax.set_xticklabels([hb(s) for s in S], rotation=45, ha="right", fontsize=8)
        ax.set_xlabel("AllReduce payload (bytes/rank)")
        ax.grid(True, which="both", alpha=0.25)

    fig.suptitle(r"In-network vs point-to-point AllReduce vs payload on the multi-domain "
                 r"simulator ($|G|=72$)" "\n"
                 "single-switch radix-72 scale-up crossbar, lossless_input PFC, 3600 Gbps/port, "
                 "NIC pinned to the realised wire rate; INC charged 100 ns reduce; ideal ring "
                 "at the realised 492.3 B/ns",
                 fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf  ({len(rows)} points, |G|={N_GROUP})")


if __name__ == "__main__":
    main()
