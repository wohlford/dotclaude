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

import hashlib
import importlib.util
import inspect
import itertools
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
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

_HEREDOC_OPERATOR = re.compile(
    r"<<(-?)[ \t]*((?:'[^']*'|\"[^\"]*\"|\\\S|[^\s;&|<>()])+)"
)


def _only_quoted_plain_heredocs(command):
    """True when every heredoc operator in `command` is `<<` (never `<<-`) with a QUOTED or escaped
    delimiter -- the population the insert-only and byte-identity guarantees still cover.

    Since 2026-09-18 the masking pass TRANSFORMS two other shapes, because bash does: `<<-` strips
    leading tabs from every body line, and an UNQUOTED body gets bash's own backslash and
    continuation pass before the consumer reads it. Those are held to the bash differential
    (`test_git_command_bash_differential.py`) and to the removal-only and opener-parity properties
    below instead. A body whose last line ends in a continuation is dropped for any delimiter, but
    no body in this corpus ends that way.
    """
    ops = _HEREDOC_OPERATOR.findall(command)
    return bool(ops) and all(
        dash == "" and any(c in word for c in "'\"\\") for dash, word in ops
    )


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

    Scoped since 2026-09-18 to `_only_quoted_plain_heredocs` cases -- see that helper for why the
    other shapes are now transformed, and which properties hold them instead.
    """
    allowed_insertions = set("\\")
    checked = 0
    for case in CASES:
        if not _only_quoted_plain_heredocs(case.command):
            continue
        checked += 1
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
    assert checked > 150, checked


def test_masking_is_idempotent():
    """`_prepare` re-runs the pass on every extracted context, so a heredoc inside `$( … )` is
    masked more than once; growth across runs would corrupt the body."""
    for case in CASES:
        once = git_command.mask_heredoc_quotes(case.command)
        assert git_command.mask_heredoc_quotes(once) == once, case.command


def test_a_balanced_body_comes_out_byte_identical():
    """The blast-radius guarantee, and the reason the fix is narrow enough to be safe: if every
    body already balances, nothing is touched, so no command that parses today can begin parsing
    differently tomorrow. Scoped since 2026-09-18 to `_only_quoted_plain_heredocs` cases."""
    checked = 0
    for case in CASES:
        if not _only_quoted_plain_heredocs(case.command):
            continue
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
# Unchanged behaviour where NOTHING is ambiguous, against the vendored pre-change oracle
# =====================================================================================
#
# The 2026-09-19 reading-variant change enumerates readings of an ambiguous heredoc. A command with
# no ambiguous heredoc ANYWHERE has exactly one assignment, so it must come out byte-identical to
# the build before the change — same invocations, same order. That is the only claim strong enough
# to be worth stating: an "over-read is acceptable" rule cannot distinguish a deliberate second
# reading from a regression that invented one.
#
# WHY A VENDORED FILE AND NOT `git show <sha>:<path>`. The pre-change tree is reachable only from
# this branch, and the pre-publish history fold rewrites those commits — after which the command
# fails in any fresh clone and this property silently stops being checkable. It is loaded the way
# `test_guard_corpus.py:_load_baseline_gitcmd` loads its own baseline: under its OWN module name,
# never `git_command`, which this module already occupies in `sys.modules`.
# `scripts/tests/fixtures/prechange/` is a DIFFERENT frozen baseline (782039d) and is not touched.
#
# WHY THE AMBIGUITY COUNT IS GLOBAL. Scoping it to the top-level context is wrong and measurably
# so — see `test_the_ambiguity_filter_must_be_scoped_globally_not_to_the_top_level` below, which is
# the control that moves.

_PRE_VARIANTS_ORACLE = (
    Path(__file__).resolve().parent / "fixtures" / "pre-variants" / "git_command.py"
)

# sha256 of the tokenizer as it stood BEFORE the reading-variant change, computed when the
# file was vendored. Deliberately not expressed as a commit SHA: the branch that carried it
# is re-derived into bricks and deleted by the adopted finish, so such a SHA resolves for
# nobody afterwards -- and this very file explains that the pre-publish fold rewrites those
# commits. The vendored copy below is tracked, so ITS path in history is the provenance.
# Asserted on every load: a silently edited oracle turns every comparison below into the code
# comparing itself with itself, which passes.
_PRE_VARIANTS_RELPATH = "scripts/tests/fixtures/pre-variants/git_command.py"
"""The oracle's path as git spells it -- what `git show <commit>:<path>` needs."""

