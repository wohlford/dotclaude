#!/usr/bin/env python3
# Script: run-hooks.py
# Purpose: Re-run the hooks settings.json registers for an edit or Bash command, as the harness would
# Usage: run-hooks.py [--event E] [--tool T] [--command STR] [--settings FILE] [--scripts-dir DIR] [--cwd DIR] [--only NAME]... [--concurrent] [--list] [--full] [PATH]
"""Re-run, by hand, the hooks `settings.json` registers for one edit or Bash command.

The harness fires hooks only for the tools it sees: an edit made through python in the Bash tool
bypasses every PostToolUse hook, and surveying a hook's duration against its registered timeout took
a hand-written script each time. This tool derives the matching registrations from the same walker
every other checker uses (`scripts/lib/settings_hooks.py`) and runs each the way the harness does.

What the harness does was MEASURED (Claude Code 2.1.286, `claude -p` with throwaway probe hooks), not
assumed. The public docs state the comma list, case-sensitivity, the empty matcher and the 600 s default,
and are silent on anchoring, the `tool_response` schema, the hook's cwd, dedupe and what Claude receives
when a hook is killed — those five were measured:

* a `matcher` is a regex that must match the WHOLE tool name (`Ed` does not match `Edit`), is
  case-sensitive, takes a comma list (`Write, Bash`), and an empty or `*` matcher matches every tool;
* identical `command` strings registered more than once run ONCE;
* the payload carries `session_id transcript_path cwd prompt_id permission_mode hook_event_name tool_name
  tool_input tool_use_id`, plus `tool_response duration_ms` after the tool ran;
* the hook runs under `sh -c` with cwd == `CLAUDE_PROJECT_DIR` == the payload's `cwd`;
* (earlier, 2026-09-15) a hook that outlives its `timeout` is killed and Claude hears nothing, exit 2
  delivers stderr, and any other nonzero exit is a non-blocking error.

`scripts/tests/fixtures/hook-payloads/` holds the captured payloads this tool's synthesized ones are
compared against. Known differences from the harness, all stated rather than hidden: stdin is a pipe
(the harness gives a socket); `content`, `old_string`, `new_string` and the `tool_response` content
fields are empty (the one repo hook that reads `tool_response` is the commit-subject advisor on a Bash
call, so PostToolUse with tool Bash is refused); a hook whose stdout is a JSON object carrying a key the
harness acts on (`decision`, `hookSpecificOutput`, `continue` …) is reported UNMODELLED, because the
harness acts on it and this tool does not; only ONE settings file is read — project/local settings and
plugin hooks are not — and by default it is `~/.claude/settings.json`, which here is a symlink into the
PRODUCTION clone (pass `--settings <repo>/settings.json` with `--scripts-dir` to grade a branch); Edit,
Write and Bash are the only tools whose payload shape was measured, so any other tool is refused; a hook
that exits leaving a descendant running is judged by its own exit status, and the descendant is not
reaped; a comma inside a regex quantifier (`{1,2}`) is split like any other comma (unmeasured); and
`--concurrent` prints nothing until every hook has finished. Run it from the project root: the default
cwd is `$PWD` when that is absolute and names the current directory (the shell's logical path, through
any symlink), else the physical `os.getcwd()`; a relative `--cwd` or PATH is joined onto that same
directory, and the result is what the hooks and `CLAUDE_PROJECT_DIR` see.

TERM, INT, HUP and QUIT kill every live hook group and exit 128+signo. A SIGKILL to the tool itself
always orphans the hook that is running — each hook leads its own session, so nothing kills it with the
tool; measured, and inherent (no handler can run on SIGKILL).

Verdict, last line of stdout: `RESULT: <STATUS> rc=<n> matched= filtered= ran= ...` where STATUS is PASS (rc 0: at least one hook
ran and every one that ran exited 0 with no decision JSON, nothing unmodelled) | FAIL (rc 1: a hook blocked) | ERROR (rc 2:
usage error or zero matching registrations; also what a would-be PASS exits when its stdout could not
be delivered) | INCOMPLETE (rc 3: a hook died,
was killed — or finished after its deadline, which the harness would have killed — or exited with some
other nonzero status, an entry carried a key this tool does not model, or nothing ran). Clean is EXACTLY `RESULT: PASS` — an allowlist. `--list` prints the plan and, unless it hits a usage
error (which still ends in `RESULT: ERROR`: an ERROR is never a pass), NO RESULT line. A PASS covers
only what ran: `filtered=N` counts registrations `--only` dropped and `skipped=N` the guard
registrations below, so a PASS with either nonzero is partial coverage (a PreToolUse Bash PASS, for one,
never consults the exec-bit-guard gate).

Registrations whose command matches `exec[-_]bit[-_]guard`, in any letter case (APFS is
case-insensitive), or whose FIRST WORD (a plain path, or the `$HOME/.claude/scripts/` form) resolves
through a symlink to a file whose name does, are SKIPPED. Wrapped, prefixed or quoted forms
(`FOO=1 link`, `bash link`, a quoted path with spaces, `$CLAUDE_PROJECT_DIR/...`) are only text-matched,
so a symlink behind one is not detected. The skip is
unconditional (standing constraint: never run or test against them), and a PATH whose basename, or
whose symlink target's basename, matches the pattern is refused; the skipped count is in
the verdict line (the pattern is searched in the whole command text, so a checkout under a directory
with that name skips every hook — the safe direction). This tool does not look INSIDE hooks: a hook that itself runs other suites
(`hook-machinery-test.sh`, for the `settings.json`, `scripts/lib/settings_hooks.py`,
`scripts/tests/test_hook_suite_guard.sh` and similar subjects) does what it does on an edit of those
files. Measured by reading its subject table: those subjects run the argv-refusal module, which
executes the guard's test-runner SCRIPT (`exec-bit-guard-test.sh`) with an inert payload, and
`test_hook_suite_guard.sh` also selects the guard suite's stub-backed rows. No accepted subject
reaches `exec-bit-guard.sh` itself or its real suite `test_exec_bit_guard.sh`; the executions that do
happen are the ones `/audit --tests` makes before every publish. The commands run are the ones the settings file names, so a
hook edited in a branch is graded by the INSTALLED copy unless `--scripts-dir` points at the branch's.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import settings_hooks  # noqa: E402

FARM_PREFIX = "$HOME/.claude/scripts/"
# IGNORECASE: APFS is case-insensitive, so `Exec-Bit-Guard.sh` IS the guard file
FORBIDDEN = re.compile(r"exec[-_]bit[-_]guard", re.IGNORECASE)
DECISION_KEYS = frozenset(
    {
        "decision",
        "hookSpecificOutput",
        "continue",
        "stopReason",
        "permissionDecision",
        "systemMessage",
        "suppressOutput",
        "updatedInput",
    }
)
EVENTS = ("PreToolUse", "PostToolUse")
TOOLS = ("Edit", "Write", "Bash")
KNOWN_KEYS = frozenset({"type", "command", "timeout"})
TAIL_LINES = 20
TERM_GRACE = 5.0
KILL_GRACE = 2.0

_live: list[subprocess.Popen] = []
_output_failed = False


def say(line: str = "") -> None:
    """Print one line, remembering a failure to deliver it (a closed stdout is not a pass).

    After a failure fd 1 is pointed at /dev/null: otherwise the bytes left in the buffer make the
    interpreter's flush at exit fail, and that exits 120 and replaces the status this tool chose.
    """
    global _output_failed
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except (OSError, ValueError, AttributeError):
        _output_failed = True
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(fd, 1)
            if fd != 1:
                os.close(fd)
        except OSError:
            pass


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


@dataclass
class Row:
    n: int
    command: str
    run_command: str
    timeout: float
    kind: str  # run | skip | unmodelled
    why: str = ""
    name: str = "?"
    script: str = ""
    copy: str = ""
    verdict: str = ""
    rc: int | None = None
    elapsed: float = 0.0
    stderr: str = ""


def matcher_matches(matcher, tool: str) -> bool:
    """The harness's matcher rule as measured. Raises re.error on an invalid pattern."""
    if matcher is None:
        return True
    if not isinstance(matcher, str):
        raise TypeError("matcher is not a string (%r)" % (matcher,))
    if matcher.strip() in ("", "*"):
        return True
    return any(re.fullmatch(alt.strip(), tool) for alt in matcher.split(","))


