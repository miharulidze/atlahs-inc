#!/usr/bin/env python3
"""G72 application-level suite: the llama3 A/B redone at group size 72 on the
multi-domain simulator with the 72-GPU scale-up domain.

Successor of ../llama3_ab_pcm/tp_share_sweep (16 ranks, |G| = tp <= 16): the
TP group is pinned at 72 (= the collective-level headline group size) and the
dp/pp structure sweeps the TP share, one 72-GPU scale-up domain per node.

MODEL (72-consistent llama3-class scaling; every sharded dim must divide 72):
  no published llama3 divides 72 (8B: 32 heads/4096; 70B: 64/8192), so the
  8B-scale shape is re-factored to 72 heads x head_dim 64 -> hidden 4608
  (vs 4096), intermediate 3.5x = 16128, kv_heads = heads = 72 (the generator's
  existing MHA convention), seq_len 144 (SP asserts batch_seq % tp == 0;
  128 % 72 != 0). Parameter count stays ~8B-layer scale (~0.6B for the
  2-layer excerpt), so simulated byte volumes stay tractable.

CONFIGS (label, tp, dp, pp) -- gpn = 72 everywhere, nodes = dp*pp:
  G1  72/1/1   72 ranks, 1 domain   (pure TP; the C5 analog; dp=1 guard)
  G2  72/2/1  144 ranks, 2 domains  (DP)
  G3  72/4/1  288 ranks, 4 domains  (DP-heavy; the C2 analog)
  G4  72/2/2  288 ranks, 4 domains  (PP-amplified; the C3 analog)
  SPG1/SPG4   sequence-parallel renderings of G1/G4 (own-workload A/Bs)
  H4          G4's graphs re-rendered under COMPUTE_MODEL=h100 (both arms)

PIPELINE per config: patch generator literals (restored via git checkout) ->
build per-rank pkl graphs (04d zero-padding; the stock 02d scrambles lexical
rank order at >= 100 ranks) -> render both arms with the size-1-communicator
guard (render_c5 precedent) -> txt2bin -> pcm two-tier run
(-intranode_linkspeed 4000000 pin, lossless_input, tree16 scale-out).

Usage:
  python3 run_g72_suite.py --phase gen          # traces (serial, mutates submodule)
  python3 run_g72_suite.py --phase run          # sims (parallel pool)
  python3 run_g72_suite.py --phase metrics      # results.csv
  --configs G1,SPG1 to filter; --jobs N (default 4).
"""
import argparse
import concurrent.futures as cf
import csv
import os
import re
import shutil
import subprocess
import sys

SUB = "/Users/wstaempfli/CLionProjects/atlahs/goal_gen/ai/nccl_generator_v2"
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACES = os.path.join(SUITE, "traces")
SIM = ("/Users/wstaempfli/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi/"
       "pcm/build/bin/htsim_flow_app_atlahs")
TXT2BIN = os.path.expanduser("~/CLionProjects/LogGOPSim-1.1-coll/txt2bin")
# rank = scale-out endpoint in the pcm federation (each GPU has its own
# scale-out NIC), so the scale-out topo must hold >= total ranks: tree16
# capped every earlier suite at 16 ranks; G72 needs up to 288.
SO_TOPO = ("/Users/wstaempfli/CLionProjects/atlahs/sim/htsim-backend/sim/"
           "datacenter/topologies/tree288_100Gbps.topo")
SU_TOPO = ("/Users/wstaempfli/CLionProjects/atlahs/sim/htsim-backend/sim/"
           "datacenter/topologies/scaleup_single_switch_72_3600Gbps.topo")
GPN = 72

# (label, driver, tp, dp, pp, compute_model, graph_source)
CONFIGS = {
    "G1":   ("plain", 72, 1, 1, None, None),
    "G2":   ("plain", 72, 2, 1, None, None),
    "G3":   ("plain", 72, 4, 1, None, None),
    "G4":   ("plain", 72, 2, 2, None, None),
    "SPG1": ("sp",    72, 1, 1, None, None),
    "SPG4": ("sp",    72, 2, 2, None, None),
    "H4":   ("plain", 72, 2, 2, "h100", "G4"),   # re-render of G4's graphs
}