_PRE_VARIANTS_SHA256 = (
    "2d0e92e38973b7eabd3bb43d657cdab0d0a49d24adadfc1660ce44d7697ef1b9"
)

_PRE_VARIANTS_MODULE = None


def _pre_variants_gitcmd():
    """Import the vendored pre-change tokenizer under its own module name, digest first."""
    global _PRE_VARIANTS_MODULE
    if _PRE_VARIANTS_MODULE is None:
        digest = hashlib.sha256(_PRE_VARIANTS_ORACLE.read_bytes()).hexdigest()
        assert digest == _PRE_VARIANTS_SHA256, (
            "the vendored pre-change oracle has drifted: {0} has sha256 {1}, expected {2}. "
            "It is the reference every comparison in this section is made against; restore it "
            "from this fixture's own history (`git log --oneline -- {3}`, then "
            "`git show <commit>:{3}`) rather than updating this "
            # {3} is REPO-RELATIVE on purpose: `git show <commit>:<path>` rejects an absolute
            # path, so interpolating {0} here would print a command that cannot run -- in the one
            # message a reader meets at the moment they can least afford to debug it.
            "constant.".format(
                _PRE_VARIANTS_ORACLE,
                digest,
                _PRE_VARIANTS_SHA256,
                _PRE_VARIANTS_RELPATH,
            )
        )
        spec = importlib.util.spec_from_file_location(
            "pre_variants_git_command", _PRE_VARIANTS_ORACLE
        )
        assert spec is not None and spec.loader is not None, _PRE_VARIANTS_ORACLE
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PRE_VARIANTS_MODULE = module
    return _PRE_VARIANTS_MODULE


# A raise is a VERDICT here, not an error: the two builds must agree about it too, so it is
# recorded as a value rather than allowed to escape.
_RAISED = ("<ValueError>",)


def _invocation_record(module, command):
    """Every field the union's dedup identity reads, in order — so "same invocations, same order"
    is asserted over the whole tuple and not over subcommands alone."""
    try:
        return [
            (
                inv.effective_dir,
                inv.cdir,
                inv.subcommand,
                tuple(inv.arg_tokens),
                tuple(inv.tokens.env),
                tuple(inv.tokens.opts),
            )
            for inv in module.iter_git_invocations_detailed(command, "/repo")
        ]
    except ValueError:
        return _RAISED


def _global_ambiguity_count(command, depth=0):
    """Ambiguous heredocs in this context AND in every context it contains.

    Mirrors the walk: each context is masked (total, never raises) and its children come from the
    PRIMARY preparation — which is sound as a population filter, because a context with no
    ambiguity has exactly one assignment and therefore exactly one set of children.
    """
    _masked, count = git_command._mask_with_count(command, depth)
    total = count
    if depth > git_command.MAX_CONTEXT_DEPTH:
        return total
    try:
        _outer, nested, _count = git_command._prepare(command, depth, None)
    except ValueError:
        return total
    for child in nested:
        total += _global_ambiguity_count(child.text, child.depth)
    return total


# Commands that ARE ambiguous, so the filter above has something to exclude. Without them the
# filter is decorative: measured, not one of the 4,855 generated commands below contains an
# ambiguous heredoc, so a corpus-only population would pass with the filter deleted.
_AMBIGUITY_WITNESSES = (
    # top level: old build DROPPED, new build records drop then join
    "bash <<'EOF'\ngit status" + "\\" + "\nEOF\n",
    # inside `$( )`: the OLD build already recorded both readings here (by emitting the body
    # twice), so this one is excluded by the filter without differing — kept because the filter
    # must exclude it on the AMBIGUITY, never on whether the answer happens to have changed
    "x=$(bash <<'EOF'\ngit status" + "\\" + "\nEOF\n)",
    # inside backticks: old build JOINED only, new build records both
    "x=`bash <<'EOF'\ngit status" + "\\" + "\nEOF\n`",
    # backtick halving: an EVEN run outside becomes ODD inside, so only a GLOBAL count sees it
    "x=`bash <<'EOF'\ngit status" + "\\" * 2 + "\nEOF\n`",
)


