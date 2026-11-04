"""Keeps scripts/tests/serial_shell_suites.txt honest: the manifest the shell-suite pool of audit.sh reads.

The file has three sections. `[serial]` lists suites that must not overlap anything else (each with a `#`
evidence reference), `[weights]` gives each suite's serial per-suite seconds (measured serially) for the pool's longest-first order, and `[floor]`
names suites whose absence from the tracked listing must fail the audit. The rows read the file through the
engine's own `shell_manifest_section`, never through a second copy of the grammar, and compare it with
`git ls-files`. Every row but the control calls `manifest_path()`, which fails when the file is absent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "skills" / "audit" / "audit.sh"
MANIFEST = ROOT / "scripts" / "tests" / "serial_shell_suites.txt"


def manifest_path() -> Path:
    assert MANIFEST.is_file(), "the manifest does not exist: %s" % MANIFEST
    return MANIFEST


def tracked_suites() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", "scripts/tests/test_*.sh"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.rsplit("/", 1)[-1] for line in out.splitlines() if line]


def section(path: Path, name: str) -> list[str]:
    got = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; shell_manifest_section "$2" "$3"',
            "_",
            str(ENGINE),
            str(path),
            name,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in got.stdout.splitlines() if line.strip()]


def missing_evidence(rows: list[str]) -> list[str]:
    bad = []
    for row in rows:
        name, _, evidence = row.partition("#")
        if not name.split() or not evidence.strip():
            bad.append(row)
    return bad


def test_manifest_exists_with_the_three_sections() -> None:
    text = manifest_path().read_text()
    for name in ("serial", "weights", "floor"):
        assert text.count("\n[%s]\n" % name) == 1, name


def test_every_serial_member_exists_and_carries_evidence() -> None:
    rows = section(manifest_path(), "serial")
    names = [row.split()[0] for row in rows]
    assert set(names) <= set(tracked_suites()), names
    assert missing_evidence(rows) == []
    assert len(rows) - len(missing_evidence(rows)) == len(names)


def test_every_floor_name_is_a_tracked_suite() -> None:
    names = [row.split()[0] for row in section(manifest_path(), "floor")]
    unknown = sorted(set(names) - set(tracked_suites()))
    assert unknown == [], unknown


def test_the_floor_is_not_vacuous() -> None:
    names = [row.split()[0] for row in section(manifest_path(), "floor")]
    assert len(names) >= 1
    assert len(names) == len(set(names)), "a floor name is listed twice"


def test_weights_name_tracked_suites_with_positive_seconds() -> None:
    seen = []
    for row in section(manifest_path(), "weights"):
        fields = row.split()
        assert len(fields) >= 2 and fields[1].isdigit() and int(fields[1]) > 0, row
        assert fields[0] in tracked_suites(), row
        seen.append(fields[0])
    assert len(seen) == len(set(seen)), "a suite is weighted twice"


def test_the_evidence_check_detects_a_member_without_one(tmp_path: Path) -> None:
    synthetic = tmp_path / "m.txt"
    synthetic.write_text(
        "[serial]\ntest_a.sh  # has evidence\ntest_b.sh\ntest_c.sh  #\n"
        "[weights]\n[floor]\n"
    )
    rows = section(synthetic, "serial")
    assert missing_evidence(rows) == ["test_b.sh", "test_c.sh  #"]
