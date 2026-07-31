#!/usr/bin/env python3
"""INC-vs-endpoint A/B sweep for AllReduce / ReduceScatter / AllGather, isolated
to a single scale-up domain, on the pcm-sdk two-tier simulator.

This is the GENERATOR-FAITHFUL successor to ../allreduce_ab_pcm (whose ring arm
was hand-rolled in Python). Design decisions locked with the user 2026-07-20
(see AA-plan-Scaleup-Baselines):

  * Q1 Baseline = the GENERATOR's own decomposition. The endpoint (baseline) arm
    is emitted by calling nccl_generator_v2's communication.py directly
    (Communicator + {AllReduce,ReduceScatter,AllGather}.to_goal) -- the exact
    Ring / Recursive-doubling step sequences the ATLAHS GOAL generator produces
    for real workloads (NCCL algorithms per Demystifying-NCCL Tables V-VII). No
    hand-roll.
  * Q2 Charge NEITHER arm for reduction compute. The INC arm runs
    -reduce_compute_latency 0; the generator's synthetic decomposition emits no
    reduction `calc`. Both treat reduction arithmetic as free (same total work
    either way); the A/B isolates DATA MOVEMENT + STEP COUNT, which is what INC
    changes. The ALU charge stays available as a sensitivity knob (--reduce-compute).
  * Q3 AllReduce runs against BOTH baselines: --algo ring and --algo rdouble.
    ReduceScatter / AllGather are ring-only (generator default). rdouble is
    power-of-two N only.

Isolation: all N ranks live in node 0 of ONE scale-up domain (-nodes N
-num_gpus_per_node N), so the whole collective -- INC or decomposed -- runs
intranode with no scale-out flows. A single domain is also free of the
shared-intranode-topology aliasing that clouds the multi-domain runs.

TOPOLOGY IS DEFERRED. --su-topo defaults to the single-switch crossbar purely so
generation/compile/one smoke run can be validated; the RESULT-producing sweep
waits on the scale-up-topology decision (crossbar vs NVL72-style tree vs the
emerging NVLink5/UALink/SUE set). Use --validate to check generation + step
counts + compile WITHOUT running the simulator (fully topology-independent).

GOTCHA (inherited from ../allreduce_ab_pcm, verified in pcm-sdk source): the
.topo link speed sets only the fabric PIPES; the per-GPU NIC injection rate is
-intranode_linkspeed (Mbps), which DEFAULTS to 200 Gbps COPY_ENG and silently
caps every p2p arm. This driver ALWAYS passes -intranode_linkspeed (default
4000000 = pins the NIC frame time to the pipes' 2 ps/B realised 492.3 B/ns). The
ACK-less INC datapath bypasses the NIC pacer regardless.

Examples:
  python3 run_coll_ab_sweep.py --validate                       # no sim, all ops
  python3 run_coll_ab_sweep.py --collective reduce_scatter --n 8 --out rs.csv
  python3 run_coll_ab_sweep.py --collective allreduce --algo rdouble --n 8
"""
import argparse
import csv
import os
import re
import subprocess
import sys

TAIL_NS = 100  # identical dependent calc tail in both arms; cancels in the A/B

# --- import the generator's own decomposition code (Q1) -----------------------
GEN_DIR = os.path.expanduser(
    "~/CLionProjects/atlahs/goal_gen/ai/nccl_generator_v2")
sys.path.insert(0, GEN_DIR)
try:
    from communication import (Communicator, CollDevice,  # noqa: E402
                               AllReduce, ReduceScatter, AllGather, CollAlgo)
    from goal import (GoalOpAtom, GoalSend, GoalRecv, GoalCalc,  # noqa: E402
                      GoalCollective)
except ImportError as e:
    sys.exit(f"cannot import the nccl_generator_v2 decomposition from {GEN_DIR}: {e}\n"
             "Is the goal_gen/ai/nccl_generator_v2 submodule checked out (branch simple-sim-coll)?")

COLL_CLS = {"allreduce": AllReduce, "reduce_scatter": ReduceScatter, "allgather": AllGather}
ALGO = {"ring": CollAlgo.RING, "rdouble": CollAlgo.RECURSIVE_DOUBLING}
# Sanity: every INC kind we emit must be a rootless coll grammar kind.
for _k in COLL_CLS:
    assert _k in GoalCollective.KINDS and not GoalCollective.KINDS[_k], _k


def _reset_goal_state():
    """goal.py keeps per-rank label + per-(src,dst,ctx) message-id counters in
    class-level dicts; clear them between files so labels/tags restart cleanly."""
    GoalOpAtom.task_id_for_rank.clear()
    GoalSend.send_message_id.clear()
    GoalRecv.recv_message_id.clear()