def test_a_command_with_no_ambiguous_heredoc_reads_exactly_as_the_pre_change_oracle():
    """The population is every generated command whose ambiguity count, summed over ALL contexts,
    is zero. For those the new build must return the pre-change build's invocations, in order,
    including agreeing about a raise."""
    oracle = _pre_variants_gitcmd()
    commands = (
        [case.command for case in CASES]
        + list(_fuzz_inputs())
        + list(_AMBIGUITY_WITNESSES)
    )
    unchanged, excluded = [], []
    for command in commands:
        (excluded if _global_ambiguity_count(command) else unchanged).append(command)

    # FLOOR, and a non-zero denominator: a filter that excluded everything would leave the loop
    # below asserting nothing at all, and report success.
    assert len(unchanged) >= 4800, len(unchanged)
    # And the filter must really be filtering. The witnesses are appended last and are the only
    # ambiguous commands here (measured over the whole corpus), so this is an exact statement of
    # what the filter excluded, not a lower bound on it.
    assert excluded == list(_AMBIGUITY_WITNESSES), excluded

    for command in unchanged:
        assert _invocation_record(oracle, command) == _invocation_record(
            git_command, command
        ), (
            "reading-variant change altered a command with no ambiguous heredoc: "
            "{!r}".format(command)
        )


def test_the_ambiguity_filter_must_be_scoped_globally_not_to_the_top_level():
    """CONTROL that MOVES for the property above: a command the TOP-LEVEL count calls
    unambiguous, whose reading nonetheless changed.

    Backtick text loses one backslash of each pair before it runs, so a body line ending in an
    EVEN run at the top level ends in an ODD one inside the backticks — ambiguous in the child
    context and invisible to a top-level-scoped count. Scoping the filter that way would admit
    this command into the unchanged population, where the final assertion would fail on it.
    """
    command = "x=`bash <<'EOF'\ngit status" + "\\" * 2 + "\nEOF\n`"
    assert git_command._mask_with_count(command, 0)[1] == 0
    assert _global_ambiguity_count(command) == 1
    assert _invocation_record(_pre_variants_gitcmd(), command) != _invocation_record(
        git_command, command
    )


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


# ---------- UNQUOTED and `<<-` bodies: transformed as bash transforms them (2026-09-18) ----------
#
# The insert-only guarantee above existed to catch repairs that relocated a command boundary by
# INSERTING text (four such repairs failed open; see the module docstring). Unquoted and `<<-` bodies
# are now transformed the way bash transforms them, which DELETES characters, so they need their own
# bash-free guarantees: every change is a deletion of a backslash, newline or tab (or the
# neutraliser's inserted backslash), and no substitution opener is ever made to look escaped.

_UNQ_PIECES = (
    "a",
    " ",
    "\\",
    "\\",
    "$(",
    ")",
    "`",
    "'",
    '"',
    "\t",
    "$X",
    "echo ",
    "git ",
)


def _unquoted_bodies(rounds=3000):
    rng = random.Random(SEED + 17)
    for _ in range(rounds):
        lines = []
        for _ in range(rng.randint(1, 4)):
            lines.append(
                "".join(rng.choice(_UNQ_PIECES) for _ in range(rng.randint(0, 8)))
            )
        yield rng.choice(("<<", "<<-")), "\n".join(lines)


def _aligns_removal_only(raw, masked):
    """True when `masked` is reachable from `raw` by deleting only backslash, LF and TAB IN PLACE,
    and inserting a backslash only IMMEDIATELY BEFORE a quote (the neutraliser's one move).

    An exact alignment, not a greedy two-pointer: a first version accepted a deletion and an
    insertion as independent moves with no positional tie, so `a\\` -> `\\a` -- a backslash
    RELOCATED across content, the boundary-moving class this property exists to catch -- passed.
    """
    from functools import lru_cache

    n, m = len(raw), len(masked)

    @lru_cache(maxsize=None)
    def ok(i, j):
        if i == n and j == m:
            return True
        if i < n and j < m and raw[i] == masked[j] and ok(i + 1, j + 1):
            return True
        if i < n and raw[i] in "\\\n\t" and ok(i + 1, j):
            return True
        return (
            j + 1 < m and masked[j] == "\\" and masked[j + 1] in "'\"" and ok(i, j + 1)
        )

    return ok(0, 0)


