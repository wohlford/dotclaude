"""Shared `settings.json` hook walker.

The repo had TWO independent parses of this shape before this module existed:
`scripts/tests/test_hook_argv_refusal.py::registered_hooks` (silent on malformed shape — a
group or entry of the wrong type is simply skipped) and
`scripts/settings-hooks-check.py::_triples` (raises `ValueError` naming what it found instead).
Adding a checker with its own third parse would be the two-spellings defect this repo keeps
hitting, so this module is that parse, and `registered_hooks` now filters over it.

**`settings-hooks-check.py::_triples` is deliberately NOT converted, and this is the trap to read
before you "finish the job".** Making it delegate here looks like the obvious tidy-up and is
measured to be wrong: three of `scripts/tests/mutate_settings_hooks_check.py`'s `Mutation.old`
anchors are string literals inside `_triples`'s own body, so moving that body drops the campaign
from `caught=7` to `caught=4 survived=3` — a verification tool quietly losing half its teeth while
every suite stays green. Deduplicating requires re-anchoring that campaign FIRST, as its own
change. Until then the two functions are duplicates that must be kept in step by hand, and that
cost is deliberate rather than overlooked.

The raising behaviour is the one that survives. A hooks block a parser cannot read is not an
empty hooks block, and silently treating it as one would drop exactly the registrations a
consumer exists to find — see CLAUDE.md's "Clear by allowlist" and "A tool that IGNORES an
argument it cannot parse" hazards. The silent variant was the weaker of the two readings.
"""

from __future__ import annotations


def walk_hook_triples(doc, origin):
    """Flatten a settings document into a set of (event, matcher, command) triples.

    Shape errors raise ValueError rather than being skipped: a hooks block this function cannot
    read is not an empty hooks block, and silently treating it as one would drop exactly the
    registrations a caller exists to find.

    `origin` is a label for the document (a path, or a "ref:path" string) used only to make a
    raised message name where the bad shape was found.
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
                out.add((event, matcher, command))
    return out


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
