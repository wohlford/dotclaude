"""Tests for scripts/memory-status-check.py.

Every fixture is a throwaway git repository built under a resolved temp root, with commit and tag
signing off and a fixed identity and dates. No test reads the real memory store: it holds whatever
the operator has, so a verdict that depended on it would depend on that. Each row names the
behaviour it pins.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "memory-status-check.py"
BASE_TIME = 1_700_000_000


class Out:
    """The captured outcome of one run of the tool."""

    def __init__(self, proc):
        self.rc = proc.returncode
        self.out = proc.stdout
        self.err = proc.stderr
        self.lines = proc.stdout.splitlines()

    @property
    def result(self):
        last = self.lines[-1] if self.lines else ""
        assert last.startswith("RESULT: "), (
            f"no RESULT line; stdout was:\n{self.out}\nstderr:\n{self.err}"
        )
        fields = dict(part.split("=", 1) for part in last.split()[3:] if "=" in part)
        fields["status"] = last.split()[1]
        fields["rc"] = last.split()[2].split("=", 1)[1]
        return fields

    def rows(self, prefix):
        return [ln for ln in self.lines if ln.startswith(prefix + " ")]

    @property
    def header(self):
        return next(
            (ln for ln in self.lines if ln.startswith("memory-status-check:")), ""
        )


class World:
    """A dev repo, an orphan origin/main, a production clone and two sibling projects.

    Built once per module: nothing a test does may change these repositories. A test that needs a
    different production state calls `make_production`, which clones its own.
    """

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.home = self.root / "home"
        self.cfg = self.root / "gitconfig"
        self.cfg.write_text("", encoding="utf-8")
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "GIT_CONFIG_GLOBAL": str(self.cfg),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "LC_ALL": "C",
        }
        self.dev = self.root / "dev"
        self.production = self.root / "production"
        self.other = self.root / "other"
        self.diverged = self.root / "diverged"
        self.c = {}
        self._build()

    # ---- git plumbing
    def git(self, repo, *args, when=None, check=True):
        env = dict(self.env)
        if when is not None:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{when} +0000"
        proc = subprocess.run(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "tag.gpgsign=false",
                "-c",
                "core.logAllRefUpdates=true",
                "-C",
                str(repo),
                *args,
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if check and proc.returncode != 0:
            raise AssertionError(f"git {args} failed: {proc.stderr}")
        return proc.stdout.strip()

    def commit(self, repo, files, message, when):
        for name, text in files.items():
            path = Path(repo) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", message, when=when)
        return self.git(repo, "rev-parse", "HEAD")

    def at(self, n):
        return BASE_TIME + n * 3600

    def _build(self):
        self.home.mkdir()
        self.dev.mkdir()
        self.git(self.dev, "init", "-q", "-b", "dev")
        self.c[1] = self.commit(
            self.dev,
            {"scripts/keep.txt": "k\n", ".publication.toml": "x\n", "f1": "1\n"},
            "c1",
            self.at(1),
        )
        self.git(self.root, "clone", "-q", str(self.dev), str(self.production))
        for n in (2, 3, 4, 5):
            self.c[n] = self.commit(self.dev, {f"f{n}": f"{n}\n"}, f"c{n}", self.at(n))
        # A feature line off c2: an intermediate commit, then an oracle whose tree equals c4's tree.
        self.git(self.dev, "checkout", "-q", "-b", "feat/oracle", self.c[2])
        self.intermediate = self.commit(
            self.dev, {"feat1.txt": "f\n"}, "intermediate", self.at(6)
        )
        self.git(self.dev, "checkout", "-q", self.c[4], "--", ".")
        self.git(self.dev, "rm", "-q", "feat1.txt")
        self.git(self.dev, "commit", "-q", "-m", "oracle", when=self.at(7))
        self.oracle = self.git(self.dev, "rev-parse", "HEAD")
        # A branch tip whose tree equals its own base (c3).
        self.git(self.dev, "checkout", "-q", "-b", "feat/same", self.c[3])
        self.tip_same = self.commit(self.dev, {}, "tip same tree as base", self.at(8))
        # A branch that merged dev (c3) into c2 with --no-ff; its tree equals c3's tree.
        self.git(self.dev, "checkout", "-q", "-b", "feat/merged", self.c[2])
        self.git(
            self.dev,
            "merge",
            "-q",
            "--no-ff",
            "-m",
            "merge c3",
            self.c[3],
            when=self.at(9),
        )
        self.merged = self.git(self.dev, "rev-parse", "HEAD")
        # Genuinely unlanded work: a branch tip with a tree no dev commit has.
        self.git(self.dev, "checkout", "-q", "-b", "feat/work", self.c[2])
        self.work = self.commit(
            self.dev, {"work.txt": "w\n"}, "unlanded work", self.at(10)
        )
        self.git(self.dev, "checkout", "-q", "dev")
        # origin/main: an orphan history that shares nothing with dev, with a tag on its commit.
        empty_tree = subprocess.run(
            ["git", "-C", str(self.dev), "hash-object", "-t", "tree", "-w", "--stdin"],
            input="",
            capture_output=True,
            text=True,
            env=self.env,
            check=True,
        ).stdout.strip()
        self.main1 = self.git(
            self.dev, "commit-tree", empty_tree, "-m", "main1", when=self.at(11)
        )
        self.git(self.dev, "update-ref", "refs/remotes/origin/main", self.main1)
        self.git(self.dev, "tag", "v1.0.0", self.main1)
        # The watermark is a DEV commit, not on the orphan line.
        self.git(self.dev, "update-ref", "refs/published/main", self.c[2])
        # Production: cloned at c1, fast-forwarded to c3 with a dated reflog entry.
        self.advance_production(self.c[3], self.at(20))
        # An unrelated project, and a diverged second checkout of this one (shared root).
        self.other.mkdir()
        self.git(self.other, "init", "-q", "-b", "dev")
        self.commit(
            self.other, {"o": "o\n", "scripts/o.txt": "o\n"}, "other root", self.at(1)
        )
        self.git(self.root, "clone", "-q", str(self.dev), str(self.diverged))
        self.git(self.diverged, "reset", "-q", "--hard", self.c[1])
        self.commit(self.diverged, {"d1": "d\n"}, "diverged", self.at(30))
        # The defaulted production lookup: HOME/.claude/scripts -> production/scripts.
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "scripts").symlink_to(self.production / "scripts")

    def advance_production(self, sha, when, repo=None):
        repo = repo or self.production
        self.git(repo, "fetch", "-q", "origin")
        self.git(repo, "merge", "-q", "--ff-only", sha, when=when)

    def make_production(self, name, steps):
        """Clone dev at c1 into `name`, then fast-forward through `steps` [(sha, when), ...]."""
        dest = self.root / name
        if dest.exists():
            return dest
        self.git(self.root, "clone", "-q", str(self.dev), str(dest))
        self.git(dest, "reset", "-q", "--hard", self.c[1])
        for sha, when in steps:
            self.advance_production(sha, when, repo=dest)
        return dest


class Case:
    """One test's view: the shared world plus its own memory directory."""

    def __init__(self, world, mem):
        self.world = world
        self.mem = Path(mem)
        self.mem.mkdir()

    def __getattr__(self, name):
        return getattr(self.world, name)

    def memfile(self, name, body, description="d"):
        text = f'---\nname: {name}\ndescription: "{description}"\nmetadata:\n  type: project\n---\n\n{body}\n'
        (self.mem / name).write_text(text, encoding="utf-8")

    def raw(self, name, text):
        (self.mem / name).write_text(text, encoding="utf-8")

    def s7(self, sha):
        return sha[:7]

    def run(
        self,
        *extra,
        mem=None,
        defaults=True,
        cwd=None,
        env=None,
        timeout=60,
        production=None,
        dev=None,
    ):
        """Run the tool; `production=False` omits the flag so the default lookup runs."""
        args = []
        if defaults:
            args += ["--memory-dir", str(mem or self.mem)]
            args += ["--dev", str(dev or self.dev)]
            if production is not False:
                args += ["--production", str(production or self.production)]
        proc = subprocess.run(
            [sys.executable, str(TOOL), *args, *extra],
            capture_output=True,
            text=True,
            env={**self.env, **(env or {})},
            cwd=str(cwd or self.root),
            timeout=timeout,
            check=False,
        )
        return Out(proc)


def tree_of(fx):
    return lambda sha: fx.git(fx.dev, "rev-parse", f"{sha}^{{tree}}")


def roots_of(fx):
    return lambda repo: set(fx.git(repo, "rev-list", "--max-parents=0", "HEAD").split())


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return World(tmp_path_factory.mktemp("world"))


@pytest.fixture
def fx(world, tmp_path):
    return Case(world, tmp_path / "mem")


# ---------------------------------------------------------------- fixture self-checks
def test_fixture_temp_root_is_physical(world):
    assert world.root == Path(os.path.realpath(world.root))


def test_fixture_watermark_is_a_dev_commit_and_origin_main_is_an_orphan(fx):
    wm = fx.git(fx.dev, "rev-parse", "refs/published/main")
    assert wm == fx.c[2]
    assert (
        subprocess.run(
            ["git", "-C", str(fx.dev), "merge-base", "--is-ancestor", wm, "dev"]
        ).returncode
        == 0
    )
    ok = subprocess.run(
        ["git", "-C", str(fx.dev), "merge-base", "dev", "refs/remotes/origin/main"],
        capture_output=True,
    )
    assert ok.returncode == 1, "origin/main must share no history with dev"
    assert fx.git(fx.dev, "rev-parse", "v1.0.0^{commit}") == fx.main1


def test_fixture_oracle_is_tree_equal_to_c4_without_having_it_as_an_ancestor(fx):
    tree = tree_of(fx)
    assert tree(fx.oracle) == tree(fx.c[4])
    assert (
        subprocess.run(
            [
                "git",
                "-C",
                str(fx.dev),
                "merge-base",
                "--is-ancestor",
                fx.c[4],
                fx.oracle,
            ]
        ).returncode
        == 1
    )
    assert (
        subprocess.run(
            [
                "git",
                "-C",
                str(fx.dev),
                "merge-base",
                "--is-ancestor",
                fx.intermediate,
                fx.oracle,
            ]
        ).returncode
        == 0
    )


def test_fixture_same_tree_and_merged_tips_have_their_equal_commit_as_ancestor(fx):
    tree = tree_of(fx)
    assert tree(fx.tip_same) == tree(fx.c[3])
    assert tree(fx.merged) == tree(fx.c[3])
    for tip in (fx.tip_same, fx.merged):
        assert (
            subprocess.run(
                ["git", "-C", str(fx.dev), "merge-base", "--is-ancestor", fx.c[3], tip]
            ).returncode
            == 0
        )


def test_fixture_unlanded_work_has_a_tree_no_dev_commit_has(fx):
    work_tree = fx.git(fx.dev, "rev-parse", f"{fx.work}^{{tree}}")
    dev_trees = fx.git(fx.dev, "log", "--format=%T", "dev").split()
    assert work_tree not in dev_trees


def test_fixture_production_is_at_c3_with_a_dated_reflog(fx):
    assert fx.git(fx.production, "rev-parse", "HEAD") == fx.c[3]
    reflog = fx.git(
        fx.production, "reflog", "show", "--date=unix", "--format=%H %gd", "HEAD"
    ).splitlines()
    assert len(reflog) >= 2
    assert reflog[0].startswith(fx.c[3]) and f"{{{fx.at(20)}}}" in reflog[0]


def test_fixture_diverged_checkout_shares_the_root_and_the_other_project_does_not(fx):
    roots = roots_of(fx)
    assert roots(fx.diverged) == roots(fx.dev)
    assert not roots(fx.other) & roots(fx.dev)
    assert (
        subprocess.run(
            ["git", "-C", str(fx.dev), "merge-base", "--is-ancestor", "HEAD", "dev"]
        ).returncode
        == 0
    )


def test_fixture_commits_are_unsigned_and_home_resolves_to_production(fx):
    assert "gpgsig" not in fx.git(fx.dev, "cat-file", "-p", fx.c[1])
    assert os.path.realpath(fx.home / ".claude" / "scripts") == str(
        fx.production / "scripts"
    )


# ---------------------------------------------------------------- verdict mechanics
RESULT_SHAPE = re.compile(
    r"^RESULT: (PASS|FAIL|ERROR|INCOMPLETE|NOT-APPLICABLE) rc=\d+ "
    r"checks=\d+/\d+/\d+ files=\d+ notes=\d+ outside-range=\d+$"
)


def test_a_clean_store_passes_and_the_result_line_has_the_declared_shape(fx):
    fx.memfile("a.md", "nothing to claim here.")
    out = fx.run()
    assert RESULT_SHAPE.match(out.lines[-1]), out.out
    assert out.rc == 0 and out.result["status"] == "PASS" and out.result["rc"] == "0"
    assert out.result["files"] == "1" and out.result["checks"] == "0/0/0"


def test_the_result_rc_field_equals_the_process_exit_status(fx):
    out = fx.run(mem=fx.mem / "absent")
    assert out.rc == 2 and out.result["rc"] == "2"


ERROR_CASES = [
    ("usage", {"extra": ["--only", "bogus"]}, None),
    ("memory-dir-absent", {"mem": "ABSENT"}, None),
    ("no-files", {}, "empty"),
    ("zones-empty", {}, "no-zone"),
]


