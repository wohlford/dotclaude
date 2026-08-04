#!/usr/bin/env python3
# Script: mutation-anchors-check.py
# Purpose: Assert every mutation campaign's `old` anchor still resolves exactly once in its subject
# Usage: mutation-anchors-check.py --scope <repo>
"""Assert that no mutation campaign's anchor has come unstuck from the file it mutates.

A campaign (`scripts/tests/mutate_*.py`) declares rows of `Mutation(label, old, new)` and applies
them by replacing `old` with `new` in its SUBJECT. That makes `old` a load-bearing reference into
another file, maintained nowhere and checked by nothing until someone runs the campaign. This
check reads every row statically and requires `old` to occur in the subject **exactly once**.

## The two measured defects it catches, which look identical from outside

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

Both surface the same way — the row's `old` string is no longer present — which is why one check
covers both.

## Why only the `old` half is asserted

An earlier draft also required each `new` string to be ABSENT. That half is unsound: replacement
strings are routinely generic (`pass`, `if False:` both appear in the repo's current campaigns)
and occur legitimately all over the subjects, so it would false-positive immediately. It is also
redundant — a live mutation is already caught by its own `old` having gone missing.

## Counted, not merely present

An anchor occurring TWICE is a defect too, and a different one: `mutate.py` refuses to apply a
row whose `old` is ambiguous, so that campaign is not grading anything either. Neither zero nor
two is visible without running the campaign.

## Static by construction — it never imports the campaign

Anchors are read with `ast`, not by importing. Two reasons, and the second is the load-bearing
one: an importing check would execute the scope's own code, and this runs inside `/audit`'s
STATIC sweep, whose hermetic guard only brackets the `--tests` phase. Code executed outside that
window could write to the tree with nothing watching.

The resolver is an ALLOWLIST — string literals, implicit and `+` concatenation, and module-level
names bound to those. Anything else is an ERROR, never a skipped row: a blocklist would admit
every expression shape nobody thought of, and a silently skipped row is exactly the vacuous pass
this check exists to prevent.

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

`PASS` (every anchor resolves exactly once), `FAIL` (at least one does not), `ERROR` (no verdict
could be reached — an unparseable campaign, an unresolvable anchor or subject, a missing subject
file, a campaign declaring zero mutations, an untracked campaign this run did not read, or zero
campaigns discovered).

An untracked campaign is ERROR rather than FAIL for the same reason an unparseable one is: the
run reached no verdict *about it*, and FAIL would claim a finding about an anchor nobody looked
at. Zero campaigns is an ERROR rather than a vacuous PASS, and so is an empty `MUTATIONS` list. A
sweep over nothing reports success loudest of all; `find` returning zero files reads exactly like
"nothing to fix". ERROR outranks FAIL — an unread campaign has not been judged, so a run holding
both must not report the weaker, more reassuring verdict.

Exit codes: 0 PASS, 1 FAIL, 2 ERROR. The last line of stdout is always the verdict.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

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
    """The (label, old) pairs of a `MUTATIONS = [...]` list, or raise."""
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
        out.append((text, _as_str(old, env)))
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


def _inspect(scope, relative):
    """Check one campaign, returning (rows_checked, findings). Raises Unresolvable on ERROR."""
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

    findings = []
    for label, old in rows:
        count = subject_text.count(old)
        if count != 1:
            findings.append((relative, subject_rel, label, old, count))
    return len(rows), findings


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
    rows_checked = 0

    try:
        campaigns = _campaigns(scope)
        untracked = _untracked_campaigns(scope)
    except Unresolvable as exc:
        sys.stdout.write("%s\n" % exc)
        sys.stdout.write("RESULT: ERROR rc=2 campaigns=0 rows=0 bad=0 untracked=0\n")
        return 2

    for relative in campaigns:
        try:
            rows, found = _inspect(scope, relative)
        except Unresolvable as exc:
            errors.append(str(exc))
            continue
        rows_checked += rows
        findings.extend(found)

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

    if errors:
        sys.stdout.write("\nCAMPAIGNS THIS RUN DID NOT JUDGE (no verdict for these):\n")
        for message in errors:
            sys.stdout.write("  %s\n" % message)

    # ERROR outranks FAIL: a campaign that could not be read has not been judged, so a run
    # holding both must not report the weaker, more reassuring verdict.
    if errors:
        status, rc = "ERROR", 2
    elif findings:
        status, rc = "FAIL", 1
    else:
        status, rc = "PASS", 0
    # `untracked=` rides on every verdict, PASS included. Reporting it only when it is non-zero
    # would leave a clean run making a coverage claim with nothing behind it — which is the state
    # this field exists to end: the count was always printed, but a reader had no independent
    # expectation to compare it against.
    sys.stdout.write(
        "RESULT: %s rc=%d campaigns=%d rows=%d bad=%d untracked=%d\n"
        % (status, rc, len(campaigns), rows_checked, len(findings), len(untracked))
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
