#!/usr/bin/env python3
"""Term dominance inside the measured-ring step model at |G| = 72.

T_ring(S) = 2(N-1) x ( 2*t_hop + t_pkt + W(S/N) )
                      \__ ACK/latency __/  \_ wire _/

Per step the constant part is the data+ACK round trip plus the switch
hand-off (2,600 + 8.32 ns); the payload part is the chunk serialisation
W(S/72). The 2(N-1) = 142 factor multiplies both, so the RATIO between the
terms is per-step and the plotted quantity is W(S/72) / (2*t_hop + t_pkt).

Clean single-line style, same range as ideal_ring_terms.py.
"""
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

C_RING = "#d95f02"
N = 72
T_PKT = 8.32
T_HOP = 1300.0
CONST = 2 * T_HOP + T_PKT          # 2,608.32 ns per step


def W(x):
    return math.ceil(x / 4096) * T_PKT


S = [4608 * 4**k for k in range(9)]            # 4.5 KiB .. 288 MiB
ratio = [W(s / N) / CONST for s in S]
cross = N * (CONST / T_PKT) * 4096             # S where W(S/72) = CONST


def hb(n):
    n = int(n)
    if n >= 1000 * 1024:
        v = n / (1024 * 1024)
        return f"{v:.0f} MiB" if abs(v - round(v)) < 0.05 else f"{v:.3g} MiB"
    if n >= 1024:
        v = n / 1024
        return f"{v:.0f} KiB" if abs(v - round(v)) < 0.05 else f"{v:.1f} KiB"
    return f"{n} B"


fig, ax = plt.subplots(figsize=(7.6, 5.0))
ax.plot(S, ratio, "-", color=C_RING, lw=2.4)
ax.axhline(1.0, color="k", lw=1.0, ls="--")
ax.set_xscale("log", base=2)
ax.set_yscale("log")
ax.set_xticks(S)
ax.set_xticklabels([hb(s) for s in S], rotation=45, ha="right", fontsize=8)
ax.set_xlabel("AllReduce payload (bytes/rank)")
ax.set_ylabel(r"$W(S/72) \,/\, (2\,t_{hop} + t_{pkt})$", fontsize=13)
ax.set_title(r"Measured ring at $|G| = 72$: wire term / ACK term (per step)")
ax.grid(True, which="both", alpha=0.25)
fig.tight_layout()
for ext in ("png", "pdf"):
    fig.savefig(f"measured_ring_terms.{ext}", dpi=140, bbox_inches="tight")
print(f"crossover S ~= {cross:,.0f} B = {cross/1024/1024:.1f} MiB/rank "
      f"(W(S/72) = {CONST} ns)")
for s, q in zip(S, ratio):
    print(f"{hb(s):>9}: W(S/72) {W(s/N):>10,.2f} ns  const {CONST:,.2f} ns  "
          f"ratio {q:9.5f}")
