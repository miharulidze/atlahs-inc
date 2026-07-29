#!/usr/bin/env python3
"""Traffic-composition probe: TP vs DP bytes per rank per iteration as a
function of micro-batch and depth (generator only, no sims). Run from
goal_gen/ai/nccl_generator_v2 inside the sim image."""
import collections
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")
import simple_sim2goal as SG
from simple_sim.ir import CommOp as SimCommOp


def gen(tp, dp, layers, batch, d):
    subprocess.run([sys.executable, "-m", "simple_sim.llama3_training",
                    "--tp", str(tp), "--dp", str(dp), "--pp", "1",
                    "--num-layers", str(layers), "--seq-len", "4096",
                    "--ffn", "11008", "--hidden", "4096",
                    "--heads", "32", "--kv-heads", "32",
                    "--batch", str(batch), "--iters", "2",
                    "--graphs-dir", d], check=True, capture_output=True)


ITERS = 2
print("cfg              TP_GB/rank  DP_GB/rank  TP_share   ~TPt_ms  ~DPt_ms  comp_ms")
for layers, batch in ((2, 1), (2, 4), (2, 8), (4, 4)):
    d = f"/tmp/comp_l{layers}_b{batch}"
    gen(4, 4, layers, batch, d)
    rn = SG.get_graphs(Path(d))
    byctx = collections.Counter()
    for nodes in rn.values():
        for n in nodes:
            if isinstance(n, SimCommOp) and getattr(n, "inputs", None):
                sz = 2
                for x in n.inputs[0].shape:
                    sz *= x
                byctx[n.context] += sz
    tp = byctx.get("tp", 0) / 16 / ITERS / 1e9
    dp = byctx.get("zero1", 0) / 16 / ITERS / 1e9
    # first-order serial times: ring 2(N-1)/N of S; TP over 4000 Gbps
    # (500 B/ns), DP over 400 Gbps (50 B/ns)
    tpt = tp * 1e9 * 1.5 / 500 / 1e6
    dpt = dp * 1e9 * 1.5 / 50 / 1e6
    comp_max, _ = SG.compute_ns_per_iter if False else (None, None)
    print(f"l{layers} b{batch} tp4dp4    {tp:9.3f}  {dp:9.3f}  "
          f"{100 * tp / (tp + dp):7.1f}%  {tpt:8.2f}  {dpt:7.2f}")
