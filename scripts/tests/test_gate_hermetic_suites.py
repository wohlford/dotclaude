"""Pins which test suites execute the gated tools and how each stays off the real gate state.

The manifest scripts/tests/gate_hermetic_suites.txt classifies every suite that names the gated
tools. The population is derived from the working tree with a filesystem glob (never git ls-files,
which cannot see a file written minutes ago), floored on the three suites that must always be
classified, and reported by file name.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "scripts" / "tests"
MANIFEST = TESTS / "gate_hermetic_suites.txt"
SECTIONS = ("helper", "self-hermetic", "mentions-only")
FLOOR = (
    "scripts/tests/test_audit.sh",
    "scripts/tests/test_run_long.sh",
    "scripts/tests/test_heavy_gate.sh",
)
TOOLS = re.compile(r"run-long\.sh|audit\.sh")


def read_manifest() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {name: {} for name in SECTIONS}
    current = None
    for raw in MANIFEST.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = re.fullmatch(r"\[([a-z-]+)\]", line)
        if header:
            assert header.group(1) in SECTIONS, f"unknown manifest section {line}"
            current = header.group(1)
            continue
        assert current, f"manifest entry before any section: {line}"
        path, _, reason = line.partition("#")
        out[current][path.strip()] = reason.strip()
    return out


def candidates() -> list[str]:
    files = sorted(TESTS.glob("test_*.sh")) + sorted(TESTS.glob("test_*.py"))
    return [str(p.relative_to(ROOT)) for p in files]


def text(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_the_floor_suites_are_classified_as_gated():
    m = read_manifest()
    gated = set(m["helper"]) | set(m["self-hermetic"])
    missing = [f for f in FLOOR if f not in gated]
    assert not missing, f"floor suites not in [helper] or [self-hermetic]: {missing}"


# A top-level (column 0) CALL of the helper: not a comment, not a definition, not indented inside a
# function or row.
HELPER_CALL = re.compile(r'^hermetic_gate_env "\$\w+"\s*\|\|\s*exit\b')
# The first line that can EXECUTE the engine: a non-comment line invoking "$engine" with its stderr
# redirected (every launch in the helper suites captures it). Reads of the engine's text (sed -n ...
# "$engine") carry no redirect, so they do not count.
ENGINE_RUN = re.compile(r'"\$engine".*2>')


def first_line(body: str, pattern: re.Pattern[str]) -> int | None:
    for n, ln in enumerate(body.splitlines(), 1):
        if not ln.lstrip().startswith("#") and pattern.search(ln):
            return n
    return None


def helper_problem(body: str) -> str | None:
    call = first_line(body, HELPER_CALL)
    if call is None:
        return 'no column-0 call of hermetic_gate_env "$var" || exit'
    run = first_line(body, ENGINE_RUN)
    if run is None:
        return "no engine invocation found: the ordering cannot be judged"
    if call >= run:
        return f"the call (line {call}) is not before the first engine run (line {run})"
    return None


def self_hermetic_problem(body: str) -> str | None:
    folded = body.replace("\\\n", " ")
    joined = " ".join(ln for ln in folded.splitlines() if re.match(r"unset\b", ln))
    if "HEAVY_GATE_HELD" not in joined or "GATE_WIRING" not in joined:
        return "no column-0 unset naming both HEAVY_GATE_HELD and GATE_WIRING"
    return None


def test_every_helper_suite_calls_the_helper():
    helpers = read_manifest()["helper"]
    assert helpers, "[helper] is empty"
    bad = {f: helper_problem(text(f)) for f in helpers if helper_problem(text(f))}
    assert not bad, f"[helper] suites with a missing or late helper call: {bad}"


def test_every_self_hermetic_suite_unsets_the_seams():
    selfs = read_manifest()["self-hermetic"]
    assert selfs, "[self-hermetic] is empty"
    bad = {
        f: self_hermetic_problem(text(f))
        for f in selfs
        if self_hermetic_problem(text(f))
    }
    assert not bad, (
        f"[self-hermetic] suites lacking a top-level unset of both seams: {bad}"
    )


def test_every_suite_naming_the_tools_is_classified():
    m = read_manifest()
    known = set().union(*m.values())
    found = candidates()
    assert found, "the glob found no test suites"
    naming = [f for f in found if TOOLS.search(text(f))]
    assert naming, "no suite names the gated tools: the derivation read nothing"
    unclassified = [f for f in naming if f not in known]
    assert not unclassified, (
        f"unclassified suites naming the gated tools: {unclassified}"
    )


def test_every_manifest_entry_exists_and_mentions_only_carry_reasons():
    m = read_manifest()
    for section, entries in m.items():
        for f, reason in entries.items():
            assert (ROOT / f).is_file(), f"[{section}] names a missing file: {f}"
            if section == "mentions-only":
                assert reason, f"[mentions-only] entry without a reason: {f}"