@pytest.mark.parametrize("reason,kwargs,setup", ERROR_CASES)
def test_every_error_names_one_reason_line_before_the_verdict(
    fx, reason, kwargs, setup
):
    if setup == "no-zone":
        fx.raw("plain.md", "\n\n")
    kwargs = dict(kwargs)
    if kwargs.get("mem") == "ABSENT":
        kwargs["mem"] = fx.mem / "absent"
    out = fx.run(*kwargs.pop("extra", []), **kwargs)
    assert out.rc == 2 and out.result["status"] == "ERROR"
    assert out.lines[-2].startswith(f"ERROR reason={reason} detail="), out.out
    assert out.header.startswith("memory-status-check: memory="), out.out
    assert sum(1 for ln in out.lines if ln.startswith("ERROR reason=")) == 1


def test_backlog_is_excluded_by_exact_name_and_a_lookalike_is_scanned(fx):
    fx.memfile("BACKLOG.md", "x")
    assert fx.run().lines[-2].startswith("ERROR reason=no-files")
    fx.memfile("BACKLOG-notes.md", "x")
    out = fx.run()
    assert out.result["status"] == "PASS" and out.result["files"] == "1"


def test_a_directory_named_like_a_note_is_not_a_file(fx):
    fx.memfile("a.md", "x")
    (fx.mem / "dir.md").mkdir()
    assert fx.run().result["files"] == "1"


def test_a_note_with_invalid_utf8_is_read_with_replacement_not_a_crash(fx):
    fx.memfile("ok.md", "fine")
    bad = b'---\nname: bad\ndescription: "caf\xe9"\n---\n\nbody\n'
    (fx.mem / "bad.md").write_bytes(bad)
    out = fx.run()
    assert out.rc == 0 and out.result["status"] == "PASS", out.out


def test_an_unterminated_frontmatter_is_skipped_by_name_and_alone_is_an_error(fx):
    fx.raw("broken.md", '---\nname: broken\ndescription: "never closed"\n\nbody\n')
    out = fx.run()
    assert out.rc == 2
    skip = "SKIP broken.md:1 subject=topic reason=frontmatter-unterminated"
    assert skip in out.lines
    assert out.lines.index(skip) < out.lines.index(out.lines[-2])
    assert out.lines[-2].startswith("ERROR reason=zones-empty")
    fx.memfile("ok.md", "fine")
    out = fx.run()
    assert out.rc == 0 and skip in out.lines and out.result["checks"] == "0/0/1"


def test_the_description_zone_counts_even_when_the_body_is_empty(fx):
    fx.raw("d.md", '---\nname: d\ndescription: "only a description"\n---\n')
    out = fx.run()
    assert out.rc == 0 and "zones=1" in out.header


def test_a_file_that_cannot_be_read_is_incomplete_rc_3_not_a_failure(fx):
    if os.geteuid() == 0:
        pytest.skip("chmod 000 does not stop root")
    fx.memfile("a.md", "x")
    locked = fx.mem / "locked.md"
    locked.write_text("x", encoding="utf-8")
    locked.chmod(0)
    try:
        out = fx.run()
    finally:
        locked.chmod(0o644)
    assert (
        out.rc == 3 and out.result["status"] == "INCOMPLETE" and out.result["rc"] == "3"
    )
    assert "PermissionError" in out.err


def test_help_prints_usage_and_is_not_a_run(fx):
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--help"], capture_output=True, text=True
    )
    assert (
        proc.returncode == 0
        and "--memory-dir" in proc.stdout
        and "RESULT:" not in proc.stdout
    )


# ---------------------------------------------------------------- git context
NOT_MEASURABLE = "NOT-MEASURABLE class={cls} reason={reason}"


def clone_dev(fx, name):
    """A private clone of dev: it has the dev branch and .publication.toml but no watermark."""
    dest = fx.mem.parent / name
    fx.git(fx.root, "clone", "-q", str(fx.dev), str(dest))
    return dest


def shim_dir(tmp_path, body):
    """A directory holding a fake `git` that runs `body` first; `exec "$REAL_GIT"` is the fallback."""
    d = tmp_path / "shim"
    d.mkdir()
    script = d / "git"
    script.write_text(f'#!/bin/sh\n{body}\nexec "$REAL_GIT" "$@"\n', encoding="utf-8")
    script.chmod(0o755)
    return d


def shim_env(tmp_path, body):
    real = shutil.which("git")
    return {
        "PATH": f"{shim_dir(tmp_path, body)}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": real,
    }


def home_pointing_at(tmp_path, repo):
    home = tmp_path / "otherhome"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "scripts").symlink_to(Path(repo) / "scripts")
    return {"HOME": str(home)}


def test_a_directory_that_is_not_a_git_repo_is_not_applicable_never_a_pass(
    fx, tmp_path
):
    bare = tmp_path / "notarepo"
    bare.mkdir()
    out = fx.run("--memory-dir", str(fx.mem), defaults=False, cwd=bare)
    assert out.rc == 0 and out.result["status"] == "NOT-APPLICABLE"
    assert "NOT-APPLICABLE reason=dev-not-git" in out.lines
    assert out.lines[-2] == "NOT-APPLICABLE reason=dev-not-git"


def test_an_explicit_dev_that_is_not_a_repo_is_a_usage_error_not_a_skip(fx, tmp_path):
    typo = tmp_path / "typo"
    typo.mkdir()
    fx.memfile("a.md", "x")
    out = fx.run(dev=typo)
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    assert out.lines[-2].startswith("ERROR reason=usage detail=")


def test_a_repo_with_neither_watermark_nor_publication_toml_is_not_applicable(fx):
    out = fx.run(dev=fx.other)
    assert out.rc == 0 and out.result["status"] == "NOT-APPLICABLE"
    assert "NOT-APPLICABLE reason=not-adopted" in out.lines


FAILING_READS = [
    (
        "watermark",
        'case " $* " in *" rev-parse --verify -q refs/published/main "*) exit 128;; esac',
    ),
    (
        "dev-ref",
        'case " $* " in *" rev-parse --verify -q refs/heads/dev "*) exit 128;; esac',
    ),
    (
        "publication-file",
        'case " $* " in *" ls-tree --name-only refs/heads/dev "*) exit 128;; esac',
    ),
]


@pytest.mark.parametrize("name,body", FAILING_READS)
def test_a_failing_applicability_read_is_an_error_not_a_skip(fx, tmp_path, name, body):
    fx.memfile("a.md", "x")
    out = fx.run(dev=fx.other, env=shim_env(tmp_path, body))
    assert out.rc == 2 and out.result["status"] == "ERROR", (name, out.out)
    assert (
        NOT_MEASURABLE.format(
            cls="promoted,published", reason="object-store-unreadable"
        )
        in out.lines
    )


def test_a_dev_repo_whose_objects_cannot_be_read_is_an_error_not_a_skip(fx, tmp_path):
    fx.memfile("a.md", "x")
    broken = tmp_path / "broken"
    shutil.copytree(fx.other, broken)
    for obj in (broken / ".git" / "objects").rglob("*"):
        if obj.is_file():
            obj.unlink()
    out = fx.run(dev=broken)
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    assert (
        NOT_MEASURABLE.format(
            cls="promoted,published", reason="object-store-unreadable"
        )
        in out.lines
    )


def test_a_repo_with_no_dev_branch_and_no_watermark_stays_not_applicable(fx):
    fx.memfile("a.md", "x")
    clone = clone_dev(fx, "nodevbranch")
    fx.git(clone, "checkout", "-q", "-b", "trunk")
    fx.git(clone, "branch", "-q", "-D", "dev")
    out = fx.run(dev=clone)
    assert out.rc == 0 and out.result["status"] == "NOT-APPLICABLE", out.out
    assert "NOT-APPLICABLE reason=not-adopted" in out.lines


def test_publication_toml_without_a_watermark_is_a_fault_for_the_published_class(fx):
    fx.memfile("a.md", "nothing here.")
    clone = clone_dev(fx, "nowm")
    out = fx.run(dev=clone)
    assert out.rc == 2 and out.result["status"] == "ERROR"
    assert (
        NOT_MEASURABLE.format(cls="published", reason="watermark-absent") in out.lines
    )


def test_only_promoted_measures_without_a_watermark_and_says_so(fx):
    fx.memfile("a.md", "nothing here.")
    out = fx.run("--only", "promoted", dev=clone_dev(fx, "nowm"))
    assert out.rc == 0 and out.result["status"] == "PASS"
    assert "classes=promoted" in out.header and "watermark=absent" in out.header


def test_only_published_never_consults_production(fx):
    fx.memfile("a.md", "nothing here.")
    out = fx.run("--only", "published", production=fx.root / "no-such-production")
    assert out.rc == 0 and out.result["status"] == "PASS"
    assert "classes=published" in out.header and "production=absent" in out.header


def test_production_absent_not_git_and_equal_to_dev_each_name_their_reason(
    fx, tmp_path
):
    fx.memfile("a.md", "x")
    plain = tmp_path / "plain"
    plain.mkdir()
    cases = [
        (fx.root / "missing", "production-absent"),
        (plain, "production-not-git"),
        (fx.dev, "production-is-dev"),
    ]
    for path, reason in cases:
        out = fx.run(production=path)
        assert out.rc == 2, (reason, out.out)
        assert NOT_MEASURABLE.format(cls="promoted", reason=reason) in out.lines
        assert out.result["status"] == "ERROR"


def test_an_explicit_unrelated_production_is_an_error_not_a_skip(fx):
    fx.memfile("a.md", "x")
    out = fx.run(production=fx.other)
    assert out.rc == 2
    assert (
        NOT_MEASURABLE.format(cls="promoted", reason="production-unrelated")
        in out.lines
    )


def test_a_defaulted_production_of_another_lineage_is_not_this_config(fx, tmp_path):
    fx.memfile("a.md", "x")
    out = fx.run(production=False, env=home_pointing_at(tmp_path, fx.other))
    assert out.rc == 0 and out.result["status"] == "NOT-APPLICABLE"
    assert "NOT-APPLICABLE reason=not-this-config" in out.lines


def test_not_this_config_still_echoes_the_production_it_resolved(fx, tmp_path):
    fx.memfile("a.md", "x")
    out = fx.run(production=False, env=home_pointing_at(tmp_path, fx.other))
    other_head = fx.git(fx.other, "rev-parse", "HEAD")
    assert f"production={fx.other}@{other_head[:7]}" in out.header, out.out


def test_a_defaulted_diverged_checkout_of_this_project_is_still_an_error(fx, tmp_path):
    fx.memfile("a.md", "x")
    out = fx.run(production=False, env=home_pointing_at(tmp_path, fx.diverged))
    assert out.rc == 2
    assert (
        NOT_MEASURABLE.format(cls="promoted", reason="production-unrelated")
        in out.lines
    )


@pytest.mark.parametrize("which", ["dev", "other"])
def test_a_failing_lineage_read_is_an_error_not_not_this_config(fx, tmp_path, which):
    fx.memfile("a.md", "x")
    repo = fx.dev if which == "dev" else fx.other
    body = f'case " $* " in *" -C {repo} rev-list --max-parents=0 "*) exit 128;; esac'
    env = {**home_pointing_at(tmp_path, fx.other), **shim_env(tmp_path, body)}
    out = fx.run(production=False, env=env)
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    assert (
        NOT_MEASURABLE.format(cls="promoted", reason="object-store-unreadable")
        in out.lines
    )


def test_the_defaulted_production_resolves_through_home_and_is_echoed(fx):
    fx.memfile("a.md", "x")
    out = fx.run(production=False)
    assert out.rc == 0, out.out
    assert f"production={fx.production}@{fx.s7(fx.c[3])}" in out.header


def test_a_watermark_that_is_not_a_dev_ancestor_is_unrelated(fx):
    fx.memfile("a.md", "x")
    clone = clone_dev(fx, "badwm")
    empty = fx.git(clone, "hash-object", "-t", "tree", "-w", "/dev/null")
    stray = fx.git(clone, "commit-tree", empty, "-m", "stray", when=fx.at(40))
    fx.git(clone, "update-ref", "refs/published/main", stray)
    out = fx.run(dev=clone)
    assert out.rc == 2
    assert (
        NOT_MEASURABLE.format(cls="published", reason="watermark-unrelated")
        in out.lines
    )


def test_a_dev_ref_that_is_missing_is_not_measurable(fx):
    fx.memfile("a.md", "x")
    clone = clone_dev(fx, "nodevref")
    fx.git(clone, "checkout", "-q", "-b", "other", "HEAD")
    fx.git(clone, "branch", "-q", "-D", "dev")
    fx.git(clone, "update-ref", "refs/published/main", fx.c[2])
    out = fx.run(dev=clone)
    assert out.rc == 2
    assert (
        NOT_MEASURABLE.format(cls="promoted,published", reason="dev-ref-absent")
        in out.lines
    )


def test_the_header_echoes_every_parameter_actually_used(fx):
    fx.memfile("a.md", "x")
    other_production = fx.make_production("prod4", [(fx.c[4], fx.at(21))])
    out = fx.run(production=other_production)
    assert out.rc == 0, out.out
    head = out.header
    assert f"dev={fx.dev}@{fx.s7(fx.c[5])}" in head
    assert f"production={other_production}@{fx.s7(fx.c[4])}" in head
    assert f"watermark={fx.s7(fx.c[2])}" in head
    assert f"origin-main={fx.s7(fx.main1)}" in head
    assert "classes=promoted,published" in head


