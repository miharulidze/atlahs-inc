# Upstreaming notes (deferred PR to spcl/atlahs)

State as of 2026-07-31 (branch `umbrella-integration`, fully pushed to origin =
github.com/wstaempfli/atlahs, private).

- spcl/atlahs is public; main = `fb51a99` (2026-05-12). We are 211 ahead / 6 behind
  (merge-base `c7b8a45`). The 6 missing upstream commits: two demo-script merges
  (`1365e00`, `80daee1` — low risk), GOAL rank-layout detection `ad9a4d6` (touches
  `sim/htsim-backend/sim/datacenter/atlahs_htsim_api.*` — moderate conflict risk with
  our datacenter work), and `4a05a2f`, which bumps `goal_gen/ai/nccl_generator_v2` to
  the public Yanksi repo @ `b4f98b2` — a direct conflict with our redeclaration.
- Submodule visibility landmines for any public PR:
  - `goal_gen/ai/nccl_generator_v2` → `wstaempfli/nccl_generator_v2` (PRIVATE),
    pinned on branch `simple-sim-coll`. Upstream declares `Yanksi/nccl_generator_v2`
    (public). Options: make the mirror public, PR `simple-sim-coll` into Yanksi,
    or repoint the PR branch to a public pin.
  - `sim/pcm-sdk_zhiyi` → `ZhiyiHu1999/pcm-sdk` + mirror `wstaempfli/pcm-sdk` (both
    PRIVATE, branch `wanja/inc-port`). The engine roots in Khalilov's private
    pcm-sdk; open-sourcing needs his OK. Not present upstream at all.
  - Consequence: an anonymous `git clone --recursive` cannot fetch either pin today;
    examiner access requires grants on both private repos (or making them public).
- Size: our tree adds ~29 MB tracked vs upstream (topo_files, .cm matrices, results
  CSVs/plots; 4,566 added files, 1.33 M inserted lines). A reviewable PR should carry
  code + docs and exclude generated data (`scripts/topo_files` bulk,
  `connection_matrices` sweeps, `simulation-scripts/results`), or ship data as a
  release artifact.
- PR scope split still open with Zhiyi: he upstreams `validations_atlahs_v1.1`
  himself (preferred) or our PR carries his commits (authorship preserved either way).
- Suggested first step when resuming: rebase a curated branch onto `fb51a99`,
  resolving `4a05a2f` (submodule pin) and `ad9a4d6` (`atlahs_htsim_api.*`) explicitly,
  then scope the file list before opening the cross-fork PR.

Reproducibility recipes for everything the thesis quotes:
`simulation-scripts/REPRODUCING.md`.
