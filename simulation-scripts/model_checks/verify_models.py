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


# ── 4. three-tier: RS depth-uniform, AG mixed-depth ───────────────────────────
def check_three_tier():
    print("\n4. Three-tier fabric: Reduce-Scatter depth-uniform, AllGather shell-aware")
    worst_rs = max(abs(M.inc_rs(int(r["msg_bytes"]), 64, 3) - float(r["inc_ns"]))
                   for r in rows_of(M.MAIN, FT, "reduce_scatter"))
    check("Reduce-Scatter (d=3) under 1 ns", worst_rs < 1.0, f"worst {worst_rs:.2f} ns")

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
              f"(asymptotes 2(N-1)/N = {2*63/64:.3f} and {2*63/64/(2-1/64):.3f})")


if __name__ == "__main__":
    print(f"Verifying the collective models against the committed CSVs under\n  {ROOT}")
    for fn in (check_single_switch, check_ring, check_podstep,
               check_three_tier, check_allreduce):
        fn()
    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: " + "; ".join(FAILS))
        sys.exit(1)
    print("all checks passed")
