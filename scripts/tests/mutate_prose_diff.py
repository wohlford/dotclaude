#!/usr/bin/env python3
"""Mutation campaign for scripts/prose-diff.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run on demand, and never while editing the subject — the restore would clobber your edits.

Every row names ONE safety property and mutates what it names. A row that survives is not
automatically a missing fixture: it may mean the mutated code is unreachable, or a no-op.

The repo root is derived from __file__, so invoking this through a symlinked
`~/.claude/scripts/tests/` would resolve into PRODUCTION's tree and mutate that instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "prose-diff.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    str(REPO / "scripts" / "tests" / "test_prose_diff.py"),
    "-q",
    "--no-header",
]

MUTATIONS = [
    mutate.Mutation(
        "the diff loses its ADDED direction — a smuggled-in claim reads as lossless",
        "    removed = before - after\n    added = after - before",
        "    removed = before - after\n    added = type(before)()",
    ),
    mutate.Mutation(
        "the diff loses its REMOVED direction",
        "    removed = before - after\n    added = after - before",
        "    removed = type(before)()\n    added = after - before",
    ),
    mutate.Mutation(
        "the zero-denominator guard is dropped (an empty comparison reports PASS)",
        "    if sum(before.values()) == 0:",
        "    if False:",
    ),
    mutate.Mutation(
        "a section matching NOTHING is tolerated instead of refused",
        "    if not hits:",
        "    if False:",
    ),
    mutate.Mutation(
        "an AMBIGUOUS section silently picks the first match",
        "    if len(hits) > 1:",
        "    if False:",
    ),
    mutate.Mutation(
        "a section runs past the next heading of its OWN level",
        "        if m and len(m.group(1)) <= level:",
        "        if m and len(m.group(1)) < level:",
    ),
    mutate.Mutation(
        "anchor matching stops normalizing whitespace, so a WRAPPED phrase false-FAILs",
        '    return " ".join(text.split())',
        "    return text",
    ),
    mutate.Mutation(
        "a lost anchor no longer affects the verdict",
        "    bad = n_removed > 0 or bool(lost_anchors)",
        "    bad = n_removed > 0",
    ),
    mutate.Mutation(
        "the restore-don't-shorten warning is dropped from the anchor failure",
        "    if lost_anchors:",
        "    if False:",
    ),
    # This row began as "--allow-additions leaks into WORDS mode" and SURVIVED. The survivor was
    # a design finding, not a missing fixture: the flag was documented "lines mode only" and was
    # silently IGNORED there, so no behaviour distinguished the mutant. The fix was to refuse it
    # at the boundary — which also collapsed the verdict condition to a single term — and the row
    # now aims at that guard instead.
    mutate.Mutation(
        "--allow-additions is silently accepted in words mode instead of refused",
        '    if opts["allow_additions"] and opts["mode"] != "lines":',
        "    if False:",
    ),
    mutate.Mutation(
        "--allow-additions starts excusing REMOVALS as well as additions",
        "    bad = n_removed > 0 or bool(lost_anchors)",
        "    bad = bool(lost_anchors)",
    ),
    mutate.Mutation(
        "blank lines start counting as content in lines mode",
        "    out = [ln for ln in out if ln]",
        "    out = list(out)",
    ),
    mutate.Mutation(
        "case folding is forced on, hiding a recapitalization",
        "        out.append(token.casefold() if fold else token)",
        "        out.append(token.casefold())",
    ),
    mutate.Mutation(
        "edge punctuation is no longer stripped, so repunctuation reads as a content change",
        '        token = EDGE_PUNCT.sub("", raw)',
        "        token = raw",
    ),
    # Relabelled after it survived: the suite asserted the LAST line is a verdict, which stays
    # true when a spurious earlier one is printed too. The row is unchanged; the assertion it
    # needed was "exactly one verdict line", not "the last line is one".
    mutate.Mutation(
        "a SPURIOUS second verdict line is emitted before the real one",
        '    for line in report:\n        sys.stdout.write(line + "\\n")',
        '    sys.stdout.write("RESULT: PASS rc=0 mode=x removed=0 added=0\\n")\n'
        '    for line in report:\n        sys.stdout.write(line + "\\n")',
    ),
    mutate.Mutation(
        "a FAIL verdict returns 0",
        '    status, rc = ("FAIL", 1) if bad else ("PASS", 0)',
        '    status, rc = ("FAIL", 0) if bad else ("PASS", 0)',
    ),
    mutate.Mutation(
        "a missing input file is read as empty instead of refused",
        "    if not path.is_file():",
        "    if False:",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
