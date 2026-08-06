# Repository lineage

State as of 2026-08-06:

- The working branch is `wstaempfli/atlahs:umbrella-integration`.
- The maintained NCCL trace generator is the public
  `wstaempfli/nccl_generator_v2` submodule.
- The maintained thesis backend is the public
  `wstaempfli/pcm-sdk:wanja/inc-port` submodule.
- The standalone `sim/htsim-backend` path is retained as development history. The
  thesis experiments use the PCM submodule instead.

This branch diverges substantially from `spcl/atlahs`. Any future upstream pull
request should be curated rather than opened from the integration branch wholesale:

1. start from the then-current upstream default branch;
2. split generator, PCM integration, experiment harness, and reference data into
   separately reviewable changes;
3. preserve original authorship for code imported from collaborators;
4. keep generated result archives outside a source-only upstream pull request; and
5. review the licensing of each separate repository before upstreaming it.

The thesis experiment commands are in `simulation-scripts/REPRODUCING.md`.
