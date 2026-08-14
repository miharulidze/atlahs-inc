"""Cost of REPRESENTING one collective: decomposed p2p vs a first-class coll node."""
import os, sys, time, tempfile
sys.path.insert(0, "/workspace/simulation-scripts")
from common import goal
import csv

SIZE = 4 << 20
rows = []
for n in (16, 64, 256, 1024):
    rec = {"n": n}
    for algo in ("ring", "rdouble"):
        if algo == "rdouble" and (n & (n - 1)):
            continue
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.goal")
            t0 = time.time()
            try:
                goal.gen_baseline_goal(p, n, SIZE, "allreduce", algo, 100)
                gen = time.time() - t0
                nl = sum(1 for _ in open(p))
                rec[f"{algo}_lines"] = nl
                rec[f"{algo}_bytes"] = os.path.getsize(p)
                rec[f"{algo}_gen_s"] = round(gen, 2)
            except MemoryError:
                rec[f"{algo}_lines"] = -1
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "i.goal")
        g = os.path.join(d, "i.groups")
        t0 = time.time()
        goal.gen_inc_goal(p, g, n, SIZE, "allreduce", 100)
        rec["inc_lines"] = sum(1 for _ in open(p))
        rec["inc_bytes"] = os.path.getsize(p)
        rec["inc_gen_s"] = round(time.time() - t0, 2)
    rows.append(rec)
    print(rec, flush=True)

out = "/workspace/simulation-scripts/results/inc800_schedcost/schedcost.csv"
os.makedirs(os.path.dirname(out), exist_ok=True)
keys = sorted({k for r in rows for k in r})
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    w.writerows(rows)
print("wrote", out)
