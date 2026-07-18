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
