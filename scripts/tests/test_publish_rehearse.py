"""Tests for scripts/publish-rehearse.py — plan-time per-brick rehearsal (Task 3 of
plans/2026-08-21-fold-intermediate-validity.md).

WRITTEN BEFORE THE TOOL EXISTS. Every row here must be RED right now, and structured so a
correct implementation turns it GREEN for the reason the row names, not by accident.

The tool's whole failure mode is reading as validated while proving nothing (see
specs/2026-08-21-fold-intermediate-validity.md, D2-D3). So these fixtures build REAL throwaway
git repos and run the REAL checkers the rehearsal is specified to invoke (this repo's own
`skills/sync-docs/sync_docs.py` and its sibling modules), rather than a fake stand-in — a fake
checker can only prove the rehearsal calls something, never that it reaches the actual defect
class (per-brick, cross-file drift) the tool exists to catch.

Row 1 is THE regression test for the design's confirmed BLOCKER: a rehearsal anchored on a
fresh clone's already-checked-out working tree (the scope's TIP) can never fail the one
confirmed historical case, because the forward-referenced file exists at tip regardless of
which brick is nominally being graded. Its fixture shape is load-bearing — see that test's own
docstring before touching it.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "scripts" / "publish-rehearse.py"
SYNC_DOCS_LIB = REPO_ROOT / "skills" / "sync-docs"
SYNC_DOCS_FILES = [
    "sync_docs.py",
    "handlers.py",
    "extractors.py",
    "formatters.py",
    "markers.py",
]

GIT_IDENTITY = [
    "-c",
    "user.email=t@t.invalid",
    "-c",
    "user.name=t",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
]

README_MARKER = "# Scripts\n\n<!-- sync:scripts -->\n<!-- /sync:scripts -->\n"

# The four cross-file checks Task 2 always runs, by name — used both to assert they DID run
# (rows 2 and 6) and, via their ABSENCE from an enumerated line, that pre-push-installed did
# not (row 6).
CROSS_FILE_CHECKS = ["sync-docs", "md-links", "mutation-anchors", "env-claims"]

# The checker scripts beyond sync-docs, copied verbatim from this repo so a fixture that needs
# them to genuinely RUN (not SKIP for want of the file) uses the real thing.
REAL_CHECKER_FILES = [
    "md-links-check.py",
    "mutation-anchors-check.py",
    "env-claims-check.py",
]

# A minimal, self-contained excerpt of THIS repo's own CLAUDE.md carrying every anchor
# `env-claims-check.py`'s hardcoded claim table checks, and NOTHING else — no other doc links
# (which would fail md-links against files a minimal scope does not ship) and no unrelated
# backticked absolute paths (which would read as UNACCOUNTED, since a minimal scope has no
# `skills/` tree to exempt them). Verified by hand: `env-claims-check.py --scope <scope>` on a
# scope carrying exactly this text reports `RESULT: PASS rc=0 claims=14 verified=14 stale=0
# unaccounted=0` on this machine, which is the real one these claims describe.
MINIMAL_ENV_CLAIMS_CLAUDE_MD = """## Environment

- **Platform**: macOS with MacPorts package manager
- **Editor**: BBEdit (primary code editor)
- **Shell**: Prefer MacPorts bash (`/opt/local/bin/bash`) for scripts requiring advanced features
- **Default bash**: `/bin/bash` is the system bash (version 3.x, limited features)
- **GNU Core Utilities**: Installed via MacPorts (`coreutils`)
  - **`/opt/local/libexec/gnubin` is already on PATH, so the UNPREFIXED names are GNU** — plain
    `date`, `grep`, `sed`, `ls` are GNU, not BSD. Measured: BSD-only flags fail there
    (`date -j` → `invalid option -- 'j'`), and reaching for `gdate` to "get GNU" is a no-op.
  - The `g`-prefixed names (`gls`, `ggrep`, `gdate`) still resolve, so both spellings work
  - Use GNU versions for advanced features like `--long-options`

## Language and Tooling Preferences

- Location: `/opt/local/bin/`, `/opt/local/lib/`

## macOS Notes

### Bash Versions

| Version | Location | Use Case |
|---------|----------|----------|
| 3.x | `/bin/bash` | System/POSIX scripts |
| 5.x | `/opt/local/bin/bash` | Modern scripts (associative arrays, `[[`, etc.) |

### GNU vs BSD Tools

macOS ships BSD tools by default, but **this machine already prepends
`/opt/local/libexec/gnubin`**, so an unprefixed `grep`/`sed`/`date`/`ls` is the GNU one. The table
is what each side offers, not what you get by default here:

| Tool | BSD | GNU | Key Difference |
|------|-----|-----|----------------|
| grep | `/usr/bin/grep` | `ggrep` | `-P` (Perl regex) |
| sed | `/usr/bin/sed` | `gsed` | Extended features |
| date | `/bin/date` | `gdate` | Better parsing |
| ls | `/bin/ls` | `gls` | `--color`, `--group-directories-first` |