def test_a_default_memory_dir_is_derived_from_the_dev_repo_under_home(fx):
    mangled = re.sub(r"[^A-Za-z0-9]", "-", str(fx.dev))
    target = fx.home / ".claude" / "projects" / mangled / "memory"
    target.mkdir(parents=True)
    (target / "a.md").write_text('---\ndescription: "d"\n---\nx\n', encoding="utf-8")
    out = fx.run(defaults=False, cwd=fx.dev, env={"HOME": str(fx.home)})
    assert out.rc == 0, out.out
    assert f"memory={target}" in out.header


AMBIENT = {
    "GIT_DIR": lambda fx: str(fx.other / ".git"),
    "GIT_WORK_TREE": lambda fx: str(fx.other),
    "GIT_COMMON_DIR": lambda fx: str(fx.other / ".git"),
    "GIT_OBJECT_DIRECTORY": lambda fx: str(fx.other / ".git" / "objects"),
    "GIT_ALTERNATE_OBJECT_DIRECTORIES": lambda fx: str(fx.dev / ".git" / "objects"),
    "GIT_REPLACE_REF_BASE": lambda fx: "refs/alt/",
    "GIT_NO_REPLACE_OBJECTS": lambda fx: "1",
}


def ambient_repos(fx, tmp_path):
    """Two dev repositories whose verdict each depends on what git is allowed to read.

    `replaced` carries a replace ref turning c4 into a parentless commit, so production (c3) stops
    being an ancestor of the dev tip unless replacement is bypassed. `unreadable` lost every
    object, so it is an ERROR unless an alternate object store supplies them.
    """
    replaced = clone_dev(fx, "replaced")
    tree = fx.git(replaced, "rev-parse", f"{fx.c[4]}^{{tree}}")
    root = fx.git(
        replaced, "commit-tree", tree, "-m", "parentless copy", when=fx.at(60)
    )
    fx.git(replaced, "update-ref", f"refs/replace/{fx.c[4]}", root)
    unreadable = tmp_path / "unreadable"
    shutil.copytree(fx.dev, unreadable)
    for obj in (unreadable / ".git" / "objects").rglob("*"):
        if obj.is_file():
            obj.unlink()
    return replaced, unreadable


@pytest.mark.parametrize("name", sorted(AMBIENT))
def test_an_ambient_git_variable_does_not_change_what_the_tool_reads(
    fx, tmp_path, name
):
    """Each variable is set to a value that changes the output on one of the two repositories
    when the tool does not scrub it; removing the name from AMBIENT_GIT_ENV turns the row red.

    GIT_NAMESPACE and GIT_INDEX_FILE are scrubbed too but no read this tool makes depends on
    them, so no row can tell whether they are scrubbed.
    """
    fx.memfile("a.md", "x")
    for repo in ambient_repos(fx, tmp_path):
        base = fx.run(dev=repo)
        assert base.result["status"] == "ERROR", base.out
        leaked = fx.run(dev=repo, env={name: AMBIENT[name](fx)})
        assert leaked.lines == base.lines, (name, repo.name, leaked.out, base.out)


def test_an_object_store_that_answers_with_too_few_lines_is_not_a_pass(fx, tmp_path):
    fx.memfile("a.md", "x")
    body = 'case " $* " in *" cat-file --batch-check "*) cat >/dev/null; exit 0;; esac'
    env = shim_env(tmp_path, body)
    out = fx.run(env=env)
    assert out.rc == 2 and out.result["status"] == "ERROR"
    assert (
        NOT_MEASURABLE.format(
            cls="promoted,published", reason="object-store-unreadable"
        )
        in out.lines
    )


def test_a_control_object_that_is_not_a_commit_is_an_unreadable_store(fx, tmp_path):
    fx.memfile("a.md", "x")
    body = (
        'case " $* " in *" cat-file --batch-check "*) '
        '"$REAL_GIT" "$@" | sed \'s/ commit / blob /\'; exit 0;; esac'
    )
    out = fx.run(env=shim_env(tmp_path, body))
    assert out.rc == 2 and out.result["status"] == "ERROR"
    assert (
        NOT_MEASURABLE.format(
            cls="promoted,published", reason="object-store-unreadable"
        )
        in out.lines
    )


def test_a_git_that_outlives_the_deadline_is_not_measurable_not_partial(fx, tmp_path):
    fx.memfile("a.md", "x")
    env = shim_env(tmp_path, 'case " $* " in *" rev-list "*) sleep 20;; esac')
    env["MEMORY_STATUS_DEADLINE_SECS"] = "1"
    started = time.monotonic()
    out = fx.run(env=env, timeout=30)
    assert time.monotonic() - started < 15, "the run budget was not enforced"
    assert out.rc == 2 and out.result["status"] == "ERROR"
    assert (
        NOT_MEASURABLE.format(cls="promoted,published", reason="deadline") in out.lines
    )


