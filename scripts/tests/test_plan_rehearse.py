"""Tests for scripts/plan-rehearse.py.

Each row names the measured property it pins. The corpus rows at the bottom assert the extractor
against every plan in `plans/`, with a declared floor, so a silently dropped shape shows up as a
rising unpaired count rather than as quieter output.
"""

import importlib.util
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "plan-rehearse.py"


def load():
    spec = importlib.util.spec_from_file_location("plan_rehearse", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pr = load()


def plan(body):
    return textwrap.dedent(body).lstrip("\n")


def make_repo(tmp_path, files=None):
    """A git repo with one base commit, so `--base` resolves and diffs are meaningful."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "dev", str(root)], check=True)
    for key, value in (
        ("user.email", "t@t"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
    ):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    (root / "scripts").mkdir()
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    for name, text in (files or {}).items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    return root


# ---------------------------------------------------------------- extraction shapes


def test_a_run_line_pairs_to_the_expected_below_it():
    checks, _ = pr.extract(
        plan("""
        Run: `echo hi`

        Expected: `hi`.
    """)
    )
    assert [(c.shape, c.command) for c in checks] == [("run-line", "echo hi")]


def test_a_run_line_does_not_swallow_the_prose_aside_after_its_command():
    # Measured on plans/2026-09-17-bulk-edit-kit.md:1296, whose Run: line carries a parenthetical
    # naming a DIFFERENT command. Swallowing it would execute prose.
    checks, _ = pr.extract(
        plan("""
        Run: `stat -c %a x.py` (GNU `stat` is first on PATH here; `-f %Lp` is BSD)

        Expected: `644`.
    """)
    )
    assert checks[0].command == "stat -c %a x.py"


def test_the_inline_run_arrow_expected_form_is_found_at_all():
    # A `^Expected:` scan misses this shape entirely; it was invisible to my first measurement.
    checks, _ = pr.extract(
        plan("""
        Run: `bash t.sh` → Expected: `0 failed`, exit 0.
    """)
    )
    assert [(c.shape, c.command) for c in checks] == [("inline-run-arrow", "bash t.sh")]


def test_a_bash_fence_pairs_as_one_whole_unit_never_a_fragment():
    checks, _ = pr.extract(
        plan("""
        ```bash
        cd scripts
        echo one && echo two
        ```

        Expected: both lines.
    """)
    )
    assert checks[0].shape == "fence"
    assert checks[0].command == "cd scripts\necho one && echo two"
    assert checks[0].lang == "bash"


def test_a_python_fence_is_run_as_python_not_as_shell():
    # Asserting `lang` ALONE let the `python-fence-run-as-bash` mutant survive in any tree without
    # `plans/`: it flips the shape to fence-unknown-language and nulls the command while leaving
    # lang untouched. The shape and the command are what carry the behaviour.
    checks, _ = pr.extract(
        plan("""
        ```python
        print(1 + 1)
        ```

        Expected: `2`.
    """)
    )
    assert checks[0].shape == "fence"
    assert checks[0].command == "print(1 + 1)"
    assert checks[0].lang == "python"


def test_a_fence_in_an_unknown_language_yields_no_command_so_it_is_never_executed():
    # Fail closed: a fence that is not a shell or python is file CONTENT, not a command.
    checks, _ = pr.extract(
        plan("""
        ```yaml
        key: value
        ```

        Expected: the config parses.
    """)
    )
    assert checks[0].shape == "fence-unknown-language"
    assert checks[0].command is None


def test_a_backticked_span_inside_a_bold_step_heading_is_prose_and_stays_unpaired():
    # plans/2026-08-07-fixture-signing-hang.md:94 — the backticks name a config to ADD.
    checks, _ = pr.extract(
        plan("""
        - [ ] **Step 3: Re-run the probe with `git -C "$SB/r" config tag.gpgsign false` added**

        Expected: `commit.gpgsign=false tag.gpgsign=false`
    """)
    )
    assert checks[0].shape == "unpaired"
    assert checks[0].command is None


def test_a_backticked_span_after_the_closing_bold_marker_is_the_command():
    # plans/2026-08-07-fixture-signing-hang.md:98 — four lines from the row above, opposite verdict.
    checks, _ = pr.extract(
        plan("""
        - [ ] **Step 4: Run the suite** — `bash scripts/tests/test_pre_push_hook.sh`

        Expected: zero failures.
    """)
    )
    assert checks[0].shape == "trailing-backtick"
    assert checks[0].command == "bash scripts/tests/test_pre_push_hook.sh"


def test_an_expected_preceded_only_by_prose_is_unpaired_not_silently_dropped():
    checks, _ = pr.extract(
        plan("""
        The campaign owns that file and restores its own snapshot.

        Expected: the suite reports zero failures.
    """)
    )
    assert len(checks) == 1 and checks[0].shape == "unpaired"


# ---------------------------------------------------------------- tasks and taint


def test_a_files_block_that_yields_no_entry_is_marked_unparsed():
    tasks = pr.parse_tasks(
        plan("""
        ### Task 1: Something

        **Files:**
        (described in prose below)
    """).splitlines()
    )
    assert tasks[0].parsed is False


def test_an_unparsed_task_taints_rather_than_reading_as_touching_nothing(tmp_path):
    root = make_repo(tmp_path)
    tasks = [pr.Task(1, 1, (), False)]
    stale, unreadable = pr.unmoved_prerequisites(root, "dev", tasks, 1)
    # An unreadable declaration is NOT a taint reading: it asserts nothing about the tree, and no
    # edit clears it. Returning it in `stale` made 50 of this repo's 223 checks permanently
    # TAINTED while the text told the operator to re-run to clear them.
    assert stale == []
    assert unreadable == [1]


def test_a_declared_path_identical_to_base_is_reported_unmoved(tmp_path):
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    tasks = [pr.Task(1, 1, ("scripts/a.py",), True)]
    assert pr.unmoved_prerequisites(root, "dev", tasks, 1)[0] == ["scripts/a.py"]


def test_a_modified_path_is_not_reported_unmoved(tmp_path):
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    (root / "scripts/a.py").write_text("x = 2\n", encoding="utf-8")
    tasks = [pr.Task(1, 1, ("scripts/a.py",), True)]
    assert pr.unmoved_prerequisites(root, "dev", tasks, 1)[0] == []


def test_an_untracked_new_file_counts_as_applied_though_git_diff_cannot_see_it(
    tmp_path,
):
    # The defect that motivated this tool was an UNTRACKED file. `git diff --quiet <base> -- path`
    # exits 0 for a path absent at base, so a diff-only reading would call this unmoved and taint
    # every check behind it.
    root = make_repo(tmp_path)
    (root / "scripts/new.py").write_text("x = 1\n", encoding="utf-8")
    tasks = [pr.Task(1, 1, ("scripts/new.py",), True)]
    assert pr.unmoved_prerequisites(root, "dev", tasks, 1)[0] == []


def test_later_tasks_do_not_taint_an_earlier_tasks_check(tmp_path):
    root = make_repo(tmp_path, {"scripts/b.py": "x = 1\n"})
    tasks = [pr.Task(1, 1, (), True), pr.Task(2, 50, ("scripts/b.py",), True)]
    assert pr.unmoved_prerequisites(root, "dev", tasks, 1)[0] == []


# ---------------------------------------------------------------- pre-flight refusals


@pytest.mark.parametrize(
    "command",
    [
        "git push origin dev",
        "git tag -a v1.0.0 -m x",
        "git commit -m 'x'",
        "curl https://example.invalid",
        "ssh host true",
    ],
)
def test_a_command_that_would_escape_the_clone_is_refused(tmp_path, command):
    root = make_repo(tmp_path)
    assert pr.escape_reason(command, root) is not None


def test_an_absolute_path_outside_the_clone_is_refused(tmp_path):
    root = make_repo(tmp_path)
    reason = pr.escape_reason("wc -l /Users/someone/repo/CLAUDE.md", root)
    assert reason and "outside the clone" in reason


def test_a_git_C_target_outside_the_clone_is_refused(tmp_path):
    # Step 5 names this exactly: a check aimed at the real checkout grades the unedited tree
    # and passes WRONGLY, which is worse than failing.
    root = make_repo(tmp_path)
    reason = pr.escape_reason("git -C /Users/someone/repo status", root)
    assert reason and "outside the clone" in reason


def test_an_absolute_path_inside_the_clone_is_allowed(tmp_path):
    root = make_repo(tmp_path)
    assert pr.escape_reason(f"wc -l {root}/seed.txt", root) is None


def test_a_relative_command_is_allowed(tmp_path):
    root = make_repo(tmp_path)
    assert pr.escape_reason("python3 -m pytest -q scripts/tests", root) is None


# ---------------------------------------------------------------- running


def test_a_check_runs_inside_the_clone_not_the_caller_cwd(tmp_path):
    root = make_repo(tmp_path)
    check = pr.Check(1, None, "run-line", "pwd", "bash", "the clone")
    rc, out = pr.run_check(check, root, 30)
    assert rc == 0 and Path(out).resolve() == root.resolve()


def test_a_check_that_overruns_its_bound_is_unrun_not_a_verdict(tmp_path):
    # An overrun must cost attention, never a score.
    root = make_repo(tmp_path)
    check = pr.Check(1, None, "run-line", "sleep 5", "bash", "nothing")
    rc, out = pr.run_check(check, root, 1)
    assert rc is None and "exceeded" in out


def test_a_python_check_is_run_by_the_running_interpreter(tmp_path):
    root = make_repo(tmp_path)
    check = pr.Check(1, None, "fence", "print(6 * 7)", "python", "`42`")
    rc, out = pr.run_check(check, root, 30)
    assert rc == 0 and out.strip() == "42"


# ---------------------------------------------------------------- tree state


def test_an_untracked_file_makes_the_tree_read_as_edited(tmp_path):
    root = make_repo(tmp_path)
    assert pr.tree_state(root, "dev").edited is False
    (root / "brand_new.py").write_text("x = 1\n", encoding="utf-8")
    state = pr.tree_state(root, "dev")
    assert state.edited is True
    assert state.diffstat == ""  # git diff is blind to it
    assert "brand_new.py" in state.porcelain  # the index is not


# ---------------------------------------------------------------- end to end


def run_cli(plan_text, clone, tmp_path, *extra):
    p = tmp_path / "plan.md"
    p.write_text(plan_text, encoding="utf-8")
    proc = subprocess.run(
        [
            "python3",
            str(MODULE),
            "--plan",
            str(p),
            "--clone",
            str(clone),
            "--base",
            "dev",
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=str(clone),
        check=False,
    )
    return proc


def test_the_defect_this_tool_exists_for_is_reproduced_and_flagged(tmp_path):
    # The real 2026-09-17 finding: GNU `stat -f` is a VALID flag meaning "filesystem info", so the
    # command RUNS and exits 1 rather than erroring as an unknown flag. Nothing static catches it.
    root = make_repo(tmp_path)
    (root / "scripts/widget.py").write_text("x = 1\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        ### Task 1: Add it

        **Files:**
        - Create: `scripts/widget.py`

        Run: `stat -f %Lp scripts/widget.py`

        Expected: `644`.
    """),
        root,
        tmp_path,
    )
    assert "flagged=1" in proc.stdout
    assert "expects the literal '644' but exited 1" in proc.stdout


