"""Property tests for the heredoc-masking pass in scripts/lib/git_command.py.

`test_git_command.py` already states four claims about `mask_heredoc_quotes` — insert-only,
idempotent, byte-identical on balanced bodies, and no-bypass — but states each over a hand-written
list of four commands or fewer. That is how the first version of this pass shipped a fail-open: ten
assertions written for the change, three of them PRESERVE rows verified green beforehand, all
passed while the pass silently stopped matching `"git"` inside a heredoc body. A witness list
covers the inputs its author thought of, and the author is the last person able to list the one
they missed.

This module states the same claims over GENERATED input instead — every combination of heredoc
operator, delimiter-quoting style, body, and trailing command, plus several thousand seeded random
strings. Generation is deterministic (`SEED`), so any failure reproduces exactly.

Two of these are security properties, not tidiness ones. The library backs five guards
(recast-commit-gate, commit-subject-advisor, push-guard, commit-subject-guard,
publication-push-guard), and a pass that deletes a body character rather than escaping it, or that
escapes more than it must, converts a fail-closed gate into a fail-open one while every
example-based test stays green.

### The boundary-relocation corpus — kept from an abandoned change

Some of the generated shapes here exist because a heredoc repair pass was attempted four times and
withdrawn four times, each attempt shipping a fail-open. The pass is gone; the corpus is not,
because what it exercises is a property of THIS tokenizer and not of any repair:

**Any pass that repairs unparseable shell text by INSERTING characters relocates context
boundaries, and relocating boundaries hides commands.** Every one of the four attempts hit that,
by a different route — making a quoted body inert (a consuming shell still executes it); escaping
the opener (the escape takes command position and swallows the `git` behind it); closing per body
(the opener pairs with a closer OUTSIDE it); closing per whole command and requiring the result to
parse (it parses DIFFERENTLY — parseable is not the same as unchanged).

So the shapes to keep generating are the ones where an opener inside a heredoc body pairs with a
closer outside it, with a real invocation after the terminator but inside an outer wrap. Against
the current tokenizer those inputs are refused, fail-closed, and correct. They are here so that
the next attempt has to answer the only question that matters: **does every invocation the
un-repaired text exposed still appear?** — never merely "does it parse now".

The full record, including all four designs and the measurements that killed each, is in project
memory under `2026-09-03-heredoc-unbalanced-substitution`.
"""

import json
import os
import random
import re
import subprocess
import sys
import tempfile
from collections import Counter, namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import git_command  # noqa: E402, I001

SEED = 20260728
FUZZ_ROUNDS = 4000

# Case.bodies carries the heredoc bodies the command was built from, so the byte-identity property
# can select the balanced ones without asking the code under test which those are.
Case = namedtuple("Case", "command must_find_push bodies")


def _quotes_balanced(text):
    """Independent oracle: does `text` end with no quote still open?

    Deliberately a second implementation rather than a call into `git_command` — a property
    checked with the code under test as its own oracle proves only that the code agrees with
    itself.
    """
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quote != "'" and i + 1 < len(text):
            i += 2
            continue
        if quote is None and ch in "'\"":
            quote = ch
        elif ch == quote:
            quote = None
        i += 1
    return quote is None


# ---------- the generated corpus ----------

_OPERATORS = ("<<", "<<-")

# Every way a delimiter word can be written. Quoting it suppresses expansion in a real shell but
# never changes whether the BODY is literal text — which is the whole point of the pass.
_DELIMITERS = ("EOF", "'EOF'", '"EOF"', "\\EOF")

_BODIES = (
    "plain body",
    "the path's thing",  # one unmatched single quote — the reported failure
    'say "hi" twice',  # balanced double quotes — must come out untouched
    'unbalanced "quote',  # one unmatched double quote
    'it\'s "both" at once',  # unmatched single alongside a balanced pair
    'echo "#" ; still here',  # deleting these quotes would open a comment
    "don't \"mix'n\" match",  # interleaved, resolves to one unmatched double
    "already \\' escaped",  # a pre-existing escape the pass must not double
    ")",  # a bare paren, which the substitution scanner also tracks
)

_TAILS = ("", "\ngit fetch origin main", "\necho done ; git push origin dev")

