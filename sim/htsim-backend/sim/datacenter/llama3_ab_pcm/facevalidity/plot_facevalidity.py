#!/usr/bin/env python3
"""Face-validity overlay: does the D2 gain-vs-TP-share characterization hold at
a MORE REALISTIC block scale?

This reuses the committed D2 sweep (`../tp_share_sweep/results.csv`, five 16-rank
llama3 parallelism configs, 2-layer/seq-128 toy) and overlays ONE new point:
the C3 anchor parallelism (TP4/DP2/PP2, plain Megatron TP) with the Llama3 block
scaled by GEOMETRY ONLY -- num_layers 2->8, seq_len 128->512 -- everything else
(parallelism, compute cost model, engine, topos, flags) identical to the C3
anchor.  Both arms (decomposed baseline vs INC) run on the two-tier pcm-sdk
engine exactly as the anchor (`../results.md`, `../tp_share_sweep/run_summary`).

RE-MEASURED 2026-07-14 (NIC injection-rate fix): all runs re-done on the
pcm-sdk engine (run-only) with -intranode_linkspeed 3600000 -- the flag was
previously never passed, so every p2p baseline was silently NIC-capped at the
COPY_ENG default 200 Gbps while the ACK-less INC datapath was never capped.
At the fixed NIC the picture changes honestly:
  * the 8-layer face-validity point is +9.13% at share 3.33% -- still ABOVE
    the byte-share Amdahl ceiling (~2.7x), so the critical-path-structural
    character survives at the realistic geometry;
  * but the 2-layer plain-C3 toy anchor FLIPS NEGATIVE (-4.65%) under the
    placeholder compute model (schedule/congestion-structure effect, not the
    collective datapath; under the calibrated H100 roofline the same config
    is +8.81% -- see computemodel/), so the y-axis is now SYMLOG, not log,
    and the old multiplicative gain decomposition is void.
The comm-share driver is a trace property and is unchanged: 0.85%->3.33%
(x3.9) from 8 layers (4x AllReduces) and seq 512 (4x payloads).

Run with /usr/bin/python3 (matplotlib 3.9.4).
"""
import csv
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
D2_CSV = HERE.parent / "tp_share_sweep" / "results.csv"
FV_CSV = HERE / "facevalidity_point.csv"

# house palette (colorbrewer Dark2, cf. ../tp_share_sweep/plot_gain_vs_tp_share.py)
C_INC = "#1b9e77"     # teal-green: committed D2 2-layer configs
C_IDEAL = "#444444"   # grey: analytic reference curves
C_FV = "#d95f02"      # orange: the face-validity larger-geometry point


def read_d2():
    rows = []
    with open(D2_CSV) as f:
        for r in csv.DictReader(f):
            rows.append({
                "cfg": r["config"],
                "label": f'{r["config"]}: TP{r["tp"]}/DP{r["dp"]}/PP{r["pp"]}',
                "share": float(r["tp_comm_share"]),
                "gain": float(r["gain_pct"]),
            })
    return sorted(rows, key=lambda d: d["share"])


def read_fv():
    with open(FV_CSV) as f:
        r = next(csv.DictReader(f))
    return {
        "share": float(r["tp_comm_share"]),
        "gain": float(r["gain_pct"]),
        "layers": int(r["num_layers"]),
        "seq": int(r["seq_len"]),
    }


