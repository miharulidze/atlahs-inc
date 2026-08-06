# Meeting prep — Misha + Tommaso, Monday 2026-07-13

*Everything from the 2026-07-06 directives is DONE except the Shuhao-gated trace. ~2.5 weeks to Aug 1.*

## SHOW (in this order)

1. **Rank-scaling figure** (`allreduce_ab/speedup_vs_n.png`) — the D3 ask, delivered:
   N = 2…64 on radix-matched single-switch crossbars. INC flat at 1.54 µs for all N; ring O(N);
   recursive-doubling O(log N); analytic ideal ring `2(N−1)/N·8S/B + (N−1)·hop` as overlay.
   **The gap grows with N exactly as predicted**: ideal-ring/INC 2.7× (N=4) → 12.8× (16) → **53.3× (64)**
   at 64 KiB; rdouble/INC → 20.5×. Raw measured-ring ratio (213.8×) shown only as the CC-confound exhibit.
2. **TP-share characterization** (`llama3_ab_pcm/tp_share_sweep/gain_vs_tp_share.png`) — the D2 "what
   must an application look like", answered by measurement (5 llama3 layouts, 16 ranks, real two-tier
   engine, both arms, 0 drops):

   | cfg | tp/dp/pp | TP byte share | gain/iter |
   |---|---|---|---|
   | C1 | 2/8/1 | 0.49 % | 0.10 % |
   | C2 | 4/4/1 | 0.57 % | 0.04 % |
   | C3 | 4/2/2 | 0.85 % | **2.40 %** |
   | C4 | 8/2/1 | 0.85 % | 0.31 % |
   | C5 | 16/1/1 | 100 % | **93.46 %** |

   **Finding: gain is critical-path-structural, not byte-share.** C3 vs C4 = same share, 8× different
   gain (PP=2 bubbles amplify each saved collective ~16×; DP-dominated C2 hides savings at 0.26×).
   Pure-TP C5 = 1−1/S with same-engine S = 15.3 → anchors the Amdahl curve. Deployment reading: INC
   pays where TP collectives sit **exposed on the critical path** (TP-heavy layouts, bubble-propagating
   pipeline schedules, inference decoding) — supports the inference intuition from last meeting.
3. **D1 fairness implemented as sanctioned**: baseline keeps its ACKs; INC arm charged +1 RTT (2.6 µs)
   app-sync modeled on the 3-phase scheme; ½-RTT (1.3 µs) as sensitivity. The win survives: ring/INC
   still 1.3×→79.6× across N. (Note: the RTT quantification is ours — the paper says "constant-time /
   one handshake round" without a figure.)
4. **Emit side closed** (bonus since last meeting): node-containment predicate (not the tp-context
   proxy) on BOTH generator paths incl. the real NVTX/nsys pipeline; all 5 collective kinds; validated
   by byte-identical regressions + a clean negative control on the published 4-host llama capture
   (rank-per-host ⇒ nothing qualifies ⇒ output structurally unchanged, by construction).
5. **Finite-resource first sighting**: layouts with several INC ops in flight per scale-up fabric log
   PFC lossless-headroom warnings (5,788/76; zero drops) — now a threats-to-validity bullet; the
   finite-aggregation-budget modeling stays future work (SHARP/ATP-style).

## ASK

1. **Sync-cost ruling**: quote +1 RTT (Misha) as the headline with ½-RTT one-way (Tommaso) as
   sensitivity — OK? And is the charge conceptually an application-level ACK (as modeled)?
2. **Which number leads the abstract**: (a) ideal-ring/INC growing 2.7×→53× (CC-clean, algorithmic),
   (b) rdouble/INC ~20× (most conservative endpoint), (c) 2.4 % + the structural characterization.
   Current draft leads with (a), reports all three.
3. **Oracle semantics confirmation**: the analytic ideal-ring is the *ring's* floor — measured ring
   never beats it (holds), measured INC rightly does. Confirm that reading of "the theoretical best".
4. **Baseline sufficiency**: ring + recursive-doubling + analytic ideal — does rdouble satisfy the
   "tree" baseline, or is a protocol-faithful NCCL-tree still wanted?
5. **Scope sign-off for Aug 1**: generated (simple_sim) workloads + the characterization + declared
   threats = sufficient? (Real captures can't contain node-contained TP by construction — verified;
   a TP-shaped capture would need per-GPU intra-host recording.)
6. **Shuhao status**: TP-dominant trace and/or inference (prefill/decode) support — messaged 07-06.

## STATUS one-liners
- Thesis: eval chapter has sync-charge + N-sweep + TP-share sections; abstract/conclusion updated;
  needs one Overleaf compile pass. Rolling drafts can start any day.
- Reproducibility: all repos pushed; generator on a private mirror; external engine pinned
  (wanja/inc-port @ 2858452) — upstream/hand-off of the two engine deadlock fixes to Zhiyi pending.
- Remaining risk: none technical; Shuhao trace is upside, not a gate.

## LATE ADDITION (2026-07-10): the SP experiment — run and verified

Shuhao's reply (2026-07-10): tp/dp/pp knobs confirmed; NO inference support in the generator
(KV-cache scope — inference thread dropped, future-work cite only); and the key correction: modern TP
~always runs WITH sequence parallelism, which decomposes the AllReduce into RS+AG. We wired SP into
the llama3 driver the same day and ran the two-tier A/B (`llama3_ab_pcm/sp_ab/`):

| config | rendering | gain/iter |
|---|---|---|
| C5 (pure TP16) | plain AllReduce | 93.46 % |
| SP-C5 (TP16+SP) | RS+AG | **93.68 %** |
| C3 (TP4/DP2/PP2) | plain AllReduce | 2.40 % |
| SP-C3 (TP4+SP/DP2/PP2) | RS+AG | **14.15 %** (not like-for-like vs C3: 2x colls on the PP-amplified path) |

**SHOW:** INC's advantage *survives* the modern SP regime — because RS and AG are first-class INC
primitives here (the five-collective design pays off). **Apex-fusion premium measured**: SP INC costs
1.41x plain INC wall time at C5 (30 coll ops vs 16, two barrier waves per layer) — i.e. what the
monolithic AllReduce's in-switch turnaround is worth. Cross-workload framing caveat stated in the README.

**Engineering finding en route** (defense-worthy): the pcm engine's RS path hung on first exercise —
its UEC payload mss (4086 = MTU minus headers) does not divide power-of-two block sizes, so a
block-straddling chunk undershot late owners' fan-in expectations by 640 B; only RS hangs (all other
kinds overshoot, which `>=` tolerates). Fixed by capping chunks at block boundaries (wanja/inc-port
4d4361b); regression battery exact before==after.

**ASK (updated):** does the SP result change the D2 priority — is SP-C3/SP-C5 now the headline
"modern regime" use case, with plain-TP as the apex-fusion upper bound?
