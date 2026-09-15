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


# ---------------------------------------------------------------------------------------------
# RED regression rows -- unterminated substitution openers in a heredoc body (r1-r5, MUST FAIL
# until Task 2 lands). See specs/2026-09-03-heredoc-unbalanced-substitution.md and
# plans/2026-09-03-heredoc-unbalanced-substitution.md, Task 1.
#
# These pin the underlying `git_command.py` tokenizer directly, one layer below the full guard's
# rc/stderr (asserted end-to-end in test_publication_push_guard.sh's own r1-r5 rows). Today, an
# unterminated backtick or `$(` anywhere in a heredoc body raises `ParseAmbiguity` -- even when
# the heredoc's delimiter is QUOTED, so bash itself would never expand the body -- which is what
# every consumer's fail-closed catch turns into a blanket refusal. After the fix, the opener
# closes at the body boundary and the tokenizer sees the real invocation instead of raising.
# ---------------------------------------------------------------------------------------------


def _subs(command: str) -> list[str]:
    return [
        sub
        for _dir, _cdir, sub, _args in gitcmd.iter_git_invocations_with_cwd(
            command, "/tmp"
        )
    ]


def test_r6_opener_hidden_inside_a_hash_comment_is_not_mistaken_for_a_real_opener():
    """r6 (added for Task 3's mutant 6, plan `2026-09-03-heredoc-unbalanced-substitution.md`):
    a backtick sitting inside a `#`-started comment LINE of the body is not a real unterminated
    opener at all -- the top-level pipeline's own `strip_comments` pass removes that whole line
    before the tokenizer ever sees it, exactly as it would for any other command text.

    The row survives an ABANDONED change and is kept deliberately. It was written to pin one
    half of a heredoc repair pass that was tried four times and withdrawn four times, each
    attempt shipping a fail-open (see the design record in project memory). What it asserts is
    independent of that pass: a comment-hidden opener must not defeat the walk, which holds
    because the pipeline's own `strip_comments` runs before the tokenizer sees the body.

    Its value now is as a REGRESSION GUARD on the next attempt. Any future pass that repairs
    unparseable heredoc text has to decide what a comment-hidden opener means, and the failure
    mode is specific: treat it as a genuine opener, append a closer for it, and that appended
    closer survives the real `strip_comments` -- which removes the ORIGINAL backtick but not the
    append -- leaving one unmatched backtick and turning a clean parse into a raised
    `ParseAmbiguity`. No other row in this file exposes that difference, because every one of
    them gives the correct answer once a real, non-comment opener is present.
    """
    command = "cat <<'EOF'\n# a ` stray\ngit push origin dev\nEOF"
    subs = _subs(command)
    assert "push" in subs, subs


# --------------------------------------------------------------------------------------------
# Shape assertions on the MASKED TEXT (Task 3, mutants 4 and 5).
#
# Both mutants survived the first campaign, and for ONE reason: the plan asked for these
# properties at the PARSE-OUTCOME level, where neither is observable. Closer placement does not
# change any parse -- `split_command_contexts` pairs backticks and parens by a bare character
# scan with no word-boundary requirement, so only totals matter -- and a wrongly-appended closer
# on an already-unparseable body leaves the command refused either way. Asserting one level down,
# on what `mask_heredoc_quotes` EMITS, makes both decisions visible. The instrument class was the
# defect, not the assertion's wording.
# --------------------------------------------------------------------------------------------


# Mutant 4 (drop the unknown-category guard) is an EXPECTED SURVIVOR, and deliberately has no row.
#
# An assertion for it was written, then deleted as vacuous. The guard's early `return body` and the
# loop-exhaustion fallback below it converge on the SAME output for every input that can reach
# either: an unrecognised failure is one no appended closer resolves, so the mutant appends,
# re-probes, fails again, exhausts the bound, and returns the unchanged body -- exactly what the
# guard returns immediately.
#
# The branch itself is NOT rare -- an earlier draft of this comment claimed the reserved context
# marker was "the one input that reaches the branch at all"; that was false. Measured 2026-09-03
# (review finding 4, independently spot-checked at 9,088): `unbalanced quote` alone reached this
# branch thousands of times over a 200,064-body corpus of neutralized, prepared bodies
# (`_neutralize_unmatched_quotes` reads the RAW body while this helper's own probe reads
# `fold_continuations(strip_comments(body))`, so a comment can delete a quote's partner the flat
# neutralizer had already counted as balanced), plus a handful of bare `ValueError`s -- of which
# the reserved context marker (with a real backtick keeping the opener count non-zero) is one
# shape, not the only one. So mutant 4's survival rests on CONVERGENCE for the inputs this suite
# exercises, never on the branch being unreachable in general.
#
# So a row here would pass under the real code AND under the mutant, which is the definition of an
# assertion that pins nothing. The guard STAYS in the source: it states the intent explicitly and
# keeps a future added category from silently taking the append path, where today it would only be
# saved by that accidental convergence. Documented, not tested -- see the plan's Task 3.


