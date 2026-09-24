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
and `push` appears among its argument tokens, or its subcommand is INDETERMINATE
(`gitcmd.subcommand_is_indeterminate`: a git-like or opaque word such as `$V` or `$(true)git`, which
bash may run as a push). This kills the old guard's false positives — `push` in
a commit/tag message or a filename (`git add scripts/publication-push-guard.py`) no longer blocks —
while every direct push shape (`git push`, `sudo git push`, `git -C <dir> push`,
`git <globals> push`, `git subtree push`, in any control-operator or newline-joined position) stays
blocked unless authorized.

RESERVED-WORD / COMPOUND-COMMAND POSITION IS COVERED. A reserved word (`if`, `then`, `elif`,
`else`, `while`, `until`, `do`, `{`, `!`, `coproc`) or `exec` puts a following `git` in command
position exactly as a control operator does — `if true; then git push …; fi`, `{ git push …; }`,
`! git push …`, `while false; do git push …; done`, `exec git push …` and a function body
(`f() { git push …; }; f`) are all seen and blocked. This was a MEASURED defect, not a hypothesized
one: all six shapes read exit 0 (unblocked) through the real hook before this fix, because the two
`is_git(...) and starts_command(...)` call sites in `_segment_has_unauthorized_push` called
`starts_command` bare, and `starts_command` only treats reserved words as command-position
boundaries when told to (see `git_command.py`'s `RESERVED_WORDS`/`_git_starts_command`). Fixing
detection alone is a HALF fix and was measured to regress the override: `_leading_env_authorized`
walks from `seg[0]` to find a leading `ALLOW_PUSH=1`, and once a reserved word can legitimately BE
`seg[0]`, that walk breaks before ever reaching the assignment — so
`if true; then ALLOW_PUSH=1 git push …; fi` would stop being authorized. Both halves land together:
the walker also skips a leading run of reserved words / `exec` before looking for the assignment. A
WRAPPER (`sudo`, `env`, …) is deliberately NOT skipped there — that is push-guard's stricter
reading (previous paragraph) and it must survive: `sudo ALLOW_PUSH=1 git push` still blocks.

NESTED COMMAND CONTEXTS ARE COVERED. A push inside `$( … )`, backticks, `<( … )` or `>( … )` — at
any depth, and including one inside another git command's own argument span
(`git commit -m "$(git push …)"`) — is seen and judged. `ALLOW_PUSH=1` stays *segment*-scoped
inside each context, matching the shell.

CONCEDED RESIDUALS (deliberate; the same class `git_command.py`'s own docstring concedes): `push`
hidden inside a string another program shell-executes (`bash -c 'git push'`, `/bin/sh -c`,
pipe-into-shell, a herestring) is invisible to the tokenizer, and so is a QUOTED or otherwise
single-token `eval` argument (`eval "git push origin main"`) — shlex collapses the whole phrase
into one string token, so there is no standalone `git` token in THIS command's own stream to find.
A BARE `eval git push` is no longer a residual: `eval` is a member of `git_command.py`'s `WRAPPERS`
set (fix/eval-wrapper-bypass) and is stepped over like any other wrapper, so the invocation behind
it is found and blocked. A wrapper WITH its own arguments (`sudo -u deploy git push`,
`timeout 60 git push`) is also not stepped over — `starts_command` only steps over a *bare*
wrapper. Backticks were listed here until nested contexts were covered; they are no longer a
residual. Re-catching the remaining classes would reintroduce the false-positive class this
detection exists to kill, and each is open-ended rather than closed.

AMBIGUITY POSTURE — split, deliberately. A tokenizer `ValueError` on a command that MENTIONS git
now BLOCKS (exit 2) rather than being swallowed: "I could not parse it" must not silently become
"there is no push here", which was the same fail-open class as the nested-context bypasses.
Everything else still fails OPEN — unparseable/missing JSON, a missing `.tool_input.command`, an
empty command, a `ValueError` on a command with no git word, and any other unexpected exception all
exit 0. The blast radius is therefore push-shaped, and a bug in the scanner cannot brick every
command in the session. This is a NARROWER fail-closed than the sibling
`publication-push-guard.py`, which is a security boundary (keeping a `dev` branch private); this
one remains a nudge against an accidental, undeliberate push.

Exit codes:
  0 — allow: no push op found, the push op is authorized, or any internal error (fail open).
  2 — block: an unauthorized push op — stderr carries the standard actionable message.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import git_command as gitcmd  # noqa: E402

BLOCK_MESSAGE = (
    "blocked by push-guard: pushing is explicit-only. Lead the push segment with "
    "ALLOW_PUSH=1 (e.g. ALLOW_PUSH=1 git push ...) to authorize it."
)


def _ambiguity_detail(exc: BaseException | None) -> str:
    """The shared location clause plus THIS guard's path to the explain tool, or "" on any failure.

    Non-raising by contract — see the call site: a raise here would exit 1 and unblock the command.
    The location logic lives in the tokenizer so both guards share one implementation rather than
    two that can drift; only the tool path is per-caller, resolved from this file because a
    repo-relative string resolves to nothing from the deployed config farm.
    """
    try:
        clause = gitcmd.describe_ambiguity(exc)
        if not clause:
            return ""
        tool = Path(__file__).resolve().parent / "explain-git-command.py"
        return f"{clause} For the full parse, run: python3 {tool} -"
    except Exception:  # noqa: BLE001 - deliberate: a broken diagnostic must never unblock
        return ""


AMBIGUOUS_MESSAGE = (
    "blocked by push-guard: this command mentions git but could not be parsed unambiguously, "
    "so it is refused rather than allowed unchecked. Simplify the quoting and retry. "
    "Note that an env assignment does not reach a command substitution in a sibling assignment "
    '(ALLOW_PUSH=1 out="$(git push ...)" does NOT authorize that push) - put the override '
    "inside the substitution instead."
)


def _leading_env_authorized(seg: list[str]) -> bool:
    """True if seg's leading run of consecutive `NAME=val` env-assignment tokens (before the first
    non-assignment token) contains the literal token `ALLOW_PUSH=1`. A wrapper or any other
    non-assignment token at the start of the run means the segment is not authorized.

    A LEADING RUN OF RESERVED WORDS / `exec` IS SKIPPED FIRST, and this half is only correct
    alongside `_segment_has_unauthorized_push` passing `reserved_words=`/`extra_wrappers=` at its
    detection sites. Teaching the detector that `if`/`then`/`{`/`!`/`exec` put git in command
    position, WITHOUT teaching this walker the same thing, silently converts an under-block into an
    over-block: this loop starts at `seg[0]`, which is now the reserved word, and breaks before it
    ever reaches the assignment, so `if true; then ALLOW_PUSH=1 git push …; fi` stops being
    authorized. Measured, in exactly that half-applied state — the suite's "the override still
    authorizes inside a compound" row is the only one that moves, which is why it exists.

    A WRAPPER is deliberately NOT skipped here: `sudo` is in `WRAPPERS`, not `RESERVED_WORDS`, so
    `sudo ALLOW_PUSH=1 git push` still does not authorize — push-guard's stricter reading (see the
    module docstring), and it has its own row."""
    start = 0
    while start < len(seg) and (
        seg[start] in gitcmd.RESERVED_WORDS or seg[start] in gitcmd.GIT_ONLY_WRAPPERS
    ):
        start += 1
    authorized = False
    for tok in seg[start:]:
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
    inside it) contains a git invocation, in command position, whose subcommand is a push
    operation OR is INDETERMINATE (opaque, or itself git-like, so it may resolve to one at run
    time — `gitcmd.subcommand_is_indeterminate`), and the segment's own leading env-assignment
    run does not authorize it."""
    if not seg:
        return False
    authorized = _leading_env_authorized(seg)
    j, n = 0, len(seg)
    while j < n:
        if not (
            gitcmd.is_git(seg[j])
            and gitcmd.starts_command(
                seg,
                j,
                reserved_words=gitcmd.RESERVED_WORDS,
                extra_wrappers=gitcmd.GIT_ONLY_WRAPPERS,
            )
        ):
            j += 1
            continue
        sub_idx = _skip_global_options(seg, j + 1)
        if sub_idx >= n:
            break
        sub = seg[sub_idx]
        k = sub_idx + 1
        args: list[str] = []
        while k < n and not (
            gitcmd.is_git(seg[k])
            and gitcmd.starts_command(
                seg,
                k,
                reserved_words=gitcmd.RESERVED_WORDS,
                extra_wrappers=gitcmd.GIT_ONLY_WRAPPERS,
            )
        ):
            args.append(seg[k])
            k += 1
        is_push_op = (
            sub == "push"
            or (sub == "subtree" and "push" in args)
            or gitcmd.subcommand_is_indeterminate(sub)
        )
        if is_push_op and not authorized:
            return True
        j = k
    return False


def _has_unauthorized_push(command: str) -> bool:
    """Check EVERY command context for an unauthorized push op.

    Each context's tokens are split into control-operator-delimited segments and judged
    independently, so `ALLOW_PUSH=1` authorizes only the segment that leads with it — inside a
    nested context exactly as at the top level, matching the shell: `x="$(ALLOW_PUSH=1 git push …)"`
    really does export the variable for that push.

    Consumes `iter_context_token_streams`, NOT `iter_git_invocations_with_cwd`. This gate's rule is
    *segment*-scoped, and the invocation tuple has already dropped the segment's leading env
    assignments — a version rebuilt on it could not see any authorization and would block every
    push, including this repo's own publish.

    Raises:
        ValueError: On tokenizing ambiguity or excessive nesting. `main` decides what that means.
    """
    for tokens in gitcmd.iter_context_token_streams(command):
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
    # HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the
    # two invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and
    # stdin at EOF it exits 0 having examined nothing, and with a terminal stdin it blocks
    # forever. See scripts/HOOKS.md for the payload form.
    if len(sys.argv) > 1 or sys.stdin.isatty():
        sys.stderr.write(
            "%s is a Claude Code hook: it reads a JSON payload on stdin and ignores\n"
            "arguments. Running it with filenames examines nothing — see scripts/HOOKS.md.\n"
            % "push-guard.py"
        )
        sys.exit(2)
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
    except ValueError as exc:
        # D1: "I could not parse it" must stop meaning "there is no push here" -- that swallow was
        # the same fail-open class as the nested-context bypasses this change closes. Bounded by
        # has_git_word so the blast radius stays push-shaped: an unparseable command with no git
        # word still fails OPEN, exactly as before.
        #
        # This `except` MUST precede the blanket one below: ValueError is a subclass of Exception,
        # so the broad handler would otherwise win and D1 would silently not happen while every
        # test still passed.
        if gitcmd.has_git_word(command):
            # `exc` itself, NOT exc.__cause__: this guard catches the tokenizer's exception
            # UNWRAPPED, so there is no cause to unwrap. (The publication guard re-wraps and
            # therefore reads __cause__ — same feature, two mechanisms, and conflating them
            # silently produces no detail here.)
            #
            # _ambiguity_detail is contractually non-raising. It has to be: an exception raised
            # inside THIS except block is not caught by the sibling `except Exception` below, so it
            # would propagate and exit 1 — which HOOKS.md treats as noise rather than a veto, i.e.
            # the command would RUN. The block must never depend on the diagnostic succeeding.
            # Name the CATEGORY too. The constant is a fixed string that never interpolated
            # the exception, so until now this guard discarded even WHICH construct stopped
            # the tokenizer — the reader got "could not be parsed" and nothing else.
            print(
                f"{AMBIGUOUS_MESSAGE} The tokenizer stopped at: {exc}."
                f"{_ambiguity_detail(exc)}",
                file=sys.stderr,
            )
            return 2
        return 0
    except Exception:  # noqa: BLE001 - deliberate: any OTHER crash must still fail OPEN
        # D1 changes the AMBIGUITY posture, not the crash posture. A bug in the scanner must not
        # brick every git command in the session.
        return 0

    if blocked:
        print(BLOCK_MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
