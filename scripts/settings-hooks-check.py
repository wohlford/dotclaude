#!/usr/bin/env python3
# Script: settings-hooks-check.py
# Purpose: Verify a promoted runtime settings.json kept every hook registration and never lowered a committed timeout
# Usage: settings-hooks-check.py --scope <repo> [--ref <ref>]
"""Assert every hook registration in a COMMITTED settings.json is present in the RUNTIME one, and no runtime timeout is lower than committed.

`/propagate` promotes by fast-forwarding production. `settings.json` is marked `skip-worktree`
there — it carries machine-local preferences (`model`, `enabledPlugins`) that must survive a
promote — so when it blocks an `--ff-only` merge the skill parks it, fast-forwards, and restores
the parked copy. That restored copy PREDATES the incoming commit, so it lacks whatever hooks the
commit registered. Measured once at **21 runtime entries against 23 committed**: three newly
built, fully-tested hooks would have deployed and never fired.

Nothing catches this on its own. `git diff FETCH_HEAD -- settings.json` reports CLEAN, because
`skip-worktree` makes git assume worktree == index for the path — which is the entire reason a
dedicated check exists rather than a diff.

## The assertion is one-directional, on purpose

**No committed registration may be absent from the runtime file.** Never an equality, and never a
count. Both weaker forms are measured failures on this machine:

* The runtime legitimately carries machine-local hooks the repo does not track, so an equality
  check false-alarms on every promote and gets ignored, which is worse than not existing.
* Counts are not sets. A promote was measured where the runtime and the commit BOTH held 24
  entries while differing in both directions at once — a machine-local extra present, a committed
  gate missing. Every tally passes there while a dead gate ships.

Runtime-only entries are therefore REPORTED (they are worth seeing) but never fail the check.

## Identity is the triple, not the command

`(event, matcher, command)`. The same script registered under a different matcher, or a different
event, is a different registration that fires on different things — treating the command alone as
the identity would wave through a hook silently rewired to match nothing.

## A lowered timeout is a missing registration in slow motion

The harness kills a hook that outlives its `timeout` and tells nobody, so a runtime timeout LOWER than
the committed one re-opens exactly the dead-gate defect above for any run that needs the difference.
The promote's restore puts that back too: the parked copy predates a commit that raised a timeout. For
every triple present in both files the EFFECTIVE timeout is compared — absent means the harness
default, and a triple registered more than once counts its minimum, the earliest kill. Lower is a
FAIL; higher is reported, never failed, by the same one-directional rule as extra registrations.

## Statuses are an allowlist

`PASS` (nothing missing and nothing lowered), `FAIL` (at least one committed registration absent, or registered with a
lower timeout), `ERROR` (the check could not be made at all — unreadable or malformed input, or a
committed file declaring zero registrations). ERROR is not FAIL: it means no verdict was reached
about the runtime file.

Zero committed registrations is an ERROR rather than a vacuous PASS. A comparison whose expected
set is empty succeeds against literally any runtime file, and reads exactly like a clean promote.

Usage:
    settings-hooks-check.py --scope <repo> [--ref <ref>]

`--scope` is the repo whose WORKING `settings.json` is the runtime file; `--ref` (default `HEAD`)
names the commit supplying the committed side. After a promote, `HEAD` is the promoted commit,
which is the comparison you want; during one, pass `--ref FETCH_HEAD`.

Exit codes: 0 PASS, 1 FAIL, 2 ERROR. The last line of stdout is always the verdict.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
try:
    import settings_hooks
except ImportError as exc:  # reported as ERROR by main(): no walker, no verdict
    settings_hooks = None
    _IMPORT_ERROR = exc

SETTINGS = "settings.json"


def _registrations(doc, origin):
    """Map each (event, matcher, command) triple in a settings document to its EFFECTIVE timeout.

    One parse, through `settings_hooks.walk_hook_entries`, which owns the shape validation: a hooks
    block it cannot read raises ValueError rather than reading as an empty one, and silently treating
    it as empty would drop exactly the registrations this check exists to find. The timeout is the
    harness default when `timeout` is absent, and the MINIMUM when one document registers the same
    triple more than once — the earliest kill.
    """
    out = {}
    for event, matcher, entry in settings_hooks.walk_hook_entries(doc, origin):
        key = (event, matcher, entry["command"])
        seconds = settings_hooks.entry_timeout(entry, origin)
        out[key] = min(seconds, out.get(key, seconds))
    return out


def _committed(scope, ref):
    proc = subprocess.run(
        ["git", "-C", str(scope), "show", "%s:%s" % (ref, SETTINGS)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ValueError(
            "cannot read %s:%s from %s — %s"
            % (ref, SETTINGS, scope, proc.stderr.strip())
        )
    return json.loads(proc.stdout)


def _fmt(triple):
    event, matcher, command = triple
    return "%s [%s] %s" % (event, matcher, command)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scope",
        required=True,
        help="repo whose working settings.json is the runtime file",
    )
    parser.add_argument(
        "--ref",
        default="HEAD",
        help="commit supplying the committed side (default HEAD)",
    )
    opts = parser.parse_args(argv)

    scope = Path(opts.scope)
    runtime_path = scope / SETTINGS

    if settings_hooks is None:
        sys.stdout.write(
            "cannot import settings_hooks from %s — %s\n"
            % (Path(__file__).resolve().parent / "lib", _IMPORT_ERROR)
        )
        sys.stdout.write("RESULT: ERROR rc=2\n")
        return 2

    try:
        committed_t = _registrations(
            _committed(scope, opts.ref), "%s:%s" % (opts.ref, SETTINGS)
        )
        runtime_t = _registrations(
            json.loads(runtime_path.read_text()), str(runtime_path)
        )
        committed = set(committed_t)
        runtime = set(runtime_t)
    except (OSError, ValueError) as exc:
        # json.JSONDecodeError subclasses ValueError.
        sys.stdout.write("%s\n" % exc)
        sys.stdout.write("RESULT: ERROR rc=2\n")
        return 2

    if not committed:
        sys.stdout.write(
            "%s:%s declares ZERO hook registrations — refusing to compare against an empty\n"
            "expected set, which would pass against any runtime file whatsoever.\n"
            % (opts.ref, SETTINGS)
        )
        sys.stdout.write("RESULT: ERROR rc=2\n")
        return 2

    missing = sorted(committed - runtime)
    extra = sorted(runtime - committed)
    lowered = []
    raised = []
    for t in sorted(committed & runtime):
        if runtime_t[t] < committed_t[t]:
            lowered.append((t, committed_t[t], runtime_t[t]))
        elif runtime_t[t] > committed_t[t]:
            raised.append((t, committed_t[t], runtime_t[t]))

    sys.stdout.write(
        "runtime:   %s (%d registrations)\n" % (runtime_path, len(runtime))
    )
    sys.stdout.write(
        "committed: %s:%s (%d registrations)\n" % (opts.ref, SETTINGS, len(committed))
    )

    if extra:
        sys.stdout.write(
            "\nruntime-only, NOT a failure (machine-local hooks the repo does not track):\n"
        )
        for t in extra:
            sys.stdout.write("  + %s\n" % _fmt(t))

    if raised:
        sys.stdout.write(
            "\nruntime timeout HIGHER than committed, NOT a failure (a machine-local choice):\n"
        )
        for t, want, got in raised:
            sys.stdout.write(
                "  + %s: committed %gs, runtime %gs\n" % (_fmt(t), want, got)
            )

    if missing:
        sys.stdout.write("\nMISSING FROM RUNTIME — committed but not registered:\n")
        for t in missing:
            sys.stdout.write("  - %s\n" % _fmt(t))
        sys.stdout.write(
            "\nThese hooks are deployed but will never fire. Add them to %s by hand,\n"
            "keeping the runtime model/enabledPlugins values, then re-run this check.\n"
            % runtime_path
        )

    if lowered:
        sys.stdout.write(
            "\nTIMEOUT LOWERED IN RUNTIME — the runtime kills these hooks sooner than committed:\n"
        )
        for t, want, got in lowered:
            sys.stdout.write(
                "  - %s: committed %gs, runtime %gs\n" % (_fmt(t), want, got)
            )
        sys.stdout.write(
            "\nA hook killed at its timeout reports nothing, so each is a gate that can silently not\n"
            "run. Set each timeout in %s to the committed value, then re-run this check.\n"
            % runtime_path
        )

    status, rc = ("FAIL", 1) if missing else ("PASS", 0)
    if lowered:
        status, rc = ("FAIL", 1)
    sys.stdout.write(
        "RESULT: %s rc=%d missing=%d lowered=%d extra=%d committed=%d runtime=%d\n"
        % (
            status,
            rc,
            len(missing),
            len(lowered),
            len(extra),
            len(committed),
            len(runtime),
        )
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
