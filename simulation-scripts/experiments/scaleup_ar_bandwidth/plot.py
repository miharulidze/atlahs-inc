#!/usr/bin/env python3
"""Plot AllReduce reduction bandwidth: fused apex vs composed RS+AG.

Reduction bandwidth 8*S/T (Gbit/s) vs message size, one line per in-network
structure, with the wire-speed reference line. Measured-only -- no theory/model
curves (per the 2026-07-23 thesis decision to keep the plots measured-only).

Emits ar_bandwidth.pdf (+ .png preview) into the results dir. Copy the PDF into
thesis-skeleton/figures/matplotlib/ for the manuscript.

Run in the container (matplotlib lives there):
  docker run --rm -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
      /workspace/simulation-scripts/experiments/scaleup_ar_bandwidth/plot.py
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

EXP = "scaleup_ar_bandwidth"
OUTDIR = os.environ.get("SCALEUP_OUTPUT_DIR", paths.results_dir(EXP))
CSV = os.path.join(OUTDIR, f"{EXP}.csv")

# (color, marker, legend label) per arm. ASCII-safe labels (no exotic glyphs).
ARM_STYLE = {
    "apex":     ("#1f77b4", "o", "AllReduce"),
    "composed": ("#e08a1e", "s", "composed AllReduce (RS + AG)"),
}


def size_label(b):
    """Message size written out, e.g. 4KB / 256MB."""
    b = int(b)
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if b >= div:
            return f"{b / div:g}{unit}"
    return f"{b}B"


def load():
    with open(CSV) as f:
        return [r for r in csv.DictReader(f) if r.get("bandwidth_gbps")]


def series(rows, arm):
    """size (bytes) -> reduction bandwidth (Gbit/s) for one arm."""
    return {int(r["msg_bytes"]): float(r["bandwidth_gbps"])
            for r in rows if r["arm"] == arm}


def wire_gbps(rows):
    for r in rows:
        try:
            return float(r["intranode_linkspeed_mbps"]) / 1000.0  # Mbps -> Gbit/s
        except (KeyError, ValueError, TypeError):
            continue
    return None


def main():
    if not os.path.exists(CSV):
        sys.exit(f"no results CSV at {CSV} -- run the experiment first")
    rows = load()
    if not rows:
        sys.exit(f"no usable rows in {CSV}")

    gsize = rows[0].get("group_size", "?")
    wire = wire_gbps(rows)

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for arm, (color, marker, label) in ARM_STYLE.items():
        s = series(rows, arm)
        if not s:
            continue
        xs = sorted(s)
        ax.plot(xs, [s[x] for x in xs], marker=marker, color=color, lw=1.6, ms=5, label=label)
    if wire:
        ax.axhline(wire, ls=":", color="#333333", lw=1, label=f"wire speed ({wire:.0f} Gbit/s)")

    ax.set_xscale("log", base=2)
    xs_all = sorted({int(r["msg_bytes"]) for r in rows if r.get("msg_bytes")})
    ax.set_xticks(xs_all)
    ax.set_xticklabels([size_label(x) for x in xs_all], fontsize=8)
    ax.minorticks_off()
    ax.set_xlabel("message size")
    ax.set_ylabel("reduction bandwidth (Gbit/s)")
    ax.set_title(f"AllReduce reduction bandwidth: switch turnaround vs composed (RS + AG)\n"
                 f"(|G|={gsize}, pcm-sdk, measured)", fontsize=10)
    ax.grid(ls=":", alpha=0.5)
    ax.set_ylim(bottom=0)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        out = os.path.join(OUTDIR, f"ar_bandwidth.{ext}")
        fig.savefig(out, dpi=150)
        print("wrote", out)


if __name__ == "__main__":
    main()
