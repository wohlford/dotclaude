"""Every registered hook must refuse an invocation it cannot serve, instead of lying about it.

A hook takes its target from a JSON payload on stdin and ignores `argv` entirely. Run as a CLI it
has two failure modes, and BOTH read as something other than "you invoked this wrong":

* **stdin at EOF** — `cat` returns nothing, the hook finds no `file_path`, and it exits **0**.
  Byte-identical to a clean pass. Measured: `ruff-check.sh` was invoked this way three times in one
  session and "ruff clean" reported each time, having examined nothing. `md-links-check.py` then
  took the same path in the same session, immediately after the diagnosis was written down.
* **stdin a terminal** — `cat` blocks forever. No verdict, no artifact, no corpse; "still running"
  reads as normal for as long as you allow.

So the same command tells two different lies depending on the shell it lands in. Neither is
distinguishable from success by any cheap instrument, which is why this is a mechanical guard
rather than an advisory line: the advisory layer failed inside the session that authored it, and
the guard observes the actual invocation, which no prompt can see.

## The population is DERIVED, with a floor

Hooks are enumerated from `settings.json` — the authority on what is actually registered — rather
than from a hand-kept list that goes stale. But discovery cannot detect ABSENCE: a parser that
silently matches fewer scripts still reports a clean run, and a glob that stops covering a removed
member reports success loudest of all. `FLOOR` names members whose disappearance must alarm, and
`test_population_is_non_trivial` asserts a non-zero denominator.
"""

from __future__ import annotations

import json
import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SETTINGS = REPO / "settings.json"
PREFIX = "$HOME/.claude/scripts/"

# A payload naming a path no hook acts on, so every hook exits 0 through its own cheap guard
# rather than doing real work. Several of these hooks run entire test suites on a payload they
# care about; this keeps the preserve rows fast without weakening them.
INERT_PAYLOAD = '{"tool_input":{"file_path":"/nonexistent/does-not-exist.zzz"}}'

# Named so their ABSENCE alarms. Deliberately a subset the derivation may exceed — a new hook
# needs no edit here — spanning both languages and both payload shapes (`file_path` hooks and
# `command` hooks), since those read different fields and could plausibly guard differently.
FLOOR = frozenset(
    {
        "guard-secrets.sh",
        "push-guard.py",
        "ruff-check.sh",
        "md-links-check.py",
        "style-check.sh",
        "sync-docs-check.sh",
        "commit-subject-guard.py",
        "mutation-anchors-check-test.sh",
    }
)


def registered_hooks():
    """Basenames of every hook `settings.json` registers under the scripts directory."""
    doc = json.loads(SETTINGS.read_text())
    names = set()
    for groups in doc["hooks"].values():
        for group in groups:
            for entry in group.get("hooks", []):
                command = entry["command"]
                if command.startswith(PREFIX):
                    names.add(command[len(PREFIX) :].strip())
    return names


def present_hooks():
    """Registered hooks that exist in this repo, sorted — the rows the parametrised tests run."""
    return sorted(n for n in registered_hooks() if (REPO / "scripts" / n).is_file())


HOOKS = present_hooks()


def run(name, argv=(), payload=b"", tty_stdin=False, timeout=30):
    """Invoke a hook, returning (rc, stderr). `tty_stdin` reproduces the blocking case."""
    path = REPO / "scripts" / name
    if name.endswith(".py"):
        cmd = [sys.executable, str(path), *argv]
    else:
        cmd = ["bash", str(path), *argv]

    if not tty_stdin:
        proc = subprocess.run(cmd, input=payload, capture_output=True, timeout=timeout)
        return proc.returncode, proc.stderr.decode(errors="replace")

    master, slave = pty.openpty()
    try:
        proc = subprocess.run(cmd, stdin=slave, capture_output=True, timeout=timeout)
    finally:
        os.close(master)
        os.close(slave)
    return proc.returncode, proc.stderr.decode(errors="replace")


def test_population_is_non_trivial():
    """A sweep over zero hooks passes against anything and reads exactly like a clean run."""
    assert len(HOOKS) >= len(FLOOR), "derived only %d hooks: %s" % (len(HOOKS), HOOKS)


def test_floor_members_are_all_present():
    missing = sorted(FLOOR - set(HOOKS))
    assert not missing, (
        "declared floor members absent from the derived set: %s" % missing
    )


@pytest.mark.parametrize("name", HOOKS)
def test_refuses_argv(name):
    """Handed a filename it cannot honour, a hook must say so rather than exit 0."""
    rc, err = run(name, argv=["some/file.py"])
    assert rc == 2, "exited %d (0 would read as a clean pass over nothing)" % rc
    assert "stdin" in err.lower(), "refusal does not say how to invoke it: %r" % err


@pytest.mark.parametrize("name", HOOKS)
def test_refuses_a_terminal_stdin(name):
    """With no payload coming, a hook must refuse rather than block forever."""
    try:
        rc, err = run(name, tty_stdin=True, timeout=15)
    except subprocess.TimeoutExpired:
        pytest.fail("blocked on stdin instead of refusing — the hang mode")
    assert rc == 2, "exited %d rather than refusing a terminal stdin" % rc


@pytest.mark.parametrize("name", HOOKS)
def test_still_serves_a_real_payload(name):
    """PRESERVE: the guard must not fire on the invocation the harness actually makes."""
    rc, err = run(name, payload=INERT_PAYLOAD.encode())
    assert rc == 0, "refused a legitimate payload (rc %d): %s" % (rc, err)


@pytest.mark.parametrize("name", HOOKS)
def test_empty_stdin_is_left_alone(name):
    """PRESERVE: an empty payload is a real harness state; it must keep its current behaviour.

    Deliberately NOT folded into the argv refusal. The harness can hand a hook an empty stdin,
    and turning that into a refusal would fail closed on every edit — a guard whose blast radius
    is every tool call is worse than the trap it closes.
    """
    rc, _ = run(name, payload=b"")
    assert rc == 0, "empty stdin now exits %d; this path must stay as it was" % rc
