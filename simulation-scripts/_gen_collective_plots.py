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
import csv, math, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator, NullFormatter, ScalarFormatter

WS   = os.environ.get("ATLAHS_WS", "/workspace")
MAIN = f"{WS}/simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv"
SWEEP= f"{WS}/simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv"
FOOT = f"{WS}/simulation-scripts/results/scaleup_coll_footprint/scaleup_coll_footprint.csv"
OUT  = f"{WS}/simulation-scripts/results/scaleup_coll_ab/thesis_figs"

SS = "scaleup_single_switch_64_4000Gbps.topo"
FT = "scaleup_3tier_256_4000Gbps.topo"

# Sweep restriction (2026-07-26), mirroring _gen_rsag_tables.shard_ok: the sharded
# collectives are plotted only where one rank's shard fills at least a full MSS, i.e.
# S >= N*MSS.  Below that, lambda(d)'s full-MTU t_ser over-charges the ring model and the
# baseline curve carries an artefact of the model rather than of the fabric.  Broadcast
# and Reduce are not sharded and keep every size.
MSS_B  = 4096
SHARDED = {"reduce_scatter", "allgather", "allreduce", "allreduce_rs_ag"}

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
    if coll in SHARDED:
        dropped = [S for S, _, _ in out if S // N < MSS_B]
        out = [t for t in out if t[0] // N >= MSS_B]
        if dropped:
            print(f"  [shard rule] {coll} |G|={N}: dropped "
                  + ", ".join(f"{S:,}B" for S in sorted(dropped)))
    out.sort()
    return out


def plain_log_y(ax, ticks):
    """Label a log y-axis with plain integers instead of powers of ten.

    A speed-up of 122 reads better as "122" than as a point between 10^2 and 10^1, and
    the reader is comparing magnitudes by eye rather than reading exponents."""
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(FixedLocator(ticks))
    ax.yaxis.set_major_formatter(ScalarFormatter())
    ax.yaxis.set_minor_formatter(NullFormatter())
    # deliberately NO set_ylim: forcing limits from the tick list clipped both the
    # AllReduce curve at the top and the sub-unity Reduce-Scatter point at |G|=2,
    # which is a result rather than an outlier. Autoscale, and give the tick list
    # enough range to label whatever the data covers.


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
    plain_log_y(a1, [1, 2, 5, 10, 20, 50, 100, 200])
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
    a2.set_xscale("log", base=2)
    plain_log_y(a2, [0.5, 1, 2, 5, 10, 20])
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
    plain_log_y(ax, [1, 2, 5, 10, 20, 50, 100, 200])
    ax.set_title(title)
    ax.legend(fontsize=8.5)
    fig.tight_layout()
    fig.savefig(f"{OUT}/{fname}")
    plt.close(fig)


# ── the speed-up formula's two regimes, and where they hand over ──────────────
_B, _H, _MSS, _TL, _TSW = 500.0, 64, 4096, 50.0, 300.0
_FRAME, _SEG = _MSS + _H, 512 * 1024


def _fw(x):    return x + _H * math.ceil(x / _MSS)
def _lam(d):   return 2*(2*d*_TL + (2*d-1)*_TSW) + (2*d-1)*_FRAME/_B
def _tinc(d):  return 2*d*_TL + (2*d-1)*_TSW + 2*d*_FRAME/_B


def _t_pkt(x):
    """Serialization time of the leading packet of an x-byte stream."""
    return _fw(min(x, _MSS)) / _B


def _lam_chunk(d, x):
    """Residual round-trip latency after injecting an x-byte ring chunk."""
    return 2*(2*d*_TL + (2*d-1)*_TSW) + (2*d-1)*_t_pkt(x)


def _delta(d, x):
    """Residual one-way path latency after injecting an x-byte stream."""
    return 2*d*_TL + (2*d-1)*_TSW + (2*d-1)*_t_pkt(x)


def _wire_knee(d):
    """Smallest integer S for which f_w(S)/B >= delta(d, S)."""
    lo, hi = 1, _MSS
    while _fw(hi) / _B < _delta(d, hi):
        hi *= 2
    while lo < hi:
        mid = (lo + hi) // 2
        if _fw(mid) / _B < _delta(d, mid):
            lo = mid + 1
        else:
            hi = mid
    return lo


def _t_root(d):
    """The chapter's delta(d) as printed in Equation (T-INC): 2d links, 2d-1 switches,
    and 2d-1 store-and-forward frames.

    NOT _tinc(d), which charges 2d frames. That is the `fill` form, which the table
    generator uses only as an intermediate and then subtracts the leading frame back out
    of (inc_root = fill(d,w) + (f_w(S)-w)/B). The two differ by one frame time, 8.32 ns.
    Use this one wherever a figure has to agree with a validation table to the digit."""
    return 2*d*_TL + (2*d-1)*(_TSW + _FRAME/_B)


def _census(N, per_leaf=4, per_pod=16):
    c = {1: 0, 2: 0, 3: 0}
    for i in range(N - 1):
        d = 1 if i//per_leaf == (i+1)//per_leaf else (2 if i//per_pod == (i+1)//per_pod else 3)
        c[d] += 1
    return c


def _batches(N, per_leaf=4, per_pod=16, r=0):
    """A MEMBER's peers by depth -- 3/12/48 at N=64 -- not _census, which counts ring
    STEPS by depth and comes out reversed (48/12/3). Conflating them silently passes."""
    c = {1: 0, 2: 0, 3: 0}
    for i in range(N):
        if i != r:
            c[1 if i//per_leaf == r//per_leaf
              else (2 if i//per_pod == r//per_pod else 3)] += 1
    return {k: v for k, v in c.items() if v}


def fig_rsag_regimes(main, fname, N=64):
    """Figure 4.5's regime plot, for the two sharded collectives.

        speedup^C(S) = ((N-1) lam_max + (N-1)/N T) / T_inc^C(S),   T = f_w(S)/B

    Same shape as the rooted case -- a monotone decay from a latency bound to a
    bandwidth one -- but the two collectives share a NUMERATOR (one endpoint ring
    measures both) and differ in the denominator, so they leave the same latency bound
    and land on different asymptotes:
        RS:  t_d + T                              -> (N-1)/N,  a hair BELOW one
        AG:  max_s [t_s + (n_{>=s}/N) T]          -> exactly 1
    The two are 1.6% apart at N=64 and invisible on a log axis, so one dotted line is
    drawn at 1 and the RS asymptote is named in the caption rather than plotted --
    drawing two indistinguishable lines would imply a resolution the figure lacks.

    NO f_w(S)/B = t_d marker, unlike the rooted figure. There the condition is exact:
    the in-network denominator is t_d + T, so it is where the two terms cross. Here it
    would be exact for Reduce-Scatter and meaningless for AllGather, whose denominator
    is a maximum over depth batches with no single latency term to cross -- and a
    vertical line that reads for only one of two plotted curves misleads.

    Sized to be READ: 7.4in wide, not the rooted figure's 11in. Both are included at
    \linewidth (369pt = 5.1in), so 11in is downscaled 0.46x and 7.5pt type lands at
    3.5pt on the page. At 7.4in the scale is 0.69x and 10pt type lands near 7pt."""
    sizes = [2**(k/4) for k in range(70, 118)]      # from the smallest legal shard up
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.9), sharey=True)
    for ax, (tag, topo, d) in zip(axes, (
            (r"(a)  single switch, $d{=}1$", SS, 1),
            (r"(b)  three-tier, $d{=}3$",    FT, 3))):
        lam_max, t_d = _lam(d), _t_root(d)
        bat = {1: N-1} if d == 1 else _batches(N)

        def t_rs(S):
            return t_d + _fw(S)/_B

        def t_ag(S, bat=bat):
            return max(_t_root(sv)
                       + sum(n for dd, n in bat.items() if dd >= sv)*_fw(S//N)/_B
                       for sv in bat)

        for coll, tinc in (("reduce_scatter", t_rs), ("allgather", t_ag)):
            num = [(N-1)*lam_max + (N-1)/N*_fw(S)/_B for S in sizes]
            ax.plot(sizes, [n/tinc(S) for n, S in zip(num, sizes)],
                    color=C[coll], lw=1.7, label=f"{TITLE[coll]}, model")
            m = series(main, coll, topo=topo)
            if m:
                ax.plot([x[0] for x in m], [x[2]/x[1] for x in m], "o", ms=5,
                        color=C[coll], mfc="white", mew=1.4,
                        label=f"{TITLE[coll]}, measured")
        ax.axhline((N-1)*lam_max/t_d, color="#d62728", ls="--", lw=1.2,
                   label="latency bound")
        ax.axhline(1.0, color="#2ca02c", ls=":", lw=1.6, label="bandwidth bound")
        # the bound's VALUE, since the legend no longer carries its formula
        ax.text(0.97, 0.93, f"{(N-1)*lam_max/t_d:.0f}$\\times$",
                transform=ax.transAxes, ha="right", va="top",
                fontsize=9, color="#d62728")
        style(ax, ylabel=None)
        plain_log_y(ax, [1, 2, 5, 10, 20, 50, 100, 200])
        # The shard rule leaves nothing below 256 KiB, so the rooted figure's tick set
        # would spend half the axis on empty space. Retick over the range that has data.
        # three ticks over the 11.8 octaves that carry data, which is the same tick
        # density the rooted figure uses over its 16; four put "64 MiB" and "256 MiB"
        # close enough to touch at this width.
        ax.set_xticks([262144, 4194304, 67108864])
        ax.set_xticklabels(["256 KiB", "4 MiB", "64 MiB"], fontsize=9)
        ax.set_xlim(2**17.4, 2**29.2)
        ax.tick_params(axis="both", labelsize=9)
        ax.set_xlabel("message size", fontsize=10)
        ax.set_title(tag, fontsize=10)
    axes[0].set_ylabel(r"speed-up  (endpoint $/$ in-network)", fontsize=10)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, fontsize=9, ncol=3, loc="lower center",
               bbox_to_anchor=(0.5, -0.015), frameon=False)
    fig.tight_layout(rect=(0, 0.15, 1, 1))
    fig.savefig(f"{OUT}/{fname}")
    plt.close(fig)


def fig_ar_speedup(main, fname, N=64):
    """AllReduce speed-up over the endpoint ring, on both fabrics.

    Same shape as fig_rsag_regimes, and the sibling of that figure in the AllReduce
    section: two panels for the two depths, a model curve with its measured points, and
    the two asymptotes the formula decays between.

        speedup_ring(S) = (2(N-1) lam(d) + 2(N-1)/N T) / (delta(d) + T),   T = f_w(S)/B

    which leaves 2(N-1)lam(d)/delta(d) and lands on 2(N-1)/N -- the one collective in
    the chapter whose bandwidth bound is above 1, because the ring passes every byte
    over the cycle twice and the tree carries it up once and back down once.

    Recursive doubling was drawn measured-only until 2026-07-28, then removed entirely
    (user call: the chapter does not analyse it).

    The latency bound is 249x on BOTH fabrics, unlike Broadcast's 122/41 split: the ring
    here is paced by lam(d_max) in every one of its N-1 rounds, so numerator and
    denominator both scale with depth and the ratio does not. Broadcast's chain
    activates one step at a time and is charged the census sum, which cheap same-leaf
    steps dominate."""
    sizes = [2**(k/4) for k in range(70, 118)]      # from the smallest legal shard up
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.9), sharey=True)
    for ax, (tag, topo, d) in zip(axes, (
            (r"(a)  single switch, $d{=}1$", SS, 1),
            (r"(b)  three-tier, $d{=}3$",    FT, 3))):
        lam_d, t_d = _lam(d), _t_root(d)
        ax.plot(sizes,
                [(2*(N-1)*lam_d + 2*(N-1)/N*_fw(S)/_B) / (t_d + _fw(S)/_B)
                 for S in sizes],
                color="#1f77b4", lw=1.7, label="ring, model")
        m = series(main, "allreduce", topo=topo)
        if m:
            ax.plot([x[0] for x in m], [x[2]/x[1] for x in m], "o", ms=5,
                    color="#1f77b4", mfc="white", mew=1.4, label="ring, measured")
        ax.axhline(2*(N-1)*lam_d/t_d, color="#d62728", ls="--", lw=1.2,
                   label="latency bound")
        ax.axhline(2*(N-1)/N, color="#2ca02c", ls=":", lw=1.6, label="bandwidth bound")
        ax.text(0.97, 0.93, f"{2*(N-1)*lam_d/t_d:.0f}$\\times$",
                transform=ax.transAxes, ha="right", va="top",
                fontsize=9, color="#d62728")
        style(ax, ylabel=None)
        plain_log_y(ax, [1, 2, 5, 10, 20, 50, 100, 200])
        ax.set_xticks([262144, 4194304, 67108864])
        ax.set_xticklabels(["256 KiB", "4 MiB", "64 MiB"], fontsize=9)
        ax.set_xlim(2**17.4, 2**29.2)
        ax.tick_params(axis="both", labelsize=9)
        ax.set_xlabel("message size", fontsize=10)
        ax.set_title(tag, fontsize=10)
    axes[0].set_ylabel(r"speed-up  (endpoint $/$ in-network)", fontsize=10)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, fontsize=9, ncol=3, loc="lower center",
               bbox_to_anchor=(0.5, -0.012), frameon=False)
    fig.tight_layout(rect=(0, 0.13, 1, 1))
    fig.savefig(f"{OUT}/{fname}")
    plt.close(fig)


