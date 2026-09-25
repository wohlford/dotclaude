"""Tests for scripts/publish-fold-plan.py — the mechanical published?/fold classifier that
proposes brick boundaries for /propagate's adopted publish path.

The classifier keys on the lines a commit REMOVES:
  * removes nothing            -> its own brick, settled without touching the published tree
  * removes a still-published  -> its own brick (published main is immutable and is never
    line                          rewritten to absorb a later fix)
  * removes only in-range      -> folds into whichever in-range commit added them
    lines

The fixture below is the smallest repo exercising all three arms at once, plus the case that
motivated the rule: several commits touching the SAME file that must stay separate bricks.
"""

import importlib.util
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "publish-fold-plan.py"
DRIVE_TOOL = Path(__file__).resolve().parent.parent / "publish-drive.py"


class Sandbox(os.PathLike):
    """A fixture repo that also carries the shas the assertions refer to."""

    def __init__(self, path):
        self.path = path
        self.shas = {}

    def __fspath__(self):
        return str(self.path)

    def __str__(self):
        return str(self.path)

    def __truediv__(self, other):
        return self.path / other


def git(repo, *args, check=True):
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def commit(repo, message, **files):
    for name, body in files.items():
        (repo / name.replace("__", "/")).write_text(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def commit_dated(repo, message, date, **files):
    """Like `commit`, but pins GIT_COMMITTER_DATE/GIT_AUTHOR_DATE — for fixtures that need a
    specific commit order regardless of wall-clock creation order (e.g. a side-branch commit
    that must sort AFTER a mainline commit it did not descend from)."""
    for name, body in files.items():
        (repo / name.replace("__", "/")).write_text(body)
    git(repo, "add", "-A")
    env = {**os.environ, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
    proc = subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", message],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git commit failed: {proc.stderr}")
    return git(repo, "rev-parse", "HEAD")


def run(repo, *args):
    return subprocess.run(
        [sys.executable, str(TOOL), "--scope", str(repo), *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path):
    """dev: c1 -> c2(add b) -> c3(rewrite b) -> c4(rewrite a); watermark at c1.

    main is a divorced orphan carrying c1's tree, so `alpha` is PUBLISHED and `beta` is not.
    """
    d = tmp_path / "r"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    c1 = commit(d, "feat(a): add a", **{"a.txt": "alpha\n"})
    c2 = commit(d, "feat(b): add b", **{"b.txt": "beta\n"})
    c3 = commit(d, "fix(b): correct b", **{"b.txt": "gamma\n"})
    c4 = commit(d, "fix(a): correct a", **{"a.txt": "delta\n"})

    git(d, "checkout", "-q", "--orphan", "main")
    git(d, "rm", "-rq", "--cached", ".")
    git(d, "clean", "-fdq")
    git(d, "checkout", "-q", c1, "--", ".")
    (d / "CHANGELOG.md").write_text(
        "# Changelog\n\n## v0.1.0 — 2026-01-01\n- feat(a): add a\n"
    )
    git(d, "add", "-A")
    git(d, "commit", "-qm", "feat(a): add a")
    git(d, "tag", "-a", "v0.1.0", "-m", "feat(a): add a")
    git(d, "update-ref", "refs/published/main", c1)
    git(d, "checkout", "-q", "dev")
    box = Sandbox(d)
    box.shas = {"c1": c1, "c2": c2, "c3": c3, "c4": c4}
    return box


@pytest.fixture
def ordering(tmp_path):
    """The measured fatal shape: a fold whose endpoint is overwritten by a LATER brick.

    dev: w -> a(add L2) -> b(add L3) -> c(remove L2, so it folds into a); watermark at w.
    All three touch one file, so the fold puts brick[a] at position 1 with endpoint `c` —
    the dev tip's content — while brick[b], which runs AFTER it, re-materialises the same
    file at `b`, an EARLIER state. The final tree then keeps L2, which the dev tip deleted.

    Every per-commit verdict here is correct; only their composition is wrong.
    """
    d = tmp_path / "o"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    w = commit(d, "feat(doc): add doc", **{"doc.md": "L1\n"})
    a = commit(d, "feat(doc): add L2", **{"doc.md": "L1\nL2\n"})
    b = commit(d, "feat(doc): add L3", **{"doc.md": "L1\nL2\nL3\n"})
    c = commit(d, "fix(doc): drop L2", **{"doc.md": "L1\nL3\n"})

    git(d, "checkout", "-q", "--orphan", "main")
    git(d, "rm", "-rq", "--cached", ".")
    git(d, "clean", "-fdq")
    git(d, "checkout", "-q", w, "--", ".")
    (d / "CHANGELOG.md").write_text(
        "# Changelog\n\n## v0.1.0 — 2026-01-01\n- feat(doc): add doc\n"
    )
    git(d, "add", "-A")
    git(d, "commit", "-qm", "feat(doc): add doc")
    git(d, "tag", "-a", "v0.1.0", "-m", "feat(doc): add doc")
    git(d, "update-ref", "refs/published/main", w)
    git(d, "checkout", "-q", "dev")
    box = Sandbox(d)
    box.shas = {"w": w, "a": a, "b": b, "c": c}
    return box


def short(sha):
    return sha[:7]


# ---------- the three classification arms ----------


def test_exits_zero_and_reports_a_verdict_line(repo):
    proc = run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip().splitlines()[-1].startswith("RESULT: PASS rc=0")


def test_counts_three_commits_two_bricks_one_fold(repo):
    out = run(repo).stdout
    assert "commits=3" in out and "bricks=2" in out and "folds=1" in out


def test_a_commit_removing_nothing_is_its_own_brick(repo):
    out = run(repo).stdout
    verdict = out.split(short(repo.shas["c2"]))[1].split("\n")[1]
    assert "OWN BRICK" in verdict
    assert "removes no lines" in verdict


def test_a_commit_removing_an_in_range_line_folds_into_its_author(repo):
    out = run(repo).stdout
    block = out.split(short(repo.shas["c3"]))[1]
    assert "FOLD INTO" in block
    assert short(repo.shas["c2"]) in block.split("\n")[1]


def test_a_commit_removing_a_published_line_is_its_own_brick(repo):
    out = run(repo).stdout
    block = out.split(short(repo.shas["c4"]))[1]
    assert "OWN BRICK" in block
    assert "published" in block


def test_the_published_arm_names_the_offending_path_as_evidence(repo):
    out = run(repo).stdout
    block = out.split(short(repo.shas["c4"]))[1]
    assert "a.txt" in block


# ---------- the measured case: same-file commits that must stay separate ----------


def test_three_appending_commits_to_one_file_stay_three_bricks(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    base = commit(d, "feat(doc): start", **{"doc.md": "one\n"})
    commit(d, "docs(doc): add two", **{"doc.md": "one\ntwo\n"})
    commit(d, "docs(doc): add three", **{"doc.md": "one\ntwo\nthree\n"})
    commit(d, "docs(doc): add four", **{"doc.md": "one\ntwo\nthree\nfour\n"})
    git(d, "update-ref", "refs/published/main", base)
    git(d, "branch", "main", base)

    out = run(d).stdout
    assert "bricks=3" in out and "folds=0" in out
    assert out.count("OWN BRICK") == 3


def test_fold_targets_the_LATEST_in_range_commit_that_added_the_removed_lines(tmp_path):
    """Two in-range commits each authored one of the removed lines; the fix belongs with the
    later one, since that is the brick whose final state the fix corrects."""
    d = tmp_path / "l"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    base = commit(d, "feat(m): start", **{"m.txt": "keep\n"})
    commit(d, "feat(m): add alpha", **{"m.txt": "keep\nalpha\n"})
    later = commit(d, "feat(m): add beta", **{"m.txt": "keep\nalpha\nbeta\n"})
    commit(d, "fix(m): drop both", **{"m.txt": "keep\n"})
    git(d, "update-ref", "refs/published/main", base)
    git(d, "branch", "main", base)

    out = run(d).stdout
    assert f"FOLD INTO {later[:7]}" in out
    assert "bricks=2" in out and "folds=1" in out


# ---------- the proposed driver invocations ----------


def test_emits_a_publish_brick_command_per_brick(repo):
    out = run(repo).stdout
    cmds = [ln.strip() for ln in out.splitlines() if "publish-brick.sh" in ln]
    assert len(cmds) == 2


def test_a_folded_brick_takes_the_LAST_constituent_as_endpoint(repo):
    out = run(repo).stdout
    cmd = next(
        ln for ln in out.splitlines() if "publish-brick.sh" in ln and "feat(b)" in ln
    )
    # endpoint is the fix (c3); the introducing commit (c2) rides along as a constituent
    assert short(repo.shas["c3"]) in cmd.split("'")[0]
    assert short(repo.shas["c2"]) in cmd.split("'")[2]


def test_a_folded_brick_keeps_the_INTRODUCING_commit_subject(repo):
    out = run(repo).stdout
    cmd = next(
        ln for ln in out.splitlines() if "publish-brick.sh" in ln and "feat(b)" in ln
    )
    assert "'feat(b): add b'" in cmd


# ---------- the plan must CONVERGE, not merely classify each commit right ----------


def parse_plan(out):
    """Return the emitted plan as [(endpoint, [constituent, ...])], in run order.

    Reads the tool's own `publish-brick.sh` lines rather than any internal structure, so the
    assertion is against what an operator would actually run.
    """
    plan = []
    for line in out.splitlines():
        if "publish-brick.sh" not in line:
            continue
        head, _, rest = line.strip().partition("'")
        endpoint = head.split()[-1]
        trailing = rest.partition("'")[2].split()
        plan.append((endpoint, [*trailing, endpoint]))
    return plan


def simulate(repo, plan):
    """Apply the plan the way publish-brick.sh does and return {path: final blob sha}.

    Each brick writes the UNION of its constituents' paths, taking content from its ENDPOINT.
    A later brick therefore overwrites an earlier one on any shared path — which is the whole
    hazard, and is why this simulation is per-path rather than per-brick.
    """
    final = {}
    for endpoint, members in plan:
        paths = set()
        for sha in members:
            paths.update(
                git(
                    repo, "show", "--format=", "--name-only", "--no-renames", sha
                ).split()
            )
        for path in paths:
            final[path] = git(repo, "rev-parse", f"{endpoint}:{path}")
    return final


def dev_tip_blobs(repo, paths):
    return {path: git(repo, "rev-parse", f"dev:{path}") for path in paths}


def test_the_emitted_plan_materialises_the_dev_tip_on_every_path(ordering):
    """The property the per-commit verdicts do NOT establish: that the plan COMPOSES.

    Every classification can be individually correct while the emitted ORDER still ends a
    path at the wrong content — a brick sits at its FIRST member's position but materialises
    at its LAST. Convergence is what the publish path proves only after every brick is built
    and tagged; here it is asserted against the plan itself, before any of that work.
    """
    proc = run(ordering)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    plan = parse_plan(proc.stdout)
    final = simulate(ordering, plan)
    assert final == dev_tip_blobs(ordering, final), (
        "the plan does not converge to the dev tip:\n" + proc.stdout
    )


def test_a_fold_that_would_be_overwritten_by_a_later_brick_is_not_proposed(ordering):
    """The specific shape behind the property: the unsafe fold must be DROPPED, not reordered.

    Reordering is one more composition claim nobody has checked; standing the commit alone
    costs only tidiness.
    """
    out = run(ordering).stdout
    plan = parse_plan(out)
    assert [members for _, members in plan] == [
        [ordering.shas["a"][:7]],
        [ordering.shas["b"][:7]],
        [ordering.shas["c"][:7]],
    ], out


# ---------- suggested versions follow /commit's bump rules ----------


def test_suggested_versions_bump_from_the_latest_tag(repo):
    out = run(repo).stdout
    cmds = [ln for ln in out.splitlines() if "publish-brick.sh" in ln]
    # base tag v0.1.0; feat -> minor, fix -> patch
    assert " v0.2.0 " in cmds[0]
    assert " v0.2.1 " in cmds[1]


def test_a_breaking_change_bumps_minor_below_v1(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    base = commit(d, "feat(x): start", **{"x.txt": "1\n"})
    commit(d, "feat(x)!: break it", **{"x.txt": "2\n", "y.txt": "new\n"})
    git(d, "update-ref", "refs/published/main", base)
    git(d, "branch", "main", base)
    git(d, "tag", "-a", "v0.4.2", "-m", "feat(x): start", base)
    out = run(d).stdout
    assert " v0.5.0 " in out


def test_no_tags_starts_from_v0_0_0(tmp_path):
    d = tmp_path / "n"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    base = commit(d, "chore: start", **{"x.txt": "1\n"})
    commit(d, "fix(x): patch it", **{"x.txt": "2\n"})
    git(d, "update-ref", "refs/published/main", base)
    git(d, "branch", "main", base)
    out = run(d).stdout
    assert " v0.0.1 " in out


# ---------- refusals ----------


def test_refuses_a_missing_watermark(repo):
    git(repo, "update-ref", "-d", "refs/published/main")
    proc = run(repo)
    assert proc.returncode == 2
    assert "watermark" in (proc.stdout + proc.stderr)


def test_refuses_a_watermark_stranded_off_the_working_branch(repo):
    """A rebase or amend can leave the watermark unreachable from dev. Every downstream
    verdict would then be about a range that does not exist — abort, never guess."""
    git(repo, "update-ref", "refs/published/main", git(repo, "rev-parse", "main"))
    proc = run(repo)
    assert proc.returncode == 2
    assert "ancestor" in (proc.stdout + proc.stderr)


def test_refuses_an_empty_range(repo):
    git(repo, "update-ref", "refs/published/main", git(repo, "rev-parse", "dev"))
    proc = run(repo)
    assert proc.returncode == 1
    assert "RESULT: FAIL" in proc.stdout


def test_refuses_a_non_adopted_repo(repo):
    (repo / ".publication.toml").unlink()
    proc = run(repo)
    assert proc.returncode == 2
    assert ".publication.toml" in (proc.stdout + proc.stderr)


def test_refuses_a_scope_that_is_not_a_repo(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--scope", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


def test_refuses_an_unknown_flag(repo):
    proc = run(repo, "--nonsense")
    assert proc.returncode == 2


# ---------- a blank removed line must not be treated as evidence ----------


def test_a_removed_blank_line_is_reported_as_UNDECIDED_not_guessed(tmp_path):
    """A bare `-` matches a blank line in almost any published file, so it is no evidence
    either way. The honest verdict is UNDECIDED, defaulting to its own brick — a wrong fold
    converges to the identical tree and so is exactly what the convergence check cannot catch,
    while a missed fold only costs tidiness."""
    d = tmp_path / "w"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    base = commit(d, "feat(p): start", **{"p.txt": "head\n\ntail\n"})
    commit(d, "feat(q): add q", **{"q.txt": "one\n\ntwo\n"})
    # NO fixture string here may contain the words the assertions look for. An earlier version
    # named this commit "drop the blank", which made `"blank" in out` true from the echoed
    # subject alone — the assertion passed for a reason unrelated to what it claimed to test,
    # and a mutation deleting the blank filter entirely survived it.
    fix = commit(d, "fix(q): tighten spacing", **{"q.txt": "one\ntwo\n"})
    git(d, "update-ref", "refs/published/main", base)
    git(d, "branch", "main", base)
    out = run(d).stdout

    verdict = out.split(fix[:7])[1].split("\n")[1]
    assert "UNDECIDED" in verdict
    assert "only blank lines" in verdict, "the verdict must say WHY it could not decide"
    assert "undecided=1" in out
    assert "folds=0" in out, "a blank line must never be enough to justify a fold"
    assert "bricks=2" in out, "an undecided commit defaults to its own brick"


# ---------- Task 4: the verdict line states RESIDUAL, never clearance ----------


@pytest.fixture
def zero_fold_repo(tmp_path):
    """dev: base -> add-two; watermark at base. Every commit removes nothing, so no
    multi-member unit is ever proposed and folds=0."""
    d = tmp_path / "zf"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    base = commit(d, "feat(doc): start", **{"doc.md": "one\n"})
    commit(d, "docs(doc): add two", **{"doc.md": "one\ntwo\n"})
    git(d, "update-ref", "refs/published/main", base)
    git(d, "branch", "main", base)
    return d


def test_folds_zero_verdict_states_structural_impossibility(zero_fold_repo):
    """`folds=0` must be annotated as no multi-member unit existing at all — the class of
    intermediate over-reach this whole task is about cannot arise, not merely 'nothing failed'."""
    out = run(zero_fold_repo).stdout
    assert "folds=0" in out
    verdict_line = out.strip().splitlines()[-1]
    assert verdict_line.startswith("RESULT: PASS rc=0")
    assert "structurally" in verdict_line and "impossible" in verdict_line


def test_folds_positive_verdict_states_unvalidated_until_rehearsed(repo):
    """`folds=1` must be annotated as UNVALIDATED for per-brick validity until rehearsed —
    a claim of residual risk, never a claim that the fold is fine."""
    out = run(repo).stdout
    assert "folds=1" in out
    verdict_line = out.strip().splitlines()[-1]
    assert verdict_line.startswith("RESULT: PASS rc=0")
    assert "UNVALIDATED" in verdict_line
    assert "rehearsed" in verdict_line
    # Must not be the folds=0 wording.
    assert "structurally" not in verdict_line and "impossible" not in verdict_line


def test_the_two_residual_annotations_are_genuinely_different_text(
    repo, zero_fold_repo
):
    """A constant annotation string would satisfy the two tests above independently — this
    test fails on that mutant by comparing the actual annotation text, not just presence."""
    positive_line = run(repo).stdout.strip().splitlines()[-1]
    zero_line = run(zero_fold_repo).stdout.strip().splitlines()[-1]

    positive_annotation = positive_line.split("converges=yes", 1)[1]
    zero_annotation = zero_line.split("converges=yes", 1)[1]

    assert positive_annotation.strip()
    assert zero_annotation.strip()
    assert positive_annotation != zero_annotation


# ---------- evidence collides with parse_plan's own grammar (2026-08-21 plan, Task 1) ----------
#
# `publish-fold-plan.py`'s own evidence — the removed source lines it prints as proof of a
# verdict — can itself contain the substring "publish-brick.sh", because a commit that edits
# publish-brick.sh removes lines quoting its own filename. `parse_plan` (loaded from
# publish-drive.py, the same hardened parser both real consumers use) then misreads that
# evidence as an invocation. The defect has TWO shapes, verified directly against parse_plan
# and NOT re-derived from the plan's prose:
#
#   mid-line    -> the engine name sits inside a longer quoted token, so no shlex word ENDS
#                  with "publish-brick.sh"; idx is None, args is empty, and parse_plan raises
#                  "names publish-brick.sh but has 0 argument(s), need at least 3".
#   end-of-line -> the engine name IS the tail of its quoted token, so idx is found and the
#                  evidence's own trailing " added by <sha7>" supplies exactly 3 words
#                  ("added", "by", "<sha7>") — parsing with NO error into a PHANTOM BRICK
#                  {'version': 'added', 'endpoint': 'by', 'subject': '<sha7>'}, silently
#                  inflating the count. Nothing loud catches this; only count-equality does.


def load_parse_plan():
    """Load `parse_plan` from publish-drive.py by path, exactly as publish-rehearse.py does
    (`load_publish_drive`, scripts/publish-rehearse.py:101) — the same hardened parser both
    real consumers (`publish-drive.py` and `publish-rehearse.py`) use, not a hand-rolled second
    copy that could drift from it."""
    spec = importlib.util.spec_from_file_location("publish_drive", DRIVE_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_plan


def bricks_from_verdict(out):
    """The `bricks=N` the planner's own terminal RESULT line reports."""
    verdict_line = out.strip().splitlines()[-1]
    m = re.search(r"bricks=(\d+)", verdict_line)
    assert m, f"no bricks=N in verdict line: {verdict_line!r}"
    return int(m.group(1))


@pytest.fixture
def evidence_collision_repo(tmp_path):
    """dev: c1(add a) -> c2(add script.sh, two lines naming the engine) -> c3(remove both
    lines, folding into c2) -> c4(add b, unrelated). watermark at c1.

    c2's script.sh carries the engine name in BOTH shapes; c3 removes both lines in one commit,
    so the rendered plan's FOLD-arm evidence for c3 (`{path}: {line!r} added by {sha7}`)
    contains both shapes at once:
      mid-line:    script.sh: 'fail_brick "constituent $c invokes publish-brick.sh with bad
                   args"' added by <c2 sha7>
      end-of-line: script.sh: 'echo about to run publish-brick.sh' added by <c2 sha7>

    Two proposed bricks: {c2, c3} folded, and {c4} alone — the minimum needed for Task 2's
    single-invocation mutation, which requires at least two.
    """
    d = tmp_path / "ec"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    c1 = commit(d, "feat(a): add a", **{"a.txt": "alpha\n"})
    c2 = commit(
        d,
        "feat(s): add script",
        **{
            "script.sh": (
                "#!/bin/bash\n"
                'fail_brick "constituent $c invokes publish-brick.sh with bad args"\n'
                "echo about to run publish-brick.sh\n"
                "echo done\n"
            )
        },
    )
    c3 = commit(
        d, "fix(s): tighten script", **{"script.sh": "#!/bin/bash\necho done\n"}
    )
    c4 = commit(d, "feat(b): add b", **{"b.txt": "beta\n"})

    git(d, "checkout", "-q", "--orphan", "main")
    git(d, "rm", "-rq", "--cached", ".")
    git(d, "clean", "-fdq")
    git(d, "checkout", "-q", c1, "--", ".")
    (d / "CHANGELOG.md").write_text(
        "# Changelog\n\n## v0.1.0 — 2026-01-01\n- feat(a): add a\n"
    )
    git(d, "add", "-A")
    git(d, "commit", "-qm", "feat(a): add a")
    git(d, "tag", "-a", "v0.1.0", "-m", "feat(a): add a")
    git(d, "update-ref", "refs/published/main", c1)
    git(d, "checkout", "-q", "dev")
    box = Sandbox(d)
    box.shas = {"c1": c1, "c2": c2, "c3": c3, "c4": c4}
    return box


def test_the_plan_parses_despite_evidence_naming_the_engine(evidence_collision_repo):
    """`parse_plan(plan_text)` must not raise merely because its own evidence text mentions
    the engine's own filename. Currently it does, via the mid-line shape."""
    out = run(evidence_collision_repo).stdout
    parse_plan = load_parse_plan()
    parse_plan(out)  # must not raise


def test_parsed_brick_count_matches_the_planners_own_verdict(evidence_collision_repo):
    """The count parse_plan yields must equal the `bricks=N` the planner itself reports (spec
    D4) — the falsifiable guard that points at the dangerous direction: a wrong fix that still
    collides fails loudly and costs nothing, but a wrong fix that over-prefixes silently drops
    a brick and yields a shorter plan that reads complete."""
    out = run(evidence_collision_repo).stdout
    parse_plan = load_parse_plan()
    bricks = parse_plan(out)
    assert len(bricks) == bricks_from_verdict(out)


def test_parsed_brick_count_matches_the_hand_declared_fixture_size(
    evidence_collision_repo,
):
    """Derivation alone (the row above) cannot catch a shared upstream fault: if `assemble()`
    itself dropped a brick, `bricks=N` and the parse count would agree and that row would pass
    for free. This hand-declared floor (2: {c2+c3} folded, {c4} alone) closes that gap — the
    repo's derive-plus-declared-floor rule."""
    out = run(evidence_collision_repo).stdout
    parse_plan = load_parse_plan()
    bricks = parse_plan(out)
    assert len(bricks) == 2


# ---------- the SILENT shape alone: parses clean, count inflated (permanent suite row) ----------
#
# The combined fixture above cannot show row-1-passes-while-2/3-fail: the mid-line shape's raise
# always fires somewhere in the plan text, aborting the whole parse before a caller can observe
# what the end-of-line shape did silently along the way. That discrimination needs its OWN
# fixture, carrying ONLY the end-of-line shape — nothing raises, so a regression that reintroduces
# just the silent half would otherwise be invisible to every row above (all of which are also
# green once nothing raises) and visible only to the count rows below.


@pytest.fixture
def phantom_only_repo(tmp_path):
    """dev: c1(add a) -> c2(add script.sh, ONE line ending in the engine name, no mid-line
    occurrence anywhere in range) -> c3(remove that line, folding into c2) -> c4(add b,
    unrelated). watermark at c1.

    Two proposed bricks: {c2, c3} folded, and {c4} alone. c3's FOLD-arm evidence is exactly:
      script.sh: 'echo about to run publish-brick.sh' added by <c2 sha7>
    which parses with NO error — the quoted token IS the word ending in "publish-brick.sh", and
    the evidence's own trailing " added by <sha7>" supplies exactly 3 words. Verified directly
    against parse_plan on the real plan for this fixture: parses to 3 bricks (true count 2),
    the extra one being {"version": "added", "endpoint": "by", "subject": "<c2 sha7>"}.
    """
    d = tmp_path / "po"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    c1 = commit(d, "feat(a): add a", **{"a.txt": "alpha\n"})
    c2 = commit(
        d,
        "feat(s): add script",
        **{"script.sh": "#!/bin/bash\necho about to run publish-brick.sh\necho done\n"},
    )
    c3 = commit(
        d, "fix(s): tighten script", **{"script.sh": "#!/bin/bash\necho done\n"}
    )
    c4 = commit(d, "feat(b): add b", **{"b.txt": "beta\n"})

    git(d, "checkout", "-q", "--orphan", "main")
    git(d, "rm", "-rq", "--cached", ".")
    git(d, "clean", "-fdq")
    git(d, "checkout", "-q", c1, "--", ".")
    (d / "CHANGELOG.md").write_text(
        "# Changelog\n\n## v0.1.0 — 2026-01-01\n- feat(a): add a\n"
    )
    git(d, "add", "-A")
    git(d, "commit", "-qm", "feat(a): add a")
    git(d, "tag", "-a", "v0.1.0", "-m", "feat(a): add a")
    git(d, "update-ref", "refs/published/main", c1)
    git(d, "checkout", "-q", "dev")
    box = Sandbox(d)
    box.shas = {"c1": c1, "c2": c2, "c3": c3, "c4": c4}
    return box


def test_the_silent_shape_alone_does_not_raise(phantom_only_repo):
    """Pins the SILENT half in isolation: with no mid-line occurrence anywhere in range,
    nothing raises. Expected GREEN both before and after Task 2's fix — this row is the control
    proving the failure is silent, not a redundant restatement of row 1 above: it isolates the
    end-of-line shape from the mid-line shape that otherwise masks it, and a green result here
    is what makes the RED count rows below meaningful (a raise here would mean they were
    testing an error path, not a silent one)."""
    out = run(phantom_only_repo).stdout
    parse_plan = load_parse_plan()
    parse_plan(out)  # must not raise, and does not today either


def test_the_silent_shape_inflates_the_count_past_the_planners_own_verdict(
    phantom_only_repo,
):
    """Pins the SILENT half where the mid-line shape's row above cannot: parsing succeeds, so
    only a count check can see the phantom brick the end-of-line shape adds. Not redundant with
    the combined fixture's equivalent row — that one dies from the mid-line raise before this
    comparison is ever reached; this is the only row in the suite that actually performs the
    comparison against the silent shape."""
    out = run(phantom_only_repo).stdout
    parse_plan = load_parse_plan()
    bricks = parse_plan(out)
    assert len(bricks) == bricks_from_verdict(out)


def test_the_silent_shape_inflates_the_count_past_the_hand_declared_fixture_size(
    phantom_only_repo,
):
    """Same declared-floor rationale as the combined fixture's equivalent row: derivation alone
    (the row above) cannot catch a shared upstream fault in `assemble()`. This hand-declared
    floor (2: {c2+c3} folded, {c4} alone) is the only assertion in the suite that is NOT
    derived from the tool's own output for the silent-shape-only case."""
    out = run(phantom_only_repo).stdout
    parse_plan = load_parse_plan()
    bricks = parse_plan(out)
    assert len(bricks) == 2


# ---------- a fold that jumps over a commit sharing one of its paths (2026-09-24 plan) ----------
#
# A brick sits at its FIRST member's position but materialises the UNION of its members' paths
# at its LAST member's content. For a linear range, a non-member commit strictly between
# first and last is already baked into that last member's tree — if that non-member touched a
# path in the union, the brick publishes the non-member's change under the wrong subject, one
# brick early. The final tree can still match the dev tip exactly (the jumped commit's content
# is a strict prefix of what the endpoint already carries), which is why `diverging_paths` —
# the plan's only postcondition — was measured not to fire on this shape at all.


@pytest.fixture
def jumped(tmp_path):
    """dev: w(x.txt "x0", y.txt "y0") -> A(x +"ax", y +"ay") -> B(y +"by") -> C(x -"ax").

    C removes only "ax", which A alone added, so C folds into A. B sits strictly between A and
    C and touches y.txt, which is also in {A, C}'s union of paths (A added "ay" there). The
    brick {A, C} would materialise y.txt at C's content — which already carries B's "by" line,
    since history is linear — one brick before B's own brick runs.
    """
    d = tmp_path / "j"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    w = commit(d, "feat(xy): start", **{"x.txt": "x0\n", "y.txt": "y0\n"})
    a = commit(d, "feat(xy): extend both", **{"x.txt": "x0\nax\n", "y.txt": "y0\nay\n"})
    b = commit(d, "feat(y): extend y again", **{"y.txt": "y0\nay\nby\n"})
    c = commit(d, "fix(x): revert the extension", **{"x.txt": "x0\n"})
    git(d, "update-ref", "refs/published/main", w)
    git(d, "branch", "main", w)
    box = Sandbox(d)
    box.shas = {"w": w, "a": a, "b": b, "c": c}
    return box


def test_a_fold_jumping_a_commit_that_shares_a_path_is_dropped(jumped):
    """Measured on this fixture before the jumped-collision rule: the planner proposed {a, c}
    as one brick, because its only pruning (diverging_paths) checked final-tree convergence,
    and this fold's final tree matches the dev tip on every path — B's content is a strict
    prefix of C's y.txt. The fold is dropped instead: C stands alone."""
    out = run(jumped).stdout
    assert "dropped=1" in out, out
    assert "folds=0" in out, out


def test_the_dropped_fold_names_the_shared_path_and_the_jumped_commit(jumped):
    """The dropped commit's block should name the colliding path and the commit that jumped
    over — the evidence an operator needs to see WHY a fold that looked safe was refused.

    Checked as ONE output line carrying both facts (planned evidence format:
    `collides on: <path> (changed by <sha7>[, <sha7>...])`), not merely that both substrings
    appear somewhere in the tail of the output — `short(sha)` alone is satisfied by unrelated
    text, including the proposed `publish-brick.sh ... <sha7> ...` line further down.
    """
    out = run(jumped).stdout
    b7 = short(jumped.shas["b"])
    matching = [
        ln
        for ln in out.splitlines()
        if "collides on: y.txt" in ln and f"changed by {b7}" in ln
    ]
    assert matching, out


def test_the_jumped_fold_converges_so_convergence_cannot_see_it(jumped):
    """CONTROL: applying the fold the planner used to propose — {a, c} then {b} — converges
    to the dev tip on every path. This is the measured reason the convergence postcondition
    could not catch the defect: it is green here before AND after the jumped-collision rule,
    because the fold really does converge; only its attribution is wrong."""
    plan = [
        (jumped.shas["c"][:7], [jumped.shas["a"][:7], jumped.shas["c"][:7]]),
        (jumped.shas["b"][:7], [jumped.shas["b"][:7]]),
    ]
    final = simulate(jumped, plan)
    assert final == dev_tip_blobs(jumped, final)


@pytest.fixture
def leap(tmp_path):
    """dev: w(x.txt "x0") -> A(x +"ax") -> B(new z.txt) -> C(x -"ax", folds into A).

    B sits strictly between A and C but touches z.txt, a path entirely disjoint from the
    fold's union ({x.txt}) — nothing collides, so the fold is safe to keep. It is still worth
    surfacing as an advisory: the operator sees which commit the surviving fold jumped over.
    """
    d = tmp_path / "lp"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    w = commit(d, "feat(x): start", **{"x.txt": "x0\n"})
    a = commit(d, "feat(x): extend", **{"x.txt": "x0\nax\n"})
    b = commit(d, "feat(z): add z", **{"z.txt": "z0\n"})
    c = commit(d, "fix(x): revert", **{"x.txt": "x0\n"})
    git(d, "update-ref", "refs/published/main", w)
    git(d, "branch", "main", w)
    box = Sandbox(d)
    box.shas = {"w": w, "a": a, "b": b, "c": c}
    return box


def test_a_fold_jumping_only_disjoint_commits_survives_and_is_listed(leap):
    """The surviving fold should be listed as an advisory naming what it jumped over, and the
    parsed plan's brick count should match the planner's own verdict line."""
    out = run(leap).stdout
    assert "folds=1" in out, out
    jump_lines = [
        ln
        for ln in out.splitlines()
        if ln.lstrip().startswith("#") and "jumps over" in ln
    ]
    assert jump_lines, out
    assert "jumps over 1" in jump_lines[0]
    assert short(leap.shas["b"]) in jump_lines[0]
    # Counted with the hardened drive parser — the one the real consumers (publish-drive.py,
    # publish-rehearse.py) actually read — rather than the local `parse_plan` helper above.
    # For this fixture the two cannot disagree: the advisory line is `#`-prefixed AND never
    # contains "publish-brick.sh", so a mutant that dropped its leading "#" would be invisible
    # to either parser (design: consumers key on the substring, not the prefix alone). This is
    # still the right instrument to reach for, since it is what production actually runs.
    parse_plan_drive = load_parse_plan()
    bricks = parse_plan_drive(out)
    assert len(bricks) == bricks_from_verdict(out)


def test_an_adjacent_fold_lists_no_jump(repo):
    """CONTROL: `repo`'s fold {c2, c3} is adjacent — nothing sits strictly between them — so
    no advisory line should ever be emitted for it, before or after any change here."""
    out = run(repo).stdout
    jump_lines = [ln for ln in out.splitlines() if "jumps over" in ln]
    assert not jump_lines, out


# ---------- property: no plan ever publishes a jumped commit's path early ----------


RANDOM_HISTORY_FILES = ("alpha.txt", "beta.txt", "gamma.txt")


def build_random_history(d, seed):
    """A small history over 3 files: 4-7 commits, each touching 1-2 files, each touched file
    either gaining a unique appended line or (40% of the time, once it has one available)
    losing a previously in-range-added line. Deterministic per seed."""
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    for f in RANDOM_HISTORY_FILES:
        (d / f).write_text("seed\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "feat(r): start")
    w = git(d, "rev-parse", "HEAD")
    git(d, "update-ref", "refs/published/main", w)
    git(d, "branch", "main", w)

    rng = random.Random(seed)
    added = {f: [] for f in RANDOM_HISTORY_FILES}
    counter = 0
    for i in range(rng.randint(4, 7)):
        touched = rng.sample(RANDOM_HISTORY_FILES, rng.choice([1, 2]))
        payload = {}
        for f in touched:
            text = (d / f).read_text()
            if added[f] and rng.random() < 0.4:
                victim = rng.choice(added[f])
                remaining = [ln for ln in text.splitlines() if ln != victim]
                payload[f] = "\n".join(remaining) + ("\n" if remaining else "")
                added[f].remove(victim)
            else:
                counter += 1
                unique = f"s{seed}n{counter}"
                payload[f] = text + unique + "\n"
                added[f].append(unique)
        commit(d, f"feat(r): step {i}", **payload)
    return d


def full_sha_map(repo):
    """Map every 7-char sha prefix reachable in this repo to its full sha."""
    out = git(repo, "log", "--all", "--format=%H")
    return {sha[:7]: sha for sha in out.split("\n") if sha}


def touched_paths(repo, sha):
    out = git(repo, "show", "--format=", "--name-only", "--no-renames", sha)
    return {line for line in out.split("\n") if line}


def test_random_histories_never_publish_a_jumped_commit_early(tmp_path):
    """Property: for every multi-member unit the planner proposes, no commit strictly between
    its first and last member (in watermark..dev order) may share a path with the unit's own
    union of paths — that is exactly the shape a brick publishes one position early. Checked
    entirely test-side from git, never by importing the planner's own functions, so the check
    cannot inherit whatever the planner gets wrong. Measured: 7 of these 15 seeded histories
    reproduced the defect on the planner before the jumped-collision rule."""
    total_multi_member = 0
    total_dropped = 0
    for seed in range(15):
        d = tmp_path / f"rh{seed}"
        build_random_history(d, seed)

        proc = run(d)
        assert proc.returncode == 0, f"seed {seed}: {proc.stdout}{proc.stderr}"
        assert "converges=yes" in proc.stdout, f"seed {seed}: {proc.stdout}"

        dropped_match = re.search(r"dropped=(\d+)", proc.stdout)
        total_dropped += int(dropped_match.group(1)) if dropped_match else 0

        sha_map = full_sha_map(d)
        full_order = git(d, "rev-list", "--reverse", "refs/published/main..dev").split()
        plan = parse_plan(proc.stdout)

        for _endpoint, members in plan:
            full_members = [sha_map[s] for s in members]
            if len(full_members) < 2:
                continue
            total_multi_member += 1
            first_idx = full_order.index(full_members[0])
            last_idx = full_order.index(full_members[-1])
            jumped_commits = [
                sha
                for sha in full_order[first_idx + 1 : last_idx]
                if sha not in full_members
            ]
            union_paths: set[str] = set()
            for member_sha in full_members:
                union_paths |= touched_paths(d, member_sha)
            for jumped_sha in jumped_commits:
                collision = union_paths & touched_paths(d, jumped_sha)
                assert not collision, (
                    f"seed {seed}: fold {[s[:7] for s in full_members]} jumps over "
                    f"{jumped_sha[:7]} sharing {sorted(collision)}"
                )

        final = simulate(d, plan)
        assert final == dev_tip_blobs(d, final), f"seed {seed}: plan does not converge"

    assert total_multi_member >= 1, "no seed produced a folded (multi-member) unit"
    # Weak on its own: measured, this already held before the jumped-collision rule, satisfied
    # entirely by OTHER seeds' drops through the divergence-based prune that rule replaced — it
    # would pass even if the rule were never built.
    # See the pinned-seed row below for the assertion that actually depends on the new rule.
    assert total_dropped >= 1, "no seed produced any dropped fold"


def test_a_seed_with_a_collision_but_no_divergence_is_still_dropped(tmp_path):
    """Pinned regression for the property test's own `total_dropped >= 1` floor above, which was
    already satisfied before the jumped-collision rule by other seeds' divergence-based drops.
    Seed 3 of the same generator (`build_random_history`) is the seed whose outcome depends on
    the rule: measured, it produced a surviving fold sharing a path with a commit it jumps over,
    while `diverging_paths` never fired for it (the fold still converges), so the planner
    reported `dropped=0` for this exact history."""
    d = tmp_path / "rh_pinned_3"
    build_random_history(d, 3)
    out = run(d).stdout
    dropped = int(re.search(r"dropped=(\d+)", out).group(1))
    assert dropped >= 1, out


# ---------- a merge-bearing range crashed instead of reporting a verdict (plan review) ----------
#
# A pre-existing, unrelated bug surfaced while probing the jumped-collision shape with a merge:
# `prune_unsafe_folds`'s old culprit search looked only among MULTI-member bricks; when the
# divergence it hunted was owned by a SINGLE-commit brick instead, its `next(...)` matched
# nothing, and an uncaught StopIteration killed the process before `report()` ever ran — no
# `RESULT:` line at all.


@pytest.fixture(params=["p.txt", "pé.txt"], ids=["ascii", "non-ascii"])
def merge_bearing(tmp_path, request):
    """Parametrized over an ASCII and a non-ASCII name for p.txt. The non-ASCII case once
    passed silently: a quoted `--name-only` path made `blob()` read None on both sides, so the
    divergence below never registered.

    dev: BASE(p.txt "p0") -> M1(p.txt +"m1", OLDER date) on dev directly; separately, a side
    branch off BASE carries S1(p.txt +"s1", NEWER date); dev then merges the side branch with
    `-s ours` (keeping M1's content byte-for-byte, so the merge commit's own diff is empty) ->
    X(new q.txt). Watermark/main at BASE.

    Nothing here removes an in-range line, so every commit is its own singleton brick. Yet
    `diverging_paths` still flags p.txt: in commit (hence brick) order M1, S1, merge, X, the
    LAST brick recorded as "owning" p.txt in the writer dict is S1's singleton — whose content
    the `-s ours` merge discarded — so `blob(S1, p.txt) != blob(dev tip, p.txt)`. The old
    culprit search for a MULTI-member brick to unfold found none among four singletons.
    """
    name = request.param
    d = tmp_path / "mb"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    base = commit(d, "feat(p): start", **{name: "p0\n"})
    git(d, "update-ref", "refs/published/main", base)
    git(d, "branch", "main", base)

    m1 = commit_dated(
        d, "feat(p): mainline extends", "2026-01-01T00:00:00", **{name: "p0\nm1\n"}
    )

    git(d, "checkout", "-q", "-b", "side", base)
    s1 = commit_dated(
        d, "feat(p): side extends", "2026-01-02T00:00:00", **{name: "p0\ns1\n"}
    )

    git(d, "checkout", "-q", "dev")
    env = {
        **os.environ,
        "GIT_COMMITTER_DATE": "2026-01-03T00:00:00",
        "GIT_AUTHOR_DATE": "2026-01-03T00:00:00",
    }
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(d),
            "merge",
            "-s",
            "ours",
            "-q",
            "-m",
            "feat(p): keep mainline",
            "side",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git merge failed: {proc.stderr}")
    merge = git(d, "rev-parse", "HEAD")

    x = commit_dated(d, "feat(q): add q", "2026-01-04T00:00:00", **{"q.txt": "q0\n"})

    box = Sandbox(d)
    box.shas = {"base": base, "m1": m1, "s1": s1, "merge": merge, "x": x}
    return box


def test_a_merge_bearing_range_reports_a_verdict_instead_of_crashing(merge_bearing):
    """Measured: the planner died with an uncaught StopIteration on this fixture (traceback on
    stderr, rc=1, and stdout EMPTY — `report()`, which prints every line including the
    `RESULT:` line, never ran, since the crash was in `prune_unsafe_folds`, called before it).
    It now reaches `report()`'s own postcondition and fails loudly there."""
    order = git(
        merge_bearing, "rev-list", "--reverse", "refs/published/main..dev"
    ).split()
    assert order.index(merge_bearing.shas["s1"]) > order.index(
        merge_bearing.shas["m1"]
    ), order

    proc = run(merge_bearing)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    last_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    assert last_line.startswith("RESULT: FAIL rc=1"), (
        f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
    )
    assert "diverging=" in last_line, last_line
    assert "dropped=" in last_line, last_line


# ---------- a non-ASCII path is invisible to the convergence check (plan review) ----------
#
# Git quotes/escapes a non-ASCII path by default in plain `--name-only` output and in the
# `--- a/`/`+++ b/` diff headers, and this tool once read paths from both — so the extracted
# path string did not match the real bytes. `blob()`'s `git rev-parse ref:<mangled path>` then
# failed on BOTH sides of a divergence comparison, and `None != None` is False: the `ordering`
# shape went unreported, because the check never resolved either side.


@pytest.fixture
def cafe_ordering(tmp_path):
    """The `ordering` fixture's exact shape (w -> a add L2 -> b add L3 -> c drop L2, so c folds
    into a) on a file whose name is not pure ASCII. b sits between a and c and touches the same
    path, so the jumped-collision rule drops this fold. It does so whether or not paths are
    read quoted — the rule compares the quoted string with itself — so this row does NOT pin the
    `-z` read; measured, reverting `commit_paths`'s `-z` alone left it green, and only
    `merge_bearing[non-ascii]` went red. That row is the `-z` regression; this one pins the
    drop on a non-ASCII name.
    """
    d = tmp_path / "cf"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    name = "café.md"
    w = commit(d, "feat(doc): add doc", **{name: "L1\n"})
    a = commit(d, "feat(doc): add L2", **{name: "L1\nL2\n"})
    b = commit(d, "feat(doc): add L3", **{name: "L1\nL2\nL3\n"})
    c = commit(d, "fix(doc): drop L2", **{name: "L1\nL3\n"})
    git(d, "update-ref", "refs/published/main", w)
    git(d, "branch", "main", w)
    box = Sandbox(d)
    box.shas = {"w": w, "a": a, "b": b, "c": c}
    # NOT `box.path` — `Sandbox.path` already holds the repo's real directory, and assigning
    # over it silently breaks `run()`'s `--scope`, which reads `str(box)` -> `str(box.path)`.
    box.filename = name
    return box


def touched_paths_z(repo, sha):
    """Like `touched_paths` above, but via `-z`, which git never quotes or escapes — unlike the
    shared `simulate`/`touched_paths` helpers elsewhere in this file, which read a plain
    (quoted) `--name-only` and would be blind to this exact defect the way the planner was. A
    correctness check for a non-ASCII path must not reuse those helpers."""
    out = git(repo, "show", "--format=", "--name-only", "--no-renames", "-z", sha)
    return [p for p in out.split("\0") if p]


def test_a_fold_over_a_non_ascii_path_is_not_proposed_and_actually_converges(
    cafe_ordering,
):
    """Same claim as `test_a_fold_that_would_be_overwritten_by_a_later_brick_is_not_proposed`
    (the `ordering` fixture's ASCII version): the fold should not be proposed, because it does
    not actually converge. Convergence is checked with `-z`-based path reading (see
    `touched_paths_z`) so this test's own check does not share the quoted-name blindness the
    planner's convergence postcondition once had.
    """
    out = run(cafe_ordering).stdout
    plan = parse_plan(out)
    assert [members for _, members in plan] == [
        [cafe_ordering.shas["a"][:7]],
        [cafe_ordering.shas["b"][:7]],
        [cafe_ordering.shas["c"][:7]],
    ], out

    final: dict[str, str] = {}
    for endpoint, members in plan:
        paths: set[str] = set()
        for sha in members:
            paths |= set(touched_paths_z(cafe_ordering, sha))
        for path in paths:
            final[path] = git(cafe_ordering, "rev-parse", f"{endpoint}:{path}")
    dev_tip = {path: git(cafe_ordering, "rev-parse", f"dev:{path}") for path in final}
    assert final == dev_tip, f"plan does not converge on the non-ASCII path:\n{out}"


# ---------- a published line removed from a non-ASCII path must stay its own brick ----------


@pytest.fixture
def cafe_published(tmp_path):
    """w publishes café.md ("keep", "pub"); a removes the PUBLISHED line "pub"."""
    d = tmp_path / "cp"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    w = commit(d, "feat(doc): start", **{"café.md": "keep\npub\n"})
    a = commit(d, "fix(doc): trim", **{"café.md": "keep\n"})
    git(d, "update-ref", "refs/published/main", w)
    git(d, "branch", "main", w)
    box = Sandbox(d)
    box.shas = {"w": w, "a": a}
    return box


def test_removing_a_published_line_from_a_non_ascii_path_is_its_own_brick(
    cafe_published,
):
    """Measured: `diff_lines` read the C-quoted `--- a/` header, so the removed line was keyed
    under a mangled path, `published_lines` could never resolve it, and the commit came out
    UNDECIDED — evidence of a published line lost to the quoting, and one step from a FOLD, the
    wrong direction to be wrong in on the publish path."""
    out = run(cafe_published).stdout
    verdict = out.split(short(cafe_published.shas["a"]))[1].split("\n")[1]
    assert "OWN BRICK" in verdict, out
    assert "still present in published" in verdict, out


# ---------- a path that is not valid UTF-8 must still reach a verdict ----------

LATIN1_NAME = os.fsdecode(b"caf\xe9.txt")


def commit_blobs(repo, message, files):
    """Commit `files` ({path: text}) through the index directly, so a path whose bytes are not
    valid UTF-8 can be committed without the filesystem having to accept it."""
    for path, body in files.items():
        blob_sha = (
            subprocess.run(
                ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                input=body.encode(),
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob_sha},{path}",
            ],
            capture_output=True,
            check=True,
        )
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def latin1_jumped(tmp_path):
    """The `jumped` shape with the SHARED path named in Latin-1 bytes: a folds c into itself
    across b, and b changes the Latin-1 path, so the drop's evidence must print that name."""
    d = tmp_path / "l1"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    git(d, "add", ".publication.toml")
    w = commit_blobs(d, "feat(l): start", {"x.txt": "x0\n", LATIN1_NAME: "y0\n"})
    a = commit_blobs(
        d, "feat(l): extend both", {"x.txt": "x0\nax\n", LATIN1_NAME: "y0\nay\n"}
    )
    b = commit_blobs(d, "feat(l): extend y again", {LATIN1_NAME: "y0\nay\nby\n"})
    c = commit_blobs(d, "fix(l): revert the extension", {"x.txt": "x0\n"})
    git(d, "update-ref", "refs/published/main", w)
    git(d, "branch", "main", w)
    box = Sandbox(d)
    box.shas = {"w": w, "a": a, "b": b, "c": c}
    return box


def test_a_path_that_is_not_utf8_still_reaches_a_verdict(latin1_jumped):
    """Measured by the final branch review: once git stopped quoting path names (`-z`,
    `core.quotePath=false`), a Latin-1 path reached `text=True` decoding raw and raised
    UnicodeDecodeError — a traceback and no `RESULT:` line, where the quoted form had parsed.
    The fold here must still be dropped, with evidence naming the path, and a verdict printed."""
    proc = run(latin1_jumped)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    last_line = proc.stdout.strip().split("\n")[-1]
    assert last_line.startswith("RESULT: PASS rc=0"), proc.stdout + proc.stderr
    assert "dropped=1" in last_line, last_line
    b7 = short(latin1_jumped.shas["b"])
    assert any(
        "collides on: caf" in ln and f"changed by {b7}" in ln
        for ln in proc.stdout.split("\n")
    ), proc.stdout


@pytest.fixture
def latin1_in_range(tmp_path):
    """A Latin-1-named file that is NOT published: a adds it, b trims a line a added (so b folds
    into a), c deletes it (its line was a's too, so c folds as well). Classifying b and c asks
    the published tree for that path, and the convergence check asks every ref for it — each a
    lookup that FAILS, so git's error names the raw bytes on stderr."""
    d = tmp_path / "l2"
    d.mkdir()
    git(d, "init", "-q", "-b", "dev", ".")
    git(d, "config", "user.email", "test@test.invalid")
    git(d, "config", "user.name", "test")
    git(d, "config", "commit.gpgsign", "false")
    git(d, "config", "tag.gpgsign", "false")
    (d / ".publication.toml").write_text('production = "dev"\n')
    git(d, "add", ".publication.toml")
    w = commit_blobs(d, "feat(l): start", {"x.txt": "x0\n"})
    commit_blobs(d, "feat(l): add the latin file", {LATIN1_NAME: "keep\ncut\n"})
    commit_blobs(d, "fix(l): trim it", {LATIN1_NAME: "keep\n"})
    git(d, "rm", "-q", "--cached", LATIN1_NAME)
    git(d, "commit", "-qm", "fix(l): retire it")
    git(d, "update-ref", "refs/published/main", w)
    git(d, "branch", "main", w)
    return Sandbox(d)


def test_an_unpublished_non_utf8_path_is_looked_up_without_crashing(latin1_in_range):
    """Measured by the tip review: with `surrogateescape` removed from `published_lines` and
    `blob` alone, this shape crashed (UnicodeDecodeError, no `RESULT:` line) while every other
    row stayed green — `latin1_jumped` never reaches a FAILING lookup of a non-UTF-8 path, and
    only a failing lookup puts the raw bytes on stderr."""
    proc = run(latin1_in_range)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    last_line = proc.stdout.strip().split("\n")[-1]
    assert last_line.startswith("RESULT: PASS rc=0"), proc.stdout + proc.stderr
    assert "folds=2" in last_line, last_line
