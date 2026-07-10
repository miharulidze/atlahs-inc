#!/usr/bin/env python3
"""[SP A/B] The apex-fusion premium, op by op: C5 INC-arm timelines.

Parses the per-op *_COMPLETE lines of the two C5 INC arms (extracted from the
run stdout into ops_plainC5_inc.txt / ops_SPC5_inc.txt; sources in the file
headers) and draws one lane per arm: plain TP16 (16 monolithic AllReduce) on
top, TP16+SP (16 AllGather + 14 ReduceScatter) below, aligned at t = 0.

Visual message: per-op cost is nearly IDENTICAL in the two renderings
(AR ~3.5 µs vs AG ~3.3 µs / RS ~3.5 µs -- same 1 MiB, same tree, same
fan-in barrier), so the 1.41x premium (153.25 vs 108.97 µs) is NOT a
per-collective slowdown. It is structural: the monolithic AllReduce turns
around at the switch apex -- ONE fused wave per collective site (reduce up +
broadcast down, no host round-trip) -- while the SP rendering must land the
ReduceScatter shards at the hosts, resynchronise, and re-ascend for the
AllGather: TWO barrier waves per site, i.e. ~2x the ops (30 vs 16) plus the
host turn-around gaps between them.
"""
import argparse
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

# categorical collective-kind palette (house hues, cf. allreduce_ab/)
KIND_COLOR = {"AR": "#1b9e77", "AG": "#7570b3", "RS": "#d95f02"}
KIND_NAME = {"AR": "AllReduce — one fused wave (reduce up + broadcast down)",
             "AG": "AllGather — separate wave: re-ascend + multicast down",
             "RS": "ReduceScatter — separate wave: reduce up, shards land at hosts"}
C_IDEAL = "#444444"   # grey (references / makespan lines)

LINE = re.compile(r"(ALLREDUCE|ALLGATHER|REDUCE_SCATTER)_COMPLETE .*?"
                  r"start_ns=(\d+) complete_ns=(\d+) duration_ns=(\d+)")
KIND = {"ALLREDUCE": "AR", "ALLGATHER": "AG", "REDUCE_SCATTER": "RS"}


def read_ops(path):
    ops = []
    with open(path) as f:
        for line in f:
            m = LINE.search(line)
            if m:
                ops.append({"kind": KIND[m.group(1)],
                            "start": int(m.group(2)) / 1e3,   # us
                            "end": int(m.group(3)) / 1e3,
                            "dur": int(m.group(4)) / 1e3})
    return ops


def lane(ax, ops, yc, h=0.56):
    for op in ops:
        ax.broken_barh([(op["start"], op["end"] - op["start"])],
                       (yc - h / 2, h), facecolors=KIND_COLOR[op["kind"]],
                       edgecolors="white", linewidth=0.6, zorder=3)


