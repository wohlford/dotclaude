#!/usr/bin/env python3
"""Mutation campaign for scripts/lib/cycle_profile_privacy.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand through `scripts/run-long.sh` and never while editing the subject, since the
restore would clobber your edits:

    ./scripts/tests/mutate_cycle_profile_privacy.py

Every row removes ONE core mechanism of the privacy module and names, after `caught by`, the
`scripts/tests/test_cycle_profile_privacy.py` row that must notice. A survivor means that row
does not assert the effect the mechanism exists for: strengthen the row (a failing case first),
never reword the mutation. Each `old` string occurs exactly once in the subject. A mutant may
fail rows besides the one it cites; what the campaign requires is that no two mutants fail
exactly the same rows (`scripts/kill-sets.py` is the arbiter).

The out-dir guard has two independent halves, git's exit status and the filesystem walk, and a
filesystem-identity fallback beside the resolved-path comparison. Several rows therefore isolate
one half by replacing the other, and a mutant is chosen so it removes exactly what its row
isolates:

* `t21pa` changes the git question to one that exits 128 inside a `.git` directory and in a
  bare repository, which reads as a plain directory (the walk is replaced in that row);
* `t21pb` exempts a directory that git reports as ignored from the exit-0 refusal;
* `t21pc` compares logical rather than resolved paths; the row disables the identity fallback,
  since the fallback alone would otherwise still refuse the symlinked spellings.

Mechanisms pinned by a row but deliberately NOT mutated here, each because a single-point edit
either duplicates another mutant's kill set or removes only one of several independent guards:

* the missing-`git` and unsearchable-directory refusals (`t21pe`, `t21pl`): same effect as
  the exit-status mutants for the refusal they name, and covered by their own fixtures;
* the scrubbed git environment (`t21pg`) is killed as a side effect of the `t21pa` mutant;
  a mutant that stops scrubbing would only be seen through one of three variables;
* case-variant and symlinked-transcript protection (`t21pi`, `t21pj`) and the NUL-byte
  refusal (`t21pk`): each is also reached by the `t21pc` mutant, so a mutant of its own
  would repeat that kill set in part without adding a row;
* the 0700 creation of the out-dir (`t21ph`) and the failed-write cleanup (`t22pc`): pinned
  by their rows, left unmutated by the sizing rule of one mutant per core mechanism;
* the nothing-sampled rule (`t24pc`) and the allowlist (`t26p`), the remedy words (`t35`) and
  the non-space window threshold (`t43` second row): pinned by their rows, killed in part by
  the mutants below, and not given a mutant of their own;
* `os.fchmod` beside the creation mode in `write_output`: two independent guards on the file
  mode, so the `t22pb` mutant changes the constant both read.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "lib" / "cycle_profile_privacy.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    str(REPO / "scripts" / "tests" / "test_cycle_profile_privacy.py"),
]

_IGNORED_EXEMPT = (
    "    if status == 0 and subprocess.run(\n"
    '        ["git", "-C", str(ancestor), "check-ignore", "-q", str(ancestor)],\n'
    "        stdin=subprocess.DEVNULL,\n"
    "        stdout=subprocess.DEVNULL,\n"
    "        stderr=subprocess.DEVNULL,\n"
    "        env=_scrubbed_env(),\n"
    "        check=False,\n"
    "    ).returncode != 0:\n"
    '        raise Refusal("inside-work-tree")\n'
)

MUTATIONS = [
    # ---- the out-dir guard
    mutate.Mutation(
        "git is asked for the work tree, which exits 128 in a .git directory or a bare "
        "repository and reads as no repository - caught by t21pa",
        '"rev-parse", "--git-dir"',
        '"rev-parse", "--show-toplevel"',
    ),
    mutate.Mutation(
        "a directory git reports as ignored is exempted from the exit-0 refusal - "
        "caught by t21pb",
        '    if status == 0:\n        raise Refusal("inside-work-tree")\n',
        _IGNORED_EXEMPT,
    ),
    mutate.Mutation(
        "paths are made absolute but not resolved before they are compared - "
        "caught by t21pc",
        "return Path(os.path.realpath(spelled))",
        "return Path(os.path.abspath(spelled))",
    ),
    mutate.Mutation(
        "the marker rule is dropped, so a non-empty directory without the marker is accepted - "
        "caught by t21pd",
        "if not marked and any(True for _ in os.scandir(resolved)):",
        "if False and any(True for _ in os.scandir(resolved)):",
    ),
    mutate.Mutation(
        "the filesystem walk is dropped, so only git's exit status can refuse - "
        "caught by t21pf",
        '    if _walk_finds_repository(resolved):\n        raise Refusal("inside-work-tree")\n',
        '    if False:\n        raise Refusal("inside-work-tree")\n',
    ),
    # ---- the writer
    mutate.Mutation(
        "write_output accepts any file name - caught by t22pa",
        "if name not in OUTPUT_NAMES:",
        "if False:",
    ),
    mutate.Mutation(
        "the output file mode is 0644 instead of 0600 - caught by t22pb",
        "FILE_MODE = 0o600",
        "FILE_MODE = 0o644",
    ),
    # ---- the leak check and the sampler
    mutate.Mutation(
        "the matcher no longer looks for the JSON-escaped form of a window - caught by t25",
        "if window in hay or escaped in hay:",
        "if window in hay:",
    ),
    mutate.Mutation(
        "the planted control is skipped and always reported detected - caught by t24pa",
        "    found = find(PLANTED_RAW, [planted_report, planted_data]) and find(\n"
        "        PLANTED_ESCAPED, [planted_report, planted_data]\n"
        "    )\n",
        "    found = True\n",
    ),
    mutate.Mutation(
        "the sample floor is dropped to one window - caught by t24pb",
        "len(windows) >= max(floor, 1)",
        "len(windows) >= 1",
    ),
    mutate.Mutation(
        "the window offset is fixed at 0, so only prefixes are probed - caught by t43",
        "start = rng.randrange(0, span)",
        "start = 0",
    ),
    mutate.Mutation(
        "the delegate re-read ignores the recorded size and reads the whole file - "
        "caught by t34p",
        "data = handle.read(bytes_read)",
        "data = handle.read()",
    ),
]


def main() -> int:
    report_path = sys.argv[1] if len(sys.argv) > 1 else None
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO, report_path=report_path)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
