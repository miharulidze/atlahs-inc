#!/usr/bin/env python3
"""Aggregation sweep + plots: Reduce vs Allreduce (apex turn-around) vs
Allreduce (reduce+broadcast composition).

Sweeps completion time over (a) group size |G| at fixed message size and
(b) message size at fixed |G|, on a 16-host three-tier fat tree. Each
data point draws REPS random multi-pod group memberships and reports
min / median / max (Hoefler-style non-parametric summary). Generates
the .cm files, runs htsim_uec, parses the *_COMPLETE lines, writes a
CSV, and renders two PDF/PNG figures.

Run from sim/htsim-backend/plotting with the matplotlib-capable python:
    /usr/bin/python3 plot_aggregation.py
"""
import csv, os, random, re, subprocess, statistics, tempfile
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.normpath(os.path.join(HERE, "..", "sim", "datacenter"))
BIN = os.path.join(SIM, "htsim_uec")
OUTDIR = HERE
NODES = 16
PODS = 4                       # k=4 fat tree: pods 0..3, hosts 4 per pod
REPS = 8
MSS = 2048
SEED = 1
WIRE_GBPS = 100.0              # -linkspeed 100000 Mbit/s = 100 Gbit/s
COMPLETE_RE = re.compile(r"(ALLREDUCE_RB|ALLREDUCE|REDUCE)_COMPLETE\b.*?duration_ns=(\d+)")

# (label, cm op token, extra htsim args, expected complete-prefix)
VARIANTS = [
    ("Reduce",                 "start_reduce",    [],                                   "REDUCE"),
    ("Allreduce (apex)",       "start_allreduce", ["-allreduce_mode", "apex"],          "ALLREDUCE"),
    ("Allreduce (reduce+bcast)","start_allreduce","-allreduce_mode reduce_bcast".split(),"ALLREDUCE_RB"),
]


def pod_of(h):
    return h // (NODES // PODS)


def random_multipod_group(size, rng):
    """Pick `size` distinct hosts spanning >= 2 pods."""
    while True:
        g = rng.sample(range(NODES), size)
        if len({pod_of(h) for h in g}) >= 2:
            return sorted(g)


def run_one(group, op_token, size, extra):
    """Write a one-op .cm, run htsim, return duration_ns (int) or None."""
    cm = "Nodes %d\nGrp %s\nConnections 1\n0->0 id 1 %s 0 size %d\n" % (
        NODES, " ".join(map(str, group)), op_token, size)
    with tempfile.NamedTemporaryFile("w", suffix=".cm", delete=False) as f:
        f.write(cm); path = f.name
    try:
        out = subprocess.run(
            [BIN, "-strat", "ecmp_host", "-tm", path, "-nodes", str(NODES),
             "-linkspeed", "100000", "-seed", "1",
             "-queue_type", "lossless_input"] + extra,
            capture_output=True, text=True, timeout=120).stdout
    finally:
        os.unlink(path)
    last = None
    for m in COMPLETE_RE.finditer(out):
        last = int(m.group(2))
    return last


def sweep(xvals, fixed_other, rows, axis):
    rng = random.Random(SEED)
    data = {lab: {} for lab, *_ in VARIANTS}
    xs = xvals
    for x in xs:
        gsize = x if axis == "G" else fixed_other
        size = fixed_other if axis == "G" else x
        for lab, tok, extra, _pref in VARIANTS:
            ds = []
            for rep in range(REPS):
                g = random_multipod_group(gsize, rng)
                d = run_one(g, tok, size, extra)
                if d is not None:
                    ds.append(d)
                    rows.append([axis, x, lab, rep, d])
            data[lab][x] = ds
    return xs, data


def errbars(xs, data, lab):
    X, lo, mid, hi = [], [], [], []
    for x in xs:
        ds = data[lab].get(x) or []
        if not ds:
            continue
        X.append(x); m = statistics.median(ds)
        mid.append(m / 1000.0); lo.append((m - min(ds)) / 1000.0); hi.append((max(ds) - m) / 1000.0)
    return X, lo, mid, hi


def plot_goodput(xs, data, fname):
    """Reduction bandwidth (Gbit/s) = message_size*8 / completion_time vs
    message size, with the wire-speed reference --- the SHARP metric."""
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    markers = {"Reduce": "o", "Allreduce (apex)": "s", "Allreduce (reduce+bcast)": "^"}
    ax.axhline(WIRE_GBPS, color="grey", linestyle=":", linewidth=1.2,
               label="wire speed (%g Gbit/s)" % WIRE_GBPS)
    for lab, *_ in VARIANTS:
        X, G = [], []
        for x in xs:
            ds = data[lab].get(x) or []
            if not ds:
                continue
            med = statistics.median(ds)               # ns
            X.append(x); G.append(x * 8.0 / med)      # bytes*8 / ns = Gbit/s
        if X:
            ax.plot(X, G, marker=markers[lab], label=lab, linewidth=1.4, markersize=5)
    ax.set_xscale("log", base=2)
    ax.set_xlabel("message size (bytes)")
    ax.set_ylabel("reduction bandwidth (Gbit/s)")
    ax.set_title("Aggregation bandwidth vs message size (|G|=8)")
    ax.grid(True, alpha=0.3); ax.legend()
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUTDIR, fname + "." + ext))
    plt.close(fig)
    print("wrote", fname + ".pdf/.png")


def plot(xs, data, xlabel, title, fname, logx=False):
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    markers = {"Reduce": "o", "Allreduce (apex)": "s", "Allreduce (reduce+bcast)": "^"}
    for lab, *_ in VARIANTS:
        X, lo, mid, hi = errbars(xs, data, lab)
        if not X:
            continue
        ax.errorbar(X, mid, yerr=[lo, hi], marker=markers[lab], capsize=3,
                    label=lab, linewidth=1.4, markersize=5)
    if logx:
        ax.set_xscale("log", base=2)
    ax.set_xlabel(xlabel); ax.set_ylabel("completion time (us)")
    ax.set_title(title); ax.grid(True, alpha=0.3); ax.legend()
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUTDIR, fname + "." + ext))
    plt.close(fig)
    print("wrote", fname + ".pdf/.png")


def main():
    rows = [["axis", "x", "variant", "rep", "duration_ns"]]
    # (a) vs group size, fixed 16 KiB message
    gx, gdata = sweep([2, 4, 8, 16], 16384, rows, "G")
    plot(gx, gdata, "group size |G|",
         "Aggregation completion vs |G| (16-host fat tree, 16 KiB)",
         "agg_vs_groupsize", logx=True)
    # (b) vs message size, fixed |G|=8
    mx, mdata = sweep([2048, 8192, 32768, 131072, 524288, 2097152], 8, rows, "M")
    plot(mx, mdata, "message size (bytes)",
         "Aggregation completion vs message size (|G|=8)",
         "agg_vs_msgsize", logx=True)
    # (c) bandwidth view of the same data --- the SHARP metric.
    plot_goodput(mx, mdata, "agg_bandwidth")
    with open(os.path.join(OUTDIR, "agg_sweep.csv"), "w", newline="") as f:
        csv.writer(f).writerows(rows)
    print("wrote agg_sweep.csv (%d rows)" % (len(rows) - 1))


if __name__ == "__main__":
    main()