Already in effect here: `export PATH="/opt/local/libexec/gnubin:$PATH"` — verify with
`which date` rather than assuming either way, since a shell that lacks it silently gives you BSD.
"""

# Matches a per-brick ENUMERATED verdict line -- "<check-name>: PASS" and its siblings -- so
# row 6's negative assertion (pre-push-installed must never be GRADED) can search the WHOLE
# transcript for the check-shaped pattern specifically, rather than trusting brick_blocks()'s
# per-brick text slicing. brick_blocks() attributes everything from a brick's last mention to
# the END of output to that brick's block, so a trailing global disclaimer ("pre-push-installed
# excluded because...") would be swallowed into the LAST brick's block and wrongly read as
# living inside its enumeration. Matching the VERDICT SHAPE sidesteps that: a prose exclusion
# note is not formatted as "pre-push-installed: PASS", so it cannot false-trip this pattern.
ENUMERATED_LINE_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9-]*)\s*[:\-]\s*(PASS|FAIL|SKIP|UNMEASURABLE)\b"
)


def enumerated_check_names(text: str) -> set[str]:
    """Every check name that appears in a `<name>: <VERDICT>` shaped line, lowercased."""
    return {m.group(1).lower() for m in ENUMERATED_LINE_RE.finditer(text)}


# ---------- low-level git/fixture helpers ----------


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _purge_pycache(repo: Path) -> None:
    """Remove bytecode caches `run_sync()` leaves behind importing its sibling modules.

    Without this, `git add -A` stages `skills/sync-docs/__pycache__/*.pyc` as real content —
    polluting a fixture's own file-set self-checks (`touched_paths`) with noise unrelated to
    what the test is deliberately committing.
    """
    for cache_dir in repo.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)


def commit_all(repo: Path, message: str) -> str:
    _purge_pycache(repo)
    _git(repo, "add", "-A")
    _git(repo, *GIT_IDENTITY, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def touched_paths(repo: Path, sha: str) -> list[str]:
    """The file set a single commit touches — used to self-check fixtures, not the tool."""
    out = _git(repo, "show", "--name-only", "--format=", sha).stdout
    return sorted(ln for ln in out.split("\n") if ln)


def copy_sync_docs(repo: Path) -> None:
    dest = repo / "skills" / "sync-docs"
    dest.mkdir(parents=True, exist_ok=True)
    for name in SYNC_DOCS_FILES:
        shutil.copy(SYNC_DOCS_LIB / name, dest / name)


def copy_all_checkers(repo: Path) -> None:
    """sync-docs plus the three OTHER cross-file checkers, so a fixture can make all four
    genuinely RUN (not SKIP for want of the file) — needed wherever a test asserts a check's
    verdict is present and non-SKIP, not merely that its name is mentioned somewhere."""
    copy_sync_docs(repo)
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    for name in REAL_CHECKER_FILES:
        shutil.copy(REPO_ROOT / "scripts" / name, repo / "scripts" / name)


def write_mutation_campaign(repo: Path) -> None:
    """The smallest campaign `mutation-anchors-check.py` will grade as PASS: one row whose
    `old` anchor occurs exactly once in its subject. Verified by hand against the real checker
    before use (`RESULT: PASS rc=0 campaigns=1 rows=1 bad=0 untracked=0`).
    """
    write(
        repo / "scripts" / "dummy_subject.py",
        "def ok():\n    return True\n",
    )
    write(
        repo / "scripts" / "tests" / "mutate_dummy.py",
        '"""Trivial campaign so mutation-anchors-check.py has something to grade cleanly."""\n'
        "REPO = None\n"
        'SUBJECT = REPO / "scripts" / "dummy_subject.py"\n'
        "MUTATIONS = [\n"
        '    Mutation("flip the return value", "return True", "return False"),\n'
        "]\n",
    )


def run_sync(repo: Path, check: bool = False) -> subprocess.CompletedProcess:
    """Regenerate (or --check) scope's own sync-docs markers — the REAL tool, not a fake, so
    the drift a fixture manufactures is the same drift `publish-rehearse.py` will detect."""
    args = [
        sys.executable,
        str(repo / "skills" / "sync-docs" / "sync_docs.py"),
        "--scope",
        str(repo),
        "sync",
    ]
    if check:
        args.append("--check")
    return subprocess.run(args, capture_output=True, text=True)


def digest_tree(path: Path) -> str:
    """Content digest of every tracked-or-not file under path, excluding VCS/cache noise.

    Walks the filesystem rather than `git`, so it catches a stray UNTRACKED file too — the
    same reasoning as the CLAUDE.md restore-guarantee hazard: a tool that snapshots only what
    it tracked is not the same as one that snapshots what is actually there.
    """
    skip = {".git", "__pycache__", ".pytest_cache"}
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        if skip & set(f.relative_to(path).parts):
            continue
        h.update(str(f.relative_to(path)).encode())
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def refs_snapshot(repo: Path) -> str:
    """Every ref plus HEAD — a working-tree digest alone cannot see a stray tag or branch."""
    refs = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout
    head = _git(repo, "rev-parse", "HEAD").stdout
    return refs + "HEAD=" + head


def plan_file(path: Path, *rows: tuple[str, str, str]) -> Path:
    """rows are (version, endpoint_sha, subject) — the exact shape publish-fold-plan.py emits
    and publish-drive.py's parser consumes for an UNFOLDED (single-commit) brick: no trailing
    constituent shas, since the endpoint itself is the sole member."""
    lines = ["proposed bricks — run in order:"]
    for version, endpoint, subject in rows:
        escaped = subject.replace("'", "'\\''")
        lines.append(f"  publish-brick.sh {version} {endpoint} '{escaped}'")
    path.write_text("\n".join(lines) + "\n")
    return path


def run_tool(
    plan: Path, scope: Path, base: str, artifact_dir: Path, *extra: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--plan",
            str(plan),
            "--scope",
            str(scope),
            "--base",
            base,
            "--artifact-dir",
            str(artifact_dir),
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def brick_blocks(stdout: str, versions: list[str]) -> dict[str, str]:
    """Split stdout into one text block per version, each running from that version's first
    mention to the next version's first mention (or end of output for the last).

    Confining assertions to a brick's own block is what keeps a check like "does 'ruff' appear
    for this brick" from being satisfied by unrelated static text (a header, a disclaimer)
    printed once for the whole run.
    """
    lines = stdout.split("\n")
    starts: dict[str, int] = {}
    for v in versions:
        for i, ln in enumerate(lines):
            if v in ln:
                starts[v] = i
                break
    assert len(starts) == len(versions), (
        f"not every version was mentioned in the output: wanted {versions}, "
        f"found {sorted(starts)}\n{stdout}"
    )
    ordered = sorted(starts.items(), key=lambda kv: kv[1])
    # The LAST brick's block ends at the terminal verdict line, not at end-of-output. Without
    # this bound, the run-wide trailer (the `RESULT:` line and the exclusion disclaimer) is swept
    # into whichever brick happens to be last, so a clean final brick can read as carrying a FAIL
    # that belongs to the run as a whole. Measured: that is exactly what happened, and the first
    # implementation "fixed" it by emitting `RESULT:` FIRST — which silently breaks this repo's
    # terminal-verdict convention (`publish-brick.sh:53`: "as its LAST line of stdout"), so every
    # caller reading the last line would have read a per-brick evidence line instead. Bounding
    # here makes each block STRICTER, and lets the verdict stay where the convention requires.
    trailer = next(
        (i for i, ln in enumerate(lines) if ln.lstrip().startswith("RESULT:")),
        len(lines),
    )
    blocks: dict[str, str] = {}
    for idx, (v, start) in enumerate(ordered):
        end = ordered[idx + 1][1] if idx + 1 < len(ordered) else len(lines)
        if idx + 1 == len(ordered):
            end = min(end, trailer) if trailer > start else end
        blocks[v] = "\n".join(lines[start:end])
    return blocks


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Physically resolved temp root. A symlinked $TMPDIR yields a LOGICAL path that can send
    a path-resolving tool down a different branch than the one a real invocation would take —
    the exact hazard row 3a below deliberately exploits on purpose, so the harness itself must
    not trip it by accident."""
    return tmp_path.resolve()


# ---------- shared scope builders ----------