def hook_name(command: str) -> str:
    base = settings_hooks.hook_basename(command, FARM_PREFIX)
    if base is not None:
        return base
    tokens = command.split()
    return os.path.basename(tokens[0].strip("'\"")) if tokens else "?"


def build_rows(
    entries, event, tool, only, scripts_dir
) -> tuple[list[Row], int, int, int]:
    """Matching registrations -> deduped Rows. Returns (rows, matched, deduped, filtered)."""
    hits = []  # [command, [entries], unmodelled-reason], one list per distinct command
    matched = 0
    index: dict[str, int] = {}
    deduped = 0
    filtered = 0  # matching registrations --only dropped
    for ev, matcher, entry in entries:
        if ev != event:
            continue
        why = ""
        try:
            hit = matcher_matches(matcher, tool)
        except (re.error, TypeError) as exc:
            hit, why = True, "matcher: %s" % exc
        if not hit:
            continue
        command = entry["command"]
        if only and hook_name(command) not in only:
            filtered += 1
            continue
        matched += 1
        if command in index:
            deduped += 1
            hits[index[command]][1].append(entry)
            if why and not hits[index[command]][2]:
                hits[index[command]][2] = why
            continue
        index[command] = len(hits)
        hits.append([command, [entry], why])
    rows = []
    for n, (command, group, why) in enumerate(hits, 1):
        timeouts = []
        for entry in group:
            extra = sorted(set(entry) - KNOWN_KEYS)
            if extra and not why:
                why = "key %r is not modelled" % extra[0]
            if entry.get("type", "command") != "command" and not why:
                why = "type %r is not modelled" % entry.get("type")
            try:
                timeouts.append(settings_hooks.entry_timeout(entry, "settings"))
            except ValueError as exc:
                why = why or str(exc)
            else:
                # json reads NaN and Infinity, and either leaves the hook no deadline at all
                if not math.isfinite(timeouts[-1]):
                    why = why or "timeout %r is not finite" % timeouts.pop()
        timeout = (
            min(timeouts) if timeouts else settings_hooks.HARNESS_DEFAULT_TIMEOUT_SECS
        )
        run_command = command
        under_farm = settings_hooks.hook_basename(command, FARM_PREFIX) is not None
        if scripts_dir and under_farm:
            if ('"' + FARM_PREFIX) in command or ("'" + FARM_PREFIX) in command:
                why = why or "a quoted $HOME/.claude/scripts/ path cannot be rewritten"
            else:
                run_command = command.replace(
                    FARM_PREFIX, shlex.quote(str(scripts_dir).rstrip("/")) + "/"
                )
        name = hook_name(command)
        if under_farm:
            root = str(scripts_dir) if scripts_dir else os.path.expandvars(FARM_PREFIX)
            script = os.path.realpath(os.path.join(root, name))
            copy = "override" if scripts_dir else "installed"
        else:
            script, copy = "", "outside"
        row = Row(
            n, command, run_command, timeout, "run", name=name, script=script, copy=copy
        )
        if FORBIDDEN.search(command):
            row.kind, row.why = "skip", "standing constraint: never run exec-bit-guard*"
        elif FORBIDDEN.search(os.path.basename(resolved_script(command, script))):
            row.kind, row.why = "skip", "standing constraint: runs exec-bit-guard*"
        elif why:
            row.kind, row.why = "unmodelled", why
        rows.append(row)
    return rows, matched, deduped, filtered


