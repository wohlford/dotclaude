#!/usr/bin/env python3
# Script: settings-hooks-check.py
# Purpose: Verify a promoted runtime settings.json kept every hook registration the commit added
# Usage: settings-hooks-check.py --scope <repo> [--ref <ref>]
"""Assert that every hook registration in a COMMITTED settings.json is present in the RUNTIME one.

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

## Statuses are an allowlist

`PASS` (nothing missing), `FAIL` (at least one committed registration absent), `ERROR` (the check
could not be made at all — unreadable or malformed input, or a committed file declaring zero
registrations). ERROR is not FAIL: it means no verdict was reached about the runtime file.

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

SETTINGS = "settings.json"


def _triples(doc, origin):
    """Flatten a settings document into a set of (event, matcher, command) triples.

    Shape errors raise ValueError rather than being skipped: a hooks block this function cannot
    read is not an empty hooks block, and silently treating it as one would drop exactly the
    registrations the check exists to find.
    """
    hooks = doc.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("%s: 'hooks' is not an object" % origin)
    out = set()
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise ValueError("%s: hooks.%s is not a list" % (origin, event))
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError(
                    "%s: a group under hooks.%s is not an object" % (origin, event)
                )
            matcher = group.get("matcher")
            entries = group.get("hooks", [])
            if not isinstance(entries, list):
                raise ValueError("%s: hooks.%s[].hooks is not a list" % (origin, event))
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError(
                        "%s: an entry under hooks.%s is not an object" % (origin, event)
                    )
                command = entry.get("command")
                if command is None:
                    raise ValueError(
                        "%s: an entry under hooks.%s has no command" % (origin, event)
                    )
                out.add((event, matcher, command))
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

    try:
        committed = _triples(
            _committed(scope, opts.ref), "%s:%s" % (opts.ref, SETTINGS)
        )
        runtime = _triples(json.loads(runtime_path.read_text()), str(runtime_path))
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

    if missing:
        sys.stdout.write("\nMISSING FROM RUNTIME — committed but not registered:\n")
        for t in missing:
            sys.stdout.write("  - %s\n" % _fmt(t))
        sys.stdout.write(
            "\nThese hooks are deployed but will never fire. Add them to %s by hand,\n"
            "keeping the runtime model/enabledPlugins values, then re-run this check.\n"
            % runtime_path
        )

    status, rc = ("FAIL", 1) if missing else ("PASS", 0)
    sys.stdout.write(
        "RESULT: %s rc=%d missing=%d extra=%d committed=%d runtime=%d\n"
        % (status, rc, len(missing), len(extra), len(committed), len(runtime))
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