def build_clean_two_brick_scope(
    root: Path, label: str
) -> tuple[Path, str, list[tuple[str, str, str]]]:
    """Two bricks, each adding one script and re-syncing the index. Self-consistent at every
    step: sync-docs never drifts.

    Only sync-docs is wired for real here (md-links, mutation-anchors and env-claims have no
    checker file in this scope, so a correct rehearsal SKIPs them). That is enough for row 3b
    (a safety net, not a discriminator — see its own docstring) but NOT enough for a row that
    must prove all four cross-file checks genuinely RAN; those use
    `build_fully_checked_two_brick_scope` below instead.
    """
    scope = root / f"{label}-scope"
    init_repo(scope)
    copy_sync_docs(scope)
    write(scope / "scripts" / "README.md", README_MARKER)
    base = commit_all(scope, "chore: base (no scripts yet)")

    write(
        scope / "scripts" / "foo.sh",
        "#!/usr/bin/env bash\n# Purpose: does nothing\necho hi\n",
    )
    run_sync(scope)
    a = commit_all(scope, "feat: add foo.sh")

    write(scope / "scripts" / "bar.py", '"""Does something else."""\n')
    run_sync(scope)
    b = commit_all(scope, "feat: add bar.py")

    rows = [
        ("v0.1.0", a, "feat: add foo.sh"),
        ("v0.2.0", b, "feat: add bar.py"),
    ]
    return scope, base, rows


def build_fully_checked_two_brick_scope(
    root: Path, label: str
) -> tuple[Path, str, list[tuple[str, str, str]]]:
    """Two bricks, each adding one script, where all FOUR cross-file checks genuinely RUN and
    PASS at every brick — not just sync-docs. Used by rows 2 and 6, which (per the coordinator's
    strengthening) must assert a check's verdict is actually present and non-SKIP, not merely
    that its name appears somewhere in the transcript.

    Verified by hand before use: reset-to-base then cumulative checkout of each brick's file
    set reports sync-docs/md-links/mutation-anchors/env-claims all PASS at both v0.1.0 and
    v0.2.0 on this machine.
    """
    scope = root / f"{label}-scope"
    init_repo(scope)
    copy_all_checkers(scope)
    write(scope / "CLAUDE.md", MINIMAL_ENV_CLAIMS_CLAUDE_MD)
    write_mutation_campaign(scope)
    write(scope / "scripts" / "README.md", README_MARKER)
    run_sync(scope)
    base = commit_all(
        scope, "chore: base (checkers, CLAUDE.md, mutation campaign, no scripts)"
    )

    write(
        scope / "scripts" / "foo.sh",
        "#!/usr/bin/env bash\n# Purpose: does nothing\necho hi\n",
    )
    run_sync(scope)
    a = commit_all(scope, "feat: add foo.sh")
    assert touched_paths(scope, a) == ["scripts/README.md", "scripts/foo.sh"]

    write(scope / "scripts" / "bar.py", '"""Does something else."""\n')
    run_sync(scope)
    b = commit_all(scope, "feat: add bar.py")
    assert touched_paths(scope, b) == ["scripts/README.md", "scripts/bar.py"]

    rows = [
        ("v0.1.0", a, "feat: add foo.sh"),
        ("v0.2.0", b, "feat: add bar.py"),
    ]
    return scope, base, rows


# ---------- Row 1 — the base-anchoring regression test ----------


def test_row1_base_anchored_regression_forward_reference_present_at_tip(
    root: Path,
) -> None:
    """THE regression test for the design's confirmed BLOCKER.

    Fixture shape (mandatory, see the plan's Task 3 row 1): the forward-referenced path is
    ABSENT at --base, ADDED by a LATER in-range commit, and PRESENT at scope tip. A fixture
    where the path never exists anywhere would pass under BOTH a correct base-anchored
    rehearsal and a broken tip-anchored one — it cannot distinguish them, so it is worthless.
    Verified by hand against the real sync-docs tool before writing this test: resetting to
    --base and materialising only brick v0.1.0's file set (scripts/README.md) leaves
    scripts/prose-diff.py absent from disk, and `sync --check` there reports drift (rc=1) —
    while at scope TIP (both commits applied) the same check is clean (rc=0). A tip-anchored
    implementation works in the fresh clone's already-checked-out tip state, where
    scripts/prose-diff.py is already physically present for every brick regardless of which
    file set was nominally checked out — so it would report v0.1.0 PASS, and this whole row
    would read clean. This row must go GREEN only on a BASE-anchored implementation (reset to
    --base FIRST, then apply bricks 1..N cumulatively).
    """
    scope = root / "r1-scope"
    init_repo(scope)
    copy_sync_docs(scope)
    write(
        scope / "scripts" / "foo.sh",
        "#!/usr/bin/env bash\n# Purpose: does nothing\necho hi\n",
    )
    write(scope / "scripts" / "README.md", README_MARKER)
    run_sync(scope)
    base = commit_all(scope, "chore: base (foo.sh only, index consistent)")

    # Simulate the historical shape: sync-docs was run once with prose-diff.py PHYSICALLY
    # present, producing the index row — then that row lands in a commit whose own file set
    # never adds the script (the file is deleted from disk again before this commit is made,
    # so `git add -A` only ever sees scripts/README.md as changed).
    write(
        scope / "scripts" / "prose-diff.py",
        '"""Verify a restructuring is lossless."""\n',
    )
    run_sync(scope)
    (scope / "scripts" / "prose-diff.py").unlink()
    a = commit_all(scope, "docs: index prose-diff.py ahead of adding it")
    assert touched_paths(scope, a) == ["scripts/README.md"], (
        "fixture bug, not a tool bug: commit A must touch ONLY the index, never the script"
    )

    # A LATER in-range commit finally adds the file the index already named.
    write(
        scope / "scripts" / "prose-diff.py",
        '"""Verify a restructuring is lossless."""\n',
    )
    b = commit_all(scope, "feat: add prose-diff.py")
    assert touched_paths(scope, b) == ["scripts/prose-diff.py"]

    # PRESENT AT SCOPE TIP — mandatory; see the docstring above for why.
    assert (scope / "scripts" / "prose-diff.py").exists()

    plan = plan_file(
        root / "r1-plan.txt",
        ("v0.1.0", a, "docs: index prose-diff.py ahead of adding it"),
        ("v0.2.0", b, "feat: add prose-diff.py"),
    )
    proc = run_tool(plan, scope, base, root / "r1-artifacts")

    assert re.search(
        r"RESULT: FAIL rc=\d+ bricks=2 failed=1 skipped=\d+ unmeasurable=0 tainted=0",
        proc.stdout,
    ), (
        "brick v0.1.0 must FAIL sync-docs; a tip-anchored rehearsal reports this whole plan "
        "clean, which is exactly the defect this row exists to catch:\n"
        + proc.stdout
        + proc.stderr
    )

    blocks = brick_blocks(proc.stdout, ["v0.1.0", "v0.2.0"])
    assert "FAIL" in blocks["v0.1.0"], (
        f"v0.1.0 must be NAMED as the failing brick, not merely counted:\n{blocks['v0.1.0']}"
    )
    assert "sync-docs" in proc.stdout.lower(), (
        "the report must name WHICH check failed, not just aggregate a count:\n"
        + proc.stdout
    )
    assert "PASS" in blocks["v0.2.0"] and "FAIL" not in blocks["v0.2.0"], (
        f"v0.2.0's own file set adds the script; it must read clean once it lands:\n"
        f"{blocks['v0.2.0']}"
    )


