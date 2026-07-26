# Model checks for the collective A/B

Independent verification for the analytic models in the thesis's Validation chapter
(Broadcast, Reduce, Reduce-Scatter, AllGather, AllReduce — endpoint and in-network).

## Use this one

```bash
python3 simulation-scripts/model_checks/verify_models.py     # exit 0 = all claims hold
```

`verify_models.py` re-derives **every numerical claim the chapter makes** from the
committed CSVs and prints PASS/FAIL per claim. It *imports* the model functions from
`simulation-scripts/_gen_rsag_tables.py` rather than restating them, so it cannot drift
away from the generator that produces the thesis tables: edit a model and both move
together, and any claim that stops holding turns into a FAIL.

Current output: 20/20 pass — in-network residuals under 1 ns over 42 single-switch
points (all positive, i.e. integer-nanosecond truncation rather than fitting), the ring
baseline within 1.04% over 30 points, the pod-boundary step at 1.74×, and AllReduce
apex 2.16× against composed 1.10× at 256 MB.

Since `-rs_local_fold` became the **default** datapath (2026-07-26), the Reduce-Scatter
model carries a coefficient κ = blocks on the link that binds the fan-in: N−1 where the
member's own egress is the busiest link on its path (the crossbar), N once a shared
uplink sits above it, because that uplink forwards all N slices whatever its members
skip. `-no_rs_local_fold` pins κ = N unconditionally and is the arm the model is exact
against on *both* fabrics (0.84 ns over 18 points) — the fold is then worth −1.000 block
times on the crossbar and +0.75…+0.96 (converging on +5/6) on the three-tier fabric.

Nothing anywhere here is fitted. Every constant (`B`, `t_l`, `t_sw`, `H`, `MSS`) comes
from the `.topo` files and the simulator's packet format.

## The ring baseline's residual floor, decomposed

The endpoint ring model over-predicts by a small, systematic amount that is flat in
message size below a 64 KiB shard: −141 ns on the crossbar and −174 ns on the three-tier
fabric at |G|=64. `floor_probe{,2,3,4}.sh` take it apart. It closes:

```
floor = (N-1) * [ 2d*H/B + q ],     q ~ 2.0 ns
```

**The 2d·H/B term is the ACK serialisation the model drops on purpose.** §4.1 says "the
returning ACK is just 64 B, so its serialisation (≈0.13 ns per hop) is negligible", so
λ(d) omits it. A round trip re-serialises the ACK once at the receiver's NIC and once at
each of the 2d−1 switches, hence 2d hops. This term is the *entire* depth dependence:

| | 2d·H/B | +q | = c | ×63 | predicted | observed |
|---|---|---|---|---|---|---|
| d=1 crossbar | 0.256 | 2.0 | 2.256 | 63 | 142.1 | 141.5 |
| d=3 three-tier | 0.768 | 2.0 | 2.768 | 63 | 174.4 | 174.0 |

