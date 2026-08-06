# Topology inputs

These htsim `FatTreeTopology` files are consumed by the pcm-sdk two-tier simulator as
scale-up or scale-out domains. Publication experiments select topologies explicitly;
there is no implicit "current" topology.

## Publication and optional experiment inputs

| file | publication use |
|---|---|
| `scaleup_single_switch_64_4000Gbps.topo` | Chapter 4 completion-time and footprint results; PFC census |
| `scaleup_3tier_256_4000Gbps.topo` | Chapter 4 multi-tier results; PFC stress and census |
| `scaleup_ft_radix32_1024_4000Gbps.topo` | opt-in footprint extension at paper scale; not part of the tracked thesis matrix |
| `tree16_bw200Gbps.topo` / `tree64_8.topo` | idle scale-out domains for isolated scale-up tests |

Chapter 5 generates its parameterized topologies inside each immutable run directory.
Probe files and the emerging-technology models below are validation or exploratory
inputs, not substitutions for the frozen publication topologies. The retired standalone
htsim backend is not part of the supported experiment path.

## Exploratory emerging-technology files

The following models originated with the emerging scale-up comparison in the ATLAHS v2
thesis. Their product-level latency and structure assumptions are documented here, but
they do not feed the canonical Chapter 4 or Chapter 5 results.

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

## Exploratory model notes

The paired nonblocking/realistic files are retained as distinct sensitivity inputs. They
are not a pending publication choice: canonical runners name their topology directly.

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

## Validation policy

Canonical topology loading is exercised by the supported experiment validation and
release commands in `REPRODUCING.md`. The emerging 1024- and 4096-endpoint files have
passed structural construction checks, but large packet simulations with them are not a
supported artifact claim. Treat those files as exploratory until a pcm-sdk workload and
acceptance test are added to the public suite.