# Bodies that can HOST a planted invocation, derived from `_BODIES` rather than re-listed. `)` is
# excluded because it is a bash syntax error for a reason unrelated to quoting, so nothing planted
# after it ever executes — asserting that the walk finds such a plant would assert a falsehood.
# From the repo root, this prints exactly the excluded set:
#   python3 -c "import sys;sys.path.insert(0,'scripts/tests');import test_git_command_properties as P;print(set(P._BODIES)-set(P._PLANT_HOSTS))"
# The split was checked against `bash -n` for every host × plant pair: `_quotes_balanced(host)`
# agreed with bash on whether the planted line runs in all 24 combinations (2026-07-28). Re-run that
# comparison when adding a body — a host that is malformed for some non-quote reason belongs here.
_PLANT_HOSTS = tuple(body for body in _BODIES if body != ")")

# Three ways a real invocation can sit inside a body a shell consumer would execute. The middle one
# is the regression the first version of the pass introduced: escaping turned `"git"` into `\"git\"`
# and `is_git` stopped matching it.
_PLANTS = (
    "git push origin dev",
    '"git" push origin dev',
    'echo "#" ; git push origin dev',
)


def _heredoc(consumer, operator, delimiter, body, tail):
    lines = body.split("\n")
    if operator == "<<-":
        lines = ["\t" + line for line in lines]
        terminator = "\tEOF"
    else:
        terminator = "EOF"
    return "{} {}{}\n{}\n{}{}".format(
        consumer, operator, delimiter, "\n".join(lines), terminator, tail
    )


def _structured_cases():
    """The full cross-product of heredoc shapes, each with and without a planted invocation."""
    cases = []
    for operator in _OPERATORS:
        for delimiter in _DELIMITERS:
            for body in _BODIES:
                for tail in _TAILS:
                    cases.append(
                        Case(
                            _heredoc("cat", operator, delimiter, body, tail),
                            "git push" in tail,
                            (body,),
                        )
                    )
                    if body not in _PLANT_HOSTS:
                        continue
                    for plant in _PLANTS:
                        planted = body + "\n" + plant
                        cases.append(
                            Case(
                                _heredoc("bash", operator, delimiter, planted, tail),
                                # A plant only runs if the host left no quote open: bash swallows
                                # it into the unterminated string otherwise and reports a syntax
                                # error, so the walk is right not to see it. Verified against
                                # `bash -n` for every host. A push in the TAIL sits outside the
                                # heredoc and always runs.
                                _quotes_balanced(body) or "git push" in tail,
                                (planted,),
                            )
                        )
    return cases


def _substitution_cases():
    """Heredocs nested in `$( … )`, where the enclosing context has its own quote state.

    A `"EOF"` delimiter is excluded here only: nesting a double-quoted delimiter inside a
    double-quoted substitution is ambiguous in a real shell too, so an assertion about it would be
    testing this module's opinion rather than the pass.
    """
    cases = []
    for delimiter in ("EOF", "'EOF'", "\\EOF"):
        for body in _BODIES:
            cases.append(
                Case(
                    'git commit -m "$(cat <<{}\n{}\nEOF\n)"'.format(delimiter, body),
                    False,
                    (body,),
                )
            )
            cases.append(
                Case(
                    "x=$(cat <<{}\n{}\nEOF\n) && git push origin dev".format(
                        delimiter, body
                    ),
                    True,
                    (body,),
                )
            )
    return cases


def _irregular_cases():
    """Shapes that are not a plain single heredoc, where an off-by-one is most likely."""
    return [
        # two heredocs queued on one line: both bodies are literal, in order
        Case("cat <<A <<B\na's\nA\nb's\nB", False, ("a's", "b's")),
        Case("cat <<A <<B\na's\nA\nb's\nB\ngit push origin dev", True, ("a's", "b's")),
        # `<<<` is a herestring, not a heredoc — no body follows
        Case('sh <<< "it\'s a herestring"', False, ()),
        # an operator that only appears inside quotes is not an operator
        Case("echo '<<EOF'", False, ()),
        Case('echo "a << b"', False, ()),
        # a body that is never terminated still must not swallow the parser
        Case("cat <<'EOF'\nunterminated it's", False, ("unterminated it's",)),
        # a heredoc mentioned in a comment is not a heredoc
        Case("# cat <<EOF is only a comment's text\ngit push origin dev", True, ()),
        # no heredoc at all
        Case("git status", False, ()),
        Case("git push origin dev", True, ()),
    ]


CASES = _structured_cases() + _substitution_cases() + _irregular_cases()


