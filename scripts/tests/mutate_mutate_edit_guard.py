#!/usr/bin/env python3
"""Mutation campaign for scripts/mutate-edit-guard.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run on demand, and never while editing the subject. Live sessions are unaffected by the
mutants: `~/.claude/scripts` resolves to the PRODUCTION clone's copy, not this one.

The repo root is derived from __file__, so invoking this through a symlinked
`~/.claude/scripts/tests/` would resolve into PRODUCTION's tree and mutate that instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "mutate-edit-guard.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    str(REPO / "scripts" / "tests" / "test_mutate_edit_guard.py"),
    "-q",
    "--no-header",
]

MUTATIONS = [
    mutate.Mutation(
        "the realpath candidate is dropped (an edit through a link misses the sidecar)",
        "    for spelling in (target, os.path.realpath(target)):",
        "    for spelling in (target,):",
    ),
    mutate.Mutation(
        "the as-given candidate is dropped (a legacy sidecar beside a link is missed)",
        "    for spelling in (target, os.path.realpath(target)):",
        "    for spelling in (os.path.realpath(target),):",
    ),
    mutate.Mutation(
        "an owned file is allowed (the gate never blocks)",
        "    try:\n"
        "        sys.stderr.write(message)\n"
        "        sys.stderr.flush()\n"
        "    except Exception:\n"
        "        sys.stderr = None  # nothing left to flush at exit, so the exit code stays 2 (a failing shutdown flush exits 120)\n"
        "    return 2",
        "    try:\n"
        "        sys.stderr.write(message)\n"
        "        sys.stderr.flush()\n"
        "    except Exception:\n"
        "        sys.stderr = None  # nothing left to flush at exit, so the exit code stays 2 (a failing shutdown flush exits 120)\n"
        "    return 0",
    ),
    mutate.Mutation(
        "NotebookEdit's notebook_path is ignored",
        '_TARGET_KEYS = ("file_path", "notebook_path")',
        '_TARGET_KEYS = ("file_path",)',
    ),
    mutate.Mutation(
        "a relative path is not joined to the payload cwd",
        "        return os.path.join(cwd, value)",
        "        return None",
    ),
    mutate.Mutation(
        "a relative path with no payload cwd is judged against the process cwd",
        "    if isinstance(cwd, str) and os.path.isabs(cwd):\n"
        "        return os.path.join(cwd, value)\n"
        "    return None",
        "    if isinstance(cwd, str) and os.path.isabs(cwd):\n"
        "        return os.path.join(cwd, value)\n"
        "    return os.path.join(os.getcwd(), value)",
    ),
    mutate.Mutation(
        "a ~ path is not expanded (joined to cwd instead, so the wrong file is judged)",
        "    value = os.path.expanduser(value)\n",
        "",
    ),
    mutate.Mutation(
        "a closed stdin crashes the refusal guard with a traceback",
        "    if len(argv) > 1 or (stdin is not None and stdin.isatty()):",
        "    if len(argv) > 1 or stdin.isatty():",
    ),
    mutate.Mutation(
        "the printed recovery paths are not shell-quoted",
        "    q_side, q_subj = shlex.quote(sidecar), shlex.quote(subject)",
        "    q_side, q_subj = sidecar, subject",
    ),
    mutate.Mutation(
        "an internal error fails CLOSED (wedges every edit everywhere)",
        "    except Exception:\n"
        "        # Fail open: a broken guard must never wedge every edit everywhere.\n"
        "        return 0",
        "    except Exception:\n"
        "        # Fail open: a broken guard must never wedge every edit everywhere.\n"
        "        return 2",
    ),
    mutate.Mutation(
        "an unwritable stderr turns a confirmed block into an allow",
        "    except Exception:\n"
        "        sys.stderr = None  # nothing left to flush at exit, so the exit code stays 2 (a failing shutdown flush exits 120)",
        "    except ValueError:\n"
        "        sys.stderr = None  # nothing left to flush at exit, so the exit code stays 2 (a failing shutdown flush exits 120)",
    ),
    mutate.Mutation(
        "the stderr is left in place, so the shutdown flush exits 120",
        "    except Exception:\n"
        "        sys.stderr = None  # nothing left to flush at exit, so the exit code stays 2 (a failing shutdown flush exits 120)",
        "    except Exception:\n"
        "        pass  # nothing left to flush at exit, so the exit code stays 2 (a failing shutdown flush exits 120)",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
