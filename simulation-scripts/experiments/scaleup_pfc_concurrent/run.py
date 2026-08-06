#!/usr/bin/env python3
"""PFC / lossless-backpressure validation.

The correctness precondition for in-network aggregation is losslessness: a
dropped packet corrupts a reduction irrecoverably. This experiment suite
provides the three-legged evidence that PFC does its job under the derived
provisioning (1x BDP ingress reservation, XOFF = reservation - headroom,
egress cap = radix x BDP):

  1. ENGAGEMENT     pause events > 0 in selected census/overdrive cells;
  2. INVARIANT      while pauses fire: zero `LOSSLESS not working` lines AND
                    peak per-queue occupancy <= its provisioned bound;
  3. TRIPWIRE       deliberately mis-provisioned controls make the warnings
                    reappear, so a zero elsewhere is a measurement, not a blind spot.

Modes (each writes its own CSV under the experiment results dir):

  (default)     E2 stress: N concurrent disjoint INC AllReduces on the 3-tier
                fabric, trees PINNED to one core (-mcast_pin 0) vs DISTRIBUTED
                round-robin, at 64 KiB and 1 MiB. Pinning makes the N groups
                contend at a single core. This validates contention timing but
                need not engage PFC when provisioned buffers absorb the burst. Selected cells
                also write a -pfc_trace event log for the backpressure figures.
  --census      E1: the chapter's own worst-stress corner cells (both fabrics,
                all presented arms, 64 MiB deep steady state + two 256 MiB spot
                cells) rerun with counters -- in-situ engagement/headroom.
  --controls    E3: negative controls -- (a) the historical XOFF-above-the-queue
                threshold (high=300 on the crossbar's 270-packet BDP), (b) the
                pre-fix 1x BDP egress cap on the 3-tier fabric, (c) the NIC
                pause gate disabled under NIC overdrive. The high=300 crossbar
                arm is now a documented null; the undersized egress arm must
                relight warnings; the gate-off arm is the NIC A/B twin.
  --overdrive   E2b: NIC rate 2x the fabric rate (gate ON) -- end-to-end
                exercise of pause propagation with zero warnings. The separate
                XOFF=100 control is the causal grant-withholding probe.
  --validate    generate + compile the stress trace at N=4 (no sim).

Geometry of the stress mode (unchanged from the original experiment): each
group must SPAN MULTIPLE PODS so its tree reaches the core tier (pinning only
bites at the core). On the 256-host 3-tier fat-tree (16 hosts/pod x 16 pods),
group g = one host per pod at slot g across the first PODS_SPANNED pods.
Groups are disjoint by slot, so N <= hosts-per-pod (16); N also stays <= core
count (16) so the distributed arm is conflict-free BY CONSTRUCTION (round-robin
tiles the 16 (agg-position, core) placements exactly once) -- it is a
constructed control, not a measured isolation result.

`drop_log_hits` counts the simulator's `LOSSLESS not working` warn-and-forward
log lines (htsim never actually drops on lossless queues), i.e. protocol-
invariant violations -- NOT lost packets.

Reproduce (Docker):
  docker run --rm -v $(pwd):/workspace atlahs-sim build   # instrumented binary
  docker run --rm -v $(pwd):/workspace atlahs-sim run scaleup_pfc_concurrent --validate
  docker run --rm -v $(pwd):/workspace atlahs-sim run scaleup_pfc_concurrent
  docker run --rm -v $(pwd):/workspace atlahs-sim run scaleup_pfc_concurrent --census
  docker run --rm -v $(pwd):/workspace atlahs-sim run scaleup_pfc_concurrent --controls
  docker run --rm -v $(pwd):/workspace atlahs-sim run scaleup_pfc_concurrent --overdrive
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
SU_TOPO_XBAR = "scaleup_single_switch_64_4000Gbps.topo"  # census/controls crossbar
SO_TOPO = "tree16_bw200Gbps.topo"             # idle scale-out (mode Z)
WIDTH = 256           # scale-up topo host width -> gpus_per_node (stress mode)
HOSTS_PER_POD = 16    # 3-tier 256: 16 hosts/pod x 16 pods
NODES = 2             # mode Z: small idle scale-out; all ranks land in node 0
PODS_SPANNED = 8      # group size = pods each group spans (>=2 to reach the core tier)
KIND = "allreduce"    # apex turn-around: fan-in up + multicast down (exercises both primitives)
N_SWEEP = [1, 2, 4, 8, 12, 16]  # concurrent groups; <= HOSTS_PER_POD (slots) and <= #cores
MSG_SWEEP = [65536, 1048576]    # 64 KiB (retired fork Fig 5.12) + 1 MiB (rigor plan 2nd size)

INTRANODE_LINKSPEED = sim.INTRANODE_LINKSPEED_DEFAULT
OVERDRIVE_LINKSPEED = 2 * INTRANODE_LINKSPEED   # NIC at 2x the fabric rate

# Two placement arms. mcast_pin=-1 -> round-robin (default); 0 -> all trees on one core.
ARMS = [
    {"label": "distributed", "mcast_pin": -1},
    {"label": "pinned",      "mcast_pin": 0},
]

# Stress cells that also record the -pfc_trace event log (pause raster /
# occupancy sawtooth / pause-share figures). Keep this list short: the trace
# is per-event and the runs are otherwise identical to their untraced twins.
TRACE_CELLS = {(4, 1048576, "pinned"), (16, 1048576, "pinned"),
               (16, 1048576, "distributed")}

# Census arms = the presented scaleup_coll_ab cases (source of truth:
# experiments/scaleup_coll_ab/run.py COLL_CASES; duplicated here rather than
# imported across experiment packages).
CENSUS_CASES = [
    {"collective": "allreduce",      "algo": "ring"},
    {"collective": "allreduce",      "algo": "rdouble"},
    {"collective": "allreduce",      "algo": "ring", "inc_kind": "allreduce_rs_ag"},
    {"collective": "reduce_scatter", "algo": "ring"},
    {"collective": "allgather",      "algo": "ring"},
    {"collective": "bcast",          "algo": "ring"},
    {"collective": "reduce",         "algo": "ring"},
]
CENSUS_N = 64
CENSUS_SIZE = 67108864          # 64 MiB: deep steady state (BDP is ~1-2 MiB)
CENSUS_SPOT_SIZE = 268435456    # 256 MiB spot cells (chapter max; no size cliff)

CSV_FIELDS = ["mode", "arm", "mcast_pin", "n_groups", "group_size", "pods_spanned",
              "hosts_per_pod", "collective", "baseline_algo", "msg_bytes",
              "makespan_ns", "drop_log_hits", "status",
              "pfc_high", "pfc_low", "intranode_q",
              "pauses_sent", "resumes_sent",
              "peak_ingress_bytes", "peak_ingress_frac", "peak_ingress_queue",
              "peak_egress_bytes", "peak_egress_frac", "peak_egress_queue",
              "nic_honor", "nic_pauses_seen", "nic_grants_declined", "nic_redrives_held",
              "su_topo", "so_topo", "nodes", "gpus_per_node",
              "intranode_linkspeed_mbps", "engine", "log_file", "trace_file", "command"]


def _row(out, mode, arm, fin, drop, st, cmd, px, log, trace, su_topo, so_topo,
         nodes, gpus, linkspeed, **kw):
    violations = []
    if st != "ok" or fin is None:
        violations.append(f"simulator did not complete (status={st}, makespan={fin})")
    if mode == "control_cap":
        if drop <= 0:
            violations.append("undersized-egress tripwire did not emit an invariant warning")
    elif drop != 0:
        violations.append(f"unexpected invariant warnings: {drop}")
    for key in ("pauses_sent", "peak_ingress_frac", "peak_egress_frac"):
        if px.get(key, "") == "":
            violations.append(f"missing PFC instrumentation field {key}")
    if mode != "control_cap":
        for key in ("peak_ingress_frac", "peak_egress_frac"):
            value = px.get(key, "")
            if value != "" and float(value) > 1.0:
                violations.append(f"{key} exceeds configured bound: {value}")
    if mode == "control_gate" and int(px.get("nic_redrives_held") or 0) <= 0:
        violations.append("XOFF=100 gate probe withheld no NIC redrives")
    if violations:
        detail = "; ".join(violations)
        report.print_error(f"{mode}/{arm}: {detail}")
        raise RuntimeError(detail)

    row = {"mode": mode, "arm": arm, "makespan_ns": fin or "",
           "drop_log_hits": drop, "status": st,
           "su_topo": os.path.basename(su_topo), "so_topo": os.path.basename(so_topo),
           "nodes": nodes, "gpus_per_node": gpus,
           "intranode_linkspeed_mbps": linkspeed, "engine": "pcm-sdk",
           "log_file": log, "trace_file": trace or "", "command": cmd,
           "mcast_pin": "", "n_groups": "", "group_size": "", "pods_spanned": "",
           "hosts_per_pod": "", "collective": "", "baseline_algo": "", "msg_bytes": ""}
    row.update(px)
    row.update(kw)
    out.write(row)
    return row


def _print_cell(tag, fin, drop, st, px):
    us = f"{fin/1000:.2f} us" if fin else "-"
    status = st if st != "ok" else "ok"
    if drop:
        status += f"  WARN LOSSLESS-VIOLATION drop_log_hits={drop}"
    eng = (f"pauses={px.get('pauses_sent', '')} "
           f"peakI={px.get('peak_ingress_frac', '')} "
           f"peakE={px.get('peak_egress_frac', '')} "
           f"nic_redrives={px.get('nic_redrives_held', '')}")
    print(f"  {tag:<42} {us:>12}  {eng}  {status}")


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
    goal.gen_multigroup_inc_goal(g, g[:-5] + ".groups", NUM_RANKS, groups,
                                 MSG_SWEEP[0], KIND, TAIL_NS)
    goal.compile_goal(g, g[:-5] + ".bin")
    print(f"  groups(N=4): {groups}")
    print("  compiled OK  (both arms reuse this trace; placement = -mcast_pin flag only)")
    print("validation: PASS")
    return 0


def run_stress(n_sweep, su_topo, so_topo, tmpdir, timeout):
    """E2: pinned-vs-distributed concurrent AllReduces; PFC engagement evidence."""
    os.makedirs(tmpdir, exist_ok=True)
    csv_path = os.path.join(OUTPUT_DIR, f"{EXP_NAME}.csv")
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        for msg in MSG_SWEEP:
            for n in n_sweep:
                if n > HOSTS_PER_POD:
                    report.print_warning(f"N={n}: skip (> {HOSTS_PER_POD} host-slots per pod)")
                    continue
                groups = make_groups(n)
                g = os.path.join(tmpdir, f"pfc_{n}_{msg}.goal")
                grp = g[:-5] + ".groups"
                # Both arms share ONE trace; only the -mcast_pin flag differs.
                goal.gen_multigroup_inc_goal(g, grp, NUM_RANKS, groups, msg, KIND, TAIL_NS)
                goal.compile_goal(g, g[:-5] + ".bin")
                for arm in ARMS:
                    label = f"{arm['label']}_{n}_{msg}"
                    log = os.path.join(OUTPUT_DIR, "logs", f"{label}.log.gz")
                    trace = None
                    if (n, msg, arm["label"]) in TRACE_CELLS:
                        trace = os.path.join(OUTPUT_DIR, "traces", f"{label}.csv")
                        os.makedirs(os.path.dirname(trace), exist_ok=True)
                    fin, drop, st, cmd, px = sim.run_sim(
                        g[:-5] + ".bin", so_topo, su_topo,
                        nodes=NODES, gpus_per_node=WIDTH, groups=grp,
                        timeout=timeout, intranode_linkspeed=INTRANODE_LINKSPEED,
                        mcast_pin=arm["mcast_pin"], pfc_trace=trace, save_stdout=log)
                    _row(out, "stress", arm["label"], fin, drop, st, cmd, px, log,
                         trace, su_topo, so_topo, NODES, WIDTH, INTRANODE_LINKSPEED,
                         mcast_pin=arm["mcast_pin"], n_groups=n,
                         group_size=PODS_SPANNED, pods_spanned=PODS_SPANNED,
                         hosts_per_pod=HOSTS_PER_POD, collective=KIND, msg_bytes=msg)
                    _print_cell(f"N={n} {arm['label']} {msg}B", fin, drop, st, px)
    report.print_success(f"wrote results to {csv_path}")


def _census_cell(out, mode, case, size, su_topo, so_topo, tmpdir, timeout,
                 linkspeed=None, pfc_high=None, pfc_low=None, intranode_q=None,
                 arms=("inc", "base"), extra_flags=None, trace_arms=()):
    """One presented coll_ab cell (both renderings), instrumented. Mirrors
    scaleup_coll_ab/run.py run_exp: nodes = gpus_per_node = N, groups only on
    the in-network arm."""
    linkspeed = linkspeed or INTRANODE_LINKSPEED
    coll, algo = case["collective"], case["algo"]
    inc_kind = case.get("inc_kind", coll)
    inc_root = 0 if inc_kind in ("bcast", "reduce") else -1
    label = inc_kind
    n = CENSUS_N
    su_width = sim.topology_nodes(su_topo)
    if n > su_width:
        raise ValueError(f"census group size {n} exceeds scale-up topology width {su_width}")
    rows = []
    base = os.path.join(tmpdir, f"base_{label}_{algo}_{size}.goal")
    inc = os.path.join(tmpdir, f"inc_{label}_{size}.goal")
    grp = inc[:-5] + ".groups"
    if "base" in arms:
        goal.gen_baseline_goal(base, n, size, coll, algo, TAIL_NS)
        goal.compile_goal(base, base[:-5] + ".bin")
    if "inc" in arms:
        goal.gen_inc_goal(inc, grp, n, size, inc_kind, TAIL_NS, root=inc_root)
        goal.compile_goal(inc, inc[:-5] + ".bin")
    if extra_flags is not None:
        os.environ["SIM_EXTRA_FLAGS"] = extra_flags
    try:
        topo_tag = "xbar" if "single_switch" in su_topo else "3tier"
        for rendering in arms:
            binpath = (inc if rendering == "inc" else base)[:-5] + ".bin"
            rendering_width = su_width if rendering == "inc" else n
            tag = f"{mode}_{topo_tag}_{label}_{algo}_{rendering}_{size}"
            log = os.path.join(OUTPUT_DIR, "logs", f"{tag}.log.gz")
            trace = None
            if rendering in trace_arms:
                trace = os.path.join(OUTPUT_DIR, "traces", f"{tag}.csv")
                os.makedirs(os.path.dirname(trace), exist_ok=True)
            fin, drop, st, cmd, px = sim.run_sim(
                binpath, so_topo, su_topo, nodes=n, gpus_per_node=rendering_width,
                groups=grp if rendering == "inc" else None,
                timeout=timeout, intranode_linkspeed=linkspeed,
                pfc_high=pfc_high, pfc_low=pfc_low, intranode_q=intranode_q,
                pfc_trace=trace, save_stdout=log)
            rows.append(_row(
                out, mode, rendering, fin, drop, st, cmd, px, log, trace,
                su_topo, so_topo, n, rendering_width, linkspeed,
                collective=label, baseline_algo=algo, group_size=n, msg_bytes=size))
            _print_cell(f"{topo_tag} {label}/{algo} {rendering} {size}B", fin, drop, st, px)
    finally:
        if extra_flags is not None:
            os.environ.pop("SIM_EXTRA_FLAGS", None)
    return rows


def run_census(tmpdir, timeout):
    """E1: the chapter's worst-stress corner cells, instrumented in situ."""
    os.makedirs(tmpdir, exist_ok=True)
    csv_path = os.path.join(OUTPUT_DIR, "pfc_census.csv")
    rows = []
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        for su in (SU_TOPO_XBAR, SU_TOPO):
            report.print_info(f"=== census {os.path.basename(su)} N={CENSUS_N} "
                              f"@ {CENSUS_SIZE} ===")
            for case in CENSUS_CASES:
                rows.extend(_census_cell(
                    out, "census", case, CENSUS_SIZE, paths.topo(su),
                    paths.topo(SO_TOPO), tmpdir, timeout))
        # 256 MiB spot cells on the 3-tier fabric: the historical worst cases
        # (AllGather fan-out; recursive-doubling cross-pod fan-in).
        report.print_info(f"=== census spot @ {CENSUS_SPOT_SIZE} (3-tier) ===")
        rows.extend(_census_cell(
            out, "census", {"collective": "allgather", "algo": "ring"},
            CENSUS_SPOT_SIZE, paths.topo(SU_TOPO), paths.topo(SO_TOPO),
            tmpdir, timeout, arms=("inc",)))
        rows.extend(_census_cell(
            out, "census", {"collective": "allreduce", "algo": "rdouble"},
            CENSUS_SPOT_SIZE, paths.topo(SU_TOPO), paths.topo(SO_TOPO),
            tmpdir, timeout, arms=("base",)))
    if not any(int(row["pauses_sent"] or 0) > 0 for row in rows):
        raise RuntimeError("PFC census was vacuous: no cell emitted a pause")
    report.print_success(f"wrote results to {csv_path}")