def test_the_removal_only_alignment_rejects_a_relocated_backslash():
    # The control that proves the checker above can fail: relocation and arbitrary insertion are
    # refused, in-place deletion and the neutraliser's quote escape are accepted.
    assert not _aligns_removal_only("a\\", "\\a")
    assert not _aligns_removal_only("a\\b", "\\ab")
    assert not _aligns_removal_only("ab", "a\\b")
    assert _aligns_removal_only("a\\\nb", "ab")
    assert _aligns_removal_only("it's", "it\\'s")


def _live_openers(text):
    """Count `$(` and backticks preceded by an EVEN backslash run -- the openers a scanner reads as
    live rather than escaped."""
    count = 0
    for m in re.finditer(r"\$\(|`", text):
        k = m.start()
        run = 0
        while k - run - 1 >= 0 and text[k - run - 1] == "\\":
            run += 1
        count += run % 2 == 0
    return count


def test_an_unquoted_body_is_only_ever_reduced():
    checked = 0
    for op, body in _unquoted_bodies():
        command = f"cat {op}EOF\n{body}\nEOF\n"
        try:
            masked = git_command.mask_heredoc_quotes(command)
        except ValueError:
            continue  # a refusal hides nothing: every consumer fails closed on it
        checked += 1
        assert _aligns_removal_only(command, masked), (command, masked)
        # The terminator line is operator text, copied verbatim -- whenever bash would find it
        # there (no body line IS the terminator, and no continuation swallows it).
        lines = body.split("\n")
        found_early = any(
            (line.lstrip("\t") if op == "<<-" else line) == "EOF" for line in lines
        )
        swallowed = (len(lines[-1]) - len(lines[-1].rstrip("\\"))) % 2 == 1
        if not found_early and not swallowed:
            assert masked.endswith("\nEOF\n"), (command, masked)
    assert checked > 2000, checked


def test_masking_never_makes_a_live_opener_look_escaped():
    # Measured before this property existed: collapsing `\\` directly before `$(` to one backslash
    # made the scanner read `\$(` as escaped, and a substitution the outer shell expands vanished
    # from the walk. Masking may make an escaped opener live (the consumer re-reads it) but never
    # the reverse.
    checked = 0
    for op, body in _unquoted_bodies():
        command = f"cat {op}EOF\n{body}\nEOF\n"
        try:
            masked = git_command.mask_heredoc_quotes(command)
        except ValueError:
            continue
        checked += 1
        assert _live_openers(masked) >= _live_openers(command), (command, masked)
    assert checked > 2000, checked


# =====================================================================================
# The parse cap's cost -- TIMING-SENSITIVE
# =====================================================================================


def _adversarial_reading_ladder():
    """The construction `MAX_TOTAL_PARSES` was calibrated against, rebuilt here rather than pasted.

    `MAX_CONTEXT_DEPTH` nested backtick levels, each carrying one quoted-delimiter heredoc whose
    body ends in a run of `2**level` backslashes. Backtick text loses one backslash of each pair
    before it runs, so after `level` rounds of halving that run is ODD at its own level and EVEN
    at every level above -- fresh ambiguity per level, which is what a per-CONTEXT budget cannot
    bound and what this one global cap exists for. The innermost body is padded out to just under
    `MAX_COMMAND_LENGTH`, because the cap's cost is (parses x bytes scanned per parse) and the
    second factor is maximal there.
    """
    backslash = "\\"

    def tick(level):
        # Nesting backticks REQUIRES escaping, and the escape count doubles each level.
        return backslash * (2**level - 1) + "`"

    def heredoc(tag, run, pad=""):
        return f"bash <<'{tag}'\ngit status{pad}{backslash * run}\n{tag}\n"

    def build(pad_chars):
        depth = git_command.MAX_CONTEXT_DEPTH
        pad = ("\n" + "x" * pad_chars) if pad_chars else ""
        text = heredoc(f"E{depth - 1}", 2 ** (depth - 1), pad)
        for level in range(depth - 2, -1, -1):
            text = heredoc(f"E{level}", 2**level) + tick(level) + text + tick(level)
        return text

    low, high, best = 0, git_command.MAX_COMMAND_LENGTH, build(0)
    while low <= high:
        mid = (low + high) // 2
        candidate = build(mid)
        if len(candidate) <= git_command.MAX_COMMAND_LENGTH:
            best, low = candidate, mid + 1
        else:
            high = mid - 1
    return best


