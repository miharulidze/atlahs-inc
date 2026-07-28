"""Smoke test for the PFC instrumentation plumbing (AA-plan-PFC-Validation):
one INC AllGather cell on the crossbar with -pfc_trace + save_stdout, printing
the parsed extras dict and the head of the trace CSV. Run inside the container:
  docker run --rm --entrypoint python3 -v $PWD:/workspace atlahs-sim \
      /workspace/simulation-scripts/_scratch_pfc_smoke.py
"""
import os
import sys

sys.path.insert(0, "/workspace/simulation-scripts")
from common import goal, paths, sim  # noqa: E402

tmp = "/tmp/pfc_smoke"
os.makedirs(tmp, exist_ok=True)
g = os.path.join(tmp, "ag.goal")
grp = g[:-5] + ".groups"
goal.gen_inc_goal(g, grp, 64, 4194304, "allgather", 100, root=-1)
goal.compile_goal(g, g[:-5] + ".bin")

out = "/workspace/simulation-scripts/results/_pfc_smoke"
os.makedirs(out, exist_ok=True)
fin, drops, st, cmd, ex = sim.run_sim(
    g[:-5] + ".bin", paths.topo("tree16_bw200Gbps.topo"),
    paths.topo("scaleup_single_switch_64_4000Gbps.topo"),
    nodes=64, gpus_per_node=64, groups=grp,
    pfc_trace=os.path.join(out, "smoke_trace.csv"),
    save_stdout=os.path.join(out, "smoke_stdout.gz"))
print("makespan", fin, "drops", drops, "status", st)
for k, v in ex.items():
    print(f"  {k} = {v}")