MODEL_PATCH = [  # (regex on llama3_training.py, replacement)
    (r"^    hidden_size: int = \d+", "    hidden_size: int = 4608"),
    (r"^    intermediate_size: int = \d+(  #.*)?$",
     "    intermediate_size: int = 16128  # 3.5x hidden (SwiGLU), 72-divisible"),
    (r"^    num_attention_heads: int = \d+", "    num_attention_heads: int = 72"),
    (r"^    num_kv_heads: int = \d+(\s*#.*)?$",
     "    num_kv_heads: int = 72           # MHA (generator convention)"),
    (r"^    seq_len: int = \d+(\s*#.*)?$",
     "    seq_len: int = 144             # SP needs batch_seq % 72 == 0"),
]

MAXFIN = re.compile(r"Maximum finishing time at host \d+:\s*(\d+)")
COLL_COMPLETE = re.compile(
    r"(ALLREDUCE|ALLGATHER|REDUCE_SCATTER|REDUCE|BCAST)_COMPLETE")
DROP = re.compile(r"drop arriving|drop last from queue|dropped packet|"
                  r"Random Drop|Buffer Drop|Dropping packet|LOSSLESS not working",
                  re.IGNORECASE)
COLL_LINE = re.compile(r"coll (\w+) (\d+)b (\d+) (\d+) (-?\d+)")
SEND_LINE = re.compile(r"send (\d+)b to \d+")

RENDER_GUARDED = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "render_guarded.py")


def sub_patch(cfg_label):
    """Patch generator literals for one config. ALWAYS pair with sub_restore."""
    drv, tp, dp, pp, _, _ = CONFIGS[cfg_label]
    path = os.path.join(SUB, "simple_sim/llama3_training.py")
    src = open(path).read()
    for pat, repl in MODEL_PATCH:
        src, n = re.subn(pat, repl, src, flags=re.M)
        assert n == 1, f"model patch {pat!r}: {n} matches"
    if drv == "plain":
        for name, val in (("tp_size", tp), ("dp_size", dp), ("pp_size", pp)):
            src, n = re.subn(rf"^    {name} = \d+$", f"    {name} = {val}",
                             src, flags=re.M)
            assert n == 1, f"{name}: {n} matches"
    # 04d zero-padding + total-devices loop (stock: range(16), 02d -- lexical
    # rank scrambling at >= 100 ranks)
    src, n = re.subn(r"for device_id in range\(\d+\):",
                     f"for device_id in range({tp * dp * pp}):", src, flags=re.M)
    assert n == 1
    src, n = re.subn(r'\{device_id:02d\}\.pkl', "{device_id:04d}.pkl", src)
    assert n == 1
    open(path, "w").write(src)
    if drv == "sp":
        p2 = os.path.join(SUB, "simple_sim/llama3_training_sp.py")
        s2 = open(p2).read()
        s2, n = re.subn(r'\{device_id:02d\}\.pkl', "{device_id:04d}.pkl", s2)
        assert n == 1
        open(p2, "w").write(s2)


def sub_restore():
    subprocess.run(["git", "-C", SUB, "checkout", "--",
                    "simple_sim/llama3_training.py",
                    "simple_sim/llama3_training_sp.py"], check=True)


def render(cfg_label, arm_env, out_goal_name):
    """Run the guarded renderer in the submodule cwd; returns produced paths."""
    env = dict(os.environ, **arm_env)
    r = subprocess.run([sys.executable, RENDER_GUARDED], cwd=SUB, env=env,
                       capture_output=True, text=True, timeout=14400)
    if r.returncode != 0:
        sys.exit(f"render failed ({cfg_label}, {arm_env}): {r.stderr[-800:]}")
    return r.stdout


