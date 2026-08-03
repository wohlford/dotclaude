#!/usr/bin/env python3
"""Prove a restructuring moved content without changing it.

# Script: prose-diff.py
# Purpose: Verify a restructuring is lossless by diffing word or line multisets in both directions
# Usage: prose-diff.py BEFORE AFTER [--section H] [--mode words|lines] [--anchor TEXT]...

Hand-rolled five times before this existed — three times on prose review, once as a
complement-identity check over enumerated spans, and once (outside prose entirely) as a `Counter`
over non-empty lines proving three entries moved between `## Open` and `## Closed` in a 1179-line
backlog without altering them. That fifth instance is why this ships with TWO modes rather than
the one the deferral prescribed.

## The two modes answer different questions — picking the wrong one is the failure

* `--mode words` (default) — a reflow may rewrap freely, but no WORD may vanish or appear.
  Use it for rewrites: compressing a bullet, merging two paragraphs, promoting a clause.
* `--mode lines` — lines may be REORDERED, but none may change. Use it for moves: an entry
  travelling between sections, a table regenerated, a list resorted. `--allow-additions` relaxes
  it to CLAUDE.md's other prescription, insert-only.

A rewrap passes `words` and fails `lines`; that is not a bug in either, it is the distinction.

## Why a multiset, and not an anchor list — measured

The first attempt at this was a 20-substring anchor list, and it was **insufficient**: it pinned
the claims and the evidence but almost none of the PRESCRIPTIONS ("record the rc *inside* the
artifact", "ship them together"), which sit exactly at the connective seams a reorganization
licenses to reword. The multiset caught what the anchors could not, because it needs no foresight
about which phrases matter. Anchors remain available as a cheap backstop via `--anchor`.

**When an anchor fails, restore the line — never shorten the anchor.** A legitimate rewrap can
false-FAIL an anchor, and the tempting repair (trim the anchor until it matches) silently deletes
the check. This tool says so in the failure text because that is the only place it will be read.

## The zero-denominator guard

`--section` that matches nothing extracts nothing on BOTH sides, and two empty multisets compare
equal — a triumphant PASS over no content at all. Every such case is an ERROR here: a section
missing, ambiguous, or empty on either side, and a comparison whose before-side has no units.
"""

from __future__ import annotations

import collections
import re
import subprocess
import sys
from pathlib import Path

USAGE = """Usage: prose-diff.py BEFORE AFTER [options]
       prose-diff.py --git REV FILE [options]

Prove a restructuring did not change content, by comparing multisets in BOTH directions.

Options:
  --section HEADING   scope to one markdown section (its heading through the next heading
                      of the same or a shallower level; deeper subsections are included)
  --mode words|lines  words (default): rewrapping is fine, a lost word is not
                      lines: reordering is fine, an altered line is not
  --allow-additions   lines mode only — permit added lines (an insert-only edit)
  --anchor TEXT       phrase that must survive (repeatable; whitespace-normalized)
  --ignore-case       fold case, for a clause promoted to a bullet
  --git REV           read the BEFORE side from `git show REV:FILE`
  -h, --help          show this help

Exit: 0 lossless, 1 a difference was found, 2 usage error or nothing to compare.
"""

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
EDGE_PUNCT = re.compile(r"^\W+|\W+$", re.UNICODE)


def die(message: str) -> None:
    sys.stdout.write(USAGE)
    sys.stdout.write(f"\nERROR  {message}\n")
    sys.stdout.write("RESULT: ERROR rc=2\n")
    sys.exit(2)


def normalize(text: str) -> str:
    """Collapse all whitespace, so a phrase that WRAPS still matches. This bit twice."""
    return " ".join(text.split())


def word_counts(text: str, fold: bool) -> collections.Counter:
    out: list[str] = []
    for raw in text.split():
        token = EDGE_PUNCT.sub("", raw)
        if not token:
            continue
        out.append(token.casefold() if fold else token)
    return collections.Counter(out)


def line_counts(text: str, fold: bool) -> collections.Counter:
    out = [ln.strip() for ln in text.split("\n")]
    out = [ln for ln in out if ln]
    if fold:
        out = [ln.casefold() for ln in out]
    return collections.Counter(out)


