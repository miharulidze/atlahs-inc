#!/usr/bin/env python3
"""INC-vs-ring AllReduce A/B sweep on a scale-up (NVLink-class) topology.

For each (group size |G|, message size) the harness:
  1. generates two GOAL .bin renderings via make_allreduce_ab (INC `coll` + ring),
  2. runs htsim_uec on the SAME topology for each arm,
  3. parses "Maximum finishing time at host" (the uniform oracle; identical 1-unit
     calc tail in both arms => makespan == collective time),
  4. writes one CSV row per (|G|, size) with both arms + the INC speedup.

Fairness model (see switch-latency-model.md):
  - Per-hop latency is the .topo's Downlink_Latency_ns, traversed by BOTH arms
    (plain p2p is source-routed, so -switch_latency would reach INC only and is
    NOT used here).
  - --reduce-compute charges the INC aggregation ALU cost (INC-only, justified);
    so any measured INC speedup is conservative.

Example:
  python3 run_allreduce_ab_sweep.py \\
    --htsim ../htsim_uec --gen ../make_allreduce_ab \\
    --topo ../topologies/scaleup_tree16_3600Gbps.topo --linkspeed 3600000 \\
    --group-sizes 2,4,8,16 \\
    --msg-sizes 4096,16384,65536,262144,1048576,4194304 \\
    --reduce-compute 100 --out results.csv
"""
import argparse
import csv
import os
import re
import subprocess
import sys

MAXFIN = re.compile(r"Maximum finishing time at host \d+:\s*(\d+)")
ALLRED = re.compile(r"ALLREDUCE_COMPLETE\b.*?duration_ns=(\d+)")
# Correctness oracle: a clean lossless/uncongested run drops nothing.
DROP = re.compile(r"drop arriving|drop last from queue|dropped packet|"
                  r"Random Drop|Buffer Drop|Dropping packet|LOSSLESS not working",
                  re.IGNORECASE)


def gen(genbin, arm, N, size, binpath, groupspath):
    cmd = [genbin, binpath, arm, str(N), str(size)]
    if arm == "inc":
        cmd.append(groupspath)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"gen failed ({arm} N={N} size={size}): {r.stderr}")


def run(htsim, binpath, topo, linkspeed, mtu, paths, seed,
        groups=None, reduce_compute=0, timeout=900):
    cmd = [htsim, "-goal", binpath, "-topo", topo, "-strat", "ecmp_host",
           "-linkspeed", str(linkspeed), "-mtu", str(mtu),
           "-paths", str(paths), "-seed", str(seed)]
    if groups:
        cmd += ["-groups", groups]
    if reduce_compute:
        cmd += ["-reduce_compute_latency", str(reduce_compute)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, 0, "timeout"
    if p.returncode != 0:
        sys.stderr.write(f"[rc={p.returncode}] {os.path.basename(binpath)}: "
                         f"{p.stderr[-200:]}\n")
        return None, 0, f"rc={p.returncode}"
    combined = p.stdout + "\n" + p.stderr
    drops = len(DROP.findall(combined))
    m = MAXFIN.search(p.stdout)
    return (int(m.group(1)) if m else None), drops, "ok"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--htsim", required=True)
    ap.add_argument("--gen", required=True, help="path to make_allreduce_ab")
    ap.add_argument("--topo", required=True)
    ap.add_argument("--linkspeed", type=int, required=True, help="Mbps (3600000 = 3600 Gbps)")
    ap.add_argument("--group-sizes", default="2,4,8,16")
    ap.add_argument("--msg-sizes", default="4096,16384,65536,262144,1048576,4194304")
    ap.add_argument("--reduce-compute", type=int, default=100,
                    help="INC-only in-switch aggregation latency (ns)")
    ap.add_argument("--mtu", type=int, default=4096)
    ap.add_argument("--paths", type=int, default=128)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tmpdir", default="/tmp/ar_ab")
    ap.add_argument("--out", default="results.csv")
    args = ap.parse_args()

    for tool in (args.htsim, args.gen):
        if not (os.path.isfile(tool) and os.access(tool, os.X_OK)):
            sys.exit(f"not executable: {tool}")
    os.makedirs(args.tmpdir, exist_ok=True)

    Gs = [int(x) for x in args.group_sizes.split(",")]
    Ss = [int(x) for x in args.msg_sizes.split(",")]
    rows = []
    health = {"runs": 0, "clean": 0, "drops": 0, "incomplete": 0}

    print(f"{'|G|':>4} {'size':>10} {'INC ns':>10} {'RING ns':>10} "
          f"{'speedup':>8}  status")
    for N in Gs:
        for S in Ss:
            if S % N != 0:
                print(f"{N:>4} {S:>10}  skip (size not divisible by |G|)")
                continue
            inc_bin = os.path.join(args.tmpdir, f"inc_g{N}_s{S}.bin")
            inc_grp = os.path.join(args.tmpdir, f"inc_g{N}_s{S}.groups")
            ring_bin = os.path.join(args.tmpdir, f"ring_g{N}_s{S}.bin")
            gen(args.gen, "inc", N, S, inc_bin, inc_grp)
            gen(args.gen, "ring", N, S, ring_bin, "")

            inc_ns, inc_drop, inc_st = run(
                args.htsim, inc_bin, args.topo, args.linkspeed, args.mtu,
                args.paths, args.seed, groups=inc_grp,
                reduce_compute=args.reduce_compute)
            ring_ns, ring_drop, ring_st = run(
                args.htsim, ring_bin, args.topo, args.linkspeed, args.mtu,
                args.paths, args.seed)

            for st in (inc_st, ring_st):
                health["runs"] += 1
            drops = inc_drop + ring_drop
            speedup = (ring_ns / inc_ns) if (inc_ns and ring_ns) else None
            ok = (inc_ns is not None and ring_ns is not None)
            if not ok:
                health["incomplete"] += 1
                status = f"INCOMPLETE (inc={inc_st},ring={ring_st})"
            elif drops:
                health["drops"] += 1
                status = f"WARN drops={drops}"
            else:
                health["clean"] += 1
                status = "ok"

            rows.append({
                "group_size": N,
                "msg_bytes": S,
                "inc_ns": inc_ns if inc_ns is not None else "",
                "ring_ns": ring_ns if ring_ns is not None else "",
                "speedup": f"{speedup:.3f}" if speedup else "",
                "ring_bytes_per_rank": 2 * (N - 1) * (S // N),
                "drops": drops,
                "topo": os.path.basename(args.topo),
                "linkspeed_mbps": args.linkspeed,
                "reduce_compute_ns": args.reduce_compute,
            })
            print(f"{N:>4} {S:>10} {str(inc_ns):>10} {str(ring_ns):>10} "
                  f"{(f'{speedup:.2f}x' if speedup else '-'):>8}  {status}")

    if not rows:
        sys.exit("no results")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\n=== correctness oracle ===")
    print(f"  runs: {health['runs']}  clean: {health['clean']}  "
          f"drops: {health['drops']}  incomplete: {health['incomplete']}")
    print(f"  STATUS: " + ("OK" if health["drops"] == 0 and health["incomplete"] == 0
                           else "INVESTIGATE"))
    print(f"wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
