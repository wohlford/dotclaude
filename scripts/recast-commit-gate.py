#!/usr/bin/env python3
# Script: recast-commit-gate.py
# Purpose: PreToolUse hook — run the recast suite before a commit that touches recast source
"""PreToolUse hook — run the recast suite before a commit that touches recast source.

Called by Claude Code hooks with the tool-call JSON on stdin. When a `git commit` is about to run
and the files it will include contain recast source (``skills/recast/*.{sh,py}``), this runs the
recast *full* pytest suite and blocks the commit (exit 2) if it fails. The edit-time hook runs only
the ONE test file matching an edited script; running the whole suite on every edit would be wasteful,
so the full-suite check lives here, at the commit — the rare, deliberate moment. Its settings.json
entry sets an explicit ``"timeout": 600`` so a long-but-legitimate run is not killed (a killed
PreToolUse hook fails *open*).

Written in Python — not bash like its siblings — because it must tokenize an arbitrary shell command
string safely (``shlex``, never ``eval``) to tell a `git commit` from a look-alike and to parse
``-am`` / pathspec / ``-m "message"`` forms correctly.

Because a PreToolUse hook fires *before* the command runs, a compound ``git add … && git commit`` has
not staged anything yet at gate time — so a preceding ``git add`` (and ``git rm``/``git mv``) segment
is detected and its pending files (tracked changes, untracked-to-be-added, and to-be-deleted/renamed
paths) are folded into the set the commit will include. Every ``git commit`` in a compound command is
inspected, not just the first, and its scope is over-approximated across all preceding index changes.

Known fail-open forms (the gate does not fire — degrading to *no gate*, and never false-blocking): a
commit reached only through a git alias (``git ci``), wrapped in ``sh -c "…"`` / ``eval "…"``, run
under a wrapper the WRAPPERS set does not list *with its own arguments* (``timeout 60 git commit``,
``sudo -u x git commit``), or targeting another repo via ``--git-dir``/``--work-tree`` from a non-repo
cwd. Detection sees the wrapper, not the nested commit; direct forms — subshells ``(…)``, fused
operators (``-A&&git``), leading/trailing redirects, bare wrappers (``sudo``/``time``/``env``/…), and
``-i``/``--include`` — are covered. A commit created by ``git merge``/``cherry-pick``/``rebase``/
``revert`` is out of scope (its content was gated when first committed on the branch). Resolving
aliases, nested command strings, and wrapper argument lists is tracked as future hardening.

Exit codes:
  0 — not a relevant commit, or the affected suite(s) passed (fail OPEN on any ambiguity about whether
      the command is even a commit; never false-block a non-commit).
  2 — an affected suite failed (stderr fed back to Claude to fix).

Fail direction (two layers): "is this a git commit?" ambiguity fails OPEN; once a commit is confirmed,
"which files will it include?" ambiguity fails CLOSED (over-approximate), because under-counting the
file set is the only unsafe error for a gate.
"""

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

SOURCE_RE = re.compile(r"^skills/(recast)/.*\.(sh|py)$")

# git *global* options (before the subcommand) that consume a following value token.
GLOBAL_VALUE_OPTS = {"-c", "--git-dir", "--work-tree", "--namespace"}
# `git commit` short options that consume a value (attached, e.g. -mMSG, or the next token).
COMMIT_VALUE_SHORT = set("mFCct")
COMMIT_VALUE_LONG = {
    "--message",
    "--file",
    "--reuse-message",
    "--reedit-message",
    "--template",
    "--author",
    "--date",
    "--cleanup",
    "--fixup",
    "--squash",
    "--trailer",
}
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Exec-wrappers that run their argument as a command, so `git` right after one is still in command
# position (`sudo git commit`, `time git commit`). Bounded on purpose — an unknown leading word
# (`echo git commit`) is treated as NOT a command, preserving the phantom-commit guard.
WRAPPERS = {
    "time",
    "env",
    "sudo",
    "doas",
    "nice",
    "ionice",
    "nohup",
    "setsid",
    "stdbuf",
    "command",
    "xargs",
    "timeout",
}

# git subcommands (other than add/stage) that mutate what the next commit will include. Seeing one
# before a commit means files it names WILL be committed even if they show no diff yet (a `git rm`
# of an unmodified tracked file), so their pathspecs are folded in — fail closed.
INDEX_MUTATING = {"rm", "mv"}


