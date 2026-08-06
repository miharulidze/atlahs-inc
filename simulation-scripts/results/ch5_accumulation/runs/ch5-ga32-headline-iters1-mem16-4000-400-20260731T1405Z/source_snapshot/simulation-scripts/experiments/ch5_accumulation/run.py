#!/usr/bin/env python3
"""Isolated Chapter 5 accumulation correction (Ring baseline vs INC).

This experiment deliberately does not write to ``intranode_linkspeed_sweep``.
Every invocation exclusively reserves a new run directory and retains its
manifest, source snapshot, generated traces, raw simulator logs, and table.

The corrected workload is microbatch=1 with an explicit PP=1 1F1B schedule;
``--accumulations 32`` therefore executes F0,B0,...,F31,B31 before one ZeRO-1
optimizer step.  The generator is pinned at runtime to Ring AllReduce and
physical TP-local ZeRO-1 payloads regardless of the caller's environment.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import pathlib
import pickle
import re
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

SS_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SS_ROOT))

from common import goal, paths, sim  # noqa: E402
from experiments.intranode_linkspeed_sweep import run as network  # noqa: E402

sys.path.insert(0, paths.GENERATOR_DIR)
from simple_sim.extract import topo_sort  # noqa: E402
from simple_sim.ops_comm import AllGatherOp, AllReduceOp, ReduceScatterOp  # noqa: E402


EXP_NAME = "ch5_accumulation"
CORRECTED_MICROBATCH = 1
DP = 4
PP = 1
SCHEDULE = "1f1b"
COMPUTE_MODEL = "h100_te"
SEED = 42
DTYPE_BYTES = 2
MODEL = {
    "hidden": 4096,
    "ffn": 11008,
    "heads": 32,
    "kv_heads": 32,
    "seq_len": 4096,
}

CSV_FIELDS = [
    "run_id", "workload", "tp", "dp", "pp", "total_gpus", "arm",
    "microbatch", "accumulation", "global_batch", "schedule",
    "layers", "iters", "hidden", "ffn", "heads", "kv_heads", "seq_len",
    "tp_allreduces_per_iter", "tp_allreduce_bytes",
    "tp_declared_tensor_bytes_per_iter", "algorithm", "zero1_bytes",
    "su_gbps", "so_gbps", "su_link_latency_ns", "su_switch_latency_ns",
    "so_link_latency_ns", "so_switch_latency_ns", "so_queue_packets",
    "so_ecn_low_bytes", "so_ecn_high_bytes", "min_rto_us",
    "pfc_high", "pfc_low", "intranode_q", "intranode_cc",
    "su_topo", "su_topo_sha256", "so_topo", "so_topo_sha256", "compute_model",
    "compute_ns_per_iter", "compute_ns_per_iter_mean", "makespan_ns",
    "time_per_iter_s", "drops", "rtx", "rts", "status", "elapsed_s",
    "generator_revision", "engine", "seed", "counter_found", "raw_log", "command",
]


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_run_id(accumulations: list[int]) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ga = "-".join(str(value) for value in accumulations)
    return f"{stamp}_mb1_ga{ga}_ring_1f1b"


def _write_text_exclusive(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json_exclusive(path: pathlib.Path, value) -> None:
    _write_text_exclusive(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _sha256(path: pathlib.Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_state(repo: pathlib.Path) -> dict:
    def run(*args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True,
            timeout=30, check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else "unavailable"

    status = run("status", "--short")
    return {
        "path": str(repo),
        "revision": run("rev-parse", "HEAD"),
        "status": status,
        "dirty": bool(status and status != "unavailable"),
    }


def _snapshot_sources(run_dir: pathlib.Path) -> list[dict]:
    workspace = pathlib.Path(paths.WORKSPACE)
    relative_files = [
        pathlib.Path(__file__).resolve().relative_to(workspace),
        pathlib.Path("simulation-scripts/experiments/intranode_linkspeed_sweep/run.py"),
        pathlib.Path("simulation-scripts/common/sim.py"),
        pathlib.Path("simulation-scripts/common/goal.py"),
        pathlib.Path("simulation-scripts/common/paths.py"),
        pathlib.Path("goal_gen/ai/nccl_generator_v2/simple_sim/llama3_training.py"),
        pathlib.Path("goal_gen/ai/nccl_generator_v2/simple_sim/pp_stage_builder.py"),
        pathlib.Path("goal_gen/ai/nccl_generator_v2/simple_sim/pp_schedule.py"),
        pathlib.Path("goal_gen/ai/nccl_generator_v2/simple_sim2goal.py"),
        pathlib.Path("goal_gen/ai/nccl_generator_v2/communication.py"),
    ]
    records = []
    for relative in relative_files:
        source = workspace / relative
        if not source.is_file():
            continue
        destination = run_dir / "source_snapshot" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        records.append({"path": str(relative), "sha256": _sha256(destination)})
    return records


def _generator_revision() -> str:
    return _git_state(pathlib.Path(paths.GENERATOR_DIR))["revision"]


def _generator_env(*, emit_inc: bool, skeleton: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "COMPUTE_MODEL": COMPUTE_MODEL,
        "INC_CONTEXTS": "tp",
        "EMIT_INC": "1" if emit_inc else "0",
        "SIM_AR_ALGO": "ring",
        "ZERO1_LOGICAL_BYTES": "0",
        "SKELETON_CONTEXTS": "tp" if skeleton else "",
    })
    return env


def _run_logged(command: list[str], *, cwd: pathlib.Path, env: dict[str, str],
                log_path: pathlib.Path) -> None:
    proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    body = (
        f"command: {shlex.join(command)}\n"
        f"cwd: {cwd}\n"
        f"returncode: {proc.returncode}\n\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    _write_text_exclusive(log_path, body)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed; see {log_path}")


def _audit_graph(graph_path: pathlib.Path, *, microbatch: int, accumulation: int,
                 layers: int, iters: int) -> dict:
    with graph_path.open("rb") as handle:
        graph = pickle.load(handle)
    nodes = topo_sort(graph.nodes)
    tp_ops = [
        node for node in nodes
        if isinstance(node, AllReduceOp) and node.context == "tp"
    ]
    zero1_rs = [
        node for node in nodes
        if isinstance(node, ReduceScatterOp) and node.context == "zero1"
    ]
    zero1_ag = [
        node for node in nodes
        if isinstance(node, AllGatherOp) and node.context == "zero1"
    ]

    expected_tp_count = 4 * layers * accumulation * iters
    expected_tp_bytes = microbatch * MODEL["seq_len"] * MODEL["hidden"] * DTYPE_BYTES
    expected_zero1_count = 5 * layers * iters
    actual_sizes = sorted({node.bytes for node in tp_ops})
    checks = {
        "tp_allreduce_count": len(tp_ops) == expected_tp_count,
        "tp_allreduce_size": actual_sizes == [expected_tp_bytes],
        "zero1_reduce_scatter_once_per_iteration": len(zero1_rs) == expected_zero1_count,
        "zero1_allgather_once_per_iteration": len(zero1_ag) == expected_zero1_count,
    }
    audit = {
        "expected_tp_allreduces": expected_tp_count,
        "actual_tp_allreduces": len(tp_ops),
        "expected_tp_allreduce_bytes": expected_tp_bytes,
        "actual_tp_allreduce_sizes": actual_sizes,
        "expected_zero1_collectives_each": expected_zero1_count,
        "actual_zero1_reduce_scatters": len(zero1_rs),
        "actual_zero1_allgathers": len(zero1_ag),
        "checks": checks,
        "pass": all(checks.values()),
    }
    if not audit["pass"]:
        raise RuntimeError(f"generated graph failed workload audit: {audit}")
    return audit


def _parameter_elements_per_layer() -> int:
    hidden = MODEL["hidden"]
    intermediate = MODEL["ffn"]
    head_dim = hidden // MODEL["heads"]
    qkv = hidden * (MODEL["heads"] + 2 * MODEL["kv_heads"]) * head_dim
    output = MODEL["heads"] * head_dim * hidden
    mlp = 3 * hidden * intermediate
    return qkv + output + mlp


def _audit_goal_zero1(goal_path: pathlib.Path, *, tp: int, layers: int,
                      iters: int) -> dict:
    """Assert physical TP-local ZeRO bytes after GOAL translation.

    Context 2 is ZeRO-1.  With Ring RS and AG over DP=4, every collective emits
    DP-1 sends per rank and the combined wire bytes are
    ``2 * (DP-1)/DP`` times the physical parameter bytes.
    """
    text = goal_path.read_text(encoding="utf-8")
    match = re.search(r"rank 0 \{\n(.*?)\n\}", text, flags=re.DOTALL)
    if match is None:
        raise RuntimeError(f"cannot find rank 0 block in {goal_path}")
    sizes = [
        int(value) for value in re.findall(
            r"send (\d+)b .*? tag [0-9]+002 ", match.group(1),
        )
    ]
    physical_bytes_per_iter = (
        _parameter_elements_per_layer() * layers * DTYPE_BYTES // tp
    )
    expected_sends = 5 * layers * 2 * (DP - 1) * iters
    expected_wire_bytes = (
        physical_bytes_per_iter * 2 * (DP - 1) // DP * iters
    )
    checks = {
        "send_count": len(sizes) == expected_sends,
        "wire_bytes": sum(sizes) == expected_wire_bytes,
    }
    return {
        "physical_declared_bytes_each_per_iter": physical_bytes_per_iter,
        "expected_ring_send_count": expected_sends,
        "actual_ring_send_count": len(sizes),
        "expected_ring_wire_bytes": expected_wire_bytes,
        "actual_ring_wire_bytes": sum(sizes),
        "checks": checks,
        "pass": all(checks.values()),
    }


def _localize_groups(groups: pathlib.Path, destination: pathlib.Path, *, tp: int,
                     expected_groups: int) -> None:
    global_groups = [
        [int(value) for value in line.split()]
        for line in groups.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(global_groups) != expected_groups or {len(group) for group in global_groups} != {tp}:
        raise RuntimeError(
            f"expected {expected_groups} TP groups of size {tp}, got "
            f"{len(global_groups)} groups with sizes "
            f"{sorted({len(group) for group in global_groups})}"
        )
    lines = []
    for group in global_groups:
        domains = {rank // tp for rank in group}
        if len(domains) != 1:
            raise RuntimeError(f"TP group spans scale-up domains: {group}")
        lines.append(" ".join(str(rank % tp) for rank in sorted(group)))
    _write_text_exclusive(destination, "\n".join(lines) + "\n")


def _generate_arms(config_dir: pathlib.Path, raw_dir: pathlib.Path, *, tp: int,
                   microbatch: int, accumulation: int, schedule: str,
                   layers: int, iters: int,
                   check_skeleton: bool) -> dict:
    total_gpus = tp * DP
    graphs = config_dir / "graphs"
    graphs.mkdir(parents=True, exist_ok=False)
    python = sys.executable
    generator_dir = pathlib.Path(paths.GENERATOR_DIR)

    graph_command = [
        python, "-m", "simple_sim.llama3_training",
        "--tp", str(tp), "--dp", str(DP), "--pp", str(PP),
        "--num-layers", str(layers), "--seq-len", str(MODEL["seq_len"]),
        "--ffn", str(MODEL["ffn"]), "--hidden", str(MODEL["hidden"]),
        "--heads", str(MODEL["heads"]), "--kv-heads", str(MODEL["kv_heads"]),
        "--batch", str(microbatch), "--num-microbatches", str(accumulation),
        "--schedule", schedule, "--iters", str(iters),
        "--graphs-dir", str(graphs),
    ]
    tag = f"tp{tp}_mb{microbatch}_ga{accumulation}"
    _run_logged(
        graph_command, cwd=generator_dir, env=_generator_env(emit_inc=False),
        log_path=raw_dir / f"{tag}_graph_generation.log",
    )
    audit = _audit_graph(
        graphs / "00.pkl", microbatch=microbatch, accumulation=accumulation,
        layers=layers, iters=iters,
    )

    base_goal = config_dir / "baseline.goal"
    inc_goal = config_dir / "inc.goal"
    groups = config_dir / "inc.groups"
    translations = [
        ("baseline", base_goal, False, []),
        ("inc", inc_goal, True, ["--groups", str(groups)]),
    ]
    for arm, out_goal, emit_inc, extra in translations:
        command = [
            python, "simple_sim2goal.py", "--graphs-dir", str(graphs),
            "--out-goal", str(out_goal), *extra,
        ]
        _run_logged(
            command, cwd=generator_dir, env=_generator_env(emit_inc=emit_inc),
            log_path=raw_dir / f"{tag}_{arm}_translation.log",
        )
        goal.compile_goal(str(out_goal), str(out_goal.with_suffix(".bin")))

    base_zero1 = _audit_goal_zero1(base_goal, tp=tp, layers=layers, iters=iters)
    inc_zero1 = _audit_goal_zero1(inc_goal, tp=tp, layers=layers, iters=iters)
    audit["emitted_zero1_baseline"] = base_zero1
    audit["emitted_zero1_inc"] = inc_zero1
    audit["checks"]["emitted_zero1_baseline_bytes"] = base_zero1["pass"]
    audit["checks"]["emitted_zero1_inc_bytes"] = inc_zero1["pass"]
    audit["pass"] = all(audit["checks"].values())
    if not audit["pass"]:
        raise RuntimeError(f"emitted GOAL failed ZeRO byte audit: {audit}")

    local_groups = config_dir / "inc_local.groups"
    _localize_groups(groups, local_groups, tp=tp, expected_groups=DP * PP)

    skeleton_equal = None
    if check_skeleton:
        skeleton_goals = []
        for arm, emit_inc in (("baseline", False), ("inc", True)):
            output = config_dir / f"skeleton_{arm}.goal"
            command = [
                python, "simple_sim2goal.py", "--graphs-dir", str(graphs),
                "--out-goal", str(output),
            ]
            if emit_inc:
                command += ["--groups", str(config_dir / "skeleton_inc.groups")]
            _run_logged(
                command, cwd=generator_dir,
                env=_generator_env(emit_inc=emit_inc, skeleton=True),
                log_path=raw_dir / f"{tag}_skeleton_{arm}.log",
            )
            skeleton_goals.append(output)
        skeleton_equal = _sha256(skeleton_goals[0]) == _sha256(skeleton_goals[1])
        if not skeleton_equal:
            raise RuntimeError(f"{tag}: baseline and INC skeleton traces differ")

    compute_max, compute_mean = network.compute_ns_per_iter(str(base_goal), iters)
    return {
        "base_bin": base_goal.with_suffix(".bin"),
        "inc_bin": inc_goal.with_suffix(".bin"),
        "groups": local_groups,
        "compute_ns": compute_max,
        "compute_ns_mean": compute_mean,
        "audit": audit,
        "skeleton_equal": skeleton_equal,
        "total_gpus": total_gpus,
    }


def _run_one_sim(*, binary: pathlib.Path, groups: pathlib.Path | None,
                 total_gpus: int, tp: int, su_topo: pathlib.Path,
                 so_topo: pathlib.Path, su_gbps: int, so_gbps: int,
                 pfc: tuple[int, int, int], timeout: int,
                 raw_log: pathlib.Path) -> dict:
    pfc_high, pfc_low, intranode_q = pfc
    so_queue_packets = max(
        2, network.so_qsize_bytes(so_gbps) // sim.FRAME_B,
    )
    command = [
        paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH,
        "-goal", str(binary),
        "-nodes", str(total_gpus),
        "-num_gpus_per_node", str(tp),
        "-topo", str(so_topo),
        "-linkspeed", str(so_gbps * 1000),
        "-q", str(so_queue_packets),
        "-intranode_topo", str(su_topo),
        "-intranode_linkspeed", str(su_gbps * 1000),
        "-intranode_q", str(intranode_q),
        "-strat", "ecmp_host", "-seed", str(SEED),
        "-mtu", str(sim.MTU_DEFAULT), "-paths", "128",
        "-end", str(network.SIM_END_NS), "-sender_cc_only",
        "-intranode_queue_type", "lossless_input",
        "-intranode_cc", network.INTRANODE_CC,
        "-lossless_high_pfc", str(pfc_high),
        "-lossless_low_pfc", str(pfc_low),
        "-pcm_enable", "-pcm_cc_config_file", network.PCM_CC_CONFIG,
        "-pcm_sched_poll_delay", "1000", "-pcm_handler_delay", "1000",
    ]
    if groups is not None:
        command += ["-groups", str(groups)]

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = network.PCM_LIB_DIR + (
        os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
    )
    started = time.monotonic()
    status = "ok"
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, env=env,
        )
        stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
        if returncode != 0:
            status = f"rc={returncode}"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        returncode = -1
        status = "timeout"
    elapsed_s = time.monotonic() - started
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    combined = stdout + "\n" + stderr
    drops = len(sim.DROP.findall(combined))
    counter = re.search(r"New: \d+ Rtx: (\d+) RTS: (\d+)", combined)
    rtx = int(counter.group(1)) if counter else 0
    rts = int(counter.group(2)) if counter else 0
    ecn = re.search(
        r"Setting ECN to parameters low (\d+) high (\d+)", combined,
    )
    min_rto = re.search(r"Setting min RTO to ([0-9.]+)", combined)
    makespan = sim.parse_makespan(stdout) if status == "ok" else None
    if makespan is None and status == "ok":
        status = "missing_makespan"

    partial = raw_log.with_suffix(raw_log.suffix + ".partial")
    _write_text_exclusive(
        partial,
        f"command: {shlex.join(command)}\nreturncode: {returncode}\n"
        f"elapsed_s: {elapsed_s:.6f}\n\n--- stdout ---\n{stdout}"
        f"\n--- stderr ---\n{stderr}",
    )
    if raw_log.exists():
        raise FileExistsError(f"refusing to replace existing raw log: {raw_log}")
    os.replace(partial, raw_log)
    return {
        "makespan_ns": makespan or "",
        "drops": drops,
        "rtx": rtx,
        "rts": rts,
        "counter_found": counter is not None,
        "so_ecn_low_bytes": int(ecn.group(1)) if ecn else "",
        "so_ecn_high_bytes": int(ecn.group(2)) if ecn else "",
        "min_rto_us": min_rto.group(1) if min_rto else "",
        "status": status,
        "elapsed_s": f"{elapsed_s:.6f}",
        "so_queue_packets": so_queue_packets,
        "command": shlex.join(command),
    }


def _parse_ints(value: str, label: str) -> list[int]:
    try:
        values = [int(item) for item in value.split(",") if item]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid {label}: {value}") from exc
    if not values or any(item < 1 for item in values) or len(values) != len(set(values)):
        raise argparse.ArgumentTypeError(f"{label} must be unique positive integers")
    return values


def _parse_points(value: str) -> list[tuple[int, int]]:
    result = []
    try:
        for item in value.split(","):
            su, so = (int(part) for part in item.split(":", 1))
            result.append((su, so))
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError(
            "points must be comma-separated SU:SO pairs, e.g. 4000:400,8000:800"
        ) from exc
    if (not result or any(su < 1 or so < 1 for su, so in result)
            or len(result) != len(set(result))):
        raise argparse.ArgumentTypeError("points must be positive, non-empty, and unique")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tps", default="4,8,16", help="TP widths (DP is fixed at 4)")
    parser.add_argument("--accumulations", default="32",
                        help="microbatches accumulated per optimizer step")
    parser.add_argument("--include-batch32-reference", action="store_true",
                        help="also run same-harness MB32/GA1 reference cells")
    parser.add_argument("--points", default="4000:400",
                        help="comma-separated scale-up:scale-out Gbps pairs")
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--iters", type=int, default=2)
    parser.add_argument("--jobs", type=int, default=2,
                        help="parallel simulator cells; size for Docker memory")
    parser.add_argument("--timeout", type=int, default=7200, help="seconds per cell")
    parser.add_argument("--run-id", help="safe unique result-directory name")
    parser.add_argument("--validate", action="store_true",
                        help="generate, audit, compile, and compare skeletons; do not simulate")
    parser.add_argument("--skip-skeleton-check", action="store_true",
                        help="skip baseline/INC dependency-skeleton equality check")
    args = parser.parse_args()

    try:
        tps = _parse_ints(args.tps, "tps")
        accumulations = _parse_ints(args.accumulations, "accumulations")
        points = _parse_points(args.points)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    if args.layers < 1 or args.iters < 1 or args.jobs < 1 or args.timeout < 1:
        parser.error("layers, iters, jobs, and timeout must be positive")
    if any(tp not in (4, 8, 16) for tp in tps):
        parser.error("this thesis experiment is intentionally restricted to TP=4,8,16")
    for su, so in points:
        if su < so or 8000 % su or 8000 % so:
            parser.error(
                f"invalid point {su}:{so}: require SU>=SO and exact rates dividing 8000"
            )

    run_id = args.run_id or _default_run_id(accumulations)
    if re.fullmatch(r"[A-Za-z0-9_.-]+", run_id) is None:
        parser.error("run-id may contain only letters, digits, '.', '_', and '-'")
    runs_root = pathlib.Path(paths.results_dir(EXP_NAME)) / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dir = runs_root / run_id
    try:
        run_dir.mkdir(exist_ok=False)
    except FileExistsError:
        parser.error(f"run directory already exists; refusing to overwrite: {run_dir}")

    raw_dir = run_dir / "raw"
    raw_dir.mkdir()
    table_dir = run_dir / "tables"
    table_dir.mkdir()
    table_partial = table_dir / "results.csv.partial"
    table_final = table_dir / "results.csv"
    generator_repo = pathlib.Path(paths.GENERATOR_DIR)
    simulator_repo = pathlib.Path(paths.WORKSPACE) / "sim" / "pcm-sdk_zhiyi"
    source_records = _snapshot_sources(run_dir)
    workloads = [
        {
            "name": f"corrected_mb1_ga{accumulation}",
            "microbatch": CORRECTED_MICROBATCH,
            "accumulation": accumulation,
            "schedule": SCHEDULE,
        }
        for accumulation in accumulations
    ]
    if args.include_batch32_reference:
        workloads.append({
            "name": "reference_mb32_ga1",
            "microbatch": 32,
            "accumulation": 1,
            # With one microbatch, 1F1B and GPipe are structurally identical;
            # using the explicit schedule removes schedule selection as a factor.
            "schedule": SCHEDULE,
        })
    manifest = {
        "experiment": EXP_NAME,
        "run_id": run_id,
        "created_utc": _utc_now(),
        "command": [sys.executable, *sys.argv],
        "configuration": {
            "workloads": workloads,
            "dp": DP,
            "pp": PP,
            "tps": tps,
            "points_su_so_gbps": points,
            "layers": args.layers,
            "iters": args.iters,
            "model": MODEL,
            "compute_model": COMPUTE_MODEL,
            "allreduce": "ring",
            "zero1_bytes": "physical_tp_local",
            "seed": SEED,
        },
        "network_scope": {
            "inherits_existing_harness": True,
            "scale_up_latency_ns": {"link": 50, "switch": 300},
            "scale_out_latency_ns": {"link": 1, "switch": 0},
            "warning": "Scale-out is an idealized non-blocking topology, not a hardware-latency model.",
        },
        "git": {
            "workspace": _git_state(pathlib.Path(paths.WORKSPACE)),
            "generator": _git_state(generator_repo),
            "simulator": _git_state(simulator_repo),
        },
        "binaries": {
            "simulator": {
                "path": paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH,
                "sha256": _sha256(pathlib.Path(paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH)),
            },
            "txt2bin": {
                "path": paths.COLL_TXT2BIN,
                "sha256": _sha256(pathlib.Path(paths.COLL_TXT2BIN)),
            },
            "dctcp_library": {
                "path": str(pathlib.Path(network.PCM_LIB_DIR) / "libuec_dctcp_v2.so"),
                "sha256": _sha256(pathlib.Path(network.PCM_LIB_DIR) / "libuec_dctcp_v2.so"),
            },
            "dctcp_config": {
                "path": network.PCM_CC_CONFIG,
                "sha256": _sha256(pathlib.Path(network.PCM_CC_CONFIG)),
            },
        },
        "runtime": {
            "python": sys.version,
            "docker_image_id": os.environ.get("ATLAHS_SIM_IMAGE_ID", "not_provided"),
        },
        "source_snapshot": source_records,
    }
    _write_json_exclusive(run_dir / "manifest.json", manifest)

    audits = []
    rows = []
    try:
        goal.require_txt2bin()
        goal.require_generator()
        if not args.validate:
            sim.require_simulator()
            network._preflight_cc()

        with table_partial.open("x", newline="", encoding="utf-8") as table_handle:
            writer = csv.DictWriter(table_handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for workload in workloads:
                microbatch = workload["microbatch"]
                accumulation = workload["accumulation"]
                schedule = workload["schedule"]
                for tp in tps:
                    total_gpus = tp * DP
                    tag = (
                        f"tp{tp}_g{total_gpus}_mb{microbatch}_ga{accumulation}"
                    )
                    config_dir = run_dir / "work" / tag
                    config_dir.mkdir(parents=True, exist_ok=False)
                    network.EXP_NAME = EXP_NAME
                    network.configure_scale(total_gpus, [tp], (PP,))
                    arms = _generate_arms(
                        config_dir, raw_dir, tp=tp, accumulation=accumulation,
                        microbatch=microbatch, schedule=schedule,
                        layers=args.layers, iters=args.iters,
                        check_skeleton=not args.skip_skeleton_check,
                    )
                    audit_row = {
                        "tag": tag,
                        "tp": tp,
                        "microbatch": microbatch,
                        "accumulation": accumulation,
                        "skeleton_equal": arms["skeleton_equal"],
                        **arms["audit"],
                    }
                    audits.append(audit_row)
                    if args.validate:
                        continue

                    cells = []
                    for su_gbps, so_gbps in points:
                        su_topo = pathlib.Path(network.su_topo_for(tp, su_gbps, str(config_dir)))
                        so_topo = pathlib.Path(
                            network.so_topo_for(so_gbps, str(config_dir), hosts=total_gpus)
                        )
                        pfc = sim.pfc_config(str(su_topo))
                        for arm in ("baseline", "inc"):
                            raw_log = raw_dir / (
                                f"{tag}_su{su_gbps}_so{so_gbps}_{arm}.log"
                            )
                            cells.append((su_gbps, so_gbps, su_topo, so_topo, pfc, arm, raw_log))

                    def run_cell(cell):
                        su_gbps, so_gbps, su_topo, so_topo, pfc, arm, raw_log = cell
                        result = _run_one_sim(
                            binary=arms["inc_bin"] if arm == "inc" else arms["base_bin"],
                            groups=arms["groups"] if arm == "inc" else None,
                            total_gpus=total_gpus, tp=tp, su_topo=su_topo,
                            so_topo=so_topo, su_gbps=su_gbps, so_gbps=so_gbps,
                            pfc=pfc, timeout=args.timeout, raw_log=raw_log,
                        )
                        makespan = result["makespan_ns"]
                        tpi = makespan / args.iters / 1e9 if makespan else ""
                        return {
                            "run_id": run_id,
                            "workload": workload["name"],
                            "tp": tp, "dp": DP, "pp": PP,
                            "total_gpus": total_gpus, "arm": arm,
                            "microbatch": microbatch,
                            "accumulation": accumulation,
                            "global_batch": microbatch * accumulation * DP,
                            "schedule": schedule,
                            "layers": args.layers, "iters": args.iters,
                            "hidden": MODEL["hidden"], "ffn": MODEL["ffn"],
                            "heads": MODEL["heads"], "kv_heads": MODEL["kv_heads"],
                            "seq_len": MODEL["seq_len"],
                            "tp_allreduces_per_iter": arms["audit"]["actual_tp_allreduces"] // args.iters,
                            "tp_allreduce_bytes": arms["audit"]["expected_tp_allreduce_bytes"],
                            "tp_declared_tensor_bytes_per_iter": (
                                arms["audit"]["actual_tp_allreduces"] // args.iters
                                * arms["audit"]["expected_tp_allreduce_bytes"]
                            ),
                            "algorithm": "ring", "zero1_bytes": "physical_tp_local",
                            "su_gbps": su_gbps, "so_gbps": so_gbps,
                            "su_link_latency_ns": 50, "su_switch_latency_ns": 300,
                            "so_link_latency_ns": 1, "so_switch_latency_ns": 0,
                            "pfc_high": pfc[0], "pfc_low": pfc[1],
                            "intranode_q": pfc[2],
                            "intranode_cc": network.INTRANODE_CC,
                            "su_topo": su_topo.name,
                            "su_topo_sha256": _sha256(su_topo),
                            "so_topo": so_topo.name,
                            "so_topo_sha256": _sha256(so_topo),
                            "compute_model": COMPUTE_MODEL,
                            "compute_ns_per_iter": arms["compute_ns"] or "",
                            "compute_ns_per_iter_mean": arms["compute_ns_mean"] or "",
                            "time_per_iter_s": f"{tpi:.9f}" if tpi != "" else "",
                            "generator_revision": _generator_revision(),
                            "engine": network._engine_id(), "seed": SEED,
                            "raw_log": str(raw_log.relative_to(run_dir)),
                            **result,
                        }

                    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
                        config_rows = list(executor.map(run_cell, cells))
                    for row in config_rows:
                        writer.writerow(row)
                        rows.append(row)
                    table_handle.flush()
                    os.fsync(table_handle.fileno())

        _write_json_exclusive(run_dir / "audits.json", audits)
        invalid = [
            row for row in rows
            if (row["status"] != "ok" or row["drops"] or row["rtx"]
                or row["rts"] or not row["counter_found"])
        ]
        if invalid:
            raise RuntimeError(f"{len(invalid)} simulator cell(s) failed acceptance checks")
        if table_final.exists():
            raise FileExistsError(f"refusing to replace existing table: {table_final}")
        os.replace(table_partial, table_final)
        _write_json_exclusive(run_dir / "summary.json", {
            "completed_utc": _utc_now(),
            "mode": "validate" if args.validate else "simulate",
            "audited_configurations": len(audits),
            "simulator_cells": len(rows),
            "table": str(table_final.relative_to(run_dir)),
        })
        _write_text_exclusive(run_dir / ".complete", _utc_now() + "\n")
        print(f"PASS: isolated run complete: {run_dir}")
        return 0
    except Exception as exc:
        _write_text_exclusive(run_dir / ".failed", f"{_utc_now()}\n{type(exc).__name__}: {exc}\n")
        print(f"FAIL: {exc}\nArtifacts retained at: {run_dir}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
