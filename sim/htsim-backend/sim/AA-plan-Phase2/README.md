# Phase-Two Plans — Switch-Level INC for Broadcast (and beyond)

Design and implementation documents for ATLAHS / htsim phase two:
introducing switch-level multicast on top of the phase-one
ACK-less unicast broadcast baseline.

## Active documents

- **`v4.md`** — *the locked design.* Full design rationale,
  alternatives considered, and design choices for phase two.
  Read this when you want to understand *why* the implementation
  takes the shape it does.
- **`impl.md`** — *the executable plan.* 12 ordered tasks
  (T1–T12), each with file paths, code skeletons, test specs,
  and a definition-of-done. Read this when you want to *do* the
  implementation.

When in doubt: **start with `impl.md`**, refer to `v4.md` if any
task feels under-specified.

## History (preserved for reference)

- `v1.md` — Initial proposal. Tree replication (β), reserved
  destination range (`MCAST_BASE | group_idx`) for multicast
  signalling, leaf-TOR `_dst` rewrite, one tree per
  `(root, group_idx)` pair.
- `v2.md` — Reverse-Path-Forwarding flip: one undirected tree
  per group, RPF dispatch (skip ingress port), explicit
  rationale for why this works for fat-tree multicast.
- `v3.md` — Generalises the FIB shape for the full INC family
  (multicast + aggregation): renames `McastFibEntry` →
  `INCFibEntry`, switches to `std::bitset<128>` port-bitmap
  representation. Adds §10 phase-3 preview showing the FIB
  reuses for Reduce / Allreduce / Allgather.
- `v4.md` — First-class packet type and sink class: replaces
  the `_dst`-range hack with a new `UecMcastPacket` peer of
  `UecPacket`; replaces leaf-TOR rewrite with a new
  `UecMcastSink` registered per (host, group); introduces
  operation-agnostic base classes
  `UecCollectiveSrc`/`UecCollectiveSink`. Path-derived
  `_pathid` (running hash of group + source + egress-port
  sequence) for cross-operation trace correlation.
- `impl.md` — Production-ready execution plan derived from v4.

## Files

| File | What it is |
|---|---|
| `v4.md` / `v4.pdf` | Locked design (this is *the* design doc) |
| `impl.md` / `impl.pdf` | Implementation plan (this is *the* execution doc) |
| `v1.md`–`v3.md` + PDFs | Design history |
| `print.css` | Pandoc CSS used to generate the PDFs |
| `README.md` | This file |

## Regenerating PDFs

From this directory:

```bash
pandoc v4.md \
  --pdf-engine=weasyprint --css=print.css \
  --metadata title="Phase-Two Plan v4" \
  --standalone --toc --toc-depth=3 \
  -o v4.pdf
```

Same pattern for `v1`–`v3` and `impl`. Requires `pandoc` and
`weasyprint` (e.g. `brew install pandoc weasyprint`).

## Status

- Design phase: **complete** (v4 locked).
- Implementation phase: **not started**. Awaiting green light.
- Phase-three (aggregation) preview: in v4 §10.

## Out-of-tree references

- Phase-one baseline plan: `../AA-plan-Baseline.md`.
- Thesis (separate submodule): `../../../../thesis/`. Phase-two
  write-up was drafted and reverted; recoverable from the
  thesis repo's `git reflog` (commit `f1184e6`) or re-derivable
  from `v4.md` when implementation lands.