def tokenize(command: str) -> list[str]:
    """shlex with punctuation_chars: control/redirect operators become their own tokens even when
    fused to a word (`-A&&git`), while quoted values stay intact. Raises ValueError on unbalanced
    quotes (caller fails open)."""
    lex = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
    lex.whitespace_split = True
    return list(lex)


def is_op(token: str) -> bool:
    """A control operator / command boundary: `&&`, `||`, `;`, `|`, `&`, `(`, `)` — not a redirect."""
    return (
        bool(token)
        and all(c in "();|&" for c in token)
        and not any(c in "<>" for c in token)
    )


def is_redirect(token: str) -> bool:
    """A redirection operator token: `>`, `>>`, `<`, `>&`, `&>`, …"""
    return (
        bool(token)
        and all(c in "<>&|" for c in token)
        and any(c in "<>" for c in token)
    )


def strip_redirects(seg: list[str]) -> list[str]:
    """Drop redirection operators, their targets, and a preceding bare fd number (`2 >& 1`), so a
    redirect is never misread as a commit pathspec — a phantom pathspec would silently narrow the
    gate's scope to nothing (fail open)."""
    out: list[str] = []
    i = 0
    while i < len(seg):
        t = seg[i]
        if is_redirect(t):
            if out and out[-1].isdigit():
                out.pop()  # the fd number in e.g. `2 >& 1`
            i += 2  # skip the operator and its target
            continue
        out.append(t)
        i += 1
    return out


def is_git(token: str) -> bool:
    """True if token invokes git (bare name or a path ending in /git)."""
    return token == "git" or token.endswith("/git")


def starts_command(tokens: list[str], idx: int) -> bool:
    """True if tokens[idx] is in *command position* — reachable from the input start or a control
    operator by stepping back over only leading `VAR=val` env assignments and known exec-wrappers
    (`sudo`/`time`/`env`/…). A bare word before it (e.g. `echo`) means it is that command's
    argument, so `echo VAR=1 git commit` is NOT mistaken for a commit, while `sudo git commit` and
    `ALLOW_GIT_WRITE=1 git commit` are. (Redirects are stripped globally before this runs, so a
    leading `2>&1 git commit` also resolves to command position.)"""
    j = idx - 1
    while j >= 0:
        prev = tokens[j]
        if is_op(prev):
            return True
        if ENV_ASSIGN.match(prev) or prev in WRAPPERS:
            j -= 1
            continue
        return False
    return True


def parse_commits(command: str) -> list[dict]:
    """Return one dict per `git commit` the command will run (empty list if none).

    Each dict has: ``args`` (tokens after `commit`), ``cdir`` (that commit's `-C <dir>` value or
    None), ``add_scope`` (None if no `git add` precedes it; "ALL" if a preceding add stages the whole
    tree; else the list of pathspecs the preceding adds restrict to), and ``forced`` (paths a
    preceding `git rm`/`git mv` will put in the commit even with no diff yet).

    A compound command can run several commits; ALL are returned so a later one that stages a recast
    source is not missed. ``add_scope``/``forced`` accumulate across the scan, so each commit
    over-approximates rather than under-counts (fail closed). Returns [] on tokenizing ambiguity —
    never claim a non-commit is a commit."""
    # Newlines join separate commands the way `;` does; normalize so a newline-joined
    # `git add -A\ngit commit` splits into two segments. A `\n` inside a quoted message is preserved
    # as quoted content by shlex, so this is safe. Redirects are stripped from the whole stream so a
    # leading `2>&1 git commit` is still seen and a redirect is never misread as a pathspec.
    command = command.replace("\n", " ; ").replace("\r", " ")
    try:
        tokens = strip_redirects(tokenize(command))
    except ValueError:
        return []

    commits: list[dict] = []
    add_scope = None
    forced: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        if not (is_git(tokens[i]) and starts_command(tokens, i)):
            i += 1
            continue
        # Skip global options to reach the subcommand, capturing `-C <dir>`.
        j = i + 1
        cdir = None
        while j < n and tokens[j].startswith("-"):
            opt = tokens[j]
            if opt == "-C" and j + 1 < n:
                cdir = tokens[j + 1]
                j += 2
            elif opt.startswith("-C") and len(opt) > 2:
                cdir = opt[2:]
                j += 1
            elif opt in GLOBAL_VALUE_OPTS and "=" not in opt:
                j += 2
            else:
                j += 1
        if j >= n:
            break
        sub = tokens[j]
        # Collect this segment's args until a control operator or the next `git` invocation in
        # command position.
        seg = []
        k = j + 1
        while (
            k < n
            and not is_op(tokens[k])
            and not (is_git(tokens[k]) and starts_command(tokens, k))
        ):
            seg.append(tokens[k])
            k += 1
        if sub == "commit":
            if "--dry-run" not in seg:  # a dry run creates no commit — nothing to gate
                commits.append(
                    {
                        "args": seg,
                        "cdir": cdir,
                        "add_scope": add_scope,
                        "forced": list(forced),
                    }
                )
        elif sub in ("add", "stage"):
            paths = [t for t in seg if not t.startswith("-")]
            if not paths or "." in paths:
                add_scope = "ALL"
            elif add_scope != "ALL":
                add_scope = (add_scope or []) + paths
        elif sub in INDEX_MUTATING:
            # rm/mv name paths the next commit WILL include (a deletion or rename) even with no diff
            # yet; fold them into a forced set the gate checks directly.
            forced += [t for t in seg if not t.startswith("-")]
        i = k
    return commits


