#!/usr/bin/env python3
"""Skeleton decomposition at the case-study headline cells (approved 2026-07-29).

For each scale, generate the SKELETON trace (TP collectives replaced by
dependency-preserving zero-cost calc ops via SKELETON_CONTEXTS=tp, everything
else identical) from BOTH generator paths (decomposed baseline and EMIT_INC),
assert the two are byte-identical (proof the arms differ only in TP ops), run
the skeleton at the H100-class operating point, and attribute:

    TP-attributable exposure (arm) = makespan_full(arm) - makespan_skeleton

Splitting the baseline's TP-attributable exposure into per-op data movement
(the ring serial estimate) and lost overlap converts the ">2x exposed-comm
compression" reviewer question into a measured decomposition.

Run inside the atlahs-sim container from /workspace/simulation-scripts.
"""
import csv
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from experiments.intranode_linkspeed_sweep import run as R  # noqa: E402
from common import goal, paths  # noqa: E402

BATCH = 32
LAYERS = 2
ITERS = 2
SO_GBPS = 400          # H100-class operating point
SU_GBPS = 4000
TMP = "/tmp/skel_decomp"

SCALES = [  # (total_gpus, tp, full-results csv, config tag)
    (16, 4, "sweep_internode_su4000", "pp1_tp4_dp4_pp1"),
    (32, 8, "sweep_internode_su4000_g32", "pp1_tp8_dp4_pp1"),
    (64, 16, "sweep_internode_su4000_g64", "pp1_tp16_dp4_pp1"),
]


def gen_goal(tp, dp, graphs, out_goal, emit_inc, skeleton):
    env = dict(os.environ, COMPUTE_MODEL="h100", INC_CONTEXTS="tp",
               EMIT_INC="1" if emit_inc else "0",
               SKELETON_CONTEXTS="tp" if skeleton else "")
    cmd = [sys.executable, "simple_sim2goal.py", "--graphs-dir", graphs,
           "--out-goal", out_goal]
    if emit_inc:
        cmd += ["--groups", out_goal + ".groups"]
    r = subprocess.run(cmd, env=env, cwd=paths.GENERATOR_DIR,
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"goal gen failed for {out_goal}:\n{r.stderr[-800:]}")


def full_times(csv_name, cfg):
    rows = [r for r in csv.DictReader(
        open(os.path.join(paths.results_dir("intranode_linkspeed_sweep"),
                          csv_name + ".csv")))
            if r["config"] == cfg and int(r["so_gbps"]) == SO_GBPS]
    by = {r["arm"]: r for r in rows}
    return (float(by["baseline"]["time_per_iter_s"]),
            float(by["inc"]["time_per_iter_s"]),
            int(by["baseline"]["compute_ns_per_iter"]) / 1e9)


def main():
    goal.require_txt2bin()
    goal.require_generator()
    results = []
    for total, tp, csv_name, cfg in SCALES:
        dp = total // tp
        d = os.path.join(TMP, f"tp{tp}")
        graphs = os.path.join(d, "graphs")
        os.makedirs(graphs, exist_ok=True)
        env = dict(os.environ, COMPUTE_MODEL="h100")
        r = subprocess.run(
            [sys.executable, "-m", "simple_sim.llama3_training",
             "--tp", str(tp), "--dp", str(dp), "--pp", "1",
             "--num-layers", str(LAYERS), "--seq-len", "4096",
             "--ffn", "11008", "--hidden", "4096", "--heads", "32",
             "--kv-heads", "32", "--batch", str(BATCH), "--iters", str(ITERS),
             "--graphs-dir", graphs],
            env=env, cwd=paths.GENERATOR_DIR, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"graph gen failed tp{tp}:\n{r.stderr[-800:]}")

        skel_base = os.path.join(d, "skel_base.goal")
        skel_inc = os.path.join(d, "skel_inc.goal")
        gen_goal(tp, dp, graphs, skel_base, emit_inc=False, skeleton=True)
        gen_goal(tp, dp, graphs, skel_inc, emit_inc=True, skeleton=True)
        # Consistency check: with every TP op skeletonized, the two generator
        # paths must produce THE SAME trace -- the arms differ only in TP ops.
        a, b = open(skel_base).read(), open(skel_inc).read()
        assert a == b, f"tp{tp}: skeleton traces differ between arms!"
        print(f"tp{tp}: skeleton traces identical across arms "
              f"({len(a.splitlines())} lines) -- arms differ ONLY in TP ops")

        skel_bin = skel_base[:-5] + ".bin"
        goal.compile_goal(skel_base, skel_bin)

        R.configure_scale(total, [tp], (1,))
        R.MODEL["batch"] = BATCH
        su_topo = R.su_topo_for(tp, SU_GBPS, TMP)
        so_topo = R.so_topo_for(SO_GBPS, TMP)
        from common import sim
        pfc = sim.pfc_config(su_topo)
        fin, drops, status, cmd = R.run_one_sim(
            skel_bin, su_topo, so_topo, tp, None, SU_GBPS * 1000, pfc,
            timeout=7200, so_mbps=SO_GBPS * 1000)
        assert status == "ok" and not drops, f"tp{tp} skeleton: {status} drops={drops}"
        skel_s = fin / ITERS / 1e9

        base_s, inc_s, comp_s = full_times(csv_name, cfg)
        S = BATCH * 4096 * 4096 * 2                      # bytes per TP allreduce
        n_ar = 4 * LAYERS                                 # ARs per iteration
        ring_serial = n_ar * 2 * (tp - 1) / tp * S / 500 / 1e9   # s @500 B/ns
        inc_serial = n_ar * S / 500 / 1e9
        row = dict(tp=tp, skeleton_ms=skel_s * 1e3,
                   base_full_ms=base_s * 1e3, inc_full_ms=inc_s * 1e3,
                   compute_ms=comp_s * 1e3,
                   tp_attr_base_ms=(base_s - skel_s) * 1e3,
                   tp_attr_inc_ms=(inc_s - skel_s) * 1e3,
                   ring_serial_ms=ring_serial * 1e3,
                   inc_serial_ms=inc_serial * 1e3)
        row["overlap_loss_base_ms"] = row["tp_attr_base_ms"] - row["ring_serial_ms"]
        results.append(row)
        print(f"tp{tp}: skel={skel_s*1e3:.1f}ms  "
              f"TP-attr base={row['tp_attr_base_ms']:.1f}ms "
              f"(ring serial {row['ring_serial_ms']:.1f} + overlap loss "
              f"{row['overlap_loss_base_ms']:.1f})  "
              f"TP-attr inc={row['tp_attr_inc_ms']:.1f}ms "
              f"(serial {row['inc_serial_ms']:.1f})")

    out = os.path.join(paths.results_dir("intranode_linkspeed_sweep"),
                       "skeleton_decomposition.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print("wrote", out)


if __name__ == "__main__":
    main()