**q ≈ 2.0 ns is the goal↔htsim interface's nanosecond grid.**
`atlahs_htsim_api.h:144` reads the clock as `now() / 1000` — integer ps→ns truncation —
and `null_event.cpp:27` converts back with `t * 1000`, so htsim can only be resumed on a
1 ns boundary. A ring round crosses that boundary twice (the sender's send-completion and
the receiver's recv-match, both via `MarkNodeAsDone` at a truncated `cpu_time`). Hand-
deriving the round trip stage by stage at d=1, b=65,536 gives 941.696 ns against a
measured 943.700: an excess of **2.004 ns**, i.e. two grid steps. The mechanism is located
and the magnitude matches at both depths to under 0.01 ns; the sign of each individual
snap has not been single-stepped.

What the probes ruled out, each by moving one parameter and leaving the rest fixed:

| probe | control | result |
|---|---|---|
| 1 | NIC send gate 4000 / 8000 / 16000 Gbps | completion times **bit-identical**; gate fully hidden |
| 2 | t_ℓ and t_sw doubled (λ 808→1608) | floor unchanged, 1.00× — not latency |
| 3 | N swept at **fixed** shard 65,536 B | floor exactly (N−1)×2.24 ns — strictly per-round |
| 4A | MTU 4160→8320 (16→8 packets/round) | c unchanged at 2.248 — not per-packet, not frame-related |
| 4B | gate 4000 vs 16000 Gbps at 16/64 MB | bit-identical — gate exonerated at large shards too |

`logsim-interface.cpp:960` does charge the NIC send gate one frame *more* than the message
needs, `(⌈b/4096⌉+1)·4160/B_nic`, which works out to exactly τ_b + one frame time. Probes 1
and 4B show it never surfaces: it stays inside the ACK-gated round period at every rate and
size we sweep.

### The second term: a window, with an exact law

Above a 64 KiB shard a second term appears on top of the flat floor. `floor_probe6.sh`
sweeps the shard finely at fixed N (so only bytes-per-round move) and it fits exactly:

```
excess = 0.0320 ns per round per frame beyond 48 frames,   0 at or below 48
```

| frames/round | 2 | 4 | 8 | 16 | 32 | 64 | 128 | 256 | 512 | 1024 |
|---|---|---|---|---|---|---|---|---|---|---|
| measured excess | 0 | 0 | 0 | 0 | 0 | 0.513 | 2.572 | 6.660 | 14.851 | 31.233 |
| 0.0320·(fr−48) | 0 | 0 | 0 | 0 | 0 | 0.512 | 2.560 | 6.656 | 14.848 | 31.232 |

Marginal cost constant to 0.5% over five doublings; the fit is 0.06% at 256 frames.

**48 frames is 199,680 B, and `uec.cpp:539` sets `_maxwnd = 50 * _mtu = 208,000 B` — the
sender's window, exactly two frames above the measured knee.** So the transfer goes
window-limited: past ~50 MTUs in flight, each further frame waits on a credit, at
0.0320 ns = 16 B of time each. Eliminated as causes on the way: the NIC send gate
(probe 4B, bit-identical at 4× the gate rate), GOAL chunking (`goal.py` chunks Broadcast
and Reduce only, never the ring), congestion control and buffer size (probe 5, both
bit-identical).

**Not causally proven.** `floor_probe7.sh` tries to move it with `-cwnd 2000000` and the
result is bit-identical, so that knob does not reach the effective window: the
constructor's `50 * _mtu` (uec.cpp:539) wins over the NSCC per-instance
`setMaxWnd(1.5*_bdp)` (uec.cpp:187), which `-intranode_cc none` bypasses anyway. Proving
it needs a rebuild with `_maxwnd` changed. The law is exact and the coincidence with
`_maxwnd` is two frames wide; that is the state of the evidence.

### -intranode_cc none is NOT numerically neutral

`sim.py` now passes it (the chapter claims a CC-free scale-up domain, and
`experiments/intranode_linkspeed_sweep` already did). `ccnone_neutrality.sh` checks it
against the committed CSVs over both fabrics, all collectives, three sizes: **41 of 42
rows bit-identical, one moves.**

```
three-tier, allreduce/rdouble, 64 MB:   base 442,993 -> 461,935 ns   (+4.3%)
```

Recursive doubling runs concurrent pairwise exchanges, so unlike the ring — whose steps
each own a dedicated sender/receiver pair — it genuinely contends on shared uplinks. On a
multi-tier fabric there is real congestion for NSCC to pace, and removing the window
leaves lossless PFC to absorb it, which is slower. Nothing contends on the crossbar, which
is why a probe restricted to reduce_scatter/ring there reported "neutral".

`results/scaleup_coll_ab` therefore needs regenerating. No number the chapter *quotes*
changes — `table_ar` filters to the single switch — but the CC-free arm is the harsher one
for the endpoint baseline, so keeping the flag makes INC-vs-recursive-doubling on the
three-tier fabric slightly more favourable to INC, on the grounds that the modelled fabric
has no window. That is a modelling call, not bookkeeping.

**Confound to avoid.** Doubling the *fabric* rate while leaving `-intranode_linkspeed` at
4000 makes the gate the bottleneck and the 4 MB floor blows out to −4,065 ns. That is why
`scaleup_single_switch_64_4000Gbps.topo` matches the two rates by construction; probe 2's
bw2x arm is only interpretable at a 4 KiB shard.

## The in-network AllGather deviation

Equation (4.8) is exact to under a nanosecond everywhere except the largest three-tier
shards. `ag_probe1.sh` sweeps the onset with the crossbar as a control:

| frames per shard | 64 | 128 | 256 | 512 | 1024 |
|---|---|---|---|---|---|
| three-tier (ns) | −0.6 | −0.8 | **+1,745** | **+5,276** | **+9,101** |
| in τ_b | — | — | +0.82 | +1.24 | +1.07 |
| crossbar (ns) | −0.6 | −0.8 | −0.3 | −0.2 | −0.2 |

Three facts, and no more:

1. **It needs a multi-tier fabric.** The crossbar is exact at every size up to a 4 MiB
   shard (1024 frames), so it is not a large-message or frame-count effect as such.
2. **The onset is between a 512 KiB and a 1 MiB shard** (128 → 256 frames).
3. **The magnitude is about one block time** — 0.82 to 1.24 τ_b, not a growing fraction.

Not fitted to a law: three non-zero points would support several, and this file already
records three attributions that had to be retracted for outrunning their evidence
(a start-up transient, a multicast admission threshold, and DCTCP). What the data carries
is "about one block time, multi-tier only, onset located".

**Lead, not a claim.** Both the magnitude and the multi-tier restriction match the
Reduce-Scatter fold excess (+0.75…+0.96 τ_b, also three-tier only), which is likewise
localised to a switch's egress ordering without being derived. A shared cause is plausible
and unproven; the discriminator would be a two-tier fabric, which would separate "any
shared uplink" from "depth 3 specifically".

## Run recipes

| script | what it does |
|---|---|
| `regen_all.sh` | Regenerates every dataset that touches the INC emitters, in order: `scaleup_coll_ab`, its group sweep, `scaleup_coll_footprint`, `scaleup_ar_bandwidth`. Run after any datapath change. ~2 h. |
| `fold_slack_test.py` | The causal control for `-rs_local_fold`: two fabrics differing only in the shared uplink's rate. Slack on that link flips the fold from +0.50 to −1.00 block times while the fold-off arm is unchanged. |
| `fold_placement_test.py` | Discriminates what governs `-rs_local_fold`'s payoff: tree depth, or which link binds. Four placements on one fabric; the two spread ones falsify the depth reading. |
| `podstep.sh` | The pod-boundary experiment: sweeps \|G\| ∈ {12,14,15,16,17,18,20,24} on the three-tier fabric at 85,680 B. Writes `results/_podstep`. |

`podstep.sh` uses 85,680 B deliberately — it is the LCM of every group size in the
sweep, and the harness **skips any size not divisible by N**. A first attempt at 4096 B
silently produced a single usable row (only \|G\|=16 divides 4096).

## Historical record — read before running these

`rsag_model_check{,2,3,4}.py` are the derivation trail, kept because they document how
the models were arrived at and falsified. **They are not current.** Each encodes the
assumptions of its round, and the later rounds refute the earlier ones. They also
predate the 2026-07-26 AllGather and tail-truncation fixes, so their AllGather numbers
describe the *buggy* datapath and will not match today's CSVs — which is precisely how
the bug surfaced.

| script | established | superseded by |
|---|---|---|
| `rsag_model_check.py` | In-network RS/AG fit on the single switch. **Refuted** the sum-of-steps ring model on the three-tier (−20% to −66%) and exposed the three-tier AllGather anomaly. | ring model → check 2; `MSS` → check 4 |
| `rsag_model_check2.py` | The **slowest-step** ring model, `(N−1)·λ_max`, fits ≤1%. Introduced the physical lower bound that proved the three-tier AllGather times were *impossible* (below an infinitely-fast-switch floor). | `MSS` and the padded-frame bound → check 4 and the datapath fix |
| `rsag_model_check3.py` | Group-size sweep: in-network RS is **flat in N**, AllGather tracks `(N−1)/N`. The \|G\|=2 pair (8,928 vs 4,674 ns) is the discriminator no shared-coefficient model can produce. | `MSS` → check 4 |
| `rsag_model_check4.py` | The decisive one: `MSS` = 4096 vs 4086 head to head. The true payload MSS (`_mtu − _hdr_size` = 4150 − 64) takes the Reduce-Scatter residual from 24 ns to under 1 ns. | current, but folded into `verify_models.py` |

All four take `ATLAHS_ROOT` from the environment, defaulting to the repo root derived
from their own location.

## What the checks caught

Worth recording, because it is the argument for deriving a bound at all rather than
only diffing against a reference run:

1. **The in-network AllGather completed early.** The multicast source padded every
   chunk to a full MSS while the sink credited frame capacity against an expectation of
   `(N−1)b`, so a rank finished after `⌈(N−1)b/MSS⌉` frames from *any* senders. At 4 KB,
   \|G\|=64 that was one frame — one of the sixty-three blocks it was meant to gather.
   Found by the lower bound in check 2, not by a test.
2. **Reduce and AllReduce padded their tail frame**, which only became visible once the
   AllGather fix broke the Broadcast/Reduce duality (416 vs 424 ns).
3. **`-rs_local_fold` looked depth-governed and is not.** Contiguous placement pins four
   members to every leaf, tying leaf occupancy to tree depth; the fold's effect then
   tracks depth perfectly (−1.000 / +0.499 / +0.832 at d = 1/2/3) and invites a wrong
   law. One member per leaf breaks the coupling: a depth-2 group then *saves* a full
   block time and a depth-3 group costs the same +0.5 as a depth-2 contiguous one. What
   governs it is whether a rate-matched shared uplink sits above the lowest tier holding
   two or more members. `fold_placement_test.py` is that experiment.
4. **A "drop" is not always a drop.** 639,474 reported drops in a fold-on run were
   `LOSSLESS not working!` warnings, which the harness's `DROP` regex matches; the queue
   enqueues the packet on the next line and nothing is discarded. Re-running with a
   larger `-intranode_q` gives zero warnings and a bit-identical completion time.
   Check the queue code before discarding a measurement over this.
5. **The published figures came from a superseded dataset.** `_gen_thesis_plots.py` read
   a 2026-07-22 snapshot and drew a *fitted* `t0`; both are gone, replaced by
   `_gen_collective_plots.py`.
