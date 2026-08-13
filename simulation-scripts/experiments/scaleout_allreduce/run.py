#!/usr/bin/env python3
"""64-rank scale-out AllReduce endpoint comparison.

This is the multi-domain companion to ``scaleup_coll_ab``.  It keeps the
Figure-1 group size and message sweep, but maps the 64 ranks as eight
contiguous 8-GPU scale-up domains.  Cross-domain packets use a 2:1 tapered
100-Gbps scale-out fabric; intra-domain packets use a non-blocking 12.8-Tbps
scale-up crossbar.

The simulator's first-class INC primitive is intentionally node-local, so it
cannot represent a single 64-rank in-network operation across scale-up domains.
The default measurement therefore adds the semantically complete hierarchical
variant: local INC ReduceScatter, lane-parallel Bine AllReduce across scale-out,
then local INC AllGather.  ``plot.py`` labels it explicitly rather than
presenting it as a hypothetical global in-network switch.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import goal, paths, report, sim  # noqa: E402

EXP_NAME = "scaleout_allreduce"
TAIL_NS = 100
DEFAULT_N = 64
DEFAULT_GPUS_PER_NODE = 8
DEFAULT_SIZES = [4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864]
DEFAULT_SU_TOPO = "scaleup_single_switch_8_12800Gbps.topo"
DEFAULT_SO_TOPO = "scaleout_2tier_64_oversub2_100Gbps.topo"
DEFAULT_ALGOS = ("ring", "rdouble", "tree", "bine")
HIERARCHICAL_INC_ALGO = "hier_inc_bine"
INTRANODE_LINKSPEED = 12800000  # 12.8 Tbps, matched to DEFAULT_SU_TOPO.

OUTPUT_DIR = os.environ.get("SCALEOUT_OUTPUT_DIR", paths.results_dir(EXP_NAME))
CSV_FIELDS = ["collective", "baseline_algo", "group_size", "gpus_per_node", "msg_bytes",
              "base_ns", "base_makespan_ns", "drops", "fabric", "su_topo", "so_topo",
              "intranode_linkspeed_mbps", "engine", "log_file", "command"]


def run_exp(n, gpus_per_node, sizes, algos, hierarchical_inc,
            su_topo, so_topo, tmpdir, timeout):
    """Generate and run direct endpoint schedules on a multi-domain fabric."""
    if n < 2 or n & (n - 1):
        raise ValueError(f"N must be a power of two >= 2; got {n}")
    if n % gpus_per_node:
        raise ValueError(f"N={n} must be divisible by gpus-per-node={gpus_per_node}")
    if sim.topology_nodes(su_topo) != gpus_per_node:
        raise ValueError(f"{os.path.basename(su_topo)} must have Nodes={gpus_per_node}")
    if sim.topology_nodes(so_topo) < n:
        raise ValueError(f"{os.path.basename(so_topo)} has fewer than N={n} scale-out endpoints")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(tmpdir, exist_ok=True)
    csv_path = os.path.join(OUTPUT_DIR, f"{EXP_NAME}.csv")
    failures = 0
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        for algo in algos:
            report.print_info(
                f"=== allreduce baseline={algo} N={n} ({n // gpus_per_node} x {gpus_per_node}-GPU domains) ===")
            for size in sizes:
                if size % n:
                    report.print_warning(f"{size}: skip (not divisible by N={n})")
                    continue
                stem = f"allreduce_{algo}_{n}_{size}"
                trace = os.path.join(tmpdir, stem + ".goal")
                goal.gen_baseline_goal(trace, n, size, "allreduce", algo, TAIL_NS)
                binary = trace[:-5] + ".bin"
                goal.compile_goal(trace, binary)
                finish, drops, status, command, _ = sim.run_sim(
                    binary, so_topo, su_topo, nodes=n, gpus_per_node=gpus_per_node,
                    timeout=timeout, intranode_linkspeed=INTRANODE_LINKSPEED)
                base_ns = finish - TAIL_NS if finish else None
                ok = bool(base_ns and status == "ok" and drops == 0)
                if not ok:
                    failures += 1
                log = os.path.join(OUTPUT_DIR, "logs", stem + ".log")
                os.makedirs(os.path.dirname(log), exist_ok=True)
                with open(log, "w") as stream:
                    stream.write(command + "\n")
                out.write({
                    "collective": "allreduce", "baseline_algo": algo,
                    "group_size": n, "gpus_per_node": gpus_per_node, "msg_bytes": size,
                    "base_ns": base_ns or "", "base_makespan_ns": finish or "", "drops": drops,
                    "fabric": "2to1_oversubscribed_scaleout", "su_topo": os.path.basename(su_topo),
                    "so_topo": os.path.basename(so_topo),
                    "intranode_linkspeed_mbps": INTRANODE_LINKSPEED, "engine": "pcm-sdk",
                    "log_file": log, "command": command,
                })
                print(f"  {size:>10}b  BASE {str(base_ns):>10}  "
                      f"{'ok' if ok else f'{status}, drops={drops}'}")
        if hierarchical_inc:
            report.print_info("=== allreduce hierarchical INC (local INC RS/AG + Bine lanes) "
                              f"N={n} ({n // gpus_per_node} x {gpus_per_node}-GPU domains) ===")
            for size in sizes:
                if size % n:
                    report.print_warning(f"{size}: skip (not divisible by N={n})")
                    continue
                stem = f"allreduce_{HIERARCHICAL_INC_ALGO}_{n}_{size}"
                trace = os.path.join(tmpdir, stem + ".goal")
                groups = trace[:-5] + ".groups"
                goal.gen_hierarchical_inc_allreduce_goal(
                    trace, groups, n, gpus_per_node, size, "bine", TAIL_NS)
                binary = trace[:-5] + ".bin"
                goal.compile_goal(trace, binary)
                finish, drops, status, command, _ = sim.run_sim(
                    binary, so_topo, su_topo, nodes=n, gpus_per_node=gpus_per_node,
                    groups=groups, timeout=timeout, intranode_linkspeed=INTRANODE_LINKSPEED)
                base_ns = finish - TAIL_NS if finish else None
                ok = bool(base_ns and status == "ok" and drops == 0)
                if not ok:
                    failures += 1
                log = os.path.join(OUTPUT_DIR, "logs", stem + ".log")
                os.makedirs(os.path.dirname(log), exist_ok=True)
                with open(log, "w") as stream:
                    stream.write(command + "\n")
                out.write({
                    "collective": "allreduce", "baseline_algo": HIERARCHICAL_INC_ALGO,
                    "group_size": n, "gpus_per_node": gpus_per_node, "msg_bytes": size,
                    "base_ns": base_ns or "", "base_makespan_ns": finish or "", "drops": drops,
                    "fabric": "hierarchical_inc_local_rs_ag",
                    "su_topo": os.path.basename(su_topo), "so_topo": os.path.basename(so_topo),
                    "intranode_linkspeed_mbps": INTRANODE_LINKSPEED, "engine": "pcm-sdk",
                    "log_file": log, "command": command,
                })
                print(f"  {size:>10}b  HIER-INC {str(base_ns):>6}  "
                      f"{'ok' if ok else f'{status}, drops={drops}'}")
    if failures:
        report.print_error(f"{failures} simulation cell(s) failed; partial CSV: {csv_path}")
        return 1
    report.print_success(f"wrote results to {csv_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--gpus-per-node", type=int, default=DEFAULT_GPUS_PER_NODE)
    parser.add_argument("--sizes", default=",".join(str(s) for s in DEFAULT_SIZES))
    parser.add_argument("--algos", default=",".join(DEFAULT_ALGOS),
                        help="comma-separated endpoint algorithms: ring,rdouble,tree,bine")
    parser.add_argument("--without-hierarchical-inc", action="store_false", dest="hierarchical_inc",
                        help="omit the local INC RS/AG + lane-Bine comparison arm")
    parser.add_argument("--su-topo", default=DEFAULT_SU_TOPO)
    parser.add_argument("--so-topo", default=DEFAULT_SO_TOPO)
    parser.add_argument("--tmpdir", default="/tmp/scaleout_allreduce")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    algos = tuple(a.strip() for a in args.algos.split(",") if a.strip())
    unknown = set(algos) - set(DEFAULT_ALGOS)
    if unknown:
        parser.error("unknown algorithm(s): " + ", ".join(sorted(unknown)))
    if not algos:
        parser.error("--algos selected no algorithms")
    goal.require_txt2bin()
    goal.require_generator()
    sim.require_simulator()
    sizes = [int(s) for s in args.sizes.split(",")]
    sys.exit(run_exp(args.n, args.gpus_per_node, sizes, algos, args.hierarchical_inc,
                     paths.topo(args.su_topo), paths.topo(args.so_topo),
                     args.tmpdir, args.timeout))


if __name__ == "__main__":
    main()
