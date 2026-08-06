# Phase 5 — merge INC into pcm-sdk's two-tier sim (Option A)

Date: 2026-06-26. Goal: in-network collectives running in the *real* two-tier scale-up/out
sim (Zhiyi's `pcm-sdk`), documented as a per-step commit trail.

## Where things live (the structural reality)

- Our INC lives in our `atlahs` repo (`htsim_uec`, branch `WIP-multicast-htsim-direct`) — where
  we commit. **This plan doc lives here.**
- pcm-sdk lives in the **separate study clone** `~/CLionProjects/atlahs_ZhiyiHu/sim/pcm-sdk_zhiyi`
  (now writable: **we have WRITE on `ZhiyiHu1999/pcm-sdk`**). It is NOT part of our `atlahs` repo.
- The build base for the atlahs app = **`HTSIM_spcl`** (`ZhiyiHu1999/HTSIM_spcl`,
  `SPCL_HTSIM_ROOT_DIR` in the htsim_atlahs CMakeLists) + the **patch layer**
  `HTSIM_spcl-patch/` (the two-tier scaffolding). `uet-htsim` is a read-only reference base.
- So the INC merge edits land in **HTSIM_spcl (switch/UEC/topology) + HTSIM_spcl-patch
  (logsim coll dispatch)** — both Zhiyi's, both writable. Phase-5 commits go to those repos.

## Build environment

CMake-based (≥3.16), **C++23**, needs **jsoncpp** + a **pre-built HTSIM_spcl lib** (two-stage).
A **Dockerfile exists** and the harness builds via `docker run atlahs build` — the intended,
reproducible path. Locally: Docker is installed (daemon must be started); native would need
`cmake`+`jsoncpp` and is fragile on macОS/arm64. **Recommend building/validating in Docker.**

## Step 0 — prerequisites (no INC edits yet)

- **P0a build env:** start Docker (or set up native).
- **P0b commit home:** fork pcm-sdk/HTSIM_spcl to your account *or* a clearly-named branch on
  Zhiyi's repos (you have write). Don't push to their default branch.
- **P0c validate baseline:** build the UNMODIFIED two-tier sim and run one Llama GOAL workload
  (`run_goal_workloads_exp.py`-style) → confirm it builds + runs + record the baseline makespan.
  This is the A/B's baseline arm and proves the env before we touch INC.

## Merge steps (each = one validated commit with a written description)

| step | change | commit |
|---|---|---|
| 5.1 | INC packet types (`UecMcastPacket`/`UecReducePacket`) + `inc_fib.h` into HTSIM_spcl; reconcile base drift | "Phase 5.1: INC packet types + per-switch FIB" |
| 5.2 | `handle_mcast`/`handle_reduce`/`fanout_replicas` + dispatch in `FatTreeSwitch::receivePacket`/`getNextHop` | "Phase 5.2: switch-side multicast/aggregation dispatch" |
| 5.3 | `UecCollectiveSink`/`UecCollectiveSrc` (uec_collectives) + `set_up_mcast`/`build_mcast_tree`, **tier-aware** (a scale-up group's tree lands in its scale-up instance) | "Phase 5.3: collective sinks/sources + tier-aware INC tree" |
| 5.4 | coll-op dispatch in the patch's `logsim-interface`: route a first-class `coll` to `htsim_apis[node+1]` (scale-up) + trigger INC + group bring-up. Reuses the confirmed node-containment routing (L123). | "Phase 5.4: GOAL coll → scale-up INC domain" |
| 5.5 | per-domain emit in the generator: scale-up collectives → `coll`, else decompose (the T8 change, reused) | "Phase 5.5: per-domain emit (scale-up as coll)" |
| 5.6 | run the two-tier **INC vs P2P A/B** on a TP-AllReduce workload; validate vs the 5.0 baseline | "Phase 5.6: two-tier INC A/B results" |

Each step: edit (host) → build/validate (Docker) → commit (to the chosen home) with a
description of what changed and why. Reconcile the ~100-line uet/our-fork base drift in 5.1–5.3.

## Risks
- Base drift (our fork ↔ HTSIM_spcl) in switch/UEC — sized at ~100 lines + the INC additions.
- `FatTreeTopologyWrapper`/`TopologyInterface` indirection (INC's topology hooks must reach the
  wrapped FatTreeTopology).
- coll-op routing: the patch routes per-send today; coll ops are new to its logsim path.
- Native build fragility → prefer Docker.
