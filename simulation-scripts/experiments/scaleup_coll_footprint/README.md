# scaleup_coll_footprint — multicast bandwidth-usage-reduction experiment

Reproduces (and extends) **Khalilov et al., SC24, Fig. 2** — *"relative bandwidth
usage reduction with multicast"* — as a **simulator-measured** result on the pcm-sdk
two-tier engine, scale-up domain in isolation.

```
ratio = footprint_baseline / footprint_INC          # y-axis, a multiplier >= 1
x-axis = number of participating GPUs P
```

`footprint_*` is the **network footprint** (byte·link crossings) measured by the ported
PT6 per-link counter (`-link_crosses_csv`).
The INC arm is the bandwidth-optimal multicast/aggregation datapath; the baseline is the
generator's own **Ring** / **Recursive-Doubling** decomposition. The INC footprint is
algorithm-independent, so Ring and RD share the same INC denominator (the paper's two-bar
structure). Collectives: **AllGather** (the paper's Fig. 2 collective), **AllReduce**,
**ReduceScatter**, **Broadcast**, and **Reduce**.

## Metric: byte-ratio, not packet-ratio

The counter records both `total_link_crosses` (packets) and `total_link_bytes`. **Use the
byte-ratio.** The P2P transport emits ~1 control packet (ACK/pull, ~64 B) per data packet
while the INC multicast arm emits ~none, so the *packet* ratio is control-inflated (e.g. 2.0
vs the true 1.0 at P=2). In *bytes* that control traffic is <~2 %, so the byte-ratio tracks
the analytic data-movement `2−2/P`. Both are in the CSV; the plots use bytes.

## Topologies and group-size scope

One fixed-width `.topo` per class; `P` is set by the **trace** (`--n`-equivalent per row),
not the topology. `-num_gpus_per_node` is pinned to the topo host width and `-nodes 2`
(mode Z — validated byte-for-byte vs the exact-width recipe; `nodes=2` is simply the fewest idle
host objects, not a ceiling workaround — the lossless path runs clean to ≥4096 host objects).
Idle hosts register zero crosses. NOTE: the scale-out `-topo` must respect htsim's ≤96-port
per-switch cap (`fat_tree_topology.cpp:876`) — e.g. `tree16`/`tree64_8`, NOT `tree128_nonblocking`
(129-port ToR), which aborts at construction. The `compositequeue` ~1000-host abort is a
fork / default-`COMPOSITE`-queue bug, off this lossless path.

| class | topo file | P sweep | what it shows |
|---|---|---|---|
| `single_switch` | `scaleup_single_switch_64_4000Gbps.topo` | 2,4,8,16,32,64 | every pair 2 hops → Ring≡RD, flat `2−2/P` |
| `fat3tier` | `scaleup_3tier_256_4000Gbps.topo` (4×4×16) | 2,4,8,…,256 | 2/4/6-hop regimes → RD diverges & climbs |
| `paper_r32` | `scaleup_ft_radix32_1024_4000Gbps.topo` | 2,4,8,…,1024 | radix-32 paper-scale reproduction |

The frozen thesis wrapper and tracked CSV use `single_switch` and `fat3tier` with
P=2–64, matching the plotted thesis matrix. `paper_r32` and fat-tree points above 64
are available as an explicit extension; keep them in a separately named result set.

## Run

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim build
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim run scaleup_coll_footprint --validate
simulation-scripts/experiments/scaleup_coll_footprint/run_thesis.sh
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace --entrypoint python3 atlahs-sim \
    /workspace/simulation-scripts/experiments/scaleup_coll_footprint/plot.py               # figures
```

Useful flags: `--topos single_switch,fat3tier`, `--collectives allgather,allreduce,reduce_scatter`,
`--max-p N` (cap group size), `--size-mults 1,4,16` (size-independence control), `--timeout`.

Counter correctness is validated separately by `simulation-scripts/_validate_footprint.py`
(single-switch AllGather byte-ratio == 2−2/P; Ring≡RD; mode-Z partial-population == exact-width).

## Outputs (`results/scaleup_coll_footprint/`)

The publication reference CSV and figures are tracked; newly generated additions under
`results/` are ignored unless deliberately curated into a release.
The frozen wrapper stages a complete run under `results/generated-runs/<run-id>/` and
atomically refreshes the reference CSV only after success.

The tracked CSV contains one row per logical experiment. Its earlier five appended
generations had identical measured fields and were reduced to the newest command/config
record without changing any number; see `../../REFERENCE_DATA.md`.

- `scaleup_coll_footprint.csv` — one row per (topology, collective, baseline_algo, P, size_mult):
  `crosses_inc/baseline`, `bytes_inc/baseline`, `ratio_crosses`, `ratio_bytes`, `analytic_ratio`,
  makespans, drops, status, full command.
- `footprint_allgather.png` — the direct Fig. 2 reproduction (single-switch | 3-tier).
- `footprint_all_collectives.png` — 3 collectives × 2 topologies.

See `RESULTS.md` for the measured numbers, findings, and caveats.
