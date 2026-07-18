#!/usr/bin/env python3
"""Per-arm model-validation figures at |G| = 72 (companion of plot_pcm_ab_model.py).

Two single-panel figures, same plotting range as allreduce_ab_pcm_model.png
(4.5 KiB - 288 MiB, log-log, hb tick labels), NO ideal ring:

  allreduce_ab_pcm_inc_model.{png,pdf}  : measured INC vs its analytic model
      T_INC(S)  = W(S) + t_hop + reduce + t_pkt = W(S) + 1,408.32 ns
      (one uplink serialisation + one-way hop 1,300 + charged reduce 100 +
       last chunk's descent; eq:inc-cost)
  allreduce_ab_pcm_ring_model.{png,pdf} : measured ring vs its step model
      T_ring(S) = 2(N-1) x (2*t_hop + t_pkt + W(S/N))
                = 142 x (2,600 + 8.32 + W(S/72)) ns
      (stop-and-wait steps, data + ACK round trip per step)

W(x) = ceil(x / 4096) * t_pkt with t_pkt = 8.32 ns (4,160 B frame at the
realised 2 ps/B => 492.3 payload-B/ns). Every constant read from the
configuration -- nothing fitted. Convention: line = model, open markers =
simulation.
"""
import argparse
import csv
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

C_INC = "#1b9e77"
C_RING = "#d95f02"
N_GROUP = 72
T_PKT_NS = 8.32


def W(x):
    return math.ceil(x / 4096) * T_PKT_NS


def inc_model_ns(s):
    return W(s) + 1300 + 100 + T_PKT_NS


def ring_model_ns(s):
    return 2 * (N_GROUP - 1) * (2600 + T_PKT_NS + W(s / N_GROUP))


def hb(n):
    n = int(n)
    if n >= 1000 * 1024:
        v = n / (1024 * 1024)
        return f"{v:.0f} MiB" if abs(v - round(v)) < 0.05 else f"{v:.3g} MiB"
    if n >= 1024:
        v = n / 1024
        return f"{v:.0f} KiB" if abs(v - round(v)) < 0.05 else f"{v:.1f} KiB"
    return f"{n} B"


def make_fig(S, measured, model, color, arm_label, model_label, out):
    resid = [(m - p) / p * 100 for m, p in zip(measured, model)]
    worst = max(abs(r) for r in resid)
    print(f"{out}: residuals (measured vs model, %):")
    for s, m, p, r in zip(S, measured, model, resid):
        print(f"  {hb(s):>9}: measured {m:>10,} model {p:>12,.1f}  {r:+.3f}%")

    fig, ax = plt.subplots(figsize=(8.2, 5.4))
    ax.plot(S, measured, "-", color=color, lw=2.4, alpha=0.95,
            label=f"{arm_label} (simulation)")
    ax.plot(S, model, "--", color="#444444", lw=1.8,
            label=model_label)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xticks(S)
    ax.set_xticklabels([hb(s) for s in S], rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("AllReduce payload (bytes/rank)")
    ax.set_ylabel("completion time (ns)")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=9, loc="upper left")
    ax.set_title(f"{arm_label} vs analytic model at $|G| = 72$  "
                 f"(worst residual {worst:.2f}%)")
    fig.suptitle("every constant read from the configuration "
                 "(1,300 ns one-way hop, 492.3 B/ns realised rate) — nothing fitted",
                 fontsize=9, y=0.945)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    for ext in ("png", "pdf"):
        fig.savefig(f"{out}.{ext}", dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}.png / .pdf")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results_msgsweep_pcm.csv")
    args = ap.parse_args()

    rows = []
    with open(args.csv) as f:
        for r in csv.DictReader(f):
            if int(r["group_size"]) != N_GROUP or not r["inc_ns"] or not r["ring_ns"]:
                continue
            rows.append((int(r["msg_bytes"]), int(r["inc_ns"]), int(r["ring_ns"])))
    rows.sort()
    S = [r[0] for r in rows]
    inc = [r[1] for r in rows]
    ring = [r[2] for r in rows]

    make_fig(S, inc, [inc_model_ns(s) for s in S], C_INC,
             "AllReduce INC, measured",
             r"INC model  $W(S) + 1\,408$ ns",
             "allreduce_ab_pcm_inc_model")
    make_fig(S, ring, [ring_model_ns(s) for s in S], C_RING,
             "AllReduce ring, measured",
             r"ring model  $142 \times (2\,600\,\mathrm{ns} + t_{pkt} + W(S/72))$",
             "allreduce_ab_pcm_ring_model")


if __name__ == "__main__":
    main()
