#!/usr/bin/env python3
"""Realism ladder R2/R3 on the G72 suite (2026-07-15).

Extends run_g72_suite.py's pipeline along the staged realism plan:
  R0 = G4  (toy literals, placebo compute, 100 Gbps scale-out)   [g72 suite]
  R1 = H4  (toy literals, H100-roofline compute, 100 Gbps)       [g72 suite]
  R2 = G4 shape with REALISTIC literals + H100 compute, 100 Gbps [here]
  R3 = R2's exact bins on a 400 Gbps scale-out tier              [here]
Each stage isolates one variable; all stages share tp/dp/pp = 72/2/2
(288 ranks, 4 domains — the C3/G4 analog) and the 72-divisible model
geometry (hidden 4608 / intermediate 16128 / 72 heads / kv 72).

Realistic literals (R2): num_layers and seq_len raised from the toy excerpt
(2 layers / seq 144) toward llama3-8B-class proportions, seq % 72 == 0
preserved. R2A is a half-scale smoke config used to calibrate simulation
wall-time before committing R2's literals; keep both in results for the
scaling record. R3 re-runs R2's bins with topologies/tree288_400Gbps.topo
and -linkspeed 400000 (COPY_ENG default would cap a 400 Gbps fabric; at
400 Gbps the Mbps arithmetic equals the pipes' 20 ps/B exactly, no residual).

Phases:
  python3 run_realism_suite.py --phase gen    --configs R2A     # mutates submodule
  python3 run_realism_suite.py --phase run    --configs R2A
  python3 run_realism_suite.py --phase floors --configs R2A     # 2 perturbed schedules/arm
  python3 run_realism_suite.py --phase metrics                  # realism_results.csv

COORDINATION: the gen phase patches generator literals and restores them via
git checkout (same discipline as run_g72_suite.py) — never run it while
another suite's gen phase is active.
"""
import argparse
import concurrent.futures as cf
import csv
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_g72_suite as g72

TOPO_DIR = ("/Users/wstaempfli/CLionProjects/atlahs/sim/htsim-backend/sim/"
            "datacenter/topologies")
SO_100G = os.path.join(TOPO_DIR, "tree288_100Gbps.topo")
SO_400G = os.path.join(TOPO_DIR, "tree288_400Gbps.topo")
PERTURB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "perturb_g72.py")

# label -> dict; R3 is a run-variant of R2 (same traces, different network).
RCONFIGS = {
    "R2A": dict(tp=72, dp=2, pp=2, layers=4, seq=1152, cmodel="h100",
                so_topo=SO_100G, so_linkspeed=None, traces_from=None),
    "R2":  dict(tp=72, dp=2, pp=2, layers=8, seq=2304, cmodel="h100",
                so_topo=SO_100G, so_linkspeed=None, traces_from=None),
    "R3":  dict(tp=72, dp=2, pp=2, layers=8, seq=2304, cmodel="h100",
                so_topo=SO_400G, so_linkspeed=400000, traces_from="R2"),
    # floor references on the existing g72 stages (no gen; run-variants of
    # their own traces with their own network config):
    "R0F": dict(tp=72, dp=2, pp=2, layers=2, seq=144, cmodel=None,
                so_topo=SO_100G, so_linkspeed=None, traces_from="G4"),
    "R1F": dict(tp=72, dp=2, pp=2, layers=2, seq=144, cmodel="h100",
                so_topo=SO_100G, so_linkspeed=None, traces_from="H4"),
}

CALC_LINE = re.compile(r"calc (\d+)")


