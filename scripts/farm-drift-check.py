#!/usr/bin/env python3
# Script: farm-drift-check.py
# Purpose: SessionStart hook — warn when the ~/.claude symlink farm has drifted from its source clone
# Usage: Called by Claude Code hooks (SessionStart, matcher startup|resume) with JSON on stdin
"""SessionStart hook — run the production clone's `install.sh --check` and warn on drift.

Advisory only. It never repairs, never blocks a session and keeps no state. A clean farm is
SILENT; a drifted one prints ONE `systemMessage` line naming the drifted members; a check that
cannot give a verdict (did not finish, refused, printed none) says so instead of reading as clean.

The production clone is found from this file's own physical location (`resolve()` follows the
farm's `scripts` symlink), never from a hardcoded path. The hook is silent, and does NOT run
`install.sh`, unless the payload says SessionStart, `<root>/install.sh` is a file, and
`$HOME/.claude/.installed-from` (the marker a successful install writes) is a regular file.

Exit codes:
  0 — every real invocation: silent, warned, or could not check
  2 — refused an invocation it cannot serve (argv present, or stdin a terminal); see
      scripts/HOOKS.md. The harness never takes this path: it always pipes a JSON payload.

`FARM_DRIFT_DEADLINE_SECS` replaces the child deadline (tests use it); only a positive finite
number is honoured, anything else is reported on stderr and the default used.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

DEADLINE_SECS = 8.0
KILL_GRACE_SECS = 1.0
DEADLINE_ENV = "FARM_DRIFT_DEADLINE_SECS"
MAX_NAMES = 5
STDERR_CAP = 100
SELF_INSTALL_PREFIX = "refuse: install.sh must not run from inside the install target"

DRIFT_RE = re.compile(
    r"^DRIFT: (.+?) \((?:missing or not a live symlink at|resolved to) ", re.M
)
CLEAN_RE = re.compile(r"^RESULT: PASS .*\bcheck=clean\b", re.M)
FAIL_DRIFT_RE = re.compile(r"^RESULT: FAIL .*\bcheck=drift\b", re.M)
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _write(fd: int, text: str) -> None:
    """Write `text` to `fd` with no buffered layer, swallowing any OSError.

    `print` would leave unflushed bytes for the interpreter's shutdown flush, and a broken pipe
    there turns the exit status into 120 (measured elsewhere in this repo). os.write has nothing
    left to flush.
    """
    data = text.encode("utf-8", "replace")
    try:
        while data:
            written = os.write(fd, data)
            data = data[written:]
    except OSError:
        pass


def _say(message: str) -> None:
    _write(1, json.dumps({"systemMessage": message}) + "\n")


def _clean(text: str) -> str:
    return CONTROL_RE.sub("", text)


def _deadline() -> float:
    raw = os.environ.get(DEADLINE_ENV)
    if raw is None or raw.strip() == "":
        return DEADLINE_SECS
    try:
        value = float(raw)
    except ValueError:
        value = None
    if value is None or not math.isfinite(value) or value <= 0:
        _write(
            2,
            "farm-drift-check: ignoring %s=%r (not a positive number of seconds); using %gs.\n"
            % (DEADLINE_ENV, raw, DEADLINE_SECS),
        )
        return DEADLINE_SECS
    return value


def _run_check(install: Path, deadline: float):
    """Run `install --check` in its own session. Returns (status, rc, stdout, stderr).

    status is "spawn-failed", "timeout" or "done". On timeout the whole process group gets TERM,
    then KILL after KILL_GRACE_SECS: install.sh forks git and realpath, and killing the direct
    child alone would orphan them.
    """
    try:
        proc = subprocess.Popen(
            [str(install), "--check"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=os.environ.copy(),
            start_new_session=True,
        )
    except OSError:
        return "spawn-failed", None, "", ""
    try:
        out, err = proc.communicate(timeout=deadline)
        return "done", proc.returncode, out, err
    except subprocess.TimeoutExpired:
        pass
    for sig, grace in (
        (signal.SIGTERM, KILL_GRACE_SECS),
        (signal.SIGKILL, KILL_GRACE_SECS),
    ):
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            pass
        try:
            proc.communicate(timeout=grace)
            break
        except subprocess.TimeoutExpired:
            continue
    return "timeout", None, "", ""


def _member_state(target: str, name: str) -> str:
    try:
        stamp = os.lstat(os.path.join(target, name)).st_ctime
    except OSError:
        return "absent"
    return "last changed " + time.strftime("%Y-%m-%d", time.localtime(stamp))


def _drift_message(out: str, target: str):
    """The one-line warning for the DRIFT lines in `out`, or None when no name is usable."""
    names = []
    for raw in DRIFT_RE.findall(out):
        name = _clean(raw)
        if name and "/" not in name and name not in names:
            names.append(name)
    if not names:
        return None
    shown = ", ".join(
        "%s (%s)" % (n, _member_state(target, n)) for n in names[:MAX_NAMES]
    )
    if len(names) > MAX_NAMES:
        shown += ", and %d more" % (len(names) - MAX_NAMES)
    return (
        "farm drift: ~/.claude members no longer link to their source: %s. "
        "Repair: run install.sh in the production clone (no flag; it backs up what it "
        "replaces); install.sh --check lists the detail." % shown
    )


def _classify(status: str, rc, out: str, err: str, deadline: float, target: str):
    """Map one `--check` outcome to the warning to print, or None for silence (spec D5)."""
    if status == "spawn-failed":
        return "farm drift check could not run (cannot execute install.sh)."
    if status == "timeout":
        return (
            "farm drift check did not finish within %gs; farm state unknown." % deadline
        )
    if rc == 0 and CLEAN_RE.search(out):
        return None
    if rc == 0:
        return "farm drift check printed no verdict; farm state unknown."
    if FAIL_DRIFT_RE.search(out):
        return _drift_message(out, target) or (
            "farm drift reported but its member names were unreadable."
        )
    first = err.strip().splitlines()[0] if err.strip() else ""
    if first.startswith(SELF_INSTALL_PREFIX):
        return (
            "farm drift: the hooks and install.sh are running from a copy of the source, "
            "not from the production clone; install.sh --check cannot judge from here."
        )
    detail = _clean(first)[:STDERR_CAP] if first else "exit %s" % rc
    return "farm drift check could not run (%s)." % detail


def main() -> int:
    # HOOK CONTRACT: the payload arrives as JSON on stdin; argv is ignored. Refuse the two
    # invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and
    # stdin at EOF it exits 0 having examined nothing, and with a terminal stdin it blocks
    # forever. See scripts/HOOKS.md for the payload form.
    if len(sys.argv) > 1 or sys.stdin.isatty():
        sys.stderr.write(
            "%s is a Claude Code hook: it reads a JSON payload on stdin and ignores\n"
            "arguments. Running it with filenames examines nothing — see scripts/HOOKS.md.\n"
            % "farm-drift-check.py"
        )
        sys.exit(2)
    try:
        try:
            data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        except ValueError:
            return 0
        if not isinstance(data, dict) or data.get("hook_event_name") != "SessionStart":
            return 0
        home = os.environ.get("HOME")
        if not home:
            return 0
        install = Path(__file__).resolve().parent.parent / "install.sh"
        if not install.is_file():
            return 0
        target = os.path.realpath(os.path.join(home, ".claude"))
        if not os.path.isfile(os.path.join(target, ".installed-from")):
            return 0
        deadline = _deadline()
        status, rc, out, err = _run_check(install, deadline)
        message = _classify(status, rc, out, err, deadline, target)
        if message is not None:
            _say(message)
    except Exception as exc:  # advisory: a bug here must never become a blocked session
        _say("farm drift hook failed internally (%s)." % type(exc).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
