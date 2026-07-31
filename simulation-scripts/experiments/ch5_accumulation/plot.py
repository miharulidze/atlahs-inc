#!/usr/bin/env python3
"""Build Chapter 5 assets from one immutable headline run.

The script reads completed run directories and creates a new output directory.
It never writes to the historical ``intranode_linkspeed_sweep`` experiment and
refuses to replace existing assets.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


TPS = (4, 8, 16)
WORKLOAD = "corrected_mb1_ga32"

# Single-switch collective model — constants and forms mirror
# simulation-scripts/_gen_rsag_tables.py (wire/lam/fill/ring/inc_root);
# model_checks/verify_models.py cross-checks the factors against that module.
T_L, T_SW, B_LINK, HDR, MSS = 50.0, 300.0, 500.0, 64, 4096
FRAME = MSS + HDR
TP_AR_BYTES = 32 * 1024 * 1024  # logical TP AllReduce payload (32 MiB)


def _wire(x: int) -> float:
    return x + HDR * math.ceil(x / MSS)


def _lam(d: int) -> float:
    return 2 * (2 * d * T_L + (2 * d - 1) * T_SW) + (2 * d - 1) * FRAME / B_LINK


def _fill(d: int, w: float) -> float:
    return 2 * d * T_L + (2 * d - 1) * T_SW + 2 * d * w / B_LINK


def _ring(S: int, N: int, d: int) -> float:
    return (N - 1) * (_lam(d) + _wire(S // N) / B_LINK)


def _inc_root(S: int, d: int) -> float:
    w = min(S, MSS) + HDR
    return _fill(d, w) + (_wire(S) - w) / B_LINK


def _model_tp_factor(n: int) -> float:
    """T_ring^AR / T_inc^AR (eq:ar-speedup) at S = 32 MiB on the single switch (d=1)."""
    return 2 * _ring(TP_AR_BYTES, n, 1) / _inc_root(TP_AR_BYTES, 1)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(
    run_dir: Path,
    workload: str,
    expected_tps: tuple[int, ...],
) -> dict:
    if not (run_dir / ".complete").is_file():
        raise SystemExit(f"run is not complete: {run_dir}")
    csv_path = run_dir / "tables" / "results.csv"
    if not csv_path.is_file():
        raise SystemExit(f"result table is missing: {csv_path}")
    rows = [
        row for row in csv.DictReader(csv_path.open(encoding="utf-8"))
        if row["workload"] == workload
        and int(row["su_gbps"]) == 4000
        and int(row["so_gbps"]) == 400
    ]
    cells: dict[int, dict[str, dict]] = {}
    for row in rows:
        tp = int(row["tp"])
        if tp not in expected_tps:
            continue
        if row["status"] != "ok" or any(int(row[key]) for key in ("drops", "rtx", "rts")):
            raise SystemExit(f"invalid simulator outcome for TP{tp}/{row['arm']}")
        if workload == WORKLOAD:
            expected = {
                "microbatch": "1",
                "accumulation": "32",
                "global_batch": "128",
                "tp_allreduces_per_iter": "256",
                "tp_allreduce_bytes": str(32 * 1024 * 1024),
                "tp_declared_tensor_bytes_per_iter": str(8 * 1024**3),
            }
            for key, value in expected.items():
                if row[key] != value:
                    raise SystemExit(
                        f"unexpected {key} for TP{tp}/{row['arm']}: {row[key]}"
                    )
        cells.setdefault(tp, {})[row["arm"]] = row
    expected_cells = {(tp, arm) for tp in expected_tps for arm in ("baseline", "inc")}
    actual_cells = {(tp, arm) for tp, arms in cells.items() for arm in arms}
    if actual_cells != expected_cells:
        raise SystemExit(
            f"expected cells {sorted(expected_cells)}, found {sorted(actual_cells)}"
        )
    iteration_counts = {
        int(row["iters"])
        for arms in cells.values()
        for row in arms.values()
    }
    if len(iteration_counts) != 1:
        raise SystemExit(f"mixed iteration counts in {run_dir}: {iteration_counts}")
    return {
        "run_dir": run_dir,
        "csv_path": csv_path,
        "cells": cells,
        "iters": iteration_counts.pop(),
    }


def _metrics(loaded: dict) -> list[dict]:
    result = []
    for tp, arms in sorted(loaded["cells"].items()):
        base = arms["baseline"]
        inc = arms["inc"]
        base_ms = float(base["time_per_iter_s"]) * 1000
        inc_ms = float(inc["time_per_iter_s"]) * 1000
        compute_ms = float(base["compute_ns_per_iter_mean"]) / 1e6
        if int(base["compute_ns_per_iter_mean"]) != int(inc["compute_ns_per_iter_mean"]):
            raise SystemExit(f"paired compute mismatch for TP{tp}")
        result.append({
            "tp": tp,
            "iters": int(base["iters"]),
            "gpus": tp * int(base["dp"]) * int(base["pp"]),
            "baseline_ms": base_ms,
            "inc_ms": inc_ms,
            "compute_ms": compute_ms,
            "baseline_exposed_ms": base_ms - compute_ms,
            "inc_exposed_ms": inc_ms - compute_ms,
            "speedup": base_ms / inc_ms,
            "reduction_pct": 100 * (base_ms - inc_ms) / base_ms,
        })
    return result


def _plot(rows: list[dict], output_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.6, 4.9))
    compute_color = "#bdbdbd"
    communication_color = "#6baed6"
    bar_width = 0.72
    pair_offset = 0.43
    centers = [i * 2.35 for i in range(len(rows))]
    xticks: list[float] = []
    xlabels: list[str] = []

    for i, (row, center) in enumerate(zip(rows, centers)):
        pair = (
            ("baseline", row["baseline_ms"], center - pair_offset),
            ("in-network", row["inc_ms"], center + pair_offset),
        )
        for arm, total_ms, x in pair:
            compute_ms = row["compute_ms"]
            exposed_ms = total_ms - compute_ms
            compute_pct = 100 * compute_ms / total_ms
            exposed_pct = 100 - compute_pct
            ax.bar(
                x, compute_ms, width=bar_width, color=compute_color,
                edgecolor="white", linewidth=0.6,
                label="modeled compute" if i == 0 and arm == "baseline" else None,
            )
            ax.bar(
                x, exposed_ms, bottom=compute_ms, width=bar_width,
                color=communication_color, edgecolor="white", linewidth=0.6,
                label=r"exposed residual $M-C$" if i == 0 and arm == "baseline" else None,
            )
            ax.text(x, compute_ms / 2, f"{compute_pct:.1f}%",
                    ha="center", va="center", fontsize=10)
            ax.text(x, compute_ms + exposed_ms / 2, f"{exposed_pct:.1f}%",
                    ha="center", va="center", fontsize=10, color="#102a43")
            ax.text(x, total_ms + 1.1, f"{total_ms:.1f} ms",
                    ha="center", va="bottom", fontsize=9.2)
            xticks.append(x)
            xlabels.append(f"TP{row['tp']}\n{arm}")

        bracket_y = max(row["baseline_ms"], row["inc_ms"]) + 8.0
        left, right = center - pair_offset, center + pair_offset
        ax.plot(
            [left, left, right, right],
            [bracket_y - 1.2, bracket_y, bracket_y, bracket_y - 1.2],
            color="#333333", lw=1.0, clip_on=False,
        )
        ax.text(center, bracket_y + 1.0, f"{row['speedup']:.3f}×",
                ha="center", va="bottom", fontsize=12, fontweight="bold")

    ax.set_xticks(xticks)
    ax.set_xticklabels(xlabels, fontsize=9.5)
    ax.set_ylabel("Iteration time (ms)", fontsize=12)
    ax.set_title("End-to-end outcome at 4000/400 Gb/s", fontsize=13)
    ax.set_ylim(0, max(row["baseline_ms"] for row in rows) * 1.20)
    ax.set_xlim(centers[0] - 1.05, centers[-1] + 1.05)
    ax.grid(True, axis="y", ls=":", alpha=0.45)
    ax.set_axisbelow(True)
    ax.legend(fontsize=9.5, loc="upper right", frameon=False)
    fig.tight_layout()
    fig.savefig(output_dir / "case_study_result_summary.pdf")
    fig.savefig(output_dir / "case_study_result_summary.png", dpi=180)
    plt.close(fig)


def _collectives_table(run_dir: Path) -> str:
    """Build tab_case_study_collectives from the run's audited emission record.

    Counts, AllReduce size, ZeRO-1 payload sums and the total come from
    audits.json / manifest.json; only the per-call ZeRO-1 size RANGE strings
    are workload constants (per-call shard sizes are not in the audit record).
    """
    audits = json.loads((run_dir / "audits.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    dp = int(manifest["configuration"]["dp"])
    by_tp = {int(a["tp"]): a for a in audits}
    if sorted(by_tp) != sorted(TPS):
        raise SystemExit(f"audits.json TPs {sorted(by_tp)} != expected {sorted(TPS)}")

    def _uniq(vals, what):
        s = set(vals)
        if len(s) != 1:
            raise SystemExit(f"audits.json: non-uniform {what}: {s}")
        return s.pop()

    n_ar = _uniq((a["actual_tp_allreduces"] for a in by_tp.values()), "AR count")
    s_ar = _uniq((s for a in by_tp.values() for s in a["actual_tp_allreduce_sizes"]),
                 "AR size")
    n_z = _uniq([a["actual_zero1_reduce_scatters"] for a in by_tp.values()]
                + [a["actual_zero1_allgathers"] for a in by_tp.values()], "zero1 count")
    mib = 1024 * 1024
    fmt = lambda v: f"{v:g}"  # noqa: E731
    z_sums = [by_tp[tp]["emitted_zero1_baseline"]["physical_declared_bytes_each_per_iter"] / mib
              for tp in TPS]
    ar_call = fmt(s_ar / mib)
    ar_sum = fmt(n_ar * s_ar / mib)
    z_sum_str = " / ".join(fmt(z) for z in z_sums)
    total_str = " / ".join(fmt(n_ar * s_ar / mib + 2 * z) for z in z_sums)
    # Per-call ZeRO-1 ranges: workload constants (uneven physical-TP shard sizes).
    z_range = "8--24 / 4--12 / 2--6"
    return (
        "\\begin{tabular}{l r r c c}\n"
        "  \\toprule\n"
        "  operation & \\shortstack{count per\\\\rank/iteration} & $|G|$\n"
        "  & \\shortstack{payload per call\\\\TP4/TP8/TP16 [MiB]}\n"
        "  & \\shortstack{payload sum\\\\TP4/TP8/TP16 [MiB]} \\\\\n"
        "  \\midrule\n"
        f"  TP AllReduce         & {n_ar} & $N$ & {ar_call} / {ar_call} / {ar_call}"
        f" & {ar_sum} / {ar_sum} / {ar_sum} \\\\\n"
        f"  ZeRO-1 ReduceScatter &  {n_z} & {dp}   & {z_range} & {z_sum_str} \\\\\n"
        f"  ZeRO-1 AllGather     &  {n_z} & {dp}   & {z_range} & {z_sum_str} \\\\\n"
        "  \\midrule\n"
        f"  total                & {n_ar + 2 * n_z} & --- & --- & {total_str} \\\\\n"
        "  \\bottomrule\n"
        "\\end{tabular}\n"
    )


def _write_tables(rows: list[dict], run_dir: Path, output_dir: Path) -> None:
    communication = [
        r"\begin{tabular}{r r r r r}",
        r"  \toprule",
        r"  & \shortstack{simulator-model\\TP factor}",
        r"  & \multicolumn{2}{c}{exposed time $E=M-C$ [ms]}",
        r"  & \shortstack{exposed-time\\speedup} \\",
        r"  \cmidrule(lr){3-4}",
        r"  $N$ & $T_{\mathrm{ring}}^{\mathrm{AR}}/T_{\mathrm{inc}}^{\mathrm{AR}}$",
        r"      & baseline & in-network & $E_{\mathrm{base}}/E_{\mathrm{in}}$ \\",
        r"  \midrule",
    ]
    for row in rows:
        factor = _model_tp_factor(row["tp"])
        exposed_speedup = row["baseline_exposed_ms"] / row["inc_exposed_ms"]
        communication.append(
            f"  {row['tp']:2d} & {factor:.3f} & {row['baseline_exposed_ms']:.1f} & "
            f"{row['inc_exposed_ms']:.1f} & {exposed_speedup:.3f} \\\\"
        )
    communication.extend([r"  \bottomrule", r"\end{tabular}", ""])
    (output_dir / "tab_case_study_communication.tex").write_text(
        "\n".join(communication), encoding="utf-8"
    )

    (output_dir / "tab_case_study_collectives.tex").write_text(
        _collectives_table(run_dir), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headline-run", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=False)
    headline = _load(args.headline_run.resolve(), WORKLOAD, TPS)
    if headline["iters"] != 1:
        raise SystemExit(f"headline must contain one isolated iteration, found {headline['iters']}")
    headline_rows = _metrics(headline)
    _plot(headline_rows, args.output_dir)
    _write_tables(headline_rows, headline["run_dir"], args.output_dir)

    summary = {
        "headline": headline_rows,
        "sources": {
            "plot_script": str(Path(__file__).resolve()),
            "plot_script_sha256": _sha256(Path(__file__).resolve()),
            "headline_results_csv": str(headline["csv_path"]),
            "headline_results_sha256": _sha256(headline["csv_path"]),
        },
    }
    (args.output_dir / "case_study_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
