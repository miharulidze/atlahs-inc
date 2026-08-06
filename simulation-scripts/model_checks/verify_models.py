#!/usr/bin/env python3
"""Re-derive every numerical claim the Validation chapter makes about the collective
models, straight from the committed CSVs.  Exit 0 if they all hold, 1 otherwise.

    python3 simulation-scripts/model_checks/verify_models.py

The model functions are IMPORTED from simulation-scripts/_gen_rsag_tables.py rather
than restated here, so this check cannot drift away from the generator that produces
the thesis tables: if someone edits a model, both the tables and this verifier move
together, and a claim that stops holding shows up as a FAIL.

Nothing here is fitted.  Every constant (B, t_l, t_sw, H, MSS) is read off the .topo
files and the simulator's packet format; see the generator's header.
"""
import csv, importlib.util, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ATLAHS_ROOT", os.path.abspath(os.path.join(HERE, "..", "..")))

_spec = importlib.util.spec_from_file_location(
    "rsag_tables", os.path.join(ROOT, "simulation-scripts", "_gen_rsag_tables.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

SS, FT = "single_switch", "3tier"
FAILS = []


def load(p):
    with open(p) as f:
        return list(csv.DictReader(f))


def check(name, ok, detail):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        FAILS.append(name)


def rows_of(path, topo=None, coll=None, algo="ring"):
    """Rows for one (topology, collective, baseline) triple.

    Applies the chapter's sweep restriction: a sharded collective is only reported where
    one rank's shard fills at least a full MSS (S >= N*MSS). Below that, lambda(d)'s
    full-MTU t_ser over-charges the ring model, so the residual measures the model's
    frame accounting rather than the fabric. See M.shard_ok."""
    out = load(path)
    if topo:
        out = [r for r in out if topo in r["su_topo"]]
    if coll:
        out = [r for r in out if r["collective"] == coll]
    out = [r for r in out if r["baseline_algo"] == algo]
    return [r for r in out
            if r["collective"] not in M.SHARDED
            or M.shard_ok(int(r["msg_bytes"]), int(r["group_size"]))]


# ── 1. in-network models are exact on the single-switch crossbar ──────────────
def check_single_switch():
    print("\n1. Single-switch crossbar: in-network models exact to under 1 ns")
    resid, neg = [], 0
    for src in (M.MAIN, M.SWEEP):
        for coll in ("reduce_scatter", "allgather"):
            for r in rows_of(src, SS if src == M.MAIN else None, coll):
                S, N = int(r["msg_bytes"]), int(r["group_size"])
                meas = float(r["inc_ns"])
                pred = (M.inc_rs(S, N, 1) if coll == "reduce_scatter"
                        else M.inc_ag(S, N, {1: N - 1}))
                resid.append(pred - meas)
                neg += (pred - meas) < 0
    worst = max(abs(x) for x in resid)
    check("in-network residual < 1 ns", worst < 1.0,
          f"{len(resid)} points, worst {worst:.2f} ns")
    check("every residual positive (ns truncation, not fitting)", neg == 0,
          f"{neg} negative of {len(resid)}")


# ── 2. the ring baseline is paced by its SLOWEST step, not the sum ────────────
def check_ring():
    print("\n2. Endpoint ring: (N-1)*lambda_max, not sum_i lambda_i")
    worst_max, worst_sum = 0.0, 0.0
    n = 0
    for src, topo, d in ((M.MAIN, SS, 1), (M.MAIN, FT, 3), (M.SWEEP, None, 1)):
        seen = set()
        for r in rows_of(src, topo, "reduce_scatter"):
            S, N = int(r["msg_bytes"]), int(r["group_size"])
            if (S, N, d) in seen:
                continue
            seen.add((S, N, d))
            meas = float(r["base_ns"])
            cen = M.census(N)
            p_max = M.ring(S, N, d)
            p_sum = sum(k * M.lam(dd) for dd, k in cen.items()) + (N - 1) * M.wire(S // N) / M.B
            worst_max = max(worst_max, abs(100 * (p_max - meas) / meas))
            worst_sum = max(worst_sum, abs(100 * (p_sum - meas) / meas))
            n += 1
    check("slowest-step form within 0.60%", worst_max < 0.60,
          f"{n} points, worst {worst_max:.2f}% (was 1.04% before the shard rule "
          f"excluded S < N*MSS)")
    check("sum-of-steps form is refuted (>10% somewhere)", worst_sum > 10,
          f"worst {worst_sum:.1f}% -- the two forms are cleanly separated")


# ── 3. the pod-boundary step ──────────────────────────────────────────────────
def check_podstep():
    print("\n3. Pod boundary: one rank steps the schedule by ~1.75x")
    if not os.path.exists(M.PODST):
        print("  [SKIP] results/_podstep not present")
        return
    by_n, worst = {}, 0.0
    for r in rows_of(M.PODST, coll="reduce_scatter"):
        N, S = int(r["group_size"]), int(r["msg_bytes"])
        meas = float(r["base_ns"])
        by_n[N] = meas
        cen = M.census(N)
        dmax = max(d for d, k in cen.items() if k)
        worst = max(worst, abs(100 * (M.ring(S, N, dmax) - meas) / meas))
    check("slowest-step form within 0.15%", worst < 0.15,
          f"{len(by_n)} group sizes, worst {worst:.2f}%")
    if 16 in by_n and 17 in by_n:
        step = by_n[17] / by_n[16]
        check("16 -> 17 step is 1.6x-1.9x", 1.6 < step < 1.9,
              f"{step:.2f}x (16 fits one pod, 17 does not)")


# ── 3a. the naive datapath pins Reduce-Scatter's kappa = N on BOTH fabrics ────
def check_naive_arm():
    print("\n3a. Default datapath: Equation (rsag-inc-rs) with kappa = N")
    path = os.path.join(ROOT, "simulation-scripts/results/_fold_off/scaleup_coll_ab.csv")
    if not os.path.exists(path):
        print("  [SKIP] results/_fold_off not present")
        return
    resid, neg = [], 0
    for topo, d in ((SS, 1), (FT, 3)):
        for r in rows_of(path, topo, "reduce_scatter"):
            e = M.inc_rs(int(r["msg_bytes"]), 64, d, blocks=64) - float(r["inc_ns"])
            resid.append(e)
            neg += e < 0
    # this is the arm the model was DERIVED for: no fold, so N blocks everywhere
    check("kappa = N is exact on both fabrics", max(map(abs, resid)) < 1.0,
          f"{len(resid)} points, worst {max(map(abs, resid)):.2f} ns")
    check("every residual positive (ns truncation, not fitting)", neg == 0,
          f"{neg} negative of {len(resid)}")

    on = os.path.join(ROOT, "simulation-scripts/results/_fold_on/scaleup_coll_ab.csv")
    if not os.path.exists(on):
        return
    # the two knee sizes scatter; the bandwidth-bound tail converges on 5/6
    for topo, lo, hi, what in ((SS, -1.05, -0.90, "saves one block"),
                               (FT,  0.70,  1.00, "costs 3/4 to one block")):
        d = []
        for r in rows_of(on, topo, "reduce_scatter"):
            S = int(r["msg_bytes"])
            naive = [x for x in rows_of(path, topo, "reduce_scatter")
                     if int(x["msg_bytes"]) == S]
            if naive and S >= 65536:                 # above the latency knee
                d.append((float(r["inc_ns"]) - float(naive[0]["inc_ns"]))
                         / (M.wire(S // 64) / M.B))
        check(f"on the {'crossbar' if topo == SS else 'three-tier fabric'} the fold "
              f"{what}", all(lo < x < hi for x in d),
              f"{len(d)} sizes, {min(d):+.3f} to {max(d):+.3f} block times")


# ── 3b. the own-slice fold: RS == AG on the crossbar, RS > AG above it ────────
def check_fold_symmetry():
    """Which link binds the fan-in, now that the own-slice fold is RETIRED (2026-07-27).
    With kappa = N everywhere, Reduce-Scatter puts N blocks on the member's egress while
    AllGather's ingress still takes only the N-1 it does not already hold, so the two
    part company by EXACTLY one block time even on a crossbar -- where, under the fold,
    they used to coincide."""
    print("\n3b. kappa = N on every link: which link binds the fan-in")
    for topo, want_equal in ((SS, True), (FT, False)):
        pairs = []
        for r in rows_of(M.MAIN, topo, "reduce_scatter"):
            S = int(r["msg_bytes"])
            ag = [x for x in rows_of(M.MAIN, topo, "allgather")
                  if int(x["msg_bytes"]) == S]
            if ag:
                pairs.append((S, float(r["inc_ns"]), float(ag[0]["inc_ns"])))
        eq = [abs(rs - ag) <= 1 for _, rs, ag in pairs]
        if want_equal:
            # RS ships N blocks off the member, AG receives N-1: exactly one block apart
            # the models differ by exactly wire(b)/B, so test that in NANOSECONDS:
            # as a ratio the small sizes look off by 4% purely because tau_b is 8 ns
            # there and the simulator reports on a 1 ns grid.
            errs = [abs((rs - ag) - M.wire(S // 64) / M.B) for S, rs, ag in pairs]
            check("crossbar: Reduce-Scatter is AllGather plus exactly one block time",
                  all(e < 1.0 for e in errs),
                  f"{len(errs)} sizes, |gap - tau_b| worst {max(errs):.2f} ns "
                  "-- the block the retired fold used to save")
        else:
            # a shared uplink sits above every member and carries all N slices
            worst = max((rs - ag) / ag for _, rs, ag in pairs)
            check("three-tier: Reduce-Scatter strictly slower than AllGather",
                  all(rs >= ag for _, rs, ag in pairs),
                  f"{len(pairs)} sizes, up to {100*worst:+.2f}% -- the shared uplink")


# ── 4. three-tier: RS uplink-bound, AG ingress-bound and shell-aware ──────────
def check_three_tier():
    print("\n4. Three-tier fabric: Reduce-Scatter uplink-bound, AllGather shell-aware")
    exc = []
    for r in rows_of(M.MAIN, FT, "reduce_scatter"):
        S = int(r["msg_bytes"])
        floor = M.inc_rs(S, 64, 3)                       # N blocks over the uplink
        exc.append((S, (float(r["inc_ns"]) - floor) / (M.wire(S // 64) / M.B)))
    # Before the fold was retired (2026-07-27) this was a FLOOR: the measurement sat
    # 5/6 of a block time above it, because each member skipped a DIFFERENT slice and
    # the resulting one-block skew stalled the switch's fan-in. With kappa = N the
    # members are back in lockstep and the model is an equality.
    check("uplink model is exact above the latency knee, not a floor",
          all(abs(e) < 0.05 for S, e in exc if S >= 65536),
          "residual in block times: "
          + ", ".join(f"{S>>10}K {e:+.3f}" for S, e in exc if S >= 65536))
    band = [e for S, e in exc if S >= 4194304]
    check("no residual fold artefact once bandwidth-bound",
          all(abs(e) < 0.01 for e in band),
          f"{len(band)} points, {min(band):+.4f}--{max(band):+.4f} block times")

    # AllGather used to run ~1 block time over its model above a 512 KB shard on this
    # fabric, and only on this fabric. That was never a modelling error: the driver's
    # default PFC pause threshold (100 packets) sits below the fan-in backlog a large
    # collective builds, so the pause fired and throttled the ingress. The harness now
    # declares 300 (sim.LOSSLESS_HIGH_PFC_DEFAULT, knee measured at 128, queue ceiling
    # 439) and the model is exact at every size. agprobe{2,3}.sh hold the evidence:
    # a 1000x queue changes nothing, and LOWERING the threshold reintroduces the
    # deviation at a size where it does not otherwise occur, up to +139 tau_b.
    ag = [(int(r["msg_bytes"]),
           float(r["inc_ns"]) - M.inc_ag(int(r["msg_bytes"]), 64, M.SHELLS_3TIER))
          for r in rows_of(M.MAIN, FT, "allgather")]
    check("AllGather is exact at every size, PFC pause given headroom",
          all(abs(e) < 1.0 for _, e in ag),
          f"{len(ag)} points, worst {max(abs(e) for _, e in ag):.2f} ns")


# ── 5. AllReduce: apex wins on bandwidth, composition forfeits it ─────────────
def check_allreduce():
    print("\n5. AllReduce: apex vs composed RS-then-AG")
    main = load(M.MAIN)

    def one(coll, S, algo="ring"):
        x = [r for r in main if r["collective"] == coll and SS in r["su_topo"]
             and int(r["msg_bytes"]) == S and r["baseline_algo"] == algo]
        return x[0] if x else None

    worst_ident, worst_apex, worst_comp = 0.0, 0.0, 0.0
    for r in rows_of(M.MAIN, SS, "allreduce"):
        S = int(r["msg_bytes"])
        if not M.shard_ok(S, 64):
            continue
        rs, cp = one("reduce_scatter", S), one("allreduce_rs_ag", S)
        if not (rs and cp):
            continue
        worst_ident = max(worst_ident, abs(float(r["base_ns"]) - 2 * float(rs["base_ns"])))
        worst_apex = max(worst_apex, abs(M.inc_root(S, 1) - float(r["inc_ns"])))
        worst_comp = max(worst_comp,
                         abs(M.inc_rs(S, 64, 1) + M.inc_ag(S, 64, {1: 63})
                             - float(cp["inc_ns"])))
    check("ring AllReduce == 2 x ring Reduce-Scatter", worst_ident <= 1.0,
          f"worst {worst_ident:.0f} ns over all sizes")
    check("apex model under 1 ns", worst_apex < 1.0, f"worst {worst_apex:.2f} ns")
    check("composed model == RS + AG, under 1 ns", worst_comp < 1.0,
          f"worst {worst_comp:.2f} ns")

    big = 268435456
    ar, cp = one("allreduce", big), one("allreduce_rs_ag", big)
    if ar and cp:
        sp_apex = float(ar["base_ns"]) / float(ar["inc_ns"])
        sp_comp = float(ar["base_ns"]) / float(cp["inc_ns"])
        check("apex ~2x at 256 MB while the composition ~1x", sp_apex > 2.0 > sp_comp,
              f"apex {sp_apex:.2f}x, composed {sp_comp:.2f}x "
              f"(asymptotes 2(N-1)/N = {2*63/64:.3f} and, since the folded composition "
              f"moves the ring's own 2(N-1)/N bytes, exactly 1)")


# ── 5b. shell symmetry: is one member's bound the collective's? ───────────────
def check_shell_symmetry():
    """Equation (rsag-inc-ag) is written for ONE member and compared with the whole
    collective. That is only legitimate when every member sees the same shells, which
    holds iff the group fills whole leaves and whole pods. Verified both ways: our
    |G|=64 is symmetric, the pod-boundary sweep mostly is not, and taking a further
    maximum over members predicts the asymmetric cases too."""
    print("\n5b. Shell symmetry: when is one member's bound the collective's?")

    def profile(members, h, per_leaf=4, per_pod=16):
        return {1: sum(1 for x in members if x != h and x // per_leaf == h // per_leaf),
                2: sum(1 for x in members if x // per_pod == h // per_pod
                       and x // per_leaf != h // per_leaf),
                3: sum(1 for x in members if x // per_pod != h // per_pod)}

    main64 = {frozenset(profile(list(range(64)), h).items()) for h in range(64)}
    check("the chapter's |G|=64 is symmetric: every member sees 3/12/48",
          len(main64) == 1 and dict(next(iter(main64))) == {1: 3, 2: 12, 3: 48},
          f"{len(main64)} distinct shell profile(s)")

    if not os.path.exists(M.PODST):
        print("  [SKIP] results/_podstep not present")
        return
    worst, asym = 0.0, 0
    for r in rows_of(M.PODST, coll="allgather"):
        N, S = int(r["group_size"]), int(r["msg_bytes"])
        mem = list(range(N))
        profs = {frozenset(profile(mem, h).items()) for h in mem}
        asym += len(profs) > 1
        # the straggler decides, so take a max over members as well as over shells
        pred = max(M.inc_ag(S, N, {k: v for k, v in dict(pr).items() if v} or {1: 0})
                   for pr in profs)
        worst = max(worst, abs(pred - float(r["inc_ns"])))
    check("max over members predicts the asymmetric groups too, under 1 ns",
          worst < 1.0,
          f"{asym} of 8 group sizes asymmetric, worst residual {worst:.2f} ns")


# ── 5c. the shell maximum is EXACT, not just a bound ──────────────────────────
def check_busy_period():
    """The chapter claims max_s [t_INC(s) + n_>=s tau_b] is the completion time, not merely
    a lower bound on it. That rests on the ingress being work-conserving: for a single
    server with release dates, the last completion equals the largest such bound, because
    the final busy period starts at some release and everything released earlier was
    already cleared. Simulate the server directly and compare -- and record WHICH shell
    starts the final busy period, since that is what 'binds' means."""
    print("\n5c. Shell maximum is exact: work-conserving server vs max over shells")
    w = M.MSS + M.H
    tinc = {s: M.fill(s, w) - w / M.B for s in (1, 2, 3)}
    worst, starts = 0.0, {}
    for r in rows_of(M.MAIN, FT, "allgather"):
        S = int(r["msg_bytes"])
        tb = M.wire(S // 64) / M.B
        t, start = 0.0, None
        for sh in (1, 2, 3):                       # serve the batches in release order
            if t < tinc[sh]:                       # link idles -> busy period restarts
                t, start = tinc[sh], sh
            t += M.SHELLS_3TIER[sh] * tb
        closed = max(tinc[sh] + sum(n for d, n in M.SHELLS_3TIER.items() if d >= sh) * tb
                     for sh in (1, 2, 3))
        worst = max(worst, abs(t - closed))
        starts.setdefault(start, []).append(S)
    check("simulated server == closed-form maximum, under 0.01 ns", worst < 0.01,
          f"worst {worst:.4f} ns over {sum(len(v) for v in starts.values())} sizes")
    check("the binding shell is the one starting the final busy period, and it moves",
          len(starts) > 1,
          "; ".join(f"s={k} at " + ", ".join(f"{S>>10}K" for S in sorted(v))
                    for k, v in sorted(starts.items())))


# ── 5d. the shell hand-over in closed form ────────────────────────────────────
def check_handover():
    """The chapter gives the hand-over between shells in closed form: each extra tier costs
    a constant 2(t_l + t_sw + w/B), so shell s+1 overtakes shell s exactly when
    |shell s| tau_b falls below that. Pins the constant and the predicted binding shell."""
    print("\n5d. Shell hand-over: closed form vs the argmax")
    w = M.MSS + M.H
    tinc = {s: M.fill(s, w) - w / M.B for s in (1, 2, 3)}
    step = 2 * (M.T_L + M.T_SW + w / M.B)
    check("t_INC increment per tier is constant 2(t_l + t_sw + w/B)",
          all(abs((tinc[s + 1] - tinc[s]) - step) < 0.01 for s in (1, 2)),
          f"{tinc[2]-tinc[1]:.2f} and {tinc[3]-tinc[2]:.2f} ns against {step:.2f}")

    sh, bad = M.SHELLS_3TIER, []
    for r in rows_of(M.MAIN, FT, "allgather"):
        S = int(r["msg_bytes"])
        tb = M.wire(S // 64) / M.B
        # deepest shell whose predecessor's blocks are cheaper than one tier
        pred = 3 if sh[2] * tb < step else (2 if sh[1] * tb < step else 1)
        argmax = max((1, 2, 3),
                     key=lambda s: tinc[s] + sum(n for d, n in sh.items() if d >= s) * tb)
        if pred != argmax:
            bad.append((S, pred, argmax))
    check("closed form picks the same shell as the argmax at every size", not bad,
          f"{len(list(rows_of(M.MAIN, FT, 'allgather')))} sizes agree"
          if not bad else f"mismatches: {bad}")
    # Invert f_w(b), not b: every frame carries an H-byte header. The integer search
    # also retains f_w's ceil for the final partial frame.
    def payload_at_wire_bytes(target):
        lo, hi = 1, math.ceil(target)
        while lo < hi:
            mid = (lo + hi) // 2
            if M.wire(mid) < target:
                lo = mid + 1
            else:
                hi = mid
        return lo

    handover_mib = {
        s: 64 * payload_at_wire_bytes((step / sh[s]) * M.B) / 2**20
        for s in (1, 2)
    }
    check("hand-over sizes are 1.8 MiB and 7.2 MiB as quoted",
          abs(handover_mib[2] - 1.79) < 0.02
          and abs(handover_mib[1] - 7.18) < 0.02,
          f"{handover_mib[2]:.2f} MiB and {handover_mib[1]:.2f} MiB")


# ── 6. Figure 4.11's shell arithmetic ────────────────────────────────────────
def check_shell_figure():
    """Re-derive every number drawn in the AllGather shell figure.

    The figure hard-codes bar coordinates, so it is the one place in the chapter that
    could silently drift from the data. This pins the three bounds, which shell binds at
    each size, and the model/measurement agreement."""
    print("\n6. Figure 4.11 (AllGather shell maximum): its drawn numbers")
    w = M.MSS + M.H
    tinc = {s: M.fill(s, w) - w / M.B for s in (1, 2, 3)}
    ok = all(abs(tinc[s] - v) < 0.1 for s, v in ((1, 408.3), (2, 1125.0), (3, 1841.6)))
    check("t_INC(s) as labelled: 408 / 1,125 / 1,842 ns", ok,
          " / ".join(f"{tinc[s]:.1f}" for s in (1, 2, 3)))

    nge = {s: sum(n for d, n in M.SHELLS_3TIER.items() if d >= s) for s in (1, 2, 3)}
    check("n_>=s as labelled: 63 / 60 / 48", [nge[s] for s in (1, 2, 3)] == [63, 60, 48],
          " / ".join(str(nge[s]) for s in (1, 2, 3)))

    # (size, which shell the figure draws as binding, the T it prints)
    drawn = ((262144, 3, 2241), (4194304, 2, 9112), (16777216, 1, 33955))
    rows = {int(r["msg_bytes"]): r for r in rows_of(M.MAIN, FT, "allgather")}
    for S, want_s, want_T in drawn:
        tb = M.wire(S // 64) / M.B
        bound = {s: tinc[s] + nge[s] * tb for s in (1, 2, 3)}
        got_s = max(bound, key=bound.get)
        meas = float(rows[S]["inc_ns"]) if S in rows else float("nan")
        check(f"S={S >> 10} KiB: shell s={want_s} binds at T={want_T:,}",
              got_s == want_s and abs(bound[got_s] - want_T) < 1.0
              and abs(bound[got_s] - meas) <= 1.0,
              f"binds s={got_s}, bound {bound[got_s]:,.0f}, measured {meas:,.0f}; "
              + ", ".join(f"s={s}:{bound[s]:,.0f}" for s in (1, 2, 3)))

    # the 4 MB decomposition the prose quotes: one deeper fill + four extra blocks
    tb = M.wire(4194304 // 64) / M.B
    fill_gap, blk_gap = tinc[3] - tinc[2], 4 * tb
    check("4 MB gap decomposes as 717 ns fill + 532 ns blocks",
          abs(fill_gap - 717) < 1 and abs(blk_gap - 532) < 1
          and abs((fill_gap + blk_gap) - (M.inc_rs(4194304, 64, 3)
                                          - M.inc_ag(4194304, 64, M.SHELLS_3TIER))) < 1,
          f"{fill_gap:.0f} + {blk_gap:.0f} = {fill_gap + blk_gap:.0f} ns")


# ── 5e. the recursion IS the model; the closed form is its unrolling ──────────
def batches_of(r, N, per_leaf=4, per_pod=16):
    """Depth batches as seen BY MEMBER r of a contiguous group of N ranks placed from
    rank 0: batch s = the peers whose lowest switch in common with r sits at tier s.
    Not the same census as M.census, which counts RING STEPS by depth."""
    c = {1: 0, 2: 0, 3: 0}
    for i in range(N):
        if i != r:
            c[1 if i//per_leaf == r//per_leaf
              else (2 if i//per_pod == r//per_pod else 3)] += 1
    return c


def check_recursion():
    """The chapter states the AllGather model as the busy-period recursion

        F(s) = max(F(s-1), t_INC(s)) + |batch s| tau_b,   F(0) = 0,   T = F(d)

    and derives the maximum from it by unrolling. Two claims to pin. (a) The two forms
    agree -- unconditionally, not just for the pod-aligned groups 5c already checks
    against the simulator. (b) The chapter's caveat is real: reading the bind off the
    hand-over as 'the first batch that collides with its successor' needs the batches
    BELOW THE DEEPEST to grow with s (the deepest one never enters a comparison), and
    contiguous placement does not guarantee that. Also confirms no size this chapter
    evaluates lands in a window where the shortcut would go wrong."""
    print("\n5e. Recursion == closed form, and where the hand-over shortcut needs care")
    w = M.MSS + M.H
    tinc = {s: M.fill(s, w) - w/M.B for s in (1, 2, 3)}
    delta = {1: tinc[2] - tinc[1], 2: tinc[3] - tinc[2]}

    def recur(bat, tb):
        t = 0.0
        for s in (1, 2, 3):
            t = max(t, tinc[s]) + bat[s]*tb
        return t

    def argmax(bat, tb):
        c = {s: tinc[s] + sum(n for d, n in bat.items() if d >= s)*tb for s in (1, 2, 3)}
        return max(c, key=c.get), max(c.values())

    def shortcut(bat, tb):
        return next((s for s in (1, 2) if bat[s]*tb >= delta[s]), 3)

    # (a) the unrolling, over every member of every group size across the pod boundary
    worst, n = 0.0, 0
    for N in list(range(12, 25)) + [32, 64]:
        for r in range(N):
            bat = batches_of(r, N)
            for k in range(14, 28):
                tb = M.wire(max((1 << k)//N, 1))/M.B
                worst = max(worst, abs(recur(bat, tb) - argmax(bat, tb)[1]))
                n += 1
    check("recursion == max over batches, under 0.01 ns", worst < 0.01,
          f"worst {worst:.2e} ns over {n:,} (group, member, size) triples")

    # (b) the premise the shortcut needs, and a contiguous group that violates it
    bad = sorted({(N, tuple(batches_of(r, N)[s] for s in (1, 2, 3)))
                  for N in range(12, 25) for r in range(N)
                  if batches_of(r, N)[3] > 0
                  and batches_of(r, N)[1] > batches_of(r, N)[2]})
    check("contiguous placement can put a bigger batch above a smaller one", bool(bad),
          f"{len(bad)} (|G|, batches) pairs, e.g. " +
          ", ".join(f"|G|={N} sees {b}" for N, b in bad[:3]))

    bat20 = batches_of(16, 20)                       # |G|=20 is in tab:rsag-podstep
    lo = hi = None
    tb = 1.0
    while tb < 1600:
        if shortcut(bat20, tb) != argmax(bat20, tb)[0]:
            lo, hi = (tb if lo is None else lo), tb
        tb += 0.1
    check("|G|=20's stranded members break the shortcut, over the range the prose quotes",
          lo is not None and abs(lo*M.B*20/1e6 - 2.4) < 0.2
          and abs(hi*M.B*20/1e6 - 4.8) < 0.2,
          f"batches {tuple(bat20[s] for s in (1,2,3))}, wrong for tau_b in "
          f"[{lo:.0f},{hi:.0f}] ns, i.e. S ~ {lo*M.B*20/1e6:.1f}-{hi*M.B*20/1e6:.1f} MB")

    # (c) nothing the chapter actually evaluates sits in such a window
    hits = []
    for N in (12, 14, 15, 16, 17, 18, 20, 24):
        tb = M.wire(171360//N)/M.B                   # tab:rsag-podstep's single size
        for r in range(N):
            bat = batches_of(r, N)
            if shortcut(bat, tb) != argmax(bat, tb)[0]:
                hits.append((N, r))
    for S in (262144, 4194304, 67108864):            # tab:rsag-validation, |G|=64
        tb = M.wire(S//64)/M.B
        bat = batches_of(0, 64)
        if shortcut(bat, tb) != argmax(bat, tb)[0]:
            hits.append((64, S))
    check("no evaluated (group, size) point lands in one", not hits,
          "podstep at 171,360 B and |G|=64 at 256 KiB / 4 MB / 64 MB all agree"
          if not hits else f"{hits}")


def check_instantiation():
    print("\n13. Hand-typed Ch.4 tables: tab:instantiation constants + tab:ring-rtt")
    check("constants B/t_l/t_sw/H/MSS/frame",
          (M.B, M.T_L, M.T_SW, M.H, M.MSS) == (500.0, 50.0, 300.0, 64, 4096)
          and M.MSS + M.H == 4160,
          f"B={M.B} t_l={M.T_L} t_sw={M.T_SW} H={M.H} MSS={M.MSS}")
    lam_rounded = {d: round(M.lam(d)) for d in (1, 2, 3)}
    check("ring-rtt lambda 808/2225/3642 ns",
          lam_rounded == {1: 808, 2: 2225, 3: 3642}, f"{lam_rounded}")
    check("ring-rtt census 48/12/3 at N=64",
          M.census(64) == {1: 48, 2: 12, 3: 3}, f"{M.census(64)}")


CH5_RUN = os.path.join(ROOT, "simulation-scripts", "results", "ch5_accumulation",
                       "runs", "ch5-ga32-headline-iters1-mem16-4000-400-20260731T1405Z")


def check_ch5_headline():
    print("\n14. Ch.5 headline: quoted speedups + simulator-model TP factors")
    csv_path = os.path.join(CH5_RUN, "tables", "results.csv")
    if not os.path.exists(csv_path):
        print("  [SKIP] canonical ch5 run CSV not present")
        return
    rows = [r for r in load(csv_path)
            if r["workload"] == "corrected_mb1_ga32"
            and r["su_gbps"] == "4000" and r["so_gbps"] == "400"]
    speedups = {}
    for tp in (4, 8, 16):
        cell = {r["arm"]: r for r in rows if int(r["tp"]) == tp}
        speedups[tp] = round(float(cell["baseline"]["time_per_iter_s"])
                             / float(cell["inc"]["time_per_iter_s"]), 3)
    check("quoted end-to-end speedups 1.101/1.232/1.405",
          speedups == {4: 1.101, 8: 1.232, 16: 1.405}, f"{speedups}")
    S = 32 * 1024 * 1024
    factors = {n: round(2 * M.ring(S, n, 1) / M.inc_root(S, 1), 3) for n in (4, 8, 16)}
    check("simulator-model TP factors 1.562/1.905/2.218",
          factors == {4: 1.562, 8: 1.905, 16: 2.218}, f"{factors}")


def check_pfc_instantiation():
    print("\n15. tab:instantiation PFC row: thresholds + 1xBDP reservation (pfc_census.csv)")
    p = os.path.join(ROOT, "simulation-scripts", "results",
                     "scaleup_pfc_concurrent", "pfc_census.csv")
    if not os.path.exists(p):
        print("  [SKIP] pfc_census.csv not present")
        return
    combos = {}
    for r in load(p):
        combos.setdefault(r["su_topo"], set()).add(
            (int(r["pfc_high"]), int(r["pfc_low"]), int(r["intranode_q"])))
    ss = combos.get("scaleup_single_switch_64_4000Gbps.topo", set())
    ft = combos.get("scaleup_3tier_256_4000Gbps.topo", set())
    check("pause/resume pairs 218/174 (crossbar) and 389/311 (3-tier)",
          {(h, l) for h, l, _ in ss} == {(218, 174)}
          and {(h, l) for h, l, _ in ft} == {(389, 311)},
          f"crossbar {sorted(ss)}, 3-tier {sorted(ft)}")
    check("switch buffer = radix x BDP with BDP 268/439 packets",
          {q // 64 for _, _, q in ss} == {268} and {q // 16 for _, _, q in ft} == {439},
          f"crossbar q/64={sorted({q // 64 for _, _, q in ss})}, "
          f"3-tier q/16={sorted({q // 16 for _, _, q in ft})}")


if __name__ == "__main__":
    print(f"Verifying the collective models against the committed CSVs under\n  {ROOT}")
    for fn in (check_single_switch, check_ring, check_podstep, check_naive_arm,
               check_fold_symmetry, check_three_tier, check_allreduce,
               check_shell_symmetry, check_busy_period, check_handover,
               check_recursion, check_shell_figure,
               check_instantiation, check_ch5_headline, check_pfc_instantiation):
        fn()
    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: " + "; ".join(FAILS))
        sys.exit(1)
    print("all checks passed")
