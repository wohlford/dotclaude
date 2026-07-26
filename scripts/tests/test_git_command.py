"""Unit tests for scripts/lib/git_command.py — the shared shell-command tokenizer and the
git-invocation walk (iter_git_invocations) extracted from recast-commit-gate.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import git_command  # noqa: E402, I001


# ---------- tokenize / is_op / is_redirect / strip_redirects / is_git / starts_command ----------


def test_tokenize_splits_fused_operators():
    assert git_command.tokenize("git add -A&&git commit") == [
        "git",
        "add",
        "-A",
        "&&",
        "git",
        "commit",
    ]


def test_tokenize_raises_on_unbalanced_quotes():
    with pytest.raises(ValueError):
        git_command.tokenize("git commit -m 'unterminated")


def test_is_op_true_for_control_operators():
    for tok in ("&&", "||", ";", "|", "&", "(", ")"):
        assert git_command.is_op(tok), tok


def test_is_op_false_for_redirects_and_words():
    assert not git_command.is_op(">")
    assert not git_command.is_op("git")
    assert not git_command.is_op("")


def test_is_redirect_true_for_redirect_tokens():
    for tok in (">", ">>", "<", ">&", "&>"):
        assert git_command.is_redirect(tok), tok


def test_is_redirect_false_for_control_operators():
    assert not git_command.is_redirect("&&")
    assert not git_command.is_redirect("git")


def test_strip_redirects_drops_operator_target_and_fd():
    seg = ["git", "commit", "-m", "x", "2", ">&", "1"]
    assert git_command.strip_redirects(seg) == ["git", "commit", "-m", "x"]


def test_strip_redirects_no_op_when_no_redirects():
    seg = ["git", "commit", "-m", "x"]
    assert git_command.strip_redirects(seg) == seg


def test_is_git_true_for_bare_and_path():
    assert git_command.is_git("git")
    assert git_command.is_git("/usr/bin/git")
    assert not git_command.is_git("gitk")
    assert not git_command.is_git("echo")


def test_starts_command_true_at_start_and_after_operator():
    tokens = ["git", "commit"]
    assert git_command.starts_command(tokens, 0)
    tokens = ["git", "add", "-A", "&&", "git", "commit"]
    assert git_command.starts_command(tokens, 4)


def test_starts_command_true_through_wrapper_and_env_assign():
    tokens = ["sudo", "git", "commit"]
    assert git_command.starts_command(tokens, 1)
    tokens = ["ALLOW_PUSH=1", "git", "push"]
    assert git_command.starts_command(tokens, 1)


def test_starts_command_false_after_unknown_word():
    tokens = ["echo", "git", "push"]
    assert not git_command.starts_command(tokens, 1)


# ---------- iter_git_invocations ----------


def test_simple_invocation():
    got = git_command.iter_git_invocations("git push origin main")
    assert got == [(None, "push", ["origin", "main"])]


def test_compound_invocations_in_order():
    got = git_command.iter_git_invocations("git add -A && git commit -m msg")
    assert got == [
        (None, "add", ["-A"]),
        (None, "commit", ["-m", "msg"]),
    ]


def test_wrapper_sudo():
    got = git_command.iter_git_invocations("sudo git push origin main")
    assert got == [(None, "push", ["origin", "main"])]


def test_wrapper_env_with_assignment():
    got = git_command.iter_git_invocations("env FOO=1 git commit")
    assert got == [(None, "commit", [])]


def test_wrapper_time():
    got = git_command.iter_git_invocations("time git push")
    assert got == [(None, "push", [])]


def test_dash_c_dir_separate_token():
    got = git_command.iter_git_invocations("git -C /some/path push origin dev")
    assert got == [("/some/path", "push", ["origin", "dev"])]


def test_dash_c_dir_attached():
    got = git_command.iter_git_invocations("git -C/some/path push origin dev")
    assert got == [("/some/path", "push", ["origin", "dev"])]


def test_env_assignment_prefix():
    got = git_command.iter_git_invocations("ALLOW_PUSH=1 git push")
    assert got == [(None, "push", [])]


def test_non_git_command_yields_nothing():
    got = git_command.iter_git_invocations("echo git push")
    assert got == []


def test_missing_subcommand_yields_nothing():
    got = git_command.iter_git_invocations("git -C /some/path")
    assert got == []


def test_global_value_opt_consumes_next_token():
    got = git_command.iter_git_invocations("git -c user.name=x commit -m msg")
    assert got == [(None, "commit", ["-m", "msg"])]


def test_unbalanced_quotes_yield_empty_list():
    got = git_command.iter_git_invocations("git commit -m 'unterminated")
    assert got == []


# ---------- line continuations (regression: a backslash-newline hid the subcommand) ----------
# `\` + newline is how any long git command is written. Newlines were rewritten to ` ; ` BEFORE
# shlex saw the backslash, so the injected space got escaped and became the subcommand token:
# `git \<nl>  push origin dev` resolved to subcommand " ", and both push gates allowed it.


def test_continuation_before_subcommand_is_folded():
    got = git_command.iter_git_invocations("git \\\n  push origin dev")
    assert got == [(None, "push", ["origin", "dev"])]


def test_continuation_mid_arguments_is_folded():
    got = git_command.iter_git_invocations("git push \\\n  origin dev")
    assert got == [(None, "push", ["origin", "dev"])]


def test_continuation_with_crlf_is_folded():
    got = git_command.iter_git_invocations("git \\\r\n  push origin dev")
    assert got == [(None, "push", ["origin", "dev"])]


def test_real_newline_still_separates_commands():
    """Folding continuations must not swallow ordinary newline-joined commands."""
    got = git_command.iter_git_invocations("git status\ngit push origin dev")
    assert [sub for _c, sub, _s in got] == ["status", "push"]


# ---------- command-context scanner (syntactic) ----------


def test_split_extracts_quoted_command_substitution():
    outer, ctxs = git_command.split_command_contexts('x="$(git push origin dev)"')
    assert [c.text for c in ctxs] == ["git push origin dev"]
    assert outer == 'x="__GIT_COMMAND_SUBST_0__"'


def test_split_outer_is_tokenizable_after_substitution():
    """The Defect A fix: the outer text's quotes re-balance once bodies are removed."""
    cmd = r"""x="$(sed -nE 's/a"b"c"d/\1/p' /dev/null)" && git rev-parse --show-toplevel"""
    outer, ctxs = git_command.split_command_contexts(cmd)
    assert git_command.tokenize(outer)  # must NOT raise
    assert ctxs[0].text == r"""sed -nE 's/a"b"c"d/\1/p' /dev/null"""


