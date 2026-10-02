"""Tests for scripts/mutation-anchors-check.py.

The subject asserts that every mutation campaign's `old` anchor still resolves in the file that
campaign mutates. Two measured defect classes make that worth checking, and both look identical
from outside — the anchor no longer matches the subject:

* **Anchor rot.** Whoever refactors a subject is the last person to think of re-pointing its
  campaign. Measured twice; the second time a single refactor broke two anchors in
  `mutate_lib_mutate.py`, and nothing noticed until the campaign was run by hand.
* **A live mutation stranded in the working tree.** A campaign killed or hung between writing a
  mutant and restoring leaves a corrupted tool on disk. Measured once, on a checker — it was left
  reporting PASS on unreadable input.

Every row here is written to fail for the reason it names. The two guards that matter most are
the ones that keep a vacuous run from reading as clean: zero campaigns discovered, and a campaign
declaring zero mutations, are both ERROR rather than PASS. A comparison with an empty expected
set passes against anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
CHECKER = REPO / "scripts" / "mutation-anchors-check.py"

# The repo's own campaigns, named so their DISAPPEARANCE alarms. Discovery is a glob, which
# covers whatever is added and silently stops covering whatever is removed; this floor is the
# half a glob cannot supply. Adding a campaign here is correct — removing one needs a reason.
FLOOR = (
    "mutate_audit_hermetic_subtree.py",
    "mutate_audit_test.py",
    "mutate_hook_budget.py",
    "mutate_hook_machinery_test.py",
    "mutate_hook_suite_guard_selection.py",
    "mutate_lib_mutate.py",
    "mutate_markdownlint_config.py",
    "mutate_mutation_anchors_check.py",
    "mutate_prose_diff.py",
    "mutate_publication_push_guard_test.py",
    "mutate_publish_brick_dev.py",
    "mutate_run_hooks.py",
    "mutate_run_long.py",
    "mutate_settings_hooks_check.py",
    "mutate_settings_hooks_lib.py",
)

CAMPAIGN_HEAD = '''\
"""A fixture campaign."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "subject.sh"
SUITE = ["true"]

