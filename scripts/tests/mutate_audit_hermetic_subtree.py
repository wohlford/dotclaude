#!/usr/bin/env python3
"""Mutation-test the hermetic-outside skill-sync subtree exemption in skills/audit/audit.sh.

Run on demand: `python3 scripts/tests/mutate_audit_hermetic_subtree.py`. Deliberately NOT named
`test_*`, so pytest never collects it — it mutates a tracked file in place (restored in a `finally`).

The exemption narrows a fail-closed pollution detector. Every row removes one of the properties that
keep it narrow, and the suite must notice: an exemption that reaches one directory too far turns a
real outside write into a clean PASS, which is the one outcome this check exists to prevent.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "skills" / "audit" / "audit.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_audit.sh")]

MUTATIONS = [
    mutate.Mutation(
        "the filter keeps every path, so the exemption does nothing and the sync write FAILs again",
        '        $0 != e && index($0, e "/") != 1\' <<<"$out")" || agg=1',
        '        1\' <<<"$out")" || agg=1',
    ),
    mutate.Mutation(
        "the prefix loses its `/` boundary, so skills/synced-other is swallowed too",
        '        $0 != e && index($0, e "/") != 1\' <<<"$out")" || agg=1',
        '        index($0, e) != 1\' <<<"$out")" || agg=1',
    ),
    mutate.Mutation(
        "the exemption is no longer scoped to its own top-level entry, so logs/synced leaves the watch",
        '      [[ "${s%%/*}" == "${p##*/}" ]] || continue',
        "      :",
    ),
    mutate.Mutation(
        "a wide exemption is never refused — `skills` itself could leave the watch unseen",
        '  floor_bad="$(hermetic_subtree_bad)"',
        '  floor_bad=""',
    ),
    mutate.Mutation(
        "glob characters are allowed in an exemption",
        "      /*|*/|*//*|*'*'*|*'?'*|*'['*|.|..|./*|../*|*/.|*/..|*/./*|*/../*)",
        "      /*|*/|*//*|.|..|./*|../*|*/.|*/..|*/./*|*/../*)",
    ),
    mutate.Mutation(
        "a single-segment exemption is allowed",
        '    [[ "$s" == */* ]] || printf \'%s\\n\' "$s"',
        "    :",
    ),
    mutate.Mutation(
        "a failed find walk is hidden behind the filter's status",
        '    out="$(find -L "$p" -type f "$@" 2>/dev/null)" || agg=1',
        '    out="$(find -L "$p" -type f "$@" 2>/dev/null)"',
    ),
    mutate.Mutation(
        "the prefix is passed with awk -v, which rewrites a backslash in the root path",
        '      out="$(HERMETIC_EXEMPT="$p/${s#*/}" awk \'BEGIN { e = ENVIRON["HERMETIC_EXEMPT"] }',
        '      out="$(awk -v e="$p/${s#*/}" \'BEGIN { }',
    ),
    mutate.Mutation(
        "a failed awk filter empties a root on both sides and reads as a clean PASS",
        '        $0 != e && index($0, e "/") != 1\' <<<"$out")" || agg=1',
        '        $0 != e && index($0, e "/") != 1\' <<<"$out")"',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
