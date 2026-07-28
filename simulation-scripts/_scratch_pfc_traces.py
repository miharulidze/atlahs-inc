"""One-off -pfc_trace captures for the backpressure figures (AA-plan-PFC-Validation).
Reruns two in-situ census engagement cells with the event trace on; no CSV rows.
  docker run --rm --entrypoint python3 -v $PWD:/workspace atlahs-sim \
      /workspace/simulation-scripts/_scratch_pfc_traces.py
"""
import os
import sys

sys.path.insert(0, "/workspace/simulation-scripts")
from common import goal, paths, sim  # noqa: E402

tmp = "/tmp/pfc_traces"
os.makedirs(tmp, exist_ok=True)
out = "/workspace/simulation-scripts/results/scaleup_pfc_concurrent/traces"
os.makedirs(out, exist_ok=True)
SU = paths.topo("scaleup_3tier_256_4000Gbps.topo")
SO = paths.topo("tree16_bw200Gbps.topo")
N, SIZE = 64, 67108864

# (i) 3-tier AllGather, in-network arm: the strongest in-situ engagement
# (240 pauses at the presented config).
g = os.path.join(tmp, "ag.goal")
grp = g[:-5] + ".groups"
goal.gen_inc_goal(g, grp, N, SIZE, "allgather", 100, root=-1)
goal.compile_goal(g, g[:-5] + ".bin")
fin, drops, st, _, ex = sim.run_sim(
    g[:-5] + ".bin", SO, SU, nodes=N, gpus_per_node=N, groups=grp,
    pfc_trace=os.path.join(out, "census_3tier_allgather_inc_67108864.csv"))
print("AG inc 3tier:", fin, drops, st, ex["pauses_sent"], ex["peak_ingress_frac"])

# (ii) 3-tier recursive-doubling AllReduce, endpoint arm: the lawful cross-pod
# fan-in that the radix x BDP egress cap provisions for (23 pauses in situ).
b = os.path.join(tmp, "rd.goal")
goal.gen_baseline_goal(b, N, SIZE, "allreduce", "rdouble", 100)
goal.compile_goal(b, b[:-5] + ".bin")
fin, drops, st, _, ex = sim.run_sim(
    b[:-5] + ".bin", SO, SU, nodes=N, gpus_per_node=N,
    pfc_trace=os.path.join(out, "census_3tier_rdouble_base_67108864.csv"))
print("RD base 3tier:", fin, drops, st, ex["pauses_sent"], ex["peak_ingress_frac"])
