#!/usr/bin/env python3
"""Mutation-test the stale-copy guard in skills/sync-docs/sync_docs.py.

Run on demand: `python3 scripts/tests/mutate_sync_docs_stale_guard.py`. Deliberately NOT named
`test_*`, so pytest never collects it.

This guard exists because the failure it prevents has **no symptom** — a repo's regions rendered
by a different copy of this tool yield a plausible-looking table, not an error. That is also what
makes its tests hard to trust: they pass whether or not the guard is load-bearing, since the
scenario is one nobody encounters by accident. Each row below removes one clause and requires the
suite to notice.

The rows are chosen against the two ways this guard could rot into decoration: refusing too little
(the comparison stops covering the package, or stops firing at all) and refusing too much (it
blocks a repo running its own copy, which is the normal case and the fix the message prescribes).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "skills" / "sync-docs" / "sync_docs.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    str(REPO / "skills" / "sync-docs" / "tests" / "test_stale_copy_guard.py"),
    "-q",
]

MUTATIONS = [
    mutate.Mutation(
        "the guard never refuses — the write proceeds under whichever copy happens to be "
        "running, which is the silent-corruption direction it exists to close",
        "    if stale:\n        print(stale, file=sys.stderr)\n        return 2",
        "    if False:\n        print(stale, file=sys.stderr)\n        return 2",
    ),
    mutate.Mutation(
        "the comparison covers only the ENTRY POINT, so a difference in handlers.py — where "
        "`filter=` actually lives, and exactly the historical hazard — reads as identical",
        '    for path in sorted(pkg_dir.glob("*.py")):',
        '    for path in sorted(pkg_dir.glob("sync_docs.py")):',
    ),
    mutate.Mutation(
        "file CONTENT stops being hashed, so two packages with the same filenames and "
        "different code compare equal",
        "        parts.append(hashlib.sha256(path.read_bytes()).digest())",
        "        parts.append(b'')",
    ),
    mutate.Mutation(
        "refusing TOO MUCH — two identical packages in different directories are blocked, so "
        "the digest comparison stops being the thing that lets a legitimate run through",
        "    if _package_digest(scope_pkg) == _package_digest(running):\n        return None",
        "    if False:\n        return None",
    ),
    mutate.Mutation(
        "refusing TOO MUCH — an ordinary consumer repo with no sync-docs of its own is "
        "blocked, so the tool stops working everywhere except this repo",
        "    if not scope_pkg.is_dir():",
        "    if False:",
    ),
]


def main() -> int:
    baseline = subprocess.run(SUITE, capture_output=True, text=True)
    print(f"pre-flight: rc={baseline.returncode}  {baseline.stdout.strip()[-60:]}")
    report = mutate.run(SUBJECT, SUITE, MUTATIONS)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