def _subs(command):
    return [
        sub
        for _d, _c, sub, _s in git_command.iter_git_invocations_with_cwd(
            command, "/repo"
        )
    ]


# ---------- the properties ----------


def test_the_corpus_is_large_enough_to_be_a_property():
    """Guards the generators themselves: a cross-product that silently collapsed to a handful of
    cases would leave every property below passing for the wrong reason."""
    assert len(CASES) > 500, len(CASES)
    assert sum(1 for case in CASES if case.must_find_push) > 200
    assert sum(1 for case in CASES if not case.must_find_push) > 100


def test_masking_only_ever_inserts_sanctioned_characters():
    """Text-preservation over the structured `CASES` (`test_masking_stays_insert_only_on_adversarial_input`
    is this same property over fuzz input, and states the full rationale).

    Dropping any other character silently removes a command from the walk. This was
    `masked.replace("\\\\", "") == case.command.replace("\\\\", "")` -- masking may insert
    BACKSLASHES and nothing else -- until 0 of these 855 cases exercised the closer pass (finding
    1 changed that): the closer can append a real `)` or backtick, so that exact form no longer
    holds, and it is widened the same way its fuzz twin was, for the same reason. Both halves are
    required: subsequence preserves the original catch (forbidding deletion or reordering, which
    is the shape of the fail-open this property exists to catch), and the allowlist stops
    insert-only from licensing insertion of arbitrary meaningful text.
    """
    allowed_insertions = set("\\")
    for case in CASES:
        masked = git_command.mask_heredoc_quotes(case.command)

        it = iter(masked)
        assert all(ch in it for ch in case.command), (
            "masking deleted or reordered a character, which is the shape of the fail-open "
            "this property exists to catch: {!r} -> {!r}".format(case.command, masked)
        )

        added = Counter(masked) - Counter(case.command)
        assert set(added) <= allowed_insertions, (
            "masking inserted {!r}, outside the sanctioned set {!r}: {!r}".format(
                sorted(set(added) - allowed_insertions),
                sorted(allowed_insertions),
                case.command,
            )
        )


def test_masking_is_idempotent():
    """`_prepare` re-runs the pass on every extracted context, so a heredoc inside `$( … )` is
    masked more than once; growth across runs would corrupt the body."""
    for case in CASES:
        once = git_command.mask_heredoc_quotes(case.command)
        assert git_command.mask_heredoc_quotes(once) == once, case.command


def test_a_balanced_body_comes_out_byte_identical():
    """The blast-radius guarantee, and the reason the fix is narrow enough to be safe: if every
    body already balances, nothing is touched, so no command that parses today can begin parsing
    differently tomorrow."""
    checked = 0
    for case in CASES:
        if not all(_quotes_balanced(body) for body in case.bodies):
            continue
        checked += 1
        assert git_command.mask_heredoc_quotes(case.command) == case.command, (
            case.command
        )
    assert checked > 100, checked


def test_a_planted_invocation_is_never_hidden():
    """The security direction: a heredoc body fed to a shell really is executed, so neutralising
    its quotes must not make the body inert. A fix for the fail-closed bug that buys a fail-open is
    a worse bug than the one it fixes."""
    for case in CASES:
        if not case.must_find_push:
            continue
        try:
            subs = _subs(case.command)
        except ValueError as exc:  # a fail-CLOSED regression — the original bug
            raise AssertionError(
                "walk raised on {!r}: {}".format(case.command, exc)
            ) from exc
        assert "push" in subs, "{!r} -> {!r}".format(case.command, subs)


def test_the_walk_never_fails_closed_on_a_well_formed_heredoc():
    """The original defect, stated as a property: every case here is well-formed shell, so a
    `ValueError` means an innocent command was refused."""
    for case in CASES:
        try:
            _subs(case.command)
        except ValueError as exc:
            raise AssertionError(
                "walk raised on {!r}: {}".format(case.command, exc)
            ) from exc


def _fuzz_inputs():
    alphabet = "<>'\"\\$()`;&|#\n\t EOFabc-"
    rng = random.Random(SEED)
    for _ in range(FUZZ_ROUNDS):
        yield "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 60)))


def test_masking_is_total_on_adversarial_input():
    """No crash and no hang on input nobody designed. `mask_heredoc_quotes` runs before any
    validation, so an exception here is an unhandled crash in a PreToolUse hook."""
    for text in _fuzz_inputs():
        try:
            git_command.mask_heredoc_quotes(text)
        except Exception as exc:  # noqa: BLE001 - any exception at all is the failure
            raise AssertionError("mask raised on {!r}: {}".format(text, exc)) from exc


