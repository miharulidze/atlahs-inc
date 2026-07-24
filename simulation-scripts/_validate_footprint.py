#!/usr/bin/env python3
"""Validate the ported PT6 link-cross counter against the analytic oracle, and
determine which single-fixed-width-topo mode reproduces the exact-width numbers.

RUN IN THE CONTAINER (the pcm binary is a Linux build):
  docker run --rm -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
      /workspace/simulation-scripts/_validate_footprint.py

Part 1 (counter correctness, VALIDATED recipe nodes==gpus==P, topo width==P):
  single-switch AllGather: byte-ratio ring/INC should track 2-2/P; Ring must ==(RD).
Part 2 (single-topo / partial-population): P=8 on the 64-host topo in two candidate
  modes vs the exact-width P=8 result; whichever matches proves the experiment mode.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import goal, sim, paths  # noqa: E402

TMP = "/tmp/vfp"
os.makedirs(TMP, exist_ok=True)
TAIL, MTU, PAYLOAD = 100, 4160, 4096

SW = {2: "scaleup_single_switch_2_12800Gbps.topo",
      4: "scaleup_single_switch_4_12800Gbps.topo",
      8: "scaleup_single_switch_8_12800Gbps.topo",
      64: "scaleup_single_switch_64_4000Gbps.topo"}
SO_SMALL = "tree16_bw200Gbps.topo"           # >=16 hosts
SO_BIG = "tree128_nonblocking_400Gbps.topo"  # >=128 hosts


def run_inc(kind, n, su, so, nodes, gpus, tag):
    size = n * PAYLOAD
    g = f"{TMP}/inc_{kind}_{tag}.goal"; grp = g[:-5] + ".groups"
    goal.gen_inc_goal(g, grp, n, size, kind, TAIL)
    goal.compile_goal(g, g[:-5] + ".bin")
    return sim.run_sim_footprint(g[:-5] + ".bin", paths.topo(so), paths.topo(su),
                                 nodes=nodes, gpus_per_node=gpus, groups=grp,
                                 mtu=MTU, timeout=300)


def run_base(coll, algo, n, su, so, nodes, gpus, tag):
    size = n * PAYLOAD
    g = f"{TMP}/base_{coll}_{algo}_{tag}.goal"
    goal.gen_baseline_goal(g, n, size, coll, algo, TAIL)
    goal.compile_goal(g, g[:-5] + ".bin")
    return sim.run_sim_footprint(g[:-5] + ".bin", paths.topo(so), paths.topo(su),
                                 nodes=nodes, gpus_per_node=gpus, mtu=MTU, timeout=300)


def fmt(r):  # (makespan, crosses, bytes, drops, status, cmd)
    return f"crosses={r[1]} bytes={r[2]} status={r[4]} drops={r[3]}"


print("=== Part 1: counter correctness — single-switch AllGather, exact-width recipe ===")
print(f"{'P':>3} {'INC_cross':>10} {'RING_cross':>11} {'byte_ratio':>11} {'pkt_ratio':>10} {'2-2/P':>7} {'RD_byte':>9} {'RingRD_eq':>10}")
p8_exact = None
for n in (2, 4, 8):
    inc = run_inc("allgather", n, SW[n], SO_SMALL, n, n, f"p1_{n}")
    ring = run_base("allgather", "ring", n, SW[n], SO_SMALL, n, n, f"p1_{n}")
    rd = run_base("allgather", "rdouble", n, SW[n], SO_SMALL, n, n, f"p1_{n}") if (n & (n - 1)) == 0 and n > 2 else None
    if not (inc[2] and ring[2]):
        print(f"{n:>3}  FAIL  inc[{fmt(inc)}] ring[{fmt(ring)}]")
        continue
    br = ring[2] / inc[2]
    pr = ring[1] / inc[1]
    rd_br = (rd[2] / inc[2]) if rd and rd[2] else float('nan')
    ring_rd_eq = "n/a" if not rd else ("YES" if rd[1] == ring[1] and rd[2] == ring[2] else f"NO({ring[1]}v{rd[1]})")
    print(f"{n:>3} {inc[1]:>10} {ring[1]:>11} {br:>11.4f} {pr:>10.4f} {2 - 2/n:>7.4f} {rd_br:>9.4f} {ring_rd_eq:>10}")
    if n == 8:
        p8_exact = (inc[1], inc[2], ring[1], ring[2])

print()
print("=== Part 2: single fixed-width topo (partial population), P=8 on 64-host switch ===")
if p8_exact:
    print(f"  exact-width P=8 reference: INC(cross={p8_exact[0]},bytes={p8_exact[1]}) RING(cross={p8_exact[2]},bytes={p8_exact[3]})")
    incY = run_inc("allgather", 8, SW[64], SO_BIG, 64, 64, "p2Y")
    ringY = run_base("allgather", "ring", 8, SW[64], SO_BIG, 64, 64, "p2Y")
    incZ = run_inc("allgather", 8, SW[64], SO_SMALL, 2, 64, "p2Z")
    ringZ = run_base("allgather", "ring", 8, SW[64], SO_SMALL, 2, 64, "p2Z")
    for name, inc, ring in (("Y nodes=gpus=64", incY, ringY), ("Z nodes=2,gpus=64", incZ, ringZ)):
        if inc[2] and ring[2]:
            match = (inc[1] == p8_exact[0] and inc[2] == p8_exact[1]
                     and ring[1] == p8_exact[2] and ring[2] == p8_exact[3])
            print(f"  mode {name}: INC({fmt(inc)}) RING({fmt(ring)})  -> {'MATCH exact-width' if match else 'DIFFERS'}")
        else:
            print(f"  mode {name}: FAIL inc[{fmt(inc)}] ring[{fmt(ring)}]")
else:
    print("  (skipped — Part 1 P=8 did not produce a reference)")

print("\nDONE")