def main():
    d2 = read_d2()
    fv = read_fv()

    # measured pure-TP speedup S anchors the Amdahl reference curve (C5, share=1)
    s_meas = None
    for d in d2:
        if d["share"] == 1.0:
            s_meas = 1.0 / (1.0 - d["gain"] / 100.0)

    fig, ax = plt.subplots(figsize=(7.8, 5.6))

    shares = np.logspace(np.log10(2e-3), 0, 200)
    ax.plot(shares, 100 * shares, "--", color=C_IDEAL, alpha=0.8, lw=1.2,
            label="Amdahl ceiling  gain = share  (S->inf)")
    if s_meas:
        ax.plot(shares, 100 * shares * (1 - 1 / s_meas), ":", color=C_IDEAL,
                lw=1.2,
                label=f"Amdahl  gain = share*(1-1/S),  S = {s_meas:.1f} "
                      f"(measured, C5)")

    # committed D2 2-layer configs
    ax.plot([d["share"] for d in d2], [d["gain"] for d in d2], "o",
            color=C_INC, ms=8, zorder=3,
            label="D2 sweep (2 layers, seq 128)")
    off = {"C1": (-8, 6), "C2": (8, -12), "C3": (-4, -16), "C4": (8, -12),
           "C5": (-8, 8)}
    ha = {"C5": "right", "C1": "right", "C3": "right"}
    for d in d2:
        ax.annotate(d["label"], (d["share"], d["gain"]),
                    textcoords="offset points",
                    xytext=off.get(d["cfg"], (8, 2)),
                    ha=ha.get(d["cfg"], "left"), fontsize=8, color=C_INC)

    # geometry-scaling move: C3 (2L) -> C3-8L, same parallelism, bigger block
    c3 = next(d for d in d2 if d["cfg"] == "C3")
    ax.annotate("", xy=(fv["share"], fv["gain"]),
                xytext=(c3["share"], c3["gain"]),
                arrowprops=dict(arrowstyle="-|>", color=C_FV, lw=1.6,
                                alpha=0.9, connectionstyle="arc3,rad=0.18"),
                zorder=4)

    # the face-validity point
    ax.plot([fv["share"]], [fv["gain"]], "*", color=C_FV, ms=20, zorder=6,
            markeredgecolor="white", markeredgewidth=0.8,
            label=f"face-validity: C3 geometry x{fv['layers']//2} layers, "
                  f"seq {fv['seq']}")
    ax.annotate(f"C3-8L  ({fv['layers']} layers, seq {fv['seq']})\n"
                f"gain {fv['gain']:.2f}%  at share {fv['share']*100:.1f}%\n"
                f"(plain C3 2L: {c3['gain']:.2f}% at "
                f"{c3['share']*100:.2f}%)",
                (fv["share"], fv["gain"]), textcoords="offset points",
                xytext=(20, 26), ha="left", fontsize=8.5, color=C_FV,
                fontweight="bold")

    # callout: 8L point above the ceiling, 2L point flipped negative
    ax.annotate("8L point still ABOVE the byte-share ceiling (~2.7x):\n"
                "PP puts TP AllReduce on the critical path.\n"
                "The 2L toy anchor is NEGATIVE at the fixed NIC\n"
                "(placeholder-compute schedule effect; +8.81% under\n"
                "the H100 roofline -- see computemodel/)",
                (fv["share"], fv["gain"]), textcoords="offset points",
                xytext=(20, -64), fontsize=7.5, color="#7570b3")

    ax.axhline(0, color="#999999", lw=0.8, zorder=1)
    ax.set_xscale("log")
    # symlog, not log: the re-measured 2-layer C1/C3 gains are NEGATIVE
    ax.set_yscale("symlog", linthresh=1.0)
    ax.set_ylim(-10, 200)
    ax.set_xlabel("TP communication share of bytes  "
                  "(coll / (coll + send), INC-arm .goal)")
    ax.set_ylabel("end-to-end INC gain per iteration  (%)")
    ax.set_title(
        "Face-validity: end-to-end INC gain vs TP share at larger block "
        "geometry\n"
        "C3 parallelism (TP4/DP2/PP2, 16 ranks) -- 2-layer/seq-128 toy vs "
        "8-layer/seq-512\n"
        "re-measured 2026-07-14, pcm-sdk (run-only), "
        "-intranode_linkspeed 3600000", fontsize=10)
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8, loc="lower right")

    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(HERE / f"gain_vs_tp_share_facevalidity.{ext}", dpi=140,
                    bbox_inches="tight")
    print("wrote gain_vs_tp_share_facevalidity.png / .pdf")

    # console summary (2026-07-14: the old multiplicative gain decomposition
    # is void -- the re-measured 2L C3 gain is NEGATIVE, so gain ratios
    # against it are meaningless; only the share ratio survives)
    ratio_share = fv["share"] / c3["share"]
    print(f"\nC3 2L  -> C3-8L : gain {c3['gain']:.4f}% -> {fv['gain']:.4f}% "
          f"(SIGN FLIP; no ratio)")
    print(f"                  share {c3['share']*100:.3f}% -> "
          f"{fv['share']*100:.3f}% (x{ratio_share:.2f}, trace property)")
    print(f"  8L point vs byte-share Amdahl ceiling (gain/share): "
          f"{fv['gain']/(fv['share']*100):.2f}x above")


if __name__ == "__main__":
    main()
