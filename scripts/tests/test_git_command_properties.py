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
"""

import random
import sys
from collections import namedtuple
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


def test_masking_only_ever_inserts_backslashes():
    """Text-preservation. Dropping any other character silently removes a command from the walk —
    the pass may add escapes and nothing else."""
    for case in CASES:
        masked = git_command.mask_heredoc_quotes(case.command)
        assert masked.replace("\\", "") == case.command.replace("\\", ""), case.command


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
    fail-open the hand-written suite missed."""
    for text in _fuzz_inputs():
        masked = git_command.mask_heredoc_quotes(text)
        assert masked.replace("\\", "") == text.replace("\\", ""), text


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
