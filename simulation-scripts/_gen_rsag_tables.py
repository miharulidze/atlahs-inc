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
  MSS  = 4096 B payload    the harness passes -mtu 4160, so _mss = 4160 - 64 = 4096
         (common/sim.py MTU_DEFAULT). The pcm driver's own default is 4150, which
         would give 4086 -- inconsistent with the engine's LogGOPS gap accounting
         (logsim-interface.cpp:959-961), which packetises at 4096 with a 4160 B frame.
         With 4096 every swept size is an exact multiple, so f_w is linear.

Models
  b       = S/N                                     per-rank block
  w       = min(b, MSS) + H                         on-wire size of one full chunk
  fill(d) = 2d t_l + (2d-1) t_sw + 2d w/B           leading frame down a depth-d path
  f_w(x)  = x + H ceil(x/MSS)                       on-wire size of an x-byte payload

  T_RS  = fill(d) + (c f_w(b) - w)/B                Fan-in, bound by the busiest link on
          c = N-1  no shared uplink above the       the member's path, and depth-uniform:
                   member (own-slice fold: the      the fold has to reach the apex, so
                   member skips its own slice)      every slice pays d_max.
          c = N    a shared uplink above it: that
                   uplink forwards all N slices
                   whatever the member skips
                                                    With c = N the model is a floor, not
                                                    an equality: measurement sits 5/6 of
                                                    a block time above it (see below).
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
T_L, T_SW, B, H, MSS = 50.0, 300.0, 500.0, 64, 4096
FRAME = MSS + H

# 64 contiguous ranks on the 3-tier fabric (4 hosts/leaf, 16/pod): foreign peers by depth
SHELLS_3TIER = {1: 3, 2: 12, 3: 48}

# Sweep restriction (2026-07-26).  lambda(d) charges t_ser = f_w(MSS)/B at each of the
# 2d-1 switches a round trip crosses, so for a per-rank shard b < MSS it over-charges by
# (MSS-b)/B per switch -- 508 ns on the crossbar and 2,540 ns on the three-tier fabric over
# 63 rounds at 4 KB, which was the whole of the ring model's error there.  The sharded
# collectives are therefore reported only where every shard fills at least one frame.
# Sub-threshold runs stay in the CSVs; they are filtered here, not deleted.
SHARDED = ('reduce_scatter', 'allgather', 'allreduce', 'allreduce_rs_ag')

def shard_ok(S, N):
    """Does one rank's shard fill at least a full MSS?  S >= N*MSS."""
    return S // N >= MSS

def wire(x):     return x + H*math.ceil(x/MSS)
def lam(d):      return 2*(2*d*T_L + (2*d-1)*T_SW) + (2*d-1)*FRAME/B
def fill(d, w):  return 2*d*T_L + (2*d-1)*T_SW + 2*d*w/B

def inc_rs(S, N, d, blocks=None):
    """`blocks` = how many b-byte blocks cross the link that binds the fan-in.

    Every member ships a contribution for EVERY slice, including the one it owns, so
    a block climbs for each of the N slices on every link of the member's path:
    blocks = N, on every fabric and at every depth.  No placement case, no floor.

    This is the datapath's default as of the 2026-07-27 fold retirement.  The own-slice
    local fold (-rs_local_fold) would drop the owner's own block and give N-1 where a
    link is unshared, but it desynchronises the members by one whole block -- each skips
    a DIFFERENT slice, so the switch holds a block of chunk accumulators open waiting for
    the laggard (1,028 vs 1 at |G|=16, S=64MB) and the collective runs 1/2 tau_b slower at
    d=2 and 5/6 tau_b slower at d=3.  Pass `blocks` explicitly to model that variant."""
    b = S//N; w = min(b, MSS) + H
    if blocks is None:
        blocks = N
    return fill(d, w) + (blocks*wire(b) - w)/B

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

