# scaleup_coll_ab — scale-up INC-vs-endpoint collective A/B

The headline per-collective A/B on the pcm-sdk two-tier simulator, isolated to
a single scale-up domain: an in-network collective arm (first-class `coll` op +
`.groups` sidecar, offloaded to the switch datapath) against an endpoint
baseline emitted by the NCCL generator's own decomposition (Ring /
Recursive-doubling per Demystifying-NCCL Tables V-VII).

- Cases: fused AllReduce (ring + recursive-doubling baselines), composed
  ReduceScatter+AllGather versus ring AllReduce, ReduceScatter, AllGather,
  Broadcast, and Reduce.
- Reduction model: charge-neither (`--reduce-compute 0`, the default); >0 is a
  sensitivity study.
- Isolation: all N active ranks are in domain 0. `-num_gpus_per_node` is pinned
  to the selected scale-up topology's width, so group-size sweeps partially
  populate a fixed fabric. The scale-out topology carries no traffic.

## Run

```bash
# in Docker (from the repo root; see ../../README.md for build steps)
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace \
  atlahs-sim run scaleup_coll_ab --validate

# frozen thesis sweep; stages a run archive and atomically refreshes the CSV
simulation-scripts/experiments/scaleup_coll_ab/run_thesis.sh

# locally (binaries built in-tree, no env vars needed)
python3 simulation-scripts/experiments/scaleup_coll_ab/run.py --validate
```

`--validate` is topology-independent: it generates both arms, checks rank-0
step counts against the NCCL-paper tables, and compiles with the coll txt2bin —
no simulator run.

## Output

`results/scaleup_coll_ab/scaleup_coll_ab.csv` (append-mode; override dir with
`SCALEUP_OUTPUT_DIR`) + one `.log` per case holding the INC-arm command line.
`inc_ns`/`base_ns` are makespans minus the shared `TAIL_NS` tail; `speedup` =
`base_ns / inc_ns`.

Direct `run.py` invocations append by design. The frozen wrapper writes to
`results/generated-runs/<run-id>/` first and replaces the reference CSV only after the
complete matrix succeeds.

The tracked reference data was generated with the current stable event ordering and
globally unique collective flow IDs. See `../../REFERENCE_DATA.md`. `--validate`
checks every current arm, while
`scaleup_pfc_concurrent` independently checks lossless queue engagement and bounds.
