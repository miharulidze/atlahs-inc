# Realism ladder R2/R3 on the G72 suite (2026-07-15, sims in flight)

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

## Outputs (when the chain lands)

`realism_results.csv` (suite root): per stage — TP byte share, per-rank calc
ns/iteration, baseline/INC makespans, gain, compute share of the baseline
makespan, and the schedule-noise spreads from 2 perturbed schedules per arm
(units 200/400 ns; R0F/R1F = the same floors measured on G4/H4's own traces).
The four-column thesis table (comm share / TP share / A/B delta / floor) is
derived from it.
