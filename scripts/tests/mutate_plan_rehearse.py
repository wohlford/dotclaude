#!/usr/bin/env python3
"""Mutation campaign for scripts/plan-rehearse.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_plan_rehearse.py` — and never while editing the
subject, since the restore would clobber your edits.

Every row disables ONE refusal or one piece of plumbing and requires
`scripts/tests/test_plan_rehearse.py` to notice. This tool's whole job is to report honestly what
it could and could not run, so a survivor here means the suite would not notice the tool going
quietly optimistic: strengthen the suite row, never reword the mutation.

**A fixture this campaign reaches must be HARMLESS WHEN EXECUTED.** A row here disables the
pre-flight refusal, so whatever command the suite's fixtures carry is then actually run. Measured:
an early version of the REFUSED row carried a genuine remote-write command, the mutant executed it,
and it reached nothing only because the fixture repo defines no remote — luck, not design. It also
left a stray file in the operator's `/tmp`. The rule that follows: a dangerous-looking string handed
to a pure predicate (`escape_reason`) is fine, since nothing runs it; one handed to a fixture that
SPAWNS a shell must be chosen so that executing it is a no-op you can detect.

Pieces of the subject deliberately NOT mutated, and why:

* `OUTPUT_EXCERPT_LINES`. It bounds how much of a captured output is echoed; every value produces
  a truthful report, so no row can distinguish them without asserting on cosmetics.

Two earlier exclusions were REMOVED rather than re-justified, because the branch review measured
both stated reasons false:

* `resolution_disagreement` was excluded as "fires only where a shell FUNCTION shadows a binary,
  which no fixture can produce". False twice over: it fired on `grep`, `date`, `sed` and `ls` with
  no function anywhere, because its two probes differ by LOGIN PROFILE rather than by functions;
  and checks run under `bash --noprofile --norc`, where no function is inherited at all, so what it
  reported could not affect how any check ran. It was a false-positive generator feeding `flagged=`
  and has been DELETED from the subject.
* `create_clone`'s `tag.gpgsign` line was excluded as "`commit.gpgsign` beside it is what the suite
  can observe". False: nothing in the suite touched `create_clone`, so deleting EITHER line left
  the suite green. `create_clone` now has coverage and both lines carry rows.

"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "plan-rehearse.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    str(REPO / "scripts" / "tests" / "test_plan_rehearse.py"),
]

M = mutate.Mutation

MUTATIONS = [
    # ---- the shape vocabulary is a declared floor, not decoration
    M(
        "shape-guard-off: an unknown shape is accepted silently",
        "    if shape not in SHAPES:",
        "    if False:",
    ),
    # ---- extraction: pairing the WRONG text is worse than pairing nothing
    M(
        "run-line-swallows-aside: take the whole remainder, prose and all",
        'span = re.search(r"`([^`]+)`", rest)',
        "span = None if rest else None",
    ),
    M(
        "heading-pairs-inside-bold: a described command is run as a command",
        '        closing = prev.rfind("**")\n        tail = prev[closing + 2 :] if closing != -1 else ""',
        "        tail = prev",
    ),
    M(
        "single-marker-heading-pairs: a heading with one ** pairs its own text as a command",
        '        if prev.count("**") < 2:',
        "        if False:",
    ),
    M(
        "heading-never-pairs: the trailing command after ** is dropped",
        "        trailing = TRAILING_CMD_RE.match(tail)",
        "        trailing = None",
    ),
    M(
        "bare-backtick-unanchored: pair the first span on a line carrying several",
        'BACKTICK_ONLY_RE = re.compile(r"^\\s*(?:[-*]\\s*)?`([^`]{4,})`\\s*[.;]?\\s*$")',
        'BACKTICK_ONLY_RE = re.compile(r"^.*?`([^`]{4,})`")',
    ),
    M(
        "inline-arrow-blind: the Run: ... -> Expected: form is never seen",
        "        inline = EXPECTED_INLINE_RE.match(line)",
        "        inline = None",
    ),
    M(
        "run-line-prose-executed: a Run: line with no backticks runs its own prose",
        '        if not span:\n            return "unpaired", None, ""',
        '        if not span:\n            return "run-line", rest, "bash"',
    ),
    M(
        "bare-fence-run-as-bash: a fence declaring no language is executed",
        '        if lang == "(bare)":\n            return "fence-unknown-language", None, lang',
        '        if lang == "(bare)":\n            return "fence", body, "bash"',
    ),
    M(
        "trailing-span-unanchored: a span inside a heading sentence becomes the command",
        'TRAILING_CMD_RE = re.compile(r"^\\s*[\\u2014\\u2013-]?\\s*`([^`]{4,})`\\s*[.;]?\\s*$")',
        'TRAILING_CMD_RE = re.compile(r"`([^`]{4,})`")',
    ),
    M(
        "unknown-fence-run-as-bash: a yaml block is executed",
        '        if lang in PYTHON_LANGS:\n            return "fence", body, "python"\n'
        '        return "fence-unknown-language", None, lang',
        '        if lang in PYTHON_LANGS:\n            return "fence", body, "python"\n'
        '        return "fence", body, "bash"',
    ),
    M(
        "python-fence-run-as-bash: a python body is handed to the shell",
        '        if lang in PYTHON_LANGS:\n            return "fence", body, "python"',
        '        if False:\n            return "fence", body, "python"',
    ),
    # ---- the escape gate, rebuilt on scripts/lib/git_command.py's tokenizer.
    # Every row here was an ALLOWED command against some earlier draft of this gate.
    M(
        "unparseable-allowed: a command the tokenizer rejects is run anyway",
        '    except (ValueError, RecursionError) as exc:\n        return f"cannot be parsed, so cannot be vouched for: {exc}"',
        "    except (ValueError, RecursionError):\n        return None",
    ),
    M(
        "backtick-allowed: a construct the tokenizer cannot split is run",
        '    if "`" in command:',
        "    if False:",
    ),
    M(
        "newlines-not-separated: only a fence's FIRST line is judged",
        "        text = gc.newlines_to_separators(\n"
        "            gc.strip_comments(gc.mask_heredoc_quotes(command))\n"
        "        )",
        "        text = gc.strip_comments(gc.mask_heredoc_quotes(command))",
    ),
    M(
        "git-invocations-unread: no git subcommand is judged at all",
        "    for effective_dir, _cdir, subcommand, _args in invocations:",
        "    for effective_dir, _cdir, subcommand, _args in ():",
    ),
    M(
        "unresolvable-cwd-allowed: git runs in a directory the gate cannot resolve",
        "        if effective_dir is None:",
        "        if False:",
    ),
    M(
        "writing-subcommand-allowed: push/fetch/commit run",
        "        if subcommand in WRITING_SUBCOMMANDS:",
        "        if False:",
    ),
    M(
        "wrapper-word-unjudged: xargs/eval are skipped past instead of judged",
        "            positions.append(i)\n            continue\n        positions.append(i)\n        at_start = False",
        "            continue\n        positions.append(i)\n        at_start = False",
    ),
    M(
        "command-position-hand-rolled: a reserved word stops ending the previous command",
        "            token in OPERATORS\n"
        "            or token in gc.RESERVED_WORDS\n"
        "            or gc.is_op(token)\n"
        "            or gc.is_redirect(token)",
        "            token in OPERATORS",
    ),
    M(
        "git-only-wrappers-off: `exec curl …` hides behind exec",
        "        if token in gc.WRAPPERS or token in gc.GIT_ONLY_WRAPPERS:",
        "        if token in gc.WRAPPERS:",
    ),
    M(
        "pwd-not-expanded: $PWD/.. absorbs a directory level again",
        '        return re.sub(r"\\$\\{?PWD\\}?", ".", token)',
        "        return token",
    ),
    M(
        "residual-dollar-allowed: a quoted $( ) is treated as readable",
        '        if re.search(r"\\$(?=\\S)", VAR.sub("", token)):',
        "        if False:",
    ),
    M(
        "trailing-dollar-refused: a regex anchor is read as an expansion, over-blocking",
        '        if re.search(r"\\$(?=\\S)", VAR.sub("", token)):',
        '        if "$" in VAR.sub("", token):',
    ),
    M(
        "command-position-ignored: a tool name anywhere reads as that tool",
        "        if i in positions:",
        "        if True:",
    ),
    M(
        "network-commands-off",
        "            if word in NETWORK_COMMANDS:",
        "            if False:",
    ),
    M(
        "opaque-runners-off: xargs/eval arguments are treated as readable",
        "            if word in OPAQUE_RUNNERS:",
        "            if False:",
    ),
    M(
        "shell-dash-c-off: an opaque command string is treated as readable",
        "            if word in SHELLS and _takes_command_string(tokens, i):",
        "            if False:",
    ),
    M(
        "installers-off",
        "            if word in INSTALLERS and any(\n"
        "                a in INSTALL_VERBS for a in tokens[i + 1 : i + 3]\n"
        "            ):",
        "            if False:",
    ),
    M(
        "dir-changers-off: cd/pushd out of the clone is allowed",
        "            if word in DIR_CHANGERS:",
        "            if False:",
    ),
    M(
        "bare-cd-allowed: a bare cd to the home directory is allowed",
        "                if not target or target in OPERATORS:",
        "                if False:",
    ),
    M(
        "dir-option-unchecked: a -C symlink target out of the clone is allowed",
        "        if token in DIR_OPTIONS and i + 1 < len(tokens):",
        "        if False:",
    ),
    M(
        "dir-option-equals-unchecked: --git-dir=OUT is allowed",
        "        if len(option) == 2 and option[0] in DIR_OPTIONS and outside(option[1]):",
        "        if False:",
    ),
    M(
        "assignment-target-unjudged: the assignment token itself is never resolved",
        '            if TILDE.search(value) or (path_like(value) and outside(value)):\n                return f"assigns a path outside the clone: {token}"',
        "            if False:\n                return None",
    ),
    M(
        "assigned-var-unjudged: an ALIASED variable launders its value",
        "            unknown = [n for n in VAR.findall(value) if n not in SAFE_VARS]",
        "            unknown = []",
    ),
    M(
        "assigned-expansion-unjudged: an assigned $( ) launders its value",
        '            if re.search(r"\\$(?=\\S)", VAR.sub("", value)):',
        "            if False:",
    ),
    M(
        "shell-c-literal-window: -euc / -lc / -o overrun evade the command-string check",
        "            if word in SHELLS and _takes_command_string(tokens, i):",
        '            if word in SHELLS and "-c" in tokens[i + 1 : i + 4]:',
    ),
    M(
        "shell-option-value-not-skipped: `-o pipefail` stops the scan",
        '        if token in ("-o", "+o"):',
        "        if False:",
    ),
    M(
        "python-judged-as-shell: a python body gets shell syntax rules and shell advice",
        '    if lang == "python":',
        "    if False:",
    ),
    M(
        "python-paths-unjudged: an absolute path in a python body is allowed",
        "            if not (landed == root or root in landed.parents):",
        "            if False:",
    ),
    M(
        "substitution-assignment-trusted: X=$(cat f) makes $X resolvable",
        '            if "$" not in value or not VAR.sub("", value).count("$"):',
        "            if True:",
    ),
    M(
        "tilde-off: ~ expands to the home directory unchecked",
        "        if TILDE.search(token):",
        "        if False:",
    ),
    M(
        "vars-off: $HOME and friends expand unchecked",
        "        for name in VAR.findall(token):",
        "        for name in ():",
    ),
    M(
        "locally-assigned-not-honoured: an in-block temporary is refused, over-blocking",
        "            if name not in SAFE_VARS and name not in locally_assigned:",
        "            if name not in SAFE_VARS:",
    ),
    M(
        "opt-prefix-not-stripped: --file=/abs/path hides its path",
        "        candidate = (\n"
        '            token.split("=", 1)[1]\n'
        '            if ("=" in token and token.startswith("-"))\n'
        "            else token\n"
        "        )",
        "        candidate = token",
    ),
    M(
        "sinks-refused: /dev/null is treated as an escape, over-blocking",
        "        if candidate in SINKS:",
        "        if False:",
    ),
    M(
        "path-tokens-unchecked: an absolute or ../ path is allowed",
        "        if path_like(candidate) and outside(candidate):",
        "        if False:",
    ),
    M(
        "containment-by-string-prefix: a sibling directory passes as inside",
        "        return not (landed == root or root in landed.parents)",
        "        return not str(landed).startswith(str(root))",
    ),
    M(
        "refused-runs-anyway: a refused command is executed instead of reported",
        '            rows.append(Row(check, "REFUSED", escaped, None, ""))\n            continue',
        "            pass",
    ),
    # ---- taint
    M(
        "bad-base-reads-as-differs: git rc=128 clears the taint instead of setting it",
        "            if rc not in (0, 1):",
        "            if False:",
    ),
    M(
        "base-unvalidated: an unresolvable --base reaches the rehearsal",
        "    if probe.returncode != 0:",
        "    if False:",
    ),
    M(
        "create-discards-branch-failure",
        '        _git(clone, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode\n'
        "        != 0\n"
        "    ):",
        "        False\n    ):",
    ),
    M(
        "unreadable-spelled-as-taint: a declaration no edit can clear reads as work outstanding",
        "        if unreadable and not stale:",
        "        if False:",
    ),
    M(
        "unresolved-expansion-is-a-path-component: one hop launders a parent reference",
        '        if "$" in token:\n            return True',
        "        if False:\n            return True",
    ),
    M(
        "unreadable-drops-the-finding: an expectation mismatch is silenced on an UNREADABLE row",
        '                    f"{unreadable_reason}; {reason}" if reason else unreadable_reason,',
        "                    unreadable_reason,",
    ),
    M(
        "flagged-skips-unreadable: a finding at an unreadable row misses the terminal line",
        '        "flagged": sum(1 for r in rows if r.finding),',
        '        "flagged": 0,',
    ),
    M(
        "state-words-counted-as-findings: flagged becomes the row count",
        '                    f"{unreadable_reason}; {reason}" if reason else unreadable_reason,\n'
        "                    rc,\n                    output,\n                    bool(reason),",
        '                    f"{unreadable_reason}; {reason}" if reason else unreadable_reason,\n'
        "                    rc,\n                    output,\n                    False,",
    ),
    M(
        "clone-has-no-pre-push-hook: a rehearsed check can push from the clone",
        "    _install_deny_all_pre_push(clone)\n    # The hook's two neighbours",
        "    # The hook's two neighbours",
    ),
    M(
        # The exact defect this file's own reorder repaired: the removal sat BELOW the
        # base-resolution return, so a residual clone kept a live `origin` on the operator's
        # real repo while the scope note claimed it had none. It must RELOCATE, not delete:
        # deleting is already the `create-keeps-origin` row, which dies to a SUCCESS-path
        # row -- which is exactly why the only-on-success shape stayed invisible. A relocation
        # needs a two-part edit, and `Mutation` is one (old, new) pair, so the span runs from
        # the branch call down to the return and puts the removal back on the far side of it.
        "remote-removed-only-on-success: a residual clone keeps origin on the real repo",
        '    made = _git(clone, "branch", base, f"origin/{base}")\n'
        '    _git(clone, "remote", "remove", "origin")\n'
        "    # Assert the OUTCOME, not the command's exit status. When `--branch` equals "
        "`--base` the\n"
        '    # branch already exists and `git branch` fails "already exists" \u2014 benign, and '
        "reporting that\n"
        "    # broke a passing row. What matters is only whether the base RESOLVES afterwards, "
        "because\n"
        "    # that is what every later comparison depends on.\n"
        "    if (\n"
        '        _git(clone, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode\n'
        "        != 0\n"
        "    ):\n"
        "        return (\n"
        '            f"cloned, but base {base!r} does not resolve afterwards "\n'
        "            f\"(git branch said: {made.stderr.strip() or 'nothing'})\"\n"
        "        )\n"
        "    return None\n",
        '    made = _git(clone, "branch", base, f"origin/{base}")\n'
        "    if (\n"
        '        _git(clone, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode\n'
        "        != 0\n"
        "    ):\n"
        "        return (\n"
        '            f"cloned, but base {base!r} does not resolve afterwards "\n'
        "            f\"(git branch said: {made.stderr.strip() or 'nothing'})\"\n"
        "        )\n"
        '    _git(clone, "remote", "remove", "origin")\n'
        "    return None\n",
    ),
    M(
        "taint-off: a check whose prerequisites never moved reads as clean",
        "        stale, unreadable = unmoved_prerequisites(clone, base, tasks, check.task)",
        "        stale, unreadable = [], []",
    ),
    M(
        "unparsed-task-is-clean: a declaration that could not be read is ignored entirely",
        "            unreadable.append(t.index)",
        "            pass",
    ),
    M(
        "untracked-is-unmoved: a newly created file reads as never applied",
        "            if rc == 0 and not _is_untracked(clone, path):",
        "            if rc == 0:",
    ),
    M(
        "taint-ignores-task-order: later tasks taint earlier checks",
        "        if t.index > upto:\n            break",
        "        if False:\n            break",
    ),
    M(
        "tainted-not-run: the base-tree reading is thrown away again",
        '        if stale:\n            taint = f"unchanged since base: ',
        '        if stale and False:\n            taint = f"unchanged since base: ',
    ),
    # ---- tree state
    M(
        "state-from-diff-only: an untracked-only tree reads as UNEDITED",
        "    return TreeState(diffstat, porcelain, bool(diffstat or porcelain), digest)",
        "    return TreeState(diffstat, porcelain, bool(diffstat), digest)",
    ),
    M(
        "porcelain-unread: the index is never consulted",
        '    porcelain = _git(clone, "status", "--porcelain", "-uall").stdout.strip()',
        '    porcelain = ""',
    ),
    # ---- running
    M(
        "runs-in-caller-cwd: the check grades whatever directory the caller sat in",
        "            cwd=clone,\n            timeout=timeout,",
        "            cwd=None,\n            timeout=timeout,",
    ),
    M(
        "timeout-is-a-verdict: an overrun reports a result instead of UNRUN",
        '    except subprocess.TimeoutExpired:\n        return None, f"(no verdict: exceeded the {timeout}s bound)"',
        '    except subprocess.TimeoutExpired:\n        return 0, "(timed out)"',
    ),
    M(
        "output-never-printed: the captured output is dropped from the report",
        "    if row.output:",
        "    if False:",
    ),
    # ---- honest reporting
    M(
        "literal-flag-off: a bare-literal expectation with a nonzero rc is not flagged",
        "        if literal and rc != 0:",
        "        if False:",
    ),
    M(
        "unedited-reads-complete: a base tree reports RAN",
        '    complete = rows and tally["ran"] == len(rows) and state.edited',
        '    complete = rows and tally["ran"] == len(rows)',
    ),
    M(
        "refused-forgiven: a plan whose every check was refused reports RAN",
        '    complete = rows and tally["ran"] == len(rows) and state.edited',
        '    complete = rows and tally["ran"] + tally["refused"] == len(rows) and state.edited',
    ),
    M(
        "empty-plan-reads-complete: a plan with no checks at all reports RAN",
        "    complete = rows and",
        "    complete = True and",
    ),
    M(
        "list-mode-executes: --list runs the commands it promised not to",
        '        if dry:\n            rows.append(Row(check, "UNRUN", "--list: nothing was executed", None, ""))\n            continue',
        '        if False:\n            rows.append(Row(check, "UNRUN", "--list: nothing was executed", None, ""))\n            continue',
    ),
    M(
        "unpaired-dropped: an Expected: with no command vanishes from the report",
        '            rows.append(Row(check, "UNPAIRED", "no command precedes it", None, ""))\n            continue',
        "            continue",
    ),
    # ---- preconditions and the clone builder
    M(
        "missing-plan-accepted: a nonexistent plan does not stop the run",
        "    if not plan.is_file():",
        "    if False:",
    ),
    M(
        "non-repo-clone-accepted: a directory that is not a git repo is graded",
        '    if not (clone / ".git").exists():',
        "    if False:",
    ),
    M(
        "create-keeps-origin: the clone can still reach the source remote",
        '    _git(clone, "remote", "remove", "origin")',
        "    pass",
    ),
    M(
        "create-leaves-commit-signing-on",
        '    _git(clone, "config", "commit.gpgsign", "false")',
        "    pass",
    ),
    M(
        "create-leaves-tag-signing-on",
        '    _git(clone, "config", "tag.gpgsign", "false")',
        "    pass",
    ),
    M(
        "create-overwrites-an-existing-dir",
        "    if clone.exists():",
        "    if False:",
    ),
    M(
        "help-is-an-error: --help returns 2",
        "        if exc.code == 0:  # --help is not a usage failure\n            raise",
        "        if False:\n            raise",
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
