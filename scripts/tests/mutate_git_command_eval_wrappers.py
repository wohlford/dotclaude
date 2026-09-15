#!/usr/bin/env python3
# Script: mutate_git_command_eval_wrappers.py
# Purpose: Mutation campaign for the eval/builtin wrapper fix (fix/eval-wrapper-bypass) — prove
#          every mechanism Tasks 1-5 added to scripts/lib/git_command.py is pinned BY NAME, not
#          merely that the suite goes red somewhere
# Usage: scripts/tests/mutate_git_command_eval_wrappers.py
"""Mutate each eval/builtin-wrapper mechanism and require the CORPUS or unit suite to catch it,
BY NAME.

A green suite at branch tip is also exactly what reverting every commit on this branch would
produce. This campaign is the measurement that tells those apart: one mutation per mechanism the
branch introduced, each paired with the specific row or test it is REQUIRED to kill.

SUBJECT = scripts/lib/git_command.py. SUITE runs both `test_guard_corpus.py` (the eval-axis rows
Task 4 added, `REQUIRED_EVAL_AXIS_LABELS`) and `test_git_command.py` (the unit-level
`test_env_prefix_is_collected_across_eval_and_its_dashdash`) — the two places this branch's
mechanisms are pinned.

Built on the pattern of `mutate_publication_push_guard_env.py`: a `mutate.run()` scoring pass,
then `_verify_named_rows`, which re-applies each mutation, captures FULL suite output, and
requires the named label/test to be IN it. `mutate.run()`'s `Outcome.detail` keeps only the
suite's last stdout line per caught mutant, so "the row I intended to kill is among the failures"
cannot be read from its report alone — a mutation can turn the suite red for some OTHER reason and
still score CAUGHT.

## The three anchors that bite

`cd_pos is not False` occurs at TWO call sites in `_walk_context` (the `cd`/`pushd` site and the
`popd` site), so each site's mutation anchor carries its own leading text
(`tok in ("cd", "pushd") and cd_pos is not False` vs `tok == "popd" and cd_pos is not False`) —
sharing the bare condition would make `old.count(...)` fail its own uniqueness requirement.

`return False` occurs many times in this module, so `argument_cd_made_unresolvable`'s anchor
includes the line straight after it — `return None if through_wrapper else True`, `_cd_command_
position`'s own closing line and the only occurrence of that exact text in the file — which makes
the combined two-line anchor unique even though neither line is unique alone.

Two pairs of mutations — `wrapper_cd_tracked`/`wrapper_cd_ignored`, and
`env_assignment_makes_cd_unresolvable`/`env_assignment_ends_the_cd_walk` — deliberately share one
`old` anchor with a different `new`, each probing a different wrong answer at the same site.
`mutate.run()` applies each mutation independently against the pristine original and restores
between rows, so two rows sharing an `old` is not a collision.

## Renamed-row protection

A renamed corpus label or unit test would make a `MUST_KILL` entry point at nothing, and the row
check would then read the mutation as SURVIVED for the right reason (label absent) while the
underlying cause — a stale mutation-list entry, not a weak suite — goes unstated. So before running
anything, this module asserts every `MUST_KILL` value that names a *corpus row* is a member of
`REQUIRED_EVAL_AXIS_LABELS` (loaded by importing `test_guard_corpus.py` BY PATH — its own
module-level code is limited to a `sys.path.insert`, one `import git_command`, and one `assert`
over `RESERVED_WORD_WRAP`; none of it runs a subprocess, writes a fixture, or otherwise reaches
outside plain Python, so importing it here carries no side effect a pytest collection run would
not also produce) and that the one `MUST_KILL` value naming a *unit test* — not a corpus label —
occurs verbatim in `test_git_command.py`'s text.
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

# The one MUST_KILL value that names a unit TEST rather than a corpus row LABEL — carved out of
# the "is this in REQUIRED_EVAL_AXIS_LABELS" check below, which only makes sense for corpus rows.
_TOKENIZER_UNIT_TEST_NAME = "test_env_prefix_is_collected_across_eval_and_its_dashdash"

# ---------------------------------------------------------------------------------------------
# The 13 mechanisms Tasks 1-5 added to scripts/lib/git_command.py.
# ---------------------------------------------------------------------------------------------
MUTATIONS = [
    # WRAPPERS losing "eval" reopens the bare `eval git …` bypass this whole branch exists to
    # close — the FIRST enumeration of wrapper words missed it entirely.
    mutate.Mutation(
        "drop_eval_from_wrappers",
        '    "eval",\n',
        "",
    ),
    # WRAPPERS losing "builtin" reopens `builtin eval git …`.
    mutate.Mutation(
        "drop_builtin_from_wrappers",
        '    "builtin",\n',
        "",
    ),
    # `_steps_as_wrapper`'s `--` recognition disabled outright: a wrapper's own end-of-options
    # marker (`eval -- git …`) stops being stepped, and `eval` right before `--` before `git`
    # then reads as ordinary argument text, not command position.
    mutate.Mutation(
        "dashdash_never_stepped",
        '    if tok == "--" and j > 0:',
        "    if False:",
    ),
    # The `--`'s OWNER goes unchecked: any `--`, not just one belonging to a WRAPPERS/
    # extra_wrappers member, is treated as stepped over — `echo -- git …` would then also read
    # `git` as command position, over-admitting far past this branch's own wrappers.
    mutate.Mutation(
        "dashdash_owner_unchecked",
        "        return owner in WRAPPERS or owner in extra_wrappers",
        "        return True",
    ),
    # `starts_command`'s backward walk reverted to the pre-`_steps_as_wrapper` literal spelling
    # (no `--` recognition at this call site) — the eval-dashdash bypass reopens for `git`
    # detection specifically, independent of the shared predicate's own dashdash mutation above.
    mutate.Mutation(
        "starts_command_keeps_its_own_spelling",
        "_steps_as_wrapper(tokens, j, extra_wrappers)",
        "(prev in WRAPPERS or prev in extra_wrappers)",
    ),
    # `_env_prefix`'s walk reverted the same way — env collection stops recognising a wrapper's
    # `--`, so `eval -- FOO=1 git …`'s env prefix goes uncollected even though `git` itself is
    # still detected via the (unmutated) `starts_command` walk.
    mutate.Mutation(
        "env_prefix_keeps_its_own_spelling",
        "_steps_as_wrapper(tokens, j, GIT_ONLY_WRAPPERS)",
        "(prev in WRAPPERS or prev in GIT_ONLY_WRAPPERS)",
    ),
    # `_cd_command_position`'s final line stuck at "always tracked" (revision 1's answer, and
    # revision 2's after that) — a `cd` reached through ANY wrapper (`nohup cd X`) would then be
    # resolved and TRACKED as if it moved the real shell, rather than made unresolvable.
    mutate.Mutation(
        "wrapper_cd_tracked",
        "    return None if through_wrapper else True",
        "    return True",
    ),
    # The same line stuck the other wrong way — a wrapped `cd` would be IGNORED (treated as an
    # argument, `False`) instead of UNRESOLVABLE, so the surrounding push stays judged against a
    # cwd the walk never actually lost track of, silently narrowing the safe-direction block.
    mutate.Mutation(
        "wrapper_cd_ignored",
        "    return None if through_wrapper else True",
        "    return False if through_wrapper else True",
    ),
    # A leading `FOO=1 cd X` env assignment made to ALSO set `through_wrapper = True` — an env
    # prefix alone stops being distinguished from a real wrapper, so `FOO=1 cd X` (which DOES move
    # the current shell) would be misread as unresolvable instead of tracked exactly.
    mutate.Mutation(
        "env_assignment_makes_cd_unresolvable",
        "        if ENV_ASSIGN.match(prev):\n            j -= 1\n            continue",
        "        if ENV_ASSIGN.match(prev):\n            through_wrapper = True\n"
        "            j -= 1\n            continue",
    ),
    # The ENV_ASSIGN branch made to END the walk with `return False` instead of stepping past the
    # assignment — `FOO=1 cd X` would then read as an ARGUMENT (not a directory change at all),
    # the same wrong answer as `env_assignment_makes_cd_unresolvable` reaches by a different route.
    mutate.Mutation(
        "env_assignment_ends_the_cd_walk",
        "        if ENV_ASSIGN.match(prev):\n            j -= 1\n            continue",
        "        if ENV_ASSIGN.match(prev):\n            return False",
    ),
    # `_cd_command_position`'s ARGUMENT answer (`return False`, `echo cd X` is not a directory
    # change) flipped to `None` (unresolvable) — a `cd` appearing as another command's argument
    # would then wrongly darken the cwd walk instead of being correctly ignored. The anchor
    # carries the closing line after it because `return False` alone is not unique in this file.
    mutate.Mutation(
        "argument_cd_made_unresolvable",
        "        return False\n    return None if through_wrapper else True",
        "        return None\n    return None if through_wrapper else True",
    ),
    # The `cd`/`pushd` call site's `cd_pos is not False` narrowed to `cd_pos is True` — an
    # UNRESOLVABLE (`None`) classification would then be treated the same as an ARGUMENT (`False`)
    # and the walk would skip straight over a wrapped `cd` without ever going dark, silently
    # tracking a cwd it can no longer vouch for.
    mutate.Mutation(
        "cd_site_ignores_unresolvable",
        'if tok in ("cd", "pushd") and cd_pos is not False:',
        'if tok in ("cd", "pushd") and cd_pos is True:',
    ),
    # The `popd` call site's `cd_pos is not False` narrowed the same way — a wrapped `popd`
    # (`builtin popd`) would stop making the cwd unresolvable and the walk would silently carry on
    # trusting whatever directory was tracked before it.
    mutate.Mutation(
        "popd_site_ignores_unresolvable",
        'if tok == "popd" and cd_pos is not False:',
        'if tok == "popd" and cd_pos is True:',
    ),
]

MUST_KILL = {
    "drop_eval_from_wrappers": "eval_bypass_private_branch",
    "drop_builtin_from_wrappers": "eval_bypass_builtin_eval",
    "dashdash_never_stepped": "eval_bypass_eval_dashdash",
    "dashdash_owner_unchecked": "eval_allow_double_dashdash",
    "starts_command_keeps_its_own_spelling": "eval_bypass_eval_dashdash",
    "env_prefix_keeps_its_own_spelling": _TOKENIZER_UNIT_TEST_NAME,
    "wrapper_cd_tracked": "eval_cwd_nohup_cd_does_not_move",
    "wrapper_cd_ignored": "eval_cwd_unresolvable_blocks_safe_refspec",
    "env_assignment_makes_cd_unresolvable": "eval_allow_env_prefixed_cd_still_exact",
    "env_assignment_ends_the_cd_walk": "eval_allow_env_prefixed_cd_still_exact",
    "argument_cd_made_unresolvable": "eval_allow_cd_as_an_argument",
    "cd_site_ignores_unresolvable": "eval_cwd_cd_into_adopted_from_other",
    "popd_site_ignores_unresolvable": "eval_cwd_builtin_popd_back_into_adopted",
}


def _load_required_eval_axis_labels() -> frozenset[str]:
    """`REQUIRED_EVAL_AXIS_LABELS`, loaded by importing `test_guard_corpus.py` BY PATH.

    That module's own top-level code (verified by AST inspection while authoring this campaign)
    is limited to a `sys.path.insert`, `import git_command as new_gitcmd`, plain constant
    assignments, and one `assert set(_RESERVED_WORD_WRAP) == new_gitcmd.RESERVED_WORDS` — no
    subprocess call, no fixture write, no baseline verification (that lives in a pytest fixture,
    never invoked at import time). Importing it here reaches nothing a normal pytest collection
    pass over the same file would not also reach.
    """
    spec = importlib.util.spec_from_file_location(
        "mutate_git_command_eval_wrappers_corpus", TEST_GUARD_CORPUS
    )
    module = importlib.util.module_from_spec(spec)
    # Must be registered in sys.modules BEFORE exec_module: test_guard_corpus.py defines
    # `@dataclass(frozen=True)` classes, and dataclass's own type-resolution looks the module up
    # by name via `sys.modules.get(cls.__module__)` while its body is still executing — an
    # unregistered module makes that lookup return None and raise `AttributeError` on `.__dict__`,
    # not on anything this campaign's own logic does.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.REQUIRED_EVAL_AXIS_LABELS


def _assert_must_kill_names_are_live() -> None:
    """Fail loudly, before any mutation runs, if a `MUST_KILL` entry points at a renamed row.

    Without this, a renamed corpus label or unit test makes the row check below read a mutation
    as SURVIVED for the wrong reason — "no such label in the output" is byte-identical whether
    the mechanism is truly unguarded or the row was merely renamed out from under this campaign.
    """
    required = _load_required_eval_axis_labels()
    corpus_labels = {v for v in MUST_KILL.values() if v != _TOKENIZER_UNIT_TEST_NAME}
    missing = corpus_labels - required
    if missing:
        raise RuntimeError(
            f"{len(missing)} MUST_KILL label(s) not in REQUIRED_EVAL_AXIS_LABELS "
            f"(renamed or removed corpus row?): {sorted(missing)}"
        )
    unit_text = TEST_GIT_COMMAND.read_text()
    if _TOKENIZER_UNIT_TEST_NAME not in unit_text:
        raise RuntimeError(
            f"{_TOKENIZER_UNIT_TEST_NAME!r} does not occur in {TEST_GIT_COMMAND.name} "
            "(renamed unit test?)"
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
            f"row-check  {m.label:45s} row={must_contain!r:55s} "
            f"{'FOUND' if must_contain in output else 'MISSING'}"
        )
    final = subject.read_text()
    if final != original:
        problems.append(f"{subject.name} not restored at end of row-presence pass")
    return problems


def main() -> int:
    _assert_must_kill_names_are_live()

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
