#!/usr/bin/env python3
"""Scale-up INC-vs-endpoint collective A/B experiment, isolated to a single
scale-up domain, on the pcm-sdk two-tier simulator.

Frozen A/B design (locked 2026-07-20):
  * INC arm   = one first-class `coll <kind>` op per rank + a .groups sidecar.
  * Baseline  = the GENERATOR's OWN decomposition, emitted by calling
                goal_gen/ai/nccl_generator_v2/communication.py directly (Ring /
                Recursive-doubling; NCCL algorithms per Demystifying-NCCL Tables V-VII).
                NOT hand-rolled.
  * Charge NEITHER arm for reduction compute (-reduce_compute_latency 0); the A/B
    isolates data movement + step count. (--reduce-compute >0 = sensitivity study.)
  * AllReduce runs BOTH ring and recursive-doubling baselines; RS/AG/Bcast/Reduce
    use ring-family endpoint baselines; composed RS+AG is a separate INC arm.
Isolation: all N active ranks occupy domain 0. `-num_gpus_per_node` is the selected
scale-up topology's fixed width, so group-size sweeps partially populate that domain
without resizing the fabric; no scale-out flows are emitted.

Paths resolve via common/paths.py (repo root derived from this file; each path
env-overridable). Output defaults to simulation-scripts/results/scaleup_coll_ab/
(override: SCALEUP_OUTPUT_DIR).

Reproduce (Docker, sim-only image):
  docker build -t atlahs-sim simulation-scripts/
  docker run --rm --user "$(id -u):$(id -g)" -v $(pwd):/workspace atlahs-sim build
  docker run --rm --user "$(id -u):$(id -g)" -v $(pwd):/workspace atlahs-sim run scaleup_coll_ab --validate
  docker run --rm --user "$(id -u):$(id -g)" -v $(pwd):/workspace atlahs-sim run scaleup_coll_ab --n 8 --su-topo <topo>
Local (no Docker; binaries built in-tree):
  python3 experiments/scaleup_coll_ab/run.py --validate
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import goal, paths, report, sim  # noqa: E402

EXP_NAME = "scaleup_coll_ab"
TAIL_NS = 100  # identical dependent calc tail in both arms; cancels in the A/B

OUTPUT_DIR = os.environ.get("SCALEUP_OUTPUT_DIR", paths.results_dir(EXP_NAME))

# The scale-out topo carries no traffic here (all ranks in node 0); it only
# needs >= N hosts.
SO_TOPO_DEFAULT = "tree16_bw200Gbps.topo"

# Single source of truth for the per-GPU NIC rate: the SAME value is passed to
# run_sim (the -intranode_linkspeed flag) and recorded in the CSV column, so the
# reported rate can never drift from the one actually simulated. ATLAHS_NIC_MBPS
# overrides it so the NIC can be matched to a fabric whose .topo link rate is not
# the 4000 Gb/s default (an unmatched NIC silently rate-limits every receiver).
INTRANODE_LINKSPEED = int(os.environ.get("ATLAHS_NIC_MBPS",
                                         sim.INTRANODE_LINKSPEED_DEFAULT))

# (collective, baseline algorithm[, inc_kind]). AllReduce runs both baselines;
# RS/AG ring-only. Optional `inc_kind` overrides the INC-arm coll kind while the
# baseline stays `collective` -- used for the NVLS-style AllReduce that the INC
# arm runs as ReduceScatter+AllGather (`allreduce_rs_ag`), compared against the
# same endpoint AllReduce baseline. The apex INC AllReduce (rows 1-2) is kept.
COLL_CASES = [
    {"collective": "allreduce",      "algo": "ring"},
    {"collective": "allreduce",      "algo": "rdouble"},
    {"collective": "allreduce",      "algo": "ring", "inc_kind": "allreduce_rs_ag"},
    {"collective": "reduce_scatter", "algo": "ring"},
    {"collective": "allgather",      "algo": "ring"},
    {"collective": "bcast",          "algo": "ring"},   # rooted; INC = mcast, baseline = pipelined-ring
    {"collective": "reduce",         "algo": "ring"},   # rooted; INC = aggregation, baseline = pipelined-ring
]
DEFAULT_N     = 8
DEFAULT_SIZES = [4096, 16384, 65536, 262144, 1048576, 4194304]

CSV_FIELDS = ["collective", "baseline_algo", "group_size", "msg_bytes",
              "inc_ns", "base_ns", "speedup",
              "inc_makespan_ns", "base_makespan_ns", "drops", "fabric",
              "reduce_compute_ns", "su_topo", "so_topo", "intranode_linkspeed_mbps",
              "engine", "log_file", "command"]


def validate(n, size, tmpdir):
    """Topology-independent: generate every arm, check step counts vs the paper, compile."""
    os.makedirs(tmpdir, exist_ok=True)
    report.print_info(f"validation @ N={n}, size={size} (no sim)")
    print(f"{'collective':>16} {'algo':>8} {'steps':>6} {'expect':>7} {'compile':>8}")
    all_ok = True
    for case in COLL_CASES:
        coll, algo = case["collective"], case["algo"]
        inc_kind = case.get("inc_kind", coll)
        inc_root = 0 if inc_kind in ("bcast", "reduce") else -1  # rooted INC arm (bcast/reduce)
        label = inc_kind
        if algo == "rdouble" and (n & (n - 1)):
            print(f"{label:>18} {algo:>8}    skip (N not power of 2)")
            continue
        base = os.path.join(tmpdir, f"base_{label}_{algo}_{n}_{size}.goal")
        inc = os.path.join(tmpdir, f"inc_{label}_{n}_{size}.goal")
        goal.gen_baseline_goal(base, n, size, coll, algo, TAIL_NS)
        goal.gen_inc_goal(inc, inc[:-5] + ".groups", n, size, inc_kind, TAIL_NS, root=inc_root)
        # Step-count check is on the endpoint baseline (coll/algo); the INC arm's
        # composite (allreduce_rs_ag) has no single "steps" count, so only compile-check it.
        got = goal.count_steps(base)
        if coll in ("bcast", "reduce"):
            exp, ok = got, True   # pipelined-chain K = max(N, size//SEG_BYTES) is size-dependent; compile-check only
        else:
            exp = goal.expected_steps(coll, n, algo)
            ok = (got == exp)
        goal.compile_goal(base, base[:-5] + ".bin")
        goal.compile_goal(inc, inc[:-5] + ".bin")
        all_ok &= ok
        print(f"{label:>18} {algo:>8} {got:>6} {exp:>7} {'OK' if ok else 'MISMATCH':>8}")
    print("validation:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


def run_exp(n, sizes, su_topo, so_topo, reduce_compute, tmpdir, timeout):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(tmpdir, exist_ok=True)
    su_width = sim.topology_nodes(su_topo)
    if n > su_width:
        raise ValueError(f"group size N={n} exceeds scale-up topology width {su_width}")
    csv_path = os.path.join(OUTPUT_DIR, "scaleup_coll_ab.csv")
    failures = 0
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        for case in COLL_CASES:
            coll, algo = case["collective"], case["algo"]
            inc_kind = case.get("inc_kind", coll)  # INC-arm coll kind (composite = allreduce_rs_ag)
            inc_root = 0 if inc_kind in ("bcast", "reduce") else -1  # rooted INC arm (bcast/reduce)
            label = inc_kind                        # distinct output name; baseline still uses `coll`/`algo`
            if algo == "rdouble" and (n & (n - 1)):
                report.print_warning(f"{label}/{algo}: skip (N={n} not power of 2)")
                continue
            report.print_info(f"=== {label} baseline={algo} N={n} su={os.path.basename(su_topo)} ===")
            for s in sizes:
                if s % n:
                    report.print_warning(f"{s}: skip (not divisible by N)")
                    continue
                base = os.path.join(tmpdir, f"base_{label}_{algo}_{s}.goal")
                inc = os.path.join(tmpdir, f"inc_{label}_{s}.goal")
                grp = inc[:-5] + ".groups"
                goal.gen_baseline_goal(base, n, s, coll, algo, TAIL_NS)
                goal.gen_inc_goal(inc, grp, n, s, inc_kind, TAIL_NS, root=inc_root)
                goal.compile_goal(base, base[:-5] + ".bin")
                goal.compile_goal(inc, inc[:-5] + ".bin")
                log = os.path.join(OUTPUT_DIR, "logs", f"{label}_{algo}_{n}_{s}.log")
                os.makedirs(os.path.dirname(log), exist_ok=True)
                ifin, idrop, ist, icmd, _ = sim.run_sim(
                    inc[:-5] + ".bin", so_topo, su_topo,
                    nodes=n, gpus_per_node=su_width, groups=grp,
                    reduce_compute=reduce_compute, timeout=timeout,
                    intranode_linkspeed=INTRANODE_LINKSPEED)
                bfin, bdrop, bst, _, _px = sim.run_sim(base[:-5] + ".bin", so_topo, su_topo,
                                                  # No group/FIB installation is needed on the P2P
                                                  # arm. Keeping its API width at the active rank
                                                  # count preserves the frozen flow-id/ECMP stream;
                                                  # both arms still occupy one scale-up fabric.
                                                  nodes=n, gpus_per_node=n, timeout=timeout,
                                                  intranode_linkspeed=INTRANODE_LINKSPEED)
                inc_ns = ifin - TAIL_NS if ifin else None
                base_ns = bfin - TAIL_NS if bfin else None
                ok = bool(inc_ns and base_ns and ist == "ok" and bst == "ok"
                          and idrop == 0 and bdrop == 0)
                if not ok:
                    failures += 1
                speedup = base_ns / inc_ns if ok else None
                with open(log, "w") as lf:
                    lf.write(icmd + "\n")
                out.write({
                    "collective": label, "baseline_algo": algo, "group_size": n,
                    "msg_bytes": s, "inc_ns": inc_ns or "", "base_ns": base_ns or "",
                    "speedup": f"{speedup:.3f}" if speedup else "",
                    "inc_makespan_ns": ifin or "", "base_makespan_ns": bfin or "",
                    "drops": idrop + bdrop, "fabric": "lossless_input",
                    "reduce_compute_ns": reduce_compute, "su_topo": os.path.basename(su_topo),
                    "so_topo": os.path.basename(so_topo),
                    "intranode_linkspeed_mbps": INTRANODE_LINKSPEED,
                    "engine": "pcm-sdk", "log_file": log, "command": icmd,
                })
                sp = f"{speedup:.2f}x" if speedup else "-"
                st = "ok" if ok else f"inc={ist},base={bst}"
                if idrop or bdrop:
                    st += f" WARN drops={idrop + bdrop}"
                print(f"  {s:>10}b  INC {str(inc_ns):>9}  BASE {str(base_ns):>9}  {sp:>7}  {st}")
    if failures:
        report.print_error(f"{failures} simulation cell(s) failed; partial CSV: {csv_path}")
        return 1
    report.print_success(f"wrote results to {csv_path}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=DEFAULT_N, help="active collective group size")
    ap.add_argument("--sizes", default=",".join(str(x) for x in DEFAULT_SIZES))
    ap.add_argument("--su-topo", default=None,
                    help="scale-up .topo basename in TOPO_FILES_PATH (required for sim runs)")
    ap.add_argument("--so-topo", default=SO_TOPO_DEFAULT,
                    help="scale-out .topo basename (unused under single-domain isolation)")
    ap.add_argument("--reduce-compute", type=int, default=0,
                    help="INC in-switch ALU latency (ns); DEFAULT 0 = charge-neither. >0 = sensitivity")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--tmpdir", default="/tmp/scaleup_coll_ab")
    ap.add_argument("--validate", action="store_true",
                    help="generate all arms, check step counts vs the paper, compile — NO sim")
    args = ap.parse_args()

    goal.require_txt2bin()
    goal.require_generator()  # fail on a broken GENERATOR_DIR before any filesystem writes
    sizes = [int(x) for x in args.sizes.split(",")]

    if args.validate:
        sys.exit(validate(args.n, sizes[0], args.tmpdir))

    sim.require_simulator()
    if args.su_topo is None:
        sys.exit("--su-topo is required for a sim run; use a frozen wrapper or pass an "
                 "explicit topology, or use --validate for the no-sim check")
    sys.exit(run_exp(args.n, sizes, paths.topo(args.su_topo), paths.topo(args.so_topo),
                     args.reduce_compute, args.tmpdir, args.timeout))


if __name__ == "__main__":
    main()
