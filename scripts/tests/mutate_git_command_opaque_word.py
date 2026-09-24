#!/usr/bin/env python3
# Script: mutate_git_command_opaque_word.py
# Purpose: Mutation campaign for the opaque-command-word widening — prove every clause the
#          2026-09-18 widening of `is_git` (git behind a non-literal command word) added to
#          `is_git` and `subcommand_is_indeterminate` in scripts/lib/git_command.py is pinned
#          BY NAME, not merely that the suite goes red somewhere
# Usage: scripts/tests/mutate_git_command_opaque_word.py
"""Mutate the opaque-command-word widening and require a pytest-resident row to catch it, BY NAME.

A green suite is also exactly what the suite would show with this widening's mechanism reverted,
if nothing pinned that mechanism BY NAME. This campaign is the measurement that tells those apart:
one mutation per clause `is_git`, `subcommand_is_indeterminate`, and the option-run-stop branch in
`_walk_context` gained under fix/tokenizer-glued-closer (spec
`2026-09-18-tokenizer-opaque-git-word`), each paired with the specific row it is REQUIRED to kill.

SUBJECT = scripts/lib/git_command.py, specifically:

  - `is_git`'s early `ENV_ASSIGN` return, its opaque-token guard (`_is_opaque`), the
    placeholder-removal step (`_PLACEHOLDER_RE.sub`), clause C1 (`literal.endswith("git")`), clause
    C2 (the final `_literal_git(...)` return), and the `_WORD_EXPANSION_RE` reduction it runs before
    C2;
  - `_PARAM_EXPANSION_RE`, built `[^{}]*` rather than `[^}]*` specifically to stay
    non-rescanning (see its comment: `[^}]*` rescans to end-of-token from every `$`, measured
    5.9 s on 60k of them). A brace-expansion clause and its regex were deleted after two
    quadratic-cost BLOCKERs, so they carry no mutants here;
  - `subcommand_is_indeterminate`'s `or _is_opaque(sub)` disjunct;
  - the option-run-stop branch in `_walk_context` (`if is_git(tokens[j]):`) that RECORDS a git-like
    subcommand instead of dropping the invocation, since the 2026-09-18 widening changed that
    branch from a silent drop to a `results.append(...)`.

SUITE runs only `test_git_command.py` — fast, and the one place every clause above is pinned; no
corpus row in `test_guard_corpus.py` is named by this campaign, so importing that module is not
needed here (contrast `mutate_git_command_reserved_arg.py`, which this file is modelled on and
which does need it).

Built on the pattern of `mutate_git_command_reserved_arg.py`: a `mutate.run()` scoring pass, then
`_verify_named_rows`, which re-applies each mutation, captures FULL suite output, and requires the
named test's identity to be IN it. `mutate.run()`'s `Outcome.detail` keeps only the suite's last
stdout line per caught mutant, so "the row I intended to kill is among the failures" cannot be read
from its report alone — a mutation can turn the suite red for some OTHER reason and still score
CAUGHT.

## Naming the required row: function name vs. parametrize id

Five of the nine mutants are killed by a SINGLE parametrized row, and for those `MUST_KILL` names
the row's full pytest node id — function name plus its bracketed parametrize value
(`test_an_opaque_word_that_bash_reduces_to_git_is_recorded[git$X]`) — because each of those tests
parametrizes over exactly ONE argument (`word` or `command`), and pytest's default id for a single
string argument is the string itself, so the bracketed form is exact and verifiable by inspection
of the fixture list in `test_git_command.py` (checked by `_assert_must_kill_names_are_live` below).

The other four are killed by a test that is parametrized over MORE than one argument
(`test_subcommand_is_indeterminate`, over `(sub, want)`) or whose failure spans more than one
parametrized row of the SAME test (`test_an_assignment_is_never_the_git_word`,
`test_a_git_like_subcommand_is_recorded_not_dropped`, and
`test_is_git_is_linear_on_a_pathological_word`, whose rows carry explicit ids and are each
slowed only by the regex their word stresses — three mutants name it). For a multi-argument
parametrize, pytest's id join format is not something this campaign should hard-code without
running pytest to observe it —
this module deliberately never runs pytest itself outside `mutate.run()`'s own subprocess calls, so
guessing wrong would make `_verify_named_rows` misreport a genuinely-caught mutant as one whose
suite output "does NOT contain the required name", which is indistinguishable from the mutation
surviving. `MUST_KILL` therefore names the bare FUNCTION for these four — verified present, by
mutation-specific reasoning below, to be a substring of the failure output whichever row(s) of that
function fail:

  - `test_an_assignment_is_never_the_git_word` (4 parametrized rows) — the ENV_ASSIGN mutation makes
    at least the `X=/git` row's own `is_git` assertion fail, and possibly others depending on the
    token's trailing shape; any failing row prints the function name.
  - `test_is_git_is_linear_on_a_pathological_word` (four rows with explicit ids, one per
    pathological word) — each regex mutant slows the row whose word stresses that regex past its
    0.2 s bound, and any failing row prints the function name.
  - `test_a_git_like_subcommand_is_recorded_not_dropped` (3 parametrized rows) — deleting the
    `results.append(...)` call drops the invocation outright, so `len(got) == 1` fails on every row.
  - `test_subcommand_is_indeterminate` (6 parametrized rows over two arguments) — dropping the
    `or _is_opaque(sub)` disjunct was traced by hand against every row: the `"$V"` row
    (`is_git("$V")` is False — `_PARAM_EXPANSION_RE` consumes it to the empty string, which
    `_literal_git` rejects) and the placeholder-prefix row (`is_git` also False there, for the same
    reason with the placeholder removed instead of the param expansion) both flip from True to
    False under this mutation; the other four rows are unaffected (`"git"` and `"${X}git"` are
    already `is_git`-true; `"status"` and the push verb are false either way). At least two rows
    fail, so the function name is a safe substring to require.

## Mutate the code, never the fixture lists

`_OPAQUE_SHAPES`, the negative-control `command` lists, and `RESERVED_WORDS`/`PLACEHOLDER_PREFIX`
are read by `_assert_must_kill_names_are_live` as literal text, never executed or imported as a
module (unlike the reserved-arg campaign, which imports `test_guard_corpus.py` by path for its own
`REQUIRED_RESERVED_ARG_AXIS_LABELS` — not needed here, since every row this campaign names lives in
`test_git_command.py` itself and is checked by plain substring search over that file's source text).
Every mutation below therefore edits `git_command.py`'s CODE, never `test_git_command.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "lib" / "git_command.py"
TEST_GIT_COMMAND = REPO / "scripts" / "tests" / "test_git_command.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "scripts/tests/test_git_command.py",
    "-q",
    "-p",
    "no:cacheprovider",
]

# MUST_KILL values naming a bare FUNCTION rather than one parametrized row — see the module
# docstring's "function name vs. parametrize id" section for why. Carved out of the
# "is this a live single-parameter row" check below, which only makes sense for the other five.
_UNIT_TEST_NAMES = frozenset(
    {
        "test_an_assignment_is_never_the_git_word",
        "test_is_git_is_linear_on_a_pathological_word",
        "test_a_git_like_subcommand_is_recorded_not_dropped",
        "test_subcommand_is_indeterminate",
    }
)

MUTATIONS = [
    # `is_git` never treats an env-assignment token as the git word. Deleting the early return lets
    # `X=/git`, whose token ends in `/git` after the opaque check, read as git.
    mutate.Mutation(
        "env_assign_early_return_deleted",
        "    if ENV_ASSIGN.match(token):\n        return False\n",
        "",
    ),
    # `if not _is_opaque(token): return False` is the gate before every opaque-token clause runs at
    # all. Deleting it falls through into the opaque logic even for a token carrying neither `$` nor
    # a placeholder — "legit status"'s command word, "legit", would then be scanned by the opaque
    # clauses too (though it still resolves False there; the direct effect this mutation is named
    # for is that a non-opaque word no longer short-circuits before them).
    mutate.Mutation(
        "opaque_guard_deleted",
        "    if not _is_opaque(token):\n        return False\n",
        "",
    ),
    # Clause C1: an opaque token that, with placeholders removed, ENDS in `git` (`"$X"git` becomes
    # `$Xgit` under shlex(posix=True) quote-merging). Forcing the condition False disables C1 alone.
    mutate.Mutation(
        "c1_endswith_git_forced_false",
        'literal.endswith("git")',
        "False",
    ),
    # Clause C2: the final `_literal_git(...)` return, reached for an opaque token that is literally
    # `git` once every expansion is reduced or removed (`git$X` -> `git`). Forcing it False disables
    # C2 alone, independent of C1.
    mutate.Mutation(
        "c2_final_return_forced_false",
        '    return _literal_git(_PARAM_EXPANSION_RE.sub("", literal))',
        "    return False",
    ),
    # The `_WORD_EXPANSION_RE` reduction turns `${X:-git}` into `git` before C2 sees it. Dropping the
    # reduction (a no-op assignment in its place, to keep the function's local intact) leaves
    # `${X:-git}` as `${X:-git}` when C2 runs, which `_PARAM_EXPANSION_RE` then consumes to the empty
    # string rather than to `git`.
    mutate.Mutation(
        "word_expansion_reduction_dropped",
        '    literal = _WORD_EXPANSION_RE.sub(r"\\1", literal)',
        "    literal = literal",
    ),
    # `_PARAM_EXPANSION_RE` is built `[^{}]*` rather than `[^}]*` specifically so a run of `${` costs
    # one step each instead of rescanning to end-of-token from every `$` — see its own comment,
    # measured 5.9 s on 60k of them. Narrowing it to `[^}]*` reintroduces the rescan.
    mutate.Mutation(
        "param_expansion_re_rescans",
        '    r"\\$(?:\\{[^{}]*\\}|[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-])"\n)',
        '    r"\\$(?:\\{[^}]*\\}|[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-])"\n)',
    ),
    # The placeholder-removal step (`_PLACEHOLDER_RE.sub("", token)`) strips a nested-context marker
    # before C1/C2 see the token, so `git$(true)` -- whose marker sits AFTER `git` once the tokenizer
    # substitutes it -- is read as literally `git`. Dropping the removal (`literal = token`) leaves
    # the placeholder text in place, so C1 then sees it as the tail instead of `git`: a kill set
    # distinct from the C1 and C2 mutants above, which target the clauses themselves rather than
    # what they are handed.
    mutate.Mutation(
        "placeholder_removal_dropped",
        '    literal = _PLACEHOLDER_RE.sub("", token)',
        "    literal = token",
    ),
    # The option-run-stop branch in `_walk_context`: the 2026-09-18 widening changed a silent
    # DROP into a `results.append(...)` recording the git-like word as an indeterminate
    # subcommand. Deleting just the append (keeping `_descend`, `i = j`, `continue`) reverts to
    # the drop, exactly the premise that branch's own comment argues against.
    mutate.Mutation(
        "option_run_stop_results_append_deleted",
        "                results.append(\n"
        "                    Invocation(\n"
        "                        cwd_state,\n"
        "                        cdir,\n"
        "                        tokens[j],\n"
        "                        [],\n"
        "                        InvocationTokens(env_pre, tokens[i + 1 : j]),\n"
        "                    )\n"
        "                )\n",
        "",
    ),
    # `subcommand_is_indeterminate`'s `or _is_opaque(sub)` disjunct: without it, a subcommand that is
    # opaque but for which `is_git` itself is False (`"$V"`, a bare placeholder) reads as determinate
    # — exactly the gap this disjunct exists to close, since an opaque word may expand to anything.
    mutate.Mutation(
        "subcommand_indeterminate_drops_opaque_clause",
        "    return is_git(sub) or _is_opaque(sub)",
        "    return is_git(sub)",
    ),
]

MUST_KILL = {
    "env_assign_early_return_deleted": "test_an_assignment_is_never_the_git_word",
    "opaque_guard_deleted": ("test_words_the_widening_must_not_record[legit status]"),
    "c1_endswith_git_forced_false": (
        'test_an_opaque_word_that_bash_reduces_to_git_is_recorded["$X"git]'
    ),
    "c2_final_return_forced_false": (
        "test_an_opaque_word_that_bash_reduces_to_git_is_recorded[git$X]"
    ),
    "word_expansion_reduction_dropped": (
        "test_an_opaque_word_that_bash_reduces_to_git_is_recorded[${X:-git}]"
    ),
    "param_expansion_re_rescans": "test_is_git_is_linear_on_a_pathological_word",
    "placeholder_removal_dropped": (
        "test_an_opaque_word_that_bash_reduces_to_git_is_recorded[git$(true)]"
    ),
    "option_run_stop_results_append_deleted": (
        "test_a_git_like_subcommand_is_recorded_not_dropped"
    ),
    # The bare function name, deliberately: this mutant flips exactly `[$V-True]` and
    # `[__GIT_COMMAND_SUBST_0__-True]` (hand-traced), both rows of this function, so no other row
    # can catch it. A precise id cannot be written here: `_assert_must_kill_names_are_live` looks a
    # bracketed value up as literal text in the test file, and pytest's multi-argument id
    # (`$V-True`) never appears there — tried, and it refused.
    "subcommand_indeterminate_drops_opaque_clause": "test_subcommand_is_indeterminate",
}


def _assert_must_kill_names_are_live() -> None:
    """Fail loudly, before any mutation runs, if a `MUST_KILL` entry points at a renamed row.

    Without this, a renamed test function or a fixture value dropped from `_OPAQUE_SHAPES` (or one
    of the negative-control `command` lists) makes the row check below read a mutation as SURVIVED
    for the wrong reason -- "no such name in the output" is byte-identical whether the mechanism is
    truly unguarded or the row was merely renamed or removed out from under this campaign.

    Every `MUST_KILL` value either IS a bare name in `_UNIT_TEST_NAMES` (checked for presence of
    that function name in the test file's text) or names one parametrized row as
    `function_name[literal_value]` (checked for presence of BOTH the function name and the literal
    parametrize value in the test file's text -- not a claim that the value is that row's exact
    pytest node id spelling, only that the fixture still carries it).
    """
    text = TEST_GIT_COMMAND.read_text()
    problems = []
    for label, value in MUST_KILL.items():
        if value in _UNIT_TEST_NAMES:
            if value not in text:
                problems.append(
                    f"{label}: function name {value!r} not found in test file"
                )
            continue
        if "[" not in value or not value.endswith("]"):
            problems.append(
                f"{label}: MUST_KILL value {value!r} is neither a bare unit-test name "
                "nor a function[value] parametrize reference"
            )
            continue
        func_name, bracket = value.split("[", 1)
        fixture_value = bracket[:-1]
        if func_name not in text:
            problems.append(
                f"{label}: function name {func_name!r} not found in test file"
            )
        if fixture_value not in text:
            problems.append(
                f"{label}: parametrize value {fixture_value!r} not found in test file "
                "(renamed or removed fixture entry?)"
            )
    if problems:
        raise RuntimeError(
            f"{len(problems)} MUST_KILL entry(ies) point at a renamed or removed row: "
            + "; ".join(problems)
        )


def _assert_anchors_are_unique(subject: Path) -> None:
    """Assert each mutation's `old` occurs EXACTLY once in `subject`, before the ~minutes-long
    scoring pass runs. `mutate.run()` already reports a non-1 count as a campaign defect during
    PASS 1 (scoring), and `_verify_named_rows` reports the same during PASS 2 (the named-row check,
    after scoring) -- both per-mutation, one at a time; this fails fast, up front, for every mutant
    at once, before either pass runs. Every count below was independently verified by direct Python
    count against the live subject before this module was written; see the task report for the
    counts."""
    text = subject.read_text()
    problems = [
        f"{m.label}: anchor appears {count}x in {subject.name}, need exactly 1"
        for m in MUTATIONS
        if (count := text.count(m.old)) != 1
    ]
    if problems:
        raise RuntimeError(
            f"{len(problems)} stale anchor(s) -- this is a campaign defect, not a weak suite: "
            + "; ".join(problems)
        )


def _verify_named_rows(subject: Path, mutations, must_kill: dict) -> list[str]:
    """The part `mutate.run()` cannot do: confirm the REQUIRED name is in the failure text.

    `mutate.run()`'s `Outcome.detail` keeps only the suite's LAST stdout line per caught mutant, so
    "the row I intended to kill is among the failures" cannot be read from its report — a mutation
    can turn the suite red for some OTHER reason and still score CAUGHT. This re-applies each
    mutation, captures FULL pytest stdout+stderr, and asserts the required name is actually IN it.
    Restore is verified by content, independent of `mutate.run()`'s own restore pass.
    """
    import shutil
    import subprocess

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
            f"row-check  {m.label:45s} row={must_contain!r:70s} "
            f"{'FOUND' if must_contain in output else 'MISSING'}"
        )
    final = subject.read_text()
    if final != original:
        problems.append(f"{subject.name} not restored at end of row-presence pass")
    return problems


def main() -> int:
    _assert_must_kill_names_are_live()
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
