#!/usr/bin/env python3
"""Does -rs_local_fold's payoff track TREE DEPTH, or which link binds?

Run inside the atlahs-sim container:
  docker run --rm -v <atlahs>:/workspace --entrypoint python3 atlahs-sim \\
      /workspace/simulation-scripts/model_checks/fold_placement_test.py

Measured 2026-07-26 (S = 16 MiB, three-tier 256-host fabric, in block times):
  contiguous 1 leaf    d=1, 0 shared uplinks above the collision tier -> -1.0000
  contiguous 2 leaves  d=2, 1                                        -> +0.5002
  spread 1/leaf 1 pod  d=2, 0                                        -> -1.0000
  spread 1/leaf 2 pods d=3, 1                                        -> +0.4988
The two spread rows are the discriminator: depth does NOT predict the sign. What
does is whether an exactly rate-matched SHARED uplink sits above the lowest tier
holding two or more members. Contiguous placement pins 4 members per leaf and so
ties leaf occupancy to depth, which is why the effect first looked depth-only.


Contiguous placement pins 4 members per leaf, which couples "leaf occupancy" to
"tree depth" and makes the effect LOOK depth-only. Break the coupling: put ONE
member on each of 4 leaves inside one pod. That is a d=2 tree (rooted at the agg)
but every leaf's sole member owns a slice, so each leaf genuinely falls silent and
the fold removes bytes from the binding link after all.

  depth-only law  predicts  ~ +0.5  (a cost, as contiguous d=2 shows)
  binding-link    predicts  ~ -1.0  (a saving)
"""
import os, subprocess, sys, math
sys.path.insert(0, os.environ.get("SIMSCRIPTS", "/workspace/simulation-scripts"))
from common import goal, sim, paths

TMP = "/tmp/spread"; os.makedirs(TMP, exist_ok=True)
SU = "/workspace/simulation-scripts/topo_files/scaleup_3tier_256_4000Gbps.topo"
SO = "/workspace/simulation-scripts/topo_files/tree16_bw200Gbps.topo"
S, TAIL = 16777216, 100
H, MSS, B = 64, 4086, 500.0
fw = lambda x: x + H*math.ceil(x/MSS)

CASES = {
    "contiguous 1 leaf   (d=1)": [0, 1, 2, 3],
    "contiguous 2 leaves (d=2)": [0, 1, 2, 3, 4, 5, 6, 7],
    "spread 1/leaf 1 pod (d=2)": [0, 4, 8, 12],
    "spread 1/leaf 2 pods(d=3)": [0, 4, 8, 12, 16, 20, 24, 28],
}
print(f"{'placement':>26} {'N':>3} {'blocktime':>10} {'off':>9} {'on':>9} {'delta':>9} {'in bt':>8}")
print("-"*82)
for name, members in CASES.items():
    N = len(members)
    g = os.path.join(TMP, f"g{N}_{name.split()[0]}")
    goal.gen_multigroup_inc_goal(g+".goal", g+".groups", 256, [members], S, "reduce_scatter", TAIL)
    goal.compile_goal(g+".goal", g+".bin")
    res = {}
    for mode in ("off", "on"):
        os.environ["SIM_EXTRA_FLAGS"] = "-rs_local_fold" if mode == "on" else ""
        fin, drops, st, _ = sim.run_sim(g+".bin", SO, SU, 256, 256, groups=g+".groups", timeout=900)
        res[mode] = (fin - TAIL) if fin else None
        if st != "ok": print(f"   !! {name} {mode}: {st}")
    bt = fw(S//N)/B
    d = res["on"] - res["off"]
    print(f"{name:>26} {N:>3} {bt:>10.1f} {res['off']:>9,.0f} {res['on']:>9,.0f} {d:>+9,.0f} {d/bt:>+8.4f}")