def test_sigterm_during_a_run_leaves_an_incomplete_verdict_with_rc_143(fx, tmp_path):
    fx.memfile("a.md", "x")
    mark = tmp_path / "started"
    body = f'case " $* " in *" rev-list "*) : > "{mark}"; sleep 30;; esac'
    env = {**fx.env, **shim_env(tmp_path, body)}
    proc = subprocess.Popen(
        [
            sys.executable,
            str(TOOL),
            "--memory-dir",
            str(fx.mem),
            "--dev",
            str(fx.dev),
            "--production",
            str(fx.production),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        for _ in range(100):
            if mark.exists():
                break
            time.sleep(0.1)
        assert mark.exists(), "the tool never reached its git calls"
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=20)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    assert proc.returncode == 143
    assert out.splitlines()[-1].startswith("RESULT: INCOMPLETE rc=143 ")


# ---------------------------------------------------------------- range validation
def test_equal_endpoints_are_a_legal_no_op_range(fx):
    fx.memfile("a.md", "x")
    out = fx.run("--range", f"{fx.c[3]}..{fx.c[3]}")
    assert out.rc == 0 and out.result["status"] == "PASS", out.out
    assert f"range={fx.s7(fx.c[3])}..{fx.s7(fx.c[3])} range-commits=0" in out.header


def test_a_valid_range_reports_how_many_commits_it_holds(fx):
    fx.memfile("a.md", "x")
    out = fx.run("--range", f"{fx.c[2]}..{fx.c[5]}")
    assert out.rc == 0, out.out
    assert f"range={fx.s7(fx.c[2])}..{fx.s7(fx.c[5])} range-commits=3" in out.header


def test_no_range_says_none(fx):
    fx.memfile("a.md", "x")
    assert "range=none" in fx.run().header


@pytest.mark.parametrize(
    "text",
    [
        "..HEAD",
        "HEAD..",
        "no-such-ref..HEAD",
        "HEAD~1..HEAD",
        "C4..C2",
        "ORACLE..HEAD",
        "C2...C4",
        "C2",
        "C2..C3..C4",
    ],
)
def test_a_malformed_reversed_or_diverged_range_is_range_invalid(fx, text):
    fx.memfile("a.md", "x")
    spelled = (
        text.replace("C2", fx.c[2])
        .replace("C3", fx.c[3])
        .replace("C4", fx.c[4])
        .replace("ORACLE", fx.oracle)
    )
    out = fx.run("--range", spelled)
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    assert out.lines[-2].startswith("ERROR reason=range-invalid detail="), out.out
    assert sum(1 for ln in out.lines if ln.startswith("ERROR reason=")) == 1


def resolves_as_a_commit(fx, repo, name):
    """True when git in `repo` peels `name` to a commit (the exit status is the answer)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "-q", f"{name}^{{commit}}"],
        capture_output=True,
        env=fx.env,
        check=False,
    )
    return proc.returncode == 0


def unresolvable_ends(fx, tmp_path):
    """Hex-valid ends that dev cannot peel to a commit, each paired with a valid dev commit."""
    absent = "deadbee"
    tree = fx.git(fx.dev, "rev-parse", f"{fx.c[3]}^{{tree}}")
    blob = fx.git(fx.dev, "rev-parse", f"{fx.c[3]}:f3")
    prod = tmp_path / "prod-only"
    fx.git(fx.root, "clone", "-q", str(fx.production), str(prod))
    only = fx.commit(prod, {"prod-only": "x\n"}, "production only", fx.at(70))
    assert resolves_as_a_commit(fx, prod, only)
    return {
        "absent-right": (f"{fx.c[3]}..{absent}", absent),
        "absent-left": (f"{absent}..{fx.c[3]}", absent),
        "absent-both-equal": (f"{absent}..{absent}", absent),
        "tree-right": (f"{fx.c[2]}..{tree}", tree),
        "tree-left": (f"{tree}..{fx.c[4]}", tree),
        "blob-right": (f"{fx.c[2]}..{blob}", blob),
        "production-only-right": (f"{fx.c[3]}..{only}", only),
        "production-only-left": (f"{only}..{fx.c[5]}", only),
    }


@pytest.mark.parametrize(
    "case",
    [
        "absent-right",
        "absent-left",
        "absent-both-equal",
        "tree-right",
        "tree-left",
        "blob-right",
        "production-only-right",
        "production-only-left",
    ],
)
def test_a_hex_range_end_that_is_not_a_commit_in_dev_is_range_invalid(
    fx, tmp_path, case
):
    """The end passes the name rule, so only the commit lookup in dev can refuse it."""
    fx.memfile("a.md", "x")
    spelled, bad = unresolvable_ends(fx, tmp_path)[case]
    assert not resolves_as_a_commit(fx, fx.dev, bad), case
    out = fx.run("--range", spelled)
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    errors = [ln for ln in out.lines if ln.startswith("ERROR reason=")]
    assert len(errors) == 1, out.out
    assert errors[0].startswith("ERROR reason=range-invalid detail="), out.out
    assert "is not a commit in the dev repo" in errors[0], out.out


@pytest.mark.parametrize("case", ["uppercase-end", "forty-one-digit-end"])
def test_an_uppercase_or_overlong_range_end_is_refused_as_a_name(fx, case):
    fx.memfile("a.md", "x")
    end = fx.c[4].upper() if case == "uppercase-end" else fx.c[4] + "0"
    out = fx.run("--range", f"{fx.c[2]}..{end}")
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    errors = [ln for ln in out.lines if ln.startswith("ERROR reason=")]
    assert len(errors) == 1, out.out
    assert errors[0].startswith("ERROR reason=range-invalid detail="), out.out
    assert "must be commit ids, not names" in errors[0], out.out


# ---------------------------------------------------------------- zones, claims, simple evidence
def sha7(fx, n):
    return fx.s7(fx.c[n])


def stale_rows(out):
    return out.rows("STALE")


def test_a_not_promoted_claim_whose_commit_production_has_is_stale(fx):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    out = fx.run()
    assert out.rc == 1 and out.result["status"] == "FAIL", out.out
    (row,) = stale_rows(out)
    assert row.startswith("STALE rec.md:8 subject=topic zone=lead claim=not-promoted ")
    assert f"sha={sha7(fx, 3)}" in row and f"attained=production@{sha7(fx, 3)}" in row
    assert out.result["checks"] == "0/1/0"
    assert any(ln.startswith("  text: ") for ln in out.lines)
    assert any(ln.startswith("  fix: ") for ln in out.lines)


def test_a_commit_production_lacks_keeps_the_claim_live(fx):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 4)}`, NOT promoted.")
    out = fx.run()
    assert out.rc == 0 and out.result["checks"] == "1/0/0", out.out
    assert not stale_rows(out)


def test_a_mixed_paragraph_is_never_called_stale(fx):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}` and `{sha7(fx, 4)}`, NOT promoted.")
    out = fx.run()
    assert out.rc == 0 and out.result["checks"] == "1/0/0"


def test_the_two_claim_classes_are_judged_against_different_refs(fx):
    # c3 is in production but not in the watermark: stale for "not promoted", live for "not published".
    fx.memfile("promo.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    fx.memfile("publ.md", f"Landed `{sha7(fx, 3)}`, NOT published.")
    out = fx.run()
    assert [r.split()[1] for r in stale_rows(out)] == ["promo.md:8"]
    fx.memfile("publ.md", f"Landed `{sha7(fx, 2)}`, NOT published.")
    out = fx.run()
    assert sorted(r.split()[1] for r in stale_rows(out)) == ["promo.md:8", "publ.md:8"]


def test_both_classes_in_one_paragraph_name_both(fx):
    fx.memfile("both.md", f"Landed `{sha7(fx, 2)}`, NOT promoted, NOT published.")
    (row,) = stale_rows(fx.run())
    assert " claim=not-promoted,not-published " in row


PHRASES = [
    ("not promoted", 3),
    ("Not Promoted", 3),
    ("NOT PROMOTED", 3),
    ("UNPROMOTED", 3),
    ("Not yet promoted", 3),
    ("NOT propagated", 3),
    ("not-published", 2),
    ("UNPUBLISHED", 2),
    ("not pushed", 2),
    ("unpushed", 2),
]


@pytest.mark.parametrize("phrase,n", PHRASES)
def test_every_spelling_of_a_claim_is_found_case_insensitively(fx, phrase, n):
    fx.memfile("rec.md", f"Landed `{sha7(fx, n)}`; {phrase}.")
    assert len(stale_rows(fx.run())) == 1


def test_a_full_length_sha_and_a_pure_digit_abbreviation_are_both_evidence(fx):
    fx.memfile("full.md", f"Landed `{fx.c[3]}`, NOT promoted.")
    assert len(stale_rows(fx.run())) == 1
    clone = clone_dev(fx, "digits")
    for i in range(400):
        fx.git(
            clone, "commit", "-q", "--allow-empty", "-m", f"d{i}", when=fx.at(50 + i)
        )
        sha = fx.git(clone, "rev-parse", "HEAD")
        if sha[:7].isdigit():
            break
    else:
        pytest.fail("no all-digit abbreviation in 400 commits")
    fx.git(clone, "update-ref", "refs/published/main", sha)
    fx.memfile("full.md", f"Landed `{sha[:7]}`, NOT published.")
    out = fx.run("--only", "published", dev=clone)
    assert len(stale_rows(out)) == 1, out.out


def test_the_claim_line_is_reported_not_the_paragraph_start(fx):
    fx.memfile("rec.md", f"The work is `{sha7(fx, 3)}`.\nSecond line: NOT promoted.")
    (row,) = stale_rows(fx.run())
    assert row.startswith("STALE rec.md:9 ")


def test_a_claim_without_any_commit_is_skipped_by_name_never_passed_silently(fx):
    fx.memfile("rec.md", "Landed, NOT promoted.")
    out = fx.run()
    assert out.rc == 0 and out.result["checks"] == "0/0/1"
    assert "SKIP rec.md:8 subject=topic reason=no-landed-sha" in out.lines


def test_only_restricts_the_classes_judged(fx):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 2)}`, NOT promoted, NOT published.")
    (row,) = stale_rows(fx.run("--only", "published"))
    assert " claim=not-published " in row


def test_all_prints_live_rows_too(fx):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 4)}`, NOT promoted.")
    assert fx.run().rows("LIVE") == []
    (row,) = fx.run("--all").rows("LIVE")
    assert row.startswith("LIVE rec.md:8 subject=topic zone=lead ")


# ---- zones
CLAIM = "NOT promoted"


def claim_text(fx):
    return f"`{sha7(fx, 3)}` {CLAIM}"


def test_the_description_line_is_a_zone_and_its_yaml_quotes_are_unwrapped(fx):
    fx.memfile("d.md", "nothing", description=f"Landed {claim_text(fx)}.")
    (row,) = stale_rows(fx.run())
    assert row.startswith("STALE d.md:3 subject=topic zone=description ")


def test_a_leading_heading_is_skipped_to_reach_the_lead_paragraph(fx):
    fx.memfile("h.md", f"# Title\n\nLanded {claim_text(fx)}.")
    (row,) = stale_rows(fx.run())
    assert row.startswith("STALE h.md:10 subject=topic zone=lead ")


def test_a_status_paragraph_is_a_zone_when_it_is_not_the_lead(fx):
    fx.memfile(
        "s.md",
        f"Intro paragraph with no claim.\n\nStatus 2026-10-01: {claim_text(fx)}.",
    )
    (row,) = stale_rows(fx.run())
    assert row.startswith("STALE s.md:10 subject=topic zone=status ")


def test_the_same_claim_outside_every_zone_is_not_reported(fx):
    later = "\n\n".join(["filler paragraph"] * 3)
    fx.memfile(
        "n.md",
        f"Intro, no claim.\n\n{later}\n\n- 2026-09-01 dated bullet: {claim_text(fx)}.",
    )
    assert not stale_rows(fx.run())
    deep = "\n".join(["filler"] * 60)
    fx.memfile("deep.md", f"Intro.\n\n{deep}\n\nStatus: {claim_text(fx)}.")
    assert not any("deep.md" in r for r in stale_rows(fx.run()))


def test_backlog_md_is_never_scanned_but_a_lookalike_is(fx):
    fx.memfile("ok.md", "clean")
    fx.memfile("BACKLOG.md", f"Landed {claim_text(fx)}.")
    assert fx.run().rc == 0
    fx.memfile("BACKLOG-notes.md", f"Landed {claim_text(fx)}.")
    assert fx.run().rc == 1


def test_a_dashes_line_in_the_body_is_not_a_frontmatter_edge(fx):
    fx.memfile("hr.md", f"Landed {claim_text(fx)}.\n\n---\n\nmore")
    assert len(stale_rows(fx.run())) == 1


def test_crlf_line_endings_are_judged_like_lf(fx):
    text = f'---\r\nname: c\r\ndescription: "d"\r\n---\r\n\r\nLanded `{sha7(fx, 3)}`, NOT promoted.\r\n'
    (fx.mem / "crlf.md").write_bytes(text.encode())
    (row,) = stale_rows(fx.run())
    assert row.startswith("STALE crlf.md:6 subject=topic zone=lead ")


def test_a_note_without_frontmatter_still_has_a_lead_paragraph(fx):
    fx.raw("bare.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.\n")
    (row,) = stale_rows(fx.run())
    assert row.startswith("STALE bare.md:1 subject=topic zone=lead ")


def test_a_claim_hard_wrapped_across_lines_or_spaced_twice_is_found(fx):
    fx.memfile("wrap.md", f"Landed `{sha7(fx, 3)}`; **Not\npromoted to production**.")
    (row,) = stale_rows(fx.run())
    assert row.startswith("STALE wrap.md:8 subject=topic zone=lead ")
    fx.memfile("wrap.md", f"Landed `{sha7(fx, 3)}`, NOT  promoted.")
    assert len(stale_rows(fx.run())) == 1


# ---------------------------------------------------------------- exclusions and the NOTE row
def test_a_link_target_or_wikilink_naming_a_claim_is_not_a_claim(fx):
    fx.memfile(
        "link.md",
        f"See [the plan](2026-08-13-publish-built-not-pushed.md) and [[not-published]]; "
        f"landed `{sha7(fx, 3)}`.",
    )
    out = fx.run()
    assert out.rc == 0 and out.result["checks"] == "0/0/0", out.out
    fx.memfile(
        "link.md",
        f"See [the plan](x.md); landed `{sha7(fx, 3)}`, NOT promoted.",
    )
    assert len(stale_rows(fx.run())) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Landed `{c2}`; 0 unpublished.",
        "Landed `{c2}`; 5 unpublished bricks.",
        "Watermark now `{c2}`, 0 unpublished.",
        "Landed `{c2}`; the preflight recovery deletes unpushed tags.",
    ],
)
def test_a_count_or_a_noun_phrase_is_not_a_claim(fx, text):
    fx.memfile("rec.md", text.format(c2=sha7(fx, 2)))
    out = fx.run()
    assert out.rc == 0 and out.result["checks"] == "0/0/0", out.out


@pytest.mark.parametrize(
    "text",
    [
        "Landed `{c2}`). Still UNPUBLISHED.",
        "Landed `{c2}`, postcheck 8/0/0); NOT published.",
        "Landed `{c2}`, checks=20/0/0. NOT published.",
        "PROMOTED {c2}, not published.",
        "Landed `{c2}`, 2026-09-18 — NOT published.",
        "Landed `{c2}`; 42 commits sit unpublished.",
        "Landed `{c2}`; dev has unpushed commits.",
    ],
)
def test_digit_bearing_neighbours_do_not_make_a_claim_a_count(fx, text):
    fx.memfile("rec.md", text.format(c2=sha7(fx, 2)))
    assert len(stale_rows(fx.run())) == 1


@pytest.mark.parametrize(
    "wrap",
    ['"{x}"', "“{x}”", "'{x}'", "`{x}`"],
)
def test_a_claim_inside_quotes_or_backticks_is_a_mention_not_a_claim(fx, wrap):
    fx.memfile(
        "q.md",
        f"Landed `{sha7(fx, 3)}`; the check reads {wrap.format(x='NOT promoted')} here.",
    )
    out = fx.run()
    assert out.rc == 0 and out.result["checks"] == "0/0/0", out.out


def test_apostrophes_in_prose_do_not_open_a_quotation(fx):
    fx.memfile(
        "ap.md", f"Jason's note: landed `{sha7(fx, 3)}`, NOT promoted; it's stale."
    )
    assert len(stale_rows(fx.run())) == 1


def test_an_anchored_claim_with_attained_evidence_is_a_note_not_a_failure(fx):
    fx.memfile("a1.md", f"Landed `{sha7(fx, 3)}`, NOT promoted at the time of writing.")
    fx.memfile("a2.md", f"Landed `{sha7(fx, 3)}`, NOT promoted as of 2026-10-17.")
    out = fx.run()
    assert out.rc == 0 and out.result["status"] == "PASS", out.out
    assert out.result["notes"] == "2" and out.result["checks"] == "0/0/0"
    assert sorted(out.rows("NOTE")) == [
        f"NOTE a1.md:8 anchored-claim attained=production@{sha7(fx, 3)}",
        f"NOTE a2.md:8 anchored-claim attained=production@{sha7(fx, 3)}",
    ]


def test_an_anchored_claim_whose_evidence_is_not_attained_prints_nothing(fx):
    fx.memfile("a.md", f"Landed `{sha7(fx, 4)}`, NOT promoted as of 2026-10-17.")
    out = fx.run()
    assert out.result["notes"] == "0" and not out.rows("NOTE")


def test_an_anchor_beside_another_class_does_not_silence_the_live_claim(fx):
    filler = "x" * 150
    fx.memfile(
        "m.md",
        f"Landed `{sha7(fx, 2)}`, NOT promoted as of 2026-10-17. {filler} NOT published.",
    )
    out = fx.run()
    (row,) = stale_rows(out)
    assert " claim=not-published " in row and out.result["notes"] == "1"


# ---------------------------------------------------------------- the evidence rule
def prod_at_c3(fx):
    return fx.production


def prod_at_c4(fx):
    return fx.make_production("prod4", [(fx.c[4], fx.at(21))])


def verdict_of(out, name="rec.md"):
    """STALE, LIVE or SKIP:<reason> for the one unit of `name`, read with --all."""
    stale = [r for r in out.rows("STALE") if r.split()[1].startswith(name)]
    live = [r for r in out.rows("LIVE") if r.split()[1].startswith(name)]
    skip = [r for r in out.rows("SKIP") if r.split()[1].startswith(name)]
    assert len(stale) + len(live) + len(skip) == 1, out.out
    if stale:
        return "STALE"
    if live:
        return "LIVE"
    return "SKIP:" + skip[0].rsplit("reason=", 1)[1]


def judged(fx, text, *extra, **kw):
    fx.memfile("rec.md", text)
    return verdict_of(fx.run("--all", *extra, **kw))


def test_unlanded_work_beside_an_unmarked_dev_commit_keeps_the_claim_live(fx):
    text = f"Work `{fx.s7(fx.work)}` on top of `{sha7(fx, 2)}`, NOT promoted."
    assert judged(fx, text) == "LIVE"


def test_a_marked_base_is_context_and_the_unlanded_tip_still_decides(fx):
    text = f"Tip `{fx.s7(fx.work)}` (base dev `{sha7(fx, 2)}`), NOT promoted."
    assert judged(fx, text) == "LIVE"


def test_a_paragraph_naming_only_a_base_is_skipped_as_context_only(fx):
    assert (
        judged(fx, f"Cut from dev `{sha7(fx, 3)}`, NOT promoted.")
        == "SKIP:context-only"
    )
    assert (
        judged(fx, f"Branch based on `{sha7(fx, 3)}`; NOT promoted.")
        == "SKIP:context-only"
    )


def test_only_the_first_commit_after_a_base_marker_is_context(fx):
    text = (
        f"Cut from dev `{sha7(fx, 3)}` and now at tip `{fx.s7(fx.work)}`, NOT promoted."
    )
    assert judged(fx, text) == "LIVE"


def test_the_left_side_of_a_range_is_context(fx):
    both = f"dev = `{sha7(fx, 1)}`..`{sha7(fx, 3)}`, NOT promoted."
    assert judged(fx, both) == "STALE"
    out_range = f"dev = `{sha7(fx, 3)}`..`{sha7(fx, 4)}`, NOT promoted."
    assert judged(fx, out_range) == "LIVE"
    only_base = f"dev = `{sha7(fx, 3)}`..HEAD, NOT promoted."
    assert judged(fx, only_base) == "SKIP:context-only"


def test_an_oracle_tree_equal_to_a_dev_commit_counts_as_that_commit(fx):
    prod4 = prod_at_c4(fx)
    assert (
        judged(fx, f"Oracle `{fx.s7(fx.oracle)}`, NOT promoted.", production=prod4)
        == "STALE"
    )
    assert judged(fx, f"Oracle `{fx.s7(fx.oracle)}`, NOT promoted.") == "LIVE"


def test_a_pre_rebase_commit_under_the_oracle_is_subsumed(fx):
    prod4 = prod_at_c4(fx)
    text = (
        f"Branch `{fx.s7(fx.intermediate)}`, oracle `{fx.s7(fx.oracle)}`, NOT promoted."
    )
    assert judged(fx, text, production=prod4) == "STALE"


def test_a_pre_rebase_commit_whose_oracle_is_not_cited_vetoes(fx):
    prod4 = prod_at_c4(fx)
    text = f"Branch `{fx.s7(fx.intermediate)}`, NOT promoted."
    assert judged(fx, text, production=prod4) == "LIVE"


def test_a_tip_whose_tree_equals_its_own_base_is_not_equivalent(fx):
    assert judged(fx, f"Tip `{fx.s7(fx.tip_same)}`, NOT promoted.") == "LIVE"


def test_a_branch_that_merged_dev_then_reverted_is_not_equivalent(fx):
    assert judged(fx, f"Tip `{fx.s7(fx.merged)}`, NOT promoted.") == "LIVE"


def test_a_main_side_commit_attains_published_and_is_ignored_for_promoted(fx):
    assert judged(fx, f"Shipped `{fx.s7(fx.main1)}`, NOT published.") == "STALE"
    assert (
        judged(fx, f"Shipped `{fx.s7(fx.main1)}`, NOT promoted.")
        == "SKIP:no-landed-sha"
    )


def test_a_tree_or_blob_token_is_ignored_not_read_as_a_missing_commit(fx):
    tree = fx.git(fx.dev, "rev-parse", f"{fx.c[3]}^{{tree}}")[:7]
    assert (
        judged(fx, f"Landed `{sha7(fx, 3)}`, tree `{tree}`, NOT promoted.") == "STALE"
    )


def test_a_backticked_missing_sha_vetoes_and_alone_is_unresolved(fx):
    assert judged(fx, f"Landed `{sha7(fx, 3)}` and `deadbee`, NOT promoted.") == "LIVE"
    assert judged(fx, "Landed `deadbee`, NOT promoted.") == "SKIP:sha-unresolved"
    assert judged(fx, f"Landed `{sha7(fx, 3)}`, deadbee, NOT promoted.") == "STALE"


def test_an_ambiguous_abbreviation_is_treated_like_a_missing_one(fx):
    clone = clone_dev(fx, "ambiguous")
    seen = {}
    for i in range(200_000):
        body = f"{i}\n".encode()
        sha = hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest()
        if sha[:7] in seen:
            pair = (seen[sha[:7]], body)
            prefix = sha[:7]
            break
        seen[sha[:7]] = body
    else:
        pytest.fail("no 7-digit collision found")
    for body in pair:
        subprocess.run(
            ["git", "-C", str(clone), "hash-object", "-w", "--stdin"],
            input=body,
            capture_output=True,
            env=fx.env,
            check=True,
        )
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}` and `{prefix}`, NOT promoted.")
    out = fx.run("--all", "--only", "promoted", dev=clone)
    assert verdict_of(out) == "LIVE", out.out


def test_a_tag_on_origin_main_attains_published_only(fx):
    assert judged(fx, "Shipped `v1.0.0`, NOT published.") == "STALE"
    assert (
        judged(fx, "Shipped `v1.0.0`, NOT promoted.")
        == "SKIP:tag-cannot-decide-promotion"
    )
    clone = clone_dev(fx, "notag")
    fx.git(clone, "tag", "v9.9.9", fx.c[1])
    fx.git(clone, "update-ref", "refs/published/main", fx.c[2])
    fx.memfile("rec.md", "Shipped `v9.9.9`, NOT published.")
    out = fx.run("--all", "--only", "published", dev=clone)
    assert verdict_of(out) == "SKIP:no-landed-sha", out.out


def test_the_verdict_moves_as_the_refs_move(fx):
    """One paragraph, three rewound states: not yet landed, landed but production behind, promoted."""
    clone = clone_dev(fx, "rolling")
    fx.git(clone, "reset", "-q", "--hard", fx.c[3])
    fx.memfile("rec.md", f"Landed `{sha7(fx, 4)}`, NOT promoted.")
    states = []
    out = fx.run("--all", "--only", "promoted", dev=clone, production=prod_at_c3(fx))
    states.append(verdict_of(out))
    fx.git(clone, "update-ref", "refs/heads/dev", fx.c[5])
    out = fx.run("--all", "--only", "promoted", dev=clone, production=prod_at_c3(fx))
    states.append(verdict_of(out))
    out = fx.run("--all", "--only", "promoted", dev=clone, production=prod_at_c4(fx))
    states.append(verdict_of(out))
    assert states == ["LIVE", "LIVE", "STALE"]


# ---------------------------------------------------------------- the index as a subject
def index(fx, *entries, header="# Memory Index\n\nOne line per entry.\n"):
    fx.raw("MEMORY.md", header + "\n" + "\n".join(entries) + "\n")


def topic(fx, name, n):
    fx.memfile(name, f"Landed `{sha7(fx, n)}`.")


def index_rows(out):
    return [r for r in out.rows("STALE") if "subject=index" in r]


def test_an_index_line_with_a_falsified_negative_claim_is_stale_on_its_own(fx):
    topic(fx, "t.md", 3)
    index(fx, "- [Topic](t.md) — NOT promoted yet; open it before touching X.")
    out = fx.run()
    (row,) = out.rows("STALE")
    assert row.startswith(
        "STALE MEMORY.md:5 subject=index zone=index claim=not-promoted "
    )
    assert f"sha={sha7(fx, 3)}" in row
    assert out.rc == 1


def test_index_and_topic_are_independent_subjects(fx):
    fx.memfile("t.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    index(fx, "- [Topic](t.md) — NOT promoted yet.")
    out = fx.run()
    assert sorted(r.split()[1].split(":")[0] for r in out.rows("STALE")) == [
        "MEMORY.md",
        "t.md",
    ]
    fx.memfile("t.md", f"Landed `{sha7(fx, 3)}`, promoted.")
    out = fx.run()
    assert [r.split()[1].split(":")[0] for r in out.rows("STALE")] == ["MEMORY.md"]
    index(fx, "- [Topic](t.md) — open it.")
    fx.memfile("t.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    assert [r.split()[1].split(":")[0] for r in fx.run().rows("STALE")] == ["t.md"]


def test_a_landed_label_is_a_stale_ceiling_once_production_has_the_commits(fx):
    index(fx, "- [Topic](t.md) — **LANDED — the cap.** Opt in.")
    topic(fx, "t.md", 3)
    (row,) = index_rows(fx.run())
    assert " claim=label-landed " in row and f"attained=production@{sha7(fx, 3)}" in row
    topic(fx, "t.md", 4)
    assert not index_rows(fx.run())


def test_a_promoted_label_is_a_stale_ceiling_once_the_watermark_has_the_commits(fx):
    index(fx, "- [Topic](t.md) — **PROMOTED.** Opt in.")
    topic(fx, "t.md", 3)
    assert not index_rows(fx.run())
    topic(fx, "t.md", 2)
    (row,) = index_rows(fx.run())
    assert (
        " claim=label-promoted " in row and f"attained=watermark@{sha7(fx, 2)}" in row
    )


def test_a_published_label_is_the_top_rung_and_never_reported(fx):
    index(fx, "- [Topic](t.md) — **PUBLISHED v0.1.0.** Opt in.")
    topic(fx, "t.md", 2)
    assert fx.run().rc == 0


@pytest.mark.parametrize(
    "line",
    [
        "- [Topic](t.md) — LANDED on dev 2026-10-16; open it.",
        "- [Topic](t.md) — **PROMOTED backlog entries first**, then derive.",
        "- [Topic](t.md) — **LANDED — cap.** PROMOTED to production later.",
        "- [Topic](t.md) — **LANDED** is not a label here unless punctuated.",
    ],
)
def test_text_that_is_not_a_stored_status_label_is_not_reported(fx, line):
    index(fx, line)
    topic(fx, "t.md", 3)
    assert not index_rows(fx.run())


def test_an_entry_continuation_line_belongs_to_its_entry(fx):
    index(fx, "- [Topic](t.md) — open it before X.", "  **LANDED — the cap.** Opt in.")
    topic(fx, "t.md", 3)
    (row,) = index_rows(fx.run())
    assert row.startswith("STALE MEMORY.md:6 ")


def test_the_index_fix_is_a_deletion_and_never_a_replacement_label(fx):
    index(fx, "- [Topic](t.md) — **LANDED — the cap.** Opt in.")
    topic(fx, "t.md", 3)
    out = fx.run()
    (fix,) = [ln for ln in out.lines if ln.startswith("  fix: ")]
    assert fix.startswith("  fix: delete the label ") and "state " not in fix


def test_an_index_line_whose_topic_file_is_missing_is_skipped_by_name(fx):
    index(fx, "- [Topic](gone.md) — NOT promoted.")
    fx.memfile("other.md", "clean")
    out = fx.run()
    assert "SKIP MEMORY.md:5 subject=index reason=no-landed-sha" in out.lines


def test_a_link_target_naming_a_claim_is_not_an_index_claim(fx):
    index(
        fx,
        "- [The publish](2026-08-13-publish-built-not-pushed.md) — fold planner invalid.",
    )
    fx.memfile("2026-08-13-publish-built-not-pushed.md", f"Landed `{sha7(fx, 3)}`.")
    assert fx.run().result["checks"] == "0/0/0"


def test_only_bullets_are_index_entries_not_the_header_prose(fx):
    index(
        fx,
        "- [Topic](t.md) — fine.",
        header=f"# Index\n\nOnce `{sha7(fx, 3)}` NOT promoted was written here.\n",
    )
    topic(fx, "t.md", 3)
    assert fx.run().rc == 0


def test_the_index_alone_is_a_store_with_zones(fx):
    index(fx, "- [Topic](t.md) — fine.")
    out = fx.run()
    assert out.rc == 0 and out.result["files"] == "1" and "zones=1" in out.header


def test_an_index_does_not_mask_a_topic_parser_that_found_nothing(fx):
    index(fx, "- [Topic](t.md) — fine.")
    fx.raw("empty.md", "\n\n")
    out = fx.run()
    assert out.rc == 2 and out.lines[-2].startswith("ERROR reason=zones-empty"), out.out


# ---------------------------------------------------------------- --range listing and --since
def two_stale_records(fx):
    fx.memfile("old.md", f"Landed `{sha7(fx, 1)}`, NOT promoted.")
    fx.memfile("new.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")


def test_a_ranged_run_lists_only_units_whose_commits_are_in_the_range(fx):
    two_stale_records(fx)
    out = fx.run("--range", f"{fx.c[2]}..{fx.c[3]}")
    assert [r.split()[1] for r in out.rows("STALE")] == ["new.md:8"]
    assert out.rc == 1 and out.result["outside-range"] == "1"
    assert out.result["checks"] == "0/1/0"


def test_the_listed_set_plus_the_outside_count_equals_the_unranged_total(fx):
    two_stale_records(fx)
    total = {r.split()[1] for r in fx.run().rows("STALE")}
    ranged = fx.run("--range", f"{fx.c[2]}..{fx.c[3]}")
    listed = {r.split()[1] for r in ranged.rows("STALE")}
    assert listed < total
    assert len(listed) + int(ranged.result["outside-range"]) == len(total)


def test_a_ranged_run_whose_only_stale_units_are_outside_is_a_pass(fx):
    two_stale_records(fx)
    ranged = fx.run("--range", f"{fx.c[3]}..{fx.c[3]}")
    assert ranged.rc == 0 and ranged.result["status"] == "PASS", ranged.out
    assert ranged.result["outside-range"] == "2" and not ranged.rows("STALE")
    assert fx.run().rc == 1


def test_an_oracle_unit_is_in_range_by_the_commit_it_is_equivalent_to(fx):
    fx.memfile("rec.md", f"Oracle `{fx.s7(fx.oracle)}`, NOT promoted.")
    prod4 = prod_at_c4(fx)
    inside = fx.run("--range", f"{fx.c[3]}..{fx.c[4]}", production=prod4)
    assert len(inside.rows("STALE")) == 1, inside.out
    outside = fx.run("--range", f"{fx.c[4]}..{fx.c[5]}", production=prod4)
    assert not outside.rows("STALE") and outside.result["outside-range"] == "1"


def test_stale_before_since_counts_promotes_that_predate_the_session(fx):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    assert "stale-before-since" not in fx.run().header
    later = fx.run("--since", "20300101T000000Z")
    assert "stale-before-since=1 scope=promoted" in later.header
    earlier = fx.run("--since", "2020-01-01T00:00:00Z")
    assert "stale-before-since=0 scope=promoted" in earlier.header
    assert later.rc == 1 and earlier.rc == 1


def test_the_published_class_is_not_counted_by_since(fx):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 2)}`, NOT published.")
    out = fx.run("--since", "20300101T000000Z")
    assert out.rc == 1 and "stale-before-since=0 scope=promoted" in out.header


def test_since_is_unknown_when_production_has_no_reflog_or_is_not_judged(fx):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    bare = fx.make_production("noreflog", [(fx.c[3], fx.at(22))])
    shutil.rmtree(bare / ".git" / "logs", ignore_errors=True)
    out = fx.run("--since", "20300101T000000Z", production=bare)
    assert "stale-before-since=unknown scope=promoted" in out.header
    out = fx.run("--since", "20300101T000000Z", "--only", "published")
    assert "stale-before-since=unknown scope=promoted" in out.header


def test_a_malformed_since_is_a_usage_error(fx):
    fx.memfile("rec.md", "x")
    out = fx.run("--since", "yesterday")
    assert out.rc == 2 and out.lines[-2].startswith("ERROR reason=usage detail=")


def test_note_rows_are_ranged_like_stale_rows(fx):
    fx.memfile("a.md", f"Landed `{sha7(fx, 3)}`, NOT promoted as of 2026-10-17.")
    empty = fx.run("--range", f"{fx.c[3]}..{fx.c[3]}")
    assert empty.result["notes"] == "0" and not empty.rows("NOTE"), empty.out
    assert fx.run().result["notes"] == "1"


def test_a_git_fault_while_timing_the_promote_leaves_since_unknown_not_an_error(
    fx, tmp_path
):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    env = shim_env(tmp_path, 'case " $* " in *" reflog "*) sleep 8;; esac')
    env["MEMORY_STATUS_DEADLINE_SECS"] = "4"
    out = fx.run("--since", "20300101T000000Z", env=env, timeout=30)
    assert out.rc == 1 and out.result["status"] == "FAIL", out.out
    assert "stale-before-since=unknown scope=promoted" in out.header


# ---------------------------------------------------------------- read-only, cost, process exit
def tree_digest(root):
    """Names, sizes, modes and contents of everything under `root`."""
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        info = path.lstat()
        digest.update(
            f"{path.relative_to(root)}|{info.st_size}|{info.st_mode}|".encode()
        )
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_a_run_changes_nothing_in_the_store_or_the_working_directory(fx, tmp_path):
    fx.memfile("old.md", f"Landed `{sha7(fx, 1)}`, NOT promoted.")
    fx.memfile("new.md", f"Landed `{sha7(fx, 3)}`, NOT promoted. as of 2026-10-17 x")
    index(fx, "- [New](new.md) — **LANDED — cap.** open.")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    before = (tree_digest(fx.mem), tree_digest(cwd))
    for extra in (
        [],
        ["--all"],
        ["--range", f"{fx.c[2]}..{fx.c[3]}"],
        ["--since", "20300101T000000Z"],
    ):
        out = fx.run(*extra, cwd=cwd)
        assert out.rc == 1, out.out
    assert (tree_digest(fx.mem), tree_digest(cwd)) == before
    assert not list(fx.mem.rglob("__pycache__"))


def test_a_run_with_nothing_readable_on_stdout_exits_3_not_as_a_verdict(fx):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--memory-dir",
                str(fx.mem),
                "--dev",
                str(fx.dev),
                "--production",
                str(fx.production),
            ],
            stdout=write_end,
            stderr=subprocess.PIPE,
            text=True,
            env=fx.env,
            check=False,
        )
    finally:
        os.close(write_end)
    assert proc.returncode == 3, (proc.returncode, proc.stderr)
    assert "Traceback" not in proc.stderr


def test_a_huge_paragraph_and_thousands_of_hex_tokens_finish_quickly(fx):
    started = time.monotonic()
    fx.memfile("big.md", "word " * 40_000 + f"`{sha7(fx, 3)}` NOT promoted.")
    fx.memfile(
        "hex.md",
        "Landed " + " ".join(f"{i:07x}" for i in range(5_000)) + ", NOT promoted.",
    )
    fx.memfile("rx.md", "not " * 30_000 + "un " * 30_000 + "x" * 100_000)
    out = fx.run(timeout=50)
    assert out.rc in (0, 1) and out.lines[-1].startswith("RESULT:"), out.out[-400:]
    # Measured: 1.2 s. The bound only has to tell linear from catastrophic backtracking.
    assert time.monotonic() - started < 45


def test_thousands_of_claims_in_one_paragraph_finish_quickly(fx):
    started = time.monotonic()
    fx.memfile("many.md", " ".join(["NOT promoted"] * 6_000) + f" `{sha7(fx, 3)}`")
    out = fx.run(timeout=40)
    assert out.rc == 1, out.out[-300:]
    # Measured on this fixture: 0.6 s with `quote_index` cached, 28 s without it (the quadratic
    # path), so 10 s separates the two; the old 20 s bound sat too close to the uncached time.
    assert time.monotonic() - started < 10


def test_the_script_is_executable_and_declares_future_annotations():
    text = TOOL.read_text(encoding="utf-8")
    assert os.access(TOOL, os.X_OK) and text.startswith("#!/usr/bin/env python3\n")
    assert "from __future__ import annotations" in text


# ---------------------------------------------------------------- documented commands
SKILLS = Path(__file__).resolve().parents[2] / "skills"
DOCUMENTED = [
    ("propagate/SKILL.md", '"$live/scripts/memory-status-check.py" --only promoted'),
    ("propagate/SKILL.md", "./scripts/memory-status-check.py --only published"),
    ("debrief/SKILL.md", "~/.claude/scripts/memory-status-check.py --memory-dir"),
]
INDEX_OPT_IN = "Detail lives in the topic file, never here.\n"


def documented_command(skill, starts):
    """The one-line shell command a skill documents, its script path left intact."""
    for line in (SKILLS / skill).read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(starts):
            return line.strip()
    raise AssertionError(f"{skill} documents no command starting {starts!r}")


def documented_block(skill, starts):
    """The whole fenced shell block that holds the documented command, dedented."""
    lines = (SKILLS / skill).read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(starts):
            lo = i
            while not lines[lo].lstrip().startswith("```"):
                lo -= 1
            hi = i + 1
            while not lines[hi].lstrip().startswith("```"):
                hi += 1
            return textwrap.dedent("\n".join(lines[lo + 1 : hi]))
    raise AssertionError(f"{skill} documents no command starting {starts!r}")


def run_block(fx, tmp_path, skill, starts, cwd=None):
    """Run a documented block as written: placeholders filled, nothing else injected.

    `live`, `M`, `since` and `run_file` are absent from the environment, so a block that uses a
    variable it never defines fails here instead of resolving to an empty path.
    """
    runs = fx.mem / "debrief-runs"
    fill = {"M": str(fx.mem), "run_file": str(runs / "run-own-20260925T030303Z.json")}
    block = re.sub(
        r'^(\w+)="?<[^>\n]*>"?$',
        lambda m: f'{m.group(1)}="{fill[m.group(1)]}"',
        documented_block(skill, starts),
        flags=re.M,
    )
    block = re.sub(
        r'"?(?:~/\.claude|\$live|\.)/scripts/memory-status-check\.py"?',
        f'"{sys.executable}" "{TOOL}"',
        block,
    )
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    for name in ("skills", "scripts"):
        link = home / ".claude" / name
        if not link.exists():
            link.symlink_to(fx.production / "scripts")
    env = {
        k: v for k, v in fx.env.items() if k not in ("live", "M", "since", "run_file")
    }
    env["HOME"] = str(home)
    proc = subprocess.run(
        ["bash", "-c", block],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or fx.dev),
        check=False,
    )
    return Out(proc)


@pytest.mark.parametrize("skill,starts", DOCUMENTED)
def test_each_documented_block_runs_as_written(fx, tmp_path, skill, starts):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    fx.raw("MEMORY.md", INDEX_OPT_IN)
    out = run_block(fx, tmp_path, skill, starts)
    assert out.result["status"] in ("PASS", "FAIL"), (out.out, out.err)


def test_the_documented_debrief_block_is_skipped_where_the_index_check_does_not_apply(
    fx, tmp_path
):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    skill, starts = DOCUMENTED[2]
    out = run_block(fx, tmp_path, skill, starts)
    assert "not applicable" in out.out and "RESULT:" not in out.out, out.out
    fx.raw("MEMORY.md", "# Index\n\nNo rule is declared here.\n")
    out = run_block(fx, tmp_path, skill, starts)
    assert "not applicable" in out.out and "RESULT:" not in out.out, out.out


def derive_since(fx, names, own):
    """Run the debrief's documented `since=$(...)` line over a debrief-runs directory."""
    runs = fx.mem / "debrief-runs"
    runs.mkdir(exist_ok=True)
    for name in names:
        (runs / name).write_text("{}", encoding="utf-8")
    (runs / "superseded").mkdir(exist_ok=True)
    line = documented_command("debrief/SKILL.md", "since=$(")
    env = {**fx.env, "M": str(fx.mem), "run_file": str(runs / own)}
    proc = subprocess.run(
        ["bash", "-c", f'{line}\nprintf "%s" "$since"'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.stdout


def test_the_documented_since_is_the_newest_run_other_than_this_one(fx):
    own = "run-ccc-20260925T030303Z.json"
    names = [
        "run-aaa-20260901T010101Z.json",
        "run-bbb-20260920T020202Z.json",
        "run-bbb-20260920T020202Z-2.json",
        own,
    ]
    assert derive_since(fx, names, own) == "20260920T020202Z"


def test_the_documented_since_is_empty_when_this_is_the_only_run(fx):
    own = "run-ccc-20260925T030303Z.json"
    assert derive_since(fx, [own], own) == ""


# ---------------------------------------------------------------- a base marker and its connectors
@pytest.mark.parametrize(
    "lead",
    [
        "Base branch feat/x at",
        "Base fix landed",
        "Base 3 commits behind:",
    ],
)
def test_only_a_connector_may_sit_between_a_base_marker_and_its_commit(fx, lead):
    """The unlanded tip after a non-connector word is evidence, not a base: it must veto."""
    text = f"{lead} `{fx.s7(fx.work)}`; landed `{sha7(fx, 3)}`, NOT promoted."
    assert judged(fx, text) == "LIVE"


@pytest.mark.parametrize(
    "lead",
    [
        "Base",
        "Base dev",
        "Base on dev",
        "Base commit",
        "Base:",
        "Base =",
        "Base `dev` @",
    ],
)
def test_a_base_marker_with_only_connectors_still_makes_the_commit_context(fx, lead):
    text = f"{lead} `{sha7(fx, 3)}`, NOT promoted."
    assert judged(fx, text) == "SKIP:context-only"


# ---------------------------------------------------------------- ancestry reads that fail
def ancestry_fails(tmp_path, older, newer):
    """A git shim whose `merge-base --is-ancestor OLDER NEWER` exits 128; the rest is real git."""
    body = f'case " $* " in *" merge-base --is-ancestor {older} {newer} "*) exit 128;; esac'
    return shim_env(tmp_path, body)


def assert_unreadable(out, classes="promoted"):
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    assert (
        NOT_MEASURABLE.format(cls=classes, reason="object-store-unreadable")
        in out.lines
    )


def test_a_failed_ancestry_read_never_turns_a_real_ancestor_into_a_twin(fx, tmp_path):
    """c3 IS an ancestor of tip_same, so tip_same is not equivalent to it; 128 must not say no."""
    fx.memfile("rec.md", f"Tip `{fx.s7(fx.tip_same)}`, NOT promoted.")
    env = ancestry_fails(tmp_path, fx.c[3], fx.tip_same)
    out = fx.run("--all", "--only", "promoted", env=env)
    assert_unreadable(out)
    assert not out.rows("STALE")


def test_a_failed_ancestry_read_in_the_subsumption_test_is_an_error(fx, tmp_path):
    fx.memfile(
        "rec.md",
        f"Branch `{fx.s7(fx.intermediate)}`, oracle `{fx.s7(fx.oracle)}`, NOT promoted.",
    )
    env = ancestry_fails(tmp_path, fx.intermediate, fx.oracle)
    out = fx.run("--all", "--only", "promoted", env=env, production=prod_at_c4(fx))
    assert_unreadable(out)


def test_a_failed_ancestry_read_of_production_is_unreadable_not_unrelated(fx, tmp_path):
    fx.memfile("rec.md", "x")
    env = ancestry_fails(tmp_path, fx.c[3], fx.c[5])
    out = fx.run("--only", "promoted", env=env)
    assert_unreadable(out)


def test_a_failed_ancestry_read_of_the_watermark_is_unreadable_not_unrelated(
    fx, tmp_path
):
    fx.memfile("rec.md", "x")
    env = ancestry_fails(tmp_path, fx.c[2], fx.c[5])
    out = fx.run("--only", "published", env=env)
    assert_unreadable(out, "published")


# ---------------------------------------------------------------- status zone edges
def status_at(fx, line_no, name="edge.md"):
    """A frontmatter-less note whose only Status paragraph starts on file line `line_no`."""
    text = ["Intro, no claim."] + [""] * (line_no - 2) + [f"Status: {claim_text(fx)}."]
    fx.raw(name, "\n".join(text) + "\n")
    return name


def test_a_hard_wrapped_line_beginning_status_is_not_a_status_paragraph(fx):
    """Only a paragraph's FIRST line opens a Status zone: this line merely wraps a sentence."""
    fx.memfile(
        "wrap.md",
        f"Lead, no claim.\n\nThe narrative runs on and on and the\n"
        f"Status of {claim_text(fx)} was recorded here.",
    )
    assert not stale_rows(fx.run())


@pytest.mark.parametrize("line_no,zone", [(39, True), (40, True), (41, False)])
def test_the_status_scan_window_ends_after_body_line_40(fx, line_no, zone):
    name = status_at(fx, line_no)
    rows = [r for r in stale_rows(fx.run()) if r.startswith(f"STALE {name}:")]
    assert len(rows) == (1 if zone else 0), (line_no, rows)
    if zone:
        assert f"STALE {name}:{line_no} subject=topic zone=status " in rows[0]


def test_a_second_leading_heading_is_not_skipped_to_find_a_lead(fx):
    fx.memfile("two.md", f"# One\n\n## Two\n\nLanded {claim_text(fx)}.")
    assert not stale_rows(fx.run())


def test_a_note_that_is_only_a_heading_has_no_lead_zone(fx):
    fx.memfile("title.md", "# Title only")
    fx.memfile("ok.md", f"Landed {claim_text(fx)}.")
    out = fx.run("--all")
    assert [r.split()[1] for r in stale_rows(out)] == ["ok.md:8"]
    assert not any(r.startswith("title.md") for r in out.lines if "zone=lead" in r)


# ---------------------------------------------------------------- counts are whole integer tokens
@pytest.mark.parametrize(
    "text",
    [
        "Landed `{c3}`, checks=20/0/0 NOT promoted.",
        "Landed `{c3}` on 2026-09-18 NOT promoted.",
        "Landed `{c3}`, ratio 3.5 NOT promoted.",
        "Landed `{c3}`, at 12:30 NOT promoted.",
        "Landed `{c3}`, rev=7 NOT promoted.",
        "Landed `{c3}`, item-7 NOT promoted.",
    ],
)
def test_digits_glued_to_a_separator_are_not_a_count_so_the_claim_is_live(fx, text):
    fx.memfile("rec.md", text.format(c3=sha7(fx, 3)))
    assert len(stale_rows(fx.run())) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Landed `{c3}`; 5 files not promoted.",
        "Landed `{c3}`; 0 unpublished.",
        "Landed `{c3}`; 5 unpublished bricks.",
        "Landed `{c3}`; 12+ not promoted.",
    ],
)
def test_a_whole_integer_before_the_claim_is_still_a_count(fx, text):
    fx.memfile("rec.md", text.format(c3=sha7(fx, 3)))
    out = fx.run()
    assert out.rc == 0 and out.result["checks"] == "0/0/0", out.out


def test_a_count_token_cut_by_the_scan_window_is_not_mistaken_for_a_count(fx):
    """The 41-character window starts exactly at the `7` of `20/7`, whose `/` lies outside it."""
    pad = "x" * 38
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}` checks=20/7 {pad} NOT promoted.")
    assert len(stale_rows(fx.run())) == 1


# ---------------------------------------------------------------- unpaired quotes and backticks
@pytest.mark.parametrize(
    "text",
    [
        "Landed `{c3}`, a stray ` mark, NOT promoted.",
        "A stray ` mark first, then landed `{c3}`, NOT promoted.",
        'Landed `{c3}`; a 5" pipe, NOT promoted; then "x" noted.',
        "Landed `{c3}` and a stray ` here.\nThe second line is NOT promoted.",
        'A 5" pipe here.\nLanded `{c3}` and "x"; NOT promoted.',
    ],
)
def test_an_unpaired_quote_or_backtick_does_not_turn_later_claims_into_mentions(
    fx, text
):
    fx.memfile("rec.md", text.format(c3=sha7(fx, 3)))
    assert len(stale_rows(fx.run())) == 1


