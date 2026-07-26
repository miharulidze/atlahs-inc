#!/usr/bin/env python3
"""Thesis figures for the collective A/B, generated from the CURRENT result CSVs.

Supersedes _gen_thesis_plots.py, which read results/scaleup_coll_ab_nicpaced/g{16,64}.csv
-- a 2026-07-22 snapshot taken before the 50 ns propagation refresh, the SEG-chunked
ring baselines, the receiver-NIC removal and the 2026-07-26 AllGather completion fix.
Those figures disagreed with the chapter's own tables by more than 2x (e.g. AllReduce
at 4 KiB: inc 176 / base 40,620 there against 416 / 101,148 now). It also drew a
FITTED t0 theory line, which the chapter's "nothing fitted" claim forbids.

This script is measured-only (2026-07-23 decision: theory lines removed from plots)
and reads exactly the CSVs the tables read, so figures and tables cannot drift apart:
  results/scaleup_coll_ab/scaleup_coll_ab.csv             |G|=64, 9 sizes, 2 fabrics
  results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv  |G|=2..64 at 4 KiB and 4 MiB

Run in the atlahs-sim container (which has matplotlib):
  docker run --rm -v <atlahs>:/workspace --entrypoint python3 atlahs-sim \\
      /workspace/simulation-scripts/_gen_collective_plots.py
Writes results/scaleup_coll_ab/thesis_figs/*.pdf under the repo.
"""
import csv, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

WS   = os.environ.get("ATLAHS_WS", "/workspace")
MAIN = f"{WS}/simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv"
SWEEP= f"{WS}/simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv"
OUT  = f"{WS}/simulation-scripts/results/scaleup_coll_ab/thesis_figs"

SS = "scaleup_single_switch_64_4000Gbps.topo"
FT = "scaleup_3tier_256_4000Gbps.topo"

# Tableau-10, the house palette
C = {"bcast": "#1f77b4", "reduce": "#17becf", "allreduce": "#d62728",
     "allreduce_rd": "#e377c2", "allreduce_rs_ag": "#8c564b",
     "reduce_scatter": "#2ca02c", "allgather": "#ff7f0e"}
TITLE = {"bcast": "Broadcast", "reduce": "Reduce", "allreduce": "AllReduce",
         "allreduce_rs_ag": r"AllReduce (RS$\circ$AG)",
         "reduce_scatter": "Reduce-Scatter", "allgather": "AllGather"}
SIZE_TICKS = [4096, 65536, 1048576, 16777216, 268435456]
SIZE_LBL = {4096: "4 KiB", 65536: "64 KiB", 1048576: "1 MiB",
            16777216: "16 MiB", 268435456: "256 MiB"}


def load(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def series(rows, coll, topo=SS, algo="ring", N=64):
    out = [(int(r["msg_bytes"]), float(r["inc_ns"]), float(r["base_ns"]))
           for r in rows
           if r["collective"] == coll and r["su_topo"] == topo
           and r["baseline_algo"] == algo and int(r["group_size"]) == N]
    out.sort()
    return out


def style(ax, xlabel="message size", ylabel=None, logy=True):
    ax.set_xscale("log", base=2)
    if logy:
        ax.set_yscale("log")
    ax.grid(True, which="major", ls=":", alpha=0.6)
    ax.set_xticks(SIZE_TICKS)
    ax.set_xticklabels([SIZE_LBL[s] for s in SIZE_TICKS])
    ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)


