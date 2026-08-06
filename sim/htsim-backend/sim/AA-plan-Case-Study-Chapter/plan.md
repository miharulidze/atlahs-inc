# AA-plan: Case-Study Chapter — INC under a full training workload

Status: PROPOSED 2026-07-29 (awaiting user approval). Companion to the
"Collectives in Isolation" chapter; consumes the intranode/internode sweep
result sets (`simulation-scripts/results/intranode_linkspeed_sweep/`, engine
05cdb40/4975fb9, 0 drops everywhere, PP=1, inter-node base 200 Gbps).

## 1. The framing problem

Isolation shows INC winning 2-100x per collective; end-to-end training shows
6-16%. A chapter that leads with the isolation numbers and then "shrinks" to
6% reads as a letdown; one that leads with the honest Amdahl anatomy and shows
INC winning EVERYWHERE THE WORKLOAD LETS IT reads as rigorous and still lands
every INC-favorable fact. The story is therefore built on three measured,
integrity-safe pillars, each with a "grows in INC's favor" trend, and closes
on the inference bridge (the regime where the Amdahl limiter disappears).

## 2. The three pillars (all measured)

P1 — SCALING: end-to-end speedup grows with scale-up domain width.
   At the H100-class operating point (intranode 4000, scale-out 400):
   TP4 1.065 -> TP8 1.083 -> TP16 1.098; at next-gen scale-out (800):
   1.064 -> 1.083 -> 1.121. Monotone in TP width at every operating point.
   Mechanism (ties back to isolation): ring cost 2(N-1)/N grows with N, INC
   is constant in N; and wider TP shards the compute thinner (Amdahl ceiling
   recedes: per-rank compute 7.91 -> 4.64 -> 3.00 ms/iter).

P2 — BANDWIDTH EQUIVALENCE: INC delivers the baseline's asymptotic
   performance with 5-10x less scale-up bandwidth. Measured at all three
   scales (ib200): INC@800 Gbps BEATS baseline@8000 Gbps (TP4 0.05815 vs
   0.05984; TP8 0.05638 vs 0.05776; TP16 0.05604 vs 0.05668). Framing:
   "INC buys the next NVLink generation in the switch" — the strongest
   single-sentence INC-favorable claim the data supports, and it is exact.

P3 — TREND ALIGNMENT: every direction the hardware is moving amplifies INC.
   (a) Scale-up domains widen (NVL8 -> NVL72 -> NVL576): P1.
   (b) Scale-out NICs speed up (400 -> 800 -> 1600G): internode sweep shows
       speedup RISING with scale-out rate (TP16: 1.043 @100G -> 1.156 @1600G,
       monotone, unsaturated), because a faster DP floor raises TP's share of
       the critical path. TP4 saturating at 1.065 while TP16 keeps climbing is
       itself evidence: the ceiling is compute share (59% vs 29% at 1600G),
       quantified on every plot by the compute annotation.