def test_paired_quotes_after_a_stray_one_on_another_line_still_silence_a_mention(fx):
    fx.memfile(
        "rec.md",
        f'A 5" pipe here.\nLanded `{sha7(fx, 3)}`; the check prints "NOT promoted" '
        "and `NOT published` as text.",
    )
    out = fx.run()
    assert out.rc == 0 and out.result["checks"] == "0/0/0", out.out


# ---------------------------------------------------------------- index links and fix hints
@pytest.mark.parametrize(
    "link",
    ["../x/t.md", "/abs/t.md", "sub/t.md", "https://example.org/t.md", "C:t.md"],
)
def test_an_index_link_that_is_not_a_direct_child_binds_to_no_topic_file(fx, link):
    """A top-level t.md exists with falsifying evidence; the entry did not link to THAT file."""
    topic(fx, "t.md", 3)
    index(fx, f"- [Topic]({link}) — NOT promoted yet.")
    out = fx.run()
    assert not index_rows(out), out.out
    assert "SKIP MEMORY.md:5 subject=index reason=no-landed-sha" in out.lines


@pytest.mark.parametrize("link", ["t.md", "./t.md", "t.md#section", "./t.md#a-b"])
def test_an_index_link_to_a_direct_child_resolves_with_or_without_a_fragment(fx, link):
    topic(fx, "t.md", 3)
    index(fx, f"- [Topic]({link}) — NOT promoted yet.")
    (row,) = index_rows(fx.run())
    assert f"sha={sha7(fx, 3)}" in row


