#!/usr/bin/env python3
"""INC-vs-ring AllReduce A/B sweep on the pcm-sdk two-tier simulator.

pcm-sdk counterpart of ../allreduce_ab/run_allreduce_ab_sweep.py (which drives the
htsim_uec fork). Same two arms, same fairness model, different engine:

  INC arm : each rank issues one in-network `coll allreduce` (switch reduces +
            multicasts; ACK-less line-rate collective sources). Charged
            -reduce_compute_latency (INC-only, conservative).
  ring arm: chunked bandwidth-optimal p2p ring — 2*(N-1) steps of size/N bytes,
            send_t requires recv_{t-1} (mirrors make_allreduce_ab.cpp's ring arm),
            over pcm-sdk's paced/pull UEC transport.

Both arms run on the SAME fabric: the single-switch NVLink-class scale-up topology
with -intranode_queue_type lossless_input (PFC). Both arms end in an identical
100 ns calc tail that depends on the collective, so

GOTCHA (verified in pcm-sdk source 2026-07-13): the .topo file's link speed sets only
the fabric PIPES; the per-GPU NIC injection rate is -intranode_linkspeed, which
DEFAULTS to COPY_ENG = 200,000 Mbps = 200 Gbps (main.h -> htsim_app_atlahs.cpp ->
atlahs_htsim_api.cpp). UecNIC::startSending then holds the port 166 ns per 4,150 B
frame = 24.6 payload-B/ns = 5% of the fabric's realised 492.3 B/ns, silently capping
any p2p arm whose per-step block exceeds one frame (effective MSS 4,086 B payload /
4,150 B frame). This driver therefore ALWAYS passes -intranode_linkspeed. The flag
also rescales base_rtt, BDP, cwnd and the LogGOPS htsim_G gate, so results must be
re-measured, never extrapolated, when it changes. The ACK-less INC datapath bypasses
the NIC pacer (measured slope 492.3 B/ns regardless of the flag).

NIC PINNED TO THE PIPES' REALISED RATE (default 4000000 Mbps): the fabric pipes
quantise the .topo's 3,600 Gbps to 2 ps/B (realised 492.3 payload-B/ns), but the NIC
pacer does exact Mbps arithmetic (at 3600000: 4,150x8/3.6e12 = 9.22 ns/frame = 443
payload-B/ns, ~10% under the pipes). Passing 4000000 makes the NIC frame time
4,150x8/4e12 = 8.30 ns = exactly the pipes' 4,150 x 2 ps: ONE wire rate everywhere.
The .topo keeps its 3,600 Gbps declaration (same quantised wire).
    collective time = "Maximum finishing time at host 0" - TAIL_NS
(for the INC arm this equals ALLREDUCE_COMPLETE's duration_ns; asserted per run).

The traces are emitted as .goal TEXT and compiled with the coll-extended
LogGOPSim 1.1 txt2bin (tools/loggopsim-coll), i.e. the exact pipeline a real
generator-produced trace would take.

Example:
  python3 run_pcm_ab_sweep.py --out results.csv
"""
import argparse
import csv
import os
import re
import subprocess
import sys

TAIL_NS = 100  # identical dependent calc tail in both arms; cancels in the A/B

# [D3] analytic ideal-ring reference (matches ../allreduce_ab/run_allreduce_ab_sweep.py).
# Priced at the REALISED payload-effective wire rate, NOT the nominal 3600 Gbps: the
# engine quantises per-byte time to 2 ps (500 B/ns raw) x 4096/4160 MTU framing =>
# 492.3 B/ns, == the ACK-less INC completion-vs-size slope. Crossbar one-way hop 1300 ns
# (500 link + 300 switch + 500 link); the two-level fabric is 2900 ns (4*500 + 3*300).
IDEAL_RING_RATE_BNS = 492.3
IDEAL_RING_HOP_ONEWAY_NS = 1300


def ideal_ring_ns(N, S, rate_bns=IDEAL_RING_RATE_BNS, hop_oneway_ns=IDEAL_RING_HOP_ONEWAY_NS):
    """Analytic bandwidth-optimal, line-rate, zero-CC ring AllReduce lower bound [D3].

    2(N-1)/N cost model at the REALISED wire rate: each rank moves 2(N-1)/N * S bytes at
    rate_bns B/ns, plus (N-1) serial dependency hops at hop_oneway latency. Computed (NOT
    hand-injected); the measured ring must not beat it, and speedup_vs_ideal =
    ideal_ring_ns/inc_ns is the CC-decontaminated INC-vs-perfect-ring speedup.
    """
    bw_ns = (2.0 * (N - 1) / N) * S / rate_bns
    lat_ns = (N - 1) * hop_oneway_ns
    return bw_ns + lat_ns