def run_controls(tmpdir, timeout):
    """E3: historical/null, mis-provisioned, and NIC-gate controls."""
    os.makedirs(tmpdir, exist_ok=True)
    csv_path = os.path.join(OUTPUT_DIR, "pfc_controls.csv")
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        # (a) Historical XOFF-above-BDP configuration. It is retained as a null
        # control: with the corrected receive path, this workload no longer drives
        # the crossbar ingress high enough to trigger either pause or warning.
        report.print_info("=== control (a): high=300 > 270-pkt BDP, crossbar RS 64 MiB ===")
        _census_cell(out, "control_thresh", {"collective": "reduce_scatter", "algo": "ring"},
                     CENSUS_SIZE, paths.topo(SU_TOPO_XBAR), paths.topo(SO_TOPO),
                     tmpdir, timeout, pfc_high=300, pfc_low=240, arms=("inc",))
        # (b) Pre-fix egress cap: 1x BDP (440 pkt) instead of radix x BDP on the
        # 3-tier fabric -- recursive doubling's lawful cross-pod fan-in (measured
        # peak 1,001 pkt) must relight the warnings.
        report.print_info("=== control (b): egress cap 440 pkt, 3-tier rdouble 64 MiB ===")
        _census_cell(out, "control_cap", {"collective": "allreduce", "algo": "rdouble"},
                     CENSUS_SIZE, paths.topo(SU_TOPO), paths.topo(SO_TOPO),
                     tmpdir, timeout, intranode_q=440, arms=("base",))
        # (c) NIC pause gate OFF under 2x NIC overdrive: the A/B twin of
        # --overdrive. Timing may match the gated twin (htsim's lossless queues
        # warn-and-forward, so both arms have effectively unbounded buffers);
        # the gate's evidence is the NIC-side counters (gate off, the pause
        # adapter is not even registered: nic_pauses_seen stays 0).
        report.print_info("=== control (c): -no_nic_pfc_gate + 2x NIC overdrive, AG 64 MiB ===")
        _census_cell(out, "control_nogate", {"collective": "allgather", "algo": "ring"},
                     CENSUS_SIZE, paths.topo(SU_TOPO_XBAR), paths.topo(SO_TOPO),
                     tmpdir, timeout, linkspeed=OVERDRIVE_LINKSPEED,
                     extra_flags="-no_nic_pfc_gate", arms=("inc",))
        # (d) Gate-engagement probe: the derived thresholds pause BETWEEN chunk
        # emissions (sources are chunk-paced, so grants are never attempted
        # while paused and nic_redrives_held stays 0). Lowering XOFF to the old
        # 100-packet default makes pauses land MID-block: the arbiter must then
        # actually withhold grants -- the positive nic_redrives_held > 0 datum
        # (first measured 2026-07-27 on the NIC-gate fix, 210 held grants).
        report.print_info("=== control (d): gate probe @ high=100/80, crossbar AG 64 MiB ===")
        _census_cell(out, "control_gate", {"collective": "allgather", "algo": "ring"},
                     CENSUS_SIZE, paths.topo(SU_TOPO_XBAR), paths.topo(SO_TOPO),
                     tmpdir, timeout, pfc_high=100, pfc_low=80, arms=("inc",))
    report.print_success(f"wrote results to {csv_path}")


