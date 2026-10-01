#!/usr/bin/env python3
# Script: mutation-anchors-check.py
# Purpose: Assert every mutation campaign's `old` anchor resolves exactly once and its mutant parses
# Usage: mutation-anchors-check.py --scope <repo>
"""Assert that no mutation campaign's anchor has come unstuck from the file it mutates.

A campaign (`scripts/tests/mutate_*.py`) declares rows of `Mutation(label, old, new)` and applies
them by replacing `old` with `new` in its SUBJECT. That makes `old` a load-bearing reference into
another file, maintained nowhere and checked by nothing until someone runs the campaign. This
check reads every row statically and requires `old` to occur in the subject **exactly once**, and
requires the mutant that replacement produces to still PARSE.

## The three measured defects it catches

* **Anchor rot.** The person refactoring a subject is the last one to think of re-pointing its
  campaign. Measured twice. The second time, one refactor broke two anchors in
  `mutate_lib_mutate.py`; a campaign whose `old` no longer resolves ERRORs instead of grading
  anything, so the sweep it was supposed to provide silently stops happening.
* **A live mutation stranded in the working tree.** A campaign killed (SIGKILL, a crash, a power
  loss) or hung between writing a mutant and restoring it leaves a corrupted tool on disk.
  Measured once, on a checker left reporting PASS on unreadable input — a verification tool
  inverted into a rubber stamp, with `git status` showing nothing unusual when the subject is
  untracked. `mutate.py`'s own signal handling narrows this window but cannot close it: no
  handler runs on SIGKILL.

Those two surface the same way — the row's `old` string is no longer present — which is why one
check covers both.

* **A mutant that does not parse.** `mutate.py` scores a mutant CAUGHT when the suite exits
  non-zero, and a subject that will not parse makes pytest abort collection (or bash refuse the
  script): a non-zero exit that catches nothing. Measured: `mutate_backlog.py:snapshot-failure-
  aborts` replaced the middle of a `print(` call and read CAUGHT while exercising no test. This
  check applies each anchored row's `old` -> `new` in memory and asks `mutate.judge_mutant_parse`,
  the same predicate the runner uses at run time, whether the result parses. `compile()` and
  `bash -n` parse without executing, so parsing a mutant executes nothing; the one import this
  check does make is described below.

It grades with ITS OWN installed copy of `mutate.py` (helpers resolve relative to the checker, not
the scope), so a change to the predicate is judged by the copy it replaces until it is installed.
A row is UNCHECKED — counted and named, never treated as valid — when its `new` cannot be resolved
statically (a call, an f-string), the subject is of a kind with no parser, or the pristine subject
does not itself parse under the check (no control, so no verdict). UNCHECKED is by design and never
fails a run. A row whose parser EXISTS but FAILED to run (`bash -n` timing out or failing to spawn,
`compile()` hitting the recursion limit) is CANNOT_RUN, which is not UNCHECKED: it is an ERROR
entry naming the count, the distinct reasons and each row, because reading a transient infrastructure failure
as by-design reduced coverage is how a run once printed `unchecked=25` (true value 3) and PASSed.

## Why only the `old` half is asserted

An earlier draft also required each `new` string to be ABSENT. That half is unsound: replacement
strings are routinely generic (`pass`, `if False:` both appear in the repo's current campaigns)
and occur legitimately all over the subjects, so it would false-positive immediately. It is also
redundant — a live mutation is already caught by its own `old` having gone missing. `new` is read
only to parse the mutant, never to assert its absence.

## Counted, not merely present

An anchor occurring TWICE is a defect too, and a different one: `mutate.py` refuses to apply a
row whose `old` is ambiguous, so that campaign is not grading anything either. Neither zero nor
two is visible without running the campaign.

## Static by construction — it imports only `scripts/lib/mutate.py`

Anchors are read with `ast`, not by importing a campaign. Two reasons, and the second is the
load-bearing one: an importing check would execute the scope's own code, and this runs inside
`/audit`'s STATIC sweep, whose hermetic guard only brackets the `--tests` phase. Code executed
outside that window could write to the tree with nothing watching.

The one import is the checker's OWN `scripts/lib/mutate.py`. Importing executes it, and whether
that is free of side effects is a property of whatever `mutate.py` sits there, not something this
checker guarantees. It is also a campaign subject (`mutate_lib_mutate.py`), so a
`mutate.py.mutate-backup` sidecar beside it (or beside the scope's own copy) means a mutant may be
stranded in a `mutate.py` this run depends on or grades: that is an ERROR, decided BEFORE the import so the possibly-mutated
code is never executed, since no verdict from a possibly-mutated predicate is trustworthy. An
import that fails for any reason (a stranded `if True:` raises SystemExit, a bad edit raises
SyntaxError) is the same ERROR, and so is an older `mutate.py` without the predicate (a
materialised copy at an old revision): each is stated as one rather than a traceback.

The resolver is an ALLOWLIST — string literals, implicit and `+` concatenation, and module-level
names bound to those. For `old` and SUBJECT anything else is an ERROR, never a skipped row: a
blocklist would admit every expression shape nobody thought of, and a silently skipped row is
exactly the vacuous pass this check exists to prevent. An unresolvable `new` is the one exception,
and it is counted as UNCHECKED rather than skipped silently: `new` is read only to parse the
mutant.

## Two populations, because discovery grades the COMMIT and the defect lives in the tree

Campaigns are GRADED from `git ls-files`, so the verdict is about the repo rather than about
whatever happens to be lying in a working tree. That alone reads as complete while skipping the
newest campaign in the scope — measured: `PASS … campaigns=6` where seven existed, `git add`
alone making it seven — and a campaign is untracked *precisely* when it is new, which is when its
anchors have never once been verified. Neither usual under-coverage guard catches it: the
denominator is non-zero, and a declared floor cannot name a member that did not exist when the
floor was written.

So a second predicate — untracked and unignored — reports what the first did not read, and every
verdict carries `untracked=<n>` so a clean run states its coverage instead of implying it. An
IGNORED campaign is a *declared* exclusion and stays invisible to both; that is the escape hatch
for a genuine scratch campaign, and the reason this is not simply a filesystem glob, which would
start failing runs over artifacts no commit will contain.

## Statuses are an allowlist

`PASS` (every anchor resolves exactly once and every checkable mutant parses), `FAIL` (an anchor
does not, or a mutant does not parse), `ERROR` (no verdict could be reached — an unparseable
campaign, a stranded `mutate.py` sidecar, a `mutate.py` that cannot be imported or lacks the
shared predicate, an unresolvable anchor or subject, a missing subject file, a campaign declaring
zero mutations, an untracked campaign this run did not read, a parse check that could not run for
one or more rows, or zero campaigns discovered).

An untracked campaign is ERROR rather than FAIL for the same reason an unparseable one is: the
run reached no verdict *about it*, and FAIL would claim a finding about an anchor nobody looked
at. Zero campaigns is an ERROR rather than a vacuous PASS, and so is an empty `MUTATIONS` list. A
sweep over nothing reports success loudest of all; `find` returning zero files reads exactly like
"nothing to fix". ERROR outranks FAIL — an unread campaign has not been judged, so a run holding
both must not report the weaker, more reassuring verdict.

Exit codes: 0 PASS, 1 FAIL, 2 ERROR. The last line of stdout is always the verdict, whose
`invalid=` and `unchecked=` fields (appended after `untracked=`) state how many mutants failed to
parse and how many rows nothing could judge; `bad=` keeps meaning anchor findings only.
"""

