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
