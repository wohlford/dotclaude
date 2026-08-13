"""Contract tests for fixture-signing-check.py.

The RED is synthetic on purpose. Tasks 1-3 have already fixed the real exposures by the time
this runs, so a row depending on them would pass for free forever after. Synthetic fixtures
reproduce the defect shape on every future run; Step 4 supplies the one-time evidence that the
checker catches the real historical defect.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUBJECT = REPO / "scripts" / "fixture-signing-check.py"


def _run(scope):
    return subprocess.run(
        [sys.executable, str(SUBJECT), "--scope", str(scope)],
        capture_output=True,
        text=True,
    )


def _mkrepo(tmp_path, files):
    """A throwaway git repo whose tracked content is exactly `files`."""
    r = tmp_path / "r"
    r.mkdir()

    def sub(*a):
        return subprocess.run(
            ["git", "-C", str(r), *a], check=True, capture_output=True, text=True
        )

    sub("init", "-q")
    sub("config", "user.email", "t@e.st")
    sub("config", "user.name", "t")
    sub("config", "commit.gpgsign", "false")
    sub("config", "tag.gpgsign", "false")
    for rel, body in files.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    sub("add", "-A")
    sub("commit", "-qm", "seed")
    return r


def test_flags_a_fixture_missing_tag_gpgsign(tmp_path):
    """The shape of live exposure #1: commit disabled, tag inherited."""
    r = _mkrepo(
        tmp_path,
        {
            "scripts/tests/test_x.sh": 'git init -q "$1"\ngit -C "$1" config commit.gpgsign false\n'
        },
    )
    res = _run(r)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "missing tag.gpgsign" in res.stdout, res.stdout


def test_flags_a_fixture_missing_both(tmp_path):
    """The shape of live exposure #2: neither key disabled."""
    r = _mkrepo(
        tmp_path,
        {"scripts/tests/test_y.py": 'subprocess.run(["git", "init", "-q"], cwd=bed)\n'},
    )
    res = _run(r)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "missing commit.gpgsign, tag.gpgsign" in res.stdout, res.stdout


def test_flags_a_clone_fixture(tmp_path):
    """A cloned repo inherits the signing layer identically to an inited one."""
    r = _mkrepo(tmp_path, {"scripts/tests/test_c.sh": 'git clone -q "$a" "$b"\n'})
    assert _run(r).returncode == 1


def test_detects_creation_through_a_helper_call(tmp_path):
    """git(root, "init", ...) — the word `git` never appears as an argument."""
    r = _mkrepo(
        tmp_path, {"scripts/tests/test_h.py": 'git(root, "init", "-q", "-b", "main")\n'}
    )
    assert _run(r).returncode == 1


def test_passes_a_fixture_disabling_both_inline(tmp_path):
    r = _mkrepo(
        tmp_path,
        {
            "scripts/tests/test_z.sh": 'git init -q "$1"\n'
            'git -C "$1" -c commit.gpgsign=false -c tag.gpgsign=false commit -m x\n'
        },
    )
    assert _run(r).returncode == 0


def test_resolves_config_from_a_same_directory_imported_module(tmp_path):
    """Four recast modules get both keys from an imported sibling and carry no gpgsign text."""
    r = _mkrepo(
        tmp_path,
        {
            "skills/k/tests/helpers.py": '_CFG = ["-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]\n',
            "skills/k/tests/test_a.py": 'from helpers import _CFG\nsubprocess.run(["git", "init", "-q"], cwd=d)\n',
        },
    )
    assert _run(r).returncode == 0, _run(r).stdout


def test_the_import_hop_is_load_bearing(tmp_path):
    """Mutate what the import branch NAMES: strip the sibling's config, expect a violation.

    Without this, the resolution could be inert and the row above would go green anyway.
    """
    r = _mkrepo(
        tmp_path,
        {
            "skills/k/tests/helpers.py": '_CFG = ["-c", "user.name=x"]\n',
            "skills/k/tests/test_a.py": 'from helpers import _CFG\nsubprocess.run(["git", "init", "-q"], cwd=d)\n',
        },
    )
    res = _run(r)
    assert res.returncode == 1, res.stdout
    assert "test_a.py" in res.stdout, res.stdout