def sub_patch_realism(cfg):
    """g72's model/parallelism patch + the realism literals."""
    path = os.path.join(g72.SUB, "simple_sim/llama3_training.py")
    src = open(path).read()
    patches = g72.MODEL_PATCH + [
        (r"^    num_layers: int = \d+",
         f"    num_layers: int = {cfg['layers']}"),
    ]
    for pat, repl in patches:
        src, n = re.subn(pat, repl, src, flags=re.M)
        assert n == 1, f"model patch {pat!r}: {n} matches"
    # seq: MODEL_PATCH already sets seq 144; override to the realism value
    src, n = re.subn(r"^    seq_len: int = \d+.*$",
                     f"    seq_len: int = {cfg['seq']}  # realism ladder; % 72 == 0",
                     src, flags=re.M)
    assert n == 1
    for name, val in (("tp_size", cfg["tp"]), ("dp_size", cfg["dp"]),
                      ("pp_size", cfg["pp"])):
        src, n = re.subn(rf"^    {name} = \d+$", f"    {name} = {val}",
                         src, flags=re.M)
        assert n == 1, f"{name}: {n} matches"
    ranks = cfg["tp"] * cfg["dp"] * cfg["pp"]
    src, n = re.subn(r"for device_id in range\(\d+\):",
                     f"for device_id in range({ranks}):", src, flags=re.M)
    assert n == 1
    src, n = re.subn(r"\{device_id:02d\}\.pkl", "{device_id:04d}.pkl", src)
    assert n == 1
    open(path, "w").write(src)


def gen_one(label):
    cfg = RCONFIGS[label]
    assert cfg["traces_from"] is None, f"{label} is a run-variant, no gen"
    dest = os.path.join(g72.TRACES, label)
    os.makedirs(dest, exist_ok=True)
    gdir = os.path.join(g72.SUB, "llama3_graphs")
    if os.path.isdir(gdir):
        shutil.rmtree(gdir)
    sub_patch_realism(cfg)
    try:
        r = subprocess.run([sys.executable, "-m", "simple_sim.llama3_training"],
                           cwd=g72.SUB, capture_output=True, text=True,
                           timeout=14400)
        if r.returncode != 0:
            sys.exit(f"graph build failed ({label}): {r.stderr[-800:]}")
    finally:
        g72.sub_restore()
    ranks = cfg["tp"] * cfg["dp"] * cfg["pp"]
    n_pkl = len([p for p in os.listdir(gdir) if p.endswith(".pkl")])
    assert n_pkl == ranks, f"{label}: {n_pkl} pkl != {ranks}"

    for junk in os.listdir(g72.SUB):
        if junk.endswith(".groups") or junk in ("llama3.goal", "llama3_inc.goal"):
            os.remove(os.path.join(g72.SUB, junk))
    cm = {"COMPUTE_MODEL": cfg["cmodel"]} if cfg["cmodel"] else {}
    g72.render(label, {**cm, "EMIT_INC": ""}, "llama3.goal")
    shutil.move(os.path.join(g72.SUB, "llama3.goal"),
                os.path.join(dest, "llama3.goal"))
    g72.render(label, {**cm, "EMIT_INC": "1", "INC_CONTEXTS": "tp"},
               "llama3_inc.goal")
    shutil.move(os.path.join(g72.SUB, "llama3_inc.goal"),
                os.path.join(dest, "llama3_inc.goal"))
    for g in os.listdir(g72.SUB):
        if g.endswith(".groups"):
            shutil.move(os.path.join(g72.SUB, g),
                        os.path.join(dest, "llama3_inc.groups"))
    print(f"[gen] {label} done", flush=True)


def prep_bins(label):
    cfg = RCONFIGS[label]
    src_label = cfg["traces_from"] or label
    d = os.path.join(g72.TRACES, src_label)
    if not os.path.exists(os.path.join(d, "llama3_inc_local.groups")):
        n = g72.convert_groups(os.path.join(d, "llama3_inc.groups"),
                               os.path.join(d, "llama3_inc_local.groups"))
        print(f"[prep] {src_label}: {n} groups", flush=True)
    for goal, binf in (("llama3.goal", "llama3.bin"),
                       ("llama3_inc.goal", "llama3_inc.bin")):
        gp, bp = os.path.join(d, goal), os.path.join(d, binf)
        if not os.path.exists(bp):
            r = subprocess.run([g72.TXT2BIN, "-i", gp, "-o", bp],
                               capture_output=True, text=True)
            assert r.returncode == 0, f"txt2bin {gp}: {r.stderr[-300:]}"
    return d