def wrap(colspec, header, body, rules=None):
    """A COMPLETE tabular.  Row bodies must not be \\input into a tabular from outside:
    \\input is not expandable, so TeX has already opened a cell by the time the fragment
    is read and the following rule dies with 'Misplaced \\noalign'.

    booktabs rules (the chapter loads booktabs): \\toprule / \\midrule / \\bottomrule,
    no vertical rules, which is the standard for numeric tables. `rules` is an optional
    \\cmidrule line placed under the spanning header."""
    cmid = f"\n{rules}" if rules else ""
    return ("  \\begin{tabular}{%s}\n    \\toprule\n%s%s\n    \\midrule\n%s\n"
            "    \\bottomrule\n  \\end{tabular}" % (colspec, header, cmid, body))

MAIN  = os.path.join(ROOT, 'simulation-scripts/results/scaleup_coll_ab/scaleup_coll_ab.csv')
SWEEP = os.path.join(ROOT, 'simulation-scripts/results/scaleup_coll_ab_groupsweep/scaleup_coll_ab.csv')
PODST = os.path.join(ROOT, 'simulation-scripts/results/_podstep/scaleup_coll_ab.csv')

# ── Table 1: RS/AG on BOTH fabrics, three sizes ─────────────────────────────
def table_rsag_both():
    """Reduce-Scatter and AllGather on both fabrics at three sizes.

    The two share one endpoint baseline (identical traffic on the same cycle), so the
    ring columns are printed once.  In-network they part company on a multi-tier fabric:
    with the own-slice fold, RS binds at the top-most SHARED UPLINK (N blocks) and AG at
    the member's own ingress (N-1), and only on the crossbar -- where the member's link
    is the sole link -- do the two coincide.  Returned diagnostics: worst in-network
    residual on the crossbar, worst ring error, worst three-tier RS residual in units of
    a block time (the unmodelled fold artefact), and how many sizes have RS == AG."""
    rows = [r for r in load(MAIN) if r['baseline_algo'] == 'ring'
            and r['collective'] in ('reduce_scatter', 'allgather')]
    out, worst_ss, worst_b, blk_excess, same, npair = [], 0.0, 0.0, [], 0, 0
    for tag, key, d, shells in (("single switch", 'single_switch', 1, None),
                                ("three-tier", '3tier', 3, SHELLS_3TIER)):
        out.append(f"    \\multicolumn{{7}}{{l}}{{\\itshape {tag}}}\\\\")
        for S in PICK_RSAG:
            def get(coll):
                x = [r for r in rows if key in r['su_topo'] and r['collective'] == coll
                     and int(r['msg_bytes']) == S]
                return x[0] if x else None
            rs, ag = get('reduce_scatter'), get('allgather')
            if not (rs and ag):
                continue
            N = int(rs['group_size'])
            m_rs, m_ag, m_b = float(rs['inc_ns']), float(ag['inc_ns']), float(rs['base_ns'])
            p_rs = inc_rs(S, N, d)
            p_ag = inc_ag(S, N, {1: N-1} if shells is None else shells)
            p_b = ring(S, N, d)
            same += abs(m_rs - m_ag) <= 1
            npair += 1
            if d == 1:
                worst_ss = max(worst_ss, abs(p_rs - m_rs), abs(p_ag - m_ag))
            else:
                blk_excess.append((S, (m_rs - p_rs) / (wire(S//N)/B)))
            worst_b = max(worst_b, abs(100*(p_b - m_b)/m_b))
            out.append(f"    {sizetag(S)} & {num(m_rs)} & {num(p_rs)} & {num(m_ag)} & "
                       f"{num(p_ag)} & {num(m_b)} & {num(p_b)}\\\\")
    hdr = ("    & \\multicolumn{2}{c}{Reduce-Scatter, in-net}\n"
           "    & \\multicolumn{2}{c}{AllGather, in-net}\n"
           "    & \\multicolumn{2}{c}{ring (both)}\\\\\n"
           "    \\cmidrule(lr){2-3} \\cmidrule(lr){4-5} \\cmidrule(lr){6-7}\n"
           "    size & meas. & model & meas. & model & meas. & model\\\\")
    return (wrap("r rr rr rr", hdr, "\n".join(out)),
            worst_ss, worst_b, blk_excess, f"{same}/{npair}")

# ── Table 2: group-size sweep at 4 MB ───────────────────────────────────────
def table_groupsweep():
    rows = [r for r in load(SWEEP) if r['collective'] in ('reduce_scatter', 'allgather')
            and int(r['msg_bytes']) == 4194304
            and shard_ok(4194304, int(r['group_size']))]
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
            and r['collective'] in ('reduce_scatter', 'allgather')
            and shard_ok(int(r['msg_bytes']), int(r['group_size']))]
    byS = {}
    for r in rows:
        byS.setdefault(int(r['msg_bytes']), {})[r['collective']] = r
    out = []
    for S in sorted(byS):
        rs, ag = byS[S]['reduce_scatter'], byS[S]['allgather']
        mr, ma = float(rs['inc_ns']), float(ag['inc_ns'])
        pr, pa = inc_rs(S, 64, 3), inc_ag(S, 64, SHELLS_3TIER)
        blk = wire(S//64)/B
        out.append(f"    {sizetag(S)} & {num(mr)} & {num(pr)} & {(mr-pr)/blk:+.2f} & "
                   f"{num(ma)} & {num(pa)} & {100*(pa-ma)/ma:+.2f}\\\\")
    hdr = ("    & \\multicolumn{3}{c}{Reduce-Scatter (uplink-bound, $N$ blocks)}\n"
           "    & \\multicolumn{3}{c}{AllGather (ingress-bound, mixed depth)}\\\\\n"
           "    size & meas. & floor & excess [$\\tau_b$] & meas. & model & err.\\ [\\%]\\\\")
    return wrap("r rr r rr r", hdr, "\n".join(out))

# ── Table 4: three-tier ring, sum-of-steps vs slowest-step ──────────────────
def table_3tier_ring():
    rows = [r for r in load(MAIN) if '3tier' in r['su_topo']
            and r['collective'] == 'reduce_scatter'
            and shard_ok(int(r['msg_bytes']), int(r['group_size']))]
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

# ── Table 0: Broadcast (== Reduce) validation, single switch ────────────────
SEG = 512 * 1024
PICK = (4096, 262144, 67108864)          # one small, one mid, one large
PICK_RSAG = (262144, 4194304, 67108864)  # ditto, but every shard >= one MSS at |G|=64

def ring_chain(S, N, dmax=None, cen=None):
    """Rooted pipelined chain: K chunks, fill the N-1 stages then drain the rest.
    On a multi-tier fabric only ONE step is active at a time, so each is charged at its
    own depth: pass the per-depth step census. On the crossbar every step is d=1."""
    K = max(N, math.ceil(S / SEG)); c = S // K
    lat = (N - 1) * lam(dmax) if cen is None else sum(n * lam(d) for d, n in cen.items())
    return lat + (N + K - 2) * wire(c) / B

def table_duality():
    """Broadcast and Reduce side by side, ABSOLUTE measured completion times, both arms
    on both fabrics, so equality reads down each adjacent pair."""
    rows = load(MAIN)
    def t(coll, topo, S, col):
        x = [r for r in rows if r['collective'] == coll and topo in r['su_topo']
             and int(r['msg_bytes']) == S and r['baseline_algo'] == 'ring']
        return float(x[0][col]) if x else None
    out, worst = [], 0.0
    for S in sorted(set(int(r['msg_bytes']) for r in rows)):
        cells = []
        for topo in ('single_switch', '3tier'):
            for col in ('inc_ns', 'base_ns'):
                b, d = t('bcast', topo, S, col), t('reduce', topo, S, col)
                if b is None or d is None:
                    cells += ['---', '---']; continue
                worst = max(worst, abs(100 * (d - b) / b))
                cells.append(num(b))
                cells.append(num(d) if d == b else f"\\textit{{{num(d)}}}")
        out.append(f"    {sizetag(S)} & " + " & ".join(cells) + "\\\\")
    hdr = ("    & \\multicolumn{4}{c}{single switch} & \\multicolumn{4}{c}{three-tier}\\\\\n"
           "    \\cmidrule(lr){2-5} \\cmidrule(lr){6-9}\n"
           "    & \\multicolumn{2}{c}{in-network} & \\multicolumn{2}{c}{endpoint}\n"
           "    & \\multicolumn{2}{c}{in-network} & \\multicolumn{2}{c}{endpoint}\\\\\n"
           "    \\cmidrule(lr){2-3} \\cmidrule(lr){4-5} \\cmidrule(lr){6-7} \\cmidrule(lr){8-9}\n"
           "    size & Bc. & Rd. & Bc. & Rd. & Bc. & Rd. & Bc. & Rd.\\\\")
    return wrap("r rr rr rr rr", hdr, "\n".join(out)), worst

def table_bcast_both():
    """Broadcast on BOTH fabrics in one table, at three message sizes.

    Three sizes rather than nine: one latency-bound (4 KB), one in the knee (256 KB) and
    one bandwidth-bound (64 MB) is enough to pin two models on two fabrics without
    burying the reader in near-identical digits. The two fabrics differ in exactly two
    constants -- t_INC(1) vs t_INC(3) for the tree, and (N-1)lambda vs the step census
    sum_i lambda_i for the chain -- so putting them side by side is what makes the
    comparison legible."""
    rows = [r for r in load(MAIN) if r['collective'] == 'bcast'
            and r['baseline_algo'] == 'ring']
    cen = census(64)
    out, worst_i, worst_b = [], 0.0, 0.0
    for tag, key, d, c in (("single switch", 'single_switch', 1, None),
                           ("three-tier", '3tier', 3, cen)):
        out.append(f"    \\multicolumn{{6}}{{l}}{{\\itshape {tag}}}\\\\")
        for S in PICK:
            r = [x for x in rows if key in x['su_topo'] and int(x['msg_bytes']) == S]
            if not r:
                continue
            r = r[0]; N = int(r['group_size'])
            mi, mb = float(r['inc_ns']), float(r['base_ns'])
            pi = inc_root(S, d)
            pb = ring_chain(S, N, dmax=d, cen=c)
            K = max(N, math.ceil(S / SEG))
            worst_i = max(worst_i, abs(pi - mi))
            worst_b = max(worst_b, abs(100 * (pb - mb) / mb))
            out.append(f"    {sizetag(S)} & {K} & {num(mi)} & {num(pi)} & "
                       f"{num(mb)} & {num(pb)}\\\\")
    hdr = ("    & & \\multicolumn{2}{c}{$T_{\\mathrm{inc}}$ [ns]}\n"
           "    & \\multicolumn{2}{c}{$T_{\\mathrm{ring}}^{(1)}$ [ns]}\\\\\n"
           "    \\cmidrule(lr){3-4} \\cmidrule(lr){5-6}\n"
           "    size & $K$ & meas. & model & meas. & model\\\\")
    return wrap("r r rr rr", hdr, "\n".join(out)), worst_i, worst_b

# ── Table 5: AllReduce validation on BOTH fabrics ───────────────────────────
def inc_root(S, d):
    """Bcast / Reduce / apex AllReduce: one source stream of S through a depth-d path."""
    w = min(S, MSS) + H
    return fill(d, w) + (wire(S) - w)/B

def table_ar():
    """AllReduce on both fabrics at three sizes: measurement against the models.

    Same shape as the rooted and the RS/AG tables -- meas./model pairs and nothing else.
    Dropped 2026-07-27: the composed RS-then-AG in-network variant, which the chapter
    never models in the Validation chapter (it is an abstract construction in the Design
    chapter), and the speed-up columns, which now live in their own figure where the
    recursive-doubling baseline can be shown against the same denominator.

    Recursive doubling gets ONE column, not a pair: the chapter states that it carries
    the same bandwidth term as the ring but deliberately derives no completion-time
    model for it, so there is nothing to validate against and the entry is measured only.

    Both fabrics, three sizes each, mirroring table_rsag_both: the in-network model is
    depth-parameterised (t_d) and the ring one is paced by lambda(d_max) in every round,
    so the pair is exactly what the second fabric tests."""
    rows = [r for r in load(MAIN) if r['collective'] == 'allreduce'
            and shard_ok(int(r['msg_bytes']), int(r['group_size']))]
    out, worst_i, worst_b = [], 0.0, 0.0
    for tag, key, d in (("single switch", 'single_switch', 1),
                        ("three-tier", '3tier', 3)):
        out.append(f"    \\multicolumn{{6}}{{l}}{{\\itshape {tag}}}\\\\")
        for S in PICK_RSAG:
            def get(algo):
                x = [r for r in rows if key in r['su_topo']
                     and int(r['msg_bytes']) == S and r['baseline_algo'] == algo]
                return x[0] if x else None
            ar, rd = get('ring'), get('rdouble')
            if not ar:
                continue
            N = int(ar['group_size'])
            m_inc, m_ring = float(ar['inc_ns']), float(ar['base_ns'])
            p_inc, p_ring = inc_root(S, d), 2*ring(S, N, d)
            worst_i = max(worst_i, abs(p_inc - m_inc))
            worst_b = max(worst_b, abs(100*(p_ring - m_ring)/m_ring))
            out.append(f"    {sizetag(S)} & {num(m_inc)} & {num(p_inc)} & "
                       f"{num(m_ring)} & {num(p_ring)} & "
                       f"{num(float(rd['base_ns'])) if rd else '---'}\\\\")
    hdr = ("    & \\multicolumn{2}{c}{in-network (ns)}\n"
           "    & \\multicolumn{2}{c}{ring (ns)} & rec.\\ dbl.\\ (ns)\\\\\n"
           "    \\cmidrule(lr){2-3} \\cmidrule(lr){4-5} \\cmidrule(lr){6-6}\n"
           "    size & meas. & model & meas. & model & meas.\\\\")
    return wrap("r rr rr r", hdr, "\n".join(out)), worst_i, worst_b

# ── Table 6: the pod-boundary step ──────────────────────────────────────────
def table_podstep():
    if not os.path.exists(PODST):
        return None
    rows = [r for r in load(PODST) if r['collective'] == 'reduce_scatter'
            and shard_ok(int(r['msg_bytes']), int(r['group_size']))]
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
    t1, worst, wrb, wblk, same = table_rsag_both()
    tb, wbi, wbb = table_bcast_both()
    td, wd = table_duality()
    tables = [('tab_duality.tex', td),
              ('tab_bcast_validation.tex', tb),
              ('tab_rsag_validation.tex', t1),
              ('tab_rsag_groupsweep.tex', table_groupsweep()),
              ('tab_rsag_3tier_inc.tex', table_3tier_inc())]
    t4, sum_lam = table_3tier_ring()
    tables.append(('tab_rsag_3tier_ring.tex', t4))
    tar, wai, wab = table_ar()
    tables.append(('tab_ar_validation.tex', tar))
    t5 = table_podstep()
    if t5: tables.append(('tab_rsag_podstep.tex', t5))
    for name, body in tables:
        open(os.path.join(OUT, name), 'w').write(body + "%\n")
    print(f"wrote {len(tables)} tables to {OUT}")
    # no silent caps: say what the shard rule excluded and from where
    excl = {}
    for tag, src in (('scaleup_coll_ab', MAIN), ('groupsweep', SWEEP), ('_podstep', PODST)):
        if not os.path.exists(src):
            continue
        for r in load(src):
            if r['collective'] in SHARDED and not shard_ok(int(r['msg_bytes']),
                                                           int(r['group_size'])):
                excl.setdefault(tag, set()).add((int(r['group_size']), int(r['msg_bytes'])))
    print("shard rule S >= N*MSS excluded: "
          + ("; ".join(f"{t}: " + ", ".join(f"|G|={n} @ {S:,}B" for n, S in sorted(v))
                       for t, v in sorted(excl.items())) if excl else "nothing"))
    print(f"RS/AG: crossbar in-network {worst:.2f} ns (RS == AG at {same} table rows),"
          f" ring {wrb:+.2f}%")
    print("       three-tier RS excess over its uplink floor, in block times: "
          + ", ".join(f"{sizetag(S).replace(chr(92)+',',' ')} {e:+.2f}" for S, e in wblk))
    print(f"duality: worst |Reduce - Broadcast| = {wd:.3f}% (in-network exactly 0 everywhere)")
    print(f"Broadcast, both fabrics: in-network {wbi:.2f} ns, ring {wbb:+.2f}%")
    print(f"AllReduce, both fabrics: in-network {wai:.2f} ns, ring {wab:+.2f}%")
    print(f"lambda: d=1 {lam(1):.1f}  d=2 {lam(2):.1f}  d=3 {lam(3):.1f}")
    print(f"sum_lambda(3-tier,N=64) = {sum_lam:,.0f}   63*lambda(3) = {63*lam(3):,.0f}")
