#!/usr/bin/env python3
"""Mutation campaign for scripts/script-header-check.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_script_header_check.py` — and never while editing
the subject, since the restore would clobber your edits.

The subject is a fail-closed gate over a documented convention, so its failure modes run in BOTH
directions and the two are not equally bad. A clause that stops CLEARING ships a false block on a
clean file — the operator is stopped by a gate that is simply wrong. A clause that stops FIRING
ships a silently truncated row into a generated index, which is the original defect this checker
exists to detect. Every row below names one clause and mutates what it names.

`_is_wrapped` is written fire-narrow / clear-generous: seven numbered clauses each clear one shape
a false positive could take. What reaches the final `return True` is any un-separated lowercase
comment — a genuine continuation, and also a standalone lowercase idiom (`# see README.md`). That
residual is accepted, not a defect; the subject's own docstring states it. This structure makes a
per-clause campaign the only adequate test — a suite can cover the reasons
(`missing` / `out-of-window` / `wrapped`) completely while leaving individual clauses unpinned.

MEASURED, and the reason this file exists: the suite originally cleared clause 4 with a `# Usage:`
fixture. That line is CAPITALISED, so clause 6 (not a lowercase letter -> clear) already cleared it
and clause 4 never decided the row — the fixture passed for a reason other than the one it was
named for, and `key-header-clause-removed` SURVIVED. Clause 4 is not inert: it is the sole clearer
for LOWERCASE `Key:` directives (`# noqa:`, `# type:`, `# pylint:`, `# mypy:`), which after a
Purpose header on a Python file is an ordinary shape. Lowercase fixtures were added and the mutant
died. A cleared-shape fixture is only evidence for the clause that actually decides it.

The repo root is derived from __file__, which means invoking this through a symlinked
`~/.claude/scripts/tests/` would resolve into PRODUCTION's tree and mutate that instead. Run it
from the working copy you intend to grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "script-header-check.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    str(REPO / "scripts" / "tests" / "test_script_header_check.py"),
    "-q",
    "--no-header",
]

MUTATIONS = [
    # ---- the two DERIVATIONS. Both exist so the checker cannot drift from the extractor
    # it mirrors; a hand-copy passes every fixture built at the current default.
    mutate.Mutation(
        "the window is hand-copied instead of read from extractors, so it silently stops "
        "matching the extractor the moment that constant changes",
        "    window = extractors.HEADER_WINDOW_LINES",
        "    window = 10",
    ),
    mutate.Mutation(
        "the subject line becomes the FIRST in-window match rather than the LAST, so the "
        "checker grades a line the extractor does not bind",
        "    idx = None\n"
        "    for i, ln in enumerate(lines[:limit]):\n"
        "        if PURPOSE_RE.match(ln):\n"
        "            idx = i\n"
        "    return idx",
        "    for i, ln in enumerate(lines[:limit]):\n"
        "        if PURPOSE_RE.match(ln):\n"
        "            return i\n"
        "    return None",
    ),
    mutate.Mutation(
        "the window stops bounding the search, so a `# Purpose:` inside a heredoc or a string "
        "literal binds and a compliant file is graded on text the extractor never reads",
        "    idx = _find_last_match_index(lines, window)",
        "    idx = _find_last_match_index(lines, len(lines))",
    ),
    # ---- the seven clearing clauses. Each dropped clause is a FALSE BLOCK on a clean file.
    mutate.Mutation(
        "clause 1 — a Purpose line at EOF stops clearing, so the checker indexes past the end "
        "and exits 2: a clean file fails the sweep as an instrument error, worse than a false "
        "positive because it reports no offender to fix",
        "    if i + 1 >= len(lines):\n        return False",
        "    if i + 1 >= len(lines) and False:\n        return False",
    ),
    mutate.Mutation(
        "clause 2 — a non-comment next line stops clearing, so every script whose Purpose is "
        "the last comment before code is reported wrapped",
        '    if not s.startswith("#"):\n        return False',
        '    if not s.startswith("#") and False:\n        return False',
    ),
    mutate.Mutation(
        "clause 3 — the bare `#` separator stops clearing, so the remedy this checker's own "
        "message prescribes would itself be a violation",
        "    if not body:\n        return False",
        "    if not body and False:\n        return False",
    ),
    mutate.Mutation(
        "clause 4 — lowercase `Key:` directives stop clearing, so `# noqa:` or `# type:` under "
        "a Purpose header falsely reads as a continuation. Clause 6 hides this from any "
        "CAPITALISED fixture, which is how it survived the first campaign",
        '    if re.match(r"^[A-Za-z][A-Za-z0-9 _-]*:", body):',
        '    if re.match(r"^[A-Za-z][A-Za-z0-9 _-]*:", body) and False:',
    ),
    mutate.Mutation(
        "clause 5 — the no-colon tool directives stop clearing; nothing else covers "
        "`# shellcheck disable=SC2034`, which starts lowercase and has no colon before the `=`",
        "    if body.startswith(TOOL_DIRECTIVES_NO_COLON):\n        return False",
        "    if body.startswith(TOOL_DIRECTIVES_NO_COLON) and False:\n        return False",
    ),
    mutate.Mutation(
        "clause 6 — the lowercase-prose test stops clearing, so coding cookies (`# -*- coding:`) "
        "and rule dividers (`# ------`) are reported as continuations",
        '    if not ("a" <= body[0] <= "z"):',
        '    if not ("a" <= body[0] <= "z") and False:',
    ),
    mutate.Mutation(
        "clause 7 — the wrapped verdict itself stops firing, so a genuine prose continuation "
        "is never reported: the gate this file exists to ship (it caught two live violators) "
        "would never fire again",
        "    return True  # 7: otherwise, wrapped",
        "    return False  # 7: otherwise, wrapped",
    ),
    # ---- the zero-denominator guard. Its failure direction is a silent clean pass.
    mutate.Mutation(
        "an empty population reports success instead of an instrument failure — a discovery "
        "matching nothing is the loudest false clean there is",
        '            "empty, which is an instrument failure rather than a clean subject\\n"\n'
        "        )\n"
        "        return 2",
        '            "empty, which is an instrument failure rather than a clean subject\\n"\n'
        "        )\n"
        "        return 0",
    ),
    # ---- an unreadable population member must not crash uncaught. Reverting the catch is a
    # regression to rc 1 (an uncaught traceback) rather than rc 2 (a named instrument failure);
    # the fixtures added for finding 1 (a directory, a dangling symlink, chmod 000) all assert
    # rc == 2, so this mutant is CAUGHT by every one of them, not just one.
    mutate.Mutation(
        "an unreadable population member's exception goes uncaught again, so the checker "
        "exits 1 (an undiagnosed traceback) rather than 2 (a named instrument failure) — "
        "exactly the regression finding 1 exists to close",
        "        try:\n"
        "            reason = check_file(s.path, window)\n"
        "        except Exception as exc:  # an ungradeable member must not read as a clean one\n"
        "            rel = s.path.relative_to(scope)\n"
        "            sys.stderr.write(\n"
        '                f"script-header-check: cannot read {rel} ({exc.__class__.__name__}): "\n'
        '                "an unreadable population member is an instrument failure, not a "\n'
        '                "finding\\n"\n'
        "            )\n"
        "            return 2\n"
        "        if reason:",
        "        reason = check_file(s.path, window)\n        if reason:",
    ),
]


def main() -> int:
    report_path = sys.argv[1] if len(sys.argv) > 1 else None
    report = mutate.run(
        SUBJECT, SUITE, MUTATIONS, cwd=str(REPO), report_path=report_path
    )
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
