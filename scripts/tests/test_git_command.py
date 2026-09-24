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


def test_starts_command_ignores_reserved_words_by_default():
    """A bare call (no `reserved_words`) is the pre-existing behavior byte-for-byte -- every
    `cd`/`pushd`/`popd` call site in the walk relies on this, so a reserved word must NOT be a
    boundary unless the caller opts in."""
    tokens = ["if", "git", "push"]
    assert not git_command.starts_command(tokens, 1)


def test_starts_command_true_after_reserved_word_when_opted_in():
    tokens = ["if", "git", "push"]
    assert git_command.starts_command(
        tokens, 1, reserved_words=git_command.RESERVED_WORDS
    )
    tokens = ["while", "cd", "/x"]
    assert git_command.starts_command(
        tokens, 1, reserved_words=git_command.RESERVED_WORDS
    )


def test_starts_command_ignores_extra_wrappers_by_default():
    tokens = ["exec", "git", "push"]
    assert not git_command.starts_command(tokens, 1)


def test_starts_command_true_after_extra_wrapper_when_opted_in():
    tokens = ["exec", "git", "push"]
    assert git_command.starts_command(
        tokens, 1, extra_wrappers=git_command.GIT_ONLY_WRAPPERS
    )


def test_git_starts_command_recognises_reserved_words_and_exec():
    for word in git_command.RESERVED_WORDS:
        tokens = [word, "git", "push"]
        assert git_command._git_starts_command(tokens, 1), word
    tokens = ["exec", "git", "push"]
    assert git_command._git_starts_command(tokens, 1)


def test_git_starts_command_still_false_after_unknown_word():
    tokens = ["echo", "git", "push"]
    assert not git_command._git_starts_command(tokens, 1)


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
    # The UNQUOTED eval family and every wrapper's `--` (fix/eval-wrapper-bypass). Each ran git
    # under a shim on bash 3.2.57 and 5.3.15 (`time --` under 5.3) and each yielded ZERO invocations
    # before the fix. The QUOTED eval forms stay in CONCEDED_RESIDUALS below.
    ("eval git push origin dev", "bare eval"),
    ("builtin eval git push origin dev", "builtin eval"),
    ("eval -- git push origin dev", "eval --"),
    ("builtin -- eval -- git push origin dev", "builtin -- eval --"),
    ("eval eval git push origin dev", "nested eval"),
    ("command eval git push origin dev", "command eval"),
    ("eval command git push origin dev", "eval command"),
    ("command -- git push origin dev", "command --"),
    ("exec -- git push origin dev", "exec --"),
    ("sudo -- git push origin dev", "sudo --"),
    ("env -- git push origin dev", "env --"),
    ("time -- git push origin dev", "time --"),
]

