"""Shared mutation-campaign runner: break the subject on purpose, require the suite to notice.

Mutation testing is prescribed by CLAUDE.md ("mutate what a row names; if the suite holds, the
row is not testing it"), so a harness gets hand-written for every substantive change. Ten of them
accumulated before this module existed, and measuring them is what set its contents: no two
agreed on the safety properties and **no single copy had all of them** — 7 of 8 never ran an
unmutated baseline, 5 of 8 emitted no `RESULT:` verdict line, 7 of 8 never cleared
`__pycache__`, and the predicate deciding whether a mutation was CAUGHT existed in three
mutually incompatible versions (one of them strictly better than the rest, in a copy nobody
would ever backport from).

One divergence turned out NOT to be a hole, which is worth recording so it is not "fixed" back
in: 2 of 8 copies `chmod`-ed the subject after restoring it. Mutation-testing this module showed
both such calls surviving every mutation, and a direct probe explains why — `Path.write_text`
truncates the existing file in place, so the mode is never lost to begin with. The six copies
that omitted it were right and the two that had it were carrying a no-op. The exec-bit property
is still asserted by the suite; it simply holds by construction rather than by restoration, and
would go red if this module ever switched to write-a-temp-and-rename.

Every one of those failures reads as a CLEAN SWEEP, which is what makes the drift worth
centralising rather than re-deriving. What stays per-change is the `Mutation` list — the
change-specific knowledge, and the only part that should ever be written fresh.

## The baseline earns its place twice

Nothing here matters more than running the suite UNMUTATED first, and refusing to proceed if it
is not green.

1. If the suite is already red for an unrelated reason, every mutation is "caught" and the
   campaign prints a perfect score. Seven of the eight prior harnesses had this hole.
2. It also validates the caught PREDICATE against this suite. If `fail_pattern` spuriously
   matches ordinary green output, every mutation reads as caught — the dangerous direction,
   since it inflates the score rather than deflating it. Judging the baseline with the *same*
   predicate turns that into an ERROR instead of a flawless report.

## Statuses are an allowlist, and ERROR is not FAIL

`PASS` (every mutation caught), `FAIL` (at least one survivor), `ERROR` (the campaign could not
judge at all — a red baseline, an empty mutation list, or a subject it failed to restore). The
distinction matters because ERROR means no verdict was reached, and a gate that failed closed on
an internal error has not judged your subject.

## Surviving a killed run

A default SIGTERM terminates the interpreter without running `finally`, so the restore simply
never happens. Measured here: a campaign stopped at a 2-minute foreground cap left its subject —
a checker — reporting `RESULT: PASS rc=0` on unreadable input, a verification tool inverted into
a rubber stamp. Nothing drew the eye, because the `restored:` line is ABSENT from every killed
run, and the subject was still UNTRACKED, so git had no baseline and `git status` showed a bare
`??` indistinguishable from a healthy new file.

Two mechanisms cover it, because neither is sufficient alone:

* A **SIGTERM/SIGHUP handler** restores, then re-raises the signal from the default disposition,
  so the exit status a wrapper reads is unchanged (rc 143 stays 143 — a handled signal must not
  read as a clean finish). SIGINT is deliberately left alone: it raises `KeyboardInterrupt`,
  which the `finally` already handles, and replacing that would be a reimplementation of working
  behaviour.
* A **backup sidecar**, `<subject>.mutate-backup`, written before the baseline and removed only
  once a restore is verified. No handler catches SIGKILL, so the guarantee cannot BE a handler;
  only a file already on disk survives one. The invariant is exactly: **a backup on disk means
  the subject may still be mutated.**

A campaign therefore refuses to start when a backup is present and its content DIFFERS from the
subject, naming the recovery rather than running it. Where the two match, the interrupted run
damaged nothing and the backup is simply cleared. Discriminating on content rather than on the
file's presence is what keeps that refusal from costing a single legitimate campaign — and the
refusal earns its place because the baseline check cannot substitute for it: a leftover mutation
in a path the suite does not cover leaves the baseline green.

## What this module deliberately does NOT do

**No default report destination.** `report_path` is opt-in and has no default, because a default
output path makes every run a writer of real state — measured twice in this repo, in opposite
directions. Without it the report is returned, not written.

**In-place only.** The subject is mutated where it lives and restored on every exit path that
runs code at all. Copying the tree to a sandbox first would make the clobber hazard below
structurally impossible, but it also
changes the fixture's environment — and a suite that resolves paths, reads git state, or reaches
`~/.claude` can stop reaching its subject entirely and go green for free. That trade wants its
own evidence before it ships, so it is not offered yet.

**Consequence of in-place, and it has bitten:** editing the subject while a campaign is running
gets clobbered by the restore. Do not edit the subject until the run finishes.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, NamedTuple, Sequence

STATUSES = ("PASS", "FAIL", "ERROR")

# Sidecar holding the pristine subject for the duration of a campaign. Beside the subject and
# not hidden, both on purpose: when a run dies this file IS the recovery, so a temp directory the
# OS may sweep would lose the original and the evidence together, and a dotfile would hide the
# one `??` that tells a human something died. The suffix keeps it off every `*.py` glob.
BACKUP_SUFFIX = ".mutate-backup"

# Catchable signals that terminate by default, so `finally` never runs. SIGINT is absent
# deliberately — it raises KeyboardInterrupt, so the existing `finally` already covers it, and
# installing a handler here would replace working behaviour with a reimplementation of it.
_FATAL_SIGNALS = ("SIGTERM", "SIGHUP")

# Deliberately broader than any one suite's convention, because the copies disagreed on exactly
# this: `FAIL`, `FAIL  ` (two spaces) and pytest's `FAILED` all had to match. Anchored at line
# start so a passing summary mentioning the word in passing cannot trip it — and even if a
# pattern does over-match, the baseline check catches it rather than inflating the score.
DEFAULT_FAIL_PATTERN = r"^FAIL"

CAUGHT = "CAUGHT"
SURVIVED = "SURVIVED"
TIMEOUT = "TIMEOUT"
OUTCOME_STATUSES = (CAUGHT, SURVIVED, TIMEOUT)

# A hung suite is the killed-run defect's twin, and strictly worse: a killed campaign at least
# ends, while a hung one never fires the restore at all — not `finally`, not the SIGTERM handler.
# Measured: a campaign ran past 13 minutes with its subject sitting modified the whole time.
#
# The limit is DERIVED from the baseline's own measured duration rather than hand-picked, because
# no single constant serves every suite: tight enough to catch a 6s suite's hang promptly, it
# truncates a 17s one and reports a hang that never happened. The floor is the declared minimum
# that derivation may never fall below — a fast suite must not get a uselessly tight cap.
#
# The multiplier is set from a MEASURED worst case, not taste, and the first value tried (5x) was
# already wrong. A mutation of `run-long.sh` that the suite genuinely CATCHES takes 297s against
# that suite's 28s baseline — 10.6x — because every bounded retry loop in it exhausts its full
# budget once the predicate it polls for is broken. At 5x that scored a false TIMEOUT. 20x is
# that observation doubled for headroom, and it still terminates a genuinely infinite hang.
TIMEOUT_FLOOR_SECONDS = 60.0
TIMEOUT_MULTIPLIER = 20.0

# The baseline has no measured duration of its own to scale from, so it gets a flat, generous
# cap. A suite that needs more than this UNMUTATED is pathological and worth being told about.
BASELINE_TIMEOUT_SECONDS = 600.0


def derive_timeout(baseline_elapsed: float, override):
    """Seconds to allow one mutant's suite run. `override` wins outright when given."""
    if override is not None:
        return override
    return max(TIMEOUT_FLOOR_SECONDS, TIMEOUT_MULTIPLIER * baseline_elapsed)


