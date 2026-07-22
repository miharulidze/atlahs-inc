# scaleup_coll_ab — scale-up INC-vs-endpoint collective A/B

The headline per-collective A/B on the pcm-sdk two-tier simulator, isolated to
a single scale-up domain: an in-network collective arm (first-class `coll` op +
`.groups` sidecar, offloaded to the switch datapath) against an endpoint
baseline emitted by the NCCL generator's own decomposition (Ring /
Recursive-doubling per Demystifying-NCCL Tables V-VII).

- Collectives: AllReduce (ring + recursive-doubling baselines), ReduceScatter,
  AllGather (ring).
- Reduction model: charge-neither (`--reduce-compute 0`, the default); >0 is a
  sensitivity study.
- Isolation: all N ranks in node 0 (`-nodes N -num_gpus_per_node N`) — the
  whole collective runs intranode; the scale-out topo carries no traffic and
  only needs >= N hosts (`--so-topo`, default `tree16_bw200Gbps.topo`; use
  `tree64_8.topo` for N=64).

## Run

```bash
# in Docker (from the repo root; see ../../README.md for build steps)
docker run --rm -v "$(pwd)":/workspace atlahs-sim run scaleup_coll_ab --validate
docker run --rm -v "$(pwd)":/workspace atlahs-sim run scaleup_coll_ab \
    --n 8 --su-topo scaleup_nvlink5_nvl72_7200Gbps.topo

# locally (binaries built in-tree, no env vars needed)
python3 experiments/scaleup_coll_ab/run.py --validate
```

`--validate` is topology-independent: it generates both arms, checks rank-0
step counts against the NCCL-paper tables, and compiles with the coll txt2bin —
no simulator run.

## Output

`results/scaleup_coll_ab/scaleup_coll_ab.csv` (append-mode; override dir with
`SCALEUP_OUTPUT_DIR`) + one `.log` per case holding the INC-arm command line.
`inc_ns`/`base_ns` are makespans minus the shared `TAIL_NS` tail; `speedup` =
`base_ns / inc_ns`.

Known caveat (2026-07-21): AllGather INC results at >= 256 KB are untrustworthy
until the handle_mcast lossless-credit / fanout-replica backpressure bug is
fixed — the run logs `LOSSLESS not working` violations, surfaced in the CSV
`drops` column.

Design rationale: `sim/htsim-backend/sim/AA-plan-Scaleup-Baselines/plan.md` (local).
