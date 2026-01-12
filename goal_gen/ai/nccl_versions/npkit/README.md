# NPKit-enabled NCCL versions

This folder collects notes and reproduction guides for **NPKit-enabled NCCL** builds used by ATLAHS/GOAL tooling.

## NCCL 2.20 (NPKit)

NPKit-enabled NCCL 2.20 is available here:

- https://github.com/ZhiyiHu1999/nccl_npkit_v2.20.5-1/tree/main

## NCCL 2.28 (NPKit)

For NCCL 2.28.x, see the reproduction guide in this repo:

- `nccl_228/README.md`

That guide covers:

- how to patch a clean NCCL 2.28.3 source tree,
- how to build with the required tracing flags,
- how to build/run the NPKit microbenchmark,
- and where the generated NPKit summaries are written.