def sim_cmd(label, binp, groups, cfg):
    ranks = cfg["tp"] * cfg["dp"] * cfg["pp"]
    cmd = [g72.SIM, "-goal", binp, "-nodes", str(ranks),
           "-num_gpus_per_node", str(g72.GPN),
           "-topo", cfg["so_topo"], "-intranode_topo", g72.SU_TOPO,
           "-intranode_linkspeed", "4000000"]
    if cfg["so_linkspeed"]:
        cmd += ["-linkspeed", str(cfg["so_linkspeed"])]
    cmd += ["-end", "100000000", "-sender_cc_only",
            "-intranode_queue_type", "lossless_input"]
    if groups:
        cmd += ["-groups", groups, "-reduce_compute_latency", "100"]
    return cmd


def run_one(label, arm, timeout, bin_override=None, out_tag=None):
    cfg = RCONFIGS[label]
    d = prep_bins(label)
    dest = os.path.join(g72.TRACES, label)
    os.makedirs(dest, exist_ok=True)
    binp = bin_override or os.path.join(
        d, "llama3.bin" if arm == "base" else "llama3_inc.bin")
    groups = (os.path.join(d, "llama3_inc_local.groups")
              if arm == "inc" else None)
    cmd = sim_cmd(label, binp, groups, cfg)
    pre = os.path.join(dest, (out_tag or ("baseline" if arm == "base" else "inc")))
    with open(pre + ".cmd", "w") as f:
        f.write(" ".join(cmd) + "\n")
    try:
        with open(pre + ".out", "w") as fo, open(pre + ".err", "w") as fe:
            p = subprocess.run(cmd, stdout=fo, stderr=fe, timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return label, arm, out_tag, None, None, -1, "timeout"
    out = open(pre + ".out").read() + "\n" + open(pre + ".err").read()
    fin = g72.MAXFIN.search(out)
    return (label, arm, out_tag, int(fin.group(1)) if fin else None,
            len(g72.COLL_COMPLETE.findall(out)), len(g72.DROP.findall(out)),
            "ok" if rc == 0 else f"rc={rc}")


def floors(label, timeout, jobs):
    """Two neutral perturbed schedules per arm (units 200/400 ns)."""
    cfg = RCONFIGS[label]
    src = os.path.join(g72.TRACES, cfg["traces_from"] or label)
    dest = os.path.join(g72.TRACES, label)
    os.makedirs(dest, exist_ok=True)
    tasks = []
    for arm, goal, mode in (("base", "llama3.goal", "recvk"),
                            ("inc", "llama3_inc.goal", "coll")):
        for unit in (200, 400):
            tag = f"{'baseline' if arm == 'base' else 'inc'}_p{unit}"
            pg = os.path.join(dest, tag + ".goal")
            pb = os.path.join(dest, tag + ".bin")
            if not os.path.exists(pb):
                r = subprocess.run([sys.executable, PERTURB,
                                    os.path.join(src, goal), pg, mode,
                                    str(unit), str(g72.GPN)],
                                   capture_output=True, text=True)
                assert r.returncode == 0, f"perturb: {r.stderr[-300:]}"
                r = subprocess.run([g72.TXT2BIN, "-i", pg, "-o", pb],
                                   capture_output=True, text=True)
                assert r.returncode == 0, f"txt2bin: {r.stderr[-300:]}"
                os.remove(pg)  # goal text is large; the bin suffices
            tasks.append((arm, pb, tag))
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(run_one, label, a, timeout, b, t)
                for a, b, t in tasks]
        for fu in cf.as_completed(futs):
            l, arm, tag, fin, nc, dr, st = fu.result()
            print(f"[floor] {l}/{tag}: makespan={fin} colls={nc} "
                  f"drops={dr} [{st}]", flush=True)


