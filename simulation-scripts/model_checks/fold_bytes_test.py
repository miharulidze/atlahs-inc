#!/usr/bin/env python3
"""Does -rs_local_fold actually remove the bytes it claims -- for Reduce as well as
for Reduce-Scatter?

Note (2026-07-26): the fold is now the DEFAULT datapath, so the arms flipped --
`fold=False` passes -no_rs_local_fold and `fold=True` passes nothing.

Run inside the atlahs-sim container:
  docker run --rm -v <atlahs>:/workspace --entrypoint python3 atlahs-sim \\
      /workspace/simulation-scripts/model_checks/fold_bytes_test.py

WHY THIS TEST EXISTS. The fold's effect on Reduce-Scatter is visible in the completion
time, so timing validates it. Reduce is different: the fold silences the ROOT, whose
egress is off the critical path (the fan-in and the apex-to-root descent bind instead),
so a correct Reduce fold changes the completion time by nothing at all. That is also
exactly what a dead code path looks like. Only the byte counters can tell them apart,
which is what this checks, via the driver's -link_crosses_csv totals.

Expected byte withdrawal, with b = S/N and f_w(x) = x + 64*ceil(x/4096):

  reduce_scatter  N * f_w(b) * (hops each member's own block would have climbed)
                  -- every member skips exactly its own block
  reduce          f_w(S) * (hops the root's contribution would have climbed)
                  -- only the root falls silent, and it skips its whole vector
  allreduce       0 -- no single owner (reduce_root < 0), must be untouched
  bcast           0 -- not on the reduce path at all

A member's block climbs 1 hop when its leaf holds other members (the leaf still has to
forward the folded slice upward), and further only when the silence propagates, i.e.
when it is the sole member of its leaf, and so on up.
"""
import math, os, sys

sys.path.insert(0, os.environ.get("SIMSCRIPTS", "/workspace/simulation-scripts"))
from common import goal, sim  # noqa: E402

TMP = "/tmp/foldbytes"
TOPO = "/workspace/simulation-scripts/topo_files"
SO = f"{TOPO}/tree16_bw200Gbps.topo"
SU = f"{TOPO}/scaleup_3tier_256_4000Gbps.topo"
TAIL = 100
H, MSS = 64, 4096

# (label, members, S) -- 65536 keeps every run well under a second
CASES = [
    ("contiguous |G|=8, 2 leaves", list(range(8)), 65536),
    ("cross-pod  |G|=2 (0 and 16)", [0, 16], 8192),
]
COLLS = ["reduce_scatter", "reduce", "allreduce", "bcast"]


def fw(x):
    return x + H * math.ceil(x / MSS)


def run(binpath, groups, fold):
    os.environ["SIM_EXTRA_FLAGS"] = "" if fold else "-no_rs_local_fold"
    fin, crosses, lbytes, drops, st, _ = sim.run_sim_footprint(
        binpath, SO, SU, 256, 256, groups=groups, timeout=900)
    return fin, crosses, lbytes, st


if __name__ == "__main__":
    os.makedirs(TMP, exist_ok=True)
    print(f"{'case':>28} {'collective':>15} {'bytes off':>11} {'bytes on':>11} "
          f"{'withdrawn':>10} {'/f_w(b)':>8} {'time off':>9} {'time on':>9}")
    print("-" * 110)
    for label, members, S in CASES:
        N = len(members)
        for coll in COLLS:
            tag = f"{coll}_{N}_{S}"
            g = os.path.join(TMP, tag)
            rooted = coll in ("reduce", "bcast")
            if rooted:
                goal.gen_inc_goal(g + ".goal", g + ".groups", 256, S, coll, TAIL,
                                  root=members[0])
                # gen_inc_goal builds a contiguous group 0..n-1; only use it for the
                # contiguous case, else fall back to the multigroup writer below.
                if members != list(range(N)):
                    goal.gen_multigroup_inc_goal(g + ".goal", g + ".groups", 256,
                                                 [members], S, "reduce_scatter", TAIL)
                    print(f"{label:>28} {coll:>15}  (skipped: rooted kinds need a "
                          f"contiguous group in this harness)")
                    continue
            else:
                goal.gen_multigroup_inc_goal(g + ".goal", g + ".groups", 256,
                                             [members], S, coll, TAIL)
            goal.compile_goal(g + ".goal", g + ".bin")
            r = {}
            for fold in (False, True):
                r[fold] = run(g + ".bin", g + ".groups", fold)
            (t0, _, b0, s0), (t1, _, b1, s1) = r[False], r[True]
            if s0 != "ok" or s1 != "ok" or b0 is None or b1 is None:
                print(f"{label:>28} {coll:>15}  !! {s0}/{s1}")
                continue
            d = b0 - b1
            unit = fw(S // N)
            print(f"{label:>28} {coll:>15} {b0:>11,} {b1:>11,} {d:>10,} "
                  f"{d/unit:>8.2f} {t0:>9,} {t1:>9,}")
    print("\nreduce_scatter should withdraw N*f_w(b) per hop climbed; reduce should")
    print("withdraw the ROOT's whole vector; allreduce and bcast must withdraw ZERO.")
