#!/usr/bin/env python3
# Script: memory-index-check.py
# Purpose: PostToolUse hook — flag a memory-index entry, or the whole file, grown too large
"""PostToolUse hook — flag an over-long entry, or an over-long whole file, in a memory index.

Called by Claude Code hooks with the tool-call JSON on stdin. When the edited file is a projects
memory index, each entry — a `- [` bullet PLUS its indented continuation lines — is measured in raw
UTF-8 bytes; any entry over the cap is reported on exit 2 so Claude moves the detail into the topic
file the entry points at.

Why per-entry bytes: the aggregate size warning that caught this in practice fires near the read
limit and names the symptom ("the file is big"), not the cause. The cause was one pointer entry grown
to 4,052 B — 19% of the file — by successive sessions appending summaries onto it. This fires at the
edit that grows it.

Why BLOCKS rather than physical lines: a per-line cap is gameable in the worst direction. The
cheapest way to silence it is to hard-wrap the line, which defeats the guard permanently while
reading as fixed. The remediation message therefore leads with moving detail out, never with wrapping.

Calibration (measured on the live 48-entry index): mean entry 241 B, max healthy entry 459 B, the
defect 4,052 B. The cap below gives 2.2x headroom over the healthy max and catches a runaway 4x
earlier than the aggregate warning did. A cap of 400 was considered and rejected: it fires on 5 of
the 48 healthy entries, i.e. it would ship red. Should the corpus max ever exceed ~700 B the headroom
is gone and this number owes a re-measure — that sentence is advisory; nothing fires on it.

Scope is a tail-anchored path match, not a basename test: this is a global always-on hook, and a bare
basename match would scold unrelated repos whose MEMORY.md never adopted this convention. It
therefore does NOT cover a memory index kept anywhere else; none exists today.

Scope is also content-gated, not path-anchored alone: the path predicate above matches every
project index at that tail, most of which never adopted the cap calibrated here. Requiring the
file's OWN TEXT to declare the rule (`OPT_IN_NEEDLE`, matched case-insensitively) makes the checker
enforce exactly the rule the file itself claims — the same sentence its own error message already
asserts — and is self-updating: a project opts in by stating the rule in its own header, and needs
no allowlist to maintain. The needle now gates BOTH checks below — the per-entry cap and the
whole-file aggregate cap — not just the first: a project that has not declared the rule is exempt
from the aggregate too, on the same reasoning. Measured on this machine: another project's index
(wohlford-court) sits at 21,555 B with no opt-in declared; the aggregate check must not fire there
on propagation. State plainly what this stops matching: any index that has not declared the rule,
including one whose entries — or whose total size — are genuinely over-long. That is intended — an
undeclared file has not opted in.

Whole-file aggregate cap (`AGG_MAX_BYTES`): tracks the READING HARNESS's own limit, which it
reports as "24.4KB" — a different number from the per-entry cap above, and a different failure
mode. 25000 / 1024 = 24.41, which is ambiguous between 25,000 and 24,985 bytes as the harness's
true byte cutoff, so this deliberately does NOT try to mirror that report exactly. Instead pick a
round number with headroom and let the asymmetry decide which way to round: a cap set too LOW
costs a false alarm on a file the operator can simply shorten; a cap set too HIGH costs SILENT,
UNDETECTABLE loss — entries past the harness's own cut are dropped with no signal at all, which is
the exact defect this check exists to catch. So err low.

The aggregate check is measured over RAW FILE BYTES (`len(content)`), never the sum of
`entry_blocks()` sizes — an implementation that sums entry blocks under-counts by whatever
scaffolding (headers, blank lines, prose paragraphs) sits outside any `- [` block, and would read
entirely plausible while silently missing exactly the content most likely to push a file over the
harness's real limit.

The aggregate comparison is `>=`, not `>` — this deliberately diverges from the per-entry check's
strict `>` above. Two reasons: the err-low asymmetry favours catching the boundary itself rather
than waiting one byte past it, and a `>` convention here would make an exactly-at-threshold file
pass with no aggregate logic at all, which is not what "err low" means. Do not "fix" this to match
the per-entry check's `>`; the mismatch between the two is intentional, not drift.

Known leniencies (false negatives, by choice): a MEMORY.md that is itself a symlink resolves away
from the tail and is declined; CRLF files retain \\r, shifting the boundary 1 B per line; counts
exclude the trailing newline that `wc -c` includes.

Any internal error fails open (exit 0) but prints one line first — otherwise "correctly declined" and
"crashed" are indistinguishable and every negative assertion passes for the wrong reason.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_ENTRY_BYTES = 1000
REPORT_LIMIT = 5
SCOPE_PATTERN = "projects/*/memory/MEMORY.md"
RULE = "detail lives in the topic file — never here"
OPT_IN_NEEDLE = b"detail lives in the topic file"

# See the "Whole-file aggregate cap" docstring paragraph above for why 20000 (not a mirror of
# the harness's reported "24.4KB"), why raw file bytes (not summed entry_blocks()), and why the
# comparison below is >= rather than the per-entry check's >. Do not touch MAX_ENTRY_BYTES above
# to "harmonise" it with this — it is a different check, calibrated against a different limit,
# and belongs to a different backlog entry.
AGG_MAX_BYTES = 20000
TOP_N_BY_SIZE = 3


def read_file_path() -> str | None:
    """Parse the edited file's path from the tool-call JSON on stdin."""
    # HOOK CONTRACT: the target arrives as a JSON payload on stdin; argv is ignored. Refuse the
    # two invocations this cannot serve, because each otherwise reads as SUCCESS — with argv and
    # stdin at EOF it exits 0 having examined nothing, and with a terminal stdin it blocks
    # forever. See scripts/HOOKS.md for the payload form.
    if len(sys.argv) > 1 or sys.stdin.isatty():
        sys.stderr.write(
            "memory-index-check.py is a Claude Code hook: it reads a JSON payload on stdin and\n"
            "ignores arguments. Running it with filenames examines nothing — see scripts/HOOKS.md.\n"
        )
        sys.exit(2)
    try:
        data = json.load(sys.stdin)
    except Exception:
        return None
    file_path = (data.get("tool_input") or {}).get("file_path")
    if isinstance(file_path, str) and file_path:
        return file_path
    return None