def test_masking_stays_insert_only_on_adversarial_input():
    """Text-preservation again, over strings nobody chose — this is the property that caught the
    fail-open the hand-written suite missed.

    The assertion was once `masked.replace("\\\\", "") == text.replace("\\\\", "")` — masking may
    insert BACKSLASHES and nothing else. Closing an unterminated substitution at a heredoc body
    boundary appends a real `)` or backtick, so that exact form can no longer hold. It is
    **widened deliberately and minimally**, because the catch it is famous for is about DELETION:
    removing a quote turns `echo "#"` into `echo #`, whose `#` then comments out a following push.

    Both halves below are required and neither implies the other. The subsequence check is what
    preserves the original catch — it forbids deleting or reordering ANY character, which is
    strictly what the fail-open did — and the allowlist is what stops "insert-only" from
    licensing the insertion of arbitrary meaningful text. Widening to subsequence alone would
    permit masking to inject a `#` and comment out a push, which is the same fail-open wearing
    the other hat.
    """
    allowed_insertions = set("\\")
    for text in _fuzz_inputs():
        masked = git_command.mask_heredoc_quotes(text)

        # 1. INSERT-ONLY: the original must survive, in order, inside the masked text.
        it = iter(masked)
        assert all(ch in it for ch in text), (
            "masking deleted or reordered a character, which is the shape of the fail-open "
            "this property exists to catch: {!r} -> {!r}".format(text, masked)
        )

        # 2. Only the sanctioned characters may appear as insertions.
        added = Counter(masked) - Counter(text)
        assert set(added) <= allowed_insertions, (
            "masking inserted {!r}, outside the sanctioned set {!r}: {!r}".format(
                sorted(set(added) - allowed_insertions),
                sorted(allowed_insertions),
                text,
            )
        )


def test_the_walk_raises_nothing_worse_than_value_error():
    """Random input may legitimately be unparseable, and refusing it is correct. Any OTHER
    exception type is a crash, which a hook surfaces as an internal error and fails closed on —
    indistinguishable, to the operator, from a policy refusal."""
    for text in _fuzz_inputs():
        try:
            _subs(text)
        except ValueError:
            continue
        except Exception as exc:  # noqa: BLE001 - the point is to catch the unexpected type
            raise AssertionError(
                "walk raised {} on {!r}: {}".format(type(exc).__name__, text, exc)
            ) from exc


# =====================================================================================
# Property A -- the refuse to judged transition, through the real publication-push-guard
# =====================================================================================
#
# Everything above this line asks the TOKENIZER a question ("what invocations does it find?").
# Property A asks the GUARD's question instead ("does it block, and for which reason?"), because
# `rc` alone cannot distinguish "could not parse this at all" from "parsed it and refused a real
# dev push" -- both are rc=2 today, and a row keyed on rc alone would pass unchanged before and
# after this fix, proving nothing about it.
#
# The corpus is a structured cross-product, not fuzz -- it exists to cover every SHAPE the plan
# names (operator, delimiter quoting, consumer, opener, and where the opener sits relative to the
# push), not to find inputs nobody thought of the way the fuzz properties above do.

_PROP_A_OPERATORS = ("<<", "<<-")
_PROP_A_DELIMS = ("EOF", "'EOF'")  # unquoted vs quoted delimiter
_PROP_A_CONSUMERS = ("bash", "sh", "cat > /dev/null")
_PROP_A_OPENERS = ("`", "$(")
# "before"/"after" put the push on its own line, on either side of the offending opener; "same"
# has the opener wrap the push on one shared line (the r5 shape).
_PROP_A_POSITIONS = ("before", "after", "same")

_PROP_A_DEV_PUSH = "git " + "push origin dev"
_PROP_A_READ_ONLY = "git status"


def _prop_a_body(opener, token, position):
    if position == "before":
        return "a {} stray\n{}".format(opener, token)
    if position == "after":
        return "{}\na {} stray".format(token, opener)
    return "a {} {}".format(opener, token)  # "same"