LONG_LABELLED = (
    "- [Memory-index line cap](t.md) — **LANDED — the PER-ENTRY cap.** Adding a hook is "
    "a 7-file change; read this before touching the hook suite or the index."
)


def test_the_index_hint_names_the_whole_label_span_and_is_shorter_than_the_line(fx):
    index(fx, LONG_LABELLED)
    topic(fx, "t.md", 3)
    out = fx.run()
    (fix,) = [ln for ln in out.lines if ln.startswith("  fix: ")]
    hint = fix[len("  fix: ") :]
    assert hint.startswith('delete the label "**LANDED \u2014"'), fix
    assert 'and its closing "**"' in hint and '"**LANDED"' not in hint, fix
    assert len(hint) < len(LONG_LABELLED), (len(hint), len(LONG_LABELLED))


def test_a_label_and_a_negative_claim_in_one_entry_are_reported_in_text_order(fx):
    index(fx, "- [Topic](t.md) — **LANDED — the cap.** It was NOT published.")
    topic(fx, "t.md", 2)
    (row,) = index_rows(fx.run())
    assert " claim=label-landed,not-published " in row, row


def test_a_ceiling_label_whose_topic_file_has_no_landed_sha_is_skipped_by_name(fx):
    index(fx, "- [Topic](t.md) — **LANDED — the cap.** Opt in.")
    fx.memfile("t.md", "Nothing landed here.")
    out = fx.run()
    assert not index_rows(out)
    assert "SKIP MEMORY.md:5 subject=index reason=no-landed-sha" in out.lines


