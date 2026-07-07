#!/usr/bin/env python3
"""Patch tp_size/dp_size/pp_size literals in llama3_training.py (temporary; restored via git checkout)."""
import re
import sys

path = "/Users/wstaempfli/CLionProjects/atlahs/goal_gen/ai/nccl_generator_v2/simple_sim/llama3_training.py"
tp, dp, pp = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
assert tp * dp * pp == 16, "total ranks must stay 16"

src = open(path).read()
for name, val in (("tp_size", tp), ("dp_size", dp), ("pp_size", pp)):
    pat = rf"^    {name} = \d+$"
    new, n = re.subn(pat, f"    {name} = {val}", src, flags=re.M)
    assert n == 1, f"expected exactly 1 match for {name}, got {n}"
    src = new
open(path, "w").write(src)
print(f"patched: tp={tp} dp={dp} pp={pp}")
