#!/usr/bin/env python3
"""Emit the LaTeX tables for the Reduce-Scatter / AllGather validation section.

Usage:  python3 _gen_rsag_tables.py            # writes into thesis-skeleton/figures/
        RSAG_OUT=/some/dir python3 _gen_rsag_tables.py

Every number is computed here from the measured CSVs plus constants derived from
the .topo files and the simulator's packet format; none is hand-typed, none fitted.

Fabric constants
  B    = 500 B/ns          .topo Downlink_speed_Gbps 4000
  t_l  = 50 ns             .topo Downlink_Latency_ns
  t_sw = 300 ns            .topo Switch_Latency_ns
  H    = 64 B              UecSrc::_hdr_size                       (uec.cpp:47)
  MSS  = 4086 B payload    _mss = _mtu - _hdr_size = 4150 - 64     (uec.cpp:47-49)
         NOTE: the chapter's notation block says 4096; it is 4086, and using the
         true value is what takes the in-network residual under 1 ns.

Models
  b       = S/N                                     per-rank block
  w       = min(b, MSS) + H                         on-wire size of one full chunk
  fill(d) = 2d t_l + (2d-1) t_sw + 2d w/B           leading frame down a depth-d path
  f_w(x)  = x + H ceil(x/MSS)                       on-wire size of an x-byte payload

  T_RS  = fill(d) + (N f_w(b) - w)/B                EGRESS-bound: a member injects a
                                                    contribution for all N slices.
                                                    Depth-uniform: the fold must reach
                                                    the apex, so every slice pays d_max.
  T_AG  = max over depth shells s of                INGRESS-bound: a member ingests the
          [ fill(s) + n_{>=s} f_w(b)/B - w/B ]      N-1 foreign blocks, which arrive from
                                                    DIFFERENT depths (a same-leaf peer's
                                                    block never goes near the apex) and
                                                    queue on the one ingress link.
                                                    On a single switch every peer is at
                                                    d=1 and this collapses to
                                                    fill(1) + ((N-1) f_w(b) - w)/B.
  T_ring= (N-1) (lambda(d_max) + f_w(b)/B)          all N steps run concurrently every
          lambda(d) = 2(2d t_l + (2d-1) t_sw)       round and the worst link recurs in
                      + (2d-1)(MSS+H)/B             every round, so it paces the schedule
"""
import csv, math, os

ROOT = "/Users/wstaempfli/CLionProjects/atlahs"
OUT  = os.environ.get("RSAG_OUT",
       os.path.expanduser("~/CLionProjects/thesis-skeleton/figures"))
T_L, T_SW, B, H, MSS = 50.0, 300.0, 500.0, 64, 4086
FRAME = MSS + H

# 64 contiguous ranks on the 3-tier fabric (4 hosts/leaf, 16/pod): foreign peers by depth
SHELLS_3TIER = {1: 3, 2: 12, 3: 48}

def wire(x):     return x + H*math.ceil(x/MSS)
def lam(d):      return 2*(2*d*T_L + (2*d-1)*T_SW) + (2*d-1)*FRAME/B
def fill(d, w):  return 2*d*T_L + (2*d-1)*T_SW + 2*d*w/B

def inc_rs(S, N, d):
    b = S//N; w = min(b, MSS) + H
    return fill(d, w) + (N*wire(b) - w)/B

def inc_ag(S, N, shells):
    """shells: {depth: #foreign peers at that depth}.  Single switch = {1: N-1}."""
    b = S//N; w = min(b, MSS) + H; wb = wire(b)/B
    return max(fill(s, w) + sum(n for d, n in shells.items() if d >= s)*wb - w/B
               for s in shells)