def _prop_a_cases():
    """`(kind, command)` pairs -- `kind` is `"push"` (must block, dev-target reason) or
    `"readonly"` (must allow, no parse-refusal text). Reuses this module's own `_heredoc`
    builder, the same one the tokenizer-level CASES above are built from."""
    cases = []
    for operator in _PROP_A_OPERATORS:
        for delimiter in _PROP_A_DELIMS:
            for consumer in _PROP_A_CONSUMERS:
                for opener in _PROP_A_OPENERS:
                    for position in _PROP_A_POSITIONS:
                        push_body = _prop_a_body(opener, _PROP_A_DEV_PUSH, position)
                        cases.append(
                            (
                                "push",
                                _heredoc(consumer, operator, delimiter, push_body, ""),
                            )
                        )
                        ro_body = _prop_a_body(opener, _PROP_A_READ_ONLY, position)
                        cases.append(
                            (
                                "readonly",
                                _heredoc(consumer, operator, delimiter, ro_body, ""),
                            )
                        )
    return cases


# ---- boundary-relocation shapes (finding 1, BLOCKER; final whole-branch review) ----
#
# Everything above only ever puts the push in COMMAND POSITION inside a body with no stray `)`
# and no outer wrap, so it could never reach design #3's fail-open (closed in `4563a93`): an
# opener that pairs with a closer OUTSIDE the body. Measured by the reviewer against the pre-fix
# tree: 0 of the 855 hand-written `CASES` produce a masking insertion at all, and 0 of the 486
# `must_find_push` cases reach the closer pass -- this whole shape class was excluded BY
# CONSTRUCTION. These cases add the two dimensions the review named: a body carrying an unmatched
# `)` ALONGSIDE a `$(` opener (so the opener pairs with a closer OUTSIDE the body), and the
# heredoc nested inside an outer `$( … )` or backtick with the token placed AFTER the terminator
# but still inside the outer wrap. Mirrors `test_guard_internals.py`'s delta-debugged reproducer
# (`"$(<<EOF\n)$(\nEOF\ngit push origin dev)"`) and its naturalistic `msg="$(cat <<EOF …)"` sibling.
#
# The inner opener is deliberately always `$(`, never a backtick: `$(` uses paren-depth counting
# (`_scan_to_unbalanced_paren`), which a stray `)` can prematurely satisfy from OUTSIDE the body --
# the exact relocation mechanism. A backtick body-opener was tried and measured to reach a
# DIFFERENT, narrower gap (a double-quoted outer `$( … )` around a body carrying a stray backtick
# can still lose the push) -- that is a distinct, unfixed residual, reported separately rather than
# folded in here; a body-opener dimension that reliably reproduces it is a finding for the
# tokenizer's owners, not a shape this corpus should assert clean over.
_PROP_A_OUTER_WRAPS = (("$(", ")"), ("`", "`"))
_PROP_A_OUTER_QUOTING = (
    False,
    True,
)  # bare, and double-quoted -- both real reproducers are quoted


def _prop_a_boundary_cases():
    """`(kind, command)` pairs shaped like the BLOCKER: a stray `)` alongside a `$(` opener inside
    a heredoc body, the heredoc nested in an outer substitution, and the token placed AFTER the
    terminator but still inside the outer wrap -- so a per-body closer decision (design #3) orphans
    the outer wrap's own closer and the token silently falls out of every walked context.

    Verified directly against the real `publication-push-guard.py` (never assumed): all 48 `push`
    cases block for the dev-target reason and all 48 `readonly` cases allow, with no parse-refusal
    text either way -- see the finding-1 verification script referenced in the branch's session
    notes. Falsifiability confirmed separately: patching `mask_heredoc_quotes` to always prefer the
    closed text (reinstating design #3) turns the `push` half of this corpus RED.
    """
    cases = []
    for operator in _PROP_A_OPERATORS:
        for delimiter in _PROP_A_DELIMS:
            for consumer in _PROP_A_CONSUMERS:
                for outer_open, outer_close in _PROP_A_OUTER_WRAPS:
                    for quoted in _PROP_A_OUTER_QUOTING:
                        for inner in _PROP_A_OPENERS:
                            _boundary_case(
                                cases,
                                consumer,
                                operator,
                                delimiter,
                                outer_open,
                                outer_close,
                                quoted,
                                inner,
                            )
    return cases


