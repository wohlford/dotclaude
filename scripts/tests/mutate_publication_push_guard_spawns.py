#!/usr/bin/env python3
# Script: mutate_publication_push_guard_spawns.py
# Purpose: Mutation campaign for the alias-table, tag-sweep and spawn-memo/budget mechanisms added
#          2026-09-23 on branch fix/publication-guard-spawn-bound -- prove the corpus discriminates
#          the memoized `_git`, the `MAX_GIT_SPAWNS` budget, the alias-table decoding/matching, and
#          the two-query tag sweep, BY NAME, not merely that some suite goes red somewhere
# Usage: scripts/tests/mutate_publication_push_guard_spawns.py [--selftest-only]
"""Mutate the alias-table, tag-sweep and spawn-memo/budget mechanisms added 2026-09-23 and require
the CORPUS to catch every one, BY NAME.

ONE subject, `publication-push-guard.py` -- unlike the sibling env-axis campaign, every mechanism
here (the memo, the ledger, the budget, alias-table decoding and matching, the two-query tag
sweep) lives in this one file. `git_command.py` and `guard_deadline.py` were touched only to date
their own stale timing-claim comments (the 2026-09-22 `224.89 s`/`172.40 s` measurements, now
marked as predating this branch's spawn memo), never mutated.

SUITE runs BOTH `test_publication_push_guard.sh` (subprocess-driven, black-box) and
`test_guard_internals.py` (white-box, imports the guard directly): a single `bash -c` command so
`mutate.run()` mutates the subject once and one suite run exercises both. This is necessary, not
belt-and-suspenders -- `test_unreadable_alias_table_blocks` monkeypatches `_alias_table` directly
and cannot be reached by feeding the guard a command string on stdin.

Two passes, exactly like the env-axis campaign, and for the identical reason: `mutate.run()` keeps
only the suite's LAST stdout line per mutant (`Outcome.detail`), so "the row I intended to kill is
among the failures" cannot be read from its report -- a mutation can turn the suite red for some
OTHER reason and still score CAUGHT. `_verify_named_rows` re-applies every mutation, captures the
FULL combined stdout+stderr, and asserts the required row's label is actually in it. Restore is
verified by sha256, independent of `mutate.run()`'s own restore in the first pass.

Every `MUST_KILL` label below was checked once, by hand, against the TRACKED guard on 2026-09-23,
before this campaign file was written: apply the single mutation to `publication-push-guard.py`,
run the exact suite(s) named, observe the named row fail, then restore the file byte-for-byte
(sha256-verified) before moving to the next row. That manual pass used throwaway repos and direct
module loads, exactly like the white-box suite already does, and left no separate artifact -- the
prose beside each `MUST_KILL` entry below (marked "pre-flight confirms ...") is the only record of
it; running this campaign is what re-derives it.

ROW 11 IS SPLIT: `_tags_block` has two identically *shaped* but textually DISTINCT
`.split("\\n")` sites (the `tags` set from `everything_stdout`, and the `merged` set from
`merged_stdout`), so this is two Mutation rows, not one `old` shared between them. Round 1 of
this campaign found that the brief's single prescribed discriminator ("each must be killed by
the U+2028 row", `a dev-only tag whose name holds U+2028 is not split into main-reachable
names`) works for the TAGS-set split but not the MERGED-set split: on that witness,
`merged_stdout` never contains a U+2028 tag name to begin with (the marker tag is dev-only, so
`--merged=main` never returns it), so splitting it via `.splitlines()` instead of `.split("\\n")`
changed nothing observable -- confirmed directly, the full `test_publication_push_guard.sh`
(344/0) and `test_guard_internals.py` (15/0) both passed UNCHANGED under that one mutation.

Round 2 (2026-09-23) closed that gap with its own witness instead of retargeting the existing
one: `test_publication_push_guard.sh` gained a MAIN-reachable tag whose name carries U+2028
(`a main-reachable tag whose name holds U+2028 is still reachable`, expected ALLOWED). There the
all-tags query and the `--merged=main` query return the SAME single-line name (U+2028 is not
"\\n", so neither split breaks it), so the two sets are equal singletons and the push is allowed.
A `.splitlines()` mutant on the merged side alone fragments only that copy into two names that
match neither the unfragmented tags-side entry, so the difference goes non-empty and the row
BLOCKS instead -- confirmed live: PASS unmutated, FAIL (want 0, got 2) with only that one line
mutated, restored byte-identical (sha256) afterward.

ROUND 1 ALSO FOUND THE NAMED-ROW CHECK ITSELF WAS VACUOUS. `_verify_named_rows` used to test
`must_contain in output` -- a bare substring search over the suite's FULL combined stdout. The
shell suite prints every row's label on its PASSING line too (`PASS  <label> (exit N)`), so a
label that never appears on any FAILING line still reads FOUND. This is exactly how the merged-
set survivor above was scored FOUND in round 1's pass 2 despite being SURVIVED in pass 1: the
tags-set row still printed its own PASS line (unaffected by a mutation on the merged site), and
the substring search could not tell that apart from a genuine failure. Round 2 replaced it with
`_row_failed`, which requires the label to sit on a FAILURE-shaped line specifically (a shell
`FAIL  ` row, or a pytest `FAILED ... ::<name>` summary line) -- see its docstring and the
self-test that runs before the campaign proper.

ROUND 1 ALSO FOUND THE COMBINED SUITE COMMAND SHORT-CIRCUITED. `SUITE` used to run
`bash "$1" && python3 -m pytest -q "$2"`: once a mutation reddened the shell suite, `&&` skipped
pytest entirely, so every `MUST_KILL` label naming a pytest test read MISSING even when
`mutate.run()`'s pass 1 correctly scored the mutation CAUGHT (from the shell suite's own
failure). All five of round 1's pass-2 problems were this one mechanism. Round 2 runs both
suites unconditionally and combines their exit statuses -- see `SUITE` below.

ROUND 2 CRASHED THE WHOLE CAMPAIGN ON ITS FIRST SLOW MUTANT. `_verify_named_rows` called
`subprocess.run(SUITE, ..., timeout=300)` for every row; `ledger_never_activated` genuinely
takes ~299s (with no budget enforcement, several corpus rows spend far more real spawns than
usual), which is close enough to the 300s constant that it timed out, RAISED
`subprocess.TimeoutExpired` (uncaught), and ended the pass after checking exactly one row --
`memo_never_hits` -- of fifteen. `subprocess.run(..., timeout=...)` also only kills the direct
`bash -c` child on expiry, leaving the suite's own `bash "$1"` / `python3 -m pytest` (and
everything THEY spawn) orphaned. Round 3 (2026-09-23) replaced it with `_run_suite_bounded`
(kills the whole process GROUP via `os.killpg`, SIGTERM then SIGKILL after a grace period, and
returns a `None` returncode instead of raising) and `_pass2_timeout` (reads pass 1's OWN
derived per-mutant cap straight out of `report.text` -- never a hand-picked constant, so a row
this slow can never again be measured by pass 1 and then re-judged against a smaller number in
pass 2). A row that still times out under that generous cap is recorded as its own
INDETERMINATE problem and the pass CONTINUES to the next mutation -- confirmed live end to end:
forcing a 3s cap against `[ledger_never_activated, budget_off_by_one]` produced two INDETERMINATE
problems (no crash), the subject was restored byte-identical (sha256) after each, and no
orphaned suite process survived either timeout.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "publication-push-guard.py"
SHELL_SUITE = REPO / "scripts" / "tests" / "test_publication_push_guard.sh"
PYTEST_SUITE = REPO / "scripts" / "tests" / "test_guard_internals.py"

# ONE bash -c command runs BOTH suites UNCONDITIONALLY, so mutate.run() (which drives one
# `command` per mutant) exercises both the black-box and white-box corpora against a single
# mutated subject, and pass 2's named-row check can always find a pytest-only label even when
# the shell suite also failed. An earlier `&&` form short-circuited pytest the moment the shell
# suite went red, which read every pytest-named MUST_KILL label as MISSING (round 1, all 5
# pass-2 problems) even though pass 1 correctly scored those mutations CAUGHT from the shell
# suite alone -- `mutate.run()`'s own `is_caught` only needs ONE of returncode/FAIL-line, so it
# was never fooled, but `_verify_named_rows`, which needs the SPECIFIC label, was.
# `-rf` on pytest guarantees its "short test summary info" always lists `FAILED <id>` lines
# (the form `_row_failed` below matches) rather than relying on `-q`'s default reporting chars.
SUITE = [
    "bash",
    "-c",
    'bash "$1"; a=$?; python3 -m pytest -q -rf "$2"; b=$?; [ "$a" -eq 0 ] && [ "$b" -eq 0 ]',
    "_",
    str(SHELL_SUITE),
    str(PYTEST_SUITE),
]

# ---------------------------------------------------------------------------------------------
# Mutations against scripts/publication-push-guard.py -- the alias-table, tag-sweep and
# spawn-memo/budget mechanisms added 2026-09-23
# ---------------------------------------------------------------------------------------------
MUTATIONS = [
    # `_git`'s memo: a cache hit must return the stored (rc, stdout) WITHOUT spending another
    # spawn. `pass` falls through past the early return into the budget check every time, so an
    # identical query re-spawns on every call -- the memo dict is still written but never read.
    mutate.Mutation(
        "memo_never_hits",
        "            return cached",
        "            pass",
    ),
    # The ledger `_find_block_reason` installs for the duration of one judgment. `None` means
    # `_git`'s `if ledger is not None:` gate is always False: no caching AND no budget
    # enforcement at all for that judgment.
    mutate.Mutation(
        "ledger_never_activated",
        "    _LEDGER = _SpawnLedger(MAX_GIT_SPAWNS)",
        "    _LEDGER = None",
    ),
    # Off-by-one on the budget check: `>` instead of `>=` lets `ledger.misses == ledger.budget`
    # through, permitting one MORE spawn than MAX_GIT_SPAWNS actually allows.
    mutate.Mutation(
        "budget_off_by_one",
        "        if ledger.misses >= ledger.budget:",
        "        if ledger.misses > ledger.budget:",
    ),
    # `_SpawnBudgetExceeded` must be caught in `_find_block_reason` and converted to a Block
    # naming the budget. Catching `KeyError` instead lets it propagate to `main`'s generic
    # `except Exception` arm, which still blocks (this command is never opaque_only) but with the
    # internal-error wording instead of the budget wording.
    mutate.Mutation(
        "budget_escapes_to_main",
        "    except _SpawnBudgetExceeded as exc:",
        "    except KeyError as exc:",
    ),
    # `_alias_definition`'s SUBSECTION form (`[alias "X"] command = ...`, git 2.55) must match.
    # Forcing it to `False` means a subsection-form alias is never resolved -- read as "not an
    # alias" -- the pre-git-2.55-subsection-support defect this branch's Task 1 closed.
    mutate.Mutation(
        "subsection_form_unread",
        '            matches = variable == "command" and subsection == name',
        "            matches = False",
    ),
    # The subsection NAME comparison is case-SENSITIVE in real git (unlike the plain form's
    # variable name). Case-folding it here makes `[alias "Foo"]` match a query for `foo`, which
    # git itself would refuse.
    mutate.Mutation(
        "subsection_form_case_folded",
        '            matches = variable == "command" and subsection == name',
        '            matches = variable == "command" and subsection.lower() == lowered',
    ),
    # The plain form (`alias.<v>`) IS case-insensitive in real git (`v == name.lower()`).
    # Comparing against `name` (unlowered) instead of `lowered` breaks that -- `alias.FOO` would
    # stop matching a query for `foo`.
    mutate.Mutation(
        "plain_form_case_sensitive",
        "            matches = rest == lowered",
        "            matches = rest == name",
    ),
    # git resolves the LAST matching entry in config order (barring a valueless one, which wins
    # immediately -- see the next two rows). Reversing the iteration order flips which of two
    # differently-valued matching entries wins.
    mutate.Mutation(
        "first_definition_wins",
        "    for key, value in entries:",
        "    for key, value in reversed(entries):",
    ),
    # A VALUELESS matching entry must win wherever it sits in config order -- git aborts
    # ("missing value", fatal) the moment it reaches one, before any later entry. `continue`
    # instead of the immediate return lets a later, VALUED entry overwrite `found`, so the
    # valueless definition stops winning when it is not the last match.
    mutate.Mutation(
        "valueless_match_stops_winning",
        "        if value is None:\n            return True, None\n        found",
        "        if value is None:\n            continue\n        found",
    ),
    # An unreadable alias table (`_alias_table` returns None) cannot prove a subcommand is NOT an
    # alias, so `_alias_definition` must report it as "defined, valueless" (forcing the chain to
    # BLOCK) rather than "not defined" (which the chain reads as "not an alias -- allow").
    mutate.Mutation(
        "unreadable_table_read_as_no_aliases",
        "    if entries is None:\n        return True, None",
        "    if entries is None:\n        return False, None",
    ),
    # `_tags_block`'s TAGS-set split (`everything_stdout`). `str.splitlines()` also splits on
    # U+2028, a legal tag-name character, so a dev-only tag literally named
    # `odd<U+2028>refs/tags/x` fragments into "refs/tags/odd" and "refs/tags/x" -- both of which
    # coincide with real main-reachable tags in the witness row, so the set difference goes empty
    # and the dev-only tag is (wrongly) allowed through `--tags`.
    mutate.Mutation(
        "tag_sweep_tags_set_splitlines",
        '    tags = {line for line in everything_stdout.split("\\n") if line}',
        "    tags = {line for line in everything_stdout.splitlines() if line}",
    ),
    # `_tags_block`'s MERGED-set split (`merged_stdout`) -- the SAME defect class, a DIFFERENT
    # site, so its own row per the brief. See the module docstring above ("ROW 11 IS SPLIT..."):
    # round 1 found the corpus had no row that discriminates this specific site (the dev-only
    # U+2028 witness never puts a U+2028 name into `merged_stdout`); round 2 added
    # `test_publication_push_guard.sh`'s MAIN-reachable U+2028 tag row, confirmed live to PASS
    # unmutated and FAIL under exactly this mutation.
    mutate.Mutation(
        "tag_sweep_merged_set_splitlines",
        '    return bool(tags - {line for line in merged_stdout.split("\\n") if line})',
        "    return bool(tags - {line for line in merged_stdout.splitlines() if line})",
    ),
    # The MERGED query's own failure (`merged_rc != 0`, e.g. no `main` branch to resolve
    # `--merged=main` against) must block -- no tag can be PROVEN reachable when the query that
    # would prove it failed. `return False` reads that failure as "nothing unreachable", the
    # fail-open inversion.
    mutate.Mutation(
        "tag_sweep_ignores_merged_query_failure",
        "    if merged_rc != 0:\n        return True",
        "    if merged_rc != 0:\n        return False",
    ),
    # The no-tags short-circuit: with zero tags there is nothing for `--tags`/`--follow-tags` to
    # sweep, so the answer is False before the (no `main`-dependent) merged query even runs.
    # Deleting it means a repo with no tags AND no `main` branch now falls through to the merged
    # query, which fails (no `main` to resolve), and `_tags_block` wrongly blocks an otherwise-
    # safe push that carries no tags at all.
    mutate.Mutation(
        "tag_sweep_no_tags_shortcircuit_removed",
        "    if not tags:\n        return False\n",
        "",
    ),
    # `_git`'s `errors="surrogateescape"` lets ONE non-UTF-8 byte in an unrelated alias VALUE
    # decode without raising, since the whole alias section is now read as one table (Task 1),
    # not per queried name. Removing it makes `subprocess.run`'s default strict decoding raise
    # UnicodeDecodeError on ANY judgment that happens to read a table containing that byte --
    # even for a completely unrelated command -- which escapes to main's internal-error handler
    # and blocks a command that was never a push.
    mutate.Mutation(
        "alias_table_decoding_strict",
        '        errors="surrogateescape",\n',
        "",
    ),
]

MUST_KILL = {
    # Spawn count for 1000 distinct unknown subcommands in one repo is a fixed 4 (rev-parse,
    # for-each-ref, cat-file, one alias-table read) ONLY because the memo answers every repeat of
    # the first three from cache. Without it, the repeated `_resolve_root`/adopted-check queries
    # each spend a fresh spawn and the budget (256) is exhausted long before 1000 -- pre-flight
    # confirms the guard then raises _SpawnBudgetExceeded, so the assert_eq on spawn count fails.
    "memo_never_hits": "1000 unknown subcommands in one repo cost 4 git spawns",
    # White-box only: this row monkeypatches MAX_GIT_SPAWNS to 1 and requires the budget to fire
    # (a Block naming "git-query budget"). With the ledger never installed there is no budget
    # enforcement at all, so no such Block is ever produced.
    "ledger_never_activated": "test_budget_exhaustion_clears_ledger",
    # Exactly-at-budget is allowed, one-over is refused -- the off-by-one lets one-over through
    # too, flipping this push_run's expected rc from 2 to 0.
    "budget_off_by_one": "one query over MAX_GIT_SPAWNS is refused",
    # This row inspects the REFUSAL TEXT itself for the phrase "git-query budget". Catching
    # KeyError instead of _SpawnBudgetExceeded lets the exception escape to main's generic
    # internal-error handler, which still blocks (the command is not opaque_only) but with
    # different wording -- the row's substring check fails even though the command is still rc=2.
    "budget_escapes_to_main": "the budget refusal names the budget",
    # The differential oracle against real git (60 random configs x 5 names, fixed seed) -- see
    # this campaign's module docstring for the pre-flight confirmation (1 failed, 1 passed) for
    # each of the five alias-matching mutations below.
    "subsection_form_unread": "test_alias_definition_matches_git_itself",
    "subsection_form_case_folded": "test_alias_definition_matches_git_itself",
    "plain_form_case_sensitive": "test_alias_definition_matches_git_itself",
    "first_definition_wins": "test_alias_definition_matches_git_itself",
    # Reachable via the SHELL suite too (a valueless alias after a valued one, "git vv"), not
    # white-box-only as an earlier draft of this campaign's brief assumed -- pre-flight confirms
    # the mutation flips `_resolve_alias_chain`'s result for that exact fixture from ("block",
    # None) to ("safe", None). Kept as the more specific, lower-noise discriminator.
    "valueless_match_stops_winning": (
        "a VALUELESS matching definition blocks even after a valued one"
    ),
    # White-box only, and a direct hit: this pytest row monkeypatches `_alias_table` to return
    # None and asserts `_resolve_alias_chain(...) == ("block", None)`. Pre-flight confirms the
    # mutation flips that to ("none", None).
    "unreadable_table_read_as_no_aliases": "test_unreadable_alias_table_blocks",
    "tag_sweep_tags_set_splitlines": (
        "a dev-only tag whose name holds U+2028 is not split into main-reachable names"
    ),
    # Round 2 (2026-09-23): the brief's original U+2028 row does not discriminate this site (see
    # the module docstring) -- this is the new MAIN-reachable-tag row added specifically for it,
    # confirmed live: PASS unmutated, FAIL (want 0, got 2) with only this one mutation applied,
    # subject restored byte-identical (sha256) afterward.
    "tag_sweep_merged_set_splitlines": (
        "a main-reachable tag whose name holds U+2028 is still reachable"
    ),
    "tag_sweep_ignores_merged_query_failure": "with no main branch no tag is provably reachable",
    "tag_sweep_no_tags_shortcircuit_removed": (
        "with no main and NO tags, --tags sweeps nothing (unchanged)"
    ),
    "alias_table_decoding_strict": (
        "a non-UTF-8 byte in another alias's value does not refuse an unrelated command"
    ),
}


def _row_failed(output: str, label: str) -> bool:
    """True only if `label` names a row that actually FAILED, never merely mentioned.

    Round 1 used a bare `must_contain in output` substring search over the suite's whole
    combined stdout+stderr. The shell suite prints every row's label on its PASSING line too
    (`push_run`/`assert_eq`: `printf 'PASS  %s (exit %d)\\n' "$label" ...`), so a label that
    never appears on any FAILING line still read FOUND -- measured directly: round 1's pass 2
    reported FOUND for `tag_sweep_merged_set_splitlines` against a mutation that pass 1 had
    scored SURVIVED, because the row's own PASS line (printed by an unrelated, still-correct
    tags-set check) carried the identical label text. Round 2 fixed that by requiring a
    FAILURE-shaped line, but left both ends UNANCHORED, which is its own false-FOUND:

      * shell -- `label in line` after only checking the `FAIL  ` prefix matches whenever
        `label` is a PREFIX of some OTHER, longer failing row's label. Every shell row this
        campaign names is printed by `assert_eq`/`push_run` (`FAIL  <label> (want N, got M)`)
        or the one ad hoc "budget refusal" check (`FAIL  <label> (got: ...)`) -- checked
        directly against every `MUST_KILL` row's actual `printf` call, not assumed -- so both
        shapes are `FAIL  ` + label + ` (` with nothing in between. Anchoring on that exact
        shape (not just the `FAIL  ` prefix) is what a same-prefix collision needs.
      * pytest -- `f"::{label}" in line` has the identical hole in the other direction: it
        matches whenever `label` is a PREFIX of a LONGER test name sharing the same stem
        (`..._itself` inside `..._itself_v2`), which is exactly backwards from what the old
        docstring here claimed ("keeps a label that is a prefix... from false-matching"). With
        `-q -rf` (see `SUITE`), a failure's "short test summary info" line is
        `FAILED <path>::<name>[<params>] - <reason>`; the fix parses `<name>` out of that shape
        (splitting on the first ` - ` for the reason, then on `[` for any parametrization, then
        taking the segment after the LAST `::` for the bare node name) and compares it EXACTLY
        to `label`, not as a substring in either direction.
    """
    shell_pattern = re.compile(r"^FAIL  " + re.escape(label) + r" \(")
    for line in output.splitlines():
        if shell_pattern.match(line):
            return True
        if line.startswith("FAILED "):
            head = line[len("FAILED ") :].split(" - ", 1)[0]
            node_id = head.rsplit("::", 1)[-1] if "::" in head else head
            name = node_id.split("[", 1)[0].strip()
            if name == label:
                return True
    return False


def _selftest_row_failed() -> None:
    """Prove `_row_failed` discriminates PASS-only mentions from real failures, BEFORE the
    campaign spends hours on a matcher that reads every mutation as caught. Every fixture line
    below is the EXACT shape the real suites print (see their `printf`/pytest `-rf` output),
    not an invented approximation.
    """
    label = "a main-reachable tag whose name holds U+2028 is still reachable"
    passing_only = (
        "PASS  a dev-only tag whose name holds U+2028 is not split into main-reachable "
        "names (exit 2)\n"
        f"PASS  {label} (exit 0)\n"
        "PASS  with no main branch no tag is provably reachable (exit 2)\n"
    )
    assert _row_failed(passing_only, label) is False, (
        "regression: a PASS-only line must never read as a failure -- this is the exact round-1 "
        "defect (label present, but only on a PASS line)"
    )
    failing_shell = passing_only.replace(
        f"PASS  {label} (exit 0)", f"FAIL  {label} (want 0, got 2)"
    )
    assert _row_failed(failing_shell, label) is True, (
        "a genuine shell FAIL line must be caught"
    )
    pytest_label = "test_budget_exhaustion_clears_ledger"
    passing_pytest = "...............                                                     [100%]\n15 passed in 18.65s\n"
    assert _row_failed(passing_pytest, pytest_label) is False
    failing_pytest = (
        ".F.............                                                        [100%]\n"
        "=================================== FAILURES ====================================\n"
        f"__________________________ {pytest_label} ___________________________\n"
        "    assert block is not None\n"
        "=========================== short test summary info =============================\n"
        f"FAILED scripts/tests/test_guard_internals.py::{pytest_label} - AssertionError\n"
        "1 failed, 14 passed in 18.12s\n"
    )
    assert _row_failed(failing_pytest, pytest_label) is True, (
        "a genuine pytest FAILED summary line must be caught"
    )
    # A pytest FAILURES-section header line names the test WITHOUT the "FAILED " prefix or the
    # "::" separator (pytest's `___ name ___` banner) -- must not false-match on that alone.
    header_only = (
        f"__________________________ {pytest_label} ___________________________\n"
    )
    assert _row_failed(header_only, pytest_label) is False, (
        "a bare FAILURES-section header (no FAILED summary line) must not read as caught"
    )

    # COLLISION 1 (shell, round 3's finding): a DIFFERENT, LONGER row whose label starts with
    # the target label fails, while the target itself only PASSES. Round 2's unanchored
    # `label in line` reads the target as FOUND here, because the target string IS a substring
    # of the longer row's FAIL line -- exactly backwards, since the target row never failed.
    target = "one query over MAX_GIT_SPAWNS is refused"
    longer_shell_collision = (
        f"PASS  {target} (exit 2)\nFAIL  {target} twice in a row (want 2, got 0)\n"
    )
    assert _row_failed(longer_shell_collision, target) is False, (
        "regression: a longer failing row whose label starts with the target must not make the "
        "target itself read FOUND -- the target row here only PASSED"
    )

    # COLLISION 2 (pytest, round 3's finding): a DIFFERENT, LONGER test name that has the target
    # label as its PREFIX fails (e.g. a "_v2" variant), while the target test itself is not even
    # in this output. Round 2's `f"::{label}" in line` reads the target as FOUND here too, for
    # the same reason in the other direction -- the round-2 docstring's claim that `::` prevented
    # exactly this was wrong.
    pytest_prefix_collision = (
        "F.\n"
        "=================================== FAILURES ====================================\n"
        f"__________________________ {pytest_label}_v2 ___________________________\n"
        "    assert False\n"
        "=========================== short test summary info =============================\n"
        f"FAILED scripts/tests/test_guard_internals.py::{pytest_label}_v2 - AssertionError\n"
        "1 failed, 1 passed in 1.00s\n"
    )
    assert _row_failed(pytest_prefix_collision, pytest_label) is False, (
        "regression: a longer test name with the target as a PREFIX failing must not make the "
        "target itself read FOUND -- the target test does not even appear in this output"
    )
    # And the mirror check: the longer name's OWN label must still be found against itself.
    assert _row_failed(pytest_prefix_collision, f"{pytest_label}_v2") is True, (
        "the longer test's own label must still be caught against its own FAILED line"
    )
    # A parametrized failure (`name[param]`) must still match the bare name.
    parametrized = (
        "=========================== short test summary info =============================\n"
        f"FAILED scripts/tests/test_guard_internals.py::{pytest_label}[case0] - AssertionError\n"
    )
    assert _row_failed(parametrized, pytest_label) is True, (
        "a parametrized failure must still match the bare test name before the [param] suffix"
    )


# Grace given to a group after SIGTERM before SIGKILL -- long enough for a suite's own cleanup
# (a temp repo's `trap`, a pytest fixture teardown) to run, short enough that a genuinely wedged
# group does not stall the row-check pass for long.
_GROUP_KILL_GRACE_SECONDS = 10.0


def _run_suite_bounded(cmd, cwd, timeout):
    """Run `cmd`, bounded by `timeout` seconds, killing the WHOLE process GROUP on expiry.
    NEVER RAISES `TimeoutExpired` -- a timeout is a normal, expected outcome here, not a fault.

    `subprocess.run(..., timeout=...)` (what round 2 used) only kills the DIRECT child -- here,
    the `bash -c` shell -- and lets everything IT spawned (the suite's own `bash "$1"` and
    `python3 -m pytest`, and everything THEY spawn: git subprocesses, temp-repo helpers) keep
    running, orphaned. It also RAISES on expiry, which round 2's caller did not catch, crashing
    the whole campaign after one row (`ledger_never_activated`, whose ~299s runtime legitimately
    exceeds a hand-picked 300s constant -- see `_pass2_timeout` for why pass 2 must use pass 1's
    OWN derived cap instead of guessing one).

    `start_new_session=True` puts the child in its own session, where a session leader's pid
    equals its own process group id, so `os.killpg(proc.pid, ...)` reaches the child and every
    descendant regardless of depth. Modeled in miniature on `mutate.py`'s own `_terminate_group`
    (duplicated rather than imported, since that name is private to its module) but SIGTERM
    first, SIGKILL only after a grace period -- unlike `mutate.py`'s immediate SIGKILL, because
    this caller wants the suite's own temp-repo cleanup a chance to run before the hard kill.

    Returns `(returncode, stdout_plus_stderr, elapsed)`. `returncode` is `None` on a timeout --
    the caller's signal for INDETERMINATE, never an exception.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    started = time.monotonic()
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, time.monotonic() - started
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # already gone
    try:
        stdout, _ = proc.communicate(timeout=_GROUP_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, _ = proc.communicate()
    return None, stdout, time.monotonic() - started


def _selftest_group_kill() -> None:
    """Prove `_run_suite_bounded` (a) returns a timed-out marker instead of raising, and (b)
    actually kills the whole process GROUP rather than merely the direct `bash -c` child.

    The command backgrounds a GRANDCHILD (an inner `bash -c 'sleep <marker>'`, launched via `&`
    from the outer shell, which then `wait`s) so a naive kill of only the outer `bash -c` process
    -- what `subprocess.run(..., timeout=...)` does -- would leave it running, orphaned. The
    marker is a unique numeric string embedded as the sleep duration (not a real word, so it
    cannot collide with an unrelated process on the machine) and is what `pgrep -f` searches for
    afterward: if the grandchild is still alive, group-killing did not work.
    """
    marker = f"{os.getpid()}.{time.monotonic_ns() % 1_000_000}"
    cmd = ["bash", "-c", f"bash -c 'sleep {marker}' & wait"]
    rc, _stdout, elapsed = _run_suite_bounded(cmd, REPO, timeout=1)
    assert rc is None, (
        f"expected a timed-out marker (None), got rc={rc!r} after {elapsed:.1f}s"
    )
    # Let the delivered signal actually reap the process before checking.
    time.sleep(0.3)
    check = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    assert check.returncode != 0 and not check.stdout.strip(), (
        f"a grandchild survived the group kill (marker {marker!r} still running): "
        f"{check.stdout!r}"
    )


_BASELINE_LINE_RE = re.compile(
    r"BASELINE green\s+rc=\d+\s+([\d.]+)s \(per-mutant timeout (\d+)s\)"
)


def _pass2_timeout(report) -> float:
    """The per-mutant timeout for pass 2 -- THE SAME NUMBER pass 1 used, never a hand-picked
    constant. `mutate.run()` does not expose the derived limit as a field on its `Report`, but
    it DOES print it verbatim: "BASELINE green  rc=N  <elapsed>s (per-mutant timeout <limit>s)"
    (see `mutate.py`'s `run()`). This parses `<limit>` straight out of that line, rather than
    reading `<elapsed>` and calling `mutate.derive_timeout` on it again -- `<elapsed>` in the
    text is itself rounded to 1 decimal for display, so re-deriving from it can land one second
    off the value `mutate.run()` actually used internally (measured: parsing back "114.2" and
    re-deriving gives 2284, one more than the 2283 mutate.run printed from its own unrounded
    114.153...). Reading `<limit>` directly is bit-for-bit the figure pass 1 used, with no
    double-rounding possible. Round 2's crash was a 300s constant against a row measured (by
    pass 1 itself) at ~299s -- this closes that class rather than picking a larger constant.
    """
    m = _BASELINE_LINE_RE.search(report.text)
    if not m:
        raise RuntimeError(
            "could not find mutate.run's 'BASELINE green ... (per-mutant timeout Ns)' line in "
            "its report -- pass 2 refuses to guess a timeout constant. Report text:\n"
            + report.text
        )
    return float(m.group(2))


def _verify_named_rows(
    subject: Path,
    mutations,
    must_kill: dict,
    timeout: float,
    suite=None,
    cwd=None,
) -> list[str]:
    """The part `mutate.run()` cannot do: confirm the REQUIRED row actually FAILED.

    Modeled directly on `mutate_publication_push_guard_env.py`'s function of the same name --
    same contract, same restore discipline (sha256, independent of `mutate.run()`'s own restore
    in the first pass). One subject here, so no dual-subject loop. Uses `_row_failed` (not a
    bare substring search -- see its docstring for why round 1's version was vacuous for every
    shell-suite label) and `_run_suite_bounded` (not plain `subprocess.run(..., timeout=...)`
    -- see its docstring for why round 2's version crashed the whole pass on one slow row).

    `timeout` is `_pass2_timeout(report)`'s result, passed in rather than recomputed per row:
    one baseline measurement governs every mutant's cap here, exactly as it does in pass 1.

    `suite` and `cwd` default to the campaign's real `SUITE`/`REPO` (module globals) when
    omitted -- every real call site leaves them unset. They exist as parameters solely so
    `_selftest_timeout_continues` can drive this same function with a throwaway sleeping
    command and a scratch subject, rather than needing a second, hand-duplicated copy of this
    loop to prove the timeout-then-continue behavior.

    A row that TIMES OUT is recorded as its own PROBLEM (`INDETERMINATE: ...`) and the loop
    CONTINUES to the next mutation -- pass 1's CAUGHT/SURVIVED verdict for that row is untouched
    (this pass only confirms WHICH row failed, never whether one did), and a slow row must not
    prevent every row after it from being checked.
    """
    suite = SUITE if suite is None else suite
    cwd = REPO if cwd is None else cwd
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
            rc, output, elapsed = _run_suite_bounded(suite, cwd, timeout)
        finally:
            subject.write_text(original)
        after = subject.read_text()
        if after != original:
            problems.append(
                f"{m.label}: subject NOT restored after the row-presence pass on {subject.name}"
            )
            continue
        if rc is None:
            problems.append(
                f"{m.label}: INDETERMINATE -- suite timed out after {elapsed:.1f}s "
                f"(cap {timeout:.0f}s) during the named-row pass; pass 1's verdict for this "
                "mutation stands, but this pass could not confirm which row it broke"
            )
            print(
                f"row-check  {m.label:40s} row={must_contain!r:75s} "
                f"TIMEOUT after {elapsed:.1f}s (cap {timeout:.0f}s)"
            )
            continue
        found = _row_failed(output, must_contain)
        if not found:
            problems.append(
                f"{m.label}: no FAILURE-shaped line names required row {must_contain!r} -- "
                "the mutation did not make that specific row fail (it may have reddened the "
                "suite for some OTHER reason, or not reddened it at all)"
            )
        print(
            f"row-check  {m.label:40s} row={must_contain!r:75s} "
            f"{'FOUND' if found else 'MISSING'}"
        )
    final = subject.read_text()
    if final != original:
        problems.append(f"{subject.name} not restored at end of row-presence pass")
    return problems


def _selftest_timeout_continues() -> None:
    """Prove `_verify_named_rows` (a) does not raise when the suite it runs times out, (b)
    records the timeout as its own INDETERMINATE problem rather than silently skipping the row,
    (c) CONTINUES to the next mutation instead of aborting the whole pass, and (d) still restores
    the subject byte-for-byte. This is round 3's own crash, reproduced deliberately and cheaply
    instead of trusting round 3's fix by reasoning alone: round 2 shipped without any test at
    this level and crashed the whole campaign on its first slow mutant in production.

    Uses a THROWAWAY subject in a temp dir and a `suite` that just sleeps well past a ~1s
    timeout -- both plumbed through `_verify_named_rows`'s `suite`/`cwd` parameters (added in
    round 3 specifically so this self-test needs no second, hand-duplicated copy of that
    function's loop).
    """
    with tempfile.TemporaryDirectory(prefix="pppg-selftest-") as tmp:
        subject = Path(tmp) / "scratch_subject.txt"
        original = "AAA BBB\n"
        subject.write_text(original)
        mutations = [
            mutate.Mutation("m1", "AAA", "AAAX"),
            mutate.Mutation("m2", "BBB", "BBBX"),
        ]
        must_kill = {"m1": "row-one", "m2": "row-two"}
        problems = _verify_named_rows(
            subject,
            mutations,
            must_kill,
            timeout=1,
            suite=["sleep", "5"],
            cwd=tmp,
        )
        assert len(problems) == 2, (
            f"expected exactly 2 INDETERMINATE problems (one per mutation), got "
            f"{len(problems)}: {problems}"
        )
        assert all("INDETERMINATE" in p for p in problems), (
            f"every problem here must be a timeout, not some other failure: {problems}"
        )
        assert subject.read_text() == original, (
            "the scratch subject was not restored to its original bytes after both timeouts"
        )


def _run_selftests() -> None:
    """Every self-test the campaign runs before touching the real subject, in order.

    Each is cheap (milliseconds to low seconds) and proves a mechanism a PRIOR round shipped
    without testing and then broke in production: round 1 shipped `_row_failed` vacuous, round 2
    shipped `_run_suite_bounded`'s timeout path untested and crashed the whole campaign on its
    first slow mutant. `--selftest-only` runs just this function (see `main`), so these can be
    re-verified in seconds without paying for pass 1's multi-minute baseline.
    """
    _selftest_row_failed()
    print(
        "self-test: _row_failed discriminates PASS-only mentions from real failures, "
        "including same-prefix collisions in both directions -- OK"
    )
    _selftest_group_kill()
    print(
        "self-test: _run_suite_bounded times out without raising and kills the whole "
        "process group -- OK"
    )
    _selftest_timeout_continues()
    print(
        "self-test: _verify_named_rows survives a per-row timeout (INDETERMINATE, "
        "continues, restores) without raising -- OK"
    )


def main() -> int:
    if "--selftest-only" in sys.argv[1:]:
        _run_selftests()
        print("\nRESULT: PASS rc=0 (self-tests only)")
        return 0

    _run_selftests()
    print()

    print("=" * 88)
    print("PASS 1: mutate.run() scoring against", SUBJECT.name)
    print("=" * 88)
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO)
    print(report.text)

    print()
    print("=" * 88)
    print("PASS 2: named-row verification against", SUBJECT.name)
    print("=" * 88)
    pass2_timeout = _pass2_timeout(report)
    print(
        f"pass 2 per-mutant timeout: {pass2_timeout:.0f}s (derived from pass 1's own "
        "baseline measurement -- see _pass2_timeout)"
    )
    problems = _verify_named_rows(SUBJECT, MUTATIONS, MUST_KILL, pass2_timeout)

    print()
    print("=" * 88)
    ok = report.status == "PASS" and not problems
    if problems:
        print(f"ROW-CHECK: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
    status = report.status if report.status != "PASS" else ("PASS" if ok else "FAIL")
    rc = 0 if status == "PASS" else (2 if status == "ERROR" else 1)
    print(
        f"RESULT: {status} rc={rc} mutate_verdict={report.verdict!r} "
        f"row_check_problems={len(problems)}"
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