def test_the_corrected_form_of_that_same_check_is_not_flagged(tmp_path):
    # The control: a green row here and a flagged row above is the only evidence the flag
    # discriminates rather than firing on everything.
    root = make_repo(tmp_path)
    (root / "scripts/widget.py").write_text("x = 1\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        ### Task 1: Add it

        **Files:**
        - Create: `scripts/widget.py`

        Run: `stat -c %a scripts/widget.py`

        Expected: `644`.
    """),
        root,
        tmp_path,
    )
    assert "flagged=0" in proc.stdout


def test_an_unedited_clone_is_indeterminate_however_many_checks_ran(tmp_path):
    root = make_repo(tmp_path)
    proc = run_cli(
        plan("""
        Run: `true`

        Expected: silence.
    """),
        root,
        tmp_path,
    )
    assert "RESULT: INDETERMINATE" in proc.stdout
    assert "edited=no" in proc.stdout
    assert "every check below graded the BASE tree" in proc.stdout


def test_the_word_PASS_never_appears_in_the_tools_verdict_line(tmp_path):
    # Load-bearing: a RESULT: PASS line would be quoted as "the plan's checks pass", which nothing
    # mechanical can support. The token is absent from the vocabulary, not merely unused here.
    root = make_repo(tmp_path)
    (root / "x.txt").write_text("edited\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        Run: `echo PASSED-BY-THE-SUBJECT`

        Expected: anything.
    """),
        root,
        tmp_path,
    )
    verdict = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT:")]
    assert len(verdict) == 1
    assert "PASS" not in verdict[0]


