#!/usr/bin/env python3
# Script: mutate_publication_push_guard_env.py
# Purpose: Mutation campaign for the non-GIT_ env axis (fix/env-boundary-relocation) — prove the
#          corpus discriminates the mechanisms this branch introduced, not merely that the suite
#          is red somewhere
# Usage: scripts/tests/mutate_publication_push_guard_env.py
"""Mutate the env-axis mechanisms and require the CORPUS to catch every one, BY NAME.

A green suite at branch tip is also exactly what reverting every commit on this branch would
produce. This campaign is the measurement that tells those apart: one mutation per mechanism the
branch introduced, each paired with the specific corpus row it is REQUIRED to kill.

Two subjects, not one. All but one mechanism lives in `publication-push-guard.py`; the exception
(`ENV_ASSIGN` widening to admit `VAR+=`) lives in the SHARED tokenizer, `git_command.py` — Task 3c
touched it because seven consumers share that walk. `mutate.run()` mutates one file per call, so
this campaign runs it twice, against two different subjects, and both must PASS.

Two passes per subject, not one — this is the part that matters. `mutate.run()` keeps only the
suite's LAST stdout line per caught mutant (see its `Outcome.detail`), so "the row I intended to
kill is among the failures" cannot be read from its report: a mutation can turn the suite red for
some OTHER reason and still score CAUGHT. `_verify_named_rows` below re-applies every mutation,
captures the FULL pytest output, and asserts the required row's label is actually IN it. Restore
is verified by sha256 on both passes independently.

SUITE targets the corpus (`test_guard_corpus.py`), never `test_publication_push_guard.sh` — the
sibling campaign's SUITE. The shell suite predates this branch's rows entirely and would score
every one of these mutations SURVIVED for the right reason (it asserts nothing about them) while
reading as a weak suite rather than the wrong target.

Some rows here are covered by MORE than one mechanism (measured, not designed): `_REACH_FLOOR`
makes `HOME`'s reach unconditional for a standalone bare assignment, and the corpus harness's
own `_run_guard` always sets `HOME` in the guard's pinned environment -- so `HOME` is *also*
always present via the plain `name in os.environ` half of `_name_carries_export_attribute`,
independent of `_REACH_FLOOR` membership. `env_bypass_home_allexport_bundled`
(`set -ao allexport; HOME=... ; git <PUSH>`) is therefore defended TWICE: by the bundled-option
`-a` matching this row's `why` names, AND independently by that reach floor. Reverting only the
bundled-`-a` matching (measured directly, not asserted) leaves the row BLOCKED regardless --
defense in depth, not a hole, but it means that narrow revert cannot be what kills this row on the
current build. `kill_standalone_reach_for_home` reverts the whole standalone-reach computation for
this one code path instead (drops both the allexport-bundle contribution and the name-floor
contribution at their single shared consumption point), which does. See the campaign's own
verification report for the measured baseline this is derived from -- do not narrow it back to a
bundled-only revert without re-measuring first.
"""

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "publication-push-guard.py"
SUBJECT_GITCMD = REPO / "scripts" / "lib" / "git_command.py"
SUITE = [sys.executable, "-m", "pytest", "scripts/tests/test_guard_corpus.py", "-q"]

