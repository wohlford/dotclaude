#!/usr/bin/env python3
"""Mutation-test scripts/lib/mutate.py USING scripts/lib/mutate.py.

Run on demand: `python3 scripts/tests/mutate_lib_mutate.py`. Deliberately NOT named `test_*`, so
pytest never collects it — it mutates a tracked file in place (restoring it in a `finally`) and
takes ~40s, neither of which belongs inside an ordinary suite run.

It is committed rather than left in a scratchpad because losing the mutation list is the exact
disease this module was built to cure: ten harnesses were hand-written and thrown away, and each
rewrite re-derived the safety properties instead of reusing them. These rows are the evidence
that `mutate.py`'s own guarantees are tested; without them a later edit can silently drop one and
the suite stays green.

Self-referential but not circular: the runner is imported into THIS process, so the copy driving
the campaign and doing the `finally` restore is in memory and unaffected by what is written to
disk. Only the pytest subprocess imports the mutant.

Every row names one safety property, and mutates what it names — the point being that a row can
otherwise go red off some unrelated assertion that raises first, which is a green suite wearing a
red hat. If a row survives, the question is not automatically "add a fixture": it may mean the
mutated code is unreachable and should be DELETED.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "lib" / "mutate.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    str(REPO / "scripts" / "tests" / "test_mutate.py"),
    "-q",
    "--no-header",
]

MUTATIONS = [
    mutate.Mutation(
        "baseline check removed — an already-red suite scores a clean sweep",
        "        if baseline.noticed:",
        "        if False:",
    ),
    mutate.Mutation(
        "every run judged by rc alone, not the shared predicate",
        "            noticed=is_caught(proc.returncode, stdout, fail_pattern),",
        "            noticed=proc.returncode != 0,",
    ),
    mutate.Mutation(
        "predicate loses its fail-open half (a FAIL line with rc 0 reads as survived)",
        "    return returncode != 0 or re.search(fail_pattern, stdout, re.M) is not None",
        "    return returncode != 0",
    ),
    mutate.Mutation(
        "predicate loses its crash half (a silent non-zero exit reads as survived)",
        "    return returncode != 0 or re.search(fail_pattern, stdout, re.M) is not None",
        "    return re.search(fail_pattern, stdout, re.M) is not None",
    ),
    mutate.Mutation(
        "not-found-exactly-once stops being enforced",
        "            if count != 1:",
        "            if False:",
    ),
    mutate.Mutation(
        "the finally no longer restores the subject's CONTENT",
        "    finally:\n        subject.write_text(original)",
        "    finally:\n        pass",
    ),
    # The two MODE rows that were here are GONE, not silenced. Both survived round 1, and the
    # reason was not a gap in the suite: `write_text` truncates in place, so the `chmod` calls
    # they targeted restored a mode that was never lost. The repair was to delete the no-ops,
    # which makes these rows aim at absent code — they would report SURVIVED forever, true and
    # useless. The exec-bit property itself is still asserted by the suite.
    mutate.Mutation(
        "the sha256 restore assertion is dropped",
        "    if after != before:",
        "    if False:",
    ),
    mutate.Mutation(
        "the empty-campaign guard is dropped (zero mutations report PASS)",
        "    if total == 0:",
        "    if False:",
    ),
    mutate.Mutation(
        "__pycache__ is never cleared",
        '    if subject.suffix != ".py":\n        return',
        "    if True:\n        return",
    ),
    mutate.Mutation(
        "a DEFAULT report destination is introduced (group 5)",
        "    if report_path is not None:\n        dest = Path(report_path)",
        "    if True:\n"
        '        dest = Path(report_path or (subject.parent / "mutate-report.txt"))',
    ),
    # ---- INVALID: a mutant that never exercised the suite. Each row plants ONE defect in the
    # detector or its wiring. The expected killer is named per row; the controller compares KILL
    # SETS, since a duplicate mutant inflates the caught count while measuring nothing.
    mutate.Mutation(
        # Expect: test_an_UNPARSEABLE_mutant_is_INVALID_and_ends_the_campaign_ERROR (+ the
        # never-runs-the-suite row, which sees the counter reach 2).
        "the parse check is never consulted, so an unparseable mutant runs the suite and reads CAUGHT",
        "            if verdict == UNPARSEABLE:",
        "            if False:",
    ),
    mutate.Mutation(
        # Expect: test_a_PRISTINE_subject_that_does_not_parse_leaves_the_check_OFF and
        # test_judge_mutant_parse_needs_a_PARSING_control.
        "the pristine control is dropped, so a subject that never parsed marks every mutant INVALID",
        "    if control[0] != PARSES:\n        detail = ",
        "    if False:\n        detail = ",
    ),
    mutate.Mutation(
        # Expect: test_a_QUOTED_banner_beside_a_real_failure_is_still_CAUGHT[mid-line].
        "the collection-abort pattern loses its ^ anchor, so a MENTION of the banner mid-line "
        "reads as a collection abort",
        'COLLECTION_ABORT_PATTERN = r"^!+ Interrupted',
        'COLLECTION_ABORT_PATTERN = r"!+ Interrupted',
    ),
    mutate.Mutation(
        # Expect: test_a_QUOTED_banner_beside_a_real_failure_is_still_CAUGHT[trailing-text].
        "the collection-abort pattern loses its $ anchor, so a banner followed by more text "
        "reads as a collection abort",
        'during collection !+$"',
        'during collection !+"',
    ),
    mutate.Mutation(
        # Expect: test_a_collection_abort_banner_ALONE_on_its_line_is_INVALID (noticed is true
        # for that run, so a later placement scores it CAUGHT).
        "the collection-abort branch is tried AFTER run.noticed, so an aborted run reads CAUGHT",
        r"""            elif re.search(COLLECTION_ABORT_PATTERN, run.stdout, re.M):
                # Before `noticed`, which is true for this run too: pytest exits 2 having run
                # no test, and that is not a catch.
                outcomes.append(
                    Outcome(
                        m.label,
                        INVALID,
                        "pytest's collection aborted, so no test ran — indeterminate",
                    )
                )
                invalid += 1
            elif run.noticed:
                tail = (run.stdout.strip().split("\n") or [""])[-1]
                outcomes.append(Outcome(m.label, CAUGHT, tail[:96]))
                caught += 1