def test_a_file_that_never_creates_a_repo_is_out_of_scope(tmp_path):
    """Guard-tokenizer INPUT STRINGS look like executions but create nothing.

    The decoy is paired with a genuine compliant creator on purpose. Alone, it would leave the
    corpus empty, and the zero-creator ERROR — an EARLIER rule — would decide the run before the
    out-of-scope mechanism was ever consulted. The row would then report rc 2 while appearing to
    be about scope, and no verdict in it could move if that mechanism were deleted.
    """
    r = _mkrepo(
        tmp_path,
        {
            "scripts/tests/test_tok.sh": "assert 'git tag -a v1 -m x' 0 'tag text is guard input, never run'\n",
            "scripts/tests/test_real.sh": (
                'git init -q "$1"\n'
                'git -C "$1" config commit.gpgsign false\n'
                'git -C "$1" config tag.gpgsign false\n'
            ),
        },
    )
    res = _run(r)
    assert res.returncode == 0, res.stdout + res.stderr
    # The claim is that the decoy is EXCLUDED, not merely that the run came out clean.
    listed = subprocess.run(
        [sys.executable, str(SUBJECT), "--scope", str(r), "--list"],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert "scripts/tests/test_real.sh" in listed, listed
    assert "scripts/tests/test_tok.sh" not in listed, listed


def test_an_untracked_fixture_is_still_graded(tmp_path):
    """A brand-new violating file is the likeliest offender and is not yet `git add`ed.

    Enumerating from the index alone would bless the tree at exactly the moment nothing has
    ever checked it.
    """
    r = _mkrepo(
        tmp_path,
        {
            "scripts/tests/test_ok.sh": 'git init -q "$1"\n'
            'git -C "$1" config commit.gpgsign false\n'
            'git -C "$1" config tag.gpgsign false\n'
        },
    )
    (r / "scripts" / "tests" / "test_new.sh").write_text('git init -q "$1"\n')
    res = _run(r)
    assert res.returncode == 1, res.stdout
    assert "test_new.sh" in res.stdout, res.stdout


def test_a_path_containing_a_space_is_still_enumerated(tmp_path):
    """Whitespace-splitting the file list drops such a path into two unusable tokens.

    Neither token survives the suffix and TESTISH filters, so the violator lands in neither the
    denominator nor the violations and the summary reads clean. The enumeration must split on NUL.
    """
    r = _mkrepo(tmp_path, {"scripts/tests/test_a b.sh": 'git init -q "$1"\n'})
    res = _run(r)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "test_a b.sh" in res.stdout, res.stdout


def test_git_options_are_order_independent(tmp_path):
    """`-c` before `-C` is the same invocation; a fixed order misses it."""
    r = _mkrepo(
        tmp_path,
        {"scripts/tests/test_o.sh": 'git -c init.defaultBranch=main -C "$d" init -q\n'},
    )
    assert _run(r).returncode == 1


def test_a_bracket_or_paren_inside_an_argument_does_not_hide_the_verb(tmp_path):
    """`str(paths[0])` before the verb — a negated-class bound cannot cross `]` or `)`."""
    r = _mkrepo(
        tmp_path,
        {
            "scripts/tests/test_b.py": 'subprocess.run(["git", "-C", str(paths[0]), "init"])\n',
            "scripts/tests/test_p.py": 'git(str(root), "init", "-q")\n',
        },
    )
    res = _run(r)
    assert res.returncode == 1, res.stdout
    assert "test_b.py" in res.stdout and "test_p.py" in res.stdout, res.stdout


def test_a_crafted_option_run_cannot_stall_the_scan(tmp_path):
    """An ambiguous option group turns this checker into the hang it exists to prevent.

    Spelling the `-c` value as `\\S+=\\S+` lets the first `\\S+` stop at ANY `=` in the token, so a
    token with k of them has k parses and n tokens cost k**n. Measured before the fix: 0.05s,
    0.48s, 4.4s, 31s at n=5..8 — not a slow check but a non-terminating one, reachable from an
    UNTRACKED file since the population includes `--others`.

    The bound is sized from the worst legitimate run actually observed — a full sweep of this
    repo completes in well under a second — not from plausibility. An overrun here is reported as
    a stall, never as a verdict.
    """
    # The trailing `z` is load-bearing: it denies the tail a match, which is what forces the
    # engine to explore every parse. A payload ending in `init` would match immediately.
    payload = "git " + " ".join(["-c a=a=a=a=a=a=a=a=a=a"] * 12) + " z\n"
    r = _mkrepo(
        tmp_path,
        {
            "scripts/tests/test_boom.sh": payload,
            # Paired with a real creator so the corpus is non-empty. Without it the payload
            # matches nothing, `creators` is empty, and the zero-creator ERROR — a rule UPSTREAM
            # of the scan — decides the run before its running time is ever the question.
            "scripts/tests/test_real.sh": (
                'git init -q "$1"\n'
                'git -C "$1" config commit.gpgsign false\n'
                'git -C "$1" config tag.gpgsign false\n'
            ),
        },
    )
    try:
        res = subprocess.run(
            [sys.executable, str(SUBJECT), "--scope", str(r)],
            capture_output=True,
            text=True,
            timeout=20,
        )
    # Reached only when the defect this row exists for is present.
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "fixture-signing-check did not terminate on a crafted option run; "
            "the scan stalled rather than reaching any verdict"
        ) from None
    # It must reach a verdict; which verdict is not this row's claim.
    assert res.returncode in (0, 1), res.stdout + res.stderr


