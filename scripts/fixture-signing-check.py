#!/usr/bin/env python3
# Script: fixture-signing-check.py
# Purpose: Flag test fixtures whose git repos can inherit the operator's global signing config
# Usage: fixture-signing-check.py [--scope <repo>] [--list]
"""Fail when a test fixture repo can inherit the operator's global git signing config.

A fixture that runs `git init` — or `git clone` — inherits `commit.gpgsign` / `tag.gpgsign`
from the operator's global config. Where those are backed by a hardware key and no TTY is
available, the signing operation BLOCKS FOREVER rather than failing: no teardown, no verdict,
nothing in the output to point at. So every test file that creates a repo must disable BOTH.

Both, not just the one it uses today: only `commit` and `tag -a` sign, but which of those a
file performs is one edit away from changing, and telling a real execution from a
guard-tokenizer input string is exactly the inference this checker exists to avoid making.
Over-reporting costs one redundant config line; under-reporting costs a silent hang.

THIS IS A TRIPWIRE, NOT A GATE. Known limitations, all in the same class — it reads text, not
dataflow:
  1. File-scoped, not per-repo: a file building two fixture repos and disabling both keys on
     only one still passes.
  2. A multi-line shell invocation whose `init` sits on a continuation line is not counted.
  3. The import hop asks whether disabling text exists in the file or an imported sibling, not
     whether the creating call routes through it. A file importing a helper only for path
     constants, while creating its repo with bare subprocess.run, is blessed while exposed.
  4. A comment or docstring satisfies the requirement.
Closing 1, 3 or 4 needs per-repo dataflow analysis, which is the instrument class this design
deliberately rejected.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# A file "creates a repo" if it runs git init/clone in one of the three shapes this repo uses.
#
# All three alternatives are load-bearing and were arrived at by measurement — do not collapse
# them. Keying on a bare "init" token over-reports: it matches a config KEY ({"init": {...}})
# and `init` as a subcommand of a non-git script. Requiring the literal `git` adjacent to the
# verb under-reports, the direction that costs a silent hang: several files create repos via a
# helper — git(root, "init", ...) / _git(repo, "clone", ...) — where `git` is never an argument.
# Each bound below is deliberately LOOSE. Under-reporting costs a hang nobody can diagnose;
# over-reporting costs one redundant config line. Three measured under-reports drove the current
# shapes: `-c` written before `-C` (the option groups must be order-independent), and a `]` or `)`
# inside an argument — `str(paths[0])`, `git(str(root), "init")` — which a negated-class bound
# cannot cross. The bounded `[\s\S]{0,200}?` crosses them and caps the span instead.
# `_OPTS` must have exactly ONE parse per iteration. An earlier form spelled the value as
# `-c\s+\S+=\S+\s+`, where the first `\S+` may stop at ANY `=` in the token — k parses per
# iteration, k**n over n iterations, with no memoisation in `re`. Measured on this machine:
# 0.05s / 0.48s / 4.4s / 31s at n=5..8, from a single crafted line. That is not a slow check but
# a NON-TERMINATING one, and an untracked file is enough to trigger it, since the population
# includes `--others`. A checker that neither passes nor fails is the exact defect this whole
# change exists to remove — rebuilt inside the instrument meant to police it. Matching `-c`
# and `-C` identically is strictly wider and unambiguous: `\S+` can only end at whitespace.
_VERB = r"(?:init|clone)"
_OPTS = r"(?:-[Cc]\s+\S+\s+)*"
CREATES = re.compile(
    rf"""git\s+{_OPTS}{_VERB}\b"""  # shell
    rf"""|["']git["']\s*,(?:[\s\S]{{0,200}}?,)?\s*["']{_VERB}["']"""  # python literal argv
    rf"""|(?<![\w.])_?git\s*\([\s\S]{{0,200}}?["']{_VERB}["']"""  # python helper call
)
DISABLED = {
    "commit.gpgsign": re.compile(r"""commit\.gpgsign["'\s=,]+false""", re.I),
    "tag.gpgsign": re.compile(r"""tag\.gpgsign["'\s=,]+false""", re.I),
}
IMPORTS = re.compile(r"^\s*(?:from\s+(\w+)\s+import|import\s+(\w+))", re.M)
TESTISH = re.compile(r"(^|/)(tests?/|test_|conftest|mutate_|\w+_helpers\.py$)")
EXCLUDE = re.compile(r"scripts/tests/fixtures/prechange/")