def test_list_mode_executes_nothing(tmp_path):
    root = make_repo(tmp_path)
    (root / "x.txt").write_text("edited\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        Run: `touch SHOULD-NOT-EXIST`

        Expected: nothing.
    """),
        root,
        tmp_path,
        "--list",
    )
    assert not (root / "SHOULD-NOT-EXIST").exists()
    assert "RESULT: INDETERMINATE" in proc.stdout


def test_a_missing_plan_is_an_error_with_a_verdict_line(tmp_path):
    root = make_repo(tmp_path)
    proc = subprocess.run(
        [
            "python3",
            str(MODULE),
            "--plan",
            str(tmp_path / "nope.md"),
            "--clone",
            str(root),
            "--base",
            "dev",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "RESULT: ERROR rc=2" in proc.stdout


def test_a_clone_that_is_not_a_git_repo_is_an_error(tmp_path):
    (tmp_path / "notrepo").mkdir()
    p = tmp_path / "plan.md"
    p.write_text("Expected: x\n", encoding="utf-8")
    proc = subprocess.run(
        [
            "python3",
            str(MODULE),
            "--plan",
            str(p),
            "--clone",
            str(tmp_path / "notrepo"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # Assert the DIAGNOSTIC, not just the verdict. Once `--base` validation landed, a non-repo
    # clone reached ERROR rc=2 either way, so the check no longer decides the outcome — it decides
    # which message the operator gets, and "not a git repo" is the one that names the real problem.
    # A mutation of it survived a green suite for exactly this reason.
    assert proc.returncode == 2 and "RESULT: ERROR rc=2" in proc.stdout
    assert "not a git repo" in proc.stderr, proc.stderr


# ---------------------------------------------------------------- the corpus


CORPUS = REPO / "plans"


@pytest.mark.skipif(
    not CORPUS.is_dir(), reason="plans/ is gitignored and absent in a fresh clone"
)
def test_the_corpus_extraction_holds_its_declared_floor():
    """Measured 2026-09-17 over every plan in `plans/`: 223 checks, 213 paired, 10 unpaired.

    A silently dropped shape raises the unpaired count; a falsely widened matcher lowers it.
    Both directions are red, which is why this asserts the number rather than a minimum.
    """
    total = paired = 0
    for p in sorted(CORPUS.glob("*.md")):
        for c in pr.extract(p.read_text(encoding="utf-8"))[0]:
            total += 1
            paired += c.command is not None
    assert total >= 223, f"found {total} checks; the corpus only grows"
    assert total - paired == 10, f"{total - paired} unpaired, expected exactly 10"


@pytest.mark.skipif(
    not CORPUS.is_dir(), reason="plans/ is gitignored and absent in a fresh clone"
)
def test_every_corpus_plan_extracts_without_raising():
    for p in sorted(CORPUS.glob("*.md")):
        pr.extract(p.read_text(encoding="utf-8"))


# ------------------------------------------------- the committed corpus (always runs)
#
# The live `plans/` rows above SKIP in a fresh clone, because plans/ is gitignored (measured then: 35 passed,
# 2 skipped; the suite has grown since). These rows cover the same ground from committed excerpts, so shape coverage
# never depends on content the repo does not carry. They complement the live rows; they do not
# replace them, because a hand-copied corpus goes stale while still reading as authoritative.

FIXTURES = importlib.util.spec_from_file_location(
    "plan_corpus", Path(__file__).parent / "fixtures" / "plan_corpus.py"
)
plan_corpus = importlib.util.module_from_spec(FIXTURES)
FIXTURES.loader.exec_module(plan_corpus)


@pytest.mark.parametrize("shape", sorted(plan_corpus.BY_SHAPE))
def test_each_committed_excerpt_extracts_as_the_shape_it_is_filed_under(shape):
    # The excerpts are now VERBATIM, so several are REFUSED by escape_reason — that is data, not a
    # defect. These rows assert what `extract` makes of the text, never what the gate decides.
    checks, _ = pr.extract(plan_corpus.BY_SHAPE[shape])
    assert [c.shape for c in checks] == [shape]


def test_every_shape_in_the_modules_vocabulary_has_a_corpus_excerpt():
    """The declared floor: a shape added to the module without an excerpt fails here.

    `SHAPES` is asserted at Check construction, so it cannot drift into an inert list — and this
    row is what stops the corpus falling behind it. Discovery cannot detect absence; this is the
    absence detector.
    """
    assert set(plan_corpus.BY_SHAPE) == set(pr.SHAPES)


def test_a_shape_outside_the_vocabulary_is_refused_at_construction():
    # Watch the guard fire: without this the SHAPES check could be deleted and nothing would move.
    with pytest.raises(ValueError, match="unknown check shape"):
        pr.make_check(1, None, "invented-shape", "true", "bash", "x")


def test_the_corpus_records_which_excerpts_are_synthesized_rather_than_measured():
    # One shape has no real instance in any committed plan. Marking it is what stops a reader
    # citing the corpus as evidence that every shape occurs in practice.
    assert plan_corpus.SYNTHESIZED == {"fence-unknown-language"}
    assert plan_corpus.SYNTHESIZED < set(plan_corpus.BY_SHAPE), (
        "a marked shape must be present"
    )


def test_the_run_line_aside_excerpt_pairs_to_the_command_not_the_parenthetical():
    # Verbatim, this excerpt has NO blank line between its Run: and Expected: lines — the previous
    # hand-copied version inserted one, changing the very input shape the extractor is tested on.
    checks, _ = pr.extract(plan_corpus.EXTRA["run-line-with-aside"])
    assert checks[0].command == "stat -c %a scripts/lib/bulk_edit.py"


def test_the_python_fence_excerpt_is_tagged_python():
    checks, _ = pr.extract(plan_corpus.EXTRA["fence-python"])
    assert checks[0].shape == "fence"
    assert checks[0].command is not None
    assert checks[0].lang == "python"


def test_the_heading_description_excerpt_stays_unpaired():
    # The backticks sit INSIDE the bold span, describing a config to add rather than naming a
    # command. This excerpt is the corpus member filed under `unpaired`.
    checks, _ = pr.extract(plan_corpus.BY_SHAPE["unpaired"])
    assert checks[0].shape == "unpaired" and checks[0].command is None


def test_the_prose_continuation_excerpt_stays_unpaired():
    checks, _ = pr.extract(plan_corpus.EXTRA["unpaired-prose"])
    assert checks[0].shape == "unpaired" and checks[0].command is None


# ------------------------------------------------- rows the mutation campaign demanded
#
# Six mutants survived the first campaign, and every one of them named `rehearse()`'s
# orchestration rather than a helper: the helpers were tested directly and the row states they
# produce were not. One survivor was not a suite gap at all — see the escape rows below.


def test_a_line_with_two_backticked_spans_is_too_ambiguous_to_pair():
    # Mutant `bare-backtick-ambiguous-ok` survived: nothing exercised a >2-backtick line.
    checks, _ = pr.extract(
        plan("""
        run `a.sh` or perhaps `b.sh`

        Expected: one of them succeeds.
    """)
    )
    assert checks[0].shape == "unpaired" and checks[0].command is None


def test_a_relative_C_target_outside_the_clone_is_refused(tmp_path):
    # Mutant `git-C-escape-ok` survived because the branch was INERT: every absolute target was
    # already caught by the absolute-path loop, and every relative one failed `startswith("/")`.
    # So a relative target escaped undetected. The branch now resolves against the clone.
    root = make_repo(tmp_path)
    assert pr.escape_reason("git -C ../other status", root) is not None
    assert pr.escape_reason("git -C .. status", root) is not None


def test_a_relative_cd_out_of_the_clone_is_refused(tmp_path):
    root = make_repo(tmp_path)
    assert pr.escape_reason("cd .. && ls", root) is not None


def test_relative_paths_inside_the_clone_still_pass(tmp_path):
    # The blocked corpus must keep blocking, and the allowed corpus must keep being allowed:
    # resolving targets is a WIDER check, so this is what proves it did not over-block.
    root = make_repo(tmp_path)
    (root / "scripts").mkdir(exist_ok=True)
    assert pr.escape_reason("git -C scripts status", root) is None
    assert pr.escape_reason("cd scripts && ls", root) is None
    assert pr.escape_reason("python3 -m pytest -q scripts/tests", root) is None


def test_a_refused_command_is_reported_and_never_executed(tmp_path):
    # Mutant `refused-runs-anyway` survived: escape_reason was tested directly, but nothing
    # asserted that rehearse() turns a refusal into a row INSTEAD of running the command.
    #
    # The fixture command must be HARMLESS WHEN EXECUTED, because a campaign row that disables
    # this very refusal will execute it. Measured: an earlier version of this row carried a real
    # push, and the mutant ran it — it reached nothing only because the fixture repo defines no
    # remote, which is luck, not design. `touch` of a path outside the clone is refused for the
    # same reason a push is, and does nothing worse than create the file the row looks for.
    root = make_repo(tmp_path)
    (root / "edited.txt").write_text("x\n", encoding="utf-8")
    sentinel = (
        tmp_path / "MUST-NOT-EXIST"
    )  # outside the clone, inside the test's own tmp dir
    proc = run_cli(f"Run: `touch {sentinel}`\n\nExpected: nothing.\n", root, tmp_path)
    assert "REFUSED" in proc.stdout
    assert "refused=1" in proc.stdout
    # REFUSED was the one state of the four never pinned to the verdict: mutating the `complete`
    # expression to forgive refusals left the suite green, so a plan whose every check was refused
    # would have printed a clean rehearsal.
    assert "RESULT: INDETERMINATE" in proc.stdout
    assert not sentinel.exists()


def test_a_tainted_check_is_reported_as_a_row_not_merely_computed(tmp_path):
    # Mutant `taint-off` survived: unmoved_prerequisites was tested directly, rehearse() was not.
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    (root / "other.txt").write_text("edited\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        ### Task 1: Change it

        **Files:**
        - Modify: `scripts/a.py`

        Run: `true`

        Expected: silence.
    """),
        root,
        tmp_path,
    )
    assert "TAINTED" in proc.stdout
    assert "tainted=1" in proc.stdout
    assert "RESULT: INDETERMINATE" in proc.stdout


def test_an_unpaired_expected_appears_as_a_row_in_the_report(tmp_path):
    # Mutant `unpaired-dropped` survived: extraction was asserted, the REPORT was not. An
    # Expected: that vanishes from the report is the silent-skip this tool exists to prevent.
    root = make_repo(tmp_path)
    (root / "edited.txt").write_text("x\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        the campaign owns that file and restores its own snapshot.

        Expected: the suite reports zero failures.
    """),
        root,
        tmp_path,
    )
    assert "UNPAIRED" in proc.stdout
    assert "unpaired=1" in proc.stdout
    assert "RESULT: INDETERMINATE" in proc.stdout


def test_a_plan_with_no_checks_at_all_is_indeterminate_never_complete(tmp_path):
    # Mutant `empty-plan-reads-complete` survived. A plan carrying zero Expected: lines reporting
    # RAN rc=0 is a vacuous pass — the exact shape of "a discovery matching nothing reports success
    # loudest of all". The non-zero denominator is what this row asserts.
    root = make_repo(tmp_path)
    (root / "edited.txt").write_text("x\n", encoding="utf-8")
    proc = run_cli(
        "# A plan with prose only\n\nNothing to check here.\n", root, tmp_path
    )
    assert "checks=0" in proc.stdout
    assert "RESULT: INDETERMINATE" in proc.stdout
    assert proc.returncode == 1


def test_a_tainted_check_still_runs_so_the_base_tree_reading_survives(tmp_path):
    """Found by dogfooding: taint used to short-circuit BEFORE the run.

    On a real plan against a fresh clone that made every check TAINTED and nothing ran, so the
    tool was inert until edits landed — while its own spec claimed tree-independent defects
    surface at base with no operator input. Two of step 5's five unsatisfiable shapes are exactly
    that kind. The row must still say TAINTED (nothing reads as rehearsed) AND still carry the
    exit status and output.
    """
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    (root / "other.txt").write_text("edited\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        ### Task 1: Change it

        **Files:**
        - Modify: `scripts/a.py`

        Run: `printf 'BASE-READING-%s' SURVIVED`

        Expected: `BASE-READING-SURVIVED`.
    """),
        root,
        tmp_path,
    )
    assert "TAINTED" in proc.stdout
    assert "tainted=1" in proc.stdout
    assert "rc=0" in proc.stdout
    # Assert on the OUTPUT-PREFIXED line, and use a command whose text differs from its output.
    # The previous form matched the tool's own `$ <command>` echo, so deleting output printing
    # altogether left the suite green — the matcher was matching text I authored, not the subject.
    assert any(
        line.strip().startswith("| BASE-READING-SURVIVED")
        for line in proc.stdout.splitlines()
    ), proc.stdout
    assert "RESULT: INDETERMINATE" in proc.stdout  # and it is still not a rehearsal


