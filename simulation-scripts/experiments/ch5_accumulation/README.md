# Chapter 5 training case study

This experiment is isolated from `intranode_linkspeed_sweep` and defines the
training workload used in Chapter 5:

- microbatch per DP rank: 1 sequence;
- accumulated microbatches per optimizer step: 32 by default;
- PP=1 schedule: explicit 1F1B (`F0,B0,...,F31,B31`);
- baseline AllReduce: Ring;
- ZeRO-1: one optimizer step after accumulation, using TP-local physical bytes.

The existing experiment and every file under
`results/intranode_linkspeed_sweep/` are never opened for writing.

## No-clobber result contract

Each invocation exclusively creates:

```text
results/ch5_accumulation/runs/<run-id>/
├── manifest.json
├── source_snapshot/
├── work/                 # generated graphs, GOAL, binaries and topologies
├── raw/                  # generator, translator and simulator stdout/stderr
├── audits.json
├── tables/results.csv
├── summary.json
└── .complete             # or .failed; partial artifacts are retained
```

An existing run ID is a hard error. There is no overwrite or force option.
The manifest records repository states and binary hashes; the source snapshot
also preserves the relevant dirty files exactly as executed.

## Run

From the repository root, using the existing simulation image:

Allocate a 16-GiB Docker VM for the full TP16 headline run. This is host
capacity for the simulator and is not an input to the modeled workload or
network.

```bash
# Generator/GOAL correctness gate only (no simulator):
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim \
  run ch5_accumulation --validate --tps 4 --accumulations 32

# Headline TP4/TP8/TP16 experiment at the 4000/400-Gb/s point:
docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)":/workspace atlahs-sim \
  run ch5_accumulation --tps 4,8,16 --accumulations 32 \
  --points 4000:400 --iters 1 --jobs 1
```

After a completed headline run, generate the thesis plot and tables from the
recorded CSVs (the image entrypoint is overridden only to run the plotting
script):

```bash
docker run --rm --user "$(id -u):$(id -g)" --entrypoint python3 -v "$(pwd)":/workspace -w /workspace \
  atlahs-sim simulation-scripts/experiments/ch5_accumulation/plot.py \
  --headline-run simulation-scripts/results/ch5_accumulation/runs/<headline-id> \
  --output-dir simulation-scripts/results/ch5_accumulation/runs/<headline-id>/thesis-assets
```

The plotting command requires one complete TP4/TP8/TP16 headline run. It
refuses an existing output directory and records the input path, input CSV
hash, and plotting-script hash in `case_study_summary.json`.

Use a descriptive `--run-id` when desired. Run IDs may contain only letters,
digits, dots, underscores and hyphens. The default contains a UTC timestamp and
the accumulation values.

Before simulation, the runner rejects a trace unless rank 0 contains the
expected TP AllReduce count and size and exactly one set of ZeRO-1 collectives
per optimizer iteration. For the default two-layer accumulation-32 workload,
that is 256 TP AllReduces per rank and iteration, each 32 MiB (8 GiB aggregate
declared TP tensor payload, before Ring's wire-volume factor and headers). The
runner additionally asserts the physical TP-local ZeRO bytes in the emitted
GOAL, not only the pre-translation collective count. It also verifies that
baseline and INC become byte-identical when TP collectives are replaced with
dependency-preserving zero-cost skeleton operations. Every accepted simulator
row must expose its transport counters and have zero drops, retransmissions and
RTS events.

## Scope and thesis wording

The paired arms inherit the same existing network treatment. In particular,
scale-out is an idealized non-blocking fabric with 1-ns links and zero switch
latency. Report it as an idealized synthetic-network experiment, not as
hardware-realistic absolute training time or BDP behavior.

The workload is a two-layer Llama-2-7B-geometry synthetic proxy, not a captured
or "Zhiyi-matched" trace. Microbatch one and accumulation depth 32 produce a
global batch of 128 and 256 TP AllReduces of 32 MiB per rank and iteration.
Batching is part of the experimental definition: the findings apply to this
schedule and should not be described as invariant to microbatch or
accumulation policy.

Each headline cell unrolls one complete optimizer iteration. The reported
makespan is an isolated-iteration result, not an estimate of a multi-iteration
steady state.