def test_split_extracts_backticks():
    _outer, ctxs = git_command.split_command_contexts("x=`git push origin dev`")
    assert [c.text for c in ctxs] == ["git push origin dev"]


def test_split_extracts_process_substitution():
    _outer, ctxs = git_command.split_command_contexts("cat <(git push origin dev)")
    assert [c.text for c in ctxs] == ["git push origin dev"]


def test_split_ignores_contexts_inside_single_quotes():
    """Protected baseline: a single-quoted literal is inert to the shell and must stay inert."""
    outer, ctxs = git_command.split_command_contexts("echo 'git push origin dev'")
    assert ctxs == []
    assert outer == "echo 'git push origin dev'"


def test_split_process_substitution_inert_inside_double_quotes():
    _outer, ctxs = git_command.split_command_contexts('echo "<(git push)"')
    assert ctxs == []


def test_split_arithmetic_body_contains_no_command():
    """Protected baseline: $(( )) needs no special case — its body has nothing in command position."""
    _outer, ctxs = git_command.split_command_contexts('x="$(( 1 + 2 ))" && git status')
    assert all("push" not in c.text for c in ctxs)
    assert all(git_command.iter_git_invocations(c.text) == [] for c in ctxs)


def test_split_honors_backslash_escaped_backtick():
    r"""Evidence row 15, and it takes TWO fixes that fail independently.

    Measured fail-open on both gates 2026-07-25. Depth-2 backtick nesting REQUIRES backslashes in
    bash, so this is the only form nested backticks take.

    1. `\`` must not CLOSE the context early, or the body leaks into the outer string and glues
       onto the placeholder.
    2. The extracted body must then be UNESCAPED, or the recursion never opens the nested context
       and the tokenizer produces `` `git `` — which `is_git` does not match.

    Fix 1 alone gives a correct context boundary around inert contents, which passes every
    structural assertion while the bypass stays open. This test asserts the INNER push is
    reachable, not merely that the outer body was captured.
    """
    outer, ctxs = git_command.split_command_contexts(
        r"x=`echo \`git push origin dev\``"
    )
    assert outer == "x=__GIT_COMMAND_SUBST_0__"
    _inner_outer, inner = git_command.split_command_contexts(
        ctxs[0].text, ctxs[0].depth
    )
    assert [c.text for c in inner] == ["git push origin dev"]