# 1.7x over a MEASURED baseline, and deliberately NOT derived from any hook registration. Five
# consecutive trials of the pair below measured 17.64-18.07 s (a 2.4% spread) at
# `MAX_TOTAL_PARSES = 128`, and the guard process itself 17.89 s; 30.0 leaves 1.7x for an ordinary
# busy machine. An earlier version of this comment called the number "half of the 60 s
# `settings.json` registers for `publication-push-guard.py`" -- true arithmetic, wrong claim, and
# the same error `MAX_TOTAL_PARSES`'s own docstring carried: that guard overruns 60 s on shapes
# this cap does not govern (172 s on shipped `dev` for a flat 65,529-char command whose tokenizer
# costs 0.15 s), so fitting inside the registration was never this bound's to promise. What it
# does promise is a REGRESSION detector on the ladder: it catches the cap raised back to 256
# (36.65 s in that guard) and roughly a 1.7x rise in per-parse cost.
ADVERSARIAL_LADDER_BUDGET_SECONDS = 30.0


def test_the_adversarial_reading_ladder_stays_inside_its_wall_clock_budget():
    """TIMING-SENSITIVE: read a failure here as possibly the MACHINE before the code.

    A hook that outlives its registered timeout is killed, is never told it was killed, and the
    command RUNS -- so on this walk's own safety asymmetry an unbounded enumeration is a bypass,
    not a slowdown. That makes the wall clock a security property and not a tidiness one, which is
    why it is asserted rather than left to review.

    The wall-clock half is the part a loaded machine can move, so it is paired with two halves
    that it cannot: the cap must actually BIND on this input (uncapped it wants 510 parses), and
    exhausting it must yield the in-band marker rather than a raise. A green row whose parse count
    had drifted below the cap would be measuring an input that no longer reaches the enumeration.
    """
    command = _adversarial_reading_ladder()
    assert len(command) > git_command.MAX_COMMAND_LENGTH * 0.9, len(command)

    start = time.perf_counter()
    invocations = git_command.iter_git_invocations_detailed(command, None)
    streams = git_command.iter_context_token_streams(command)
    elapsed = time.perf_counter() - start

    # Deterministic: the cap binds, and both primitives degrade in band rather than raising.
    assert any(
        git_command.subcommand_is_ambiguous_reading(inv.subcommand)
        for inv in invocations
    ), [inv.subcommand for inv in invocations]
    assert any(
        any(git_command.subcommand_is_ambiguous_reading(token) for token in stream)
        for stream in streams
    ), len(streams)

    assert elapsed < ADVERSARIAL_LADDER_BUDGET_SECONDS, (
        "TIMING-SENSITIVE row: the two enumerating primitives took {:.2f}s over a {}-char "
        "adversarial ladder, against a {:.0f}s budget (half the 60s timeout settings.json "
        "registers for publication-push-guard.py, which calls both). Re-run on an idle machine "
        "before treating this as a code regression; if it reproduces idle, the cap or the "
        "per-parse cost moved -- MAX_TOTAL_PARSES is {} and was calibrated at 128.".format(
            elapsed,
            len(command),
            ADVERSARIAL_LADDER_BUDGET_SECONDS,
            git_command.MAX_TOTAL_PARSES,
        )
    )