# ---------------------------------------------------------------------------------------------
# Mutations against scripts/publication-push-guard.py
# ---------------------------------------------------------------------------------------------
MUTATIONS = [
    # The allowlist inversion itself: without it, every non-GIT_ name (HOME included) is cleared
    # again by the old `^GIT_` shape rule -- the measured full-stack bypass this branch closes.
    mutate.Mutation(
        "revert_the_allowlist_inversion",
        "    return name in _ENV_ALLOWLIST",
        "    return not _UNREACHABLE_DENY_RE.match(name)",
    ),
    # The authorization override CLAUDE.md prescribes -- 776 transcript uses. Dropping it from
    # the allowlist must make this gate refuse the repo's own documented publish command.
    mutate.Mutation(
        "drop_allow_push_from_allowlist",
        '        "ALLOW_PUSH",\n',
        "",
    ),
    # The pager class, cleared on measured ground (git does not page without a TTY).
    mutate.Mutation(
        "drop_pager_from_allowlist",
        '        "PAGER",\n',
        "",
    ),
    # Locale, cleared because the boundary hook parses no localized text.
    mutate.Mutation(
        "drop_lc_all_from_allowlist",
        '        "LC_ALL",\n',
        "",
    ),
    # `set` in ARGUMENT position (`git config set k v`) is git's OWN subcommand, not the shell
    # builtin -- Task 2's "COMMAND position only" gate is what keeps `set` from being read as an
    # exporter there. This mutation puts `set` back in the export-word family AND drops that
    # position gate for the two branches that decide it, reproducing the pre-Task-2 shape:
    # ANY occurrence of `set`/`export`/`declare`/`typeset`/`readonly`/`local` anywhere in a
    # segment starts an (unconditionally exporting) construct, so `rerere.enabled` gets judged
    # as a name and blocked -- exactly the measured historical defect.
    mutate.Mutation(
        "restore_set_as_an_unconditional_export_word",
        "                if here_at_prefix and token in _EXPORT_WORDS:\n"
        "                    # `export` exports by default; `declare`/`typeset` only with -x.\n"
        "                    exporting, at_prefix = True, False\n"
        '                    export_exports = token == "export"\n'
        "                    export_is_functions = False\n"
        "                elif here_at_prefix and token == _SET_WORD:\n"
        '                    in_set, at_prefix, prev_set_opt = True, False, ""',
        "                if token in _EXPORT_WORDS or token == _SET_WORD:\n"
        "                    exporting, at_prefix = True, False\n"
        "                    export_exports = True\n"
        "                    export_is_functions = False",
    ),
    # `set`'s OPTIONS (`-e`, `-x`, ...) must never be judged as variable names -- only its
    # allexport switches matter. This stops the `in_set` branch from skipping option tokens: it
    # lets them fall through to the shared name-check block by forcing `reaches_git_env = True`
    # instead of the unconditional `continue` every option token took. `set -e ; git status` then
    # gets '-e' blamed, which is the measured mass false-block this branch's option-skip fixes.
    mutate.Mutation(
        "stop_skipping_set_option_tokens",
        '                if token.startswith("-") and not token.startswith("--"):\n'
        '                    if "a" in token[1:]:\n'
        "                        allexport = True\n"
        "                    # A bundled `o` means the NEXT token is `-o`'s argument "
        "(`set -ao allexport`).\n"
        '                    prev_set_opt = "-o" if "o" in token[1:] else token\n'
        "                else:\n"
        '                    if prev_set_opt == "-o" and token == "allexport":\n'
        "                        allexport = True\n"
        "                    prev_set_opt = token\n"
        "                continue",
        '                if token.startswith("-") and not token.startswith("--"):\n'
        '                    if "a" in token[1:]:\n'
        "                        allexport = True\n"
        '                    prev_set_opt = "-o" if "o" in token[1:] else token\n'
        "                else:\n"
        '                    if prev_set_opt == "-o" and token == "allexport":\n'
        "                        allexport = True\n"
        "                    prev_set_opt = token\n"
        "                reaches_git_env = True",
    ),
    # The standalone-bare-assignment reach computation Task 3b introduced. Measured directly
    # (see module docstring): reverting ONLY the bundled-`-a` character matching leaves this row
    # BLOCKED regardless, because HOME's `_REACH_FLOOR` membership -- reinforced by `_run_guard`
    # always setting HOME in the guard's own environment -- covers it independently. This
    # mutation instead reverts the single line both contributions feed into, dropping the
    # allexport-bundle contribution AND the name-floor contribution together, which is what
    # actually flips `env_bypass_home_allexport_bundled` to ALLOW.
    mutate.Mutation(
        "kill_standalone_reach_for_home",
        "                    reaches_git_env = allexport or _name_carries_export_attribute(name)",
        "                    reaches_git_env = False",
    ),
    # The SEGMENT-BOUNDARY predicate. Reverting `is_op` to the old literal seven-member frozenset
    # reopens the fused-operator class in BOTH directions at once: `)&&` stops resetting position
    # (bypass) and `;;` gets judged as a variable name (false block).
    mutate.Mutation(
        "separator_set_back_to_a_literal_frozenset",
        "            if gitcmd.is_op(token):",
        '            if token in {";", "&&", "||", "|", "&", "(", ")"}:',
    ),
    # The EXPANSION arm -- a different dimension from the wrapper word SET, and the one no word
    # list could ever close. An expansion in command-prefix position may vanish or become a
    # wrapper, and bash runs the assignment in the current shell either way. Reverting this makes
    # such a token read as a real command word, ending prefix position and darkening the arm.
    mutate.Mutation(
        "expansion_reads_as_a_real_command_word",
        "                if here_at_prefix and _looks_like_unresolvable_expansion(token, gitcmd):",
        "                if False:",
    ),
    # `eval` specifically -- the member the FIRST enumeration of _SHELL_BUILTIN_WRAPPERS missed,
    # which left the wrapper regression open through one more spelling for a whole commit.
    mutate.Mutation(
        "drop_eval_from_the_wrapper_set",
        'frozenset({"command", "builtin", "time", "eval"})',
        'frozenset({"command", "builtin", "time"})',
    ),
    # The declared _REACH_FLOOR itself. Deleting it moved ZERO of the corpus's rows until three
    # standalone rows were added for names the harness does NOT pin into the guard's environment
    # -- HOME-based rows satisfy `name in os.environ` independently and pin nothing here.
    mutate.Mutation(
        "delete_the_reach_floor",
        "        or name in _REACH_FLOOR\n        or name.startswith(_REACH_FLOOR_PREFIX)\n",
        "",
    ),
    # The wrapper transparency added after a whole-branch review found a REGRESSION from dev: one
    # word (`command`/`builtin`/`time`/`eval`) in front of `export` ended command-prefix position
    # and the entire export arm went dark. Reverting it must flip the GIT_-named regression row.
    mutate.Mutation(
        "wrapper_word_ends_command_position",
        "                if here_at_prefix and token in _SHELL_BUILTIN_WRAPPERS:",
        "                if False:",
    ),
    # The BUNDLED-allexport matcher, reverted to the exact-token match it had before the Task 2
    # review found it fail-open. A SEPARATE mechanism from `kill_standalone_reach_for_home`, and
    # it needed its own row to be pinnable at all: the obvious row
    # (`env_bypass_home_allexport_bundled`) uses HOME, which is on `_REACH_FLOOR` and so reaches
    # whether or not allexport was detected -- two independent paths, pinning neither. Measured:
    # under this exact mutation the HOME rows still BLOCK while the FRESHNAME rows flip to ALLOW.
    # Do not retarget this at a HOME row.
    mutate.Mutation(
        "allexport_matches_exact_token_only",
        '                if token.startswith("-") and not token.startswith("--"):\n'
        '                    if "a" in token[1:]:',
        '                if token == "-a":\n                    if True:',
    ),
    # `_name_carries_export_attribute` stuck at "always False" -- the determinism sentinel's
    # BLOCK arm (CORPUS_EXPORTED_PROBE, pinned into the guard's own environment by
    # `_run_guard`) must still be denied.
    mutate.Mutation(
        "name_carries_export_attribute_always_false",
        "    return (\n"
        "        name in os.environ\n"
        "        or name in _REACH_FLOOR\n"
        "        or name.startswith(_REACH_FLOOR_PREFIX)\n"
        "    )",
        "    return False",
    ),
    # `_name_carries_export_attribute` stuck at "always True" -- the determinism sentinel's
    # ALLOW arm (CORPUS_ABSENT_PROBE, deliberately absent from the pinned environment) must stay
    # allowed. Paired with the mutation above: a reach rule stuck at either value must be caught,
    # and only the pair proves both arms of the computation actually move.
    mutate.Mutation(
        "name_carries_export_attribute_always_true",
        "    return (\n"
        "        name in os.environ\n"
        "        or name in _REACH_FLOOR\n"
        "        or name.startswith(_REACH_FLOOR_PREFIX)\n"
        "    )",
        "    return True",
    ),
    # Bash's own rule: if no command name survives expansion, a leading assignment affects the
    # CURRENT shell. `$(true)` and `$UNSET_VAR` are exactly the shape that can expand to nothing,
    # so treating either as proof of a following command word (dropping the
    # `_looks_like_unresolvable_expansion` guard) mis-scopes `HOME=/x $(true) ; git <PUSH>` as a
    # prefix to a command that never materializes, clearing HOME.
    mutate.Mutation(
        "unresolvable_expansion_proves_a_command_word",
        "    return (\n"
        "        not gitcmd.is_op(stream[j])\n"
        "        and stream[j] not in gitcmd.RESERVED_WORDS\n"
        "        and not _looks_like_unresolvable_expansion(stream[j], gitcmd)\n"
        "    )",
        "    return (\n"
        "        not gitcmd.is_op(stream[j])\n"
        "        and stream[j] not in gitcmd.RESERVED_WORDS\n"
        "    )",
    ),
]