def parse_scope(args: list[str]) -> tuple[bool, list[str] | None, bool]:
    """From the tokens after `commit`, return (all_mode, pathspec_paths, include). all_mode =
    `-a/--all` (or a short cluster whose flag chars include 'a'). pathspec_paths = None when the
    commit carries no pathspec, else the list of pathspec tokens. include = `-i/--include`, which
    stages the given paths *in addition to* the index (a union), unlike a bare pathspec (`--only`,
    the default) which commits ONLY those paths. Short clusters stop at the first value-taking char,
    so an attached message (`-m'add x'`) is never read as flags or a pathspec."""
    all_mode = False
    include = False
    paths = []
    i, n = 0, len(args)
    while i < n:
        t = args[i]
        if t == "--":
            paths += args[i + 1 :]
            break
        if t.startswith("--"):
            name = t.split("=", 1)[0]
            if name == "--all":
                all_mode = True
            elif name == "--include":
                include = True
            elif name in COMMIT_VALUE_LONG and "=" not in t:
                i += 1  # consume the value token
        elif t.startswith("-") and len(t) > 1:
            cluster = t[1:]
            c = 0
            while c < len(cluster):
                ch = cluster[c]
                if ch in COMMIT_VALUE_SHORT:
                    if c == len(cluster) - 1:
                        i += 1  # value is the next token (e.g. `-m <msg>`, `-am <msg>`)
                    break  # any remaining chars are the attached value — stop scanning
                if ch == "a":
                    all_mode = True
                elif ch == "i":
                    include = True
                c += 1
        else:
            paths.append(t)  # a bare token is a pathspec (partial commit)
        i += 1
    return all_mode, (paths or None), include


def git_lines(root: Path, *args: str) -> list[str]:
    """Run git in root and return its non-empty stdout lines ([] on failure)."""
    out = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        return []
    return [line for line in out.stdout.splitlines() if line]