def gen_baseline_goal(path, n, size, collective, algo):
    """Endpoint baseline via the generator's OWN decomposition (Q1).

    Constructs an N-rank Communicator and calls the collective's .to_goal per
    rank -- byte-for-byte the send/recv steps communication.py emits inside the
    real pipeline -- then appends an identical TAIL calc depending on the
    collective's completion. No reduction `calc` (Q2)."""
    _reset_goal_state()
    devices = [CollDevice(("gpu", i)) for i in range(n)]
    comm = Communicator(devices, "coll_ab")
    device2goal_rank = {d: i for i, d in enumerate(devices)}
    op = COLL_CLS[collective](comm, size=size, context=0, algo=ALGO[algo])
    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for device, rank in device2goal_rank.items():
            with device:
                coll_goal = op.to_goal(device2goal_rank, starting_cpu_id=0, nic=0)
                coll_lines = list(coll_goal.generate_lines())
                end_id = coll_goal.get_end_id()
                tail = GoalCalc(TAIL_NS, self_rank=rank, cpu=0)
                tail_lines = list(tail.generate_lines())  # allocates tail id AFTER coll
                tail_start = tail.get_start_id()
            # NB flush-left, no indentation: the txt2bin lexer anchors labels at
            # line start (indented `l0:` is a parse error) -- matches gen_inc_goal.
            f.write(f"rank {rank} {{\n")
            for ln in coll_lines:
                f.write(f"{ln}\n")
            for ln in tail_lines:
                f.write(f"{ln}\n")
            f.write(f"l{tail_start} requires l{end_id}\n")
            f.write("}\n\n")


def gen_inc_goal(path, groups_path, n, size, kind):
    """INC arm: one first-class rootless `coll <kind>` per rank + .groups sidecar.

    Line format matches goal.GoalCollective.generate_lines (group 0, instance 0,
    root -1 == ANYSOURCE), validated against txt2bin by goal.py's self-test."""
    assert kind in GoalCollective.KINDS and not GoalCollective.KINDS[kind]
    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for r in range(n):
            f.write(f"rank {r} {{\n")
            f.write(f"l1: coll {kind} {size}b 0 0 -1 cpu 1 nic 1\n")
            f.write(f"l2: calc {TAIL_NS} cpu 0\n")
            f.write("l2 requires l1\n")
            f.write("}\n\n")
    with open(groups_path, "w") as f:
        f.write(" ".join(str(i) for i in range(n)) + "\n")


# --- analytic ideal reference, per collective ---------------------------------
# Bandwidth-optimal, line-rate, zero-CC lower bound at the REALISED wire rate.
# Per-rank bytes moved: AllReduce 2(N-1)/N * S (RS phase + AG phase); ReduceScatter
# and AllGather each (N-1)/N * S. Plus (N-1) serial dependency hops. rate 492.3 B/ns
# = engine's 2 ps/B x 4096/4160 MTU framing; crossbar one-way hop 1300 ns.
IDEAL_RATE_BNS = 492.3
IDEAL_HOP_ONEWAY_NS = 1300


def ideal_ns(collective, n, s, rate_bns=IDEAL_RATE_BNS, hop_oneway_ns=IDEAL_HOP_ONEWAY_NS):
    factor = 2.0 * (n - 1) / n if collective == "allreduce" else (n - 1) / n
    return factor * s / rate_bns + (n - 1) * hop_oneway_ns


# --- expected decomposition step counts (Demystifying-NCCL Tables V-VII) ------
def expected_steps(collective, n, algo):
    """Number of parallel send/recv rounds the generator must emit per rank."""
    if collective == "allreduce":
        return 2 * (n.bit_length() - 1) if algo == "rdouble" else 2 * (n - 1)
    return n - 1  # ring RS / AG


MAXFIN = re.compile(r"Maximum finishing time at host \d+:\s*(\d+)")
DROP = re.compile(r"drop arriving|drop last from queue|dropped packet|"
                  r"Random Drop|Buffer Drop|Dropping packet|LOSSLESS not working",
                  re.IGNORECASE)

DEF_SIM = ("/Users/wstaempfli/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/"
           "pcm/build/bin/htsim_flow_app_atlahs")
DEF_WRITER = os.path.expanduser("~/CLionProjects/LogGOPSim-1.1-coll/txt2bin")
DEF_SU_TOPO = ("/Users/wstaempfli/CLionProjects/atlahs/sim/htsim-backend/sim/"
               "datacenter/topologies/scaleup_single_switch_{n}_3600Gbps.topo")