from __future__ import annotations

import argparse
import ast
import collections
import concurrent.futures
import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent / "lib"
# The literal suffix `mutate.py` parks its pristine copy under. NOT read from the imported module:
# the module is the thing a stranded mutant may have corrupted, so it cannot be asked where its
# own sidecar would be before the sidecar has been ruled out.
BACKUP_SUFFIX = ".mutate-backup"

# Imported lazily, by `_runner()`, and only after the sidecar check in `main`.
mutate = None

# What `main` needs from the runner. A copy lacking any of these (an older revision materialised
# beside this checker) cannot judge a mutant, and says so rather than raising.
PREDICATE_NAMES = (
    "check_parse",
    "judge_mutant_parse",
    "backup_path",
    "_parse_kind",
    "PARSES",
    "UNPARSEABLE",
    "UNCHECKED",
    "CANNOT_RUN",
)
POOL_WORKERS = 8
# A pool costs more than it saves for a handful of rows (every test sandbox has one or two).
POOL_MIN_JOBS = 16
POOL_CHUNK = 8


def _runner():
    """Import this checker's own `mutate.py` once, returning (module, why-not).

    `except BaseException`, not ImportError: a stranded mutant of `if __name__ == "__main__":`
    -> `if True:` makes the import raise SystemExit(2), and a mutated module can equally die of a
    SyntaxError or NameError. Any of them is "no verdict", never a traceback.
    """
    global mutate
    if mutate is not None:
        return mutate, ""
    sys.path.insert(0, str(LIB_DIR))
    try:
        import mutate as module
    except BaseException as exc:  # noqa: BLE001 — see the docstring
        return None, "could not be imported (%s: %s)" % (type(exc).__name__, exc)
    mutate = module
    return mutate, ""


