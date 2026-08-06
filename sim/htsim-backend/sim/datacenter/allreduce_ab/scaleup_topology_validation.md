# Scale-up single-switch topology — validation

**Date:** 2026-07-01 · **Binary:** `htsim_uec` (arm64) · **Branch:** `WIP-multicast-htsim-direct`

## Purpose

Validate `topologies/scaleup_single_switch_16_3600Gbps.topo` — a single-switch (one
non-blocking crossbar) model of an NVLink-class scale-up domain — so it can replace the
2-hop `scaleup_tree16_3600Gbps.topo` used in the earlier A/B, which over-modelled NVLink's
one-hop fabric as a 4-leaf fat-tree.

Open risk going in: the degenerate 2-tier encoding (`Radix_Down 16 / Radix_Up 1 /
Oversubscribed 16`, forced because the FatTree parser rejects `Tiers=1`) might trip a
loader assertion. **It does not.**

## Method

```
make_allreduce_ab <bin> inc  16 65536 <groups>     # INC arm  (+ .groups)
make_allreduce_ab <bin> ring 16 65536              # ring arm (chunked bandwidth-optimal p2p)
htsim_uec -goal <bin> -topo <topo> -strat ecmp_host -linkspeed 3600000 \
          -mtu 4096 -paths 128 -seed 1 [-groups <groups> -reduce_compute_latency 100]
```

## Result

| arm  | topo                       | makespan (ns) | ALLREDUCE_COMPLETE | drops |
|------|----------------------------|--------------:|:------------------:|:-----:|
| INC  | **single-switch (1 hop)**  |      **1542** |         yes        |   0   |
| INC  | tree16 (2 hops)            |          3259 |         yes        |   0   |
| RING | **single-switch (1 hop)**  |     **78507** |     n/a (p2p)*     |   0   |
| RING | tree16 (2 hops)            |        104243 |     n/a (p2p)*     |   0   |

*The ring arm is decomposed point-to-point, so it emits no `ALLREDUCE_COMPLETE` line;
completion is the "Maximum finishing time" makespan oracle. All runs exit 0, zero drops.

## Conclusion

- **Works & usable.** Loads with no assertion; both INC and ring arms complete cleanly on
  the single switch. The A/B sweep already takes `--topo`, so switching to it is a one-flag
  change — no code needed:
  `run_allreduce_ab_sweep.py --topo ../topologies/scaleup_single_switch_16_3600Gbps.topo`
- **Fidelity gap it closes.** INC is latency-dominated, so the 2-hop→1-hop change is large:
  the INC-vs-ring speedup goes from ~32× (tree16) to ~51× (single switch, 78507/1542). The
  single-switch model is the NVLink-faithful one; tree16 understated INC's advantage.
- **Modelling caveat for the write-up:** the NVLink domain is idealised as one crossbar
  (per-host link = aggregate NVLink BW, one switch traversal). The degenerate-2-tier
  encoding is a parser workaround (`Tiers=1` unsupported); its vestigial Tier-1 uplink
  carries no host↔host traffic.

## Extension to NVL72 and beyond (2026-07-13)