def resolved_script(command: str, script: str) -> str:
    """The real file a registration runs: the farm row's resolved `script`, else the command's first
    word when it names an existing file (a symlink's target is what runs, whatever the link is called)."""
    if script:
        return script
    # the first word as hook_name() reads it — never shlex, which took minutes on a 3 MB command
    words = command.split(None, 1)
    first = os.path.expandvars(words[0].strip("'\"")) if words else ""
    return os.path.realpath(first) if first and os.path.exists(first) else ""


def build_payload(event, tool, subject, cwd) -> dict:
    payload = {
        "session_id": str(uuid.uuid4()),
        "transcript_path": os.path.join(
            tempfile.gettempdir(), "run-hooks-no-transcript.jsonl"
        ),
        "cwd": cwd,
        "prompt_id": str(uuid.uuid4()),
        "permission_mode": "default",
        "hook_event_name": event,
        "tool_name": tool,
    }
    if tool == "Edit":
        payload["tool_input"] = {
            "file_path": subject,
            "old_string": "",
            "new_string": "",
            "replace_all": False,
        }
        response = {
            "filePath": subject,
            "oldString": "",
            "newString": "",
            "originalFile": "",
            "structuredPatch": [],
            "userModified": False,
            "replaceAll": False,
        }
    elif tool == "Write":
        payload["tool_input"] = {"file_path": subject, "content": ""}
        response = {
            "type": "create",
            "filePath": subject,
            "content": "",
            "structuredPatch": [],
            "originalFile": None,
            "userModified": False,
        }
    else:
        payload["tool_input"] = {"command": subject, "description": ""}
        response = {
            "stdout": "",
            "stderr": "",
            "interrupted": False,
            "isImage": False,
            "noOutputExpected": False,
        }
    payload["tool_use_id"] = "toolu_" + uuid.uuid4().hex[:24]
    if event == "PostToolUse":
        payload["tool_response"] = response
        payload["duration_ms"] = 0
    return payload