def gen_one(label):
    drv, tp, dp, pp, cmodel, graph_src = CONFIGS[label]
    dest = os.path.join(TRACES, label)
    os.makedirs(dest, exist_ok=True)
    gdir = os.path.join(SUB, "llama3_graphs")

    if graph_src is None:
        # build graphs
        if os.path.isdir(gdir):
            shutil.rmtree(gdir)
        sub_patch(label)
        try:
            env = dict(os.environ)
            mod = "simple_sim.llama3_training"
            if drv == "sp":
                mod = "simple_sim.llama3_training_sp"
                env.update(SP_TP=str(tp), SP_DP=str(dp), SP_PP=str(pp))
            r = subprocess.run([sys.executable, "-m", mod], cwd=SUB, env=env,
                               capture_output=True, text=True, timeout=14400)
            if r.returncode != 0:
                sys.exit(f"graph build failed ({label}): {r.stderr[-800:]}")
        finally:
            sub_restore()
        n_pkl = len([p for p in os.listdir(gdir) if p.endswith(".pkl")])
        assert n_pkl == tp * dp * pp, f"{label}: {n_pkl} pkl != {tp*dp*pp}"
    else:
        # reuse another config's graphs (H4 <- G4): regenerate if absent
        if not os.path.isdir(gdir):
            sys.exit(f"{label}: needs {graph_src}'s graphs in the submodule; "
                     f"run gen for {graph_src} immediately before {label}")

    # stale-output guard: old sessions leave llama3*.goal/.groups in the
    # submodule root; a stale .groups would be moved as if freshly rendered.
    for junk in os.listdir(SUB):
        if junk.endswith(".groups") or junk in ("llama3.goal", "llama3_inc.goal"):
            os.remove(os.path.join(SUB, junk))
    cm = {"COMPUTE_MODEL": cmodel} if cmodel else {}
    # baseline arm
    render(label, {**cm, "EMIT_INC": ""}, "llama3.goal")
    shutil.move(os.path.join(SUB, "llama3.goal"),
                os.path.join(dest, "llama3.goal"))
    # INC arm (+ .groups sidecar). SP renderings tag their tensor-parallel
    # comm context="tp_sp" (sp_ab precedent: INC_CONTEXTS=tp,tp_sp).
    contexts = "tp,tp_sp" if drv == "sp" else "tp"
    render(label, {**cm, "EMIT_INC": "1", "INC_CONTEXTS": contexts},
           "llama3_inc.goal")
    shutil.move(os.path.join(SUB, "llama3_inc.goal"),
                os.path.join(dest, "llama3_inc.goal"))
    for g in os.listdir(SUB):
        if g.endswith(".groups"):
            shutil.move(os.path.join(SUB, g),
                        os.path.join(dest, "llama3_inc.groups"))
    print(f"[gen] {label} done", flush=True)