def test_an_unreadable_candidate_is_a_violation_not_a_silent_skip(tmp_path):
    """A file the scan cannot read must fail closed — "could not measure" is never a pass.

    Reachable, not hypothetical: the population comes from git, so a file still in the index but
    gone from the working tree lands here and raises. Skipping it would drop it from BOTH the
    denominator and the violations, so the run would report a clean sweep over a corpus it
    silently shrank — the exact shape this checker exists to make impossible.

    Paired with a compliant creator so the corpus is non-empty; alone, the zero-creator ERROR
    would decide the run before this branch was ever consulted.
    """
    r = _mkrepo(
        tmp_path,
        {
            "scripts/tests/test_real.sh": (
                'git init -q "$1"\n'
                'git -C "$1" config commit.gpgsign false\n'
                'git -C "$1" config tag.gpgsign false\n'
            ),
            "scripts/tests/test_gone.sh": 'git init -q "$1"\n',
        },
    )
    # Tracked, so `git ls-files --cached` still lists it; absent, so reading it raises.
    (r / "scripts" / "tests" / "test_gone.sh").unlink()
    res = _run(r)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "test_gone.sh" in res.stdout, res.stdout
    assert "unreadable" in res.stdout, res.stdout


def test_errors_rather_than_passing_when_it_finds_nothing(tmp_path):
    """Zero creators reports success loudest of all — an ERROR, never a pass."""
    r = _mkrepo(tmp_path, {"scripts/tests/test_none.sh": "echo hi\n"})
    res = _run(r)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "no repo-creating test files" in res.stderr


def test_the_real_repo_is_clean_with_named_members_and_a_real_denominator():
    """The must-still-pass corpus, asserted by NAME as well as by count.

    A rule narrowed to dodge false positives silently stops matching true ones, and a count
    alone cannot see a one-file loss. The named members are the FLOOR; the count only guards
    against wholesale collapse.
    """
    res = _run(REPO)
    assert res.returncode == 0, res.stdout + res.stderr
    listed = subprocess.run(
        [sys.executable, str(SUBJECT), "--scope", str(REPO), "--list"],
        capture_output=True,
        text=True,
    ).stdout.split()
    for member in (
        "scripts/tests/test_publish_brick.sh",  # already-correct, inline+config idiom
        "scripts/tests/test_publish_fold_plan.py",  # already-correct, python helper idiom
        "skills/recast/tests/conftest.py",  # config only via an imported sibling
        "skills/recast/tests/test_recast_state.py",  # creation via clone through a helper
        "scripts/tests/test_pre_push_hook.sh",  # live exposure #1, now fixed
        "scripts/tests/test_prose_diff.py",  # live exposure #2, now fixed
    ):
        assert member in listed, f"{member} dropped out of the population: {listed}"
    # Measured 2026-08-07: 21 creators pre-change, 22 once this test file itself lands (its
    # fixture bodies contain literal `git init`). The floor sits below both so adding a fixture
    # never breaks the row; the named members above are what catch a silent loss.
    assert len(listed) >= 20, f"denominator collapsed to {len(listed)}"
