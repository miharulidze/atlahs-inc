#!/usr/bin/env python3
"""Neutral schedule perturbation for G72 GOAL traces (noise-floor sampling).

Generalisation of ../../llama3_ab_pcm/tp_share_sweep/scripts/perturb_schedule.py
to arbitrary gpn and to the RING baseline used at |G| = 72 (the 16-rank suite's
recursive-doubling heuristic — every 4th intranode recv — does not apply).

Inserts a dependency-preserving `calc ((rank % 8) * unit)` node after selected
TP-op completion points and redirects the ops' dependents through it. ns-scale
against >100 ms makespans; no bytes, kinds, or dependency shape change.

Modes:
  coll   after every `coll` line                     (INC arm)
  recvk  after every 64th INTRANODE recv per rank    (ring baseline arm;
         a 72-ring AllReduce has 142 intranode recvs per rank per op, so
         this lands ~2 perturbation points per op per rank)

Usage: perturb_g72.py <in.goal> <out.goal> <coll|recvk> <unit_ns> <gpn>
"""
import re
import sys

inp, outp, mode, unit, gpn = (sys.argv[1], sys.argv[2], sys.argv[3],
                              int(sys.argv[4]), int(sys.argv[5]))
RECV_EVERY = 64

txt = open(inp).read()
blocks = re.split(r"(rank \d+ \{)", txt)
out = [blocks[0]]
n = 0
for i in range(1, len(blocks), 2):
    hdr, body = blocks[i], blocks[i + 1]
    rank = int(re.match(r"rank (\d+)", hdr).group(1))
    node = rank // gpn
    stagger = (rank % 8) * unit
    if mode == "coll":
        targets = re.findall(r"(l\d+): coll \w+", body)
    else:
        intr = [m.group(1) for m in
                re.finditer(r"(l\d+): recv (\d+)b from (\d+)", body)
                if int(m.group(3)) // gpn == node]
        targets = intr[RECV_EVERY - 1::RECV_EVERY]
    for lbl in targets:
        s = f"{lbl}s"
        body = re.sub(rf"^({lbl}: (?:coll|recv)[^\n]+)$",
                      rf"\1\n{s}: calc {stagger} cpu 0\n{s} requires {lbl}",
                      body, flags=re.M)
        body = re.sub(rf"^(l\d+) requires {lbl}$",
                      lambda m: (m.group(0) if m.group(1) == s
                                 else f"{m.group(1)} requires {s}"),
                      body, flags=re.M)
        n += 1
    out.append(hdr)
    out.append(body)
open(outp, "w").write("".join(out))
print(f"{outp}: perturbed {n} sites (mode {mode}, unit {unit}, gpn {gpn})")
