#!/usr/bin/env python3
# Script: commit-subject-guard.py
# Purpose: PreToolUse hook — refuse a commit whose subject is provably at or over the block limit
# Usage: Called by Claude Code hooks with JSON on stdin
"""PreToolUse hook — refuse a `git commit` whose subject is provably at or over the block threshold.

Called by Claude Code hooks with the tool-call JSON on stdin. Only ever blocks on a subject it can
prove is too long: the value must be literal (no unexpanded `$`, no command substitution) and, for
the heredoc form `/commit` emits, the delimiter must be QUOTED. Everything else — an unknowable
substitution, `-F`, `-C`, `--fixup`, a repo that has not opted in, or any tokenizing ambiguity —
is allowed. The companion PostToolUse advisor covers what cannot be measured here by reading the
real subject after the fact.

Because the measured value is a LOWER BOUND on the true first line (see
`commit_subject.lower_bound_first_line`), this hook cannot over-measure, so a false block is
impossible by construction rather than by care.

Exit codes:
  0 — allow (not a commit, not opted in, unmeasurable, exempt, overridden, or any internal error)
  2 — blocked: the subject is provably at or over the block threshold (stderr fed back to Claude)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import commit_subject as cs  # noqa: E402
import git_command as gitcmd  # noqa: E402

# The override spelling and the segment/override helpers live in commit_subject.py so the advisor
# shares exactly one definition. Reference them as cs.OVERRIDE / cs.segments /
# cs.has_leading_override; do NOT redefine them here.

# `git commit` short options that consume a value; only -m carries an inline subject.
VALUE_SHORT = "mFCct"
VALUE_LONG = {
    "--message",
    "--file",
    "--reuse-message",
    "--reedit-message",
    "--template",
    "--author",
    "--date",
    "--cleanup",
    "--fixup",
    "--squash",
    "--trailer",
}

# The heredoc form skills/commit/SKILL.md step 7 emits. Two deliberate restrictions:
#   * the delimiter MUST be quoted — an unquoted `<<EOF` expands its body at runtime, so the literal
#     text is not the final subject;
#   * `<<-` is deliberately NOT matched. It strips leading tabs from body lines while this regex would
#     keep them, so a tab-indented 79-character line measures 80 (verified) — a boundary false block.
#     `/commit` never emits `<<-`, and not matching it fails open.
HEREDOC_RE = re.compile(
    r"^\s*cat\s+<<\s*(?P<q>['\"])(?P<delim>[A-Za-z_][A-Za-z0-9_]*)(?P=q)\s*\n"
    r"(?P<body>.*?)\n(?P=delim)\s*$",
    re.DOTALL,
)


def _placeholder_index(token: str) -> int | None:
    """The nested-context index a placeholder token refers to, or None."""
    if token.startswith(gitcmd.PLACEHOLDER_PREFIX) and token.endswith(
        gitcmd.PLACEHOLDER_SUFFIX
    ):
        body = token[len(gitcmd.PLACEHOLDER_PREFIX) : -len(gitcmd.PLACEHOLDER_SUFFIX)]
        if body.isdigit():
            return int(body)
    return None


def _is_literal(token: str) -> bool:
    """True when the token's text is what the shell will actually pass to git.

    The placeholder check is LOAD-BEARING, not belt-and-braces. A value with an EMBEDDED
    substitution — `-m "chore: bump $(cat VERSION) and sync $(cat OTHER) manifests"` — tokenizes to
    text carrying `__GIT_COMMAND_SUBST_n__` markers but containing no `$` and no backtick (measured:
    90 characters). Checking only `$`/backtick therefore calls it literal and measures the marker
    text, false-blocking a subject whose real expansion may be 40 characters.
    """
    return (
        "$" not in token and "`" not in token and gitcmd.PLACEHOLDER_PREFIX not in token
    )


def _message_token(args: list[str]) -> str | None:
    """The first `-m`/`--message` value in a commit's argument list, or None.

    First only, never concatenated: `-m subject -m body` is git's multi-paragraph form, and the
    subject is the first. A cluster's first value-taking character consumes the rest of the cluster
    if any remains, else the next token — so `-am X` yields X but `-ma` yields "a".
    """
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            return None
        if tok == "--message":
            return args[i + 1] if i + 1 < len(args) else None
        if tok.startswith("--message="):
            return tok.split("=", 1)[1]
        if tok in VALUE_LONG:
            i += 2
            continue
        if tok.startswith("--"):
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            body = tok[1:]
            for j, ch in enumerate(body):
                if ch in VALUE_SHORT:
                    rest = body[j + 1 :]
                    if ch != "m":
                        return (
                            None  # -F/-C/-c/-t took the value: no inline subject here
                        )
                    if rest:
                        return rest
                    return args[i + 1] if i + 1 < len(args) else None
        i += 1
    return None


def _resolvable_heredoc(command: str) -> str | None:
    """The one top-level nested context's heredoc first line, when resolution is UNAMBIGUOUS.

    Placeholder indices are scoped to the context that OWNS them.
    `iter_context_token_streams` flattens every context into one flat list of streams, so an index
    read out of a *nested* stream cannot be resolved against the *top-level* nested list. Measured
    counterexample: for

        echo "$(cat <<'EOF' … 80 f's … EOF)" ; out=$(git commit -m "$(gen_msg)")

    the commit appears in `streams[2]` carrying placeholder index 0, but top-level `nested[0]` is the
    ECHOED heredoc — so resolving there measures 80 and FALSE-BLOCKS a message the hook cannot know.
    The mirror case silently misses a real over-long heredoc subject.

    Rather than re-implement `_walk_context`'s recursion here — the exact divergence the shared
    tokenizer exists to prevent, and a walk hardened over five published bricks — this narrows to the
    provably unambiguous shape: exactly two contexts in total (the top level plus one child) and
    exactly one top-level nested entry. Then the only possible index is 0 and it can only mean that
    child. **That is precisely the shape `/commit` emits** (verified: streams=2, nested=1); anything
    richer is skipped, which fails open.
    """
    if len(gitcmd.iter_context_token_streams(command)) != 2:
        return None
    _outer, nested = gitcmd.split_command_contexts(
        gitcmd.fold_continuations(gitcmd.strip_comments(command)), 0
    )
    if len(nested) != 1:
        return None
    match = HEREDOC_RE.match(nested[0].text)
    if not match:
        return None  # not a quoted heredoc -> the real text is unknowable
    body = match.group("body")
    if not _is_literal(body):
        return None
    return body.split("\n", 1)[0].rstrip()


def _subject_from(token: str, heredoc_subject: str | None) -> str | None:
    """The provable lower-bound subject for a `-m` value, or None when unmeasurable.

    A token that is ENTIRELY one placeholder resolves only via the unambiguous heredoc case. A token
    with an EMBEDDED placeholder is not literal (see `_is_literal`) and is therefore skipped.
    """
    if _placeholder_index(token) is not None:
        return heredoc_subject
    if not _is_literal(token):
        return None
    return cs.lower_bound_first_line(token)


def _repo_root(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    root = out.stdout.strip()
    return root if out.returncode == 0 and root else None


def _target_dir(cdir: str | None, cwd: str) -> str:
    """The directory a `-C <dir>` value addresses, mirroring the advisor's target computation.

    An absolute `-C` value is used as-is; a relative one resolves against the payload `cwd`.
    Carrying no `-C` at all, the invocation targets `cwd` itself.
    """
    if not cdir:
        return cwd
    return cdir if Path(cdir).is_absolute() else str(Path(cwd) / cdir)


def _offending_subject(command: str, cwd: str) -> tuple[str, cs.Policy] | None:
    """The first (subject, policy) pair in `command` that must be blocked, or None.

    Policy is resolved PER INVOCATION, from THAT invocation's own `-C` directory when it carries
    one, falling back to `cwd` otherwise — never once, upfront, from the payload `cwd` alone.
    Resolving policy once from `cwd` reads the WRONG repo for `git -C <other> commit …`: measured
    to FALSE BLOCK a non-opted-in target when `cwd` happens to be opted in, and to silently MISS a
    real violation when `cwd` is not opted in but the `-C` target is. Mirrors
    `commit-subject-advisor.py`'s `_commit_dirs`, which names this exact hazard.

    Segment-scoped, like push-guard: the override is an env assignment leading its own segment, and
    an invocation tuple would already have discarded it.
    """
    heredoc_subject = _resolvable_heredoc(command)
    root_cache: dict[str, str | None] = {}
    for tokens in gitcmd.iter_context_token_streams(command):
        for seg in cs.segments(tokens):
            if not seg or cs.has_leading_override(seg):
                continue
            for i, tok in enumerate(seg):
                # `starts_command` is REQUIRED, not decorative: without it `echo git commit -m <long>`
                # reads as a commit and FALSE-BLOCKS a command that only prints text (verified).
                if not gitcmd.is_git(tok) or not gitcmd.starts_command(seg, i):
                    continue
                # Skip git's GLOBAL options before the subcommand, capturing `-C`'s value as we go
                # (both `-C <dir>` and attached `-C<dir>`) so policy can be resolved against the
                # repo THIS invocation actually targets, not the payload cwd. `-C` must be named
                # EXPLICITLY: GLOBAL_VALUE_OPTS contains lowercase `-c` only (verified —
                # {'--git-dir','--namespace','--work-tree','-c'}), so relying on it alone reads
                # `-C <dir>`'s VALUE as the subcommand and misses `git -C <repo> commit` entirely,
                # which is this repo's routine form.
                j = i + 1
                cdir: str | None = None
                while j < len(seg):
                    cur = seg[j]
                    if cur == "-C" and j + 1 < len(seg):
                        cdir = seg[j + 1]
                        j += 2
                        continue
                    if cur.startswith("-C") and len(cur) > 2:
                        cdir = cur[2:]
                        j += 1
                        continue
                    if cur in gitcmd.GLOBAL_VALUE_OPTS:
                        j += 2  # consumes a separate value
                        continue
                    if cur.startswith("-"):
                        j += 1  # attached value (-c k=v) or a plain flag
                        continue
                    break
                if j >= len(seg) or seg[j] != "commit":
                    continue
                args = seg[j + 1 :]
                if "--dry-run" in args:
                    continue
                value = _message_token(args)
                if value is None:
                    continue
                subject = _subject_from(value, heredoc_subject)
                if not subject:
                    continue
                target = _target_dir(cdir, cwd)
                if target not in root_cache:
                    root_cache[target] = _repo_root(target)
                policy = cs.load_policy(root_cache[target])
                if policy is None:
                    continue
                if cs.classify(subject, policy) == "block":
                    return subject, policy
    return None


def main() -> int:
    """Hook entry point: 2 blocks the commit, 0 allows it (fail open on anything unclear)."""
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
        return 0  # cheap pre-filter; sound only because this gate fails OPEN

    cwd = data.get("cwd") or "."
    try:
        offense = _offending_subject(command, cwd)
    except ValueError:
        return 0  # tokenizing ambiguity: not a security gate, so allow
    if offense is None:
        return 0
    subject, policy = offense

    print(
        f"blocked by commit-subject-guard: the subject is {len(subject)} characters, at or over the "
        f"{policy.block}-character limit.\n"
        f"  {subject}\n"
        f"Fix: shorten it (a tighter scope usually recovers the most). The documented limit is "
        f"under {policy.advise} characters.\n"
        f"If this length is genuinely required, lead the segment with {cs.OVERRIDE}.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