def test_unclosed_link_and_wikilink_openers_scan_in_linear_time(fx):
    n = 120_000
    fx.memfile("quad.md", f"Landed `{sha7(fx, 3)}`; " + "](" * n + " NOT promoted.")
    fx.memfile("quad2.md", f"Landed `{sha7(fx, 3)}`; " + "[[" * n + " NOT published.")
    started = time.monotonic()
    out = fx.run(timeout=50)
    assert out.result["status"] in ("PASS", "FAIL"), out.out
    assert time.monotonic() - started < 8, "link blanking is quadratic again"


# ---------------------------------------------------------------- the header comes before any verdict
def second_batch_check(tmp_path, action):
    """A shim that lets the self-check's `cat-file --batch-check` through and runs `action` on the next.

    The next one is the classification of the hex tokens, which happens while units are judged.
    """
    count = tmp_path / "batch-count"
    body = (
        f'case " $* " in *" cat-file --batch-check "*) '
        f'n=$(cat "{count}" 2>/dev/null || echo 0); echo $((n + 1)) > "{count}"; '
        f'if [ "$n" -ge 1 ]; then {action}; fi;; esac'
    )
    return shim_env(tmp_path, body)


def test_an_error_while_judging_keeps_the_real_header_fields(fx, tmp_path):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    clean = fx.run()
    env = second_batch_check(tmp_path, "cat >/dev/null; exit 0")
    out = fx.run(env=env)
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    assert out.header == clean.header, (out.header, clean.header)
    assert "production=unresolved" not in out.header


def test_sigterm_while_judging_keeps_the_header_before_the_incomplete_verdict(
    fx, tmp_path
):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    clean = fx.run()
    mark = tmp_path / "judging"
    env = {**fx.env, **second_batch_check(tmp_path, f': > "{mark}"; sleep 30')}
    proc = subprocess.Popen(
        [
            sys.executable,
            str(TOOL),
            "--memory-dir",
            str(fx.mem),
            "--dev",
            str(fx.dev),
            "--production",
            str(fx.production),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        for _ in range(100):
            if mark.exists():
                break
            time.sleep(0.1)
        assert mark.exists(), "the tool never reached the judging step"
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=20)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    lines = out.splitlines()
    assert proc.returncode == 143 and len(lines) == 2, out
    assert lines[0] == clean.header, (lines[0], clean.header)
    assert lines[1].startswith("RESULT: INCOMPLETE rc=143 ")


# ---------------------------------------------------------------- promote time search
def utc(epoch):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(epoch))


def test_since_dates_each_unit_by_the_first_reflog_entry_that_holds_its_commits(fx):
    """Production fast-forwarded c2, c3, c4 at three distinct times; each unit has its own entry."""
    prod = fx.make_production(
        "prod-three-steps",
        [(fx.c[2], fx.at(41)), (fx.c[3], fx.at(42)), (fx.c[4], fx.at(43))],
    )
    for n in (2, 3, 4):
        fx.memfile(f"u{n}.md", f"Landed `{sha7(fx, n)}`, NOT promoted.")
    # Each pair: `--since` this far in, how many promotes predate it.
    for since, expected in [
        (fx.at(41) - 1800, 0),
        (fx.at(41) + 1800, 1),
        (fx.at(42) + 1800, 2),
        (fx.at(43) + 1800, 3),
    ]:
        out = fx.run("--since", utc(since), production=prod)
        assert f"stale-before-since={expected} scope=promoted" in out.header, (
            since,
            out.header,
        )


# ---------------------------------------------------------------- stdout failures and signals
def tool_argv(fx):
    return [
        sys.executable,
        str(TOOL),
        "--memory-dir",
        str(fx.mem),
        "--dev",
        str(fx.dev),
        "--production",
        str(fx.production),
    ]


def test_a_read_only_stdout_exits_3_without_a_traceback(fx):
    """stdout opened read-only (`1</dev/null`): the first write fails with EBADF, not EPIPE."""
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        proc = subprocess.run(
            tool_argv(fx),
            stdout=fd,
            stderr=subprocess.PIPE,
            text=True,
            env=fx.env,
            check=False,
        )
    finally:
        os.close(fd)
    assert proc.returncode == 3, (proc.returncode, proc.stderr)
    assert "Traceback" not in proc.stderr, proc.stderr


def test_a_pipe_that_breaks_after_the_header_exits_3_without_a_traceback(fx):
    """The reader takes the header and leaves; later writes (more than a pipe holds) hit EPIPE."""
    for i in range(150):
        fx.memfile(
            f"u{i:03d}.md", f"Landed `{sha7(fx, 3)}`, NOT promoted. " + "x" * 200
        )
    proc = subprocess.Popen(
        tool_argv(fx),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=fx.env,
    )
    first = proc.stdout.readline()
    proc.stdout.close()
    err = proc.stderr.read()
    proc.wait(timeout=30)
    assert first.startswith("memory-status-check: "), first
    assert proc.returncode == 3, (proc.returncode, err)
    assert "Traceback" not in err, err


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def stalled_run(fx, tmp_path, sig):
    """Start the tool, stall its judging step in a shimmed git, send `sig`.

    Returns the Popen, its stdout and the shim pids that were STILL alive five seconds after the
    tool ended (read before this helper's own cleanup, which would hide a leak).
    """
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    mark = tmp_path / "judging"
    pids = tmp_path / "pids"
    action = f'echo $$ > "{pids}"; sleep 30 & echo $! >> "{pids}"; : > "{mark}"; wait'
    env = {**fx.env, **second_batch_check(tmp_path, action)}
    proc = subprocess.Popen(
        tool_argv(fx),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    lingering, shim_pids = [], []
    try:
        for _ in range(100):
            if mark.exists():
                break
            time.sleep(0.1)
        assert mark.exists(), "the tool never reached the judging step"
        shim_pids = [int(p) for p in pids.read_text().split()]
        assert len(shim_pids) == 2, shim_pids
        proc.send_signal(sig)
        out, _ = proc.communicate(timeout=20)
        deadline = time.monotonic() + 5
        while any(alive(p) for p in shim_pids) and time.monotonic() < deadline:
            time.sleep(0.1)
        lingering = [p for p in shim_pids if alive(p)]
    finally:
        for p in shim_pids:
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return proc, out, lingering


@pytest.mark.parametrize(
    "sig,rc",
    [(signal.SIGTERM, 143), (signal.SIGINT, 130), (signal.SIGHUP, 129)],
    ids=["sigterm", "sigint", "sighup"],
)
def test_each_caught_signal_ends_with_its_incomplete_verdict_and_leaves_no_git_behind(
    fx, tmp_path, sig, rc
):
    proc, out, lingering = stalled_run(fx, tmp_path, sig)
    assert proc.returncode == rc, out
    assert out.splitlines()[-1].startswith(f"RESULT: INCOMPLETE rc={rc} "), out
    assert not lingering, f"a git call outlived the tool: {lingering}"


# ---------------------------------------------------------------- documented commands pass their flags
PLUMBING = [
    (
        DOCUMENTED[0],
        r" classes=promoted range=[0-9a-f]{7}\.\.[0-9a-f]{7} range-commits=\d+ ",
    ),
    (DOCUMENTED[1], r" classes=published range=none "),
    (DOCUMENTED[2], r" stale-before-since=\d+ scope=promoted$"),
]


@pytest.mark.parametrize(
    "documented,header", PLUMBING, ids=["promote", "publish", "debrief"]
)
def test_each_documented_block_passes_its_flags_through_to_the_tool(
    fx, tmp_path, documented, header
):
    """Dropping `--only`, `--range` or `--since` from a documented command must be visible."""
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    fx.raw("MEMORY.md", INDEX_OPT_IN)
    runs = fx.mem / "debrief-runs"
    runs.mkdir()
    (runs / "run-prev-20260920T020202Z.json").write_text("{}", encoding="utf-8")
    skill, starts = documented
    out = run_block(fx, tmp_path, skill, starts)
    assert out.result["status"] in ("PASS", "FAIL"), (out.out, out.err)
    assert re.search(header, out.header), (header, out.header)


# ---------------------------------------------------------------- fix line, NOTE row, evaluation count and tags
def test_the_fix_line_names_the_highest_stale_stage_when_both_classes_are_stale(fx):
    fx.memfile("both.md", f"Landed `{sha7(fx, 2)}`, NOT promoted, NOT published.")
    out = fx.run()
    (fix,) = [ln for ln in out.lines if ln.startswith("  fix: ")]
    assert fix.startswith("  fix: state PUBLISHED ("), fix
    assert f"attained watermark@{sha7(fx, 2)}" in fix, fix


def test_the_note_row_cites_the_anchored_claim_not_an_earlier_live_one(fx):
    fx.memfile(
        "n.md",
        f"Landed `{sha7(fx, 3)}`, NOT promoted.\n{'y' * 200}\n"
        "It was NOT promoted as of 2026-10-17.",
    )
    out = fx.run()
    assert [r.split()[1] for r in stale_rows(out)] == ["n.md:8"], out.out
    assert out.rows("NOTE") == [
        f"NOTE n.md:10 anchored-claim attained=production@{sha7(fx, 3)}"
    ], out.out


def test_a_class_is_evaluated_once_per_unit_even_with_anchored_and_live_claims(
    fx, tmp_path
):
    log = tmp_path / "tree-reads"
    body = f'case " $* " in *" rev-parse "*"^{{tree}}"*) echo x >> "{log}";; esac'
    fx.memfile(
        "once.md",
        f"Work `{fx.s7(fx.work)}`, NOT promoted.\n{'y' * 200}\n"
        "It was NOT promoted as of 2026-10-17.",
    )
    out = fx.run("--only", "promoted", env=shim_env(tmp_path, body))
    assert out.rc == 0 and out.result["checks"] == "1/0/0", out.out
    assert len(log.read_text().split()) == 1, log.read_text()


def test_an_anchor_does_not_silence_a_same_class_claim_far_from_it(fx):
    fx.memfile(
        "same.md",
        f"Landed `{sha7(fx, 3)}`, NOT promoted as of 2026-10-17. {'x' * 300} NOT promoted.",
    )
    out = fx.run()
    (row,) = stale_rows(out)
    assert out.result["notes"] == "1", out.out


def test_a_claim_wrapped_inside_a_block_quote_is_still_found(fx):
    fx.memfile("bq.md", f"> Landed `{sha7(fx, 3)}`, NOT\n> promoted.")
    (row,) = stale_rows(fx.run())
    assert row.startswith("STALE bq.md:8 "), row


def test_a_failed_listing_of_dev_commits_is_an_unreadable_store_not_no_trees(
    fx, tmp_path
):
    fx.memfile("a.md", "x")
    body = 'case " $* " in *" log --format=%H %T "*) exit 128;; esac'
    out = fx.run(env=shim_env(tmp_path, body))
    assert_unreadable(out, "promoted,published")


def test_a_main_side_only_unit_is_outside_every_range(fx):
    fx.memfile("m.md", f"Shipped `{fx.s7(fx.main1)}`, NOT published.")
    whole = fx.run()
    assert whole.rc == 1 and whole.result["outside-range"] == "0", whole.out
    ranged = fx.run("--range", f"{fx.c[2]}..{fx.c[3]}")
    assert ranged.rc == 0 and ranged.result["outside-range"] == "1", ranged.out


def ambiguous_clone(fx, name):
    """A clone of dev holding two blobs that share a 7-digit prefix, and that prefix."""
    clone = clone_dev(fx, name)
    seen = {}
    for i in range(200_000):
        body = f"{i}\n".encode()
        sha = hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest()
        if sha[:7] in seen:
            pair, prefix = (seen[sha[:7]], body), sha[:7]
            break
        seen[sha[:7]] = body
    else:
        pytest.fail("no 7-digit collision found")
    for body in pair:
        subprocess.run(
            ["git", "-C", str(clone), "hash-object", "-w", "--stdin"],
            input=body,
            capture_output=True,
            env=fx.env,
            check=True,
        )
    return clone, prefix


def test_an_ambiguous_abbreviation_only_vetoes_when_it_is_backticked(fx):
    """Pinned: a bare ambiguous token is prose, a backticked one is a claimed commit."""
    clone, prefix = ambiguous_clone(fx, "ambiguous2")
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}` and {prefix}, NOT promoted.")
    out = fx.run("--all", "--only", "promoted", dev=clone)
    assert verdict_of(out) == "STALE", out.out
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}` and `{prefix}`, NOT promoted.")
    out = fx.run("--all", "--only", "promoted", dev=clone)
    assert verdict_of(out) == "LIVE", out.out