def candidates(scope: Path):
    """Test files in the WORKING TREE — tracked plus untracked-and-unignored.

    Not `git ls-files` alone: a brand-new violating fixture is the likeliest offender and is
    invisible to the index until `git add`, so a tracked-only population would bless the tree
    at exactly the moment nothing has ever checked it. `--exclude-standard` keeps ignored
    build artefacts out, so this never grades content no commit will contain.

    `-z` with a NUL split, never plain `.split()`: whitespace-splitting drops a path containing a
    space into two tokens, neither of which survives the suffix and TESTISH filters, so the
    violator lands in neither the denominator nor the violations and the run reads clean. `-z`
    also bypasses `core.quotePath`, which would otherwise wrap a non-ASCII path in quotes.
    """
    out = subprocess.run(
        [
            "git",
            "-C",
            str(scope),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    seen, rels = set(), []
    for f in out:
        if not f:
            continue
        if f in seen or not f.endswith((".sh", ".py")):
            continue
        if TESTISH.search(f) and not EXCLUDE.search(f):
            seen.add(f)
            rels.append(f)
    return rels


def reachable_text(scope: Path, rel: str) -> str:
    """The file's text, plus any same-directory module it imports.

    Four recast modules disable both keys via `recast_helpers.git()` and carry no `gpgsign`
    text of their own; resolving that one hop keeps them from being reported as violations
    they are not. A sibling that cannot be read is skipped rather than fatal — the file's own
    text still decides, so the worst case is a false violation, which fails loud.
    """
    p = scope / rel
    text = p.read_text(errors="replace")
    if not rel.endswith(".py"):
        return text  # no shell suite in this repo sources a shared harness
    for m in IMPORTS.finditer(text):
        sib = p.parent / f"{m.group(1) or m.group(2)}.py"
        try:
            if sib.is_file():
                text += "\n" + sib.read_text(errors="replace")
        except OSError:
            continue
    return text


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Check test fixture repos for inherited signing."
    )
    ap.add_argument("--scope", default=".", help="repo root to check (default: cwd)")
    ap.add_argument(
        "--list",
        action="store_true",
        help="print the repo-creating files found, one per line, and exit 0",
    )
    args = ap.parse_args()
    scope = Path(args.scope).resolve()

    creators, violations = [], []
    for rel in candidates(scope):
        try:
            raw = (scope / rel).read_text(errors="replace")
        except OSError as exc:
            # Unreadable is a FAIL, never a pass: a verdict we cannot ground is not a clean one.
            violations.append((rel, [f"unreadable: {exc}"]))
            continue
        if not CREATES.search(raw):
            continue
        creators.append(rel)
        missing = [
            k for k, rx in DISABLED.items() if not rx.search(reachable_text(scope, rel))
        ]
        if missing:
            violations.append((rel, missing))

    if args.list:
        print("\n".join(creators))
        return 0

    for rel, missing in violations:
        print(f"violation: {rel} — missing {', '.join(missing)}")
    print(
        f"fixture-signing: {len(creators)} file(s) create a git repo, "
        f"{len(violations)} violation(s)"
    )

    if not creators:
        # Zero is the limiting case of a discovery that reports success loudest of all.
        print(
            "fixture-signing: ERROR — no repo-creating test files found; the scan is broken",
            file=sys.stderr,
        )
        return 2
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
