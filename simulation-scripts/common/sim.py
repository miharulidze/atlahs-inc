"""pcm-sdk simulator invocation + output parsing shared by the experiments.

run_sim() wraps one htsim_flow_app_atlahs run on a compiled .bin GOAL trace.
The nodes/gpus_per_node split is explicit so both single-domain isolation
(nodes == gpus_per_node == N, everything intranode) and future multi-domain
experiments use the same entry point.
"""
import os
import re
import subprocess
import sys

from common import paths

# The scale-up domain is CC-FREE by construction (-intranode_cc none): it models an
# NVLink-class fabric, where NIC line rate and lossless PFC govern and there is no
# congestion-control window. Without the flag the driver leaves UecSrc::_sender_cc_algo at
# its NSCC default (uec.cpp:63) -- which on a non-blocking crossbar with dedicated ports
# per ring step never throttles, so the flag is numerically neutral here (verified
# bit-identical, model_checks/floor_probe5.sh) -- but the default WOULD bite on a
# contended or oversubscribed scale-up topology, and the chapter claims a CC-free scale-up
# domain, so the command line should say so rather than rely on it not mattering.
# The scale-up per-GPU NIC rate is MANDATORY (-intranode_linkspeed): the .topo
# sets only fabric pipes; omitting the flag defaults to 200 Gbps COPY_ENG and
# silently caps every p2p arm. 4000000 pins the NIC frame time to the pipes'
# realised 2 ps/B (492.3 payload-B/ns).
INTRANODE_LINKSPEED_DEFAULT = 4000000

# Wire MTU, passed as -mtu so the PAYLOAD MSS is a round 4096 B (MSS = MTU - 64).
#
# The pcm driver defaults to 4150, giving MSS = 4086, which leaves the two halves of
# the engine inconsistent: the LogGOPS per-message gap in logsim-interface.cpp:959-961
# computes its own packetisation with packet_size = 4096 and a 4160 B frame, so the gap
# model assumes MSS = 4096 while the wire carries 4086. 4160 makes them agree.
# It also makes f_w linear on the swept sizes -- every message size here is a power of
# two, so 4096 divides them exactly and the ceiling in f_w never rounds.
MTU_DEFAULT = 4160

# PFC thresholds on the scale-up path, DERIVED per fabric rather than fixed.
#
# The rule everyone else uses runs the opposite way from picking a threshold and hoping:
#   headroom = MTU_in_flight + MTU_being_sent + response + 2 x link_delay
#   XOFF     = queue - headroom
# Headroom is the data that keeps arriving after the pause is sent and must still fit;
# the threshold falls out of it. Choosing XOFF first is what produced a value (300) above
# the crossbar's own 270-packet queue, so the queue overflowed before it could ever pause.
#
# Both terms are topology-dependent, so the threshold has to be too. The queue is
# auto-sized to 1 BDP from the fabric's diameter -- 270 packets on the crossbar, 440 on
# the three-tier -- and link_delay is the fabric's own t_l. A single global constant is
# wrong in principle, not merely badly chosen.
#
# For these fabrics headroom is 2*t_l*B + 2 frames + t_sw*B = 208,320 B = 50 frames, so
# ~19% of the crossbar queue and ~11% of the fat tree's. Measured with the derived values
# at the realistic 1x BDP queue: zero lossless warnings on both, AllGather exact to 0.6 ns.
#
# CAVEAT worth keeping: htsim's own BDP arithmetic lands one packet off our reconstruction
# on the crossbar (270 against 269), so we take the conservative side and leave a little
# more headroom than the formula demands. And note that a real scale-up fabric would not
# use PFC at all -- Broadcom's Scale-Up Ethernet and InfiniBand are both credit-based,
# per-class or per-virtual-lane, which is finer-grained than a link-wide pause. PFC is what
# htsim models, and Section 4.1 says so.
FRAME_B = 4160
PFC_HEADROOM_FRAMES = 50


def pfc_thresholds(su_topo):
    """(high, low) in packets for this .topo: XOFF = 1 BDP - headroom, XON = 0.8 x XOFF."""
    tiers, t_l, t_sw, gbps = 2, 50.0, 300.0, 4000.0
    try:
        with open(su_topo) as f:
            for line in f:
                w = line.split()
                if len(w) == 2:
                    if w[0] == "Tiers":                tiers = int(w[1])
                    elif w[0] == "Downlink_Latency_ns": t_l = float(w[1])
                    elif w[0] == "Switch_Latency_ns":   t_sw = float(w[1])
                    elif w[0] == "Downlink_speed_Gbps": gbps = float(w[1])
    except OSError:
        pass
    B = gbps / 8.0                                        # Gb/s -> B/ns
    rtt = 2 * (t_l * 2 * tiers + t_sw * (2 * tiers - 1))  # 2 x diameter latency
    bdp = int(rtt * B / (FRAME_B - 64))                   # queue, in packets, floored
    high = max(2, bdp - PFC_HEADROOM_FRAMES)
    return high, max(1, int(high * 0.8))

# Primary metric: the makespan summary line (verified emitted by
# htsim_flow_app_atlahs). Fallback: max over per-host "Host N: t" lines (only
# printed for <=16 ranks).
MAXFIN = re.compile(r"Maximum finishing time at host \d+:\s*(\d+)")
HOSTLINE = re.compile(r"^Host \d+:\s*(\d+)", re.MULTILINE)
DROP = re.compile(r"drop arriving|drop last from queue|dropped packet|Random Drop|"
                  r"Buffer Drop|Dropping packet|LOSSLESS not working", re.IGNORECASE)