def _boundary_case(
    cases, consumer, operator, delimiter, outer_open, outer_close, quoted, inner
):
    """One boundary-relocation shape: a stray `)` then an opener, partnered OUTSIDE the body.

    `inner` is varied across BOTH opener kinds deliberately. An earlier version hard-coded `$(`,
    and that single fixed choice is why the corpus could not see the fail-open that survived the
    fourth repair attempt -- it needed a BACKTICK inner opener specifically. A corpus that pins one
    dimension answers only about the slice it pinned, which is the failure this module's docstring
    exists to warn about.
    """
    heredoc = _heredoc(consumer, operator, delimiter, ")" + inner, "")
    for kind, token in (("push", _PROP_A_DEV_PUSH), ("readonly", _PROP_A_READ_ONLY)):
        core = "{}{}\n{}{}".format(outer_open, heredoc, token, outer_close)
        cases.append((kind, ('"' + core + '"') if quoted else core))


PROP_A_CASES = _prop_a_cases() + _prop_a_boundary_cases()

_GUARD_PATH = Path(__file__).resolve().parent.parent / "publication-push-guard.py"


def _prop_a_git(repo, *args):
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "-c",
            "user.email=t@t.invalid",
            "-c",
            "user.name=t",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _prop_a_build_repo():
    """A throwaway repo adopted into the dev/main publication model, with a `dev` branch --
    mirrors `build_repo()` in `test_publication_push_guard.sh`. Torn down at interpreter exit
    rather than left behind (see CLAUDE.md's "what a run LEAVES BEHIND" hazard)."""
    import atexit
    import shutil

    root = Path(tempfile.mkdtemp(prefix="git_command_props_prop_a_"))
    atexit.register(shutil.rmtree, str(root), ignore_errors=True)
    _prop_a_git(root, "init", "-q")
    (root / "README.md").write_text("hello\n")
    (root / ".publication.toml").write_text('production = "dev"\n')
    stub = "#!/bin/sh\nexit 0\n"
    for hooks_dir in (root / "git-hooks", root / ".git" / "hooks"):
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook = hooks_dir / "pre-push"
        hook.write_text(stub)
        hook.chmod(0o755)
    _prop_a_git(root, "add", "-A")
    _prop_a_git(root, "commit", "-q", "-m", "init")
    _prop_a_git(root, "branch", "-q", "dev")
    return root


_PROP_A_REPO = None


def _adopted_repo():
    """Built once, lazily, and reused across both Property A tests -- one guard invocation is
    already several subprocesses (python3 + git rev-parse + branch lookups), so paying repo
    construction per case would multiply that needlessly."""
    global _PROP_A_REPO
    if _PROP_A_REPO is None:
        _PROP_A_REPO = _prop_a_build_repo()
    return _PROP_A_REPO


