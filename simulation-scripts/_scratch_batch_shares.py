#!/usr/bin/env python3
"""TP/DP byte shares for the batch-sensitivity table (post zero1 fix):
b in {1,8,16,32} at TP4/DP4 (2 layers), plus TP16/DP4 at b=32 for the
workload-figure cross-check. Generator only, no sims. Run from
goal_gen/ai/nccl_generator_v2 inside the sim image."""
import collections
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")
import simple_sim2goal as SG
from simple_sim.ir import CommOp as SimCommOp


def gen(tp, dp, batch, d):
    subprocess.run([sys.executable, "-m", "simple_sim.llama3_training",
                    "--tp", str(tp), "--dp", str(dp), "--pp", "1",
                    "--num-layers", "2", "--seq-len", "4096",
                    "--ffn", "11008", "--hidden", "4096",
                    "--heads", "32", "--kv-heads", "32",
                    "--batch", str(batch), "--iters", "2",
                    "--graphs-dir", d], check=True, capture_output=True)


ITERS = 2
print("cfg            TP_GB/rank  DP_GB/rank  TP_share  TP_GB_total  DP_GB_total")
for tp_deg, dp_deg, batch in ((4, 4, 1), (4, 4, 8), (4, 4, 16), (4, 4, 32),
                              (16, 4, 32)):
    d = f"/tmp/bshare_tp{tp_deg}_b{batch}"
    gen(tp_deg, dp_deg, batch, d)
    rn = SG.get_graphs(Path(d))
    nranks = tp_deg * dp_deg
    byctx = collections.Counter()
    for nodes in rn.values():
        for n in nodes:
            if isinstance(n, SimCommOp) and getattr(n, "inputs", None):
                sz = 2
                for x in n.inputs[0].shape:
                    sz *= x
                # mirror the translator's zero1 physical-shard correction
                # (2026-07-30): logical shapes overstate DP by the TP factor
                if (n.context == "zero1"
                        and getattr(n.inputs[0], "tp_group", None) is not None):
                    sz //= n.inputs[0].tp_group.size
                byctx[n.context] += sz
    tp_gb = byctx.get("tp", 0) / nranks / ITERS / 1e9
    dp_gb = byctx.get("zero1", 0) / nranks / ITERS / 1e9
    print(f"tp{tp_deg} b{batch:<3}      {tp_gb:9.3f}  {dp_gb:9.3f}  "
          f"{100 * tp_gb / (tp_gb + dp_gb):7.1f}%  "
          f"{tp_gb * nranks * ITERS:10.1f}  {dp_gb * nranks * ITERS:10.1f}")
