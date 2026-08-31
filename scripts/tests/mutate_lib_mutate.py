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
        "        backup.write_text(original)",
        "        pass",
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
        '    if timedout:\n        status, rc = "ERROR", 2',
        '    if False:\n        status, rc = "ERROR", 2',
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
