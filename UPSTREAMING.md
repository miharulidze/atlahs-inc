# Repository lineage and upstreaming notes

State as of 2026-08-06:

- The public artifact branch is `wstaempfli/atlahs:umbrella-integration`.
- The maintained NCCL trace generator is the public
  `wstaempfli/nccl_generator_v2` submodule.
- The maintained thesis backend is the public
  `wstaempfli/pcm-sdk:wanja/inc-port` submodule.
- The retired standalone `sim/htsim-backend` development fork was removed from the
  release tree. Its previous revisions remain available in this repository's history.

This artifact diverges substantially from `spcl/atlahs`. Any future upstream pull
request should be curated rather than opened from the integration branch wholesale:

1. start from the then-current upstream default branch;
2. split generator, PCM integration, experiment harness, and reference data into
   separately reviewable changes;
3. preserve original authorship for code imported from collaborators;
4. keep generated result archives outside a source-only upstream pull request; and
5. resolve licensing for the PCM repository before proposing redistribution under an
   upstream project license.

The supported thesis reproduction recipes are in
`simulation-scripts/REPRODUCING.md`; publication gates are in
`simulation-scripts/PUBLICATION_CHECKLIST.md`.