def test_split_honors_backslash_escaped_paren():
    r"""A `\)` must not close a `$( )` context early — same mechanism as the backtick case."""
    _outer, ctxs = git_command.split_command_contexts(
        r'x="$(echo \) ; git push origin dev)"'
    )
    assert ctxs[0].text.endswith("git push origin dev")


def test_split_raises_on_unterminated_substitution():
    with pytest.raises(ValueError):
        git_command.split_command_contexts('x="$(git push origin dev')


def test_split_raises_on_unbalanced_quote():
    with pytest.raises(ValueError):
        git_command.split_command_contexts("echo 'unterminated")


def test_split_refuses_input_containing_the_reserved_marker():
    """Reachable by ordinary work — `git grep __GIT_COMMAND_SUBST_0__` finds this very file.

    Must be ValueError, never IndexError: consumers swallow only ValueError, so anything else
    escapes into a third gate as an uncaught traceback.
    """
    with pytest.raises(ValueError):
        git_command.split_command_contexts(
            f"git grep {git_command.PLACEHOLDER_PREFIX}0__ scripts/"
        )


# ---------- comment stripping (evidence row 13) ----------


def test_strip_comments_removes_a_trailing_comment():
    assert git_command.strip_comments("git status  # note") == "git status  "


def test_strip_comments_keeps_the_newline_that_ends_a_comment():
    """The terminating newline is load-bearing downstream — it separates the next command."""
    assert (
        git_command.strip_comments("git status  # note\ngit push origin dev")
        == "git status  \ngit push origin dev"
    )


@pytest.mark.parametrize(
    "command",
    [
        "git log --pretty=%h#%d",
        "git checkout feature#123",
        'git commit -m "#42 fix"',
        "echo '#!/bin/bash'",
        "git push origin dev#notacomment",
        "curl https://example.test/x#frag",
    ],
)
def test_strip_comments_never_removes_real_command_text(command):
    """A false positive here is a BYPASS, not a false block — it deletes text before anything sees it.

    The `#`-must-be-at-word-start rule is deliberately a SUBSET of bash's real comment boundaries:
    it may over-keep (costing at most a loud false block) but must never over-remove.
    """
    assert git_command.strip_comments(command) == command


# ---------- unified walk: every bypass must become visible ----------


def _subs(command):
    """Every git subcommand the walk finds, across all contexts."""
    return [
        sub
        for _dir, _cdir, sub, _seg in git_command.iter_git_invocations_with_cwd(
            command, "/repo"
        )
    ]


BYPASSES = [
    ('x="$(git push origin dev)"', "quoted $( )"),
    ("x=`git push origin dev`", "backticks"),
    ('x="$(git push origin dev)" && git status', "quoted + trailing git"),
    ('x="$(echo `git push origin dev`)"', "backtick inside $( )"),
    ("cat <(git push origin dev)", "process substitution"),
    # THE SPAN RULE (spec 4.6, evidence rows 10-12). A substitution inside a git command's OWN
    # token span. Bash evaluates it FIRST, then runs the benign-looking outer command; the guard
    # sees only commit/tag/status, all in KNOWN_SAFE_SUBCOMMANDS. Row 10 is named in success
    # criterion 1. A walk that checks placeholders only at command-position tokens passes every
    # other case here and still fails these three.
    ('git commit -m "$(git push origin dev)"', "subst in commit's arg span"),
    ('git tag -a v1 -m "$(git push origin dev)"', "subst in tag's arg span"),
    ('git -c x="$(git push origin dev)" status', "subst in the global-option run"),
    # Evidence row 15: the escape must be honored or the context ends early (see Task 1).
    (r"x=`echo \`git push origin dev\``", "escaped backticks, depth 2"),
    # Evidence row 13: shlex's default commenters="#" eats every line after a trailing comment,
    # because newlines are normalized to `;` BEFORE tokenizing.
    ("git status  # note\ngit push origin dev", "# comment truncation"),
    # Regression pin for the SHIPPED v0.49.7 continuation fold. Green today; it goes red the moment
    # the walk re-derives normalization instead of calling normalize_command.
    ("git \\\n push origin dev", "backslash-newline continuation"),
    # The dropped-context backstop: strip_redirects deletes a redirect operator AND its target, so
    # this context never reaches the token loop -- but bash still executes it.
    ('git status > "$(git push origin dev)"', "context as a redirect target"),
]

