#!/usr/bin/env python3
# Script: mutate_git_command_reserved_arg.py
# Purpose: Mutation campaign for the reserved-word-argument-position fix — prove the
#          argument-segment growth `_walk_context` added in scripts/lib/git_command.py is pinned
#          BY NAME, not merely that the suite goes red somewhere
# Usage: scripts/tests/mutate_git_command_reserved_arg.py
"""Mutate the reserved-word-argument-position fix and require a pytest-resident row or test to
catch it, BY NAME.

A green suite is also exactly what the suite would show with this fix's mechanism reverted, if
nothing pinned that mechanism BY NAME. This campaign is the measurement that tells those apart:
one mutation per wrong answer the fix comment on `_walk_context`'s argument-segment scan in
git_command.py records as considered and rejected, each paired with the specific row or test it is
REQUIRED to kill.

SUBJECT = scripts/lib/git_command.py, specifically the branch in `_walk_context`'s
argument-segment scan:

    resume = k
    while k < n and not is_op(tokens[k]):
        seg.append(tokens[k])
        k += 1

and the descent/resume that follow it (`_descend(tokens[i:resume], ...)`, `i = resume`), plus one
older line in `starts_command`:

    if is_op(prev) or prev in reserved_words:
        return True

Before this fix, the argument-segment scan stopped at the first `git` token reached from behind a
reserved word (`git <push> origin main -o then HEAD:refs/heads/x/git dev` recorded argv
`["origin", "main", "-o", "then"]` and lost `dev`) — the scan was treating `starts_command`'s
command-position reading of `RESERVED_WORDS` as if it applied inside an argument list too, where
bash does not recognise a reserved word as a keyword at all. The fix grows the argument list past
that phantom `git` while still resuming the outer walk there, so the phantom invocation is kept
(bash might run it if an operator was missed) AND the true argument list is not truncated by it.

SUITE runs `test_guard_corpus.py` (the derived reserved-word/argument-axis rows,
`REQUIRED_RESERVED_ARG_AXIS_LABELS`) and `test_git_command.py` (the chained-phantom and
operator-boundary unit tests) — the two places this fix's mechanism is pinned.

Built on the pattern of `mutate_git_command_reserved_cd.py`: a `mutate.run()` scoring pass, then
`_verify_named_rows`, which re-applies each mutation, captures FULL suite output, and requires the
named label/test to be IN it. `mutate.run()`'s `Outcome.detail` keeps only the suite's last stdout
line per caught mutant, so "the row I intended to kill is among the failures" cannot be read from
its report alone — a mutation can turn the suite red for some OTHER reason and still score CAUGHT.

## Mutate the code, never RESERVED_WORDS

`test_guard_corpus.py` carries a MODULE-LEVEL `assert set(_RESERVED_WORD_WRAP) ==
new_gitcmd.RESERVED_WORDS`. Any mutation touching the `RESERVED_WORDS` frozenset itself (adding,
removing, or renaming a member) raises at IMPORT time, so pytest's collection is interrupted, NO
test in either file runs, every mutation scores CAUGHT for the wrong reason (an import error, not
the intended failure), and the run can never reach `FINAL: PASS` on a correct fix. Every mutation
below therefore edits the scan/resume CODE, never the frozenset it tests membership against.

## Six mutations, four anchors

Three of the six mutations share the `resume`/grown-scan anchor (`_GROW`, unique in the file), each
with a different `new`: `argument_list_cut_at_phantom`, `grown_list_stops_at_next_phantom`, and
`grown_list_ignores_operators`. One mutation, `phantom_dropped`, uses the `i = resume\n    continue`
anchor. One mutation, `reserved_word_only_in_command_position`, uses the older `starts_command`
boundary line. One mutation, `descent_covers_grown_tail`, uses the `_descend(tokens[i:resume], …)`
anchor. `mutate.run()` applies each mutation independently against the pristine original and
restores between rows, so three rows sharing one `old` is not a collision.

- `argument_list_cut_at_phantom` reverts the fix outright: the grown scan never runs (`while
  False:`), so the argument list stops at the phantom `git` exactly as it did before this fix —
  killed by `reserved_arg_option_value_hides_dev_refspec`, the corpus row built from the real
  fail-open this fix closed (`-o` swallowing `then`, an `/git` refspec read as a new git command,
  and `dev` lost from the argv).

- `grown_list_stops_at_next_phantom` lets the grown scan stop at a SECOND nested `git`, using a bare
  `is_git` check guarded by `k > resume` — not the pre-fix scan's reserved-word test
  (`_git_starts_command`), which this mutation does not call at all. `k > resume` guards it: the
  grown scan always starts ON the nested `git` recorded by the outer scan, so a bare
  `not is_git(...)` check with no guard would stop at once and duplicate `argument_list_cut_at_phantom`
  above — the `k > resume` term is what makes this mutation distinct, by letting the FIRST phantom
  grow correctly and only re-truncating at a SECOND one. Killed by
  `test_chained_phantoms_each_see_the_rest_of_the_argv`, which chains three `git` invocations and
  asserts each one's argv runs to the end of the line.

- `grown_list_ignores_operators` drops the grown scan's `not is_op(tokens[k])` term, so the
  argument list swallows a real control operator and everything after it — the outer command's argv
  absorbs a command that should have started fresh after `&&`. Killed by
  `test_an_operator_still_ends_the_argument_list`, which asserts a `&&`-separated second invocation
  is recorded on its own, not folded into the first one's argv.

- `phantom_dropped` changes the walk's resume point from `i = resume` to `i = k`, jumping past the
  phantom invocation's own tokens instead of resuming the outer walk there. This is the rejected
  "drop the phantom" design the fix's own comment argues against: dropping it is exact only while
  `is_op` never misses a real operator, and a missed operator would turn a dropped phantom into a
  real push hidden in another command's argv. Killed by `reserved_arg_phantom_push_kept`, the
  corpus row asserting the phantom invocation is still recorded (a DECIDED over-block) even though
  bash runs nothing there in that row's own case.

- `reserved_word_only_in_command_position` changes `starts_command` so a reserved word only counts
  as a command-position boundary when it is ITSELF in command position (recursing via
  `starts_command` again), rather than unconditionally. This is the OTHER rejected design the fix
  comment in git_command.py records — measured to stop detecting `for NAME do`, `function NAME {` and
  `coproc NAME {`, each a push bash can run from inside a script, function, or coprocess where the
  reserved word is not itself in command position. Killed by `reserved_arg_for_name_do_push`, the
  corpus row built from exactly that `for x do <push>; done` shape.

- `descent_covers_grown_tail` widens the descend call from `tokens[i:resume]` to `tokens[i:k]`, so a
  nested context sitting in the GROWN part of the argument list (past `resume`) is descended by the
  outer invocation's own call instead of being left for the nested invocation's own walk to reach
  when the main loop resumes there. This reorders the recorded invocations: a nested context in the
  grown tail is walked BEFORE the outer record is appended, instead of after it. Killed by
  `test_the_grown_tail_is_descended_after_the_outer_record`, which pins the exact three-record order
  a chained `git log … then git log "$(git <push> …)" y` produces.

## Renamed-row protection

A renamed corpus label or unit test would make a `MUST_KILL` entry point at nothing, and the row
check would then read the mutation as SURVIVED for the right *symptom* (label absent from suite
output) but the wrong *cause* — a stale mutation-list entry, not a weak suite. So before running
anything, this module asserts every `MUST_KILL` value that names a *corpus row* is a member of
`REQUIRED_RESERVED_ARG_AXIS_LABELS` (loaded by importing `test_guard_corpus.py` BY PATH — its own
module-level code is limited to imports, a `sys.path.insert`, one `import git_command`, function
and dataclass definitions, plain constant assignments, one `_p(...)` call assignment, and one
`assert` over `_RESERVED_WORD_WRAP`; none of it runs a subprocess, writes a fixture, or otherwise
reaches outside plain Python, so importing it here carries no side effect a pytest collection run
would not also produce), and that the three `MUST_KILL` values naming *unit tests* occur verbatim in
`test_git_command.py`'s text.

Before any mutation runs, this module also asserts each of the four anchors above occurs EXACTLY
once in `git_command.py` — the existing `_verify_named_rows` already reports a non-1 count as a
campaign defect per-mutation, but a stale anchor should fail before the ~minutes-long scoring pass,
not partway through it.
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

# The three MUST_KILL values naming unit TESTS rather than corpus row LABELS — carved out of the
# "is this a live corpus label" check below, which only makes sense for corpus rows.
_UNIT_TEST_NAMES = frozenset(
    {
        "test_chained_phantoms_each_see_the_rest_of_the_argv",
        "test_an_operator_still_ends_the_argument_list",
        "test_the_grown_tail_is_descended_after_the_outer_record",
    }
)

# ---------------------------------------------------------------------------------------------
# The four anchors this campaign mutates. `_GROW` is shared by three of the six rows; the other
# three rows each use one of the remaining three anchors, one row per anchor.
# ---------------------------------------------------------------------------------------------
_ANCHORS = [
    "            resume = k\n            while k < n and not is_op(tokens[k]):\n",
    "            i = resume\n            continue",
    "        if is_op(prev) or prev in reserved_words:\n            return True\n",
    "            _descend(tokens[i:resume], cwd_state)",
]

_GROW = "            resume = k\n            while k < n and not is_op(tokens[k]):\n"

MUTATIONS = [
    # The fix reverted: the argument list stops at the nested git again.
    mutate.Mutation(
        "argument_list_cut_at_phantom",
        _GROW,
        "            resume = k\n            while False:\n",
    ),
    # The grown scan stops at the NEXT phantom, using a bare `is_git` check (not the pre-fix scan's
    # reserved-word test): a second nested git cuts the outer list again. `k > resume` is required --
    # the grown scan always starts ON the nested git, so a bare `not is_git` with no guard would stop
    # at once and duplicate the mutation above.
    mutate.Mutation(
        "grown_list_stops_at_next_phantom",
        _GROW,
        "            resume = k\n"
        "            while k < n and not is_op(tokens[k]) and not (k > resume and is_git(tokens[k])):\n",
    ),
    # The grown scan ignores operators: the outer list swallows the next command outright.
    mutate.Mutation(
        "grown_list_ignores_operators",
        _GROW,
        "            resume = k\n            while k < n:\n",
    ),
    # The phantom dropped: the walk jumps past the nested git instead of resuming at it.
    mutate.Mutation(
        "phantom_dropped",
        "            i = resume\n            continue",
        "            i = k\n            continue",
    ),
    # The rejected design: a reserved word opens a command only if it is itself in command position.
    mutate.Mutation(
        "reserved_word_only_in_command_position",
        "        if is_op(prev) or prev in reserved_words:\n            return True\n",
        "        if is_op(prev):\n            return True\n"
        "        if prev in reserved_words:\n"
        "            return starts_command(tokens, j, reserved_words, extra_wrappers)\n",
    ),
    # The descend call widened from `resume` to `k`: a nested context in the grown tail is now
    # descended by the outer invocation's own call, before the outer record is appended, instead of
    # being left for the nested invocation's own walk to reach when the main loop resumes there.
    mutate.Mutation(
        "descent_covers_grown_tail",
        "            _descend(tokens[i:resume], cwd_state)",
        "            _descend(tokens[i:k], cwd_state)",
    ),
]

MUST_KILL = {
    "argument_list_cut_at_phantom": "reserved_arg_option_value_hides_dev_refspec",
    "grown_list_stops_at_next_phantom": "test_chained_phantoms_each_see_the_rest_of_the_argv",
    "grown_list_ignores_operators": "test_an_operator_still_ends_the_argument_list",
    "phantom_dropped": "reserved_arg_phantom_push_kept",
    "reserved_word_only_in_command_position": "reserved_arg_for_name_do_push",
    "descent_covers_grown_tail": "test_the_grown_tail_is_descended_after_the_outer_record",
}


def _load_corpus_module():
    """Import `test_guard_corpus.py` BY PATH — see the module docstring's renamed-row-protection
    section for why this is side-effect-free."""
    spec = importlib.util.spec_from_file_location(
        "mutate_git_command_reserved_arg_corpus", TEST_GUARD_CORPUS
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

    `REQUIRED_RESERVED_ARG_AXIS_LABELS` covers every row this campaign names — unlike the
    reserved-cd campaign this is modeled on, no row here is drawn from a derived preserve matrix,
    so this is the only source consulted.
    """
    return set(module.REQUIRED_RESERVED_ARG_AXIS_LABELS)


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


def _assert_anchors_are_unique(subject: Path) -> None:
    """Assert each anchor this campaign mutates occurs EXACTLY once in `subject`, before the
    ~minutes-long scoring pass runs. `mutate.run()` already reports a non-1 count as a campaign
    defect during PASS 1 (scoring), and `_verify_named_rows` reports the same during PASS 2 (the
    named-row check, after scoring) -- both per-mutation, one at a time; this fails fast, up front,
    for all four anchors at once, before either pass runs."""
    text = subject.read_text()
    problems = [
        f"anchor appears {count}x in {subject.name}, need exactly 1: {anchor[:60]!r}..."
        for anchor in _ANCHORS
        if (count := text.count(anchor)) != 1
    ]
    if problems:
        raise RuntimeError(
            f"{len(problems)} stale anchor(s) -- this is a campaign defect, not a weak suite: "
            + "; ".join(problems)
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
    _assert_anchors_are_unique(SUBJECT)

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
