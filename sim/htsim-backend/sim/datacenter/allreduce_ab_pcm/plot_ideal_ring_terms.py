#!/usr/bin/env python3
"""Term dominance inside the analytic ideal ring at |G| = 72.

T_ideal(S) = 2(N-1)/N * S/r  +  (N-1) * t_hop
             \__ bandwidth __/   \__ latency __/

bandwidth: the 2(N-1)/N traffic bound priced at the realised r = 492.3 B/ns
           (payload-linear)
latency:   (N-1) = 71 serial hops x 1,300 ns = 92,300 ns (constant)

Companion of plot_pcm_ab_model.py's right panel (which does the same for the
measured ring's ACK vs wire terms); same 4.5 KiB - 288 MiB range and style.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

C_IDEAL = "#444444"
N = 72
R_BNS = 492.3
T_HOP = 1300.0

S = [4608 * 4**k for k in range(9)]                # 4.5 KiB .. 288 MiB
bw = [2.0 * (N - 1) / N * s / R_BNS for s in S]     # ns
lat = (N - 1) * T_HOP                               # 92,300 ns
ratio = [b / lat for b in bw]
cross = lat * R_BNS * N / (2.0 * (N - 1))           # S where terms are equal


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
ax.plot(S, ratio, "-", color=C_IDEAL, lw=2.4)
ax.axhline(1.0, color="k", lw=1.0, ls="--")
ax.set_xscale("log", base=2)
ax.set_yscale("log")
ax.set_xticks(S)
ax.set_xticklabels([hb(s) for s in S], rotation=45, ha="right", fontsize=8)
ax.set_xlabel("AllReduce payload (bytes/rank)")
ax.set_ylabel(r"$\frac{2(N-1)}{N}\cdot\frac{S}{r} \,/\, (N{-}1)\,t_{hop}$",
              fontsize=13)
ax.set_title(r"Ideal ring at $|G| = 72$: bandwidth term / latency term")
ax.grid(True, which="both", alpha=0.25)
fig.tight_layout()
for ext in ("png", "pdf"):
    fig.savefig(f"ideal_ring_terms.{ext}", dpi=140, bbox_inches="tight")
print(f"crossover S = {cross:,.0f} B = {cross/1024/1024:.2f} MiB/rank")
for s, b, q in zip(S, bw, ratio):
    print(f"{hb(s):>9}: bw {b:>12,.0f} ns  lat {lat:>9,.0f} ns  ratio {q:8.4f}")