def calc_total_ns(goal_path):
    tot = 0
    with open(goal_path) as f:
        for line in f:
            m = CALC_LINE.search(line)
            if m:
                tot += int(m.group(1))
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["gen", "run", "floors", "metrics"])
    ap.add_argument("--configs", default="")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=28800)
    args = ap.parse_args()
    labels = args.configs.split(",") if args.configs else list(RCONFIGS)

    if args.phase == "gen":
        for l in labels:
            gen_one(l)
        return

    if args.phase == "run":
        for l in labels:      # serial: bins/groups before the parallel pool
            prep_bins(l)
        jobs = [(l, a) for l in labels for a in ("base", "inc")]
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = [ex.submit(run_one, l, a, args.timeout) for l, a in jobs]
            for fu in cf.as_completed(futs):
                l, arm, _, fin, nc, dr, st = fu.result()
                print(f"[run] {l}/{arm}: makespan={fin} colls={nc} "
                      f"drops={dr} [{st}]", flush=True)
        return

    if args.phase == "floors":
        for l in labels:
            floors(l, args.timeout, args.jobs)
        return

    # metrics: A/B + goal-derived shares + floor spreads per stage
    rows = []
    for l in labels:
        cfg = RCONFIGS[l]
        src = os.path.join(g72.TRACES, cfg["traces_from"] or l)
        dest = os.path.join(g72.TRACES, l)
        cb, sb, ncoll, nops, kinds = g72.goal_metrics(
            os.path.join(src, "llama3_inc.goal"))
        ranks = cfg["tp"] * cfg["dp"] * cfg["pp"]
        calc_ns = calc_total_ns(os.path.join(src, "llama3_inc.goal"))
        res = {}
        for arm, pre in (("base", "baseline"), ("inc", "inc")):
            p = os.path.join(dest, pre + ".out")
            if not os.path.exists(p):     # run-variant reusing source runs
                p = os.path.join(src, pre + ".out")
            out = open(p).read()
            m = g72.MAXFIN.search(out)
            res[arm] = int(m.group(1)) if m else None
        floors_ns = {}
        for pre in ("baseline", "inc"):
            vals = []
            for unit in (200, 400):
                p = os.path.join(dest, f"{pre}_p{unit}.out")
                if os.path.exists(p):
                    m = g72.MAXFIN.search(open(p).read())
                    if m:
                        vals.append(int(m.group(1)))
            floors_ns[pre] = vals
        bfin, ifin = res["base"], res["inc"]
        gain = (1 - ifin / bfin) * 100 if (bfin and ifin) else None
        calc_per_rank = calc_ns / ranks / 2      # per iteration (2 iters)
        base_samples = ([bfin] if bfin else []) + floors_ns["baseline"]
        inc_samples = ([ifin] if ifin else []) + floors_ns["inc"]
        spread = lambda v: (max(v) - min(v)) if len(v) > 1 else None
        rows.append(dict(
            stage=l, layers=cfg["layers"], seq=cfg["seq"],
            compute_model=cfg["cmodel"] or "placebo",
            so_topo=os.path.basename(cfg["so_topo"]),
            so_linkspeed_mbps=cfg["so_linkspeed"] or "engine-default",
            ranks=ranks, coll_bytes=cb, inc_send_bytes=sb,
            tp_comm_share=round(cb / (cb + sb), 6) if (cb + sb) else None,
            calc_ns_per_rank_iter=int(calc_per_rank),
            baseline_ns=bfin, inc_ns=ifin,
            gain_pct=round(gain, 4) if gain is not None else None,
            compute_share_base=round(2 * calc_per_rank / bfin, 4) if bfin else None,
            base_floor_spread_ns=spread(base_samples),
            inc_floor_spread_ns=spread(inc_samples),
            base_samples="/".join(map(str, base_samples)),
            inc_samples="/".join(map(str, inc_samples)),
            intranode_linkspeed_mbps=4000000,
        ))
        print(rows[-1], flush=True)
    out = os.path.join(g72.SUITE, "realism_results.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("wrote", out)


if __name__ == "__main__":
    main()
