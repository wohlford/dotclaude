#!/usr/bin/env python3
"""Mutation campaign for scripts/farm-drift-check.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `python3 scripts/tests/mutate_farm_drift_check.py [report-path]` — and
never while editing the subject, since the restore would clobber your edits. The suite takes about 20
seconds per mutant (the whole campaign, 22 mutants, about 9 minutes), so run it through `scripts/run-long.sh`, never in
a delegate's foreground.

This subject's failure mode is SILENCE: a hook that goes quiet reads exactly like a clean farm.
Every row names ONE property `test_farm_drift_check.py` is meant to defend and mutates exactly
what it names; a row that survives means the suite does not test what the row claims, and the
repair is a stronger SUITE row, never a reworded mutant. Rows are meant to kill DIFFERENT sets of
suite rows: a duplicate mutant inflates the caught count and measures nothing.

The `exit status` row is a near-total mutant (it fails almost every row, because nearly every row
asserts rc 0), so it says little beyond "the suite checks the exit code"; the rows that carry
information are the ones that kill one or two tests.

The repo root is derived from __file__, so invoking this through a symlinked
`~/.claude/scripts/tests/` would resolve into PRODUCTION's tree and mutate that instead. Run it
from the working copy you intend to grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "farm-drift-check.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    str(REPO / "scripts" / "tests" / "test_farm_drift_check.py"),
]

MUTATIONS = [
    mutate.Mutation(
        "verdict-presence: rc 0 alone reads clean, so a run that printed no verdict is silent",
        "    if rc == 0 and CLEAN_RE.search(out):",
        "    if rc == 0:",
    ),
    mutate.Mutation(
        "verdict position: the clean verdict must be the FIRST output, so the WARN lines a real "
        "run prints first make every clean farm read as no verdict",
        'CLEAN_RE = re.compile(r"^RESULT: PASS .*\\bcheck=clean\\b", re.M)',
        'CLEAN_RE = re.compile(r"\\ARESULT: PASS .*\\bcheck=clean\\b", re.M)',
    ),
    mutate.Mutation(
        "marker gate: any machine runs the check, so a non-farm reports sixteen drifted members",
        'if not os.path.isfile(os.path.join(target, ".installed-from")):',
        "if False:",
    ),
    mutate.Mutation(
        "marker type: a directory named like the marker counts as a farm",
        'if not os.path.isfile(os.path.join(target, ".installed-from")):',
        'if not os.path.exists(os.path.join(target, ".installed-from")):',
    ),
    mutate.Mutation(
        "payload gate: every payload runs the check, including the inert ones the argv-refusal "
        "contract rows send against the real farm",
        'or data.get("hook_event_name") != "SessionStart"',
        "or False",
    ),
    mutate.Mutation(
        "deadline override: FARM_DRIFT_DEADLINE_SECS is never read, so the default always wins "
        "— caught only because the rows pass a value that differs from the default",
        "    raw = os.environ.get(DEADLINE_ENV)",
        "    raw = None",
    ),
    mutate.Mutation(
        "group kill: only the direct child is signalled, so install.sh's grandchildren outlive "
        "the deadline",
        "os.killpg(proc.pid, sig)",
        "proc.send_signal(sig)",
    ),
    mutate.Mutation(
        "name cap: every drifted name is listed, so the 5-name cap and the 'and N more' tail go",
        "MAX_NAMES = 5",
        "MAX_NAMES = 50",
    ),
    mutate.Mutation(
        "control characters: member names are not sanitised, so an escape sequence reaches the "
        "terminal",
        '    return CONTROL_RE.sub("", text)',
        "    return text",
    ),
    mutate.Mutation(
        "exit status (near-total: fails almost every row): a real invocation exits 1, which the "
        "harness reads as noise",
        "    return 0\n\n\nif __name__",
        "    return 1\n\n\nif __name__",
    ),
    mutate.Mutation(
        "stdout hardening: the message goes through buffered print, so a closed or broken stdout "
        "fails at interpreter shutdown instead of being swallowed",
        '_write(1, json.dumps({"systemMessage": message}) + "\\n")',
        'print(json.dumps({"systemMessage": message}))',
    ),
    mutate.Mutation(
        "self-install mapping: install.sh's own refusal is reported as a generic could-not-run",
        "    if first.startswith(SELF_INSTALL_PREFIX):",
        "    if False:",
    ),
    mutate.Mutation(
        "location pre-test: a hook running from inside the install target short-circuits the "
        "check, replacing install.sh's complete drift list",
        "        deadline = _deadline()\n",
        "        deadline = _deadline()\n"
        "        if Path(__file__).resolve().parent.parent == Path(target):\n"
        '            _say("farm drift: scripts replaced.")\n'
        "            return 0\n",
    ),
    mutate.Mutation(
        "stderr hardening: the audible-ignore line goes through sys.stderr, so a closed stderr "
        "raises into the outer handler, which then speaks on stdout",
        "        _write(\n            2,\n",
        "        sys.stderr.write(\n",
    ),
    mutate.Mutation(
        "date source: members are dated by mtime, so a later write or an old copied mtime "
        "replaces the last-change date",
        ")).st_ctime",
        ")).st_mtime",
    ),
    mutate.Mutation(
        "link handling: members are statted through links, so a dangling link reads as absent",
        "        stamp = os.lstat(",
        "        stamp = os.stat(",
    ),
    mutate.Mutation(
        "name filter: a name containing a slash is accepted, so a path fragment is named",
        '        if name and "/" not in name and name not in names:',
        "        if name and name not in names:",
    ),
    mutate.Mutation(
        "stderr cap: the first stderr line is not truncated, so one refusal can fill the message",
        "_clean(first)[:STDERR_CAP]",
        "_clean(first)",
    ),
    mutate.Mutation(
        "install gate: a missing install.sh is run anyway, so a clone without one reports "
        "could-not-run instead of staying silent",
        "        if not install.is_file():",
        "        if False:",
    ),
    mutate.Mutation(
        "SIGKILL step: the kill sequence stops at TERM, so an install.sh that ignores TERM "
        "outlives the deadline",
        "        (signal.SIGKILL, KILL_GRACE_SECS),\n",
        "",
    ),
    mutate.Mutation(
        "check flag: the hook runs install.sh WITHOUT --check, so every session start "
        "would install for real (against the fixture HOME only)",
        '[str(install), "--check"],',
        "[str(install)],",
    ),
    mutate.Mutation(
        "HOME gate: a missing HOME is not refused up front, so it falls into the catch-all "
        "and speaks on stdout",
        "        if not home:\n            return 0\n",
        "",
    ),
]


def main() -> int:
    report_path = sys.argv[1] if len(sys.argv) > 1 else None
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=REPO, report_path=report_path)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