# ---------- Row 2 — a clean plan is a real PASS, not a vacuous one ----------


def test_row2_a_clean_plan_is_PASS_with_a_nonzero_brick_count(root: Path) -> None:
    """A discovery matching nothing reports success loudest of all.

    STRENGTHENED after a real control run: the ORIGINAL version of this row asserted only the
    terminal RESULT line plus the substring "sync-docs" appearing ANYWHERE in stdout. A
    rubber-stamp stub that prints one fake per-brick line (naming only v0.1.0, never v0.2.0)
    and a hardcoded RESULT line satisfied it completely. Per the coordinator's amendment to
    Task 2, the tool must ENUMERATE, per brick, every check it ran and its individual verdict.
    This now requires that enumeration for BOTH bricks (`brick_blocks` fails outright if either
    version is never mentioned — which alone catches the one-line stub above) and requires each
    of the four cross-file checks to show a verdict OTHER than SKIP within EACH brick's own
    block. A PASS whose checks all skipped is a brick rehearsed in name only.

    RESIDUAL, stated honestly: a sufficiently motivated stub can still print a correctly-shaped
    fake enumeration for every brick, claiming PASS for all four checks without having run
    anything — this row cannot distinguish that from the real thing on transcript text alone
    (confirmed by running exactly such a stub; see this session's report). What a fake-PASS
    stub CANNOT do is also satisfy row 1, which requires the tool to correctly report a brick
    as FAILING on a fixture with genuine drift — so the suite's power to catch "always says
    PASS" comes from row 1, not from this row alone. This row's own job is narrower and still
    real: a genuine implementation that quietly SKIPS everything (a broken checker-resolution
    path, say) and reports PASS by the `failed=0/unmeasurable=0/tainted=0` allowlist alone is
    caught here.
    """
    scope, base, rows = build_fully_checked_two_brick_scope(root, "r2")
    plan = plan_file(root / "r2-plan.txt", *rows)
    proc = run_tool(plan, scope, base, root / "r2-artifacts")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert re.search(
        r"RESULT: PASS rc=0 bricks=2 failed=0 skipped=\d+ unmeasurable=0 tainted=0",
        proc.stdout,
    ), proc.stdout

    blocks = brick_blocks(proc.stdout, [v for v, _, _ in rows])
    for version, block in blocks.items():
        lower = block.lower()
        for check in CROSS_FILE_CHECKS:
            matches = [ln for ln in lower.split("\n") if check in ln]
            assert matches, (
                f"{version}'s own enumeration never names {check!r} — a brick where a check "
                f"went unenumerated has been rehearsed in name only:\n{block}"
            )
            assert not any("skip" in ln for ln in matches), (
                f"{check} must actually RUN (not SKIP) for a clean plan's {version} to mean "
                f"anything real:\n{block}"
            )


# ---------- Row 3 — scope isolation, two rows because one is vacuous ----------


def test_row3a_isolation_guard_fires_before_any_git_operation(root: Path) -> None:
    """The guard must FIRE, and before touching --scope at all.

    Without this row, a digest-only test (row 3b) passes forever whether the guard exists or
    is deleted, because a run that never attacks the scope path never exercises it. This
    arranges --artifact-dir to RESOLVE to the same real path as --scope via a symlink — the
    same containment hazard CLAUDE.md's verification-hazards section names: a guard comparing
    unresolved strings, or resolving only one side, would miss a symlink that makes two
    textually different paths the same real path.
    """
    scope = root / "r3a-scope"
    init_repo(scope)
    write(scope / "f.txt", "x\n")
    commit_all(scope, "chore: init")
    before_head = _git(scope, "rev-parse", "HEAD").stdout.strip()
    before_status = _git(scope, "status", "--porcelain").stdout
    before_refs = refs_snapshot(scope)

    plan = plan_file(root / "r3a-plan.txt", ("v0.1.0", before_head, "chore: init"))
    artifact_link = root / "r3a-artifact-link"
    artifact_link.symlink_to(scope, target_is_directory=True)

    proc = run_tool(plan, scope, before_head, artifact_link)

    assert proc.returncode != 0, (
        "the isolation guard must refuse, not proceed, when --artifact-dir resolves to "
        "--scope:\n" + proc.stdout + proc.stderr
    )
    assert "RESULT: PASS" not in proc.stdout, proc.stdout
    combined = (proc.stdout + proc.stderr).lower()
    assert "scope" in combined and (
        "isolat" in combined or "artifact-dir" in combined or "insid" in combined
    ), f"the refusal must explain itself:\n{combined}"

    # Load-bearing: nothing in --scope moved. A guard that fires only AFTER a clone/reset
    # already started would leave a footprint here even while still "refusing" at the end.
    assert _git(scope, "rev-parse", "HEAD").stdout.strip() == before_head
    assert _git(scope, "status", "--porcelain").stdout == before_status
    assert refs_snapshot(scope) == before_refs


def test_row3b_scope_files_and_refs_are_byte_identical_after_a_normal_run(
    root: Path,
) -> None:
    """Digest scope's FILES *and* REFS before/after a normal, successful run.

    HONEST LIMITATION, confirmed by a real control run: this row is a safety net against
    INCIDENTAL mutation by a real implementation, not a discriminator against a do-nothing
    stub — a tool that touches nothing cannot fail a "did anything change" check, so a
    rubber-stamp stub that never opens --scope at all passes this row trivially. That is
    ACCEPTABLE only because row 3a carries the discriminating power for isolation: it arranges
    for the guard to have to FIRE (via a symlink making --artifact-dir resolve to --scope) and
    asserts refusal happens before any git operation. Row 3a is deleted or broken, this row
    gives no warning either way — it is not a substitute for 3a, only a companion to it.

    A stray tag or branch is the realistic mutation channel for a git-driven tool, and a
    working-tree-only digest cannot see it — so this checks `git for-each-ref` plus HEAD in
    addition to file content, on a plan that actually runs (unlike row 3a, which never lets
    the tool reach git at all).
    """
    scope, base, rows = build_clean_two_brick_scope(root, "r3b")
    artifact_dir = root / "r3b-artifacts"

    before_files = digest_tree(scope)
    before_refs = refs_snapshot(scope)

    plan = plan_file(root / "r3b-plan.txt", *rows)
    proc = run_tool(plan, scope, base, artifact_dir)

    after_files = digest_tree(scope)
    after_refs = refs_snapshot(scope)

    assert "RESULT:" in proc.stdout, (
        "sanity: the run must have actually reached a verdict, or a same-digest no-op that "
        "never touched scope proves nothing:\n" + proc.stdout + proc.stderr
    )
    assert after_files == before_files, "scope's files changed during rehearsal"
    assert after_refs == before_refs, (
        "scope's refs changed during rehearsal — a stray tag or branch is invisible to a "
        "working-tree-only digest, which is exactly why refs are checked separately"
    )