def fig_footprint(fname, colls, *, group_symbol="P", show_fixed_depth=True):
    """Network-footprint reduction: baseline byte-link-crossings / in-network ones.

    A CAPACITY result, orthogonal to the completion-time figures. The speed-up decays
    to ~1 once both arms are bandwidth-bound; this ratio does not, so at large messages
    the in-network path stops being faster while still moving half the bytes.

    Grouped bars per group size, following Khalilov et al. SC24 Fig. 2, which this
    reproduces. Their two bars are Ring and Recursive Doubling; ours are the
    collectives, since we implement no recursive-doubling variant of these four and RD
    is what makes the published figure climb with depth.

    Bars START AT ZERO. The ratio's own floor is 1.0 (no reduction at all), so a
    1.0-baselined axis would be defensible for lines, but a truncated bar chart
    misrepresents the encoded quantity, which for bars is length. A dotted line at the
    2x asymptote carries the comparison the eye needs instead.

    The analytic overlay was dropped 2026-07-27 (same measured-only rule as the header;
    the chapter argues the 2x convergence in prose). For the record, two closed forms
    hold exactly on the crossbar, where every pair is two hops:
        Bcast / AllGather / AllReduce :  2 - 2/P      (P crossings, 2P for AllReduce)
        Reduce / Reduce-Scatter      :  2(P-1)/(P+1)  (P+1 crossings)
    The split is the own-shard fold: Reduce's root and Reduce-Scatter's shard owners
    ship their own contribution rather than keeping it local, which adds one block;
    Broadcast's root never receives its own data, so it has nothing to keep. Measured
    sits 1.54% above both, uniformly -- the ring pays a 64 B acknowledgement per 4160 B
    frame and the multicast arm does not."""
    rows = [r for r in load(FOOT) if r["baseline_algo"] == "ring"]
    ps = [4, 8, 16, 32, 64]
    x = list(range(len(ps)))
    w = 0.8 / max(len(colls), 1)
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.4), sharey=True)
    if show_fixed_depth:
        panels = ((r"(a)  single switch, $d{=}1$", "single_switch"),
                  (r"(b)  three-tier, $d{=}3$", "fat3tier"))
    else:
        panels = ((r"(a)  single switch", "single_switch"),
                  (r"(b)  three-tier fat tree", "fat3tier"))
    for ax, (tag, cls) in zip(axes, panels):
        for k, coll in enumerate(colls):
            d = {int(r["group_size"]): float(r["ratio_bytes"]) for r in rows
                 if r["topology_class"] == cls and r["collective"] == coll}
            off = (k - (len(colls) - 1) / 2) * w
            ax.bar([xi + off for xi in x], [d.get(p, 0.0) for p in ps], w,
                   color=C[coll], edgecolor="white", linewidth=0.5, label=TITLE[coll])
        ax.axhline(2.0, color="black", ls=":", lw=0.9, alpha=0.45)
        ax.set_xticks(x); ax.set_xticklabels(ps, fontsize=9)
        ax.set_ylim(0, 2.25)
        ax.tick_params(axis="both", labelsize=9)
        ax.grid(True, axis="y", ls=":", alpha=0.6)
        ax.set_axisbelow(True)
        ax.set_xlabel(f"group size ${group_symbol}$", fontsize=10)
        ax.set_title(tag, fontsize=10)
    axes[0].set_ylabel("footprint reduction\n(endpoint $/$ in-network)", fontsize=10)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, fontsize=9, ncol=min(3, len(h)), loc="lower center",
               bbox_to_anchor=(0.5, -0.015), frameon=False)
    fig.tight_layout(rect=(0, 0.16, 1, 1))
    fig.savefig(f"{OUT}/{fname}")
    plt.close(fig)


