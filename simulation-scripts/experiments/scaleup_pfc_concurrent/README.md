# scaleup_pfc_concurrent — PFC / lossless-backpressure validation

**Status: validation bundle refreshed 2026-08-08. Requires the instrumented
PCM build (`-mcast_pin`, PFC counters, `-pfc_trace`).**

## What this validates

Losslessness is the correctness *precondition* for in-network aggregation — a dropped
packet corrupts a reduction irrecoverably. Zero `LOSSLESS not working` lines alone is
weak evidence (a run that never stresses the fabric also prints none), so this suite
provides the three-legged proof:

1. **Engagement** — PFC pause events > 0 under the presented configs (measured by new
   in-binary counters, not inferred).
2. **Invariant under engagement** — while pauses fire: zero warn lines AND peak
   per-queue occupancy within its provisioned bound (1×BDP ingress reservation;
   radix×BDP shared-buffer egress cap).
3. **Tripwire liveness** — deliberately mis-provisioned controls make the warn lines
   reappear, so the zeros elsewhere are measurements, not blind spots.

This validates the simulator's one-class, link-wide PFC-style abstraction for the
tested workloads. It is not standards PFC, per-VC CBFC, or a deadlock proof for arbitrary
mixed traffic.

## Measured outcome (2026-08-08)

- **Census (30 chapter corner cells, both fabrics, 64 + 256 MiB):** peak ingress
  ≤ 0.92 of reservation, peak egress ≤ 0.22 of cap, 0 warn lines; PFC engages *in
  situ* in 7 cells (AllGather in-network 64/240/1714 pauses, composed RS+AG
  likewise, rec.-doubling endpoint 58/853).
- **Stress (pinned vs distributed concurrent AllReduces):** pinned N=16 = 8.85×
  distributed at 1 MiB — 55 % of the full-serialisation bound (1.96× / 12 % at
  64 KiB) — with **zero pause frames**: the contention is absorbed by the
  provisioned shared egress buffer draining at link rate under chunk-paced sources.
  The distributed arm is conflict-free **by construction** (round-robin tiles the 16
  (agg, core) placements once for N ≤ 16); it is a constructed control, not a
  measured isolation result.
- **Overdrive (NIC 2× fabric):** wire schedule unchanged, 4096 pause frames observed
  at the host NIC adapters, 0 warn lines.
- **Controls:** old 1×BDP egress cap → 224,018 warn lines (peak egress 3.14× the
  cap); XOFF=100 gate probe → `nic_redrives_held = 286` (the NIC arbiter actively
  withholds grants); gate off → the pause adapter is not even registered; XOFF=300
  on the crossbar is a **null**: post-fix, no crossbar ingress can be driven over
  any threshold by these collectives (peak 0.003 of reservation).

`drop_log_hits` counts the simulator's warn-and-forward `LOSSLESS not working` log
lines (protocol-invariant violations), **not** lost packets — htsim lossless queues
never drop, which is also why mis-provisioned runs can keep identical makespans.

## Geometry of the stress mode (the crux)

Each group must **span multiple pods** so its tree reaches the *core* tier — pinning
only bites at the core. On the 256-host 3-tier fat-tree (16 hosts/pod × 16 pods):
group *g* = one host per pod at slot *g* across pods 0–7. Disjoint by slot ⇒ N ≤ 16
(hosts/pod), also ≤ core count (16). 8-pod groups keep the pinned core the *sole*
contended resource.

## Parameters

| parameter | value |
|---|---|
| engine | pcm-sdk (`htsim_flow_app_atlahs`, instrumented build) |
| stress topology | `scaleup_3tier_256_4000Gbps.topo`; census also `scaleup_single_switch_64_4000Gbps.topo` |
| group geometry (stress) | 8-host groups, one per pod over pods 0–7 |
| N sweep (stress) | 1, 2, 4, 8, 12, 16 concurrent disjoint groups |
| messages (stress) | 64 KiB and 1 MiB AllReduce (apex) per group |
| census cells | 7 presented arms × both renderings × both fabrics @ 64 MiB, + AG-INC/RD-base @ 256 MiB |
| placements | distributed (`mcast_pin=-1`) vs pinned (`mcast_pin=0`) |
| PFC provisioning | derived per fabric (`common/sim.py pfc_config`): XOFF/XON 218/174 (crossbar), 389/311 (3-tier), egress cap radix×BDP 17152/7024 pkt |
| metrics | makespan; `pauses_sent/resumes_sent`; peak ingress/egress occupancy as fraction of bound; `NIC_PFC_GATE` counters; `drop_log_hits` |
| traces | `-pfc_trace` CSV (PAUSE/RESUME + decimated occupancy) on selected cells |

## Instrumentation (submodule changes)

- `HTSIM_spcl-patch/queue_lossless_input.{h,cpp}` + `queue_lossless_output.{h,cpp}`:
  pause/resume counters, per-class peak-occupancy watermarks, `-pfc_trace` event log
  (`PFC_SUMMARY_INGRESS/_EGRESS` printed at teardown next to `NIC_PFC_GATE`).
  The counters do not intentionally alter queue scheduling; timing compatibility is
  can be checked by rerunning the matrices rather than inferred from old cells.
- `HTSIM_spcl-patch/datacenter/fat_tree_topology.cpp`: accountants named
  `VQ-<from>-><to>(<bank>)` for the trace/raster labels.
- `pcm/apps/htsim_atlahs/htsim_app_atlahs.cpp`: `-pfc_trace` flag; teardown dumps.
- `-mcast_pin` (pre-existing): `-1` = round-robin; `≥0` = pin all trees.

## Reproduce

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim build
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim run scaleup_pfc_concurrent --validate
simulation-scripts/experiments/scaleup_pfc_concurrent/run_thesis.sh  # stress+census+controls+overdrive
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
    /workspace/simulation-scripts/experiments/scaleup_pfc_concurrent/plot.py
```

Tracked reference outputs in `simulation-scripts/results/scaleup_pfc_concurrent/`:
`scaleup_pfc_concurrent.csv` (stress), `pfc_census.csv`, `pfc_controls.csv`,
`pfc_overdrive.csv`, selected `traces/*.csv.gz`, `pfc_backpressure|pfc_sawtooth|pfc_raster.pdf`,
`tab_pfc_validation.tex`. Fresh runs retain gzipped stdout under ignored `logs/`; those
logs are not part of the tracked reference bundle. The selected compressed traces used
by the figures are tracked.