DEF_SO_TOPO = ("/Users/wstaempfli/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/"
               "pcm/apps/htsim_atlahs/example_incast/tree16.topo")


def compile_goal(writer, goal, binout):
    r = subprocess.run([writer, "-i", goal, "-o", binout], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"txt2bin failed on {goal}: {r.stderr[-300:]}")


def run(sim, binpath, so_topo, su_topo, n, groups=None, reduce_compute=0,
        timeout=300, intranode_linkspeed_mbps=4000000):
    cmd = [sim, "-goal", binpath, "-nodes", str(n), "-num_gpus_per_node", str(n),
           "-topo", so_topo, "-intranode_topo", su_topo,
           "-intranode_linkspeed", str(intranode_linkspeed_mbps),
           "-end", "100000000", "-sender_cc_only",
           "-intranode_queue_type", "lossless_input"]
    if groups:
        cmd += ["-groups", groups]
    if reduce_compute:
        cmd += ["-reduce_compute_latency", str(reduce_compute)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, -1, "timeout"
    combined = p.stdout + "\n" + p.stderr
    drops = len(DROP.findall(combined))
    fin = MAXFIN.search(p.stdout)
    status = "ok" if p.returncode == 0 else f"rc={p.returncode}"
    return (int(fin.group(1)) if fin else None), drops, status


def count_steps(goal_path):
    """Count send (== recv) rounds in rank 0's block of a generated baseline .goal."""
    sends = 0
    in_rank0 = False
    with open(goal_path) as f:
        for line in f:
            s = line.strip()
            if s.startswith("rank 0 {"):
                in_rank0 = True
            elif in_rank0 and s == "}":
                break
            elif in_rank0 and re.search(r":\s*send ", s):
                sends += 1
    return sends


def validate(args):
    """Topology-independent: generate every arm, check step counts vs the paper,
    compile with txt2bin. No simulator run."""
    os.makedirs(args.tmpdir, exist_ok=True)
    n, s = args.n, int(args.sizes.split(",")[0])
    plan = [("allreduce", "ring"), ("allreduce", "rdouble"),
            ("reduce_scatter", "ring"), ("allgather", "ring")]
    print(f"validation @ N={n}, size={s} (no sim run)\n")
    print(f"{'collective':>16} {'algo':>8} {'steps':>6} {'expect':>7} {'compile':>8}")
    all_ok = True
    for coll, algo in plan:
        if algo == "rdouble" and (n & (n - 1)):
            print(f"{coll:>16} {algo:>8}    skip (N not power of 2)")
            continue
        base = os.path.join(args.tmpdir, f"base_{coll}_{algo}_{n}_{s}.goal")
        inc = os.path.join(args.tmpdir, f"inc_{coll}_{n}_{s}.goal")
        grp = inc[:-5] + ".groups"
        gen_baseline_goal(base, n, s, coll, algo)
        gen_inc_goal(inc, grp, n, s, coll)
        got = count_steps(base)
        exp = expected_steps(coll, n, algo)
        compile_goal(args.writer, base, base[:-5] + ".bin")
        compile_goal(args.writer, inc, inc[:-5] + ".bin")
        ok = (got == exp)
        all_ok &= ok
        print(f"{coll:>16} {algo:>8} {got:>6} {exp:>7} {'OK' if ok else 'MISMATCH':>8}"
              f"{'  <-- step count wrong' if not ok else ''}")
    print("\nvalidation:", "PASS" if all_ok else "FAIL (step-count mismatch)")
    return 0 if all_ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collective", choices=list(COLL_CLS), default="allreduce")
    ap.add_argument("--algo", choices=list(ALGO), default="ring",
                    help="baseline decomposition algorithm; rdouble is AllReduce-only, power-of-2 N")
    ap.add_argument("--sim", default=DEF_SIM)
    ap.add_argument("--writer", default=DEF_WRITER)
    ap.add_argument("--su-topo", default=None,
                    help="scale-up .topo; default = single-switch crossbar for N (PLACEHOLDER "
                         "until the topology decision — this is not the committed topology)")
    ap.add_argument("--so-topo", default=DEF_SO_TOPO)
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--sizes", default="4096,16384,65536,262144,1048576,4194304")
    ap.add_argument("--reduce-compute", type=int, default=0,
                    help="INC in-switch aggregation latency (ns); DEFAULT 0 (charge-neither, Q2). "
                         "Set >0 as a sensitivity study.")
    ap.add_argument("--analytic-rate-bns", type=float, default=IDEAL_RATE_BNS)
    ap.add_argument("--hop-oneway-ns", type=int, default=IDEAL_HOP_ONEWAY_NS)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--intranode-linkspeed", type=int, default=4000000)
    ap.add_argument("--tmpdir", default="/tmp/coll_ab_pcm")
    ap.add_argument("--out", default=None)
    ap.add_argument("--validate", action="store_true",
                    help="generate all arms, check step counts vs the paper, compile — NO sim run")
    args = ap.parse_args()

    if not (os.path.isfile(args.writer) and os.access(args.writer, os.X_OK)):
        sys.exit(f"txt2bin not executable: {args.writer}")

    if args.validate:
        sys.exit(validate(args))

    if args.algo == "rdouble" and args.collective != "allreduce":
        sys.exit("rdouble is AllReduce-only; RS/AG are ring")
    if args.algo == "rdouble" and (args.n & (args.n - 1)):
        sys.exit(f"rdouble requires power-of-2 N (got {args.n})")
    if not (os.path.isfile(args.sim) and os.access(args.sim, os.X_OK)):
        sys.exit(f"simulator not executable: {args.sim}\n"
                 "(topology-gated run — build/point --sim at the pcm-sdk htsim_flow_app_atlahs)")

    su_topo = args.su_topo or DEF_SU_TOPO.format(n=args.n)
    coll, algo, n = args.collective, args.algo, args.n
    out = args.out or f"results_{coll}_{algo}.csv"
    os.makedirs(args.tmpdir, exist_ok=True)

    rows = []
    print(f"{coll} baseline={algo} N={n}  su_topo={os.path.basename(su_topo)}")
    print(f"{'size':>10} {'INC ns':>10} {'BASE ns':>10} {'speedup':>8} {'vs_ideal':>9}  status")
    for s in (int(x) for x in args.sizes.split(",")):
        if s % n:
            print(f"{s:>10}  skip (not divisible by N)")
            continue
        base = os.path.join(args.tmpdir, f"base_{coll}_{algo}_{s}.goal")
        inc = os.path.join(args.tmpdir, f"inc_{coll}_{s}.goal")
        grp = inc[:-5] + ".groups"
        gen_baseline_goal(base, n, s, coll, algo)
        gen_inc_goal(inc, grp, n, s, coll)
        compile_goal(args.writer, base, base[:-5] + ".bin")
        compile_goal(args.writer, inc, inc[:-5] + ".bin")

        ifin, idrop, ist = run(args.sim, inc[:-5] + ".bin", args.so_topo, su_topo, n,
                               groups=grp, reduce_compute=args.reduce_compute,
                               timeout=args.timeout,
                               intranode_linkspeed_mbps=args.intranode_linkspeed)
        bfin, bdrop, bst = run(args.sim, base[:-5] + ".bin", args.so_topo, su_topo, n,
                               timeout=args.timeout,
                               intranode_linkspeed_mbps=args.intranode_linkspeed)
        inc_ns = ifin - TAIL_NS if ifin else None
        base_ns = bfin - TAIL_NS if bfin else None
        ok = inc_ns and base_ns and ist == "ok" and bst == "ok"
        speedup = base_ns / inc_ns if ok else None
        ideal = ideal_ns(coll, n, s, args.analytic_rate_bns, args.hop_oneway_ns)
        vs_ideal = ideal / inc_ns if inc_ns else None
        status = "ok" if ok else f"inc={ist},base={bst}"
        if idrop or bdrop:
            status += f" WARN drops={idrop + bdrop}"
        rows.append({
            "collective": coll, "baseline_algo": algo, "group_size": n, "msg_bytes": s,
            "inc_ns": inc_ns or "", "base_ns": base_ns or "",
            "ideal_ns": f"{ideal:.1f}",
            "speedup": f"{speedup:.3f}" if speedup else "",
            "speedup_vs_ideal": f"{vs_ideal:.3f}" if vs_ideal else "",
            "inc_makespan_ns": ifin or "", "base_makespan_ns": bfin or "",
            "drops": idrop + bdrop, "fabric": "lossless_input",
            "reduce_compute_ns": args.reduce_compute,
            "su_topo": os.path.basename(su_topo),
            "analytic_rate_bns": args.analytic_rate_bns, "hop_oneway_ns": args.hop_oneway_ns,
            "intranode_linkspeed_mbps": args.intranode_linkspeed, "engine": "pcm-sdk",
        })
        print(f"{s:>10} {str(inc_ns):>10} {str(base_ns):>10} "
              f"{(f'{speedup:.2f}x' if speedup else '-'):>8} "
              f"{(f'{vs_ideal:.2f}x' if vs_ideal else '-'):>9}  {status}")

    if rows:
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
