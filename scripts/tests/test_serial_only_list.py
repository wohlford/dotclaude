"""Keeps the `serial_only` partition honest: the rows that carry the marker and their evidence agree.

`SERIAL_BY_MEASUREMENT` maps each marked row's node id to one sentence naming why it runs alone (the
rule that selected it, or a flake-sweep artifact and its rate). Four rows hold it to the tree: every
marked row has an entry (L1), every entry names a row that exists (L2), the comparison is not
vacuous (L3), and the entries are exactly the marked rows, so a listed row that lost its decorator
fails (L4). It cannot find a row that SHOULD be marked and is not; that discovery is a flake sweep
over the tree.

The nested collects run from the repository that CONTAINS this file, found from `__file__` and
resolved to its PHYSICAL path, never from where the outer pytest was launched: a launch from a
subdirectory or from an unrelated directory must collect the same ids. The resolved path matters:
given a logical path (`/var/folders/...` for the physical `/private/var/folders/...`) pytest could
not relativise the paths it found, fell back to ids relative to the initial argument, and took 2.5 to
4.5 s instead of 0.01 s. The interpreter is `sys.executable` (the very `python3` the audit started
pytest with) and the environment is the outer run's minus exactly what `audit.sh` scrubs from every
runner call, `PYTEST_ADDOPTS`.
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Each entry says why the row runs alone. The rule that selected them: the row asserts an elapsed
# wall-clock time, or passes a literal `timeout` under five seconds to a call, or says in its own
# name that it depends on load around a deadline. Whether a row may leave this mapping is decided by
# a per-row sweep artifact that the operator keeps and names in the commit that removes it; a row
# that flakes in a sweep is added here and never given a retry decorator.
SERIAL_BY_MEASUREMENT: dict[str, str] = {
    "scripts/tests/test_farm_drift_check.py::test_deadline_is_honoured_and_reaches_grandchildren": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_farm_drift_check.py::test_sigkill_ends_a_child_that_ignores_term": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_git_command.py::test_a_long_relative_cd_chain_does_not_make_the_walk_superlinear": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_git_command.py::test_is_git_is_linear_on_a_pathological_word[dollar-brace-run]": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_git_command.py::test_is_git_is_linear_on_a_pathological_word[open-brace-commas]": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_git_command.py::test_is_git_is_linear_on_a_pathological_word[long-prefix-brace-long-suffix]": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_git_command.py::test_is_git_is_linear_on_a_pathological_word[dollar-name-run]": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_git_command_properties.py::test_the_adversarial_reading_ladder_stays_inside_its_wall_clock_budget": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_git_command_properties.py::test_a_small_command_with_many_ambiguous_heredocs_stays_cheap": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_guard_parity.py::test_a_hang_reads_as_timed_out_never_as_a_verdict": (
        "selected by the serial-only rule: relies on a timeout=0.5 deadline firing"
    ),
    "scripts/tests/test_guard_parity.py::test_a_worker_hung_in_preload_is_killed_at_its_deadline_and_reported": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time; relies on a timeout=0.5 deadline firing"
    ),
    "scripts/tests/test_guard_parity.py::test_a_version_probe_that_hangs_is_bounded_and_keyed_as_timed_out": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_guard_parity.py::test_a_hang_is_incomparable_and_the_tolerance_knob_is_honest_about_it": (
        "selected by the serial-only rule: passes a literal --timeout 2 deadline on the command line"
    ),
    "scripts/tests/test_kill_sets.py::test_t04b_baseline_gating": (
        "selected by the serial-only rule: passes a literal `timeout` under five seconds to a call"
    ),
    "scripts/tests/test_kill_sets.py::test_t09_timeout_and_process_group": (
        "selected by the serial-only rule: passes a literal `timeout` under five seconds to a call"
    ),
    "scripts/tests/test_publish_drive.py::test_a_hang_is_INDETERMINATE_not_a_failure_verdict": (
        "selected by the serial-only rule: passes a literal --timeout 1 deadline on the command line"
    ),
    "scripts/tests/test_publish_drive.py::test_an_INDETERMINATE_halt_states_the_engine_was_killed_and_suppresses_the_relay": (
        "selected by the serial-only rule: passes a literal --timeout 1 deadline on the command line"
    ),
    "scripts/tests/test_mutate.py::test_a_hanging_mutant_is_INDETERMINATE_rather_than_caught": (
        "selected by the serial-only rule: relies on a timeout=2 deadline firing"
    ),
    "scripts/tests/test_mutate.py::test_a_hanging_mutant_still_restores_the_subject": (
        "selected by the serial-only rule: relies on a timeout=2 deadline firing"
    ),
    "scripts/tests/test_mutate.py::test_a_hanging_BASELINE_errors_rather_than_hanging": (
        "selected by the serial-only rule: relies on a timeout=2 deadline firing"
    ),
    "scripts/tests/test_mutate.py::test_a_timed_out_suite_does_not_leak_its_CHILDREN": (
        "selected by the serial-only rule: relies on a timeout=3 deadline firing"
    ),
    "scripts/tests/test_mutate.py::test_a_timed_out_row_makes_the_line_LEAD_with_exhausted": (
        "selected by the serial-only rule: relies on a timeout=2 deadline firing"
    ),
    "scripts/tests/test_mutate.py::test_every_row_timing_out_still_leads_with_exhausted_and_never_divides": (
        "selected by the serial-only rule: relies on a timeout=2 deadline firing"
    ),
    "scripts/tests/test_run_hooks.py::test_overrun_is_killed_and_not_a_pass": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
    "scripts/tests/test_run_hooks.py::test_a_late_exit_is_killed_and_an_in_time_exit_stays_allowed_under_load": (
        "selected by the serial-only rule: its own name says it depends on load around a deadline"
    ),
    "scripts/tests/test_run_hooks.py::test_a_hook_that_never_reads_a_large_payload_is_still_stopped_on_time": (
        "selected by the serial-only rule: asserts an elapsed wall-clock time"
    ),
}

# The accepted summary forms, each ending ` in <seconds>s`; a line such as `N tests collected, K
# warnings in ...` is NOT one of them and is refused as unreadable, never guessed at.
_SOME = re.compile(r"^(\d+) tests? collected( \(\d+ deselected\))? in [0-9.]+s")
_PART = re.compile(r"^(\d+)/\d+ tests? collected \(\d+ deselected\) in [0-9.]+s")
_NONE = re.compile(r"^no tests collected( \(\d+ deselected\))? in [0-9.]+s")


def _collect(*extra):
    """(node ids, summary count) of a no-xdist collect rooted at this repository."""
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(ROOT),
            *extra,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 5):
        pytest.fail(
            f"the nested collect could not run: rc={proc.returncode}\n{proc.stdout[-800:]}{proc.stderr[-800:]}"
        )
    lines = proc.stdout.splitlines()
    ids = []
    for line in lines:
        if not line.strip():
            break
        if "::" in line:
            ids.append(line.strip())
    last = next((ln for ln in reversed(lines) if ln.strip()), "")
    m = _SOME.match(last) or _PART.match(last)
    if m:
        count = int(m.group(1))
    elif _NONE.match(last):
        count = 0
    else:
        pytest.fail(f"collect summary unreadable: {last!r}")
    if count != len(ids):
        pytest.fail(
            f"collect summary unreadable: the listing holds {len(ids)} node ids but the summary says {count}"
        )
    return ids, count


@functools.cache
def _state():
    everything, n_all = _collect()
    marked, _ = _collect("-m", "serial_only")
    return everything, marked, n_all


def test_every_serial_only_row_has_recorded_evidence():
    _, marked, _ = _state()
    missing = sorted(set(marked) - set(SERIAL_BY_MEASUREMENT))
    assert not missing, (
        "rows carrying @pytest.mark.serial_only with no SERIAL_BY_MEASUREMENT entry: "
        f"{missing}"
    )


def test_every_serial_by_measurement_entry_names_a_collected_row():
    everything, _, _ = _state()
    stale = sorted(set(SERIAL_BY_MEASUREMENT) - set(everything))
    assert not stale, f"SERIAL_BY_MEASUREMENT entries naming no collected row: {stale}"


def test_the_partition_evidence_is_not_vacuous():
    _, marked, n_all = _state()
    assert n_all >= 1 and n_all >= len(marked), (n_all, len(marked))
    assert SERIAL_BY_MEASUREMENT, "no serial-only evidence is recorded at all"
    assert all(
        isinstance(v, str) and v.strip() for v in SERIAL_BY_MEASUREMENT.values()
    ), SERIAL_BY_MEASUREMENT


def test_the_evidence_names_exactly_the_marked_rows():
    everything, marked, _ = _state()
    listed = set(SERIAL_BY_MEASUREMENT)
    unmarked = sorted((listed & set(everything)) - set(marked))
    extra = sorted(set(marked) - listed)
    assert not unmarked and not extra, (
        "SERIAL_BY_MEASUREMENT and the rows carrying @pytest.mark.serial_only disagree: "
        f"listed but not marked: {unmarked}; marked but not listed: {extra}"
    )