## 3. Chapter structure (sections)

  §1 Setup & methodology (short, disarming): synthetic Megatron-style
     Llama iteration (Zhiyi-matched per-layer dims, 2-layer stack, linear
     depth-scaling argument); two arms differ ONLY in TP collectives;
     roofline compute model with the share annotated per figure; exact-rate
     grid + derived PFC + engine-hash provenance; PP=1 scope.
  §2 Anatomy of an iteration: where the time goes (compute share, TP vs
     DP/PP), setting the Amdahl expectation BEFORE any result. New Fig A.
  §3 Result: scaling with domain width (P1). New Fig B (bar chart), plus the
     kept per-scale time+speedup sweeps as supporting panels.
  §4 Result: bandwidth equivalence (P2). New Fig C.
  §5 Result: the scale-out trend (P3). Kept internode time+speedup figures;
     optionally replot x = intranode:internode bandwidth RATIO (40:1 .. 2.5:1)
     — the generation-invariant axis.
  §6 Discussion: what bounds the gain (compute share; DP outside INC's reach),
     the integrity register (below), and the inference bridge (one paragraph,
     forward-referencing Conclusion/future work).

## 4. Figures

Existing (kept per review): per-scale intranode time + speedup (ib200, grid
from 200), per-scale internode time + speedup (su4000).
New, derived from existing CSVs (no new sims):
  A. Stacked "anatomy" bars per scale: compute (measured, fixed) vs
     communication+overlap (makespan - compute), baseline vs INC, at the
     H100-class point. Shows WHAT INC compresses and what it cannot touch.
  B. Scaling bars: x = TP width {4,8,16}, y = end-to-end speedup, grouped by
     operating point {(4000,400), (4000,800)}. THE headline figure.
  C. Bandwidth equivalence: per scale, horizontal arrows on the time-vs-
     intranode-speed curves: "INC@800 = baseline@8000"; or a table.
  D. (optional) Speedup vs bandwidth ratio (internode sweeps replotted).

## 5. Is 800 Gbps a matching choice for intranode 4000? (reviewed)

No — it is a half-generation mismatch. Real pairings (per-GPU, unidirectional):
  H100 era:  NVLink4 ~3600 Gbps + ConnectX-7 400 Gbps  -> ratio ~9:1
  GB200 era: NVLink5 ~7200 Gbps + ConnectX-8 800 Gbps  -> ratio ~9:1
Our exact-rate values 4000 and 8000 are the natural stand-ins, so the
generation-consistent measured cells are (4000, 400) [H100-class] and
(8000, 800) [GB200-class]. The 800G marker on a 4000-intranode plot mixes
generations. Actions:
  - Replace the single "current per-GPU NIC" vline with TWO labeled operating
    points (H100-class, GB200-class) where applicable.
  - MISSING CELL: (8000, 800) — one extra internode sweep at
    --intranode_gbps 8000 per scale (~same cost as today's reruns) fills the
    GB200-class row and extends P3. PROPOSED RUN.
  - The ratio view (Fig D) makes generation-consistency explicit: both real
    generations sit at ~9-10:1, where measured speedups are 6.5-12.1%,
    growing with TP width.

## 6. The inference bridge (Conclusion / future work)

Training is INC's HARDEST case, and it still wins 6-12% at realistic operating
points. Grounded reasons inference is the favorable regime (no inference
measurements claimed — motivated extrapolation, clearly labeled):
  - Message sizes: decode-phase TP allreduces are latency-regime (KB-MB);
    the ISOLATION chapter measures INC's largest wins exactly there
    (order-of-magnitude at small sizes) vs the 2x bandwidth bound at large.
  - No DP gradient sync: the scale-out floor that dominates the training
    critical path (61-97% comm share is mostly DP) does not exist in serving.
  - Compute per token is small and falling (speculative decoding, MoE
    routing): communication share -> dominant.
  - TP is THE serving parallelism (rack-scale NVL72 is marketed for
    inference); domain widths of 8-72 sit exactly on P1's rising curve.
Future-work section: generate decode-phase traces (simple_sim extension or
vLLM capture) and repeat the A/B — ties to the existing "Traces for the
Inference case" notebox in the Conclusion.

## 7. Integrity register (stated in-chapter)

  - Trace-free synthetic workload; roofline compute (share annotated; results
    quoted as ratios, robust to the compute constant).
  - Single-schedule points (no seed ensembles); PP=1 scope (PP=2 retired:
    schedule-noise + one open engine wedge, plan.md SS16 of the sweep doc).
  - 2-layer stack with linear-in-depth argument; charge-neither reduction
    compute; known engine caveats (aliasing fixed 05cdb40; tie-order wedge
    excluded cell documented).
  - INC arm pays real headers + NIC serialization (fair-endpoint datapath).

## 8. Proposed additional runs (cheap, close the gaps)

  R1: internode sweep at --intranode_gbps 8000, all three scales -> the
      GB200-class (8000,800) cells + P3 at the next generation. [~1-2 h]
  R2: (optional) seed/schedule ensemble at the headline cells only, to put a
      +-band on the bar chart. Requires a -seed sweep knob. [~1 h]

## 9. DECISIONS (walkthrough with user, 2026-07-29 — plan APPROVED)

1. Framing: anatomy-first + one-sentence teaser in the intro (bandwidth
   equivalence, asymptote phrasing).
2. Pillars: P1 -> P2 -> P3 as \S3-\S5; P2 stated with the ASYMPTOTE phrasing.
3. Structure: six sections; integrity register in \S6.
   Title: "Collectives in Context: A Tensor-Parallel Training Case Study".
4. Figures: build Fig A (anatomy bars) + Fig B (scaling bars); bandwidth
   equivalence as annotation on the existing intranode time curves (no
   separate Fig C); NO ratio figure (Fig D dropped).
5. Operating points: (4000, 400) = THE realistic H100-class pair (user
   ruling); intranode sweeps re-based to ib400, grid starts at 400;
   internode-plot markers become H100-class (400) + GB200-class (800).
   R1 RUN: internode sweeps at intranode 8000 -> GB200-class cells.
   R1 FINDING: at constant ~10:1 ratio the next generation's speedup is
   slightly LOWER (TP4 1.053 vs 1.065; TP8 1.066 vs 1.083) -- the fixed
   compute floor takes a larger share as all comm terms halve. Reported
   straight; sharpens the inference bridge (the eroding term is the one
   that vanishes at inference). R2 (seed ensemble) SKIPPED: single-path
   fabrics at both tiers make seed variance meaningless; single-schedule
   disclosure instead.
6. Inference bridge: one paragraph in \S6 + fuller version in Conclusion;
   WITH the sanctioned quantitative hook (decode sizes -> the |G|=72
   isolation series 64.8x @4.5KiB -> 10.1x @4.5MiB).
7. Integrity register: the six points as scoped.
8. Drafting: LaTeX directly into thesis-skeleton ("Collectives in
   Context.tex", \input after Collectives in Isolation, compile-verified,
   rebase-then-push Overleaf pattern).

## 10. Skeleton decomposition SHIPPED (2026-07-29 night; answers the ">2x?" question)

User's challenge on Fig A: exposed-communication compression (3.7-5.0x) exceeds
the ~2x per-op bandwidth bound -- how? Measured answer via SKELETON_CONTEXTS
(generator 32034b3; runner _run_skeleton_decomposition.py; both arms' skeleton
traces byte-identical = proof the A/B differs only in TP ops):

  scale  skel(ms)  TP-attr base = ring-serial + LOST-OVERLAP   TP-attr INC
  TP4     251.8       99.9      =   25.8      +   74.2            14.1
  TP8     147.9      117.4      =   30.1      +   87.3            13.1
  TP16     95.6      139.6      =   32.2      +  107.4            13.5

Reading: per-op ratio stays theory-bounded (1.5-1.9x = 2(N-1)/N); 74-77% of the
baseline's TP-attributable exposure is DESTROYED compute-communication overlap
(ring steps are ACK-gated serialization barriers between layer computes; DP
queues behind them), growing with width. INC's TP exposure is ~13.5 ms, CONSTANT
in domain width and below its own serial estimate (partial overlap). Fig A now
shows the causal three-way split (compute / non-TP skeleton / TP-attributable).
Chapter placement: \S2 methodology (skeleton method) + \S3 (Fig A discussion);
kills the obvious reviewer objection with a measurement.

## 11. Full plan re-audit against the b32 results (2026-07-29 night; user-prompted)

User approved the P3 reframe and asked whether P3 is the ONLY reframe needed.
Re-audit verdict: NO — seven changes, most flowing from one root cause: at
realistic batch the two-tier story changes from "DP floor + small TP slice"
to "overlap structure".

1. P1 MECHANISM REFRAMED (claim unchanged, cause corrected). The width scaling
   (1.32→1.65→2.16) is NOT primarily the 2(N-1)/N ring factor (skeleton: ring
   serial grows only 25.8→32.2 ms) — it is the overlap destruction growing
   with width (74→107 ms; more ring steps = more serialization barriers).
   §3 text must credit the right mechanism; the skeleton table is the evidence.
2. §2 ANATOMY REWRITTEN. The b1-era narrative ("makespan ~90% DP/PP comm")
   is DEAD at b32: baseline is compute-dominant at TP4 (67%) and
   communication-dominant at TP16 (41% compute); the INC arm is
   compute-dominant at EVERY width (constant ~31 ms exposure). §2 now sets up
   the three-way split (compute / non-TP / TP-attributable) via the skeleton
   method, with the "impossible >2x" decomposition as its centerpiece
   (user-confirmed: goes in the thesis).
3. P2 PHRASING UPGRADED: "reaches the asymptote with 10x less bandwidth" →
   "EXCEEDS the baseline's asymptotic performance" (measured at all scales;
   TP16 by 24%). Teaser sentence updated accordingly.
4. NEW §2 SUBSECTION: batch sensitivity. The b1→b8→b16→b32 saturation curve +
   the traffic-composition table (TP share 14→57→84%) + the GA-proxy framing
   ("b sequences per DP rank per optimizer step"; micro-batch-vs-GA
   equivalence argued at the network level). All previous b1 headline numbers
   demote to this panel as the low end of the sensitivity axis.
5. P3 REFRAMED (approved): INC pays most where communication pressure is
   highest (3.9x at slow scale-out) and keeps 1.3-2.2x at generation-
   consistent points; intranode 4000-vs-8000 nearly immaterial (solid≈dashed).
   The b1 "faster scale-out grows the gain" trend = DP-dominance artifact.
6. INFERENCE BRIDGE TONE SHIFT: no longer compensatory ("training gains are
   modest, but...") — training already wins 1.3-2.2x; inference becomes the
   amplifying continuation (decode = latency regime, isolation's 10-65x zone;
   no DP at all). Conclusion chapter hook unchanged in substance.
7. INTEGRITY REGISTER +3: (a) micro-batch-as-GA-proxy assumption (activation-
   memory caveat vs network-level equivalence); (b) the scale-out queue/RTO
   artifact + fix + Rtx tripwire (disclosed in methodology — also an honest
   demonstration of the harness's guardrails); (c) the excluded TP16/su8000/
   so1600 INC cell (2h timeout, wedge-family suspect, diagnostic pending).

Fig D x-tick rendering (powers-of-two labels) = cosmetic fix in the polish pass.

---

## §12 POST-GATE-FIX REWRITE PROPOSAL (2026-07-30, awaiting approval)

All chapter numbers predate the per-tier LogGOPS gate fix (engine a8864b3) and are
invalid. Rerun #3 (gate-fixed, ring default, zero1-fixed payloads, true-packet -q)
is COMPLETE: 6 sweeps × 3 scales + probes + skeleton decomposition, all 0-drops.
Wedge-family exclusions: TP16×su8000 INC cells at so200/so400/so1600 + TP16
intranode@8000 (retry running; engine-defect note stands).

### 12.1 Corrected numbers (all verified, independent workflow check running)

Headline (b=32): H100-class (4000/400): TP4 1.036 / TP8 1.093 / TP16 1.155.
GB200-class (8000/800): 1.019 / 1.052 / 1.096.
Intranode sweep (so=400): TP4 1.215@400→1.012@8000; TP8 1.434→1.052;
TP16 1.613@400→1.155@4000 (8000 excluded/pending).
Internode sweeps: FLAT in so rate (su4000: ~1.03/1.09/1.15–1.17; su8000 lower).
Skeleton (fresh, all-post-fix): skel 238.6/131.7/78.3 ms; TP-attr base
23.2/29.3/32.5 vs ring-serial analytic 25.8/30.1/32.2; TP-attr inc 14.2/15.6/17.6
vs serial 17.2; overlap-loss −2.5/−0.8/+0.3 ≈ 0. Skeleton exposure 4.3/2.1/1.1 ms
⇒ ≥98% of DP hidden. Closed-form (skel+ring)/(skel+inc) predicts measured ≤0.6%.
Batch probes (TP4@4000): 1.022/1.026/1.021/1.036 at b=1/8/16/32 — flat (model-
consistent: C, ring, inc all ∝ b). Byte shares post-zero1-fix (logical): TP
39.9/84.1/91.4/95.5% at b=1/8/16/32 (TP4); 98.8% TP16 b32.
RD-vs-ring baseline delta <0.5% (TP4 +0.16%, TP16 +0.47%).
Bandwidth equivalence: baseline needs 1.68–1.95× the scale-up rate to match INC
(interpolated across the grid; VERIFIED direction: base@8000 is 1.0–1.5% FASTER
than inc@4000 — never claim inc@X beats base@2X); model predicts the factor
= 2(N−1)/N = 1.5/1.75/1.875 exactly (B-independent), which the measurements bracket.

### 12.2 What dies (never re-quote)

- 1.32×–2.16× end-to-end series (and GB200 1.159/1.342/1.615).
- P2 "INC@800 beats baseline@8000" — FAILS on the fixed engine (322.5 vs 249.1 ms).
- "Lost overlap dominates" mechanism + the 74–107 ms rows + "gain exceeds per-op bound".
- "62% of gradient traffic hides" calibration (was gate-inflated skeleton exposure).
- "4.2× at 100 Gb/s scale-out" and the falling-with-so-rate trend (P3): curves now flat.
- Batch table's rising speedup column (14→84% share story): speedup is b-invariant.

### 12.3 Section-by-section plan

- Intro: headline → "1.04–1.16× end-to-end at generation-consistent operating
  points, growing with domain width; equivalently the baseline needs ≈2× the
  scale-up bandwidth to match the in-network arm at TP16 (measured 1.9×, model
  2(N−1)/N)." Amdahl framing up front.
- §5.1: keep; TP-share sentence gets zero1-fixed numbers; scale-out fabric sentence
  becomes the rail-equivalence claim (single non-blocking switch = rail-optimised
  fabric with rails collapsed; exact for PP=1 index-preserving DP traffic — user
  ruling 07-30). Provenance para: add gate-fix disclosure pointer to §5.6.
- §5.2 (mechanism, biggest rewrite): exposure identity + skeleton method KEPT
  verbatim in spirit; new table (measured makespans 261.9/161.0/110.8,
  252.8/147.3/95.9, skel 238.6/131.7/78.3; TP-attr rows; serial analytics).
  New narrative: overlap is essentially complete in BOTH arms; baseline TP cost =
  ring serial transfer (2(N−1)/N law), INC cost ≈ its serial floor; end-to-end
  gain = per-op gain diluted by compute (closed-form, ≤0.6%). "Exceeds per-op
  bound" paragraph DELETED. "62%" → "≥98% of gradient traffic hidden".
- §5.2.1 batch: table becomes {shares 39.9/84.1/91.4/95.5%, speedups flat ~1.02–1.04};
  reading: b-invariance is model-predicted robustness, b=32 is the reported point.
- §5.3 width scaling: numbers → 1.036/1.093/1.155 + GB200 1.019/1.052/1.096;
  mechanism → compute floor ∝ 1/N while serial terms ~constant ⇒ communication
  share and the 2(N−1)/N ratio both grow with width; NVL8→NVL72 sentence stays.
- §5.4 "Bandwidth Equivalence" REPLACED: new claim = equivalent-bandwidth factor
  (measured 1.68–1.95× by interpolation, model 2(N−1)/N; phrase as "approaching
  the 2× of a hardware generation at wide domains", never ">=2x"); Fig C stays as
  the carrier; the dead inc@800-beats-base@8000 comparison removed.
- §5.5 scale-out sweep REWRITTEN as robustness: flat curves = the gain is
  scale-up-native; fold in RD row (<0.5%) → one "Sensitivity" section
  (batch / algorithm / scale-out fabric all flat).
- §5.6 discussion: Amdahl paragraph updated (INC arm 80.5% compute at TP16);
  limits list keeps all items, ADDS gate-fix disclosure (defect, fix, invariance
  proof: INC ns-exact, isolation bit-identical 28/28) and updates the excluded-
  cells item (4 wedge cells, TP16×su8000 corner). Inference outlook unchanged —
  now carries the growth story (user-approved storyline).
- Figures: A/B/C/D regenerated (done); workload fig already current.

### 12.4 Effort & sequencing

LaTeX rewrite ≈ one pass over 330 lines, table+numbers mechanical, two paragraphs
new prose (§5.2 narrative, §5.4 claim). Local commits only, no push (user reviews).
Post-approval: plan.md close-out, memory updates, design-wiki ADR stub.

## 13. AS EXECUTED (2026-07-30, evening)

User directive (supersedes parts of §12): rewrite ch. 5 from scratch, lean and
factual, few meaningful plots; **drop the 400/4000-vs-800/8000 comparison
entirely — single H100-class operating point 4000/400** (coherent with the
H100-roofline compute model); also write ch. 3 §3.3 (Phase-3 changes: simple_sim
generator + scale-up/out simulator), coherent with the ch. 3 system-model
opening; commit locally only.

Landed (thesis-skeleton @ b62e6b5, atlahs @ 4856dd7):
- ch. 5 rewritten, 330→~300 lines, 7 printed pages (was 8+): §5.1 setup
  (rail-collapse reference), §5.2 anatomy (skeleton table w/ serial-analytic
  rows + closed form, headroom bound 1.10/1.22/1.41×), §5.3 sensitivity
  (batch / scale-up sweep / scale-out flat / RD), §5.4 discussion.
- Figures reduced 5→3: workload, anatomy, scale-up sweep (w/ operating-point
  vline). scaling + internode figs deleted from the thesis (kept in the script
  for review). Workload panel (a) rebuilt with pcolormesh vector quads:
  **matplotlib imshow rasters render BLANK in the thesis PDF chain** (even at
  300 dpi in gs) — gotcha recorded in memory.
- Deviations from §12: batch table = payloads (0.27/2.15/4.29/8.59 GB TP vs
  0.41 GB DP) + speedups, not shares (wire-vs-logical ambiguity avoided);
  equivalence stated from raw grid ("overtakes at 2× rate by 1.0–1.5%",
  serial-law bracket 1.50–1.88×), no interpolated factor; gate-fix disclosure
  folded into ch. 3 §3.3.3 (per-tier gates paragraph) instead of a ch. 5 bullet;
  noise floor = measured ±1.2% scale-out-sweep wobble vs 3.6% smallest delta.
- ch. 3 §3.3: 3.3.1 two routes + 4 generator changes (parameterised builder,
  ring baseline, ZeRO-1 physical shard, H100 roofline w/ Zhiyi's
  underestimation caveat cited); 3.3.2 EMIT_INC + eligibility + skeleton
  rendering; 3.3.3 port, per-tier transport policy, per-tier LogGOPS gates.
- Compile verified (texlive docker, 1 known pdfcprot error, 0 undefined).
- OPEN: TP16×su8000 in-network cell (retry8000 in determined_shtern, ~4h/6h) —
  chapter currently EXCLUDES it (fig 5.3 caption + §5.4 bullet). If the retry
  lands: append cell to sweep_ib400_g64.csv, regen fig C, drop both exclusion
  notes, extend the overtake sentence to "all three widths measured directly".
- RESOLVED 23:20: the retry TIMED OUT again at the 6 h cap
  (sweep_ib400_g64_retry8000.csv: baseline 8000 ok 0.094482 s — byte-matches the
  sweep row; inc 8000 timeout). The TP16×su8000 in-network wedge is persistent;
  the chapter's exclusion (fig 5.3 caption + §5.4 bullet) is FINAL. Monitor
  b1fhvwt7a stopped.
