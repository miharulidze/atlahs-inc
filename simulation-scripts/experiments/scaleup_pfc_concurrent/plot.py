#!/usr/bin/env python3
"""Plot PFC/backpressure: slowest completion vs N concurrent AllReduces, pinned vs distributed.

Reproduces the fork's Fig 5.12 shape on pcm: pinned grows ~linearly (PFC serialisation at
one core), distributed stays flat (independent cores). Annotates zero packet loss.
Measured-only. Emits pfc_backpressure.pdf (+ .png) into the results dir.

Run in the container (matplotlib):
  docker run --rm -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
      /workspace/simulation-scripts/experiments/scaleup_pfc_concurrent/plot.py
"""
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from common import paths  # noqa: E402

EXP = "scaleup_pfc_concurrent"
OUTDIR = os.environ.get("SCALEUP_OUTPUT_DIR", paths.results_dir(EXP))
CSV = os.path.join(OUTDIR, f"{EXP}.csv")

ARM_STYLE = {
    "pinned":      ("#c0392b", "s", "single core (pinned)"),
    "distributed": ("#e08a1e", "o", "distributed (across cores)"),
}


def load():
    with open(CSV) as f:
        return [r for r in csv.DictReader(f) if r.get("slowest_ns")]


def series(rows, arm):
    return {int(r["n_groups"]): float(r["slowest_ns"]) / 1000.0  # ns -> us
            for r in rows if r["arm"] == arm}


def main():
    if not os.path.exists(CSV):
        sys.exit(f"no results CSV at {CSV} -- run the experiment first")
    rows = load()
    if not rows:
        sys.exit(f"no usable rows in {CSV}")
    total_drops = sum(int(r["drops"]) for r in rows if r.get("drops"))

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for arm, (color, marker, label) in ARM_STYLE.items():
        s = series(rows, arm)
        if not s:
            continue
        xs = sorted(s)
        ax.plot(xs, [s[x] for x in xs], marker=marker, color=color, lw=1.8, ms=6, label=label)

    ax.set_xlabel("concurrent AllReduces (disjoint groups, each 64 KiB)")
    ax.set_ylabel("slowest completion time (us)")
    ax.set_title("Lossless backpressure: one core serialises, many cores share\n"
                 "(pcm-sdk, 256-host 3-tier fat-tree, measured)", fontsize=10)
    ax.grid(ls=":", alpha=0.5)
    ax.set_ylim(bottom=0)
    ax.legend(fontsize=9, loc="upper left")
    loss_txt = "lossless throughout: 0 packet drops" if total_drops == 0 \
        else f"WARNING: {total_drops} drops (lossless violated)"
    ax.text(0.02, 0.02, loss_txt, transform=ax.transAxes, fontsize=8,
            style="italic", color="#333333")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        out = os.path.join(OUTDIR, f"pfc_backpressure.{ext}")
        fig.savefig(out, dpi=150)
        print("wrote", out)


if __name__ == "__main__":
    main()
