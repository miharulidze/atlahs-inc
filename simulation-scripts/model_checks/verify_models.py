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
    out = load(path)
    if topo:
        out = [r for r in out if topo in r["su_topo"]]
    if coll:
        out = [r for r in out if r["collective"] == coll]
    return [r for r in out if r["baseline_algo"] == algo]


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
    check("slowest-step form within 1.05%", worst_max < 1.05,
          f"{n} points, worst {worst_max:.2f}%")
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
    print("\n3a. -no_rs_local_fold arm: Equation (rsag-inc-rs) with kappa = N")
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
    print("\n3b. Own-slice fold (default datapath): which link binds the fan-in")
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
            # no link above the member's own, so the fold takes both to (N-1) blocks
            check("crossbar: Reduce-Scatter == AllGather at every size", all(eq),
                  f"{sum(eq)}/{len(eq)} sizes agree to 1 ns")
        else:
            # a shared uplink sits above every member and still carries all N slices
            worst = max((rs - ag) / ag for _, rs, ag in pairs)
            check("three-tier: Reduce-Scatter strictly slower than AllGather",
                  all(rs >= ag for _, rs, ag in pairs),
                  f"{len(pairs)} sizes, up to {100*worst:+.2f}% -- the uplink "
                  "the fold cannot unload")


# ── 4. three-tier: RS uplink-bound, AG ingress-bound and shell-aware ──────────
def check_three_tier():
    print("\n4. Three-tier fabric: Reduce-Scatter uplink-bound, AllGather shell-aware")
    exc = []
    for r in rows_of(M.MAIN, FT, "reduce_scatter"):
        S = int(r["msg_bytes"])
        floor = M.inc_rs(S, 64, 3)                       # N blocks over the uplink
        exc.append((S, (float(r["inc_ns"]) - floor) / (M.wire(S // 64) / M.B)))
    check("uplink floor is never violated above the latency knee",
          all(e > 0 for S, e in exc if S >= 65536),
          "excess over the floor, in block times: "
          + ", ".join(f"{S>>10}K {e:+.2f}" for S, e in exc if S >= 65536))
    band = [e for S, e in exc if S >= 4194304]
    check("bandwidth-bound excess is a constant ~5/6 of a block time",
          all(0.75 < e < 0.95 for e in band),
          f"{len(band)} points, {min(band):.3f}--{max(band):.3f} "
          "-- the unmodelled fold artefact")

    small, large = [], []
    for r in rows_of(M.MAIN, FT, "allgather"):
        S = int(r["msg_bytes"])
        e = M.inc_ag(S, 64, M.SHELLS_3TIER) - float(r["inc_ns"])
        (large if S >= 67108864 else small).append((S, e, 100 * e / float(r["inc_ns"])))
    check("AllGather mixed-depth under 1 ns below 64 MB",
          all(abs(e) < 1.0 for _, e, _ in small),
          f"{len(small)} points, worst {max(abs(e) for _, e, _ in small):.2f} ns")
    check("the two largest are conservative (measured SLOWER than ideal)",
          all(p < 0 for _, _, p in large),
          "; ".join(f"{s>>20} MB {p:+.2f}%" for s, _, p in large)
          + "  -- unmodelled multicast admission threshold")


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


if __name__ == "__main__":
    print(f"Verifying the collective models against the committed CSVs under\n  {ROOT}")
    for fn in (check_single_switch, check_ring, check_podstep, check_naive_arm,
               check_fold_symmetry, check_three_tier, check_allreduce):
        fn()
    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: " + "; ".join(FAILS))
        sys.exit(1)
    print("all checks passed")