def _sidecars(scope):
    """Existing `mutate.py.mutate-backup` files beside the checker's own runner and the scope's.

    They are the same file when a repo audits itself. Both are resolved the way
    `mutate.backup_path` resolves them, from a literal suffix rather than from the module.
    """
    found = set()
    for runner in (LIB_DIR / "mutate.py", scope / "scripts" / "lib" / "mutate.py"):
        runner = runner.resolve()
        found.add(runner.with_name(runner.name + BACKUP_SUFFIX))
    return sorted(p for p in found if p.exists())


CAMPAIGN_PREFIX = "mutate_"
MUTATIONS_NAME = "MUTATIONS"
SUBJECT_NAME = "SUBJECT"
ROOT_NAME = "REPO"


class Unresolvable(ValueError):
    """A campaign could not be read statically — reported as ERROR, never skipped."""


def _module_constants(tree):
    """Module-level `NAME = <expr>` bindings, so an anchor held in a constant resolves.

    Measured shape: `mutate_markdownlint_config.py` binds its `old` to a module-level `LIVE`
    and reuses it across three rows.
    """
    env = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            env[node.targets[0].id] = node.value
    return env


def _as_str(node, env, seen=()):
    """Resolve an expression to a string, or raise. Allowlist — see the module docstring."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _as_str(node.left, env, seen) + _as_str(node.right, env, seen)
    if isinstance(node, ast.Name):
        if node.id in seen:
            raise Unresolvable("%s is defined in terms of itself" % node.id)
        if node.id not in env:
            raise Unresolvable("%s is not a module-level constant" % node.id)
        return _as_str(env[node.id], env, seen + (node.id,))
    raise Unresolvable(
        "cannot read a %s statically — make it a string literal or a module-level "
        "constant" % type(node).__name__
    )


def _as_subject(node, env, campaign):
    """Resolve `SUBJECT = REPO / "a" / "b"` to a scope-relative path, or raise."""
    parts = []
    cur = node
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
        parts.append(_as_str(cur.right, env))
        cur = cur.left
    if not (isinstance(cur, ast.Name) and cur.id == ROOT_NAME):
        raise Unresolvable(
            "%s must be %s / <literal parts>, so it can be resolved without importing %s"
            % (SUBJECT_NAME, ROOT_NAME, campaign)
        )
    parts.reverse()
    for part in parts:
        if part in ("", ".", "..") or Path(part).is_absolute():
            raise Unresolvable("%s escapes the scope at %r" % (SUBJECT_NAME, part))
    return Path(*parts)


def _rows(node, env, campaign):
    """The (label, old, new) triples of a `MUTATIONS = [...]` list, or raise.

    `new` is None when it cannot be resolved statically: it is read only to parse the mutant, so
    an unreadable one makes that row UNCHECKED rather than an error. `old` never gets that
    latitude.
    """
    if not isinstance(node, ast.List):
        raise Unresolvable(
            "%s in %s is not a list literal" % (MUTATIONS_NAME, campaign)
        )
    out = []
    for index, element in enumerate(node.elts):
        if not isinstance(element, ast.Call):
            raise Unresolvable(
                "%s[%d] in %s is not a Mutation(...) call"
                % (MUTATIONS_NAME, index, campaign)
            )
        by_keyword = {kw.arg: kw.value for kw in element.keywords if kw.arg}
        args = list(element.args)
        try:
            label = args[0] if args else by_keyword["label"]
            old = args[1] if len(args) > 1 else by_keyword["old"]
        except (IndexError, KeyError):
            raise Unresolvable(
                "%s[%d] in %s does not supply both `label` and `old` positionally or by "
                "keyword" % (MUTATIONS_NAME, index, campaign)
            ) from None
        # A label that will not resolve is cosmetic, so fall back to the row's index rather
        # than failing the whole campaign over it. The `old` anchor is the subject of the
        # check, so it never gets that latitude.
        try:
            text = _as_str(label, env)
        except Unresolvable:
            text = "row %d" % index
        try:
            new = _as_str(args[2] if len(args) > 2 else by_keyword["new"], env)
        except (Unresolvable, KeyError):
            new = None
        out.append((text, _as_str(old, env), new))
    return out


def _git_campaigns(scope, *args):
    """Campaign paths from one `git ls-files` invocation, sorted for a stable report.

    `mutate.py` itself — the runner — does not match the prefix, so it is never mistaken for a
    campaign. Both discovery predicates share this one filter, so they cannot drift apart and
    disagree about what counts as a campaign.
    """
    proc = subprocess.run(
        ["git", "-C", str(scope), "ls-files", *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise Unresolvable(
            "cannot list files in %s — %s" % (scope, proc.stderr.strip())
        )
    found = []
    for line in proc.stdout.splitlines():
        name = Path(line).name
        if name.startswith(CAMPAIGN_PREFIX) and name.endswith(".py"):
            found.append(line)
    return sorted(found)


def _campaigns(scope):
    """The campaigns this run GRADES — tracked only, so the verdict is about the repo.

    Grading the working tree instead would start failing runs over scratch files no commit will
    ever contain. The gap that leaves is closed by `_untracked_campaigns` below, which reports
    rather than grades.
    """
    return _git_campaigns(scope)


def _untracked_campaigns(scope):
    """Campaigns present in the tree that this run did not read — the coverage gap, named.

    Discovery from version control grades the COMMITTED population, so a campaign written
    minutes ago is outside the sweep at exactly the moment nothing has ever checked it — and a
    campaign is untracked *precisely* when it is new, which is when an anchor is likeliest to be
    wrong. Measured: a run reported `campaigns=6` where seven existed; `git add` alone made it
    seven. Neither of the usual under-coverage guards fires here, since the denominator is
    non-zero and a declared floor cannot name a member that did not exist when it was written.

    `--exclude-standard` is what keeps this from becoming a filesystem glob. An IGNORED campaign
    is a *declared* exclusion — the repo states the file is not part of itself — while an
    untracked, unignored one is an undeclared omission, which is the whole defect. So ignoring a
    scratch campaign is the supported escape hatch.
    """
    return _git_campaigns(scope, "--others", "--exclude-standard")


def _inspect(scope, relative, controls):
    """Check one campaign: (rows_checked, anchor findings, mutant jobs, unchecked reasons).

    Raises Unresolvable on ERROR. A job is `(campaign, subject, label, original, old, new,
    control)` for a row whose anchor resolved exactly once — judged later, in `main`, so the slow
    `bash -n` ones can share one pool. `controls` caches the pristine parse per subject.
    """
    path = scope / relative
    try:
        tree = ast.parse(path.read_text(), filename=str(relative))
    except (OSError, SyntaxError) as exc:
        raise Unresolvable("%s: %s" % (relative, exc)) from None

    env = _module_constants(tree)
    for name in (SUBJECT_NAME, MUTATIONS_NAME):
        if name not in env:
            raise Unresolvable("%s declares no module-level %s" % (relative, name))

    try:
        subject_rel = _as_subject(env[SUBJECT_NAME], env, relative)
        rows = _rows(env[MUTATIONS_NAME], env, relative)
    except Unresolvable as exc:
        raise Unresolvable("%s: %s" % (relative, exc)) from None

    if not rows:
        raise Unresolvable(
            "%s declares ZERO mutations — refusing to report a sweep over an empty campaign"
            % relative
        )

    try:
        subject_text = (scope / subject_rel).read_text()
    except OSError as exc:
        raise Unresolvable(
            "%s names a subject that cannot be read: %s" % (relative, exc)
        ) from None

    if subject_rel not in controls:
        controls[subject_rel] = mutate.check_parse(subject_rel, subject_text)
    control = controls[subject_rel]

    findings = []
    jobs = []
    unchecked = []
    for label, old, new in rows:
        count = subject_text.count(old)
        if count != 1:
            findings.append((relative, subject_rel, label, old, count))
            unchecked.append("anchor did not resolve exactly once")
        elif new is None:
            unchecked.append("`new` is not a static literal")
        else:
            jobs.append((relative, subject_rel, label, subject_text, old, new, control))
    return len(rows), findings, jobs, unchecked


def _judge(job):
    campaign, subject, label, original, old, new, control = job
    try:
        return _runner()[0].judge_mutant_parse(
            subject, original, original.replace(old, new, 1), control
        )
    except Exception as exc:  # noqa: BLE001 — a judge that raised reached no verdict
        # Without this a raise (a UnicodeEncodeError from a stdin codec, say) escapes `main` as
        # a traceback with no RESULT line. Reported as CANNOT_RUN it becomes a named ERROR row.
        return _runner()[0].CANNOT_RUN, "the judge raised %s: %s" % (
            type(exc).__name__,
            exc,
        )


def _judge_all(jobs):
    """Judge every job, returning verdicts in job order so the report stays deterministic.

    A PROCESS pool, not threads. Measured on this repo: ~600 `.py` rows are CPU-bound `compile()`
    calls that hold the GIL (4.3 s alone), so a thread pool left the ~200 `bash -n` rows starving
    beside them — 7.4 s whatever the worker count, against 3.6 s for processes. Processes also make
    `check_parse`'s `warnings.catch_warnings()`, which is not thread-safe, a non-issue.
    """
    if len(jobs) < POOL_MIN_JOBS:
        return [_judge(job) for job in jobs]
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=POOL_WORKERS) as pool:
            return list(pool.map(_judge, jobs, chunksize=POOL_CHUNK))
    except Exception:  # noqa: BLE001 — see below
        # A sandbox that forbids spawning workers (OSError), a host without working semaphores
        # (NotImplementedError on 3.9), a worker killed mid-run (BrokenProcessPool) or a
        # worker-side exception are no reason to lose the verdict: the same predicate judged
        # serially answers the same question, and a genuine judgment error is raised again by
        # that serial pass, so this cannot hide one.
        return [_judge(job) for job in jobs]


def _describe(finding):
    campaign, subject, label, old, count = finding
    why = (
        "anchor rot, or a live mutation left in the subject"
        if count == 0
        else "ambiguous anchor — mutate.py refuses to apply a row it cannot place"
    )
    excerpt = old if len(old) <= 120 else old[:117] + "..."
    return "  %s → %s\n    %s\n    occurs %d× (%s):\n      %s" % (
        campaign,
        subject,
        label,
        count,
        why,
        excerpt.replace("\n", "\n      "),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scope", required=True, help="repo whose mutation campaigns are checked"
    )
    opts = parser.parse_args(argv)
    scope = Path(opts.scope)

    errors = []
    findings = []
    invalids = []
    unchecked = collections.Counter()
    cannot_run = []
    rows_checked = 0
    controls = {}
    zeros = "campaigns=0 rows=0 bad=0 untracked=0 invalid=0 unchecked=0"

    # Before the import: a sidecar beside `mutate.py` means a killed campaign may have left a
    # mutant INSIDE the runner, and importing it would execute that mutant. Both copies are
    # looked at — the checker's own, and the scope's, which is the same file when a repo audits
    # itself.
    stranded = _sidecars(scope)
    if stranded:
        for sidecar in stranded:
            sys.stdout.write(
                "%s exists: a killed campaign may have left a mutant in a mutate.py this run "
                "depends on or grades, so no verdict from it is trustworthy. Restore mutate.py "
                "from the sidecar before doing anything else.\n" % sidecar
            )
        sys.stdout.write("RESULT: ERROR rc=2 %s\n" % zeros)
        return 2

    runner, why = _runner()
    missing = [n for n in PREDICATE_NAMES if not hasattr(runner, n)]
    if runner is None or missing:
        sys.stdout.write(
            "this checker's own scripts/lib/mutate.py %s, so it cannot judge whether a mutant "
            "parses — no verdict.\n"
            % (
                why
                if runner is None
                else "lacks the shared predicate (%s: check_parse / judge_mutant_parse)"
                % ", ".join(missing)
            )
        )
        sys.stdout.write("RESULT: ERROR rc=2 %s\n" % zeros)
        return 2

    try:
        campaigns = _campaigns(scope)
        untracked = _untracked_campaigns(scope)
    except Unresolvable as exc:
        sys.stdout.write("%s\n" % exc)
        sys.stdout.write("RESULT: ERROR rc=2 %s\n" % zeros)
        return 2

    jobs = []
    for relative in campaigns:
        try:
            rows, found, campaign_jobs, skipped = _inspect(scope, relative, controls)
        except Unresolvable as exc:
            errors.append(str(exc))
            continue
        rows_checked += rows
        findings.extend(found)
        jobs.extend(campaign_jobs)
        unchecked.update(skipped)

    verdicts = _judge_all(jobs)
    assert len(verdicts) == len(jobs), (
        "the judge returned a different number of verdicts"
    )
    for job, (verdict, detail) in zip(jobs, verdicts):  # noqa: B905 — no strict= before 3.10; lengths asserted above
        if verdict == mutate.UNPARSEABLE:
            invalids.append((job[0], job[1], job[2], detail))
        elif verdict == mutate.CANNOT_RUN:
            # Not UNCHECKED: a parser existed and failed, so no verdict was reached.
            cannot_run.append((job[0], job[1], job[2], detail))
        elif verdict != mutate.PARSES:
            unchecked[detail] += 1

    if cannot_run:
        errors.append(
            "PARSE CHECK COULD NOT RUN for %d row(s) — no verdict about whether their mutants "
            "parse (not counted as unchecked): %s"
            % (len(cannot_run), "; ".join(sorted({row[3] for row in cannot_run})))
        )
        for campaign, subject, label, detail in cannot_run:
            errors.append("  %s → %s [%s]: %s" % (campaign, subject, label, detail))

    if not campaigns:
        errors.append(
            "no tracked %s*.py campaigns found under %s — refusing to report a sweep over "
            "zero campaigns, which passes against any repo whatsoever"
            % (CAMPAIGN_PREFIX, scope)
        )

    # An untracked campaign was not READ, so it has no verdict — the same category as a
    # campaign that could not be parsed, and deliberately not FAIL, which would claim a finding
    # about an anchor this run never looked at.
    for relative in untracked:
        errors.append(
            "%s is UNTRACKED, so this run graded everything except it. Discovery is "
            "`git ls-files`, which reads the committed population — and a campaign is "
            "untracked precisely when it is new, i.e. when its anchors have never once been "
            "verified. Run `git add %s`, or ignore it explicitly if it is scratch."
            % (relative, relative)
        )

    for relative in campaigns:
        sys.stdout.write("campaign: %s\n" % relative)

    if findings:
        sys.stdout.write("\nANCHORS THAT NO LONGER RESOLVE EXACTLY ONCE:\n")
        for finding in findings:
            sys.stdout.write("%s\n" % _describe(finding))
        sys.stdout.write(
            "\nRe-point each anchor at the text the subject now carries — or, if the subject\n"
            "is carrying a mutation from a killed campaign, restore it from its\n"
            ".mutate-backup sidecar before doing anything else.\n"
        )

    if invalids:
        sys.stdout.write("\nMUTANTS THAT DO NOT PARSE:\n")
        for campaign, subject, label, detail in invalids:
            sys.stdout.write(
                "  %s → %s\n    %s\n    %s\n" % (campaign, subject, label, detail)
            )
        sys.stdout.write(
            "\nA mutant that does not parse never exercises the suite: pytest aborts collection\n"
            "or bash refuses the script, every row fails, and the campaign scores it CAUGHT.\n"
            "Restate each as real pre-fix code that parses.\n"
        )

    if unchecked:
        sys.stdout.write(
            "\nUNCHECKED: %d row(s) no parse verdict could be reached for (never counted as "
            "valid):\n" % sum(unchecked.values())
        )
        for kind, count in sorted(unchecked.items()):
            sys.stdout.write("  %d × %s\n" % (count, kind))

    if errors:
        sys.stdout.write("\nNO VERDICT WAS REACHED FOR:\n")
        for message in errors:
            sys.stdout.write("  %s\n" % message)

    # ERROR outranks FAIL: a campaign that could not be read has not been judged, so a run
    # holding both must not report the weaker, more reassuring verdict.
    if errors:
        status, rc = "ERROR", 2
    elif findings or invalids:
        status, rc = "FAIL", 1
    else:
        status, rc = "PASS", 0
    # `untracked=` rides on every verdict, PASS included. Reporting it only when it is non-zero
    # would leave a clean run making a coverage claim with nothing behind it — which is the state
    # this field exists to end: the count was always printed, but a reader had no independent
    # expectation to compare it against.
    sys.stdout.write(
        "RESULT: %s rc=%d campaigns=%d rows=%d bad=%d untracked=%d invalid=%d unchecked=%d\n"
        % (
            status,
            rc,
            len(campaigns),
            rows_checked,
            len(findings),
            len(untracked),
            len(invalids),
            sum(unchecked.values()),
        )
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
