#!/usr/bin/env python3
"""G72 application-level plots: gain vs TP byte share (symlog) + SP/compute bars.

Left: end-to-end INC gain per iteration vs TP communication share, plain-TP
      and SP renderings, with the Amdahl reference curves anchored at the
      measured pure-TP speedup S (G1). Symlog y-axis (sub-noise-floor points
      near zero, resolvable points up to ~100 %).
Right: the resolvable configs as bars (baseline vs INC makespan, log scale).
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(HERE)
rows = {r["config"]: r for r in csv.DictReader(open(os.path.join(SUITE, "results.csv")))}

C_INC = "#1b9e77"
C_SP = "#e7298a"
C_BASE = "#666666"

share = {k: float(r["tp_comm_share"]) for k, r in rows.items()}
gain = {k: float(r["gain_pct"]) for k, r in rows.items()}
S_meas = (float(rows["G1"]["baseline_ns"]) / float(rows["G1"]["inc_ns"]))

fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 4.8),
                             gridspec_kw={"width_ratios": [3, 2]})

# ---- left: gain vs share ----
xs = np.logspace(-3, 0, 200)
a1.plot(xs, 100 * xs, "--", color="gray", lw=1,
        label="Amdahl ceiling  gain = share (S→∞)")
a1.plot(xs, 100 * xs * (1 - 1 / S_meas), ":", color="gray", lw=1,
        label=f"Amdahl  gain = share·(1−1/S),  S = {S_meas:.0f} (measured, G1)")
plain = [k for k in ("G2", "G3", "G4", "H4") if k in rows]
a1.plot([share[k] for k in plain], [gain[k] for k in plain], "o",
        color=C_INC, ms=8, label="plain-TP rendering (AllReduce, ring baseline)")
for k in plain:
    a1.annotate(f"{k}: TP72/DP{rows[k]['dp']}/PP{rows[k]['pp']}"
                + (" (H100)" if rows[k]["compute_model"] == "h100" else ""),
                (share[k], gain[k]), textcoords="offset points",
                xytext=(8, -4), fontsize=7.5)
a1.plot([share["G1"]], [gain["G1"]], "o", color=C_INC, ms=10)
a1.annotate(f"G1: TP72/DP1/PP1\n{gain['G1']:.1f} %",
            (share["G1"], gain["G1"]), textcoords="offset points",
            xytext=(-86, -16), fontsize=8, color=C_INC, fontweight="bold")
for k, anchor in (("SPG1", "G1"), ("SPG4", "G4")):
    if k in rows:
        a1.plot([share[k]], [gain[k]], "s", mfc="none", mec=C_SP, ms=11, mew=2)
        a1.annotate(f"{k}\n{gain[k]:.1f} %", (share[k], gain[k]),
                    textcoords="offset points", xytext=(8, 4),
                    fontsize=7.5, color=C_SP)
a1.axhspan(-9, 9, color="gray", alpha=0.10)
a1.annotate("±5–9 % schedule-noise floor — deltas inside are unresolved",
            (1.2e-3, 10.5), fontsize=7.5, color="gray")
a1.set_xscale("log")
a1.set_yscale("symlog", linthresh=1)
a1.set_xlabel("TP communication share of bytes  (coll / (coll + send), INC-arm .goal)")
a1.set_ylabel("end-to-end INC gain per iteration  (%)")
a1.set_title(f"llama3-class A/B at group size 72 — gain vs TP share")
a1.legend(fontsize=7.5, loc="lower right")
a1.grid(alpha=0.3)

# ---- right: resolvable configs, makespan bars ----
keys = [k for k in ("G1", "SPG1", "SPG4") if k in rows]
x = np.arange(len(keys))
b = [float(rows[k]["baseline_ns"]) / 1e6 for k in keys]
i = [float(rows[k]["inc_ns"]) / 1e6 for k in keys]
a2.bar(x - 0.18, b, 0.36, color=C_BASE, label="endpoint baseline")
a2.bar(x + 0.18, i, 0.36, color=C_INC, label="INC")
for xi, (bb, ii, k) in enumerate(zip(b, i, keys)):
    a2.annotate(f"{gain[k]:.1f} %", (xi, max(bb, ii) * 1.15), ha="center",
                fontsize=8.5, fontweight="bold", color=C_INC)
a2.set_yscale("log")
a2.set_xticks(x, [f"{k}\n{rows[k]['coll_kinds']}" for k in keys], fontsize=8)
a2.set_ylabel("makespan (ms)")
a2.set_title("resolvable configs (10–100× the noise floor)")
a2.legend(fontsize=8)
a2.grid(alpha=0.3, axis="y")

fig.suptitle("Application level at |G| = 72 — multi-domain simulator, 72-GPU scale-up domain per node, "
             "llama3-class model re-factored to 72 heads (h 4608, seq 144); ring-decomposed baseline",
             fontsize=9.5)
fig.tight_layout(rect=[0, 0, 1, 0.94])
for ext in ("png", "pdf"):
    fig.savefig(os.path.join(SUITE, f"g72_gain_vs_share.{ext}"), dpi=150)
print("wrote g72_gain_vs_share.{png,pdf}")
