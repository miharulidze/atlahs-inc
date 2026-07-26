#!/usr/bin/env python3
"""Round 4: EXACT frame arithmetic.

The thesis notation block says MSS = 4096 B.  The simulator actually runs
  Packet::set_packet_size(4150)          htsim_app_atlahs.cpp:84,784
  UecSrc::_mtu = data_packet_size()      = 4150
  UecSrc::_mss = _mtu - _hdr_size        = 4150 - 64 = 4086
so the PAYLOAD mss is 4086 B and a full frame is 4150 B on the wire.

Reduce-Scatter additionally caps every chunk at the destination-block boundary
(uec_collectives.cpp:47-53), so a rank emits N*ceil(b/4086) frames, not
ceil(S/4086) -- one short tail frame per block whenever 4086 does not divide b.

Compare MSS=4096 (thesis) against MSS=4086 (actual) on every measured point.
"""
import csv, math, os

import os as _os
ROOT = _os.environ.get("ATLAHS_ROOT", _os.path.abspath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..")))
T_L, T_SW, B, HDR = 50.0, 300.0, 500.0, 64

def frames(x, mss):  return math.ceil(x/mss)
def wire(x, mss):    return x + HDR*frames(x, mss)          # a contiguous x-byte stream
def wire_rs(S, N, mss):                                     # N blocks, each cut separately
    b = S//N
    return N*(b + HDR*frames(b, mss))
def wire_ag(S, N, mss):                                     # the N-1 foreign blocks
    b = S//N
    return (N-1)*(b + HDR*frames(b, mss))

def full_frame(S, N, mss):  return min(S//N, mss) + HDR     # on-wire size of one full chunk

def t_inc(W, S, N, d, mss):
    wf = full_frame(S, N, mss)
    return 2*d*T_L + (2*d-1)*T_SW + (2*d-1)*wf/B + W/B

def lam(d, mss):
    return 2*(2*d*T_L + (2*d-1)*T_SW) + (2*d-1)*((mss+HDR)/B)

def ring(S, N, dmax, mss):
    b = S//N
    return (N-1)*(lam(dmax, mss) + wire(b, mss)/B)

def load(p): return list(csv.DictReader(open(p)))

def run(title, rows, d, dmax):
    print(f"\n{'='*112}\n{title}")
    print(f"{'coll':>15} {'N':>3} {'bytes':>10} {'meas':>10} | "
          f"{'MSS=4096':>10} {'err ns':>9} | {'MSS=4086':>10} {'err ns':>9} {'err %':>7}")
    print('-'*112)
    worst96 = worst86 = 0.0
    for r in rows:
        S, N = int(r['msg_bytes']), int(r['group_size'])
        coll = r['collective']
        meas = float(r['inc_ns'])
        res = {}
        for mss in (4096, 4086):
            W = wire_rs(S,N,mss) if coll=='reduce_scatter' else wire_ag(S,N,mss)
            res[mss] = t_inc(W, S, N, d, mss)
        e96, e86 = res[4096]-meas, res[4086]-meas
        worst96, worst86 = max(worst96,abs(e96)), max(worst86,abs(e86))
        print(f"{coll:>15} {N:>3} {S:>10} {meas:>10,.0f} | {res[4096]:>10,.1f} {e96:>+9.1f} "
              f"| {res[4086]:>10,.1f} {e86:>+9.2f} {100*e86/meas:>+7.3f}")
    print(f"worst |err|:  MSS=4096 -> {worst96:,.1f} ns    MSS=4086 -> {worst86:,.2f} ns")

def run_ring(title, rows, dmax):
    print(f"\n{'='*112}\n{title}  (ring baseline, slowest-step model)")
    print(f"{'N':>3} {'bytes':>10} {'meas':>10} | {'MSS=4096':>10} {'err %':>8} | {'MSS=4086':>10} {'err %':>8}")
    print('-'*112)
    seen=set()
    for r in rows:
        S, N = int(r['msg_bytes']), int(r['group_size'])
        if (S,N) in seen: continue
        seen.add((S,N))
        meas = float(r['base_ns'])
        a, c = ring(S,N,dmax,4096), ring(S,N,dmax,4086)
        print(f"{N:>3} {S:>10} {meas:>10,.0f} | {a:>10,.0f} {100*(a-meas)/meas:>+8.2f} "
              f"| {c:>10,.0f} {100*(c-meas)/meas:>+8.2f}")

if __name__ == '__main__':
    rows = load(os.path.join(ROOT,'simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv'))
    ss = [r for r in rows if 'single_switch' in r['su_topo'] and r['collective'] in ('reduce_scatter','allgather')]
    ft = [r for r in rows if '3tier' in r['su_topo'] and r['collective']=='reduce_scatter']
    gs = [r for r in load(os.path.join(ROOT,'simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv'))
          if r['collective'] in ('reduce_scatter','allgather')]

    run("SINGLE SWITCH d=1, |G|=64 (in-network)", sorted(ss,key=lambda r:(r['collective'],int(r['msg_bytes']))), 1, 1)
    run("3-TIER d=3, |G|=64, Reduce-Scatter only (AllGather column is unsound)",
        sorted(ft,key=lambda r:int(r['msg_bytes'])), 3, 3)
    run("GROUP SWEEP d=1 (in-network)", sorted(gs,key=lambda r:(r['collective'],int(r['msg_bytes']),int(r['group_size']))), 1, 1)
    run_ring("SINGLE SWITCH", sorted(ss,key=lambda r:int(r['msg_bytes'])), 1)
    run_ring("3-TIER", sorted(ft,key=lambda r:int(r['msg_bytes'])), 3)
    run_ring("GROUP SWEEP", sorted(gs,key=lambda r:(int(r['msg_bytes']),int(r['group_size']))), 1)
