#!/usr/bin/env python3
"""Reject duplicate logical experiments in the tracked publication CSVs.

The runners append by design, so an interrupted or repeated direct invocation can leave
several generations in one file.  Publication wrappers stage into a fresh directory,
but this check protects the curated reference files themselves.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "simulation-scripts" / "results"

# A logical key contains every input dimension that intentionally distinguishes rows;
# output metrics, transient paths, and the rendered command are deliberately excluded.
DATASETS = {
    "collective A/B": (
        RESULTS / "scaleup_coll_ab" / "scaleup_coll_ab.csv",
        ("collective", "baseline_algo", "group_size", "msg_bytes",
         "reduce_compute_ns", "su_topo", "so_topo", "intranode_linkspeed_mbps"),
    ),
    "collective group sweep": (
        RESULTS / "scaleup_coll_ab_groupsweep" / "scaleup_coll_ab.csv",
        ("collective", "baseline_algo", "group_size", "msg_bytes",
         "reduce_compute_ns", "su_topo", "so_topo", "intranode_linkspeed_mbps"),
    ),
    "network footprint": (
        RESULTS / "scaleup_coll_footprint" / "scaleup_coll_footprint.csv",
        ("topology_class", "su_topo", "collective", "baseline_algo", "group_size",
         "msg_bytes", "size_mult", "so_topo", "nodes", "gpus_per_node", "mtu",
         "intranode_linkspeed_mbps"),
    ),
    "supplementary AR bandwidth": (
        RESULTS / "scaleup_ar_bandwidth" / "scaleup_ar_bandwidth.csv",
        ("arm", "inc_kind", "group_size", "msg_bytes", "reduce_compute_ns",
         "su_topo", "so_topo", "intranode_linkspeed_mbps"),
    ),
    "PFC stress": (
        RESULTS / "scaleup_pfc_concurrent" / "scaleup_pfc_concurrent.csv",
        ("mode", "arm", "mcast_pin", "n_groups", "group_size", "pods_spanned",
         "hosts_per_pod", "collective", "baseline_algo", "msg_bytes", "pfc_high",
         "pfc_low", "intranode_q", "su_topo", "so_topo", "nodes",
         "gpus_per_node", "intranode_linkspeed_mbps"),
    ),
    "PFC census": (
        RESULTS / "scaleup_pfc_concurrent" / "pfc_census.csv",
        ("mode", "arm", "mcast_pin", "n_groups", "group_size", "pods_spanned",
         "hosts_per_pod", "collective", "baseline_algo", "msg_bytes", "pfc_high",
         "pfc_low", "intranode_q", "su_topo", "so_topo", "nodes",
         "gpus_per_node", "intranode_linkspeed_mbps"),
    ),
    "PFC controls": (
        RESULTS / "scaleup_pfc_concurrent" / "pfc_controls.csv",
        ("mode", "arm", "mcast_pin", "n_groups", "group_size", "pods_spanned",
         "hosts_per_pod", "collective", "baseline_algo", "msg_bytes", "pfc_high",
         "pfc_low", "intranode_q", "su_topo", "so_topo", "nodes",
         "gpus_per_node", "intranode_linkspeed_mbps"),
    ),
    "PFC overdrive": (
        RESULTS / "scaleup_pfc_concurrent" / "pfc_overdrive.csv",
        ("mode", "arm", "mcast_pin", "n_groups", "group_size", "pods_spanned",
         "hosts_per_pod", "collective", "baseline_algo", "msg_bytes", "pfc_high",
         "pfc_low", "intranode_q", "su_topo", "so_topo", "nodes",
         "gpus_per_node", "intranode_linkspeed_mbps"),
    ),
    "Chapter 5 canonical run": (
        RESULTS / "ch5_accumulation" / "runs"
        / "stable-order-ga32-20260808"
        / "tables" / "results.csv",
        ("run_id", "workload", "tp", "dp", "pp", "arm", "microbatch",
         "accumulation", "layers", "iters", "su_gbps", "so_gbps",
         "compute_model", "seed"),
    ),
}


def audit(name: str, path: Path, fields: tuple[str, ...]) -> list[str]:
    if not path.is_file():
        return [f"{name}: missing {path.relative_to(ROOT)}"]
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in fields if field not in (reader.fieldnames or ())]
        if missing:
            return [f"{name}: missing key columns {', '.join(missing)}"]
        rows = list(reader)

    counts = Counter(tuple(row[field] for field in fields) for row in rows)
    repeated = [(key, count) for key, count in counts.items() if count > 1]
    if repeated:
        sample = "; ".join(f"{key!r} x{count}" for key, count in repeated[:3])
        return [f"{name}: {len(repeated)} duplicate logical key(s); {sample}"]

    print(f"  [PASS] {name}: {len(rows)} rows, {len(counts)} unique logical keys")
    return []


def main() -> int:
    print("Reference-data uniqueness")
    errors: list[str] = []
    for name, (path, fields) in DATASETS.items():
        errors.extend(audit(name, path, fields))
    for error in errors:
        print(f"  [FAIL] {error}")
    if errors:
        print(f"\nFAIL: {len(errors)} reference-data problem(s)")
        return 1
    print("\nPASS: tracked publication CSVs contain one row per logical experiment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