# The parse cap can only bound work that happens AFTER a parse is spent. The first draft of
# `_reading_assignments` returned a LIST, so `2**k` assignments were built before the loop that
# spends the budget ever ran -- and its `assignment not in ordered` membership scan made that
# `4**k`. Measured on the draft: a 444-BYTE command (k=16) spent 38.17 s inside that function
# having parsed nothing, against the 60 s the publication guard's registration allows for two
# heavy tokenizer calls. A PreToolUse hook killed at its timeout is SILENT and the command RUNS,
# so this was a denial-of-guard bypass reachable from a tiny input. After the fix: 0.113 s.
FLAT_AMBIGUITY_BUDGET_SECONDS = 5.0


_BS = chr(92)  # a literal backslash, kept out of this file's f-strings


def _flat_ambiguous_command(count: int) -> str:
    """`count` sibling heredocs in ONE context, each ambiguous. `k` is per-context and bounded by
    input LENGTH, never by `MAX_CONTEXT_DEPTH` -- which is why a 444-byte command can carry 16."""
    return "".join(f"bash <<'E{i}'\ngit status{_BS}\nE{i}\n" for i in range(count))


def test_the_reading_enumeration_is_lazy():
    """DETERMINISTIC half, and the one that fails on the draft for a reason no machine load can
    explain: a materialised list cannot be consumed a prefix at a time. Taking a handful of
    assignments for a count whose `2**k` is astronomically large must return immediately."""
    assignments = git_command._reading_assignments(2000)
    assert inspect.isgenerator(assignments), (
        "_reading_assignments must yield lazily: at MAX_COMMAND_LENGTH `k` is about 2053, so a "
        "materialised list puts 2**k in front of the parse cap, where no cap value reaches it"
    )
    prefix = list(itertools.islice(assignments, 5))
    assert prefix[0] == {}, "the primary (all-drop) reading must come first"
    assert prefix[1] == {i: True for i in range(2000)}, "all-join must come second"
    assert all(a != prefix[0] and a != prefix[1] for a in prefix[2:]), (
        "the two extremes are yielded up front and must not be repeated by the product"
    )


def test_a_small_command_with_many_ambiguous_heredocs_stays_cheap():
    """TIMING-SENSITIVE: wall clock is the half a loaded machine can move. A failure here may be
    the machine -- re-run idle before blaming the code -- but the companion row above is
    deterministic, so the two together cannot both be explained away."""
    command = _flat_ambiguous_command(16)
    assert len(command) < 500, "the point of this row is that the input is TINY"
    start = time.perf_counter()
    invocations = git_command.iter_git_invocations_detailed(command, "/repo")
    elapsed = time.perf_counter() - start
    assert invocations, "the walk must still read the command, not merely return fast"
    assert elapsed < FLAT_AMBIGUITY_BUDGET_SECONDS, (
        "a %d-byte command with 16 ambiguous heredocs took %.2f s (budget %.1f s, cap %d). The "
        "draft that returned a list took 38.17 s here; a hook killed at its timeout is silent and "
        "the command runs. TIMING-SENSITIVE: re-run on an idle machine before blaming the code."
        % (
            len(command),
            elapsed,
            FLAT_AMBIGUITY_BUDGET_SECONDS,
            git_command.MAX_TOTAL_PARSES,
        )
    )


# Generated AMBIGUOUS commands: heredocs whose delimiters carry shell metacharacters, whose bodies
# end in odd backslash runs, nested in the contexts that make a reading unparseable. This is the
# population `test_both_primitives_agree_on_the_same_string` names and cannot reach -- its fixture
# is three hand-written commands with no heredoc -- and the population the bash differential cannot
# reach either, because `_tokenizer_invocations` calls only `iter_git_invocations_detailed`. A
# review found a BLOCKER here: `_collect` guarded only `_prepare`, so a variant whose CHILD was
# unparseable raised out of the whole primitive, and push-guard and the publication guard went from
# BLOCK on dev to ALLOW on a push both bashes run.
_AMBIGUOUS_DELIMITERS = ["EOF", '"', "'", "(x", 'a"b', "x)y", "$v", "`t"]
_AMBIGUOUS_WRAPPERS = [
    ("", ""),
    ("x=$(", ")"),
    ("x=`", "`"),
    ("x=$(y=`", "`)"),
    ("x=`y=$(", ")`"),
]