def test_a_tainted_check_that_cannot_answer_is_flagged_at_base(tmp_path):
    # The whole point of the row above: an instrument error is tree-INDEPENDENT, so it must be
    # visible on a clone where nothing has been applied yet. This is the `stat -f %Lp` class.
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    (root / "other.txt").write_text("edited\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        ### Task 1: Change it

        **Files:**
        - Modify: `scripts/a.py`

        Run: `stat -f %Lp scripts/a.py`

        Expected: `644`.
    """),
        root,
        tmp_path,
    )
    assert "TAINTED" in proc.stdout
    assert "expects the literal '644' but exited 1" in proc.stdout


def test_create_builds_a_clone_with_no_remote_and_signing_off(tmp_path):
    """`--create` had ZERO coverage, and it is the entry point step 5 now tells the model to use.

    The campaign excluded its `tag.gpgsign` line on the stated grounds that `commit.gpgsign`
    beside it was observable through the suite — measured false: deleting either line left the
    suite green, because nothing touched create_clone at all.
    """
    source = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    clone = tmp_path / "made"
    err = pr.create_clone(source, clone, "dev", "dev")
    assert err is None, err
    assert (clone / ".git").is_dir()
    remotes = subprocess.run(
        ["git", "-C", str(clone), "remote"], capture_output=True, text=True, check=True
    )
    assert remotes.stdout.strip() == "", "the remote must be dropped"
    for key in ("commit.gpgsign", "tag.gpgsign"):
        got = subprocess.run(
            ["git", "-C", str(clone), "config", "--get", key],
            capture_output=True,
            text=True,
            check=False,
        )
        assert got.stdout.strip() == "false", f"{key} must be false, got {got.stdout!r}"


def test_create_refuses_an_existing_directory(tmp_path):
    source = make_repo(tmp_path)
    clone = tmp_path / "already"
    clone.mkdir()
    assert pr.create_clone(source, clone, "dev", "dev") is not None


def test_help_exits_zero_and_prints_no_error_verdict():
    # argparse raises SystemExit(0) for --help; treating every SystemExit as a usage failure made
    # `--help` print `RESULT: ERROR rc=2`, which a --expect harness would record as a failed run.
    proc = subprocess.run(
        ["python3", str(MODULE), "--help"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0
    assert "RESULT: ERROR" not in proc.stdout


def test_a_flagged_finding_on_a_tainted_row_reaches_the_terminal_line(tmp_path):
    # `flagged=` counted RAN rows only, so at base — where every row is TAINTED by design — it was
    # structurally 0 for exactly the population the base reading exists to surface. The body
    # carried the finding while the terminal line said flagged=0, and a reader skims the line.
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    (root / "other.txt").write_text("edited\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        ### Task 1: Change it

        **Files:**
        - Modify: `scripts/a.py`

        Run: `stat -f %Lp scripts/a.py`

        Expected: `644`.
    """),
        root,
        tmp_path,
    )
    assert "TAINTED" in proc.stdout
    assert "flagged=1" in proc.stdout


def test_the_gate_refuses_every_escape_the_branch_review_found(tmp_path):
    """A blocked corpus, kept as a corpus so a future narrowing has to face all of it at once.

    Every row reproduced as an ALLOWED command against the first draft of the gate. They are
    asserted as a set because narrowing a matcher silently drops true positives, and the author
    writes tests from the false positive rather than from what quietly left.
    """
    root = make_repo(tmp_path)
    sibling = str(root) + "-evil/secret"
    must_refuse = [
        "echo drift >/etc/hosts",
        "echo drift >>/etc/hosts",
        "rm -rf ~/scratch-thing",
        "cd ~ && ls",
        "cd && pwd",
        "git -C ~/repo/x status",
        "echo $(/bin/echo hi)",
        "git -c user.name=x push origin dev",
        "git --no-pager push origin dev",
        "git -C . tag -a v9.9.9 -m x",
        "git -C . commit -m x",
        "git -C .. status",
        "cat ../../CLAUDE.md",
        "echo hi > ../escaped.txt",
        "bash ../real-repo/scripts/audit.sh",
        "pushd ../.. && ls",
        "git fetch origin dev",
        "pip install requests",
        "rsync -a x host:/y",
        f"cat {sibling}",
    ]
    still_allowed = [c for c in must_refuse if pr.escape_reason(c, root) is None]
    assert still_allowed == [], f"these escaped the gate: {still_allowed}"


def test_the_gate_still_allows_ordinary_in_clone_checks(tmp_path):
    # The other half of the trade: widening a refusal moves legitimate checks into the hand-read
    # bucket, and only an allowed corpus makes that cost visible.
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    must_allow = [
        "python3 -m pytest -q scripts/tests",
        "git -C scripts status",
        "git -C . status",
        "cd scripts && ls",
        "bash scripts/tests/test_x.sh 2>&1 | tail -5",
        "ruff check scripts/a.py",
        "rc=$?; echo $rc",
        "grep -c foo scripts/a.py >/dev/null",
        "true",
    ]
    blocked = [(c, pr.escape_reason(c, root)) for c in must_allow]
    assert [c for c, r in blocked if r is not None] == [], f"over-blocked: {blocked}"


def test_the_gate_refuses_shapes_the_first_blocked_corpus_missed(tmp_path):
    """Four campaign mutants survived a green suite; these are the rows they demanded.

    Each names a check reachable by NO other branch: `$HOME` (no corpus row used a variable),
    backtick substitution (none used one), a redirect to `..` (the path scan cannot see a token
    with no slash), and a bare `..` argument, which was a live hole — `rm -rf ..` was ALLOWED.
    """
    root = make_repo(tmp_path)
    for command in [
        "cat $HOME/.ssh/id_rsa",
        "echo `cat /etc/hosts`",
        "echo x > ..",
        "rm -rf ..",
        "echo $(rm -rf ..)",
    ]:
        assert pr.escape_reason(command, root) is not None, command


def test_a_redirect_target_outside_the_clone_is_refused(tmp_path):
    # `..` carries no slash, so a naive path scan could not see it. The rebuilt gate tokenizes, so
    # a redirect target is its own token and is resolved like any other.
    root = make_repo(tmp_path)
    assert pr.escape_reason("echo x > ..", root) is not None
    assert pr.escape_reason("echo x >/etc/hosts", root) is not None
    assert pr.escape_reason("echo x >/dev/null", root) is None


def test_a_command_inside_a_substitution_is_judged_as_a_command(tmp_path):
    """The rebuild reaches INTO `$( )` instead of refusing the whole construct.

    Measured on the tokenizer: `echo $(curl ...)` yields the command words ['echo', 'curl'], so the
    inner command is judged in command position. Backticks are a STATED LIMITATION — the tokenizer
    does not split them (`echo ` + backtick + `curl ...` yielded ['echo'] alone), so they are
    refused outright rather than half-read.
    """
    root = make_repo(tmp_path)
    reason = pr.escape_reason("echo $(curl http://example.invalid)", root)
    assert reason is not None and "reaches the network" in reason, reason
    # A CONTAINED command inside a substitution is not refused merely for being in one.
    assert pr.escape_reason("echo $(date)", root) is None
    backtick = pr.escape_reason("echo " + chr(96) + "date" + chr(96), root)
    assert backtick is not None and "backtick" in backtick, backtick


