#!/usr/bin/env python3
"""Independent check of the candidate RS/AG analytic models against the measured CSVs.

Nothing here is fitted: every constant comes from the .topo files and the packet
format (B, t_l, t_sw, H, MSS).  Run from the atlahs repo root.
"""
import csv, math, os, sys

import os as _os
ROOT = _os.environ.get("ATLAHS_ROOT", _os.path.abspath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..")))
# ── fabric primitives (derived, not fitted) ──────────────────────────────────
H   = 64          # bytes of header per frame
MSS = 4096        # bytes of payload per frame
T_L = 50.0        # ns, link propagation
T_SW = 300.0      # ns, switch latency

def fw(S):
    """on-wire bytes for an S-byte payload"""
    if S <= 0:
        return 0
    return S + H * math.ceil(S / MSS)

def t_ser(B, frame=MSS):
    return fw(frame) / B

def t_inc(d, B, frame=MSS):
    """in-network floor: leading frame across 2d links, 2d-1 switches.
    Collapsed form used in the thesis: 2d*t_l + (2d-1)*(t_sw + t_ser)."""
    return 2*d*T_L + (2*d-1)*T_SW + (2*d-1)*t_ser(B, frame)

def t_inc_fill(d, B, frame):
    """uncollapsed fill: the leading frame is serialised on each of the 2d links."""
    return 2*d*T_L + (2*d-1)*T_SW + 2*d*(fw(frame)/B)

def lam(d, B):
    """ACK-gated ring-step round trip"""
    return 2*(2*d*T_L + (2*d-1)*T_SW) + (2*d-1)*t_ser(B)

# ── models ───────────────────────────────────────────────────────────────────
def inc_rs(S, N, d, B):
    """egress-bound: every rank injects the full S (one contribution per slice).
    fill = leading frame down one depth-d path; drain = rest of S on the egress link."""
    b = S / N
    frame = min(b, MSS)
    return t_inc_fill(d, B, frame) + (fw(S) - fw(frame)) / B

def inc_ag(S, N, d, B, own_block_returned=False):
    """ingress-bound: a rank ingests the N-1 foreign blocks (N if not source-pruned)."""
    b = S / N
    frame = min(b, MSS)
    nblocks = N if own_block_returned else N - 1
    return t_inc_fill(d, B, frame) + (nblocks * fw(b) - fw(frame)) / B

def ring_rs_ag(S, N, sum_lambda, B):
    """N-1 dependent rounds; every round all N links carry one block b=S/N;
    per-round cost = serialise b + one full round trip.  Latency per ROUND."""
    b = S / N
    return sum_lambda + (N - 1) * fw(b) / B

# ── ring step census for contiguous placement ────────────────────────────────
def sum_lambda_single_switch(N, B):
    return (N - 1) * lam(1, B)

def sum_lambda_3tier(N, B, hosts_per_leaf=4, leaves_per_pod=4):
    """contiguous host i = rank i on the 256-host 3-tier fabric (4/leaf, 16/pod).
    A ring step i->i+1 has depth 1 inside a leaf, 2 inside a pod, 3 across pods."""
    hosts_per_pod = hosts_per_leaf * leaves_per_pod
    tot = 0.0
    census = {1: 0, 2: 0, 3: 0}
    for i in range(N - 1):                      # open chain: N-1 steps
        a, bb = i, i + 1
        if a // hosts_per_leaf == bb // hosts_per_leaf:
            d = 1
        elif a // hosts_per_pod == bb // hosts_per_pod:
            d = 2
        else:
            d = 3
        census[d] += 1
        tot += lam(d, B)
    return tot, census

# ── measured data ────────────────────────────────────────────────────────────
def load(csvpath):
    with open(csvpath) as f:
        return list(csv.DictReader(f))

def report(title, rows, N, d, B, sum_lam, ag_own_returned=False):
    print(f"\n{'='*104}\n{title}   (N={N}, d={d}, B={B} B/ns, sum_lambda={sum_lam:,.0f} ns)")
    print(f"{'bytes':>12} {'coll':>15} | {'meas inc':>10} {'model inc':>10} {'err%':>7} "
          f"| {'meas base':>10} {'model base':>10} {'err%':>7}")
    print('-'*104)
    worst = 0.0
    for r in rows:
        S = int(r['msg_bytes']); coll = r['collective']
        meas_i = float(r['inc_ns']); meas_b = float(r['base_ns'])
        if coll == 'reduce_scatter':
            mi = inc_rs(S, N, d, B)
        elif coll == 'allgather':
            mi = inc_ag(S, N, d, B, own_block_returned=ag_own_returned)
        else:
            continue
        mb = ring_rs_ag(S, N, sum_lam, B)
        ei = 100*(mi-meas_i)/meas_i
        eb = 100*(mb-meas_b)/meas_b
        worst = max(worst, abs(ei))
        print(f"{S:>12} {coll:>15} | {meas_i:>10,.0f} {mi:>10,.0f} {ei:>+7.2f} "
              f"| {meas_b:>10,.0f} {mb:>10,.0f} {eb:>+7.2f}")
    print(f"worst |err| on the in-network column: {worst:.2f}%")

def main():
    B = 500.0   # 4000 Gbps
    N = 64
    rows = load(os.path.join(ROOT, 'simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv'))
    ss = [r for r in rows if 'single_switch' in r['su_topo']]
    ft = [r for r in rows if '3tier' in r['su_topo']]

    print(f"derived constants at B={B} B/ns:")
    print(f"  t_ser        = {t_ser(B):.3f} ns")
    for d in (1, 2, 3):
        print(f"  t_INC({d})     = {t_inc(d,B):8.2f} ns    lambda({d}) = {lam(d,B):8.2f} ns")

    sl_ss = sum_lambda_single_switch(N, B)
    sl_ft, census = sum_lambda_3tier(N, B)
    print(f"  ring census 3-tier: {census}  ->  sum_lambda = {sl_ft:,.0f} ns")

    report("SINGLE-SWITCH crossbar (d=1)", ss, N, 1, B, sl_ss)
    report("3-TIER 256-host fat tree (d=3), AG source-pruned", ft, N, 3, B, sl_ft)
    report("3-TIER 256-host fat tree (d=3), AG own block RETURNED", ft, N, 3, B, sl_ft,
           ag_own_returned=True)

    # ── |G|=16 set: different topology + link speed, so re-derive B from the CSV
    print(f"\n{'='*104}\n|G|=16 dataset")
    g16 = load(os.path.join(ROOT, 'simulation-scripts/results/scaleup_coll_ab_g16/scaleup_coll_ab.csv'))
    speeds = set(r['intranode_linkspeed_mbps'] for r in g16)
    topos  = set(r['su_topo'] for r in g16)
    print(f"  su_topo(s): {topos}")
    print(f"  intranode_linkspeed_mbps: {speeds}")
    for sp in speeds:
        print(f"  -> B = {float(sp)/8/1000:.1f} B/ns")

if __name__ == '__main__':
    main()
