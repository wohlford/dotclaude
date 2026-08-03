#!/usr/bin/env python3
"""Mutation campaign for scripts/run-long.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_run_long.py` — and never while editing the
subject, since the restore would clobber your edits.

Every row names ONE safety property and mutates what it names. A row that goes red off some
unrelated assertion raising first is a green suite wearing a red hat, so the labels are written
to be falsifiable: if the suite survives a row, the property that row names is not being tested.

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

SUBJECT = REPO / "scripts" / "run-long.sh"
SUITE = [str(REPO / "scripts" / "tests" / "test_run_long.sh")]

MUTATIONS = [
    mutate.Mutation(
        "the no-default-output-path rule is dropped (group 5: every run becomes a writer)",
        "[[ -n \"$out\" ]] || die 'no --out given; this tool has no default output path, by design'",
        '[[ -n "$out" ]] || out="run-long.log"',
    ),
    mutate.Mutation(
        "the clobber refusal is dropped — an existing artifact is silently destroyed",
        'if [[ -e "$out" && "$force" -ne 1 ]]; then',
        "if false; then",
    ),
    mutate.Mutation(
        "the status trailer is never written — every finished run looks killed",
        'printf "%s%d\\n" "$trailer" "$rc" >> "$art"',
        'printf "" >> "$art"',
    ),
    mutate.Mutation(
        "the launcher stops exiting 0, so its status starts masquerading as a verdict",
        "printf '      Read the verdict with: run-long.sh --status %s\\n' \"$out\"\nexit 0",
        "printf '      Read the verdict with: run-long.sh --status %s\\n' \"$out\"\nexit 1",
    ),
    mutate.Mutation(
        "a DIED run reports success — the exact false-clean this tool exists to prevent",
        "  printf '        Whatever it printed is a PREFIX — absence of FAIL is not a pass.\\n'\n  exit 4",
        "  printf '        Whatever it printed is a PREFIX — absence of FAIL is not a pass.\\n'\n  exit 0",
    ),
    mutate.Mutation(
        "liveness is assumed rather than checked, so a killed run reads as RUNNING forever",
        'if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then',
        'if [[ -n "$pid" ]]; then',
    ),
    mutate.Mutation(
        "--status stops distinguishing a failed run from a successful one",
        '    [[ "$rc" == "0" ]] && exit 0\n    exit 1',
        '    [[ "$rc" == "0" ]] && exit 0\n    exit 0',
    ),
    mutate.Mutation(
        "the trailing-newline guard is dropped — the trailer fuses onto the last output line",
        'if [ -n "$(tail -c 1 "$art")" ]; then printf "\\n" >> "$art"; fi',
        ":",
    ),
    mutate.Mutation(
        "the BEGIN token leaves the header, so --status can never recover the pid",
        'printf "%s pid=%d label=%s\\n" "$begin" "$$" "$tag"',
        'printf "pid=%d label=%s\\n" "$$" "$tag"',
    ),
    mutate.Mutation(
        "stderr is no longer captured into the artifact",
        '"$@" >> "$art" 2>&1',
        '"$@" >> "$art"',
    ),
    # A row for the launch/artifact race guard was here and has been REMOVED, not silenced.
    # It survived, and probing said the guard is nonetheless load-bearing: instrumenting the
    # loop shows it spends exactly 1 iteration (~20ms) on every launch, so without it the
    # launcher returns before the header is on disk. The absence is simply not observable from
    # outside — forking a `--status` process costs far longer than the window it closes, so 40/40
    # probe runs reported RUNNING with the guard removed. The row would therefore report SURVIVED
    # forever: true, and useless.
    #
    # Note this lands the OPPOSITE way from the `chmod` no-ops deleted from mutate.py. There the
    # property held by CONSTRUCTION (write_text truncates in place), so the code was pointless.
    # Here it holds only by a timing accident that a loaded machine could break, so the guard is
    # kept and the untestable row is dropped — a flagged "untestable: timing" exception.
    mutate.Mutation(
        "the recorded command line stops being argv-faithful (%q -> flat $*)",
        'for a in "$@"; do printf " %q" "$a"; done',
        'printf " %s" "$*"',
    ),
    mutate.Mutation(
        "--label stops being recorded",
        'printf "%s pid=%d label=%s\\n" "$begin" "$$" "$tag"',
        'printf "%s pid=%d\\n" "$begin" "$$"',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
