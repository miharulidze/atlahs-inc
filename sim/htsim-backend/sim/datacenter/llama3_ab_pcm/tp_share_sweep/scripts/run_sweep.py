#!/usr/bin/env python3
"""D2 TP-share sweep: per-config groups conversion, txt2bin, two-tier A/B runs, metrics.

Per config (16 ranks = tp*dp*pp):
  1. convert generator .groups (GLOBAL ranks) -> node-local host ids (r % gpn),
     asserting node containment (r // gpn constant per group, node-major layout);
  2. txt2bin both arms;
  3. run the two-tier engine (anchor invocation, adapted gpn + intranode topo +
     groups) on both arms;
  4. parse makespan / ALLREDUCE_COMPLETE count / drops;
  5. tp_comm_share from the INC .goal: coll bytes / (coll + send bytes).
"""
import csv
import os
import re
import subprocess
import sys

SCRATCH = ("/private/tmp/claude-501/-Users-wstaempfli-CLionProjects-atlahs/"
           "8d83afe2-32f9-46e5-99c5-e0fd019ccaf3/scratchpad/tp_sweep")
SIM = ("/Users/wstaempfli/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/"
       "pcm/build/bin/htsim_flow_app_atlahs")
TXT2BIN = os.path.expanduser("~/CLionProjects/LogGOPSim-1.1-coll/txt2bin")
SO_TOPO = ("/Users/wstaempfli/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/"
           "pcm/apps/htsim_atlahs/example_incast/tree16.topo")
AB_DIR = ("/Users/wstaempfli/CLionProjects/atlahs/sim/htsim-backend/sim/"
          "datacenter/llama3_ab_pcm")
TOPO_DIR = ("/Users/wstaempfli/CLionProjects/atlahs/sim/htsim-backend/sim/"
            "datacenter/topologies")

# gpn=4 uses the anchor's own topo copy (byte-comment-differs only from
# topologies/, functionally identical) so C3 is the EXACT anchor invocation.
SU_TOPO = {
    4: f"{AB_DIR}/scaleup_single_switch_4_3600Gbps.topo",
    8: f"{TOPO_DIR}/scaleup_single_switch_8_3600Gbps.topo",
    16: f"{TOPO_DIR}/scaleup_single_switch_16_3600Gbps.topo",
}

CONFIGS = [  # (label, tp, dp, pp, gpn)
    ("C1", 2, 8, 1, 4),
    ("C2", 4, 4, 1, 4),
    ("C3", 4, 2, 2, 4),
    ("C4", 8, 2, 1, 8),
    ("C5", 16, 1, 1, 16),
]

MAXFIN = re.compile(r"Maximum finishing time at host \d+:\s*(\d+)")
COLL_COMPLETE = re.compile(r"(ALLREDUCE|ALLGATHER|REDUCE_SCATTER|REDUCE|BCAST)_COMPLETE")
DROP = re.compile(r"drop arriving|drop last from queue|dropped packet|"
                  r"Random Drop|Buffer Drop|Dropping packet|LOSSLESS not working",
                  re.IGNORECASE)
COLL_LINE = re.compile(r"coll (\w+) (\d+)b (\d+) (\d+) (-?\d+)")
SEND_LINE = re.compile(r"send (\d+)b to \d+")


