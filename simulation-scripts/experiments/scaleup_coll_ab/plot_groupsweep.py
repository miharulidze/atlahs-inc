#!/usr/bin/env python3
"""Plot the group-size sweep (X1): the constant-in-|G| demonstration.

Reads results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv (the coll_ab schema, with
group_size swept). Measured-only. Per fixed message size emits:
  * groupsweep_time__<size>.pdf    — completion time vs |G|: in-network (flat for
    bcast/reduce/allreduce = constant-in-|G|; rising for RS/AG = ingress-bound) vs
    the endpoint baseline (rising O(N)/O(logN)).
  * groupsweep_speedup__<size>.pdf — speedup vs |G| (grows for the constant-in-|G| colls).

Run in the container (matplotlib):
  docker run --rm -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
      /workspace/simulation-scripts/experiments/scaleup_coll_ab/plot_groupsweep.py
"""
import csv
import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from common import paths  # noqa: E402

# The group-sweep writes to a sibling results dir (set by run_groupsweep.sh).
OUTDIR = os.environ.get("SCALEUP_OUTPUT_DIR",
                        os.path.join(paths.RESULTS_ROOT, "scaleup_coll_ab_groupsweep"))
CSV = os.path.join(OUTDIR, "scaleup_coll_ab.csv")

COLORS = {"allreduce": "#1f77b4", "allreduce_rs_ag": "#17becf", "reduce_scatter": "#2ca02c",
          "allgather": "#e08a1e", "bcast": "#9467bd", "reduce": "#d62728"}


def fnum(r, k):
    try:
        return float(r[k])
    except (KeyError, ValueError, TypeError):
        return None


def load():
    with open(CSV) as f:
        return list(csv.DictReader(f))


def human(size):
    s = int(size)
    return f"{s // 1024} KiB" if s < 1024 * 1024 else f"{s // (1024 * 1024)} MiB"


def time_plot(rows, size, outdir):
    sub = [r for r in rows if fnum(r, "msg_bytes") == size]
    if not sub:
        return
    inc = defaultdict(dict)   # coll -> {G: inc_ns}
    base = defaultdict(dict)  # coll -> {G: base_ns (ring)}
    for r in sub:
        g = fnum(r, "group_size")
        if not g:
            continue
        coll = r["collective"]
        if fnum(r, "inc_ns"):
            inc[coll][g] = fnum(r, "inc_ns")
        if r.get("baseline_algo") == "ring" and fnum(r, "base_ns"):
            base[coll][g] = fnum(r, "base_ns")
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    for coll, d in sorted(inc.items()):
        xs = sorted(d)
        ax.plot(xs, [d[x] for x in xs], marker="o", ms=4, lw=1.7,
                color=COLORS.get(coll, "#555"), label=f"{coll} (in-network)")
    for coll, d in sorted(base.items()):
        xs = sorted(d)
        ax.plot(xs, [d[x] for x in xs], marker="s", ms=3, lw=1.0, ls="--", alpha=0.7,
                color=COLORS.get(coll, "#555"), label=f"{coll} (endpoint ring)")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    gs = sorted({g for d in list(inc.values()) + list(base.values()) for g in d})
    ax.set_xticks(gs)
    ax.set_xticklabels([str(int(g)) for g in gs])
    ax.minorticks_off()
    ax.set_xlabel("group size |G|")
    ax.set_ylabel("completion time (ns)")
    ax.set_title(f"Completion time vs |G| — {human(size)} (pcm-sdk, single-switch, measured)\n"
                 "in-network flat = constant-in-|G| (bcast/reduce/allreduce)", fontsize=9)
    ax.grid(ls=":", alpha=0.5, which="both")
    ax.legend(fontsize=6, ncol=2)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        out = os.path.join(outdir, f"groupsweep_time__{int(size)}.{ext}")
        fig.savefig(out, dpi=150)
        print("wrote", out)
    plt.close(fig)


def speedup_plot(rows, size, outdir):
    sub = [r for r in rows if fnum(r, "msg_bytes") == size and fnum(r, "speedup")]
    if not sub:
        return
    lines = defaultdict(dict)
    for r in sub:
        lab = f'{r["collective"]}/{r["baseline_algo"]}'
        g = fnum(r, "group_size")
        if g:
            lines[lab][g] = fnum(r, "speedup")
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    for lab, d in sorted(lines.items()):
        xs = sorted(d)
        coll = lab.split("/")[0]
        ax.plot(xs, [d[x] for x in xs], marker="o", ms=4, lw=1.5,
                color=COLORS.get(coll, "#555"), label=lab)
    ax.axhline(1.0, ls=":", color="#999", lw=1)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    gs = sorted({g for d in lines.values() for g in d})
    ax.set_xticks(gs)
    ax.set_xticklabels([str(int(g)) for g in gs])
    ax.minorticks_off()
    ax.set_xlabel("group size |G|")
    ax.set_ylabel("speedup (endpoint / in-network)")
    ax.set_title(f"Speed-up vs |G| — {human(size)} (pcm-sdk, single-switch, measured)", fontsize=10)
    ax.grid(ls=":", alpha=0.5, which="both")
    ax.legend(fontsize=6, ncol=2)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        out = os.path.join(outdir, f"groupsweep_speedup__{int(size)}.{ext}")
        fig.savefig(out, dpi=150)
        print("wrote", out)
    plt.close(fig)


def main():
    if not os.path.exists(CSV):
        sys.exit(f"no group-sweep CSV at {CSV} -- run run_groupsweep.sh first")
    rows = load()
    if not rows:
        sys.exit(f"no usable rows in {CSV}")
    sizes = sorted({fnum(r, "msg_bytes") for r in rows if fnum(r, "msg_bytes")})
    for size in sizes:
        time_plot(rows, size, OUTDIR)
        speedup_plot(rows, size, OUTDIR)


if __name__ == "__main__":
    main()
