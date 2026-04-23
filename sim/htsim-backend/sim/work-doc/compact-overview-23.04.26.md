# Phase-One Compact Overview — ACK-less P2P Broadcast Baseline

## Goal

Establish an ACK-less point-to-point baseline for `MPI_Bcast` in the
ATLAHS/htsim UEC backend, as the reference curve phase-two
switch-level multicast must beat.

## Architecture: (A) + (D)

- **(A) Dedicated subclasses** `UecBcastSrc : UecSrc` and
  `UecBcastSink : UecSink` in `sim/htsim-backend/sim/uec_bcast.{h,cpp}`.
  No CWND gating, no RTO, no ACK/NACK emission. Source fires packets
  once via `bcast_send_once()`; sink counts bytes and fires
  `_end_trigger` on last byte.
- **(D) Leg expansion** in `main_uec.cpp`: one
  `start_bcast ROOT_IDX -> GROUP_IDX` entry is decomposed into
  |G|−1 unicast legs joined by one `BarrierTrigger(count=|G|-1)`.
  Collective-complete = barrier fires = last byte received at the
  farthest sink.

## Parser & safety

- New tokens: `Grp` (member group), `start_bcast` (collective start).
- `ConnectionMatrix::max_flowid()` and `::max_triggerid()` seed
  synthesised ids from user-visible maxima so bcast legs can't collide
  with user-assigned flowids on shared `(host, flow_id)` ToR FIB
  entries.
- Parser rejects a matrix that uses `start_bcast` but leaves any
  connection without an explicit `id` (closes the remaining
  `PacketFlow`-default collision window).
- Naming split: `bcast` for the collective (everything user-facing);
  `mcast` reserved for the network-layer mechanism
  (`FatTreeTopology::set_up_mcast()` stub — phase two).
- Leg names include the op's user-supplied `id` when present:
  `uec_bcast_op1_1_2`.

## Measurement harness

- `BcastCompletionRecorder` attached to every barrier emits one
  machine-parseable line on completion:
  ```
  BCAST_COMPLETE op_id=… root=… group=… size=… legs=…
                 start_ns=… complete_ns=… duration_ns=…
  ```
- Three scripts in `sim/htsim-backend/`:
  - `…/connection_matrices/gen_bcast_sweep.py` — per
    `(nodes, group, rep)` random-group `.cm` generator.
  - `…/connection_matrices/run_bcast_sweep.py` — runs `htsim_uec`,
    parses, writes CSV.
  - `…/plotting/plot_bcast_baseline.py` — completion-time vs.
    group-size plot with min/median/max error bars.

## Results (3 fat-trees × up to 10 group sizes × 10 reps each, 4 KB payload, 100 Gbps)

- **Linear scaling in leg count**: ≈339 ns per added leg, matching
  the 327 ns 100-Gbps serialisation of one 4 KB MTU.
- **Topology size is invisible**: 16-, 128-, and 1024-node curves
  overlap exactly at every shared x-value — the root's uplink is the
  one bottleneck; fat-tree path diversity doesn't help.
- **Group-membership variance is negligible**: only one reproducible
  outlier at |G|=2 (intra-rack pairing shaves ~1.1 µs) — because
  under `ECMP_FIB` path selection is deterministic and for |G|>2 the
  root-link serialisation cost swamps any fabric variation.

## Thesis documentation

- `sim/htsim-backend/sim/AA-thesis-baseline.md` — internal design
  record, 9 sections.
- `thesis/Design and Implementation.tex` — new `§sec:bcast-baseline`
  with 7 subsections (assumptions, incompatibility analysis, design
  space, chosen architecture, leg synthesis, ID allocation, naming,
  measurement), captioned code listings.
- `thesis/Validation and Evaluation.tex` — filled Methodology +
  Broadcast Baseline sections, three-block results table, figure,
  four-paragraph analysis.
- `thesis/figures/bcast_baseline.pdf` bundled into the thesis repo
  for standalone Overleaf builds.
- `thesis/refs.bib` — added Hoefler & Belli 2015 benchmarking
  citation.

## Commits

**atlahs** (branch `WIP-multicast-htsim-direct`):
- `01fb408` — baseline classes + leg expansion
- `8f18023` — benchmark tooling + first sweep
- `5a6d3e2` — move plotting under `sim/htsim-backend/`
- `ae08d17` — extend sweep to 1024-node

**thesis** (branch `main`, pushed to `wstaempfli/Thesis`):
- `f9d12c0` — Design + Eval sections
- `7ffbfae` — polish + bundle figure

## Deferred

- `src`/`dst` field convention swap on `bcast` lines (you said
  "omit for now").
- Phase-two switch-level `mcast` replication in
  `FatTreeTopology::set_up_mcast()`.
