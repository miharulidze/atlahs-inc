# Emerging scale-up domain topologies (Table 2.2)

htsim fat-tree `.topo` models of the emerging **scale-up** interconnects compared in the ATLAHS v2
thesis (Zhiyi Hu, "Extending ATLAHS", Table 2.2 "Comparison of Emerging Scale-Up Technologies").
Each fabric is a `FatTreeTopology` `.topo` (shared format — the pcm-sdk two-tier simulator IS htsim).

**These files live here, in the experiment harness's topo dir (`simulation-scripts/topo_files/`), because
all HEADLINE scale-up experiments run through the pcm-sdk two-tier simulator** — the htsim-backend fork
was only the primitive-development / `.cm`-validation sandbox. The experiments
(`simulation-scripts/experiments/*/run.py`, e.g. `scaleup_coll_ab`) feed each file to the pcm binary as
the scale-up domain:

- headline (pcm two-tier): `run scaleup_coll_ab --su-topo <file>` → `htsim_flow_app_atlahs … -intranode_topo <file>`,
  run inside the `atlahs-sim` Docker image (the pcm binaries are Linux/aarch64 builds).
- dev cross-check only (fork, macOS-native): `htsim_uec -topo <file> …`.

**OISA 2.0 is intentionally omitted** (China Mobile; bandwidth "Unknown", no public deployment
structure to mimic). NVLink 5.0, UALink 1.0 and SUE are modelled as **one faithful `.topo` per
technology** (real structure + real-ish latency). *The earlier nonblocking/realistic two-variant split is
being retired — NVLink is already consolidated; UALink and SUE consolidation is pending.*

## Files

| file | tech (org) | max scale | bidir BW | per-host unidir | tiers | switch structure | oversub | max hops |
|---|---|---:|---:|---:|:--:|---|:--:|:--:|
| `scaleup_nvlink5_nvl72_7200Gbps.topo` | NVLink 5.0 (NVIDIA) | 72 | 1800 GB/s | 7200 Gbps | 1* | single non-blocking crossbar (real NVSwitch-5 radix; **estimated** latency) | 1:1 | 1 |
| `scaleup_ualink1_pod1024_nonblocking_3200Gbps.topo` | UALink 1.0 (UALink Consortium) | 1024 | 800 GB/s | 3200 Gbps | 2 | 32 leaf(r64) + 32 spine(r32), full bisection | 1:1 | 2 |
| `scaleup_ualink1_pod1024_realistic_3200Gbps.topo`  | UALink 1.0 (UALink Consortium) | 1024 | 800 GB/s | 3200 Gbps | 2 | same Clos, real UALink latencies, leaf radix = per-ULS-chip | 1:1 | 2 |
| `scaleup_sue_4096_nonblocking_800Gbps.topo` | SUE (Broadcom) | 4096 | 200 GB/s | 800 Gbps | 3 | 256 leaf + 256 agg + 256 core (radix 32/32/16), full bisection | 1:1 | 3 |
| `scaleup_sue_4096_realistic_800Gbps.topo`   | SUE (Broadcom) | 4096 | 200 GB/s | 800 Gbps | 2 | 64 leaf(r96) + 32 spine(r64), real Tomahawk-Ultra latency | 2:1** | 2 |

\* encoded as a **degenerate 2-tier** fat-tree because htsim's parser rejects `Tiers=1`; the Tier-1
uplink is vestigial (all pairs one hop on one logical ToR). \*\* the SUE realistic 2:1 oversub is a
htsim-cap artifact, **not** real SUE — see caveats.

## Bandwidth convention (locked)

Table 2.2 quotes **bidirectional GB/s per accelerator**; the htsim `.topo` `Downlink_speed_Gbps` is
**per-direction (unidirectional) Gbps per host link**. Matching the existing sibling files where
`3600 Gbps = NVLink-4 = 900 GB/s bidir`:

> **Downlink_speed_Gbps = (Table 2.2 bidirectional GB/s) × 4**  ( = /2 for one direction, ×8 for bits )