# CONCEDED RESIDUALS (spec 7b, operator-approved 2026-07-25). These are live fail-opens and
# STAY that way: the exec-wrapper class is out of scope. Asserted here so current behavior is
# pinned -- a future change that closes one shows up as a deliberate improvement, and the
# concession can never be mistaken for an oversight. DO NOT move these into BYPASSES.
CONCEDED_RESIDUALS = [
    ('sh -c "git push origin dev"', "sh -c"),
    ("bash -c 'git push origin dev'", "bash -c"),
    ('eval "git push origin dev"', "eval"),
    ("bash -lc 'git push origin dev'", "bash -lc bundled"),
    ("/bin/sh -c 'git push origin dev'", "path-qualified shell"),
    ("echo 'git push origin dev' | sh", "pipe-into-shell"),
    ('sh <<< "git push origin dev"', "herestring"),
]


@pytest.mark.parametrize(("command", "label"), BYPASSES)
def test_every_known_bypass_exposes_the_push(command, label):
    assert "push" in _subs(command), label


@pytest.mark.parametrize(("command", "label"), CONCEDED_RESIDUALS)
def test_conceded_residuals_stay_invisible(command, label):
    """Pin the concession (spec 7b). These are GREEN from the start — a change-detector, not a proof.

    If one goes red, something CLOSED it: that is an improvement to document and move out of this
    list, never an assertion to invert. The list must not be left defined-but-unused, which is how
    it survived the 2026-07-25 fold as dead code while the bash suites asserted the opposite.
    """
    assert "push" not in _subs(command), label


def test_already_detected_shapes_still_detected():
    for command in (
        "git push origin dev",
        "x=$(git push origin dev)",
        'x="$(echo "$(git push origin dev)")"',
    ):
        assert "push" in _subs(command), command


def test_protected_baselines_expose_no_push():
    for command in (
        "git status -s",
        'x="$(( 1 + 2 ))" && git status',
        "echo 'git push origin dev'",
    ):
        assert "push" not in _subs(command), command


def test_defect_a_repro_parses_and_finds_no_push():
    cmd = r"""x="$(sed -nE 's/a"b"c"d/\1/p' /dev/null)" && git rev-parse --show-toplevel"""
    assert _subs(cmd) == ["rev-parse"]


def test_propagate_step5_snippet_old_quoted_form_parses():
    """The OLD (quoted) /propagate step-5 marker parse, verbatim — the shape shlex mis-parsed.

    `58caf17` fixed the CALL SITE by dropping the outer quotes, not the parser. Keeping this form
    is what stops the workaround from masking the tokenizer defect it worked around: if only the
    shipped form were tested, the underlying bug could silently return.
    """
    cmd = (
        "want=\"$(sed -nE 's/^[[:space:]]*production[[:space:]]*="
        '[[:space:]]*"([^"]*)".*/\\1/p\' "$marker")"\n'
        'got="$(git -C "$live" rev-parse --abbrev-ref HEAD)"'
    )
    assert _subs(cmd) == ["rev-parse"]


