#!/usr/bin/env python3
# Script: push-guard.py
# Purpose: PreToolUse hook — block `git push` unless the push segment leads with an ALLOW_PUSH=1 override
# Usage: Called by Claude Code hooks with JSON on stdin
"""PreToolUse hook — force every `git push` to be an explicit, per-push decision.

A DELIBERATENESS gate, not an adversarial defense (same posture as the `push-guard.sh` it replaces).
It blocks a `git push` (or `git subtree push`) unless the command SEGMENT that carries it itself
leads with `ALLOW_PUSH=1` — other `NAME=val` assignments may precede it, but a wrapper (`sudo`,
`env`, …) before it breaks the run, and an `ALLOW_PUSH=1` on a different segment does not authorize
this one. An author bent on evasion can always prepend `ALLOW_PUSH=1`, alias push, or use plumbing
(`git send-pack`, `git svn dcommit`) — the override concedes that by design.

Detection is now a git-SUBCOMMAND match, via the shared `scripts/lib/git_command` tokenizer, instead
of the old raw "a git word and a push word anywhere in the segment" regex: a git invocation in
command position is a push operation iff its subcommand is `push`, or its subcommand is `subtree`
and `push` appears among its argument tokens. This kills the old guard's false positives — `push` in
a commit/tag message or a filename (`git add scripts/publication-push-guard.py`) no longer blocks —
while every direct push shape (`git push`, `sudo git push`, `git -C <dir> push`,
`git <globals> push`, `git subtree push`, in any control-operator or newline-joined position) stays
blocked unless authorized.

CONCEDED RESIDUALS (deliberate; same class of gap `git_command.py`'s own docstring already
concedes): `push` hidden inside an opaque string (`bash -c 'git push'`, `` echo `git push` ``) is
invisible to the tokenizer, and a wrapper WITH its own arguments (`sudo -u deploy git push`,
`timeout 60 git push`) is not stepped over — `starts_command` only steps over a *bare* wrapper. See
specs/2026-07-18-guard-tokenizer-detection.md §Conceded residuals for the rationale: re-catching
these would reintroduce the false-positive class this change exists to kill.

Fails OPEN on anything ambiguous: unparseable/missing JSON, a missing `.tool_input.command`, an empty
command, a tokenizer `ValueError` (unbalanced quotes), or any other unexpected exception — all exit
0 (allow). This is the OPPOSITE posture of the sibling `publication-push-guard.py`, which fails
CLOSED: that hook is a security boundary (keeping a `dev` branch private), this one is a nudge
against an accidental, undeliberate push.

Exit codes:
  0 — allow: no push op found, the push op is authorized, or any internal error (fail open).
  2 — block: an unauthorized push op — stderr carries the standard actionable message.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import git_command as gitcmd  # noqa: E402

BLOCK_MESSAGE = (
    "blocked by push-guard: pushing is explicit-only. Lead the push segment with "
    "ALLOW_PUSH=1 (e.g. ALLOW_PUSH=1 git push ...) to authorize it."
)


def _leading_env_authorized(seg: list[str]) -> bool:
    """True if seg's leading run of consecutive `NAME=val` env-assignment tokens (before the first
    non-assignment token) contains the literal token `ALLOW_PUSH=1`. A wrapper or any other
    non-assignment token at the start of the run means the segment is not authorized."""
    authorized = False
    for tok in seg:
        if not gitcmd.ENV_ASSIGN.match(tok):
            break
        if tok == "ALLOW_PUSH=1":
            authorized = True
    return authorized


def _skip_global_options(seg: list[str], start: int) -> int:
    """Return the index of the subcommand token, stepping over git's global options starting at
    `start` (just past the `git` token itself) — mirrors `iter_git_invocations`'s own walk,
    including its `-C <dir>` / `-C<dir>` handling."""
    k, n = start, len(seg)
    while k < n and seg[k].startswith("-"):
        opt = seg[k]
        if opt == "-C" and k + 1 < n:
            k += 2
        elif opt.startswith("-C") and len(opt) > 2:
            k += 1
        elif opt in gitcmd.GLOBAL_VALUE_OPTS and "=" not in opt:
            k += 2
        else:
            k += 1
    return k


def _segment_has_unauthorized_push(seg: list[str]) -> bool:
    """True if `seg` (one control-operator-delimited slice of the token stream, no operators
    inside it) contains a git invocation, in command position, that is a push operation, and the
    segment's own leading env-assignment run does not authorize it."""
    if not seg:
        return False
    authorized = _leading_env_authorized(seg)
    j, n = 0, len(seg)
    while j < n:
        if not (gitcmd.is_git(seg[j]) and gitcmd.starts_command(seg, j)):
            j += 1
            continue
        sub_idx = _skip_global_options(seg, j + 1)
        if sub_idx >= n:
            break
        sub = seg[sub_idx]
        k = sub_idx + 1
        args: list[str] = []
        while k < n and not (gitcmd.is_git(seg[k]) and gitcmd.starts_command(seg, k)):
            args.append(seg[k])
            k += 1
        is_push_op = sub == "push" or (sub == "subtree" and "push" in args)
        if is_push_op and not authorized:
            return True
        j = k
    return False


def _has_unauthorized_push(command: str) -> bool:
    """Newline-normalize, tokenize, strip redirects, split into control-operator-delimited
    segments, and check each for an unauthorized push op. Raises on a tokenizer `ValueError`
    (unbalanced quotes) — the caller's try/except turns that into fail-open."""
    normalized = command.replace("\n", " ; ").replace("\r", " ")
    tokens = gitcmd.strip_redirects(gitcmd.tokenize(normalized))

    seg_start = 0
    n = len(tokens)
    for i in range(n + 1):
        if i == n or gitcmd.is_op(tokens[i]):
            if _segment_has_unauthorized_push(tokens[seg_start:i]):
                return True
            seg_start = i + 1
    return False


def main() -> int:
    """Hook entry point: 2 blocks the push, 0 allows it (fail open on any ambiguity)."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    command = tool_input.get("command") or ""
    if not isinstance(command, str):
        command = ""
    if not command:
        return 0

    blocked = False
    try:
        blocked = _has_unauthorized_push(command)
    except Exception:  # noqa: BLE001 - deliberate: any crash here must fail OPEN
        return 0

    if blocked:
        print(BLOCK_MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
