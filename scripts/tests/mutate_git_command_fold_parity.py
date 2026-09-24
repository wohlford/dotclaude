#!/usr/bin/env python3
# Script: mutate_git_command_fold_parity.py
# Purpose: Mutation campaign for the heredoc work in scripts/lib/git_command.py -- the 2026-09-18
#          fold/heredoc parity port and the 2026-09-19 reading-variant redesign that replaced its
#          context-gated drop/join decision -- proving every mechanism those changes added or
#          altered is pinned BY NAME, not merely that the suite goes red somewhere
# Usage: scripts/tests/mutate_git_command_fold_parity.py
"""Mutate the heredoc machinery and require a pytest-resident row to catch it, BY NAME.

A green suite is also exactly what the suite would show with this change's mechanism reverted, if
nothing pinned that mechanism BY NAME. This campaign is the measurement that tells those apart: one
mutation per mechanism the change added or altered, each paired with the specific row it is
REQUIRED to kill.

SUBJECT = scripts/lib/git_command.py. Two changes are under test:

**2026-09-18, the fold/heredoc parity port:**

  - `fold_continuations`'s parity check (only an ODD trailing backslash run before LF continues a
    line) and its END-OF-INPUT backslash-drop branch;
  - `_bash_unquoted_heredoc`'s physical-line JOIN (an odd trailing backslash run joins the next
    physical line before the terminator comparison) and its logical-line TAB STRIP (`<<-` strips
    leading tabs from the JOINED line, not the raw physical line);
  - `_unescape_heredoc`'s two backslash-run rules: the general pair-collapse ("any other run") and
    the opener-specific rule (an EVEN run directly before `$(`/backtick is kept whole, not
    collapsed to one), plus the RAW-copy of `$( ... )` spans that keeps a nested substitution's own
    backslashes untouched;
  - `_consume_heredoc_body`'s unquoted top-level path: the recursive re-mask of the processed body
    and the `MAX_CONTEXT_DEPTH` bound guarding that recursion;
  - the `quoted` flag itself, threaded from `_read_heredoc_delimiter` through `mask_heredoc_quotes`'s
    `pending` list into `_consume_heredoc_body`, which is what selects between the literal (quoted)
    path and bash's-own-pass (unquoted) path in the first place.

**2026-09-19, readings as walk VARIANTS rather than duplicated text:**

  - `_ReadingState.next_reading` -- the drop/join reading is now an INPUT, not a question about
    context, and the ambiguity predicate (`dropped != body`) is what consumes an ordinal;
  - `_reading_assignments` -- the enumeration itself, and its FIXED order: primary (all-drop)
    first, all-join second. The order is load-bearing in both primitives;
  - `_union_max_multiset` -- multiplicity is the MAX across variants, never a set;
  - `_walk_context`'s all-raise rule (raise only when EVERY variant raised) and its in-band
    `_indeterminate_invocation`, appended by BOTH branches that can lose a reading -- a variant
    that raised, and a budget that truncated;
  - `iter_context_token_streams`'s mirror of all three: the same enumeration, the same all-raise
    rule, and `_indeterminate_stream`. The two primitives disagreeing about what a context contains
    is a fail-open, not a mismatch: measured, the walk blocked the `<<'(x'` witness at rc 2 while
    push-guard and the timing guard returned rc 0;
  - `_ParseBudget` threading -- ONE budget for the whole walk, passed into every child.

SUITE runs BOTH `test_git_command.py` and `test_git_command_properties.py` (the opener-parity and
removal-only properties this port depends on live in the latter) -- no corpus row in
`test_guard_corpus.py` is named by this campaign, so importing that module is not needed here.

## Naming the required row: explicit parametrize ids

Several mutants below flip rows of one parametrized test,
`test_a_continuation_folds_only_where_bash_folds(command, want)`
(`scripts/tests/test_git_command.py`, section "line continuations: fold only where bash does").
That table's rows are computed from module-level variables, so pytest's automatic ids would be
positional (`command0-want0`) and unverifiable as literal text -- a first draft of this campaign
therefore named the bare FUNCTION for all ten, which only confirms that SOME row failed. The table
carries explicit `ids=[...]`, written as literal strings in the test file, so `MUST_KILL` names the
exact row each mutant is traced to flip and `_assert_must_kill_names_are_live` checks that id as
source text. The reading-variant mutants name their own non-parametrized functions directly.

## Mutate the code, never the fixture lists

Every mutation below edits `git_command.py`'s CODE, never `test_git_command.py`. `_OPAQUE`-style
fixture text does not apply here: the rows this campaign relies on are read as plain source text by
`_assert_must_kill_names_are_live` (function-name presence only, never executed or imported), never
mutated themselves.

## Every kill below was MEASURED, in a scratch mirror, before it was written

Each mutant was applied to a COPY of `git_command.py` outside the repository and the full SUITE run
against it, so the `MUST_KILL` names here are observed FAILED rows rather than hand-traced
predictions. The baseline was run first and scored 840 passed / 0 failed on the unmutated copy: an
already-red suite scores every mutation CAUGHT and the sweep reads flawless. Where a kill set is
noted below it is the measured set of FAILED rows, because a mutant that duplicates another is
CAUGHT for free and inflates the score while measuring nothing -- the two `streams_*` mutants share
a kill set on purpose, and that is recorded at each one rather than left to be rediscovered.

## What corpus generation alone cannot kill -- the raise rule

The all-raise rule fires on NO command the spike could generate: 0 cases in 5400 generated scripts
and 0 in a 5400-case adversarial enumeration where one variant raises and another parses. The one
such case is Fable's `<<'(x'` witness, built by HAND, and it is a fixture only because Task 4 added
it. So `indeterminate_signal_dropped`, `raise_when_any_raises`, `streams_raise_when_any_raises` and
`streams_marker_dropped` are killable ONLY because that hand-built witness exists: their
`MUST_KILL` rows (`test_a_variant_that_raises_does_not_hide_a_reading_that_parses` and
`test_both_primitives_carry_the_same_in_band_marker`) are both written around it. Delete the
witness and all four go SURVIVED while every generated corpus stays green -- "a campaign measures
REDUNDANCY, not OMISSION" in miniature. A row depending on corpus generation alone would have
scored CAUGHT for free.

## NOT written: `parse_cap_removed`

Making `_ParseBudget.spend()` always return True does not terminate on the adversarial ladder, and
`mutate.py` scores a non-terminating mutant `TIMEOUT` -- a status distinct from `CAUGHT`. A mutant
that can only time out is not evidence of coverage, so the cap's presence is not asserted that way.
What IS asserted is that the cap is threaded correctly (`budget_not_threaded_into_children`) and
that exhausting it degrades to the in-band marker rather than to silence
(`truncation_not_signalled`).

## Mechanisms measured as UNPINNED -- deliberately absent, not overlooked

Each of the following was written as a mutant, applied in the scratch mirror, and SURVIVED the full
840-row suite. None is shipped: a mutant nothing can kill makes the campaign fail on a defect in the
SUITE rather than report one. They are recorded here because an absent mutant is otherwise
indistinguishable from a mechanism nobody thought of.

  - `top_level=not contexts and not backtick_open` -> `not contexts`, and the backtick toggle's
    `quote != "'"` guard. Since 2026-09-19 `top_level` gates ONLY the unquoted-body pass, so both
    mutants are invisible to every existing backtick row -- all of those use a QUOTED delimiter,
    and for a quoted heredoc `top_level` decides nothing. Pinning them again needs a row with an
    UNQUOTED heredoc inside backticks; the suite has none. NOTE that the anchors are still unique,
    so `_assert_anchors_are_unique` would NOT have caught this: the stale half was the RATIONALE,
    which is silent.
  - `mask_heredoc_quotes(body, depth + 1, state)` -> dropping `state`, which un-shares the ordinal
    namespace so heredocs inside an unquoted body become drop-only forever. Needs a row with an
    ambiguous QUOTED heredoc nested inside an UNQUOTED body; the suite has none.
  - the walk's and the streams primitive's `not parsed_any and truncated` branches raising instead
    of emitting the marker. Reaching either needs the budget exhausted BEFORE any variant of a
    context parses. In a CHILD context that state is unobservable -- the child's raise is caught by
    the parent's own `except ValueError`, which then appends the very marker the mutant removed, so
    the output is byte-identical. Only a TOP-LEVEL context at `MAX_TOTAL_PARSES = 0` reaches it,
    and no row sets 0. The plan named `test_an_exhausted_cap_yields_the_marker_and_never_raises` as
    this mutant's killer; measured, that row exercises the OTHER truncation branch (some variant
    parsed, then the budget ran out), which `truncation_not_signalled` owns.
  - `_indeterminate_stream`'s token ORDER (`["git", MARKER]` reversed) and
    `_indeterminate_invocation`'s `effective_dir=None`. Both are contracts with the three GUARDS,
    whose suites this campaign does not run; nothing in the tokenizer's own suite reads either.

## NOT run here

This module is WRITTEN but deliberately not executed as part of authoring it: the campaign mutates
`scripts/lib/git_command.py` in place while scoring, and running it here would race any peer reading
or testing that file concurrently -- and a campaign killed mid-run leaves a live mutant in a
security tokenizer. Run it last, alone, once nothing else is reading or writing the subject file.

Budget it: 24 mutants x two passes x a ~45 s suite is roughly an hour, so it needs
`~/.claude/scripts/run-long.sh` with an artifact outside the repo, and a `--expect` matching the
verdict's SHAPE rather than its passing value.
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
    "scripts/tests/test_git_command_properties.py",
    "-q",
    "-p",
    "no:cacheprovider",
]

# MUST_KILL values naming a bare FUNCTION (the non-parametrized killers) rather than a
# bracketed parametrize id -- carved out of the literal-id liveness check below. A bare name that
# is NOT listed here falls through to the bracket branch and is reported as malformed, so this set
# going stale is LOUD rather than silent.
_UNIT_TEST_NAMES = frozenset(
    {
        "test_deeply_nested_unquoted_heredocs_still_read_the_innermost_command",
        "test_an_unquoted_dash_body_strips_tabs_before_the_terminator_comparison",
        "test_a_join_reading_keeps_the_continuation_and_a_drop_removes_it",
        "test_a_reading_state_numbers_only_ambiguous_heredocs",
        "test_a_substitution_body_ending_in_a_continuation_records_the_joined_reading",
        "test_both_readings_are_recorded_for_an_ambiguous_heredoc",
        "test_both_readings_survive_the_cap_at_its_exact_cost",
        "test_both_primitives_carry_the_same_in_band_marker",
        "test_an_invocation_run_twice_is_recorded_twice",
        "test_a_variant_that_raises_does_not_hide_a_reading_that_parses",
        "test_an_exhausted_cap_yields_the_marker_and_never_raises",
        "test_the_streams_carry_every_reading_primary_first",
        "test_the_streams_primitive_never_raises_where_the_walk_reads",
        "test_the_enumeration_yields_the_mixed_assignments_not_only_the_extremes",
    }
)

MUTATIONS = [
    # ---------- 2026-09-18: the fold / heredoc parity port ----------
    #
    # `fold_continuations` must fold ONLY
    # an ODD trailing backslash run before LF -- an EVEN run is a literal backslash pair, not a
    # continuation (measured: bash runs `echo a\\` + LF + `git push origin dev` as TWO commands).
    # Widening `run % 2 == 1` to `run >= 1` folds an even run too: the escaped-backslash row has
    # run=2 (even), so the
    # mutant joins "echo a" directly onto "git push origin dev" with no separator, fusing "a" and
    # "git" into one word ("agit"-shaped) and losing the push.
    # MEASURED kill set: exactly `[escaped-backslash-lf]`.
    mutate.Mutation(
        "parity_check_widened_to_any_run",
        '        if run % 2 == 1 and j < n and command[j] == "\\n":\n',
        '        if run >= 1 and j < n and command[j] == "\\n":\n',
    ),
    # Until 2026-09-18 this pass folded `\` + CRLF the
    # same as `\` + LF; that was a fail-open (bash escapes the CR and the LF still ends the command,
    # so folding hides a real command boundary). The current code has NO CRLF branch at all -- a
    # backslash directly before `\r` never matches `command[j] == "\n"`. This mutant reintroduces the
    # retired CRLF-fold branch verbatim (odd run, `\r\n` immediately after): the backslash-CRLF
    # row is
    # `echo a\` + CRLF + `git push origin dev`, which the mutant now folds into "echo a" glued
    # directly onto "git push origin dev" (no separator), losing the "git" word and the push.
    # MEASURED kill set: `[backslash-crlf]` plus
    # `test_a_backslash_before_crlf_is_not_a_continuation`.
    mutate.Mutation(
        "crlf_backslash_refolded",
        '            out.append("\\\\" * (run - 1))\n'
        "            i = j + 1\n"
        "            continue\n"
        "        if j == n:\n",
        '            out.append("\\\\" * (run - 1))\n'
        "            i = j + 1\n"
        "            continue\n"
        "        if (\n"
        "            run % 2 == 1\n"
        "            and j + 1 < n\n"
        '            and command[j] == "\\r"\n'
        '            and command[j + 1] == "\\n"\n'
        "        ):\n"
        '            out.append("\\\\" * (run - 1))\n'
        "            i = j + 2\n"
        "            continue\n"
        "        if j == n:\n",
    ),
    # Backslashes at END OF INPUT (no
    # trailing newline) must be DROPPED -- bash 3.2 drops an unescaped one and 5.3 keeps it literal,
    # which git then rejects as a ref, so dropping is the reading that runs under both. Deleting this
    # branch falls through to the normal path just below it (`out.append(command[i:j]); i = j`),
    # keeping the trailing backslash(es) literal instead. The end-of-input rows are `git status`
    # followed by
    # one, then three, trailing backslashes with no newline: with the drop removed, `tokenize`
    # (shlex-based) meets a trailing unescaped backslash with nothing after it, which is a shlex
    # parse error ("No escaped character") rather than a clean `status` token.
    # MEASURED: the row dies with `ValueError: No escaped character`, not an AssertionError.
    mutate.Mutation(
        "end_of_input_drop_removed",
        "        if j == n:\n"
        "            # backslashes at END OF INPUT: bash 3.2 drops an unescaped one (and, measured, more\n"
        "            # in some contexts); 5.3 keeps them literal, which git then rejects as a command or\n"
        "            # ref. Dropping them all yields the reading that RUNS under every version.\n"
        "            i = j\n"
        "            continue\n",
        "",
    ),
    # `quoted` is what selects the
    # literal (quoted-delimiter) heredoc path over bash's-own-pass (unquoted) path in
    # `_consume_heredoc_body`. Forcing it False at the one call site that threads it from
    # `mask_heredoc_quotes`'s `pending` list makes a QUOTED `<<'EOF'` delimiter get the UNQUOTED
    # treatment. The quoted-body-even-run row (`bash <<'EOF'` / `git log -1 dev\\` -- two
    # backslashes, an EVEN run -- / `EOF`, expecting the arg `dev\`, one literal backslash) then
    # runs through `_bash_unquoted_heredoc` and its `_unescape_heredoc` pass instead of being
    # copied literally: the even pair collapses to ONE backslash (the general pair-collapse rule),
    # which is now an ODD trailing run, so the unquoted path's own `_drop_final_continuation` call
    # then strips it entirely -- losing the backslash outright rather than keeping the single
    # literal one.
    # NOTE the anchor keeps `top_level=not contexts and not backtick_open` as CONTEXT only: that
    # argument is no longer part of the drop/join decision, and this mutant does not touch it.
    mutate.Mutation(
        "quoted_flag_forced_false",
        "                    strip_tabs,\n"
        "                    out,\n"
        "                    quoted,\n"
        "                    top_level=not contexts and not backtick_open,\n",
        "                    strip_tabs,\n"
        "                    out,\n"
        "                    False,\n"
        "                    top_level=not contexts and not backtick_open,\n",
    ),
    # `_bash_unquoted_heredoc` joins a physical line ending in
    # an odd backslash run onto the NEXT physical line before comparing to the delimiter -- this is
    # bash's own within-heredoc continuation handling, separate from the top-level fold. Forcing the
    # join condition False (via `if False and ...`, keeping the branch body inert) disables it. The
    # PRESERVE row `bash <<EOF` / `git log -1 dev\` / `EOF` (single trailing backslash, an ODD run)
    # currently JOINS onto the "EOF" line, producing "devEOF" as the recorded refspec -- an intended
    # bash-faithful quirk. With the join disabled, the terminator IS found on its own line, and
    # `_drop_final_continuation` (still active) strips the dangling backslash, yielding plain "dev"
    # instead of the expected "devEOF" -- a regression on a row this port exists to PRESERVE. This
    # is the row where a naive one-line disable was verified NOT to accidentally also disable
    # `_drop_final_continuation`, keeping this mutant distinct from `unescape_pair_collapse_disabled`
    # below.
    mutate.Mutation(
        "heredoc_line_join_disabled",
        "        if eol != -1 and _odd_trailing_backslashes(piece):\n"
        "            logical.append(piece[:-1])\n"
        "            j = nxt\n"
        "            continue\n",
        "        if False and eol != -1 and _odd_trailing_backslashes(piece):\n"
        "            logical.append(piece[:-1])\n"
        "            j = nxt\n"
        "            continue\n",
    ),
    # `_unescape_heredoc`'s general
    # ("any other run") branch collapses a backslash run to `run // 2 + run % 2` -- each pair to one
    # backslash, an odd leftover keeping its character. Replacing that with `run` (no collapsing at
    # all) leaves a run's parity UNCHANGED by the unescape pass. The PRESERVE row `bash <<EOF` /
    # `git pu\\` (two backslashes) / `sh origin dev` / `EOF` depends on exactly this collapse: the
    # pair must become ONE backslash (odd) so the later top-level `fold_continuations` pass folds it,
    # joining "pu" and "sh" into "push" and recording the push. With collapsing disabled the pair
    # stays TWO backslashes (even), the top-level fold never fires, "pu" and "sh" stay on separate
    # (newline-then-`;`-separated) commands, and no `git push` is ever recorded.
    mutate.Mutation(
        "unescape_pair_collapse_disabled",
        '            out.append("\\\\" * (run // 2 + run % 2))\n'
        "            k = j\n"
        "            continue\n",
        '            out.append("\\\\" * run)\n            k = j\n            continue\n',
    ),
    # `_unescape_heredoc`'s
    # opener-specific branch: an EVEN backslash run directly before `$(`/backtick must be kept AS IS
    # (`out.append("\\" * run)`), because the outer shell will still see the run as even and expand
    # the opener -- collapsing it to a single backslash was the measured REGRESSION this branch
    # exists to prevent (see its docstring): the scanner then reads `\$(` as escaped and the
    # substitution vanishes. Reintroducing exactly that regression (`out.append("\\")`, one backslash
    # regardless of run length) breaks the even-run-before-opener row: `echo "\\$(git status)"` (two
    # backslashes, even) inside an
    # unquoted body -- `dev` records the nested `status` push; the mutant makes the opener read as
    # escaped and the invocation disappears. The sibling backtick row and the four-backslash row in
    # the same parametrize are also expected to flip.
    mutate.Mutation(
        "opener_even_run_collapsed_to_one",
        "                if run % 2 == 0:\n"
        '                    out.append("\\\\" * run)\n'
        "                    k = j\n"
        "                    continue\n",
        "                if run % 2 == 0:\n"
        '                    out.append("\\\\")\n'
        "                    k = j\n"
        "                    continue\n",
    ),
    # `_unescape_heredoc`
    # copies a `$( ... )` span RAW, untouched -- the outer shell expands it from its own raw text, so
    # this pass must not apply ITS backslash rules to what is inside. Disabling the special case (via
    # `if False and ...`) makes the scanner fall through to ordinary char-by-char processing instead,
    # which DOES apply the general collapse rule to the backslash run inside the substitution. The
    # row `bash <<EOF` / `x=$(echo x\\` / `git status)` / `EOF` (two backslashes before the embedded
    # newline, inside the still-open `$(`) currently keeps that pair verbatim inside the raw-copied
    # span, so the OUTER top-level fold never sees an odd run there and the embedded newline stays a
    # command separator, exposing `git status` as its own command. With the skip removed, the pair
    # collapses to one backslash (odd) before that same embedded newline, and the subsequent top-level
    # `fold_continuations` pass folds it, fusing "x" and "git" into one word and losing the invocation.
    mutate.Mutation(
        "unescape_substitution_raw_copy_removed",
        '        if ch == "$" and body.startswith("$(", k):\n',
        '        if False and ch == "$" and body.startswith("$(", k):\n',
    ),
    # The unquoted top-level path in
    # `_consume_heredoc_body` re-masks its own processed body so a heredoc NESTED inside an unquoted
    # body gets its own fold/join/unescape pass -- without this a nested `<<EOF2 ... EOF2` sitting
    # inside an outer unquoted body is never recognised as a heredoc at all and is carried through as
    # inert literal text. The nested row (`bash <<EOF` / `bash <<EOF2` / `git sta` + four backslashes
    # / `tus` / `EOF2` / `EOF`, expecting `status`) needs exactly this second pass to fold the inner
    # backslash run and glue "sta" onto "tus". Replacing the recursive call with a no-op assignment
    # (`body = body`) disables it.
    # RE-ANCHORED 2026-09-19: the call now threads `state`, so the pre-change anchor
    # `mask_heredoc_quotes(body, depth + 1)` no longer occurs.
    mutate.Mutation(
        "nested_heredoc_remask_removed",
        "        body = mask_heredoc_quotes(body, depth + 1, state)\n",
        "        body = body\n",
    ),
    # `MAX_CONTEXT_DEPTH` bounds the recursive re-mask above: past it the unquoted top-level
    # path is skipped and the body is copied verbatim, so pathological nesting neither recurses
    # without limit nor raises. Removing `depth < MAX_CONTEXT_DEPTH` from the path's condition lets
    # the recursion run past Python's own call stack, and the depth row (1000 nested unquoted
    # heredocs around `git status`) ERRORS with `RecursionError` -- never a hang. The row once
    # nested only 400 levels, which the unbounded recursion still completed, so this mutant
    # SURVIVED until the row was deepened.
    # MEASURED kill set: exactly that one row.
    mutate.Mutation(
        "depth_guard_removed",
        "    if not quoted and top_level and depth < MAX_CONTEXT_DEPTH:\n",
        "    if not quoted and top_level:\n",
    ),
    # `<<-`
    # must strip leading tabs from the JOINED logical line (post physical-line-join), not from each
    # raw physical line, so a continued line's tabs are stripped only once the join has happened.
    # Disabling the strip (`if False and strip_tabs:`) is the exact killer named in the test's own
    # comment: `test_an_unquoted_dash_body_strips_tabs_before_the_terminator_comparison` asserts
    # `bash <<-EOF` / TAB `echo x` / TAB `EOF` / `echo "` RAISES `ValueError`, because with the strip
    # applied the terminator line TAB+"EOF" reduces to "EOF" and is found, leaving the trailing
    # `echo "` as top-level text with an unbalanced quote. Without the strip, TAB+"EOF" never equals
    # the bare delimiter "EOF", the terminator is never found, the whole remaining text (including
    # `echo "`) is swallowed into the (unterminated) heredoc body, and its quote is neutralised as
    # body content instead of raising -- so the `pytest.raises(ValueError)` block sees no exception and
    # the test itself fails (matches the test's own comment, "Without the strip the terminator is
    # missed and the quote is neutralised as body text").
    mutate.Mutation(
        "unquoted_dash_logical_line_tab_strip_removed",
        '        if strip_tabs:\n            line = line.lstrip("\\t")\n',
        '        if False and strip_tabs:\n            line = line.lstrip("\\t")\n',
    ),
    # ---------- 2026-09-19: readings as walk VARIANTS ----------
    #
    # `next_reading` is the whole drop/join decision, now an INPUT rather than a context question.
    # Forcing it False makes every variant read DROP, so `_reading_assignments`' all-join vector
    # produces text identical to the primary and the join reading vanishes everywhere. That is the
    # under-read direction and it is the BYPASS one: only the join can mangle `dev` into `devEOF`,
    # so losing it can lose a push target.
    # MEASURED kill set: 20 rows. Its MUST_KILL is the MASK-level row
    # `test_a_join_reading_keeps_the_continuation_and_a_drop_removes_it`, which asserts the emitted
    # TEXT rather than the walk's output -- chosen because it distinguishes this mutant from
    # `assignments_only_primary` below, whose 20-row kill set does NOT contain it (there the mask
    # still honours a JOIN assignment; nothing ever hands it one).
    mutate.Mutation(
        "reading_forced_to_drop",
        "        return self.readings.get(ordinal, False)\n",
        "        return False\n",
    ),
    # The mirror: forcing `next_reading` True makes every variant read JOIN, so the PRIMARY becomes
    # the joined reading and the drop -- what bash actually runs at the top level, and what fixes
    # invocation ORDER and cwd -- is gone. `[quoted-body-final-continuation]` expects
    # `[(push, [origin, dev]), (push, [origin, devEOF])]`; the mutant yields the joined record
    # first and never the clean one.
    # MEASURED kill set: 18 rows, including that one.
    mutate.Mutation(
        "reading_forced_to_join",
        "        return self.readings.get(ordinal, False)\n",
        "        return True\n",
    ),
    # The ambiguity PREDICATE, one level above the reading: `dropped != body` is what decides that a
    # heredoc is ambiguous at all, and it is what consumes an ordinal. Disabling it means no ordinal
    # is ever consumed, so `_ReadingState.count` stays 0, `_reading_assignments(0)` returns the
    # single primary, and every body is emitted verbatim -- the JOIN reading, unconditionally, with
    # no enumeration at all.
    # Distinct from `reading_forced_to_drop`/`_join`: it is the only one of the three whose kill set
    # (22 rows, the largest) contains `test_a_reading_state_numbers_only_ambiguous_heredocs`, which
    # asserts the COUNT and nothing about a reading. If that row ever goes green here while the
    # other two stay red, the counter has stopped being the enumeration's basis.
    mutate.Mutation(
        "ambiguity_never_detected",
        "        dropped = _drop_final_continuation(body)\n        if dropped != body:\n",
        "        dropped = _drop_final_continuation(body)\n"
        "        if False and dropped != body:\n",
    ),
    # The ENUMERATION itself. `count >= 0` makes `_reading_assignments` return `[{}]` for every
    # count, so both primitives walk the primary only and no variant is ever tried. The mask still
    # honours a JOIN assignment perfectly -- nothing ever hands it one -- which is exactly why its
    # MUST_KILL must be a row reading the WALK's output rather than the mask's:
    # `test_a_substitution_body_ending_in_a_continuation_records_the_joined_reading` asserts
    # `(pushEOF, [])` is among the recorded invocations of an ambiguous `$( )` command.
    # MEASURED kill set: 20 rows, NOT including
    # `test_a_join_reading_keeps_the_continuation_and_a_drop_removes_it` -- the discriminator
    # against `reading_forced_to_drop`.
    mutate.Mutation(
        "assignments_only_primary",
        "    if count <= 0:\n        yield {}\n        return\n",
        "    if count >= 0:\n        yield {}\n        return\n",
    ),
    # The ORDER of the enumeration, which is load-bearing in both primitives: the primary (all-drop)
    # must come FIRST, because it fixes invocation order and cwd for the walk, and because
    # `_find_first_push` returns on the FIRST push-carrying segment with the timing guard returning 0
    # when that one is authorized -- so a join-first order lets a body's last line authorize a push
    # it does not authorize. Swapping the two head entries keeps BOTH readings present (so no
    # invocation is lost) and only reverses which is primary, which is why a row asserting
    # membership cannot see it and a row asserting the LIST can.
    # MEASURED kill set: 14 rows, including `test_both_readings_are_recorded_for_an_ambiguous_heredoc`
    # (which asserts the exact ordered list `[(push, []), (pushEOF, [])]`) and
    # `test_the_streams_carry_every_reading_primary_first`. Named on the former, so its attribution
    # stays distinct from `streams_primary_last`, which is streams-only.
    mutate.Mutation(
        "all_join_becomes_primary",
        "    yield {}  # the primary: all-drop, what bash does at the top level\n"
        "    yield {i: True for i in range(count)}  # all-join, the other extreme\n",
        "    yield {i: True for i in range(count)}  # all-join, the other extreme\n"
        "    yield {}  # the primary: all-drop, what bash does at the top level\n",
    ),
    # The union is a MAX-MULTISET, not a set: a key's multiplicity is the MAX across variants, so a
    # command that genuinely runs one invocation TWICE keeps both records. `counts[key] == 0` is
    # first-occurrence-wins, i.e. exactly the set semantics revision 2 specified -- measured on the
    # differential's default seed as hidden_valid=92 against 0 for the multiset. The killer row is
    # built so the duplicate lives in a VARIANT rather than the primary (drop reads
    # `[status, stat]`, join reads `[status, status]`), because the primary of a plain
    # `git status; git status` already carries both and such a row cannot detect set semantics.
    # MEASURED kill set: exactly ONE row -- no other assertion in either suite sees it.
    mutate.Mutation(
        "union_is_a_set",
        "        seen[key] += 1\n        if seen[key] > counts[key]:\n",
        "        seen[key] += 1\n        if counts[key] == 0:\n",
    ),
    # The walk's in-band ambiguity signal. When some variant was lost -- it raised, or the budget
    # truncated the enumeration -- ONE `_indeterminate_invocation` is appended so the loss is
    # visible to a consumer instead of silently discarded. Dropping the append (`results` passed
    # through unchanged) is the silent-loss direction: push-guard and the timing guard treat the
    # marker's subcommand as push-shaped, and without it the timing guard fails OPEN.
    # MEASURED kill set: 4 rows. Killable ONLY because the hand-built `<<'(x'` witness exists --
    # see "What corpus generation alone cannot kill" above.
    mutate.Mutation(
        "indeterminate_signal_dropped",
        "        results = results + [_indeterminate_invocation()]\n",
        "        results = list(results)\n",
    ),
    # The same append, but reached by the OTHER branch: dropping `or truncated` keeps the marker for
    # a variant that RAISED and loses it for a budget that TRUNCATED. The two branches are reached
    # differently -- `continue` versus `break` -- so a fix for one need not cover the other, and
    # this mutant is what separates them.
    # MEASURED kill set: 3 rows, and unlike `indeterminate_signal_dropped` it does NOT contain
    # `test_a_variant_that_raises_does_not_hide_a_reading_that_parses`. That difference is the
    # evidence the two mutants are not one mutant written twice.
    mutate.Mutation(
        "truncation_not_signalled",
        "    if first_error is not None or truncated:\n"
        "        results = results + [_indeterminate_invocation()]\n",
        "    if first_error is not None:\n"
        "        results = results + [_indeterminate_invocation()]\n",
    ),
    # The ALL-RAISE rule in the walk: raise only if EVERY variant raised. A quoted delimiter may
    # contain `(`, so the all-join reading of a command both bashes RUN can raise `unterminated
    # command substitution`; propagating that raise discards a correct read and hands the timing
    # guard -- and push-guard behind a hidden git word -- a fail-open. Re-raising on the first
    # failure restores exactly that.
    # MEASURED kill set: 2 rows, both dying with `ParseAmbiguity: unterminated command substitution`
    # rather than an AssertionError -- so this mutant is attributable by the EXCEPTION as well as by
    # the row. Killable only because of the hand-built witness.
    mutate.Mutation(
        "raise_when_any_raises",
        "        except ValueError as exc:\n"
        "            first_error = first_error if first_error is not None else exc\n"
        "            if len(unreadable) < _MAX_REPORTED_READINGS:\n"
        "                unreadable.append((joined, _reading_category(exc)))\n"
        "            continue\n",
        "        except ValueError:\n            raise\n",
    ),
    # The same rule in `iter_context_token_streams`, which is a SEPARATE implementation: the two
    # primitives must agree about what a context contains, because the publication guard pairs
    # `_exported_injection_reason` with `_find_block_reason` and the timing guard correlates
    # `_find_first_push` with `_push_target_dirs` BY ORDER. Note the deeper indentation in the
    # anchor: it is what distinguishes `_collect`'s handler from `_walk_context`'s above.
    # MEASURED kill set: exactly `test_both_primitives_carry_the_same_in_band_marker`, the SAME
    # single row as `streams_marker_dropped` below. That is not a duplicate mutant -- one makes the
    # primitive RAISE where it should degrade, the other makes it go SILENT where it should signal,
    # and the row catches both because it carries a CONTROL assertion (the real reading was
    # produced) as well as the marker assertion. If that row is ever split, each half must keep one
    # of the two.
    mutate.Mutation(
        "streams_raise_when_any_raises",
        "            except ValueError as exc:\n"
        "                # This reading contributed nothing; leave no partial trace behind.\n"
        "                del streams[mark:]\n"
        "                first_error = first_error if first_error is not None else exc\n"
        "                continue\n",
        "            except ValueError:\n                raise\n",
    ),
    # The SCOPE of that try, which is the defect it replaced rather than a variation on it.
    # Guarding only `_prepare` -- leaving `tokenize` and the child recursion outside -- means a
    # reading whose CHILD context is unparseable raises out of the whole primitive instead of
    # being one failed reading. Measured: push-guard and the publication guard went from BLOCK
    # on shipped `dev` to ALLOW on a push both bashes run, 24 such regressions in 432
    # push-carrying shapes, and the pre-fix tokenizer reports 13 divergences over the ambiguous
    # corpus that `test_the_streams_primitive_never_raises_where_the_walk_reads` walks.
    # REPLACED 2026-09-22. The first version of this mutant left `except` after unindented
    # statements, so the subject did not PARSE: pytest exited rc=2 at collection, both test files
    # reported as failures, and pass 1 scored it CAUGHT having exercised nothing. Pass 2 is what
    # caught that -- the named row was absent from the output. A mutant must reach the mechanism,
    # and a syntax error reaches every row at once for free.
    mutate.Mutation(
        "streams_child_recursion_outside_the_try",
        "            try:\n"
        "                outer, nested, _ = _prepare(ctx.text, ctx.depth, readings)\n"
        "                streams.append(strip_redirects(tokenize(newlines_to_separators(outer))))\n"
        "                for child in nested:\n"
        "                    _collect(child, primary_chain=free)\n"
        "            except ValueError as exc:\n"
        "                # This reading contributed nothing; leave no partial trace behind.\n"
        "                del streams[mark:]\n"
        "                first_error = first_error if first_error is not None else exc\n"
        "                continue\n"
        "            parsed_any = True\n",
        "            try:\n"
        "                outer, nested, _ = _prepare(ctx.text, ctx.depth, readings)\n"
        "            except ValueError as exc:\n"
        "                # This reading contributed nothing; leave no partial trace behind.\n"
        "                del streams[mark:]\n"
        "                first_error = first_error if first_error is not None else exc\n"
        "                continue\n"
        "            streams.append(strip_redirects(tokenize(newlines_to_separators(outer))))\n"
        "            for child in nested:\n"
        "                _collect(child, primary_chain=free)\n"
        "            parsed_any = True\n",
    ),
    # `_indeterminate_stream` is the STREAM form of the walk's marker, and leaving it out was
    # measured as a REGRESSION against shipped `dev`: on the `<<'(x'` witness the walk blocked at
    # rc 2 while push-guard and the timing guard, both stream-shaped, returned rc 0 -- turning a
    # `dev` BLOCK into an ALLOW. This mutant restores that asymmetry.
    # MEASURED kill set: the same single row as `streams_raise_when_any_raises`; see the note there
    # for why both are kept.
    mutate.Mutation(
        "streams_marker_dropped",
        "        if first_error is not None or truncated:\n"
        "            streams.append(_indeterminate_stream())\n",
        "        if first_error is not None or truncated:\n            pass\n",
    ),
    # The streams primitive's ORDER, mutated WITHOUT touching `_reading_assignments`: inserting each
    # variant's stream at the front leaves every reading present and reverses only their order, so
    # the primary lands LAST among its context's streams. That is the shape the invariant exists to
    # forbid -- `_find_first_push` returns on the first push-carrying segment, so a join-first order
    # lets a body ending `ALLOW_GIT_WRITE=1` plus a continuation authorize a push it does not
    # authorize.
    # MEASURED kill set: exactly `test_the_streams_carry_every_reading_primary_first`. Disjoint from
    # `all_join_becomes_primary`'s 14 rows except for that one, which is what makes the
    # streams-only mechanism separately attributable.
    mutate.Mutation(
        "streams_primary_last",
        "            streams.append(strip_redirects(tokenize(newlines_to_separators(outer))))\n",
        "            streams.insert(\n"
        "                0, strip_redirects(tokenize(newlines_to_separators(outer)))\n"
        "            )\n",
    ),
    # ONE budget for the whole walk, threaded into every child. Passing None instead gives each
    # nested context a FRESH `MAX_TOTAL_PARSES`, which is the measured 75,421-parses / 9.14 s
    # hazard: work is variants x contexts, and a per-context cap counts one factor. A hook timeout
    # lets the command RUN, so an unbounded walk is a bypass rather than a slowdown.
    # This is the cap mutant that CAN be written: unlike `parse_cap_removed` it terminates, because
    # the per-context limit still binds. MEASURED kill set: exactly
    # `test_both_readings_survive_the_cap_at_its_exact_cost`, whose N-1 half is what moves -- the
    # fresh child budget lets the join's child parse, so `statusA` reappears where the row requires
    # it to be absent. Without that N-1 control the row would pass on a cap that never binds.
    mutate.Mutation(
        "budget_not_threaded_into_children",
        "                sub_results, _, sub_trails = _walk_context(\n"
        "                    nested[idx], cwd, max_depth, budget, primary_chain=primary_chain\n"
        "                )\n",
        "                sub_results, _, sub_trails = _walk_context(\n"
        "                    nested[idx], cwd, max_depth, None, primary_chain=primary_chain\n"
        "                )\n",
    ),
    # The MIXED assignments -- everything between the two extremes. Measured 2026-09-23: with this
    # loop replaced by `return`, so only all-drop and all-join are ever tried, the whole suite was
    # 844/844 GREEN on a mirror. The mechanism is live (17 of 432 two-heredoc shapes read
    # differently without it, losing real invocations while the marker survives), so the gap was
    # OMISSION, which no score on the other 25 mutants could have reported.
    mutate.Mutation(
        "assignments_extremes_only",
        "    for bits in itertools.product((False, True), repeat=count):\n",
        "    return\n    for bits in itertools.product((False, True), repeat=count):\n",
    ),
]

MUST_KILL = {
    "parity_check_widened_to_any_run": "test_a_continuation_folds_only_where_bash_folds[escaped-backslash-lf]",
    "crlf_backslash_refolded": "test_a_continuation_folds_only_where_bash_folds[backslash-crlf]",
    "end_of_input_drop_removed": "test_a_continuation_folds_only_where_bash_folds[eoi-single-backslash]",
    "quoted_flag_forced_false": "test_a_continuation_folds_only_where_bash_folds[quoted-body-even-run]",
    "heredoc_line_join_disabled": "test_a_continuation_folds_only_where_bash_folds[unquoted-body-swallows-terminator]",
    "unescape_pair_collapse_disabled": (
        "test_a_continuation_folds_only_where_bash_folds[unquoted-body-halves-pair]"
    ),
    "opener_even_run_collapsed_to_one": "test_a_continuation_folds_only_where_bash_folds[even-run-before-dollar-paren]",
    "unescape_substitution_raw_copy_removed": (
        "test_a_continuation_folds_only_where_bash_folds[unquoted-body-substitution-raw]"
    ),
    "nested_heredoc_remask_removed": "test_a_continuation_folds_only_where_bash_folds[nested-heredoc-in-unquoted-body]",
    "depth_guard_removed": (
        "test_deeply_nested_unquoted_heredocs_still_read_the_innermost_command"
    ),
    "unquoted_dash_logical_line_tab_strip_removed": (
        "test_an_unquoted_dash_body_strips_tabs_before_the_terminator_comparison"
    ),
    "reading_forced_to_drop": (
        "test_a_join_reading_keeps_the_continuation_and_a_drop_removes_it"
    ),
    "reading_forced_to_join": (
        "test_a_continuation_folds_only_where_bash_folds[quoted-body-final-continuation]"
    ),
    "ambiguity_never_detected": "test_a_reading_state_numbers_only_ambiguous_heredocs",
    "assignments_only_primary": (
        "test_a_substitution_body_ending_in_a_continuation_records_the_joined_reading"
    ),
    "all_join_becomes_primary": "test_both_readings_are_recorded_for_an_ambiguous_heredoc",
    "union_is_a_set": "test_an_invocation_run_twice_is_recorded_twice",
    "indeterminate_signal_dropped": (
        "test_a_variant_that_raises_does_not_hide_a_reading_that_parses"
    ),
    "truncation_not_signalled": (
        "test_an_exhausted_cap_yields_the_marker_and_never_raises"
    ),
    "raise_when_any_raises": (
        "test_a_variant_that_raises_does_not_hide_a_reading_that_parses"
    ),
    "streams_raise_when_any_raises": "test_both_primitives_carry_the_same_in_band_marker",
    "streams_child_recursion_outside_the_try": (
        "test_the_streams_primitive_never_raises_where_the_walk_reads"
    ),
    "streams_marker_dropped": "test_both_primitives_carry_the_same_in_band_marker",
    "streams_primary_last": "test_the_streams_carry_every_reading_primary_first",
    "budget_not_threaded_into_children": (
        "test_both_readings_survive_the_cap_at_its_exact_cost"
    ),
    "assignments_extremes_only": (
        "test_the_enumeration_yields_the_mixed_assignments_not_only_the_extremes"
    ),
}


def _suite_test_text() -> str:
    """Every test file SUITE runs, concatenated -- the population a MUST_KILL name may live in.

    Derived from SUITE, never hand-listed: the checker below asks whether a row NAME still exists,
    and reading a narrower set than the suite runs makes a live row indistinguishable from a
    deleted one. Measured 2026-09-22 -- with only `test_git_command.py` read, a row added to
    `test_git_command_properties.py` was reported as "renamed or removed", which is the one
    diagnosis that sends a reader to fix the wrong thing.

    FLOOR of 2, because discovery cannot detect absence: a SUITE edit that drops a file would
    otherwise shrink this check silently and every name in the dropped file would read as dead.
    """
    paths = [REPO / a for a in SUITE if a.endswith(".py") and a.startswith("scripts/")]
    if len(paths) < 2:
        raise RuntimeError(
            f"SUITE names {len(paths)} test file(s); this check needs the whole suite "
            "population and a floor of 2 (see the docstring)"
        )
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise RuntimeError(
            "SUITE names test files that do not exist: "
            + ", ".join(str(p) for p in missing)
        )
    return "\n".join(p.read_text() for p in paths)


def _assert_must_kill_names_are_live() -> None:
    """Fail loudly, before any mutation runs, if a `MUST_KILL` entry points at a renamed row.

    Without this, a renamed test function makes the row check below read a mutation as SURVIVED for
    the wrong reason -- "no such name in the output" is byte-identical whether the mechanism is truly
    unguarded or the row was merely renamed or removed out from under this campaign.

    A `MUST_KILL` value is either a bare name in `_UNIT_TEST_NAMES` or a `function[id]` parametrize
    reference whose id is written literally in the test file -- see the module docstring's "Naming
    the required row" section.
    """
    text = _suite_test_text()
    problems = []
    missing_labels = sorted(set(MUTATIONS_LABELS) - set(MUST_KILL))
    if missing_labels:
        problems.append("mutations with no MUST_KILL row: " + ", ".join(missing_labels))
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


MUTATIONS_LABELS = [m.label for m in MUTATIONS]


def _assert_anchors_are_unique(subject: Path) -> None:
    """Assert each mutation's `old` occurs EXACTLY once in `subject`, before the ~hour-long scoring
    pass runs. `mutate.run()` already reports a non-1 count as a campaign defect during PASS 1
    (scoring), and `_verify_named_rows` reports the same during PASS 2 (the named-row check, after
    scoring) -- both per-mutation, one at a time; this fails fast, up front, for every mutant at
    once, before either pass runs.

    What this CANNOT catch, so do not read a clean run as one: an anchor that still occurs once
    while the MECHANISM it was written for has changed meaning underneath it. That is silent, and
    it is what happened to the two backtick mutants the 2026-09-19 redesign retired -- their
    anchors stayed unique and their kill rows stopped being able to fail. See the module
    docstring's "Mechanisms measured as UNPINNED".
    """
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
    "the row I intended to kill is among the failures" cannot be read from its report -- a mutation
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
    # The campaign takes no arguments. Any argument -- `--help` included -- prints the docstring and
    # exits WITHOUT running: an earlier version ignored argv, so `--help` started a campaign that
    # mutates the subject in place.
    if len(sys.argv) > 1:
        print(__doc__)
        return 0 if sys.argv[1] in ("-h", "--help") else 2
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
