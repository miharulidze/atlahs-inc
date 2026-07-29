#!/usr/bin/env python3
"""Intranode link-speed sweep — INC vs baseline, trace-free Llama-2-7B iteration.

Reproduces the shape of the "Tuning Intranode Link Speed" figure as an
INC-vs-endpoint A/B on PURELY SYNTHETIC workloads (no captured trace, no GPU):
for each parallelism config we synthesize one Llama training iteration with the
`simple_sim` analytical generator, in two arms —
  * baseline : the generator's decomposed collectives (EMIT_INC unset)
  * INC      : node-contained TP collectives as first-class `coll` ops + a
               .groups sidecar (EMIT_INC=1)
— compile to `.bin`, and sweep the scale-up (intranode) link speed while timing
one training iteration on the pcm-sdk two-tier simulator.

Layout is multi-domain and falls out of the generator: `simple_sim2goal`
derives the scale-up-domain width from the TP group size, so
  gpus_per_node = TP,  nodes = 16 / TP  (= DP*PP).
TP collectives live INSIDE a node (the swept intranode link); DP/PP cross nodes
on the fixed scale-out fabric (SO_TOPO). Only TP collectives go INC
(INC_CONTEXTS=tp), so DP/PP contribute the same fixed floor to both arms and the
INC-vs-baseline gap isolates the TP data movement.

Model: Zhiyi-matched Llama-2 7B PER-LAYER dims (hidden 4096, FFN 11008, 32 MHA
heads, seq 4096, micro-batch 1) so message sizes place the bandwidth->latency
knee in the plotted range, but a SHALLOW stack (default 2 layers): iteration time
and the INC gap scale ~linearly in depth, so the trend extrapolates to full 7B.
COMPUTE_MODEL=h100 gives a realistic compute floor (cancels in the INC gap).

The swept x-axis is `-intranode_linkspeed` (Mbps = Gbps*1000). The intranode
.topo is GENERATED PER RATE so the fabric pipes and the NIC/copy-engine flag
carry the SAME speed at every point (consistent link speed in the network AND
at the sender endpoints -- the earlier pinned-12800 topo left the pipes at a
different, non-integer-ps/B rate than the swept flag). Swept rates are
restricted to divisors of 8000 Gbps: htsim stores link rate as integer
picoseconds per byte (ps/B = 8000/Gbps), so only these rates are exact on the
wire -- 3200/3600/6400/12800 silently quantise. PFC thresholds and the
intranode queue are DERIVED per generated fabric via sim.pfc_config() (1x BDP
ingress reservation, rate-derived headroom, radix x BDP egress cap), and
-mtu 4160 keeps the payload MSS at a round 4096 B (sim.MTU_DEFAULT). Two PNGs,
split by PP; each: 2 configs x {baseline solid, INC dashed} + the NVLink line.

The fixed scale-out (inter-node) fabric bandwidth is selectable via
`--internode_gbps {100,200}` (default 100). Outputs are suffixed `_ib<N>` so the
variants coexist; at 200 the fabric pipe matches the scale-out NIC exactly.

Reproduce (Docker, sim-only image):
  docker build -f simulation-scripts/Dockerfile -t atlahs-sim .
  docker run --rm -v $(pwd):/workspace atlahs-sim build
  docker run --rm -v $(pwd):/workspace atlahs-sim run intranode_linkspeed_sweep --validate
  docker run --rm -v $(pwd):/workspace atlahs-sim run intranode_linkspeed_sweep                      # inter-node 100 Gbps
  docker run --rm -v $(pwd):/workspace atlahs-sim run intranode_linkspeed_sweep --internode_gbps 200 # inter-node 200 Gbps
Local (binaries built in-tree; needs numpy/scipy/tqdm/matplotlib on PATH python):
  python3 experiments/intranode_linkspeed_sweep/run.py --validate
"""
import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from common import goal, paths, report, sim  # noqa: E402

EXP_NAME = "intranode_linkspeed_sweep"

# Zhiyi-matched Llama-2 7B per-layer dims (see module docstring).
MODEL = dict(hidden=4096, ffn=11008, heads=32, kv_heads=32, seq_len=4096, batch=1)
COMPUTE_MODEL = "h100"     # realistic compute floor; identical in both arms
INC_CONTEXTS = "tp"        # INC only on tensor-parallel collectives

TOTAL_GPUS = 16
# 16-GPU configs, split into two plots by pipeline degree. gpus/node = TP.
CONFIGS = [
    dict(plot="pp1", tp=4, dp=4, pp=1),
    dict(plot="pp1", tp=2, dp=8, pp=1),
    dict(plot="pp2", tp=4, dp=2, pp=2),
    dict(plot="pp2", tp=2, dp=4, pp=2),
]
# Only rates that are EXACT in the network: htsim stores a link's rate as
# integer picoseconds per byte (ps/B = 8000/Gbps), so a swept rate must divide
# 8000 or the wire quantises to a different speed than the flag (e.g. 3200 Gbps
# -> 2.5 ps/B -> truncated, no longer the nominal rate). run_exp() rejects
# non-divisors loudly.
DEFAULT_SPEEDS_GBPS = [100, 200, 400, 800, 1600, 2000, 4000, 8000]
NVLINK_GBPS = 3600         # reference line only (NOT simulated: 3600 is not exact in ps/B)

# Intranode (scale-up) topo: GENERATED per (TP, rate) -- a single-switch
# crossbar with EXACTLY `gpus_per_node = TP` hosts (the sim indexes a
# per-domain vector of that size, so it MUST match) whose pipes run at the
# swept rate itself, so the fabric and -intranode_linkspeed agree at every
# point. Format mirrors the committed scaleup_single_switch_* topos: degenerate
# 2-tier encoding (the FatTree parser rejects Tiers=1), 50 ns links / 300 ns
# switch (the thesis-standard latencies). SO_TOPO is the fixed scale-out fabric
# (DP/PP) with 16 hosts = the total GPU count that `-nodes` must equal (this sim
# uses -nodes for TOTAL endpoints and -num_gpus_per_node for the domain width).
# SO_TOPO is NON-BLOCKING (single switch, same 100 Gbps links): the earlier
# 2:1-oversubscribed tree16 penalised TP4 specifically (its DP ring lands one-per-
# rack, so 100% of DP traffic hit the squeezed cross-rack uplinks) and injected
# ECMP routing noise — both confounds of the intranode sweep. Non-blocking removes
# the exit-ramp bottleneck without changing link speed (structure, not speed).
SU_TOPO_TEMPLATE = """\
# scaleup_single_switch_{tp}_{gbps}Gbps.topo (generated by {exp})
# {tp}-host NVLink-class scale-up domain: one non-blocking crossbar (Podsize==
# Nodes==Radix_Down => 1 ToR, every pair one hop). Pipes at the SWEPT rate so
# the fabric and -intranode_linkspeed agree (no in-series ceiling). Degenerate
# 2-tier encoding because the FatTree parser rejects Tiers=1.
Nodes {tp}
Tiers 2
Podsize {tp}

Tier 0
Downlink_speed_Gbps {gbps}
Radix_Down {tp}
Radix_Up 1
Downlink_Latency_ns 50
Switch_Latency_ns 300
Oversubscribed {tp}

Tier 1
Downlink_speed_Gbps {gbps}
Radix_Down 1
Downlink_Latency_ns 50
Switch_Latency_ns 300
"""


