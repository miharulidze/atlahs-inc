#!/usr/bin/env python3
"""Neutral schedule perturbation for GOAL traces (C3 sensitivity analysis, 2026-07-15).

Inserts a dependency-preserving `calc (rank % 4) * unit` node after each TP-op
completion point -- the coll node (mode `coll`, INC arm) or the op's final
intranode recv (mode `recv`, decomposed arm; 4 recvs per recursive-doubling op
per rank at |G|=4) -- and redirects the op's dependents through it. The
perturbation is nanosecond-scale (unit 100-400 ns against a >200 ms makespan)
and touches no byte counts, no op kinds, no dependency shape.

Purpose: measure the makespan's sensitivity to microscopic schedule shifts.
Result (see ../schedule_sensitivity.md): the 16-rank llama3 anchor's makespan
moves +-10-25 ms (+-5-9%) in BOTH arms under these perturbations, so
single-schedule A/B deltas at sub-percent TP share are below the trace's own
noise floor and must not be quoted as point estimates.

Usage:
  python3 perturb_schedule.py <in.goal> <out.goal> <coll|recv> <unit_ns>

Assumes the C3 layout (gpn=4, node = rank // 4). Compile the output with the
coll-extended txt2bin and run with the usual anchor invocation (including
-intranode_linkspeed 4000000, the NIC pinned to the pipes' realised rate).
"""
import re
import sys

inp, outp, mode, unit = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
txt = open(inp).read()
blocks = re.split(r'(rank \d+ \{)', txt)
out = [blocks[0]]
n = 0
for i in range(1, len(blocks), 2):
    hdr, body = blocks[i], blocks[i + 1]
    rank = int(re.match(r'rank (\d+)', hdr).group(1))
    node = rank // 4
    stagger = (rank % 4) * unit
    if mode == 'coll':
        targets = re.findall(r'(l\d+): coll \w+', body)
    else:
        intr = [m.group(1) for m in re.finditer(r'(l\d+): recv (\d+)b from (\d+)', body)
                if int(m.group(3)) // 4 == node]
        targets = intr[3::4]   # every 4th intranode recv = end of one RD op
    for lbl in targets:
        s = f'{lbl}s'
        body = re.sub(rf'^({lbl}: (?:coll|recv)[^\n]+)$',
                      rf'\1\n{s}: calc {stagger} cpu 0\n{s} requires {lbl}',
                      body, flags=re.M)
        body = re.sub(rf'^(l\d+) requires {lbl}$',
                      lambda m: m.group(0) if m.group(1) == s else f'{m.group(1)} requires {s}',
                      body, flags=re.M)
        n += 1
    out.append(hdr)
    out.append(body)
open(outp, 'w').write(''.join(out))
print(f'{outp}: perturbed {n} sites (unit {unit})')