def _run_guard(command, repo):
    """Feed `command`/`repo` to the real `publication-push-guard.py` as its PreToolUse hook JSON
    payload on stdin -- argv is ignored by the guard's own contract, see `test_guard_corpus.py`'s
    `_run_guard`. Returns `(rc, combined stdout+stderr)`; the guard's refusal reasons print to
    stderr, so callers need both."""
    payload = json.dumps({"tool_input": {"command": command}, "cwd": str(repo)})
    env = dict(os.environ)
    # Hermeticity: never let a guard-internal-error branch append to the operator's real log --
    # see test_guard_corpus.py's module docstring for the measured cost of skipping this.
    env["PUBLICATION_PUSH_GUARD_LOG"] = str(repo.parent / "guard-internal-errors.log")
    proc = subprocess.run(
        [sys.executable, str(_GUARD_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_no_boundary_relocation_shape_ever_silently_allows_a_dev_push():
    """The corpus's live assertion, and the reason it outlived the change it was built for.

    Every case here puts an opener inside a heredoc body whose partner sits OUTSIDE it, with a
    token after the terminator but inside an outer wrap. Four separate repair passes were written
    for this class and all four shipped a fail-open, each by inserting characters that relocated a
    context boundary and hid the invocation. The passes are gone; the shapes remain, because the
    hazard is a property of the tokenizer's structure rather than of any repair.

    The claim asserted is deliberately WEAK and one-directional: a `push` case may be REFUSED
    (fail-closed, which is what the current tokenizer does) or BLOCKED on policy -- it may never be
    silently ALLOWED. Anything stronger would encode one particular repair's behaviour and go
    stale the moment the next attempt lands, which is exactly how the assertion this replaces
    became a claim about code that no longer existed.

    The read-only cases carry the complement, so the property cannot pass by refusing everything:
    a corpus where every row is refused proves nothing about a guard, since refusing is free.
    """
    repo = _adopted_repo()
    pushes = [cmd for kind, cmd in PROP_A_CASES if kind == "push"]
    readonly = [cmd for kind, cmd in PROP_A_CASES if kind == "readonly"]
    assert pushes and readonly, "the corpus must carry BOTH kinds or it is one-sided"

    allowed_pushes = []
    for command in pushes:
        rc, output = _run_guard(command, repo)
        if rc == 0 and "could not parse" not in output:
            allowed_pushes.append(command)
    assert not allowed_pushes, (
        "a boundary-relocation shape carrying a real dev push was silently ALLOWED -- this is the "
        "fail-open that four repair designs each reintroduced: {!r}".format(
            allowed_pushes[:3]
        )
    )

    # The complement: not every row may be refused, or the assertion above is vacuous.
    verdicts = {_run_guard(command, repo)[0] for command in readonly}
    assert verdicts != {2}, (
        "every read-only case was refused too, so the push assertion is measuring the refusal "
        "rate rather than the guard -- the corpus has stopped discriminating"
    )


# ---------- eval transparency (fix/eval-wrapper-bypass) ----------
#
# A TOKENIZER-CONSISTENCY property over words eval re-joins to themselves: eval concatenates its
# words with single spaces and parses them again, and a non-empty word drawn from
# `_REPARSE_STABLE`'s alphabet comes back unchanged. Over that space, putting an eval-family
# wrapper in front of the git command must leave every invocation record the walk reports
# IDENTICAL. NOT a claim of bash fidelity beyond the alphabet -- `~`, globs, `$`, quotes and an
# empty word all change meaning under a second parse (measured).
#
# The wrapper goes in front of GIT, never in front of the leading `cd`: a cd behind a wrapper makes
# the cwd unresolvable by design, so a prefixed cd would differ on purpose.

_REPARSE_STABLE = re.compile(r"^[A-Za-z0-9_./=:+-]+$")
_T_OPERATORS = frozenset({"&&", ";", "|"})
_T_LEAD = ["", "cd /y && "]
_T_ENV = ["", "FOO=1 ", "GIT_CONFIG_COUNT=1 ", "A=1 B=2 ", "X+=y "]
_T_OPTS = ["", "-C /x ", "-c a.b=c ", "--no-pager ", "-C /x -c a.b=c "]
_T_SUBS = [
    "push origin dev",
    "push --no-verify origin main",
    "status",
    "commit -m msg",
    "config --global core.hooksPath /dev/null",
    "log --oneline -1",
]
_T_TAILS = ["", " && git status", " ; cd /z && git push origin dev", " | cat"]
_T_PREFIXES = [
    "eval ",
    "builtin eval ",
    "eval -- ",
    "builtin -- eval -- ",
    "eval eval ",
    "command eval ",
    "time eval ",
]


def _transparency_cases():
    for lead in _T_LEAD:
        for env in _T_ENV:
            for opts in _T_OPTS:
                for sub in _T_SUBS:
                    for tail in _T_TAILS:
                        yield lead, f"{env}git {opts}{sub}{tail}"


def _records(command):
    return [
        (
            i.effective_dir,
            i.cdir,
            i.subcommand,
            i.arg_tokens,
            i.tokens.env,
            i.tokens.opts,
        )
        for i in git_command.iter_git_invocations_detailed(command, "/repo")
    ]


def test_the_transparency_space_is_large_enough_to_be_a_property():
    assert sum(1 for _ in _transparency_cases()) > 1000


def test_every_generated_word_is_reparse_stable():
    """The property's premise, asserted rather than assumed."""
    for lead, rest in _transparency_cases():
        for word in (lead + rest).split(" "):
            assert word in _T_OPERATORS or _REPARSE_STABLE.match(word), (
                lead + rest,
                word,
            )


def test_prefixing_an_eval_family_wrapper_changes_no_invocation():
    failures = []
    for lead, rest in _transparency_cases():
        base = _records(lead + rest)
        assert base, (
            f"precondition: the base command must yield an invocation: {lead + rest!r}"
        )
        for prefix in _T_PREFIXES:
            got = _records(lead + prefix + rest)
            if got != base:
                failures.append(f"{lead + prefix + rest!r}: {got!r} != {base!r}")
    assert not failures, f"{len(failures)} case(s), first 5:\n" + "\n".join(
        failures[:5]
    )