# Each row asserts WHICH branch refused, not merely that something did. Three earlier rows passed
# on a branch they did not name — `cat $HOME/.ssh/id_rsa` was refused as "reaches the network"
# because the matcher hit the `.ssh` PATH COMPONENT — so the mutants written for them survived a
# green suite. A RED proves only that SOMETHING failed.
@pytest.mark.parametrize(
    "command,because",
    [
        ("cat $HOME/secrets.txt", "$HOME"),
        ("echo `date`", "backtick substitution"),
        ("rm -rf ..", "path outside the clone"),
        ("cat /etc/hosts", "path outside the clone"),
        ("cd ~ && ls", "~"),
        ("cd && pwd", "bare `cd`"),
        ("git -c user.name=x push origin main", "runs `git push`"),
        ("curl https://example.invalid", "reaches the network"),
        ("pip install requests", "installs packages"),
        # A command INSIDE a substitution reaches command position and is judged as a command,
        # instead of the whole construct being refused wholesale.
        ("echo $(curl https://example.invalid)", "reaches the network"),
        ("echo $(cat /etc/hosts)", "path outside the clone"),
        # Every row below was ALLOWED by the previous gate — each a measured regression from ITS
        # predecessor, caused by anchoring escapes to the start of the RAW TEXT rather than
        # matching tokens in command position.
        ("set -e\ngit push origin main", "runs `git push`"),
        ("cd scripts\ncurl -sS http://example.invalid", "reaches the network"),
        ("env FOO=1 git push origin main", "runs `git push`"),
        ("sh -c 'curl http://example.invalid'", "command string this gate cannot read"),
        ("echo x | xargs -I{} git push origin main", "cannot read"),
        ("  git push origin main", "runs `git push`"),
        ("X=/etc/passwd; cat $X", "assigns a path outside the clone"),
        ("tar --file=/tmp/out.tar .", "path outside the clone"),
    ],
)
def test_each_escape_shape_is_refused_by_the_branch_that_names_it(
    tmp_path, command, because
):
    root = make_repo(tmp_path)
    reason = pr.escape_reason(command, root)
    assert reason is not None, f"not refused at all: {command}"
    assert because in reason, f"{command!r} refused for the wrong reason: {reason!r}"


def test_a_tool_name_inside_a_path_component_is_not_read_as_that_tool(tmp_path):
    # `\bssh\b` matched `.ssh` in a path, so any command naming an ssh directory read as reaching
    # the network. A name only RUNS in command position; anywhere else it is an argument.
    root = make_repo(tmp_path)
    reason = pr.escape_reason("cat $HOME/.ssh/id_rsa", root)
    assert reason is not None
    assert "network" not in reason, f"path component read as a command: {reason!r}"


def test_an_in_clone_path_naming_a_networking_tool_is_allowed(tmp_path):
    # The other direction of the same defect: a legitimate in-clone path must not be refused just
    # because a tool name appears inside it.
    root = make_repo(tmp_path, {"scripts/nc/helper.sh": "echo hi\n"})
    assert pr.escape_reason("bash scripts/nc/helper.sh", root) is None


# The module's docstring claims extraction "prefers a miss to a guess". The branch review found
# three branches where that was FALSE — each would have executed the EXPECTATION or surrounding
# prose as a shell command. All three were latent (0 instances in the 223-check corpus), which is
# why nothing caught them; the corpus floor is unchanged at 223/213/10 after the fix.


def test_a_run_line_with_no_backticked_span_is_unpaired_not_prose_executed():
    # Was: the whole remainder became the command, so
    # "Run the audit and compare: the summary against base" yielded `the summary against base`.
    checks, _ = pr.extract(
        "Run the audit and compare: the summary against base\n\nExpected: they agree.\n"
    )
    assert checks[0].shape == "unpaired"
    assert checks[0].command is None


def test_a_bare_fence_is_not_assumed_to_be_shell():
    # A fence declaring no language is as likely to hold expected OUTPUT as a command. Was:
    # a block containing `0 failed` was extracted as a command and would have been run.
    checks, _ = pr.extract("```\n0 failed\n```\n\nExpected: as shown.\n")
    assert checks[0].shape == "fence-unknown-language"
    assert checks[0].command is None


def test_a_backticked_span_inside_a_heading_sentence_is_not_the_command():
    # `rfind("**")` found the OPENING marker on a heading with one pair, so everything after it —
    # including the expectation literal — read as the command. The tail must BE the span.
    text = (
        "**Step 6: Verify** the summary line must read `0 failed`\n"
        "\nExpected: `0 failed`.\n"
    )
    checks, _ = pr.extract(text)
    assert checks[0].shape == "unpaired"
    assert checks[0].command is None


def test_the_real_trailing_command_form_still_pairs():
    # The other direction: narrowing must not drop the shape the corpus actually contains.
    text = (
        "- [ ] **Step 4: Run the suite** \u2014 `bash scripts/tests/test_x.sh`\n"
        "\nExpected: zero failures.\n"
    )
    checks, _ = pr.extract(text)
    assert checks[0].shape == "trailing-backtick"
    assert checks[0].command == "bash scripts/tests/test_x.sh"


# Round 8 of the campaign left 8 survivors. Probing each for "can this branch change any outcome"
# split them three ways: three named REDUNDANT branches (removed — the invocation walk's directory
# fields raced the token scan for the same verdict), three named live code with no row (below),
# and one had a rotted anchor. Redundancy is why a mutation survives a suite that is not weak.


def test_a_command_the_tokenizer_cannot_parse_is_refused_not_run(tmp_path):
    # Failing OPEN here would be indistinguishable from judging the command safe, which is the one
    # outcome a gate must never produce for input it could not read.
    root = make_repo(tmp_path)
    unbalanced = "echo " + chr(39) + "unbalanced"
    reason = pr.escape_reason(unbalanced, root)
    assert reason is not None
    assert "cannot be parsed" in reason, reason


def test_a_tool_name_as_a_bare_ARGUMENT_is_not_read_as_that_tool(tmp_path):
    # The complement of the path-component row: `echo curl` names curl but does not RUN it. Only
    # command position decides, and without that distinction the gate refuses plans for mentioning
    # a tool in an argument.
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    assert pr.escape_reason("echo curl", root) is None
    assert pr.escape_reason("echo git", root) is None
    assert pr.escape_reason("grep -n curl scripts/a.py", root) is None


def test_a_directory_option_with_an_equals_value_outside_the_clone_is_refused(tmp_path):
    # `--git-dir=link` fuses option and value into ONE token, so a space-separated scan misses it.
    # A symlink target carries no slash either, so the path scan cannot see it. This is the branch
    # that was deleted as "inert" on a probe set containing no symlink, and it was live.
    root = make_repo(tmp_path)
    (root / "link").symlink_to("/etc")
    assert pr.escape_reason("git --git-dir=link status", root) is not None
    assert pr.escape_reason("git --work-tree=/etc status", root) is not None
    assert pr.escape_reason("git -C link status", root) is not None
    # …and the in-clone forms stay allowed.
    assert pr.escape_reason("git -C scripts status", root) is None


def test_git_in_an_unresolvable_working_directory_is_refused(tmp_path):
    """The branch I deleted as inert and a reviewer proved live.

    My inertness probe ran twelve commands and found none that distinguished it — because none of
    them made a `cd` target unresolvable, which is the only thing this branch decides. A probe is
    only as strong as its inputs, and mine were drawn from the cases I had already thought of.

    The true positive it closes: the working directory comes from FILE CONTENT, so it could be
    anywhere, while every other branch sees only a contained-looking `cd`.
    """
    root = make_repo(tmp_path)
    reason = pr.escape_reason("cd $(cat somefile) && git status", root)
    assert reason is not None, "a git command in an unresolvable cwd must be refused"
    assert "cannot resolve" in reason, reason


def test_a_contained_cd_before_git_is_still_allowed(tmp_path):
    # The other direction: restoring the branch must not refuse an ordinary contained `cd`.
    root = make_repo(tmp_path)
    assert pr.escape_reason("cd scripts && git status", root) is None
    assert pr.escape_reason("git status", root) is None


# ---------------------------------------------------------- the third review's three BLOCKERs
#
# Their shared root: wherever this gate re-derived something `scripts/lib/git_command.py` already
# exports, it re-derived it ONE HARDENING SHORT. The suite could not see any of them because every
# escape fixture in this file was anchored at the start of its text.


