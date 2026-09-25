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

**Classifying each commit right does not make the PLAN right.** A brick sits at its FIRST
member's position but materialises the UNION of its members' paths at its LAST. Every non-member
commit strictly between first and last is therefore already baked into that endpoint's tree — if
it touches a path in the union, the brick publishes that commit's change early, under the wrong
subject. Two shapes were measured:

- Overwrite: the later brick holding the jumped commit then re-materialises the shared path at
  an EARLIER state. On a 27-commit publish the final tree would have been 10 lines short —
  visible to final-tree convergence, but only by the publish path's own check, after every brick
  was built and tagged.
- Misattribution: on the 28-commit `055a44d..85458d6` range, `3ee47c4` folding into
  `9c1cf46` (`fix(guards): …`) jumped `51a8127` (the alias fix), which rewrites three of that
  unit's paths — so the alias fix would have shipped one brick early under the `fix(guards)`
  subject, and its own brick would have been a near no-op. The final tree was IDENTICAL, so no
  convergence check could see it: attribution is wrong, the content is not.

So a fold is now dropped whenever its unit **jumps over** a commit sharing one of its paths — a
rule that SUBSUMES the overwrite shape too: collision-free implies convergent, for a
LINEAR range. Proof sketch: let a path `p`'s last toucher `t` sit in unit `U` (first `f`, last
`e`; `f < e` only when `U` is multi-member — a singleton has nothing strictly between). If `U` is
the last-positioned unit touching `p`, nothing after `t` touches it, so `blob(e, p) ==
blob(tip, p)`. Otherwise a later-positioned unit `V` (first `v > f`) holds some toucher `t'` of
`p` with `v <= t' <= e`; since `t' not in U`, `t'` is a jumped non-member of `U` that touches `p`
— a collision. So collision-free forces convergence, and the repair stays DROP, never reorder:
reordering is one more composition claim nothing has checked, and standing a commit alone costs
only tidiness.

The convergence check STAYS — not as the thing that catches this rule's own defect (it
structurally cannot, by the proof above), but as the plan's postcondition on everything the
jumped rule does not decide for it: a `RESULT: FAIL` there means the RANGE cannot converge AS
ORDERED (a merge, or any other non-linear history) — a fact about the range this file was asked
to plan over, not a silent repair and not a bug in this file to reorder its way out of. A
surviving fold can still jump over a commit that shares NO path with it at all (docs landing one
brick before the tool they document) — no path set can see that by construction, so it is listed
as an advisory for the operator to judge, never dropped on its own.

The `--audit`-style holistic pairings the publish path requires (a skill and its regenerated
sync-docs index entry in one brick; a shebang file and its exec bit in one brick) are NOT
modelled here — they are a reason to merge two proposed bricks by hand before running them.
Nor is per-brick VALIDITY: convergence constrains the FINAL tree only, so a surviving fold can
still leave an intermediate brick that fails its own `/audit` (a regenerated index naming a
file that brick has not added yet). `publish-rehearse.py` is the instrument for that gap: it
materialises each proposed brick cumulatively and runs the cross-file checks that can fail there
(`sync-docs`, `md-links`, `mutation-anchors`, `env-claims`, and conditionally `ruff`/`markdownlint`).
**Its residual:** it covers only the checks it runs at each brick, never the test suite, and it is
not a substitute for the apply-time audit, which remains authoritative — a rehearsal PASS is a
prediction, never a clearance.

Usage: publish-fold-plan.py [--scope <path>] [--watermark <ref>] [--published <ref>]
                            [--working <ref>]

