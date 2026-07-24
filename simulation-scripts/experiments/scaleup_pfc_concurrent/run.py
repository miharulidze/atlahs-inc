#!/usr/bin/env python3
"""PFC / lossless-backpressure validation: N concurrent disjoint INC AllReduces,
trees PINNED to one core vs. DISTRIBUTED round-robin, on the pcm-sdk engine.

The correctness precondition for in-network aggregation is losslessness: a dropped
packet corrupts a reduction irrecoverably. This experiment stresses that precondition
under adversarial load and shows PFC doing its job:

  * DISTRIBUTED (default placement): the N groups' multicast/aggregation trees spread
    round-robin across core switches, run independently -> completion flat in N.
  * PINNED (-mcast_pin 0): every tree is forced onto ONE aggregation position + core,
    so the N collectives contend at that single core; PFC backpressure SERIALISES them
    -> completion grows ~linearly in N. ZERO packet loss in both arms (lossless_input).

This is the pcm rendering of the fork's Fig 5.12 (retired with the single-engine
consolidation). It also exercises the post-fix real NIC-rate ingress datapath (M2)
under concurrent multicast fan-out.

Geometry (crux): each group must SPAN MULTIPLE PODS so its tree reaches the core tier
(pinning only bites at the core; a single-pod group tops out at the agg tier). On the
256-host 3-tier fat-tree (16 hosts/pod x 16 pods), group g = one host per pod at slot g
across the first PODS_SPANNED pods: {p*16 + g}. Groups are disjoint by slot, so N <=
hosts-per-pod (16); N also stays <= core count (16) so the distributed arm is genuinely
flat. Small-ish groups keep the pinned core the SOLE contended resource (clean
attribution). Metric = makespan (= the slowest group's completion); assert drops == 0.

Reproduce (Docker):
  docker run --rm -v $(pwd):/workspace atlahs-sim build   # MUST rebuild: adds -mcast_pin
  docker run --rm -v $(pwd):/workspace atlahs-sim run scaleup_pfc_concurrent --validate
  docker run --rm -v $(pwd):/workspace atlahs-sim run scaleup_pfc_concurrent
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import goal, paths, report, sim  # noqa: E402

EXP_NAME = "scaleup_pfc_concurrent"
TAIL_NS = 100

OUTPUT_DIR = os.environ.get("SCALEUP_OUTPUT_DIR", paths.results_dir(EXP_NAME))

SU_TOPO = "scaleup_3tier_256_4000Gbps.topo"   # multi-core 3-tier (pinning is meaningful)
SO_TOPO = "tree16_bw200Gbps.topo"             # idle scale-out (mode Z)
WIDTH = 256           # scale-up topo host width -> gpus_per_node
HOSTS_PER_POD = 16    # 3-tier 256: 16 hosts/pod x 16 pods
NODES = 2             # mode Z: small idle scale-out; all ranks land in node 0
PODS_SPANNED = 8      # group size = pods each group spans (>=2 to reach the core tier)
MSG_BYTES = 65536     # 64 KiB AllReduce per group (matches the retired fork Fig 5.12)
KIND = "allreduce"    # apex turn-around: fan-in up + multicast down (exercises both primitives)
N_SWEEP = [1, 2, 4, 8, 12, 16]  # concurrent groups; <= HOSTS_PER_POD (slots) and <= #cores

INTRANODE_LINKSPEED = sim.INTRANODE_LINKSPEED_DEFAULT

# Two placement arms. mcast_pin=-1 -> round-robin (default); 0 -> all trees on one core.
ARMS = [
    {"label": "distributed", "mcast_pin": -1},
    {"label": "pinned",      "mcast_pin": 0},
]

CSV_FIELDS = ["arm", "mcast_pin", "n_groups", "group_size", "pods_spanned",
              "hosts_per_pod", "msg_bytes", "slowest_ns", "makespan_ns", "drops",
              "status", "su_topo", "so_topo", "nodes", "gpus_per_node",
              "intranode_linkspeed_mbps", "engine", "log_file", "command"]


def make_groups(n_groups):
    """Group g = one host per pod (slot g) across the first PODS_SPANNED pods:
    {p*HOSTS_PER_POD + g : p in [0, PODS_SPANNED)}. Disjoint for g in [0, n_groups)."""
    return [[p * HOSTS_PER_POD + g for p in range(PODS_SPANNED)] for g in range(n_groups)]


NUM_RANKS = PODS_SPANNED * HOSTS_PER_POD   # covers pods 0..PODS_SPANNED-1 (max member id)


def validate(tmpdir):
    """Generate + compile both arms at N=4 (no sim). The two arms share the SAME trace
    (placement differs only by the -mcast_pin flag), so this checks trace synthesis."""
    os.makedirs(tmpdir, exist_ok=True)
    report.print_info(f"validation @ N=4 (no sim); num_ranks={NUM_RANKS}")
    groups = make_groups(4)
    g = os.path.join(tmpdir, "v_pfc_4.goal")
    goal.gen_multigroup_inc_goal(g, g[:-5] + ".groups", NUM_RANKS, groups, MSG_BYTES, KIND, TAIL_NS)
    goal.compile_goal(g, g[:-5] + ".bin")
    print(f"  groups(N=4): {groups}")
    print("  compiled OK  (both arms reuse this trace; placement = -mcast_pin flag only)")
    print("validation: PASS")
    return 0


def run_exp(n_sweep, su_topo, so_topo, tmpdir, timeout):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(tmpdir, exist_ok=True)
    csv_path = os.path.join(OUTPUT_DIR, f"{EXP_NAME}.csv")
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        for n in n_sweep:
            if n > HOSTS_PER_POD:
                report.print_warning(f"N={n}: skip (> {HOSTS_PER_POD} host-slots per pod)")
                continue
            groups = make_groups(n)
            g = os.path.join(tmpdir, f"pfc_{n}.goal")
            grp = g[:-5] + ".groups"
            # Both arms share ONE trace; only the -mcast_pin flag differs.
            goal.gen_multigroup_inc_goal(g, grp, NUM_RANKS, groups, MSG_BYTES, KIND, TAIL_NS)
            goal.compile_goal(g, g[:-5] + ".bin")
            for arm in ARMS:
                report.print_info(f"=== N={n} {arm['label']} (mcast_pin={arm['mcast_pin']}) ===")
                log = os.path.join(OUTPUT_DIR, "logs", f"{arm['label']}_{n}.log")
                os.makedirs(os.path.dirname(log), exist_ok=True)
                fin, drop, st, cmd = sim.run_sim(g[:-5] + ".bin", so_topo, su_topo,
                                                 nodes=NODES, gpus_per_node=WIDTH, groups=grp,
                                                 timeout=timeout,
                                                 intranode_linkspeed=INTRANODE_LINKSPEED,
                                                 mcast_pin=arm["mcast_pin"])
                with open(log, "w") as lf:
                    lf.write(cmd + "\n")
                ok = fin and st == "ok"
                out.write({
                    "arm": arm["label"], "mcast_pin": arm["mcast_pin"], "n_groups": n,
                    "group_size": PODS_SPANNED, "pods_spanned": PODS_SPANNED,
                    "hosts_per_pod": HOSTS_PER_POD, "msg_bytes": MSG_BYTES,
                    "slowest_ns": fin or "", "makespan_ns": fin or "", "drops": drop,
                    "status": st, "su_topo": SU_TOPO, "so_topo": SO_TOPO, "nodes": NODES,
                    "gpus_per_node": WIDTH, "intranode_linkspeed_mbps": INTRANODE_LINKSPEED,
                    "engine": "pcm-sdk", "log_file": log, "command": cmd,
                })
                us = f"{fin/1000:.2f} us" if fin else "-"
                status = "ok" if ok else st
                if drop:
                    status += f"  WARN LOSSLESS-VIOLATION drops={drop}"
                print(f"  N={n:>3} {arm['label']:>11}  slowest {us:>10}  {status}")
    report.print_success(f"wrote results to {csv_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n-sweep", default=",".join(str(x) for x in N_SWEEP),
                    help="comma list of concurrent-group counts (each <= hosts-per-pod)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--tmpdir", default="/tmp/scaleup_pfc_concurrent")
    ap.add_argument("--validate", action="store_true", help="generate + compile only, no sim")
    args = ap.parse_args()

    goal.require_txt2bin()
    goal.require_generator()
    if args.validate:
        sys.exit(validate(args.tmpdir))

    sim.require_simulator()
    n_sweep = [int(x) for x in args.n_sweep.split(",")]
    run_exp(n_sweep, paths.topo(SU_TOPO), paths.topo(SO_TOPO), args.tmpdir, args.timeout)


if __name__ == "__main__":
    main()