@pytest.mark.parametrize(
    "prefix",
    [
        "if true; then {cmd}; fi",
        "{{ {cmd}; }}",
        "for i in 1; do {cmd}; done",
        "while :; do {cmd}; done",
        "! {cmd}",
        "exec {cmd}",
    ],
)
@pytest.mark.parametrize(
    "cmd",
    ["curl http://example.invalid", "eval 'x'", "pip install requests", "cd /etc"],
)
def test_a_command_position_prefix_does_not_defeat_a_refusal(tmp_path, prefix, cmd):
    """A reserved word ends the previous command exactly as `;` does.

    Measured before the fix: 8 of 10 name-based refusals leaked behind a single
    `if true; then … fi`, while a git write in the IDENTICAL position was still refused — because
    that path runs through `iter_git_invocations_with_cwd`, which consults `RESERVED_WORDS`. One
    function, two readings of command position, and only the hand-rolled one was wrong.

    This asserts the PROPERTY (every refusal survives every prefix) rather than a list of
    witnesses, because a witness list is what let the class hide: all 13 rows of the blocked
    corpus were anchored at offset 0.
    """
    root = make_repo(tmp_path)
    assert pr.escape_reason(prefix.format(cmd=cmd), root) is not None, prefix.format(
        cmd=cmd
    )


def test_a_variable_this_gate_knows_does_not_absorb_a_directory_level(tmp_path):
    # `$PWD` IS the clone, but leaving its literal text in place made it count as a directory
    # COMPONENT, so `$PWD/..` resolved back INSIDE the clone. One absorbed level is exactly one
    # `..` laundered — and `rm -rf $PWD/..` deletes the clone's PARENT.
    root = make_repo(tmp_path)
    for command in ["rm -rf $PWD/..", "ls $PWD/..", "cat $PWD/../secret"]:
        assert pr.escape_reason(command, root) is not None, command
    # …while a contained use of the same variable stays allowed.
    assert pr.escape_reason("ls $PWD/scripts", root) is None


def test_an_expansion_this_gate_cannot_read_is_refused_in_any_position(tmp_path):
    """A QUOTED substitution survives tokenization as ONE token.

    So `echo "$(curl …)"` reached no command position at all while the unquoted form did — and the
    quoted spelling is the idiom: 18 of the 213 corpus checks carry one. The docstring even
    directed authors toward it.
    """
    root = make_repo(tmp_path)
    for command in [
        'echo "$(curl http://example.invalid)"',
        "cat $" + chr(39) + "/etc/passwd" + chr(39),
        "cd ${TARGET:-/etc} && ls",
    ]:
        reason = pr.escape_reason(command, root)
        assert reason is not None, command


def test_a_trailing_dollar_is_a_regex_anchor_not_an_expansion(tmp_path):
    # The shell expands nothing at end of word. This was the expansion rule's ONLY false positive
    # over the real corpus — a genuine check, `grep -c 'byte diff);$' …`.
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    assert pr.escape_reason("grep -c 'byte diff);$' scripts/a.py", root) is None


def test_an_unresolvable_base_is_a_precondition_failure_not_a_clean_run(tmp_path):
    """The worst defect this tool has carried: a broken instrument produced its ONE clean verdict.

    `git diff --quiet <bad-rev>` exits 128, which `!= 0` read as "this path differs" — so no
    declared path was ever stale, nothing was TAINTED, every row reached RAN, and the run printed
    `RESULT: RAN rc=0`. Measured against a control on the SAME clone with the SAME tree digest,
    where a resolvable base gave `INDETERMINATE rc=1`. The only surface tell was `base=?`, which
    the tool already computed and used solely as a display string.
    """
    root = make_repo(tmp_path, {"a.txt": "base\n"})
    (root / "unrelated.txt").write_text(
        "x\n", encoding="utf-8"
    )  # a.txt deliberately untouched
    body = plan("""
        ### Task 1: Change it

        **Files:**
        - Modify: `a.txt`

        Run: `true`

        Expected: silence.
    """)
    bad = run_cli(body, root, tmp_path, "--base", "no-such-base")
    assert bad.returncode == 2, bad.stdout
    assert "RESULT: ERROR rc=2" in bad.stdout
    assert "RAN rc=0" not in bad.stdout


def test_a_base_that_cannot_be_compared_taints_rather_than_clears(tmp_path):
    # The origin, asserted at its own level: an unanswerable comparison must taint, never clear.
    root = make_repo(tmp_path, {"a.txt": "base\n"})
    tasks = [pr.Task(1, 1, ("a.txt",), True)]
    stale, _unreadable = pr.unmoved_prerequisites(root, "no-such-base", tasks, 1)
    assert stale, "an uncomparable base must leave the check tainted"
    assert "could not be compared" in stale[0], stale


def test_create_reports_a_failure_to_make_the_base_branch(tmp_path):
    # Discarding this is how a clone ends up without the base everything else compares against.
    source = make_repo(tmp_path, {"a.txt": "x\n"})
    clone = tmp_path / "made"
    err = pr.create_clone(source, clone, "dev", "no-such-base")
    assert err is not None and "does not resolve" in err, err


@pytest.mark.parametrize(
    "command",
    [
        "X=$HOME; cat $X/.ssh/id_rsa",
        "P=$TMPDIR; cat $P/x",
        "HOME_DIR=$HOME; ls $HOME_DIR",
        "D=$(pwd)/..; cd $D",
    ],
)
def test_an_ALIASED_variable_is_judged_like_a_direct_one(tmp_path, command):
    """The same hole one hop away.

    The assignment branch judged an assigned PATH and then `continue`d past the token's own
    `~`/variable checks, so `X=/etc/passwd` was refused while `X=$HOME` was not — the value is
    unresolvable either way, and aliasing it through one name was enough to launder it.
    """
    assert pr.escape_reason(command, make_repo(tmp_path)) is not None, command


def test_a_name_assigned_from_a_substitution_is_not_treated_as_resolvable(tmp_path):
    """The tokenizer SPLITS a substitution, so `X=$(cat f)` arrives as the token `X=$`.

    Recording X as locally-assigned on that basis let `cat $X` through while the real value came
    from file content and could be any path. No mutant named this — it surfaced from probing
    whether a NEIGHBOURING branch was redundant, which is a different question that happened to
    walk past it.
    """
    root = make_repo(tmp_path)
    reason = pr.escape_reason("X=$(cat somefile); cat $X", root)
    assert reason is not None and "$X" in reason, reason


def test_an_assignment_of_an_unreadable_expansion_names_the_assignment(tmp_path):
    # Diagnostic-only now that the rule above covers the class: both paths refuse, and this branch
    # decides WHICH message. Naming the assignment is more actionable than naming the later use.
    root = make_repo(tmp_path)
    reason = pr.escape_reason(
        "X=$" + chr(39) + "/etc/passwd" + chr(39) + "; cat $X", root
    )
    assert reason is not None
    # Either "assigns a path outside the clone" or "assigns an expansion …" — both name the
    # ASSIGNMENT, which is the point: pointing at the later use would send the reader to the
    # wrong line. Which of the two fires depends on whether the value also looks like a path.
    assert reason.startswith("assigns "), reason


def test_an_assignment_of_a_resolvable_value_is_still_allowed(tmp_path):
    # The other direction: in-block temporaries must keep working, or every fence with an
    # `rc=$?` idiom moves into the hand-read bucket.
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    assert pr.escape_reason("rc=$?; echo $rc", root) is None
    assert pr.escape_reason("D=scripts; ls $D", root) is None


@pytest.mark.parametrize(
    "command",
    [
        "sh -c 'curl http://example.invalid'",
        "sh -euc 'curl http://example.invalid'",
        "bash -lc 'curl http://example.invalid'",
        "bash -o pipefail -e -u -c 'curl http://example.invalid'",
        "bash --noprofile --norc -c 'curl http://example.invalid'",
    ],
)
def test_every_spelling_of_a_shell_command_string_is_refused(tmp_path, command):
    """`-c` was matched literally inside a fixed three-token window.

    That missed a short-option CLUSTER (`sh -euc`, `bash -lc` — the latter is what the deleted
    resolution probe itself used) and a window overrun (`bash -o pipefail -e -u -c`, where `-o`
    consumes the following word so a scan stopping at the first non-option token stopped there).
    """
    assert pr.escape_reason(command, make_repo(tmp_path)) is not None, command


def test_a_shell_running_a_FILE_is_still_allowed(tmp_path):
    # Only `-c` takes an opaque command string; `bash script.sh` runs a file the gate can see.
    # Refusing the file form over-blocked an ordinary in-clone check.
    root = make_repo(tmp_path)
    assert pr.escape_reason("bash scripts/tests/test_x.sh", root) is None
    assert pr.escape_reason("bash -n scripts/tests/test_x.sh", root) is None
    assert pr.escape_reason("bash -o pipefail scripts/tests/test_x.sh", root) is None


