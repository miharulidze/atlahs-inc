#!/usr/bin/env python3
"""Generate the thesis figures for \\section{Claude results} in TWO toolchains so
the user can compare look-and-feel:

  A) matplotlib -> vector PDF, matching the existing simulation-scripts house style
     (Tableau colors, figsize (8,6), :-dotted grid, solid+o baseline / dashed+^ INC),
     upgraded from PNG dpi=150 to PDF for paper-level embedding.
  B) pgfplots/TikZ -> \\input-able .tex, LaTeX-native (fonts inherit the document).

Figure set (identical in both):
  1. overview  : INC speedup vs message size, all collectives, 2 panels (|G|=16, |G|=64)
  2. allreduce : absolute completion time vs size, |G|=64, INC measured + INC theory + ring + rdouble
  3. reduce_scatter : absolute, |G|=64, INC measured + INC theory + ring
  4. allgather : absolute, |G|=64, INC measured + INC theory + ring

Theory (INC only): T = t0 + (bytes_over_nic)/R, R = 500 B/ns (4 Tbps NIC rate, not fitted).
  AllReduce / ReduceScatter: bytes_over_nic = M           (N-independent)
  AllGather                : bytes_over_nic = M*(N-1)/N    (own shard is local)
t0 = median residual per collective (~168 ns latency floor).

Run in the atlahs-sim container:
  docker run --rm -v <atlahs>:/workspace -v <jobtmp>:/job atlahs-sim:latest \\
      python3 /job/gen_thesis_plots.py
Reads /workspace/simulation-scripts/results/scaleup_coll_ab_nicpaced/g{16,64}.csv
Writes /job/out/matplotlib/*.pdf and /job/out/pgfplots/*.tex
"""
import csv
import os
import statistics

CSV_DIR = "/workspace/simulation-scripts/results/scaleup_coll_ab_nicpaced"
OUT_MPL = "/job/out/matplotlib"
OUT_TIKZ = "/job/out/pgfplots"

R_NIC = 500.0  # bytes / ns  == 4 Tbps NIC line rate (-intranode_linkspeed 4000000 Mbps)

SIZES = [4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864, 268435456]
SIZE_LABELS = {4096: "4 KiB", 16384: "16 KiB", 65536: "64 KiB", 262144: "256 KiB",
               1048576: "1 MiB", 4194304: "4 MiB", 16777216: "16 MiB",
               67108864: "64 MiB", 268435456: "256 MiB"}

# Tableau-10 (the existing house palette in simulation-scripts/*/run.py)
C_BLUE, C_ORANGE, C_GREEN, C_RED = "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"

COLL_TITLE = {"allreduce": "AllReduce", "reduce_scatter": "ReduceScatter",
              "allgather": "AllGather"}


# ---------------------------------------------------------------- data loading
def load(gsize):
    """rows[(collective, algo)] = list of dicts sorted by msg_bytes."""
    path = os.path.join(CSV_DIR, f"g{gsize}.csv")
    rows = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            key = (r["collective"], r["baseline_algo"])
            rows.setdefault(key, []).append(
                {"M": int(r["msg_bytes"]),
                 "inc": float(r["inc_ns"]),
                 "base": float(r["base_ns"])})
    for v in rows.values():
        v.sort(key=lambda d: d["M"])
    return rows


def inc_series(rows, collective):
    """INC (M, ns) for a collective (algo-independent; take the ring rows)."""
    v = rows[(collective, "ring")]
    return [(d["M"], d["inc"]) for d in v]


def base_series(rows, collective, algo):
    v = rows.get((collective, algo))
    return [(d["M"], d["base"]) for d in v] if v else None


def theory_fn(collective, N):
    """Return (t0, f) where f(M) = t0 + bytes_over_nic(M)/R_NIC."""
    def bytes_over_nic(M):
        if collective == "allgather":
            return M * (N - 1) / N
        return M  # allreduce, reduce_scatter
    return bytes_over_nic


def fit_t0(inc_pts, collective, N):
    """t0 = median(inc_i - bytes_over_nic_i / R_NIC)."""
    b = theory_fn(collective, N)
    return statistics.median(ns - b(M) / R_NIC for M, ns in inc_pts)


def theory_curve(collective, N, t0, npts=60):
    """Log-spaced (M, ns) for a smooth theory line across the measured range."""
    b = theory_fn(collective, N)
    lo, hi = SIZES[0], SIZES[-1]
    import math
    pts = []
    for i in range(npts):
        frac = i / (npts - 1)
        M = lo * (hi / lo) ** frac
        pts.append((M, t0 + b(M) / R_NIC))
    return pts


def speedups(rows, collective, algo):
    v = rows.get((collective, algo))
    if not v:
        return None
    return [(d["M"], d["base"] / d["inc"]) for d in v]


