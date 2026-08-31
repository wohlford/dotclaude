"""White-box unit tests for `publication-push-guard.py` internals that a subprocess-driven
corpus row (see `test_guard_corpus.py`) cannot reach, because it can only feed the guard a
command string on stdin -- it cannot stub one of the guard's own functions.

T5 / F4 -- `_judge_invocation`'s root-resolved check reads `if root is None:`; it should read
`if not root:` so an empty-string root is refused too, not only a `None` one.

THIS IS DEFENSIVE HARDENING, NOT COVERAGE OF A LIVE PATH -- say so here rather than let the test
read as though it exercised something reachable. Measured: `_resolve_root` cannot currently
return `""`. It returns `None` whenever `git rev-parse --show-toplevel` exits non-zero, and in a
bare repo (the shape most likely to omit a worktree) that command exits 128, not 0 with empty
stdout -- no input was found that drives a real `_resolve_root` call to the empty string. The
test below stubs `_resolve_root` directly to reach the branch anyway. It is worth reaching:
`_repo_is_adopted_root` was already hardened against an empty root at its OWN call site (`if not
root: return True`, fail-closed), specifically because an unguarded empty root flows into
`git -C ""` subprocesses, which git documents as leaving the working directory UNCHANGED --
silently judging whatever repo the guard process happens to be sitting in, rather than the one
the caller meant. `_judge_invocation`'s root check is the OTHER place that same empty string can
still flow through, one call earlier.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUARD_PATH = REPO_ROOT / "scripts" / "publication-push-guard.py"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
import git_command as gitcmd  # noqa: E402, I001


def _load_guard() -> ModuleType:
    """Import the live guard under its own module name (never `publication_push_guard`, which
    nothing else currently claims, but naming it distinctly costs nothing and matches
    `test_guard_corpus.py`'s baseline loader). `exec_module` runs the file's top-level code --
    function and constant definitions only, since `main()` is gated by `if __name__ ==
    "__main__"` -- so importing it here has no side effects."""
    spec = importlib.util.spec_from_file_location(
        "publication_push_guard_live", GUARD_PATH
    )
    assert spec is not None and spec.loader is not None, f"could not load {GUARD_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()

# The exact substring `_judge_invocation` emits ONLY from its own root-unresolved branch -- not
# from `_repo_is_adopted_root`'s differently-worded empty-root guard, and not from anything
# `_judge_push` can say. Asserting this, rather than "some Block resulted", is what makes the
# test mean something -- see the row built below, which DOES produce a Block pre-fix too, for a
# reason that has nothing to do with the root.
_ROOT_UNRESOLVED_SUBSTRING = "the repo root could not be resolved"


def test_empty_string_root_is_refused_with_the_root_unresolved_reason(monkeypatch):
    """Defensive branch (see module docstring) -- stub `_resolve_root` to return `""` and require
    the SPECIFIC root-unresolved reason, not a bare refusal.

    The remaining argument list is `["--all"]`. `_judge_push`'s `--all` check is pure Python --
    no subprocess, no dependence on the ambient cwd's git state -- and returns a block for its
    own, unrelated reason UNCONDITIONALLY, on any root including `""`. Pre-fix, an empty root
    sails past the (already-hardened) adoption check and into `_judge_push`, which returns THAT
    block -- so a bare `assert result is not None` would already be green before the fix,
    proving nothing about the root check at all. Only the specific substring distinguishes
    "refused because the root is empty" from "refused because of --all", and that substring is
    unreachable until `_judge_invocation`'s own check widens from `is None` to falsy.
    """
    monkeypatch.setattr(guard, "_resolve_root", lambda effective_dir: "")

    result = guard._judge_invocation(
        "/wherever", "push", ["--all"], gitcmd, gitdir_override=False
    )

    assert result is not None, "an empty-string root must still be refused"
    assert _ROOT_UNRESOLVED_SUBSTRING in result.reason, (
        f"refused, but not for the root-unresolved reason -- got: {result.reason!r}"
    )


def test_describe_ambiguity_is_non_raising_for_every_poisoned_shape() -> None:
    """A broken diagnostic must degrade to "", never raise.

    This is the pin for the review's BLOCKER, and the reason it matters is the hook contract, not
    tidiness: `describe_ambiguity` is called from INSIDE the `except` block that emits each guard's
    refusal, and per `scripts/HOOKS.md` only exit 2 blocks -- any other nonzero exit is treated as
    noise rather than a veto. An exception escaping here exits 1, so the guarded command RUNS. A
    diagnostic that can unblock is strictly worse than no diagnostic.

    Every shape below is reachable: a non-ambiguity cause (the depth, length, internal-marker and
    reserved-marker raise sites, plus shlex's own errors, none of which carry attributes); a None
    cause; and a position that has drifted out of range of the prepared text.
    """
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location(
        "git_command", REPO_ROOT / "scripts" / "lib" / "git_command.py"
    )
    gitcmd = _ilu.module_from_spec(spec)
    sys.modules["git_command"] = gitcmd
    spec.loader.exec_module(gitcmd)

    class _Poison:
        """Attribute access itself raises -- the nastiest shape a getattr-based reader can meet."""

        def __getattr__(self, name: str):
            raise RuntimeError(f"poisoned attribute {name}")

    poisoned = [
        None,
        ValueError("plain, carries nothing"),
        gitcmd.ParseAmbiguity("cat", text="abc", pos=None),
        gitcmd.ParseAmbiguity("cat", text="abc", pos=999),
        gitcmd.ParseAmbiguity("cat", text="abc", pos=-1),
        gitcmd.ParseAmbiguity("cat", text="", pos=0),
        gitcmd.ParseAmbiguity("cat", text=None, pos=1),
        gitcmd.ParseAmbiguity("cat", text="abc", pos="not an int"),
        _Poison(),
    ]
    for exc in poisoned:
        out = gitcmd.describe_ambiguity(exc)
        assert isinstance(out, str), f"{exc!r} returned a non-string {out!r}"
        assert out == "", f"{exc!r} produced a clause it should not have: {out!r}"


def test_describe_ambiguity_locates_a_real_opener() -> None:
    """The other half: when a position IS available it must point at the OPENER.

    A wrong position is the one way this change is worse than the category alone -- it "works" and
    mis-teaches, so nothing flags it. Measured for exactly this reason: `_first_unmatched_quote`
    was the originally-proposed source and points into a backtick body on prose-with-apostrophes,
    which is the measured heredoc class.
    """
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location(
        "git_command", REPO_ROOT / "scripts" / "lib" / "git_command.py"
    )
    gitcmd = _ilu.module_from_spec(spec)
    sys.modules["git_command"] = gitcmd
    spec.loader.exec_module(gitcmd)

    cmd = 'git status "a ` stray"'
    try:
        list(gitcmd.iter_git_invocations_detailed(cmd, "/tmp"))
        raise AssertionError("expected an ambiguity for an odd backtick count")
    except gitcmd.ParseAmbiguity as exc:
        assert exc.text[exc.pos] == "`", (
            f"pos {exc.pos} points at {exc.text[exc.pos]!r}, not the opening backtick"
        )
        clause = gitcmd.describe_ambiguity(exc)
        assert "col " in clause and "search for the excerpt" in clause, clause


def test_parse_ambiguity_str_is_byte_identical_to_the_bare_message() -> None:
    """`str()` must equal EXACTLY what a bare ValueError said before the subclass existed.

    This is the load-bearing property of the whole change: every consumer catches bare
    `except ValueError`, so the subclass is inert ONLY while its message is unchanged. A caller
    that greps the message -- and the guards interpolate it verbatim into their refusals -- sees a
    different string the moment this drifts.

    EXACT equality, not `in`. A mutation prepending text to the message survived the entire suite
    because every other assertion here matches by substring, and the original category is still a
    substring of a prefixed one. Substring matching cannot see this class of change at all.

    The three strings below are the messages `dev` raised at these sites before `ParseAmbiguity`
    was introduced; they are the contract, not a restatement of the implementation.
    """
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location(
        "git_command", REPO_ROOT / "scripts" / "lib" / "git_command.py"
    )
    gitcmd = _ilu.module_from_spec(spec)
    sys.modules["git_command"] = gitcmd
    spec.loader.exec_module(gitcmd)

    expected = {
        'git status "a ` stray"': "unterminated backtick substitution",
        'git status "a $( stray"': "unterminated command substitution",
        'git status "unclosed': "unbalanced quote",
    }
    for cmd, message in expected.items():
        try:
            list(gitcmd.iter_git_invocations_detailed(cmd, "/tmp"))
            raise AssertionError(f"expected an ambiguity for {cmd!r}")
        except gitcmd.ParseAmbiguity as exc:
            assert str(exc) == message, (
                f"{cmd!r}: str() is {str(exc)!r}, must be EXACTLY {message!r} -- "
                "the subclass stops being inert the moment this drifts"
            )
            assert exc.args == (message,), (
                f"{cmd!r}: args are {exc.args!r}; a single-element tuple of the bare message is "
                "what keeps str() identical"
            )