MUST_KILL = {
    "revert_the_allowlist_inversion": "env_bypass_home_inline",
    "drop_allow_push_from_allowlist": "env_allow_control_plane_allow_push",
    "drop_pager_from_allowlist": "env_allow_pager_plain",
    "drop_lc_all_from_allowlist": "env_allow_locale_lc_all",
    "restore_set_as_an_unconditional_export_word": "shell_shape_config_set_subcommand",
    "stop_skipping_set_option_tokens": "shell_shape_set_e",
    "kill_standalone_reach_for_home": "env_bypass_home_allexport_bundled",
    "allexport_matches_exact_token_only": "env_bypass_allexport_bundled_pins_the_matcher",
    "wrapper_word_ends_command_position": "env_bypass_wrapper_command_export_git_name",
    "delete_the_reach_floor": "env_bypass_reach_floor_xdg_standalone",
    "drop_eval_from_the_wrapper_set": "env_bypass_wrapper_eval_export_git_name",
    "expansion_reads_as_a_real_command_word": "env_bypass_expansion_before_export_git_name",
    "separator_set_back_to_a_literal_frozenset": "env_bypass_fused_operator_before_export",
    "name_carries_export_attribute_always_false": "env_bypass_corpus_exported_probe",
    "name_carries_export_attribute_always_true": "env_allow_corpus_absent_probe",
    "unresolvable_expansion_proves_a_command_word": "env_bypass_home_command_substitution_standalone",
}