def su_topo_for(tp, gbps, tmpdir):
    """Write (idempotently) and return the per-rate intranode .topo path."""
    d = os.path.join(tmpdir, "topos")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"scaleup_single_switch_{tp}_{gbps}Gbps.topo")
    body = SU_TOPO_TEMPLATE.format(tp=tp, gbps=gbps, exp=EXP_NAME)
    if not (os.path.isfile(p) and open(p).read() == body):
        with open(p, "w") as f:
            f.write(body)
    return p


# Scale-out topo for the INTERNODE sweep: same 16-host non-blocking single
# switch as the committed tree16_nonblocking_{100,200}Gbps.topo, generated per
# swept rate so the fabric pipes AND the scale-out NIC (-linkspeed) carry the
# same speed (the intranode-sweep mode keeps its fixed 100/200 topos + 200 Gbps
# NIC untouched, for comparability with the existing sweep_ib<N>.csv results).
SO_TOPO_TEMPLATE = """\
# tree16_nonblocking_{gbps}Gbps.topo (generated by {exp})
# Scale-out fabric: all 16 hosts on ONE non-blocking switch (Podsize==Nodes==
# Radix_Down => 1 ToR, every pair one hop, no oversubscription). Pipes at the
# swept inter-node rate; the scale-out NIC (-linkspeed) is set to the same
# value, so effective inter-node bandwidth == the nominal rate exactly.
Nodes 16
Tiers 2
Podsize 16

Tier 0
Downlink_speed_Gbps {gbps}
Radix_Down 16
Radix_Up 1
Downlink_Latency_ns 1
Switch_Latency_ns 0
Oversubscribed 16

Tier 1
Downlink_speed_Gbps {gbps}
Radix_Down 1
Downlink_Latency_ns 1
Switch_Latency_ns 0
"""


def so_topo_for(gbps, tmpdir):
    """Write (idempotently) and return the per-rate scale-out .topo path."""
    d = os.path.join(tmpdir, "topos")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"tree16_nonblocking_{gbps}Gbps.topo")
    body = SO_TOPO_TEMPLATE.format(gbps=gbps, exp=EXP_NAME)
    if not (os.path.isfile(p) and open(p).read() == body):
        with open(p, "w") as f:
            f.write(body)
    return p


def _require_exact_rates(speeds_gbps, what):
    """TODO guardrail (dev): htsim stores link rate as integer ps/B (= 8000/Gbps);
    a swept rate must divide 8000 or the wire quantises to a different speed."""
    bad = [g for g in speeds_gbps if 8000 % g]
    if bad:
        sys.exit(f"non-exact {what} speed(s) {bad} Gbps: rates must divide 8000 "
                 f"(integer ps/B), e.g. 100, 200, 400, 800, 1600, 2000, 4000, 8000")


# --- Compute time from the trace ------------------------------------------------
# The generator's calc ops carry H100-roofline durations (ns) that depend only on
# the model math and the parallelism split -- NOT on the swept link speeds and NOT
# on the arm (baseline and INC traces share the same calc ops). So the per-rank
# compute per iteration is one fixed number per config, parsed once per config
# from the generated .goal and annotated on every plot as the compute/communication
# split.
_CALC = re.compile(r"^l\d+: calc (\d+)\b")