MAXFIN = re.compile(r"Maximum finishing time at host \d+:\s*(\d+)")
ALLRED = re.compile(r"ALLREDUCE_COMPLETE\b.*?duration_ns=(\d+)")
DROP = re.compile(r"drop arriving|drop last from queue|dropped packet|"
                  r"Random Drop|Buffer Drop|Dropping packet|LOSSLESS not working",
                  re.IGNORECASE)

DEF_SIM = ("/Users/wstaempfli/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/"
           "pcm/build/bin/htsim_flow_app_atlahs")
DEF_WRITER = os.path.expanduser("~/CLionProjects/LogGOPSim-1.1-coll/txt2bin")
DEF_SU_TOPO = ("/Users/wstaempfli/CLionProjects/atlahs/sim/htsim-backend/sim/"
               "datacenter/topologies/scaleup_single_switch_16_3600Gbps.topo")
DEF_SO_TOPO = ("/Users/wstaempfli/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/"
               "pcm/apps/htsim_atlahs/example_incast/tree16.topo")


def gen_inc_goal(path, groups_path, n, size):
    """One in-network allreduce per rank + the .groups sidecar."""
    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for r in range(n):
            f.write(f"rank {r} {{\n")
            f.write(f"l1: coll allreduce {size}b 0 0 -1 cpu 1 nic 1\n")
            f.write(f"l2: calc {TAIL_NS} cpu 0\n")
            f.write("l2 requires l1\n")
            f.write("}\n\n")
    with open(groups_path, "w") as f:
        f.write(" ".join(str(i) for i in range(n)) + "\n")


def gen_ring_goal(path, n, size):
    """Chunked bandwidth-optimal ring: 2(N-1) steps of size/N, forward-after-receive.

    Mirrors make_allreduce_ab.cpp's ring arm: rank r sends to (r+1)%N and receives
    from (r-1+N)%N each step; send_t requires recv_{t-1} (t>=1); tag = step index
    (offset +1 to avoid tag 0). Identical TAIL_NS calc requiring the last recv.
    """
    chunk = size // n
    assert chunk > 0, "size/N == 0"
    steps = 2 * (n - 1)
    with open(path, "w") as f:
        f.write(f"num_ranks {n}\n\n")
        for r in range(n):
            dst, pred = (r + 1) % n, (r - 1 + n) % n
            f.write(f"rank {r} {{\n")
            lbl = 0
            snd, rcv = [], []
            for t in range(steps):
                lbl += 1; snd.append(lbl)
                f.write(f"l{lbl}: send {chunk}b to {dst} tag {t+1}\n")
                lbl += 1; rcv.append(lbl)
                f.write(f"l{lbl}: recv {chunk}b from {pred} tag {t+1}\n")
            lbl += 1
            f.write(f"l{lbl}: calc {TAIL_NS} cpu 0\n")
            for t in range(1, steps):
                f.write(f"l{snd[t]} requires l{rcv[t-1]}\n")
            f.write(f"l{lbl} requires l{rcv[-1]}\n")
            f.write("}\n\n")


def compile_goal(writer, goal, binout):
    r = subprocess.run([writer, "-i", goal, "-o", binout],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"txt2bin failed on {goal}: {r.stderr[-300:]}")


