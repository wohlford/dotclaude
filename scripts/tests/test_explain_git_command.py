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
    # A ParseAmbiguity-free ValueError out of `tokenize` carries no position, and the tool must say
    # so rather than crash or fabricate one. The input that used to produce it -- a trailing
    # backslash -- no longer does (since 2026-09-18 `fold_continuations` drops backslashes at end of
    # input, as bash 3.2 does), and 200,000 random inputs then produced no plain ValueError at
    # all. So the failure is forced: the tokenizer's `tokenize` is replaced by one that raises,
    # and the tool runs against that.
    driver = (
        "import runpy, sys\n"
        f"sys.path.insert(0, {str(TOOL.parent / 'lib')!r})\n"
        "import git_command\n"
        "def _raise(_c):\n"
        "    raise ValueError('No escaped character')\n"
        "git_command.tokenize = _raise\n"
        f"sys.argv = [{str(TOOL)!r}, 'echo foo']\n"
        f"runpy.run_path({str(TOOL)!r}, run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", driver], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "VERDICT:     AMBIGUOUS —" in result.stdout
    assert "WHERE:       no byte position available for this category" in result.stdout
    # No traceback reached stderr.
    assert "Traceback" not in result.stderr


def test_a_trailing_backslash_at_end_of_input_now_parses():
    # bash 3.2 drops an unescaped backslash at end of input and runs the command; 5.3 keeps it
    # literal. Either way nothing is ambiguous about where the command is.
    result = run("echo foo\\")
    assert result.returncode == 0


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


# ---------- reading provenance ----------
#
# The tokenizer walks BOTH readings of an ambiguous heredoc continuation and records their union,
# so a report can carry an invocation bash would not run. The redesign accepts that over-read on
# one condition: the operator can see WHY the command blocked. An unexplainable block is what gets
# "fixed" by narrowing a matcher — the repair this repo has twice measured as the fail-open it was
# trying to remove — so these rows are load-bearing, not cosmetic.

# A quoted heredoc body whose last line ends in an odd backslash run: DROP reads the argument as
# `dev`, JOIN glues the terminator on and reads `devEOF`. Both are recorded.
_AMBIGUOUS = "bash <<'EOF'\ngit status dev\\\nEOF"

# Fable's witness: the JOIN reading rebuilds `x=$` + `(x` into an unterminated substitution, so
# that reading cannot be walked at all and the walk emits its in-band marker instead.
_WITNESS = "bash <<'(x'\ngit status\nx=$\\\n(x"


def _line_after(stdout: str, needle: str, offset: int = 1) -> str:
    """The line `offset` below the one holding `needle` — the reading line belongs to the record
    directly above it, so this asserts the ASSOCIATION rather than mere co-occurrence."""
    lines = stdout.splitlines()
    idx = next(i for i, line in enumerate(lines) if needle in line)
    return lines[idx + offset]


def _reading_line(stdout: str, subcommand: str) -> str:
    """The `reading:` line of the record whose subcommand is `subcommand`. A record is four lines
    — subcommand, env/opts, args, reading — so the reading line is the third below the first."""
    line = _line_after(stdout, f"subcommand={subcommand!r}", offset=3)
    assert "reading:" in line, f"not a reading line: {line!r}"
    return line


def test_each_invocation_names_the_reading_that_produced_it():
    result = run(_AMBIGUOUS)
    assert result.returncode == 0
    # The record bash really runs came from the primary (all-drop) reading...
    assert "PRIMARY" in _line_after(result.stdout, "args=['dev']")
    # ...and the extra one is attributed to the reading that produced it, by NAME, not merely
    # flagged as "some variant": the operator has to know which heredoc and which way it was read.
    variant = _line_after(result.stdout, "args=['devEOF']")
    assert "VARIANT" in variant, variant
    assert "heredoc #0 of 1 read as JOIN" in variant, variant


def test_the_witness_names_the_reading_that_could_not_be_read():
    result = run(_WITNESS)
    assert result.returncode == 0
    # The reading bash takes is recorded, and named as the primary one...
    assert "PRIMARY" in _reading_line(result.stdout, "status")
    # ...and the marker standing in for the reading that could not be walked says so on its own
    # record, not in a footnote the reader has to associate with it themselves.
    assert "LOST" in _reading_line(result.stdout, "$<ambiguous-heredoc-reading>")
    # WHICH reading, and why it failed — a bare "something was lost" would leave the operator
    # exactly where an unexplainable block leaves them.
    assert (
        "unreadable: heredoc #0 of 1 read as JOIN → unterminated command substitution"
        in result.stdout
    )
    # And that this record is not a command they typed, so hunting for it in their own text or
    # trying to authorize it with an env prefix is wasted effort.
    assert "NOT a command you typed" in result.stdout


def test_a_truncated_enumeration_says_the_cap_stopped_it():
    # The other way a reading is lost: the walk-wide parse cap runs out before every assignment
    # has been tried. Forced by lowering the cap, because reaching it honestly needs an input
    # engineered for that alone -- but the RENDERING is what this row pins, and a reader meeting
    # the marker cannot tell a cap from an unreadable reading unless the report says so.
    driver = (
        "import runpy, sys\n"
        f"sys.path.insert(0, {str(TOOL.parent / 'lib')!r})\n"
        "import git_command\n"
        "git_command.MAX_TOTAL_PARSES = 2\n"
        f"sys.argv = [{str(TOOL)!r}, '-']\n"
        f"runpy.run_path({str(TOOL)!r}, run_name='__main__')\n"
    )
    command = (
        "bash <<'A'\ngit status origin dev\\\nA\nbash <<'B'\ngit log origin main\\\nB\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", driver],
        input=command,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "reading: LOST" in result.stdout
    # 1, not 2: two top-level ambiguous heredocs share ONE context, so k=2 gives 4 assignments,
    # and since capability preservation each context's PRIMARY reading is free of the budget (see
    # `MAX_TOTAL_PARSES`). A cap of 2 therefore buys primary + 2 paid = 3 of the 4, leaving
    # exactly one untried. Measured: cap=1 -> 2 untried, cap=2 -> 1, cap=3 -> no truncation.
    assert "the parse cap stopped the enumeration: 1 reading(s) never tried" in (
        result.stdout
    )


def test_an_ordinary_command_prints_no_reading_lines():
    """PRESERVE row, green before this change and after — deliberately, and what it pins is the
    gate rather than the feature: with no ambiguous heredoc there is only one reading, so every
    record would be labelled PRIMARY and the label that matters would be the one nobody reads.
    It also keeps the report for an ordinary command byte-identical to the shipped one."""
    result = run('git commit -m "$(echo hi)" && sudo git status')
    assert result.returncode == 0
    assert "reading:" not in result.stdout
    assert "ambiguous heredoc continuation" not in result.stdout
    # A heredoc that is not AMBIGUOUS is not a reading choice either.
    plain = run("bash <<'EOF'\ngit status\nEOF")
    assert plain.returncode == 0
    assert "reading:" not in plain.stdout


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