""",
        r"""            elif run.noticed:
                tail = (run.stdout.strip().split("\n") or [""])[-1]
                outcomes.append(Outcome(m.label, CAUGHT, tail[:96]))
                caught += 1
            elif re.search(COLLECTION_ABORT_PATTERN, run.stdout, re.M):
                outcomes.append(
                    Outcome(
                        m.label,
                        INVALID,
                        "pytest's collection aborted, so no test ran — indeterminate",
                    )
                )
                invalid += 1
""",
    ),
    mutate.Mutation(
        # Expect: the two INVALID rows asserting (status, rc) == ("ERROR", 2).
        "an INVALID mutant no longer forces ERROR, so an unjudged campaign reports PASS",
        "    if timedout or invalid:",
        "    if timedout:",
    ),
    mutate.Mutation(
        # Expect: the verdict-shape rows (well_formed, never_disagree, BYTE_IDENTICAL).
        "the invalid= verdict field is dropped, so a consumer cannot see why the campaign errored",
        'f"timedout={timedout} total={total} invalid={invalid}"',
        'f"timedout={timedout} total={total}"',
    ),
    mutate.Mutation(
        # Expect: test_the_banner_on_a_GREEN_baseline_is_an_ERROR_before_any_mutation.
        "the baseline is not checked against the collection-abort predicate",
        "        if re.search(COLLECTION_ABORT_PATTERN, baseline.stdout, re.M):",
        "        if False:",
    ),
    mutate.Mutation(
        # Expect: the parse-check ON/OFF report-line rows.
        "the parse-check line is dropped, so an unjudged subject kind reads as validated",
        r"""        lines.append(
            "parse check: on"
            if control[0] == PARSES
            else f"parse check: OFF — {control[1]}"
        )
        emit(lines[-1])