'''


def run(*args):
    """Invoke the checker, returning (rc, stdout+stderr)."""
    proc = subprocess.run(
        [sys.executable, str(CHECKER), *args], capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


def verdict(out):
    """The checker's terminal verdict line — asserted to be the LAST line of output."""
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "no output at all"
    assert lines[-1].startswith("RESULT: "), (
        "verdict is not the last line; got %r" % lines[-1]
    )
    return lines[-1]


class Sandbox:
    """A throwaway git repo carrying one subject, plus helpers to populate it with campaigns."""

    def __init__(self, root):
        self.root = root

    def __truediv__(self, other):
        return self.root / other

    def __str__(self):
        return str(self.root)

    def track(self, path):
        subprocess.run(["git", "-C", str(self.root), "add", str(path)], check=True)

    def campaign(
        self, name, rows, subject='REPO / "subject.sh"', preamble="", track=True
    ):
        body = CAMPAIGN_HEAD.replace('REPO / "subject.sh"', subject) + preamble
        body += "MUTATIONS = [\n"
        for label, old, new in rows:
            body += "    mutate.Mutation(\n"
            body += "        %s,\n        %s,\n        %s,\n    ),\n" % (
                label,
                old,
                new,
            )
        body += "]\n"
        path = self.root / "scripts" / "tests" / name
        path.write_text(body)
        if track:
            self.track(path)
        return path


@pytest.fixture
def sandbox(tmp_path):
    """An initialised Sandbox.

    `tmp_path` is resolved physically: a logical $TMPDIR path (macOS symlinks /tmp) can send a
    path-resolving subject down a different branch, and a fixture that never reaches the code it
    targets passes for free.
    """
    root = tmp_path.resolve()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "commit.gpgsign", "false"], check=True
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "tag.gpgsign", "false"], check=True
    )
    (root / "subject.sh").write_text("alpha\nbravo\ncharlie\n")
    (root / "scripts" / "tests").mkdir(parents=True)
    box = Sandbox(root)
    box.track(root / "subject.sh")
    return box


# --- the healthy case, and the two anchor defects -------------------------------------------


def test_intact_anchor_passes(sandbox):
    sandbox.campaign("mutate_x.py", [('"row"', '"alpha"', '"ALPHA"')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 0, out
    assert verdict(out).startswith("RESULT: PASS rc=0")


def test_rotted_anchor_fails(sandbox):
    """The `old` string no longer appears — the subject was refactored under the campaign."""
    sandbox.campaign("mutate_x.py", [('"row"', '"no-such-text"', '"X"')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 1, out
    assert "no-such-text" in out
    assert verdict(out).startswith("RESULT: FAIL rc=1")


def test_live_mutation_left_in_subject_fails(sandbox):
    """A killed campaign's mutant is still on disk, so its own anchor no longer resolves."""
    sandbox.campaign("mutate_x.py", [('"row"', '"bravo"', '"BRAVO"')])
    (sandbox / "subject.sh").write_text("alpha\nBRAVO\ncharlie\n")
    rc, out = run("--scope", str(sandbox))
    assert rc == 1, out
    assert verdict(out).startswith("RESULT: FAIL rc=1")


def test_ambiguous_anchor_fails(sandbox):
    """Two occurrences is a defect too — mutate.py refuses to apply such a row."""
    (sandbox / "subject.sh").write_text("alpha\nalpha\ncharlie\n")
    sandbox.campaign("mutate_x.py", [('"row"', '"alpha"', '"ALPHA"')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 1, out
    assert "2" in out
    assert verdict(out).startswith("RESULT: FAIL rc=1")


# --- the vacuous-pass guards ----------------------------------------------------------------


def test_zero_campaigns_is_error_not_pass(sandbox):
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert verdict(out).startswith("RESULT: ERROR rc=2")


def test_empty_mutations_list_is_error(sandbox):
    sandbox.campaign("mutate_x.py", [])
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert verdict(out).startswith("RESULT: ERROR rc=2")


# --- the untracked-campaign guard -------------------------------------------------------------
#
# Discovery is `git ls-files`, which grades the COMMITTED population. A campaign is untracked
# precisely when it is brand new — i.e. when its anchors have never once been verified — so the
# window this closes is narrow and is exactly the wrong moment to be blind. Measured: a run
# reported `campaigns=6` where seven existed, and `git add` alone made it seven.
#
# The verdict is ERROR rather than FAIL because the run reached no verdict ABOUT that campaign;
# it is the same category as zero-campaigns-discovered, not a finding about an anchor.


def test_untracked_campaign_beside_a_tracked_one_is_error(sandbox):
    """The measured shape: a healthy tracked campaign makes every other guard pass.

    This is the row the old suite lacked. Its predecessor put a lone untracked campaign in the
    sandbox, so the ERROR it asserted came from the zero-campaign guard — it would have read
    green with no untracked handling at all. Here the tracked campaign satisfies that guard and
    resolves cleanly, so ERROR can only come from the untracked one.
    """
    sandbox.campaign("mutate_tracked.py", [('"row"', '"alpha"', '"ALPHA"')])
    sandbox.campaign("mutate_scratch.py", [('"row"', '"bravo"', '"X"')], track=False)
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert "mutate_scratch.py" in out, "the untracked campaign was not named"
    assert verdict(out).startswith("RESULT: ERROR rc=2")
    assert "untracked=1" in verdict(out), verdict(out)


def test_untracked_campaign_alone_is_error_naming_it(sandbox):
    """The lone-untracked case still ERRORs — but now it says which campaign it never read.

    Both guards fire here (zero tracked campaigns AND one untracked), which is why the rc alone
    proves nothing; the name is the part that distinguishes this from its predecessor.
    """
    sandbox.campaign("mutate_x.py", [('"row"', '"nope"', '"X"')], track=False)
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert "mutate_x.py" in out, "the untracked campaign was not named"
    assert verdict(out).startswith("RESULT: ERROR rc=2")


def test_ignored_campaign_is_a_declared_exclusion_and_passes(sandbox):
    """An IGNORED campaign is the escape hatch, and the reason the guard is not just a glob.

    Untracked-and-unignored is an UNDECLARED omission — nobody said this file was out of scope,
    it simply has not been added yet. An ignored one is declared: the repo states it is not part
    of itself. Grading it would be the filesystem-glob mistake, which starts failing runs over
    artifacts the commit will never contain.
    """
    sandbox.campaign("mutate_tracked.py", [('"row"', '"alpha"', '"ALPHA"')])
    sandbox.campaign("mutate_scratch.py", [('"row"', '"nope"', '"X"')], track=False)
    (sandbox / ".gitignore").write_text("scripts/tests/mutate_scratch.py\n")
    rc, out = run("--scope", str(sandbox))
    assert rc == 0, out
    assert verdict(out).startswith("RESULT: PASS rc=0")
    assert "untracked=0" in verdict(out), verdict(out)


def test_healthy_repo_verdict_reports_zero_untracked(sandbox):
    """The count is in the verdict on the PASS path too — an expectation to compare against.

    Reporting it only on the failing path would leave a clean run making a coverage claim with
    nothing behind it, which is the state this whole guard exists to end.
    """
    sandbox.campaign("mutate_x.py", [('"row"', '"alpha"', '"ALPHA"')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 0, out
    assert "untracked=0" in verdict(out), verdict(out)


# --- unresolvable input is ERROR, never a silent skip ----------------------------------------


def test_unresolvable_old_expression_is_error(sandbox):
    """A computed anchor cannot be read statically — refuse rather than skip the row."""
    sandbox.campaign("mutate_x.py", [('"row"', 'compute("alpha")', '"X"')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert verdict(out).startswith("RESULT: ERROR rc=2")


def test_unresolvable_subject_is_error(sandbox):
    sandbox.campaign(
        "mutate_x.py", [('"row"', '"alpha"', '"X"')], subject="discover_subject()"
    )
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert verdict(out).startswith("RESULT: ERROR rc=2")


def test_subject_escaping_the_scope_is_error(sandbox):
    """A campaign may not aim the checker at a file outside the repo it is auditing."""
    sandbox.campaign(
        "mutate_x.py",
        [('"row"', '"alpha"', '"X"')],
        subject='REPO / ".." / "escape.sh"',
    )
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert verdict(out).startswith("RESULT: ERROR rc=2")


def test_missing_subject_file_is_error(sandbox):
    sandbox.campaign(
        "mutate_x.py", [('"row"', '"alpha"', '"X"')], subject='REPO / "absent.sh"'
    )
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert verdict(out).startswith("RESULT: ERROR rc=2")


def test_unparseable_campaign_is_error(sandbox):
    path = sandbox / "scripts" / "tests" / "mutate_broken.py"
    path.write_text("this is (not python\n")
    subprocess.run(["git", "-C", str(sandbox), "add", str(path)], check=True)
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert verdict(out).startswith("RESULT: ERROR rc=2")


# --- the expression shapes the real campaigns actually use ------------------------------------


def test_module_level_constant_anchor_resolves(sandbox):
    """`mutate_markdownlint_config.py` binds its `old` to a module-level name."""
    sandbox.campaign(
        "mutate_x.py",
        [('"row"', "LIVE", '"X"')],
        preamble='LIVE = "alpha"\n\n',
    )
    rc, out = run("--scope", str(sandbox))
    assert rc == 0, out


def test_implicit_and_explicit_concatenation_resolve(sandbox):
    (sandbox / "subject.sh").write_text("alpha\nbravo\ncharlie\n")
    sandbox.campaign(
        "mutate_x.py",
        [
            ('"implicit"', '"al" "pha"', '"X"'),
            ('"explicit"', '"bra" + "vo"', '"Y"'),
        ],
    )
    rc, out = run("--scope", str(sandbox))
    assert rc == 0, out


def test_runner_itself_is_not_mistaken_for_a_campaign(sandbox):
    """`scripts/lib/mutate.py` is the runner; only `mutate_*.py` files are campaigns."""
    lib = sandbox / "scripts" / "lib"
    lib.mkdir(parents=True)
    (lib / "mutate.py").write_text("# the runner, not a campaign\n")
    subprocess.run(
        ["git", "-C", str(sandbox), "add", str(lib / "mutate.py")], check=True
    )
    sandbox.campaign("mutate_x.py", [('"row"', '"alpha"', '"ALPHA"')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 0, out
    assert "lib/mutate.py" not in out


# --- a mutant that does not PARSE never exercises the suite ------------------------------------
#
# `mutate.py` scores a mutant CAUGHT when the suite exits non-zero, and a subject that will not
# parse makes pytest abort collection (or bash refuse the script) — a non-zero exit that catches
# nothing. Measured: `mutate_backlog.py:snapshot-failure-aborts` read CAUGHT over an unclosed
# `print(`. The checker judges every row's mutant with the runner's own predicate.


def test_unparseable_python_mutant_fails_naming_the_row_and_the_parser(sandbox):
    (sandbox / "subject.py").write_text("GUARD = True\n")
    sandbox.track(sandbox / "subject.py")
    sandbox.campaign(
        "mutate_x.py",
        [('"half-open call"', '"GUARD = True"', '"GUARD = ("')],
        subject='REPO / "subject.py"',
    )
    rc, out = run("--scope", str(sandbox))
    assert rc == 1, out
    assert "MUTANTS THAT DO NOT PARSE" in out
    assert "mutate_x.py" in out and "half-open call" in out
    assert "was never closed" in out, "the parser's own detail is not shown"
    assert verdict(out).startswith("RESULT: FAIL rc=1")
    assert "invalid=1" in verdict(out), verdict(out)
    assert "bad=0" in verdict(out), "an invalid mutant is not an anchor finding"


def test_unparseable_bash_mutant_fails(sandbox):
    """The `bash -n` path: a stray `fi` is a syntax error bash refuses before running anything."""
    sandbox.campaign("mutate_x.py", [('"stray fi"', '"bravo"', '"fi"')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 1, out
    assert "MUTANTS THAT DO NOT PARSE" in out and "stray fi" in out
    assert verdict(out).startswith("RESULT: FAIL rc=1")
    assert "invalid=1" in verdict(out), verdict(out)


def test_parseable_mutant_passes_and_a_clean_run_states_its_coverage(sandbox):
    """`invalid=0` on a parseable mutant; a subject with no parser is `unchecked`, not valid."""
    sandbox.campaign("mutate_x.py", [('"row"', '"alpha"', '"ALPHA"')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 0, out
    assert "invalid=0" in verdict(out) and "unchecked=0" in verdict(out), verdict(out)

    (sandbox / "subject.jsonc").write_text('{"a": 1}\n')
    sandbox.track(sandbox / "subject.jsonc")
    sandbox.campaign(
        "mutate_y.py",
        [('"row"', '"\\"a\\""', '"\\"a"')],
        subject='REPO / "subject.jsonc"',
    )
    rc, out = run("--scope", str(sandbox))
    assert rc == 0, out
    assert "invalid=0" in verdict(out), verdict(out)
    assert "unchecked=1" in verdict(out), verdict(out)
    assert "UNCHECKED" in out, "the unchecked kinds are not named"


def test_pristine_subject_that_does_not_parse_leaves_rows_unchecked_not_invalid(
    sandbox,
):
    """No control, no verdict: a checker on an older bash must not mark every mutant invalid."""
    (sandbox / "subject.sh").write_text("if then\n")
    sandbox.campaign("mutate_x.py", [('"row"', '"then"', '"else"')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 0, out
    assert "invalid=0" in verdict(out) and "unchecked=1" in verdict(out), verdict(out)


def test_a_bash_parser_that_cannot_run_is_ERROR_and_not_counted_unchecked(
    sandbox, tmp_path_factory
):
    """`bash -n` failing to SPAWN is not "no parser for this kind": no verdict was reached.

    The real checker process runs with a PATH holding only `git`, so `bash` does not resolve and
    `subprocess.run` raises FileNotFoundError (an OSError) — the shape of a transient spawn
    failure. Old behaviour: every `.sh` row read UNCHECKED and the run PASSed with the coverage
    silently gone (measured once as `unchecked=25` against a true 3).
    """
    import os
    import shutil

    bindir = tmp_path_factory.mktemp("nobash").resolve()
    (bindir / "git").symlink_to(shutil.which("git"))
    sandbox.campaign(
        "mutate_x.py",
        [('"one"', '"alpha"', '"ALPHA"'), ('"two"', '"bravo"', '"BRAVO"')],
    )
    proc = subprocess.run(
        [sys.executable, str(CHECKER), "--scope", str(sandbox)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": str(bindir)},
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    last = verdict(out)
    assert last.startswith("RESULT: ERROR rc=2"), last
    assert "unchecked=0" in last and "invalid=0" in last, last
    assert "PARSE CHECK COULD NOT RUN" in out and "2 row(s)" in out, out
    assert "bash -n could not run" in out, "the reason is not named"
    assert "NO VERDICT WAS REACHED FOR:" in out, out
    # Each row is NAMED (campaign, subject, label), not only counted.
    assert "mutate_x.py" in out and "[one]" in out and "[two]" in out, out


def test_non_literal_new_is_unchecked_not_an_error(sandbox):
    """`old` gets no latitude, `new` does: a call cannot be read, so the row is unchecked."""
    sandbox.campaign("mutate_x.py", [('"row"', '"alpha"', 'compute("x")')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 0, out
    assert "invalid=0" in verdict(out) and "unchecked=1" in verdict(out), verdict(out)


def test_anchor_finding_and_invalid_mutant_both_print_and_error_outranks_both(sandbox):
    sandbox.campaign(
        "mutate_x.py",
        [
            ('"rotted"', '"no-such-text"', '"X"'),
            ('"stray fi"', '"bravo"', '"fi"'),
        ],
    )
    rc, out = run("--scope", str(sandbox))
    assert rc == 1, out
    assert "ANCHORS THAT NO LONGER RESOLVE" in out
    assert "MUTANTS THAT DO NOT PARSE" in out
    assert "bad=1" in verdict(out) and "invalid=1" in verdict(out), verdict(out)

    path = sandbox / "scripts" / "tests" / "mutate_broken.py"
    path.write_text("this is (not python\n")
    sandbox.track(path)
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert verdict(out).startswith("RESULT: ERROR rc=2")
    assert "invalid=1" in verdict(out), (
        "the finding must still be reported beside the ERROR"
    )


def test_a_stranded_runner_mutant_is_error(sandbox):
    """The checker grades with `scripts/lib/mutate.py`, itself a campaign subject: a sidecar
    beside it means a mutant may be sitting inside the judge, so no verdict is trustworthy."""
    lib = sandbox / "scripts" / "lib"
    lib.mkdir(parents=True)
    (lib / "mutate.py.mutate-backup").write_text("# pristine\n")
    sandbox.campaign("mutate_x.py", [('"row"', '"alpha"', '"ALPHA"')])
    rc, out = run("--scope", str(sandbox))
    assert rc == 2, out
    assert "mutate.py.mutate-backup" in out
    assert verdict(out).startswith("RESULT: ERROR rc=2")


def test_a_checker_without_the_shared_predicate_is_error_not_a_traceback(sandbox):
    """Materialised beside an older `mutate.py` (a rehearsal clone at an old rev), the import
    cannot supply the predicate. That is no verdict, stated as one — never a bare traceback.

    The scope carries a healthy campaign, so the zero-campaign ERROR cannot be what this sees.
    """
    scripts = sandbox / "scripts"
    (scripts / "lib").mkdir(parents=True)
    (scripts / "lib" / "mutate.py").write_text("# an older runner: no check_parse\n")
    copy = scripts / "mutation-anchors-check.py"
    copy.write_text(CHECKER.read_text())
    sandbox.campaign("mutate_x.py", [('"row"', '"alpha"', '"ALPHA"')])
    proc = subprocess.run(
        [sys.executable, str(copy), "--scope", str(sandbox)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert verdict(proc.stdout).startswith("RESULT: ERROR rc=2")
    assert "check_parse" in proc.stdout or "judge_mutant_parse" in proc.stdout


def _materialise_checker(sandbox, mutate_text):
    """A copy of the checker beside a `lib/mutate.py` of the caller's choosing, in the sandbox."""
    scripts = sandbox / "scripts"
    (scripts / "lib").mkdir(parents=True, exist_ok=True)
    (scripts / "lib" / "mutate.py").write_text(mutate_text)
    copy = scripts / "mutation-anchors-check.py"
    copy.write_text(CHECKER.read_text())
    sandbox.campaign("mutate_x.py", [('"row"', '"alpha"', '"ALPHA"')])
    return copy


def _run_copy(copy, sandbox):
    return subprocess.run(
        [sys.executable, str(copy), "--scope", str(sandbox)],
        capture_output=True,
        text=True,
    )


def test_a_sidecar_is_looked_for_before_the_runner_is_imported(sandbox):
    """A sidecar beside `lib/mutate.py` means a mutant may sit inside it. The module here is
    perfectly importable — and stamps a marker when it is — so an ERROR alone proves nothing:
    only the marker's ABSENCE proves the possibly-mutated code was never executed."""
    marker = sandbox / "imported.marker"
    real = (REPO / "scripts" / "lib" / "mutate.py").read_text()
    stamped = real + "\nopen(%r, 'w').close()\n" % str(marker)
    copy = _materialise_checker(sandbox, stamped)
    (sandbox / "scripts" / "lib" / "mutate.py.mutate-backup").write_text("# pristine\n")
    proc = _run_copy(copy, sandbox)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert verdict(proc.stdout).startswith("RESULT: ERROR rc=2")
    assert "mutate.py.mutate-backup" in proc.stdout
    assert not marker.exists(), (
        "the runner was imported although a sidecar sat beside it"
    )


@pytest.mark.parametrize(
    "broken",
    [
        "raise SystemExit(2)\n",
        "def broken(:\n",
        "raise NameError('boom')\n",
    ],
    ids=["system-exit", "syntax-error", "name-error"],
)
def test_a_runner_that_fails_at_import_is_error_not_a_traceback(sandbox, broken):
    """A stranded mutant of `if __name__ == "__main__":` -> `if True:` makes the import raise
    SystemExit(2); the others are the ordinary ways a mutated module dies. None may end without
    a `RESULT:` last line."""
    copy = _materialise_checker(sandbox, broken)
    proc = _run_copy(copy, sandbox)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert verdict(proc.stdout).startswith("RESULT: ERROR rc=2")


def test_a_sidecar_beside_only_the_checkers_own_runner_is_error(
    sandbox, tmp_path_factory
):
    """The checker sits OUTSIDE the scope, and only ITS `lib/mutate.py` has a sidecar: the scope
    carries no `scripts/lib` at all. The checker still judges with that copy, so it must refuse."""
    elsewhere = tmp_path_factory.mktemp("checker").resolve()
    (elsewhere / "lib").mkdir()
    (elsewhere / "lib" / "mutate.py").write_text(
        (REPO / "scripts" / "lib" / "mutate.py").read_text()
    )
    (elsewhere / "lib" / "mutate.py.mutate-backup").write_text("# pristine\n")
    copy = elsewhere / "mutation-anchors-check.py"
    copy.write_text(CHECKER.read_text())
    sandbox.campaign("mutate_x.py", [('"row"', '"alpha"', '"ALPHA"')])
    assert not (sandbox.root / "scripts" / "lib").exists()
    proc = _run_copy(copy, sandbox)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert verdict(proc.stdout).startswith("RESULT: ERROR rc=2")
    assert str(elsewhere / "lib" / "mutate.py.mutate-backup") in proc.stdout


def _load_checker():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mutation_anchors_check_under_test", CHECKER
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_pool_that_cannot_run_falls_back_to_judging_serially(monkeypatch):
    """A sandbox forbidding worker processes (OSError) or a worker killed mid-run
    (BrokenProcessPool) must not cost the verdict — the same predicate judges serially."""
    import concurrent.futures
    import concurrent.futures.process

    module = _load_checker()
    jobs = list(range(module.POOL_MIN_JOBS + 4))
    monkeypatch.setattr(module, "_judge", lambda job: ("verdict", str(job)))
    for failure in (concurrent.futures.process.BrokenProcessPool, OSError):

        def refusing(*args, _failure=failure, **kwargs):
            raise _failure("no workers")

        monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", refusing)
        assert module._judge_all(jobs) == [("verdict", str(j)) for j in jobs], failure


def test_a_pool_that_raises_anything_else_still_falls_back_to_serial(monkeypatch):
    """Python 3.9 without working semaphores raises NotImplementedError from the executor, and a
    worker-side AttributeError escapes `pool.map`: neither may end the run with no RESULT line."""
    import concurrent.futures

    module = _load_checker()
    jobs = list(range(module.POOL_MIN_JOBS + 4))
    monkeypatch.setattr(module, "_judge", lambda job: ("verdict", str(job)))
    for failure in (NotImplementedError, AttributeError):

        def refusing(*args, _failure=failure, **kwargs):
            raise _failure("no semaphores")

        monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", refusing)
        assert module._judge_all(jobs) == [("verdict", str(j)) for j in jobs], failure


def test_a_judge_that_raises_is_CANNOT_RUN_with_a_RESULT_line(
    sandbox, monkeypatch, capsys
):
    """Every path in `main` ends in a RESULT line: a judge raising is a named ERROR row."""
    module = _load_checker()
    sandbox.campaign("mutate_x.py", [('"one"', '"alpha"', '"ALPHA"')])
    runner = module._runner()[0]

    def boom(*args, **kwargs):
        raise UnicodeEncodeError("ascii", "x", 0, 1, "boom")

    monkeypatch.setattr(runner, "judge_mutant_parse", boom)
    rc = module.main(["--scope", str(sandbox)])
    out = capsys.readouterr().out
    assert rc == 2, out
    assert verdict(out).startswith("RESULT: ERROR rc=2"), out
    assert "the judge raised UnicodeEncodeError" in out, out


def test_pool_and_serial_verdicts_agree_with_a_late_unparseable_mutant(sandbox):
    """At >= POOL_MIN_JOBS rows the checker judges in a process pool, which no other sandbox
    row reaches (they carry one or two rows). The unparseable mutant is LATE in the list, so a
    verdict paired with the wrong job — or lost with a chunk — would name another row or none."""
    real = (REPO / "scripts" / "lib" / "mutate.py").read_text()
    lines = "".join("line%02d\n" % n for n in range(20))
    (sandbox / "subject.sh").write_text(lines)
    rows = [('"row%02d"' % n, '"line%02d"' % n, '"LINE%02d"' % n) for n in range(20)]
    rows[17] = ('"row17"', '"line17"', '"if then"')
    outputs = {}
    for name, threshold in (("pool", None), ("serial", "10**9")):
        scripts = sandbox / "scripts"
        (scripts / "lib").mkdir(parents=True, exist_ok=True)
        (scripts / "lib" / "mutate.py").write_text(real)
        text = CHECKER.read_text()
        if threshold:
            assert text.count("POOL_MIN_JOBS = 16") == 1
            text = text.replace("POOL_MIN_JOBS = 16", "POOL_MIN_JOBS = %s" % threshold)
        copy = scripts / ("check_%s.py" % name)
        copy.write_text(text)
        if name == "pool":
            sandbox.campaign("mutate_x.py", rows)
        proc = _run_copy(copy, sandbox)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        outputs[name] = proc.stdout
    assert "row17" in outputs["pool"]
    assert verdict(outputs["pool"]).startswith("RESULT: FAIL rc=1")
    assert "invalid=1" in verdict(outputs["pool"])
    assert outputs["pool"] == outputs["serial"]


# --- against this repo, which is the population the check actually guards ---------------------


def test_this_repo_passes_and_covers_every_floor_campaign():
    rc, out = run("--scope", str(REPO))
    for name in FLOOR:
        assert name in out, "%s was not discovered — the floor is not covered" % name
    assert rc == 0, out
    assert verdict(out).startswith("RESULT: PASS rc=0")
    assert "invalid=0" in verdict(out), "this repo carries a mutant that does not parse"