def fig_regimes(main, fname, N=64):
    """Where the speed-up formula hands over from its latency bound to its bandwidth one.

        x = S/K,  K = max(N, floor(S/SEG))
        speedup(S) = [sum_i lambda_i(x) + (N+K-2) f_w(x)/B]
                     / [delta(d,S) + f_w(S)/B]

    The red line is the small-positive-payload latency limit. The green line is the exact
    ratio of the two serialization terms; once chunks are MSS-aligned it equals
    (N+K-2)/K. The hand-over is marked at the exact integer payload for which
    f_w(S)/B reaches delta(d,S)."""
    sizes = [2**k for k in range(12, 29)]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharey=True)
    for ax, (tag, topo, d, census) in zip(axes, (
            (r"(a)  single switch, $d{=}1$", SS, 1, {1: N-1}),
            (r"(b)  three-tier, $d{=}3$",    FT, 3, _census(N)))):
        model, bw = [], []
        for S in sizes:
            K = max(N, S // _SEG)
            assert S % K == 0, f"rooted-ring size {S} is not divisible by K={K}"
            chunk = S // K
            Lr = sum(count * _lam_chunk(depth, chunk)
                     for depth, count in census.items())
            ring = Lr + (N + K - 2) * _fw(chunk) / _B
            inc = _delta(d, S) + _fw(S) / _B
            model.append(ring / inc)
            bw.append((N + K - 2) * _fw(chunk) / _fw(S))
        ax.plot(sizes, model, color="#1f77b4", lw=1.9, label="model")
        m = series(main, "bcast", topo=topo)
        if m:
            ax.plot([x[0] for x in m], [x[2]/x[1] for x in m], "o", ms=5,
                    color="#1f77b4", mfc="white", mew=1.4, label="measured")
        header_ser = _H / _B
        Lr_small = sum(count * (2*(2*depth*_TL + (2*depth-1)*_TSW)
                                + (2*depth-1)*header_ser)
                       for depth, count in census.items())
        Li_small = 2*d*_TL + (2*d-1)*(_TSW + header_ser)
        latency_bound = Lr_small / Li_small
        ax.axhline(latency_bound, color="#d62728", ls="--", lw=1.2,
                   label=r"small-message limit")
        ax.plot(sizes, bw, color="#2ca02c", ls=":", lw=1.6,
                label="exact serialization ratio")
        Sk = _wire_knee(d)
        ax.axvline(Sk, color="black", lw=0.8, ls="-.", alpha=0.55)
        # Label the CONDITION that fixes this x-position, not a bare "T": the chapter
        # uses T with subscripts for completion TIMES, so "T = t_INC" reads as the
        # completion time equalling its own floor, which happens only at S = 0.
        ax.annotate(f"$f_w(S)/B = \\delta(d,S)$\n{Sk/1024:.0f} KiB",
                    xy=(Sk, 2.6), xytext=(Sk*1.45, 3.2), fontsize=7, color="black",
                    va="center")
        ax.text(0.97, 0.93, f"{latency_bound:.0f}$\\times$", transform=ax.transAxes,
                ha="right", va="top", fontsize=8, color="#d62728")
        style(ax, ylabel=None)
        plain_log_y(ax, [1, 2, 5, 10, 20, 50, 100, 200])
        ax.set_title(tag)
    axes[0].set_ylabel(r"speed-up  (endpoint $/$ in-network)")
    axes[0].legend(fontsize=7.5, loc="lower left")
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
    fig_regimes(main, "inc_bcast_regimes.pdf")
    fig_rsag_regimes(main, "inc_rsag_regimes.pdf")
    fig_ar_speedup(main, "inc_allreduce_speedup.pdf")
    fig_footprint("inc_footprint_bcast_reduce.pdf", ("bcast", "reduce"),
                  group_symbol="N", show_fixed_depth=False)
    fig_footprint("inc_footprint_rsag.pdf", ("reduce_scatter", "allgather"))
    fig_footprint("inc_footprint_allreduce.pdf", ("allreduce",))
    fig_speedup_two_fabrics(main, "bcast", "inc_bcast_speedup.pdf",
                            r"Broadcast, $|G|=64$")
    fig_time(main, "bcast", "inc_bcast_time.pdf")
    fig_time(main, "reduce", "inc_reduce_time.pdf")
    print(f"wrote figures to {OUT}")
