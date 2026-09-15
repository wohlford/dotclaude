#!/usr/bin/env python3
# Script: mutate_git_command_reserved_cd.py
# Purpose: Mutation campaign for the reserved-word-cd fix (fix/reserved-word-cd) — prove the ONE
#          branch Tasks 1-5 added to `_cd_command_position` in scripts/lib/git_command.py is
#          pinned BY NAME, not merely that the suite goes red somewhere
# Usage: scripts/tests/mutate_git_command_reserved_cd.py
"""Mutate the reserved-word-cd branch and require a pytest-resident row or test to catch it, BY
NAME.

A green suite at branch tip is also exactly what reverting every commit on this branch would
produce. This campaign is the measurement that tells those apart: one mutation per wrong answer
the branch's own review history considered, each paired with the specific row or test it is
REQUIRED to kill.

SUBJECT = scripts/lib/git_command.py, specifically the branch

    if prev in RESERVED_WORDS:
        return None

placed immediately before `_cd_command_position`'s own `return False`. SUITE runs both
`test_guard_corpus.py` (the derived reserved-word/cd-family preserve matrix and the
`reserved_cd_if_then_from_other` / `reserved_cd_brace_from_other` fail-open rows) and
`test_git_command.py` (`test_a_cd_after_a_reserved_word_is_unresolvable` and its CONTROL,
`test_a_cd_after_a_non_boundary_word_is_untouched`) — the two places this branch's mechanism is
pinned.

Built on the pattern of `mutate_git_command_eval_wrappers.py`: a `mutate.run()` scoring pass,
then `_verify_named_rows`, which re-applies each mutation, captures FULL suite output, and
requires the named label/test to be IN it. `mutate.run()`'s `Outcome.detail` keeps only the
suite's last stdout line per caught mutant, so "the row I intended to kill is among the failures"
cannot be read from its report alone — a mutation can turn the suite red for some OTHER reason and
still score CAUGHT.

## Mutate the branch, never the shared constant

`test_guard_corpus.py` carries a MODULE-LEVEL `assert set(_RESERVED_WORD_WRAP) ==
new_gitcmd.RESERVED_WORDS`. Any mutation touching the `RESERVED_WORDS` frozenset itself (adding,
removing, or renaming a member) raises at IMPORT time, so pytest's collection is interrupted, NO
test in either file runs, every mutation scores CAUGHT for the wrong reason (an import error, not
the intended failure), and the run can never reach `FINAL: PASS` on a correct fix. Mutating the
frozenset would also break `_git_starts_command`'s own use of it simultaneously, making any kill
unattributable to this branch's mechanism specifically. Every mutation below therefore edits the
`if prev in RESERVED_WORDS:` / `return None` BRANCH, never the frozenset it tests membership
against.

## The one anchor, five mutations

`return None` and `return False` each occur several times in this module (six and three
occurrences respectively), so no bare `return None` or `return False` is a safe anchor alone. The
combined two-line anchor

    if prev in RESERVED_WORDS:
        return None

is unique in the file (`if prev in RESERVED_WORDS:` occurs exactly once) and is shared, verbatim,
across all five mutations below with a different `new` each time — the same pattern
`mutate_git_command_eval_wrappers.py` uses for `wrapper_cd_tracked`/`wrapper_cd_ignored`.
`mutate.run()` applies each mutation independently against the pristine original and restores
between rows, so five rows sharing one `old` is not a collision.

## `reserved_cd_tracked` is load-bearing

It is the ONLY mutation that distinguishes `None` (unresolvable) from `True` (tracked exactly).
Turning the branch's `return None` into `break` falls through to the function's own closing line,
`return None if through_wrapper else True` — since no wrapper was stepped over on this walk,
`through_wrapper` is still `False`, so the classification becomes `True`: TRACKED, not
unresolvable. Its kill must therefore be a `cd`/`pushd` row, never a `popd` one: at the `popd` call
site in `_walk_context` the guarding condition is `cd_pos is not False`, which `None` AND `True`
BOTH satisfy, so a `popd` row cannot tell the two apart. `reserved_bang_cd_preserve` is a
`_reserved_word_preserve_rows`-derived `cd` row (word `!`, cd-family member `cd`) and is the
correct choice: under `break`, `! cd /other` resolves to `/other` and stays TRACKED there rather
than going unresolvable, so a push judged after it is wrongly evaluated against a cwd the shell
never actually left dark.

## Renamed-row protection

A renamed corpus label or unit test would make a `MUST_KILL` entry point at nothing, and the row
check would then read the mutation as SURVIVED for the right *symptom* (label absent from suite
output) but the wrong *cause* — a stale mutation-list entry, not a weak suite. So before running
anything, this module asserts every `MUST_KILL` value that names a *corpus row* is either a member
of `REQUIRED_RESERVED_CD_AXIS_LABELS` or a label the derived preserve matrix
(`_reserved_word_preserve_rows`) actually produces (loaded by importing `test_guard_corpus.py` BY
PATH — its own module-level code is limited to imports, a `sys.path.insert`, one `import
git_command`, plain constant assignments, and one `assert` over `_RESERVED_WORD_WRAP`; none of it
runs a subprocess, writes a fixture, or otherwise reaches outside plain Python, so importing it
here carries no side effect a pytest collection run would not also produce), and that the two
`MUST_KILL` values naming *unit tests* occur verbatim in `test_git_command.py`'s text.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "lib" / "git_command.py"
TEST_GUARD_CORPUS = REPO / "scripts" / "tests" / "test_guard_corpus.py"
TEST_GIT_COMMAND = REPO / "scripts" / "tests" / "test_git_command.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "scripts/tests/test_guard_corpus.py",
    "scripts/tests/test_git_command.py",
    "-q",
    "-p",
    "no:cacheprovider",
]

# The two MUST_KILL values naming unit TESTS rather than corpus row LABELS — carved out of the
# "is this a live corpus label" check below, which only makes sense for corpus rows.
_UNIT_TEST_NAMES = frozenset(
    {
        "test_a_cd_after_a_reserved_word_is_unresolvable",
        "test_a_cd_after_a_non_boundary_word_is_untouched",
    }
)

# ---------------------------------------------------------------------------------------------
# The ONE branch this campaign mutates, shared across all five rows:
#
#     if prev in RESERVED_WORDS:
#         return None
# ---------------------------------------------------------------------------------------------
_BRANCH_ANCHOR = "        if prev in RESERVED_WORDS:\n            return None"

MUTATIONS = [
    # The branch made unreachable outright: a cd/pushd/popd right after a reserved word falls
    # through to the old `return False` (ARGUMENT) instead of becoming unresolvable — the exact
    # regression `test_a_cd_after_a_reserved_word_is_unresolvable` exists to pin, across every
    # RESERVED_WORDS member x {cd, pushd, popd}.
    mutate.Mutation(
        "reserved_cd_ignored",
        _BRANCH_ANCHOR,
        "        if False:\n            return None",
    ),
    # The branch still fires, but its `return None` becomes `break` — the walk falls through to
    # `return None if through_wrapper else True`, and since no wrapper was stepped over,
    # `through_wrapper` is still False: the classification becomes True (TRACKED), not None
    # (unresolvable). The ONLY mutation distinguishing None from True; see the module docstring
    # for why its kill must be a cd/pushd row, never a popd one.
    mutate.Mutation(
        "reserved_cd_tracked",
        _BRANCH_ANCHOR,
        "        if prev in RESERVED_WORDS:\n            break",
    ),
    # The condition widened to also cover two BLOCK-ENDER words that are deliberately excluded
    # from RESERVED_WORDS (`fi`, `done` close a construct, they do not open one) — a cd after
    # either would then be wrongly made unresolvable, over-blocking past what the fix's own
    # CONTROL test guards.
    mutate.Mutation(
        "reserved_cd_covers_block_enders",
        _BRANCH_ANCHOR,
        '        if prev in RESERVED_WORDS or prev in ("fi", "done"):\n            return None',
    ),
    # The condition narrowed to exclude "if" specifically — a cd right after `if` would fall
    # through to `return False` (ARGUMENT) again, reopening exactly the fail-open
    # `reserved_cd_if_then_from_other` exists to close for that one reserved word.
    mutate.Mutation(
        "drop_if_from_the_branch",
        _BRANCH_ANCHOR,
        '        if prev in RESERVED_WORDS and prev != "if":\n            return None',
    ),
    # The condition narrowed to exclude "{" specifically — the brace-group sibling of the same
    # hole, pinned by `reserved_cd_brace_from_other`.
    mutate.Mutation(
        "drop_brace_from_the_branch",
        _BRANCH_ANCHOR,
        '        if prev in RESERVED_WORDS and prev != "{":\n            return None',
    ),
]

MUST_KILL = {
    "reserved_cd_ignored": "test_a_cd_after_a_reserved_word_is_unresolvable",
    "reserved_cd_tracked": "reserved_bang_cd_preserve",
    "reserved_cd_covers_block_enders": "test_a_cd_after_a_non_boundary_word_is_untouched",
    "drop_if_from_the_branch": "reserved_cd_if_then_from_other",
    "drop_brace_from_the_branch": "reserved_cd_brace_from_other",
}


def _load_corpus_module():
    """Import `test_guard_corpus.py` BY PATH — see the module docstring's renamed-row-protection
    section for why this is side-effect-free."""
    spec = importlib.util.spec_from_file_location(
        "mutate_git_command_reserved_cd_corpus", TEST_GUARD_CORPUS
    )
    module = importlib.util.module_from_spec(spec)
    # Must be registered in sys.modules BEFORE exec_module: test_guard_corpus.py defines
    # `@dataclass(frozen=True)` classes, and dataclass's own type-resolution looks the module up
    # by name via `sys.modules.get(cls.__module__)` while its body is still executing — an
    # unregistered module makes that lookup return None and raise `AttributeError` on `.__dict__`,
    # not on anything this campaign's own logic does.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _live_corpus_labels(module) -> set[str]:
    """Every corpus label this campaign may legally name in `MUST_KILL`.

    `REQUIRED_RESERVED_CD_AXIS_LABELS` covers the two fail-open rows added directly for this
    branch. `reserved_bang_cd_preserve` is not in any REQUIRED_* set — it is one row of the
    derived reserved-word/cd-family preserve matrix, generated by `_reserved_word_preserve_rows`
    rather than hand-listed — so it is verified by actually calling that function (a plain string
    builder over a Path, no filesystem access; the same function `rows()` calls with the
    sandbox's real "other" repo) and checking the label is among what it produces.
    """
    required = set(module.REQUIRED_RESERVED_CD_AXIS_LABELS)
    preserve_labels = {
        r.label for r in module._reserved_word_preserve_rows(Path("/other"))
    }
    return required | preserve_labels


def _assert_must_kill_names_are_live(module) -> None:
    """Fail loudly, before any mutation runs, if a `MUST_KILL` entry points at a renamed row.

    Without this, a renamed corpus label or unit test makes the row check below read a mutation
    as SURVIVED for the wrong reason -- "no such label in the output" is byte-identical whether
    the mechanism is truly unguarded or the row was merely renamed out from under this campaign.
    """
    live_labels = _live_corpus_labels(module)
    corpus_labels = {v for v in MUST_KILL.values() if v not in _UNIT_TEST_NAMES}
    missing = corpus_labels - live_labels
    if missing:
        raise RuntimeError(
            f"{len(missing)} MUST_KILL label(s) not among live corpus labels "
            f"(renamed or removed corpus row?): {sorted(missing)}"
        )
    unit_text = TEST_GIT_COMMAND.read_text()
    missing_tests = {name for name in _UNIT_TEST_NAMES if name not in unit_text}
    if missing_tests:
        raise RuntimeError(
            f"unit test name(s) not found in {TEST_GIT_COMMAND.name} "
            f"(renamed test?): {sorted(missing_tests)}"
        )


def _verify_named_rows(subject: Path, mutations, must_kill: dict) -> list[str]:
    """The part `mutate.run()` cannot do: confirm the REQUIRED row/test name is in the failure
    text.

    `mutate.run()`'s `Outcome.detail` keeps only the suite's LAST stdout line per caught mutant,
    so "the row I intended to kill is among the failures" cannot be read from its report — a
    mutation can turn the suite red for some OTHER reason and still score CAUGHT. This re-applies
    each mutation, captures FULL pytest stdout+stderr, and asserts the required name is actually
    IN it. Restore is verified by content, independent of `mutate.run()`'s own restore pass.
    """
    original = subject.read_text()
    problems = []
    for m in mutations:
        must_contain = must_kill[m.label]
        count = original.count(m.old)
        if count != 1:
            problems.append(
                f"{m.label}: anchor appears {count}x in {subject.name}, need exactly 1 "
                "-- this is a campaign defect (a stale anchor), not a weak suite"
            )
            continue
        mutated = original.replace(m.old, m.new, 1)
        subject.write_text(mutated)
        for cache in subject.parent.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
        try:
            proc = subprocess.run(
                SUITE, cwd=REPO, capture_output=True, text=True, timeout=300
            )
        finally:
            subject.write_text(original)
        after = subject.read_text()
        if after != original:
            problems.append(
                f"{m.label}: subject NOT restored after the row-presence pass on {subject.name}"
            )
            continue
        output = proc.stdout + proc.stderr
        if must_contain not in output:
            problems.append(
                f"{m.label}: suite output does NOT contain required name "
                f"{must_contain!r} -- the mutation reddened the suite for some OTHER reason"
            )
        print(
            f"row-check  {m.label:35s} row={must_contain!r:55s} "
            f"{'FOUND' if must_contain in output else 'MISSING'}"
        )
    final = subject.read_text()
    if final != original:
        problems.append(f"{subject.name} not restored at end of row-presence pass")
    return problems


def main() -> int:
    corpus_module = _load_corpus_module()
    _assert_must_kill_names_are_live(corpus_module)

    print("=" * 88)
    print("PASS 1: mutate.run() scoring against", SUBJECT.name)
    print("=" * 88)
    report1 = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO)
    print(report1.text)

    print()
    print("=" * 88)
    print("PASS 2: named-row verification against", SUBJECT.name)
    print("=" * 88)
    problems = _verify_named_rows(SUBJECT, MUTATIONS, MUST_KILL)

    print()
    print("=" * 88)
    ok = report1.status == "PASS" and not problems
    if problems:
        print(f"ROW-CHECK FAIL: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
    print(
        f"FINAL: {'PASS' if ok else 'FAIL'} "
        f"pass1={report1.verdict} row_check_problems={len(problems)}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