def changed_files(root: Path, base: Path, info: dict) -> set[str]:
    """The set of root-relative paths the commit will include, computed per form and fail-closed: a
    plain commit → the index (staged only, so a dirty *unstaged* file never false-blocks an unrelated
    commit); `-a` → all tracked changes plus the index; an explicit pathspec → only those paths (a
    partial commit, so an unrelated dirty file is never dragged in); a preceding `git add` → the index
    plus what that add will stage (tracked changes and untracked files), scoped to the add's own
    pathspecs. Pathspec-scoped queries run from `base` — the dir the command runs in — because the
    command's pathspecs are relative to it; diff output stays root-relative regardless, and ls-files
    gets --full-name to match."""
    all_mode, pathspec_paths, include = parse_scope(info["args"])
    add_scope = info["add_scope"]
    head = (
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "-q", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )

    def tracked(paths):
        if paths:
            return set(
                git_lines(
                    base,
                    "diff",
                    "HEAD" if head else "--cached",
                    "--name-only",
                    "--",
                    *paths,
                )
            )
        return set(
            git_lines(root, "diff", "HEAD" if head else "--cached", "--name-only")
        )

    def staged():
        return set(git_lines(root, "diff", "--cached", "--name-only"))

    def untracked(paths):
        if paths:
            return set(
                git_lines(
                    base,
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "--full-name",
                    "--",
                    *paths,
                )
            )
        return set(git_lines(root, "ls-files", "--others", "--exclude-standard"))

    # An EXCLUSIVE partial commit (bare pathspec, no -i) commits ONLY those paths — ignoring the
    # index, -a, and any staged rm/mv — so scope to exactly them and drop everything else.
    if pathspec_paths and not include:
        return tracked(pathspec_paths)

    # Otherwise the index is always committed, and -a, `-i <paths>`, and/or a preceding `git add`
    # each add MORE. Union them (never exclusive) so e.g. `git add x && git commit -a` sees every
    # modified tracked file, and `git commit -i README` still commits a separately-staged file.
    files = staged()
    if all_mode:  # -a sweeps all modified tracked files
        files |= tracked(None)
    if include and pathspec_paths:  # -i stages the given paths ON TOP of the index
        files |= tracked(pathspec_paths) | untracked(pathspec_paths)
    if add_scope is not None:  # a preceding `git add` also stages untracked files
        paths = None if add_scope == "ALL" else add_scope
        files |= tracked(paths) | untracked(paths)
    # A preceding `git rm`/`git mv` commits its paths even with no diff yet (a diff-based query can't
    # see a not-yet-run deletion), so add them directly, normalized to root-relative for SOURCE_RE.
    # realpath both sides so a base/root prefix mismatch from a symlinked tmp (/var -> /private/var)
    # doesn't garble the relative path; drop anything that resolves outside the repo.
    root_real = os.path.realpath(root)
    for p in info.get("forced", []):
        rel = os.path.relpath(os.path.realpath(os.path.join(base, p)), root_real)
        if rel != ".." and not rel.startswith(".." + os.sep):
            files.add(rel)
    return files


def resolve_dirs(cwd: str, cdir: str | None) -> tuple[Path, Path] | None:
    """Return (base, root): the directory the git command effectively runs in (honoring its `-C`)
    and that repo's toplevel. Pathspecs in the command are relative to `base`, NOT `root` — querying
    them from the root would silently match nothing for a subdirectory invocation (fail open)."""
    # Path's `/` already yields cdir unchanged when cdir is absolute, so no isabs branch is needed.
    base = Path(cwd) / cdir if cdir else Path(cwd)
    out = subprocess.run(
        ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        return None
    return base, Path(out.stdout.strip())


def main() -> int:
    """Hook entry point: run affected suites; 2 blocks the commit, 0 allows it."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    command = data.get("tool_input", {}).get("command", "")
    cwd = data.get("cwd") or os.getcwd()
    if not command:
        return 0

    commits = parse_commits(command)
    if not commits:
        return 0  # not a git commit — fail open

    # Collect (root, sub) pairs across EVERY commit the command runs — a later commit in a compound
    # command can touch a different subsystem (or, via -C, a different repo) than the first.
    targets: set[tuple[str, str]] = set()
    for info in commits:
        dirs = resolve_dirs(cwd, info["cdir"])
        if dirs is None:
            continue  # can't locate this commit's repo — can't test what we can't find
        base, root = dirs
        for f in changed_files(root, base, info):
            m = SOURCE_RE.match(f)
            if m and (root / "skills" / m.group(1) / "tests").is_dir():
                targets.add((str(root), m.group(1)))
    if not targets:
        return 0

    if importlib.util.find_spec("pytest") is None:
        return 0  # can't test — never falsely block

    pytest_args = ["tests/", "-q"]
    if importlib.util.find_spec("xdist") is not None:
        pytest_args += ["-n", "auto"]

    failed = False
    passed = []
    for root_s, sub in sorted(targets):
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *pytest_args],
            cwd=str(Path(root_s) / "skills" / sub),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            failed = True
            print(f"commit BLOCKED: {sub} tests failed", file=sys.stderr)
            tail = (proc.stdout + proc.stderr).splitlines()[-20:]
            print("\n".join(tail), file=sys.stderr)
        else:
            passed.append(sub)

    if failed:
        return 2
    print(f"recast commit gate passed: {', '.join(sorted(set(passed)))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