- NVLink 5.0: 1800 GB/s bidir → **7200 Gbps** (2× the NVLink-4 sweep class, matching NVLink-5's doubling).
- UALink 1.0: 800 GB/s bidir → **3200 Gbps**.
- SUE: 200 GB/s bidir → **800 Gbps**.

## htsim encoding constraints (why the structures look the way they do)

- **Tiers must be 2 or 3** (`fat_tree_topology.cpp` parser). A single switch layer is expressed as a
  degenerate 2-tier fat-tree.
- **≤ 96 ports per switch** (`assert(addPort(...) < 96)`). A single non-blocking switch is therefore
  only expressible up to ~96 hosts. **1024 (UALink) and 4096 (SUE) MUST be multi-tier** — which is
  faithful, since those fabrics are genuinely multi-switch at that scale. htsim is **not** modified.

## Per-technology structure & sources

### NVLink 5.0 — NVIDIA GB200 NVL72 (72 GPUs, 7200 Gbps)
72 Blackwell GPUs in one NVLink-5 domain, **single non-blocking NVSwitch-5 tier, 1 hop**. Each GPU:
18 NVLink-5 ports × 100 GB/s bidir = 1800 GB/s. Switch: 18 NVSwitch-5 ASICs (9 trays × 2), radix 72;
the 18 ASICs are **18 parallel planes over one switch stage** (not a leaf/spine hierarchy) → genuine
single-tier non-blocking (130 TB/s aggregate = 72 × 1.8 TB/s; **65 TB/s bisection = 36 × 1.8 TB/s**). 72 < 96, so single-switch is exact, not
a compromise. NVSwitch-5 also hosts SHARP (in-network reduce/multicast) — the physical INC substrate.
Sources: NVIDIA GB200 NVL72 / NVLink pages, NVIDIA dev blog, SemiAnalysis GB200 teardown, NVSwitch
Hot Chips 2022.

### UALink 1.0 — UALink Consortium pod (1024 accelerators, 3200 Gbps)
Open memory-semantic scale-up interconnect (ratified Apr 2025). 200 GT/s/lane; a "station" = 4 lanes =
800 Gb/s; 4 stations/accelerator = 800 GB/s bidir. Spec defines a **single switch hop up to 1024
endpoints** (flat 10-bit routing ID) over a **non-blocking, lossless** UALink Switch (ULS) fabric; ULS
chips target 102.4 Tb/s (512×200G lanes) = 128 x4-ports = 32 full-bandwidth accelerators/chip. Modelled
as a non-blocking 2-tier Clos (leaf radix 32 = accelerators per real 512-lane ULS chip). Sources:
UALink 1.0 white paper + spec overview, APNIC scale-up-fabrics, Cadence, Tom's Hardware, StorageReview.

### SUE — Broadcom Scale-Up Ethernet (4096 endpoints, 800 Gbps)
Ethernet-native scale-up framework (spec v1.0 → OCP, Sep 2025). 200 GB/s bidir/endpoint (4×200G lanes
/dir). Reference switches Tomahawk-class: **Tomahawk Ultra** (51.2 Tbps, **250 ns** switch latency,
lossless via LLR + CBFC — the low-latency scale-up chip) and **Tomahawk 6** (102.4 Tbps, radix-128
800G ports — the throughput chip). SUE uses **single- or two-tier** hierarchies; a real 4096 pod on
radix-128 TH6 is **non-blocking in 2 tiers**. Sources: Broadcom SUE framework spec + blogs, Tomahawk 6
/ Tomahawk Ultra releases, HPCwire, ServeTheHome.

## Model per technology (consolidation in progress)

The design is moving to **one faithful `.topo` per technology** — real structure + real-ish latency, with
unsourced latencies labelled as estimates. The earlier **nonblocking/realistic** two-variant scheme
(idealized `Oversubscribed 1` + generic 500/300 ns latency, vs. product-faithful radix/tiers/latency) is
being retired. NVLink is consolidated; UALink and SUE are pending.

Honest per-tech notes:
- **NVLink 5.0 (consolidated → `scaleup_nvlink5_nvl72_7200Gbps.topo`):** genuinely single-tier non-blocking
  (a 2-tier Clos was considered and rejected as *less* faithful), so the two former variants were
  structurally identical and differed only in latency. The single file keeps NVLink-5 latency **estimates**
  (100 ns switch / 30 ns link — order-of-magnitude, no NVIDIA disclosure; see the `.topo` header).
- **UALink 1.0:** both variants are non-blocking (the real UALink pod is non-blocking). Difference is
  latency fidelity: realistic uses UALink-class figures (~125 ns switch / ~30 ns short-reach link) but,
  because htsim forces 2 hops where real UALink is 1 hop, end-to-end latency is a documented **upper
  bound** vs. the real single-hop pod.
- **SUE:** nonblocking = full-bisection **3-tier** (the 96-port cap forces 3 tiers where real TH6 does it
  in 2). Realistic = **2-tier** with a **2:1 leaf oversubscription that is a cap artifact** (real 4096 SUE
  pods are non-blocking; htsim can't express radix-128), but it carries the real **250 ns Tomahawk-Ultra**
  switch latency, deliberately capturing the genuine Ethernet-vs-NVLink switch-latency gap.

## Validation status

**Topology load/construct — ALL 6 PASS.** Each builds exactly its Max Scale host count (72 / 1024 /
4096) with zero structural asserts, respecting the 96-port cap. Recipe:

```
cd sim/htsim-backend/sim/datacenter
./make_allreduce_ab /tmp/probe.bin ring 8 4096
./htsim_uec -goal /tmp/probe.bin -topo topologies/scaleup_emerging/<file> \
            -strat ecmp_host -linkspeed <bidirGBps*4>000 -mtu 4096 -paths 128 -seed 1
# PASS = "FatTree constructor done, <MaxScale> nodes created", 0 asserts
```

**Full packet-sim run — a known htsim scale ceiling, NOT a topology defect:**
- NVLink 5.0 (72) runs a full ring sim to completion cleanly.
- UALink 1.0 (1024) and SUE (4096) **load correctly** but a full default-config UEC packet sim aborts in
  htsim's queue scheduler (`compositequeue.cpp:120 assert(0)`) at ~100 %+ progress. This is a htsim
  **engine/config ceiling above ~1000 hosts**, proven **orthogonal to topology validity**: a
  hand-built non-blocking 2-tier fat-tree at **256** hosts (same shape as the UALink file) runs clean,
  while the identical structure at 1024 aborts. Independent of `-paths` and `-queue_type`. Resolving it
  (CC/queue/coupling-mode tuning, or a htsim fix) is a separate task before running full sims at
  1024/4096; the topology files themselves are correct and reusable.
