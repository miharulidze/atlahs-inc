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
.topo is pinned at the sweep MAX (SU_TOPO, 12800 Gbps): the flag (NIC/copy-engine)
and the topo fabric pipe are in-series limiters, so with flag <= topo the flag
governs at every point (verified in AA-plan-Intranode-Linkspeed-Sweep). Two PNGs,
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
DEFAULT_SPEEDS_GBPS = [100, 200, 400, 800, 1600, 3200, 3600, 6400]
NVLINK_GBPS = 3600         # kept reference line

# Intranode (scale-up) topo per TP degree: a single-switch crossbar with EXACTLY
# `gpus_per_node = TP` hosts (the sim indexes a per-domain vector of that size, so
# it MUST match), pinned at the sweep MAX (12800 Gbps) so the swept
# -intranode_linkspeed governs under it. SO_TOPO is the fixed scale-out fabric
# (DP/PP) with 16 hosts = the total GPU count that `-nodes` must equal (this sim
# uses -nodes for TOTAL endpoints and -num_gpus_per_node for the domain width).
# SO_TOPO is NON-BLOCKING (single switch, same 100 Gbps links): the earlier
# 2:1-oversubscribed tree16 penalised TP4 specifically (its DP ring lands one-per-
# rack, so 100% of DP traffic hit the squeezed cross-rack uplinks) and injected
# ECMP routing noise — both confounds of the intranode sweep. Non-blocking removes
# the exit-ramp bottleneck without changing link speed (structure, not speed).
SU_TOPO = {4: "scaleup_single_switch_4_12800Gbps.topo",
           2: "scaleup_single_switch_2_12800Gbps.topo"}
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
# DCTCP (`-pcm_enable`) since it is lossy and would RTO-collapse without it. With
# no window loop on the intranode, the lossless queue alone bounds the buffer, so
# the PFC pause threshold must sit BELOW the queue size (else PAUSE can't fire
# before overflow): high=1500 pkt x 4096 B ~= 6.1 MB (~2.4x the 12800 Gbps BDP),
# resume at 1200 pkt, in a 20 MB intranode queue for ample headroom. Validated
# drop-free / violation-free across the whole sweep (see AA-plan §12).
INTRANODE_CC = "none"
LOSSLESS_HIGH_PFC = 1500     # PFC pause threshold (packets); ~2.4x intranode BDP @12800 Gbps
LOSSLESS_LOW_PFC = 1200      # PFC resume threshold (packets)
INTRANODE_QSIZE = 20000000   # intranode buffer (bytes) >> PFC high threshold (~6.1 MB)

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
              "drops", "status", "su_topo", "so_topo", "so_gbps", "intranode_cc",
              "pfc_high", "compute_model", "engine", "command"]


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
    return dict(base_bin=base_bin, inc_bin=inc_bin, groups=local_groups,
                n_groups=n_groups, group_size=gpn)