# ------------------------------------------ intranode link-speed sweep (app-level)
INTRANODE_CSV = ("/workspace/simulation-scripts/results/"
                 "intranode_linkspeed_sweep/sweep_ib200.csv")
INTRA_SPEEDS = [100, 200, 400, 800, 1600, 3200, 6400]  # NVLink 3600 shown as a vline
NVLINK_GBPS = 3600
INTRA_CONFIGS = [("pp1_tp4_dp4_pp1", "TP4$\\cdot$DP4$\\cdot$PP1", C_BLUE, "o"),
                 ("pp1_tp2_dp8_pp1", "TP2$\\cdot$DP8$\\cdot$PP1", C_ORANGE, "s")]


def intranode_speedups():
    """{config: [(speed_gbps, baseline/inc), ...]} from the ib200 sweep CSV."""
    import csv as _csv
    t = {}
    for r in _csv.DictReader(open(INTRANODE_CSV)):
        v = r.get("time_per_iter_s", "")
        if v not in ("", None):
            t[(r["config"], int(r["intranode_linkspeed_gbps"]), r["arm"])] = float(v)
    out = {}
    for cfg, *_ in INTRA_CONFIGS:
        pts = []
        for s in INTRA_SPEEDS:
            b, i = t.get((cfg, s, "baseline")), t.get((cfg, s, "inc"))
            if b and i:
                pts.append((s, b / i))
        out[cfg] = pts
    return out


# ------------------------------------------------------------------ matplotlib
def make_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FixedLocator, FixedFormatter, NullLocator

    os.makedirs(OUT_MPL, exist_ok=True)
    g16, g64 = load(16), load(64)

    def size_axis(ax, labels=True):
        ax.set_xscale("log", base=2)
        ax.xaxis.set_major_locator(FixedLocator(SIZES))
        ax.xaxis.set_minor_locator(NullLocator())
        if labels:
            ax.set_xticklabels([SIZE_LABELS[s] for s in SIZES], rotation=45, ha="right",
                               fontsize=8)
        else:
            ax.set_xticklabels([])
        ax.grid(True, which="both", ls=":", alpha=0.5)

    # ---- Fig 1: overview speedup. 2x2: top row = log (full magnitude),
    #      bottom row = linear zoom to [0.9, 3.6] so the converged tail is readable.
    series = [("allreduce", "ring", C_BLUE, "o", "AllReduce (ring)"),
              ("allreduce", "rdouble", C_ORANGE, "s", "AllReduce (rec.-doubling)"),
              ("reduce_scatter", "ring", C_GREEN, "^", "ReduceScatter"),
              ("allgather", "ring", C_RED, "D", "AllGather")]
    logticks = [1, 2, 5, 10, 20, 50, 100, 200]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for col, (rows, N) in enumerate(((g16, 16), (g64, 64))):
        ax = axes[col]
        for coll, algo, color, mk, lab in series:
            sp = speedups(rows, coll, algo)
            if not sp:
                continue
            xs, ys = zip(*sp)
            ax.plot(xs, ys, ls="-", marker=mk, color=color, markersize=6, label=lab)
        ax.axhline(1.0, color="grey", ls=":", lw=1.2, label="no speed-up (1$\\times$)")
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(FixedLocator(logticks))
        ax.yaxis.set_major_formatter(FixedFormatter([str(t) for t in logticks]))
        ax.yaxis.set_minor_locator(NullLocator())
        ax.set_title(f"$|G| = {N}$", fontsize=14)
        size_axis(ax, labels=True)
        ax.set_xlabel("Message size", fontsize=13)
    axes[0].set_ylabel("Speed-up  (baseline / INC)", fontsize=13)
    axes[1].legend(fontsize=9, loc="upper right")
    fig.suptitle("INC speed-up over the endpoint baseline (NIC-paced, scale-up domain)",
                 fontsize=14)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_MPL, "inc_overview_speedup.pdf"))
    plt.close(fig)

    # ---- Figs 2-4: per-collective absolute time + theory ----
    per_coll = [
        ("allreduce", [("ring", C_ORANGE, "s", "Baseline (ring)"),
                       ("rdouble", C_GREEN, "^", "Baseline (rec.-doubling)")]),
        ("reduce_scatter", [("ring", C_ORANGE, "s", "Baseline (ring)")]),
        ("allgather", [("ring", C_ORANGE, "s", "Baseline (ring)")]),
    ]
    N = 64
    rows = g64
    for coll, baselines in per_coll:
        fig, ax = plt.subplots(figsize=(8, 6))
        # baselines (measured only, no theory)
        for algo, color, mk, lab in baselines:
            bs = base_series(rows, coll, algo)
            if not bs:
                continue
            xs, ys = zip(*bs)
            ax.plot(xs, ys, ls="-", marker=mk, color=color, markersize=6, label=lab)
        # INC measured
        inc = inc_series(rows, coll)
        xs, ys = zip(*inc)
        ax.plot(xs, ys, ls="-", marker="o", color=C_BLUE, markersize=6, label="INC")
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        size_axis(ax)
        ax.set_xlabel("Message size", fontsize=13)
        ax.set_ylabel("Completion time (ns)", fontsize=13)
        ax.set_title(f"{COLL_TITLE[coll]} — completion time ($|G| = {N}$)", fontsize=14)
        ax.legend(fontsize=10, loc="upper left")
        fig.tight_layout()
        fig.savefig(os.path.join(OUT_MPL, f"inc_{coll}_time.pdf"))
        plt.close(fig)

    # ---- Fig 5: intranode link-speed sweep (application-level, 16-GPU Llama) ----
    data = intranode_speedups()
    fig, ax = plt.subplots(figsize=(8, 6))
    for cfg, lab, color, mk in INTRA_CONFIGS:
        if not data[cfg]:
            continue
        xs, ys = zip(*data[cfg])
        ax.plot(xs, ys, ls="-", marker=mk, color=color, markersize=6, label=lab)
    ax.axhline(1.0, color="grey", ls=":", lw=1.2, label="no speed-up (1$\\times$)")
    ax.axvline(NVLINK_GBPS, color="#7b1fa2", ls="--", lw=1.3,
               label=f"NVLink ({NVLINK_GBPS} Gbps)")
    ax.set_xscale("log", base=2)
    ax.xaxis.set_major_locator(FixedLocator(INTRA_SPEEDS))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xticklabels([str(s) for s in INTRA_SPEEDS])
    ax.set_ylim(0.99, 1.45)
    ax.set_xlabel("Intranode link speed (Gbps)", fontsize=13)
    ax.set_ylabel("Speed-up  (baseline / INC)", fontsize=13)
    ax.set_title("INC speed-up over baseline (PP=1, inter-node 200 Gbps)", fontsize=13)
    ax.grid(True, which="both", ls=":", alpha=0.5)
    ax.legend(fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_MPL, "inc_intranode_linkspeed.pdf"))
    plt.close(fig)

    print("[matplotlib] wrote", len(os.listdir(OUT_MPL)), "PDFs to", OUT_MPL)


