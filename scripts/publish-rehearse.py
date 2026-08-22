#!/usr/bin/env python3
# Script: publish-rehearse.py
# Purpose: Predict, per brick, which /audit checks a fold plan would fail once actually applied
# Usage: publish-rehearse.py --plan <file> --scope <repo> --artifact-dir <dir> [--base <ref>]
"""Plan-time rehearsal of `publish-brick.sh`'s per-brick audit, over a plan nobody has applied yet.

**Why this exists.** `publish-fold-plan.py` proves a plan CONVERGES (the final tree is right);
it says nothing about whether any INTERMEDIATE brick is buildable — a brick can materialise onto
a tree that is internally inconsistent (an index naming a script not yet added, a lint config
tightened ahead of the files it would now reject) and `publish-brick.sh` would halt on it mid-run,
after earlier bricks already landed. This tool predicts that halt before the operator starts.

**The one property the whole tool hangs on: BASE-ANCHORED, not TIP-ANCHORED.** A fresh clone's
already-checked-out working tree is the scope's TIP. Grading a brick against the tip makes every
file outside that brick's own file set already final, so a forward reference that is genuinely
absent at that point in history can never be caught — the file exists at tip regardless of which
brick is nominally being graded. So this tool resets the working clone to `--base` FIRST, then
applies bricks 1..N CUMULATIVELY (`git checkout <endpoint> -- <files>`, the same primitive
`publish-brick.sh` uses), so brick N is graded on `base + bricks 1..N` — the exact tree the real
apply step would build and audit at that point, no more and no less.

**What "rehearsed" means here, precisely.** Four checks always run (sync-docs, md-links,
mutation-anchors, env-claims); ruff and markdownlint run only when some in-range commit touches a
lint config, because a fold can carry a stricter or looser config into a tree of not-yet-adjusted
files and that is a genuine new failure mode, not a per-file property. `pre-push-installed` is
NEVER invoked — it grades a historical tree against the LIVE installed hook and would fail on
every brick regardless of validity, so running it would manufacture false findings, not real ones.
Every check is resolved from the MATERIALISED clone's own copy, matching exactly what
`publish-brick.sh` would run at that tree (see its own header comment on "which copy of what").

**A brick whose constituents delete or rename a path is UNMEASURABLE, never guessed at** — a
`git checkout` cannot express either. Because materialisation is cumulative, an unmaterialisable
brick leaves the tree wrong for every brick after it too, so every later brick is reported
TAINTED rather than judged: presenting a verdict for a brick built on a wrong tree would be a
false finding, not a real one.

**This is a prediction, not a clearance.** It never mutates the plan, never touches `--scope`
(it clones into `--artifact-dir` and works only there), and its terminal line is never read as
"the plan is safe" — only as "these checks, at these bricks, on this machine, right now."

Exit codes: 0 clean rehearsal (PASS), 1 at least one brick genuinely failed a check, 2 nothing
was proven either way (a refused isolation guard, a plan/precondition error, or a rehearsal left
INDETERMINATE by an unmeasurable/tainted brick).

Terminal verdict line: `RESULT: <PASS|FAIL|INDETERMINATE> rc=<n> bricks=<n> failed=<n>
skipped=<n> unmeasurable=<n> tainted=<n>`. PASS only when failed=0 AND unmeasurable=0 AND
tainted=0 — anything unrehearsed is INDETERMINATE, never PASS.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

# Every checker subprocess gets this ceiling. Generous on purpose: the real risk here is a hang
# (a checker mis-invoked in a way that waits on stdin, say), not a slow-but-legitimate run, and an
# overrun must read as unmeasured rather than as a wrong verdict either way.
CHECK_TIMEOUT = 300

# (file, in-tree config) pairs, not per-file: a fold carrying one of these forward puts a
# different config into a tree of not-yet-adjusted files, which is a genuinely new failure mode.
# Settled from source (audit.sh's check_ruff/check_markdownlint), not preference.
LINT_CONFIG_FILES = {"ruff.toml", ".markdownlint-cli2.jsonc", "pyproject.toml"}

PRE_PUSH_EXCLUSION_NOTE = (
    "excluded: pre-push-installed — it grades a historical tree against the LIVE "
    "installed hook and would fail on every brick regardless of validity; never run here"
)


class RehearseError(Exception):
    """A precondition failed before any brick could be rehearsed."""


# ---------- reusing the driver's hardened plan parser, without touching it ----------


def load_publish_drive() -> types.ModuleType:
    """Load `publish-drive.py` by path, exactly as Task 1 specifies.

    Its `parse_plan` is hardened (shlex, no eval, refuses a line naming the engine that does not
    parse) and re-deriving it drops a different safety property every time — a measured defect
    class in this repo. It is loaded rather than imported as a package because it is not one, and
    loaded rather than extracted because extracting it would touch the exact file the weekend
    publish depends on. Safe to exec: the driver has an `if __name__ == "__main__"` guard and its
    top level defines only constants and functions.
    """
    path = Path(__file__).resolve().parent / "publish-drive.py"
    spec = importlib.util.spec_from_file_location("publish_drive", path)
    if spec is None or spec.loader is None:
        raise RehearseError(f"cannot load the plan parser from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- subprocess plumbing ----------


def run(cmd: list[str], cwd: str | None = None, input_text: str | None = None):
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=CHECK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            cmd, 124, "", f"TIMEOUT after {CHECK_TIMEOUT}s"
        )


def resolve_base_commit(clone_dir: Path, base: str) -> tuple[str | None, str]:
    """Resolve --base to a commit SHA INSIDE THE CLONE, robustly.

    A fresh `git clone` creates a LOCAL branch only for whatever the source repo's HEAD pointed
    to at clone time; every other branch is reachable solely as `origin/<name>`. `--base` is
    documented as "the published ref" and an operator will naturally pass a branch name like
    `main` — which then only resolves when the clone happens to have checked out `main` itself.
    Every rehearsal fixture commits directly on `main` without ever switching away, so this bug
    was invisible to the whole suite until a real clone of an adopted repo (checked out to a
    feature branch, not `main`) hit it: `git checkout --detach main` failed there with
    `fatal: '--detach' cannot be used with '-b/-B/--orphan'`, because git DWIM's the unqualified,
    branch-shaped name into "create a local branch named main" — illegal in combination with
    `--detach`. `git rev-parse main` fails identically; rev-parse does not apply the DWIM either.

    Tries the ref literally first (a raw SHA, a tag, or the one branch that IS local), then
    `origin/<base>` — the exact form a fresh clone produces for every OTHER branch. Returns
    (sha, which_form_resolved) on success, or (None, "<form1>, <form2>") naming both tried forms
    so a genuine failure never reads as a silent guess.
    """
    tried: list[str] = []
    for candidate in (base, f"origin/{base}"):
        tried.append(candidate)
        proc = run(
            [
                "git",
                "-C",
                str(clone_dir),
                "rev-parse",
                "--verify",
                f"{candidate}^{{commit}}",
            ]
        )
        sha = proc.stdout.strip()
        if proc.returncode == 0 and sha:
            return sha, candidate
    return None, ", ".join(tried)


# publish-fold-plan.py's own header line, e.g. "range:     a4cf596..dev  (9 commits)". The LEFT
# side is non-greedy up to the first ".." so it never swallows the separator.
RANGE_HEADER_RE = re.compile(
    r"^\s*range:\s*(\S+?)\.\.(\S+)\s+\(\d+\s+commits?\)", re.MULTILINE
)


def parse_range_header(plan_text: str) -> str | None:
    """Extract the LEFT side of `publish-fold-plan.py`'s own range header, if present.

    Derived from what the plan already asserted rather than a hand-typed copy that can silently
    diverge from it — the exact class of bug this closes: `--base main` computed a meaningless
    commit range because `main` and the plan's actual watermark (`refs/published/main`) are
    divergent histories in this repo after a recast, and nothing forced the two to agree.
    """
    m = RANGE_HEADER_RE.search(plan_text)
    return m.group(1) if m else None


def assert_base_is_ancestor(
    clone_dir: Path, base_sha: str, bricks: list[dict]
) -> tuple[bool, str]:
    """Every brick endpoint must descend from the resolved base, or the whole rehearsal is
    noise: a non-ancestor base computes a meaningless commit range for lint-config gating (and
    a meaningless starting tree for materialisation), producing PLAUSIBLE-LOOKING findings that
    are not measurements of anything. Checked against every brick, not just the first, so a
    plan mixing a good prefix with a bad tail still refuses rather than half-rehearsing.
    """
    for brick in bricks:
        endpoint = brick["endpoint"]
        proc = run(
            [
                "git",
                "-C",
                str(clone_dir),
                "merge-base",
                "--is-ancestor",
                base_sha,
                endpoint,
            ]
        )
        if proc.returncode != 0:
            return False, (
                f"base {base_sha} is not an ancestor of brick {brick['version']}'s endpoint "
                f"{endpoint} — these are unrelated histories, so no rehearsal is possible"
            )
    return True, ""


def _argparse_rejected(proc) -> bool:
    """True when a subprocess died in argument PARSING, not in its own logic.

    The rehearsal hardcodes a MODERN invocation against a possibly-HISTORICAL checker copy. An
    old copy lacking a flag this tool passes must read UNMEASURABLE, never FAIL — at apply time
    the contemporaneous audit.sh and checker co-evolved in one tree, so the real run would not
    have erred. argparse's own rejection is distinctive: `error: unrecognized arguments: ...` on
    exit 2, which no checker's OWN documented failure mode ever prints.
    """
    combined = ((proc.stdout or "") + (proc.stderr or "")).lower()
    return proc.returncode == 2 and "unrecognized arguments" in combined


# ---------- the four always-run cross-file checks, mirroring audit.sh's check_* exactly ----------


def check_sync_docs(clone: Path) -> tuple[str, str]:
    runner = clone / "skills" / "sync-docs" / "sync_docs.py"
    if not runner.is_file():
        return "SKIP", "runner not present"
    hits = run(["git", "-C", str(clone), "grep", "-l", "<!-- sync:", "--", "*.md"])
    if not hits.stdout.strip():
        return "SKIP", "no sync markers in scope"
    proc = run([sys.executable, str(runner), "--scope", str(clone), "sync", "--check"])
    if _argparse_rejected(proc):
        return "UNMEASURABLE", "the materialised checker rejected a modern flag"
    if proc.returncode != 0:
        return "FAIL", "sync-docs reported drift"
    return "PASS", ""


def check_md_links(clone: Path) -> tuple[str, str]:
    checker = clone / "scripts" / "md-links-check.py"
    if not checker.is_file():
        return "SKIP", "checker or python3 not found"
    files_proc = run(["git", "-C", str(clone), "ls-files", "--", "*.md"])
    files = [f for f in files_proc.stdout.splitlines() if f]
    bad: list[str] = []
    for f in files:
        payload = json.dumps({"tool_input": {"file_path": str(clone / f)}})
        proc = run([sys.executable, str(checker)], input_text=payload)
        if proc.returncode == 2:
            bad.append(f)
    if bad:
        return "FAIL", "broken relative link(s) or anchor(s): " + ", ".join(bad)
    return "PASS", ""


def check_mutation_anchors(clone: Path) -> tuple[str, str]:
    runner = clone / "scripts" / "mutation-anchors-check.py"
    if not runner.is_file():
        return "SKIP", "runner not present"
    files_proc = run(
        [
            "git",
            "-C",
            str(clone),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ]
    )
    campaigns = [
        f
        for f in files_proc.stdout.splitlines()
        if f and Path(f).name.startswith("mutate_") and f.endswith(".py")
    ]
    if not campaigns:
        return "SKIP", "no mutation campaigns in scope"
    proc = run([sys.executable, str(runner), "--scope", str(clone)])
    if _argparse_rejected(proc):
        return "UNMEASURABLE", "the materialised checker rejected a modern flag"
    if proc.returncode != 0:
        return (
            "FAIL",
            "a campaign anchor no longer resolves, or a campaign went unjudged",
        )
    return "PASS", ""


def check_env_claims(clone: Path) -> tuple[str, str]:
    checker = clone / "scripts" / "env-claims-check.py"
    if not checker.is_file():
        return "SKIP", "scope does not ship scripts/env-claims-check.py"
    proc = run([sys.executable, str(checker), "--scope", str(clone)])
    if _argparse_rejected(proc):
        return "UNMEASURABLE", "the materialised checker rejected a modern flag"
    if proc.returncode == 0:
        return "PASS", ""
    if proc.returncode == 3:
        return "SKIP", "the documented environment is not present on this machine"
    if proc.returncode == 2:
        return "FAIL", "unprovable: the checker could not reach a verdict"
    return "FAIL", "a documented environment claim no longer holds"


def run_cross_file_checks(clone: Path):
    for name, fn in (
        ("sync-docs", check_sync_docs),
        ("md-links", check_md_links),
        ("mutation-anchors", check_mutation_anchors),
        ("env-claims", check_env_claims),
    ):
        verdict, detail = fn(clone)
        yield name, verdict, detail


# ---------- ruff/markdownlint, only when a lint config was touched in range ----------


def lint_config_touched(clone: Path, base: str, endpoint: str) -> bool:
    proc = run(
        [
            "git",
            "-C",
            str(clone),
            "log",
            "--name-only",
            "--format=",
            f"{base}..{endpoint}",
        ]
    )
    if proc.returncode != 0:
        return False
    touched = {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}
    return bool(touched & LINT_CONFIG_FILES)


def check_ruff(clone: Path) -> tuple[str, str]:
    files_proc = run(["git", "-C", str(clone), "ls-files", "--", "*.py"])
    files = [f for f in files_proc.stdout.splitlines() if f]
    if not files:
        return "SKIP", "no python files"
    if shutil.which("ruff") is None:
        return "SKIP", "ruff not found"
    bad = False
    for f in files:
        if run(["ruff", "check", f], cwd=str(clone)).returncode != 0:
            bad = True
        if run(["ruff", "format", "--check", f], cwd=str(clone)).returncode != 0:
            bad = True
    if bad:
        return "FAIL", "ruff check/format reported findings"
    return "PASS", ""


def _find_markdownlint_cli2() -> str | None:
    found = shutil.which("markdownlint-cli2")
    if found:
        return found
    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if nvm_dir.is_dir():
        for v in sorted(
            (p.name for p in nvm_dir.iterdir() if p.is_dir()), reverse=True
        ):
            candidate = nvm_dir / v / "bin" / "markdownlint-cli2"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def check_markdownlint(clone: Path) -> tuple[str, str]:
    if not (clone / ".markdownlint-cli2.jsonc").is_file():
        return "SKIP", "repo not opted in"
    binary = _find_markdownlint_cli2()
    if not binary:
        return "SKIP", "markdownlint-cli2 not found"
    proc = run([binary, "**/*.md"], cwd=str(clone))
    if proc.returncode != 0:
        return "FAIL", "markdownlint-cli2 reported findings"
    return "PASS", ""


def run_lint_checks(clone: Path):
    for name, fn in (("ruff", check_ruff), ("markdownlint", check_markdownlint)):
        verdict, detail = fn(clone)
        yield name, verdict, detail


# ---------- isolation guard ----------


def assert_scope_isolated(
    drive_mod: types.ModuleType, scope: Path, artifact_dir: Path
) -> None:
    """Refuse BEFORE any git operation runs if --artifact-dir would collide with --scope.

    Reuses the driver's own `resolve()` so both sides are resolved the SAME way — a comparison
    whose sides resolve differently can never fire (measured, in shipped code, on this exact
    hazard: one side took `pwd`, the other `cd -P`).
    """
    scope_r = drive_mod.resolve(scope)
    artifact_r = drive_mod.resolve(artifact_dir)
    if scope_r == artifact_r or scope_r in artifact_r.parents:
        raise RehearseError(
            f"--artifact-dir {artifact_r} resolves onto or inside --scope {scope_r}; a "
            "rehearsal must be unable to modify --scope, so it refuses in isolation before any "
            "git operation touches anything"
        )


# ---------- main rehearsal loop ----------


def rehearse(
    plan_bricks: list[dict], clone_dir: Path, base: str
) -> tuple[dict, list[str]]:
    """Materialise and grade each brick cumulatively.

    Returns (tally, brick_lines) rather than printing directly: the terminal RESULT line is
    computed from the tally and must be emitted BEFORE these per-brick blocks, never after. A
    trailing RESULT/disclaimer is otherwise swept into the LAST brick's own block by any
    consumer that slices output from a brick's first mention through end-of-output — read as
    "FAIL" leaking into an otherwise-clean final brick's report. Printing the verdict first and
    the evidence after keeps the two apart with no marker games.
    """
    lines: list[str] = []

    def emit(line: str = "") -> None:
        lines.append(line)

    failed_bricks = 0
    unmeasurable_bricks = 0
    tainted_bricks = 0
    skipped_checks = 0
    tainted = False

    for brick in plan_bricks:
        version = brick["version"]
        endpoint = brick["endpoint"]
        subject = brick["subject"]
        emit(f"===== brick {version} <- {endpoint} {subject!r} =====")

        if tainted:
            tainted_bricks += 1
            emit(
                "  TAINTED: an earlier brick could not be materialised, and materialisation is "
                "cumulative — this tree is not what the real apply would produce; "
                "unrehearsable until the plan is repaired"
            )
            emit("  brick verdict: TAINTED")
            emit("")
            continue

        # The effective constituent set: the plan's own folded constituents plus the endpoint,
        # deduplicated with the endpoint last if not already present — the same combination
        # publish-brick.sh's assert_applicable performs.
        effective: list[str] = []
        seen: set[str] = set()
        for c in list(brick["constituents"]) + [endpoint]:
            if c not in seen:
                seen.add(c)
                effective.append(c)

        bad_rows: list[tuple[str, str, list[str]]] = []
        touched: set[str] = set()
        inspect_failed = False
        for c in effective:
            proc = run(
                ["git", "-C", str(clone_dir), "show", "--name-status", "--format=", c]
            )
            if proc.returncode != 0:
                emit(
                    f"  UNMEASURABLE: constituent {c} could not be inspected: {proc.stderr.strip()}"
                )
                inspect_failed = True
                continue
            for line in proc.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                status, paths = parts[0], parts[1:]
                if status in ("A", "M", "T"):
                    touched.update(paths)
                else:
                    bad_rows.append((c, status, paths))

        if inspect_failed or bad_rows:
            for c, status, paths in bad_rows:
                emit(
                    f"  UNMEASURABLE: constituent {c} {status} {' -> '.join(paths)} — a "
                    "checkout cannot express a delete, rename or copy"
                )
            unmeasurable_bricks += 1
            tainted = True
            emit("  brick verdict: UNMEASURABLE")
            emit("")
            continue

        files = sorted(touched)
        if not files:
            emit("  UNMEASURABLE: the brick has an empty file set")
            unmeasurable_bricks += 1
            tainted = True
            emit("  brick verdict: UNMEASURABLE")
            emit("")
            continue

        checkout_proc = run(
            ["git", "-C", str(clone_dir), "checkout", endpoint, "--", *files]
        )
        if checkout_proc.returncode != 0:
            emit(
                f"  UNMEASURABLE: could not materialise from {endpoint}: "
                f"{checkout_proc.stderr.strip()}"
            )
            unmeasurable_bricks += 1
            tainted = True
            emit("  brick verdict: UNMEASURABLE")
            emit("")
            continue

        emit(f"  files: {len(files)}")

        brick_failed = False
        brick_check_unmeasurable = False

        for name, verdict, detail in run_cross_file_checks(clone_dir):
            line = f"  {name}: {verdict}"
            if detail:
                line += f" — {detail}"
            emit(line)
            if verdict == "FAIL":
                brick_failed = True
            elif verdict == "SKIP":
                skipped_checks += 1
            elif verdict == "UNMEASURABLE":
                brick_check_unmeasurable = True

        if lint_config_touched(clone_dir, base, endpoint):
            for name, verdict, detail in run_lint_checks(clone_dir):
                line = f"  {name}: {verdict}"
                if detail:
                    line += f" — {detail}"
                emit(line)
                if verdict == "FAIL":
                    brick_failed = True
                elif verdict == "SKIP":
                    skipped_checks += 1
                elif verdict == "UNMEASURABLE":
                    brick_check_unmeasurable = True

        if brick_failed:
            failed_bricks += 1
            emit("  brick verdict: FAIL")
        elif brick_check_unmeasurable:
            # A single check that could not be run (an old checker rejecting a modern flag) is
            # NOT the cumulative-materialisation defect above and does not taint later bricks —
            # each later brick still materialises from its own, independent checkout — but the
            # brick itself was not fully proven either, so it cannot read as a clean PASS.
            unmeasurable_bricks += 1
            emit("  brick verdict: UNMEASURABLE")
        else:
            emit("  brick verdict: PASS")
        emit("")

    tally = {
        "bricks": len(plan_bricks),
        "failed": failed_bricks,
        "skipped": skipped_checks,
        "unmeasurable": unmeasurable_bricks,
        "tainted": tainted_bricks,
    }
    return tally, lines


def write_artifact(artifact_dir: Path, lines: list[str], rc: int) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "rehearsal-report.txt"
    # The real exit status is recorded INSIDE the artifact, so its absence there is itself the
    # signal that the run died — an external announcement of the exit code cannot be trusted.
    text = "\n".join(lines) + f"\nREHEARSAL_EXIT_STATUS={rc}\n"
    artifact_path.write_text(text)
    return artifact_path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rehearse a fold plan brick-by-brick on a throwaway clone, predicting which "
            "/audit checks publish-brick.sh would fail at each brick. A prediction, never a "
            "clearance — it does not replace the apply-time audit."
        )
    )
    parser.add_argument("--plan", required=True, help="the fold plan file to rehearse")
    parser.add_argument("--scope", required=True, help="the published repo (read-only)")
    parser.add_argument(
        "--base",
        default=None,
        help=(
            "the published ref the plan builds on; optional — derived from the plan's own "
            "'range: <base>..<working>' header when omitted"
        ),
    )
    parser.add_argument(
        "--artifact-dir",
        required=True,
        dest="artifact_dir",
        help="scratch directory OUTSIDE --scope for the working clone and report",
    )
    args = parser.parse_args(argv)

    scope = Path(args.scope)
    artifact_dir = Path(args.artifact_dir)

    try:
        drive_mod = load_publish_drive()
        # Isolation FIRST, before the plan is even read: the guard's whole job is to refuse
        # before any git operation runs, and reading the plan is harmless but resolving/cloning
        # scope is not.
        assert_scope_isolated(drive_mod, scope, artifact_dir)
    except RehearseError as exc:
        print(f"cannot rehearse: {exc}")
        print(f"cannot rehearse: {exc}", file=sys.stderr)
        return 2

    out_lines: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        out_lines.append(line)

    try:
        plan_text = Path(args.plan).read_text()
    except OSError as exc:
        emit(f"cannot rehearse: cannot read --plan {args.plan}: {exc}")
        emit("RESULT: ERROR rc=2 bricks=0 failed=0 skipped=0 unmeasurable=0 tainted=0")
        write_artifact(artifact_dir, out_lines, 2)
        return 2

    try:
        bricks = drive_mod.parse_plan(plan_text)
    except drive_mod.DriveError as exc:
        emit(f"cannot rehearse: {exc}")
        emit("RESULT: ERROR rc=2 bricks=0 failed=0 skipped=0 unmeasurable=0 tainted=0")
        write_artifact(artifact_dir, out_lines, 2)
        return 2

    # DERIVE the base from the plan's own range header rather than making the operator retype
    # it: a hand-typed copy can silently diverge from what the plan was actually built against
    # (measured: --base main computed a meaningless range in a recast repo, because main and
    # the plan's true watermark are divergent histories). A declared FLOOR pairs the
    # derivation, since discovery cannot detect absence: when NEITHER a header NOR --base is
    # available, this refuses rather than falling back to a guess like main or HEAD.
    header_base = parse_range_header(plan_text)
    if args.base is None and header_base is None:
        emit(
            "cannot rehearse: no --base was supplied and the plan has no parseable range "
            "header (range: base..working) - refusing rather than guessing "
            "(never defaults to main or HEAD)"
        )
        emit("RESULT: ERROR rc=2 bricks=0 failed=0 skipped=0 unmeasurable=0 tainted=0")
        write_artifact(artifact_dir, out_lines, 2)
        return 2

    scope_r = drive_mod.resolve(scope)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_r = drive_mod.resolve(artifact_dir)
    clone_dir = artifact_r / "clone"
    if clone_dir.exists():
        shutil.rmtree(clone_dir)

    emit(
        "publish-rehearse.py — plan-time per-brick rehearsal (a PREDICTION, never a clearance)"
    )
    emit(f"scope:    {scope_r} (read-only; never modified by this run)")
    emit(f"clone:    {clone_dir}")
    emit(f"{PRE_PUSH_EXCLUSION_NOTE}")
    emit(f"bricks:   {len(bricks)} from the plan, in order")
    emit("")

    clone_proc = run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(scope_r), str(clone_dir)]
    )
    if clone_proc.returncode != 0:
        emit(f"cannot rehearse: could not clone --scope: {clone_proc.stderr.strip()}")
        emit("RESULT: ERROR rc=2 bricks=0 failed=0 skipped=0 unmeasurable=0 tainted=0")
        write_artifact(artifact_dir, out_lines, 2)
        return 2

    # Resolve whichever candidate(s) are in play. When BOTH --base and the header are present,
    # both are resolved to actual commit SHAs (never compared as raw text - a short SHA and its
    # full form name the same commit and must not read as a false disagreement) before being
    # compared; silently preferring one over the other is exactly the failure this closes.
    resolved_arg = resolved_hdr = None
    tried_arg = tried_hdr = ""
    if args.base is not None:
        resolved_arg, tried_arg = resolve_base_commit(clone_dir, args.base)
    if header_base is not None:
        resolved_hdr, tried_hdr = resolve_base_commit(clone_dir, header_base)

    if args.base is not None and header_base is not None:
        if resolved_arg is None:
            emit(
                f"cannot rehearse: --base {args.base} does not resolve to a commit inside "
                f"the clone (tried: {tried_arg})"
            )
            emit(
                "RESULT: ERROR rc=2 bricks=0 failed=0 skipped=0 unmeasurable=0 tainted=0"
            )
            write_artifact(artifact_dir, out_lines, 2)
            return 2
        if resolved_hdr is not None and resolved_arg != resolved_hdr:
            emit(
                f"cannot rehearse: --base {args.base} (resolved {resolved_arg}) disagrees "
                f"with the plan's own range header base {header_base} (resolved "
                f"{resolved_hdr}) - refusing rather than silently preferring one"
            )
            emit(
                "RESULT: ERROR rc=2 bricks=0 failed=0 skipped=0 unmeasurable=0 tainted=0"
            )
            write_artifact(artifact_dir, out_lines, 2)
            return 2
        resolved_base = resolved_arg
        base_source = f"--base {args.base}"
    elif args.base is not None:
        if resolved_arg is None:
            emit(
                f"cannot rehearse: --base {args.base} does not resolve to a commit inside "
                f"the clone (tried: {tried_arg}) - a fresh clone only keeps ONE branch "
                "local, so a branch-shaped --base usually needs the origin/<name> form, "
                "tried automatically above and still not found"
            )
            emit(
                "RESULT: ERROR rc=2 bricks=0 failed=0 skipped=0 unmeasurable=0 tainted=0"
            )
            write_artifact(artifact_dir, out_lines, 2)
            return 2
        resolved_base = resolved_arg
        base_source = f"--base {args.base}"
    else:
        if resolved_hdr is None:
            emit(
                f"cannot rehearse: the plan header names base {header_base!r} but it does "
                f"not resolve to a commit inside the clone (tried: {tried_hdr})"
            )
            emit(
                "RESULT: ERROR rc=2 bricks=0 failed=0 skipped=0 unmeasurable=0 tainted=0"
            )
            write_artifact(artifact_dir, out_lines, 2)
            return 2
        resolved_base = resolved_hdr
        base_source = f"plan header (range: {header_base}..)"

    # Echo the parameter actually used, and how it was obtained - the cheapest defence against
    # the class of error this whole fix closes (a true verdict about a base nobody chose).
    emit(f"base:     {resolved_base}  (source: {base_source})")
    emit("")

    # REFUSE a base that is not an ancestor of every brick endpoint. A non-ancestor base makes
    # the commit range meaningless (both for the lint-config gate and for materialisation), so
    # any per-brick verdict computed from it would be a plausible-looking finding about nothing.
    ok, why = assert_base_is_ancestor(clone_dir, resolved_base, bricks)
    if not ok:
        emit(f"cannot rehearse: {why}")
        emit("RESULT: ERROR rc=2 bricks=0 failed=0 skipped=0 unmeasurable=0 tainted=0")
        write_artifact(artifact_dir, out_lines, 2)
        return 2

    reset_proc = run(
        ["git", "-C", str(clone_dir), "checkout", "--quiet", "--detach", resolved_base]
    )
    if reset_proc.returncode != 0:
        emit(
            f"cannot rehearse: could not reset the clone to base {resolved_base} "
            f"(source: {base_source}): {reset_proc.stderr.strip()}"
        )
        emit("RESULT: ERROR rc=2 bricks=0 failed=0 skipped=0 unmeasurable=0 tainted=0")
        write_artifact(artifact_dir, out_lines, 2)
        return 2

    tally, brick_lines = rehearse(bricks, clone_dir, resolved_base)

    if tally["failed"] > 0:
        status, rc = "FAIL", 1
    elif tally["unmeasurable"] > 0 or tally["tainted"] > 0:
        status, rc = "INDETERMINATE", 2
    else:
        status, rc = "PASS", 0

    # Evidence first, then the disclaimer, then the verdict LAST. This repo's terminal-verdict
    # convention is explicit and shared by every sibling tool — `publish-brick.sh:53`, "as its
    # LAST line of stdout" — and callers are told throughout to read the last line and treat any
    # other value, or its absence, as not-clean. Emitting the verdict FIRST would leave every
    # such caller reading a per-brick evidence line and finding no verdict at all.
    #
    # An earlier build did emit it first, to stop a run-wide trailer being swept into the LAST
    # brick's block by a consumer that slices output by brick. That is a real hazard, but the
    # fix belongs in the SLICER — bound the final block at the verdict line — not in the
    # emitter, because relocating the verdict trades a slicing bug for a convention break that
    # every downstream reader inherits silently.
    emit("---- per-brick evidence ----")
    emit("")
    for line in brick_lines:
        emit(line)
    emit("")
    emit(
        "This rehearsal is a PREDICTION of what publish-brick.sh's audit would find at each "
        "brick on THIS machine, right now — it is not a clearance and does not replace the "
        "apply-time audit."
    )
    emit(
        f"RESULT: {status} rc={rc} bricks={tally['bricks']} failed={tally['failed']} "
        f"skipped={tally['skipped']} unmeasurable={tally['unmeasurable']} "
        f"tainted={tally['tainted']}"
    )

    write_artifact(artifact_dir, out_lines, rc)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
