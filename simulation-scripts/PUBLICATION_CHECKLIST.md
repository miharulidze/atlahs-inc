# Publication handoff checklist

Use this checklist when cutting the supervisor/workshop artifact. It separates source
publication from experiment validity; both must be complete.

## Source identity

- Commit and push the generator and pcm-sdk changes in their own public repositories.
- Update and commit the two root gitlinks only after those commits are publicly
  reachable without authentication.
- Record a clean root integration commit and release tag. `git submodule status` must
  not show a leading `+`, and the pre-build source checkout must have no unpublished
  changes.
- Record the Docker image ID or digest used for every long run.
- Pin the base-image digest for the release and record the installed package manifest;
  the development Dockerfile currently names an Ubuntu tag and apt packages.

## Licensing

- Confirm that the public generator repository has an explicit repository-wide license.
- Confirm the intended license for the pcm-sdk repository and preserve the licenses of
  its upstream/nested components. The root repository's MIT license does not
  automatically license separate submodule repositories.

## Reproducibility gates

- Build from a fresh recursive clone with `docker build -t atlahs-sim simulation-scripts/`.
- Run `simulation-scripts/validate_artifact.sh --extended`.
- Run the full `scaleup_pfc_concurrent` census, controls, stress, and overdrive suite on
  the exact release backend, using a fresh output directory.
- Run the complete Chapter 4 A/B, group-size, and footprint matrices on that same
  commit. Keep them separate from the historical thesis CSVs until their deltas have
  been reviewed.
- Run `model_checks/audit_reference_data.py`, regenerate figures and tables from the
  coherent release dataset, and compare the claim verifier's anchors with the manuscript.

## Chapter 5 provenance

- Keep the historical data-of-record run unchanged and include its `PROVENANCE.md` and
  complete source snapshot.
- Produce a fresh canonical run from the clean release commit. Verify that its manifest
  records root and submodule revisions, Docker image identity, binary hashes, all source
  copies, workload parameters, and completion marker.

## Handoff contents

- Root release commit/tag and public submodule URLs.
- `simulation-scripts/README.md`, `REPRODUCING.md`, and experiment-local READMEs.
- Tracked reference CSVs, tables, figures, and Chapter 5 run metadata.
- `REFERENCE_DATA.md` and an old/new comparison for any refreshed result set.
- A short note distinguishing the PFC-style abstraction from future CBFC work.
- Any manuscript-specific plotting wrapper or style file that lives outside this
  repository.

The frozen shell wrappers write into ignored `results/generated-runs/<run-id>/`
directories first and only replace reference CSVs after the complete run succeeds. Keep
the generated archive until the refreshed reference data has been reviewed.