def convert_groups(global_path, local_path, gpn):
    """Generator .groups (global ranks) -> node-local ids; assert containment."""
    lines_out = []
    with open(global_path) as f:
        for gi, line in enumerate(f):
            ranks = [int(x) for x in line.split()]
            if not ranks:
                continue
            nodes = {r // gpn for r in ranks}
            assert len(nodes) == 1, (
                f"group {gi} not node-contained under gpn={gpn}: {ranks}")
            lines_out.append(" ".join(str(r % gpn) for r in ranks))
    with open(local_path, "w") as f:
        f.write("\n".join(lines_out) + "\n")
    return len(lines_out)


def goal_metrics(inc_goal_path):
    """(coll_bytes, send_bytes, n_coll_lines, n_distinct_coll_ops, coll_kinds)."""
    coll_bytes = send_bytes = n_coll = 0
    instances = set()
    kinds = set()
    with open(inc_goal_path) as f:
        for line in f:
            m = COLL_LINE.search(line)
            if m:
                kinds.add(m.group(1))
                coll_bytes += int(m.group(2))
                instances.add(int(m.group(4)))  # pre-packed (group<<16)|k
                n_coll += 1
                continue
            m = SEND_LINE.search(line)
            if m:
                send_bytes += int(m.group(1))
    return coll_bytes, send_bytes, n_coll, len(instances), kinds


def baseline_send_bytes(goal_path):
    total = 0
    with open(goal_path) as f:
        for line in f:
            m = SEND_LINE.search(line)
            if m:
                total += int(m.group(1))
    return total


def txt2bin(goal, binout):
    r = subprocess.run([TXT2BIN, "-i", goal, "-o", binout],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"txt2bin failed on {goal}: {r.stderr[-400:]}")


def run_sim(binpath, gpn, groups=None, reduce_compute=0, out_prefix="run",
            timeout=600, intranode_linkspeed_mbps=4000000):
    # GOTCHA: the .topo speed sets only the fabric pipes; the per-GPU NIC injection
    # rate is -intranode_linkspeed (Mbps) and DEFAULTS to 200 Gbps (COPY_ENG), which
    # silently caps any p2p arm whose per-send block exceeds one 4,150 B frame at
    # ~24.6 payload-B/ns = 5% of the 3,600 Gbps fabric. Always pass it explicitly.
    # 4000000 pins the NIC frame time (4,150x8/4e12 = 8.30 ns) to the pipes' 2 ps/B
    # quantisation of the .topo's 3,600 Gbps: one realised wire rate (492.3 B/ns)
    # everywhere. (The literal 3600000 would pace the NIC ~10% under the pipes.)
    cmd = [SIM, "-goal", binpath, "-nodes", "16",
           "-num_gpus_per_node", str(gpn),
           "-topo", SO_TOPO, "-intranode_topo", SU_TOPO[gpn],
           "-intranode_linkspeed", str(intranode_linkspeed_mbps),
           "-end", "100000000", "-sender_cc_only",
           "-intranode_queue_type", "lossless_input"]
    if groups:
        cmd += ["-groups", groups]
    if reduce_compute:
        cmd += ["-reduce_compute_latency", str(reduce_compute)]
    outf, errf = out_prefix + ".out", out_prefix + ".err"
    try:
        with open(outf, "w") as fo, open(errf, "w") as fe:
            p = subprocess.run(cmd, stdout=fo, stderr=fe, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, None, -1, "timeout"
    stdout = open(outf).read()
    stderr = open(errf).read()
    drops = len(DROP.findall(stdout + "\n" + stderr))
    fin = MAXFIN.search(stdout)
    n_complete = len(COLL_COMPLETE.findall(stdout))
    status = "ok" if p.returncode == 0 else f"rc={p.returncode}"
    return (int(fin.group(1)) if fin else None), n_complete, drops, status


def main():
    rows = []
    for label, tp, dp, pp, gpn in CONFIGS:
        d = os.path.join(SCRATCH, label)
        os.chdir(d)
        inc_goal, base_goal = "llama3_inc.goal", "llama3.goal"
        # 1. groups conversion (+ containment assert)
        n_groups = convert_groups("llama3_inc.groups", "llama3_inc_local.groups", gpn)
        # 2. metrics from the goal text
        cb, sb, n_coll, n_ops, kinds = goal_metrics(inc_goal)
        base_sb = baseline_send_bytes(base_goal)
        share = cb / (cb + sb)
        exp_members = n_coll // n_ops
        # 3. compile
        txt2bin(base_goal, "llama3.bin")
        txt2bin(inc_goal, "llama3_inc.bin")
        # 4. run both arms
        bfin, bcomp, bdrop, bst = run_sim("llama3.bin", gpn, out_prefix="baseline")
        ifin, icomp, idrop, ist = run_sim("llama3_inc.bin", gpn,
                                          groups="llama3_inc_local.groups",
                                          reduce_compute=100, out_prefix="inc")
        gain = (1 - ifin / bfin) * 100 if (bfin and ifin) else None
        row = dict(config=label, tp=tp, dp=dp, pp=pp, gpn=gpn,
                   n_groups=n_groups, group_size=exp_members,
                   coll_kinds="+".join(sorted(kinds)),
                   n_coll_ops_expected=n_ops,
                   coll_bytes=cb, inc_send_bytes=sb,
                   baseline_send_bytes=base_sb,
                   tp_comm_share=round(share, 6),
                   baseline_ns=bfin, inc_ns=ifin,
                   gain_pct=(round(gain, 4) if gain is not None else None),
                   allreduce_complete=icomp, allreduce_expected=n_ops,
                   baseline_complete=bcomp,
                   drops=bdrop + idrop,
                   status=(f"base={bst},inc={ist}"))
        rows.append(row)
        print(f"{label}: TP{tp}/DP{dp}/PP{pp} gpn={gpn} share={share:.4f} "
              f"base={bfin} inc={ifin} gain={gain if gain is None else f'{gain:.3f}%'} "
              f"colls={icomp}/{n_ops} drops={bdrop + idrop} [{bst}/{ist}]")

    out = os.path.join(SCRATCH, "sweep_results.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("wrote", out)


if __name__ == "__main__":
    main()