def _ambiguous_commands():
    """Every (delimiter, wrapper, backslash-run) combination, as one command each."""
    backslash = chr(92)
    for delimiter in _AMBIGUOUS_DELIMITERS:
        quoted = "'%s'" % delimiter if "'" not in delimiter else '"%s"' % delimiter
        for open_wrap, close_wrap in _AMBIGUOUS_WRAPPERS:
            for run in (0, 1, 2, 3):
                body = "git status origin dev" + backslash * run
                yield "%sbash <<%s\n%s\n%s\n%s" % (
                    open_wrap,
                    quoted,
                    body,
                    delimiter,
                    close_wrap,
                )


def test_the_streams_primitive_never_raises_where_the_walk_reads():
    """Wherever the walk READS a command, the streams primitive must not RAISE on it.

    Scope, stated exactly: this row asserts RAISE PARITY and nothing else. It does not compare
    what the two primitives found -- an earlier version of this docstring said they "must agree
    about what a context CONTAINS", which the assertions below have never checked.

    Two guards depend on the parity it does assert: push-guard consumes streams ONLY, and the
    publication guard pairs a streams pass with an invocation pass. A streams-only raise is
    therefore not a tidy asymmetry -- at push-guard's ValueError arm (which returns 0 without a
    visible git word) and at the timing guard's fail-open it is a LOST invocation, the regression
    class this whole branch exists to prevent.
    """
    divergences = []
    walked = 0
    streams_read = 0
    tokens_seen = 0
    for command in _ambiguous_commands():
        try:
            git_command.iter_git_invocations_detailed(command, "/repo")
        except ValueError:
            continue  # the walk could not read it either; parity holds trivially
        walked += 1
        try:
            streams = list(git_command.iter_context_token_streams(command))
        except ValueError as exc:
            divergences.append((command, str(exc)))
            continue
        streams_read += 1
        tokens_seen += sum(len(stream) for stream in streams)
    # Measured floor, not a guess: the corpus is 160 commands of which the walk reads 77, the same
    # 77 before and after the fix, and against the PRE-FIX tokenizer (8ca921e) this corpus reports
    # 13 divergences. A first draft asserted 100 and failed on a clean tree -- the denominator
    # guard doing its job, which is why it is here at all.
    assert walked >= 70, (
        "the corpus must actually exercise the walk, or this row passes by measuring nothing "
        "(walked=%d)" % walked
    )
    # A floor on the CHECKER's own completed reads, named separately from the walk's. `walked`
    # counts what the OTHER primitive did, so it stays healthy while this row reads nothing at
    # all: measured 2026-09-23, stubbing `iter_context_token_streams` to `return []` left this
    # row passing in 0.11 s with walked=77 and no divergences. One number cannot be a floor for
    # both parties. Measured: streams_read=77, tokens_seen=1231.
    assert streams_read >= 70 and tokens_seen >= 1000, (
        "the streams primitive itself produced almost nothing (streams_read=%d, tokens_seen=%d); "
        "a parity row that never read its own subject reports clean whatever the subject does"
        % (streams_read, tokens_seen)
    )
    assert not divergences, (
        "%d of %d commands: the walk read them and the streams primitive raised. Each one is a "
        "command push-guard and the publication guard stop judging. First: %r -> %s"
        % (len(divergences), walked, divergences[0][0], divergences[0][1])
    )


def test_the_primary_reading_survives_an_exhausted_budget(monkeypatch):
    """CAPABILITY PRESERVATION: no cap value may leave this module weaker than the pre-change one.

    The primary reading is exactly what the pre-change tokenizer walked, so charging it to the
    same budget as the variants this change ADDED means a large enough input silently withdraws
    a reading the old code always had. Measured 2026-09-22 before the fix, through the real
    `commit-subject-guard.py` with an 80-character subject (`.commit-conventions.toml` blocks at
    >= 80), sweeping the number of preceding ambiguous contexts:

        n:      0    1   60  120  126  127  128  200
        dev:    2    2    2    2    2    2    2    2
        branch: 2    2    2    2    2    0    0    0

    The threshold sits exactly at `MAX_TOTAL_PARSES`. The push gates were unaffected only because
    they read the in-band marker; a consumer that does not read it -- and `commit-subject-guard.py`
    is one -- just stops seeing the command.

    `MAX_TOTAL_PARSES = 0` is the sharpest form of the property: zero budget for variants, and the
    primary must still come through for every context.
    """
    monkeypatch.setattr(git_command, "MAX_TOTAL_PARSES", 0)
    command = (
        "".join("v%d=$(cat <<'A'\nnote\\\nA\n)\n" % i for i in range(5))
        + 'out=$(git commit -m "subject")'
    )

    streams = list(git_command.iter_context_token_streams(command))
    flat = [tok for s in streams for tok in s]
    assert "commit" in flat, (
        "the commit vanished at cap 0: the primary reading is being charged to the variant budget"
    )

    subs = [
        i.subcommand
        for i in git_command.iter_git_invocations_detailed(command, "/repo")
    ]
    assert "commit" in subs, "the walk lost the commit behind an exhausted budget"