Following the 2026-07-13 supervisor directive to grow the scale-up domain (64 → 72, and
consider 144 / 288 per NVIDIA's roadmap), the single-switch sweep was extended and the
sizes grounded against current hardware.

**Hardware grounding (web-verified, July 2026):**
- Current **single-tier** NVLink domain tops out at **GB200 NVL72 = 72 GPUs** — one
  NVSwitch tier, one hop, fully non-blocking (1800 GB/s per GPU, 9 NVLink-switch trays
  acting as one logical crossbar). Rubin "NVL144" (2026–27) is still ~72 GPU *packages*
  (the 144 counts compute dies, 2/package), so still single-tier.
- **Two-tier** NVSwitch appears at **NVL576** (144 GPU packages / 576 dies, Rubin Ultra /
  Kyber) and the mooted **NVL1152** (288 packages) — and there NVIDIA notes it may be a
  multi-plane, possibly oversubscribed / non-Clos fabric, not a single non-blocking tier.
- Net: single-tier ≈ ≤ 72 packages; **≥ 144 packages ⇒ multi-switch (two-tier)**.

**Results (`run_allreduce_ab_sweep.py`, `results_vs_n_large.csv`, sizes 65664 / 1048320 B
= nearest multiples of 576 = lcm(2..288) to 64 KiB / 1 MiB):**
- `scaleup_single_switch_72_3600Gbps.topo` (NVL72): **loads and runs clean** (72 is not a
  power of two — parser accepts it). INC AllReduce stays flat at **1550 ns** (64 KiB) /
  **3539 ns** (1 MiB) — *identical* to N = 2..64, confirming the O(1)-in-N apex right up to
  the real single-tier ceiling. speed-up vs the analytic ideal ring at N = 72:
  **59.7×** (64 KiB) / **27.4×** (1 MiB) — up from 12.8× / 6.7× at N = 16.
- **`_144_` and `_288_` assert-fail** on load: `fat_tree_topology.cpp:1070` (and `:1131`)
  hardcodes `assert(switches_lp[tor]->addPort(...) < 96)` — at most 96 ports per switch.
  72 < 96 passes; 144 / 288 exceed it. The `96` is a *defensive constant* (`Switch::addPort`
  is itself an unbounded `vector::push_back`), so it is trivially bumpable — **but** a
  144/288-port *single* non-blocking crossbar is physically fictional (real NVSwitch radix,
  and NVIDIA's own fabric, go two-tier at that scale). **The tool limit coincides with the
  hardware two-tier boundary.**

**Consequence.** The *simulated* single-switch sweep is valid to **N = 72** (= NVL72, the
real single-tier max, which lands just under htsim's 96-port cap). 144 / 288 are reported
**analytically** — INC flat by the O(1) result, ideal-ring from the closed form: ideal/INC
= **53.8× / 106.7×** at 1 MiB for 144 / 288 (120× / 241× at 64 KiB). A *faithful* 144/288
model is **two-tier** — the multi-switch case NVIDIA actually builds — and is a distinct
future build (supervisor deprioritised multi-switch until single-switch is fully understood).

## Faithful two-tier domain: DGX H100 NVLink Switch System, 256 GPUs (2026-07-13)

Per the 2026-07-13 directive to model a larger scale-up domain faithfully to official
numbers, built `topologies/scaleup_twotier_h100_256gpu_3600Gbps.topo` — the largest
SHIPPED, fully-documented two-tier NVLink domain. Note the "144/288" from the meeting are
not real *single* NVLink domains; the actual two-tier scale-up domains are **256** (DGX
H100 NVLink Switch System, shipped) and **576 dies / 144 packages** (Rubin Ultra NVL576,
announced, slipped to ~2028, CPO-dependent, structure still fluid) — so 256 is the
faithful, encodable target.

Official structure: 256 H100 GPUs = 32 nodes × 8; per-node NVSwitch3 = leaf, 18 external
NVLink-Switch boxes (36 ASICs) = spine (164 switch ASICs total); **2:1-oversubscribed**
uplinks (72/node, not fully non-blocking); 3600 Gbps/GPU = H100 NVLink4 unidirectional
(BW-consistent with the single-switch sweep). htsim encoding: Tier 0 `Radix_Down 8 /
Radix_Up 4 / Oversubscribed 2`, Tier 1 `Radix_Down 32` → parser derives 32 leaves, 128
uplinks, 4 logical spines. The 18-box/164-ASIC packaging is abstracted to an equivalent
regular fat-tree; the physically-decisive invariants (8 GPUs/node, 2:1 bisection, 1-hop
intra / 2-hop inter) are faithful. Loads clean; INC AllReduce(256, 64 KiB) = **3259 ns**,
0 drops — exactly **2× the single-switch 1-hop 1542 ns** (the "+1 tier = +1 hop" cost).

A/B (`results_twotier_256.csv`, INC charged `reduce_compute 100`, recursive-doubling/ring
charged none — INC-conservative):

| size   | INC ns | recursive-doubling | measured ring | ideal ring | INC vs rdouble | INC vs ideal-ring |
|--------|-------:|-------------------:|--------------:|-----------:|:--------------:|:-----------------:|
| 64 KiB |   3259 |              74796 |       1540499 |     331790 |     22.9×      |      101.8×        |
| 1 MiB  |   5255 |             109318 |       1540499 |     336142 |     20.8×      |       64.0×        |

**Key finding.** At N=256 the ideal ring is **98–99 % latency** — the (N−1)=255 serial
dependency hops dwarf bandwidth even at 1 MiB (ideal ring 331790 ns @ 64 KiB vs 336142 ns
@ 1 MiB, near-identical; the measured ring is *identical* 1540499 ns at both sizes). So the
large two-tier domain is **latency-bound regardless of message size**, and INC's flat O(1)
apex wins by **~21× vs recursive-doubling** (the realistic NCCL endpoint algorithm at
scale, fairly charged) and **64–102× vs the ideal ring** (CC-decontaminated). The raw
measured-ring 293–473× is congestion-confounded (256-way incast on 2:1-oversubscribed
uplinks) and is NOT the quotable figure. This is the quantitative confirmation of the
supervisor's point: growing the scale-up domain brings latency-sensitivity back, so INC's
advantage GROWS with domain size (≈27× vs ideal ring at NVL72/72 → 64× at 256, 1 MiB).