def convert_groups(global_path, local_path):
    lines_out = []
    with open(global_path) as f:
        for gi, line in enumerate(f):
            ranks = [int(x) for x in line.split()]
            if not ranks:
                continue
            nodes = {r // GPN for r in ranks}
            assert len(nodes) == 1, \
                f"group {gi} not node-contained under gpn={GPN}: {ranks[:8]}..."
            lines_out.append(" ".join(str(r % GPN) for r in ranks))
    with open(local_path, "w") as f:
        f.write("\n".join(lines_out) + "\n")
    return len(lines_out)


def run_sim(label, arm, timeout):
    drv, tp, dp, pp, _, _ = CONFIGS[label]
    d = os.path.join(TRACES, label)
    nodes = tp * dp * pp
    binp = os.path.join(d, "llama3.bin" if arm == "base" else "llama3_inc.bin")
    cmd = [SIM, "-goal", binp, "-nodes", str(nodes),
           "-num_gpus_per_node", str(GPN),
           "-topo", SO_TOPO, "-intranode_topo", SU_TOPO,
           "-intranode_linkspeed", "4000000",
           "-end", "100000000", "-sender_cc_only",
           "-intranode_queue_type", "lossless_input"]
    if arm == "inc":
        cmd += ["-groups", os.path.join(d, "llama3_inc_local.groups"),
                "-reduce_compute_latency", "100"]
    pre = os.path.join(d, "baseline" if arm == "base" else "inc")
    with open(pre + ".cmd", "w") as f:
        f.write(" ".join(cmd) + "\n")
    try:
        with open(pre + ".out", "w") as fo, open(pre + ".err", "w") as fe:
            p = subprocess.run(cmd, stdout=fo, stderr=fe, timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return label, arm, None, None, -1, "timeout"
    out = open(pre + ".out").read() + "\n" + open(pre + ".err").read()
    fin = MAXFIN.search(out)
    return (label, arm, int(fin.group(1)) if fin else None,
            len(COLL_COMPLETE.findall(out)), len(DROP.findall(out)),
            "ok" if rc == 0 else f"rc={rc}")


def goal_metrics(path):
    cb = sb = ncoll = 0
    inst, kinds = set(), set()
    with open(path) as f:
        for line in f:
            m = COLL_LINE.search(line)
            if m:
                kinds.add(m.group(1)); cb += int(m.group(2))
                inst.add(int(m.group(4))); ncoll += 1
                continue
            m = SEND_LINE.search(line)
            if m:
                sb += int(m.group(1))
    return cb, sb, ncoll, len(inst), kinds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["gen", "run", "metrics"])
    ap.add_argument("--configs", default="")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=14400)
    args = ap.parse_args()
    labels = (args.configs.split(",") if args.configs
              else list(CONFIGS))
    os.makedirs(TRACES, exist_ok=True)

    if args.phase == "gen":
        # keep H4 right after G4 so it can reuse the graphs still on disk
        order = [l for l in ["G1", "SPG1", "G2", "G3", "G4", "H4", "SPG4"]
                 if l in labels]
        for l in order:
            gen_one(l)
        return

    if args.phase == "run":
        for l in labels:  # prep: groups + bins (cheap, serial)
            d = os.path.join(TRACES, l)
            if not os.path.exists(os.path.join(d, "llama3_inc_local.groups")):
                n = convert_groups(os.path.join(d, "llama3_inc.groups"),
                                   os.path.join(d, "llama3_inc_local.groups"))
                print(f"[prep] {l}: {n} groups (node-contained)", flush=True)
            for g, b in (("llama3.goal", "llama3.bin"),
                         ("llama3_inc.goal", "llama3_inc.bin")):
                gp, bp = os.path.join(d, g), os.path.join(d, b)
                if not os.path.exists(bp):
                    r = subprocess.run([TXT2BIN, "-i", gp, "-o", bp],
                                       capture_output=True, text=True)
                    assert r.returncode == 0, f"txt2bin {gp}: {r.stderr[-300:]}"
        jobs = [(l, a) for l in labels for a in ("base", "inc")]
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = [ex.submit(run_sim, l, a, args.timeout) for l, a in jobs]
            for fu in cf.as_completed(futs):
                label, arm, fin, ncomp, drops, st = fu.result()
                print(f"[run] {label}/{arm}: makespan={fin} colls={ncomp} "
                      f"drops={drops} [{st}]", flush=True)
        return

    # metrics
    rows = []
    for l in labels:
        drv, tp, dp, pp, cmodel, _ = CONFIGS[l]
        d = os.path.join(TRACES, l)
        cb, sb, ncoll, nops, kinds = goal_metrics(os.path.join(d, "llama3_inc.goal"))
        base_sb = goal_metrics(os.path.join(d, "llama3.goal"))[1]
        res = {}
        for arm, pre in (("base", "baseline"), ("inc", "inc")):
            out = open(os.path.join(d, pre + ".out")).read() \
                + "\n" + open(os.path.join(d, pre + ".err")).read()
            m = MAXFIN.search(out)
            res[arm] = (int(m.group(1)) if m else None,
                        len(COLL_COMPLETE.findall(out)),
                        len(DROP.findall(out)))
        bfin, ifin = res["base"][0], res["inc"][0]
        gain = (1 - ifin / bfin) * 100 if (bfin and ifin) else None
        rows.append(dict(
            config=l, driver=drv, tp=tp, dp=dp, pp=pp, gpn=GPN,
            ranks=tp * dp * pp, compute_model=cmodel or "placebo",
            coll_kinds="+".join(sorted(kinds)),
            n_coll_lines=ncoll, n_coll_ops=nops,
            coll_bytes=cb, inc_send_bytes=sb, baseline_send_bytes=base_sb,
            tp_comm_share=round(cb / (cb + sb), 6) if (cb + sb) else None,
            baseline_ns=bfin, inc_ns=ifin,
            gain_pct=round(gain, 4) if gain is not None else None,
            coll_complete=res["inc"][1], coll_expected=nops,
            drops=res["base"][2] + res["inc"][2],
            intranode_linkspeed_mbps=4000000,
            model="h4608/heads72/kv72/seq144/L2"))
        print(rows[-1], flush=True)
    out = os.path.join(SUITE, "results.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("wrote", out)


if __name__ == "__main__":
    main()
