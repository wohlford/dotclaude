"""Shared policy and classification for the commit-subject length gate.

Pure functions only — no I/O beyond reading the opt-in marker, no command parsing. The two hooks
(`commit-subject-guard.py`, `commit-subject-advisor.py`) own their own extraction and share these
decisions, so a threshold or exemption can never mean two different things in the two tiers.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_command as gitcmd  # noqa: E402

MARKER_NAME = ".commit-conventions.toml"
DEFAULT_ADVISE = 72
DEFAULT_BLOCK = 80
KNOWN_KEYS = ("subject_advise", "subject_block")

# Cap on the marker file's SIZE, checked before it is read. 64KB is generous for a handful of
# `key = value` lines; measured, a 115MB marker cost 6.2s to read and parse per commit-shaped Bash
# call, on the gate's hot path. A file over the cap is treated exactly like any other malformed
# marker: inert, never an error.
MAX_MARKER_BYTES = 64 * 1024

# Bound on a digit VALUE's length, checked before `int()` ever sees it. Six digits is far beyond any
# realistic threshold and comfortably under CPython's int-string conversion limit (the default is
# 4300 digits; a value at or past it raises ValueError out of `int()` itself).
MAX_DIGIT_VALUE_LEN = 6

# `git_command.newlines_to_separators` rewrites every newline as exactly this before tokenizing.
NEWLINE_SUBSTITUTE = " ; "

OVERRIDE = "ALLOW_LONG_SUBJECT=1"

# Subjects git generates or matches BY TEXT. They inherit their length from a target commit and
# cannot be shortened — `--autosquash` pairs on the subject string — so demanding a reword would be
# an unsatisfiable block.
MACHINE_PREFIXES = ("fixup! ", "squash! ", "amend! ", 'Revert "')


class Policy(NamedTuple):
    """Thresholds at (and above) which a subject is advised on or blocked."""

    advise: int
    block: int


def load_policy(repo_root: str | None) -> Policy | None:
    """Read the repo's opt-in marker.

    Returns None whenever the gate must stay inert: no repo, no marker, a marker over
    `MAX_MARKER_BYTES`, an unreadable or malformed marker, or incoherent thresholds. A marker we
    cannot read must never enable enforcement at assumed numbers — "I could not read the policy" is
    the most ambiguous case there is, and this gate fails open on ambiguity.

    The ENTIRE parse — read, split, and every `int()` conversion — runs inside one `try`, so nothing
    a hostile or corrupted marker can contain reaches the caller as an exception. Belt and braces:
    `value.isdigit()` alone does not imply `int(value)` succeeds (a non-ASCII digit like `²` is
    `isdigit()`-true and `int()`-false) or that it is cheap (a value past CPython's int-string
    conversion limit, ~4300 digits, raises ValueError out of `int()` itself) — so the digit test
    below is tightened to reject both before `int()` is ever called, and the `try` is the backstop
    for anything that tightening missed. The contract is "never raises", not "usually doesn't".

    Args:
        repo_root: Absolute path to the repository root, or None when it could not be resolved.

    Returns:
        The resolved Policy, or None to stay inert.
    """
    if not repo_root:
        return None
    try:
        marker = Path(repo_root) / MARKER_NAME
        if not marker.is_file():
            return None
        if marker.stat().st_size > MAX_MARKER_BYTES:
            return None
        text = marker.read_text(encoding="utf-8")

        values: dict[str, int] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key, sep, rest = stripped.partition("=")
            if not sep:
                return None
            key = key.strip()
            if key not in KNOWN_KEYS:
                continue  # forward-compatible: ignore keys this version does not know
            value = rest.split("#", 1)[0].strip()
            if not (
                value.isascii()
                and value.isdigit()
                and len(value) <= MAX_DIGIT_VALUE_LEN
            ):
                return None
            values[key] = int(value)
    except (OSError, ValueError):
        return None

    policy = Policy(
        advise=values.get("subject_advise", DEFAULT_ADVISE),
        block=values.get("subject_block", DEFAULT_BLOCK),
    )
    if policy.advise > policy.block:
        return None
    return policy


def is_exempt(subject: str) -> bool:
    """True when the subject is machine-generated and therefore cannot be shortened.

    Args:
        subject: The subject line to check.

    Returns:
        True when the subject starts with one of `MACHINE_PREFIXES`.
    """
    return subject.startswith(MACHINE_PREFIXES)


def lower_bound_first_line(value: str) -> str:
    """The provably shortest candidate for this value's first line.

    A multi-line `-m` message reaches a PreToolUse hook as ONE token, its newlines already rewritten
    to `" ; "`. The prefix before the first `" ; "` is therefore either the true first line, or a
    strict prefix of it when the first line itself contained a literal `" ; "`. Either way it is a
    LOWER BOUND on the real first-line length, so measuring it can never over-report — which is what
    makes a false block impossible on this path.

    Args:
        value: The raw `-m` value token.

    Returns:
        The lower-bound first line, right-stripped.
    """
    return value.split(NEWLINE_SUBSTITUTE, 1)[0].rstrip()


def classify(subject: str, policy: Policy) -> str:
    """Grade an already-extracted subject against a policy.

    Args:
        subject: The subject line to grade.
        policy: The resolved thresholds.

    Returns:
        "block" at or above `policy.block`, "advise" at or above `policy.advise`, else "ok".
        Machine-generated subjects are always "ok".
    """
    if is_exempt(subject):
        return "ok"
    length = len(subject.rstrip())
    if length >= policy.block:
        return "block"
    if length >= policy.advise:
        return "advise"
    return "ok"


def segments(tokens: list[str]) -> list[list[str]]:
    """Split one context's token stream on control operators, mirroring push-guard's segment walk.

    Args:
        tokens: A flat token stream for one command context.

    Returns:
        The list of segments, split on (and excluding) each control-operator token.
    """
    out: list[list[str]] = []
    start = 0
    for i in range(len(tokens) + 1):
        if i == len(tokens) or gitcmd.is_op(tokens[i]):
            out.append(tokens[start:i])
            start = i + 1
    return out


def has_leading_override(seg: list[str]) -> bool:
    """True when the override sits among the segment's LEADING env assignments.

    Leading-position only, matching push-guard's contract and the documented spelling. A bare
    `OVERRIDE in seg` would also accept the token as a pathspec or a message word, silently
    disarming the gate.

    Args:
        seg: One command segment's tokens.

    Returns:
        True when `OVERRIDE` appears among the segment's leading run of env-assignment tokens.
    """
    for tok in seg:
        if tok == OVERRIDE:
            return True
        if not gitcmd.ENV_ASSIGN.match(tok):
            return False
    return False


def command_is_overridden(command: str) -> bool:
    """True when ANY segment of the command leads with the override.

    Used by the PostToolUse advisor, which has already lost segment structure by the time it reads
    the committed subject. Deliberately looser than the guard's per-segment test: suppressing an
    advisory is the safe direction, whereas suppressing a block would not be. Returns False on
    tokenizing ambiguity.

    Args:
        command: The raw shell command string.

    Returns:
        True when at least one segment leads with `OVERRIDE`; False otherwise, including on any
        tokenizing ambiguity.
    """
    try:
        for tokens in gitcmd.iter_context_token_streams(command):
            for seg in segments(tokens):
                if seg and has_leading_override(seg):
                    return True
    except ValueError:
        return False
    return False
