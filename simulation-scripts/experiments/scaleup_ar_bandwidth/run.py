#!/usr/bin/env python3
"""Scale-up AllReduce reduction-bandwidth: fused apex vs composed RS-then-AG.

Both arms are IN-NETWORK (there is NO endpoint baseline here):
  * apex     -- a single rootless `coll allreduce`: the SHARP/NVLS-style fused
                apex turn-around (reduce up, turn around in-switch, multicast down).
  * composed -- `allreduce_rs_ag`: an NVLS-multimem-style AllReduce composed from a
                ReduceScatter followed by a dependent AllGather (two coll ops sharing
                one group, with a host resync between the two waves).

Metric = reduction bandwidth 8*S/T (Gbit/s) vs message size S -- the same recast as the
fork's "Aggregation bandwidth vs message size" figure (apex approaching wire speed while
the two-wave decomposition plateaus near half wire).

Why this is NOT gated on the pcm `B_inc != B_ring` header bug: both arms are INC coll
ops with the identical headerless analytic completion, so the ~2% header artifact is
COMMON-MODE and cancels in the apex/composed ratio. (The absolute "% of wire" annotation
reads ~2% optimistic until the header fix lands; the apex-vs-composed RATIO is exact.)

Isolation: all N ranks in node 0 of one scale-up domain (-nodes N -num_gpus_per_node N),
so the whole collective runs intranode. Charge NEITHER arm for reduction compute
(-reduce_compute_latency 0); both structures do one full in-network reduction pass, so
the charge is ~symmetric. (--reduce-compute >0 = sensitivity study.)

Citations: fused apex = SHARP/NVLS (graham2020sharp; + sapio2021switchml, lao2021atp);
composed RS+AG = NVLS-multimem / MSCCL++ (needs an `mscclpp` bib entry -- absent today).

Reproduce (local; binaries built in-tree):
  python3 experiments/scaleup_ar_bandwidth/run.py --validate            # generate+compile, NO sim
  python3 experiments/scaleup_ar_bandwidth/run.py --su-topo scaleup_single_switch_64_4000Gbps.topo
  python3 experiments/scaleup_ar_bandwidth/plot.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import goal, paths, report, sim  # noqa: E402

EXP_NAME = "scaleup_ar_bandwidth"
TAIL_NS = 100  # dependent calc tail on every arm; subtracted from each makespan

OUTPUT_DIR = os.environ.get("SCALEUP_OUTPUT_DIR", paths.results_dir(EXP_NAME))

# Scale-out topo carries no traffic (all ranks in node 0); matches scaleup_coll_ab.
SO_TOPO_DEFAULT = "tree16_bw200Gbps.topo"
INTRANODE_LINKSPEED = sim.INTRANODE_LINKSPEED_DEFAULT  # pinned NIC rate = the "wire"

# The two IN-NETWORK AllReduce structures compared on reduction bandwidth. Both are
# INC coll ops (no endpoint baseline), so their ratio is bug-common-mode.
ARMS = [
    {"label": "apex",     "inc_kind": "allreduce"},        # fused apex turn-around
    {"label": "composed", "inc_kind": "allreduce_rs_ag"},  # RS then AG (host resync between waves)
]

DEFAULT_N = 64
# 9-point log grid spanning the latency regime (small) -> bandwidth regime (large).
# All divisible by N=64 (the coll grammar requires size % N == 0 for clean blocks).
DEFAULT_SIZES = [4096, 16384, 65536, 262144, 1048576,
                 4194304, 16777216, 67108864, 268435456]

CSV_FIELDS = ["arm", "inc_kind", "group_size", "msg_bytes",
              "inc_ns", "makespan_ns", "bandwidth_gbps", "pct_wire", "drops",
              "fabric", "reduce_compute_ns", "su_topo", "so_topo",
              "intranode_linkspeed_mbps", "engine", "log_file", "command"]


def bandwidth_gbps(msg_bytes, inc_ns):
    """Reduction bandwidth 8*S/T: (bytes*8) bits / ns = Gbit/s."""
    return 8.0 * msg_bytes / inc_ns if inc_ns else None


def validate(n, size, tmpdir):
    """Generate + compile both INC arms (NO sim). The INC coll ops carry no endpoint
    'steps' count to check against the paper, so this is purely a compile check."""
    os.makedirs(tmpdir, exist_ok=True)
    report.print_info(f"validation @ N={n}, size={size} (no sim)")
    for arm in ARMS:
        g = os.path.join(tmpdir, f"inc_{arm['label']}_{n}_{size}.goal")
        goal.gen_inc_goal(g, g[:-5] + ".groups", n, size, arm["inc_kind"], TAIL_NS)
        goal.compile_goal(g, g[:-5] + ".bin")
        print(f"  {arm['label']:>9} ({arm['inc_kind']}): compiled OK")
    print("validation: PASS")
    return 0


def run_exp(n, sizes, su_topo, so_topo, reduce_compute, tmpdir, timeout):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(tmpdir, exist_ok=True)
    wire_gbps = INTRANODE_LINKSPEED / 1000.0  # Mbps -> Gbit/s
    csv_path = os.path.join(OUTPUT_DIR, f"{EXP_NAME}.csv")
    failures = 0
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        for arm in ARMS:
            report.print_info(f"=== {arm['label']} ({arm['inc_kind']}) N={n} "
                              f"su={os.path.basename(su_topo)} ===")
            for s in sizes:
                if s % n:
                    report.print_warning(f"{s}: skip (not divisible by N={n})")
                    continue
                g = os.path.join(tmpdir, f"inc_{arm['label']}_{s}.goal")
                grp = g[:-5] + ".groups"
                goal.gen_inc_goal(g, grp, n, s, arm["inc_kind"], TAIL_NS)
                goal.compile_goal(g, g[:-5] + ".bin")
                log = os.path.join(OUTPUT_DIR, "logs", f"{arm['label']}_{n}_{s}.log")
                os.makedirs(os.path.dirname(log), exist_ok=True)
                fin, drop, st, cmd, _ = sim.run_sim(g[:-5] + ".bin", so_topo, su_topo,
                                                 nodes=n, gpus_per_node=n, groups=grp,
                                                 reduce_compute=reduce_compute,
                                                 timeout=timeout,
                                                 intranode_linkspeed=INTRANODE_LINKSPEED)
                inc_ns = fin - TAIL_NS if fin else None
                ok = bool(inc_ns and st == "ok" and drop == 0)
                if not ok:
                    failures += 1
                bw = bandwidth_gbps(s, inc_ns) if ok else None
                pct = 100.0 * bw / wire_gbps if bw else None
                with open(log, "w") as lf:
                    lf.write(cmd + "\n")
                out.write({
                    "arm": arm["label"], "inc_kind": arm["inc_kind"],
                    "group_size": n, "msg_bytes": s,
                    "inc_ns": inc_ns or "", "makespan_ns": fin or "",
                    "bandwidth_gbps": f"{bw:.2f}" if bw else "",
                    "pct_wire": f"{pct:.1f}" if pct else "",
                    "drops": drop, "fabric": "lossless_input",
                    "reduce_compute_ns": reduce_compute,
                    "su_topo": os.path.basename(su_topo),
                    "so_topo": os.path.basename(so_topo),
                    "intranode_linkspeed_mbps": INTRANODE_LINKSPEED,
                    "engine": "pcm-sdk", "log_file": log, "command": cmd,
                })
                shown = f"{bw:.1f} Gb/s ({pct:.0f}%)" if bw else "-"
                status = "ok" if ok else st
                if drop:
                    status += f" WARN drops={drop}"
                print(f"  {s:>11}b  inc {str(inc_ns):>10}ns  {shown:>18}  {status}")
    if failures:
        report.print_error(f"{failures} simulation cell(s) failed; partial CSV: {csv_path}")
        return 1
    report.print_success(f"wrote results to {csv_path}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=DEFAULT_N, help="scale-up domain width / group size |G|")
    ap.add_argument("--sizes", default=",".join(str(x) for x in DEFAULT_SIZES),
                    help="comma-separated message sizes in bytes (full gathered vector)")
    ap.add_argument("--su-topo", default=None,
                    help="scale-up .topo basename in TOPO_FILES_PATH "
                         "(e.g. scaleup_single_switch_64_4000Gbps.topo)")
    ap.add_argument("--so-topo", default=SO_TOPO_DEFAULT,
                    help="scale-out .topo basename (carries no traffic under single-domain isolation)")
    ap.add_argument("--reduce-compute", type=int, default=0,
                    help="INC in-switch ALU latency (ns); DEFAULT 0 = charge-neither. >0 = sensitivity")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--tmpdir", default="/tmp/scaleup_ar_bandwidth")
    ap.add_argument("--validate", action="store_true",
                    help="generate + compile both INC arms, NO sim")
    args = ap.parse_args()

    goal.require_txt2bin()
    goal.require_generator()  # fail on a broken GENERATOR_DIR before any filesystem writes
    sizes = [int(x) for x in args.sizes.split(",")]

    if args.validate:
        sys.exit(validate(args.n, sizes[0], args.tmpdir))

    sim.require_simulator()
    if args.su_topo is None:
        sys.exit("--su-topo required for a sim run (e.g. "
                 "scaleup_single_switch_64_4000Gbps.topo); or use --validate for the no-sim check")
    sys.exit(run_exp(args.n, sizes, paths.topo(args.su_topo), paths.topo(args.so_topo),
                     args.reduce_compute, args.tmpdir, args.timeout))


if __name__ == "__main__":
    main()
