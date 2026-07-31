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
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


TPS = (4, 8, 16)
WORKLOAD = "corrected_mb1_ga32"


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


def _write_tables(rows: list[dict], output_dir: Path) -> None:
    communication = [
        r"\begin{tabular}{r r r r r}",
        r"  \toprule",
        r"  & \shortstack{ideal TP\\factor}",
        r"  & \multicolumn{2}{c}{exposed time $E=M-C$ [ms]}",
        r"  & \shortstack{exposed-time\\speedup} \\",
        r"  \cmidrule(lr){3-4}",
        r"  $N$ & $2(N-1)/N$ & baseline & in-network & $E_{\mathrm{base}}/E_{\mathrm{in}}$ \\",
        r"  \midrule",
    ]
    for row in rows:
        ideal = 2 * (row["tp"] - 1) / row["tp"]
        exposed_speedup = row["baseline_exposed_ms"] / row["inc_exposed_ms"]
        communication.append(
            f"  {row['tp']:2d} & {ideal:.3f} & {row['baseline_exposed_ms']:.1f} & "
            f"{row['inc_exposed_ms']:.1f} & {exposed_speedup:.3f} \\\\"
        )
    communication.extend([r"  \bottomrule", r"\end{tabular}", ""])
    (output_dir / "tab_case_study_communication.tex").write_text(
        "\n".join(communication), encoding="utf-8"
    )

    collectives = r"""\begin{tabular}{l r r c c}
  \toprule
  operation & \shortstack{count per\\rank/iteration} & $|G|$
  & \shortstack{payload per call\\TP4/TP8/TP16 [MiB]}
  & \shortstack{payload sum\\TP4/TP8/TP16 [MiB]} \\
  \midrule
  TP AllReduce         & 256 & $N$ & 32 / 32 / 32 & 8192 / 8192 / 8192 \\
  ZeRO-1 ReduceScatter &  10 & 4   & 8--24 / 4--12 / 2--6 & 193 / 96.5 / 48.25 \\
  ZeRO-1 AllGather     &  10 & 4   & 8--24 / 4--12 / 2--6 & 193 / 96.5 / 48.25 \\
  \midrule
  total                & 276 & --- & --- & 8578 / 8385 / 8288.5 \\
  \bottomrule
\end{tabular}
"""
    (output_dir / "tab_case_study_collectives.tex").write_text(
        collectives, encoding="utf-8"
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
    _write_tables(headline_rows, args.output_dir)

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