# CONCEDED RESIDUALS (spec 7b, operator-approved 2026-07-25). These are live fail-opens and
# STAY that way: the NESTED-COMMAND-STRING class is out of scope -- for `eval`, its RE-PARSE (a
# quoted word carrying whitespace or syntax, or an empty word); the bare `eval git …` form IS
# followed since fix/eval-wrapper-bypass. Asserted here so current behavior is pinned -- a future
# change that closes one shows up as a deliberate improvement, and the concession can never be
# mistaken for an oversight. DO NOT move these into BYPASSES.
CONCEDED_RESIDUALS = [
    ('sh -c "git push origin dev"', "sh -c"),
    ("bash -c 'git push origin dev'", "bash -c"),
    ('eval "git push origin dev"', "eval"),
    ("bash -lc 'git push origin dev'", "bash -lc bundled"),
    ("/bin/sh -c 'git push origin dev'", "path-qualified shell"),
    ("echo 'git push origin dev' | sh", "pipe-into-shell"),
    ('sh <<< "git push origin dev"', "herestring"),
    ("eval '' git push origin dev", "eval: an empty word, which eval's re-join drops"),
    ("eval ':;' git push origin dev", "eval: a quoted word carrying syntax"),
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


# ---------- F1: reserved words + `exec` are command boundaries for a `git` invocation ----------

RESERVED_WORD_AND_EXEC_GAINS = [
    ("! git push origin dev", "! bang"),
    ("{ git push origin dev; }", "{ brace group"),
    ("if true; then git push origin dev; fi", "if/then"),
    ("while :; do git push origin dev; done", "while/do"),
    ("until false; do git push origin dev; done", "until/do"),
    ("for i in 1; do git push origin dev; done", "for/do"),
    ("if false; then true; else git push origin dev; fi", "else"),
    ("if false; then true; elif true; then git push origin dev; fi", "elif/then"),
    ("f() { git push origin dev; }; f", "function body {"),
    ("exec git push origin dev", "exec wrapper"),
]


@pytest.mark.parametrize(("command", "label"), RESERVED_WORD_AND_EXEC_GAINS)
def test_reserved_word_and_exec_shapes_are_now_detected(command, label):
    """F1 + L2's gain set: nine reserved-word shapes plus `exec`, all previously invisible because
    `starts_command` only stepped back over `VAR=` assignments and `WRAPPERS`."""
    assert "push" in _subs(command), label


def test_for_in_git_does_not_manufacture_a_phantom_invocation():
    """`in` is deliberately excluded from RESERVED_WORDS (see its docstring): a for-loop's list
    item is not a command, so `git` right after `in` must stay invisible."""
    assert _subs("for f in git; do echo $f; done") == []


def test_exec_echo_git_is_still_a_phantom():
    """`exec` only counts as a boundary immediately before `git` itself -- `echo` in between still
    blocks recognition, exactly as an un-wrapped `echo git push` does."""
    assert _subs("exec echo git push origin dev") == []


# ---------- A cd right after a reserved word must be UNRESOLVABLE, not ignored or tracked --------

# One shape per RESERVED_WORDS member that places a `cd`/`pushd`/`popd` immediately after the
# reserved word -- DERIVED from the constant itself, not hand-listed, because hand-listing this
# exact matrix once covered only 3 of the 10 measured loss shapes (see git_command.RESERVED_WORDS's
# docstring). Each wraps `body` in the syntax that puts `body` directly after the named word.
_RESERVED_WORD_WRAP = {
    "{": lambda body: f"{{ {body}; }}",
    "!": lambda body: f"! {body}",
    "if": lambda body: f"if {body}; then :; fi",
    "then": lambda body: f"if true; then {body}; fi",
    "elif": lambda body: f"if false; then :; elif {body}; then :; fi",
    "else": lambda body: f"if false; then :; else {body}; fi",
    "while": lambda body: f"while {body}; do :; done",
    "until": lambda body: f"until {body}; do :; done",
    "do": lambda body: f"while :; do {body}; done",
    "coproc": lambda body: f"coproc {body}",
}


def test_reserved_word_wrap_shapes_cover_every_member():
    """FLOOR: if RESERVED_WORDS gains a member with no known wrap shape, this fails loudly instead
    of the derived matrix below silently under-covering it."""
    assert set(_RESERVED_WORD_WRAP) == git_command.RESERVED_WORDS


def _reserved_word_cd_family_cases():
    for word in sorted(git_command.RESERVED_WORDS):
        for cd_cmd in ("cd", "pushd", "popd"):
            body = "popd" if cd_cmd == "popd" else f"{cd_cmd} /other"
            command = f"{_RESERVED_WORD_WRAP[word](body)} ; git push origin dev"
            yield pytest.param(command, id=f"{word}-{cd_cmd}")


@pytest.mark.parametrize("command", list(_reserved_word_cd_family_cases()))
def test_a_cd_after_a_reserved_word_is_unresolvable(command):
    """A `cd`/`pushd`/`popd` immediately after a reserved word (`if`, `!`, `{`, ...) really runs --
    reaching it through a reserved word does not stop the shell moving -- so the tracked cwd must
    become UNRESOLVABLE (None). Of the three possible outcomes here, only None is safe:

    - unresolvable (None): the required outcome asserted below.
    - ignored (False): the old bug. The cd is read as a plain argument even though it really moves
      the shell, so the push that follows is judged against a cwd the shell already left.
    - tracked (the target directory): the three-blocks regression `RESERVED_WORDS`'s docstring
      documents -- `! cd OTHER ; <push>` and its `if`/`while` siblings would flip BLOCK to ALLOW.
    """
    invocations = git_command.iter_git_invocations_with_cwd(command, "/adopted")
    assert invocations, command
    assert invocations[-1][0] is None, (
        f"cd after a reserved word must be unresolvable, not ignored or tracked: {command}"
    )


def test_a_cd_after_a_non_boundary_word_is_untouched():
    """CONTROL: these nine words must leave `_cd_command_position` returning False, unchanged by
    the fix above -- proving the reserved-word fix does not widen past `RESERVED_WORDS` itself.
    `for`/`case`/`select`/`function` are included deliberately even though none of them is in
    `RESERVED_WORDS`: the failure this guards is a future branch consulting a SUPERSET
    (`RESERVED_WORDS | {"for"}`), which would make `for cd in a b` unresolvable. Neither
    `test_reserved_word_wrap_shapes_cover_every_member`'s equality assert nor the derived matrix
    above can see that regression; only a must-remain-False row like this one can."""
    for word in ("in", "}", "fi", "done", "esac", "for", "case", "select", "function"):
        tokens = git_command.tokenize(f"{word} cd /tmp")
        assert git_command._cd_command_position(tokens, 1) is False, word


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


@pytest.mark.parametrize(
    "prefix",
    [
        "eval cd",
        "builtin cd",
        "eval -- cd",
        "command -- cd",
        "command cd",
        "time cd",
        "time eval cd",
        "nohup cd",
        "env cd",
        "sudo cd",
        "env eval cd",
        "nice eval cd",
        "nohup builtin cd",
        "builtin time cd",
        "time time cd",
        "FOO=1 time cd",
        "env -- cd",
        "time -- cd",
        "eval pushd",
    ],
)
def test_a_cd_behind_any_wrapper_makes_the_cwd_unresolvable(prefix):
    """FAIL-CLOSED by design. Whether the shell moves depends on the whole chain -- `eval cd` moves
    it, `nohup cd` and `env eval cd` do not, `time cd` moves only where `time` is still the keyword
    (`builtin time cd` runs /usr/bin/time in a child). Two review rounds each found a fail-open in
    a set that tried to enumerate the movers; an unresolvable cwd blocks every guarded operation
    and no read, and 0 of 17,929 real cds went through a wrapper."""
    result = git_command.iter_git_invocations_with_cwd(
        f"{prefix} /elsewhere ; git push origin dev", "/repo"
    )
    push = next(r for r in result if r[2] == "push")
    assert push[0] is None, (prefix, push)


@pytest.mark.parametrize("prefix", ["cd", "FOO=1 cd", "A=1 B=2 cd", "FOO=1 pushd"])
def test_a_cd_reached_over_env_assignments_only_is_still_exact(prefix):
    """The exactness the fail-closed rule must NOT cost: a prefix assignment leaves the cd running
    in this shell (measured), so the target is tracked, not made unresolvable."""
    result = git_command.iter_git_invocations_with_cwd(
        f"{prefix} /elsewhere ; git push origin dev", "/repo"
    )
    push = next(r for r in result if r[2] == "push")
    assert push[0] == "/elsewhere", prefix


@pytest.mark.parametrize("prefix", ["exec cd", "exec -- cd", "echo cd", "echo eval cd"])
def test_a_cd_that_is_not_a_directory_change_leaves_the_cwd_alone(prefix):
    """`exec cd` never continues -- the non-interactive shell exits, so nothing after it runs
    (measured) -- and `echo cd` is an argument. Neither changes the cwd."""
    result = git_command.iter_git_invocations_with_cwd(
        f"{prefix} /elsewhere ; git push origin dev", "/repo"
    )
    push = next(r for r in result if r[2] == "push")
    assert push[0] == "/repo", prefix


@pytest.mark.parametrize("wrapper", ["builtin", "eval", "nohup", "command"])
def test_a_popd_behind_any_wrapper_makes_the_cwd_unresolvable(wrapper):
    """`builtin popd`/`eval popd` really pop (bash is back where it started) and were never SEEN
    before this change -- `pushd OTHER ; builtin popd ; <push dev>` was judged from OTHER, a
    fail-open. `nohup`/`command` were already seen; all four now agree."""
    result = git_command.iter_git_invocations_with_cwd(
        f"pushd /a ; {wrapper} popd ; git push origin dev", "/repo"
    )
    push = next(r for r in result if r[2] == "push")
    assert push[0] is None, wrapper


@pytest.mark.parametrize(
    ("command", "env"),
    [
        ("GIT_CONFIG_COUNT=1 eval -- git status", ["GIT_CONFIG_COUNT=1"]),
        ("eval GIT_CONFIG_COUNT=1 git status", ["GIT_CONFIG_COUNT=1"]),
        ("HOME=/x builtin eval git status", ["HOME=/x"]),
        ("GIT_CONFIG_COUNT=1 exec -- git status", ["GIT_CONFIG_COUNT=1"]),
    ],
)
def test_env_prefix_is_collected_across_eval_and_its_dashdash(command, env):
    """THE pin for `_env_prefix` using the shared predicate: give it its own spelling back and the
    `--` stops that walk before the assignment, so `tokens.env` goes empty while the invocation is
    still found. No guard-level row can pin this -- the export arm refuses a prefix in front of
    eval independently (measured)."""
    invs = git_command.iter_git_invocations_detailed(command, "/repo")
    assert len(invs) == 1, f"the walk must find exactly one invocation: {invs!r}"
    assert invs[0].subcommand == "status"
    assert invs[0].tokens.env == env


@pytest.mark.parametrize(
    "command",
    [
        "echo eval git push origin dev",
        "printf %s builtin eval git push origin dev",
        "git log -- eval -- git push origin dev",
        "eval -- -- git push origin dev",
        "for w in eval git push; do :; done",
        # Pins _steps_as_wrapper's `j > 0` guard on the `--`-owner lookup. Without it, a leading
        # `--` at index 0 (`tokens[j]` with `j == 0`) would fall through to `owner = tokens[j - 1]`
        # with `j - 1 == -1`; Python's negative indexing then makes the LAST token of the whole
        # command -- here the trailing `eval` after `;` -- the `--`'s owner, so `_steps_as_wrapper`
        # wrongly reports the leading `--` as wrapper-stepped and the walk manufactures a phantom
        # push out of `-- git push origin dev`. `j > 0` refuses the lookup when there is no real
        # preceding token, so this stays a bare `--` and `git` is its argument, not a command.
        "-- git push origin dev ; eval",
    ],
)
def test_eval_in_argument_position_manufactures_no_push(command):
    """The phantom-invocation guard. `eval -- -- git` runs a command named `--` (measured): one
    `--` per wrapper, so a `--` owned by another `--` is never stepped."""
    assert "push" not in _subs(command), command


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


def test_comment_stripping_that_drifts_still_exposes_the_push():
    """`strip_comments` tracks quotes linearly over pre-split text — the class Defect A says drifts.

    On an odd-inner-quote body it fails to strip, and the surviving `#` must then be INERT rather
    than a comment, or shlex (newlines already `;`) eats the rest of the command. Measured: the
    push vanished entirely.

    Closed by pairing `strip_comments` with `commenters = ""` in `tokenize`. The two are
    complementary and neither is safe alone — setting `commenters = ""` before every consumer
    routed through `strip_comments` was measured to regress 3 of 6 ordinary commands, so it was
    deliberately deferred to the change that completed the wiring.

    This case spent that interval as `xfail(strict=True)`, which is why the deferral could not be
    silently forgotten: the moment the pairing landed it XPASSed, and strict turned that into a
    failure demanding the marker be removed. Keep it as a plain assertion now.
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


# The QUOTED form, `eval "cd /elsewhere" && git push origin dev`, is a conceded residual (spec 7b):
# the cd inside the string is not seen at all. The BARE `eval cd /elsewhere` IS seen since
# fix/eval-wrapper-bypass, and makes the cwd unresolvable -- see
# test_a_cd_behind_any_wrapper_makes_the_cwd_unresolvable.


def test_iter_git_invocations_sees_substitution_nested_push():
    # "wrapper" here means the metadata-free convenience function, NOT shell-wrapper detection.
    subs = [sub for _c, sub, _s in git_command.iter_git_invocations('x="$(git push)"')]
    assert "push" in subs


# ---------- heredoc bodies: literal text, not a quoting context ----------
# In a real shell a heredoc body never undergoes quote removal -- `'` and `"` inside one are
# ordinary characters. Modelling them as quoting operators drifts the scanner's quote state, so a
# body containing an apostrophe made the enclosing `$( ... )` look unterminated and every consumer
# gate failed CLOSED on an innocent command. Measured 2026-07-28 against the heredoc commit form
# `/commit`'s own SKILL.md prescribes.
#
# The fix must not buy that back with a fail-open. A heredoc body is still ordinary command text to
# this walk -- `bash <<EOF ... EOF` really does execute what it carries -- so the body must stay
# VISIBLE. The PRESERVE assertions below are as load-bearing as the fix assertions above them, and
# each was measured to hold BEFORE the fix; if one goes red, the fix bought a bypass.

HEREDOC_APOS = "git commit -m \"$(cat <<'EOF'\nthe path's thing\nEOF\n)\""
HEREDOC_PLAIN = "git commit -m \"$(cat <<'EOF'\nthe path thing\nEOF\n)\""


def _subs(command):
    return [
        sub
        for _d, _c, sub, _s in git_command.iter_git_invocations_with_cwd(
            command, "/repo"
        )
    ]


def test_heredoc_four_way_only_the_combination_was_broken():
    """All four cells, not just the failing one -- otherwise the fix could pass by breaking the
    three forms that already worked."""
    cells = {
        "plain/noapos": 'git commit -m "the path thing"',
        "plain/apos": 'git commit -m "the path\'s thing"',
        "heredoc/noapos": HEREDOC_PLAIN,
        "heredoc/apos": HEREDOC_APOS,
    }
    for label, command in cells.items():
        assert "commit" in _subs(command), label


def test_heredoc_apostrophe_does_not_break_the_enclosing_substitution():
    assert _subs(HEREDOC_APOS).count("commit") == 1


def test_bare_heredoc_with_apostrophe_does_not_hide_a_later_invocation():
    command = "cat <<'EOF' > /tmp/x\nthe path's thing\nEOF\ngit fetch origin main"
    assert _subs(command) == ["fetch"]


def test_heredoc_dash_and_unquoted_delimiter_forms_also_parse():
    for command in (
        "cat <<-'EOF'\n\tthe path's thing\n\tEOF\ngit fetch origin main",
        "cat <<EOF\nthe path's thing\nEOF\ngit fetch origin main",
    ):
        assert _subs(command) == ["fetch"], command


def test_heredoc_body_is_still_scanned_for_real_invocations():
    """PRESERVE: a body fed to a shell really is executed. Caught today; a fix that makes heredoc
    bodies inert would turn this into a silent bypass."""
    for command in (
        "bash <<EOF\ngit push origin dev\nEOF",
        "bash <<'EOF'\ngit push origin dev\nEOF",
    ):
        assert "push" in _subs(command), command


def test_heredoc_body_quoted_hash_still_shields_a_following_invocation():
    """PRESERVE: neutralising body quotes by DELETING them lets `#` start a comment that swallows
    the rest of the line -- a fail-open. Caught today; must stay caught."""
    command = 'bash <<EOF\necho "#" ; git push origin dev\nEOF'
    assert "push" in _subs(command)


def test_genuinely_unbalanced_quote_outside_a_heredoc_still_raises():
    """PRESERVE: the fix must not become a blanket tolerance for unbalanced quotes."""
    with pytest.raises(ValueError):
        git_command.iter_git_invocations_with_cwd(
            "git fetch origin main 'oops", "/repo"
        )


def test_heredoc_body_with_a_quoted_git_token_is_still_seen():
    """Regression, found by probing rather than by the suite: the first version of the heredoc
    pass escaped EVERY body quote, so `"git"` tokenized as `"git"` and `is_git` stopped matching
    it -- a catch that worked before the pass existed. Only unmatched quotes may be touched."""
    command = 'bash <<EOF\n"git" push origin dev\nEOF'
    assert "push" in _subs(command)


def test_balanced_heredoc_body_is_left_byte_identical():
    """The blast-radius guarantee, asserted directly on the pass rather than inferred from the
    walk: a body whose quotes already balance must come out unchanged, so no command that parses
    today can begin parsing differently."""
    for command in (
        'bash <<EOF\necho "#" ; git push origin dev\nEOF',
        'bash <<EOF\n"git" push origin dev\nEOF',
        "cat <<'EOF'\nplain body\nEOF",
        "git status",
    ):
        assert git_command.mask_heredoc_quotes(command) == command, command


def test_mask_heredoc_quotes_only_ever_inserts_backslashes():
    """Text-preservation: the pass may add escapes and nothing else. Dropping a character here
    would silently remove a command from the walk."""
    for command in (
        "cat <<'EOF'\nit's\nEOF",
        "cat <<A <<B\na's\nA\nb's\nB",
        "cat <<'EOF'\nunterminated it's",
        'sh <<< "it\'s a herestring"',
    ):
        masked = git_command.mask_heredoc_quotes(command)
        assert masked.replace("\\", "") == command.replace("\\", ""), command


def test_mask_heredoc_quotes_is_idempotent():
    """`_prepare` re-runs the pass on every extracted context, so a heredoc inside `$( … )` is
    masked twice; growth across runs would corrupt the body."""
    for command in (
        "cat <<'EOF'\nit's\nEOF",
        "git commit -m \"$(cat <<'EOF'\nit's\nEOF\n)\"",
    ):
        once = git_command.mask_heredoc_quotes(command)
        assert git_command.mask_heredoc_quotes(once) == once, command


# ---------- an options-only invocation must not swallow the following operator ----------
# `git --version | head` carries no subcommand: the option walk runs off the end of the options and
# lands on the operator, which the walk then appended as the subcommand. That is not merely a
# nonsense verdict -- it STEALS the token from the paren branch above, so a `)` never pops
# `subshell_cwds`, the subshell's cd leaks, and the NEXT invocation is judged in the wrong
# directory. Measured against the shipped guard 2026-08-03:
#     (cd /elsewhere && git --version) && git push origin dev   -> ALLOWED (rc 0)
#     (cd /elsewhere && true)          && git push origin dev   -> blocked (rc 2)
# Bash runs that push in the OUTER, adopted repo; the guard judged it against a non-adopted one and
# went dormant. The leak is why these rows exist; the tidy verdict is a side effect.
#
# `test_plain_subshell_cd_does_not_leak` above pins this exact hazard class and did NOT catch it,
# because every one of its cases puts a non-git command (`ls`) inside the subshell. One token.


@pytest.mark.parametrize(
    "command",
    [
        "git --version | head",
        "git --version && echo ok",
        "git --version ; echo ok",
        "( git --version ) | head",
        "(git --version)&&echo ok",
        "git -c core.pager=cat --version | head",
    ],
)
def test_options_only_invocation_records_no_subcommand(command):
    """No subcommand exists, so no invocation may be recorded -- and never the operator."""
    assert _subs(command) == [], command


def test_options_only_invocation_does_not_leak_a_subshell_cwd():
    """THE SECURITY ROW. The `)` must reach the paren branch so the subshell's cd is popped."""
    found = git_command.iter_git_invocations_with_cwd(
        "(cd /elsewhere && git --version) && git push origin dev", "/repo"
    )
    push = next(r for r in found if r[2] == "push")
    assert push[0] == "/repo", f"cwd leaked out of the subshell: {found!r}"


@pytest.mark.parametrize(
    "command",
    [
        "git --version && git push origin dev",
        "git --version ; git push origin dev",
        "(git --version)&&git push origin dev",
        "git --version | head && git push origin dev",
    ],
)
def test_a_real_push_after_an_options_only_invocation_is_still_seen(command):
    """PRESERVE, and the direction that ALLOWS if the fix is written as `break`.

    Stopping the walk at the operator satisfies the rows above while silently dropping every
    invocation after it -- these commands are caught today and must stay caught.
    """
    assert "push" in _subs(command), command


def test_context_hidden_in_an_options_only_run_is_still_descended():
    """PRESERVE: the option run must still be descended -- a push can hide in `-c x=$( … )`.

    A fix that skips the option run to avoid the operator would lose this, which the SPAN RULE
    above exists to prevent.
    """
    assert "push" in _subs('git -c x="$(git push origin dev)" --version | head')


# ---------- an option's VALUE slot must not steal a control operator either ----------
# The same theft one slot to the LEFT, and a measured fail-open: `-c` consumed the next token
# unconditionally, so `git -c ; git push origin dev` ate the `;`, the walk resumed at `git`, and
# ONE invocation was recorded with subcommand "git" and the push buried in its argument segment.
# `git` is not `push`, is not allowlisted, and `alias.git` does not exist -- the guard ALLOWED it.
#
# The compound form is the regression marker: it BLOCKED before the options-only fix above (the
# bogus operator-as-subcommand invocation failed the guard's literal-subcommand rule, catching it
# by accident) and ALLOWED after, until the value-slot guard landed. An accidental catch is not
# coverage, and removing one is a regression even when the code that replaced it is better.


@pytest.mark.parametrize(
    "command",
    [
        "git -c ; git push origin dev",
        "git -C ; git push origin dev",
        "git --namespace ; git push origin dev",
        "git --version ; git -c ; git push origin dev",
        "git -c && git push origin dev",
        "git -c | git push origin dev",
    ],
)
def test_an_option_value_slot_does_not_swallow_an_operator(command):
    """The push must remain visible as its own invocation, not buried in an argument segment."""
    assert "push" in _subs(command), command


@pytest.mark.parametrize(
    ("command", "want"),
    [
        ("git -c user.name=x commit -m msg", ["commit"]),
        ("git -c user.name=x status", ["status"]),
        ("git -C /some/path push origin dev", ["push"]),
        ('git -c x="$(git push origin dev)" status', ["push", "status"]),
    ],
)
def test_an_option_that_really_takes_a_value_still_consumes_it(command, want):
    """PRESERVE: over-correcting here would mis-parse every ordinary `-c key=value` invocation."""
    assert _subs(command) == want, command


# ---------- F3: the value-slot boundary token must reach the paren branch ----------
# `value_slot_op`'s fallback (the branch just above, in `_walk_context`) used to record the
# ambiguous token as a bogus subcommand AND keep scanning forward for its argument segment,
# swallowing everything up to the next real operator or `git` -- including a `)` that was closing
# a real subshell. That stole the token from the paren-counting branch at the top of the walk, so
# the subshell's cd never popped. Measured: `(cd /elsewhere && git -c) ; git push origin dev`
# yielded TWO invocations, both with effective_dir "/elsewhere" -- the real push judged where the
# guard is dormant. The fix is two-part: hand the token back to the loop (`i = j`, not `i = k`) so
# the paren branch runs, AND still append an unjudgeable invocation for the truncated `git -c` so
# nothing disappears -- see git_command.py's `is_op(tokens[j])` / `value_slot_op` branch.


def test_value_slot_ambiguity_does_not_leak_a_subshell_cwd():
    """THE SECURITY ROW for F3, mirroring test_options_only_invocation_does_not_leak_a_subshell_cwd
    one slot to the left: the `)` must reach the paren branch even though it arrived by way of an
    option's ambiguous value slot, not a bare options-only run."""
    found = git_command.iter_git_invocations_with_cwd(
        "(cd /elsewhere && git -c) ; git push origin dev", "/repo"
    )
    push = next(r for r in found if r[2] == "push")
    assert push[0] == "/repo", f"cwd leaked out of the subshell: {found!r}"


@pytest.mark.parametrize(
    "command",
    [
        "git -C ';' push origin dev",
        "git -c ';' push origin dev",
    ],
)
def test_value_slot_ambiguity_still_records_an_invocation(command):
    """PRESERVE, and the direction that ALLOWS if F3 is implemented as only "hand the token back":
    a literal reading of "do not consume the boundary token" makes `i = j` the whole fix, which
    drops the invocation outright -- neither "push" nor anything else ever gets attached to a `git`
    invocation, so the walk finds nothing and a real command that should block is silently allowed.
    The fix must also append an unjudgeable invocation for the truncated `git`, so at least one
    invocation is always recorded here. A SECOND `git` token before "push" would re-anchor the walk
    on its own (the pre-existing, already-covered two-`git` form) and pass even under a "drop"
    implementation -- deliberately single-`git`, so this actually exercises the append half."""
    found = git_command.iter_git_invocations_with_cwd(command, "/repo")
    assert found, f"invocation disappeared for {command!r}: {found!r}"


def test_value_slot_ambiguity_control_rows_still_detect_the_real_push():
    """Controls, both PRESERVE. The first has no `-c`/`-C` at all -- an ordinary subshell `cd`
    isolated by the pre-existing paren-counting branch, untouched by F3. The second DOES exercise
    F3's branch (`-c` immediately precedes the closing `)`, the same ambiguity as the security row
    above) but has no `cd` inside the subshell to leak, so it was already correctly detected before
    this fix; it pins that the two-part change does not newly drop it."""
    assert "push" in _subs("(cd /elsewhere && true) ; git push origin dev")
    assert "push" in _subs("(git -c) ; git push origin dev")


# ---------- the walk's record shape was restated in three places, and drifted ----------
# `_walk_context` appends FIVE-element records at all three of its append sites, while its return
# annotation, its `results` declaration and its docstring each said FOUR. Nothing failed and nothing
# could: annotations are not checked at runtime, and both public wrappers were independently correct
# -- `iter_git_invocations_with_cwd` slices to four deliberately, `iter_git_invocations_detailed` is
# annotated five. So the code was right and every line that claimed to describe it was wrong, which
# is the adjacency hazard exactly: the next editor adding a sixth element is told the wrong shape by
# the only three lines that state one.
#
# The repair is to stop RESTATING the shape rather than to correct the three copies of it: one
# `Invocation` NamedTuple that the annotations name instead of respelling. Slicing a NamedTuple
# yields a plain tuple, so `r[:4]` and every positional unpack downstream stay byte-identical --
# asserted below rather than assumed.
#
# This pair also closes a real gap: until now nothing in THIS suite exercised the detailed form or
# `InvocationTokens` at all. The fifth element is what lets a config check scope itself to one
# invocation's own tokens, and its only coverage was end-to-end, through the guard.


def test_detailed_records_expose_their_fields_by_name():
    """RED before the NamedTuple -- the walk emitted bare tuples, so there were no names to read and
    prose was the only statement of the shape, which is precisely what had drifted. Naming the
    fields makes the shape a value that cannot disagree with itself.

    `-c core.hooksPath=...` is the shape the fifth element exists for: the key sits in the global
    option run, ahead of a subcommand that is itself entirely innocent."""
    (inv,) = git_command.iter_git_invocations_detailed(
        "git -c core.hooksPath=/x status", "/repo"
    )
    assert inv.effective_dir == "/repo"
    assert inv.cdir is None
    assert inv.subcommand == "status"
    assert inv.arg_tokens == []
    assert inv.tokens.opts == ["-c", "core.hooksPath=/x"], (
        f"the option run must be carried verbatim and in order: {inv.tokens.opts!r}"
    )
    assert inv.tokens.env == []


def test_detailed_records_carry_the_env_prefix_of_their_own_invocation():
    """The other half of the fifth element, and the one alignment is load-bearing for: with two
    invocations in one line, each must carry ITS OWN env prefix. A consumer that scoped a check to
    `tokens.env` while the walk handed it the other command's assignments would match text belonging
    to a command it is not judging -- the whole reason these tokens ride on the record instead of
    being re-derived by the caller."""
    first, second = git_command.iter_git_invocations_detailed(
        "GIT_CONFIG_COUNT=1 git status && git log", "/repo"
    )
    assert first.subcommand == "status"
    assert first.tokens.env == ["GIT_CONFIG_COUNT=1"]
    assert second.subcommand == "log"
    assert second.tokens.env == [], (
        f"the second invocation inherited the first's env prefix: {second.tokens.env!r}"
    )


def test_the_four_tuple_form_stays_a_plain_four_tuple():
    """PRESERVE, and green before the refactor as well as after -- recorded as a preserve row rather
    than dressed up as the RED. Its value is the one direction a NamedTuple could break things:
    `iter_git_invocations_with_cwd` is unpacked positionally at ~40 call sites, so its slice must
    keep yielding a PLAIN four-tuple, not a five-field record that merely compares equal to one.
    `type(...) is tuple` is deliberate over `isinstance`, which every NamedTuple would satisfy."""
    command = "cd /elsewhere && git -c core.hooksPath=/x status"
    four = git_command.iter_git_invocations_with_cwd(command, "/repo")
    five = git_command.iter_git_invocations_detailed(command, "/repo")
    assert four, "fixture built no invocations -- the rows below would pass vacuously"
    assert [len(r) for r in four] == [4] * len(four)
    assert all(type(r) is tuple for r in four), (
        f"the four-tuple form must stay a plain tuple for positional unpacking: {four!r}"
    )
    assert four == [tuple(r)[:4] for r in five], (
        "the two public forms disagree: the four-tuple form is no longer the detailed form's "
        f"first four elements -- {four!r} vs {five!r}"
    )


# ---------- the two backward walks must consult the SAME wrapper set ----------
# `_git_starts_command` proves command position with `extra_wrappers=GIT_ONLY_WRAPPERS`, while
# `_env_prefix` -- whose docstring says the two "must stay in step", because the tokens one steps
# OVER are the tokens the other must COLLECT -- hand-copied the single current member as a literal
# `prev == "exec"`. The two agree today only because the set happens to have exactly one element, so
# no input can currently tell them apart and no ordinary test could be red.
#
# The drift direction is FAIL-OPEN, which is why this is pinned rather than left to the docstring:
# add a member to GIT_ONLY_WRAPPERS and the walk still finds `git` after it, but the env prefix in
# front of it is silently dropped -- so a `GIT_CONFIG_*` assignment becomes invisible to any check
# scoped to `tokens.env`, which is exactly the config-injection detector.


def test_env_prefix_consults_GIT_ONLY_WRAPPERS_rather_than_a_hand_copied_member(
    monkeypatch,
):
    """RED before the fix. The set is monkeypatched because the invariant is about DRIFT, and drift
    is unobservable while the set has one member -- the coupling has to be exercised at a second
    member or it is not being tested at all. `runwrap` is deliberately absent from `WRAPPERS`, so
    the only thing that can put `git` in command position here is `GIT_ONLY_WRAPPERS`."""
    monkeypatch.setattr(
        git_command, "GIT_ONLY_WRAPPERS", frozenset({"exec", "runwrap"})
    )
    (inv,) = git_command.iter_git_invocations_detailed(
        "GIT_CONFIG_COUNT=1 runwrap git status", "/repo"
    )
    assert inv.subcommand == "status", (
        "precondition failed: the walk never reached the invocation, so the env assertion below "
        f"would be about nothing -- {inv!r}"
    )
    assert inv.tokens.env == ["GIT_CONFIG_COUNT=1"], (
        "the env prefix was dropped in front of a GIT_ONLY_WRAPPERS member: `_env_prefix` broke on "
        f"a wrapper `_git_starts_command` steps over -- {inv.tokens.env!r}"
    )


def test_env_prefix_still_collects_across_the_real_wrapper_sets():
    """PRESERVE, green before and after -- the un-monkeypatched controls, one per set, so the fix
    cannot buy the row above by breaking the behaviour that already worked."""
    (via_git_only,) = git_command.iter_git_invocations_detailed(
        "GIT_CONFIG_COUNT=1 exec git status", "/repo"
    )
    assert via_git_only.tokens.env == ["GIT_CONFIG_COUNT=1"]
    (via_wrappers,) = git_command.iter_git_invocations_detailed(
        "GIT_CONFIG_COUNT=1 sudo git status", "/repo"
    )
    assert via_wrappers.tokens.env == ["GIT_CONFIG_COUNT=1"]


def test_only_dash_C_has_an_attached_short_form_because_only_dash_C_accepts_one():
    """The asymmetry between `-C` and `-c` is real, not an oversight, and a consumer downstream had
    written a branch for an attached `-c` that could never fire. MEASURED against git 2.55:
    `git -cfoo.bar=baz config --get foo.bar` exits 129 with "unknown option: -cfoo.bar=baz", while
    the separated `-c foo.bar=baz` form prints `baz`. So an attached `-c` is not merely unhandled
    here -- it is not a command git will run.

    Pinned because a deletion downstream now RESTS on it: were this to start classifying as `flag`,
    the invocation would become judgeable and a spelling that is currently refused would be waved
    through, with nothing left to catch it."""
    assert git_command.classify_global_opt("-cfoo.bar=baz") == "unknown"
    assert git_command.classify_global_opt("-c") == "value"
    assert git_command.classify_global_opt("-C/some/path") == "flag"
    assert git_command.classify_global_opt("-C") == "value"


# ---------- a reserved word in ARGUMENT position ----------
#
# Bash recognises a reserved word only in command position. After an argument it is an ordinary
# word, so everything behind it is still the same command's argv. The argument-segment scan once
# stopped at any `git` that `_git_starts_command` placed after a reserved word, which cut the list
# short: `git <push> origin main -o then HEAD:refs/heads/x/git dev` recorded only
# `['origin', 'main', '-o', 'then']`, and the publication guard never saw `dev`. The property below
# is CAPABILITY PRESERVATION: every recorded invocation equals the one recorded for its NEUTRAL
# spelling -- the same text with every reserved word replaced by an ordinary word -- and each nested
# `git` is still recorded on its own, grown by the same rule. A verdict that moves therefore lands on
# one the neutral spelling already gets; nobody gains a command they could not already type.

_ARG_VERB = "pu" + "sh"
_NEUTRAL = "ZZ"
# What may sit between the reserved word and the nested `git`: nothing, one of each kind of wrapper
# spelling `_steps_as_wrapper` steps over, `exec` (a git-only wrapper), and an env assignment. (A
# second reserved word is NOT a shape here: it would open a nested invocation in the neutral
# spelling too.)
_ARG_BETWEEN = ("", "eval ", "builtin ", "eval -- ", "sudo ", "exec ", "FOO=1 ")


def _invs(command: str) -> list:
    return git_command.iter_git_invocations_detailed(command, "/base")


@pytest.mark.parametrize("between", _ARG_BETWEEN)
@pytest.mark.parametrize("word", sorted(git_command.RESERVED_WORDS))
def test_a_reserved_word_in_argument_position_does_not_cut_the_argument_list(
    word, between
):
    command = (
        f"git {_ARG_VERB} origin main -o {word} {between}HEAD:refs/heads/x/git dev"
    )
    neutral = (
        f"git {_ARG_VERB} origin main -o {_NEUTRAL} {between}HEAD:refs/heads/x/git dev"
    )
    got, want = _invs(command), _invs(neutral)
    # CONTROL: the neutral spelling really is neutral -- one invocation, the whole argv.
    assert len(want) == 1, want
    outer = got[0]
    assert outer.subcommand == _ARG_VERB
    renamed = [_NEUTRAL if t == word else t for t in outer.arg_tokens]
    assert outer._replace(arg_tokens=renamed) == want[0]
    assert outer.arg_tokens[-1] == "dev"
    # The nested `git` (here the refspec ending in /git) is still recorded, as before.
    assert [inv.subcommand for inv in got[1:]] == ["dev"]


def _neutral(text: str) -> str:
    return " ".join(
        _NEUTRAL if t in git_command.RESERVED_WORDS else t for t in text.split(" ")
    )


def _renamed(inv, word: str):
    # The cutting word can sit in the SUBCOMMAND slot as well as in the argument list.
    def swap(t):
        return _NEUTRAL if t == word else t

    return inv._replace(
        subcommand=swap(inv.subcommand), arg_tokens=[swap(t) for t in inv.arg_tokens]
    )


# Each slot is (prefix up to the cutting word, text after the word). The prefix never contains a
# reserved word, so `_neutral` touches only the words this test inserts.
_ARG_SLOTS = {
    "argument": (f"git {_ARG_VERB} origin main", "git log x"),
    "option_value": (f"git {_ARG_VERB} origin main -o", "HEAD:refs/heads/x/git dev"),
    "subcommand": ("git", "git status x"),
}


@pytest.mark.parametrize("tail", [False, True])
@pytest.mark.parametrize("between", _ARG_BETWEEN)
@pytest.mark.parametrize("slot", sorted(_ARG_SLOTS))
@pytest.mark.parametrize("word", sorted(git_command.RESERVED_WORDS))
def test_every_record_reads_like_its_neutral_spelling(word, slot, between, tail):
    prefix, after = _ARG_SLOTS[slot]
    # Each nested command starts at `between` -- its env/wrapper tokens belong to it, not to the
    # command before it -- and `tail` adds a SECOND cut inside the first nested command's argv.
    nested = [f"{between}{after}"] + ([f"{between}git status dev"] if tail else [])
    command = f"{prefix} {word} " + f" {word} ".join(nested)
    got = _invs(command)
    whole = _invs(_neutral(command))
    # CONTROL: the neutral spelling is one invocation carrying the whole argv.
    assert len(whole) == 1, whole
    assert len(got) == 1 + len(nested), got
    assert _renamed(got[0], word) == whole[0]
    for index, _text in enumerate(nested, start=1):
        rest = f" {word} ".join(nested[index - 1 :])
        alone = _invs(_neutral(rest))
        assert len(alone) == 1, alone
        assert _renamed(got[index], word) == alone[0]
    # Nothing is cut: every record's words run to the last token of the line. (A nested record can
    # consist of its subcommand alone -- `… HEAD:refs/heads/x/git dev` records subcommand `dev`.)
    last = command.split(" ")[-1]
    assert all(([inv.subcommand] + inv.arg_tokens)[-1] == last for inv in got), got


def test_a_reserved_word_in_the_subcommand_slot_does_not_cut_the_list():
    got = _invs("git then git status dev")
    assert [(inv.subcommand, inv.arg_tokens) for inv in got] == [
        ("then", ["git", "status", "dev"]),
        ("status", ["dev"]),
    ]


def test_an_operator_still_ends_the_argument_list():
    got = _invs(f"git {_ARG_VERB} origin main && git log dev")
    assert [(inv.subcommand, inv.arg_tokens) for inv in got] == [
        (_ARG_VERB, ["origin", "main"]),
        ("log", ["dev"]),
    ]


def test_a_phantom_inside_an_argument_list_is_still_recorded():
    # Bash pushes nothing here: `then git <push> origin dev` are arguments of `git log`. The nested
    # invocation is kept on purpose -- dropping it is exact only while `is_op` never misses a real
    # operator, and a missed operator would turn a dropped phantom into a hidden real push.
    got = _invs(f"git log x then git {_ARG_VERB} origin dev")
    assert [(inv.subcommand, inv.arg_tokens) for inv in got] == [
        ("log", ["x", "then", "git", _ARG_VERB, "origin", "dev"]),
        (_ARG_VERB, ["origin", "dev"]),
    ]


def test_chained_phantoms_each_see_the_rest_of_the_argv():
    got = _invs(f"git {_ARG_VERB} origin then git log then git status dev")
    assert [(inv.subcommand, inv.arg_tokens) for inv in got] == [
        (_ARG_VERB, ["origin", "then", "git", "log", "then", "git", "status", "dev"]),
        ("log", ["then", "git", "status", "dev"]),
        ("status", ["dev"]),
    ]


def test_the_grown_tail_is_descended_after_the_outer_record():
    # The descent stops at `resume`, not at the end of the grown argument segment: every token past
    # `resume` belongs to the nested invocation the walk resumes at, because nested contexts in the
    # grown tail belong to that nested invocation, not to the outer one. They are therefore descended
    # only once that nested invocation's own turn comes, so its record comes AFTER the outer one.
    placeholder = git_command.PLACEHOLDER_PREFIX + "0" + git_command.PLACEHOLDER_SUFFIX
    got = _invs(f'git log x then git log "$(git {_ARG_VERB} origin dev)" y')
    assert [(inv.subcommand, inv.arg_tokens) for inv in got] == [
        ("log", ["x", "then", "git", "log", placeholder, "y"]),
        (_ARG_VERB, ["origin", "dev"]),
        ("log", [placeholder, "y"]),
    ]


@pytest.mark.parametrize(
    "command",
    [
        f"for x do git {_ARG_VERB} origin dev; done",
        f"function f {{ git {_ARG_VERB} origin dev; }}; f",
        f"coproc NAME {{ git {_ARG_VERB} origin dev; }}",
        f"echo if git {_ARG_VERB} origin dev",
    ],
)
def test_a_reserved_word_after_a_non_git_word_still_opens_a_command(command):
    # These carry no outer git invocation, so the segment scan never reaches them; each must still
    # record the push. A reading that honours reserved words ONLY in command position was measured
    # to stop detecting the first three, each a push bash can run (`for x do` iterates the positional
    # parameters, so it runs inside a script or function).
    got = _invs(command)
    assert [(inv.subcommand, inv.arg_tokens) for inv in got] == [
        (_ARG_VERB, ["origin", "dev"])
    ]


# ---------- opaque command words (2026-09-18: git behind a substitution / expansion) ----------
#
# Each shape below was run under /bin/bash 3.2.57 and MacPorts bash 5.3.15, with a fake `git`
# first on PATH that only echoes its argv: bash ran git for every one. Before this change every
# guard allowed all of them, because `is_git` accepted only the literal text `git` or a path
# ending in `/git`.

_OPAQUE_VERB = "pu" + "sh"
_OPAQUE_SHAPES = [
    "$(true)git",
    "`true`git",
    "git$(true)",
    '"$(true)"git',
    "${X}git",
    '"$X"git',
    '$X""git',
    "$'git'",
    "g$(true)it",
    "gi${X}t",
    "/usr/bin/$(true)git",
    "git$X",
    "git${X}",
    'git"$X"',
    "${X:-git}",
    "${X-git}",
    "${X:=git}",
    "${X+git}",
]


@pytest.mark.parametrize("word", _OPAQUE_SHAPES)
def test_an_opaque_word_that_bash_reduces_to_git_is_recorded(word):
    got = _invs(f"{word} {_OPAQUE_VERB} origin dev")
    assert [(inv.subcommand, inv.arg_tokens) for inv in got] == [
        (_OPAQUE_VERB, ["origin", "dev"])
    ]


@pytest.mark.parametrize(
    "command",
    [
        "legit status",  # static text: not opaque, so the widening never applies
        "digit status",
        "gitk status",
        "echo $(true)git status",  # argument position
        "ls ${HOME}/git",  # argument position
        "$G status",  # wholly dynamic: the stated residual, NOT recorded
        "$(echo git) status",  # wholly dynamic: the stated residual, NOT recorded
        "${X:-a,git} status",  # a comma inside `${…}` is not a brace expansion: bash runs `a,git`
    ],
)
def test_words_the_widening_must_not_record(command):
    assert _invs(command) == []


@pytest.mark.parametrize(
    "command",
    [
        f"REPO=$BASE/foo.git git {_OPAQUE_VERB} origin dev",
        f"BIN=$R/nogit git {_OPAQUE_VERB} origin dev",
        f"X=/git git {_OPAQUE_VERB} origin dev",
        f"TOKEN=$(cat t)git git {_OPAQUE_VERB} origin dev",
    ],
)
def test_an_assignment_is_never_the_git_word(command):
    # An assignment is never the command word. Matching one made push-guard's own scan take
    # the real `git` as the subcommand and bury the push.
    assert not git_command.is_git(command.split()[0])
    got = _invs(command)
    assert [(inv.subcommand, inv.arg_tokens) for inv in got] == [
        (_OPAQUE_VERB, ["origin", "dev"])
    ]


@pytest.mark.parametrize(
    "command",
    [
        "git $(true)git origin dev",
        f"git -c alias.git={_OPAQUE_VERB} git origin dev",
        "git ${X}git origin dev",
    ],
)
def test_a_git_like_subcommand_is_recorded_not_dropped(command):
    # The option-run stop used to DROP the invocation when the subcommand slot held a bare
    # `git` (premise: never a real subcommand). An alias named `git` makes it one, and an opaque
    # word may expand to anything — so it is recorded, indeterminate, and every consumer blocks it.
    got = _invs(command)
    assert len(got) == 1
    assert git_command.subcommand_is_indeterminate(got[0].subcommand)


@pytest.mark.parametrize(
    ("sub", "want"),
    [
        ("status", False),
        (_OPAQUE_VERB, False),
        ("git", True),
        ("$V", True),
        (git_command.PLACEHOLDER_PREFIX + "0" + git_command.PLACEHOLDER_SUFFIX, True),
        ("${X}git", True),
    ],
)
def test_subcommand_is_indeterminate(sub, want):
    assert git_command.subcommand_is_indeterminate(sub) is want


_N = git_command.MAX_COMMAND_LENGTH


@pytest.mark.parametrize(
    "word",
    [
        "${" * (_N // 2),
        "{" + "," * (_N - 1),
        "x" * (_N // 4) + "{" + "," * (_N // 4) + "}" + "$a" * (_N // 4),
        "$a" * (_N // 2),
    ],
    ids=[
        "dollar-brace-run",
        "open-brace-commas",
        "long-prefix-brace-long-suffix",
        "dollar-name-run",
    ],
)
def test_is_git_is_linear_on_a_pathological_word(word):
    # A guard that outruns its hook timeout lets the command RUN, so `is_git` must stay linear on
    # a maximum-length word. Three drafts were not: `\{[^}]*\}` in the parameter regex rescanned
    # to end-of-token from every `$` (5.9 s on 60k); a brace-expansion regex with the comma inside
    # its pattern backtracked on an unclosed `{` (14.3 s at 32k commas); and recursing into each
    # brace alternative with the whole prefix and suffix was quadratic again (67.8 s at 64k). The
    # brace clause was then deleted; the two brace-shaped words stay here so that bringing any
    # such clause back is measured against them. Shipped cost on this bound: under 0.01 s.
    import time

    start = time.perf_counter()
    git_command.is_git(word)
    assert time.perf_counter() - start < 0.2
