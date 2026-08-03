#!/usr/bin/env python3
# Script: commit-subject-advisor.py
# Purpose: PostToolUse hook — advise an amend when a committed subject reaches the advisory limit
# Usage: Called by Claude Code hooks with JSON on stdin
"""PostToolUse hook — after a successful `git commit`, advise an amend when the subject is too long.

The precise half of the commit-subject gate. It reads the REAL subject with
`git log -1 --format=%s`, so it is post-expansion and exact: no literality gate, no `$VAR` guessing,
no heredoc parsing, and it covers `-F`, `-C` and editor-driven commits that a pre-execution parse
structurally cannot see. The PreToolUse guard prevents the egregious cases; this one catches
everything else immediately, while an amend is still free.

Identification is by TOKENIZER, never by substring: `"commit" in command` also matches
`bash scripts/tests/test_commit_subject_guard.sh` — the substring sits in the FILENAME — and this
hook would then read HEAD after an unrelated command and prescribe amending whatever was there.

Exit codes:
  0 — silent (not a commit, command failed, not opted in, compliant, exempt, pushed, or any error)
  2 — advisory: the real subject is at or over the advise threshold (stderr fed back to Claude)
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import commit_subject as cs  # noqa: E402
import git_command as gitcmd  # noqa: E402


def _git(root: str, *args: str) -> tuple[int, str]:
    try:
        out = subprocess.run(
            ["git", "-C", root, *args], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return out.returncode, out.stdout.strip()


def _commit_dirs(command: str) -> list[str | None] | None:
    """The `-C` dir of every real `git commit` in the command, or None when there is none.

    Confirmed by TOKENIZER, never by substring: `"commit" in command` also matches
    `bash scripts/tests/test_commit_subject_guard.sh` — the substring is in the FILENAME.

    Returning the dirs (not a bool) is load-bearing. Resolving the repo from the payload `cwd` alone
    reads the WRONG repo for `git -C <other> commit …` — the `/propagate` shape — and would then
    advise amending a commit the command never touched, resurfacing the very hazard rule 1 exists to
    defuse.

    NOTE: `iter_git_invocations` was measured NOT to raise ValueError on the reserved marker,
    over-length input, or excessive depth (unlike `iter_context_token_streams`). The except clause is
    therefore defensive only — do not claim it is exercised by a test.
    """
    dirs: list[str | None] = []
    try:
        for cdir, sub, args in gitcmd.iter_git_invocations(command):
            if sub == "commit" and "--dry-run" not in args:
                dirs.append(cdir)
    except ValueError:
        return None
    return dirs or None


def _observed_failure(data: dict) -> bool:
    """True only on POSITIVE evidence that the Bash call failed.

    Deliberately inverted from "did it succeed?", and the inversion is the safe direction here.

    OBSERVED, not guessed: pinned from this session's own PostToolUse transcript (4657 records). A
    successful Bash call's `tool_response` is a dict carrying `stdout`, `stderr`, `interrupted`,
    `isImage`, `noOutputExpected` — no exit-code field of any kind. A FAILED Bash call's
    `tool_response` is not a dict at all: it is a plain STRING, e.g. `"Error: Exit code 1\nnothing
    to commit"`, `"Error: Exit code 128\n..."`, or `"Error: File does not exist..."`. `exit_code`,
    `exitCode` and `returncode` appear NOWHERE in the transcript — not as failure keys, not as
    anything. `success` does appear, but only from OTHER tools, never from Bash; the check on it is
    kept below because a caller could still pass it, and `False` is unambiguous positive evidence.

    So: stay silent only when the payload positively says the command failed — a dict with
    `success is False` or `interrupted is True`, or (the real Bash-failure shape) a non-dict
    response whose stripped text starts with `"Error:"`. The dangerous case this function does NOT
    cover on its own — the commit did not happen and we advise amending a pre-existing commit — is
    carried by `_head_is_fresh` instead, which is schema-independent: a failed commit leaves HEAD
    unchanged and older than the window. The two checks are complementary, not redundant: this one
    reads the payload's own verdict, that one reads git's, so a gap in either schema is still caught
    by the other.

    Residual, accepted: a failing command run within the freshness window of a genuine over-long
    commit advises on that earlier commit. It is the caller's own unpushed commit and it really is
    over-long, so the advice is correct, merely mistimed.
    """
    response = data.get("tool_response")
    if isinstance(response, dict):
        if response.get("success") is False:
            return True
        if response.get("interrupted") is True:
            return True
        # Never observed in the transcript on a Bash tool_response: exit_code, exitCode, returncode.
        # Recorded here as a comment, not live code, so a future schema that adds one is a deliberate
        # decision to write, not a silently forgotten possibility.
        return False
    if isinstance(response, str):
        return response.strip().startswith("Error:")
    return False


def _head_is_fresh(root: str, window: int = 60) -> bool:
    """True when HEAD was committed within `window` seconds.

    Schema-independent backstop for the one hazard that makes reading HEAD dangerous: if the commit
    did NOT happen, HEAD is unchanged and older, so it cannot be mistaken for this command's result
    and advised on. Complementary to `_observed_failure`, never a replacement for it: that check
    reads the PAYLOAD's own verdict (dict `success`/`interrupted`, or a string starting `"Error:"`);
    this one reads GIT's, so a gap in either schema is still caught by the other.
    """
    code, stamp = _git(root, "log", "-1", "--format=%ct")
    if code != 0 or not stamp.isdigit():
        return False
    return (time.time() - int(stamp)) <= window


def main() -> int:
    """Hook entry point: 2 asks for an amend, 0 stays silent."""
    # HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the
    # two invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and
    # stdin at EOF it exits 0 having examined nothing, and with a terminal stdin it blocks
    # forever. See scripts/HOOKS.md for the payload form.
    if len(sys.argv) > 1 or sys.stdin.isatty():
        sys.stderr.write(
            "%s is a Claude Code hook: it reads a JSON payload on stdin and ignores\n"
            "arguments. Running it with filenames examines nothing — see scripts/HOOKS.md.\n"
            % "commit-subject-advisor.py"
        )
        sys.exit(2)
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    tool_input = data.get("tool_input")
    command = (
        (tool_input or {}).get("command") or "" if isinstance(tool_input, dict) else ""
    )
    if not command or "commit" not in command:
        return 0
    if _observed_failure(data):
        return 0
    dirs = _commit_dirs(command)
    if dirs is None:
        return 0
    # An overridden long subject was authorized on purpose. Nagging for an amend here would defeat
    # the override one tier later and, on a /recast replay, would demand shortening a deliberately
    # preserved historical subject — which skills/recast/SKILL.md explicitly forbids (Task 6).
    if cs.command_is_overridden(command):
        return 0
    # Resolve the repo the commit actually targeted, not merely the shell's cwd. Stay silent when
    # several commits target different places — there is then no single HEAD to speak about.
    if len(set(dirs)) != 1:
        return 0
    cdir = dirs[0]
    cwd = data.get("cwd") or "."
    target = (
        cdir
        if cdir and Path(cdir).is_absolute()
        else (str(Path(cwd) / cdir) if cdir else cwd)
    )

    code, root = _git(target, "rev-parse", "--show-toplevel")
    if code != 0 or not root:
        return 0
    policy = cs.load_policy(root)
    if policy is None:
        return 0
    if not _head_is_fresh(root):
        return 0  # HEAD predates this command: the commit did not happen

    # Never advise amending a commit that has left this machine.
    code, remotes = _git(root, "branch", "-r", "--contains", "HEAD")
    if code != 0 or remotes:
        return 0

    code, subject = _git(root, "log", "-1", "--format=%s")
    if code != 0 or not subject:
        return 0

    verdict = cs.classify(subject, policy)
    if verdict == "ok":
        return 0

    limit = policy.block if verdict == "block" else policy.advise
    print(
        f"commit-subject-advisor: the committed subject is {len(subject)} characters, at or over "
        f"the {limit}-character {'limit' if verdict == 'block' else 'advisory threshold'}.\n"
        f"  {subject}\n"
        f"The commit is local and unpushed, so amend it now:\n"
        f'  git commit --amend -m "<shorter subject>"\n'
        f"If a tag or CHANGELOG entry references it, keep them in sync (see /commit's amend flow).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
