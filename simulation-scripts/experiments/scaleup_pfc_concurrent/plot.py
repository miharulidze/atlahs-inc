#!/usr/bin/env python3
"""Figures + validation table for the PFC backpressure suite (AA-plan-PFC-Validation).

Emits into the results dir:
  pfc_backpressure.pdf/.png   completion vs N, pinned vs distributed, 2 sizes,
                              with the ideal-isolation and full-serialisation
                              reference lines (the rigor-plan sanctioned bounds)
  pfc_sawtooth.pdf/.png       ingress-accountant occupancy vs time under the
                              in-situ AllGather engagement cell, against the
                              derived XOFF/XON thresholds and the reservation
  pfc_raster.pdf/.png         per-link PAUSE intervals (AllGather + rec.-doubling
                              traces), pause-time share annotated per link
  tab_pfc_validation.tex      census/stress/overdrive/controls summary table

Measured-only except the two sanctioned reference lines. Run in the container:
  docker run --rm -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
      /workspace/simulation-scripts/experiments/scaleup_pfc_concurrent/plot.py
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

EXP = "scaleup_pfc_concurrent"
OUTDIR = os.environ.get("SCALEUP_OUTPUT_DIR", paths.results_dir(EXP))
FRAME_B = 4160

# CVD-validated pair (dataviz six-checks: worst adjacent dE 20.9 protan).
ARM_STYLE = {
    "pinned":      ("#c0392b", "s", "pinned (one core)"),
    "distributed": ("#1f77b4", "o", "distributed (round-robin)"),
}


def _read(name):
    p = os.path.join(OUTDIR, name)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return list(csv.DictReader(f))


def _save(fig, stem):
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUTDIR, f"{stem}.{ext}"), bbox_inches="tight",
                    dpi=150 if ext == "png" else None)
    plt.close(fig)
    print(f"  wrote {stem}.pdf/.png")


# ---------------------------------------------------------------- stress figure
def fig_backpressure(rows):
    rows = [r for r in rows if r["mode"] == "stress"]
    if not rows:
        print("  (no stress rows; skipping pfc_backpressure)")
        return
    sizes = sorted({int(r["msg_bytes"]) for r in rows})
    fig, axes = plt.subplots(1, len(sizes), figsize=(7.4, 3.9))
    axes = [axes] if len(sizes) == 1 else list(axes)
    for ax, size in zip(axes, sizes):
        sub = [r for r in rows if int(r["msg_bytes"]) == size]
        for r in sub:  # rigor: never silently drop a failed configuration
            if r["status"] != "ok" or not r["makespan_ns"]:
                print(f"  WARN stress cell failed: N={r['n_groups']} {r['arm']} "
                      f"{r['msg_bytes']}B status={r['status']}")
        ns = sorted({int(r["n_groups"]) for r in sub})
        t1 = {a: next((float(r["makespan_ns"]) / 1000 for r in sub
                       if r["arm"] == a and int(r["n_groups"]) == 1
                       and r["makespan_ns"]), None)
              for a in ARM_STYLE}
        # Sanctioned reference bounds: ideal isolation (flat at the single-group
        # completion) and full serialisation (N x the single-group completion).
        if t1["distributed"]:
            ax.axhline(t1["distributed"], color="0.45", ls=":", lw=1.1)
            ax.annotate("ideal isolation", (ns[-1], t1["distributed"]),
                        textcoords="offset points", xytext=(0, 4),
                        ha="right", fontsize=8, color="0.35")
        if t1["pinned"]:
            bound = [n * t1["pinned"] for n in ns]
            ax.plot(ns, bound, color="0.45", ls="--", lw=1.1)
            ax.annotate("full serialisation", (ns[-2], bound[-2]),
                        textcoords="offset points", xytext=(4, 4),
                        ha="left", fontsize=8, color="0.35", rotation=30)
        for arm, (color, mk, label) in ARM_STYLE.items():
            pts = sorted((int(r["n_groups"]), float(r["makespan_ns"]) / 1000)
                         for r in sub if r["arm"] == arm and r["makespan_ns"])
            ax.plot([p[0] for p in pts], [p[1] for p in pts],
                    marker=mk, ms=4.5, lw=1.6, color=color, label=label)
        ax.set_xticks(ns)
        ax.set_xlabel("concurrent AllReduce groups $N$")
        ax.set_title(f"{size // 1024} KiB" if size < 1048576
                     else f"{size // 1048576} MiB", fontsize=10)
        ax.grid(axis="y", alpha=0.3)
        ax.set_ylim(bottom=0)
    axes[0].set_ylabel("slowest group completion [µs]")
    axes[0].legend(frameon=False, fontsize=9, loc="upper left")
    _save(fig, "pfc_backpressure")


# ---------------------------------------------------------------- trace parsing
def read_trace(name):
    """-> (pauses per queue: [(t_pause_us, t_resume_us)], iocc per queue:
    [(t_us, bytes)], t_end_us). Traces are committed gzipped; reads either."""
    import gzip
    p = os.path.join(OUTDIR, "traces", name)
    if os.path.exists(p):
        opener = lambda: open(p)
    elif os.path.exists(p + ".gz"):
        opener = lambda: gzip.open(p + ".gz", "rt")
    else:
        return None
    pauses, occ, open_pause, t_end = defaultdict(list), defaultdict(list), {}, 0.0
    with opener() as f:
        for row in csv.DictReader(f):
            t = int(row["t_ns"]) / 1000.0
            t_end = max(t_end, t)
            q, kind = row["queue"], row["kind"]
            if kind == "PAUSE":
                open_pause[q] = t
            elif kind == "RESUME":
                if q in open_pause:
                    pauses[q].append((open_pause.pop(q), t))
            elif kind == "IOCC":
                occ[q].append((t, int(row["bytes"])))
    for q, t0 in open_pause.items():   # pause never resumed: runs to the end
        pauses[q].append((t0, t_end))
    return pauses, occ, t_end


# ---------------------------------------------------------------- sawtooth
def fig_sawtooth(trace_name, hi_pkt, lo_pkt, headroom_frames=50):
    tr = read_trace(trace_name)
    if tr is None:
        print(f"  (no trace {trace_name}; skipping pfc_sawtooth)")
        return
    pauses, occ, _ = tr
    if not pauses:
        print("  (trace has no pauses; skipping pfc_sawtooth)")
        return
    q = max(pauses, key=lambda k: len(pauses[k]))     # most-paused link
    pts = occ.get(q, [])
    if not pts:
        print("  (no occupancy samples for the paused link)")
        return
    fig, ax = plt.subplots(figsize=(7, 4.0))
    ax.plot([p[0] for p in pts], [p[1] / 1024 for p in pts],
            lw=0.9, color="#1f77b4", label=f"ingress charge, {q}")
    for a, b in pauses[q]:
        ax.axvspan(a, b, color="#c0392b", alpha=0.14, lw=0)
    for pkt, ls, lbl in ((hi_pkt + headroom_frames, "-", "reservation (1 BDP)"),
                         (hi_pkt, "--", f"XOFF ({hi_pkt} pkt)"),
                         (lo_pkt, ":", f"XON ({lo_pkt} pkt)")):
        ax.axhline(pkt * FRAME_B / 1024, color="0.25", ls=ls, lw=1.1)
        ax.annotate(lbl, (1.0, pkt * FRAME_B / 1024),
                    xycoords=("axes fraction", "data"),
                    textcoords="offset points", xytext=(-4, 3),
                    ha="right", fontsize=8, color="0.25")
    ax.set_xlabel("time [µs]")
    ax.set_ylabel("ingress-accountant occupancy [KiB]")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    _save(fig, "pfc_sawtooth")


# ---------------------------------------------------------------- pause raster
def fig_raster(traces, top=14):
    panels = [(name, title, read_trace(name)) for name, title in traces]
    panels = [(n, t, tr) for n, t, tr in panels if tr is not None]
    if not panels:
        print("  (no traces; skipping pfc_raster)")
        return
    fig, axes = plt.subplots(1, len(panels), figsize=(7.4, 4.0))
    axes = [axes] if len(panels) == 1 else list(axes)
    for ax, (name, title, (pauses, _occ, t_end)) in zip(axes, panels):
        share = {q: sum(b - a for a, b in iv) / t_end for q, iv in pauses.items()}
        links = sorted(share, key=share.get, reverse=True)[:top]
        links.reverse()   # largest share on top
        for y, q in enumerate(links):
            ax.broken_barh([(a, b - a) for a, b in pauses[q]], (y - 0.32, 0.64),
                           facecolors="#c0392b", lw=0)
            ax.annotate(f"{share[q]*100:.1f}%", (t_end * 1.13, y), fontsize=7,
                        color="0.3", va="center", ha="right")
        ax.set_yticks(range(len(links)))
        ax.set_yticklabels(links, fontsize=7)
        ax.set_xlim(0, t_end * 1.15)
        ax.set_ylim(-0.7, len(links) - 0.3 if links else 0.7)
        ax.set_xlabel("time [µs]")
        ax.set_title(title, fontsize=10)
        ax.grid(axis="x", alpha=0.3)
        if not links:
            ax.annotate("no pauses", (0.5, 0.5), xycoords="axes fraction",
                        ha="center", fontsize=10, color="0.4")
    axes[0].set_ylabel("paused link (ingress accountant), pause-time share")
    fig.subplots_adjust(wspace=0.58)
    _save(fig, "pfc_raster")


# ---------------------------------------------------------------- table
def _agg(rows):
    """(cells, engaged, max pauses, max peakI, max peakE, sum hits) over rows."""
    fl = lambda k, r: float(r[k]) if r.get(k) not in ("", None) else 0.0
    return (len(rows),
            sum(1 for r in rows if fl("pauses_sent", r) > 0),
            int(max((fl("pauses_sent", r) for r in rows), default=0)),
            max((fl("peak_ingress_frac", r) for r in rows), default=0.0),
            max((fl("peak_egress_frac", r) for r in rows), default=0.0),
            sum(int(r["drop_log_hits"]) for r in rows))


def gen_table(census, stress, overdrive, controls):
    xbar = [r for r in census if "single_switch" in r["su_topo"]]
    tier = [r for r in census if "3tier" in r["su_topo"]
            and int(r["msg_bytes"]) < 268435456]
    spot = [r for r in census if int(r["msg_bytes"]) == 268435456]
    groups = [("census, crossbar, 64\\,MiB", xbar),
              ("census, three-tier, 64\\,MiB", tier),
              ("census, three-tier, 256\\,MiB", spot),
              ("stress, pinned + distributed",
               [r for r in stress if r["mode"] == "stress"]),
              ("overdrive, NIC $2\\times$", overdrive)]
    ctl_names = {"control_thresh": "control: XOFF above queue",
                 "control_cap":    "control: egress cap $1\\times$ BDP",
                 "control_nogate": "control: NIC gate off, NIC $2\\times$",
                 "control_gate":   "control: XOFF $=100$ (gate probe)"}
    lines = [
        "% generated by experiments/scaleup_pfc_concurrent/plot.py -- do not edit",
        "\\begin{tabular}{l r r r r r r}",
        "\\hline",
        "configuration & cells & paused & max pauses & peak$_{\\mathrm{in}}$ & "
        "peak$_{\\mathrm{eg}}$ & warn lines\\\\",
        "\\hline",
    ]
    for name, rows in groups:
        if not rows:
            continue
        c, e, mp, pi, pe, h = _agg(rows)
        lines.append(f"{name} & {c} & {e} & {mp} & {pi:.2f} & {pe:.2f} & {h}\\\\")
    lines.append("\\hline")
    for r in controls:
        c, e, mp, pi, pe, h = _agg([r])
        name = ctl_names.get(r["mode"], r["mode"])
        lines.append(f"{name} & 1 & {e} & {mp} & {pi:.2f} & {pe:.2f} & {h}\\\\")
    lines += ["\\hline", "\\end{tabular}"]
    out = os.path.join(OUTDIR, "tab_pfc_validation.tex")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("  wrote tab_pfc_validation.tex")


def main():
    stress = _read(f"{EXP}.csv")
    census = _read("pfc_census.csv")
    controls = _read("pfc_controls.csv")
    overdrive = _read("pfc_overdrive.csv")
    print(f"rows: stress={len(stress)} census={len(census)} "
          f"controls={len(controls)} overdrive={len(overdrive)}")
    fig_backpressure(stress)
    # Derived 3-tier thresholds (pfc_config): XOFF 389, XON 311, +50-frame headroom.
    fig_sawtooth("census_3tier_allgather_inc_67108864.csv", 389, 311)
    fig_raster([("census_3tier_allgather_inc_67108864.csv",
                 "AllGather, in-network (64 MiB)"),
                ("census_3tier_rdouble_base_67108864.csv",
                 "AllReduce rec.-doubling, endpoint (64 MiB)")])
    gen_table(census, stress, overdrive, controls)


if __name__ == "__main__":
    main()