def test_propagate_step5_snippet_new_unquoted_form_parses():
    """The SHIPPED (unquoted) form, as it exists in skills/propagate/SKILL.md today.

    Spec criterion 2 names BOTH forms; testing only the old one would leave the form actually in
    production unverified.
    """
    cmd = (
        "want=$(sed -nE 's/^[[:space:]]*production[[:space:]]*="
        '[[:space:]]*"([^"]*)".*/\\1/p\' "$marker")\n'
        'got="$(git -C "$live" rev-parse --abbrev-ref HEAD)"'
    )
    assert _subs(cmd) == ["rev-parse"]


def test_output_process_substitution_is_a_context():
    """`>( … )` is one of the four covered constructs and had no test until 2026-07-25."""
    assert "push" in _subs("tee >(git push origin dev) < /dev/null")


def test_depth_limit_raises():
    deep = "git status"
    for _ in range(git_command.MAX_CONTEXT_DEPTH + 2):
        deep = f'x="$({deep})"'
    with pytest.raises(ValueError):
        git_command.iter_git_invocations_with_cwd(deep, "/repo")


def test_cd_inside_a_subshell_does_not_leak():
    """A cd in a subshell must NOT change the dir attributed to a later OUTER invocation."""
    result = git_command.iter_git_invocations_with_cwd(
        'x="$(cd /elsewhere && true)" && git push origin dev', "/repo"
    )
    push = next(r for r in result if r[2] == "push")
    assert push[0] == "/repo"


def test_cd_in_the_outer_context_still_applies():
    result = git_command.iter_git_invocations_with_cwd(
        "cd /elsewhere && git push origin dev", "/repo"
    )
    push = next(r for r in result if r[2] == "push")
    assert push[0] == "/elsewhere"


def test_popd_makes_the_cwd_unresolvable():
    """Ported from publication-push-guard, whose own cwd walk Task 4 deletes.

    No stack is tracked, so any popd forfeits cwd knowledge. Dropping this rule during the move
    into the library would be a NEW fail-open: an unresolvable cwd blocks, a wrongly-resolved one
    can allow. Measured: `cd /tmp && popd && git push origin dev` blocks today.
    """
    result = git_command.iter_git_invocations_with_cwd(
        "cd /tmp && popd && git push origin dev", "/repo"
    )
    push = next(r for r in result if r[2] == "push")
    assert push[0] is None


def test_cd_to_a_substitution_target_is_unresolvable():
    """`cd "$(…)"` is no more statically resolvable than `cd "$VAR"`.

    Joining the placeholder as a path segment would invent a directory that is not the adopted
    repo, turning a push that must block into one that is allowed.
    """
    result = git_command.iter_git_invocations_with_cwd(
        'cd "$(echo /repo)" && git push origin dev', "/repo"
    )
    push = next(r for r in result if r[2] == "push")
    assert push[0] is None


def test_nested_invocations_are_reported_before_their_host_command():
    """Bash evaluates a substitution BEFORE running the command whose arguments carry it."""
    subs = _subs('git commit -m "$(git push origin dev)"')
    assert subs == ["push", "commit"]


# ---------- regression pins for fixes that would otherwise ship untested ----------
# Each of these closes a MEASURED defect. Without a committed test a later refactor regresses them
# with every suite green -- and three of the five were themselves introduced by a previous fix.


@pytest.mark.parametrize(
    "command",
    [
        "(cd /elsewhere && ls) && git push origin dev",  # spaced
        "(cd /elsewhere && ls)&&git push origin dev",  # `)&&` fused into one token
        "((cd /elsewhere && ls)) && git push origin dev",  # `((` / `))` grouped
        "(cd /elsewhere && ls);git push origin dev",  # `);` fused
    ],
)
def test_plain_subshell_cd_does_not_leak(command):
    """A `( … )` subshell isolates cwd. Each spelling tokenizes differently; all must isolate.

    The grouped and operator-fused forms are the trap: `punctuation_chars` fuses `)` to whatever
    follows, so a check written against the spaced form passes its own test and leaks everywhere
    else. A wrongly-RESOLVED directory is the direction that ALLOWS.
    """
    push = next(
        r
        for r in git_command.iter_git_invocations_with_cwd(command, "/repo")
        if r[2] == "push"
    )
    assert push[0] == "/repo"