# --------------------------------------------------------------------------------------------
# The BOUNDARY-RELOCATION fail-open (design #3's defect; found by the final whole-branch review).
#
# Choosing the closer from the body IN ISOLATION is unsound: in the full command that same opener
# often pairs with a closer OUTSIDE the body. Appending orphans that partner, moves every context
# boundary after the heredoc, and a real push falls out of any walked context -- so a POLICY BLOCK
# became an ALLOW. Measured: the base build reported one invocation and refused; design #3
# reported zero and allowed, and the shape really executes (verified with a harmless payload in
# the push's position, which ran before bash errored on the outer construct).
#
# The lesson these rows exist to keep: INSERTING text hides a command as effectively as DELETING
# it, because it relocates boundaries. Design #2 was rejected for hiding a command by escaping;
# design #3 hid one by the mirror mechanism, and the whole safety argument missed it because it
# reasoned about what gets ESCAPED rather than about where boundaries LAND.
# --------------------------------------------------------------------------------------------


def _pushes_found(command):
    """Subcommands the walk reports, or the refusal category. Never executes anything."""
    try:
        return _subs(command)
    except ValueError as exc:  # ParseAmbiguity included
        return "REFUSED: {}".format(exc)


def test_a_heredoc_whose_opener_pairs_outside_the_body_still_sees_the_push():
    """The minimal delta-debugged reproducer. The body core is `)$(`: probed alone the `)` closes
    nothing and the `$(` is unterminated, so a body-local decision appends `)`. In the FULL text
    the outer `$(` scan closes on the body's `)`, and the trailing `)` then pairs with the body's
    `$(` -- making the push a walked context. Appending destroys exactly that pairing.
    """
    command = '"$(<<EOF\n)$(\nEOF\ngit push origin dev)"'
    assert "push" in _pushes_found(command), _pushes_found(command)


def test_dropping_the_pipeline_preparation_cannot_turn_a_refusal_into_a_silent_allow():
    """The closer pass must probe with the SAME preparation the real pipeline applies.

    Under the earlier per-body design a comment-hidden opener was enough to show this. Design #4
    gates the closer pass behind a whole-command parse failure, so that row no longer reaches the
    mutated code — it parses, returns unchanged, and the probe is never consulted. The mutant
    survived, which looked like convergence and is not: measured over 4,000 generated commands it
    diverges on 6, and this is one of them.

    The direction is what makes it worth a row. Here the real code REFUSES, while a probe without
    the preparation reports ZERO invocations — an allow. A refusal turning into a silent allow is
    the fail-open direction, so the assertion is that this input is still refused rather than
    quietly waved through.
    """
    dq, tick = chr(34), chr(96)
    command = (
        "x=" + dq + "$(cat <<EOF\n"
        "O<<EOF"
        + dq
        + "#"
        + dq
        + dq
        + ">>git push origin dev< a)|##&>"
        + tick
        + "O<<EOF\n"
        "EOF\n)" + dq
    )
    assert _pushes_found(command).startswith("REFUSED"), _pushes_found(command)


def test_the_reported_msg_capture_form_still_sees_the_push():
    """The naturalistic shape of the same class, and the form `mask_heredoc_quotes`'s own docstring
    calls "the reported form" -- a message captured from a heredoc."""
    command = 'msg="$(cat <<EOF\nsee foo) and $(\nEOF\ngit push origin dev)"'
    assert "push" in _pushes_found(command), _pushes_found(command)


def test_append_assign_re_stays_a_subset_of_the_shared_env_assign() -> None:
    """`_APPEND_ASSIGN_RE` must never match a token `gitcmd.ENV_ASSIGN` does not.

    This is the branch's THIRD instance of one defect class made loud. Twice a local predicate
    silently diverged from the shared one it duplicated -- `_looks_like_unresolvable_expansion`
    wired into one of two sites, and a literal separator frozenset that a comment CLAIMED matched
    `is_op` and did not. Both were fail-opens, and neither had a local tell.

    The coupling this pins is the last one of that shape. `_assign_name` strips the `+` from an
    append-form token, and it only ever sees such a token because `ENV_ASSIGN` admits it. Narrow
    `ENV_ASSIGN` back to `=`-only and the guard stops seeing the whole invocation (measured: ZERO
    invocations for `FOO+=1 git <verb>`), while this narrower regex keeps matching -- so nothing
    here would fail. That is exactly the silent divergence this asserts against.
    """
    import itertools

    matched = 0
    for n in range(1, 5):
        for token in ("".join(c) for c in itertools.product("aB_9+=", repeat=n)):
            if guard._APPEND_ASSIGN_RE.match(token):
                matched += 1
                assert gitcmd.ENV_ASSIGN.match(token), (
                    f"{token!r} matches the guard's append-form regex but NOT the shared "
                    "gitcmd.ENV_ASSIGN -- the two have diverged, and `_assign_name` is now "
                    "reachable for a token the tokenizer never collects"
                )
    assert matched > 0, (
        "the enumeration matched no append-form token at all -- a vacuous pass, so the "
        "subset assertion above proved nothing"
    )
