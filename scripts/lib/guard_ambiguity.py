"""WHY a reading of the command was lost, and the remedy that actually clears THAT cause.

## The defect

All three guards used to end their ambiguity refusal with one fixed sentence: *"Simplify the
quoting and retry; the usual cause is a heredoc delimiter containing shell metacharacters, as in
`<<'(x'`."*

The marker has TWO causes, and that sentence names the one that does not occur in practice.
the shared tokenizer's `iter_context_token_streams`/`_walk_context` emit it when a variant RAISED, and
also when the parse BUDGET (`MAX_TOTAL_PARSES`) stopped the enumeration before every reading was
tried — `_collect`'s own comment says so: *"Both branches that can lose a reading emit it: a
variant that RAISES, and an exhausted BUDGET."* Over 90,675 real commands from the local
transcripts, exactly 6 emit a marker and **6 of 6 are the budget cause, 0 the delimiter cause**.
Those 6 are ordinary 13–47 KB "write a long report via a heredoc" commands, and simplifying
quoting cannot clear a parse-cap truncation — the operator is handed the one remedy that cannot
work, for the only cause anyone actually hits.

`scripts/explain-git-command.py` already distinguishes them (`the parse cap stopped the
enumeration: N reading(s) never tried`), from `ReadingTrail.lost`. This module is that same
distinction, phrased as a REMEDY, for the three guards that print a refusal rather than a report.

## How the cause is recovered, and what it costs

`ReadingTrail.lost` is the source, and it is already exact: `"unreadable"`, `"truncated"`, or
`"unreadable+truncated"` per context. Nothing here re-derives it from the text.

A caller that already walked (`publication-push-guard.py`) passes its trails in and pays nothing.
A caller whose own primitive is stream-shaped (`push-guard.py`, `git-timing-guard.py`) has no
trails, because `iter_context_token_streams` returns none — so `cause()` walks once more. That
second walk is paid ONLY on a path that is already emitting a block, never on an allow, and it is
bounded by the guard's own deadline (`guard_deadline.py`).

## Non-raising by contract

Every function here is called from inside a block-emitting path, and `scripts/HOOKS.md` makes exit
2 the only veto — any other nonzero exit is noise and the guarded command RUNS. So an exception
escaping this module would UNBLOCK. Every failure therefore degrades to the undetermined cause,
whose wording names both possibilities rather than guessing one.

## Why no count is printed

`ReadingTrail.untried` is `2**k - spent`, and `k` is bounded by input LENGTH (~2053 at
`MAX_COMMAND_LENGTH`), so the number is routinely astronomical — rendering it readably is a
problem `explain-git-command.py` has already had to solve once. The refusal names that tool
instead, and leaves the arithmetic to it.
"""

from __future__ import annotations

UNREADABLE = "unreadable"
TRUNCATED = "truncated"
UNDETERMINED = ""

_REMEDY_UNREADABLE = (
    "One reading of it RAISED while being parsed, so simplify the quoting and retry: the usual "
    "cause is a heredoc delimiter containing shell metacharacters, as in <<'(x'."
)

_REMEDY_TRUNCATED = (
    "This one is the parse CAP, not the quoting: each quoted heredoc whose last body line ends in "
    "an odd backslash run DOUBLES the readings that must be enumerated, and the tokenizer stopped "
    "before it had tried them all. Simplifying the quoting will not clear it — remove the trailing "
    "backslash from the last body line of those heredocs, or send fewer of them in one command."
)

_REMEDY_UNDETERMINED = (
    "Two things cause this and the guard could not tell which: a reading that RAISED (simplify the "
    "quoting — the usual cause is a heredoc delimiter containing shell metacharacters, as in "
    "<<'(x'), or the parse CAP stopping the enumeration (remove the trailing backslash from the "
    "last body line of the quoted heredocs, or send fewer of them; simplifying the quoting will "
    "not clear that one)."
)


def cause(gitcmd, command: str, trails=None) -> str:
    """WHY a reading was lost: `UNREADABLE`, `TRUNCATED`, both joined by `+`, or `UNDETERMINED`.

    Args:
        gitcmd: the shared tokenizer module (passed in, because `git-timing-guard.py` imports it
            lazily and must keep doing so).
        command: the raw command, walked only when `trails` is None.
        trails: `ReadingTrail`s from a walk the caller already made. Passing them is the whole
            difference between paying for a second walk and paying nothing.

    Returns `UNDETERMINED` rather than raising on anything unexpected — see the module docstring.
    """
    try:
        if trails is None:
            _invocations, trails = gitcmd.iter_git_invocations_with_readings(
                command, None
            )
        kinds = set()
        for trail in trails:
            for part in getattr(trail, "lost", "").split("+"):
                if part in (UNREADABLE, TRUNCATED):
                    kinds.add(part)
        # Canonical order, matching `ReadingTrail.lost`'s own "unreadable+truncated".
        return "+".join(k for k in (UNREADABLE, TRUNCATED) if k in kinds)
    except Exception:  # noqa: BLE001 - a broken diagnostic must never unblock
        return UNDETERMINED


def remedy(lost_cause: str) -> str:
    """The sentence naming a remedy that WORKS for `lost_cause`. Never raises, never empty.

    An ALLOWLIST over the three values `cause` can return: anything else — including a future
    `ReadingTrail.lost` value this module has not been taught — lands on the undetermined wording,
    which names both remedies. A blocklist here would silently hand one cause the other's remedy,
    which is the defect this module exists to remove.
    """
    if lost_cause == UNREADABLE:
        return _REMEDY_UNREADABLE
    if lost_cause == TRUNCATED:
        return _REMEDY_TRUNCATED
    if lost_cause == "%s+%s" % (UNREADABLE, TRUNCATED):
        return "%s %s" % (_REMEDY_UNREADABLE, _REMEDY_TRUNCATED)
    return _REMEDY_UNDETERMINED
