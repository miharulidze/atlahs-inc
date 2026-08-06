# coll-extended LogGOPSim 1.1 `txt2bin` (GOAL → .bin with INC collective verb)

The htsim / pcm-sdk two-tier simulator reads GOAL traces as `.bin`. The canonical
compiler is **upstream LogGOPSim 1.1**, *not* the `lgs/txt2bin` bundled in the
htsim source tree (that one has drifted and emits `.bin` the simulator misreads —
its incast output segfaults `start_lgs`). This is what produced the shipped
`example_incast/16_nodes_incast.bin`.

`coll.patch` adds the in-network-computing **`coll`** verb to LogGOPSim 1.1's
`txt2bin`, so INC collective traces (e.g. `llama3_inc.goal`) compile to
**sim-readable** `.bin`. It changes only three source files (`txt2bin.re`,
`Goal.hpp`, `Parser.hpp`) and leaves the point-to-point (`send`/`recv`/`calc`)
`.bin` encoding **byte-identical**.

`op-flow-id-range.patch` restricts collective instance/`Tag` to
`0..999,999,999`. GOAL stores the field as uint32, but htsim reserves IDs from
`1,000,000,000` upward for dynamically allocated point-to-point flows. The
guard rejects reserved or overflowing values instead of silently aliasing a
trace-wide `op_flow_id`.

## Build

```bash
# 1. Get the canonical compiler
curl -O https://htor.inf.ethz.ch/research/LogGOPSim/LogGOPSim-1.1.tgz
tar xzf LogGOPSim-1.1.tgz && cd LogGOPSim-1.1

# 2. Apply the coll grammar (from the dir holding this README)
patch -p1 < /path/to/tools/loggopsim-coll/coll.patch
patch -p1 < /path/to/tools/loggopsim-coll/op-flow-id-range.patch

# 3. Regenerate the lexer and build (macOS: brew install re2c gengetopt)
re2c -o txt2bin.cpp txt2bin.re
g++ -g -O3 txt2bin.cpp cmdline_txt2bin.c -o txt2bin        # or: make txt2bin

# 4. Compile a GOAL trace
./txt2bin -i trace.goal -o trace.bin
```

## `coll` .goal syntax

```
coll <kind> <size>b <group> <instance> <root> [cpu <c>] [nic <n>]
```
- `kind` ∈ `bcast | reduce | allreduce | reduce_scatter | allgather`
- `root` = global GOAL rank of the rooted participant for Broadcast/Reduce;
  `-1` for rootless operations (AllReduce/AllGather/ReduceScatter)
- `instance` = trace-wide `op_flow_id` in `0..999,999,999`

## `.bin` encoding contract

Must match the simulator's reader (committed in pcm-sdk `HTSIM_spcl-patch/lgs/Parser.hpp`,
commit `e5c3ddf`). A coll op reuses the existing fixed Node record:
- `Type` = `OPTYPE_BCAST..OPTYPE_ALLGATHER` (10–14)
- `Peer` = `group | (root << 16)` (`root == 0xFFFF` ⇒ rootless)
- `Tag`  = `instance`

p2p `send`/`recv`/`calc` records are unchanged.

## Validation

- **Byte-identical p2p:** the patched `txt2bin` on the shipped `16_nodes_incast.goal`
  reproduces `16_nodes_incast.bin` (3064 B) **byte-for-byte** (`cmp`-clean) — the coll
  additions do not disturb the p2p format.
- **Collective execution:** `simulation-scripts/validate_artifact.sh` compiles all five
  verbs and runs packet-level positive and fail-fast regressions against the public PCM
  backend, including delayed arrivals, rooted nonzero domains, partial domains, malformed
  groups, incomplete operations, and the `op_flow_id` namespace boundary.

## Provenance

This writer originated in the ARCH-1 INC port and mirrors the reader under the public
PCM repository's `HTSIM_spcl-patch/lgs/`. The root ATLAHS gitlink records the exact
reader revision paired with this patch.
