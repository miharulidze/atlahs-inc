#!/usr/bin/env python3
"""Plot the measured endpoint results from ``scaleout_allreduce/run.py``."""
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from common import paths  # noqa: E402

EXP_NAME = "scaleout_allreduce"
OUTDIR = os.environ.get("SCALEOUT_OUTPUT_DIR", paths.results_dir(EXP_NAME))
CSV = os.path.join(OUTDIR, f"{EXP_NAME}.csv")
LINES = [
    ("ring", "#ff7f0e", "s", "Ring"),
    ("rdouble", "#2ca02c", "^", "Recursive doubling"),
    ("tree", "#9467bd", "o", "Binomial tree"),
    ("bine", "#d62728", "X", "Bine butterfly"),
    ("naive_inc_bine", "#8c564b", "D", "Naive INC (local AR + Bine)"),
    ("hier_inc_bine", "#159588", "P", "Hierarchical INC (local RS/AG + Bine)"),
]


def size_label(value):
    value = int(value)
    for unit, divisor in (("MB", 1 << 20), ("KB", 1 << 10)):
        if value >= divisor:
            return f"{value / divisor:g}{unit}"
    return f"{value}B"


def points(rows, algo):
    return {int(row["msg_bytes"]): float(row["base_ns"])
            for row in rows if row["baseline_algo"] == algo and row.get("base_ns")}


def setup_xaxis(ax, xs):
    ax.set_xscale("log", base=2)
    ax.set_xticks(xs)
    ax.set_xticklabels([size_label(x) for x in xs], fontsize=8, rotation=45, ha="right")
    ax.minorticks_off()
    ax.set_xlabel("message size")


def save(fig, stem):
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUTDIR, f"{stem}.{ext}"), dpi=150)
    print(f"wrote {stem}")
    plt.close(fig)


def main():
    if not os.path.exists(CSV):
        sys.exit(f"no results CSV at {CSV} -- run the experiment first")
    with open(CSV) as stream:
        rows = [row for row in csv.DictReader(stream) if row.get("base_ns")]
    if not rows:
        sys.exit(f"no successful result rows in {CSV}")
    data = {algo: points(rows, algo) for algo, _, _, _ in LINES}
    xs = sorted({size for series in data.values() for size in series})
    domains = int(rows[0]["group_size"]) // int(rows[0]["gpus_per_node"])
    gpus = rows[0]["gpus_per_node"]
    so = rows[0]["so_topo"].replace(".topo", "")

    fig, ax = plt.subplots(figsize=(6.7, 4.2))
    for algo, color, marker, label in LINES:
        series = data[algo]
        if not series:
            continue
        series_x = sorted(series)
        ax.plot(series_x, [series[x] for x in series_x], color=color, marker=marker,
                ms=5, lw=1.8, label=label)
    ax.set_yscale("log")
    setup_xaxis(ax, xs)
    ax.set_ylabel("endpoint completion time (ns)")
    ax.set_title(f"AllReduce, |G|=64 ({domains} x {gpus}-GPU domains), {so}", fontsize=9)
    ax.grid(ls=":", alpha=0.5, which="both")
    ax.legend(fontsize=8)
    save(fig, "scaleout_allreduce_completion_time")

    rd = data["rdouble"]
    fig, ax = plt.subplots(figsize=(6.7, 4.2))
    for algo, color, marker, label in LINES:
        series = data[algo]
        common = sorted(set(rd) & set(series))
        if not common:
            continue
        ax.plot(common, [rd[x] / series[x] for x in common], color=color, marker=marker,
                ms=5, lw=1.8, label=label)
    ax.axhline(1.0, color="#888", lw=1, ls=":")
    ax.set_yscale("log")
    setup_xaxis(ax, xs)
    ax.set_ylabel("speed relative to recursive doubling")
    ax.set_title(f"Scale-out AllReduce, |G|=64 ({domains} x {gpus}-GPU domains)", fontsize=10)
    ax.grid(ls=":", alpha=0.5, which="both")
    ax.legend(fontsize=8)
    save(fig, "scaleout_allreduce_relative_to_rdouble")


if __name__ == "__main__":
    main()