def compute_ns_per_iter(goal_path, iters):
    """(max_ns, mean_ns) of per-rank summed `calc` durations per iteration."""
    per_rank = []
    cur = None
    with open(goal_path) as f:
        for ln in f:
            if ln.startswith("rank "):
                cur = 0
            elif ln.startswith("}"):
                if cur is not None:
                    per_rank.append(cur)
                    cur = None
            elif cur is not None:
                m = _CALC.match(ln)
                if m:
                    cur += int(m.group(1))
    if not per_rank:
        return None, None
    return (max(per_rank) // iters, sum(per_rank) // len(per_rank) // iters)


def compute_only(cfg, layers, iters, tmpdir):
    """Generate ONLY the baseline .goal for cfg and return compute_ns_per_iter.
    Used by plot-only paths on CSVs that predate the compute columns."""
    py = sys.executable
    d = os.path.join(tmpdir, cfg_tag(cfg) + "_computeonly")
    graphs = os.path.join(d, "graphs")
    os.makedirs(graphs, exist_ok=True)
    _run_gen([py, "-m", "simple_sim.llama3_training",
              "--tp", str(cfg["tp"]), "--dp", str(cfg["dp"]), "--pp", str(cfg["pp"]),
              "--num-layers", str(layers), "--seq-len", str(MODEL["seq_len"]),
              "--ffn", str(MODEL["ffn"]), "--hidden", str(MODEL["hidden"]),
              "--heads", str(MODEL["heads"]), "--kv-heads", str(MODEL["kv_heads"]),
              "--batch", str(MODEL["batch"]), "--iters", str(iters),
              "--graphs-dir", graphs], emit_inc=False)
    g = os.path.join(d, "base.goal")
    _run_gen([py, "simple_sim2goal.py", "--graphs-dir", graphs,
              "--out-goal", g], emit_inc=False)
    return compute_ns_per_iter(g, iters)


def compute_map_from_rows(rows, layers, iters, tmpdir):
    """cfg_tag -> compute_ns_per_iter (max rank), from the CSV when present,
    regenerated from the deterministic generator otherwise."""
    cmap = {}
    tags = {r["config"] for r in rows}
    for cfg in CONFIGS:
        if cfg_tag(cfg) not in tags:
            continue
        vals = {r.get("compute_ns_per_iter", "") for r in rows if r["config"] == cfg_tag(cfg)}
        vals.discard("")
        if vals:
            cmap[cfg_tag(cfg)] = int(float(next(iter(vals))))
        else:
            c_max, _ = compute_only(cfg, layers, iters, tmpdir)
            if c_max is not None:
                cmap[cfg_tag(cfg)] = c_max
    return cmap


def _annotate_compute(ax, cfgs, cmap, rows, hlines):
    """Corner box with the fixed per-rank compute time + its share range over the
    plotted makespans; optionally a dotted horizontal compute-floor line per config
    (time plots only -- the y axis must be seconds/iteration)."""
    colors = {"pp1_tp4_dp4_pp1": "#1f77b4", "pp1_tp2_dp8_pp1": "#ff7f0e",
              "pp2_tp4_dp2_pp2": "#1f77b4", "pp2_tp2_dp4_pp2": "#ff7f0e"}
    lines = []
    for cfg in cfgs:
        tag = cfg_tag(cfg)
        if tag not in cmap:
            continue
        comp_s = cmap[tag] / 1e9
        tpis = [float(r["time_per_iter_s"]) for r in rows
                if r["config"] == tag and r.get("time_per_iter_s")]
        if not tpis:
            continue
        lo, hi = 100 * comp_s / max(tpis), 100 * comp_s / min(tpis)
        lines.append(f"{cfg_label(cfg)}: {comp_s * 1e3:.2f} ms ({lo:.1f}–{hi:.1f}% of iter)")
        # Draw the compute floor only when it lands inside the plotted range --
        # at a few % share it sits far below the makespans and a line would
        # either be invisible or squash the axis.
        ymin, ymax = ax.get_ylim()
        if hlines and ymin <= comp_s <= ymax:
            ax.axhline(comp_s, color=colors.get(tag, "grey"), ls=(0, (1, 2)),
                       lw=1.1, alpha=0.8)
    if lines:
        ax.text(0.02, 0.02, "per-rank compute / iter (fixed, max rank):\n" + "\n".join(lines),
                transform=ax.transAxes, fontsize=8, va="bottom", ha="left",
                bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#999999", alpha=0.85))
# Scale-out (inter-node) fabric, selectable via --internode_gbps. Non-blocking
# 16-host single switch at the chosen per-host link rate. At 200 the fabric pipe
# matches the scale-out NIC (SO_LINKSPEED_MBPS below) exactly, so effective
# inter-node bandwidth == 200 with no NIC/fabric mismatch; at 100 the 100 Gbps
# pipe is the binding constraint (NIC over-provisioned, effective 100).
SO_TOPO_BY_GBPS = {100: "tree16_nonblocking_100Gbps.topo",
                   200: "tree16_nonblocking_200Gbps.topo"}
SIM_END_NS = 10 ** 12   # ceiling >> any iteration makespan; the sim stops at completion
SO_LINKSPEED_MBPS = 200000   # scale-out NIC rate (200 Gbps); the fabric pipe caps the effective rate
QSIZE = 1000000              # scale-out buffer (bytes)

# Intranode CC bypass (AA-plan-Intranode-CC-Bypass): the scale-up domain models
# NVLink, which has NO end-to-end congestion control -- only NIC line-rate
# serialization + lossless PFC. So the intranode tier runs `-intranode_cc none`
# (plain UecSrc, native CONSTANT no-op window), while the scale-out fabric KEEPS
# DCTCP (`-pcm_enable`) since it is lossy and would RTO-collapse without it.
# PFC thresholds and the intranode queue are NOT hand-picked constants any more:
# they are DERIVED per generated fabric by sim.pfc_config() -- XOFF = 1 BDP at
# the fabric's own rate minus the rate-derived pause-propagation headroom,
# XON = 0.8 x XOFF, and the intranode queue = radix x BDP (the shared-buffer
# egress cap) -- the same provisioning rule the isolation experiments use
# (thesis Table 4.1), applied at every swept rate.
INTRANODE_CC = "none"

# SCALE-OUT CC only (the intranode is CC-free, see above). The decomposed
# baseline's large (seq-4096, ~33 MB) DP/PP collectives overflow the lossy
# scale-out queue without real sender pacing, and the makespan then becomes
# buffer-dependent (an artifact). -pcm_enable + this DCTCP CC config (as in the
# reference scripts/run_goal_workloads_exp.py that produced the target plot) paces
# scale-out injection -> 0 lossless warnings, 0 retransmits. The _v2 config loads
# libuec_dctcp_v2.so from pcm/build/lib (added to LD_LIBRARY_PATH at run time).
CC_CONFIG_NAME = "pcm_cc_config_all_uec_dctcp_v2.json"
PCM_CC_CONFIG = os.environ.get(
    "PCM_CC_CONFIG",
    os.path.join(paths.WORKSPACE, "scripts", "pcm_cc_configs", CC_CONFIG_NAME))
PCM_LIB_DIR = os.environ.get(
    "PCM_LIB_DIR",
    os.path.join(os.path.dirname(os.path.dirname(paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH)), "lib"))

CSV_FIELDS = ["plot", "config", "tp", "dp", "pp", "arm", "intranode_linkspeed_gbps",
              "intranode_linkspeed_mbps", "makespan_ns", "iters", "time_per_iter_s",
              "compute_ns_per_iter", "compute_ns_per_iter_mean",
              "drops", "status", "su_topo", "so_topo", "so_gbps", "intranode_cc",
              "pfc_high", "pfc_low", "intranode_q", "compute_model", "engine", "command"]


def _engine_id():
    """Provenance: record WHICH engine commit produced a row (the G72 suite's
    unrecorded '@ current build' made its runs unreconstructable). A dirty
    working tree is flagged, since the binary may not match any commit."""
    sub = os.path.join(paths.WORKSPACE, "sim", "pcm-sdk_zhiyi")
    try:
        r = subprocess.run(["git", "-C", sub, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            d = subprocess.run(["git", "-C", sub, "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10)
            dirty = "-dirty" if (d.returncode == 0 and d.stdout.strip()) else ""
            return f"pcm-sdk@{r.stdout.strip()}{dirty}"
    except OSError:
        pass
    return "pcm-sdk"


def _preflight_cc():
    """Loud preflight for the scale-out DCTCP stack shared by both sweep modes."""
    if not os.path.isfile(PCM_CC_CONFIG):
        sys.exit(f"pcm CC config not found: {PCM_CC_CONFIG} (set PCM_CC_CONFIG)")
    _so = os.path.join(PCM_LIB_DIR, "libuec_dctcp_v2.so")
    if not os.path.isfile(_so):
        sys.exit(f"pcm CC library not found: {_so}\n"
                 "(build the pcm CC algorithm libraries, or set PCM_LIB_DIR)")


def cfg_tag(cfg):
    return f"{cfg['plot']}_tp{cfg['tp']}_dp{cfg['dp']}_pp{cfg['pp']}"


def cfg_label(cfg):
    return f"TP{cfg['tp']}·DP{cfg['dp']}·PP{cfg['pp']}"


def _gen_env(emit_inc):
    env = dict(os.environ)
    env["COMPUTE_MODEL"] = COMPUTE_MODEL
    env["INC_CONTEXTS"] = INC_CONTEXTS
    env["EMIT_INC"] = "1" if emit_inc else "0"   # goal.py: only "1" enables INC
    return env


def _run_gen(cmd, emit_inc):
    """Run a generator subprocess from GENERATOR_DIR; fail loudly."""
    r = subprocess.run(cmd, env=_gen_env(emit_inc), cwd=paths.GENERATOR_DIR,
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"generator step failed (emit_inc={emit_inc}):\n  {' '.join(cmd)}\n"
                 f"{r.stderr[-1000:]}")
    return r


def generate_arms(cfg, layers, iters, tmpdir):
    """Build the per-device graphs once, emit + compile both arms.

    Returns dict(base_bin, inc_bin, groups, n_groups, group_size)."""
    # TODO guardrail (dev): the whole point is a 16-GPU domain split TP*DP*PP.
    assert cfg["tp"] * cfg["dp"] * cfg["pp"] == TOTAL_GPUS, \
        f"{cfg_tag(cfg)}: TP*DP*PP != {TOTAL_GPUS}"
    py = sys.executable
    d = os.path.join(tmpdir, cfg_tag(cfg))
    graphs = os.path.join(d, "graphs")
    os.makedirs(graphs, exist_ok=True)

    # 1. per-device graphs (structure shared by both arms)
    _run_gen([py, "-m", "simple_sim.llama3_training",
              "--tp", str(cfg["tp"]), "--dp", str(cfg["dp"]), "--pp", str(cfg["pp"]),
              "--num-layers", str(layers), "--seq-len", str(MODEL["seq_len"]),
              "--ffn", str(MODEL["ffn"]), "--hidden", str(MODEL["hidden"]),
              "--heads", str(MODEL["heads"]), "--kv-heads", str(MODEL["kv_heads"]),
              "--batch", str(MODEL["batch"]), "--iters", str(iters),
              "--graphs-dir", graphs], emit_inc=False)

    base_goal = os.path.join(d, "base.goal")
    inc_goal = os.path.join(d, "inc.goal")
    groups = os.path.join(d, "inc.groups")
    # 2. baseline (decomposed) and 3. INC (coll + .groups)
    _run_gen([py, "simple_sim2goal.py", "--graphs-dir", graphs,
              "--out-goal", base_goal], emit_inc=False)
    _run_gen([py, "simple_sim2goal.py", "--graphs-dir", graphs,
              "--out-goal", inc_goal, "--groups", groups], emit_inc=True)

    base_bin, inc_bin = base_goal[:-5] + ".bin", inc_goal[:-5] + ".bin"
    goal.compile_goal(base_goal, base_bin)
    goal.compile_goal(inc_goal, inc_bin)

    # Validate the derived scale-up grouping = the TP groups, then convert the
    # generator's GLOBAL-rank groups to the NODE-LOCAL ids the simulator expects
    # (rank % gpn), asserting each group is contained in one scale-up domain.
    gpn = cfg["tp"]
    with open(groups) as f:
        grp = [[int(x) for x in ln.split()] for ln in f if ln.strip()]
    n_groups, sizes = len(grp), {len(g) for g in grp}
    # TODO guardrail (dev): INC groups must be exactly the (domains x TP) layout.
    assert n_groups == cfg["dp"] * cfg["pp"] and sizes == {gpn}, (
        f"{cfg_tag(cfg)}: expected {cfg['dp'] * cfg['pp']} INC group(s) of size "
        f"{gpn}, got {n_groups} group(s) of size(s) {sorted(sizes)}")
    local_groups = os.path.join(d, "inc_local.groups")
    with open(local_groups, "w") as f:
        for g in grp:
            domains = {r // gpn for r in g}
            assert len(domains) == 1, \
                f"{cfg_tag(cfg)}: INC group {sorted(g)} spans scale-up domains {sorted(domains)}"
            f.write(" ".join(str(r % gpn) for r in sorted(g)) + "\n")
    comp_max, comp_mean = compute_ns_per_iter(base_goal, iters)
    return dict(base_bin=base_bin, inc_bin=inc_bin, groups=local_groups,
                n_groups=n_groups, group_size=gpn,
                compute_ns=comp_max, compute_ns_mean=comp_mean)


def run_one_sim(binp, su_topo, so_topo, gpn, groups, mbps, pfc, timeout,
                so_mbps=SO_LINKSPEED_MBPS):
    """One multi-domain htsim run with PER-TIER CC. Two-tier: scale-out fabric
    (DP/PP) keeps DCTCP-via-PCM (lossy fabric); scale-up domain (TP, the swept
    intranode link) is CC-FREE (`-intranode_cc none`: line-rate NIC + lossless
    PFC, no window loop) to model NVLink faithfully. The INC `coll` arm is paced
    through the same intranode NIC as the baseline, so the A/B is fair.
    pfc = the sim.pfc_config(su_topo) triple (high, low, egress_cap). so_mbps =
    the scale-out NIC rate (-linkspeed); the internode-sweep mode sets it equal
    to the generated scale-out topo's pipe rate. Returns
    (makespan_ns|None, drop/lossless-violation count, status, command)."""
    pfc_high, pfc_low, intranode_q = pfc
    cmd = [paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH, "-goal", binp,
           "-nodes", str(TOTAL_GPUS), "-num_gpus_per_node", str(gpn),
           "-topo", so_topo, "-linkspeed", str(so_mbps), "-q", str(QSIZE),
           "-intranode_topo", su_topo, "-intranode_linkspeed", str(mbps),
           "-intranode_q", str(intranode_q), "-strat", "ecmp_host", "-seed", "42",
           "-mtu", str(sim.MTU_DEFAULT), "-paths", "128", "-end", str(SIM_END_NS),
           "-sender_cc_only",
           "-intranode_queue_type", "lossless_input",
           "-intranode_cc", INTRANODE_CC,
           "-lossless_high_pfc", str(pfc_high),
           "-lossless_low_pfc", str(pfc_low),
           "-pcm_enable",
           "-pcm_cc_config_file", PCM_CC_CONFIG, "-pcm_sched_poll_delay", "1000",
           "-pcm_handler_delay", "1000"]
    if groups:
        cmd += ["-groups", groups]
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = PCM_LIB_DIR + (os.pathsep + env["LD_LIBRARY_PATH"]
                                            if env.get("LD_LIBRARY_PATH") else "")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, -1, "timeout", " ".join(cmd)
    combined = p.stdout + "\n" + p.stderr
    # sim.DROP also matches "LOSSLESS not working" -> a nonzero count flags an
    # invalid (buffer-artifact) run, exactly what the CC config must prevent.
    drops = len(sim.DROP.findall(combined))
    status = "ok" if p.returncode == 0 else f"rc={p.returncode}"
    return sim.parse_makespan(p.stdout), drops, status, " ".join(cmd)


def validate(layers, iters, tmpdir):
    """No-sim: build every config/arm, check layout + INC grouping, compile."""
    goal.require_txt2bin()
    goal.require_generator()
    report.print_info(f"validation: {len(CONFIGS)} configs x 2 arms (no sim), "
                      f"layers={layers} seq={MODEL['seq_len']} iters={iters}")
    print(f"{'config':>16} {'gpus/node':>9} {'nodes':>6} {'inc_groups':>11} {'grp_size':>8}")
    ok = True
    for cfg in CONFIGS:
        try:
            a = generate_arms(cfg, layers, iters, tmpdir)
            print(f"{cfg_tag(cfg):>16} {cfg['tp']:>9} {cfg['dp'] * cfg['pp']:>6} "
                  f"{a['n_groups']:>11} {a['group_size']:>8}   OK")
        except SystemExit as e:
            ok = False
            print(f"{cfg_tag(cfg):>16}   FAIL: {e}")
    print("validation:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def run_exp(speeds_gbps, layers, iters, tmpdir, timeout, do_plot, internode_gbps):
    goal.require_txt2bin()
    goal.require_generator()
    sim.require_simulator()
    _require_exact_rates(speeds_gbps, "intranode link")
    so_topo_name = SO_TOPO_BY_GBPS[internode_gbps]
    so_topo = paths.topo(so_topo_name)
    if not os.path.isfile(so_topo):
        sys.exit(f"topology not found: {so_topo}")
    _preflight_cc()

    out_dir = paths.results_dir(EXP_NAME)
    os.makedirs(out_dir, exist_ok=True)
    # Suffix outputs by inter-node bandwidth so the 100 and 200 Gbps variants coexist.
    csv_path = os.path.join(out_dir, f"sweep_ib{internode_gbps}.csv")
    # A full sweep is one self-contained result set: start a FRESH CSV (CsvAppender
    # appends, so without this a re-run would mix rows from the previous config).
    if os.path.exists(csv_path):
        os.remove(csv_path)
    rows = []
    cmap = {}
    engine = _engine_id()
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        for cfg in CONFIGS:
            gpn = cfg["tp"]
            report.print_info(f"=== {cfg_tag(cfg)}  (gpus/node={gpn}, "
                              f"domains={cfg['dp'] * cfg['pp']}, -nodes={TOTAL_GPUS}) ===")
            arms = generate_arms(cfg, layers, iters, tmpdir)
            if arms["compute_ns"] is not None:
                cmap[cfg_tag(cfg)] = arms["compute_ns"]
            for gbps in speeds_gbps:
                mbps = gbps * 1000
                su_topo = su_topo_for(gpn, gbps, tmpdir)
                pfc = sim.pfc_config(su_topo)
                for arm in ("baseline", "inc"):
                    binp = arms["inc_bin"] if arm == "inc" else arms["base_bin"]
                    groups = arms["groups"] if arm == "inc" else None
                    fin, drops, status, cmd = run_one_sim(
                        binp, su_topo, so_topo, gpn, groups, mbps, pfc, timeout)
                    tpi = (fin / iters / 1e9) if (fin and status == "ok") else None
                    row = {"plot": cfg["plot"], "config": cfg_tag(cfg),
                           "tp": cfg["tp"], "dp": cfg["dp"], "pp": cfg["pp"], "arm": arm,
                           "intranode_linkspeed_gbps": gbps, "intranode_linkspeed_mbps": mbps,
                           "makespan_ns": fin or "", "iters": iters,
                           "time_per_iter_s": f"{tpi:.9f}" if tpi is not None else "",
                           "compute_ns_per_iter": arms["compute_ns"] or "",
                           "compute_ns_per_iter_mean": arms["compute_ns_mean"] or "",
                           "drops": drops, "status": status,
                           "su_topo": os.path.basename(su_topo),
                           "so_topo": so_topo_name, "so_gbps": internode_gbps,
                           "intranode_cc": INTRANODE_CC, "pfc_high": pfc[0],
                           "pfc_low": pfc[1], "intranode_q": pfc[2],
                           "compute_model": COMPUTE_MODEL, "engine": engine, "command": cmd}
                    out.write(row)
                    rows.append(row)
                b = next((r for r in rows if r["config"] == cfg_tag(cfg)
                          and r["intranode_linkspeed_gbps"] == gbps and r["arm"] == "baseline"), None)
                i = next((r for r in rows if r["config"] == cfg_tag(cfg)
                          and r["intranode_linkspeed_gbps"] == gbps and r["arm"] == "inc"), None)
                warn = ""
                if (b and b["drops"]) or (i and i["drops"]):
                    warn = f"  WARN drops b={b['drops'] if b else '-'} i={i['drops'] if i else '-'}"
                print(f"  {gbps:>6} Gbps  base {str(b['time_per_iter_s']) if b else '-':>13} s  "
                      f"INC {str(i['time_per_iter_s']) if i else '-':>13} s{warn}")
    report.print_success(f"wrote {len(rows)} rows to {csv_path}")
    if do_plot:
        plot_from_rows(rows, out_dir, speeds_gbps, internode_gbps, cmap)
    return 0


def _read_csv(csv_path):
    import csv
    with open(csv_path) as f:
        return list(csv.DictReader(f))


def plot_from_rows(rows, out_dir, speeds_gbps, internode_gbps, cmap=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def val(r, key):
        v = r.get(key, "")
        return None if v in ("", None) else float(v)

    # stable color per config, consistent markers per arm
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
    speed_set = set(speeds_gbps)   # only plot the requested speeds (CSV may hold more)
    made = []
    for plot_key, title in (
            ("pp1", f"Tuning Intranode Link Speed (PP=1, inter-node {internode_gbps} Gbps)"),
            ("pp2", f"Tuning Intranode Link Speed (PP=2, inter-node {internode_gbps} Gbps)")):
        cfgs = [c for c in CONFIGS if c["plot"] == plot_key]
        fig, ax = plt.subplots(figsize=(8, 6))
        for ci, cfg in enumerate(cfgs):
            color = colors[ci % len(colors)]
            for arm, ls, mk in (("baseline", "-", "o"), ("inc", "--", "^")):
                pts = [(int(r["intranode_linkspeed_gbps"]), val(r, "time_per_iter_s"))
                       for r in rows if r["config"] == cfg_tag(cfg) and r["arm"] == arm
                       and int(r["intranode_linkspeed_gbps"]) in speed_set]
                pts = sorted((x, y) for x, y in pts if y is not None)
                if not pts:
                    continue
                xs, ys = zip(*pts)
                ax.plot(xs, ys, ls=ls, marker=mk, color=color, markersize=6,
                        label=f"{cfg_label(cfg)} {'INC' if arm == 'inc' else 'baseline'}")
        ax.axvline(NVLINK_GBPS, color="#7b1fa2", ls="--", lw=1.3,
                   label=f"NVLink Bandwidth ({NVLINK_GBPS} Gbps)")
        ax.set_xscale("log", base=2)
        # Tick the swept speeds, but drop the NVLink point (3600, shown as the vline)
        # and prune any label that crowds its neighbour on the log axis (keep the
        # larger) so e.g. 3200/3600 or 6400/8000 don't overprint.
        ticks = []
        for s in [x for x in speeds_gbps if x != NVLINK_GBPS]:
            if ticks and s / ticks[-1] < 1.5:
                ticks[-1] = s
            else:
                ticks.append(s)
        ax.set_xticks(ticks)
        ax.set_xticklabels([str(s) for s in ticks])
        ax.minorticks_off()
        ax.set_xlabel("Intranode Link Speed (Gbps)", fontsize=13)
        ax.set_ylabel("Time / Training Iteration (s)", fontsize=13)
        ax.set_title(title, fontsize=14)
        ax.grid(True, which="both", ls=":", alpha=0.5)
        ax.legend(fontsize=9)
        _annotate_compute(ax, cfgs, cmap or {},
                          [r for r in rows if r["plot"] == plot_key], hlines=True)
        fig.tight_layout()
        png = os.path.join(out_dir, f"intranode_linkspeed_{plot_key}_ib{internode_gbps}.png")
        fig.savefig(png, dpi=150)
        plt.close(fig)
        made.append(png)
    report.print_success("plots: " + ", ".join(made))


def plot_speedup(out_dir, speeds_gbps, ib, layers, iters, tmpdir):
    """Plot INC speedup (baseline_time / INC_time) vs intranode link speed for the
    PP=1 configs at a SINGLE inter-node bandwidth (reads sweep_ib<ib>.csv). Two
    lines: TP4·DP4 and TP2·DP8. 1.0 = no speedup; >1 = INC faster. No sim (the
    compute-share annotation regenerates traces if the CSV predates the compute
    columns)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    p = os.path.join(out_dir, f"sweep_ib{ib}.csv")
    if not os.path.isfile(p):
        sys.exit(f"no {p} (run the sweep first: ... --internode_gbps {ib})")
    rows = _read_csv(p)
    cmap = compute_map_from_rows(rows, layers, iters, tmpdir)

    def tval(cfg, gbps, arm):
        for r in rows:
            if (r["config"] == cfg_tag(cfg) and int(r["intranode_linkspeed_gbps"]) == gbps
                    and r["arm"] == arm):
                v = r.get("time_per_iter_s", "")
                return float(v) if v not in ("", None) else None
        return None

    colors = {"pp1_tp4_dp4_pp1": "#1f77b4", "pp1_tp2_dp8_pp1": "#ff7f0e"}
    pp1_cfgs = [c for c in CONFIGS if c["plot"] == "pp1"]

    fig, ax = plt.subplots(figsize=(8, 6))
    for cfg in pp1_cfgs:
        pts = []
        for gbps in speeds_gbps:
            b = tval(cfg, gbps, "baseline")
            i = tval(cfg, gbps, "inc")
            if b and i:
                pts.append((gbps, b / i))
        pts.sort()
        if not pts:
            continue
        xs, ys = zip(*pts)
        ax.plot(xs, ys, ls="-", marker="o", markersize=6,
                color=colors.get(cfg_tag(cfg), "#2ca02c"), label=cfg_label(cfg))
    ax.axhline(1.0, color="grey", ls=":", lw=1.2, label="no speedup (1.0×)")
    ax.axvline(NVLINK_GBPS, color="#7b1fa2", ls="--", lw=1.3,
               label=f"NVLink Bandwidth ({NVLINK_GBPS} Gbps)")
    ax.set_xscale("log", base=2)
    ticks = []
    for s in [x for x in speeds_gbps if x != NVLINK_GBPS]:
        if ticks and s / ticks[-1] < 1.5:
            ticks[-1] = s
        else:
            ticks.append(s)
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(s) for s in ticks])
    ax.minorticks_off()
    ax.set_xlabel("Intranode Link Speed (Gbps)", fontsize=13)
    ax.set_ylabel("Speedup  (baseline time / INC time)", fontsize=13)
    ax.set_title(f"INC Speedup over Baseline (PP=1, inter-node {ib} Gbps)", fontsize=14)
    ax.grid(True, which="both", ls=":", alpha=0.5)
    ax.legend(fontsize=10)
    _annotate_compute(ax, pp1_cfgs, cmap, [r for r in rows if r["plot"] == "pp1"],
                      hlines=False)
    fig.tight_layout()
    png = os.path.join(out_dir, f"intranode_linkspeed_speedup_pp1_ib{ib}.png")
    fig.savefig(png, dpi=150)
    plt.close(fig)
    report.print_success(f"speedup plot: {png}")
    return png


# --- Internode sweep mode (fixed intranode rate) --------------------------------

DEFAULT_SO_SPEEDS_GBPS = [100, 200, 400, 800, 1600]
TODAY_NIC_GBPS = 800   # current-gen per-GPU scale-out NIC (XDR IB / 800GbE), plot marker


def plot_internode(rows, out_dir, so_speeds, intranode_gbps, cmap):
    """Time + speedup plots for the internode sweep (PP=1 configs), x = inter-node
    link speed. Expectation: a faster scale-out fabric shrinks the fixed DP/PP
    floor, so the TP collective is a larger share of the critical path and INC's
    speedup RISES with x."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def val(r, key):
        v = r.get(key, "")
        return None if v in ("", None) else float(v)

    colors = {"pp1_tp4_dp4_pp1": "#1f77b4", "pp1_tp2_dp8_pp1": "#ff7f0e"}
    pp1_cfgs = [c for c in CONFIGS if c["plot"] == "pp1"]
    speed_set = set(so_speeds)
    made = []

    def _xaxis(ax):
        ax.set_xscale("log", base=2)
        ax.set_xticks(so_speeds)
        ax.set_xticklabels([str(s) for s in so_speeds])
        ax.minorticks_off()
        if min(so_speeds) <= TODAY_NIC_GBPS <= max(so_speeds):
            ax.axvline(TODAY_NIC_GBPS, color="#7b1fa2", ls="--", lw=1.3,
                       label=f"current per-GPU NIC ({TODAY_NIC_GBPS} Gbps)")
        ax.set_xlabel("Inter-node Link Speed (Gbps)", fontsize=13)
        ax.grid(True, which="both", ls=":", alpha=0.5)

    # 1) completion time, both arms
    fig, ax = plt.subplots(figsize=(8, 6))
    for cfg in pp1_cfgs:
        color = colors.get(cfg_tag(cfg), "#2ca02c")
        for arm, ls, mk in (("baseline", "-", "o"), ("inc", "--", "^")):
            pts = [(int(r["so_gbps"]), val(r, "time_per_iter_s"))
                   for r in rows if r["config"] == cfg_tag(cfg) and r["arm"] == arm
                   and int(r["so_gbps"]) in speed_set]
            pts = sorted((x, y) for x, y in pts if y is not None)
            if not pts:
                continue
            xs, ys = zip(*pts)
            ax.plot(xs, ys, ls=ls, marker=mk, color=color, markersize=6,
                    label=f"{cfg_label(cfg)} {'INC' if arm == 'inc' else 'baseline'}")
    _xaxis(ax)
    ax.set_ylabel("Time / Training Iteration (s)", fontsize=13)
    ax.set_title(f"Tuning Inter-node Link Speed (PP=1, intranode {intranode_gbps} Gbps)",
                 fontsize=14)
    ax.legend(fontsize=9)
    _annotate_compute(ax, pp1_cfgs, cmap, [r for r in rows if r["plot"] == "pp1"],
                      hlines=True)
    fig.tight_layout()
    png = os.path.join(out_dir, f"internode_linkspeed_time_pp1_su{intranode_gbps}.png")
    fig.savefig(png, dpi=150)
    plt.close(fig)
    made.append(png)

    # 2) INC speedup
    fig, ax = plt.subplots(figsize=(8, 6))
    for cfg in pp1_cfgs:
        pts = []
        for g in so_speeds:
            b = next((val(r, "time_per_iter_s") for r in rows
                      if r["config"] == cfg_tag(cfg) and int(r["so_gbps"]) == g
                      and r["arm"] == "baseline"), None)
            i = next((val(r, "time_per_iter_s") for r in rows
                      if r["config"] == cfg_tag(cfg) and int(r["so_gbps"]) == g
                      and r["arm"] == "inc"), None)
            if b and i:
                pts.append((g, b / i))
        if not pts:
            continue
        xs, ys = zip(*sorted(pts))
        ax.plot(xs, ys, ls="-", marker="o", markersize=6,
                color=colors.get(cfg_tag(cfg), "#2ca02c"), label=cfg_label(cfg))
    ax.axhline(1.0, color="grey", ls=":", lw=1.2, label="no speedup (1.0×)")
    _xaxis(ax)
    ax.set_ylabel("Speedup  (baseline time / INC time)", fontsize=13)
    ax.set_title(f"INC Speedup vs Inter-node Link Speed (PP=1, intranode {intranode_gbps} Gbps)",
                 fontsize=14)
    # center-right sits in the gap between TP4's plateau (top) and TP2's tail
    # (bottom); the default 'best' lands on the compute box in the lower left.
    ax.legend(fontsize=10, loc="center right")
    _annotate_compute(ax, pp1_cfgs, cmap, [r for r in rows if r["plot"] == "pp1"],
                      hlines=False)
    fig.tight_layout()
    png = os.path.join(out_dir, f"internode_linkspeed_speedup_pp1_su{intranode_gbps}.png")
    fig.savefig(png, dpi=150)
    plt.close(fig)
    made.append(png)
    report.print_success("plots: " + ", ".join(made))


def run_internode_exp(so_speeds, layers, iters, tmpdir, timeout, do_plot, intranode_gbps):
    """Fix the intranode (scale-up) rate, sweep the inter-node (scale-out) rate.
    Both the scale-out topo pipes AND the scale-out NIC (-linkspeed) carry the
    swept rate; the intranode topo pipes and -intranode_linkspeed carry the fixed
    rate -- consistent speeds in the network and at the endpoints on BOTH tiers."""
    goal.require_txt2bin()
    goal.require_generator()
    sim.require_simulator()
    _require_exact_rates(so_speeds, "inter-node link")
    _require_exact_rates([intranode_gbps], "intranode link")
    _preflight_cc()

    out_dir = paths.results_dir(EXP_NAME)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f"sweep_internode_su{intranode_gbps}.csv")
    if os.path.exists(csv_path):
        os.remove(csv_path)
    rows = []
    cmap = {}
    engine = _engine_id()
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        for cfg in CONFIGS:
            gpn = cfg["tp"]
            su_topo = su_topo_for(gpn, intranode_gbps, tmpdir)
            pfc = sim.pfc_config(su_topo)
            report.print_info(f"=== {cfg_tag(cfg)}  (gpus/node={gpn}, "
                              f"domains={cfg['dp'] * cfg['pp']}, intranode {intranode_gbps} Gbps) ===")
            arms = generate_arms(cfg, layers, iters, tmpdir)
            if arms["compute_ns"] is not None:
                cmap[cfg_tag(cfg)] = arms["compute_ns"]
            for so_gbps in so_speeds:
                so_topo = so_topo_for(so_gbps, tmpdir)
                for arm in ("baseline", "inc"):
                    binp = arms["inc_bin"] if arm == "inc" else arms["base_bin"]
                    groups = arms["groups"] if arm == "inc" else None
                    fin, drops, status, cmd = run_one_sim(
                        binp, su_topo, so_topo, gpn, groups, intranode_gbps * 1000,
                        pfc, timeout, so_mbps=so_gbps * 1000)
                    tpi = (fin / iters / 1e9) if (fin and status == "ok") else None
                    row = {"plot": cfg["plot"], "config": cfg_tag(cfg),
                           "tp": cfg["tp"], "dp": cfg["dp"], "pp": cfg["pp"], "arm": arm,
                           "intranode_linkspeed_gbps": intranode_gbps,
                           "intranode_linkspeed_mbps": intranode_gbps * 1000,
                           "makespan_ns": fin or "", "iters": iters,
                           "time_per_iter_s": f"{tpi:.9f}" if tpi is not None else "",
                           "compute_ns_per_iter": arms["compute_ns"] or "",
                           "compute_ns_per_iter_mean": arms["compute_ns_mean"] or "",
                           "drops": drops, "status": status,
                           "su_topo": os.path.basename(su_topo),
                           "so_topo": os.path.basename(so_topo), "so_gbps": so_gbps,
                           "intranode_cc": INTRANODE_CC, "pfc_high": pfc[0],
                           "pfc_low": pfc[1], "intranode_q": pfc[2],
                           "compute_model": COMPUTE_MODEL, "engine": engine, "command": cmd}
                    out.write(row)
                    rows.append(row)
                b = next((r for r in rows if r["config"] == cfg_tag(cfg)
                          and r["so_gbps"] == so_gbps and r["arm"] == "baseline"), None)
                i = next((r for r in rows if r["config"] == cfg_tag(cfg)
                          and r["so_gbps"] == so_gbps and r["arm"] == "inc"), None)
                warn = ""
                if (b and b["drops"]) or (i and i["drops"]):
                    warn = f"  WARN drops b={b['drops'] if b else '-'} i={i['drops'] if i else '-'}"
                print(f"  so {so_gbps:>5} Gbps  base {str(b['time_per_iter_s']) if b else '-':>13} s  "
                      f"INC {str(i['time_per_iter_s']) if i else '-':>13} s{warn}")
    report.print_success(f"wrote {len(rows)} rows to {csv_path}")
    if do_plot:
        plot_internode(rows, out_dir, so_speeds, intranode_gbps, cmap)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--speeds", default=",".join(str(x) for x in DEFAULT_SPEEDS_GBPS),
                    help="comma-separated intranode link speeds in Gbps")
    ap.add_argument("--layers", type=int, default=2, help="transformer layers (depth-invariant y-scale)")
    ap.add_argument("--iters", type=int, default=2, help="training iterations (metric divides this out)")
    ap.add_argument("--timeout", type=int, default=1800, help="per-sim-run timeout (s)")
    ap.add_argument("--tmpdir", default="/tmp/intranode_linkspeed_sweep")
    ap.add_argument("--validate", action="store_true", help="build + check layout, no sim")
    ap.add_argument("--no-plot", action="store_true", help="write CSV only, skip PNGs")
    ap.add_argument("--only-plot", action="store_true",
                    help="re-plot from an existing results/<exp>/sweep_ib<N>.csv (no gen, no sim)")
    ap.add_argument("--internode_gbps", type=int, default=100, choices=sorted(SO_TOPO_BY_GBPS),
                    help="intranode mode: scale-out fabric bandwidth in Gbps (picks the SO topo)")
    ap.add_argument("--speedup", action="store_true",
                    help="plot INC speedup (baseline/INC) vs intranode speed for the PP=1 configs "
                         "at --internode_gbps (reads that sweep_ib<N>.csv; no gen, no sim)")
    ap.add_argument("--mode", choices=("intranode", "internode"), default="intranode",
                    help="which link speed to sweep: intranode (default; scale-out fixed via "
                         "--internode_gbps) or internode (intranode fixed via --intranode_gbps, "
                         "scale-out topo AND NIC carry the swept rate)")
    ap.add_argument("--so_speeds", default=",".join(str(x) for x in DEFAULT_SO_SPEEDS_GBPS),
                    help="internode mode: comma-separated inter-node link speeds in Gbps "
                         "(must divide 8000; 800 = current-gen per-GPU NIC, 1600 = next-gen)")
    ap.add_argument("--intranode_gbps", type=int, default=4000,
                    help="internode mode: the FIXED intranode link speed in Gbps")
    args = ap.parse_args()
    speeds = [int(x) for x in args.speeds.split(",")]

    if args.mode == "internode":
        so_speeds = [int(x) for x in args.so_speeds.split(",")]
        out_dir = paths.results_dir(EXP_NAME)
        if args.only_plot:
            csv_path = os.path.join(out_dir, f"sweep_internode_su{args.intranode_gbps}.csv")
            if not os.path.isfile(csv_path):
                sys.exit(f"no CSV to plot: {csv_path}")
            rows = _read_csv(csv_path)
            cmap = compute_map_from_rows(rows, args.layers, args.iters, args.tmpdir)
            plot_internode(rows, out_dir, so_speeds, args.intranode_gbps, cmap)
            return 0
        if args.validate:
            return validate(args.layers, args.iters, args.tmpdir)
        return run_internode_exp(so_speeds, args.layers, args.iters, args.tmpdir,
                                 args.timeout, do_plot=not args.no_plot,
                                 intranode_gbps=args.intranode_gbps)

    if args.speedup:
        out_dir = paths.results_dir(EXP_NAME)
        plot_speedup(out_dir, speeds, args.internode_gbps, args.layers, args.iters,
                     args.tmpdir)
        return 0
    if args.only_plot:
        out_dir = paths.results_dir(EXP_NAME)
        csv_path = os.path.join(out_dir, f"sweep_ib{args.internode_gbps}.csv")
        if not os.path.isfile(csv_path):
            sys.exit(f"no CSV to plot: {csv_path}")
        rows = _read_csv(csv_path)
        cmap = compute_map_from_rows(rows, args.layers, args.iters, args.tmpdir)
        plot_from_rows(rows, out_dir, speeds, args.internode_gbps, cmap)
        return 0
    if args.validate:
        return validate(args.layers, args.iters, args.tmpdir)
    return run_exp(speeds, args.layers, args.iters, args.tmpdir, args.timeout,
                   do_plot=not args.no_plot, internode_gbps=args.internode_gbps)


if __name__ == "__main__":
    sys.exit(main())