Exit codes: 0 a plan was produced and PROVEN convergent, 1 nothing to plan (empty range) or a
residual divergence, 2 usage/precondition error.
Terminal verdict line: `RESULT: PASS rc=0 commits=<n> bricks=<n> folds=<n> undecided=<n>
dropped=<n> converges=yes`; on a residual divergence `RESULT: FAIL rc=1 … dropped=<n>
diverging=<n>`; on an empty range `RESULT: FAIL rc=1 commits=0 bricks=0 folds=0 undecided=0`.
"""

from __future__ import annotations

import argparse
import functools
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
    """Run git in `scope` and return its stdout, raising PlanError on failure.

    Every git call here decodes with `surrogateescape`: this file reads paths unquoted (`-z`,
    `core.quotePath=false`), so a name that is not valid UTF-8 arrives as raw bytes. Measured,
    strict decoding raised UnicodeDecodeError on a Latin-1 name — a traceback and no verdict
    line. Surrogate-escaped, the same string round-trips back into the next git call's argv.
    """
    proc = subprocess.run(
        ["git", "-C", scope, *args],
        capture_output=True,
        text=True,
        errors="surrogateescape",
        check=False,
    )
    if proc.returncode != 0:
        raise PlanError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def git_ok(scope: str, *args: str) -> bool:
    """True when the git command exits 0. For predicates, where failure is an answer."""
    return (
        subprocess.run(
            ["git", "-C", scope, *args],
            capture_output=True,
            text=True,
            errors="surrogateescape",
            check=False,
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

    `-c core.quotePath=false` on the `show` call: without it, git C-quotes a non-ASCII path in
    the `--- a/`/`+++ b/` headers (`"a/caf\\303\\251.md"`), and this function's `target[2:]`
    prefix-strip then drops the leading `"a` and keeps the rest, octal escapes and trailing
    quote included (`/caf\\303\\251.md"`) — the extracted path is garbage, though the SAME
    garbage on both sides of every diff for that file (git's quoting is deterministic), so it
    stays a stable dict key here and folding still works. What breaks is `published_lines()`,
    called with that same garbled key: `git show ref:<garbage>` cannot resolve a real blob, so
    it always reads empty — a commit that removes a still-published line in a non-ASCII-named
    file is misclassified as FOLD/UNDECIDED instead of OWN BRICK, which is the wrong direction
    to be wrong in. `quotePath=false` fixes the common
    case (accented and other non-ASCII bytes); it does NOT fix a path containing a literal quote,
    backslash, tab or newline — git C-quotes those unconditionally, `quotePath` or not — and this
    function does not attempt to un-quote that shape.
    """
    out = git(
        scope,
        "-c",
        "core.quotePath=false",
        "show",
        "--format=",
        "--unified=0",
        "--no-renames",
        "--no-color",
        sha,
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
        errors="surrogateescape",
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
                errors="surrogateescape",
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


@functools.lru_cache(maxsize=None)
def commit_paths(scope: str, sha: str) -> frozenset[str]:
    """The paths one commit touches — a brick's file set is the union over its members.

    Cached: `jumped_collisions` calls this once per (member, jumped-commit) pair per candidate
    brick, so a busy range calls it O(bricks × jumps) times over the SAME (scope, sha) pairs
    every prune-loop iteration. Read via `-z`, which git never quotes or escapes — unlike a
    plain `--name-only` listing (or the diff headers `diff_lines` parses), which C-quotes a
    non-ASCII path and was measured blind to it: on a fixture pairing this with `diverging_paths`
    (`ordering`'s shape, on a `café.md`-named file), the quoted path made `blob()` read `None` on
    BOTH sides of the comparison, and `None != None` is `False` — the fold was proposed and
    reported `converges=yes` while actually overwriting the file.
    """
    out = git(scope, "show", "--format=", "--name-only", "-z", "--no-renames", sha)
    return frozenset(p for p in out.split("\0") if p)


def blob(scope: str, ref: str, path: str) -> str | None:
    """The blob sha of `path` at `ref`, or None when it is not there."""
    proc = subprocess.run(
        ["git", "-C", scope, "rev-parse", f"{ref}:{path}"],
        capture_output=True,
        text=True,
        errors="surrogateescape",
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def brick_writers(scope: str, bricks: list[dict]) -> dict[str, str]:
    """Map path -> the endpoint sha that LAST writes it, applying bricks in plan order.

    A brick materialises its whole file set at its endpoint; walking bricks in order and
    letting the last write win follows `publish-brick.sh`'s application order. Not its file set
    exactly: for a RENAME the engine's publish mode lists only the new path, where this file's
    `--no-renames` lists both — so a `converges=yes` here does not vouch for a renamed path.
    """
    writer: dict[str, str] = {}
    for brick in bricks:
        endpoint = brick["members"][-1]
        for path in set().union(
            *(commit_paths(scope, sha) for sha in brick["members"])
        ):
            writer[path] = endpoint
    return writer


def diverging_paths(scope: str, bricks: list[dict], working: str) -> set[str]:
    """Paths the plan would NOT leave at the working tip's content.

    This is the publish path's own convergence predicate — no longer what PRUNES an unsafe
    fold (see `jumped_collisions`), but the plan's postcondition on everything that rule does
    not decide for it: a range that cannot converge as ordered (a merge, or any other
    non-linear history) still fails here, loudly, rather than shipping silently wrong.
    """
    writer = brick_writers(scope, bricks)
    return {
        path
        for path, endpoint in writer.items()
        if blob(scope, endpoint, path) != blob(scope, working, path)
    }


def jumped_commits(brick: dict, order: list[str]) -> list[str]:
    """Non-member commits strictly between a brick's first and last member, in range order.

    A brick sits at its first member's position but is applied at its last, so everything
    strictly between them is already baked into the last member's tree by the time the brick
    would publish — this is what the brick JUMPS OVER.
    """
    members = set(brick["members"])
    first_idx = order.index(brick["members"][0])
    last_idx = order.index(brick["members"][-1])
    return [sha for sha in order[first_idx + 1 : last_idx] if sha not in members]


def jumped_collisions(
    scope: str, brick: dict, order: list[str]
) -> dict[str, list[str]]:
    """{path: [shas]} for every jumped commit touching a path in the brick's own file set.

    Covers both measured shapes (module docstring): the one that leaves a path short of the tip,
    and the one `diverging_paths` can never see, where the final tree still matches the tip and
    only the subject the change shipped under is wrong.
    """
    union = set().union(*(commit_paths(scope, sha) for sha in brick["members"]))
    collisions: dict[str, list[str]] = {}
    for sha in jumped_commits(brick, order):
        for path in commit_paths(scope, sha) & union:
            collisions.setdefault(path, []).append(sha)
    return collisions


def prune_unsafe_folds(scope: str, results: list[dict]) -> list[str]:
    """Unfold every brick whose unit jumps over a commit sharing one of its paths.

    A fold is only ever a tidiness win, so the repair is to DROP it — never to reorder the
    bricks, which would assert one more composition claim nothing has checked. The culprit is
    the LAST-positioned multi-member brick with a collision; dropping strictly reduces the fold
    count each pass, so this terminates. `diverging_paths` is deliberately NOT consulted here —
    collision-free implies convergent for a linear range (module docstring), so once this loop
    finishes it finds nothing on a linear range; what it can still find — a range that cannot
    converge as ordered, such as one carrying a merge — is not repairable by dropping a fold,
    and is left to `report()`'s postcondition to FAIL.
    """
    order = [item["sha"] for item in results]
    dropped: list[str] = []
    while True:
        bricks = assemble(results)
        culprit = None
        collisions: dict[str, list[str]] = {}
        for brick in reversed(bricks):
            if len(brick["members"]) <= 1:
                continue
            found = jumped_collisions(scope, brick, order)
            if found:
                culprit, collisions = brick, found
                break
        if culprit is None:
            return dropped
        for sha in culprit["members"][1:]:
            item = next(entry for entry in results if entry["sha"] == sha)
            item["target"] = None
            item["verdict"] = "OWN BRICK"
            item["detail"] = (
                "its unit would have jumped over a commit changing one of its paths — the "
                "brick would publish that change early under its own subject, so the fold is "
                "dropped"
            )
            item["evidence"] = [
                f"collides on: {path} (changed by "
                f"{', '.join(jumped[:7] for jumped in shas)})"
                for path, shas in sorted(collisions.items())[:3]
            ]
            dropped.append(sha)


def report(
    scope: str, args: argparse.Namespace, results: list[dict], dropped: list[str]
) -> int:
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
            f"NOTE: {len(merges)} merge commit(s) in range went through the same classifier, "
            "which reads a merge's diff as an ordinary one, so their verdicts are unreliable — "
            "each needs a human call before running the plan"
        )
    print()

    for n, item in enumerate(results, 1):
        head = item["verdict"]
        if item["target"]:
            head = f"{head} {item['target'][:7]}"
        # Prefixed with `# ` so `parse_plan`'s existing comment-skip (it checks the
        # stripped line before shlex-splitting) covers this whole diagnostic block: any of
        # these three sites can embed arbitrary text — a commit subject, removed source, or
        # evidence — that happens to name `publish-brick.sh` and would otherwise misparse as
        # an invocation. Render-site prefixing, not per-producer, catches all three known
        # producers (`classify`'s still-published evidence, `classify`'s fold-target
        # evidence, and `prune_unsafe_folds`'s collision evidence) and any future one in one
        # place — named by function here rather than by line, which rots the moment any of
        # them moves.
        print(f"# [{n}] {item['sha'][:7]}  {item['subject']}")
        print(f"    # -> {head} — {item['detail']}")
        for line in item["evidence"]:
            print(f"       # {line}")

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
    if dropped:
        print(
            f"\n{len(dropped)} fold(s) were DROPPED because their unit would have jumped "
            "over a commit changing one of its paths:"
        )
        for sha in dropped:
            print(f"  {sha[:7]} stands alone")

    # A surviving multi-member brick can still jump over a commit that shares NO path with
    # it — nothing here can drop that (no path set sees it: docs landing one brick before
    # the tool they document is the measured case), so it is surfaced as a question for the
    # operator instead. `#`-prefixed, shas only, and the sentence below deliberately never
    # names publish-brick.sh, so parse_plan's real consumers cannot misread this block.
    order = [item["sha"] for item in results]
    jump_notes: list[str] = []
    for brick in bricks:
        if len(brick["members"]) <= 1:
            continue
        jumps = jumped_commits(brick, order)
        if not jumps:
            continue
        first, *others = brick["members"]
        tail = "".join(f" +{sha[:7]}" for sha in others)
        shas = " ".join(sha[:7] for sha in jumps)
        jump_notes.append(f"# {first[:7]}{tail} jumps over {len(jumps)}: {shas}")
    if jump_notes:
        print(
            "\nA surviving fold can still jump over a commit sharing none of its paths — "
            "ordering that no path set can see; the operator should judge it:"
        )
        for note in jump_notes:
            print(note)

    # Postcondition, not decoration: a PASS is offered only when this holds. A FAIL here
    # means the RANGE cannot converge as ordered — a merge, or any other non-linear history —
    # which is a fact about the range, not a bug in this file to silently repair by reordering.
    residual = diverging_paths(scope, bricks, args.working)
    if residual:
        print(
            f"\nRESULT: FAIL rc=1 commits={len(results)} bricks={len(bricks)} "
            f"folds={folds} undecided={undecided} dropped={len(dropped)} "
            f"diverging={len(residual)}"
        )
        writer = brick_writers(scope, bricks)
        for path in sorted(residual)[:5]:
            print(
                f"  would not converge: {path} (last written by brick {writer[path][:7]})",
                file=sys.stderr,
            )
        return 1

    if folds:
        residual_note = f" — {folds} multi-member unit(s) UNVALIDATED for per-brick validity until rehearsed"
    else:
        residual_note = (
            " — no multi-member unit exists, so intermediate over-reach is structurally"
            " impossible here"
        )
    print(
        f"\nRESULT: PASS rc=0 commits={len(results)} bricks={len(bricks)} "
        f"folds={folds} undecided={undecided} dropped={len(dropped)} converges=yes"
        f"{residual_note}"
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
    # A surrogate-escaped path (see `git`) cannot be encoded back out strictly, so printing one
    # in evidence would raise at the last step. `backslashreplace` prints the surrogate code
    # point instead (measured: `caf\udce9.txt` for the Latin-1 byte 0xE9), not the raw byte.
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.stderr.reconfigure(errors="backslashreplace")

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

    try:
        dropped = prune_unsafe_folds(scope, results)
    except PlanError as exc:
        print(f"cannot plan: {exc}", file=sys.stderr)
        return 2

    return report(scope, args, results, dropped)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
