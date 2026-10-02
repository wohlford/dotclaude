#!/usr/bin/env python3
# Script: mutate_run_hooks.py
# Purpose: Mutation campaign for scripts/run-hooks.py, driven by scripts/lib/mutate.py
# Usage: ./scripts/tests/mutate_run_hooks.py [--only N]   (env KILLSETS=<file> records killed rows)
"""Mutation campaign for scripts/run-hooks.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect it.
Run it on demand, never while editing the subject (the restore would clobber your edits), and through
`scripts/run-long.sh` — it runs the whole suite once per mutant, which outruns a foreground call. The
repo root is derived from __file__: run it from the working copy you intend to grade, not through a
symlinked `~/.claude/scripts/tests/`.

The subject is an INSTRUMENT whose `RESULT: PASS` is read as "the hooks passed", so each mutant is one
way that reading becomes false: a matcher rule that stopped being the harness's, a hook that did not run
or was not stopped, a verdict that should not have been PASS, a payload that drifted. Every anchor
occurs exactly once in the subject, which `mutate.py` enforces.

`--only N` (1-based) runs the baseline plus that one mutant. Without KILLSETS the suite stops at its
first failing row (`-x`) to keep the run short; with it the suite runs whole so every killed row is
named, and each mutant's killed rows print as it resolves — compare the sets, since a mutant that kills
exactly the rows another kills measures nothing.

NOT mutated, on purpose: the whole-name regex match's `re.fullmatch` -> `re.match` (a prefix match is
indistinguishable from `fullmatch` for every row but `Ed`, which the unanchored mutant already covers).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "run-hooks.py"
_TESTS = str(REPO / "scripts" / "tests" / "test_run_hooks.py")
_PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", _TESTS]
# Plain (stopping at the first failing row) unless KILLSETS is set; the wrapper keeps the suite's
# stdout and exit status intact and appends every `FAILED` summary line to the KILLSETS file.
_WRAPPER = (
    'out="$("$@" 2>&1)"; rc=$?; printf "%s\\n" "$out"; '
    'if [[ -n "${KILLSETS:-}" ]]; then grep "^FAILED" <<<"$out" >>"$KILLSETS"; fi; exit "$rc"'
)
if os.environ.get("KILLSETS"):
    SUITE = ["bash", "-c", _WRAPPER, "_", *_PYTEST]
else:
    SUITE = [*_PYTEST[:3], "-x", *_PYTEST[3:]]

M = mutate.Mutation

MUTATIONS = [
    # ---- matcher rule, as measured against the real harness
    M(
        "matcher is a search, not a whole-name match (`Ed` would match `Edit`)",
        "re.fullmatch(alt.strip(), tool)",
        "re.search(alt.strip(), tool)",
    ),
    M(
        "matcher is case-insensitive (`edit` would match `Edit`)",
        "re.fullmatch(alt.strip(), tool)",
        "re.fullmatch(alt.strip(), tool, re.I)",
    ),
    M(
        "a comma list is read as one pattern",
        'for alt in matcher.split(","))',
        "for alt in [matcher])",
    ),
    M(
        "an empty or `*` matcher no longer matches every tool",
        'if matcher.strip() in ("", "*"):',
        "if False:",
    ),
    M(
        "an absent matcher key matches nothing",
        "    if matcher is None:\n        return True",
        "    if matcher is None:\n        return False",
    ),
    M(
        "a non-string matcher is judged instead of reported unmodelled",
        "    if not isinstance(matcher, str):\n"
        '        raise TypeError("matcher is not a string (%r)" % (matcher,))\n',
        "",
    ),
    # ---- dedupe, unmodelled entries, the standing constraint
    M(
        "identical commands are no longer collapsed",
        "if command in index:",
        "if False:",
    ),
    M(
        "a deduped command keeps the LATEST kill instead of the earliest",
        "min(timeouts) if timeouts",
        "max(timeouts) if timeouts",
    ),
    M(
        "an entry key beyond type/command/timeout is run as if absent",
        "extra = sorted(set(entry) - KNOWN_KEYS)",
        "extra = []",
    ),
    M(
        "a non-command hook type is run as if it were a command",
        'if entry.get("type", "command") != "command" and not why:',
        "if False:",
    ),
    M(
        "a timeout the harness would not honour is no longer reported",
        'timeouts.append(settings_hooks.entry_timeout(entry, "settings"))',
        "timeouts.append(600)",
    ),
    M(
        "an unmodelled entry is classified as runnable",
        '        elif why:\n            row.kind, row.why = "unmodelled", why',
        '        elif False:\n            row.kind, row.why = "unmodelled", why',
    ),
    M(
        "exec-bit-guard is no longer skipped",
        "if FORBIDDEN.search(command):",
        "if False:",
    ),
    # ---- selection and the refusals
    M(
        "--only no longer selects",
        "if only and hook_name(command) not in only:",
        "if False:",
    ),
    M(
        "--scripts-dir no longer rewrites the farm prefix",
        "if scripts_dir and under_farm:",
        "if False:",
    ),
    M(
        "the row names the installed copy even under --scripts-dir",
        'copy = "override" if scripts_dir else "installed"',
        'copy = "installed"',
    ),
    M(
        "a PATH that is not a file is accepted",
        "if not os.path.isfile(subject):",
        "if False:",
    ),
    M(
        "Bash accepts a PATH beside --command",
        "if args.command is None or args.path:",
        "if args.command is None:",
    ),
    M(
        "a tool whose payload was never measured is accepted",
        "if tool not in TOOLS:",
        "if False:",
    ),
    M(
        "zero matching registrations is no longer an error",
        "    if matched == 0:",
        "    if False:",
    ),
    # ---- the payload and the process environment
    M(
        "the payload carries a different tool_input shape for Edit",
        '            "replace_all": False,\n        }\n        response = {',
        "        }\n        response = {",
    ),
    M(
        "PostToolUse stops carrying tool_response",
        '    if event == "PostToolUse":\n        payload["tool_response"] = response',
        '    if event == "PostToolUse":\n        response = None',
    ),
    M(
        "the payload never reaches the hook's stdin",
        "self.proc.stdin.write(payload)",
        "self.proc.stdin.write(b'{}')",
    ),
    M(
        "CLAUDE_PROJECT_DIR is not set",
        "env = dict(os.environ, CLAUDE_PROJECT_DIR=cwd)",
        "env = dict(os.environ)",
    ),
    M(
        "the hook runs in the tool's cwd, not --cwd",
        "                cwd=cwd,\n                env=env,",
        "                env=env,",
    ),
    # ---- verdicts
    M(
        "exit 2 is not a block",
        "elif row.rc == 2:",
        "elif row.rc == 99:",
    ),
    M(
        "death by signal reads as ordinary noise",
        "elif row.rc is not None and row.rc < 0:",
        "elif False:",
    ),
    M(
        "a block no longer outranks an incomplete sibling",
        '    if count["BLOCK"]:\n        return result_line("FAIL", 1, counts)',
        '    if count["BLOCK"] and not count["NOISE"]:\n        return result_line("FAIL", 1, counts)',
    ),
    M(
        "an unmodelled sibling no longer stops a PASS",
        'if to_run and count["ALLOW"] == len(to_run) and not unmodelled:',
        'if to_run and count["ALLOW"] == len(to_run):',
    ),
    M(
        "a run where nothing ran is a PASS",
        'if to_run and count["ALLOW"] == len(to_run) and not unmodelled:',
        'if count["ALLOW"] == len(to_run) and not unmodelled:',
    ),
    M(
        "an ALLOW row prints its stderr",
        'if row.verdict != "ALLOW" or full:',
        "if True:",
    ),
    M(
        "the stderr tail is not capped",
        "if full or len(lines) <= TAIL_LINES:",
        "if True:",
    ),
    M(
        "the stderr tail keeps the FIRST lines instead of the last",
        "lines[\n        -TAIL_LINES:\n    ]",
        "lines[:TAIL_LINES]",
    ),
    M(
        "--list prints a verdict line",
        "        return 0\n\n    payload = json.dumps(",
        '        return result_line("PASS", 0, (matched, filtered, 0, 0, 0, 0, 0, 0, skipped, unmodelled, deduped))\n\n    payload = json.dumps(',
    ),
    M(
        "a closed stdout still exits 0",
        "if _output_failed and rc == 0:",
        "if False:",
    ),
    # ---- lifecycle: stop what was started
    M(
        "an overrunning hook is not stopped",
        "                kill_group(job.proc)\n                job.done.wait(timeout=KILL_GRACE)",
        "                job.done.wait(timeout=KILL_GRACE)",
    ),
    M(
        "TERM only: a hook ignoring TERM is never KILLed",
        "((signal.SIGTERM, term_grace), (signal.SIGKILL, KILL_GRACE))",
        "((signal.SIGTERM, term_grace),)",
    ),
    M(
        "only the hook's leader is signalled, not its group",
        "os.killpg(proc.pid, sig)",
        "os.kill(proc.pid, sig)",
    ),
    M(
        "hooks start in the tool's own session (no group to kill)",
        "                env=env,\n                start_new_session=True,\n",
        "                env=env,\n",
    ),
    M(
        "SIGTERM/SIGINT are not handled",
        "signal.signal(sig, _on_signal)",
        "pass",
    ),
    M(
        "the signal handler leaves the live hooks running",
        "            os.killpg(proc.pid, signal.SIGKILL)",
        "            pass",
    ),
    M(
        "the signal handler exits 0",
        "raise SystemExit(128 + signum)",
        "raise SystemExit(0)",
    ),
    M(
        "the payload is written on the caller's thread, so a hook that never reads stalls the tool",
        "threading.Thread(target=self._feed, args=(payload,), daemon=True).start()",
        "self._feed(payload)",
    ),
    M(
        "a relative PATH is sent to the hooks as given",
        "subject = absolute(args.path)",
        "subject = args.path",
    ),
    M(
        "--concurrent runs sequentially",
        "            if args.concurrent:\n                started =",
        "            if False:\n                started =",
    ),
    M(
        "concurrent output is ordered by elapsed time, not registration",
        "                wait_all([j for j in started if j], lambda row: None)\n                for row in rows:",
        "                wait_all([j for j in started if j], lambda row: None)\n                for row in sorted(rows, key=lambda r: r.elapsed):",
    ),
    # ---- findings of the diverse plan review
    M(
        "a hook blocking through stdout JSON is allowed",
        'if row.verdict == "ALLOW" and decision_json(',
        "if False and decision_json(",
    ),
    M(
        "the decision-key set shrinks to one name",
        "bool(DECISION_KEYS & set(obj))",
        'bool({"decision"} & set(obj))',
    ),
    M(
        "a hook that exits after its deadline is not counted as killed",
        "overran = not killed and row.elapsed > row.timeout",
        "overran = False",
    ),
    M(
        "elapsed is read when the loop gets to the hook, not when the hook exited",
        "row.elapsed = (self.finished_at or time.monotonic()) - self.start",
        "row.elapsed = time.monotonic() - self.start",
    ),
    M(
        "PostToolUse with tool Bash is run over an empty response",
        'if args.event == "PostToolUse" and tool == "Bash":',
        "if False:",
    ),
    M(
        "a PATH naming the guard is accepted",
        "if FORBIDDEN.search(os.path.basename(subject)) or FORBIDDEN.search(",
        "if False or FORBIDDEN.search(",
    ),
    M(
        "the guard pattern matches the hyphenated spelling only",
        'FORBIDDEN = re.compile(r"exec[-_]bit[-_]guard", re.IGNORECASE)',
        'FORBIDDEN = re.compile(r"exec-bit-guard", re.IGNORECASE)',
    ),
    M(
        "--scripts-dir is spliced into the command unquoted",
        'shlex.quote(str(scripts_dir).rstrip("/"))',
        'str(scripts_dir).rstrip("/")',
    ),
    M(
        "a quoted farm prefix is rewritten as if it were bare",
        "if ('\"' + FARM_PREFIX) in command or (\"'\" + FARM_PREFIX) in command:",
        "if False:",
    ),
    M(
        "--scripts-dir is left relative",
        "scripts_dir = os.path.abspath(args.scripts_dir) if args.scripts_dir else None",
        "scripts_dir = args.scripts_dir",
    ),
    M(
        "a failed write leaves fd 1 on the dead pipe, so the exit flush replaces the status with 120",
        "os.dup2(fd, 1)",
        "pass",
    ),
    M(
        "a hook the OS cannot start is a traceback instead of a NOISE row",
        "    except (OSError, ValueError) as exc:  # a NUL byte",
        "    except ZeroDivisionError as exc:  # a NUL byte",
    ),
    M(
        "a symlink to a guard file is accepted",
        "or FORBIDDEN.search(\n            os.path.basename(os.path.realpath(subject))\n        ):",
        "or False:",
    ),
    M(
        "identical commands keep the LAST registration's timeout",
        "min(timeouts) if timeouts",
        "timeouts[-1] if timeouts",
    ),
    M(
        "--cwd is left relative",
        "cwd = absolute(args.cwd) if args.cwd else logical_cwd()",
        "cwd = args.cwd if args.cwd else logical_cwd()",
    ),
    M(
        "the guard pattern is case-sensitive",
        'exec[-_]bit[-_]guard", re.IGNORECASE)',
        'exec[-_]bit[-_]guard")',
    ),
    M(
        "a registered script that resolves to the guard is not skipped",
        "elif FORBIDDEN.search(os.path.basename(resolved_script(command, script))):",
        "elif False:",
    ),
    M(
        "--only drops registrations without counting them",
        "filtered += 1",
        "filtered += 0",
    ),
    M(
        "a non-finite timeout is accepted",
        "if not math.isfinite(timeouts[-1]):",
        "if False:",
    ),
    M(
        "SIGHUP is not handled",
        "signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)",
        "signal.SIGINT, signal.SIGQUIT)",
    ),
    M(
        "SIGQUIT is not handled",
        "signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)",
        "signal.SIGINT, signal.SIGHUP)",
    ),
    M(
        "a stale $PWD is believed",
        "os.path.isabs(pwd) and os.path.samefile(pwd, here)",
        "os.path.isabs(pwd)",
    ),
]


def _progress_factory():
    """Stream mutate's lines; under KILLSETS also print and reset the rows the last mutant killed."""
    killsets = os.environ.get("KILLSETS")

    def progress(line: str) -> None:
        print(line, flush=True)
        if killsets and re.match(r"^\[\d+/\d+\]", line):
            p = Path(killsets)
            rows = sorted(set(p.read_text().splitlines())) if p.exists() else []
            print(
                "          killed rows: " + (" | ".join(rows) if rows else "(none)"),
                flush=True,
            )
            p.write_text("")

    return progress


def main(argv: list[str]) -> int:
    mutations = MUTATIONS
    if argv[:1] == ["--only"] and len(argv) == 2 and argv[1].isdigit():
        n = int(argv[1])
        if not 1 <= n <= len(MUTATIONS):
            print(f"--only: N must be 1..{len(MUTATIONS)}", file=sys.stderr)
            return 2
        mutations = [MUTATIONS[n - 1]]
    elif argv:
        print("usage: mutate_run_hooks.py [--only N]", file=sys.stderr)
        return 2
    if os.environ.get("KILLSETS"):
        Path(os.environ["KILLSETS"]).write_text("")
    report = mutate.run(
        SUBJECT, SUITE, mutations, cwd=str(REPO), progress=_progress_factory()
    )
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
