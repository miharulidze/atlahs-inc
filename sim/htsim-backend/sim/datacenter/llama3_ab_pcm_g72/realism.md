# Realism ladder R2/R3 on the G72 suite (2026-07-15, sims in flight)

> **STATUS 2026-07-18: UNVERIFIED — do not quote without re-validation.** Recorded
> for provenance. The shared-intranode-topology aliasing (all 4 domains share one
> fabric object) is unfixed and may bias the INC arm and the aggregation-pressure
> signal in unquantified direction; see ../llama3_ab_pcm results.md OPEN item 1.

Staged answer to "the anchor trace is 99% communication — can we create
realistic traces?": four stages on ONE config (tp/dp/pp = 72/2/2, 288 ranks,
4 domains — the C3/G4 analog), each isolating one variable. Driver:
`scripts/run_realism_suite.py` (extends `run_g72_suite.py`); floors via
`scripts/perturb_g72.py` (the 16-rank perturbation tool generalised to
gpn = 72 and the ring baseline).

| stage | literals | compute model | scale-out | status |
|---|---|---|---|---|
| R0 = G4 | 2 L, seq 144 (toy) | placebo | tree288 100 G (engine-default NIC) | done (g72 suite) |
| R1 = H4 | 2 L, seq 144 | H100 roofline | same | done (g72 suite) |
| R2A (smoke) | 4 L, seq 1152 | H100 roofline | same | **done** |
| R2 | 8 L, seq 2304 | H100 roofline | same | running |
| R3 | = R2's exact bins | H100 roofline | tree288_400Gbps + `-linkspeed 400000` | queued |

Model geometry is the suite's 72-divisible llama3-class shape (hidden 4608 /
intermediate 16128 / 72 heads / kv 72); R2's literals move the *proportions*
toward 8B-class training (2.46 B params at 8 layers; TP AllReduce payload =
tokens x hidden x 2 B = **21.2 MB** — real Megatron scale — vs the toy 1.33 MB),
batch 1 x seq 2304 (% 72 == 0), 2 iterations. R4 (real per-GPU captures with
measured compute; no compute model at all) remains externally blocked on the
TP-shaped capture (Shuhao) and is documented as the endgame, not built.

Scale-out NIC note: R0–R2 keep the g72 suite's engine-default scale-out NIC
(200 G COPY_ENG > 100 G pipes — pipes bind, harmless, comparable). At 400 G the
default would CAP the fabric, so R3 passes `-linkspeed 400000`; at that rate the
NIC's Mbps arithmetic equals the pipes' 20 ps/B exactly (83.0 ns/frame), so
nominal == realised with zero residual. Validated by loading the topo + flag on
G1's single-domain baseline: makespan reproduces byte-identically (6,039,542 ns).

## R2A (smoke) results — 2026-07-15

* baseline 647,998,173 ns / INC 568,765,880 ns -> **+12.23 %** single-schedule
  gain (toy G4: +6.24 %; the gain GROWS as payloads become realistic).
* 64/64 collectives complete; **zero packet drops in both arms**.
* INC per-op durations: min 22,980 ns == the clean model
  (10,616,832/492.3 + 1,409 + 100 = 23.1 µs) to within rounding; p50 32 µs;
  tail (up to 62.7 ms) = member-arrival skew waits, the C3 pattern.
* **First genuine aggregation-state pressure signal**: 263,520
  `LOSSLESS not working` held-packet warnings on the last-hop scale-up queues
  (LS0->DST*) in the INC arm — zero drops, packets held, PFC doing its job.
  Unlike the 16-rank suite's warnings (retracted as the NIC-drain artifact),
  these occur WITH the NIC pinned at the fabric rate and are driven by
  tens-of-MB collective payloads holding fold state. CAVEAT: the engine's
  shared-intranode-topology aliasing (all 4 domains share one fabric object;
  see ../llama3_ab_pcm 'OPEN item 1') means cross-domain contention may
  overstate this pressure — and may bias the INC arm's completion, direction
  unquantified until the per-domain-topology fix lands (Zhiyi's repo,
  propose-then-approve).