# ---------------------------------------------------------------------------------------------
# The eleventh mechanism: the shared tokenizer's ENV_ASSIGN widening (Task 3c), a different
# subject file entirely.
# ---------------------------------------------------------------------------------------------
MUTATIONS_GITCMD = [
    # Before Task 3c, `ENV_ASSIGN` did not match `VAR+=val`, so `starts_command` read `git` as
    # being in ARGUMENT position after an append-form prefix and the whole invocation vanished
    # from every downstream consumer -- rc=0, no invocation judged at all. Narrowing it back
    # reproduces that: `HOME+=ZZ git <PUSH>` stops being seen as a git invocation.
    mutate.Mutation(
        "narrow_env_assign_to_exclude_append_form",
        'ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\\+?=")',
        'ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")',
    ),
]

MUST_KILL_GITCMD = {
    "narrow_env_assign_to_exclude_append_form": "env_bypass_home_append_inline_prefix",
}


def _verify_named_rows(subject: Path, mutations, must_kill: dict) -> list[str]:
    """The part `mutate.run()` cannot do: confirm the REQUIRED row is in the failure text.

    `mutate.run()`'s `Outcome.detail` keeps only the suite's last stdout line per mutant, so
    "the named row is among the failures" cannot be read from its report -- a mutation can turn
    the suite red for an unrelated reason and still score CAUGHT. This re-applies each mutation,
    captures FULL pytest stdout+stderr, and asserts the row's label is actually in it. Restore is
    verified by sha256, independent of `mutate.run()`'s own restore in the first pass.
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
                f"{m.label}: suite output does NOT contain required row label "
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
    print("=" * 88)
    print("PASS 1a: mutate.run() scoring against", SUBJECT.name)
    print("=" * 88)
    report1 = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO)
    print(report1.text)

    print()
    print("=" * 88)
    print("PASS 1b: mutate.run() scoring against", SUBJECT_GITCMD.name)
    print("=" * 88)
    report2 = mutate.run(SUBJECT_GITCMD, SUITE, MUTATIONS_GITCMD, cwd=REPO)
    print(report2.text)

    print()
    print("=" * 88)
    print("PASS 2a: named-row verification against", SUBJECT.name)
    print("=" * 88)
    problems = _verify_named_rows(SUBJECT, MUTATIONS, MUST_KILL)

    print()
    print("=" * 88)
    print("PASS 2b: named-row verification against", SUBJECT_GITCMD.name)
    print("=" * 88)
    problems += _verify_named_rows(SUBJECT_GITCMD, MUTATIONS_GITCMD, MUST_KILL_GITCMD)

    print()
    print("=" * 88)
    ok = report1.status == "PASS" and report2.status == "PASS" and not problems
    if problems:
        print(f"ROW-CHECK FAIL: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
    print(
        f"FINAL: {'PASS' if ok else 'FAIL'} "
        f"pass1a={report1.verdict} pass1b={report2.verdict} "
        f"row_check_problems={len(problems)}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