# ---------- Row 4 — a delete is MEASURED and does not taint the following brick ----------


def test_row4_a_delete_is_measured_and_does_not_taint_the_following_brick(
    root: Path,
) -> None:
    """INVERTED (plan Task 4): a `D`-status constituent used to be refused as UNMEASURABLE,
    which tainted every later brick. The fixture (delete todelete.txt, then a later brick adds
    other.txt) is UNCHANGED — only the expectations are. The inversion moves TWO bricks:
    v0.1.0 (the delete itself) goes from UNMEASURABLE to MEASURED, and v0.2.0 (the innocent
    follower) goes from TAINTED to UNTAINTED — the taint was the row's original point, so both
    are asserted explicitly rather than just the terminal RESULT line.
    """
    scope = root / "r4-scope"
    init_repo(scope)
    write(scope / "scripts" / "todelete.txt", "content\n")
    write(scope / "scripts" / "keep.txt", "keep\n")
    base = commit_all(scope, "chore: base")

    _git(scope, "rm", "-q", "scripts/todelete.txt")
    a = commit_all(scope, "chore: remove todelete.txt")
    assert touched_paths(scope, a) == ["scripts/todelete.txt"]

    write(scope / "scripts" / "other.txt", "other\n")
    b = commit_all(scope, "chore: add other.txt")
    assert touched_paths(scope, b) == ["scripts/other.txt"]

    plan = plan_file(
        root / "r4-plan.txt",
        ("v0.1.0", a, "chore: remove todelete.txt"),
        ("v0.2.0", b, "chore: add other.txt"),
    )
    proc = run_tool(plan, scope, base, root / "r4-artifacts")

    assert "Traceback" not in (proc.stdout + proc.stderr), (
        "a delete must be MEASURED, never crash the rehearsal:\n"
        + proc.stdout
        + proc.stderr
    )

    blocks = brick_blocks(proc.stdout, ["v0.1.0", "v0.2.0"])
    assert "UNMEASURABLE" not in blocks["v0.1.0"], (
        "a checkout expressing a delete via `git rm` is now MEASURABLE — it must not read "
        f"UNMEASURABLE:\n{blocks['v0.1.0']}"
    )
    assert "PASS" in blocks["v0.1.0"], blocks["v0.1.0"]
    assert "TAINTED" not in blocks["v0.2.0"], (
        "the delete is applied, not refused, so materialisation is no longer wrong for a "
        f"later brick — v0.2.0 must not read TAINTED:\n{blocks['v0.2.0']}"
    )
    assert "PASS" in blocks["v0.2.0"], (
        f"the innocent follower must present as a clean finding:\n{blocks['v0.2.0']}"
    )
    assert re.search(r"unmeasurable=0\b", proc.stdout), proc.stdout
    assert re.search(r"tainted=0\b", proc.stdout), proc.stdout
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert re.search(
        r"RESULT: PASS rc=0 bricks=2 failed=0 skipped=\d+ unmeasurable=0 tainted=0",
        proc.stdout,
    ), proc.stdout


# ---------- Row 5 — two independent failing bricks, both reported (no halt) ----------


def test_row5_two_failing_bricks_are_both_reported(root: Path) -> None:
    """Pins the no-halt behaviour: brick 3 must be REACHED and reported PASS, proving the run
    did not stop at brick 1's (or brick 2's) failure. A halting driver would leave v0.3.0
    entirely unmentioned rather than reporting it clean.
    """
    scope = root / "r5-scope"
    init_repo(scope)
    copy_sync_docs(scope)
    write(scope / "scripts" / "README.md", README_MARKER)
    base = commit_all(scope, "chore: base")

    # The index ends up referencing BOTH forward-referenced scripts before either is added.
    write(scope / "scripts" / "one.py", '"""One."""\n')
    write(scope / "scripts" / "two.py", '"""Two."""\n')
    run_sync(scope)
    (scope / "scripts" / "one.py").unlink()
    (scope / "scripts" / "two.py").unlink()
    a = commit_all(scope, "docs: index one.py and two.py ahead of adding them")
    assert touched_paths(scope, a) == ["scripts/README.md"]

    write(scope / "scripts" / "one.py", '"""One."""\n')
    b = commit_all(scope, "feat: add one.py")  # two.py is still missing -> still drifts
    assert touched_paths(scope, b) == ["scripts/one.py"]

    write(scope / "scripts" / "two.py", '"""Two."""\n')
    c = commit_all(scope, "feat: add two.py")  # now consistent
    assert touched_paths(scope, c) == ["scripts/two.py"]

    plan = plan_file(
        root / "r5-plan.txt",
        ("v0.1.0", a, "docs: index one.py and two.py ahead of adding them"),
        ("v0.2.0", b, "feat: add one.py"),
        ("v0.3.0", c, "feat: add two.py"),
    )
    proc = run_tool(plan, scope, base, root / "r5-artifacts")

    blocks = brick_blocks(proc.stdout, ["v0.1.0", "v0.2.0", "v0.3.0"])
    assert "FAIL" in blocks["v0.1.0"], blocks["v0.1.0"]
    assert "FAIL" in blocks["v0.2.0"], (
        "two.py is STILL missing at v0.2.0 — a second, independent drift, not a repeat "
        f"report of the first:\n{blocks['v0.2.0']}"
    )
    assert "PASS" in blocks["v0.3.0"] and "FAIL" not in blocks["v0.3.0"], blocks[
        "v0.3.0"
    ]
    assert re.search(r"bricks=3\b", proc.stdout), proc.stdout
    assert re.search(r"failed=2\b", proc.stdout), proc.stdout


# ---------- Row 6 — pre-push-installed never runs, and the exclusion is named ----------


