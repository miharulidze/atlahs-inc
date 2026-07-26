#!/usr/bin/env python3
"""Round 2: the ring RS/AG round is gated by the ring's SLOWEST step, not the sum;
and a physical lower bound for in-network AllGather on a mixed-depth fabric.

Run from anywhere.  Nothing fitted.
"""
import csv, math, os

import os as _os
ROOT = _os.environ.get("ATLAHS_ROOT", _os.path.abspath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..")))
H, MSS, T_L, T_SW = 64, 4096, 50.0, 300.0

def fw(S):
    return 0 if S <= 0 else S + H*math.ceil(S/MSS)
def t_ser(B, frame=MSS):
    return fw(frame)/B
def lam(d, B):
    return 2*(2*d*T_L + (2*d-1)*T_SW) + (2*d-1)*t_ser(B)
def fill(d, B, frame):
    """leading frame across 2d links and 2d-1 switches"""
    return 2*d*T_L + (2*d-1)*T_SW + 2*d*(fw(frame)/B)

# ── ring step census, contiguous placement ───────────────────────────────────
def census(N, hosts_per_leaf, leaves_per_pod):
    hpp = hosts_per_leaf*leaves_per_pod
    c = {1:0, 2:0, 3:0}
    for i in range(N-1):
        a, b = i, i+1
        d = 1 if a//hosts_per_leaf == b//hosts_per_leaf else (2 if a//hpp == b//hpp else 3)
        c[d] += 1
    return c

# ── the two competing ring models ────────────────────────────────────────────
def ring_sum(S, N, cen, B):
    """H1: sum of the step latencies (correct for a SEQUENTIAL chain)"""
    return sum(n*lam(d,B) for d,n in cen.items()) + (N-1)*fw(S/N)/B

def ring_max(S, N, cen, B):
    """H1': every round is paced by the ring's SLOWEST step, because all N steps
    run concurrently in every round and the slow link recurs in every round."""
    dmax = max(d for d,n in cen.items() if n > 0)
    return (N-1)*(lam(dmax,B) + fw(S/N)/B)

# ── in-network AllGather on a mixed-depth fabric: physical lower bound ───────
def ag_mixed_bound(S, N, groups, B, include_own=False):
    """groups: {depth: number of SOURCES at that depth (excluding self)}.
    All foreign blocks queue on the one host ingress link.  A block from depth d
    cannot begin arriving before fill(d).  Hence
        T = max_d [ fill(d) + (blocks at depth >= d) * w ]
    with w the on-wire time of one block.  This is a LOWER BOUND on completion:
    no implementation can beat it."""
    b = S/N
    w = fw(b)/B
    frame = min(b, MSS)
    best = 0.0
    depths = sorted(groups)
    for i, d in enumerate(depths):
        remaining = sum(groups[dd] for dd in depths[i:]) + (1 if include_own else 0)
        cand = fill(d, B, frame) + remaining*w - fw(frame)/B
        best = max(best, cand)
    return best

def load(p):
    return list(csv.DictReader(open(p)))

def main():
    B, N = 500.0, 64
    rows = load(os.path.join(ROOT,'simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv'))
    ss = [r for r in rows if 'single_switch' in r['su_topo']]
    ft = [r for r in rows if '3tier' in r['su_topo']]

    cen_ss = {1: N-1, 2: 0, 3: 0}
    cen_ft = census(N, 4, 4)
    print(f"ring step census: single-switch {cen_ss}   3-tier {cen_ft}")
    print(f"lambda(1)={lam(1,B):.1f}  lambda(2)={lam(2,B):.1f}  lambda(3)={lam(3,B):.1f}\n")

    for name, rws, cen in (("SINGLE-SWITCH", ss, cen_ss), ("3-TIER", ft, cen_ft)):
        print(f"{'='*92}\n{name}: ring baseline, sum-of-steps vs slowest-step-per-round")
        print(f"{'bytes':>12} {'measured':>10} | {'sum model':>10} {'err%':>8} | {'max model':>10} {'err%':>8}")
        print('-'*92)
        seen = set()
        for r in rws:
            if r['collective'] != 'reduce_scatter':
                continue
            S = int(r['msg_bytes'])
            if S in seen: continue
            seen.add(S)
            meas = float(r['base_ns'])
            m1, m2 = ring_sum(S,N,cen,B), ring_max(S,N,cen,B)
            print(f"{S:>12} {meas:>10,.0f} | {m1:>10,.0f} {100*(m1-meas)/meas:>+8.2f} "
                  f"| {m2:>10,.0f} {100*(m2-meas)/meas:>+8.2f}")
        print()

    # ── the 3-tier in-network AllGather against its physical lower bound ─────
    # 64 contiguous hosts on 4 hosts/leaf, 16/pod: 3 same-leaf, 12 same-pod, 48 cross-pod
    groups = {1: 3, 2: 12, 3: 48}
    print(f"{'='*92}\n3-TIER in-network AllGather vs the PHYSICAL LOWER BOUND")
    print(f"source census by depth (excluding self): {groups}   (sums to {sum(groups.values())} = N-1)")
    print(f"{'bytes':>12} {'measured':>10} {'lower bnd':>10} {'slack':>10}  verdict")
    print('-'*92)
    for r in ft:
        if r['collective'] != 'allgather': continue
        S = int(r['msg_bytes']); meas = float(r['inc_ns'])
        lb = ag_mixed_bound(S, N, groups, B)
        slack = meas - lb
        verdict = "OK" if slack >= -1 else "*** BELOW PHYSICAL LOWER BOUND ***"
        print(f"{S:>12} {meas:>10,.0f} {lb:>10,.0f} {slack:>+10,.0f}  {verdict}")

    print(f"\n{'='*92}\n3-TIER in-network AllGather: how many blocks does the ingress link carry?")
    print(f"{'bytes':>12} {'measured':>10} {'63 blocks':>10} {'err%':>8} {'64 blocks':>10} {'err%':>8}")
    print('-'*92)
    for r in ft:
        if r['collective'] != 'allgather': continue
        S = int(r['msg_bytes']); meas = float(r['inc_ns'])
        b = S/N; w = fw(b)/B; frame = min(b, MSS)
        m63 = fill(1,B,frame) + (63*fw(b) - fw(frame))/B
        m64 = fill(1,B,frame) + (64*fw(b) - fw(frame))/B
        print(f"{S:>12} {meas:>10,.0f} {m63:>10,.0f} {100*(m63-meas)/meas:>+8.2f} "
              f"{m64:>10,.0f} {100*(m64-meas)/meas:>+8.2f}")

if __name__ == '__main__':
    main()
