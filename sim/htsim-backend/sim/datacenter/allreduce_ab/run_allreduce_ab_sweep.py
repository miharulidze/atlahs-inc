#!/usr/bin/env python3
"""INC-vs-ring(-vs-recursive-doubling) AllReduce A/B sweep on a scale-up topology.

For each (group size |G|, message size) the harness:
  1. generates up to three GOAL .bin renderings via make_allreduce_ab:
       - inc     : one in-network Collective(ALLREDUCE) per rank (+ .groups sidecar),
       - ring    : chunked bandwidth-optimal p2p ring (2(N-1) steps of size/N),
       - rdouble : recursive halving/doubling (2*log2 N steps; power-of-two N only) [D4],
  2. runs htsim_uec on the SAME (per-N) topology for each arm,
  3. parses "Maximum finishing time at host" (the uniform oracle; identical 1-unit
     calc tail in all arms => makespan == collective time),
  4. writes one CSV row per (|G|, size) with the measured arms PLUS two computed
     analytic reference columns (no simulation):
       - ideal_ring_ns   [D3]: the 2(N-1)/N line-rate, zero-CC ring lower bound
                                = 2(N-1)/N * 8*S/B  +  (N-1)*hop_oneway_ns,
                                with speedup_vs_ideal = ideal_ring_ns / inc_ns
                                (the CC-decontaminated INC-vs-perfect-ring speedup),
       - inc_synced_ns   [D1]: inc_ns + one app-sync RTT (Khalilov 3-phase scheme:
                                ring barrier -> ACK-less mcast/aggr -> neighbor scan),
                                with speedup_synced = ring_ns / inc_synced_ns.

Fairness model (see switch-latency-model.md and README.md):
  - Per-hop latency is the .topo's Downlink_Latency_ns + Switch_Latency_ns, traversed by
    ALL arms (plain p2p is source-routed, so -switch_latency would reach INC only and is
    NOT used here).
  - --reduce-compute charges the INC aggregation ALU cost (INC-only, justified); so any
    measured INC speedup is conservative.
  - D1 (supervisor 2026-07-06): INC completes ACK-less while ring/rdouble complete at
    ACK-return, ~1 RTT optimistic for INC. We do NOT strip baseline ACKs; instead we
    charge INC an analytic app-sync cost of --inc-sync-rtt-ns (default 2600 ns = +1 RTT
    = 2*hop_oneway; use 1300 for the 1/2-RTT one-way sensitivity lower bound).
    hop_oneway = 1300 ns = 500 link + 300 switch + 500 link.

Topology selection:
  - --topo TOPO            : one fixed topology for every |G| (backward compatible), or
  - --topo-template TMPL   : a per-N template with '{n}', e.g.
                             ../topologies/scaleup_single_switch_{n}_3600Gbps.topo
                             so each |G| runs on a single switch whose radix == |G|
                             (D5 single-switch scale-up scope). Exactly one is required.

Example (D3 speedup-vs-N sweep on single-switch scale-up crossbars):
  python3 run_allreduce_ab_sweep.py \\
    --htsim ../htsim_uec --gen ../make_allreduce_ab \\
    --topo-template ../topologies/scaleup_single_switch_{n}_3600Gbps.topo \\
    --linkspeed 3600000 --group-sizes 2,4,8,16,32,64 \\
    --msg-sizes 65536,1048576 --reduce-compute 100 --out results_vs_n.csv
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


def is_pow2(n):
    return n >= 1 and (n & (n - 1)) == 0


def ideal_ring_ns(N, S, rate_bns, hop_oneway_ns):
    """Analytic bandwidth-optimal, line-rate, zero-CC ring AllReduce lower bound [D3].

    Textbook 2(N-1)/N cost model: each rank moves 2(N-1)/N * S bytes at the *realised*
    line rate, plus (N-1) serial dependency hops at hop_oneway latency. This is the floor
    the *measured* ring must not beat (bug-oracle); speedup_vs_ideal = ideal_ring_ns/inc_ns
    is the CC-decontaminated INC-vs-perfect-ring speedup (removes the measured ring's
    congestion-control cold-start confound).

    IMPORTANT (realised wire rate): the analytic bandwidth term is priced at the rate the
    engine ACTUALLY realises, NOT the nominal --linkspeed. htsim's queue quantises the
    per-byte serialisation time to integer picoseconds: nominal 3600 Gbps = 450 B/ns would
    be 2.222 ps/B, truncated to 2 ps/B => 500 B/ns raw, and x (MTU / MTU+hdr) = 4096/4160
    framing => ~492.3 B/ns payload-effective. This is confirmed empirically: the ACK-less
    INC arm's completion-vs-size slope is 492.26-492.29 B/ns. Pricing the ideal ring at
    492.3 (rate_bns) instead of the nominal 450 removes a ~9% bandwidth-term bias and makes
    the analytic floor consistent with the measured INC bandwidth slope. hop_oneway is the
    topology's own one-way hop latency (crossbar 2*500+300 = 1300 ns; the two-level fabric
    4*500+3*300 = 2900 ns) and is realised as-is (latencies are not byte-quantised).
    """
    bw_ns = (2.0 * (N - 1) / N) * S / rate_bns
    lat_ns = (N - 1) * hop_oneway_ns
    return bw_ns + lat_ns


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
    ap.add_argument("--topo", help="one fixed .topo for every |G| (mutually exclusive "
                                   "with --topo-template)")
    ap.add_argument("--topo-template", help="per-N .topo template with '{n}', e.g. "
                    "../topologies/scaleup_single_switch_{n}_3600Gbps.topo")
    ap.add_argument("--linkspeed", type=int, required=True, help="Mbps (3600000 = 3600 Gbps)")
    ap.add_argument("--group-sizes", default="2,4,8,16")
    ap.add_argument("--msg-sizes", default="4096,16384,65536,262144,1048576,4194304")
    ap.add_argument("--reduce-compute", type=int, default=100,
                    help="INC-only in-switch aggregation latency (ns)")
    ap.add_argument("--mtu", type=int, default=4096)
    ap.add_argument("--paths", type=int, default=128)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--inc-sync-rtt-ns", type=int, default=2600,
                    help="[D1] analytic app-sync cost charged to INC (default 2600 = "
                         "+1 RTT = 2*hop_oneway; 1300 = 1/2-RTT one-way sensitivity)")
    ap.add_argument("--hop-oneway-ns", type=int, default=1300,
                    help="[D3] one-way per-hop latency for the ideal-ring latency term "
                         "(crossbar default 1300 = 500 link + 300 switch + 500 link; the "
                         "two-level fabric is 2900 = 4*500 + 3*300)")
    ap.add_argument("--analytic-rate-bns", type=float, default=492.3,
                    help="[D3] REALISED payload-effective wire rate (B/ns) for the analytic "
                         "ideal-ring bandwidth term; decoupled from --linkspeed (the engine "
                         "rate). Default 492.3 = 500 B/ns raw (2 ps/B quantised) x 4096/4160 "
                         "MTU framing == the ACK-less INC arm's measured size-slope. The "
                         "measured arms are unaffected (engine still uses --linkspeed).")
    ap.add_argument("--no-rdouble", action="store_true",
                    help="skip the recursive-doubling arm (D4)")
    ap.add_argument("--tmpdir", default="/tmp/ar_ab")
    ap.add_argument("--out", default="results.csv")
    args = ap.parse_args()

    if bool(args.topo) == bool(args.topo_template):
        sys.exit("provide exactly one of --topo or --topo-template")

    def topo_for(N):
        return args.topo_template.format(n=N) if args.topo_template else args.topo

    for tool in (args.htsim, args.gen):
        if not (os.path.isfile(tool) and os.access(tool, os.X_OK)):
            sys.exit(f"not executable: {tool}")
    os.makedirs(args.tmpdir, exist_ok=True)

    # NOTE: --linkspeed drives the ENGINE (measured arms). The analytic ideal-ring is
    # priced separately at the REALISED payload-effective rate (--analytic-rate-bns,
    # default 492.3 B/ns), because the engine quantises per-byte time to 2 ps (see
    # ideal_ring_ns docstring). Keeping them separate is why measured values are unchanged
    # by this correction and only the analytic references move.

    Gs = [int(x) for x in args.group_sizes.split(",")]
    Ss = [int(x) for x in args.msg_sizes.split(",")]
    rows = []
    health = {"runs": 0, "clean": 0, "drops": 0, "incomplete": 0,
              "ring_below_ideal": 0, "inc_beats_ideal": 0}

    print(f"{'|G|':>4} {'size':>10} {'INC ns':>10} {'RING ns':>10} {'RDBL ns':>10} "
          f"{'IDEAL ns':>10} {'r/inc':>7} {'ideal/inc':>9}  status")
    for N in Gs:
        topo = topo_for(N)
        if not os.path.isfile(topo):
            sys.exit(f"topo not found for |G|={N}: {topo}")
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
                args.htsim, inc_bin, topo, args.linkspeed, args.mtu,
                args.paths, args.seed, groups=inc_grp,
                reduce_compute=args.reduce_compute)
            ring_ns, ring_drop, ring_st = run(
                args.htsim, ring_bin, topo, args.linkspeed, args.mtu,
                args.paths, args.seed)

            # D4: recursive-doubling arm (power-of-two |G| only; endpoint reduces, so
            # NOT charged reduce_compute -- same fairness footing as the ring).
            rdouble_ns, rdouble_drop, rdouble_st = None, 0, "skip"
            if not args.no_rdouble and is_pow2(N):
                rd_bin = os.path.join(args.tmpdir, f"rdouble_g{N}_s{S}.bin")
                gen(args.gen, "rdouble", N, S, rd_bin, "")
                rdouble_ns, rdouble_drop, rdouble_st = run(
                    args.htsim, rd_bin, topo, args.linkspeed, args.mtu,
                    args.paths, args.seed)

            # D3 / D1 computed (analytic) columns -- pure post-processing, no sim.
            ideal_ns = ideal_ring_ns(N, S, args.analytic_rate_bns, args.hop_oneway_ns)
            speedup = (ring_ns / inc_ns) if (inc_ns and ring_ns) else None
            speedup_vs_ideal = (ideal_ns / inc_ns) if inc_ns else None
            inc_synced_ns = (inc_ns + args.inc_sync_rtt_ns) if inc_ns else None
            speedup_synced = (ring_ns / inc_synced_ns) if (inc_synced_ns and ring_ns) else None
            speedup_rdouble = (rdouble_ns / inc_ns) if (inc_ns and rdouble_ns) else None

            for st in (inc_st, ring_st):
                health["runs"] += 1
            if not args.no_rdouble and is_pow2(N):
                health["runs"] += 1
            drops = inc_drop + ring_drop + rdouble_drop
            # Bug-oracle: the measured, CC'd ring can never beat the analytic line-rate
            # ideal ring. INC beating the ideal ring is EXPECTED in the latency regime
            # (flat O(1) apex vs the ideal ring's (N-1) serial hops) -- tracked as info.
            if ring_ns and ring_ns < ideal_ns:
                health["ring_below_ideal"] += 1
            if inc_ns and inc_ns < ideal_ns:
                health["inc_beats_ideal"] += 1
            arms_ok = inc_ns is not None and ring_ns is not None and (
                args.no_rdouble or not is_pow2(N) or rdouble_ns is not None)
            if not arms_ok:
                health["incomplete"] += 1
                status = (f"INCOMPLETE (inc={inc_st},ring={ring_st},"
                          f"rdbl={rdouble_st})")
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
                "rdouble_ns": rdouble_ns if rdouble_ns is not None else "",
                "ideal_ring_ns": f"{ideal_ns:.1f}",
                "speedup": f"{speedup:.3f}" if speedup else "",
                "speedup_vs_ideal": f"{speedup_vs_ideal:.3f}" if speedup_vs_ideal else "",
                "speedup_rdouble": f"{speedup_rdouble:.3f}" if speedup_rdouble else "",
                "inc_synced_ns": inc_synced_ns if inc_synced_ns is not None else "",
                "speedup_synced": f"{speedup_synced:.3f}" if speedup_synced else "",
                "ring_bytes_per_rank": 2 * (N - 1) * (S // N),
                "drops": drops,
                "topo": os.path.basename(topo),
                "linkspeed_mbps": args.linkspeed,
                "reduce_compute_ns": args.reduce_compute,
                "hop_oneway_ns": args.hop_oneway_ns,
                "analytic_rate_bns": args.analytic_rate_bns,
                "inc_sync_rtt_ns": args.inc_sync_rtt_ns,
            })
            print(f"{N:>4} {S:>10} {str(inc_ns):>10} {str(ring_ns):>10} "
                  f"{str(rdouble_ns):>10} {ideal_ns:>10.0f} "
                  f"{(f'{speedup:.1f}x' if speedup else '-'):>7} "
                  f"{(f'{speedup_vs_ideal:.1f}x' if speedup_vs_ideal else '-'):>9}  {status}")

    if not rows:
        sys.exit("no results")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\n=== correctness + bug oracles ===")
    print(f"  runs: {health['runs']}  clean: {health['clean']}  "
          f"drops: {health['drops']}  incomplete: {health['incomplete']}")
    print(f"  ideal-ring floor: measured ring < ideal_ring in "
          f"{health['ring_below_ideal']} cell(s) "
          f"({'OK' if health['ring_below_ideal'] == 0 else 'INVESTIGATE'} -- the "
          f"measured CC'd ring must never beat the analytic line-rate ideal ring)")
    print(f"  INC vs ideal-ring: INC beats ideal_ring in {health['inc_beats_ideal']} "
          f"cell(s) (EXPECTED in the latency regime -- flat O(1) apex vs the ideal "
          f"ring's (N-1) serial hops; this is the D3 algorithmic result, not a bug)")
    print(f"  STATUS: " + ("OK" if health["drops"] == 0 and health["incomplete"] == 0
                           and health["ring_below_ideal"] == 0 else "INVESTIGATE"))
    print(f"wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