def test_backstop_walks_dropped_contexts_as_UNRESOLVABLE():
    """Discriminating shape: the cwd must be None, not the entry cwd.

    `strip_redirects` deletes a redirect's target, so this context never reaches the token loop.
    Walking it at `base_cwd` still finds the push -- so a test run from the adopted repo passes
    under BOTH the correct and the buggy version. Only a `cd` to a different directory first
    distinguishes them, which is why this case names one.
    """
    push = next(
        r
        for r in git_command.iter_git_invocations_with_cwd(
            'cd /adopted && git status > "$(git push origin dev)"', "/repo"
        )
        if r[2] == "push"
    )
    assert push[0] is None


def test_reserved_marker_in_the_input_is_refused():
    """Reachable by ordinary work — `git grep __GIT_COMMAND_SUBST_0__` once this file contains it.

    Must be ValueError, never IndexError: consumers swallow only ValueError, so anything else
    escapes into a third gate as an uncaught traceback.
    """
    with pytest.raises(ValueError):
        git_command.iter_git_invocations_with_cwd(
            f"git grep {git_command.PLACEHOLDER_PREFIX}0__ scripts/", "/repo"
        )


def test_oversized_input_is_refused_rather_than_ground_through():
    """Past the gate's hook timeout the hook is KILLED before its own fail-closed handler runs."""
    with pytest.raises(ValueError):
        git_command.iter_git_invocations_with_cwd("echo " + "$(x)" * 40_000, "/repo")


def test_comment_inside_a_substitution_body_does_not_truncate_it():
    """A `)` inside a comment must not close the context — bash agrees it does not."""
    assert "push" in _subs('x="$(echo hi  # )\ngit push origin dev)"')


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Closes only when `tokenize` sets commenters='', which lands with the change that routes "
        "every consumer through strip_comments — not before. STRICT on purpose: the moment that "
        "lands, this XPASSes and strict turns that into a failure, forcing the marker off."
    ),
)
def test_comment_stripping_that_drifts_still_exposes_the_push():
    """`strip_comments` tracks quotes linearly over pre-split text — the class Defect A says drifts.

    On an odd-inner-quote body it fails to strip, and the surviving `#` must then be INERT rather
    than a comment, or shlex (newlines already `;`) eats the rest of the command. Measured: the
    push vanished entirely — a live bypass, currently OPEN and pinned here.

    The fix is `commenters = ""` in `tokenize`, paired with `strip_comments`; the two are
    complementary and neither is safe alone. Setting it before every consumer routes through
    `strip_comments` was measured to regress 3 of 6 ordinary commands, so it is deliberately
    deferred. This test is the tripwire that the deferral was honored and then discharged.
    """
    cmd = 'x="$(sed -nE \'s/a"b"c"d/\\1/p\' /dev/null)"  # note\ngit push origin dev'
    assert "push" in _subs(cmd)


def test_backslash_terminated_comment_does_not_eat_the_next_line():
    """A backslash does NOT continue a comment; bash ends it at the physical newline."""
    assert "push" in _subs("# push to origin \\\ngit push origin dev")


def test_both_primitives_agree_on_the_same_string():
    """The two primitives must never disagree — a drifted build made them return opposite verdicts.

    No other test drives `iter_context_token_streams`, which is exactly how that drift survived.
    """
    for command in (
        'x="$(echo hi  # note\ngit status)"',
        'x="$(echo hi  # )\ngit push origin dev)"',
        "git status  # note\ngit push origin dev",
    ):
        walk_ok = streams_ok = True
        try:
            git_command.iter_git_invocations_with_cwd(command, "/repo")
        except ValueError:
            walk_ok = False
        try:
            git_command.iter_context_token_streams(command)
        except ValueError:
            streams_ok = False
        assert walk_ok == streams_ok, command


# REMOVED with Task 2: there is no eval modelling, so there is no eval cwd behavior to assert.
# `eval "cd /elsewhere" && git push origin dev` is a conceded residual (spec 7b) -- the push
# inside it is not seen at all, which is the concession, not a bug to test around.


def test_iter_git_invocations_sees_substitution_nested_push():
    # "wrapper" here means the metadata-free convenience function, NOT shell-wrapper detection.
    subs = [sub for _c, sub, _s in git_command.iter_git_invocations('x="$(git push)"')]
    assert "push" in subs