def parse_makespan(stdout):
    m = MAXFIN.search(stdout)
    if m:
        return int(m.group(1))
    hosts = [int(x) for x in HOSTLINE.findall(stdout)]
    return max(hosts) if hosts else None


def run_sim(binpath, so_topo, su_topo, nodes, gpus_per_node, groups=None,
            reduce_compute=0, timeout=600,
            intranode_linkspeed=INTRANODE_LINKSPEED_DEFAULT, end=100000000,
            mcast_pin=-1, mtu=MTU_DEFAULT):
    """One simulator run. Returns (makespan_ns|None, drop_count, status, command).

    mcast_pin: -1 (default) = round-robin INC tree placement; >=0 pins every tree onto
    one aggregation position + core (the PFC/backpressure experiment knob). Only appended
    when >=0, so it needs a pcm binary built with the -mcast_pin flag (else it errors)."""
    _hi, _lo = pfc_thresholds(su_topo)
    cmd = [paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH, "-goal", binpath,
           "-nodes", str(nodes), "-num_gpus_per_node", str(gpus_per_node),
           "-topo", so_topo, "-intranode_topo", su_topo,
           "-intranode_linkspeed", str(intranode_linkspeed),
           "-end", str(end), "-sender_cc_only",
           "-intranode_cc", "none",
           "-intranode_queue_type", "lossless_input",
           "-lossless_high_pfc", str(_hi),
           "-lossless_low_pfc", str(_lo)]
    if mtu:
        cmd += ["-mtu", str(mtu)]
    if groups:
        cmd += ["-groups", groups]
    if reduce_compute:
        cmd += ["-reduce_compute_latency", str(reduce_compute)]
    if mcast_pin >= 0:
        cmd += ["-mcast_pin", str(mcast_pin)]
    # Escape hatch for datapath-policy flags under study (e.g. -rs_local_fold) so a
    # sensitivity run needs no harness edit. Space-separated, appended verbatim.
    if os.environ.get("SIM_EXTRA_FLAGS"):
        cmd += os.environ["SIM_EXTRA_FLAGS"].split()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, -1, "timeout", " ".join(cmd)
    combined = p.stdout + "\n" + p.stderr
    drops = len(DROP.findall(combined))
    status = "ok" if p.returncode == 0 else f"rc={p.returncode}"
    return parse_makespan(p.stdout), drops, status, " ".join(cmd)


def _read_link_crosses(path):
    """Read the one-row footprint CSV emitted by -link_crosses_csv
    ('total_link_crosses,total_link_bytes' header + one int,int row).
    Returns (link_crosses, link_bytes) or (None, None)."""
    try:
        with open(path) as f:
            rows = [ln.strip() for ln in f if ln.strip()]
        cols = rows[1].split(",")
        return int(cols[0]), int(cols[1])
    except (OSError, IndexError, ValueError):
        return None, None


def run_sim_footprint(binpath, so_topo, su_topo, nodes, gpus_per_node, groups=None,
                      reduce_compute=0, timeout=600,
                      intranode_linkspeed=INTRANODE_LINKSPEED_DEFAULT, end=100000000,
                      mtu=MTU_DEFAULT):
    """One footprint run: like run_sim() but adds -link_crosses_csv and reads the
    per-link-cross totals back. Returns
    (makespan_ns|None, link_crosses|None, link_bytes|None, drop_count, status, command).

    NOTE gpus_per_node is the SCALE-UP TOPO WIDTH (pinned by the caller), decoupled
    from the group size N (which lives in the trace); idle hosts register zero
    crosses, so one fixed-width topo serves the whole N sweep."""
    import tempfile
    fd, lc_path = tempfile.mkstemp(prefix="lc_", suffix=".csv")
    os.close(fd)
    _hi, _lo = pfc_thresholds(su_topo)
    cmd = [paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH, "-goal", binpath,
           "-nodes", str(nodes), "-num_gpus_per_node", str(gpus_per_node),
           "-topo", so_topo, "-intranode_topo", su_topo,
           "-intranode_linkspeed", str(intranode_linkspeed),
           "-end", str(end), "-sender_cc_only",
           "-intranode_cc", "none",
           "-intranode_queue_type", "lossless_input",
           "-lossless_high_pfc", str(_hi),
           "-lossless_low_pfc", str(_lo),
           "-link_crosses_csv", lc_path]
    if mtu:
        cmd += ["-mtu", str(mtu)]
    if groups:
        cmd += ["-groups", groups]
    if reduce_compute:
        cmd += ["-reduce_compute_latency", str(reduce_compute)]
    if os.environ.get("SIM_EXTRA_FLAGS"):
        cmd += os.environ["SIM_EXTRA_FLAGS"].split()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.remove(lc_path)
        except OSError:
            pass
        return None, None, None, -1, "timeout", " ".join(cmd)
    combined = p.stdout + "\n" + p.stderr
    drops = len(DROP.findall(combined))
    status = "ok" if p.returncode == 0 else f"rc={p.returncode}"
    crosses, lbytes = _read_link_crosses(lc_path)
    try:
        os.remove(lc_path)
    except OSError:
        pass
    return parse_makespan(p.stdout), crosses, lbytes, drops, status, " ".join(cmd)


def require_simulator():
    """Loud preflight: the pcm binary must be built and executable."""
    if not (os.path.isfile(paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH)
            and os.access(paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH, os.X_OK)):
        sys.exit(f"simulator not executable: {paths.PCM_APP_HTSIM_ATLAHS_EXEC_PATH}\n"
                 "(build with `build` in the container, or set "
                 "PCM_APP_HTSIM_ATLAHS_EXEC_PATH locally)")
