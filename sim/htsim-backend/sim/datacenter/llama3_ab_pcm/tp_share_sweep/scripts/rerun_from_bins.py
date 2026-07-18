#!/usr/bin/env python3
"""Re-run the D2 TP-share A/B from the committed per-config .bin files.

Purpose: re-measure C1-C5 after the -intranode_linkspeed fix (see run_sweep.py's
GOTCHA: the engine's per-GPU scale-up NIC injection rate defaults to 200 Gbps
COPY_ENG, capping the baseline arm's TP ring sends at ~5% of the 3,600 Gbps
fabric; the original results.csv was measured under that default). The trace
files themselves are unchanged, so the committed C*/llama3{,_inc}.bin and
groups sidecars are reused as-is and no generator re-run is needed. Trace-derived
columns (byte counts, tp_comm_share, expected collective counts) are carried
over from the existing results.csv; only the measured columns are replaced.

Usage:
  python3 scripts/rerun_from_bins.py [--configs C1,C2,C3,C4,C5] \
      [--out results.csv] [--scratch <dir for .out/.err>]
"""
import argparse
import concurrent.futures
import csv
import os
import re
import subprocess

SIM = ("/Users/wstaempfli/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/"
       "pcm/build/bin/htsim_flow_app_atlahs")
SO_TOPO = ("/Users/wstaempfli/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/"
           "pcm/apps/htsim_atlahs/example_incast/tree16.topo")
AB_DIR = ("/Users/wstaempfli/CLionProjects/atlahs/sim/htsim-backend/sim/"
          "datacenter/llama3_ab_pcm")
TOPO_DIR = ("/Users/wstaempfli/CLionProjects/atlahs/sim/htsim-backend/sim/"
            "datacenter/topologies")
SWEEP_DIR = os.path.join(AB_DIR, "tp_share_sweep")

# gpn=4 keeps the anchor's own topo copy so C3 stays the EXACT anchor invocation.
SU_TOPO = {
    4: f"{AB_DIR}/scaleup_single_switch_4_3600Gbps.topo",
    8: f"{TOPO_DIR}/scaleup_single_switch_8_3600Gbps.topo",
    16: f"{TOPO_DIR}/scaleup_single_switch_16_3600Gbps.topo",
}
GPN = {"C1": 4, "C2": 4, "C3": 4, "C4": 8, "C5": 16}

MAXFIN = re.compile(r"Maximum finishing time at host \d+:\s*(\d+)")
COLL_COMPLETE = re.compile(r"(ALLREDUCE|ALLGATHER|REDUCE_SCATTER|REDUCE|BCAST)_COMPLETE")
HEADROOM = re.compile(r"LOSSLESS not working", re.IGNORECASE)
DROP = re.compile(r"drop arriving|drop last from queue|dropped packet|"
                  r"Random Drop|Buffer Drop|Dropping packet", re.IGNORECASE)


def run_sim(binpath, gpn, out_prefix, groups=None, reduce_compute=0,
            timeout=1800, intranode_linkspeed_mbps=4000000):
    # 4000000 Mbps pins the NIC frame time (8.30 ns) to the pipes' 2 ps/B
    # quantisation of 3,600 Gbps — one realised wire rate (492.3 B/ns) everywhere.
    # -end is in MICROSECONDS; -intranode_linkspeed in Mbps.
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
        return dict(fin=None, colls=None, drops=-1, warns=-1, status="timeout",
                    cmd=" ".join(cmd))
    combined = open(outf).read() + "\n" + open(errf).read()
    fin = MAXFIN.search(combined)
    return dict(fin=int(fin.group(1)) if fin else None,
                colls=len(COLL_COMPLETE.findall(combined)),
                drops=len(DROP.findall(combined)),
                warns=len(HEADROOM.findall(combined)),
                status="ok" if p.returncode == 0 else f"rc={p.returncode}",
                cmd=" ".join(cmd))


def rerun_config(label, scratch):
    d = os.path.join(SWEEP_DIR, label)
    os.makedirs(os.path.join(scratch, label), exist_ok=True)
    pre = os.path.join(scratch, label, "")
    base = run_sim(os.path.join(d, "llama3.bin"), GPN[label], pre + "baseline")
    inc = run_sim(os.path.join(d, "llama3_inc.bin"), GPN[label], pre + "inc",
                  groups=os.path.join(d, "llama3_inc_local.groups"),
                  reduce_compute=100)
    return label, base, inc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="C1,C2,C3,C4,C5")
    ap.add_argument("--out", default=os.path.join(SWEEP_DIR, "results.csv"))
    ap.add_argument("--scratch", default="/tmp/tp_sweep_nicfix")
    ap.add_argument("--jobs", type=int, default=5)
    args = ap.parse_args()
    labels = args.configs.split(",")
    os.makedirs(args.scratch, exist_ok=True)

    with open(os.path.join(SWEEP_DIR, "results.csv")) as f:
        old = {r["config"]: r for r in csv.DictReader(f)}

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(rerun_config, lb, args.scratch) for lb in labels]
        for fut in concurrent.futures.as_completed(futs):
            label, base, inc = fut.result()
            results[label] = (base, inc)
            g = ((1 - inc["fin"] / base["fin"]) * 100
                 if base["fin"] and inc["fin"] else None)
            print(f"{label}: base={base['fin']} inc={inc['fin']} "
                  f"gain={g if g is None else f'{g:.4f}%'} "
                  f"colls={inc['colls']} drops={base['drops'] + inc['drops']} "
                  f"warns(inc)={inc['warns']} [{base['status']}/{inc['status']}]",
                  flush=True)

    rows = []
    for label in old:  # preserve original row order
        r = dict(old[label])
        if label in results:
            base, inc = results[label]
            bfin, ifin = base["fin"], inc["fin"]
            r["baseline_ns"] = bfin
            r["inc_ns"] = ifin
            r["delta_ns"] = (bfin - ifin) if (bfin and ifin) else ""
            r["gain_pct"] = (round((1 - ifin / bfin) * 100, 4)
                             if (bfin and ifin) else "")
            r["allreduce_complete"] = inc["colls"]
            r["dropped_packets"] = base["drops"] + inc["drops"]
            r["lossless_headroom_warnings_inc_arm"] = inc["warns"]
            r["status"] = f"base={base['status']},inc={inc['status']}"
            r["intranode_linkspeed_mbps"] = 4000000
        rows.append(r)
    fields = list(rows[0].keys())
    if "intranode_linkspeed_mbps" not in fields:
        fields.append("intranode_linkspeed_mbps")
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
