"""Shared `settings.json` hook walker.

The repo had TWO independent parses of this shape before this module existed:
`scripts/tests/test_hook_argv_refusal.py::registered_hooks` (silent on malformed shape — a
group or entry of the wrong type is simply skipped) and
`scripts/settings-hooks-check.py::_triples` (raised `ValueError` naming what it found instead).
Adding a checker with its own third parse would be the two-spellings defect this repo keeps
hitting, so this module is that parse, and `registered_hooks` now filters over it.

**`settings-hooks-check.py::_triples` was converted on 2026-09-15, together with its campaign.**
Its duplicate `_triples` parse had carried three campaign anchors, which is why the conversion
waited: moving the body without re-anchoring them first measured `caught=4 survived=3` — a
verification tool quietly losing half its teeth while every suite stayed green. Once the checker
started reading timeouts through this walker (`entry_timeout`, for the lowered-runtime-timeout
check), the duplicate's own validation could no longer change any outcome the checker's suite
could observe, so the conversion stopped being optional — a mutation of `_triples`'s shape checks
would have survived regardless of what it did. The identity rows re-anchored to the checker's
projection over this walker's output; the shape-validation rows moved to
`scripts/tests/mutate_settings_hooks_lib.py`, whose subject is this module.

The raising behaviour is the one that survives. A hooks block a parser cannot read is not an
empty hooks block, and silently treating it as one would drop exactly the registrations a
consumer exists to find — see CLAUDE.md's "Clear by allowlist" and "A tool that IGNORES an
argument it cannot parse" hazards. The silent variant was the weaker of the two readings.
"""

from __future__ import annotations

HARNESS_DEFAULT_TIMEOUT_SECS = 600


def walk_hook_entries(doc, origin):
    """Flatten a settings document into a list of (event, matcher, entry) tuples.

    `entry` is the registration's own dict, so a caller can read keys beyond `command` (such as
    `timeout`) without a third parse of this shape. Shape errors raise ValueError rather than being
    skipped: a hooks block this function cannot read is not an empty hooks block, and silently
    treating it as one would drop exactly the registrations a caller exists to find.

    `origin` is a label for the document (a path, or a "ref:path" string) used only to make a
    raised message name where the bad shape was found.
    """
    hooks = doc.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("%s: 'hooks' is not an object" % origin)
    out = []
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
                if not isinstance(command, str):
                    # `command is None` (the key absent) and "present but not a string" are the
                    # same shape error to a caller that immediately does `command.startswith(...)`
                    # — both raise AttributeError there rather than the ValueError this function
                    # promises. A hooks block holding either is not an empty one; treating a
                    # wrong-typed command as absent-and-skippable would drop exactly the
                    # registration a caller exists to find.
                    raise ValueError(
                        "%s: an entry under hooks.%s has a non-string command (%r)"
                        % (origin, event, command)
                    )
                out.append((event, matcher, entry))
    return out


def walk_hook_triples(doc, origin):
    """Flatten a settings document into a set of (event, matcher, command) triples.

    A projection of `walk_hook_entries`, which owns the shape validation — see its docstring.
    """
    return {
        (event, matcher, entry["command"])
        for event, matcher, entry in walk_hook_entries(doc, origin)
    }


def entry_timeout(entry, origin):
    """The timeout, in seconds, the harness applies to one registration.

    An absent `timeout` is the harness default, never "unbounded" — the harness applies its default
    whether or not the key is written. A value the harness would not honour as a duration (a bool,
    a string, zero, a negative) raises ValueError rather than being read as some number.
    """
    if "timeout" not in entry:
        return HARNESS_DEFAULT_TIMEOUT_SECS
    value = entry["timeout"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(
            "%s: hook %r has a timeout the harness cannot honour (%r)"
            % (origin, entry.get("command"), value)
        )
    return value


def hook_basename(command, prefix):
    """The hook filename a registered `command` names, or None if it names none.

    ONE spelling of a derivation two callers had grown independently — the same defect
    `walk_hook_triples` exists to prevent, reappearing one layer up. `test_hook_parity` took
    `command.split()[0][len(prefix):].strip('"')` and `test_hook_argv_refusal` took
    `command[len(prefix):].strip()`; they agree only while no registration carries arguments.

    Normalisation happens BEFORE the prefix test, not after. A quote-stripping step placed after
    a `startswith(prefix)` gate is inert against the case it appears to handle: a command written
    `"$HOME/.claude/scripts/x-test.sh"` fails the gate on its leading quote and leaves the
    population with nothing raised — measured.
    """
    for token in command.split():
        bare = token.strip("'\"")
        if bare.startswith(prefix):
            return bare[len(prefix) :]
    return None
