#!/usr/bin/env python3
# Script: publish-fold-plan.py
# Purpose: Propose brick boundaries for the publish path by classifying what each commit removes
# Usage: publish-fold-plan.py [--scope <path>] [--watermark <ref>] [--published <ref>]
"""Propose brick boundaries for /propagate's adopted publish path, with the evidence.

The publish path folds a fix into the brick it fixes **when that brick is also unpublished**;
a fix targeting already-published work becomes its own new brick, because published `main` is
immutable and is never rewritten to absorb a later fix. Deciding which is which by hand, per
commit, is the part of a publish that gets re-derived from prose every time.

**The test is mechanical, and it keys on the lines a commit REMOVES.**

1. It removes nothing (`+N/-0`) — OWN BRICK, settled immediately. This costs one diff and no
   `merge-base` call at all, so it is tried first, every time.
2. Some removed line still exists in the published tree — OWN BRICK. The commit is editing
   published content, which this path may only append past, never rewrite.
3. Every removed line was added by an in-range commit — FOLD into the latest such commit.
4. Anything else — UNDECIDED, defaulting to OWN BRICK and flagged for the operator. Blank
   removed lines are the common case: a bare `-` matches a blank line in nearly any file, so it
   is evidence of nothing and is filtered out before any of the above is asked.

**This proposes; it never decides.** Brick boundaries are judgment, and step 5's convergence
check structurally cannot catch a wrong fold — a fix folded into the wrong brick converges to
the identical final tree. So the default direction is deliberately the safe one: when the
evidence does not settle it, the commit stands alone. A missed fold costs tidiness; a wrong
fold misrepresents which brick fixed what, permanently and in public.

The `--audit`-style holistic pairings the publish path requires (a skill and its regenerated
sync-docs index entry in one brick; a shebang file and its exec bit in one brick) are NOT
modelled here — they are a reason to merge two proposed bricks by hand before running them.

Usage: publish-fold-plan.py [--scope <path>] [--watermark <ref>] [--published <ref>]
                            [--working <ref>]

Exit codes: 0 a plan was produced, 1 nothing to plan (empty range), 2 usage/precondition error.
Terminal verdict line: `RESULT: <STATUS> rc=<n> commits=<n> bricks=<n> folds=<n> undecided=<n>`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_WATERMARK = "refs/published/main"
DEFAULT_PUBLISHED = "main"
DEFAULT_WORKING = "dev"
SUBJECT_RE = re.compile(r"^(?P<type>[a-z]+)(\((?P<scope>[^)]*)\))?(?P<bang>!)?:")


class PlanError(Exception):
    """A precondition failed; no plan can be produced."""


def git(scope: str, *args: str) -> str:
    """Run git in `scope` and return its stdout, raising PlanError on failure."""
    proc = subprocess.run(
        ["git", "-C", scope, *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise PlanError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def git_ok(scope: str, *args: str) -> bool:
    """True when the git command exits 0. For predicates, where failure is an answer."""
    return (
        subprocess.run(
            ["git", "-C", scope, *args], capture_output=True, text=True, check=False
        ).returncode
        == 0
    )


def diff_lines(
    scope: str, sha: str
) -> tuple[dict[str, set[str]], dict[str, set[str]], int]:
    """Return ({path: removed non-blank}, {path: added non-blank}, raw removal count).

    Blank lines are dropped on both sides: a removed blank matches nearly any file, so it can
    only manufacture a false verdict, never support a true one. The RAW count is returned
    alongside so the caller can still tell "removed nothing at all" from "removed only blanks"
    — reporting the second as the first would be a false statement about the commit.
    """
    out = git(
        scope, "show", "--format=", "--unified=0", "--no-renames", "--no-color", sha
    )
    removed: dict[str, set[str]] = {}
    added: dict[str, set[str]] = {}
    raw_removed = 0
    old_path: str | None = None
    new_path: str | None = None
    for line in out.split("\n"):
        if line.startswith("--- "):
            target = line[4:].strip()
            old_path = None if target == "/dev/null" else target[2:]
        elif line.startswith("+++ "):
            target = line[4:].strip()
            new_path = None if target == "/dev/null" else target[2:]
        elif line.startswith(("@@", "diff --git", "index ", "old mode", "new mode")):
            continue
        elif line.startswith("-") and old_path:
            raw_removed += 1
            if line[1:].strip():
                removed.setdefault(old_path, set()).add(line[1:])
        elif line.startswith("+") and new_path and line[1:].strip():
            added.setdefault(new_path, set()).add(line[1:])
    return removed, added, raw_removed


def bump(base: str, subject: str) -> str:
    """Return the next version after `base` for a commit with this subject.

    Follows /commit's rules: `!` is MAJOR except below v1.0.0 where it is MINOR (SemVer 0.x
    says anything may change, so reaching 1.0.0 stays a deliberate choice); `feat` is MINOR;
    everything else is PATCH.
    """
    major, minor, patch = (int(part) for part in base.lstrip("v").split("."))
    match = SUBJECT_RE.match(subject)
    kind = match.group("type") if match else ""
    breaking = bool(match and match.group("bang"))
    if breaking and major > 0:
        return f"v{major + 1}.0.0"
    if breaking or kind == "feat":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def base_version(scope: str, published: str) -> str:
    """The latest tag reachable from the published branch, or v0.0.0 when there is none."""
    proc = subprocess.run(
        ["git", "-C", scope, "describe", "--tags", "--abbrev=0", published],
        capture_output=True,
        text=True,
        check=False,
    )
    tag = proc.stdout.strip()
    return (
        tag if proc.returncode == 0 and re.match(r"^v\d+\.\d+\.\d+$", tag) else "v0.0.0"
    )


class Planner:
    """Classifies one `watermark..working` range against a published tree."""

    def __init__(
        self, scope: str, watermark: str, published: str, working: str
    ) -> None:
        self.scope = scope
        self.watermark = watermark
        self.published = published
        self.working = working
        self._published_cache: dict[str, set[str]] = {}
        # (path, line) -> the latest IN-RANGE commit that added it, built as the walk proceeds
        self.origin: dict[tuple[str, str], str] = {}

    def published_lines(self, path: str) -> set[str]:
        """Non-blank lines of `path` in the published tree; empty when it is not there."""
        if path not in self._published_cache:
            proc = subprocess.run(
                ["git", "-C", self.scope, "show", f"{self.published}:{path}"],
                capture_output=True,
                text=True,
                check=False,
            )
            body = proc.stdout if proc.returncode == 0 else ""
            self._published_cache[path] = {
                line for line in body.split("\n") if line.strip()
            }
        return self._published_cache[path]

    def classify(self, sha: str) -> tuple[str, str, list[str], str | None]:
        """Return (verdict, detail, evidence, fold-target) for one in-range commit.

        The fold target is returned as its own value rather than embedded in the prose: a
        later step needs it, and re-parsing this function's own report to recover it would
        make the report's wording load-bearing.
        """
        removed, added, raw_removed = diff_lines(self.scope, sha)

        if not removed:
            self._record(sha, added)
            if raw_removed:
                return (
                    "UNDECIDED",
                    f"removes only blank lines ({raw_removed}), which are evidence of "
                    "nothing either way",
                    [],
                    None,
                )
            return "OWN BRICK", "removes no lines", [], None

        still_published = [
            f"{path}: {line!r}"
            for path, lines in sorted(removed.items())
            for line in sorted(lines)
            if line in self.published_lines(path)
        ]
        if still_published:
            self._record(sha, added)
            return (
                "OWN BRICK",
                f"removes a line still present in published {self.published}",
                still_published[:3],
                None,
            )

        authors: set[str] = set()
        evidence: list[str] = []
        for path, lines in sorted(removed.items()):
            for line in sorted(lines):
                origin = self.origin.get((path, line))
                if origin is None:
                    self._record(sha, added)
                    return (
                        "UNDECIDED",
                        f"removes {path}: {line!r}, which is neither published nor added "
                        "in range — the evidence does not settle it",
                        [],
                        None,
                    )
                authors.add(origin)
                if len(evidence) < 3:
                    evidence.append(f"{path}: {line!r} added by {origin[:7]}")

        # The LATEST author: that is the brick whose final state this commit corrects.
        target = max(authors, key=self._order_key)
        self._record(sha, added)
        return (
            "FOLD INTO",
            "every line it removes was added in-range",
            evidence,
            target,
        )

    def _record(self, sha: str, added: dict[str, set[str]]) -> None:
        for path, lines in added.items():
            for line in lines:
                self.origin[(path, line)] = sha

    def _order_key(self, sha: str) -> int:
        return self._order.index(sha) if sha in self._order else -1

    def run(self) -> list[dict]:
        """Classify every commit in the range, oldest first."""
        commits = git(
            self.scope, "rev-list", "--reverse", f"{self.watermark}..{self.working}"
        ).split()
        self._order = commits
        results = []
        for sha in commits:
            subject = git(self.scope, "log", "-1", "--format=%s", sha).strip()
            verdict, detail, evidence, target = self.classify(sha)
            results.append(
                {
                    "sha": sha,
                    "subject": subject,
                    "verdict": verdict,
                    "detail": detail,
                    "evidence": evidence,
                    "target": target,
                }
            )
        return results


def assemble(results: list[dict]) -> list[dict]:
    """Group classified commits into bricks, oldest brick first.

    A brick keeps the INTRODUCING commit's subject (it describes what the brick is) and takes
    its content from the LAST member (the corrected state) — which is exactly the endpoint
    contract publish-brick.sh materialises against.
    """
    bricks: list[dict] = []
    index: dict[str, int] = {}
    for item in results:
        slot = index.get(item["target"]) if item["target"] else None
        if slot is not None:
            bricks[slot]["members"].append(item["sha"])
            index[item["sha"]] = slot
            continue
        index[item["sha"]] = len(bricks)
        bricks.append({"subject": item["subject"], "members": [item["sha"]]})
    return bricks


def report(scope: str, args: argparse.Namespace, results: list[dict]) -> int:
    """Print the plan and its verdict line. Returns the process exit code."""
    wm_short = git(scope, "rev-parse", "--short", args.watermark).strip()
    pub_short = git(scope, "rev-parse", "--short", args.published).strip()
    print(f"range:     {wm_short}..{args.working}  ({len(results)} commits)")
    print(f"published: {args.published} @ {pub_short}")
    merges = git(
        scope, "rev-list", "--merges", f"{args.watermark}..{args.working}"
    ).split()
    if merges:
        print(
            f"NOTE: {len(merges)} merge commit(s) in range are not classified — "
            "they are proposed as their own bricks and need a human call"
        )
    print()

    for n, item in enumerate(results, 1):
        head = item["verdict"]
        if item["target"]:
            head = f"{head} {item['target'][:7]}"
        print(f"[{n}] {item['sha'][:7]}  {item['subject']}")
        print(f"    -> {head} — {item['detail']}")
        for line in item["evidence"]:
            print(f"       {line}")

    bricks = assemble(results)
    version = base_version(scope, args.published)
    print("\nproposed bricks — run in order, reading each verdict before the next:")
    for brick in bricks:
        version = bump(version, brick["subject"])
        endpoint = brick["members"][-1][:7]
        extra = " ".join(sha[:7] for sha in brick["members"][:-1])
        subject = brick["subject"].replace("'", "'\\''")
        line = f"  publish-brick.sh {version} {endpoint} '{subject}'"
        print(f"{line} {extra}".rstrip())

    folds = sum(1 for item in results if item["verdict"] == "FOLD INTO")
    undecided = sum(1 for item in results if item["verdict"] == "UNDECIDED")
    if undecided:
        print(
            f"\n{undecided} commit(s) are UNDECIDED and stand alone by default — "
            "review them before running the plan."
        )
    print(
        f"\nRESULT: PASS rc=0 commits={len(results)} bricks={len(bricks)} "
        f"folds={folds} undecided={undecided}"
    )
    return 0


def main(argv: list[str]) -> int:
    """Produce the fold plan, or explain why one cannot be produced."""
    parser = argparse.ArgumentParser(add_help=True, description=__doc__)
    parser.add_argument("--scope", default=None)
    parser.add_argument("--watermark", default=DEFAULT_WATERMARK)
    parser.add_argument("--published", default=DEFAULT_PUBLISHED)
    parser.add_argument("--working", default=DEFAULT_WORKING)
    args = parser.parse_args(argv)

    scope = args.scope or "."
    if not git_ok(scope, "rev-parse", "--show-toplevel"):
        print(f"not a git repository: {scope}", file=sys.stderr)
        return 2
    scope = git(scope, "rev-parse", "--show-toplevel").strip()

    if not Path(scope, ".publication.toml").is_file():
        print(
            f"no .publication.toml at {scope} — not an adopted repo, so there is no "
            "publish path to plan",
            file=sys.stderr,
        )
        return 2

    for ref, label in (
        (args.watermark, "watermark"),
        (args.published, "published branch"),
        (args.working, "working branch"),
    ):
        if not git_ok(scope, "rev-parse", "--verify", "-q", ref):
            print(f"no {label} at {ref}", file=sys.stderr)
            return 2

    if not git_ok(scope, "merge-base", "--is-ancestor", args.watermark, args.working):
        print(
            f"the watermark {args.watermark} is not an ancestor of {args.working} — "
            "a rebase or amend stranded it; abort rather than guess a replacement",
            file=sys.stderr,
        )
        return 2

    try:
        results = Planner(scope, args.watermark, args.published, args.working).run()
    except PlanError as exc:
        print(f"cannot plan: {exc}", file=sys.stderr)
        return 2

    if not results:
        print(
            f"nothing after the watermark — no bricks to derive onto {args.published}"
        )
        print("RESULT: FAIL rc=1 commits=0 bricks=0 folds=0 undecided=0")
        return 1

    return report(scope, args, results)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