# --------------------------------------------------------------------- pgfplots
def _coords(pts):
    return " ".join(f"({M:.0f},{y:.3f})" for M, y in pts)


TIKZ_HEADER = "% Auto-generated. \\input from \\section{Claude results}. Needs pgfplots.\n"


def _axis_size_opts():
    """Common log-x axis with human message-size tick labels."""
    ticks = ",".join(str(s) for s in SIZES)
    labels = ",".join("{" + SIZE_LABELS[s] + "}" for s in SIZES)
    return (f"  xmode=log, log basis x=2,\n"
            f"  xtick={{{ticks}}},\n"
            f"  xticklabels={{{labels}}},\n"
            f"  x tick label style={{rotate=45, anchor=east, font=\\footnotesize}},\n"
            f"  grid=both, major grid style={{dotted, gray!50}},\n"
            f"  minor grid style={{dotted, gray!20}}, minor tick num=0,\n")


def make_pgfplots():
    os.makedirs(OUT_TIKZ, exist_ok=True)
    g16, g64 = load(16), load(64)

    # ---- Fig 1: overview speedup. 2x2 groupplot: top row = log (magnitude),
    #      bottom row = linear zoom to [0.9,3.6] so the converged tail is readable.
    series = [("allreduce", "ring", "clrAR", "*", "AllReduce (ring)"),
              ("allreduce", "rdouble", "clrARd", "square*", "AllReduce (rec.-doubling)"),
              ("reduce_scatter", "ring", "clrRS", "triangle*", "ReduceScatter"),
              ("allgather", "ring", "clrAG", "diamond*", "AllGather")]
    parts = [TIKZ_HEADER,
             "\\begin{tikzpicture}\n",
             "\\begin{groupplot}[\n",
             "  group style={group size=2 by 2, horizontal sep=1.4cm, vertical sep=1.1cm,\n",
             "               x descriptions at=edge bottom},\n",
             "  width=0.47\\linewidth, height=5.3cm,\n",
             _axis_size_opts(),
             "  xlabel={Message size}, tick align=outside,\n",
             "  legend style={font=\\footnotesize, at={(0.97,0.97)}, anchor=north east},\n",
             "  legend cell align=left,\n",
             "]\n"]
    # groupplot fills row-major: (r0,c0),(r0,c1),(r1,c0),(r1,c1)
    cells = [(g16, 16, "log"), (g64, 64, "log"), (g16, 16, "lin"), (g64, 64, "lin")]
    for idx, (rows, N, mode) in enumerate(cells):
        col = idx % 2
        if mode == "log":
            opts = ["ymode=log", "log ticks with fixed point",
                    "ytick={1,2,5,10,20,50,100,200}", "ymin=0.8",
                    f"title={{$|G|={N}$}}"]
            if col == 0:
                opts.append("ylabel={Speed-up (baseline / INC)}")
        else:
            opts = ["ymode=normal", "ymin=0.9", "ymax=3.6",
                    "ytick={1,1.5,2,2.5,3,3.5}"]
            if col == 0:
                opts.append("ylabel={Speed-up (linear zoom)}")
        parts.append("\\nextgroupplot[" + ", ".join(opts) + "]\n")
        for coll, algo, color, mark, lab in series:
            sp = speedups(rows, coll, algo)
            if not sp:
                continue
            parts.append(f"  \\addplot[color={color}, mark={mark}, thick, "
                         f"mark size=1.8pt] coordinates {{{_coords(sp)}}};\n")
            if mode == "log" and col == 1:
                parts.append(f"  \\addlegendentry{{{lab}}}\n")
        refstyle = "dotted" if mode == "log" else "dashed"
        parts.append(f"  \\addplot[gray, {refstyle}, thick, no marks, forget plot] "
                     f"coordinates {{({SIZES[0]},1) ({SIZES[-1]},1)}};\n")
    parts.append("\\end{groupplot}\n\\end{tikzpicture}\n")
    with open(os.path.join(OUT_TIKZ, "inc_overview_speedup.tex"), "w") as f:
        f.write("".join(parts))

    # ---- Figs 2-4: per-collective absolute time + theory ----
    per_coll = [
        ("allreduce", [("ring", "clrBase", "square*", "Baseline (ring)"),
                       ("rdouble", "clrBase2", "triangle*", "Baseline (rec.-doubling)")]),
        ("reduce_scatter", [("ring", "clrBase", "square*", "Baseline (ring)")]),
        ("allgather", [("ring", "clrBase", "square*", "Baseline (ring)")]),
    ]
    N = 64
    rows = g64
    for coll, baselines in per_coll:
        p = [TIKZ_HEADER, "\\begin{tikzpicture}\n", "\\begin{axis}[\n",
             "  width=0.9\\linewidth, height=7cm,\n",
             "  ymode=log,\n", _axis_size_opts(),
             "  xlabel={Message size}, ylabel={Completion time (ns)},\n",
             f"  title={{{COLL_TITLE[coll]} --- completion time ($|G|={N}$)}},\n",
             "  legend style={font=\\footnotesize, at={(0.02,0.98)}, anchor=north west},\n",
             "  legend cell align=left, tick align=outside,\n", "]\n"]
        for algo, color, mark, lab in baselines:
            bs = base_series(rows, coll, algo)
            if not bs:
                continue
            p.append(f"  \\addplot[color={color}, mark={mark}, thick, mark size=2pt] "
                     f"coordinates {{{_coords(bs)}}};\n  \\addlegendentry{{{lab}}}\n")
        inc = inc_series(rows, coll)
        p.append(f"  \\addplot[color=clrINC, mark=*, thick, mark size=2pt] "
                 f"coordinates {{{_coords(inc)}}};\n  \\addlegendentry{{INC (measured)}}\n")
        t0 = fit_t0(inc, coll, N)
        tc = theory_curve(coll, N, t0)
        if coll == "allgather":
            tlab = "INC model: $t_0 + \\frac{N-1}{N}\\,M/R$"
        else:
            tlab = "INC model: $t_0 + M/R$"
        p.append(f"  \\addplot[color=clrINC, dashed, line width=1pt, no marks] "
                 f"coordinates {{{_coords(tc)}}};\n  \\addlegendentry{{{tlab}}}\n")
        p.append("\\end{axis}\n\\end{tikzpicture}\n")
        with open(os.path.join(OUT_TIKZ, f"inc_{coll}_time.tex"), "w") as f:
            f.write("".join(p))
    print("[pgfplots] wrote", len([x for x in os.listdir(OUT_TIKZ) if x.endswith('.tex')]),
          "tex files to", OUT_TIKZ)


if __name__ == "__main__":
    make_matplotlib()
    make_pgfplots()
    # Report the fitted floors for the record.
    for N, rows in ((16, load(16)), (64, load(64))):
        for coll in ("allreduce", "reduce_scatter", "allgather"):
            inc = inc_series(rows, coll)
            t0 = fit_t0(inc, coll, N)
            print(f"  t0[{coll} N={N}] = {t0:.1f} ns")