def test_an_exhausted_budget_never_withdraws_a_push_from_either_primitive(monkeypatch):
    """The same property where it is load-bearing: a push must not disappear behind the cap.

    push-guard reads ONLY `iter_context_token_streams`, so a push present to the walk and absent
    from the streams is a fail-open at that guard however correct the walk is. This row asserts
    both primitives at a budget too small to enumerate anything.
    """
    monkeypatch.setattr(git_command, "MAX_TOTAL_PARSES", 0)
    command = (
        "".join("v%d=$(cat <<'A'\nnote\\\nA\n)\n" % i for i in range(8))
        + "git push origin dev"
    )

    flat = [tok for s in git_command.iter_context_token_streams(command) for tok in s]
    assert "push" in flat, "the streams primitive withdrew a push at cap 0"

    subs = [
        i.subcommand
        for i in git_command.iter_git_invocations_detailed(command, "/repo")
    ]
    assert "push" in subs, "the walk withdrew a push at cap 0"


def test_the_enumeration_yields_the_mixed_assignments_not_only_the_extremes():
    """The two extremes are the first two; everything BETWEEN them must follow.

    For k ambiguous heredocs there are 2**k assignments. Yielding only all-drop and all-join
    covers each heredoc's two readings in isolation but never a COMBINATION -- and a combination
    is what a command with two ambiguous heredocs in one context actually needs.

    Deterministic on purpose: it reads the generator directly rather than timing anything, so it
    cannot be waved away as machine noise. `_reading_assignments` is lazy and k can reach ~2053 at
    `MAX_COMMAND_LENGTH`, so this takes only the first four.
    """
    import itertools

    first_four = list(itertools.islice(git_command._reading_assignments(2), 4))
    assert first_four[0] == {}, ("the primary must come first", first_four)
    assert first_four[1] == {0: True, 1: True}, (
        "all-join must come second",
        first_four,
    )
    assert {frozenset(a.items()) for a in first_four} == {
        frozenset({}.items()),
        frozenset({0: True, 1: True}.items()),
        frozenset({0: True}.items()),
        frozenset({1: True}.items()),
    }, ("all four assignments must be yielded, extremes first", first_four)


def test_a_mixed_reading_recovers_an_invocation_neither_extreme_finds():
    """The behavioural half: the walk must CONSUME the mixed assignments, not merely be offered
    them.

    Witness measured 2026-09-23 -- two ambiguous heredocs in one backtick context, where the
    `git status` is visible only when the first heredoc JOINS and the second DROPS. Neither
    extreme produces it. Without the mixed assignments the marker still appears, so a guard still
    blocks; what is lost is the guard's ability to say WHAT it refused, which is the difference
    between a verdict and a shrug.
    """
    bs = "\\"
    command = (
        "`bash <<'A'"
        + chr(10)
        + "git log -1 dev"
        + bs
        + chr(10)
        + "A"
        + chr(10)
        + "bash <<"
        + chr(39)
        + 'a"b'
        + chr(39)
        + chr(10)
        + "git status"
        + bs
        + chr(10)
        + 'a"b'
        + chr(10)
        + "`"
    )
    subs = [
        i.subcommand
        for i in git_command.iter_git_invocations_detailed(command, "/repo")
    ]
    assert "status" in subs, (
        "the `git status` is visible only under a MIXED reading; the extremes alone lose it",
        subs,
    )
