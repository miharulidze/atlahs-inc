#!/usr/bin/env python3
"""Round 3: validate the canonical RS/AG models across the GROUP-SIZE sweep
(N = 2..64 on the single-switch crossbar), which is the clean N cross-check.

Canonical models (nothing fitted):
  b        = S/N                              per-rank block
  frame    = min(b, MSS)                      leading frame actually put on the wire
  fill(d)  = 2d*t_l + (2d-1)*t_sw + 2d*fw(frame)/B
  INC RS   = fill(d) + (fw(S)        - fw(frame))/B      egress-bound, N-INDEPENDENT
  INC AG   = fill(d) + ((N-1)*fw(b)  - fw(frame))/B      ingress-bound
  ring     = (N-1) * (lambda_max + fw(b)/B)              slowest step paces every round
"""
import csv, math, os

import os as _os
ROOT = _os.environ.get("ATLAHS_ROOT", _os.path.abspath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..")))
H, MSS, T_L, T_SW = 64, 4096, 50.0, 300.0

def fw(S):     return 0 if S <= 0 else S + H*math.ceil(S/MSS)
def t_ser(B):  return fw(MSS)/B
def lam(d,B):  return 2*(2*d*T_L + (2*d-1)*T_SW) + (2*d-1)*t_ser(B)
def fill(d,B,frame): return 2*d*T_L + (2*d-1)*T_SW + 2*d*(fw(frame)/B)

def inc_rs(S,N,d,B):
    frame = min(S/N, MSS)
    return fill(d,B,frame) + (fw(S) - fw(frame))/B

def inc_ag(S,N,d,B):
    b = S/N; frame = min(b, MSS)
    return fill(d,B,frame) + ((N-1)*fw(b) - fw(frame))/B

def ring(S,N,dmax,B):
    return (N-1)*(lam(dmax,B) + fw(S/N)/B)

def main():
    B, d = 500.0, 1
    p = os.path.join(ROOT,'simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv')
    rows = [r for r in csv.DictReader(open(p)) if r['collective'] in ('reduce_scatter','allgather')]

    print("GROUP-SIZE SWEEP, single-switch crossbar (d=1, B=500 B/ns)")
    print(f"{'coll':>15} {'N':>3} {'S':>9} | {'meas inc':>9} {'model':>9} {'err%':>7} "
          f"| {'meas base':>9} {'model':>9} {'err%':>7} | {'meas sp':>8} {'model sp':>8}")
    print('-'*118)
    worst_i = worst_b = 0.0
    for r in sorted(rows, key=lambda r:(r['collective'], int(r['msg_bytes']), int(r['group_size']))):
        N, S = int(r['group_size']), int(r['msg_bytes'])
        mi_meas, mb_meas = float(r['inc_ns']), float(r['base_ns'])
        mi = inc_rs(S,N,d,B) if r['collective']=='reduce_scatter' else inc_ag(S,N,d,B)
        mb = ring(S,N,d,B)
        ei, eb = 100*(mi-mi_meas)/mi_meas, 100*(mb-mb_meas)/mb_meas
        worst_i, worst_b = max(worst_i,abs(ei)), max(worst_b,abs(eb))
        print(f"{r['collective']:>15} {N:>3} {S:>9} | {mi_meas:>9,.0f} {mi:>9,.0f} {ei:>+7.2f} "
              f"| {mb_meas:>9,.0f} {mb:>9,.0f} {eb:>+7.2f} | {mb_meas/mi_meas:>8.3f} {mb/mi:>8.3f}")
    print(f"\nworst |err|: in-network {worst_i:.2f}%   ring {worst_b:.2f}%")

    # ── the asymptotic story ────────────────────────────────────────────────
    print(f"\n{'='*80}\nAsymptotic speed-up as S -> infinity (single switch)")
    print(f"{'N':>4} {'RS: (N-1)/N':>14} {'AG: 1':>8}   meaning")
    for N in (2,4,8,16,32,64):
        print(f"{N:>4} {(N-1)/N:>14.4f} {1.0:>8.2f}   in-network RS is {N/(N-1):.3f}x SLOWER than the ring")

if __name__ == '__main__':
    main()