## Results (complete, 2026-07-16; realism_results.csv at the suite root)

Floors: 3 schedules per arm (unperturbed + p200 + p400), 9 A/B pairings per
stage. All runs zero real drops (INC-arm `LOSSLESS not working` lines are
held-packet PFC headroom warnings, not drops).

| stage | trace | comm share (base) | TP byte share | gain worst/mean/best | base spread | INC spread | verdict |
|---|---|---|---|---|---|---|---|
| R0 = G4 | 2L/seq144, placebo | 99.997 % | 0.85 % | +6.2 / +10.6 / +14.9 % | 8.1 % | 1.9 % | at floor's edge |
| R1 = H4 | 2L/seq144, h100 | 99.3 % | 0.85 % | −3.3 / +11.8 / +19.1 % | 23.2 % | 3.6 % | UNRESOLVED (sign flips) |
| R2A | 4L/seq1152, h100 | 99.3 % | 6.4 % | **+12.2** / +16.5 / +21.4 % | 10.6 % | 0.9 % | resolved, all 9 positive |
| R2 | 8L/seq2304, h100, 2.5 B params | 99.2 % | 12.0 % | **+12.8** / +17.2 / +20.9 % | 9.0 % | 1.1 % | resolved, all 9 positive |
| R3 | = R2 bins @ 400 G scale-out | 97.1 % | 12.0 % | **+19.5** / +22.9 / +27.1 % | 9.0 % | 1.2 % | resolved, all 9 positive |

Readings: the gain rises monotonically with realism (payloads, then scale-out
bandwidth); the in-network arm is ~10x more schedule-stable than the ring
baseline at every rung; R1's 23 % baseline spread is the definitive
demonstration that toy-trace single-schedule deltas (H4's −0.8 %, the 16-rank
C3) are draws, not measurements.

## Skeleton decomposition (R2A): the gain, derived causally

Re-run each arm with its TP operations neutralised into dependency-preserving
no-ops (`coll` -> `calc 0` in the INC arm; the 288k intranode ring send/recvs
-> `calc 0` in the baseline). TP-attributable cost = full − skeleton:

| quantity | baseline (ring) | in-network |
|---|---|---|
| full makespan | 647.998 ms | 568.766 ms |
| TP-neutralised skeleton | 482.691 ms | 473.756 ms |
| **TP-attributable cost** | **165.31 ms (25.5 %)** | **95.01 ms (16.7 %)** |

* Gain identity: Δ = (165.31 − 95.01) + (482.69 − 473.76) = 70.30 + 8.94 =
  79.23 ms — **89 % of the measured gain is directly TP-attributable**; the
  8.9 ms skeleton difference (1.9 %) is DAG microstructure, inside the floor.
* Byte share understates TP's critical-path weight **4.0x** (6.4 % of bytes,
  25.5 % of makespan) — the tier-bandwidth-ratio effect, now measured.
* The naive serial saving (16 ops x (413 − 23.1) µs = 6.2 ms) is amplified
  **11.3x** by convoy re-timing — the sync-amplification mechanism of
  schedule_sensitivity.md, reproduced at 288 ranks.
* The in-network arm's residual 95 ms is group-max straggler waiting: its
  actual TP data movement is ~0.4 ms (per-op minima byte-exact against the
  cost model: 22,980 ns measured vs 23.1 µs predicted at 10.6 MB; 44,543 ns
  vs 44.6 µs at R3's 21.2 MB). INC removes 43 % of the baseline's TP
  critical-path cost; the rest is workload physics no collective
  implementation can remove (the result cannot exist before its slowest
  member arrives).

Skeleton artifacts: traces/R2A/{base,inc}_notp.{bin,out} (zero drops).
