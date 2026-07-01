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