def kill_group(proc: subprocess.Popen, term_grace: float = TERM_GRACE) -> None:
    """TERM the hook's whole process group, then KILL what outlives the grace. The leader is
    reaped on each poll so a zombie leader does not keep the group looking alive."""
    for sig, grace in ((signal.SIGTERM, term_grace), (signal.SIGKILL, KILL_GRACE)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        end = time.monotonic() + grace
        while time.monotonic() < end:
            proc.poll()
            try:
                os.killpg(proc.pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


def _kill_live() -> None:
    """KILL every hook group still running (the signal path and the exception path both end here)."""
    for proc in list(_live):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _on_signal(signum, _frame):
    _kill_live()
    raise SystemExit(128 + signum)


def start(row: Row, payload: bytes, cwd: str, env: dict, tmp: Path):
    """A started Job, or None when the hook could not be started (the row is then NOISE)."""
    try:
        return Job(row, payload, cwd, env, tmp)
    except (OSError, ValueError) as exc:  # a NUL byte in the command: ValueError
        row.verdict, row.why = "NOISE", "could not start: %s" % exc
        return None


def decision_json(text: str) -> bool:
    """True when a hook's stdout is a JSON object carrying a key the harness acts on."""
    text = text.strip()
    if not text.startswith("{"):
        return False
    try:
        obj = json.loads(text)
    except ValueError:
        return False
    return isinstance(obj, dict) and bool(DECISION_KEYS & set(obj))


class Job:
    def __init__(self, row: Row, payload: bytes, cwd: str, env: dict, tmp: Path):
        self.row = row
        self.out_path = tmp / ("%d.out" % row.n)
        self.err_path = tmp / ("%d.err" % row.n)
        self.start = time.monotonic()
        self.deadline = self.start + row.timeout
        self.finished_at: float | None = None
        self.done = threading.Event()
        with open(self.out_path, "wb") as out, open(self.err_path, "wb") as err:
            self.proc = subprocess.Popen(
                ["/bin/sh", "-c", row.run_command],
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        _live.append(self.proc)
        threading.Thread(target=self._feed, args=(payload,), daemon=True).start()
        threading.Thread(target=self._reap, daemon=True).start()

    def _feed(self, payload: bytes) -> None:
        try:
            self.proc.stdin.write(payload)
            self.proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def _reap(self) -> None:
        """Stamp the moment the hook really exited, so a busy main loop cannot make a hook that
        finished in time look as if it overran (or one that overran look as if it did not)."""
        self.proc.wait()
        self.finished_at = time.monotonic()
        self.done.set()

    def settle(self, killed: bool) -> None:
        row = self.row
        row.elapsed = (self.finished_at or time.monotonic()) - self.start
        row.rc = self.proc.returncode
        if self.proc in _live:
            _live.remove(self.proc)
        overran = not killed and row.elapsed > row.timeout
        if killed or overran:
            row.verdict = "KILLED"
            if overran:
                row.why = (
                    "finished after its deadline; the harness would have killed it"
                )
        elif row.rc == 0:
            row.verdict = "ALLOW"
        elif row.rc == 2:
            row.verdict = "BLOCK"
        elif row.rc is not None and row.rc < 0:
            row.verdict = "DIED"
        else:
            row.verdict = "NOISE"
        row.stderr = self.err_path.read_bytes().decode("utf-8", "replace")
        if row.verdict == "ALLOW" and decision_json(
            self.out_path.read_bytes().decode("utf-8", "replace")
        ):
            row.verdict = "UNMODELLED"
            row.why = "stdout is a JSON object carrying a key the harness acts on"


def wait_all(jobs: list[Job], on_done) -> None:
    live = list(jobs)
    while live:
        now = time.monotonic()
        for job in list(live):
            if job.done.is_set():
                job.settle(False)
            elif now >= job.deadline:
                kill_group(job.proc)
                job.done.wait(timeout=KILL_GRACE)
                job.settle(True)
            else:
                continue
            live.remove(job)
            on_done(job.row)
        if live:
            time.sleep(0.02)


def tail(text: str, full: bool) -> list[str]:
    lines = text.splitlines()
    if full or len(lines) <= TAIL_LINES:
        return lines
    return ["(%d earlier stderr lines omitted)" % (len(lines) - TAIL_LINES)] + lines[
        -TAIL_LINES:
    ]


def show(row: Row, total: int, full: bool) -> None:
    label = "[%d/%d]" % (row.n, total)
    where = (
        "copy=%s %s" % (row.copy, row.script) if row.script else "copy=%s" % row.copy
    )
    if row.kind == "skip":
        say("%s SKIPPED     %s  (%s)" % (label, row.name, row.why))
        return
    if row.kind == "unmodelled":
        say("%s UNMODELLED  %s  (%s)" % (label, row.name, row.why))
        return
    if not row.verdict:
        say(
            "%s WOULD RUN   %s  timeout=%gs  %s" % (label, row.name, row.timeout, where)
        )
        return
    say(
        "%s %-11s %s  %.2fs/%gs  rc=%s  %s"
        % (label, row.verdict, row.name, row.elapsed, row.timeout, row.rc, where)
    )
    if row.why:
        say("      ! " + row.why)
    if row.verdict != "ALLOW" or full:
        for line in tail(row.stderr, full):
            say("      | " + line)


def result_line(status, rc, counts) -> int:
    say(
        "RESULT: %s rc=%d matched=%d filtered=%d ran=%d allow=%d block=%d noise=%d died=%d killed=%d "
        "skipped=%d unmodelled=%d deduped=%d" % ((status, rc) + counts)
    )
    return rc


def logical_cwd() -> str:
    """$PWD when it is absolute and names the current directory (a shell's logical path, through any
    symlink), else the physical os.getcwd() — a stale or foreign $PWD is never believed."""
    pwd = os.environ.get("PWD", "")
    here = os.getcwd()
    try:
        if os.path.isabs(pwd) and os.path.samefile(pwd, here):
            return os.path.normpath(pwd)
    except OSError:
        pass
    return here


def absolute(path: str) -> str:
    """`path` made absolute. With no `..` component it joins onto logical_cwd() (what the harness sent,
    unresolved); with one it resolves PHYSICALLY (os.path.abspath against os.getcwd()), because the
    kernel and shell resolve `..` through the symlink's target, which a lexical collapse would not."""
    if os.path.isabs(path):
        return os.path.normpath(path)
    if ".." in path.split(os.sep):
        return os.path.abspath(path)
    return os.path.normpath(os.path.join(logical_cwd(), path))


def parse(argv):
    p = _Parser(
        prog="run-hooks.py", description="Re-run the hooks the harness would fire."
    )
    p.add_argument("path", nargs="?", help="the edited file (Edit/Write)")
    p.add_argument("--event", choices=EVENTS, default="PostToolUse")
    p.add_argument("--tool")
    p.add_argument("--command", help="the Bash command (tool Bash)")
    p.add_argument("--settings", default=os.path.expanduser("~/.claude/settings.json"))
    p.add_argument("--scripts-dir")
    p.add_argument("--cwd")
    p.add_argument("--only", action="append", default=[])
    p.add_argument("--concurrent", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("--full", action="store_true")
    return p.parse_args(argv)


def run(argv) -> int:
    args = parse(argv)
    tool = args.tool or ("Bash" if args.command is not None else "Edit")
    if tool not in TOOLS:
        raise UsageError(
            "tool %r: payload shape not measured (supported: %s)"
            % (tool, ", ".join(TOOLS))
        )
    if args.event == "PostToolUse" and tool == "Bash":
        raise UsageError(
            "PostToolUse with tool Bash is refused: its tool_response (stdout, stderr) cannot be "
            "synthesized, and the hook that reads it would pass over an empty one"
        )
    if tool == "Bash":
        if args.command is None or args.path:
            raise UsageError("tool Bash takes --command and no PATH")
        subject = args.command
    else:
        if args.command is not None or not args.path:
            raise UsageError("tool %s takes a PATH and no --command" % tool)
        subject = absolute(args.path)
        if not os.path.isfile(subject):
            raise UsageError("PATH %s is not an existing file" % subject)
        if FORBIDDEN.search(os.path.basename(subject)) or FORBIDDEN.search(
            os.path.basename(os.path.realpath(subject))
        ):
            raise UsageError(
                "PATH %s names an exec-bit-guard file: standing constraint, refused"
                % subject
            )
    cwd = absolute(args.cwd) if args.cwd else logical_cwd()
    if not os.path.isdir(cwd):
        raise UsageError("--cwd %s is not a directory" % cwd)
    scripts_dir = os.path.abspath(args.scripts_dir) if args.scripts_dir else None
    if scripts_dir and not os.path.isdir(scripts_dir):
        raise UsageError("--scripts-dir %s is not a directory" % scripts_dir)
    try:
        doc = json.loads(Path(args.settings).read_text())
        if not isinstance(doc, dict):
            raise ValueError("not a JSON object")
        entries = settings_hooks.walk_hook_entries(doc, args.settings)
    except (OSError, ValueError) as exc:
        raise UsageError("settings %s unreadable: %s" % (args.settings, exc)) from exc

    rows, matched, deduped, filtered = build_rows(
        entries, args.event, tool, set(args.only), scripts_dir
    )
    to_run = [r for r in rows if r.kind == "run"]
    skipped = sum(r.kind == "skip" for r in rows)
    unmodelled = sum(r.kind == "unmodelled" for r in rows)
    if matched == 0:
        raise UsageError(
            "no registration matches %s %s in %s%s (reads only that file; project/local settings "
            "and plugin hooks are not read)"
            % (args.event, tool, args.settings, " with --only" if args.only else "")
        )

    say(
        "settings: %s (%d registrations; reads ONLY this file — project/local settings and plugin hooks are not read)"
        % (args.settings, len(entries))
    )
    say(
        "subject:  %s %s %s  cwd=%s  payload: synthesized, content fields empty"
        % (args.event, tool, subject, cwd)
    )
    say(
        "plan:     matched=%d filtered=%d deduped=%d run=%d skipped=%d unmodelled=%d  mode=%s  scripts=%s"
        % (
            matched,
            filtered,
            deduped,
            len(to_run),
            skipped,
            unmodelled,
            "concurrent" if args.concurrent else "sequential",
            "override %s" % scripts_dir if scripts_dir else "installed",
        )
    )

    if args.list:
        for row in rows:
            show(row, len(rows), args.full)
        say(
            "LIST: %d to run, %d skipped, %d unmodelled (%d matched, %d filtered, %d deduped)"
            % (len(to_run), skipped, unmodelled, matched, filtered, deduped)
        )
        return 0

    payload = json.dumps(build_payload(args.event, tool, subject, cwd)).encode()
    env = dict(os.environ, CLAUDE_PROJECT_DIR=cwd)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(sig, _on_signal)
    with tempfile.TemporaryDirectory(prefix="run-hooks-") as tmp:
        done = lambda row: show(row, len(rows), args.full)  # noqa: E731
        try:
            for row in rows:
                if row.kind != "run" and not args.concurrent:
                    show(row, len(rows), args.full)
            if args.concurrent:
                started = [start(r, payload, cwd, env, Path(tmp)) for r in to_run]
                wait_all([j for j in started if j], lambda row: None)
                for row in rows:
                    show(row, len(rows), args.full)
            else:
                for row in rows:
                    if row.kind == "run":
                        job = start(row, payload, cwd, env, Path(tmp))
                        if job:
                            wait_all([job], done)
                        else:
                            done(row)
        finally:
            _kill_live()

    count = {
        v: sum(r.verdict == v for r in rows)
        for v in ("ALLOW", "BLOCK", "NOISE", "DIED", "KILLED", "UNMODELLED")
    }
    unmodelled += count["UNMODELLED"]
    counts = (
        matched,
        filtered,
        len(to_run),
        count["ALLOW"],
        count["BLOCK"],
        count["NOISE"],
        count["DIED"],
        count["KILLED"],
        skipped,
        unmodelled,
        deduped,
    )
    if count["BLOCK"]:
        return result_line("FAIL", 1, counts)
    if to_run and count["ALLOW"] == len(to_run) and not unmodelled:
        return result_line("PASS", 0, counts)
    return result_line("INCOMPLETE", 3, counts)


def main(argv=None) -> int:
    try:
        rc = run(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        say("run-hooks.py: %s" % exc)
        rc = 2
        say("RESULT: ERROR rc=2 reason=%s" % exc)
    if _output_failed and rc == 0:
        rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