def run(sim, binpath, so_topo, su_topo, n, groups=None, reduce_compute=0,
        timeout=300, intranode_linkspeed_mbps=4000000):
    # -end is parsed in MICROSECONDS; -intranode_linkspeed in Mbps (see GOTCHA above).
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
        return None, None, -1, "timeout"
    combined = p.stdout + "\n" + p.stderr
    drops = len(DROP.findall(combined))
    fin = MAXFIN.search(p.stdout)
    dur = ALLRED.search(p.stdout)
    status = "ok" if p.returncode == 0 else f"rc={p.returncode}"
    return (int(fin.group(1)) if fin else None,
            int(dur.group(1)) if dur else None, drops, status)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", default=DEF_SIM)
    ap.add_argument("--writer", default=DEF_WRITER)
    ap.add_argument("--su-topo", default=DEF_SU_TOPO)
    ap.add_argument("--so-topo", default=DEF_SO_TOPO)
    ap.add_argument("--analytic-rate-bns", type=float, default=IDEAL_RING_RATE_BNS,
                    help="realised payload-effective wire rate (B/ns) for the analytic "
                         "ideal ring; default 492.3 (2 ps/B x MTU framing == INC slope)")
    ap.add_argument("--hop-oneway-ns", type=int, default=IDEAL_RING_HOP_ONEWAY_NS,
                    help="one-way per-hop latency for the ideal-ring latency term "
                         "(1300 crossbar; 2900 two-level fabric)")
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--sizes", default="4096,16384,65536,262144,1048576,4194304")
    ap.add_argument("--reduce-compute", type=int, default=100,
                    help="INC-only in-switch aggregation latency (ns)")
    ap.add_argument("--timeout", type=int, default=300,
                    help="per-run wall-clock timeout (s); raise for very large payloads")
    ap.add_argument("--intranode-linkspeed", type=int, default=4000000,
                    help="scale-up per-GPU NIC injection rate in Mbps (see GOTCHA in the "
                         "module docstring); default 4000000 pins the NIC frame time to "
                         "the pipes' 2 ps/B quantised rate (8.30 ns/frame = 492.3 "
                         "payload-B/ns) — the engine default (200000 = 200 Gbps COPY_ENG) "
                         "silently caps p2p arms at 5%% of a 3600 Gbps fabric, and the "
                         "literal 3600000 paces ~10%% under the pipes (443 B/ns)")
    ap.add_argument("--tmpdir", default="/tmp/pcm_ar_ab")
    ap.add_argument("--out", default="results.csv")
    args = ap.parse_args()

    for tool in (args.sim, args.writer):
        if not (os.path.isfile(tool) and os.access(tool, os.X_OK)):
            sys.exit(f"not executable: {tool}")
    os.makedirs(args.tmpdir, exist_ok=True)

    n = args.n
    rows = []
    print(f"{'size':>10} {'INC ns':>10} {'RING ns':>10} {'speedup':>8}  status")
    for s in (int(x) for x in args.sizes.split(",")):
        if s % n:
            print(f"{s:>10}  skip (not divisible by N)")
            continue
        inc_goal = os.path.join(args.tmpdir, f"inc_{s}.goal")
        inc_bin = inc_goal[:-5] + ".bin"
        grp = inc_goal[:-5] + ".groups"
        ring_goal = os.path.join(args.tmpdir, f"ring_{s}.goal")
        ring_bin = ring_goal[:-5] + ".bin"
        gen_inc_goal(inc_goal, grp, n, s)
        gen_ring_goal(ring_goal, n, s)
        compile_goal(args.writer, inc_goal, inc_bin)
        compile_goal(args.writer, ring_goal, ring_bin)

        ifin, idur, idrop, ist = run(args.sim, inc_bin, args.so_topo, args.su_topo,
                                     n, groups=grp, reduce_compute=args.reduce_compute,
                                     timeout=args.timeout,
                                     intranode_linkspeed_mbps=args.intranode_linkspeed)
        rfin, _, rdrop, rst = run(args.sim, ring_bin, args.so_topo, args.su_topo, n,
                                  timeout=args.timeout,
                                  intranode_linkspeed_mbps=args.intranode_linkspeed)

        inc_ns = ifin - TAIL_NS if ifin else None
        ring_ns = rfin - TAIL_NS if rfin else None
        if inc_ns is not None and idur is not None and inc_ns != idur:
            print(f"  WARN: INC makespan-tail ({inc_ns}) != duration_ns ({idur})")
        ok = inc_ns and ring_ns and ist == "ok" and rst == "ok"
        speedup = ring_ns / inc_ns if ok else None
        # [D3] computed analytic ideal-ring reference + CC-decontaminated speedup.
        ideal_ns = ideal_ring_ns(n, s, args.analytic_rate_bns, args.hop_oneway_ns)
        speedup_vs_ideal = ideal_ns / inc_ns if inc_ns else None
        status = "ok" if ok else f"inc={ist},ring={rst}"
        if idrop or rdrop:
            status += f" WARN drops={idrop + rdrop}"
        rows.append({
            "group_size": n, "msg_bytes": s,
            "inc_ns": inc_ns or "", "ring_ns": ring_ns or "",
            "ideal_ring_ns": f"{ideal_ns:.1f}",
            "speedup": f"{speedup:.3f}" if speedup else "",
            "speedup_vs_ideal": f"{speedup_vs_ideal:.3f}" if speedup_vs_ideal else "",
            "inc_makespan_ns": ifin or "", "ring_makespan_ns": rfin or "",
            "drops": idrop + rdrop,
            "fabric": "lossless_input",
            "reduce_compute_ns": args.reduce_compute,
            "su_topo": os.path.basename(args.su_topo),
            "analytic_rate_bns": args.analytic_rate_bns,
            "hop_oneway_ns": args.hop_oneway_ns,
            "intranode_linkspeed_mbps": args.intranode_linkspeed,
            "engine": "pcm-sdk",
        })
        print(f"{s:>10} {str(inc_ns):>10} {str(ring_ns):>10} "
              f"{(f'{speedup:.2f}x' if speedup else '-'):>8}  {status}")

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
