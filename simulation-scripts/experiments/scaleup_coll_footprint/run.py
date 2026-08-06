#!/usr/bin/env python3
"""Multicast network-bandwidth-usage-reduction (footprint) A/B, on the pcm-sdk
two-tier simulator, scale-up domain in isolation.

Reproduces (and extends) Khalilov et al. SC24 Fig. 2 ("relative bandwidth usage
reduction with multicast") as a SIMULATOR-MEASURED result, using the ported PT6
per-link footprint counter (-link_crosses_csv):

    ratio = footprint_baseline / footprint_INC          (y-axis, a multiplier >= 1)
    x-axis = number of participating GPUs P

The INC arm (one rootless `coll <kind>` op + a .groups sidecar) is the bandwidth-
optimal multicast/aggregation datapath; the baseline arm is the generator's OWN
Ring / Recursive-Doubling decomposition. The INC footprint is algorithm-independent,
so per (collective, P, topology) we run ONE INC arm and one baseline per algorithm;
Ring and RD share the same INC denominator (exactly the paper's two-bar structure).

Two thesis topology classes:
  * single-switch (one 64-host crossbar): every pair is 2 hops, so Ring == RD ==
    2-2/P (flat plateau at 2x). The constant-hop control.
  * 3-tier fat-tree (one 256-host tree, 4 hosts/leaf x 4 leaves/pod x 16 pods):
    3 hop regimes (2/4/6), so RD's late-step messages cross tiers -> RD diverges
    from Ring and climbs with P. Reproduces the paper's shape.

Group size P is swept via the TRACE, not the topology: gpus_per_node is pinned to
the topo host WIDTH and nodes=2 (idle scale-out); P participating ranks land on
hosts 0..P-1 and the rest of the fabric registers zero crosses. Validated
byte-for-byte against the exact-width recipe (mode Z), see _validate_footprint.py.

HEADLINE METRIC = byte-ratio. Packet-cross ratio is inflated by transport control
traffic (ACK/pull ~64 B): the INC multicast arm has ~zero control crossings, the
P2P arm has ~1 per data packet. In BYTES that control traffic is <~2%, so the byte-
ratio tracks the analytic data-movement 2-2/P; the packet-ratio does not. Both are
recorded; plots use bytes.

Message size is MTU-aligned (-mtu 4160 -> 4096 B payload; per-rank chunk = size/P):
size = P * 4096 * mult makes every chunk exactly `mult` full packets, removing MTU
ceil-quantization drift. The footprint ratio is size-independent (paper's "N cancels")
so mult=1 is the cheap default; --size-mults sweeps a control to show the plateau.

Reproduce (Docker, sim-only image; the pcm binary is a Linux build):
  docker run --rm --user "$(id -u):$(id -g)" -v $(pwd):/workspace atlahs-sim build
  docker run --rm --user "$(id -u):$(id -g)" -v $(pwd):/workspace atlahs-sim run scaleup_coll_footprint --validate
  simulation-scripts/experiments/scaleup_coll_footprint/run_thesis.sh
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import goal, paths, report, sim  # noqa: E402

EXP_NAME = "scaleup_coll_footprint"
TAIL_NS = 100
MTU = 4160
PAYLOAD = 4096  # MTU - 64 B header
NODES = 2       # mode Z: small idle scale-out; P ranks all land in node 0
SO_TOPO = "tree16_bw200Gbps.topo"  # idle scale-out, only needs >= NODES hosts

OUTPUT_DIR = os.environ.get("FOOTPRINT_OUTPUT_DIR", paths.results_dir(EXP_NAME))

# One fixed topology per class; P swept via the trace (partial population).
TOPOS = [
    {"cls": "single_switch", "topo": "scaleup_single_switch_64_4000Gbps.topo",
     "width": 64, "P": [2, 4, 8, 16, 32, 64]},
    {"cls": "fat3tier", "topo": "scaleup_3tier_256_4000Gbps.topo",
     "width": 256, "P": [2, 4, 8, 16, 32, 64, 128, 256]},
    # Direct reproduction of Khalilov Fig. 2's topology: radix-32 3-tier, 1024 hosts
    # (leaf=16, pod=256). Ring -> ~2x, RD -> ~3.6x at P=1024, per the paper.
    {"cls": "paper_r32", "topo": "scaleup_ft_radix32_1024_4000Gbps.topo",
     "width": 1024, "P": [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]},
]

# INC kind == collective (rootless apex primitive). Each collective compares its INC
# footprint against Ring and Recursive-Doubling P2P baselines.
COLL_CASES = [
    {"collective": "allgather",      "algos": ["ring", "rdouble"]},  # the paper's Fig. 2 collective
    {"collective": "allreduce",      "algos": ["ring", "rdouble"]},
    {"collective": "reduce_scatter", "algos": ["ring", "rdouble"]},
    {"collective": "bcast",          "algos": ["ring"]},  # rooted; ring-only baseline vs INC mcast
    {"collective": "reduce",         "algos": ["ring"]},  # rooted; ring-only baseline vs INC aggregation
]

INTRANODE_LINKSPEED = sim.INTRANODE_LINKSPEED_DEFAULT

CSV_FIELDS = ["topology_class", "su_topo", "collective", "baseline_algo",
              "group_size", "msg_bytes", "size_mult",
              "crosses_inc", "crosses_baseline", "bytes_inc", "bytes_baseline",
              "ratio_crosses", "ratio_bytes", "analytic_ratio",
              "inc_makespan_ns", "base_makespan_ns", "drops",
              "inc_status", "base_status", "so_topo", "nodes", "gpus_per_node",
              "mtu", "intranode_linkspeed_mbps", "engine", "command"]


def analytic_ratio(collective, cls, p):
    """The `2-2/P` Ring reference (Khalilov Fig. 2). Exact for AllGather on a single
    switch (every pair 2 hops, Ring==RD); on multi-tier it is the nearest-neighbour Ring
    reference (measured Ring should sit on it, RD climbs above). Blank for reduce ops."""
    if collective == "allgather":
        return f"{2 - 2 / p:.4f}"
    return ""


def _gen_baseline_safe(path, n, size, collective, algo_name):
    """Some (collective, algo) combos may be unimplemented in the generator
    (e.g. a TREE algo). Returns True on success, False if the generator refuses."""
    try:
        goal.gen_baseline_goal(path, n, size, collective, algo_name, TAIL_NS)
        return True
    except (NotImplementedError, AssertionError, KeyError) as e:
        report.print_warning(f"generator has no {collective}/{algo_name}: {e}")
        return False


def run_exp(topos, collectives, size_mults, tmpdir, timeout):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(tmpdir, exist_ok=True)
    csv_path = os.path.join(OUTPUT_DIR, f"{EXP_NAME}.csv")
    cases = [c for c in COLL_CASES if c["collective"] in collectives]
    failures = 0
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        for topo in topos:
            cls, su_name, width = topo["cls"], topo["topo"], topo["width"]
            su_topo = paths.topo(su_name)
            so_topo = paths.topo(SO_TOPO)
            for p in topo["P"]:
                if p > width:
                    continue
                for mult in size_mults:
                    size = p * PAYLOAD * mult  # per-rank chunk = mult full packets
                    for case in cases:
                        coll = case["collective"]
                        inc_root = 0 if coll in ("bcast", "reduce") else -1  # rooted INC arm
                        report.print_info(f"=== {cls} P={p} size={size} {coll} ===")
                        # INC arm (once; shared denominator across baseline algos)
                        inc = os.path.join(tmpdir, f"inc_{cls}_{coll}_{p}_{mult}.goal")
                        grp = inc[:-5] + ".groups"
                        goal.gen_inc_goal(inc, grp, p, size, coll, TAIL_NS, root=inc_root)
                        goal.compile_goal(inc, inc[:-5] + ".bin")
                        imk, ic, ib, idr, ist, icmd = sim.run_sim_footprint(
                            inc[:-5] + ".bin", so_topo, su_topo,
                            nodes=NODES, gpus_per_node=width, groups=grp,
                            mtu=MTU, timeout=timeout,
                            intranode_linkspeed=INTRANODE_LINKSPEED)
                        for algo in case["algos"]:
                            if algo == "rdouble" and (p & (p - 1)):
                                continue  # RD needs power-of-two P
                            base = os.path.join(tmpdir, f"base_{cls}_{coll}_{algo}_{p}_{mult}.goal")
                            if not _gen_baseline_safe(base, p, size, coll, algo):
                                continue
                            goal.compile_goal(base, base[:-5] + ".bin")
                            bmk, bc, bb, bdr, bst, _ = sim.run_sim_footprint(
                                base[:-5] + ".bin", so_topo, su_topo,
                                nodes=NODES, gpus_per_node=width, mtu=MTU,
                                timeout=timeout, intranode_linkspeed=INTRANODE_LINKSPEED)
                            rc = f"{bc / ic:.4f}" if (ic and bc) else ""
                            rb = f"{bb / ib:.4f}" if (ib and bb) else ""
                            ok = bool(imk and bmk and ic and bc and ib and bb
                                      and ist == "ok" and bst == "ok"
                                      and idr == 0 and bdr == 0)
                            if not ok:
                                failures += 1
                            out.write({
                                "topology_class": cls, "su_topo": su_name,
                                "collective": coll, "baseline_algo": algo,
                                "group_size": p, "msg_bytes": size, "size_mult": mult,
                                "crosses_inc": ic or "", "crosses_baseline": bc or "",
                                "bytes_inc": ib or "", "bytes_baseline": bb or "",
                                "ratio_crosses": rc, "ratio_bytes": rb,
                                "analytic_ratio": analytic_ratio(coll, cls, p),
                                "inc_makespan_ns": imk or "", "base_makespan_ns": bmk or "",
                                "drops": idr + bdr, "inc_status": ist, "base_status": bst,
                                "so_topo": SO_TOPO, "nodes": NODES, "gpus_per_node": width,
                                "mtu": MTU, "intranode_linkspeed_mbps": INTRANODE_LINKSPEED,
                                "engine": "pcm-sdk", "command": icmd,
                            })
                            st = "ok" if ok else f"inc={ist},base={bst}"
                            print(f"  {coll:>14} {algo:>7} P={p:>4} m={mult}: "
                                  f"ratio_bytes={rb or '-':>7}  (crosses {ic}->{bc})  {st}")
    if failures:
        report.print_error(f"{failures} simulation cell(s) failed; partial CSV: {csv_path}")
        return 1
    report.print_success(f"wrote results to {csv_path}")
    return 0


def validate(tmpdir):
    """No-sim: generate every (collective, algo) arm at P=8 and compile, so a broken
    generator / txt2bin fails before a sim sweep."""
    os.makedirs(tmpdir, exist_ok=True)
    report.print_info("validation @ P=8 (no sim): generate + compile every arm")
    ok = True
    p, size = 8, 8 * PAYLOAD
    for case in COLL_CASES:
        coll = case["collective"]
        inc_root = 0 if coll in ("bcast", "reduce") else -1
        inc = os.path.join(tmpdir, f"v_inc_{coll}.goal")
        goal.gen_inc_goal(inc, inc[:-5] + ".groups", p, size, coll, TAIL_NS, root=inc_root)
        goal.compile_goal(inc, inc[:-5] + ".bin")
        for algo in case["algos"]:
            base = os.path.join(tmpdir, f"v_base_{coll}_{algo}.goal")
            gen_ok = _gen_baseline_safe(base, p, size, coll, algo)
            if gen_ok:
                goal.compile_goal(base, base[:-5] + ".bin")
            print(f"  {coll:>14} INC=OK  {algo:>7}={'OK' if gen_ok else 'UNSUPPORTED'}")
            ok &= gen_ok
    print("validation:", "PASS" if ok else "PARTIAL (some baseline algos unsupported)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topos", default="single_switch,fat3tier,paper_r32",
                    help="comma list of topology classes to run (paper_r32 = radix-32 1024-host "
                         "reproduction; its ring baseline at P=1024 is slow)")
    ap.add_argument("--collectives", default="allgather,allreduce,reduce_scatter,bcast,reduce")
    ap.add_argument("--size-mults", default="1",
                    help="comma list of per-rank chunk sizes in packets (1 = P*4096 B)")
    ap.add_argument("--max-p", type=int, default=None, help="cap group size (debug)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--tmpdir", default="/tmp/scaleup_coll_footprint")
    ap.add_argument("--validate", action="store_true", help="generate+compile only, no sim")
    args = ap.parse_args()

    goal.require_txt2bin()
    goal.require_generator()
    if args.validate:
        sys.exit(validate(args.tmpdir))

    sim.require_simulator()
    topo_sel = args.topos.split(",")
    topos = [t for t in TOPOS if t["cls"] in topo_sel]
    if args.max_p is not None:
        for t in topos:
            t["P"] = [p for p in t["P"] if p <= args.max_p]
    collectives = args.collectives.split(",")
    size_mults = [int(x) for x in args.size_mults.split(",")]
    sys.exit(run_exp(topos, collectives, size_mults, args.tmpdir, args.timeout))


if __name__ == "__main__":
    main()