def test_a_heading_with_only_one_bold_marker_does_not_pair(tmp_path):
    """The input the two-marker guard actually decides.

    Its comment previously cited an example with TWO markers, which the guard never sees — that
    case is decided by the trailing pattern's anchoring. One example was made to justify two
    mechanisms, and the misattribution is why the guard survived the whole suite unexercised.
    """
    tick = chr(96)
    text = f"- [ ] **{tick}bash scripts/tests/test_x.sh{tick}\n\nExpected: zero failures.\n"
    checks, _ = pr.extract(text)
    assert checks[0].shape == "unpaired", checks[0]
    assert checks[0].command is None


def test_the_documented_usage_line_names_every_required_flag():
    """A command written into documentation is unverified until you run it as written.

    `--create` also requires `--branch`, which the usage line omitted — run as documented it
    exits 2. This asserts the header names each flag the code demands.
    """
    header = MODULE.read_text(encoding="utf-8").splitlines()[3]
    assert header.startswith("# Usage:")
    for flag in ("--plan", "--clone", "--create", "--scope", "--branch", "--base"):
        assert flag in header, f"{flag} missing from the usage line: {header}"


def test_create_without_branch_is_a_usage_error(tmp_path):
    # The behaviour the usage line must not misdescribe.
    source = make_repo(tmp_path)
    plan_file = tmp_path / "p.md"
    plan_file.write_text("Run: `true`\n\nExpected: x.\n", encoding="utf-8")
    proc = subprocess.run(
        [
            "python3",
            str(MODULE),
            "--plan",
            str(plan_file),
            "--clone",
            str(tmp_path / "c"),
            "--create",
            "--scope",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "--branch" in proc.stderr


def test_a_python_fence_is_not_judged_as_shell(tmp_path):
    """Reading a Python body with a shell tokenizer produced confident nonsense.

    Three of the four python-fence checks in this repo's plans were refused for "contains backtick
    substitution … use $( )" — the backtick sat inside a Python STRING LITERAL, where it is not
    substitution at all and the remediation is meaningless. An instrument answers ONE question,
    and a body in another language is a different question however alike the two look.
    """
    root = make_repo(tmp_path)
    tick = chr(96)
    body = f"a = 'see {tick}src{tick} for detail'\nprint(a)"
    assert pr.escape_reason(body, root, "python") is None
    # …and the same text read as SHELL is refused, which is the contrast that makes the point.
    assert pr.escape_reason(body, root, "bash") is not None


def test_a_python_fence_still_has_its_paths_judged(tmp_path):
    # The PATH question is language-independent: an absolute path outside the clone means the same
    # thing in either language, so narrowing the shell rules must not drop it.
    root = make_repo(tmp_path)
    assert pr.escape_reason('open("/etc/passwd").read()', root, "python") is not None
    assert pr.escape_reason('p="../secret"; open(p)', root, "python") is not None
    assert pr.escape_reason('open("scripts/a.py").read()', root, "python") is None


def test_every_real_python_fence_in_the_corpus_is_allowed():
    """A corpus row, not a witness: the shell reading refused 3 of these 4 for shell syntax.

    Skipped where `plans/` is absent, like the other live-corpus rows.
    """
    if not CORPUS.is_dir():
        pytest.skip("plans/ is gitignored and absent in a fresh clone")
    import tempfile

    clone = Path(tempfile.mkdtemp()) / "c"
    (clone / "scripts").mkdir(parents=True)
    refused = []
    for p in sorted(CORPUS.glob("*.md")):
        for check in pr.extract(p.read_text(encoding="utf-8"))[0]:
            if check.command is None or check.lang != "python":
                continue
            reason = pr.escape_reason(check.command, clone, check.lang)
            if reason:
                refused.append((f"{p.name}:{check.line}", reason))
    assert refused == [], refused


# ----------------------------------------------------- the fourth review's two BLOCKERs
#
# Both were repairs by DELETION or honesty, not new mechanism. The review's recommendation was to
# land them and then declare the gate's scope rather than harden further; that scope statement now
# lives in escape_reason's docstring and in SKILL.md step 5, and the rows below pin the parts of it
# that are checkable.


def test_an_unreadable_declaration_is_not_reported_as_a_tree_reading(tmp_path):
    """A sentinel no edit can clear was being spelled the same as one every edit clears.

    `unmoved_prerequisites` returned `<task N: Files: unparsed>` inside `stale`, so the row read
    `TAINTED — unchanged since base`, and SKILL.md told the operator to re-run to clear it. For
    those checks that is a loop with no exit, and the reason sends them to redo edits that already
    landed. Measured over this repo's plans: 77 of 305 tasks (25%) declare their files in prose,
    which left 50 of 223 checks (22%) across 11 plans permanently TAINTED.
    """
    root = make_repo(tmp_path, {"scripts/a.py": "x = 1\n"})
    (root / "other.txt").write_text("edited\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        ### Task 1: Something

        **Files:**
        (described in prose below)

        Run: `true`

        Expected: silence.
    """),
        root,
        tmp_path,
    )
    assert "UNREADABLE" in proc.stdout
    assert "unreadable=1" in proc.stdout
    assert "TAINTED" not in proc.stdout, "it must not be spelled as a taint reading"
    assert "re-running will not change that" in proc.stdout
    assert "RESULT: INDETERMINATE" in proc.stdout


def test_widening_the_files_parser_was_measured_and_is_not_the_repair():
    """Recorded so the parser is not re-attempted: no regex recovers this.

    Accepting the other declaration spellings moves 77 unparsed tasks to about 70; the remainder
    are genuinely prose (`- Test: a live subagent probe`, `- Modify: sync tables (regenerated)`).
    The honest repair is to say the declaration could not be read.
    """
    if not CORPUS.is_dir():
        pytest.skip("plans/ is gitignored and absent in a fresh clone")
    unparsed = 0
    total = 0
    for p in sorted(CORPUS.glob("*.md")):
        for t in pr.parse_tasks(p.read_text(encoding="utf-8").splitlines()):
            total += 1
            unparsed += not t.parsed
    assert total > 200, total
    assert unparsed > total // 10, (
        f"only {unparsed}/{total} unparsed — if this has dropped sharply, the plan template "
        f"changed and the UNREADABLE row's justification should be re-measured"
    )


@pytest.mark.parametrize(
    "command",
    [
        "ROOT=$PWD; cd $ROOT/.. && ls",
        "ROOT=$PWD; diff -r $ROOT/../live scripts",
        "ROOT=$PWD; git -C $ROOT/.. status",
        "ROOT=$PWD; rm -rf $ROOT/..",
    ],
)
def test_one_hop_through_a_name_does_not_launder_a_parent_reference(tmp_path, command):
    """The `$PWD` defect, re-opened one name away.

    `locally_assigned` suppressed the unknown-variable refusal but nothing substituted the value,
    so `$ROOT` survived into `Path.resolve()` as a literal directory component and absorbed exactly
    one level — while every DIRECT spelling of the same intent was refused. An unresolved expansion
    is now not a path component at all. Measured: 0 of 223 corpus checks change verdict.
    """
    assert pr.escape_reason(command, make_repo(tmp_path)) is not None, command


def test_the_gate_states_its_scope_rather_than_listing_closed_holes():
    """The bound itself, asserted so it cannot quietly become a progress report again.

    Four review rounds each closed holes in this gate; a docstring that recites them invites the
    reader to expect the next entry, which is the opposite of a declared limit.
    """
    doc = pr.escape_reason.__doc__
    assert "does not follow a value through an indirection" in doc, doc
    for accepted in ("timeout", "wrapper"):
        assert accepted in doc, (
            f"the accepted limit {accepted!r} must be named, not implied"
        )


@pytest.mark.parametrize(
    "command",
    [
        'echo "$(date)"',
        'echo "$(whoami)"',
        'touch "$(mktemp -u)"',
        'echo "$(rm -rf ..)"',
    ],
)
def test_a_quoted_substitution_with_no_slash_is_still_refused(tmp_path, command):
    """The case an inertness probe drawn from remembered examples keeps missing.

    A QUOTED substitution survives tokenization as one token; if it carries no `/` it is not
    path-like, so it never reaches the path scan and only the residual-`$` rule sees it. Probing
    this branch with slash-bearing substitutions alone made it read as redundant — the fourth time
    on this branch that a probe set drawn from the cases already in mind gave the wrong answer.
    """
    assert pr.escape_reason(command, make_repo(tmp_path)) is not None, command


def test_an_assigned_substitution_is_refused_even_when_never_used(tmp_path):
    # `X="$(cat f)"; echo done` never dereferences X, so nothing downstream looks at it. The
    # assignment itself is the only place the unreadable value appears.
    root = make_repo(tmp_path)
    reason = pr.escape_reason('X="$(cat f)"; echo done', root)
    assert reason is not None and reason.startswith("assigns "), reason


# --------------------------------------------------- the freeze gate's two BLOCKERs
#
# Both were introduced by the commit that folded the PREVIOUS review's fixes — the third time on
# this branch a repair created the next defect. Both are pinned here rather than trusted.


def test_an_unreadable_row_still_carries_its_expectation_mismatch(tmp_path):
    """The new state dropped a finding the state it replaced carried.

    `reason` holds the expectation mismatch, and the TAINTED branch appends it. The UNREADABLE
    branch did not — so on 50 of this repo's 223 checks a literal mismatch vanished from the row
    AND from `flagged=`, at precisely the re-run where UNREADABLE takes over from TAINTED and the
    operator is looking for a verdict.
    """
    root = make_repo(tmp_path, {"a.txt": "base\n"})
    (root / "edited.txt").write_text("x\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        ### Task 1: Something

        **Files:**
        (described in prose below)

        Run: `false`

        Expected: `0`.
    """),
        root,
        tmp_path,
    )
    assert "UNREADABLE" in proc.stdout
    assert "expects the literal '0' but exited 1" in proc.stdout, proc.stdout
    assert "flagged=1" in proc.stdout, proc.stdout


def test_a_state_explanation_alone_is_not_counted_as_a_finding(tmp_path):
    # The other direction: TAINTED and UNREADABLE always carry a reason describing the state
    # itself. Counting that would make `flagged` equal the row count and mean nothing.
    root = make_repo(tmp_path, {"a.txt": "base\n"})
    (root / "edited.txt").write_text("x\n", encoding="utf-8")
    proc = run_cli(
        plan("""
        ### Task 1: Something

        **Files:**
        (described in prose below)

        Run: `true`

        Expected: silence.
    """),
        root,
        tmp_path,
    )
    assert "UNREADABLE" in proc.stdout
    assert "flagged=0" in proc.stdout, proc.stdout


def test_the_clone_carries_a_deny_all_pre_push_hook(tmp_path):
    """The gate's scope note cites this as containment, so it must exist.

    An earlier draft cited *the repo's own push guard* instead — false for a clone, measured:
    `git clone` copies no hooks, and this repo's guards are PreToolUse hooks on the operator's
    shell that never see a child this tool spawns.
    """
    source = make_repo(tmp_path, {"a.txt": "x\n"})
    clone = tmp_path / "made"
    assert pr.create_clone(source, clone, "dev", "dev") is None
    hook = clone / ".git" / "hooks" / "pre-push"
    assert hook.is_file(), "the scope note claims this hook exists"
    assert hook.stat().st_mode & 0o111, "an unexecutable hook never runs"


def test_that_pre_push_hook_actually_blocks_a_push(tmp_path):
    """Watch it FIRE. A hook that is merely INSTALLED has never run.

    The 'remote' is a local bare repo — nothing leaves the machine. The CONTROL is what makes the
    block meaningful: the same push from a clone WITHOUT the hook must succeed, or a refusal would
    prove nothing about the hook.
    """
    source = make_repo(tmp_path, {"a.txt": "x\n"})
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)

    control = tmp_path / "plain"
    subprocess.run(["git", "clone", "-q", str(source), str(control)], check=True)
    ok = subprocess.run(
        ["git", "-C", str(control), "push", str(bare), "HEAD:refs/heads/probe"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok.returncode == 0, f"control push must succeed: {ok.stderr}"

    clone = tmp_path / "rehearsal"
    assert pr.create_clone(source, clone, "dev", "dev") is None
    blocked = subprocess.run(
        ["git", "-C", str(clone), "push", str(bare), "HEAD:refs/heads/nope"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked.returncode != 0, "the rehearsal clone must not be able to push"
    assert "rehearsal clone" in blocked.stderr, blocked.stderr
    refs = subprocess.run(
        ["git", "-C", str(bare), "for-each-ref", "--format=%(refname)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "refs/heads/probe" in refs, "the control's ref should be there"
    assert "refs/heads/nope" not in refs, "the blocked push must not have landed"


def test_a_failed_create_still_leaves_a_hooked_clone(tmp_path):
    """The failure path an operator is most likely to hit produced the LEAST protected tree.

    A mistyped `--base` left the clone directory behind without the hook; `--create` then refuses
    that path forever ("already exists"), and the documented re-run form rehearses inside it. The
    hook is now installed before any path that can return.
    """
    source = make_repo(tmp_path, {"a.txt": "x\n"})
    residue = tmp_path / "residue"
    err = pr.create_clone(source, residue, "dev", "no-such-base")
    assert err is not None, "a bad base must still be reported"
    assert residue.exists(), "the clone directory survives, which is why this matters"
    assert (residue / ".git" / "hooks" / "pre-push").is_file(), (
        "a clone that survives a failure must still carry the hook"
    )


def test_a_failed_create_leaves_no_remote_and_no_signing(tmp_path):
    """The hook was moved above the failure returns; its two neighbours were left behind.

    The scope note in both files says the clone "has no remote" — false for exactly the residual
    directory the re-run form rehearses inside, because `remote remove origin` sat BELOW the
    base-resolution return. A `--no-verify` push from there can write any ref into the operator's
    real repo, whose own `pre-push` is a send-side guard that never fires on a receive. Signing
    travels with it: a check that commits in an unconfigured residue prompts a hardware key and
    hangs the run.
    """
    source = make_repo(tmp_path, {"a.txt": "x\n"})
    residue = tmp_path / "residue"
    assert pr.create_clone(source, residue, "dev", "no-such-base") is not None
    remotes = subprocess.run(
        ["git", "-C", str(residue), "remote"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert remotes == [], f"a surviving clone must keep no remote, found {remotes}"
    for key in ("commit.gpgsign", "tag.gpgsign"):
        got = subprocess.run(
            ["git", "-C", str(residue), "config", "--get", key],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        assert got == "false", (
            f"{key} must be disabled in a surviving clone, got {got!r}"
        )


def test_the_scope_note_claims_no_sandbox(tmp_path):
    """Two drafts of this paragraph each named a backstop that did not exist.

    Both were written to justify accepting the wrapper limit, and both read as reassurance. The
    text must now say plainly that there is no sandbox, and must NOT claim the hook contains a
    check that is routing around it.
    """
    doc = pr.escape_reason.__doc__
    assert "NO SANDBOX" in doc, doc
    for flag in ("--no-verify", "core.hooksPath"):
        assert flag in doc, f"the bypass {flag!r} must be named, not implied"
    assert "read every check" in doc


def test_the_hook_is_accident_protection_not_containment(tmp_path):
    """Pins the measured fact behind the wording: the hook stops a plain push and nothing more.

    Local bare repo only — nothing leaves the machine. The plain push is the CONTROL: without it
    firing, a bypass landing a ref would prove nothing about the hook.
    """
    source = make_repo(tmp_path, {"a.txt": "x\n"})
    clone = tmp_path / "rehearsal"
    assert pr.create_clone(source, clone, "dev", "dev") is None
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    empty_hooks = tmp_path / "emptyhooks"
    empty_hooks.mkdir()

    plain = subprocess.run(
        ["git", "-C", str(clone), "push", str(bare), "HEAD:refs/heads/plain"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert plain.returncode != 0, "CONTROL: the hook must stop a plain push"
    assert "rehearsal clone" in plain.stderr

    bypass = subprocess.run(
        [
            "git",
            "-C",
            str(clone),
            "-c",
            f"core.hooksPath={empty_hooks}",
            "push",
            str(bare),
            "HEAD:refs/heads/bypass",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bypass.returncode == 0, (
        "this documents a LIMIT, not a wish: if this ever starts failing, the scope note saying "
        "the hook does not contain a routing-around check has become wrong and must be revisited"
    )
    refs = subprocess.run(
        ["git", "-C", str(bare), "for-each-ref", "--format=%(refname)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "refs/heads/plain" not in refs
    assert "refs/heads/bypass" in refs, "the bypass really does land a ref"
