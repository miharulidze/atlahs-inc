#!/usr/bin/env python3
"""Verify the preserved source snapshot for the Chapter 5 data-of-record run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


RUN_ID = "stable-order-ga32-20260808"
RUN_DIR = (
    Path(__file__).resolve().parents[1]
    / "results"
    / "ch5_accumulation"
    / "runs"
    / RUN_ID
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    manifest_path = RUN_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []

    records = manifest.get("source_snapshot", [])
    for record in records:
        relative = Path(record["path"])
        snapshot = RUN_DIR / "source_snapshot" / relative
        if not snapshot.is_file():
            failures.append(f"missing: {relative}")
            continue
        actual = sha256(snapshot)
        if actual != record["sha256"]:
            failures.append(
                f"hash mismatch: {relative} (expected {record['sha256']}, got {actual})"
            )

    if failures:
        for failure in failures:
            print(f"FAIL ch5 source snapshot: {failure}", file=sys.stderr)
        return 1

    print(f"PASS ch5 source snapshot: {len(records)} files match manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
