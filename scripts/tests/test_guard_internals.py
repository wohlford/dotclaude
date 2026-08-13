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