def test_row6_pre_push_installed_never_runs_and_is_named_as_excluded(
    root: Path,
) -> None:
    """STRENGTHENED after a real control run: the ORIGINAL version of this row only checked
    that a line containing "pre-push-installed" also contained an exclusion-shaped word — a
    rubber-stamp stub satisfied it by printing one static disclaimer sentence with no per-brick
    enumeration behind it at all. Per the coordinator: absence from an EMPTY or MISSING
    enumeration is not evidence of anything (a discovery matching nothing reports success
    loudest of all), so this now requires the POSITIVE half first — a real, non-empty,
    per-brick enumeration naming all four cross-file checks — before the negative half
    (pre-push-installed absent from it) can mean anything.

    The negative half is checked GLOBALLY via `enumerated_check_names()`, not by scoping into
    `brick_blocks()`: that per-brick slicer attributes everything from the LAST brick's first
    mention to the end of output to that brick's block, so a trailing global disclaimer would
    be swept into it and wrongly read as living inside the enumeration. Matching the VERDICT
    SHAPE (`pre-push-installed: PASS/FAIL/SKIP/...`) instead of a bare substring sidesteps
    that: a prose exclusion note is not formatted that way, so it cannot false-trip this check
    even when it happens to trail the last brick's own report.
    """
    scope, base, rows = build_fully_checked_two_brick_scope(root, "r6")
    plan = plan_file(root / "r6-plan.txt", *rows)
    proc = run_tool(plan, scope, base, root / "r6-artifacts")

    blocks = brick_blocks(proc.stdout, [v for v, _, _ in rows])
    for version, block in blocks.items():
        lower = block.lower()
        for check in CROSS_FILE_CHECKS:
            assert check in lower, (
                f"{version}'s enumeration never names {check!r} — an empty or missing "
                f"enumeration cannot make pre-push-installed's absence mean anything:\n{block}"
            )

    graded = enumerated_check_names(proc.stdout)
    assert "pre-push-installed" not in graded, (
        "pre-push-installed grades a historical tree against the LIVE installed hook and "
        f"fails on every brick regardless of validity — it must never be GRADED with a "
        f"verdict:\n{proc.stdout}"
    )

    # Weaker, secondary clause, kept but no longer load-bearing: the exclusion should still be
    # EXPLAINED somewhere in the transcript, not merely absent.
    lines_with_it = [
        ln for ln in proc.stdout.split("\n") if "pre-push-installed" in ln.lower()
    ]
    assert lines_with_it, (
        "an unstated exclusion reads as coverage — the report must NAME pre-push-installed "
        "somewhere, not merely never mention it:\n" + proc.stdout
    )
    joined = " ".join(lines_with_it).lower()
    assert any(w in joined for w in ("exclud", "never", "not run", "skip")), (
        f"the exclusion must be EXPLAINED, not just implied:\n{lines_with_it}"
    )


# ---------- Row 7 — ruff/markdownlint gated on the range touching lint config ----------


def build_single_brick_scope(
    root: Path, label: str, touch_lint_config: bool
) -> tuple[Path, str, str]:
    scope = root / f"{label}-scope"
    init_repo(scope)
    write(scope / "ruff.toml", "line-length = 100\n")
    write(scope / "pyproject.toml", "[tool.x]\n")
    write(scope / ".markdownlint-cli2.jsonc", "{}\n")
    write(scope / "unrelated.txt", "x\n")
    base = commit_all(scope, "chore: base")

    if touch_lint_config:
        write(scope / "ruff.toml", "line-length = 88\n")
        a = commit_all(scope, "chore: tighten ruff.toml")
    else:
        write(scope / "unrelated.txt", "y\n")
        a = commit_all(scope, "chore: touch something unrelated")

    return scope, base, a


def test_row7_ruff_and_markdownlint_are_gated_on_the_commit_range_touching_config(
    root: Path,
) -> None:
    """A plan whose range touches a lint config -> ruff/markdownlint ARE run; one whose range
    does not -> they are not. Pins the config-coupling rule rather than asserting it in prose.

    VACUITY NOTE: a stub that always prints a fixed disclaimer mentioning "ruff" and
    "markdownlint" somewhere in its output (e.g. the residual/exclusion boilerplate) would
    pass a whole-output substring check on the touching scenario, and — if that disclaimer is
    unconditional — WOULD ALSO WRONGLY PASS the non-touching scenario's negative assertion
    only if the disclaimer happened to omit those words there too. `brick_blocks()` narrows
    the search to each brick's own reported section specifically to make that harder to fake
    by accident; it cannot rule out a stub engineered to specifically defeat this row.
    """
    touch_scope, touch_base, touch_a = build_single_brick_scope(root, "r7-touch", True)
    plan_touch = plan_file(
        root / "r7-touch-plan.txt", ("v0.1.0", touch_a, "chore: tighten ruff.toml")
    )
    proc_touch = run_tool(
        plan_touch, touch_scope, touch_base, root / "r7-touch-artifacts"
    )
    block_touch = brick_blocks(proc_touch.stdout, ["v0.1.0"])["v0.1.0"].lower()
    assert "ruff" in block_touch, proc_touch.stdout + proc_touch.stderr
    assert "markdownlint" in block_touch, proc_touch.stdout + proc_touch.stderr

    clean_scope, clean_base, clean_a = build_single_brick_scope(root, "r7-clean", False)
    plan_clean = plan_file(
        root / "r7-clean-plan.txt",
        ("v0.1.0", clean_a, "chore: touch something unrelated"),
    )
    proc_clean = run_tool(
        plan_clean, clean_scope, clean_base, root / "r7-clean-artifacts"
    )
    block_clean = brick_blocks(proc_clean.stdout, ["v0.1.0"])["v0.1.0"].lower()
    assert "ruff" not in block_clean, (
        "no in-range commit touched ruff.toml/.markdownlint-cli2.jsonc/pyproject.toml — ruff "
        "must not even be ATTEMPTED for this brick:\n"
        + proc_clean.stdout
        + proc_clean.stderr
    )
    assert "markdownlint" not in block_clean, proc_clean.stdout + proc_clean.stderr


# ---------- Row 8 — a lone delete-only brick is MEASURED, not INDETERMINATE ----------


def test_row8_a_lone_delete_only_brick_is_measured_not_indeterminate(
    root: Path,
) -> None:
    """INVERTED (plan Task 4). Isolated from row 4 on purpose: a single brick, deleting a
    path, with no LATER brick to taint — this used to be the row proving an UNMEASURABLE
    brick with nothing else to fail reports the third value, INDETERMINATE, rather than PASS
    or FAIL. Now that a delete is measurable, the SAME fixture (delete-only brick, nothing
    after it) must report a clean PASS instead: there is no longer anything left unrehearsed.
    """
    scope = root / "r8-scope"
    init_repo(scope)
    write(scope / "scripts" / "todelete.txt", "content\n")
    base = commit_all(scope, "chore: base")
    _git(scope, "rm", "-q", "scripts/todelete.txt")
    a = commit_all(scope, "chore: remove todelete.txt")

    plan = plan_file(root / "r8-plan.txt", ("v0.1.0", a, "chore: remove todelete.txt"))
    proc = run_tool(plan, scope, base, root / "r8-artifacts")

    assert "RESULT: PASS" in proc.stdout, (
        "a delete-only brick is now MEASURABLE — with nothing left unrehearsed, this must "
        f"report a clean PASS, not INDETERMINATE:\n{proc.stdout}{proc.stderr}"
    )
    assert "RESULT: INDETERMINATE" not in proc.stdout, proc.stdout
    assert "RESULT: FAIL" not in proc.stdout, proc.stdout
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------- Row 9 — --base must resolve even when it is not the clone's local branch ----------