def ring(S, N, dmax):  return (N-1)*(lam(dmax) + wire(S//N)/B)

def census(N, per_leaf=4, per_pod=16):
    c = {1: 0, 2: 0, 3: 0}
    for i in range(N-1):
        d = 1 if i//per_leaf == (i+1)//per_leaf else (2 if i//per_pod == (i+1)//per_pod else 3)
        c[d] += 1
    return c

def sizetag(S):
    for unit, div in (("MB", 1<<20), ("KB", 1<<10)):
        if S >= div and S % div == 0:
            return f"{S//div}\\,{unit}"
    return f"{S:,}".replace(",", "{,}") + "\\,B"

def num(x):  return f"{round(x):,}".replace(",", "{,}")
def load(p): return list(csv.DictReader(open(p)))

def wrap(colspec, header, body):
    """A COMPLETE tabular.  Row bodies must not be \\input into a tabular from outside:
    \\input is not expandable, so TeX has already opened a cell by the time the fragment
    is read and the following \\hline dies with 'Misplaced \\noalign'."""
    return ("  \\begin{tabular}{%s}\n    \\hline\n%s\n    \\hline\n%s\n    \\hline\n"
            "  \\end{tabular}" % (colspec, header, body))

MAIN  = os.path.join(ROOT, 'simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv')
SWEEP = os.path.join(ROOT, 'simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv')
PODST = os.path.join(ROOT, 'simulation-scripts/results/_podstep/scaleup_coll_ab.csv')

# ── Table 1: single-switch |G|=64 ───────────────────────────────────────────
def table_single_switch():
    rows = [r for r in load(MAIN) if 'single_switch' in r['su_topo']
            and r['collective'] in ('reduce_scatter', 'allgather')]
    out, worst = [], 0.0
    for coll, label in (('reduce_scatter', 'Reduce-Scatter'), ('allgather', 'AllGather')):
        out.append(f"    \\multicolumn{{8}}{{l}}{{\\itshape {label}}}\\\\")
        for r in sorted([x for x in rows if x['collective'] == coll],
                        key=lambda r: int(r['msg_bytes'])):
            S, N = int(r['msg_bytes']), int(r['group_size'])
            mi, mb = float(r['inc_ns']), float(r['base_ns'])
            pi = inc_rs(S, N, 1) if coll == 'reduce_scatter' else inc_ag(S, N, {1: N-1})
            pb = ring(S, N, 1)
            worst = max(worst, abs(pi-mi))
            out.append(f"    {sizetag(S)} & {num(S//N)} & {num(mi)} & {num(pi)} & "
                       f"{num(mb)} & {num(pb)} & {mb/mi:.2f} & {pb/pi:.2f}\\\\")
    hdr = ("    & & \\multicolumn{2}{c}{$T_{\\mathrm{inc}}$ (ns)}\n"
           "    & \\multicolumn{2}{c}{$T_{\\mathrm{ring}}$ (ns)}\n"
           "    & \\multicolumn{2}{c}{speed-up}\\\\\n"
           "    size & $b$ [B] & meas. & model & meas. & model & meas. & model\\\\")
    return wrap("r r rr rr rr", hdr, "\n".join(out)), worst

# ── Table 2: group-size sweep at 4 MB ───────────────────────────────────────
def table_groupsweep():
    rows = [r for r in load(SWEEP) if r['collective'] in ('reduce_scatter', 'allgather')
            and int(r['msg_bytes']) == 4194304]
    byN = {}
    for r in rows:
        byN.setdefault(int(r['group_size']), {})[r['collective']] = r
    out = []
    for N in sorted(byN):
        rs, ag = byN[N]['reduce_scatter'], byN[N]['allgather']
        mrs, mag, mb = float(rs['inc_ns']), float(ag['inc_ns']), float(rs['base_ns'])
        out.append(f"    {N} & {num(mb)} & {num(mrs)} & {mb/mrs:.2f} & {(N-1)/N:.3f} & "
                   f"{num(mag)} & {mb/mag:.2f} & {1.0:.3f}\\\\")
    hdr = ("    & & \\multicolumn{3}{c}{Reduce-Scatter}\n"
           "      & \\multicolumn{3}{c}{AllGather}\\\\\n"
           "    $|G|$ & ring (ns) & in-net & speed-up & $S\\!\\to\\!\\infty$\n"
           "          & in-net & speed-up & $S\\!\\to\\!\\infty$\\\\")
    return wrap("r r rrr rrr", hdr, "\n".join(out))

# ── Table 3: three-tier, in-network, both collectives ───────────────────────
def table_3tier_inc():
    rows = [r for r in load(MAIN) if '3tier' in r['su_topo']
            and r['collective'] in ('reduce_scatter', 'allgather')]
    byS = {}
    for r in rows:
        byS.setdefault(int(r['msg_bytes']), {})[r['collective']] = r
    out = []
    for S in sorted(byS):
        rs, ag = byS[S]['reduce_scatter'], byS[S]['allgather']
        mr, ma = float(rs['inc_ns']), float(ag['inc_ns'])
        pr, pa = inc_rs(S, 64, 3), inc_ag(S, 64, SHELLS_3TIER)
        out.append(f"    {sizetag(S)} & {num(mr)} & {num(pr)} & {pr-mr:+.1f} & "
                   f"{num(ma)} & {num(pa)} & {100*(pa-ma)/ma:+.2f}\\\\")
    hdr = ("    & \\multicolumn{3}{c}{Reduce-Scatter (depth-uniform)}\n"
           "    & \\multicolumn{3}{c}{AllGather (mixed-depth)}\\\\\n"
           "    size & meas. & model & err.\\ [ns] & meas. & model & err.\\ [\\%]\\\\")
    return wrap("r rr r rr r", hdr, "\n".join(out))

# ── Table 4: three-tier ring, sum-of-steps vs slowest-step ──────────────────
def table_3tier_ring():
    rows = [r for r in load(MAIN) if '3tier' in r['su_topo']
            and r['collective'] == 'reduce_scatter']
    cen = census(64)
    sum_lam = sum(n*lam(d) for d, n in cen.items())
    out = []
    for r in sorted(rows, key=lambda r: int(r['msg_bytes'])):
        S, N = int(r['msg_bytes']), int(r['group_size'])
        mb = float(r['base_ns'])
        p_sum = sum_lam + (N-1)*wire(S//N)/B
        p_max = ring(S, N, 3)
        out.append(f"    {sizetag(S)} & {num(mb)} & {num(p_sum)} & {100*(p_sum-mb)/mb:+.1f} & "
                   f"{num(p_max)} & {100*(p_max-mb)/mb:+.1f}\\\\")
    hdr = ("    & & \\multicolumn{2}{c}{$\\sum_i\\lambda_i$ form}\n"
           "      & \\multicolumn{2}{c}{$(N-1)\\lambda_{\\max}$ form}\\\\\n"
           "    size & measured (ns) & model & err.\\ [\\%] & model & err.\\ [\\%]\\\\")
    return wrap("r r rr rr", hdr, "\n".join(out)), sum_lam

# ── Table 5: AllReduce, apex vs composed, both baselines ────────────────────
def inc_root(S, d):
    """Bcast / Reduce / apex AllReduce: one source stream of S through a depth-d path."""
    w = min(S, MSS) + H
    return fill(d, w) + (wire(S) - w)/B

def table_ar():
    rows = [r for r in load(MAIN) if 'single_switch' in r['su_topo']]
    def get(c, S, algo='ring'):
        x = [r for r in rows if r['collective'] == c and int(r['msg_bytes']) == S
             and r['baseline_algo'] == algo]
        return x[0] if x else None
    sizes = sorted(set(int(r['msg_bytes']) for r in rows))
    out = []
    for S in sizes:
        ar, rd, comp = get('allreduce', S), get('allreduce', S, 'rdouble'), get('allreduce_rs_ag', S)
        if not (ar and comp):
            continue
        N = 64
        m_ring, m_rd = float(ar['base_ns']), (float(rd['base_ns']) if rd else float('nan'))
        m_apex, m_comp = float(ar['inc_ns']), float(comp['inc_ns'])
        p_ring = 2*ring(S, N, 1)
        p_apex = inc_root(S, 1)
        p_comp = inc_rs(S, N, 1) + inc_ag(S, N, {1: N-1})
        out.append(f"    {sizetag(S)} & {num(m_ring)} & {num(m_rd)} & "
                   f"{num(m_apex)} & {num(p_apex)} & {num(m_comp)} & {num(p_comp)} & "
                   f"{m_ring/m_apex:.2f} & {m_ring/m_comp:.2f}\\\\")
    hdr = ("    & \\multicolumn{2}{c}{endpoint (ns)} & \\multicolumn{2}{c}{apex in-net (ns)}\n"
           "    & \\multicolumn{2}{c}{composed (ns)} & \\multicolumn{2}{c}{speed-up vs ring}\\\\\n"
           "    size & ring & rec.\\ dbl. & meas. & model & meas. & model & apex & comp.\\\\")
    return wrap("r rr rr rr rr", hdr, "\n".join(out))

# ── Table 6: the pod-boundary step ──────────────────────────────────────────
def table_podstep():
    if not os.path.exists(PODST):
        return None
    rows = [r for r in load(PODST) if r['collective'] == 'reduce_scatter']
    out, prev = [], None
    for r in sorted(rows, key=lambda r: int(r['group_size'])):
        N, S = int(r['group_size']), int(r['msg_bytes'])
        m = float(r['base_ns'])
        c = census(N); dmax = max(d for d, n in c.items() if n)
        p_max = (N-1)*(lam(dmax) + wire(S//N)/B)
        p_sum = sum(n*lam(d) for d, n in c.items()) + (N-1)*wire(S//N)/B
        step = f"{m/prev:.2f}$\\times$" if prev else "---"
        out.append(f"    {N} & {math.ceil(N/16)} & {num(lam(dmax))} & {num(m)} & {step} & "
                   f"{100*(p_max-m)/m:+.1f} & {100*(p_sum-m)/m:+.1f}\\\\")
        prev = m
    hdr = ("    & & & & & \\multicolumn{2}{c}{model error [\\%]}\\\\\n"
           "    $|G|$ & pods & $\\lambda_{\\max}$ & measured (ns) & vs.\\ previous "
           "& $(N{-}1)\\lambda_{\\max}$ & $\\sum_i\\lambda_i$\\\\")
    return wrap("r r r r r rr", hdr, "\n".join(out))

if __name__ == '__main__':
    t1, worst = table_single_switch()
    tables = [('tab_rsag_validation.tex', t1),
              ('tab_rsag_groupsweep.tex', table_groupsweep()),
              ('tab_rsag_3tier_inc.tex', table_3tier_inc())]
    t4, sum_lam = table_3tier_ring()
    tables.append(('tab_rsag_3tier_ring.tex', t4))
    tables.append(('tab_ar_validation.tex', table_ar()))
    t5 = table_podstep()
    if t5: tables.append(('tab_rsag_podstep.tex', t5))
    for name, body in tables:
        open(os.path.join(OUT, name), 'w').write(body + "%\n")
    print(f"wrote {len(tables)} tables to {OUT}")
    print(f"worst single-switch in-network residual: {worst:.2f} ns")
    print(f"lambda: d=1 {lam(1):.1f}  d=2 {lam(2):.1f}  d=3 {lam(3):.1f}")
    print(f"sum_lambda(3-tier,N=64) = {sum_lam:,.0f}   63*lambda(3) = {63*lam(3):,.0f}")