def extract_section(text: str, name: str, side: str) -> str:
    """Return one named section, or ERROR. Never returns an empty or ambiguous match."""
    lines = text.split("\n")
    hits = []
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m and m.group(2).strip() == name:
            hits.append((i, len(m.group(1))))

    if not hits:
        die(
            f"section {name!r} not found in the {side} side — nothing would be compared"
        )
    if len(hits) > 1:
        die(
            f"section {name!r} matches {len(hits)} headings in the {side} side; refusing to guess"
        )

    start, level = hits[0]
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = HEADING.match(lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    return "\n".join(lines[start:end])


def read_side(path: Path, label: str) -> str:
    if not path.is_file():
        die(f"no such file: {path} ({label} side)")
    return path.read_text()


def read_git(rev: str, rel: str) -> str:
    proc = subprocess.run(
        ["git", "show", f"{rev}:{rel}"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        die(f"git show {rev}:{rel} failed — {proc.stderr.strip()}")
    return proc.stdout


def parse_argv(argv):
    opts = {
        "paths": [],
        "section": None,
        "mode": "words",
        "allow_additions": False,
        "anchors": [],
        "ignore_case": False,
        "git": None,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            sys.stdout.write(USAGE)
            sys.exit(0)
        elif arg == "--section":
            if i + 1 >= len(argv):
                die("--section needs a heading")
            opts["section"] = argv[i + 1]
            i += 2
        elif arg == "--mode":
            if i + 1 >= len(argv) or argv[i + 1] not in ("words", "lines"):
                die("--mode needs 'words' or 'lines'")
            opts["mode"] = argv[i + 1]
            i += 2
        elif arg == "--anchor":
            if i + 1 >= len(argv):
                die("--anchor needs a phrase")
            opts["anchors"].append(argv[i + 1])
            i += 2
        elif arg == "--git":
            if i + 1 >= len(argv):
                die("--git needs a revision")
            opts["git"] = argv[i + 1]
            i += 2
        elif arg == "--allow-additions":
            opts["allow_additions"] = True
            i += 1
        elif arg == "--ignore-case":
            opts["ignore_case"] = True
            i += 1
        elif arg.startswith("-"):
            die(f"unrecognized option: {arg}")
        else:
            opts["paths"].append(arg)
            i += 1
    return opts


def main(argv) -> int:
    if not argv:
        die("no arguments")
    opts = parse_argv(argv)

    # Validate the flag against the handler that would consume it. A flag that does not apply to
    # the chosen mode must be REFUSED, never silently dropped — this repo has measured a
    # documented directive being ignored by six of seven handlers because nothing checked.
    # Refusing here is also what lets the verdict logic below stay a single condition.
    if opts["allow_additions"] and opts["mode"] != "lines":
        die(
            f"--allow-additions applies to --mode lines only, not {opts['mode']!r}; "
            "refusing rather than ignoring it"
        )

    if opts["git"] is not None:
        if len(opts["paths"]) != 1:
            die("--git REV takes exactly one FILE")
        rel = opts["paths"][0]
        before_text = read_git(opts["git"], rel)
        after_text = read_side(Path(rel), "after")
    else:
        if len(opts["paths"]) != 2:
            die("expected BEFORE and AFTER paths")
        before_text = read_side(Path(opts["paths"][0]), "before")
        after_text = read_side(Path(opts["paths"][1]), "after")

    if opts["section"] is not None:
        before_text = extract_section(before_text, opts["section"], "before")
        after_text = extract_section(after_text, opts["section"], "after")

    fold = opts["ignore_case"]
    counter = word_counts if opts["mode"] == "words" else line_counts
    before = counter(before_text, fold)
    after = counter(after_text, fold)

    # Zero denominator: a comparison with nothing on the before side proves nothing, and reports
    # success loudest of all. Refuse it rather than blessing it.
    if sum(before.values()) == 0:
        die(
            f"the before side has no {opts['mode']} to compare "
            "— an empty comparison is not a clean one"
        )

    removed = before - after
    added = after - before

    report: list[str] = []
    unit = opts["mode"][:-1]
    for token, n in sorted(removed.items()):
        report.append(f"REMOVED  ({n}x) {token}")
    for token, n in sorted(added.items()):
        report.append(f"ADDED    ({n}x) {token}")

    haystack = normalize(after_text)
    if fold:
        haystack = haystack.casefold()
    lost_anchors = []
    for anchor in opts["anchors"]:
        needle = normalize(anchor)
        if fold:
            needle = needle.casefold()
        if needle not in haystack:
            lost_anchors.append(anchor)
            report.append(f"ANCHOR   lost: {anchor}")

    if lost_anchors:
        report.append(
            "         An anchor can false-FAIL on a legitimate rewrap. The repair is to "
            "RESTORE the line — never to shorten the anchor, which deletes the check."
        )

    n_removed = sum(removed.values())
    n_added = sum(added.values())
    bad = n_removed > 0 or bool(lost_anchors)
    if not opts["allow_additions"]:  # guarded above to lines mode only
        bad = bad or n_added > 0

    for line in report:
        sys.stdout.write(line + "\n")

    status, rc = ("FAIL", 1) if bad else ("PASS", 0)
    sys.stdout.write(
        f"RESULT: {status} rc={rc} mode={opts['mode']} "
        f"removed={n_removed} added={n_added} "
        f"anchors={len(opts['anchors']) - len(lost_anchors)}/{len(opts['anchors'])} "
        f"{unit}s={sum(before.values())}\n"
    )
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