def test_row9_base_resolves_when_it_is_not_the_clones_local_branch(root: Path) -> None:
    """Regression test for a real bug a manual run hit that no OTHER fixture in this file can
    reach: a fresh clone only creates a LOCAL branch for whatever the SOURCE repo's HEAD pointed
    to at clone time. Every branch is otherwise available solely as `origin/<name>`.

    Every other row inits with `init.defaultBranch=main` and commits directly on `main` without
    ever switching away, so in every OTHER fixture `main` IS the clone's checked-out local
    branch — the tool's `git checkout --detach <base>` happens to work there by construction,
    not because it resolves `--base` correctly. Against a real clone of an adopted repo whose
    checked-out branch is a feature branch (not `main`), the identical command failed:
    `fatal: '--detach' cannot be used with '-b/-B/--orphan'` — git DWIM's the unqualified,
    branch-shaped name `main` into "create a local branch named main", which `--detach` refuses.

    This fixture reproduces that shape directly: commits land on `main`, then the SOURCE repo's
    HEAD is moved to a DIFFERENT branch before the tool ever clones it, so in the resulting
    clone `main` exists only as `origin/main` — exactly the real failure's precondition.
    """
    scope = root / "r9-scope"
    init_repo(scope)
    copy_sync_docs(scope)
    write(scope / "scripts" / "README.md", README_MARKER)
    commit_all(scope, "chore: base (no scripts yet)")

    write(
        scope / "scripts" / "foo.sh",
        "#!/usr/bin/env bash\n# Purpose: does nothing\necho hi\n",
    )
    run_sync(scope)
    a = commit_all(scope, "feat: add foo.sh")

    # Move the SOURCE repo's HEAD off main and onto a different branch. The clone this tool
    # makes will then have THAT branch as its sole local branch, with main reachable only as
    # origin/main — the precondition the real bug needed and no other fixture creates.
    _git(scope, "checkout", "-q", "-b", "other-branch")

    plan = plan_file(root / "r9-plan.txt", ("v0.1.0", a, "feat: add foo.sh"))
    proc = run_tool(plan, scope, "main", root / "r9-artifacts")

    assert proc.returncode == 0, (
        "--base main must resolve via origin/main inside the clone; a nonzero exit here means "
        "the reset step failed before any brick was ever reached:\n"
        + proc.stdout
        + proc.stderr
    )
    assert re.search(
        r"RESULT: PASS rc=0 bricks=1 failed=0 skipped=\d+ unmeasurable=0 tainted=0",
        proc.stdout,
    ), (
        "bricks=0 (or an ERROR line) means --base was never resolved and nothing was ever "
        "rehearsed, which is exactly what the bug report showed:\n"
        + proc.stdout
        + proc.stderr
    )


# ---------- Row 10 -- --base is DERIVED from the plan's own range header ----------


def run_tool_without_base(
    plan: Path, scope: Path, artifact_dir: Path, *extra: str
) -> subprocess.CompletedProcess:
    """Like run_tool(), but omits --base entirely, so the tool must derive it (or refuse)."""
    return subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--plan",
            str(plan),
            "--scope",
            str(scope),
            "--artifact-dir",
            str(artifact_dir),
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def plan_file_with_range_header(
    path: Path, base: str, working: str, *rows: tuple[str, str, str]
) -> Path:
    """A NEW builder, not a modification of plan_file() above: prepends
    publish-fold-plan.py's own header line (range: <base>..<working> (<n> commits)) so a test
    can exercise HEADER-derived base resolution without touching any existing row's plan
    shape."""
    lines = [
        f"range:     {base}..{working}  ({len(rows)} commits)",
        "",
        "proposed bricks -- run in order:",
    ]
    for version, endpoint, subject in rows:
        escaped = subject.replace("'", "'\\''")
        lines.append(f"  publish-brick.sh {version} {endpoint} '{escaped}'")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_row10_base_is_derived_from_the_plans_own_range_header(root: Path) -> None:
    """--base is optional. When omitted, the base is DERIVED from the plan's own range header
    line, exactly as publish-fold-plan.py emits it -- never a hand-typed copy that could
    silently diverge from what the plan was actually built against. That divergence is the
    real bug this whole change closes: a plain --base main computed a meaningless range in a
    recast repo, because main and the plan's true watermark are unrelated histories.
    """
    scope, base, rows = build_fully_checked_two_brick_scope(root, "r10")
    plan = plan_file_with_range_header(root / "r10-plan.txt", base, "dev", *rows)
    proc = run_tool_without_base(plan, scope, root / "r10-artifacts")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert re.search(
        r"RESULT: PASS rc=0 bricks=2 failed=0 skipped=\d+ unmeasurable=0 tainted=0",
        proc.stdout,
    ), (
        "omitting --base must derive it from the plan's own range header rather than "
        "refusing or guessing:\n" + proc.stdout + proc.stderr
    )
    assert "plan header" in proc.stdout.lower(), (
        "the tool must echo that the base came from the plan header, not --base, as the "
        "cheapest defence against a true verdict about a base nobody chose:\n"
        + proc.stdout
    )


# ---------- Row 11 -- a non-ancestor base is REFUSED, not silently rehearsed ----------


