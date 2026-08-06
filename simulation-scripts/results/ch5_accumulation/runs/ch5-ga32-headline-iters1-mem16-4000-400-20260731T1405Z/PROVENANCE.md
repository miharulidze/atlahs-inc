# Provenance of this historical run

This directory is the Chapter 5 thesis data of record. Its result CSV, audits, summary,
and generated thesis assets remain valid inputs for replotting and claim verification.

The original run was executed in a workspace where Git metadata was unavailable and no
Docker image ID was supplied. Consequently, `manifest.json` records the three repository
revisions as `unavailable` and the image as `not_provided`. It does record hashes for the
simulator, compiler, congestion-control configuration, and ten source inputs.

All ten source inputs are retained under `source_snapshot/`. Their SHA-256 values were
rechecked during public-artifact preparation on 2026-08-06, and every file matches its
entry in `manifest.json`. The snapshot is intentionally immutable, so commands and paths
inside it may differ from the cleaned current documentation.

The exact executed sources and binaries are therefore identifiable. What is missing is
the original repository ancestry and container/base-package identity, so the artifact
cannot independently prove how those inputs were assembled or recreate the original
runtime environment byte for byte. The tracked outputs can still be independently
re-analysed from their complete recorded input-source snapshot.

For a publication release, keep this directory unchanged and run the same frozen command
again from a clean, tagged public integration commit. The replacement run should record
reachable root and submodule revisions, a Docker image ID, all ten source copies, and the
same binary/configuration hashes or an explicitly documented change.