def run_one_sim(binp, su_topo, so_topo, gpn, groups, mbps, timeout):
    """One multi-domain htsim run with PER-TIER CC. Two-tier: scale-out fabric
    (DP/PP) keeps DCTCP-via-PCM (lossy fabric); scale-up domain (TP, the swept
    intranode link) is CC-FREE (`-intranode_cc none`: line-rate NIC + lossless
    PFC, no window loop) to model NVLink faithfully. The INC `coll` arm is paced
    through the same intranode NIC as the baseline, so the A/B is fair. Returns
    (makespan_ns|None, drop/lossless-violation count, status, command)."""
    cmd = [paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH, "-goal", binp,
           "-nodes", str(TOTAL_GPUS), "-num_gpus_per_node", str(gpn),
           "-topo", so_topo, "-linkspeed", str(SO_LINKSPEED_MBPS), "-q", str(QSIZE),
           "-intranode_topo", su_topo, "-intranode_linkspeed", str(mbps),
           "-intranode_q", str(INTRANODE_QSIZE), "-strat", "ecmp_host", "-seed", "42",
           "-mtu", "4096", "-paths", "128", "-end", str(SIM_END_NS), "-sender_cc_only",
           "-intranode_queue_type", "lossless_input",
           "-intranode_cc", INTRANODE_CC,
           "-lossless_high_pfc", str(LOSSLESS_HIGH_PFC),
           "-lossless_low_pfc", str(LOSSLESS_LOW_PFC),
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
    so_topo_name = SO_TOPO_BY_GBPS[internode_gbps]
    so_topo = paths.topo(so_topo_name)
    needed = [so_topo] + [paths.topo(v) for v in set(SU_TOPO.values())]
    for t in needed:
        if not os.path.isfile(t):
            sys.exit(f"topology not found: {t}")
    if not os.path.isfile(PCM_CC_CONFIG):
        sys.exit(f"pcm CC config not found: {PCM_CC_CONFIG} (set PCM_CC_CONFIG)")
    _so = os.path.join(PCM_LIB_DIR, "libuec_dctcp_v2.so")
    if not os.path.isfile(_so):
        sys.exit(f"pcm CC library not found: {_so}\n"
                 "(build the pcm CC algorithm libraries, or set PCM_LIB_DIR)")

    out_dir = paths.results_dir(EXP_NAME)
    os.makedirs(out_dir, exist_ok=True)
    # Suffix outputs by inter-node bandwidth so the 100 and 200 Gbps variants coexist.
    csv_path = os.path.join(out_dir, f"sweep_ib{internode_gbps}.csv")
    # A full sweep is one self-contained result set: start a FRESH CSV (CsvAppender
    # appends, so without this a re-run would mix rows from the previous config).
    if os.path.exists(csv_path):
        os.remove(csv_path)
    rows = []
    with report.CsvAppender(csv_path, CSV_FIELDS) as out:
        for cfg in CONFIGS:
            gpn = cfg["tp"]
            su_topo = paths.topo(SU_TOPO[gpn])
            report.print_info(f"=== {cfg_tag(cfg)}  (gpus/node={gpn}, "
                              f"domains={cfg['dp'] * cfg['pp']}, -nodes={TOTAL_GPUS}) ===")
            arms = generate_arms(cfg, layers, iters, tmpdir)
            for gbps in speeds_gbps:
                mbps = gbps * 1000
                for arm in ("baseline", "inc"):
                    binp = arms["inc_bin"] if arm == "inc" else arms["base_bin"]
                    groups = arms["groups"] if arm == "inc" else None
                    fin, drops, status, cmd = run_one_sim(
                        binp, su_topo, so_topo, gpn, groups, mbps, timeout)
                    tpi = (fin / iters / 1e9) if (fin and status == "ok") else None
                    row = {"plot": cfg["plot"], "config": cfg_tag(cfg),
                           "tp": cfg["tp"], "dp": cfg["dp"], "pp": cfg["pp"], "arm": arm,
                           "intranode_linkspeed_gbps": gbps, "intranode_linkspeed_mbps": mbps,
                           "makespan_ns": fin or "", "iters": iters,
                           "time_per_iter_s": f"{tpi:.9f}" if tpi is not None else "",
                           "drops": drops, "status": status, "su_topo": SU_TOPO[gpn],
                           "so_topo": so_topo_name, "so_gbps": internode_gbps,
                           "intranode_cc": INTRANODE_CC, "pfc_high": LOSSLESS_HIGH_PFC,
                           "compute_model": COMPUTE_MODEL, "engine": "pcm-sdk", "command": cmd}
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
        plot_from_rows(rows, out_dir, speeds_gbps, internode_gbps)
    return 0


def _read_csv(csv_path):
    import csv
    with open(csv_path) as f:
        return list(csv.DictReader(f))


def plot_from_rows(rows, out_dir, speeds_gbps, internode_gbps):
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
        fig.tight_layout()
        png = os.path.join(out_dir, f"intranode_linkspeed_{plot_key}_ib{internode_gbps}.png")
        fig.savefig(png, dpi=150)
        plt.close(fig)
        made.append(png)
    report.print_success("plots: " + ", ".join(made))


def plot_speedup(out_dir, speeds_gbps, ib):
    """Plot INC speedup (baseline_time / INC_time) vs intranode link speed for the
    PP=1 configs at a SINGLE inter-node bandwidth (reads sweep_ib<ib>.csv). Two
    lines: TP4·DP4 and TP2·DP8. 1.0 = no speedup; >1 = INC faster. No sim."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    p = os.path.join(out_dir, f"sweep_ib{ib}.csv")
    if not os.path.isfile(p):
        sys.exit(f"no {p} (run the sweep first: ... --internode_gbps {ib})")
    rows = _read_csv(p)

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
    fig.tight_layout()
    png = os.path.join(out_dir, f"intranode_linkspeed_speedup_pp1_ib{ib}.png")
    fig.savefig(png, dpi=150)
    plt.close(fig)
    report.print_success(f"speedup plot: {png}")
    return png


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
                    help="scale-out (inter-node) fabric bandwidth in Gbps (picks the SO topo)")
    ap.add_argument("--speedup", action="store_true",
                    help="plot INC speedup (baseline/INC) vs intranode speed for the PP=1 configs "
                         "at --internode_gbps (reads that sweep_ib<N>.csv; no gen, no sim)")
    args = ap.parse_args()
    speeds = [int(x) for x in args.speeds.split(",")]

    if args.speedup:
        out_dir = paths.results_dir(EXP_NAME)
        plot_speedup(out_dir, speeds, args.internode_gbps)
        return 0
    if args.only_plot:
        out_dir = paths.results_dir(EXP_NAME)
        csv_path = os.path.join(out_dir, f"sweep_ib{args.internode_gbps}.csv")
        if not os.path.isfile(csv_path):
            sys.exit(f"no CSV to plot: {csv_path}")
        plot_from_rows(_read_csv(csv_path), out_dir, speeds, args.internode_gbps)
        return 0
    if args.validate:
        return validate(args.layers, args.iters, args.tmpdir)
    return run_exp(speeds, args.layers, args.iters, args.tmpdir, args.timeout,
                   do_plot=not args.no_plot, internode_gbps=args.internode_gbps)


if __name__ == "__main__":
    sys.exit(main())
