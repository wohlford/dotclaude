"""Tests for scripts/explain-git-command.py — the stand-alone WHICH-BYTE diagnostic for the shared
tokenizer at scripts/lib/git_command.py.

Every row invokes the tool as a subprocess, the way a caller actually would (positional argv, or
`-` for stdin) — never by importing it, so what is tested is exactly what a blocked caller runs.

The safety-property row (`test_does_not_execute_the_command_it_diagnoses`) is the load-bearing one:
this tool exists to be handed a command that just blocked a guard, so it must be provably incapable
of running any part of it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "explain-git-command.py"


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        input=stdin,
        capture_output=True,
        text=True,
    )


# ---------- ambiguous input: verdict, position, caret ----------


def test_ambiguous_backtick_reports_verdict_position_and_caret():
    # The plan's own spike example: `echo "a ` stray"` — pos=8 (0-based), the backtick.
    result = run('echo "a ` stray"')
    assert result.returncode == 2
    assert (
        "VERDICT:     AMBIGUOUS — unterminated backtick substitution" in result.stdout
    )
    assert "WHERE:       context #0, line 1, col 9" in result.stdout
    lines = result.stdout.splitlines()
    excerpt_idx = next(
        i for i, line in enumerate(lines) if line.strip() == 'echo "a ` stray"'
    )
    excerpt_line = lines[excerpt_idx]
    caret_line = lines[excerpt_idx + 1]
    # The caret sits directly under the backtick — wherever the shared line-prefix happens to
    # place it, not at a hardcoded column, since both lines share that prefix.
    assert caret_line.count("^") == 1
    assert caret_line.index("^") == excerpt_line.index("`")


def test_ambiguous_unbalanced_quote_reports_position():
    result = run("git commit -m 'unterminated")
    assert result.returncode == 2
    assert "VERDICT:     AMBIGUOUS — unbalanced quote" in result.stdout
    assert "WHERE:       context #0, line 1, col 15" in result.stdout


def test_ambiguous_unterminated_command_substitution_reports_position():
    result = run('echo "$(git status')
    assert result.returncode == 2
    assert "VERDICT:     AMBIGUOUS — unterminated command substitution" in result.stdout
    assert "WHERE:       context #0, line 1, col 7" in result.stdout


# ---------- parses-fine input: contexts, invocations, has_git_word ----------


def test_parses_fine_reports_contexts_invocations_and_has_git_word():
    result = run('git commit -m "$(echo hi)" && sudo git status')
    assert result.returncode == 0
    assert result.stdout.startswith("VERDICT:     PARSED")
    assert "CONTEXTS:" in result.stdout
    assert "#0  depth 0, top level" in result.stdout
    assert "#1  depth 1, opened at line 1, col 16 in context #0" in result.stdout
    assert "INVOCATIONS:" in result.stdout
    assert "has_git_word: True" in result.stdout
    assert "subcommand='commit'" in result.stdout
    assert "subcommand='status'" in result.stdout


def test_parses_fine_balanced_backtick_registers_a_real_invocation():
    # The plan's second measured shape: balanced backticks around prose that NAMES a git command
    # parse fine and register a REAL invocation — bash really does run a backtick body regardless
    # of the surrounding quotes, so this is a POLICY block, not an ambiguity refusal.
    result = run('echo "please do not run `git status`"')
    assert result.returncode == 0
    assert result.stdout.startswith("VERDICT:     PARSED")
    assert "has_git_word: True" in result.stdout
    assert "subcommand='status'" in result.stdout


def test_parses_fine_git_word_in_plain_quotes_finds_no_invocation():
    # The OTHER parses-fine shape: "git" appearing only as an ordinary word inside a quoted
    # argument (no backticks, no substitution) tokenizes as part of that single quoted argument,
    # never as its own `git` token — has_git_word is True (it ignores quoting), but the walk finds
    # no invocation at all. This is what INVOCATIONS exists to explain: a `has_git_word` pre-gate
    # firing with nothing for the real walk to find.
    result = run('echo "please do not run git status"')
    assert result.returncode == 0
    assert result.stdout.startswith("VERDICT:     PARSED")
    assert "has_git_word: True" in result.stdout
    assert "(none found)" in result.stdout


def test_local_placeholder_index_is_not_mischaracterised_as_global():
    # Regression for a real bug found while building this tool: an invocation whose args carry
    # TWO nested-context placeholders, where the first sibling has its OWN descendant. Local
    # placeholder numbering (0, 1) then does NOT line up with this tool's global context
    # numbering (#1 and #3) — asserted here by construction, from the tool's OWN CONTEXTS output.
    result = run("git log -- $(echo $(echo inner)) $(echo b)")
    assert result.returncode == 0
    assert "#1  depth 1, opened at line 1, col 12 in context #0" in result.stdout
    assert "#2  depth 2, opened at line 1, col 6 in context #1" in result.stdout
    assert "#3  depth 1, opened at line 1, col 36 in context #0" in result.stdout
    # The raw, un-rewritten local placeholders — NOT a wrongly-resolved "<context #1>" for the
    # second argument, which is actually global context #3.
    assert (
        "args=['--', '__GIT_COMMAND_SUBST_0__', '__GIT_COMMAND_SUBST_1__']"
        in result.stdout
    )
    assert "<context #" not in result.stdout
    assert "LOCAL to whichever context housed this invocation" in result.stdout


# ---------- stdin mode ----------


def test_stdin_mode_matches_positional_argument_mode():
    positional = run("git status")
    piped = run("-", stdin="git status")
    assert positional.returncode == piped.returncode == 0
    assert positional.stdout == piped.stdout


def test_stdin_mode_strips_exactly_one_trailing_newline():
    # A single trailing newline (as `printf '%s\n' "$cmd" | tool -` would produce) must not itself
    # read as part of the command; more than one is left alone rather than guessed at.
    piped = run("-", stdin="git status\n")
    assert piped.returncode == 0
    assert "subcommand='status'" in piped.stdout


# ---------- exit codes ----------


def test_exit_code_0_on_parse_2_on_ambiguity():
    assert run("git status").returncode == 0
    assert run('echo "a ` stray"').returncode == 2


def test_usage_error_exits_nonzero_without_crashing():
    result = run()
    assert result.returncode == 2
    assert "usage:" in result.stderr


# ---------- no-position degrade (task verification item 5) ----------


def test_shlex_origin_failure_degrades_to_category_without_crashing():
    # A trailing backslash reaches shlex directly (`tokenize`), past `split_command_contexts` —
    # this ParseAmbiguity-free ValueError carries no position, and the tool must say so rather
    # than crash or fabricate one.
    result = run("echo foo\\")
    assert result.returncode == 2
    assert "VERDICT:     AMBIGUOUS —" in result.stdout
    assert "WHERE:       no byte position available for this category" in result.stdout
    # No traceback reached stderr.
    assert "Traceback" not in result.stderr


# ---------- heredoc advisory ----------


def test_heredoc_advisory_names_unquoted_delimiter():
    result = run("bash <<EOF\ngit status\nEOF")
    assert result.returncode == 0
    assert "delimiter 'EOF', NOT quoted" in result.stdout
    assert "Quoting the delimiter" in result.stdout


def test_heredoc_advisory_recognises_quoted_delimiter():
    result = run("bash <<'EOF'\ngit status\nEOF")
    assert result.returncode == 0
    assert "delimiter 'EOF', quoted" in result.stdout
    assert "already literal to bash" in result.stdout


# ---------- the safety property ----------


def test_does_not_execute_the_command_it_diagnoses(tmp_path: Path):
    sentinel = tmp_path / "sentinel"
    command = f"touch {sentinel} && git status"
    result = run(command)
    assert not sentinel.exists(), (
        "explain-git-command.py executed part of the command it was asked to diagnose"
    )
    # It still produced the normal parsed-input report — the safety property is not a refusal.
    assert result.returncode == 0
    assert result.stdout.startswith("VERDICT:     PARSED")
    assert "subcommand='status'" in result.stdout


def test_does_not_execute_even_the_ambiguous_half(tmp_path: Path):
    sentinel = tmp_path / "sentinel2"
    # Unterminated backtick — the walk gives up, but a `touch` before it must still never run.
    command = f"touch {sentinel} && echo `unterminated"
    result = run(command)
    assert not sentinel.exists()
    assert result.returncode == 2