def _terminate_group(proc) -> None:
    """Kill the suite's whole process GROUP, not just the process we spawned.

    A suite that backgrounds work (this repo has one that does) leaves grandchildren behind when
    only the direct child is killed — measured as stray `sleep` processes and three concurrent
    copies of one suite, all outliving the campaign and adding noise the next mutation inherits.
    `start_new_session=True` at spawn is what makes one `killpg` sufficient here.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        # Already reaped, or the platform refused — fall back to the single process.
        try:
            proc.kill()
        except OSError:
            pass


class Mutation(NamedTuple):
    """One deliberate defect: replace `old` with `new`, exactly once, in the subject."""

    label: str
    old: str
    new: str


class Outcome(NamedTuple):
    label: str
    status: str  # one of OUTCOME_STATUSES
    detail: str


class Report(NamedTuple):
    status: str
    rc: int
    caught: int
    survived: int
    total: int
    outcomes: tuple
    text: str
    verdict: str
    timedout: int = 0


class _Run(NamedTuple):
    """One completed suite run. A run that TIMED OUT is represented by `None`, not by this."""

    returncode: int
    stdout: str
    noticed: bool
    elapsed: float


def is_caught(
    returncode: int, stdout: str, fail_pattern: str = DEFAULT_FAIL_PATTERN
) -> bool:
    """Did the suite NOTICE? One definition, used for both the baseline and every mutant.

    The disjunction is load-bearing in both directions, and each half covers a measured hole:

    * `returncode != 0` alone misses a **fail-open** suite, which prints its failure and exits 0
      anyway — a shape this repo has measured in its own test-runner hooks.
    * a failure-line scan alone misses a **crash**, which exits non-zero with no output at all
      and would otherwise be silently scored as a survivor.
    """
    return returncode != 0 or re.search(fail_pattern, stdout, re.M) is not None


def backup_path(subject) -> Path:
    """Where a campaign parks the pristine subject. Public: it is what a human recovers from."""
    subject = Path(subject)
    return subject.with_name(subject.name + BACKUP_SUFFIX)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clear_pycache(subject: Path) -> None:
    """Drop compiled caches beside a Python subject so the MUTANT is what actually runs.

    Python validates a `.pyc` against the source's (mtime, size). A mutation that preserves the
    byte length and lands inside the same mtime second therefore leaves the cache looking valid,
    and the pre-mutation code answers — the mutation reads as SURVIVED having never run. Only 1
    of the 8 prior harnesses did this.
    """
    if subject.suffix != ".py":
        return
    for cache in subject.parent.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def _verdict(
    status: str, rc: int, caught: int, survived: int, total: int, timedout: int = 0
) -> str:
    return (
        f"RESULT: {status} rc={rc} caught={caught} survived={survived} "
        f"timedout={timedout} total={total}"
    )


def _pct(part: float, whole: float) -> int:
    """Whole-number percent. Only ever called where `whole` is a cap that actually applied."""
    return round(100.0 * part / whole) if whole else 0


def _headroom_line(
    completed,
    timedout_labels,
    limit: float,
    baseline_elapsed: float,
    baseline_limit: float,
    override,
) -> str:
    """How close the slowest row came to its cap — one line, always prefixed `HEADROOM: `.

    RETROSPECTIVE by design, and the framing is the honest one: nobody reads a green campaign's
    report, so this warns no one in time. What it buys is that when a cap DOES bite, the PREVIOUS
    run's artifact already holds the answer. Measured 2026-08-09: a campaign returned
    `ERROR … timedout=1` on a pre-existing row after its suite grew 80 -> 108 rows, and diagnosis
    cost a full re-run plus an isolated single-row probe (CAUGHT at 1351.0s against a 26.9s
    baseline). The run BEFORE it had a slowest row at 628.5s of a 900s cap — 70%, recorded
    nowhere. The line lands in the report's last few lines — `restored:` and then `RESULT:`
    follow it — so a short `tail` of an artifact carries it without a grep.

    Every state shares the `HEADROOM: ` prefix so ONE grep finds it whatever happened, and the
    three states are not cosmetic:

    * A timed-out row has NO elapsed — `run_suite` returns None and the `_Run` carrying `elapsed`
      is never built — so it contributes nothing to a "slowest completed" figure. On the one run
      that matters most, a naive implementation would therefore print a reassuring percentage
      beside an actual failure. `exhausted` LEADS instead, the timed-out rows are named, and the
      completed rows follow explicitly marked secondary.
    * Rows that ran but none COMPLETED get prose. Never `0%`, which is indistinguishable from a
      fast healthy run, and never a division. Reachable when every row was NOT APPLIED — which
      has `timedout == 0`, so it must not say "exhausted" either.
    * The normal state names the row's LABEL, not just a number. A campaign where every row is
      caught fast has enormous headroom and says nothing; the figure is only meaningful against
      the row it belongs to, and without the label it invites raising the cap when the answer may
      be that one row is pathological.

    `derived=` appears only when an override is in force, and asserts NOTHING. There is no
    threshold and no warning severity here: the runner sees one run and cannot compute a trend.
    But TIMEOUT_MULTIPLIER is calibrated from a measured worst case rather than taste, so
    printing what the cap WOULD have been makes "this row survives only because of the override"
    legible without the runner claiming a judgement it cannot support. On the run before the
    incident that reads 628.5s against `max(60, 20x26.9)` = 538s — exceeded, one run early.

    The baseline's own cap rides along because it is the one guaranteed to erode:
    BASELINE_TIMEOUT_SECONDS is FLAT, while the per-mutant cap DERIVES from the baseline and so
    rescales as a suite grows. The existing `BASELINE green` line prints the baseline's elapsed
    and the MUTANT limit — never the limit the baseline itself ran under.
    """
    derived = ""
    if override is not None:
        derived = f" derived={derive_timeout(baseline_elapsed, None):.0f}s"
    base = (
        f" baseline={baseline_elapsed:.1f}s/{baseline_limit:.0f}s"
        f" ({_pct(baseline_elapsed, baseline_limit)}%)"
    )
    slowest = max(completed, key=lambda row: row[1]) if completed else None

    if timedout_labels:
        named = ", ".join(repr(label) for label in timedout_labels)
        if slowest is None:
            secondary = "no row completed, so no completed-row figure exists"
        else:
            secondary = (
                f"slowest completed={slowest[1]:.1f}s used={_pct(slowest[1], limit)}% "
                f"row={slowest[0]!r}"
            )
        return (
            f"HEADROOM: exhausted cap={limit:.0f}s{derived}{base}"
            f" — {len(timedout_labels)} row(s) consumed the whole cap: {named}"
            f" (secondary: {secondary})"
        )
    if slowest is None:
        return (
            f"HEADROOM: not measured cap={limit:.0f}s{derived}{base}"
            " — rows ran but none completed a suite run (all NOT APPLIED), so there is no"
            " percentage to report"
        )
    return (
        f"HEADROOM: slowest={slowest[1]:.1f}s cap={limit:.0f}s"
        f" used={_pct(slowest[1], limit)}%{derived}{base} row={slowest[0]!r}"
    )


def _stream(line: str) -> None:
    """Write one progress line to stdout and FLUSH it.

    The flush is the entire point, not hygiene. A campaign runs for tens of minutes and is
    invoked with its stdout redirected to a file, where Python block-buffers — so without the
    flush every line would still arrive only at exit, reproducing the defect this exists to
    close while *looking*, in the source, like it had been fixed.
    """
    print(line, flush=True)


def _report(
    status: str,
    rc: int,
    caught: int,
    survived: int,
    total: int,
    lines,
    outcomes=(),
    timedout: int = 0,
) -> Report:
    verdict = _verdict(status, rc, caught, survived, total, timedout)
    return Report(
        status=status,
        rc=rc,
        caught=caught,
        survived=survived,
        total=total,
        outcomes=tuple(outcomes),
        text="\n".join([*lines, verdict]),
        verdict=verdict,
        timedout=timedout,
    )


def run(
    subject,
    command: Sequence[str],
    mutations: Iterable[Mutation],
    *,
    cwd=None,
    report_path=None,
    fail_pattern: str = DEFAULT_FAIL_PATTERN,
    timeout=None,
    progress=_stream,
) -> Report:
    """Run every mutation against `command`, restore the subject, and report.

    Args:
        subject: the file to mutate, in place.
        command: argv for the suite that should notice each mutation.
        mutations: the per-change `Mutation` list — the only part meant to be written fresh.
        cwd: working directory for the suite.
        report_path: where to write the report. **No default, by design**; omit it and nothing
            is written anywhere.
        fail_pattern: line-anchored regex marking a failure in the suite's stdout.
        timeout: seconds to allow one suite run. Omit it and the limit is DERIVED from the
            baseline's measured duration (`derive_timeout`), which is what lets one default
            serve both a 6s suite and a 17s one.
        progress: called with one line per resolved mutation, AS IT RESOLVES. Defaults to a
            flushing write to stdout; pass `None` to silence it. This is deliberately
            default-ON: opting in would leave every campaign that forgot to, and every one
            written later, carrying the defect. It streams to stdout rather than a file, so
            it does not introduce an output DESTINATION — see the note on `report_path`.

    Returns:
        A `Report`. Its `verdict` is always the last line of its `text`. The streamed lines
        are progress only; `text` is unchanged by them and stays the authoritative record.
    """
    emit = progress if progress is not None else lambda _line: None
    subject = Path(subject)
    mutations = list(mutations)
    total = len(mutations)
    lines = [f"subject: {subject}", f"command: {' '.join(str(c) for c in command)}"]
    # Emitted before any work starts, so the artifact is non-empty from the first moment. An
    # empty file is what a stall looks like, and that ambiguity is the whole defect.
    for line in lines:
        emit(line)

    backup = backup_path(subject)
    if backup.exists():
        # A previous campaign died before its restore. Whether that DAMAGED anything is settled
        # by content, never by the file's presence: a run killed before its first mutation
        # landed left the subject pristine, and refusing there would false-block every
        # legitimate campaign that followed.
        if backup.read_text() == subject.read_text():
            backup.unlink()
            lines.append(
                f"cleared an undamaged backup from an interrupted run: {backup}"
            )
        else:
            lines += [
                "ERROR  the subject does not match the backup an interrupted campaign left "
                "behind — it is still MUTATED. The baseline check cannot be relied on to "
                "notice: it only goes red where the suite happens to cover the mutated path.",
                f"       the original is preserved at {backup} — recover with:",
                f"       cp {backup} {subject} && rm {backup}",
            ]
            return _report("ERROR", 2, 0, 0, total, lines)

    # A campaign of nothing is the limiting case of a discovery that found nothing: it would
    # report `PASS caught=0 survived=0` — success, stated loudest of all, on a denominator of
    # zero. Refuse instead.
    if total == 0:
        lines.append("ERROR  no mutations supplied — a campaign of zero proves nothing")
        return _report("ERROR", 2, 0, 0, 0, lines)

    def run_suite(limit):
        """Run the suite once under `limit` seconds. Returns None if it TIMED OUT.

        `start_new_session=True` is what makes the timeout enforceable: it puts the suite in its
        own process group, so one `killpg` reaps whatever it backgrounded rather than orphaning
        it into the next mutation's run.
        """
        _clear_pycache(subject)
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            start_new_session=True,
        )
        started = time.monotonic()
        try:
            stdout, _ = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            _terminate_group(proc)
            try:
                proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            return None
        return _Run(
            returncode=proc.returncode,
            stdout=stdout,
            noticed=is_caught(proc.returncode, stdout, fail_pattern),
            elapsed=time.monotonic() - started,
        )

    original = subject.read_text()
    before = _sha256(subject)
    try:
        backup.write_text(original)
    except OSError as exc:
        lines.append(f"ERROR  cannot write the backup {backup} — {exc}")
        return _report("ERROR", 2, 0, 0, total, lines)

    def restore_and_die(signum, _frame):
        """Run the restore a default SIGTERM would otherwise skip, then die as asked.

        Resetting to SIG_DFL and re-raising is what preserves the caller's view of the exit
        status — the process still reports killed-by-signum, so a wrapper reading rc 143 still
        reads 143 and does not mistake a handled signal for a clean finish.
        """
        try:
            subject.write_text(original)
            backup.unlink(missing_ok=True)
        finally:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    installed = {}
    for name in _FATAL_SIGNALS:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        if signal.getsignal(sig) == signal.SIG_IGN:
            # The parent deliberately arranged for this signal to be ignored — `nohup` does it
            # for SIGHUP, and a shell backgrounding a job does it for SIGINT/SIGQUIT, which is
            # how these campaigns are actually run now that the foreground cap is what strands a
            # subject. Installing a handler here would UN-ignore it and kill a run the operator
            # asked to survive. Measured: backgrounding is not a hypothetical here.
            continue
        try:
            installed[sig] = signal.signal(sig, restore_and_die)
        except (OSError, ValueError):
            # Not the main thread, or the platform refuses. Uncovered by a handler is not
            # uncovered: the backup file carries this case, exactly as it carries SIGKILL.
            pass

    outcomes = []
    caught = survived = timedout = 0
    # Per-row elapsed, RETAINED rather than only streamed, because `_headroom_line` reads it. The
    # two lists are disjoint and neither holds a NOT APPLIED row: such a row never runs the suite
    # at all, which is why "none completed" is a state of its own rather than a zero.
    completed, timedout_labels = [], []

    try:
        # Hoisted so the cap the baseline RAN under and the cap the report PRINTS are one value.
        # Two spellings of one intent is itself the defect: this repo has measured a tool whose
        # post-action pass blessed the state its own read-only mode called broken, because each
        # side had re-derived the same rule slightly differently.
        baseline_limit = timeout if timeout is not None else BASELINE_TIMEOUT_SECONDS
        baseline = run_suite(baseline_limit)
        if baseline is None:
            lines.append(
                "ERROR  the baseline timed out — the suite never finishes even UNMUTATED, so "
                "no verdict can be reached about any mutation"
            )
            return _report("ERROR", 2, 0, 0, total, lines)
        if baseline.noticed:
            tail = (baseline.stdout.strip().split("\n") or [""])[-1]
            lines += [
                "ERROR  the baseline is not green — the suite fails BEFORE any mutation, so "
                "every mutation would score as caught",
                f"       rc={baseline.returncode}  {tail[:96]}",
                "       (an always-true fail_pattern reads identically here, which is the "
                "second thing this check is for)",
            ]
            return _report("ERROR", 2, 0, 0, total, lines)
        limit = derive_timeout(baseline.elapsed, timeout)
        lines.append(
            f"BASELINE green  rc={baseline.returncode}  {baseline.elapsed:.1f}s "
            f"(per-mutant timeout {limit:.0f}s)"
        )
        emit(f"[0/{total}] {lines[-1]}")

        for n, m in enumerate(mutations, 1):
            count = original.count(m.old)
            if count != 1:
                # NOT a skip. An unapplied mutation establishes nothing, and counting it as
                # skipped is what lets a stale mutation list report a clean sweep.
                outcomes.append(
                    Outcome(
                        m.label,
                        SURVIVED,
                        f"patch target appears {count}x, need exactly 1",
                    )
                )
                survived += 1
                emit(f"[{n}/{total}] {SURVIVED:9s} {m.label}  (not applied)")
                continue

            subject.write_text(original.replace(m.old, m.new, 1))
            run = run_suite(limit)
            if run is None:
                # NEITHER caught nor survived. A hang is indeterminate — the suite may or may
                # not have gone on to notice — and crediting it either way states something
                # that was not measured. CAUGHT would be the inflating direction, which is how
                # every prior hole in this module failed, so it gets its own outcome and the
                # campaign ends in ERROR: no verdict was reached.
                outcomes.append(
                    Outcome(
                        m.label,
                        TIMEOUT,
                        f"the suite did not finish within {limit:.0f}s — indeterminate",
                    )
                )
                timedout += 1
                timedout_labels.append(m.label)
            elif run.noticed:
                tail = (run.stdout.strip().split("\n") or [""])[-1]
                outcomes.append(Outcome(m.label, CAUGHT, tail[:96]))
                caught += 1
            else:
                outcomes.append(
                    Outcome(m.label, SURVIVED, "the suite asserts nothing about this")
                )
                survived += 1
            # Per-mutation elapsed, not just the outcome: a 297s outlier in a 14-row campaign
            # took three investigations to find, and one timed line would have shown it first.
            # A timed-out row has no elapsed to show, so the streamed figure falls back to the
            # cap it was killed at — which is a LOWER bound, and the reason `_headroom_line`
            # refuses to turn that row into a percentage.
            took = limit
            if run is not None:
                completed.append((m.label, run.elapsed))
                took = run.elapsed
            emit(f"[{n}/{total}] {outcomes[-1].status:9s} {m.label}  {took:.1f}s")
    finally:
        subject.write_text(original)
        # The invariant this buys: a backup on disk means the subject MAY STILL BE MUTATED.
        # Dropping it here covers every exit path that runs code at all — including the
        # KeyboardInterrupt a Ctrl-C raises, which unwinds past the normal return and would
        # otherwise strand an undamaged backup as litter someone could commit. A killed run
        # never reaches this line, which is exactly when the backup has to survive.
        try:
            if _sha256(subject) == before:
                backup.unlink(missing_ok=True)
        except OSError:
            pass
        for sig, previous in installed.items():
            signal.signal(sig, previous)

    for o in outcomes:
        lines.append(f"{o.status:9s} {o.label}")
        if o.detail:
            lines.append(f"          {o.detail}")

    # Emitted HERE — after the outcome loop, before the restore check — so BOTH post-loop reports
    # carry it: the normal one and the restore-failure ERROR alike. Appending it just before the
    # final `_report` instead would silently drop the line from the restore-failure path, which is
    # the report a reader has most reason to grep. The pre-loop early returns (stale backup, empty
    # mutation list, backup-write failure, baseline timeout, baseline red) legitimately do not emit
    # it: they have no rows, and the baseline paths have no `limit` computed at all.
    lines.append(
        _headroom_line(
            completed, timedout_labels, limit, baseline.elapsed, baseline_limit, timeout
        )
    )

    after = _sha256(subject)
    if after != before:
        # The backup is deliberately NOT removed here — it is the only surviving copy.
        lines += [
            f"ERROR  subject NOT restored — sha256 before={before[:16]} after={after[:16]}",
            f"       the original is preserved at {backup} — recover with:",
            f"       cp {backup} {subject} && rm {backup}",
        ]
        return _report(
            "ERROR", 2, caught, survived, total, lines, outcomes, timedout=timedout
        )
    lines.append(f"restored: sha256 unchanged ({before[:16]})")

    # ERROR outranks FAIL: a timed-out mutation means no verdict was reached about it, and a
    # campaign that could not judge every row has not judged the change.
    if timedout:
        status, rc = "ERROR", 2
    elif survived:
        status, rc = "FAIL", 1
    else:
        status, rc = "PASS", 0
    report = _report(
        status, rc, caught, survived, total, lines, outcomes, timedout=timedout
    )

    if report_path is not None:
        dest = Path(report_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(report.text + "\n")

    return report


_EXAMPLE = """\
from mutate import Mutation, run