def test_row11_a_non_ancestor_base_is_refused_not_silently_rehearsed(
    root: Path,
) -> None:
    """The important row: built so the non-ancestor base would, if NOT refused, produce a
    PLAUSIBLE-LOOKING FAIL -- a row where the wrong base happens to produce a clean result
    cannot tell whether the refusal fired.

    An UNRELATED orphan commit stands in for --base. It carries an extra script
    (scripts/bogus.sh) the real branch's sync-docs index never mentions. A checkout --detach
    to any commit succeeds regardless of ancestry, so without the refusal this tool would
    materialise from the orphan commit, leave scripts/bogus.sh sitting in the clone through
    the brick's own checkout (its file set never touches that path), and sync-docs would
    report real drift -- a genuine-looking FAIL about a base nobody chose. The correct
    behaviour is a REFUSAL before any brick is graded at all, never that FAIL.
    """
    scope = root / "r11-scope"
    init_repo(scope)
    copy_sync_docs(scope)
    write(scope / "scripts" / "README.md", README_MARKER)
    commit_all(scope, "chore: real base (no scripts yet)")

    write(
        scope / "scripts" / "foo.sh",
        "#!/usr/bin/env bash\n# Purpose: does nothing\necho hi\n",
    )
    run_sync(scope)
    a = commit_all(scope, "feat: add foo.sh")

    _git(scope, "checkout", "-q", "--orphan", "unrelated-branch")
    _git(scope, "rm", "-rf", "-q", ".")
    # The sync-docs checker itself must survive in this unrelated history too, or it reads as
    # SKIP ("runner not present") once materialisation starts from here -- which would make
    # this row vacuous: a SKIP is not the plausible-looking FAIL the row exists to demonstrate.
    copy_sync_docs(scope)
    write(scope / "scripts" / "bogus.sh", "#!/usr/bin/env bash\necho bogus\n")
    _git(scope, "add", "-A")
    _git(scope, *GIT_IDENTITY, "commit", "-q", "-m", "chore: unrelated history")
    bogus_base = _git(scope, "rev-parse", "HEAD").stdout.strip()

    not_ancestor = _git(
        scope, "merge-base", "--is-ancestor", bogus_base, a, check=False
    )
    assert not_ancestor.returncode != 0, (
        "fixture bug, not a tool bug: bogus_base must NOT be an ancestor of a"
    )

    plan = plan_file(root / "r11-plan.txt", ("v0.1.0", a, "feat: add foo.sh"))
    proc = run_tool(plan, scope, bogus_base, root / "r11-artifacts")

    assert proc.returncode == 2, (
        "a non-ancestor --base must be REFUSED with rc=2, not silently rehearsed:\n"
        + proc.stdout
        + proc.stderr
    )
    assert "RESULT: ERROR rc=2" in proc.stdout, proc.stdout
    assert "RESULT: PASS" not in proc.stdout, proc.stdout
    assert "RESULT: FAIL" not in proc.stdout, (
        "this is the load-bearing assertion: the wrong base would otherwise produce a "
        "plausible-looking sync-docs FAIL (scripts/bogus.sh left over) that reads as a real "
        "finding rather than as noise about a base nobody chose:\n" + proc.stdout
    )
    assert "===== brick" not in proc.stdout, (
        "a refused base must never reach per-brick evidence at all:\n" + proc.stdout
    )
    combined = proc.stdout.lower()
    assert "not an ancestor" in combined or "unrelated" in combined, (
        f"the refusal must explain itself:\n{proc.stdout}"
    )


# ---------- Row 12 -- no header and no --base refuses, never defaults ----------


def test_row12_no_base_and_no_header_refuses_rather_than_defaulting(root: Path) -> None:
    """The declared FLOOR pairing the derivation in row 10: discovery cannot detect absence,
    so when NEITHER --base NOR a parseable plan header is available, this must refuse --
    never silently fall back to a guess like main or HEAD.
    """
    scope, base, rows = build_clean_two_brick_scope(root, "r12")
    plan = plan_file(root / "r12-plan.txt", *rows)
    proc = run_tool_without_base(plan, scope, root / "r12-artifacts")

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "RESULT: ERROR rc=2" in proc.stdout, proc.stdout
    assert "RESULT: PASS" not in proc.stdout, proc.stdout
    combined = (proc.stdout + proc.stderr).lower()
    assert "--base" in combined and "header" in combined, (
        "the refusal must explain that NEITHER --base nor a range header was available:\n"
        + combined
    )


# ---------- Row 13 — cross-brick modify-then-delete needs `git rm -f` (plan Task 4) ----------


def test_row13_cross_brick_modify_then_delete_needs_git_rm_dash_f(root: Path) -> None:
    """NEW (plan Task 4), not an inversion of an existing row. Mirrors the real range's shape:
    `16f3b69` modifies `agents/security-reviewer.md`, `8504a2c` deletes it, and the planner
    puts them in SEPARATE bricks — exactly the publish this whole change exists to unblock.

    The rehearsal's clone is cumulative and UNCOMMITTED between bricks (fact 5 in the plan):
    brick N's checkout leaves the path staged with a diff against HEAD, still uncommitted when
    brick N+1 runs. A naive `git rm` (no `-f`) on that path then refuses with rc=1 ('has
    changes staged in the index'), which is OUTSIDE the "skip on rm's rc=128" rule and would
    be neither skipped nor understood — so a naive implementation must go RED here even though
    it might pass row 4 (whose lone delete brick never collides with a prior brick's staged
    change). `git rm -f` is correct and safe ONLY because this clone is throwaway and never
    committed to.
    """
    scope = root / "r13-scope"
    init_repo(scope)
    write(scope / "target.txt", "orig\n")
    base = commit_all(scope, "chore: base")

    write(scope / "target.txt", "modified\n")
    a = commit_all(scope, "feat: modify target.txt")
    assert touched_paths(scope, a) == ["target.txt"]

    _git(scope, "rm", "-q", "target.txt")
    b = commit_all(scope, "feat: drop target.txt")
    assert touched_paths(scope, b) == ["target.txt"]

    plan = plan_file(
        root / "r13-plan.txt",
        ("v0.1.0", a, "feat: modify target.txt"),
        ("v0.2.0", b, "feat: drop target.txt"),
    )
    artifact_dir = root / "r13-artifacts"
    proc = run_tool(plan, scope, base, artifact_dir)

    assert "Traceback" not in (proc.stdout + proc.stderr), (
        "a cumulative modify-then-delete must be REHEARSED, never crash:\n"
        + proc.stdout
        + proc.stderr
    )

    blocks = brick_blocks(proc.stdout, ["v0.1.0", "v0.2.0"])
    assert "UNMEASURABLE" not in blocks["v0.1.0"], blocks["v0.1.0"]
    assert "PASS" in blocks["v0.1.0"], blocks["v0.1.0"]
    assert "UNMEASURABLE" not in blocks["v0.2.0"], (
        "brick N+1 deletes a path brick N just modified, in this cumulative uncommitted "
        "clone — a naive `git rm` (no -f) refuses it ('has changes staged in the index', "
        "rc=1) and that refusal must not be read as UNMEASURABLE:\n" + blocks["v0.2.0"]
    )
    assert "TAINTED" not in blocks["v0.2.0"], blocks["v0.2.0"]
    assert "PASS" in blocks["v0.2.0"], blocks["v0.2.0"]

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert re.search(
        r"RESULT: PASS rc=0 bricks=2 failed=0 skipped=\d+ unmeasurable=0 tainted=0",
        proc.stdout,
    ), proc.stdout

    clone_dir = artifact_dir.resolve() / "clone"
    assert not (clone_dir / "target.txt").exists(), (
        "the endpoint deletes target.txt — it must not survive materialisation in the "
        f"cumulative clone:\n{sorted(p.name for p in clone_dir.iterdir())}"
    )
