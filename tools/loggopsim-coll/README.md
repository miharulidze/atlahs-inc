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

## Build

```bash
# 1. Get the canonical compiler
curl -O https://htor.inf.ethz.ch/research/LogGOPSim/LogGOPSim-1.1.tgz
tar xzf LogGOPSim-1.1.tgz && cd LogGOPSim-1.1

# 2. Apply the coll grammar (from the dir holding this README)
patch -p1 < /path/to/tools/loggopsim-coll/coll.patch

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
- `root` = destination host for rooted ops; `-1` for rootless (allreduce/allgather/reduce_scatter)

## `.bin` encoding contract

Must match the simulator's reader (committed in pcm-sdk `HTSIM_spcl-patch/lgs/Parser.hpp`,
commit `e5c3ddf`). A coll op reuses the existing fixed Node record:
- `Type` = `OPTYPE_BCAST..OPTYPE_ALLGATHER` (10–14)
- `Peer` = `group | (root << 16)` (`root == 0xFFFF` ⇒ rootless)
- `Tag`  = `instance`

p2p `send`/`recv`/`calc` records are unchanged.

## Validation (2026-07-01)

- **Byte-identical p2p:** the patched `txt2bin` on the shipped `16_nodes_incast.goal`
  reproduces `16_nodes_incast.bin` (3064 B) **byte-for-byte** (`cmp`-clean) — the coll
  additions do not disturb the p2p format.
- **Coll round-trip:** a `coll allreduce` `.goal` compiles to a `.bin` that the sim
  **parses and recognizes** (`OPTYPE_ALLREDUCE → OP_ALLREDUCE`), currently no-op'd
  (0 packets, exit 0) — correct until Increment 3 wires the collective src/sink that
  turns recognized coll ops into actual INC traffic.

## Provenance

Part of the ARCH-1 INC port (Increment 2c, writer side). The coll grammar mirrors the
reader-side committed in pcm-sdk `HTSIM_spcl-patch/lgs/` (`e5c3ddf`); this patch is the
matching writer on the canonical LogGOPSim 1.1 base.