report = run(
    SUBJECT,                                    # Path to the file to mutate
    ["python3", "-m", "pytest", "-q", SUITE],   # argv of the suite that must notice
    [Mutation("label", "old text", "new text")],
    report_path=REPORT,                         # no default - omit and nothing is written
)
raise SystemExit(report.rc)"""


def _render_default(value) -> str:
    """Render a parameter default without leaking a memory address."""
    return value.__name__ if callable(value) else repr(value)


def _usage() -> str:
    """Build usage text, DERIVING the call signature from the code itself.

    Derived rather than restated: a usage string repeating the signature is a copy, and a copy
    drifts the moment either side changes. Nothing would ever compare them, so the drift is silent.
    """
    import inspect

    rendered = []
    for name, param in inspect.signature(run).parameters.items():
        if param.kind is inspect.Parameter.KEYWORD_ONLY and "*" not in rendered:
            rendered.append("*")
        if param.default is inspect.Parameter.empty:
            rendered.append(name)
        else:
            rendered.append(name + "=" + _render_default(param.default))
    return "\n".join(
        [
            (__doc__ or "").splitlines()[0],
            "This is a LIBRARY - import it; running it directly only prints this text.",
            "",
            "    from mutate import Mutation, run",
            "    run(" + ", ".join(rendered) + ")",
            "",
            "    Mutation fields: " + ", ".join(Mutation._fields),
            "",
            "Minimal campaign:",
            _EXAMPLE,
            "",
            "Contract points a signature cannot show:",
            "  * report_path has NO default - omit it and nothing is written anywhere.",
            "  * The unmutated BASELINE must be green, or the campaign is ERROR, not PASS.",
            "  * The subject is mutated in place and restored; do not edit it during a run.",
            "  * Each old string must appear EXACTLY ONCE in the subject.",
            "",
            "Worked examples: scripts/tests/mutate_*.py",
        ]
    )


def main(argv) -> int:
    """Print usage; 0 when it was asked for, 2 otherwise."""
    if list(argv) in (["--help"], ["-h"]):
        print(_usage())
        return 0
    print(_usage(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
