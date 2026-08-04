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
    "mutate_lib_mutate.py",
    "mutate_markdownlint_config.py",
    "mutate_mutation_anchors_check.py",
    "mutate_prose_diff.py",
    "mutate_run_long.py",
    "mutate_settings_hooks_check.py",
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


# --- against this repo, which is the population the check actually guards ---------------------


def test_this_repo_passes_and_covers_every_floor_campaign():
    rc, out = run("--scope", str(REPO))
    for name in FLOOR:
        assert name in out, "%s was not discovered — the floor is not covered" % name
    assert rc == 0, out
    assert verdict(out).startswith("RESULT: PASS rc=0")
