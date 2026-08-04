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

import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "publish-fold-plan.py"


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