def entry_blocks(raw: bytes) -> list[tuple[int, int, bytes]]:
    """Group each `- [` bullet with its indented continuations: (lineno, byte_len, head)."""
    lines = raw.split(b"\n")
    blocks: list[tuple[int, int, bytes]] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith(b"- ["):
            i += 1
            continue
        start, size = i, len(lines[i])
        i += 1
        while i < len(lines):
            nxt = lines[i]
            # The terminator must be symmetric with the head test above (b"- [", not the bare
            # b"- "): a bullet-less "- " line is cheaper than hard-wrapping as an evasion — drop
            # the bracket and the old asymmetric test let it contribute zero bytes at any length.
            if not nxt.strip() or nxt.startswith(b"- [") or nxt.startswith(b"#"):
                break
            size += 1 + len(nxt)  # +1 for the newline that joins them
            i += 1
        blocks.append((start + 1, size, lines[start]))
    return blocks


def main() -> int:
    """Hook entry point: 0 allows the edit; 2 reports offenders on stderr."""
    file_path = read_file_path()
    if not file_path or not file_path.endswith("MEMORY.md"):
        return (
            0  # cheapest guard first: no filesystem call for the overwhelming majority
        )
    abs_file = Path(file_path).resolve()
    if not abs_file.match(SCOPE_PATTERN) or not abs_file.is_file():
        return 0
    content = abs_file.read_bytes()
    if OPT_IN_NEEDLE not in content.lower():
        return 0
    blocks = entry_blocks(content)
    offenders = [b for b in blocks if b[1] > MAX_ENTRY_BYTES]
    total_bytes = len(content)
    agg_breach = total_bytes >= AGG_MAX_BYTES

    if not offenders and not agg_breach:
        return 0

    # Both checks can breach on the same file at once (a runaway entry inside a runaway file).
    # Decision: report BOTH sections rather than letting one suppress the other. An
    # aggregate-only exit that dropped the per-entry offender list would hide the actionable
    # half — the per-entry list names exactly which block to shrink first; the aggregate
    # section alone only proves the file is too big, not where the bytes are concentrated.
    if offenders:
        print(
            f"memory-index-check: {len(offenders)} entry/entries over {MAX_ENTRY_BYTES}B in "
            f"{abs_file} — this index's own header rule is: {RULE}.",
            file=sys.stderr,
        )
        for lineno, size, head in offenders[:REPORT_LIMIT]:
            excerpt = head.decode("utf-8", errors="replace")[:70]
            print(f"  line {lineno}: {size}B — {excerpt}…", file=sys.stderr)
        if len(offenders) > REPORT_LIMIT:
            print(f"  … and {len(offenders) - REPORT_LIMIT} more", file=sys.stderr)
        print(
            "  Move the detail into the topic file each entry points at; leave a one-line hook.\n"
            "  Do NOT wrap the line — that hides the entry from this check without shrinking it.",
            file=sys.stderr,
        )

    if agg_breach:
        print(
            f"memory-index-check: {abs_file} is {total_bytes}B, at or over the "
            f"{AGG_MAX_BYTES}B aggregate threshold — entries past the reading harness's own "
            f'cut are ALREADY invisible, not "will be" once it grows further.',
            file=sys.stderr,
        )
        # Named by size, not by offender status: agg-small-entries proves a file can breach
        # the aggregate with every entry individually under MAX_ENTRY_BYTES, so the per-entry
        # offender list can be empty here. Without naming a target, a breach invites trimming
        # whatever was just edited rather than the entries actually holding the most bytes.
        by_size = sorted(blocks, key=lambda b: b[1], reverse=True)[:TOP_N_BY_SIZE]
        if by_size:
            print("  Largest entries by bytes:", file=sys.stderr)
            for lineno, size, head in by_size:
                excerpt = head.decode("utf-8", errors="replace")[:70]
                print(f"    line {lineno}: {size}B — {excerpt}…", file=sys.stderr)
        print(
            "  Move detail out of the largest entries into their topic files, or archive "
            "closed items; shortening the file is the only fix — do not narrow this check.",
            file=sys.stderr,
        )

    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise  # the argv/TTY refusal must not be swallowed by the fail-open below
    except Exception as exc:  # fail open: a checker bug must never block edits
        print(
            f"memory-index-check: internal error (fail-open): {exc!r}", file=sys.stderr
        )
        sys.exit(0)