def tagged_clone(fx, name):
    """A clone with origin/main at c1, the watermark at c2, a tag on c1 and one on c2."""
    clone = clone_dev(fx, name)
    fx.git(clone, "update-ref", "refs/remotes/origin/main", fx.c[1])
    fx.git(clone, "update-ref", "refs/published/main", fx.c[2])
    fx.git(clone, "tag", "v9.9.1", fx.c[1])
    fx.git(clone, "tag", "v9.9.2", fx.c[2])
    return clone


def test_a_tag_attains_published_only_when_its_commit_is_on_origin_main(fx):
    clone = tagged_clone(fx, "tagged")
    on_origin = judged(
        fx, "Shipped `v9.9.1`, NOT published.", "--only", "published", dev=clone
    )
    off_origin = judged(
        fx, "Shipped `v9.9.2`, NOT published.", "--only", "published", dev=clone
    )
    assert (on_origin, off_origin) == ("STALE", "LIVE")


def test_an_unlanded_commit_vetoes_when_origin_main_is_absent(fx):
    clone = clone_dev(fx, "noorigin")
    text = f"Work `{fx.s7(fx.work)}` on `{sha7(fx, 3)}`, NOT promoted."
    assert judged(fx, text, "--only", "promoted", dev=clone) == "LIVE"


# ---------------------------------------------------------------- self-check scope and production controls
def test_only_promoted_does_not_probe_a_watermark_that_is_not_a_commit(fx):
    """The watermark is a control only when the published class is judged."""
    clone = clone_dev(fx, "badmark")
    blob = fx.git(clone, "rev-parse", f"{fx.c[3]}:f3")
    fx.git(clone, "update-ref", "refs/published/main", blob)
    fx.memfile("a.md", "x")
    promoted = fx.run("--only", "promoted", dev=clone)
    assert promoted.rc == 0 and promoted.result["status"] == "PASS", promoted.out
    published = fx.run("--only", "published", dev=clone)
    assert published.rc == 2 and published.result["status"] == "ERROR", published.out


def test_a_production_repo_without_a_commit_is_named_not_judged(fx, tmp_path):
    fx.memfile("a.md", "x")
    empty = tmp_path / "empty-production"
    empty.mkdir()
    fx.git(empty, "init", "-q", "-b", "dev")
    out = fx.run(production=empty)
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    assert (
        NOT_MEASURABLE.format(cls="promoted", reason="production-absent") in out.lines
    )


def test_a_defaulted_production_without_a_scripts_directory_is_named(fx, tmp_path):
    fx.memfile("a.md", "x")
    home = tmp_path / "bare-home"
    (home / ".claude").mkdir(parents=True)
    out = fx.run(production=False, env={"HOME": str(home)})
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    assert (
        NOT_MEASURABLE.format(cls="promoted", reason="production-absent") in out.lines
    )


# ---------------------------------------------------------------- zones: headings, fences, wrapped spans
def test_a_status_paragraph_directly_under_a_heading_is_a_zone(fx):
    fx.memfile(
        "h.md",
        f"Intro, no claim.\n\n## Notes\nStatus: Landed `{sha7(fx, 3)}`, NOT promoted.",
    )
    (row,) = stale_rows(fx.run())
    assert row.startswith("STALE h.md:11 subject=topic zone=status "), row


FENCED_EXAMPLE = (
    "Lead, no claim.\n\n```markdown\nan example line\n\n"
    "Status: Landed `{c3}`, NOT promoted.\n```\n"
)


def test_a_status_paragraph_inside_a_code_fence_is_not_a_zone(fx):
    """The fence holds a blank line, so only fence awareness stops the example opening a zone."""
    fx.memfile("fence.md", FENCED_EXAMPLE.format(c3=sha7(fx, 3)))
    assert not stale_rows(fx.run())


def test_a_fenced_block_at_the_top_is_not_a_lead_paragraph(fx):
    fx.memfile("top.md", f"```\nLanded `{sha7(fx, 3)}`, NOT promoted.\n```\n")
    assert not stale_rows(fx.run())


def test_a_claim_inside_a_fence_that_follows_a_status_line_is_not_read(fx):
    fx.memfile(
        "after.md",
        f"Status: Landed `{sha7(fx, 3)}`.\n```\nNOT promoted\n```\nok",
    )
    assert not stale_rows(fx.run())


def test_a_status_line_after_a_closed_fence_is_a_zone_again(fx):
    fx.memfile(
        "again.md",
        f"Lead, no claim.\n\n```\ncode\n```\nStatus: Landed `{sha7(fx, 3)}`, NOT promoted.",
    )
    (row,) = stale_rows(fx.run())
    assert " zone=status " in row, row


def test_a_code_span_wrapped_across_two_lines_still_quotes_its_claim(fx):
    fx.memfile("span.md", f"Landed `{sha7(fx, 3)}`; see `wrapped\nNOT promoted` here.")
    out = fx.run()
    assert out.rc == 0 and out.result["checks"] == "0/0/0", out.out


def test_a_stray_backtick_still_does_not_silence_a_claim_two_lines_later(fx):
    """The drift control: the stray pairs with the next line's first backtick at most."""
    fx.memfile(
        "drift.md",
        f"Landed `{sha7(fx, 3)}` and a stray ` here.\nThe second line is NOT promoted.\n"
        "Then `code` appears.",
    )
    assert len(stale_rows(fx.run())) == 1


# ---------------------------------------------------------------- stdout that cannot encode the report
def load_tool():
    """The tool as a fresh module, for the rows that drive one function directly."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("msc_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_ascii_stdout_still_ends_in_a_verdict_with_each_row_once(fx):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}` \u2014 NOT promoted.")
    proc = subprocess.run(
        tool_argv(fx),
        capture_output=True,
        text=True,
        env={**fx.env, "PYTHONIOENCODING": "ascii"},
        check=False,
    )
    lines = proc.stdout.splitlines()
    assert lines and lines[-1].startswith("RESULT: FAIL rc=1 "), (
        proc.stdout,
        proc.stderr,
    )
    assert sum(1 for ln in lines if ln.startswith("STALE ")) == 1, proc.stdout
    assert "\\u2014" in proc.stdout and proc.returncode == 1, proc.stdout


def test_a_failed_write_never_leaves_its_row_queued_to_be_printed_again():
    tool = load_tool()
    tool.S.rows[:] = ["one", "two", "three"]
    seen = []

    def emit(line):
        seen.append(line)
        if line == "two":
            raise OSError("write failed")

    tool.emit = emit
    with pytest.raises(OSError):
        tool.flush_rows()
    tool.flush_rows()
    assert seen == ["one", "two", "three"], seen


def test_text_stdout_cannot_encode_is_a_stdout_failure_not_a_crash():
    tool = load_tool()

    def refuse(*_args, **_kwargs):
        raise UnicodeEncodeError("ascii", "\u2014", 0, 1, "ordinal not in range")

    tool.print = refuse
    with pytest.raises(tool.StdoutFailed):
        tool.emit("a line")


# ---------------------------------------------------------------- documented commands name the dev repo
@pytest.mark.parametrize(
    "documented", [DOCUMENTED[0], DOCUMENTED[1]], ids=["promote", "publish"]
)
def test_a_propagate_block_run_outside_the_dev_repo_is_an_error_not_a_skip(
    fx, tmp_path, documented
):
    """`--dev "$(git rev-parse --show-toplevel)"` is empty there: the check must say it did not run."""
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    skill, starts = documented
    out = run_block(fx, tmp_path, skill, starts, cwd=elsewhere)
    assert out.rc == 2 and out.result["status"] == "ERROR", (out.out, out.err)
    assert any(ln.startswith("ERROR reason=usage ") for ln in out.lines), out.out


@pytest.mark.parametrize(
    "documented", [DOCUMENTED[0], DOCUMENTED[1]], ids=["promote", "publish"]
)
def test_a_propagate_block_echoes_the_dev_repo_it_was_told(fx, tmp_path, documented):
    fx.memfile("rec.md", f"Landed `{sha7(fx, 3)}`, NOT promoted.")
    subdir = fx.dev / "scripts"
    skill, starts = documented
    out = run_block(fx, tmp_path, skill, starts, cwd=subdir)
    assert f" dev={fx.dev}@" in out.header, out.header


def test_an_empty_dev_value_is_a_usage_error_never_the_working_directory(fx):
    fx.memfile("rec.md", "x")
    out = fx.run("--memory-dir", str(fx.mem), "--dev", "", defaults=False, cwd=fx.dev)
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    assert any(ln.startswith("ERROR reason=usage ") for ln in out.lines), out.out


# ---------------------------------------------------------------- base connectors and the production header
@pytest.mark.parametrize("lead", ["Base is", "Base was", "Base was on dev"])
def test_is_and_was_may_sit_between_a_base_marker_and_its_commit(fx, lead):
    text = f"{lead} `{sha7(fx, 3)}`, NOT promoted."
    assert judged(fx, text) == "SKIP:context-only"


def test_an_explicit_production_that_is_not_a_directory_keeps_its_path_in_the_header(
    fx, tmp_path
):
    fx.memfile("a.md", "x")
    missing = tmp_path / "no-such-production"
    out = fx.run(production=missing)
    assert out.rc == 2 and out.result["status"] == "ERROR", out.out
    assert f" production={missing}@unresolved " in out.header, out.header