def run_overdrive(tmpdir, timeout):
    """E2b: NIC at 2x the fabric rate, gate ON -- pause propagation must engage
    with zero warnings. Grant withholding is established by control_gate."""
    os.makedirs(tmpdir, exist_ok=True)
    csv_path = os.path.join(OUTPUT_DIR, "pfc_overdrive.csv")
    rows = []
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        report.print_info("=== overdrive: NIC 2x fabric, crossbar AG 64 MiB (INC arm) ===")
        rows.extend(_census_cell(
            out, "overdrive", {"collective": "allgather", "algo": "ring"},
            CENSUS_SIZE, paths.topo(SU_TOPO_XBAR), paths.topo(SO_TOPO),
            tmpdir, timeout, linkspeed=OVERDRIVE_LINKSPEED, arms=("inc",),
            trace_arms=("inc",)))
        # Concurrent pinned AllReduces under overdrive: both stressors at once.
        report.print_info("=== overdrive: NIC 2x fabric, pinned N=16 concurrent AR 1 MiB ===")
        groups = make_groups(16)
        g = os.path.join(tmpdir, "pfc_od_16.goal")
        grp = g[:-5] + ".groups"
        goal.gen_multigroup_inc_goal(g, grp, NUM_RANKS, groups, 1048576, KIND, TAIL_NS)
        goal.compile_goal(g, g[:-5] + ".bin")
        log = os.path.join(OUTPUT_DIR, "logs", "overdrive_pinned_16_1048576.log.gz")
        fin, drop, st, cmd, px = sim.run_sim(
            g[:-5] + ".bin", paths.topo(SO_TOPO), paths.topo(SU_TOPO),
            nodes=NODES, gpus_per_node=WIDTH, groups=grp, timeout=timeout,
            intranode_linkspeed=OVERDRIVE_LINKSPEED, mcast_pin=0, save_stdout=log)
        rows.append(_row(
            out, "overdrive", "pinned", fin, drop, st, cmd, px, log, None,
            paths.topo(SU_TOPO), paths.topo(SO_TOPO), NODES, WIDTH,
            OVERDRIVE_LINKSPEED, mcast_pin=0, n_groups=16,
            group_size=PODS_SPANNED, pods_spanned=PODS_SPANNED,
            hosts_per_pod=HOSTS_PER_POD, collective=KIND, msg_bytes=1048576))
        _print_cell("pinned N=16 overdrive 1 MiB", fin, drop, st, px)
    if not any(int(row["pauses_sent"] or 0) > 0 for row in rows):
        raise RuntimeError("PFC overdrive suite was vacuous: no cell emitted a pause")
    report.print_success(f"wrote results to {csv_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n-sweep", default=",".join(str(x) for x in N_SWEEP),
                    help="stress mode: comma list of concurrent-group counts")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--tmpdir", default="/tmp/scaleup_pfc_concurrent")
    ap.add_argument("--validate", action="store_true", help="generate + compile only, no sim")
    ap.add_argument("--census", action="store_true", help="E1 in-situ corner-cell census")
    ap.add_argument("--controls", action="store_true", help="E3 mis-provisioned negative controls")
    ap.add_argument("--overdrive", action="store_true", help="E2b NIC-overdrive points (gate ON)")
    args = ap.parse_args()

    goal.require_txt2bin()
    goal.require_generator()
    if args.validate:
        sys.exit(validate(args.tmpdir))

    sim.require_simulator()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if args.census:
        run_census(args.tmpdir, args.timeout)
    elif args.controls:
        run_controls(args.tmpdir, args.timeout)
    elif args.overdrive:
        run_overdrive(args.tmpdir, args.timeout)
    else:
        n_sweep = [int(x) for x in args.n_sweep.split(",")]
        run_stress(n_sweep, paths.topo(SU_TOPO), paths.topo(SO_TOPO),
                   args.tmpdir, args.timeout)


if __name__ == "__main__":
    main()