def fig_overview(main, sweep):
    """Left: speed-up vs size at |G|=64.  Right: speed-up vs |G| at 4 MiB.
    The right panel replaces the old |G|=16 panel, whose only data lived on a
    different fabric (NVL72, 7200 Gbps) and could not be compared with anything."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.6))
    for coll in ("bcast", "reduce", "allreduce", "allreduce_rs_ag",
                 "reduce_scatter", "allgather"):
        s = series(main, coll)
        if not s:
            continue
        a1.plot([x[0] for x in s], [x[2]/x[1] for x in s], marker="o", ms=4,
                color=C[coll], label=TITLE[coll])
    s = series(main, "allreduce", algo="rdouble")
    if s:
        a1.plot([x[0] for x in s], [x[2]/x[1] for x in s], marker="^", ms=4, ls="--",
                color=C["allreduce_rd"], label="AllReduce (rec. doubling base)")
    a1.axhline(1.0, color="k", lw=0.8, ls=":")
    style(a1, ylabel=r"speed-up  (baseline $/$ in-network)")
    a1.set_title(r"(a)  $|G|=64$, single-switch")
    a1.legend(fontsize=7.5, loc="upper right")

    for coll in ("allreduce", "reduce_scatter", "allgather"):
        pts = sorted((int(r["group_size"]), float(r["base_ns"])/float(r["inc_ns"]))
                     for r in sweep
                     if r["collective"] == coll and int(r["msg_bytes"]) == 4194304
                     and r["baseline_algo"] == "ring")
        if pts:
            a2.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", ms=4,
                    color=C[coll], label=TITLE[coll])
    a2.axhline(1.0, color="k", lw=0.8, ls=":")
    a2.set_xscale("log", base=2); a2.set_yscale("log")
    a2.grid(True, which="major", ls=":", alpha=0.6)
    a2.set_xticks([2, 4, 8, 16, 32, 64]); a2.set_xticklabels([2, 4, 8, 16, 32, 64])
    a2.set_xlabel(r"group size $|G|$")
    a2.set_title(r"(b)  $S=4$ MiB, single-switch")
    a2.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(f"{OUT}/inc_overview_speedup.pdf")
    plt.close(fig)


def fig_time(main, coll, fname, extra=()):
    """Absolute completion time vs size, |G|=64, single-switch, measured only."""
    fig, ax = plt.subplots(figsize=(7, 4.6))
    s = series(main, coll)
    ax.plot([x[0] for x in s], [x[2] for x in s], marker="o", ms=4, color="#ff7f0e",
            label="endpoint baseline (ring)")
    ax.plot([x[0] for x in s], [x[1] for x in s], marker="^", ms=4, ls="--",
            color="#1f77b4", label="in-network")
    for ecoll, elabel, ecolor, els in extra:
        es = series(main, ecoll) if ecoll != "rdouble" else series(main, coll, algo="rdouble")
        if not es:
            continue
        y = [x[2] for x in es] if ecoll == "rdouble" else [x[1] for x in es]
        ax.plot([x[0] for x in es], y, marker="s", ms=3.5, ls=els, color=ecolor,
                label=elabel)
    style(ax, ylabel="completion time [ns]")
    ax.set_title(f"{TITLE[coll]},  $|G|=64$,  single-switch")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{OUT}/{fname}")
    plt.close(fig)


def fig_speedup_two_fabrics(main, coll, fname, title):
    """Speed-up vs message size for ONE collective on BOTH fabrics.

    Replaces the absolute-time plot: the absolute curves are two near-parallel lines
    whose interesting content (the ratio) the reader has to compute by eye, and the
    numbers are already in the validation table. The ratio is the quantity the section
    argues about, and putting both fabrics on one axis shows what changes with depth."""
    fig, ax = plt.subplots(figsize=(7, 4.6))
    for topo, label, colour, mk, ls in (
            (SS, r"single switch ($d{=}1$)", "#1f77b4", "o", "-"),
            (FT, r"three-tier ($d{=}3$)",    "#d62728", "^", "--")):
        s_ = series(main, coll, topo=topo)
        if not s_:
            continue
        ax.plot([x[0] for x in s_], [x[2]/x[1] for x in s_],
                marker=mk, ms=4.5, ls=ls, color=colour, label=label)
    ax.axhline(1.0, color="k", lw=0.8, ls=":")
    style(ax, ylabel=r"speed-up  (endpoint $/$ in-network)")
    ax.set_title(title)
    ax.legend(fontsize=8.5)
    fig.tight_layout()
    fig.savefig(f"{OUT}/{fname}")
    plt.close(fig)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    main, sweep = load(MAIN), load(SWEEP)
    fig_overview(main, sweep)
    fig_time(main, "allreduce", "inc_allreduce_time.pdf",
             extra=[("rdouble", "endpoint baseline (rec. doubling)", "#e377c2", "-"),
                    ("allreduce_rs_ag", r"in-network, composed RS$\circ$AG", "#8c564b", "--")])
    fig_time(main, "reduce_scatter", "inc_reduce_scatter_time.pdf")
    fig_time(main, "allgather", "inc_allgather_time.pdf")
    fig_speedup_two_fabrics(main, "bcast", "inc_bcast_speedup.pdf",
                            r"Broadcast, $|G|=64$")
    fig_time(main, "bcast", "inc_bcast_time.pdf")
    fig_time(main, "reduce", "inc_reduce_time.pdf")
    print(f"wrote figures to {OUT}")
