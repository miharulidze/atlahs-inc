#!/usr/bin/env python3
"""Causal control for -rs_local_fold: is the penalty caused by lack of slack on the
shared uplink?

Run inside the atlahs-sim container:
  docker run --rm -v <atlahs>:/workspace --entrypoint python3 atlahs-sim \\
      /workspace/simulation-scripts/model_checks/fold_slack_test.py

The fold unloads a member's host-to-leaf link only. The claim is that this buys nothing
once a SHARED uplink binds instead, and that the residual penalty is the fold's reshaping
of arrivals hitting a pipeline with no idle time to absorb it. If that is the cause, then
giving the shared link slack -- and changing nothing else -- must flip the sign.

Two fabrics differing ONLY in the shared leaf<->top link rate, same group, same message,
same depth, same leaf occupancy:

  foldctl_2tier_64_h4_matched.topo   shared link 4000 Gbps (= host rate)
  foldctl_2tier_64_h4_up8000.topo    shared link 8000 Gbps (2x host rate)

Measured 2026-07-26, |G|=16, S=16 MiB (blocktime = f_w(S/N)/B = 2130.05 ns):

  matched   fold off 35,205   fold on 36,265   delta +1060 ns = +0.4977 bt   (a COST)
  up8000    fold off 35,197   fold on 33,071   delta -2126 ns = -0.9981 bt   (full SAVING)

The unfolded arm barely moves (8 ns), so the faster shared link is not what speeds
anything up -- the off arm was host-bound and stays host-bound. Only the folded arm
changes, and it changes sign. That is the control: slack on the shared link decides
whether the fold's saving can be cashed.
"""
import math, os, sys

sys.path.insert(0, os.environ.get("SIMSCRIPTS", "/workspace/simulation-scripts"))
from common import goal, sim  # noqa: E402

TMP = "/tmp/foldslack"
TOPO = "/workspace/simulation-scripts/topo_files"
SO = f"{TOPO}/tree16_bw200Gbps.topo"
N, S, TAIL = 16, 16777216, 100
H, MSS, B = 64, 4086, 500.0

FABRICS = [("shared link 4000 Gbps (rate matched)", "foldctl_2tier_64_h4_matched.topo"),
           ("shared link 8000 Gbps (2x host rate)", "foldctl_2tier_64_h4_up8000.topo")]


def fw(x):
    return x + H * math.ceil(x / MSS)


if __name__ == "__main__":
    os.makedirs(TMP, exist_ok=True)
    g = os.path.join(TMP, f"rs{N}")
    goal.gen_multigroup_inc_goal(g + ".goal", g + ".groups", 64, [list(range(N))],
                                 S, "reduce_scatter", TAIL)
    goal.compile_goal(g + ".goal", g + ".bin")
    bt = fw(S // N) / B
    print(f"|G|={N}, S={S} B, blocktime={bt:.2f} ns\n")
    print(f"{'fabric':>38} {'fold off':>9} {'fold on':>9} {'delta':>9} {'in bt':>8}")
    print("-" * 78)
    for label, topo in FABRICS:
        res = {}
        for mode in ("off", "on"):
            os.environ["SIM_EXTRA_FLAGS"] = "-rs_local_fold" if mode == "on" else ""
            fin, _, st, _ = sim.run_sim(g + ".bin", SO, f"{TOPO}/{topo}", 64, 64,
                                        groups=g + ".groups", timeout=900)
            if st != "ok" or fin is None:
                print(f"   !! {label} {mode}: {st}")
            res[mode] = fin - TAIL if fin else float("nan")
        d = res["on"] - res["off"]
        print(f"{label:>38} {res['off']:>9,.0f} {res['on']:>9,.0f} {d:>+9,.0f} {d/bt:>+8.4f}")
    print("\nExpected: a COST of about +0.50 bt when the shared link is rate matched, and")
    print("the FULL -1.00 bt saving when it is not, with the fold-off arm unchanged.")