def stats(name, ops, makespan):
    busy = sum(o["dur"] for o in ops)
    per = {}
    for o in ops:
        per.setdefault(o["kind"], []).append(o["dur"])
    mix = ", ".join(f'{k} n={len(v)} mean={sum(v)/len(v):.2f}us'
                    for k, v in sorted(per.items()))
    print(f"{name}: {len(ops)} ops, busy {busy:.1f}us / makespan "
          f"{makespan:.1f}us (gaps {makespan - busy:.1f}us); {mix}")
    return busy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plain", default="ops_plainC5_inc.txt")
    ap.add_argument("--sp", default="ops_SPC5_inc.txt")
    ap.add_argument("--out", default="apex_timeline")
    args = ap.parse_args()

    plain = read_ops(args.plain)
    sp = read_ops(args.sp)
    mk_plain, mk_sp = 108.969, 153.252   # reported makespans (run_summary)

    stats("plain C5 INC", plain, mk_plain)
    stats("SP-C5  INC", sp, mk_sp)

    fig, ax = plt.subplots(figsize=(10, 4.4))
    Y_P, Y_S = 1.0, 0.0
    lane(ax, plain, Y_P)
    lane(ax, sp, Y_S)

    # makespan fences + the premium arrow between them
    ax.vlines(mk_plain, Y_P - 0.28, Y_P + 0.28, color=C_IDEAL, ls="--", lw=1.2)
    ax.vlines(mk_sp, Y_S - 0.28, Y_S + 0.28, color=C_IDEAL, ls="--", lw=1.2)
    ax.annotate("108.97 µs", (mk_plain + 1.5, Y_P + 0.30), fontsize=8,
                ha="left", va="bottom", color=C_IDEAL)
    ax.annotate("153.25 µs", (mk_sp + 1.5, Y_S), fontsize=8,
                ha="left", va="center", color=C_IDEAL)
    ax.annotate("", xy=(mk_sp, 0.5), xytext=(mk_plain, 0.5),
                arrowprops=dict(arrowstyle="<->", color="black", lw=1.2))
    ax.annotate("×1.41 apex-fusion premium", ((mk_plain + mk_sp) / 2, 0.56),
                ha="center", va="bottom", fontsize=9, fontweight="bold")

    # iteration boundaries (trace has 2 training iterations)
    it_p = plain[8]["start"]   # first op of iteration 2, plain arm
    it_s = sp[15]["start"]     # first op of iteration 2, SP arm
    ax.vlines(it_p, Y_P - 0.28, Y_P + 0.28, color="black", ls=":", lw=1.0)
    ax.vlines(it_s, Y_S - 0.28, Y_S + 0.28, color="black", ls=":", lw=1.0)
    ax.annotate("iteration 2", (it_p + 1.5, Y_P + 0.30), fontsize=7.5,
                ha="left", va="bottom", color="#555555")
    ax.annotate("iteration 2", (it_s + 1.5, Y_S - 0.33), fontsize=7.5,
                ha="left", va="top", color="#555555")

    # first-site callouts: one wave vs two waves
    ax.annotate("one fused wave per site:\nreduce up + broadcast down\n"
                "in-switch, 3.54 µs",
                xy=(plain[0]["end"] - 1.0, Y_P + 0.29), xytext=(14, Y_P + 0.44),
                ha="left", va="bottom", fontsize=7.5,
                arrowprops=dict(arrowstyle="->", lw=0.9, color="#555555"))
    ax.annotate("two waves per site: AG (3.31 µs), host resync, RS (3.54 µs)",
                xy=(sp[1]["end"] - 1.0, Y_S - 0.29), xytext=(16, Y_S - 0.50),
                ha="left", va="top", fontsize=7.5,
                arrowprops=dict(arrowstyle="->", lw=0.9, color="#555555"))

    # the not-per-op-cost summary
    busy_p = sum(o["dur"] for o in plain)
    busy_s = sum(o["dur"] for o in sp)
    ax.annotate(f"per-op cost ≈ unchanged; op count is not: "
                f"{len(plain)} ops, {busy_p:.0f} µs in-collective (plain)  vs  "
                f"{len(sp)} ops, {busy_s:.0f} µs (SP)",
                xy=(0.99, 0.015), xycoords="axes fraction",
                ha="right", va="bottom", fontsize=7.5, color="#555555")

    ax.set_yticks([Y_S, Y_P])
    ax.set_yticklabels(["TP16+SP\n16 AG + 14 RS", "plain TP16\n16 AllReduce"],
                       fontsize=9)
    ax.set_ylim(-1.05, 1.95)
    ax.set_xlim(-2, 168)
    ax.set_xlabel("time since trace start  (µs)")
    ax.set_title("C5 INC arms op by op: monolithic AllReduce vs its SP "
                 "re-rendering (ReduceScatter+AllGather)\n16 ranks, 1 MiB per "
                 "collective, 2 iterations — single-switch scale-up "
                 "(3600 Gbps, lossless PFC), pcm-sdk two-tier", fontsize=10)
    ax.grid(True, axis="x", alpha=0.25)
    ax.set_axisbelow(True)

    handles = [Patch(facecolor=KIND_COLOR[k], label=KIND_NAME[k])
               for k in ("AR", "RS", "AG")]
    ax.legend(handles=handles, fontsize=7.5, loc="upper right")

    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(f"{args.out}.{ext}", dpi=140, bbox_inches="tight")
    print(f"wrote {args.out}.png / .pdf")


if __name__ == "__main__":
    main()
