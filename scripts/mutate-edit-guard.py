#!/usr/bin/env python3
# Script: mutate-edit-guard.py
# Purpose: PreToolUse hook — refuse an edit to a file a mutation campaign owns (its sidecar exists)
# Usage: Called by Claude Code hooks with JSON on stdin
"""PreToolUse hook — refuse Edit/Write/MultiEdit/NotebookEdit on a file a mutation campaign owns.

`scripts/lib/mutate.py` mutates its subject in place and, when the campaign ends, writes back the
snapshot it took at the start — unconditionally. An edit made meanwhile is overwritten without a
word, and the campaign still reports `restored: sha256 unchanged`, which is true of the snapshot
and silent about the edit. mutate.py writes `<subject>.mutate-backup` before its baseline and
deletes it only after a verified restore, so the sidecar's existence IS the ownership signal; a
killed campaign strands it, and then the subject may still hold a live MUTANT, which is just as
wrong to edit.

Two candidate sidecars are checked: beside the path as given, and beside its realpath. mutate.py
now parks the sidecar beside the resolved subject, which the realpath candidate always reaches;
the as-given candidate covers an older mutate.py (an old session, production before a promote)
that parked it beside a symlink, when the edit uses that same link spelling — an edit that instead
resolves through the real path finds no sidecar there.

The suffix is COPIED, never imported from mutate.py: this hook must keep working while mutate.py is
itself a campaign subject, when importing it would load a live mutant. The suite asserts the two
constants are equal.

Accepted limits: a shell write (sed -i, a heredoc, cp) through the Bash tool is not intercepted;
reads are not refused; a live campaign and a dead one's stranded sidecar block alike, which is
correct, since either way the file is not safe to edit; and a campaign that STARTS between this
check and the write it allowed still clobbers that write (an unavoidable check-then-act window).

Exit codes:
  0 — allow (no sidecar, nothing to judge, or ANY internal error: fail open)
  2 — blocked: a campaign owns the file (stderr names the sidecar and the recovery), or the hook
      was invoked with argv / a terminal stdin
"""

from __future__ import annotations

import json
import os
import shlex
import sys

# Must equal scripts/lib/mutate.py's BACKUP_SUFFIX — tests/test_mutate_edit_guard.py asserts it.
BACKUP_SUFFIX = ".mutate-backup"

# NotebookEdit keys its target differently from the other file tools.
_TARGET_KEYS = ("file_path", "notebook_path")


def target_of(payload):
    """The absolute path the tool call would write, or None when there is nothing to judge."""
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    value = None
    for key in _TARGET_KEYS:
        candidate = tool_input.get(key)
        if isinstance(candidate, str) and candidate:
            value = candidate
            break
    if value is None:
        return None
    value = os.path.expanduser(value)
    if os.path.isabs(value):
        return value
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and os.path.isabs(cwd):
        return os.path.join(cwd, value)
    return None


def sidecar_candidates(target):
    """Every place a campaign owning `target` may have parked its sidecar, deduplicated."""
    out = []
    for spelling in (target, os.path.realpath(target)):
        candidate = spelling + BACKUP_SUFFIX
        if candidate not in out:
            out.append(candidate)
    return out


def owning_sidecar(target):
    """The first candidate sidecar on disk, or None."""
    for candidate in sidecar_candidates(target):
        if os.path.lexists(candidate):
            return candidate
    return None


def refusal(tool, target, sidecar):
    subject = sidecar[: -len(BACKUP_SUFFIX)]
    q_side, q_subj = shlex.quote(sidecar), shlex.quote(subject)
    return "\n".join(
        [
            f"mutate-edit-guard: BLOCKED {tool} on {target}",
            f"  A mutation campaign owns this file: {sidecar} exists. scripts/lib/mutate.py writes",
            "  it before its baseline and deletes it only after a verified restore — and that restore",
            "  writes back the pre-run snapshot, so this edit would be SILENTLY OVERWRITTEN. Until then",
            "  the file may hold a live MUTANT, not the source.",
            "  Do not route around this with a shell write: the restore clobbers that identically.",
            "  - Campaign still running (check: pgrep -fl '(^|[ /])mutate_[a-z0-9_]+[.]py'): wait",
            "    for it to finish, then retry.",
            "  - None running: it died and stranded the sidecar. Recover with",
            f"      cmp -s {q_side} {q_subj} && rm {q_side}   # undamaged: clear it",
            f"      cp {q_side} {q_subj} && rm {q_side}   # if cmp differs: the subject is still MUTATED",
            "",
        ]
    )


def main(argv, stdin):
    # HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the two
    # invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and stdin
    # at EOF it exits 0 having examined nothing, and with a terminal stdin it blocks forever.
    if len(argv) > 1 or (stdin is not None and stdin.isatty()):
        sys.stderr.write(
            f"{os.path.basename(argv[0])} is a Claude Code hook: it reads a JSON payload on stdin "
            "and ignores arguments.\nRunning it with filenames examines nothing. See "
            "scripts/HOOKS.md for the payload form.\n"
        )
        return 2
    try:
        payload = json.loads(stdin.read())  # a CLOSED stdin (None) raises here: allowed
        target = target_of(payload)
        if target is None:
            return 0
        sidecar = owning_sidecar(target)
        if sidecar is None:
            return 0
        tool = payload.get("tool_name")
        message = refusal(tool if isinstance(tool, str) else "edit", target, sidecar)
    except Exception:
        # Fail open: a broken guard must never wedge every edit everywhere.
        return 0
    try:
        sys.stderr.write(message)
        sys.stderr.flush()
    except Exception:
        sys.stderr = None  # nothing left to flush at exit, so the exit code stays 2 (a failing shutdown flush exits 120)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin))