""",
        "        pass\n",
    ),
    mutate.Mutation(
        # Expect: test_an_UNPARSEABLE_mutant_is_INVALID_and_ends_the_campaign_ERROR (invalid==1).
        "a statically INVALID mutant is not counted, so the campaign can still end PASS",
        '                invalid += 1\n                emit(f"[{n}/{total}] {INVALID:9s} {m.label}  (does not parse)")',
        '                emit(f"[{n}/{total}] {INVALID:9s} {m.label}  (does not parse)")',
    ),
    # ---- CANNOT_RUN: a parser that FAILED to run is not "no parser for this kind". Each row
    # collapses or drops one hop of the path from `check_parse` to the campaign's verdict.
    mutate.Mutation(
        # Expect: test_check_parse_of_a_bash_parser_that_cannot_run_is_CANNOT_RUN (3 params) and
        # test_a_control_whose_parse_check_CANNOT_RUN_ends_the_campaign_ERROR_untouched.
        "a bash -n that cannot run collapses back to UNCHECKED (a transient failure reads as by-design)",
        '            return CANNOT_RUN, f"bash -n could not run: {exc}"',
        '            return UNCHECKED, f"bash -n could not run: {exc}"',
    ),
    mutate.Mutation(
        # Expect: test_check_parse_of_a_python_compile_that_gives_up_is_CANNOT_RUN.
        "a compile() that gives up collapses back to UNCHECKED",
        '            return CANNOT_RUN, f"compile() gave up: {exc}"',
        '            return UNCHECKED, f"compile() gave up: {exc}"',
    ),
    mutate.Mutation(
        # Expect: test_judge_mutant_parse_propagates_a_control_that_CANNOT_RUN.
        "a control that cannot run is downgraded to UNCHECKED by judge_mutant_parse",
        '    if control[0] == CANNOT_RUN:\n        return CANNOT_RUN, f"no control: {control[1]}"',
        '    if False:\n        return CANNOT_RUN, f"no control: {control[1]}"',
    ),
    mutate.Mutation(
        # Expect: test_a_control_whose_parse_check_CANNOT_RUN_ends_the_campaign_ERROR_untouched.
        "the failed-control ERROR is removed — mutants are scored by the suite with no parser",
        "        if control[0] == CANNOT_RUN:\n            # Before any mutation",
        "        if False:\n            # Before any mutation",
    ),
    mutate.Mutation(
        # Expect: test_a_mutant_whose_parse_check_CANNOT_RUN_is_INVALID_and_never_runs.
        "a mutant whose parse check cannot run is no longer INVALID — the suite scores it",
        "            if verdict == CANNOT_RUN:\n                outcomes.append(",
        "            if False:\n                outcomes.append(",
    ),
    mutate.Mutation(
        # Expect: test_a_bash_n_exit_other_than_1_or_2_is_the_PARSER_failing_not_the_mutant (126,
        # 127, 137, -9 params: a nonzero rc with stderr would read UNPARSEABLE).
        "any nonzero bash -n exit is UNPARSEABLE — a parser that died blames the mutant",
        "        if proc.returncode in _BASH_SYNTAX_ERROR_RCS and lines:",
        "        if proc.returncode:",
    ),
    mutate.Mutation(
        # Expect: test_a_bash_n_exit_1_with_stderr_is_UNPARSEABLE_carrying_the_first_line and
        # test_a_bash_n_exit_2_is_an_UNPARSEABLE_verdict_carrying_bashs_own_words.
        "the bash -n rc classification branch is inert — every syntax error reads CANNOT_RUN",
        "        if proc.returncode in _BASH_SYNTAX_ERROR_RCS and lines:",
        "        if False:",
    ),
    mutate.Mutation(
        # Expect: test_check_parse_passes_utf8_surrogateescape_not_the_locale_codec.
        "the bash -n stdin encoding kwargs are dropped — the locale codec encodes the subject",
        '                encoding="utf-8",\n                errors="surrogateescape",\n',
        "",
    ),
    mutate.Mutation(
        "the verdict stops being the last line of the report",
        'text="\\n".join([*lines, verdict]),',
        'text="\\n".join([verdict, *lines]),',
    ),
    # ---- surviving a killed run. Every row below is a way for the module to strand a live
    # mutant in the working tree while reporting nothing wrong, which is the measured defect
    # these guarantees exist for: a checker left reporting PASS on unreadable input.
    mutate.Mutation(
        "no SIGTERM handler is installed — a default SIGTERM skips the finally",
        "            installed[sig] = signal.signal(sig, restore_and_die)",
        "            pass",
    ),
    mutate.Mutation(
        "the handler restores but SWALLOWS the signal, so a killed run reads as a clean finish",
        "            signal.signal(signum, signal.SIG_DFL)\n"
        "            os.kill(os.getpid(), signum)",
        "            pass",
    ),
    mutate.Mutation(
        "a deliberately-IGNORED signal is un-ignored, killing a run the operator protected",
        "        if signal.getsignal(sig) == signal.SIG_IGN:",
        "        if False:",
    ),
    mutate.Mutation(
        "the backup is never written, so nothing survives an uncatchable SIGKILL",
        "        _write_atomic(backup, original)",
        "        pass",
    ),
    mutate.Mutation(
        "the backup is written in place, so a torn write leaves a partial sidecar",
        "        tmp.write_text(text)\n        os.replace(tmp, path)",
        "        path.write_text(text)",
    ),
    mutate.Mutation(
        "a stale backup is ignored, so a campaign runs against an already-mutated subject",
        "    if backup.exists():",
        "    if False:",
    ),
    mutate.Mutation(
        "the stale-backup check stops discriminating on CONTENT and refuses every time",
        "        if backup.read_text() == subject.read_text():",
        "        if False:",
    ),
    mutate.Mutation(
        "a verified restore no longer drops the backup, leaving committable litter",
        "            if _sha256(subject) == before:\n"
        "                backup.unlink(missing_ok=True)",
        "            pass",
    ),
    mutate.Mutation(
        "the backup is dropped unconditionally, destroying the only copy after a FAILED restore",
        "            if _sha256(subject) == before:\n"
        "                backup.unlink(missing_ok=True)",
        "            backup.unlink(missing_ok=True)",
    ),
    # ---- a HUNG suite. The killed-run defect's twin: nothing ever fires the restore, so the
    # subject sits mutated for as long as the hang lasts, which is forever.
    mutate.Mutation(
        "no per-suite timeout, so one hanging mutation hangs the whole campaign",
        "            stdout, _ = proc.communicate(timeout=limit)",
        "            stdout, _ = proc.communicate()",
    ),
    mutate.Mutation(
        "a hang is scored CAUGHT — the inflating direction, and a clean-looking sweep",
        "                timedout += 1",
        "                caught += 1",
    ),
    mutate.Mutation(
        "a timeout stops forcing ERROR, so an unjudged campaign reports PASS",
        '    if timedout or invalid:\n        status, rc = "ERROR", 2',
        '    if invalid:\n        status, rc = "ERROR", 2',
    ),
    mutate.Mutation(
        "the derived timeout loses its FLOOR, so a fast suite gets a uselessly tight cap",
        "    return max(TIMEOUT_FLOOR_SECONDS, TIMEOUT_MULTIPLIER * baseline_elapsed)",
        "    return TIMEOUT_MULTIPLIER * baseline_elapsed",
    ),
    mutate.Mutation(
        "the derived timeout stops SCALING, so a slow suite is truncated at the floor",
        "    return max(TIMEOUT_FLOOR_SECONDS, TIMEOUT_MULTIPLIER * baseline_elapsed)",
        "    return TIMEOUT_FLOOR_SECONDS",
    ),
    mutate.Mutation(
        "an explicit timeout= is ignored in favour of the derivation",
        "    if override is not None:\n        return override",
        "    if False:\n        return override",
    ),
    # ---- HEADROOM. A per-mutant TIMEOUT is INDETERMINATE and carries the whole campaign to
    # ERROR, reading like a defect in the change under test. The line's value is RETROSPECTIVE:
    # when a cap bites, the PREVIOUS run's artifact already holds the answer. Measured 2026-08-09,
    # diagnosis cost a full re-run plus an isolated single-row probe.
    mutate.Mutation(
        "the headroom line names no ROW, so a reader cannot tell whether to raise the cap or "
        "look at one pathological row",
        '        f" used={_pct(slowest[1], limit)}%{derived}{base} row={slowest[0]!r}"',
        '        f" used={_pct(slowest[1], limit)}%{derived}{base}"',
    ),
    mutate.Mutation(
        "the slowest row is computed as the FASTEST, so the figure reports the row with the "
        "most headroom — reassuring, and about the wrong row",
        "    slowest = max(completed, key=lambda row: row[1]) if completed else None",
        "    slowest = min(completed, key=lambda row: row[1]) if completed else None",
    ),
    mutate.Mutation(
        "a run that TIMED OUT reports an ordinary percentage, so a completed row's headroom "
        "leads the line on the one run where the cap actually bit",
        "    if timedout_labels:\n        named = ",
        "    if False:\n        named = ",
    ),
    mutate.Mutation(
        "the headroom line is emitted AFTER the verdict instead of before it, displacing the "
        "last line every consumer reads",
        'text="\\n".join([*lines, verdict]),',
        'text="\\n".join(\n'
        '            [ln for ln in lines if not ln.startswith("HEADROOM: ")]\n'
        "            + [verdict]\n"
        '            + [ln for ln in lines if ln.startswith("HEADROOM: ")]\n'
        "        ),",
    ),
    mutate.Mutation(
        "the DERIVED cap is never printed, so a row surviving only because of an override is "
        "indistinguishable from one with real headroom",
        "    if override is not None:\n        derived = ",
        "    if False:\n        derived = ",
    ),
    mutate.Mutation(
        "no headroom line is emitted at all",
        "    lines.append(\n        _headroom_line(",
        "    (\n        _headroom_line(",
    ),
    mutate.Mutation(
        "the headroom line is dropped from the restore-FAILURE report — the one a reader has "
        "most reason to grep",
        '        lines += [\n            f"ERROR  subject NOT restored',
        "        lines.pop()\n"
        '        lines += [\n            f"ERROR  subject NOT restored',
    ),
    mutate.Mutation(
        "only the direct child is killed, orphaning whatever the suite backgrounded",
        "        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)",
        "        proc.kill()",
    ),
    mutate.Mutation(
        "the suite no longer gets its own session, so killpg cannot reach its children",
        "            start_new_session=True,",
        "            start_new_session=False,",
    ),
    mutate.Mutation(
        "a hanging BASELINE is no longer caught, so the campaign hangs before it starts",
        "        if baseline is None:",
        "        if False:",
    ),
    mutate.Mutation(
        "progress is no longer FLUSHED, so a redirected campaign buffers to the very end — "
        "the artifact stays empty for the whole run and a stall is indistinguishable from work",
        "    print(line, flush=True)",
        "    print(line)",
    ),
    mutate.Mutation(
        "progress is off by default, so every campaign that does not opt in keeps the defect",
        "    progress=_stream,",
        "    progress=None,",
    ),
    # ---- CLI usage: `--help` derives its call signature instead of printing nothing.
    mutate.Mutation(
        "the __main__ guard no longer gates anything, so the CLI block fires on plain import — "
        "the highest-stakes row: it protects all 14 importers",
        'if __name__ == "__main__":',
        "if True:",
    ),
    mutate.Mutation(
        "the --help/-h condition is always false, so no invocation ever prints usage to stdout",
        '    if list(argv) in (["--help"], ["-h"]):',
        "    if False:",
    ),
    mutate.Mutation(
        "main() no longer exits 2 for a bad invocation",
        "    return 2",
        "    return 0",
    ),
    mutate.Mutation(
        "the rendered run(...) line stops reflecting run's real parameters — the anti-drift row",
        '", ".join(rendered)',
        '"subject, command, mutations"',
    ),
    mutate.Mutation(
        "_render_default falls back to a bare repr(), leaking a callable's memory address",
        "    return value.__name__ if callable(value) else repr(value)",
        "    return repr(value)",
    ),
]


def main() -> int:
    # The subject is the runner itself, so prove the disk copy is byte-identical to what this
    # process imported before trusting any verdict it produces.
    baseline = subprocess.run(SUITE, capture_output=True, text=True)
    print(
        f"pre-flight suite: rc={baseline.returncode}  {baseline.stdout.strip()[-60:]}"
    )

    report = mutate.run(SUBJECT, SUITE, MUTATIONS)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
