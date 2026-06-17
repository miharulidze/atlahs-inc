#!/usr/bin/env python3
"""Derived message-size views: multicast speed-up and effective
broadcast goodput vs payload, on one topology (completion time is
topology-size invariant, so the largest fat tree is representative).

--metric speedup : baseline/mcast median completion ratio vs payload,
    one line per |G|, with the theoretical ceiling |G|-1 as a dashed
    horizontal asymptote. The ceiling is the information-theoretic
    limit: multicast replaces |G|-1 serialised copies with one.

--metric goodput : effective broadcast goodput (payload*8 / completion,
    in Gbps) vs payload, one line per (|G|, mode). Multicast saturates
    the link (~link rate) independent of |G|; the unicast emulation is
    throttled to link_rate/(|G|-1). Two theoretical-ceiling overlays make
    the "fraction of wire realised" explicit (cf. the host-centric
    all-reduce ceilings of Patarasuk & Yuan, JPDC 2009): the full link
    rate as the multicast (100%) ceiling, and a per-|G| dashed asymptote
    at link_rate/(|G|-1) as the host-baseline broadcast ceiling.

CSV columns: nodes, group_size, rep, mode, payload_bytes, ...,
    duration_ns  (produced by run_bcast_sweep.py on the MTU manifest)
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt


def load(path):
    by = defaultdict(list)  # (nodes, g, mode, payload) -> [duration_ns]
    with open(path) as f:
        for r in csv.DictReader(f):
            by[(int(r["nodes"]), int(r["group_size"]), r["mode"],
                int(r["payload_bytes"]))].append(int(r["duration_ns"]))
    return by


def med(x):
    x = sorted(x)
    return x[len(x) // 2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--metric", choices=["speedup", "goodput"],
                   required=True)
    p.add_argument("--nodes", type=int, default=1024,
                   help="Topology to plot (default 1024; completion is "
                        "topology-size invariant).")
    p.add_argument("--linkspeed-gbps", type=float, default=100.0)
    p.add_argument("--title", default=None)
    args = p.parse_args()

    by = load(args.csv)
    if not by:
        sys.exit(f"no rows in {args.csv}")
    N = args.nodes
    groups = sorted({k[1] for k in by if k[0] == N})

    fig, ax = plt.subplots(figsize=(8, 5))
    color_map = {}

    for g in groups:
        payloads = sorted({k[3] for k in by
                           if k[0] == N and k[1] == g
                           and k[2] == "mcast"})
        if not payloads:
            continue
        xs = payloads
        line = None
        if args.metric == "speedup":
            ys = [med(by[(N, g, "baseline", s)])
                  / med(by[(N, g, "mcast", s)]) for s in payloads]
            line, = ax.plot(xs, ys, marker="o", markersize=4,
                            linewidth=1.5, label=f"|G|={g}")
            # ceiling |G|-1
            ax.axhline(g - 1, color=line.get_color(), linestyle=":",
                       linewidth=1.0, alpha=0.7)
            ax.text(xs[0], (g - 1) * 1.03, f"$|G|-1={g-1}$",
                    color=line.get_color(), fontsize=7, va="bottom")
        else:  # goodput: one baseline line per |G| (mcast drawn once)
            ys = [(s * 8.0) / med(by[(N, g, "baseline", s)])
                  for s in payloads]
            line, = ax.plot(xs, ys, marker="o", markersize=4,
                            linewidth=1.4, linestyle="-",
                            label=f"|G|={g} baseline")
            # Host-baseline goodput ceiling: the root serialises |G|-1
            # unicast copies, so it can realise at most link_rate/(|G|-1)
            # of the wire. Draw it as a per-|G| dashed asymptote -- the
            # broadcast analogue of the recursive-doubling / RS+AG ceilings
            # in the host-centric all-reduce literature (Patarasuk & Yuan).
            ceil = args.linkspeed_gbps / (g - 1)
            ax.axhline(ceil, color=line.get_color(), linestyle=":",
                       linewidth=1.0, alpha=0.7)
            ax.text(xs[0], ceil * 1.03,
                    f"link/$(|G|-1)$ = {ceil:.0f} Gbps",
                    color=line.get_color(), fontsize=7, va="bottom")

    if args.metric == "goodput":
        # Multicast goodput is independent of |G|, so draw it once.
        g0 = groups[-1]
        payloads = sorted({k[3] for k in by
                           if k[0] == N and k[1] == g0
                           and k[2] == "mcast"})
        ys = [(s * 8.0) / med(by[(N, g0, "mcast", s)]) for s in payloads]
        ax.plot(payloads, ys, marker="s", markersize=5, linewidth=2.0,
                linestyle="--", color="black",
                label="multicast (any |G|)")

    ax.set_xscale("log")
    ax.set_xlabel("Message size (bytes)")
    if args.metric == "speedup":
        ax.set_yscale("log")
        ax.set_ylabel(r"Speed-up (baseline / multicast)")
        default_title = ("Multicast speed-up vs message size "
                         f"({N}-host fat tree)")
    else:
        ax.axhline(args.linkspeed_gbps, color="black", linestyle="--",
                   linewidth=1.2, alpha=0.7,
                   label=(f"link rate = 100% multicast ceiling "
                          f"({args.linkspeed_gbps:.0f} Gbps)"))
        ax.set_ylabel("Effective broadcast goodput (Gbps)")
        default_title = ("Effective broadcast goodput vs message size "
                         f"({N}-host fat tree)")
    ax.set_title(args.title or default_title)
    ax.grid(True, which="both", linestyle=":", linewidth=0.5, alpha=0.6)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    for ext in ("pdf", "png"):
        f = f"{args.out}.{ext}"
        fig.savefig(f, dpi=150, bbox_inches="tight")
        print(f"wrote {f}")


if __name__ == "__main__":
    main()
