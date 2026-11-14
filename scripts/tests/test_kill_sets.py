"""Rows for `scripts/kill-sets.py`, the kill-set instrument for mutation campaigns.

Every row drives the SHIPPED process (a subprocess of the tool) and reads its stdout and its
exit status, because the `RESULT:` line and the exit code are the verdict and an in-process
call never reaches process exit. The one exception is `t14a`, which loads the tool in-process
to call `load_campaign` over the real campaigns of this repository.

Each fixture is a mini repository under `tmp_path`: a copy of the real `scripts/lib/mutate.py`,
a campaign `scripts/tests/mutate_fix.py`, a subject and a suite. The shell suite prints
`PASS  rowN` when the subject holds the line `okN` and `FAIL  rowN (detail)` otherwise, so a
mutant that removes one token kills one row; directive lines in the subject (`silent2`, `dump`,
`orphan`, ...) make the suite produce the other shapes a row needs. No fixture runs `git`.

Rows are named `t<NN>` after the spec's T-ids so a campaign label can cite them.
"""

from __future__ import annotations

import ast
import importlib.util
import math
import os
import re
import secrets
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "scripts" / "kill-sets.py"
MUTATE = REPO / "scripts" / "lib" / "mutate.py"
# The tool's own join bound, read from its source so a row never restates it.
JOIN_SECONDS = int(re.search(r"^JOIN_SECONDS = (\d+)", TOOL.read_text(), re.M).group(1))

EXPECT = (
    r"^RESULT: (PASS|FAIL|ERROR) rc=[0-9]+ mutants=[0-9]+ "
    r"identical_pairs=[0-9]+ killed_all=[0-9]+"
)
EXPECT_PARALLEL = (
    r"^RESULT: (PASS|PARALLEL-PASS|FAIL|ERROR) rc=[0-9]+ mutants=[0-9]+ "
    r"identical_pairs=[0-9]+ killed_all=[0-9]+"
)
FIELDS = [
    "mutants",
    "identical_pairs",
    "killed_all",
    "survivors",
    "unattributed",
    "invalid",
    "timedout",
    "notapplied",
    "incomplete",
    "single_row",
]
FIELDS_PARALLEL = FIELDS + ["died", "jobs"]
SERIAL_SENTENCE = "serial, one run per mutant: determinism NOT assessed"
OPAQUE_REASON = "opaque -c body: pytest cannot be armed; increment 1 does not grade it"
WATCHED = [
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "HOOK_TESTS_ONLY",
    "CLAUDE_PROJECT_DIR",
    "AUDIT_TEST_JOBS",
]

CAMPAIGN = """\
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__){resolve}.parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / {subject!r}
MUTATIONS = [mutate.Mutation(*m) for m in {mutations!r}]
SUITE = {suite}


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
"""

SHELL_ARGV = '["bash", str(REPO / "scripts" / "tests" / "suite.sh")]'
PYTEST_ARGV = (
    '[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", '
    'str(REPO / "scripts" / "tests" / "test_subj.py")]'
)

# The generic shell suite. Each row N passes when the subject holds the line `okN`; the other
# per-row spellings are the shapes the rows need: `silentN` fails the run with no FAIL line,
# `twofailN` prints two FAIL lines for one row, `quietN` drops the row under exit 0, `skipN`
# turns it into a skip under exit 0. Run-level directives follow the loop; `slow` holds the
# run for two seconds after the rows, so mutants that carry it overlap on parallel workers,
# and `hang` holds it for thirty, so a mutant that carries it runs into a short `--timeout`.
GENERIC_SUITE = r"""#!/usr/bin/env bash
subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"
has() { grep -qx -- "$1" "$subj"; }
fails=0
for n in $(seq 1 @ROWS@); do
  if has "ok$n"; then echo "PASS  row$n"
  elif has "silent$n"; then fails=1
  elif has "twofail$n"; then echo "FAIL  row$n first"; echo "FAIL  row$n second"; fails=1
  elif has "quiet$n"; then :
  elif has "skip$n"; then echo "SKIP  row$n"
  else
    echo "FAIL  row$n (detail)"; fails=1
    if has stop; then exit 1; fi
  fi
done
if has slow; then sleep 2; fi
if has hang; then sleep 30; fi
if has dump; then
  echo "  --- output ---"; echo "PASS  row1"; echo "FAIL  inner row"; echo "  --------------"
fi
if has dumpok; then
  echo "  --- output ---"; echo "PASS  row1"; echo "  --------------"
fi
if has orphan; then echo "FAIL  could not derive the marker"; fails=1; fi
if has extra; then echo "PASS  extra1"; echo "PASS  extra2"; fi
if has leakout; then echo "want [PASS  zz"; echo "PASS  zz2]"; fi
if has leakdup; then echo "PASS  row2"; fi
if has leak3; then echo "PASS  row3"; fi
if has bytes; then printf 'PASS  \377\376 junk\n'; fi
if has die; then exit 1; fi
@EXTRA@
exit $fails
"""


def generic_suite(rows: int = 3, extra: str = "") -> str:
    return GENERIC_SUITE.replace("@ROWS@", str(rows)).replace("@EXTRA@", extra)


def tokens(rows: int = 3) -> str:
    return "".join(f"ok{i}\n" for i in range(1, rows + 1))


def make_root(
    tmp_path: Path,
    *,
    family: str = "shell",
    subject_text: str | None = None,
    suite_text: str | None = None,
    mutations=(),
    suite_argv: str | None = None,
    campaign_text: str | None = None,
    resolve: bool = True,
    name: str = "root",
) -> Path:
    """Write a mini repository and return its root.

    `suite_argv` is Python SOURCE for the campaign's `SUITE` (it may use `REPO`), so a case can
    hard-code a live path or build one from the copy's own location.
    """
    root = tmp_path / name
    (root / "scripts" / "lib").mkdir(parents=True)
    (root / "scripts" / "tests").mkdir(parents=True)
    shutil.copy(MUTATE, root / "scripts" / "lib" / "mutate.py")
    if family == "shell":
        subject = "scripts/subject.sh"
        suite = root / "scripts" / "tests" / "suite.sh"
        default_suite = generic_suite()
        default_argv = SHELL_ARGV
        default_subject = tokens()
    else:
        subject = "scripts/subj.py"
        suite = root / "scripts" / "tests" / "test_subj.py"
        default_suite = (
            "from pathlib import Path\n\n"
            "TEXT = (Path(__file__).resolve().parent.parent / 'subj.py').read_text()\n\n\n"
            "def test_ok1():\n    assert 'ok1' in TEXT\n\n\n"
            "def test_ok2():\n    assert 'ok2' in TEXT\n"
        )
        default_argv = PYTEST_ARGV
        default_subject = "OK = ['ok1', 'ok2']\n"
    (root / subject).write_text(
        default_subject if subject_text is None else subject_text
    )
    suite.write_text(default_suite if suite_text is None else suite_text)
    text = campaign_text
    if text is None:
        text = CAMPAIGN.format(
            resolve=".resolve()" if resolve else "",
            subject=subject,
            mutations=[tuple(m) for m in mutations],
            suite=default_argv if suite_argv is None else suite_argv,
        )
    (root / "scripts" / "tests" / "mutate_fix.py").write_text(text)
    return root


class Run:
    """One finished tool process: rc, stdout, the parsed RESULT line and mutant blocks."""

    def __init__(self, proc: subprocess.CompletedProcess):
        self.rc = proc.returncode
        self.out = proc.stdout
        self.err = proc.stderr
        self.lines = self.out.splitlines()
        self.result = None
        self.order: list[str] = []
        for line in self.lines:
            m = re.match(r"^RESULT: (\S+) rc=(\d+)((?: \w+=\d+)*)$", line)
            if m:
                fields = dict(kv.split("=") for kv in m.group(3).split())
                self.order = list(fields)
                self.result = {k: int(v) for k, v in fields.items()}
                self.result["status"] = m.group(1)
                self.result["rc"] = int(m.group(2))
        self.blocks: dict[int, dict] = {}
        current = None
        for line in self.lines:
            m = re.match(r"^\[(\d+)/(\d+)\] (\S+)\s+k=(\d+)  (.*)$", line)
            if m:
                current = {
                    "status": m.group(3),
                    "k": int(m.group(4)),
                    "label": m.group(5),
                    "lines": [],
                    "rows": [],
                }
                self.blocks[int(m.group(1))] = current
            elif current is not None and line.startswith("          "):
                body = line.strip()
                current["lines"].append(body)
                if body.startswith("| "):
                    current["rows"].append(body[2:])
            else:
                current = None

    @property
    def status(self):
        return None if self.result is None else self.result["status"]

    def has_line(self, prefix: str) -> bool:
        return any(line.startswith(prefix) for line in self.lines)

    def error_lines(self) -> list[str]:
        return [line for line in self.lines if line.startswith("ERROR")]

    def __repr__(self) -> str:  # shown by pytest on a failed assertion
        return f"Run(rc={self.rc})\n{self.out}\n--- stderr ---\n{self.err}"


def run_tool(
    root: Path,
    *flags: str,
    env: dict | None = None,
    timeout: int = 300,
    tool: Path = TOOL,
    campaign: Path | None = None,
) -> Run:
    if campaign is None:
        campaign = root / "scripts" / "tests" / "mutate_fix.py"
    argv = [
        sys.executable,
        str(tool),
        "--campaign",
        str(campaign),
        "--root",
        str(root),
        *flags,
    ]
    proc = subprocess.run(
        argv,
        cwd=str(root.parent),
        env=os.environ.copy() if env is None else env,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    return Run(proc)


def load_tool():
    spec = importlib.util.spec_from_file_location("kill_sets", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_refused_before_baseline(run: Run, why: str) -> None:
    assert run.status == "ERROR", f"{why}: expected ERROR\n{run!r}"
    assert run.rc == 2, f"{why}: the exit code must be 2\n{run!r}"
    assert not run.has_line("BASELINE"), f"{why}: a baseline ran\n{run!r}"
    assert not run.blocks, f"{why}: a mutant ran\n{run!r}"


# --------------------------------------------------------------------------------------
# Slice 1.1: discovery, copy faithfulness, the opaque refusal, pre-run errors.
# --------------------------------------------------------------------------------------

OPAQUE_SHAPES = [
    '["python3", "-c", "pass"]',
    '["bash", "-c", "true"]',
    '["env", "X=1", "python3", "-c", "pass"]',
    '["bash", "-ec", "true"]',
    '["python3", "-I", "-c", "pass"]',
    '["bash", "-o", "pipefail", "-c", "true"]',
    '["env", "bash", "-c", "true"]',
    '["zsh", "-c", "true"]',
    '["dash", "-c", "true"]',
    '["/bin/bash", "-c", "true"]',
    # `env` options before the head, including the value-taking ones and `-S`'s split string.
    '["env", "-i", "python3", "-c", "pass"]',
    '["env", "-u", "X", "bash", "-c", "true"]',
    '["env", "-uX", "python3", "-c", "pass"]',
    '["env", "-S", "bash -c true"]',
    '["env", "--", "bash", "-c", "true"]',
    # A `/` inside an option VALUE is not a script path: the scan must reach the `-c`.
    '["python3", "-X", "pycache_prefix=/tmp", "-c", "pass"]',
    '["python3", "-Werror", "-c", "pass"]',
]

SHELL_PATH = 'str(REPO / "scripts" / "tests" / "suite.sh")'
PYTEST_PATH = 'str(REPO / "scripts" / "tests" / "test_subj.py")'
CONTROL_SHAPES = [
    ("shell", SHELL_ARGV),
    ("shell", f'["env", "X=1", "bash", {SHELL_PATH}]'),
    ("pytest", PYTEST_ARGV),
    # A `c` inside an option's VALUE is not a `-c`: `-Werror::DeprecationWarning`.
    (
        "pytest",
        '[sys.executable, "-Wignore::DeprecationWarning", "-m", "pytest", "-q", '
        f'"-p", "no:cacheprovider", {PYTEST_PATH}]',
    ),
    ("shell", f'["env", "-u", "KILL_SETS_NOT_SET", "bash", {SHELL_PATH}]'),
    ("shell", f'["bash", "-o", "pipefail", {SHELL_PATH}]'),
    # An interpreter option whose value is the NEXT token, before `-m pytest`.
    (
        "pytest",
        '[sys.executable, "-W", "ignore::DeprecationWarning", "-m", "pytest", "-q", '
        f'"-p", "no:cacheprovider", {PYTEST_PATH}]',
    ),
    # `env` options before the interpreter.
    (
        "pytest",
        '["env", "-u", "KILL_SETS_NOT_SET", sys.executable, "-m", "pytest", "-q", '
        f'"-p", "no:cacheprovider", {PYTEST_PATH}]',
    ),
    (
        "pytest",
        '["env", "-i", sys.executable, "-m", "pytest", "-q", '
        f'"-p", "no:cacheprovider", {PYTEST_PATH}]',
    ),
]


def test_t04a_opaque_argv(tmp_path):
    for i, shape in enumerate(OPAQUE_SHAPES):
        root = make_root(
            tmp_path,
            mutations=[("m", "ok1\n", "no1\n")],
            suite_argv=shape,
            name=f"o{i}",
        )
        run = run_tool(root)
        assert OPAQUE_REASON in run.out, f"{shape} was not refused as opaque\n{run!r}"
        assert any("mutate_fix.py" in line for line in run.error_lines()), (
            f"{shape}: the refusal does not name the campaign\n{run!r}"
        )
        assert_refused_before_baseline(run, shape)
    for i, (family, shape) in enumerate(CONTROL_SHAPES):
        old = "ok1\n" if family == "shell" else "'ok1'"
        root = make_root(
            tmp_path,
            family=family,
            mutations=[("m", old, old.replace("ok1", "no1"))],
            suite_argv=shape,
            name=f"c{i}",
        )
        run = run_tool(root)
        assert OPAQUE_REASON not in run.out, f"control {shape} was refused\n{run!r}"
        if family == "pytest":
            suite_line = next((x for x in run.lines if x.startswith("suite: ")), "")
            assert suite_line.endswith(" -rpfE"), (
                f"control {shape} was not armed as pytest\n{run!r}"
            )
        assert run.has_line("BASELINE green"), (
            f"control {shape} ran no baseline\n{run!r}"
        )
        one = run.blocks.get(1)
        assert one and one["status"] == "KILLED" and one["k"] == 1, (
            f"control {shape} graded no mutant\n{run!r}"
        )
        assert run.status == "PASS", run


def _named_error(run: Run, needle: str, why: str) -> None:
    assert run.status == "ERROR", f"{why}: expected ERROR\n{run!r}"
    assert any(needle in line for line in run.error_lines()), (
        f"{why}: no ERROR line names {needle!r}\n{run!r}"
    )
    assert not run.blocks, f"{why}: a mutant ran\n{run!r}"


def test_t11_copy_faithfulness(tmp_path):
    kill_one = [("m", "ok1\n", "no1\n")]

    # The five ERROR cases: each names the path that resolves into the live tree or master.
    live = tmp_path / "live"
    root = make_root(tmp_path, name="live", mutations=kill_one)
    live_suite = str(live / "scripts" / "tests" / "suite.sh")
    (root / "scripts" / "tests" / "mutate_fix.py").write_text(
        CAMPAIGN.format(
            resolve=".resolve()",
            subject="scripts/subject.sh",
            mutations=kill_one,
            suite=repr(["bash", live_suite]),
        )
    )
    _named_error(run_tool(root), live_suite, "a live path in SUITE")

    root = make_root(
        tmp_path,
        name="master",
        mutations=kill_one,
        suite_argv='["bash", str(REPO.parent / "master" / "scripts/tests/suite.sh")]',
    )
    _named_error(run_tool(root), "/master/scripts/tests/suite.sh", "a path under M")

    nowhere = str(tmp_path / "gone" / "scripts" / "tests" / "nope.sh")
    root = make_root(tmp_path, name="gone", mutations=kill_one)
    (root / "scripts" / "tests" / "mutate_fix.py").write_text(
        CAMPAIGN.format(
            resolve=".resolve()",
            subject="scripts/subject.sh",
            mutations=kill_one,
            suite=f'["bash", str(REPO / "scripts/tests/suite.sh"), {nowhere!r}]',
        )
    )
    _named_error(run_tool(root), nowhere, "a nonexistent path under L")

    for name, token in (
        ("envtok", "SUBJ={live}/scripts/subject.sh"),
        ("opttok", "--opt={live}/scripts/subject.sh"),
    ):
        root = make_root(tmp_path, name=name, mutations=kill_one)
        value = token.format(live=root)
        if name == "envtok":
            suite = f'["env", {value!r}, "bash", str(REPO / "scripts/tests/suite.sh")]'
        else:
            suite = f'["bash", str(REPO / "scripts/tests/suite.sh"), {value!r}]'
        (root / "scripts" / "tests" / "mutate_fix.py").write_text(
            CAMPAIGN.format(
                resolve=".resolve()",
                subject="scripts/subject.sh",
                mutations=kill_one,
                suite=suite,
            )
        )
        _named_error(run_tool(root), str(root / "scripts" / "subject.sh"), name)

    # A path outside every tree is recorded in the header and not judged.
    outside = "/usr/share/kill-sets-not-a-file"
    root = make_root(
        tmp_path,
        name="outside",
        mutations=kill_one,
        suite_argv=f'["bash", str(REPO / "scripts/tests/suite.sh"), {outside!r}]',
    )
    run = run_tool(root)
    assert any(outside in line and "recorded" in line for line in run.lines), (
        f"the outside-every-tree path is not recorded in the header\n{run!r}"
    )
    assert not run.error_lines() and run.status == "PASS", run

    # Preflight: a backup sidecar and a git lock each ERROR before any mutant.
    root = make_root(tmp_path, name="backup", mutations=kill_one)
    backup = root / "scripts" / "subject.sh.mutate-backup"
    backup.write_text(tokens())
    run = run_tool(root)
    _named_error(run, str(backup), "a live .mutate-backup")
    assert_refused_before_baseline(run, "a live .mutate-backup")

    root = make_root(tmp_path, name="lock", mutations=kill_one)
    (root / ".git").mkdir()
    lock = root / ".git" / "index.lock"
    lock.write_text("")
    run = run_tool(root)
    _named_error(run, str(lock), "a .git/index.lock")
    assert_refused_before_baseline(run, "a .git/index.lock")

    # A `.git` that is not a directory (a worktree's or submodule's gitdir FILE, or a
    # symlink) would be copied as a pointer, so every suite's git would act on the LIVE git
    # directory. Refused before any copy, and nothing is created inside the root.
    elsewhere = tmp_path / "elsewhere" / ".git"
    (elsewhere / "worktrees" / "wt").mkdir(parents=True)
    for name, make in (
        (
            "gitfile",
            lambda p: p.write_text(f"gitdir: {elsewhere / 'worktrees' / 'wt'}\n"),
        ),
        ("gitlink", lambda p: p.symlink_to(elsewhere)),
    ):
        root = make_root(tmp_path, name=name, mutations=kill_one)
        make(root / ".git")
        before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        run = run_tool(root)
        _named_error(
            run, "the copy would share the live git directory", f"a .git {name}"
        )
        assert any(str(root / ".git") in line for line in run.error_lines()), (
            f"the refusal does not name the .git path\n{run!r}"
        )
        assert_refused_before_baseline(run, f"a .git {name}")
        assert not run.has_line("copy: "), f"a .git {name}: a copy was made\n{run!r}"
        after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        assert after == before, f"a .git {name}: the run wrote under the root"

    # A campaign that does not bind the name `mutate` proceeds and says so.
    unbound = (
        "from pathlib import Path\n"
        "REPO = Path(__file__).resolve().parent.parent.parent\n"
        "SUBJECT = REPO / 'scripts' / 'subject.sh'\n"
        "MUTATIONS = [('m', 'ok1\\n', 'no1\\n')]\n"
        "SUITE = ['bash', str(REPO / 'scripts' / 'tests' / 'suite.sh')]\n"
    )
    root = make_root(tmp_path, name="unbound", campaign_text=unbound)
    run = run_tool(root)
    assert "campaign mutate path: not bound" in run.out, run
    assert run.status == "PASS", run

    # /var against /private/var, the production shape: the working copy is SPELLED through a
    # symlinked TMPDIR while the campaign resolves its own path, so SUBJECT and the argv carry
    # the REAL spelling. Only a realpath on both sides finds them inside the working copy.
    real = tmp_path / "realtmp"
    real.mkdir()
    link = tmp_path / "linktmp"
    link.symlink_to(real)
    root = make_root(tmp_path, name="spelling", mutations=kill_one, resolve=True)
    env = os.environ.copy()
    env["TMPDIR"] = str(link)
    run = run_tool(root, env=env)
    copy_line = next((x for x in run.lines if x.startswith("copy: ")), "")
    assert f"copy: {link}{os.sep}kill-sets." in copy_line, (
        f"the working copy is not spelled through the link, so the case compares one "
        f"spelling\n{run!r}"
    )
    suite_line = next((x for x in run.lines if x.startswith("suite: ")), "")
    assert f"{real}{os.sep}kill-sets." in suite_line, (
        f"the argv does not carry the real spelling\n{run!r}"
    )
    assert run.has_line("BASELINE green"), f"the two spellings did not proceed\n{run!r}"
    assert not run.error_lines() and run.status == "PASS", run

    # TMPDIR inside the root: the copies would land inside the tree they copy. Refused
    # before anything is copied, and nothing is left under the root.
    root = make_root(tmp_path, name="tmpin", mutations=kill_one)
    inner = root / "tmpdir"
    inner.mkdir()
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    env = os.environ.copy()
    env["TMPDIR"] = str(inner)
    run = run_tool(root, env=env)
    assert any(
        line.startswith("ERROR  copy unproven: ") and "overlap" in line
        for line in run.error_lines()
    ), f"TMPDIR inside the root was not refused as an overlap\n{run!r}"
    assert_refused_before_baseline(run, "TMPDIR inside the root")
    after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    assert after == before, (
        f"the run wrote under the root: {sorted(set(after) - set(before))[:5]}"
    )

    # The campaign path: missing, outside the root, a symlink escaping it.
    root = make_root(tmp_path, name="cpath", mutations=kill_one)
    missing = root / "scripts" / "tests" / "mutate_missing.py"
    run = run_tool(root, campaign=missing)
    _named_error(run, str(missing), "a missing campaign path")
    stray = tmp_path / "stray"
    stray.mkdir()
    escaped = stray / "mutate_fix.py"
    shutil.copy(root / "scripts" / "tests" / "mutate_fix.py", escaped)
    run = run_tool(root, campaign=escaped)
    _named_error(run, str(escaped), "a campaign outside the root")
    linked = root / "scripts" / "tests" / "mutate_link.py"
    linked.symlink_to(escaped)
    run = run_tool(root, campaign=linked)
    _named_error(run, str(linked), "a campaign symlink escaping the root")

    # A root whose path holds a space.
    spaced = tmp_path / "with space"
    spaced.mkdir()
    root = make_root(spaced, name="root", mutations=kill_one)
    run = run_tool(root)
    assert run.has_line("BASELINE green"), f"a spaced root ran no baseline\n{run!r}"
    one = run.blocks.get(1)
    assert one and one["status"] == "KILLED" and one["rows"] == ["row1"], run
    assert not run.error_lines() and run.status == "PASS", run


def _campaign_with(body: str) -> str:
    return (
        "from __future__ import annotations\n"
        "import sys\n"
        "from pathlib import Path\n"
        "REPO = Path(__file__).resolve().parent.parent.parent\n"
        "sys.path.insert(0, str(REPO / 'scripts' / 'lib'))\n"
        "import mutate  # noqa: E402\n"
        "SUBJECT = REPO / 'scripts' / 'subject.sh'\n"
        "MUTATIONS = [mutate.Mutation('m', 'ok1\\n', 'no1\\n')]\n"
        "SUITE = ['bash', str(REPO / 'scripts' / 'tests' / 'suite.sh')]\n" + body
    )


def _tool_copy(tmp_path: Path, name: str, mutate_text: str | None, edit=None) -> Path:
    """A copy of the tool in its own `scripts/`, with its own `lib/mutate.py`."""
    scripts = tmp_path / name / "scripts"
    (scripts / "lib").mkdir(parents=True)
    text = TOOL.read_text()
    if edit is not None:
        old, new = edit
        assert text.count(old) == 1, f"the tool copy cannot be edited: {old!r}"
        text = text.replace(old, new)
    (scripts / "kill-sets.py").write_text(text)
    (scripts / "lib" / "mutate.py").write_text(
        MUTATE.read_text() if mutate_text is None else mutate_text
    )
    return scripts / "kill-sets.py"


def test_t14a_loader_and_prerun_errors(tmp_path):
    cases = [
        ("import", _campaign_with("raise RuntimeError('boom at import')\n"), "boom"),
        ("exit", _campaign_with("sys.exit(3)\n"), "SystemExit"),
        ("notpath", _campaign_with("SUBJECT = str(SUBJECT)\n"), "not a Path"),
        ("zero", _campaign_with("MUTATIONS = []\n"), "no mutants"),
        (
            "same",
            _campaign_with("MUTATIONS = [mutate.Mutation('m', 'ok1', 'ok1')]\n"),
            "old == new",
        ),
    ]
    for name, text, needle in cases:
        root = make_root(tmp_path, name=name, campaign_text=text)
        run = run_tool(root)
        _named_error(run, needle, name)
        assert_refused_before_baseline(run, name)

    # The tool's own `mutate` missing a name it uses.
    root = make_root(tmp_path, name="compat", mutations=[("m", "ok1\n", "no1\n")])
    stripped = _tool_copy(
        tmp_path, "stripped", '"""A mutate.py with nothing in it."""\n'
    )
    run = run_tool(root, tool=stripped)
    _named_error(run, "incompatible mutate.py", "a stripped mutate.py")

    # An unexpected internal exception: a SUBJECT that is a directory.
    root = make_root(
        tmp_path, name="dirsubj", campaign_text=_campaign_with("SUBJECT = REPO\n")
    )
    run = run_tool(root)
    assert "Traceback" in run.out, f"the exception was not printed\n{run!r}"
    tail = run.out[run.out.rindex("Traceback") :]
    assert re.search(r"^RESULT: ERROR rc=2 ", tail, re.M), run
    assert run.rc == 2, run

    # The copy-size constant refuses a tree over it.
    root = make_root(tmp_path, name="big", mutations=[("m", "ok1\n", "no1\n")])
    (root / "blob.bin").write_bytes(b"\0" * (2 * 1024 * 1024))
    small = _tool_copy(
        tmp_path, "small", None, edit=("MAX_COPY_MB = 1024", "MAX_COPY_MB = 1")
    )
    run = run_tool(root, tool=small)
    _named_error(run, "MAX_COPY_MB", "a tree over MAX_COPY_MB")
    assert_refused_before_baseline(run, "MAX_COPY_MB")

    # A Python without os.waitid is a named startup error.
    root = make_root(tmp_path, name="nowaitid", mutations=[("m", "ok1\n", "no1\n")])
    campaign = root / "scripts" / "tests" / "mutate_fix.py"
    driver = (
        "import os, runpy, sys\n"
        "del os.waitid\n"
        "sys.argv = [sys.argv[1], '--campaign', sys.argv[2], '--root', sys.argv[3]]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", driver, str(TOOL), str(campaign), str(root)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
    )
    run = Run(proc)
    _named_error(run, "os.waitid", "a Python without os.waitid")
    assert_refused_before_baseline(run, "os.waitid")

    # In-process: the loader over the real campaigns, and the mutate names the tool uses.
    tool = load_tool()
    loaded, failed, refused = 0, [], set()
    for path in sorted((REPO / "scripts" / "tests").glob("mutate_*.py")):
        try:
            c = tool.load_campaign(REPO, path)
        except tool.KillSetsError as exc:
            failed.append(f"{path.name}: {exc}")
            continue
        assert isinstance(c.subject, Path) and c.subject.is_absolute(), path
        assert c.mutations and all(len(m) == 3 for m in c.mutations), path
        assert c.suite and all(isinstance(t, str) for t in c.suite), path
        if tool.opaque(c.suite):
            refused.add(path.name)
            assert "-c" in c.suite, (
                f"{path.name} refused as opaque with no -c: {c.suite}"
            )
        loaded += 1
    assert loaded >= 40, f"only {loaded} real campaigns loaded: {failed}"
    # The two real `-c` wrappers are refused by the detector over their REAL argv.
    wrappers = {
        "mutate_plan_corpus_floor.py",
        "mutate_publication_push_guard_spawns.py",
    }
    assert wrappers <= refused, f"a real -c wrapper was not refused: {refused}"

    used = {
        node.attr
        for node in ast.walk(ast.parse(TOOL.read_text()))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "mutate"
    }
    real = load_mutate()
    assert used, "the tool uses no mutate name at all"
    assert all(not name.startswith("_") for name in used), used
    assert all(hasattr(real, name) for name in used), used
    assert set(used) <= set(tool.REQUIRED_MUTATE_NAMES), used

    root = make_root(tmp_path, name="ownmutate", mutations=[("m", "ok1\n", "no1\n")])
    c = tool.load_campaign(root, root / "scripts" / "tests" / "mutate_fix.py")
    assert c.mutate_path is not None
    assert os.path.realpath(c.mutate_path) != os.path.realpath(tool.mutate.__file__)
    assert os.path.realpath(c.mutate_path) == os.path.realpath(
        root / "scripts" / "lib" / "mutate.py"
    )


class _DoneEvent(threading.Event):
    """A ticket's `done` flag that lets the ticket finish at a chosen read of it.

    The first `is_set` call runs `finish` (which stores the ticket's error and sets the
    flag). `report_finished` says whether that first read already sees the finished state
    (the ticket completed just before the read) or the earlier one (it completed just after).
    """

    def __init__(self, finish, report_finished):
        super().__init__()
        self._finish, self._report, self._reads = finish, report_finished, 0

    def is_set(self):
        self._reads += 1
        if self._reads == 1:
            before = super().is_set()
            self._finish()
            return before or self._report
        return super().is_set()


def _failing_ticket(tool, error, report_finished):
    ticket = tool.Ticket(lambda: None)

    def finish():
        ticket.error = error
        threading.Event.set(ticket.done)

    ticket.done = _DoneEvent(finish, report_finished)
    return ticket


def _grading_over(tool, count):
    mutations = [(f"m{n}", "old", "new") for n in range(1, count + 1)]
    worker = tool.Worker(0, Path("."), Path("."), [])
    g = tool.Grading(
        types.SimpleNamespace(mutations=mutations), None, None, "s", "", None, [worker]
    )
    g.preclassify = lambda n, label, old, new: (None, "mutated")
    return g, worker


class _OneTicketPool:
    """Stands in for `Pool.submit` and `activity`: no thread, one ticket made by hand."""

    def __init__(self, workers, ticket):
        self.workers, self.activity, self._ticket = workers, threading.Event(), ticket

    def submit(self, index, fn):
        return self._ticket


def test_t14c_poll_loops_keep_a_ticket_error():
    tool = load_tool()
    boom = ValueError("the original failure")

    # (i) A drain ticket errors just after the poll loop read it as not done: the loop must
    # raise that error, not the "no outcome" RuntimeError of the gap check behind it.
    g, worker = _grading_over(tool, 2)
    ticket = _failing_ticket(tool, boom, report_finished=False)
    with pytest.raises(ValueError) as caught:
        g.run_mutants(tool.State(), _OneTicketPool([worker], ticket))
    assert caught.value is boom, (
        f"raised {caught.value!r} in place of the ticket's error"
    )

    # (i) again with the first read already seeing the finished ticket: a loop that releases,
    # scans, reads `finished` and only then breaks passes the setting above and fails this one.
    g, worker = _grading_over(tool, 2)
    ticket = _failing_ticket(tool, boom, report_finished=True)
    with pytest.raises(ValueError) as caught:
        g.run_mutants(tool.State(), _OneTicketPool([worker], ticket))
    assert caught.value is boom, (
        f"raised {caught.value!r} in place of the ticket's error (finished read)"
    )

    # (ii) The same window in `Pool.wait`: it must raise, never return a None result.
    ticket = _failing_ticket(tool, boom, report_finished=False)
    pool = tool.Pool([])
    try:
        pool.wait([ticket])
    except ValueError as exc:
        assert exc is boom
    else:
        pytest.fail("Pool.wait returned instead of raising the ticket's error")

    # (iii) An outcome stored just before the failure was released before the exception
    # propagated: blocks 1 to k-1 print, then the exception, as the serial tool did.
    g, worker = _grading_over(tool, 2)
    ticket = tool.Ticket(lambda: None)
    released = []
    g.release = lambda state, outcome: released.append(outcome.n)
    real_release_ready = g._release_ready
    calls = []

    def release_then_finish(state):
        real_release_ready(state)
        calls.append(1)
        if len(calls) == 1:
            with g._results_lock:
                g._results[1] = tool.Outcome(
                    1, "KILLED", (), ("[1/2] KILLED",), (), None
                )
            ticket.error = boom
            ticket.done.set()

    g._release_ready = release_then_finish
    with pytest.raises(ValueError) as caught:
        g.run_mutants(tool.State(), _OneTicketPool([worker], ticket))
    assert caught.value is boom
    assert released == [1], f"blocks released before the exception: {released}"


def test_t16b0_rebase_spellings(tmp_path):
    tool = load_tool()
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    logical_tmp = link / "kill-sets.ab"
    real_tmp = real / "kill-sets.ab"
    canonical = logical_tmp / "work"
    (real_tmp / "work" / "scripts").mkdir(parents=True)
    (real_tmp / "pool" / "1").mkdir(parents=True)
    # A sibling that shares the canonical text up to its last component and RESOLVES into the
    # canonical copy: inside it by realpath, yet not below it on a path boundary as text.
    (real_tmp / "workalias").symlink_to(real_tmp / "work")
    assert os.path.realpath(canonical) == str(real_tmp / "work")
    assert str(canonical) != os.path.realpath(canonical)
    target = logical_tmp / "pool" / "1"
    target_real = str(real_tmp / "pool" / "1")
    assert os.path.realpath(target) == target_real

    # The layout: worker 0 keeps `work`, worker k is `pool/k`.
    assert tool.worker_copy_path(logical_tmp, 0) == logical_tmp / "work"
    assert tool.worker_copy_path(logical_tmp, 3) == logical_tmp / "pool" / "3"
    assert tool.worker_copy_path(str(logical_tmp), 1) == target

    logical = str(canonical)
    realtext = str(real_tmp / "work")
    cases = [
        (
            "logical spelling",
            f"{logical}/scripts/suite.sh",
            f"{target}/scripts/suite.sh",
        ),
        (
            "realpath spelling",
            f"{realtext}/scripts/suite.sh",
            f"{target_real}/scripts/suite.sh",
        ),
        (
            "dotdot form",
            f"{logical}/scripts/../scripts/x.sh",
            f"{target_real}/scripts/x.sh",
        ),
        ("the copy root", logical, str(target)),
        ("the copy root, real", realtext, target_real),
        ("dotdot to the root", f"{logical}/scripts/..", target_real),
        ("X= prefix, logical", f"SUITE={logical}/s.sh", f"SUITE={target}/s.sh"),
        ("X= prefix, real", f"SUITE={realtext}/s.sh", f"SUITE={target_real}/s.sh"),
        ("outside the copy", "/usr/bin/env", "/usr/bin/env"),
        ("not a path", "-v", "-v"),
        ("relative", "scripts/suite.sh", "scripts/suite.sh"),
        ("sibling merely sharing the text", f"{logical}2/x", f"{logical}2/x"),
        ("sibling, real", f"{realtext}2/x", f"{realtext}2/x"),
        (
            "a dotdot back out of the copy",
            f"{logical}/../elsewhere/x",
            f"{logical}/../elsewhere/x",
        ),
        (
            "symlinked sibling sharing the text, logical",
            f"{logical}alias/x",
            f"{target_real}/x",
        ),
        (
            "symlinked sibling sharing the text, real",
            f"{realtext}alias/x",
            f"{target_real}/x",
        ),
    ]
    argv = [token for _, token, _ in cases]
    rebased = tool.rebase_argv(argv, canonical, target)
    assert isinstance(rebased, list) and rebased is not argv
    assert argv == [token for _, token, _ in cases], "the input list was changed"
    for (name, token, want), got in zip(cases, rebased, strict=True):
        assert got == want, f"{name}: {token!r} re-based to {got!r}, expected {want!r}"

    # The residue rule: a substring match on either spelling of the canonical copy.
    tokens = [token for _, token, _ in cases]
    flagged = {
        token
        for token in tokens
        if any(
            token in message for message in tool.residue_errors([token], canonical, 1)
        )
    }
    assert flagged == {
        logical,
        realtext,
        f"{logical}/scripts/suite.sh",
        f"{realtext}/scripts/suite.sh",
        f"{logical}/scripts/../scripts/x.sh",
        f"{logical}/scripts/..",
        f"SUITE={logical}/s.sh",
        f"SUITE={realtext}/s.sh",
        f"{logical}2/x",
        f"{realtext}2/x",
        f"{logical}/../elsewhere/x",
        f"{logical}alias/x",
        f"{realtext}alias/x",
    }, f"un-re-based tokens flagged: {sorted(flagged)}"
    good = [
        got
        for (name, _, _), got in zip(cases, rebased, strict=True)
        if name
        in (
            "logical spelling",
            "realpath spelling",
            "dotdot form",
            "the copy root",
            "the copy root, real",
            "dotdot to the root",
            "X= prefix, logical",
            "X= prefix, real",
            "outside the copy",
            "not a path",
            "relative",
            "symlinked sibling sharing the text, logical",
            "symlinked sibling sharing the text, real",
        )
    ]
    assert tool.residue_errors(good, canonical, 1) == [], (
        "a correct re-base left residue"
    )
    under_worker = [f"{tool.worker_copy_path(logical_tmp, 1)}/scripts/tests/suite.sh"]
    assert tool.residue_errors(under_worker, canonical, 1) == []

    survivors = [f"-C{logical}/scripts", f"a:{realtext}/x:b"]
    assert tool.rebase_argv(survivors, canonical, target) == survivors
    messages = tool.residue_errors(["ok", *survivors], canonical, 2)
    assert messages == [
        f"copy unproven: worker 2: SUITE token {token!r} still names the canonical copy {canonical}"
        for token in survivors
    ], messages


def load_mutate():
    spec = importlib.util.spec_from_file_location("mutate_real", MUTATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------------------
# Slice 1.2: the baseline, the shell reader, the statuses and the verdict.
# --------------------------------------------------------------------------------------

FLAGS = ("incomplete:", "excess:", "dup_in_run:")


def flags_of(block: dict) -> list[str]:
    return [line for line in block["lines"] if line.startswith(FLAGS)]


T01_SUITE = r"""#!/usr/bin/env bash
subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"
has() { grep -qx -- "$1" "$subj"; }
fails=0
if has ok1; then echo "PASS  row1"; else echo "FAIL  row1 (detail)"; fails=1; fi
if has ok2; then echo "PASS  row2 (rc 0, stderr empty)"
else printf 'FAIL  %s\n' "$(grep '^why ' "$subj" | sed 's/^why //')"; fails=1; fi
v=$(sed -n 's/^val=//p' "$subj")
echo "PASS  row3 ($v)"
echo "PASS  (whole label one)"
echo "PASS  (whole label two)"
exit $fails
"""


def test_t01_shell_duplicate_and_value_strip(tmp_path):
    root = make_root(
        tmp_path,
        subject_text="ok1\nok2\nval=19\n",
        suite_text=T01_SUITE,
        mutations=[
            ("unrelated FAIL text", "ok2\n", "why boom 17\n"),
            (
                "multi-line FAIL text",
                "ok2\n",
                "why x — message: first\nwhy second line\n",
            ),
            ("value in the group", "val=19\n", "val=20\n"),
        ],
    )
    run = run_tool(root)
    # row_base's empty-base decision (a departure from the spec's literal rule, recorded): a
    # label that IS one balanced group would strip to nothing, so it keeps its full text.
    # Under the literal rule the two whole-group labels share the base "" and the baseline
    # refuses them as indistinguishable.
    assert run.has_line("BASELINE green") and "rows=5" in run.out, (
        f"a label whose stripped base would be empty did not keep its full text\n{run!r}"
    )
    a, b, c = run.blocks.get(1), run.blocks.get(2), run.blocks.get(3)
    assert a and b and c, f"a mutant block is missing\n{run!r}"
    assert a["status"] == b["status"] == "KILLED", run
    assert a["rows"] == b["rows"] == ["row2"], f"the two kill sets differ\n{run!r}"
    assert not flags_of(a) and not flags_of(b), run
    assert run.has_line("DUPLICATE [1] [2]"), f"no DUPLICATE line\n{run!r}"
    assert run.result and run.result["identical_pairs"] == 1, run
    assert c["status"] == "SURVIVED" and c["k"] == 0, (
        f"a changed value inside the trailing group read as a kill\n{run!r}"
    )
    assert run.status == "FAIL" and run.rc == 1, run


T03_MUTANTS = [
    ("nested dump", "ok1\n", "dump\n"),
    ("silent row", "ok2\n", "silent2\n"),
    ("two FAIL lines", "ok3\n", "twofail3\n"),
    ("equal counts", "ok4\n", "silent4\norphan\n"),
    ("extra rows", "ok1\n", "extra\n"),
    ("leak outside U", "ok1\n", "leakout\n"),
    ("leak duplicates a row", "ok1\n", "leakdup\n"),
    ("leak hides a silent row", "ok1\nok2\nok3\n", "x1\nok2\nsilent3\nleak3\n"),
    ("non-UTF-8 stdout", "ok2\n", "bytes\n"),
]


def test_t03a_shell_absence_reading(tmp_path):
    root = make_root(
        tmp_path,
        subject_text=tokens(4),
        suite_text=generic_suite(4),
        mutations=T03_MUTANTS,
    )
    run = run_tool(root)
    assert run.result is not None, f"no RESULT line\n{run!r}"
    b = run.blocks
    assert len(b) == len(T03_MUTANTS), f"a block is missing\n{run!r}"

    assert b[1]["status"] == "KILLED" and b[1]["rows"] == ["row1"], (
        f"nested-dump rows were read\n{run!r}"
    )
    assert not flags_of(b[1]), run
    assert any(x.startswith("nested dumps: ") for x in b[1]["lines"]), run
    assert b[2]["status"] == "KILLED" and b[2]["rows"] == ["row2"], run
    assert flags_of(b[2]) == ["incomplete: 1 absent rows without a FAIL line"], run
    assert b[3]["status"] == "KILLED" and b[3]["rows"] == ["row3"], run
    assert flags_of(b[3]) == ["excess: 1 FAIL lines own no absent row"], run
    assert b[4]["status"] == "KILLED" and b[4]["rows"] == ["row4"], run
    assert not flags_of(b[4]), f"the equal-counts residual was flagged\n{run!r}"
    assert b[5]["status"] == "KILLED" and b[5]["rows"] == ["row1"], (
        f"extra pass rows changed the kill set\n{run!r}"
    )
    assert not flags_of(b[5]), run
    assert "+ extra1" in b[5]["lines"] and "+ extra2" in b[5]["lines"], run
    assert b[6]["rows"] == ["row1"] and "+ zz2]" in b[6]["lines"], run
    assert not flags_of(b[6]), run
    assert b[7]["rows"] == ["row1"], run
    assert flags_of(b[7]) == ["dup_in_run: 1 bases printed more than once"], run
    assert b[8]["status"] == "KILLED" and b[8]["rows"] == ["row1"], (
        f"the once-colliding leak did not read as passed\n{run!r}"
    )
    assert not flags_of(b[8]), run
    assert b[9]["status"] == "KILLED" and b[9]["rows"] == ["row2"], run

    # Unflagged KILLED with {row1}: 1, 5, 6, 8 -> 6 pairs; 7 is flagged and excluded.
    assert run.result["identical_pairs"] == 6, run
    assert not any("[7]" in x for x in run.lines if x.startswith("DUPLICATE")), run
    assert run.result["incomplete"] == 3, run
    assert run.status == "FAIL" and run.rc == 1, run

    # FAIL lines that own no absent row (noticed, kill empty) are UNATTRIBUTED.
    root = make_root(
        tmp_path,
        name="orphan",
        subject_text=tokens(4),
        suite_text=generic_suite(4),
        mutations=[
            ("orphan FAIL only", "ok4\n", "ok4\norphan\n"),
            ("a dump, then a failure naming no row", "ok4\n", "ok4\ndump\ndie\n"),
            ("a dump under a green run", "ok4\n", "ok4\ndumpok\n"),
        ],
    )
    run = run_tool(root)
    assert run.blocks.get(1, {}).get("status") == "UNATTRIBUTED", run
    # The nested-dumps diagnostic prints for every graded status, not only KILLED.
    for n, status in ((2, "UNATTRIBUTED"), (3, "SURVIVED")):
        block = run.blocks.get(n, {})
        assert block.get("status") == status, run
        assert any(x.startswith("nested dumps: ") for x in block["lines"]), (
            f"a {status} block does not name its nested-dump lines\n{run!r}"
        )
    assert run.status == "ERROR" and run.result["unattributed"] == 2, run


@pytest.mark.serial_only
def test_t04b_baseline_gating(tmp_path):
    def gate(name, suite_text, needles, *flags):
        root = make_root(
            tmp_path,
            name=name,
            suite_text="#!/usr/bin/env bash\n" + suite_text,
            mutations=[("m", "ok1\n", "no1\n")],
        )
        run = run_tool(root, *flags, timeout=120)
        for needle in needles:
            _named_error(run, needle, name)
        assert_refused_before_baseline(run, name)

    gate(
        "red", 'echo "PASS  a"\necho "FAIL  b last"\nexit 1\n', ["rc=1", "FAIL  b last"]
    )
    gate("blind", 'echo "ok 1 first"\necho "1..1 last"\n', ["blind", "ok 1 first"])
    gate("blind2", 'echo "ok 1 first"\necho "1..1 last"\n', ["1..1 last"])
    gate(
        "dupbase",
        'echo "PASS  same (1)"\necho "PASS  same (2)"\n',
        ["same (1)", "same (2)", "label edit"],
    )
    counter = tmp_path / "counter outside W"
    gate(
        "volatile",
        f'c="{counter}"\nn=$(cat "$c" 2>/dev/null || echo 0)\nn=$((n + 1))\n'
        f'echo "$n" > "$c"\necho "PASS  row-c$n"\n',
        ["row-c1", "row-c2"],
    )
    gate(
        "slow",
        'echo "PASS  a"\necho "last before the hang"\nsleep 3\n',
        ["timed out", "last before the hang"],
        "--timeout",
        "1",
    )


def test_t05_survivor_and_notapplied(tmp_path):
    subject = "ok1\nok2\nok3\nguarded_token\ndup\ndup\n"
    root = make_root(
        tmp_path,
        subject_text=subject,
        mutations=[
            ("survives", "guarded_token", "changed_token"),
            ("anchor absent", "absent_token", "x"),
            ("anchor twice", "dup", "x"),
        ],
    )
    run = run_tool(root)
    s = run.blocks.get(1)
    assert s and s["status"] == "SURVIVED", run
    assert any(
        line.startswith("at scripts/subject.sh:4") and "guarded_token" in line
        for line in s["lines"]
    ), f"the survivor does not name its code\n{run!r}"
    assert run.blocks.get(2, {}).get("status") == "NOT-APPLIED", run
    assert run.blocks.get(3, {}).get("status") == "NOT-APPLIED", run
    assert run.result["notapplied"] == 2 and run.status == "ERROR", run

    # A campaign whose every mutant survives is a FAIL, never a PASS.
    root = make_root(
        tmp_path,
        name="allsurvive",
        subject_text=subject,
        mutations=[
            ("survives one", "guarded_token", "changed_token"),
            ("survives two", "ok3\nguarded", "ok3\n\nguarded"),
        ],
    )
    run = run_tool(root)
    assert run.status == "FAIL" and run.rc == 1, run
    assert run.result["survivors"] == 2, run


def test_t07a_unattributed_and_incomplete(tmp_path):
    root = make_root(
        tmp_path,
        subject_text=tokens(5),
        suite_text=generic_suite(5),
        mutations=[("set -e after row 3", "ok3\n", "stop\n")],
    )
    run = run_tool(root)
    b = run.blocks.get(1)
    assert b and b["status"] == "KILLED", f"fewer rows did not read KILLED\n{run!r}"
    assert b["rows"] == ["row3", "row4", "row5"], run
    assert flags_of(b) == ["incomplete: 2 absent rows without a FAIL line"], run
    assert run.status == "FAIL" and run.rc == 1, run

    root = make_root(
        tmp_path,
        name="vanish",
        subject_text=tokens(5),
        suite_text=generic_suite(5),
        mutations=[
            ("rows vanish under rc 0", "ok4\nok5\n", "quiet4\nquiet5\n"),
            ("a pass turns into a skip", "ok5\n", "skip5\n"),
            ("fails with no named row", "ok5\n", "ok5\ndie\n"),
        ],
    )
    run = run_tool(root)
    for n in (1, 2, 3):
        assert run.blocks.get(n, {}).get("status") == "UNATTRIBUTED", run
    assert run.result["unattributed"] == 3, run
    assert run.status == "ERROR" and run.rc == 2, run


def test_t10a_invalid_parse(tmp_path):
    log = tmp_path / "suite runs.log"
    root = make_root(
        tmp_path,
        suite_text=generic_suite(3, extra=f'echo run >> "{log}"'),
        mutations=[("does not parse", "ok1\n", "ok1 (\n")],
    )
    run = run_tool(root)
    b = run.blocks.get(1)
    assert b and b["status"] == "INVALID", f"an unparseable mutant ran\n{run!r}"
    assert run.result["invalid"] == 1 and run.status == "ERROR", run
    # Two baseline runs and the canary; none for the INVALID mutant.
    assert log.read_text().splitlines() == ["run", "run", "run"], (
        f"the suite ran for the INVALID mutant\n{run!r}"
    )


def test_t14b_verdict_and_parity(tmp_path):
    quoted = "RESULT: PASS rc=0 mutants=9 identical_pairs=0 killed_all=0 (quoted)"
    # Every AUDIT_TEST_* is scrubbed, not only the watched one: an operator's exported
    # AUDIT_TEST_BUDGET (or any other) would otherwise turn `set: none` red.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in WATCHED and not k.startswith("AUDIT_TEST_")
    }
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    root = make_root(
        tmp_path,
        name="pass",
        mutations=[
            (quoted, "ok1\n", "no1\n"),
            ("kill two", "ok2\n", "no2\n"),
            ("kill three", "ok3\n", "no3\n"),
        ],
    )
    run = run_tool(root, env=env)
    assert run.status == "PASS", run
    runs = {"PASS": run}
    assert re.search(EXPECT, run.lines[-1]), (
        f"the RESULT line does not match the grammar\n{run!r}"
    )
    label_lines = [x for x in run.lines if x.startswith("[1/3]")]
    assert label_lines and re.search(EXPECT[1:], label_lines[0]), (
        f"the label does not quote the grammar, so the case proves nothing\n{run!r}"
    )
    anchored = [x for x in run.lines if re.search(EXPECT, x)]
    assert anchored == [run.lines[-1]], f"the anchored regex matched a label\n{run!r}"
    for sentence in (
        "grades the tree as copied at launch; later live edits are not graded",
        "serial, one run per mutant: determinism NOT assessed",
    ):
        assert sentence in run.lines, f"missing header sentence {sentence!r}\n{run!r}"
    env_line = next(x for x in run.lines if x.startswith("env: "))
    assert "set: none" in env_line, run

    root = make_root(
        tmp_path, name="fail", mutations=[("silent", "ok1\n", "silent1\n")]
    )
    runs["FAIL"] = run = run_tool(root)
    assert run.status == "FAIL", run
    gating = ("survivors", "identical_pairs", "killed_all", "incomplete")
    assert [f for f in gating if run.result[f]] == ["incomplete"], run

    root = make_root(tmp_path, name="error", mutations=[("absent", "nope", "x")])
    env = os.environ.copy()
    env.update(
        PYTEST_ADDOPTS="-q",
        PYTEST_PLUGINS="kill_sets_none",
        HOOK_TESTS_ONLY="1",
        CLAUDE_PROJECT_DIR=str(tmp_path),
        AUDIT_TEST_JOBS="2",
    )
    runs["ERROR"] = run = run_tool(root, env=env)
    assert run.status == "ERROR", run
    env_line = next(x for x in run.lines if x.startswith("env: "))
    for name in WATCHED:
        assert name in env_line, f"the header does not name {name}\n{run!r}"

    for status, r in runs.items():
        assert re.search(EXPECT, r.lines[-1]), r
        assert r.order == FIELDS, f"field order {r.order}\n{r!r}"
        assert r.result["rc"] == r.rc == {"PASS": 0, "FAIL": 1, "ERROR": 2}[status], r

    # Parity: the tool's noticed set equals mutate.run's CAUGHT set on one fixture.
    mutations = [
        ("caught", "ok1\n", "no1\n"),
        ("survived", "keep_me\n", "keep_you\n"),
        ("invalid", "ok2\n", "ok2 (\n"),
    ]
    subject = tokens() + "keep_me\n"
    root = make_root(tmp_path, name="tool", subject_text=subject, mutations=mutations)
    run = run_tool(root)
    tool_said = {b["label"]: b["status"] for b in run.blocks.values()}
    other = make_root(
        tmp_path, name="inplace", subject_text=subject, mutations=mutations
    )
    proc = subprocess.run(
        [sys.executable, str(other / "scripts" / "tests" / "mutate_fix.py")],
        cwd=str(other),
        capture_output=True,
        text=True,
        timeout=300,
    )
    said = dict(
        (m.group(2), m.group(1))
        for m in re.finditer(
            r"^(CAUGHT|SURVIVED|INVALID|TIMEOUT) +(.+)$", proc.stdout, re.M
        )
    )
    assert set(said) == {m[0] for m in mutations}, proc.stdout
    noticed = {k for k, v in tool_said.items() if v in ("KILLED", "KILLED-ALL")}
    assert noticed == {k for k, v in said.items() if v == "CAUGHT"}, (tool_said, said)
    for kind in ("SURVIVED", "INVALID"):
        assert {k for k, v in tool_said.items() if v == kind} == {
            k for k, v in said.items() if v == kind
        }, (tool_said, said)


# --------------------------------------------------------------------------------------
# Slice 1b: process groups, the canary and KILLED-ALL, pytest reading, isolation, env.
# --------------------------------------------------------------------------------------

PYTEST_HEAD = (
    "from pathlib import Path\n\n"
    "import pytest\n\n"
    "TEXT = (Path(__file__).resolve().parent.parent / 'subj.py').read_text()\n"
)


def short(rows) -> list[str]:
    """pytest node ids without their path: `a/b.py::test_x` -> `test_x`."""
    return [r.rsplit("::", 1)[-1] for r in rows]


def pytest_test(name: str, body: str, args: str = "") -> str:
    return f"\n\ndef {name}({args}):\n" + "".join(
        f"    {line}\n" for line in body.split("\n")
    )


T02_SUITE = (
    PYTEST_HEAD
    + "\n\n@pytest.fixture\ndef fx():\n    assert 'tokF' in TEXT\n"
    + "\n\n@pytest.fixture\ndef td():\n    yield\n    assert 'tokT' in TEXT\n"
    + pytest_test("test_a1", "assert 'tokA' in TEXT")
    + pytest_test("test_a2", "assert 'tokA' in TEXT")
    + pytest_test("test_b", "assert 'tokB' in TEXT")
    + "\n\n@pytest.fixture\ndef tq():\n    yield\n    assert 'tokQ' in TEXT\n"
    + pytest_test("test_setup", "pass", "fx")
    + pytest_test("test_teardown", "pass", "td")
    # Fails in the call AND errors in teardown: one id on a FAILED and an ERROR line.
    + pytest_test("test_fail_and_teardown", "assert 'tokQ' in TEXT", "tq")
    # A parametrised id whose brackets hold ` - `, the separator of a message.
    + "\n\n@pytest.mark.parametrize('v', ['a - b'])"
    + pytest_test("test_p", "assert 'tokP' in TEXT", "v")
    + pytest_test("test_echo", "print('FAIL  x')")
    + pytest_test(
        "test_new",
        "if 'tokN' not in TEXT:\n"
        "    pytest.skip('a row only a mutant reaches')\n"
        "assert False, 'a row the baseline never passed'",
    )
)

# A shadow `pytest` package: `python -m pytest` run with cwd = the copy finds it first, and it
# prints a canned short summary whose final line carries the 60 s-or-more duration form.
SHADOW_MAIN = """\
import sys
from pathlib import Path

text = Path("scripts/subj.py").read_text()
try:
    compile(text, "subj.py", "exec")
except SyntaxError:
    sys.exit(2)
print("=" * 27 + " short test summary info " + "=" * 28)
print("PASSED t.py::test_b")
if "tokA" in text:
    print("PASSED t.py::test_a")
    print("2 passed in 62.10s (0:01:02)")
    sys.exit(0)
print("FAILED t.py::test_a - assert 0")
print("1 failed, 1 passed in 62.10s (0:01:02)")
sys.exit(1)
"""


def test_t02_pytest_duplicate_teardown_and_rpfE(tmp_path):
    root = make_root(
        tmp_path,
        family="pytest",
        subject_text="TOKENS = ['tokA', 'tokB', 'tokF', 'tokT', 'tokQ', 'tokP']\n",
        suite_text=T02_SUITE,
        mutations=[
            ("tokA one way", "'tokA'", "'xxA'"),
            ("tokA another way", "['tokA'", "['yyA'"),
            ("setup error", "'tokF'", "'xxF'"),
            ("teardown error", "'tokT'", "'xxT'"),
            ("fails a row outside U too", "'tokB'", "'tokN'"),
            ("fails and errors in teardown", "'tokQ'", "'xxQ'"),
            ("fails a bracketed id", "'tokP'", "'xxP'"),
        ],
    )
    run = run_tool(root)
    assert run.has_line("BASELINE green"), (
        f"a passing test printing a column-0 `FAIL  x` made the baseline read noticed "
        f"(pytest armed with -rA?)\n{run!r}"
    )
    b = run.blocks
    assert len(b) == 7, f"a mutant block is missing\n{run!r}"
    assert b[6]["status"] == "KILLED", run
    assert short(b[6]["rows"]) == ["test_fail_and_teardown"], run
    assert not flags_of(b[6]), (
        f"one id on a FAILED and an ERROR line was counted twice (nF by lines, not "
        f"distinct ids)\n{run!r}"
    )
    assert any(
        x.startswith("explained by 1 FAIL lines (nF=1)") for x in b[6]["lines"]
    ), run
    assert b[7]["status"] == "KILLED" and short(b[7]["rows"]) == ["test_p[a - b]"], (
        f"a ` - ` inside the id's brackets split the id\n{run!r}"
    )
    assert not flags_of(b[7]), run
    assert b[1]["status"] == b[2]["status"] == "KILLED", run
    assert short(b[1]["rows"]) == short(b[2]["rows"]) == ["test_a1", "test_a2"], run
    assert run.has_line("DUPLICATE [1] [2]"), f"no DUPLICATE line\n{run!r}"
    assert b[3]["status"] == "KILLED" and short(b[3]["rows"]) == ["test_setup"], (
        f"a setup ERROR is not a killed row\n{run!r}"
    )
    assert not flags_of(b[3]), f"a setup ERROR read as incomplete\n{run!r}"
    assert b[4]["status"] == "KILLED" and short(b[4]["rows"]) == ["test_teardown"], (
        f"a passing test with a teardown error is not in the kill set\n{run!r}"
    )
    assert not flags_of(b[4]), run
    assert b[5]["status"] == "KILLED" and short(b[5]["rows"]) == ["test_b"], (
        f"a failing id outside U entered the kill set\n{run!r}"
    )
    assert not flags_of(b[5]), run
    assert any(
        x.startswith("+ ") and x.endswith("::test_new") for x in b[5]["lines"]
    ), f"the failing id outside U is not named as a new_labels diagnostic\n{run!r}"
    assert run.result["identical_pairs"] == 1, run
    assert run.result["incomplete"] == 0, run
    assert run.status == "FAIL" and run.rc == 1, run

    # No count is read from the summary line: a shadow pytest ends `in 62.10s (0:01:02)`.
    root = make_root(
        tmp_path,
        name="shadow",
        family="pytest",
        subject_text="TOKENS = ['tokA']\n",
        mutations=[("drops tokA", "'tokA'", "'xxA'")],
    )
    (root / "pytest").mkdir()
    (root / "pytest" / "__init__.py").write_text("")
    (root / "pytest" / "__main__.py").write_text(SHADOW_MAIN)
    run = run_tool(root)
    assert run.has_line("BASELINE green") and "rows=2" in run.out, run
    one = run.blocks.get(1)
    assert one and one["status"] == "KILLED" and one["rows"] == ["t.py::test_a"], run
    assert not flags_of(one), run
    assert run.status == "PASS", run


T03B_SUITE = (
    PYTEST_HEAD
    + pytest_test(
        "test_x",
        "if 'tokX' not in TEXT:\n"
        "    base = request.node.nodeid.rsplit('::', 1)[0]\n"
        "    print(f'PASSED {base}::test_y')\n"
        "    print(f'FAILED {base}::test_z - planted')\n"
        "    print(f'ERROR {base}::test_z - planted')\n"
        "    print('PASS  row9')\n"
        "    assert False",
        "request",
    )
    + pytest_test("test_y", "assert 'tokY' in TEXT")
    + pytest_test("test_z", "assert 'tokZ' in TEXT")
)


def test_t03b_pytest_absence_reading(tmp_path):
    root = make_root(
        tmp_path,
        family="pytest",
        subject_text="TOKENS = ['tokX', 'tokY', 'tokZ']\n",
        suite_text=T03B_SUITE,
        mutations=[("drops tokX", "'tokX'", "'xxX'")],
    )
    run = run_tool(root)
    one = run.blocks.get(1)
    assert one and one["status"] == "KILLED", f"no KILLED block\n{run!r}"
    assert short(one["rows"]) == ["test_x"], (
        f"captured column-0 text above the summary was read as rows\n{run!r}"
    )
    assert not flags_of(one), f"captured text produced a flagged shape\n{run!r}"
    assert not any(x.startswith("+ ") for x in one["lines"]), run
    assert run.status == "PASS", run

    # A failing test whose captured stdout holds a whole short-summary block: only the LAST
    # block (the real summary) is read, so a captured `PASSED <id>` cannot hide a row that
    # vanished with no FAILED line (here a pass that turned into a skip).
    nested = (
        PYTEST_HEAD
        + pytest_test(
            "test_x",
            "if 'failX' in TEXT:\n"
            "    base = request.node.nodeid.rsplit('::', 1)[0]\n"
            "    print('=' * 10 + ' short test summary info ' + '=' * 10)\n"
            "    print(f'PASSED {base}::test_q')\n"
            "    assert False",
            "request",
        )
        + pytest_test(
            "test_q",
            "if 'skipQ' in TEXT:\n    pytest.skip('vanishes with no FAILED line')",
        )
        # The only row the canary kills, so the mutant's kill set is not KILLED-ALL.
        + pytest_test("test_z", "assert 'tokZ' in TEXT")
    )
    root = make_root(
        tmp_path,
        name="nested",
        family="pytest",
        subject_text="TOKENS = ['tokZ']\n",
        suite_text=nested,
        mutations=[("fails x, skips q", "['tokZ']", "['tokZ', 'failX', 'skipQ']")],
    )
    run = run_tool(root)
    one = run.blocks.get(1)
    assert one and one["status"] == "KILLED", f"no KILLED block\n{run!r}"
    assert short(one["rows"]) == ["test_q", "test_x"], (
        f"a captured short-summary block was read as rows\n{run!r}"
    )
    assert flags_of(one) == ["incomplete: 1 absent rows without a FAIL line"], run


T06_SUITE = r"""#!/usr/bin/env bash
subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"
has() { grep -qx -- "$1" "$subj"; }
fails=0
for n in $(seq 1 @ROWS@); do
  if has "ok$n"; then echo "PASS  row$n"; else echo "FAIL  row$n (detail)"; fails=1; fi
done
@CONST@
exit $fails
"""


def t06_suite(rows: int = 3, const: str = "") -> str:
    return T06_SUITE.replace("@ROWS@", str(rows)).replace("@CONST@", const)


def test_t06a_killed_all_shell(tmp_path):
    root = make_root(
        tmp_path,
        suite_text=t06_suite(),
        mutations=[
            ("kills every row", "ok1\nok2\nok3\n", "gone\n"),
            ("kills every row again", "ok1\nok2\nok3\n", "gone2\n"),
            ("kills |U|-1 rows", "ok1\nok2\n", "x\n"),
        ],
    )
    run = run_tool(root)
    b = run.blocks
    assert b.get(1, {}).get("status") == "KILLED-ALL", f"not KILLED-ALL\n{run!r}"
    assert b.get(2, {}).get("status") == "KILLED-ALL", run
    assert b[1]["k"] == 3 and "(all 3 rows)" in b[1]["lines"], run
    assert b.get(3, {}).get("status") == "KILLED" and b[3]["k"] == 2, (
        f"a mutant killing |U|-1 rows did not read plain KILLED\n{run!r}"
    )
    assert run.result["killed_all"] == 2, run
    assert run.result["identical_pairs"] == 0, (
        f"two KILLED-ALL mutants were counted as a duplicate pair\n{run!r}"
    )
    assert run.result["incomplete"] == 0, run
    assert run.status == "FAIL" and run.rc == 1, run

    # A canary set C strictly inside U: the constant row never reads the subject.
    root = make_root(
        tmp_path,
        name="strict",
        suite_text=t06_suite(3, 'echo "PASS  constant"'),
        mutations=[
            ("kills exactly C", "ok1\nok2\nok3\n", "gone\n"),
            ("kills |C|-1 rows", "ok1\nok2\n", "x\n"),
            ("kills one row", "ok1\n", "x1\n"),
        ],
    )
    run = run_tool(root)
    assert run.has_line("canary: noticed, 3 of 4 rows absent"), run
    b = run.blocks
    assert b.get(1, {}).get("status") == "KILLED-ALL", (
        f"a mutant killing a superset of C did not read KILLED-ALL\n{run!r}"
    )
    assert b.get(2, {}).get("status") == "KILLED" and b[2]["k"] == 2, run
    assert b.get(3, {}).get("status") == "KILLED" and b[3]["k"] == 1, run
    assert run.result["killed_all"] == 1, run

    # A canary set C that is EMPTY: the suite notices the canary through a FAIL-only line and
    # every row still passes, so KILLED-ALL falls back to `kill == U`.
    c_empty = (
        "#!/usr/bin/env bash\n"
        'subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"\n'
        "fails=0\n"
        "for n in 1 2 3; do\n"
        '  if grep -qx "bad$n" "$subj"; then echo "FAIL  row$n"; fails=1\n'
        '  else echo "PASS  row$n"; fi\n'
        "done\n"
        'if grep -qx \'{\' "$subj"; then echo "FAIL  the canary is seen"; exit 1; fi\n'
        "exit $fails\n"
    )
    root = make_root(
        tmp_path,
        name="cempty",
        subject_text="fine\n",
        suite_text=c_empty,
        mutations=[
            ("kills every row, C empty", "fine\n", "bad1\nbad2\nbad3\n"),
            ("kills two rows, C empty", "fine\n", "bad1\nbad2\n"),
        ],
    )
    run = run_tool(root)
    assert run.has_line("canary: noticed, 0 of 3 rows absent"), (
        f"the case does not have an empty canary set\n{run!r}"
    )
    assert run.blocks.get(1, {}).get("status") == "KILLED-ALL", (
        f"with C empty, a mutant killing every row is not KILLED-ALL\n{run!r}"
    )
    assert run.blocks.get(2, {}).get("status") == "KILLED", run
    assert run.result["killed_all"] == 1, run

    # A one-row suite: KILLED-ALL is n/a, said so, and not counted.
    root = make_root(
        tmp_path,
        name="onerow",
        subject_text="ok1\n",
        suite_text=t06_suite(1),
        mutations=[("kills the one row", "ok1\n", "x1\n")],
    )
    run = run_tool(root)
    one = run.blocks.get(1)
    assert one and one["status"] == "KILLED", run
    assert any("n/a" in x for x in one["lines"]), f"no n/a line\n{run!r}"
    assert run.result["killed_all"] == 0, run


def test_t06b_killed_all_pytest(tmp_path):
    root = make_root(
        tmp_path,
        family="pytest",
        mutations=[
            ("kills every test", "OK = ['ok1', 'ok2']", "OK = []"),
            ("kills |U|-1 tests", "'ok1'", "'no1'"),
        ],
    )
    run = run_tool(root)
    b = run.blocks
    assert b.get(1, {}).get("status") == "KILLED-ALL", f"not KILLED-ALL\n{run!r}"
    assert "(all 2 rows)" in b[1]["lines"], run
    assert b.get(2, {}).get("status") == "KILLED" and b[2]["k"] == 1, run
    assert run.result["killed_all"] == 1 and run.result["incomplete"] == 0, run
    assert run.status == "FAIL", run


T07B_SHELL = r"""#!/usr/bin/env bash
subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"
has() { grep -qx -- "$1" "$subj"; }
if has crash; then exit 2; fi
fails=0
for n in 1 2 3; do
  if has "ok$n"; then echo "PASS  row$n"; else echo "FAIL  row$n"; fails=1; fi
done
exit $fails
"""


def test_t07b_killed_all_crash_and_abort(tmp_path):
    root = make_root(
        tmp_path,
        suite_text=T07B_SHELL,
        mutations=[("crashes with no rows", "ok3\n", "ok3\ncrash\n")],
    )
    run = run_tool(root)
    one = run.blocks.get(1)
    assert one and one["status"] == "KILLED-ALL", f"a no-rows crash\n{run!r}"
    assert run.result["killed_all"] == 1 and run.result["incomplete"] == 0, run

    suite = (
        "import os\n"
        + PYTEST_HEAD
        + pytest_test("test_die", "if 'die' in TEXT:\n    os._exit(3)")
        + pytest_test("test_ok1", "assert 'ok1' in TEXT")
        + pytest_test("test_ok2", "assert 'ok2' in TEXT")
    )
    root = make_root(
        tmp_path,
        name="abort",
        family="pytest",
        suite_text=suite,
        mutations=[("aborts pytest", "'ok2']", "'ok2', 'die']")],
    )
    run = run_tool(root)
    one = run.blocks.get(1)
    assert one and one["status"] == "KILLED-ALL", (
        f"a pytest abort with no short summary\n{run!r}"
    )
    assert run.result["killed_all"] == 1, run


T08_SUITE = (
    PYTEST_HEAD
    + pytest_test("test_first", "assert 'ok1' in TEXT")
    + pytest_test("test_s1", "assert 'shared' in TEXT")
    + pytest_test("test_s2", "assert 'shared' in TEXT")
    + pytest_test("test_s3", "assert 'shared' in TEXT")
    + pytest_test("test_last", "assert 'ok1' in TEXT")
)
T08_PATH = 'str(REPO / "scripts" / "tests" / "test_subj.py")'


def test_t08_x_override(tmp_path):
    common = {
        "family": "pytest",
        "subject_text": "OK = ['shared', 'ok1']\n",
        "suite_text": T08_SUITE,
        "mutations": [("breaks shared", "'shared'", "'gone'")],
    }
    for i, (stop, gone) in enumerate(
        (
            ('"-x"', ["-x"]),
            ('"--exitfirst"', ["--exitfirst"]),
            ('"--maxfail=1"', ["--maxfail=1"]),
            ('"--maxfail", "1"', ["--maxfail", "1"]),
        )
    ):
        root = make_root(
            tmp_path,
            name=f"argv{i}",
            suite_argv=f'[sys.executable, "-m", "pytest", {stop}, "-q", {T08_PATH}]',
            **common,
        )
        run = run_tool(root)
        suite_line = next((x for x in run.lines if x.startswith("suite: ")), "")
        assert suite_line and not set(gone) & set(suite_line.split()), (
            f"{gone} was not stripped from the argv\n{run!r}"
        )
        one = run.blocks.get(1)
        assert one and one["status"] == "KILLED" and one["k"] == 3, (
            f"{gone} in the argv stopped the run\n{run!r}"
        )
        assert short(one["rows"]) == ["test_s1", "test_s2", "test_s3"], run
        assert not flags_of(one), run

    root = make_root(
        tmp_path,
        name="addopts",
        suite_argv=(
            f'["env", "PYTEST_ADDOPTS=-x", sys.executable, "-m", "pytest", "-q", '
            f"{T08_PATH}]"
        ),
        **common,
    )
    run = run_tool(root)
    one = run.blocks.get(1)
    assert one and one["status"] == "KILLED", f"no KILLED block\n{run!r}"
    flags = flags_of(one)
    assert len(flags) == 1 and flags[0].startswith("incomplete: 3 absent rows"), run
    assert "stopping after 1 failures" in flags[0], (
        f"the stopping banner is not the incomplete set's reason\n{run!r}"
    )
    assert run.result["incomplete"] == 1 and run.status == "FAIL", run


def test_t10b_invalid_collection_abort(tmp_path):
    suite = (
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
        "import subj  # noqa: E402\n"
        + pytest_test("test_ok1", "assert 'ok1' in subj.OK")
        + pytest_test("test_ok2", "assert 'ok2' in subj.OK")
    )
    root = make_root(
        tmp_path,
        family="pytest",
        suite_text=suite,
        mutations=[("fails at import", "OK = ['ok1', 'ok2']", "OK = undefined_name")],
    )
    run = run_tool(root)
    # The canary aborts collection, so the silent-loss sentence must say what such a suite's
    # mutants really read (INVALID), not the shell wording (incomplete).
    assert run.has_line("canary: collection aborted"), run
    assert (
        "the canary aborts this suite's collection; mutants that break the subject's "
        "import will read INVALID (no test ran), not killed."
    ) in run.lines, f"the collection-abort canary sentence is missing\n{run!r}"
    assert not any("will read incomplete" in x for x in run.lines), (
        f"a collection abort was described as reading incomplete\n{run!r}"
    )
    one = run.blocks.get(1)
    assert one and one["status"] == "INVALID", f"not INVALID\n{run!r}"
    assert any("collection" in x for x in one["lines"]), run
    assert run.result["invalid"] == 1 and run.status == "ERROR", run


T09_SUITE = r"""#!/usr/bin/env bash
subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"
has() { grep -qx -- "$1" "$subj"; }
if has bg; then bash -c 'exec -a @MARK@ sleep 60' & fi
if has late; then (sleep 1; echo "LATE-WRITE-@MARK@" >&2) & fi
if has hang; then sleep 30; fi
fails=0
for n in 1 2 3; do
  if has "ok$n"; then echo "PASS  row$n"; else echo "FAIL  row$n"; fails=1; fi
done
exit $fails
"""


def _pgrep(marker: str) -> str:
    proc = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    return proc.stdout.strip()


@pytest.mark.serial_only
def test_t09_timeout_and_process_group(tmp_path):
    marker = f"kstest-{tmp_path.name}"
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    env = os.environ.copy()
    env["TMPDIR"] = str(scratch)
    root = make_root(
        tmp_path,
        subject_text="ok1\nok2\nok3\nbg\nlate\n",
        suite_text=T09_SUITE.replace("@MARK@", marker),
        mutations=[
            ("exits 0 with a backgrounded marker", "ok1\n", "no1\n"),
            ("hangs (and drops its own late writer)", "late\n", "hang\n"),
        ],
    )
    try:
        run = run_tool(root, "--timeout", "2", "--keep", env=env)
        assert run.blocks.get(2, {}).get("status") == "TIMEOUT", run
        assert run.result["timedout"] == 1 and run.status == "ERROR", run
        assert run.rc == 2, run
        assert _pgrep(marker) == "", (
            f"the marker outlived its run (process group not killed)\n{run!r}"
        )
        kept = next(
            (x.split()[2] for x in run.lines if x.startswith("copy: kept ")), None
        )
        assert kept, run
        runs = sorted(Path(kept, "runs").glob("*.err"))
        assert len(runs) >= 5, f"fewer per-run stderr files than runs: {runs}"
        subprocess.run(["sleep", "1.5"], check=True)
        late = [p.name for p in runs if f"LATE-WRITE-{marker}" in p.read_text()]
        assert late == [], f"a late grandchild wrote into run files {late}\n{run!r}"
        assert _pgrep(marker) == "", run
    finally:
        subprocess.run(["pkill", "-9", "-f", marker], capture_output=True)


def test_t12_canary(tmp_path):
    # A suite that grades the LIVE subject by absolute path never sees the canary.
    root = make_root(tmp_path, name="live", mutations=[("m", "ok1\n", "no1\n")])
    live = root / "scripts" / "subject.sh"
    (root / "scripts" / "tests" / "suite.sh").write_text(
        generic_suite().replace(
            'subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"', f'subj="{live}"'
        )
    )
    run = run_tool(root)
    assert run.has_line("BASELINE green"), run
    assert run.status == "ERROR" and not run.blocks, (
        f"a suite reading the live subject was graded\n{run!r}"
    )
    assert any("canary" in x for x in run.error_lines()), run

    # A SOURCED shell subject whose suite ONLY checks that it loads: an empty file sources
    # fine and `{` does not, so an empty canary would read green here.
    sourced = (
        "#!/usr/bin/env bash\n"
        'subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"\n'
        'if ! . "$subj" 2>/dev/null; then echo "FAIL  subject loads"; exit 1; fi\n'
        'echo "PASS  subject loads"\n'
    )
    root = make_root(
        tmp_path,
        name="sourced",
        subject_text="A=1\n",
        suite_text=sourced,
        mutations=[("fails to load", "A=1\n", "return 1\n")],
    )
    run = run_tool(root)
    assert run.has_line("canary: noticed"), (
        f"the canary was not noticed by a suite that only checks its subject loads\n"
        f"{run!r}"
    )
    assert run.blocks.get(1, {}).get("status") == "KILLED", run
    assert run.status == "PASS", run

    # A suite that dies with no output on the canary loses its rows SILENTLY: the canary line
    # counts them and the silent-loss sentence follows.
    silent = generic_suite().replace(
        "fails=0\n", "if grep -qx '{' \"$subj\"; then exit 2; fi\nfails=0\n", 1
    )
    root = make_root(
        tmp_path,
        name="silent",
        suite_text=silent,
        mutations=[("m", "ok1\n", "no1\n")],
    )
    run = run_tool(root)
    assert run.has_line("canary: noticed, 3 of 3 rows absent, 3 without a FAIL line"), (
        f"the canary line does not count the silent losses\n{run!r}"
    )
    assert (
        "this suite loses rows silently when its subject breaks; mutants that abort it "
        "will read incomplete (a lower bound), not an error."
    ) in run.lines, f"the silent-loss sentence is missing\n{run!r}"

    # A `.jsonc`-style reader that treats an empty file as `{}`.
    reader = (
        "#!/usr/bin/env bash\n"
        'subj="$(cd "$(dirname "$0")/.." && pwd)/config.jsonc"\n'
        f'"{sys.executable}" - "$subj" <<\'EOF\'\n'
        "import json, sys\n"
        "text = open(sys.argv[1]).read().strip() or '{}'\n"
        "try:\n"
        "    data = json.loads(text)\n"
        "except ValueError:\n"
        "    print('FAIL  parses')\n"
        "    sys.exit(1)\n"
        "print('PASS  parses')\n"
        "print('PASS  a is 1' if data.get('a') == 1 else 'FAIL  a is 1')\n"
        "sys.exit(0 if data.get('a') == 1 else 1)\n"
        "EOF\n"
    )
    campaign = CAMPAIGN.format(
        resolve=".resolve()",
        subject="scripts/config.jsonc",
        mutations=[("a changes", '"a": 1', '"a": 2')],
        suite=SHELL_ARGV,
    )
    root = make_root(tmp_path, name="jsonc", suite_text=reader, campaign_text=campaign)
    (root / "scripts" / "config.jsonc").write_text('{"a": 1}\n')
    run = run_tool(root)
    assert run.has_line("canary: noticed"), run
    assert run.blocks.get(1, {}).get("status") == "KILLED", run
    assert run.status == "PASS", run


def _anchor_line(stripped: str) -> tuple[str, str]:
    """The one line of the tool whose stripped text is `stripped`: (line with its newline, indent)."""
    hits = [
        x for x in TOOL.read_text().splitlines(keepends=True) if x.strip() == stripped
    ]
    assert len(hits) == 1, f"{len(hits)} lines of the tool read {stripped!r}, need 1"
    line = hits[0]
    return line, line[: len(line) - len(line.lstrip())]


def _injected(tmp_path: Path, name: str, stripped: str, code: str) -> Path:
    """A tool copy whose anchor line `stripped` is preceded by `code` (same indentation)."""
    line, indent = _anchor_line(stripped)
    body = "".join(f"{indent}{x}\n" for x in code.splitlines())
    return _tool_copy(tmp_path, name, None, edit=(line, body + line))


T13_SUITE = r"""#!/usr/bin/env bash
here="$(cd "$(dirname "$0")/.." && pwd)"
subj="$here/subject.sh"
has() { grep -qx -- "$1" "$subj"; }
printf '%s\n' "${PYTHONDONTWRITEBYTECODE-unset}" >> "@SEEN@"
fails=0
if [ -e "$here/marker" ]; then echo "FAIL  no marker"; fails=1; else echo "PASS  no marker"; fi
if has writemark; then : > "$here/marker"; fi
if has leakother; then echo unrelated-text > "@LIVE@"; fi
if has leakme; then cp "$subj" "@LIVE@"; fi
if has lockme; then mkdir -p "$here/locked"; : > "$here/locked/f"; chmod 500 "$here/locked"; fi
for n in 1 2 3; do
  if has "ok$n"; then echo "PASS  row$n"; else echo "FAIL  row$n"; fails=1; fi
done
if [ "${1:-}" = --killsets ]; then echo "PASS  killsets row"; fi
if [ -n "${KILL_LOG:-}" ]; then echo "PASS  kill_log row"; fi
exit $fails
"""

T13_CAMPAIGN_TAIL = """
import os  # noqa: E402

if os.environ.get("KILLSETS"):
    SUITE = SUITE + ["--killsets"]
"""


def _digest_tree(root: Path) -> dict:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _real_status() -> str:
    proc = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain", "-uall"],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _normalised(run: Run) -> list[str]:
    out = []
    for line in run.lines:
        line = re.sub(r"kill-sets\.[A-Za-z0-9_]+", "kill-sets.X", line)
        line = re.sub(r"[0-9]+\.[0-9]+s\b", "Ts", line)
        line = re.sub(r"\(limit [0-9]+s\)", "(limit Ts)", line)
        out.append(line)
    return out


def test_t13a_isolation_leak_env(tmp_path):
    seen = tmp_path / "seen.txt"

    def t13_root(name, mutations, live=None):
        root = make_root(
            tmp_path,
            name=name,
            suite_text=T13_SUITE.replace("@SEEN@", str(seen)).replace(
                "@LIVE@", str(live or tmp_path / "nowhere")
            ),
            mutations=mutations,
        )
        campaign = root / "scripts" / "tests" / "mutate_fix.py"
        text = campaign.read_text().replace(
            "\n\ndef main()", T13_CAMPAIGN_TAIL + "\n\ndef main()", 1
        )
        campaign.write_text(text)
        return root

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("KILLSETS", "KILL_LOG", "PYTHONDONTWRITEBYTECODE")
    }
    isolation = [
        ("writes a marker in the copy", "ok1\n", "no1\nwritemark\n"),
        ("looks for the marker", "ok2\n", "no2\n"),
    ]
    status_before = _real_status()
    root = t13_root("iso", isolation)
    files_before = _digest_tree(root)
    seen.write_text("")
    run = run_tool(root, env=env)
    assert run.blocks.get(1, {}).get("rows") == ["row1"], run
    assert run.blocks.get(2, {}).get("rows") == ["row2"], (
        f"mutant 1's marker reached mutant 2 (the copy was reused)\n{run!r}"
    )
    assert run.status == "PASS", run
    assert _digest_tree(root) == files_before, "a PASS run changed the live fixture"
    assert _real_status() == status_before, "a PASS run changed the real repository"
    assert set(seen.read_text().split()) == {"unset"}, (
        f"the tool set PYTHONDONTWRITEBYTECODE in a suite: {seen.read_text()!r}"
    )
    env_line = next(x for x in run.lines if x.startswith("env: "))
    assert "PYTHONDONTWRITEBYTECODE" not in env_line, run
    plain = _normalised(run)

    # KILLSETS and KILL_LOG in the parent: the campaign's sidecar SUITE is not used.
    sidecar = dict(env, KILLSETS="1", KILL_LOG=str(tmp_path / "kill.log"))
    run = run_tool(root, env=sidecar)
    assert _normalised(run) == plain, (
        f"KILLSETS/KILL_LOG in the parent changed the output\n{run!r}"
    )

    # PYTHONDONTWRITEBYTECODE set in the parent: printed as a watch-list entry, inherited.
    seen.write_text("")
    run = run_tool(root, env=dict(env, PYTHONDONTWRITEBYTECODE="1"))
    env_line = next(x for x in run.lines if x.startswith("env: "))
    assert "PYTHONDONTWRITEBYTECODE" in env_line, run
    assert set(seen.read_text().split()) == {"1"}, seen.read_text()
    assert run.status == "PASS", run

    # An ERROR run leaves the live fixture and the real repository unchanged too.
    root = t13_root("err", [("anchor absent", "nope\n", "x\n")])
    files_before = _digest_tree(root)
    run = run_tool(root, env=env)
    assert run.status == "ERROR", run
    assert _digest_tree(root) == files_before, "an ERROR run changed the live fixture"
    assert _real_status() == status_before, "an ERROR run changed the real repository"

    # A mutant whose suite writes the mutant text into the LIVE subject is named.
    live_subject = tmp_path / "leak" / "scripts" / "subject.sh"
    root = t13_root(
        "leak",
        [("leaks into the live subject", "ok3\n", "no3\nleakme\n")],
        live_subject,
    )
    run = run_tool(root, env=env)
    assert run.status == "ERROR" and run.rc == 2, run
    assert any(
        "leak" in x and "leaks into the live subject" in x for x in run.error_lines()
    ), f"the leak was not named\n{run!r}"

    # The same leak, then a fault in the mutant's own grading (injected after the mutant write
    # and its `applied` record): the mutant is DIED, and the leak check still attributes the
    # leak to it by label, because the record was made at write time.
    live_subject = tmp_path / "leakdied" / "scripts" / "subject.sh"
    root = t13_root(
        "leakdied",
        [("leaks, then its grading raises", "ok3\n", "no3\nleakme\n")],
        live_subject,
    )
    raising = _injected(
        tmp_path,
        "raising-grade",
        "outcome = self.grade(n, label, old, run)",
        'raise RuntimeError("kill-sets test: injected")',
    )
    run = run_tool(root, env=env, tool=raising)
    assert run.blocks.get(1, {}).get("status") == "DIED", (
        f"a fault in grading was not a DIED block\n{run!r}"
    )
    assert "kill-sets test: injected" in " ".join(run.blocks[1]["lines"]), run
    assert run.result and run.result["died"] == 1, run
    assert not run.has_line("ERROR  internal exception"), run
    assert any(
        "leak" in x and "leaks, then its grading raises" in x for x in run.error_lines()
    ), f"a DIED mutant's leak was not attributed to it\n{run!r}"
    assert run.status == "ERROR" and run.rc == 2, run

    # The same leak, then an internal exception at the post-drain check, which no mutant owns:
    # the leak check runs on that ERROR path too, and does not mask the exception.
    live_subject = tmp_path / "leakexc" / "scripts" / "subject.sh"
    root = t13_root(
        "leakexc",
        [("leaks, then the tool raises", "ok3\n", "no3\nleakme\n")],
        live_subject,
    )
    line, indent = _anchor_line("self._check_all_graded()")
    raising = _tool_copy(
        tmp_path,
        "raising",
        None,
        edit=(
            line,
            f'{indent}raise RuntimeError("kill-sets test: injected")\n',
        ),
    )
    run = run_tool(root, env=env, tool=raising)
    assert "kill-sets test: injected" in run.out and "Traceback" in run.out, (
        f"the injected exception was not reported\n{run!r}"
    )
    assert any(
        "leak" in x and "leaks, then the tool raises" in x for x in run.error_lines()
    ), f"an internal exception skipped the leak check\n{run!r}"
    assert run.status == "ERROR" and run.rc == 2, run

    # Teardown, verified: the temp directory is announced the moment it exists (before the
    # copy and before discovery), is gone after the run, and `copy: removed` says so.
    private = tmp_path / "teardown-tmp"
    private.mkdir()
    root = t13_root("teardown", [("m", "ok1\n", "no1\n")])
    run = run_tool(root, env=dict(env, TMPDIR=str(private)))
    assert run.status == "PASS", run
    firsts = {
        key: next((i for i, x in enumerate(run.lines) if x.startswith(key)), None)
        for key in ("copy: ", "copy: size ", "subject: ", "BASELINE")
    }
    assert None not in firsts.values(), f"a line is missing: {firsts}\n{run!r}"
    assert run.lines[firsts["copy: "]].startswith(
        f"copy: {private}{os.sep}kill-sets."
    ), run
    assert (
        firsts["copy: "]
        < firsts["copy: size "]
        < firsts["subject: "]
        < firsts["BASELINE"]
    ), f"the temp directory was not announced before the copy and discovery\n{run!r}"
    assert run.lines[-2] == "copy: removed", (
        f"the run did not report its temp directory removed\n{run!r}"
    )
    assert list(private.iterdir()) == [], (
        f"the run left {sorted(private.iterdir())} under its TMPDIR"
    )

    # A run killed DURING discovery leaves exactly the one directory it announced (the
    # discovery scratch lives inside it, not beside it as a second `kill-sets.` dir).
    private = tmp_path / "killed-tmp"
    private.mkdir()
    root = t13_root("killed", [("m", "ok1\n", "no1\n")])
    campaign = root / "scripts" / "tests" / "mutate_fix.py"
    campaign.write_text(
        campaign.read_text().replace(
            "\n\ndef main()",
            "\nimport time  # noqa: E402\ntime.sleep(30)\n\n\ndef main()",
        )
    )
    tool = subprocess.Popen(
        [sys.executable, str(TOOL), "--campaign", str(campaign), "--root", str(root)],
        cwd=str(tmp_path),
        env=dict(env, TMPDIR=str(private)),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        announced = None
        for line in tool.stdout:
            if line.startswith("copy: "):
                announced = line.split()[1]
                break
        # The (tiny) copy and the discovery child follow; the child then sleeps 30 s.
        subprocess.run(["sleep", "2"], check=True)
        assert tool.poll() is None, "the tool finished before it could be killed"
        tool.kill()
        tool.wait()
        left = sorted(str(p) for p in private.iterdir())
        assert announced and left == [announced], (
            f"a killed run left {left}; it announced only {announced}"
        )
    finally:
        tool.kill()
        subprocess.run(["pkill", "-9", "-f", str(private)], capture_output=True)
        shutil.rmtree(private, ignore_errors=True)

    # A temp directory the teardown cannot remove is NOT reported removed: ERROR, rc 2.
    private = tmp_path / "locked-tmp"
    private.mkdir()
    root = t13_root(
        "locked", [("locks a directory in the copy", "ok1\n", "no1\nlockme\n")]
    )
    try:
        run = run_tool(root, env=dict(env, TMPDIR=str(private)))
        assert not run.has_line("copy: removed"), (
            f"`copy: removed` printed for a directory still present\n{run!r}"
        )
        assert any(x.startswith("ERROR  copy: NOT removed ") for x in run.lines), run
        assert run.status == "ERROR" and run.rc == 2, run
    finally:
        for p in private.rglob("*"):
            if p.is_dir() and not p.is_symlink():
                p.chmod(0o700)
        shutil.rmtree(private, ignore_errors=True)

    # The tool's own interpreter writes no bytecode beside itself (its `mutate` import).
    root = t13_root("pyc", [("m", "ok1\n", "no1\n")])
    own = _tool_copy(tmp_path, "pyctool", None)
    run = run_tool(root, env=env, tool=own)
    assert run.status == "PASS", run
    for lib in (own.parent / "lib", root / "scripts" / "lib"):
        assert not (lib / "__pycache__").exists(), f"a run wrote {lib / '__pycache__'}"


def test_t13d_leak_matching_no_mutant(tmp_path):
    """A live subject changed to text no mutant wrote is reported as matching none."""
    seen = tmp_path / "seen.txt"
    seen.write_text("")
    live_subject = tmp_path / "leakother" / "scripts" / "subject.sh"
    root = make_root(
        tmp_path,
        name="leakother",
        suite_text=T13_SUITE.replace("@SEEN@", str(seen)).replace(
            "@LIVE@", str(live_subject)
        ),
        mutations=[
            ("writes unrelated text to the live subject", "ok3\n", "no3\nleakother\n")
        ],
    )
    run = run_tool(root)
    assert run.has_line(
        "leak: none (the live subject changed during the run, matching no mutant)"
    ), run
    assert not run.has_line("ERROR  leak"), run


# --------------------------------------------------------------------------------------
# `--jobs N`: the flag, the worker copies, the verdict word and the header.
# --------------------------------------------------------------------------------------

POOL_NAMES = (
    "AUDIT_SHELL_JOBS",
    "AUDIT_PYTEST_JOBS",
    "AUDIT_SHELLCHECK_JOBS",
    "AUDIT_TEST_JOBS",
)
JOBS_LINE = (
    r"^jobs: 3 requested, 3 workers \(\d+ runnable mutants\); "
    r"load average at launch [0-9.]+ [0-9.]+ [0-9.]+ on \d+ CPUs$"
)
KILL_THREE = [
    ("kill row1", "ok1\n", "no1\n"),
    ("kill row2", "ok2\n", "no2\n"),
    ("kill row3", "ok3\n", "no3\n"),
]


def _env_without(*names: str) -> dict:
    return {k: v for k, v in os.environ.items() if k not in names}


def _line_index(run: Run, prefix: str) -> int:
    hits = [i for i, line in enumerate(run.lines) if line.startswith(prefix)]
    assert len(hits) == 1, f"{len(hits)} lines start {prefix!r}\n{run!r}"
    return hits[0]


def _parity_view(run: Run) -> list[str]:
    """The run's lines as a serial and a parallel run of one tree must agree on them.

    The lines only a parallel run prints, the copy paths, the two sentences that name the
    mode, the timings and the status word with its tail are what a reader may not compare.
    """
    view = []
    for line in _normalised(run):
        if line.startswith(("copy:", "jobs:", "proof:", "nested pools:")):
            continue
        if line.startswith(
            ("serial, one run per mutant", "parallel, one run per mutant")
        ):
            continue
        view.append(
            re.sub(r"^RESULT: \S+ (rc=\d+ .*?single_row=\d+).*$", r"RESULT: \1", line)
        )
    return view


def test_t15a_jobs_flag_and_verdict_shape(tmp_path):
    quoted = (
        "RESULT: PARALLEL-PASS rc=0 mutants=9 identical_pairs=0 killed_all=0 (quoted)"
    )
    four = [
        (quoted, "ok1\n", "no1\n"),
        ("kill two", "ok2\n", "no2\n"),
        ("kill three", "ok3\n", "no3\n"),
        ("kill four", "ok4\n", "no4\n"),
    ]

    # (a) A value outside 1..8 is the tool's own refusal: before any copy, the serial form.
    root = make_root(tmp_path, name="bad", mutations=[("m", "ok1\n", "no1\n")])
    for text in ("0", "-1", "abc", "9"):
        run = run_tool(root, "--jobs", text)
        assert run.status == "ERROR" and run.rc == 2, f"--jobs {text}\n{run!r}"
        assert run.result["mutants"] == 0 and run.order == FIELDS, (
            f"--jobs {text}: the RESULT line is not the serial form\n{run!r}"
        )
        assert any(
            f"--jobs {text}: expected an integer from 1 to 8" in line
            for line in run.error_lines()
        ), f"--jobs {text}: no ERROR line names the refusal\n{run!r}"
        assert not any(line.startswith("copy:") for line in run.lines), (
            f"--jobs {text}: a copy was made\n{run!r}"
        )
        assert not run.blocks, f"--jobs {text}: a mutant ran\n{run!r}"

    # (b) `--jobs 1` is the default run, line for line, with today's sentence and fields.
    root = make_root(
        tmp_path,
        name="ok",
        suite_text=generic_suite(rows=4),
        subject_text=tokens(4),
        mutations=four,
    )
    default = run_tool(root)
    one = run_tool(root, "--jobs", "1")
    assert one.status == "PASS" and one.order == FIELDS, one
    assert SERIAL_SENTENCE in one.lines, one
    assert not any(line.startswith("parallel, one run") for line in one.lines), one
    assert not any(line.startswith("jobs:") for line in one.lines), one
    assert _normalised(one) == _normalised(default), "--jobs 1 differs from the default"

    # (c) `--jobs 3`: the parallel sentence, the verdict word, the header and the tail.
    run = run_tool(root, "--jobs", "3")
    assert run.status == "PARALLEL-PASS" and run.rc == 0, run
    assert run.order == FIELDS_PARALLEL, f"field order {run.order}\n{run!r}"
    assert run.result["died"] == 0 and run.result["jobs"] == 3, run
    assert re.search(r" single_row=\d+ died=0 jobs=3$", run.lines[-1]), run
    sentence = next(
        x for x in run.lines if x.startswith("parallel, one run per mutant")
    )
    assert "on its own copy, 3 workers:" in sentence, sentence
    assert "determinism NOT assessed" in sentence, sentence
    assert SERIAL_SENTENCE not in run.lines, run
    jobs_line = next(x for x in run.lines if x.startswith("jobs: 3 requested"))
    assert re.search(JOBS_LINE, jobs_line), (
        f"the jobs line has the wrong shape\n{run!r}"
    )
    assert (
        _line_index(run, "parse check:")
        < _line_index(run, "jobs: 3 requested")
        < _line_index(run, "BASELINE")
    ), f"the jobs line is not between the parse check and the baseline\n{run!r}"
    assert (
        _line_index(run, "leak:")
        < _line_index(run, "jobs: finished; load average ")
        < _line_index(run, "copy: removed")
    ), f"the closing jobs line is not between the leak check and the teardown\n{run!r}"
    assert re.search(
        r"^jobs: finished; load average [0-9.]+ [0-9.]+ [0-9.]+$",
        run.lines[_line_index(run, "jobs: finished")],
    ), run
    label_lines = [x for x in run.lines if x.startswith("[1/4]")]
    assert label_lines and re.search(EXPECT_PARALLEL[1:], label_lines[0]), (
        f"the label does not quote the grammar, so the case proves nothing\n{run!r}"
    )
    anchored = [x for x in run.lines if re.search(EXPECT_PARALLEL, x)]
    assert anchored == [run.lines[-1]], f"the anchored regex matched a label\n{run!r}"

    # FAIL and ERROR keep their words, with the tail.
    root_fail = make_root(
        tmp_path,
        name="fail",
        mutations=[("silent", "ok1\n", "silent1\n"), ("kill two", "ok2\n", "no2\n")],
    )
    run = run_tool(root_fail, "--jobs", "3")
    assert (run.status, run.rc) == ("FAIL", 1), run
    assert run.order == FIELDS_PARALLEL and run.result["jobs"] == 3, run
    root_error = make_root(
        tmp_path,
        name="error",
        mutations=[("absent", "nope\n", "x\n"), ("kill one", "ok1\n", "no1\n")],
    )
    run = run_tool(root_error, "--jobs", "3")
    assert (run.status, run.rc) == ("ERROR", 2), run
    assert run.order == FIELDS_PARALLEL and run.result["jobs"] == 3, run

    # (d) More workers than runnable mutants: the header counts the effective workers.
    root_one = make_root(
        tmp_path,
        name="one",
        mutations=[
            ("kill one", "ok1\n", "no1\n"),
            ("absent one", "nope1\n", "x\n"),
            ("absent two", "nope2\n", "x\n"),
        ],
    )
    run = run_tool(root_one, "--jobs", "4")
    assert any(
        x.startswith("jobs: 4 requested, 1 workers (1 runnable mutants);")
        for x in run.lines
    ), run
    first_copy = next(x for x in run.lines if x.startswith("copy: "))
    assert "working copies W0..W3 re-created per mutant" in first_copy, first_copy

    # (e) The free-space gate refuses before anything is copied; `--jobs 1` has none.
    huge = _tool_copy(
        tmp_path,
        "huge-factor",
        None,
        edit=("DISK_FACTOR = 2", "DISK_FACTOR = 1000000000"),
    )
    run = run_tool(root, "--jobs", "2", tool=huge)
    assert any(
        "MB free" in x and "nothing was copied" in x for x in run.error_lines()
    ), f"no ERROR line names the shortfall\n{run!r}"
    assert (run.status, run.rc) == ("ERROR", 2), run
    assert not run.has_line("BASELINE") and not run.blocks, run
    assert not run.has_line("copy: size"), f"a tree was copied\n{run!r}"
    run = run_tool(root, "--jobs", "1", tool=huge)
    assert run.status == "PASS", f"the gate refused a serial run\n{run!r}"

    # (f) The nested-pools line names exactly the pools that are set, in a fixed order.
    clean = _env_without(*POOL_NAMES)
    run = run_tool(root, "--jobs", "3", env=dict(clean, AUDIT_SHELL_JOBS="4"))
    want = "nested pools: AUDIT_SHELL_JOBS=4 (inherited by every suite)"
    assert run.lines.count(want) == 1, f"no exact nested pools line\n{run!r}"
    assert (
        _line_index(run, "jobs: 3 requested") + 1
        == run.lines.index(want)
        < _line_index(run, "BASELINE")
    ), run
    run = run_tool(
        root,
        "--jobs",
        "3",
        env=dict(clean, AUDIT_TEST_JOBS="2", AUDIT_SHELL_JOBS="4"),
    )
    assert (
        "nested pools: AUDIT_SHELL_JOBS=4 AUDIT_TEST_JOBS=2 (inherited by every suite)"
        in run.lines
    ), run
    run = run_tool(root, "--jobs", "1", env=dict(clean, AUDIT_SHELL_JOBS="4"))
    assert not any(x.startswith("nested pools:") for x in run.lines), run
    run = run_tool(root, "--jobs", "3", env=clean)
    assert not any(x.startswith("nested pools:") for x in run.lines), run

    # (g) `--keep` names every copy, and every named copy exists. The mutants carry `slow`,
    # so the three workers overlap and each one holds a copy on disk whatever the proof did.
    root_keep = make_root(
        tmp_path,
        name="keepslow",
        suite_text=generic_suite(rows=4),
        subject_text=tokens(4),
        mutations=[(label, old, new + "slow\n") for label, old, new in four],
    )
    private = tmp_path / "keep-tmp"
    private.mkdir()
    kept = None
    try:
        run = run_tool(
            root_keep, "--jobs", "3", "--keep", env=dict(clean, TMPDIR=str(private))
        )
        assert run.status == "PARALLEL-PASS", run
        first = next(x for x in run.lines if x.startswith("copy: "))
        kept = first.split()[1]
        line = next(x for x in run.lines if x.startswith("copy: kept "))
        listed = line.split("working copies ", 1)[1].rstrip(")").split()
        assert listed == [f"{kept}/work", f"{kept}/pool/1", f"{kept}/pool/2"], line
        for path in listed:
            assert Path(path).is_dir(), (
                f"{path} was printed but does not exist\n{run!r}"
            )
    finally:
        if kept is not None:
            shutil.rmtree(kept, ignore_errors=True)


@pytest.mark.serial_only
def test_t15b_parity_and_order(tmp_path):
    mutations = [
        ("slow head kills row1", "ok1\n", "no1\nslow\n"),
        ("kills row1 again", "ok1\n", "xx1\n"),
        ("kills every row", "ok1\nok2\nok3\n", "zz\n"),
        ("survives", "keep_me\n", "keep_you\n"),
        ("anchor absent", "nope\n", "x\n"),
        ("does not parse", "ok3\n", "ok3 (\n"),
        ("rows vanish quietly", "ok2\n", "quiet2\n"),
        ("kills row3", "ok3\n", "no3\n"),
    ]
    root = make_root(
        tmp_path,
        name="mix",
        subject_text=tokens() + "keep_me\n",
        mutations=mutations,
    )
    serial = run_tool(root, "--jobs", "1")
    parallel = run_tool(root, "--jobs", "3")
    kinds = {b["status"] for b in serial.blocks.values()}
    assert kinds == {
        "KILLED",
        "KILLED-ALL",
        "SURVIVED",
        "NOT-APPLIED",
        "INVALID",
        "UNATTRIBUTED",
    }, f"the fixture does not mix the statuses: {kinds}\n{serial!r}"
    assert parallel.status == "ERROR" and parallel.result["jobs"] == 3, parallel
    for run in (serial, parallel):
        heads = [
            int(m.group(1))
            for m in (re.match(r"^\[(\d+)/\d+\] ", x) for x in run.lines)
            if m
        ]
        assert heads == list(range(1, len(mutations) + 1)), (
            f"the blocks are not in mutant order: {heads}\n{run!r}"
        )
        dups = [x for x in run.lines if x.startswith("DUPLICATE")]
        assert dups == ["DUPLICATE [1] [2] (k=1)"], f"{dups}\n{run!r}"
    left, right = _parity_view(serial), _parity_view(parallel)
    assert left == right, (
        "\n".join(
            f"serial:   {a}\nparallel: {b}"
            for a, b in zip(left, right, strict=False)
            if a != b
        )
        or f"serial has {len(left)} lines, parallel {len(right)}"
    )


def test_t16b1_default_and_logical_spelling(tmp_path):
    real = tmp_path / "realtmp"
    real.mkdir()
    link = tmp_path / "linktmp"
    link.symlink_to(real)
    env = dict(os.environ, TMPDIR=str(link))
    for name, resolve in (("realspelling", True), ("logicalspelling", False)):
        root = make_root(tmp_path, name=name, mutations=KILL_THREE, resolve=resolve)
        run = run_tool(root, "--jobs", "3", env=env)
        assert run.status == "PARALLEL-PASS", f"{name}\n{run!r}"
        first = next(x for x in run.lines if x.startswith("copy: "))
        assert first.startswith(f"copy: {link}{os.sep}kill-sets."), (
            f"{name}: the logical spelling was not the one in play\n{run!r}"
        )
        assert {n: b["rows"] for n, b in run.blocks.items()} == {
            1: ["row1"],
            2: ["row2"],
            3: ["row3"],
        }, f"{name}: a worker did not read its own copy\n{run!r}"


def test_t16b2_residue_refused(tmp_path):
    flagged = (
        '["bash", str(REPO / "scripts" / "tests" / "suite.sh"), '
        '"-C" + str(REPO / "scripts")]'
    )
    root = make_root(tmp_path, name="residue", mutations=KILL_THREE, suite_argv=flagged)
    run = run_tool(root, "--jobs", "3")
    assert_refused_before_baseline(run, "a token the swap cannot reach")
    for worker in (1, 2):
        assert any(
            f"worker {worker}: SUITE token '-C" in x
            and "still names the canonical copy" in x
            for x in run.error_lines()
        ), f"no ERROR line names the token and worker {worker}\n{run!r}"
    serial = run_tool(root, "--jobs", "1")
    assert serial.status == "PASS" and not serial.error_lines(), serial


def test_t16b3_cwd_relative_suite(tmp_path):
    root = make_root(
        tmp_path,
        name="cwdrel",
        mutations=[*KILL_THREE, ("survives", "keep_me\n", "keep_you\n")],
        subject_text=tokens() + "keep_me\n",
        suite_argv='["bash", "scripts/tests/suite_rel.sh"]',
    )
    absolute = 'subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"'
    text = generic_suite()
    assert absolute in text
    (root / "scripts" / "tests" / "suite_rel.sh").write_text(
        text.replace(absolute, 'subj="scripts/subject.sh"')
    )
    serial = run_tool(root, "--jobs", "1")
    parallel = run_tool(root, "--jobs", "3")
    shape = {n: (b["status"], b["rows"]) for n, b in serial.blocks.items()}
    assert shape[1] == ("KILLED", ["row1"]) and shape[4][0] == "SURVIVED", serial
    assert {n: (b["status"], b["rows"]) for n, b in parallel.blocks.items()} == shape, (
        f"a worker's suite did not read its own copy from its cwd\n{parallel!r}"
    )


def _three_workers(tmp_path, argv):
    tool = load_tool()
    tmp = tmp_path / "kill-sets.ab"
    canonical = tmp / "work"
    workers = tool.build_workers(tmp, canonical, "scripts/subject.sh", argv, 3)
    return tmp, canonical, workers


def test_t16b4_worker_copy_paths(tmp_path):
    tmp, canonical, workers = _three_workers(
        tmp_path, ["bash", "scripts/tests/suite_rel.sh"]
    )
    assert [w.work for w in workers] == [
        tmp / "work",
        tmp / "pool" / "1",
        tmp / "pool" / "2",
    ]
    assert [w.index for w in workers] == [0, 1, 2]
    for w in workers[1:]:
        assert not str(w.work).startswith(str(canonical)), (
            f"the canonical text is a prefix of {w.work}"
        )


def test_t16b5_worker_subjects(tmp_path):
    tmp, canonical, workers = _three_workers(
        tmp_path, ["bash", "scripts/tests/suite_rel.sh"]
    )
    subject_rel = "scripts/subject.sh"
    assert [w.subject for w in workers] == [
        tmp / "work" / subject_rel,
        tmp / "pool" / "1" / subject_rel,
        tmp / "pool" / "2" / subject_rel,
    ]


def test_t16b6_worker_argv(tmp_path):
    tmp = tmp_path / "kill-sets.ab"
    canonical = tmp / "work"
    argv = [
        "bash",
        str(canonical / "scripts/tests/suite.sh"),
        f"X={canonical}/scripts/tests/x.txt",
        "-v",
    ]
    _tmp, _canonical, workers = _three_workers(tmp_path, argv)
    assert workers[0].argv == argv
    for k in (1, 2):
        pool = f"{tmp}/pool/{k}"
        assert workers[k].argv == [
            "bash",
            f"{pool}/scripts/tests/suite.sh",
            f"X={pool}/scripts/tests/x.txt",
            "-v",
        ], workers[k].argv
        assert not any(str(canonical) in token for token in workers[k].argv)
    assert argv[1] == str(canonical / "scripts/tests/suite.sh"), "the input was changed"


@pytest.mark.serial_only
def test_t17_isolation_under_concurrency(tmp_path):
    root = make_root(
        tmp_path,
        name="overlap",
        mutations=[
            ("slow, kills row1", "ok1\n", "no1\nslow\n"),
            ("slow, kills row2", "ok2\n", "no2\nslow\n"),
            ("slow, kills row3", "ok3\n", "no3\nslow\n"),
        ],
    )
    serial = run_tool(root, "--jobs", "1")
    parallel = run_tool(root, "--jobs", "3")
    sets = {n: b["rows"] for n, b in serial.blocks.items()}
    assert sets == {1: ["row1"], 2: ["row2"], 3: ["row3"]}, serial
    assert parallel.status == "PARALLEL-PASS", parallel
    assert {n: b["rows"] for n, b in parallel.blocks.items()} == sets, (
        f"a concurrent run read another worker's copy\n{parallel!r}"
    )


# --------------------------------------------------------------------------------------
# The staged worker proof (D5) and the timeout limit derived from it (D8).
# --------------------------------------------------------------------------------------

NOT_SAFE = (
    "the suite is not safe to run concurrently here (a shared path or port, a label "
    "that carries the copy path, or a suite that reads another copy); re-run with "
    "--jobs 1"
)
KILL_TWO = KILL_THREE[:2]
SUBJECT_OF_COPY = 'subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"'

# A PASS label that carries the name of the copy it runs in, and a row that needs `ok1`, so
# the serial canary removes that row too and only the pristine comparison can see the label.
LABEL_ROW = r"""
here="$(cd "$(dirname "$0")/../.." && pwd)"
if has ok1; then echo "PASS  where $(basename "$here")"
else echo "FAIL  where (detail)"; fails=1; fi
"""

# Takes a fixed lock directory and, once the worker copies exist (the proof and the mutants,
# never the serial runs), holds it for three seconds; a suite that finds it held fails.
LOCK_ROW = r"""
copies="$(ls -d "$TMPDIR"/kill-sets.*)"
if mkdir "@LOCK@" 2>/dev/null; then
  if [ -d "$copies/pool/1" ]; then sleep 3; fi
  rmdir "@LOCK@"; echo "PASS  lock"
else echo "FAIL  lock (held by another suite)"; fails=1; fi
"""


def _proof_errors(run: Run) -> list[str]:
    return [x for x in run.lines if x.startswith("ERROR  proof: worker ")]


def test_t16a1_label_carries_the_copy_path(tmp_path):
    root = make_root(
        tmp_path,
        name="label",
        suite_text=generic_suite(extra=LABEL_ROW),
        mutations=KILL_TWO,
    )
    serial = run_tool(root, "--jobs", "1")
    assert serial.status == "PASS" and not serial.error_lines(), serial
    assert serial.blocks[1]["rows"] == ["row1", "where work"], serial
    run = run_tool(root, "--jobs", "2")
    assert (run.status, run.rc) == ("ERROR", 2), run
    assert run.has_line("BASELINE green"), f"the serial baseline did not run\n{run!r}"
    assert not run.blocks, f"a mutant was graded on an unproven pool\n{run!r}"
    errors = _proof_errors(run)
    assert len(errors) == 1, f"one difference expected\n{run!r}"
    assert errors[0].startswith("ERROR  proof: worker 1, round 1/2 (pristine): "), (
        errors
    )
    assert "where work" in errors[0] and "where 1" in errors[0], (
        f"the error does not name the differing rows\n{run!r}"
    )
    assert f"ERROR  {NOT_SAFE}" in run.lines, run


@pytest.mark.serial_only
def test_t16a2_fixed_lock_collision(tmp_path):
    lock = tmp_path / "shared.lock"
    private = tmp_path / "ptmp"
    private.mkdir()
    env = dict(os.environ, TMPDIR=str(private))
    root = make_root(
        tmp_path,
        name="lock",
        suite_text=generic_suite(extra=LOCK_ROW.replace("@LOCK@", str(lock))),
        mutations=KILL_THREE,
    )
    one = make_root(
        tmp_path,
        name="lockserial",
        suite_text=generic_suite(extra=LOCK_ROW.replace("@LOCK@", str(lock))),
        mutations=KILL_THREE[:1],
    )
    serial = run_tool(one, "--jobs", "1", env=env)
    assert serial.status == "PASS" and not serial.error_lines(), serial
    run = run_tool(root, "--jobs", "3", env=env)
    assert (run.status, run.rc) == ("ERROR", 2), run
    assert not run.blocks, f"a mutant was graded on an unproven pool\n{run!r}"
    assert _proof_errors(run), f"no proof difference named a worker\n{run!r}"
    assert f"ERROR  {NOT_SAFE}" in run.lines, run


def test_t16a3_proof_lines_per_round(tmp_path):
    def header(total, workers):
        return (
            rf"^proof: {total} rounds x {workers} workers, each about one suite run; "
            r"nothing is judged until the last round ends$"
        )

    def ran(i, total):
        return (
            rf"^proof: ran round {i}/{total} in [0-9.]+s, judged after the last round$"
        )

    def judged(i, total, holders, pristine, rows, absent):
        canary = f", canary absent={absent} equal" if holders != "none" else ""
        return (
            rf"^proof: round {i}/{total} canary in workers {holders} pristine in "
            rf"workers {pristine}: green rows={rows} equal{canary} \([0-9.]+s\)$"
        )

    def coverage(exercised, rows, aborted=False):
        if aborted:
            return (
                "^proof: the canary aborted collection, so no row is judged singly; "
                "a read of another copy's subject is visible only where it turns a "
                "pristine run red$"
            )
        if exercised == rows:
            return f"^proof: the canary exercises {rows} of {rows} rows$"
        return (
            f"^proof: the canary exercises {exercised} of {rows} rows; a read of "
            f"another copy's subject by the other {rows - exercised} rows is not "
            "visible to this proof$"
        )

    def summary(total, workers):
        return (
            rf"^proof: {total} rounds x {workers} workers concurrent; slowest "
            r"pristine run [0-9.]+s against serial [0-9.]+s; limit [0-9]+s"
            r"( \(raised from [0-9]+s\))?$"
        )

    def check(run, spec, workers, rows, absent, aborted=False):
        """Every `proof:` line, in order: header, progress, judged rounds, coverage, summary."""
        total = len(spec)
        want = [header(total, workers)]
        want += [ran(i, total) for i in range(1, total + 1)]
        want += [
            judged(i, total, holders, pristine, rows, absent)
            for i, (holders, pristine) in enumerate(spec, 1)
        ]
        want += [coverage(absent, rows, aborted), summary(total, workers)]
        lines = [x for x in run.lines if x.startswith("proof: ")]
        assert len(lines) == len(want), (
            f"{len(lines)} proof lines, {len(want)} wanted\n{run!r}"
        )
        for n, (line, pattern) in enumerate(zip(lines, want, strict=True), 1):
            assert re.search(pattern, line), f"proof line {n}: {line!r}\n{run!r}"
        assert (
            _line_index(run, "canary:")
            < run.lines.index(lines[0])
            < run.lines.index(lines[-1])
            < _line_index(run, "[1/")
        ), f"the proof lines are not between the canary and the first mutant\n{run!r}"

    # One worker holds the canary per round: the three codes are the three one-round subsets.
    three = [("0", "1,2"), ("1", "0,2"), ("2", "0,1")]
    root = make_root(tmp_path, name="rounds", mutations=KILL_THREE)

    # Every row changes its verdict under the canary: the short coverage form.
    run = run_tool(root, "--jobs", "3")
    assert run.status == "PARALLEL-PASS", run
    check(run, three, 3, rows=3, absent=3)

    # One row answers the same under the canary: the coverage line names the gap.
    root_const = make_root(
        tmp_path,
        name="const",
        suite_text=generic_suite(extra='echo "PASS  const"'),
        mutations=KILL_THREE,
    )
    run = run_tool(root_const, "--jobs", "3")
    assert run.status == "PARALLEL-PASS", run
    check(run, three, 3, rows=4, absent=3)

    # A pytest suite that imports its subject: the canary aborts collection, so no row is
    # judged singly and the coverage line says that instead of counting rows.
    imports = (
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
        "import subj  # noqa: E402\n"
        + pytest_test("test_ok1", "assert 'ok1' in subj.OK")
        + pytest_test("test_ok2", "assert 'ok2' in subj.OK")
    )
    root_abort = make_root(
        tmp_path,
        name="aborts",
        family="pytest",
        suite_text=imports,
        mutations=[("kills one", "'ok1'", "'no1'"), ("kills two", "'ok2'", "'no2'")],
    )
    run = run_tool(root_abort, "--jobs", "2")
    assert run.has_line("canary: collection aborted"), run
    assert run.status == "PARALLEL-PASS", run
    check(run, [("0", "1"), ("1", "0")], 2, rows=2, absent=2, aborted=True)

    run = run_tool(root, "--jobs", "2")
    assert run.status == "PARALLEL-PASS", run
    check(run, [("0", "1"), ("1", "0")], 2, rows=3, absent=3)

    # Fewer than two effective workers: no concurrent phase, so no proof run, and the line says
    # which of the two cases it was.
    root_one = make_root(
        tmp_path,
        name="one",
        mutations=[
            ("kill one", "ok1\n", "no1\n"),
            ("absent one", "nope1\n", "x\n"),
            ("absent two", "nope2\n", "x\n"),
        ],
    )
    run = run_tool(root_one, "--jobs", "4")
    assert "proof: skipped (one effective worker)" in run.lines, run
    assert not any(x.startswith("proof: round") for x in run.lines), run
    root_none = make_root(
        tmp_path,
        name="none",
        mutations=[("absent one", "nope1\n", "x\n"), ("absent two", "nope2\n", "x\n")],
    )
    run = run_tool(root_none, "--jobs", "3")
    assert any(
        x.startswith("jobs: 3 requested, 0 workers (0 runnable mutants);")
        for x in run.lines
    ), run
    assert "proof: skipped (no runnable mutants)" in run.lines, run
    assert not any(x.startswith("proof: round") for x in run.lines), run

    run = run_tool(root, "--jobs", "1")
    assert not any(x.startswith("proof:") for x in run.lines), run


def test_t16c_cross_read_visible(tmp_path):
    private = tmp_path / "ptmp"
    private.mkdir()
    env = dict(os.environ, TMPDIR=str(private))
    text = generic_suite()
    own = 'subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"'
    canonical = 'subj="$(ls -d "$TMPDIR"/kill-sets.*/work)/scripts/subject.sh"'
    assert own in text
    root = make_root(
        tmp_path,
        name="crossread",
        suite_text=text.replace(own, canonical),
        mutations=KILL_THREE,
    )
    serial = run_tool(root, "--jobs", "1", env=env)
    assert serial.status == "PASS" and not serial.error_lines(), serial
    assert {n: b["rows"] for n, b in serial.blocks.items()} == {
        1: ["row1"],
        2: ["row2"],
        3: ["row3"],
    }, serial
    run = run_tool(root, "--jobs", "3", env=env)
    assert (run.status, run.rc) == ("ERROR", 2), run
    assert not run.blocks, f"a mutant was graded on an unproven pool\n{run!r}"
    errors = _proof_errors(run)
    # Worker 0 always reads its own copy; every other worker reads copy 0. Worker 0 holds the
    # canary in round 1 only, worker 1 in round 2 only, worker 2 in round 3 only. So workers 1
    # and 2 read `{` while pristine in round 1, and worker 1 (round 2) and worker 2 (round 3)
    # read copy 0 pristine while holding. No other round differs.
    heads = {
        "ERROR  proof: worker 1, round 1/3 (pristine): ",
        "ERROR  proof: worker 2, round 1/3 (pristine): ",
        "ERROR  proof: worker 1, round 2/3 (canary holder): ",
        "ERROR  proof: worker 2, round 3/3 (canary holder): ",
    }
    seen = {m.group(0) for m in (re.match(r"^[^:]*: [^:]*: ", x) for x in errors) if m}
    assert seen == heads, f"the differences named {sorted(seen)}\n{run!r}"
    assert f"ERROR  {NOT_SAFE}" in run.lines, run


PROBE_START = r"""
here="$(cd "$(dirname "$0")/../.." && pwd)"
tag="$(basename "$here")"
copies="$(ls -d "$TMPDIR"/kill-sets.*)"
state() {
  f="$1/scripts/subject.sh"
  if [ ! -f "$f" ]; then printf '?'
  elif grep -qx -- '{' "$f"; then printf 'C'
  elif grep -qx -- 'ok1' "$f"; then printf 'P'
  else printf '?'; fi
}
vector() {
  printf '%s%s%s' "$(state "$copies/work")" "$(state "$copies/pool/1")" \
    "$(state "$copies/pool/2")"
}
echo "S $(vector) $tag" >> "@LOG@"
proof_phase=0
[ -d "$copies/pool/1" ] && proof_phase=1
"""

# Once the worker copies exist (the proof and the mutants, never the serial runs), a suite
# running in `work` fills its own copy with 20000 empty files and holds three seconds, so the
# next refresh of that copy takes about a second; a suite elsewhere holds 2.5 s.
PROBE_END = r"""
if [ "$proof_phase" = 1 ]; then
  if [ "$tag" = work ]; then
    mkdir -p "$here/junk"
    (cd "$here/junk" && for i in $(seq 1 20000); do : > "f$i"; done)
    sleep 3
  else
    sleep 2.5
  fi
fi
echo "E $(vector) $tag" >> "@LOG@"
"""

PROBE_TARGETS = ["CPP", "PCP", "PPC"]


def _probe_suite(log: Path) -> str:
    text = generic_suite(extra=PROBE_END.replace("@LOG@", str(log)))
    assert text.count("fails=0\n") == 1
    return text.replace(
        "fails=0\n", "fails=0\n" + PROBE_START.replace("@LOG@", str(log)), 1
    )


@pytest.fixture(scope="module")
def probe_pairs(tmp_path_factory):
    """One probe run at `--jobs 3`, shared by `t16d1` and `t16d2`.

    Returns the run and, per proof round, each worker's START and END records. A worker's
    records alternate S, E in its own order, so they are paired per worker: worker 0's first
    three pairs are the serial baseline twice and the canary, the proof rounds follow.
    """
    base = tmp_path_factory.mktemp("probe")
    private = base / "ptmp"
    private.mkdir()
    log = private / "probe.log"
    root = make_root(
        base,
        name="probe",
        suite_text=_probe_suite(log),
        mutations=KILL_THREE,
    )
    run = run_tool(root, "--jobs", "3", env=dict(os.environ, TMPDIR=str(private)))
    records: dict[str, list] = {"work": [], "1": [], "2": []}
    for line in log.read_text().splitlines():
        kind, vector, tag = line.split()
        records[tag].append((kind, vector))
    rounds = []
    for tag, skip in (("work", 3), ("1", 0), ("2", 0)):
        kinds = [k for k, _v in records[tag]]
        assert kinds == ["S", "E"] * (len(kinds) // 2), (
            f"worker {tag}: records do not alternate S, E: {kinds}"
        )
        pairs = [
            (records[tag][i][1], records[tag][i + 1][1])
            for i in range(0, len(kinds), 2)
        ]
        rounds.append(pairs[skip : skip + 3])
    return run, rounds


@pytest.mark.serial_only
def test_t16d1_start_barrier(probe_pairs):
    run, workers = probe_pairs
    assert run.status == "PARALLEL-PASS", run
    assert all(len(pairs) == 3 for pairs in workers), (
        f"a worker ran fewer than three proof rounds: {workers}\n{run!r}"
    )
    for r in range(3):
        starts = [pairs[r][0] for pairs in workers]
        assert len(set(starts)) == 1, f"round {r + 1}: the starts disagree {starts}"
        assert starts == [PROBE_TARGETS[r]] * 3, (
            f"round {r + 1}: a copy was unwritten or mid-refresh when a suite started: "
            f"{starts}, wanted {PROBE_TARGETS[r]}"
        )


@pytest.mark.serial_only
def test_t16d2_end_barrier(probe_pairs):
    run, workers = probe_pairs
    assert run.status == "PARALLEL-PASS", run
    assert all(len(pairs) == 3 for pairs in workers), (
        f"a worker ran fewer than three proof rounds: {workers}\n{run!r}"
    )
    for tag, pairs in zip(("work", "1", "2"), workers, strict=True):
        for r, (start, end) in enumerate(pairs):
            assert start == end, (
                f"worker {tag}, round {r + 1}: a copy changed while its suite ran "
                f"({start} at the start, {end} at the end)"
            )


@pytest.mark.serial_only
def test_t16e1_timeout_line_under_jobs(tmp_path):
    root = make_root(
        tmp_path,
        name="timeout",
        mutations=[
            ("hangs", "ok1\n", "no1\nhang\n"),
            ("kills row2", "ok2\n", "no2\n"),
        ],
    )
    run = run_tool(root, "--jobs", "2", "--timeout", "10")
    assert run.blocks[1]["status"] == "TIMEOUT", run
    assert run.result["timedout"] == 1 and (run.status, run.rc) == ("ERROR", 2), run
    line = next(
        (
            x
            for x in run.blocks[1]["lines"]
            if x.startswith("jobs=2: load average now ")
        ),
        None,
    )
    assert line is not None, f"the TIMEOUT block has no jobs line\n{run!r}"
    assert re.fullmatch(
        r"jobs=2: load average now [0-9.]+ [0-9.]+ [0-9.]+; a timeout under load is "
        r"not a kill; re-run with a lower --jobs or a larger --timeout",
        line,
    ), line
    serial = run_tool(root, "--jobs", "1", "--timeout", "10")
    assert serial.blocks[1]["status"] == "TIMEOUT", serial
    assert not any(x.startswith("jobs=") for x in serial.blocks[1]["lines"]), serial


def test_t16e2_limit_follows_the_slowest_pristine_run():
    tool = load_tool()
    assert tool.derive_proof_limit(1.0, [0.5, 7.0], None) == 140.0
    assert tool.derive_proof_limit(1.0, [0.5], None) == 60
    assert tool.derive_proof_limit(1.0, [0.5, 7.0], 5.0) == 5.0
    # Pinned values, written out by hand, so a silent change of the assignment is noticed.
    assert [tool.proof_rounds(e) for e in (2, 3, 4, 6, 7, 8)] == [2, 3, 4, 4, 5, 5]
    vectors = [
        "".join("C" if tool.holds_canary(k, r, 3) else "P" for k in range(3))
        for r in range(3)
    ]
    assert vectors == PROBE_TARGETS
    four = [
        "".join("C" if tool.holds_canary(k, r, 4) else "P" for k in range(4))
        for r in range(4)
    ]
    assert four == ["CCCP", "CPPC", "PCPC", "PPCP"], four
    # The properties, at every worker count up to 64: the round count is the smallest that has
    # enough codes; every worker holds the canary in some round and is pristine in another;
    # no two codes are equal; and for every ordered pair (a, b) some round has a pristine and
    # b holding, which is what makes a read of b's copy by a visible whatever a's own copy holds.
    for count in range(2, 65):
        rounds = tool.proof_rounds(count)
        assert math.comb(rounds, rounds // 2) >= count, (
            f"E={count}: R={rounds} too small"
        )
        assert rounds == 1 or math.comb(rounds - 1, (rounds - 1) // 2) < count, (
            f"E={count}: R={rounds} is not the smallest"
        )
        holds = [
            [tool.holds_canary(k, r, rounds) for r in range(rounds)]
            for k in range(count)
        ]
        for k, code in enumerate(holds):
            assert any(code) and not all(code), f"E={count}: worker {k} {code}"
        assert len({tuple(c) for c in holds}) == count, f"E={count}: a shared code"
        for a in range(count):
            for b in range(count):
                if a != b:
                    assert any(
                        b_holds and not a_holds
                        for a_holds, b_holds in zip(holds[a], holds[b], strict=True)
                    ), f"E={count}: no round with {a} pristine and {b} holding"


# --------------------------------------------------------------------------------------
# A failed mutant costs one mutant; a failed refresh costs its worker (D9).
# --------------------------------------------------------------------------------------

GRADE_ANCHOR = "outcome = self.grade(n, label, old, run)"
REFRESH_ANCHOR = "refresh_working_copy(self.master, worker.work)"
REFRESH_ONCE = (
    'if n == 2 and not globals().get("_FIRED"):\n'
    '    globals()["_FIRED"] = True\n'
    '    raise OSError("kill-sets test: refresh failed")'
)
REFRESH_ALWAYS = 'raise OSError("kill-sets test: refresh failed")'
NO_WORKER = "no worker left: every worker retired after a failed refresh"
FOUR_MARKED = [(f"kill row{i}", f"ok{i}\n", f"no{i}\nmark{i}\n") for i in range(1, 5)]
MARK_LOG_ROW = (
    'for i in 1 2 3 4; do if has "mark$i"; then echo "mark$i" >> "@LOG@"; fi; done'
)


def _marking_root(base: Path, name: str, log: Path, mutations=FOUR_MARKED) -> Path:
    """Four-row fixture whose suite appends `markN` to `log` when the mutant carries it."""
    return make_root(
        base,
        name=name,
        suite_text=generic_suite(rows=4, extra=MARK_LOG_ROW.replace("@LOG@", str(log))),
        subject_text=tokens(4),
        mutations=mutations,
    )


def _died_view(run: Run) -> list[str]:
    """The parity view with the worker number masked: which worker took a mutant is a schedule."""
    return [re.sub(r"\(worker \d+", "(worker W", x) for x in _parity_view(run)]


def test_t18a_died_injected(tmp_path):
    # The fault is keyed to mutant number 2, never to a worker, so the same mutant dies at
    # every `--jobs`; only the worker named in its block can differ.
    tool = _injected(
        tmp_path,
        "died-tool",
        GRADE_ANCHOR,
        'if n == 2:\n    raise RuntimeError("kill-sets test: injected")',
    )
    root = make_root(tmp_path, name="died", mutations=KILL_THREE)
    runs = {jobs: run_tool(root, "--jobs", jobs, tool=tool) for jobs in ("1", "3")}
    for jobs, run in runs.items():
        assert run.status == "ERROR" and run.rc == 2, f"--jobs {jobs}\n{run!r}"
        assert run.result.get("died") == 1, f"--jobs {jobs}\n{run!r}"
        assert not run.has_line("ERROR  internal exception"), f"--jobs {jobs}\n{run!r}"
        assert [run.blocks[n]["status"] for n in (1, 2, 3)] == [
            "KILLED",
            "DIED",
            "KILLED",
        ], f"--jobs {jobs}\n{run!r}"
        assert run.blocks[1]["rows"] == ["row1"] and run.blocks[3]["rows"] == ["row3"]
        died = run.blocks[2]
        assert died["k"] == 0 and died["label"] == "kill row2", died
        assert re.fullmatch(
            r"RuntimeError: kill-sets test: injected  \(worker \d\)", died["lines"][0]
        ), died
        tail = died["lines"][1:]
        assert 1 <= len(tail) <= 3 and all(x.startswith("! ") for x in tail), died
    left, right = _died_view(runs["1"]), _died_view(runs["3"])
    assert left == right, (
        "\n".join(
            f"--jobs 1: {a}\n--jobs 3: {b}"
            for a, b in zip(left, right, strict=False)
            if a != b
        )
        or f"--jobs 1 has {len(left)} lines, --jobs 3 {len(right)}"
    )


@pytest.fixture(scope="module")
def refresh_once(tmp_path_factory):
    """One run: the refresh before mutant 2 fails ONCE (a second try would succeed), --jobs 1.

    Returns the run and the `markN` lines the suites appended, in order. The fault is keyed
    to mutant number 2 and there is one worker, so which mutant dies and which worker is
    left are fixed by the fixture.
    """
    base = tmp_path_factory.mktemp("refresh-once")
    log = base / "ran.log"
    log.write_text("")
    tool = _injected(base, "once-tool", REFRESH_ANCHOR, REFRESH_ONCE)
    root = _marking_root(base, "once", log)
    run = run_tool(root, "--jobs", "1", tool=tool, timeout=120)
    return run, log.read_text().split()


def test_t18b1a_blocks_after_a_transient_refresh_failure(refresh_once):
    run, _marks = refresh_once
    assert not run.has_line("ERROR  internal exception"), run
    assert [run.blocks[n]["status"] for n in (1, 2, 3, 4)] == [
        "KILLED",
        "DIED",
        "DIED",
        "DIED",
    ], run
    assert run.blocks[1]["rows"] == ["row1"], run
    assert run.blocks[2]["lines"][0] == (
        "OSError: kill-sets test: refresh failed  (worker 0, refresh)"
    ), run
    for n in (3, 4):
        assert run.blocks[n]["lines"] == [NO_WORKER], run
        assert run.blocks[n]["k"] == 0, run
    assert run.result["died"] == 3, run
    assert run.status == "ERROR" and run.rc == 2, (
        f"a run that lost three mutants must not end clean\n{run!r}"
    )


def test_t18b1b_a_retired_worker_takes_nothing_more(refresh_once):
    run, marks = refresh_once
    # Mutant 1's suite ran (the log works); mutant 2's refresh failed before its suite; the
    # retired worker took neither mutant 3 nor mutant 4, though a second try would succeed.
    assert marks == ["mark1"], f"the suites that ran: {marks}\n{run!r}"


def test_t18b2_transient_refresh_failure_jobs3(tmp_path):
    # The fault names mutant 2, so mutant 2 dies on whichever worker takes it; that worker
    # retires and the other two take every remaining mutant (a worker leaves its loop only
    # when the queue is empty or after its own failed refresh).
    log = tmp_path / "ran.log"
    log.write_text("")
    tool = _injected(tmp_path, "once3-tool", REFRESH_ANCHOR, REFRESH_ONCE)
    root = _marking_root(tmp_path, "once3", log)
    run = run_tool(root, "--jobs", "3", tool=tool, timeout=120)
    assert not run.has_line("ERROR  internal exception"), run
    assert [run.blocks[n]["status"] for n in (1, 2, 3, 4)] == [
        "KILLED",
        "DIED",
        "KILLED",
        "KILLED",
    ], run
    assert [run.blocks[n]["rows"] for n in (1, 3, 4)] == [["row1"], ["row3"], ["row4"]]
    assert re.fullmatch(
        r"OSError: kill-sets test: refresh failed  \(worker \d, refresh\)",
        run.blocks[2]["lines"][0],
    ), run
    assert run.result["died"] == 1 and run.result["jobs"] == 3, run
    assert run.status == "ERROR" and run.rc == 2, run
    assert sorted(log.read_text().split()) == ["mark1", "mark3", "mark4"]


@pytest.mark.serial_only
def test_t18b3_refresh_always_fails(tmp_path):
    # Every refresh at the mutant step fails, so each of the three workers takes ONE mutant
    # (the queue holds five, more than the workers) and retires: the first three mutants, in
    # queue order, die on the refresh and the last two are filled as `no worker left`.
    log = tmp_path / "ran.log"
    log.write_text("")
    five = FOUR_MARKED + [("kill row4 again", "ok4\n", "xx4\n")]
    tool = _injected(tmp_path, "always-tool", REFRESH_ANCHOR, REFRESH_ALWAYS)
    root = _marking_root(tmp_path, "always", log, mutations=five)
    started = time.monotonic()
    run = run_tool(root, "--jobs", "3", tool=tool, timeout=120)
    elapsed = time.monotonic() - started
    assert elapsed < 60, f"the run took {elapsed:.0f}s, a retired pool must not wait"
    assert not run.has_line("ERROR  internal exception"), run
    assert [run.blocks[n]["status"] for n in range(1, 6)] == ["DIED"] * 5, run
    workers = set()
    for n in (1, 2, 3):
        m = re.fullmatch(
            r"OSError: kill-sets test: refresh failed  \(worker (\d), refresh\)",
            run.blocks[n]["lines"][0],
        )
        assert m, run.blocks[n]
        workers.add(m.group(1))
    assert workers == {"0", "1", "2"}, f"a worker died twice or not at all: {workers}"
    for n in (4, 5):
        assert run.blocks[n]["lines"] == [NO_WORKER], run
    assert run.result["died"] == 5 == run.result["mutants"], run
    assert run.status == "ERROR" and run.rc == 2, run
    assert log.read_text() == "", "a suite ran for a mutant whose refresh failed"


def test_t18b4_undeletable_copy(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can delete a directory that has no write permission")
    # Mutant 1's suite locks a directory inside its own copy, so the NEXT refresh (mutant
    # 2's) cannot delete it: the real OSError, not an injected one. One worker.
    lock = (
        'if has lockme; then d="$(dirname "$subj")/locked"; mkdir -p "$d"; '
        ': > "$d/f"; chmod 500 "$d"; fi'
    )
    root = make_root(
        tmp_path,
        name="undeletable",
        suite_text=generic_suite(extra=lock),
        mutations=[
            ("locks its own copy", "ok1\n", "no1\nlockme\n"),
            ("kill row2", "ok2\n", "no2\n"),
            ("kill row3", "ok3\n", "no3\n"),
        ],
    )
    private = tmp_path / "undeletable-tmp"
    private.mkdir()
    try:
        run = run_tool(
            root,
            "--jobs",
            "1",
            env=dict(os.environ, TMPDIR=str(private)),
            timeout=120,
        )
    finally:
        # Restore the permissions the suite removed, then delete what this row created.
        for p in private.rglob("*"):
            if p.is_dir() and not p.is_symlink():
                p.chmod(0o700)
        shutil.rmtree(private, ignore_errors=True)
        assert not private.exists(), f"the private directory was left behind: {private}"
    assert not run.has_line("ERROR  internal exception"), run
    assert [run.blocks[n]["status"] for n in (1, 2, 3)] == [
        "KILLED",
        "DIED",
        "DIED",
    ], run
    assert re.fullmatch(
        r"\w+Error: .*  \(worker 0, refresh\)", run.blocks[2]["lines"][0]
    )
    assert run.blocks[3]["lines"] == [NO_WORKER], run
    assert run.result["died"] == 2, run
    assert run.status == "ERROR" and run.rc == 2, run
    # The ERROR verdict comes from the teardown, which could not remove the locked copy;
    # D9 itself ends a run with died != 0 the same way, so the row names its own cause.
    assert run.has_line("ERROR  copy: NOT removed "), (
        f"the teardown did not report the undeletable copy\n{run!r}"
    )


# --------------------------------------------------------------------------------------
# SIGTERM, SIGHUP and SIGINT stop a run cleanly and are not turned into a verdict (D10).
# --------------------------------------------------------------------------------------

# A suite whose `slow` subjects start a long marker process, log the start, signal that they
# are in flight (the READY file) and then hold the suite LEADER for 60 s, far past the 20 s the
# tool joins its workers: a run that does not kill them cannot see them end on their own.
# `waitready` holds a run until some other run is in flight. The marker is unique per scenario,
# and EVERY long-lived process of a scenario carries it (the background sleep, the 60 s hold
# as `<marker>-hold`, the wait loop as `<marker>-wait`), so one `pkill -f <marker>` reaches
# whatever a failed scenario leaves behind.
T19_SUITE = r"""#!/usr/bin/env bash
subj="$(cd "$(dirname "$0")/.." && pwd)/subject.sh"
has() { grep -qx -- "$1" "$subj"; }
if has slow; then
  bash -c 'exec -a @MARK@ sleep 120' &
  echo slow >> "@LOG@"
  : > "@READY@"
  bash -c 'exec -a @MARK@-hold sleep 60'
fi
if has waitready; then
  bash -c 'for i in $(seq 1 300); do [ -e "$1" ] && break; sleep 0.1; done' \
    @MARK@-wait "@READY@"
fi
if has waitgo; then
  bash -c 'for i in $(seq 1 300); do [ -e "$1" ] && break; sleep 0.1; done' \
    @MARK@-wait "@GO@"
fi
fails=0
for n in 1 2 3; do
  if has "ok$n"; then echo "PASS  row$n"; else echo "FAIL  row$n"; fails=1; fi
done
exit $fails
"""

# Mutants 1 to 3 add nothing slow (the first wave ends at once and block [1/6] prints);
# mutants 4 to 6 carry `slow` (the second wave is in flight when the signal arrives).
T19_MUTANTS = [
    ("kill row1", "ok1\n", "no1\n"),
    ("kill row2", "ok2\n", "no2\n"),
    ("kill row3", "ok3\n", "no3\n"),
    ("slow one", "ok1\n", "ok1\nslow\n"),
    ("slow two", "ok2\n", "ok2\nslow\n"),
    ("slow three", "ok3\n", "ok3\nslow\n"),
]

# Started through this shim because the test's own process may have inherited SIGINT and
# SIGHUP as ignored, which the tool's handler (rightly) leaves alone: the shim makes both
# default, then replaces itself by the tool, so the tool runs under the pid that is signalled.
T19_SHIM = (
    "import os, signal, sys\n"
    "for s in (signal.SIGINT, signal.SIGHUP):\n"
    "    signal.signal(s, signal.SIG_DFL)\n"
    "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
)
T19_PROBE = (
    "import signal; "
    "print([signal.getsignal(s) is signal.SIG_IGN "
    "for s in (signal.SIGINT, signal.SIGHUP)])"
)


def _t19_read(proc, lines: list) -> threading.Thread:
    """Pump the tool's stdout into `lines` from a thread.

    Set the returned thread's `stop`, join it, then close `proc.stdout`, and the tool's
    next write meets a pipe nobody reads.
    """
    fd = proc.stdout.fileno()
    stop = threading.Event()

    def pump():
        pending = b""
        while not stop.is_set():
            if not select.select([fd], [], [], 0.1)[0]:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                return
            *whole, pending = (pending + chunk).split(b"\n")
            lines.extend(x.decode("utf-8", "replace") for x in whole)

    thread = threading.Thread(target=pump, daemon=True)
    thread.stop = stop
    thread.start()
    return thread


def _t19_wait_for(proc, lines, ready, what: str, limit: float = 120.0) -> None:
    """Poll until `ready()` holds; fail at once if the tool ends first or the limit passes."""
    deadline = time.monotonic() + limit
    while not ready():
        if proc.poll() is not None:
            pytest.fail(f"the tool ended before {what}\n" + "\n".join(lines))
        if time.monotonic() > deadline:
            pytest.fail(f"{what} did not happen in {limit:.0f}s\n" + "\n".join(lines))
        time.sleep(0.05)


def _t19_log_count(log: Path) -> int:
    return len(log.read_text().split()) if log.exists() else 0


def _t19_scenario(base, name, signum, phase, jobs, sends=1, closed=False) -> dict:
    """One run of the tool, one signal sent at a moment the fixture observed.

    `phase` is `baseline` (the pristine subject is slow, so the first baseline run is the
    one in flight) or `mutants` (block [1/6] has printed and every worker of the second wave
    is inside a slow suite). With `closed`, the read end of the tool's stdout is closed
    after those observables and before the signal, as a hangup with a dead terminal leaves
    it: nothing can read the interrupted line, and the result says so (`readable`).
    Returns what the rows read. Anything this run started is removed in the `finally`, by
    this scenario's own marker (which every process of the scenario carries) and its own
    pid only.
    """
    marker = f"ksmark-{os.getpid()}-{name}-{secrets.token_hex(4)}"
    private = base / f"{name}-tmp"
    private.mkdir()
    log, ready = private / "slow.log", private / "ready"
    in_flight = 1 if phase == "baseline" or jobs == 1 else jobs
    root = make_root(
        base,
        name=name,
        suite_text=T19_SUITE.replace("@MARK@", marker)
        .replace("@LOG@", str(log))
        .replace("@READY@", str(ready)),
        subject_text=tokens() + ("slow\n" if phase == "baseline" else ""),
        mutations=T19_MUTANTS,
    )
    argv = [
        sys.executable,
        "-c",
        T19_SHIM,
        str(TOOL),
        "--campaign",
        str(root / "scripts" / "tests" / "mutate_fix.py"),
        "--root",
        str(root),
        "--jobs",
        str(jobs),
    ]
    lines: list = []
    errors = (base / f"{name}.err").open("w")
    proc = subprocess.Popen(
        argv,
        cwd=str(base),
        env=dict(os.environ, TMPDIR=str(private)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=errors,
        text=True,
        errors="replace",
    )
    reader = _t19_read(proc, lines)
    try:
        _t19_wait_for(
            proc,
            lines,
            lambda: (
                _t19_log_count(log) >= in_flight
                and (phase == "baseline" or any(x.startswith("[1/6]") for x in lines))
            ),
            f"{in_flight} slow suite(s) were in flight",
        )
        if closed:
            reader.stop.set()
            reader.join(timeout=10)
            proc.stdout.close()
        for _ in range(sends):
            os.kill(proc.pid, signum)
        rc = proc.wait(timeout=90)
        reader.join(timeout=30)
        return {
            "rc": rc,
            "lines": list(lines),
            "marker": _pgrep(marker),
            "dirs": sorted(p.name for p in private.glob("kill-sets.*")),
            "in_flight": in_flight,
            "signum": signum,
            "readable": not closed,
        }
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        subprocess.run(["pkill", "-9", "-f", marker], capture_output=True)
        errors.close()


T19_SCENARIOS = [
    ("baseline-term", signal.SIGTERM, "baseline", 1, 1),
    ("mutants-term-3", signal.SIGTERM, "mutants", 3, 1),
    ("mutants-term-twice", signal.SIGTERM, "mutants", 3, 2),
    ("mutants-int-1", signal.SIGINT, "mutants", 1, 1),
    ("mutants-hup-3", signal.SIGHUP, "mutants", 3, 1),
    ("mutants-hup-3-closed", signal.SIGHUP, "mutants", 3, 1, True),
]


@pytest.fixture(scope="module")
def signal_runs(tmp_path_factory):
    """The six signal scenarios, run once each and shared by `t19a` and `t19b`.

    First the shim is run, under a parent that ignores SIGINT and SIGHUP, on a probe that
    reports whether either is still ignored: a shim that failed to reset them would make a
    scenario's signal vanish into the tool's `SIG_IGN` skip instead of reaching its handler.
    """
    base = tmp_path_factory.mktemp("signals")
    probe = subprocess.run(
        ["sh", "-c", 'trap "" INT HUP; exec "$@"', "sh", sys.executable, "-c"]
        + [T19_SHIM, "-c", T19_PROBE],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert probe.stdout.strip() == "[False, False]", (
        f"the exec shim left SIGINT or SIGHUP ignored: {probe.stdout!r} {probe.stderr!r}"
    )
    return {spec[0]: _t19_scenario(base, *spec) for spec in T19_SCENARIOS}


def _interrupted_lines(result: dict) -> list[str]:
    return [x for x in result["lines"] if "interrupted by" in x]


@pytest.mark.serial_only
def test_t19a_signals_exit_by_the_signal(signal_runs):
    for name, result in signal_runs.items():
        shown = "\n".join(result["lines"])
        assert result["rc"] == -result["signum"], (
            f"{name}: exit {result['rc']}, wanted the signal's {-result['signum']}\n{shown}"
        )
        assert not any(x.startswith("RESULT:") for x in result["lines"]), (
            f"{name}: a stopped run printed a RESULT line\n{shown}"
        )
        if not result["readable"]:
            # The reader had gone: the interrupted line was written to a dead pipe, so the
            # exit by the signal is the only thing this scenario can assert here.
            assert not _interrupted_lines(result), shown
            continue
        released = [
            int(m.group(1))
            for x in result["lines"]
            if (m := re.match(r"^\[(\d+)/6\] ", x))
        ]
        assert released == [n for n in (1, 2, 3) if n in released], (
            f"{name}: a block of work still in flight was released: {released}\n{shown}"
        )
        said = _interrupted_lines(result)
        name_of = signal.Signals(result["signum"]).name
        assert len(said) == 1 and said[0].startswith(
            f"ERROR  interrupted by {name_of}: "
        ), f"{name}: the interrupted line must print exactly once\n{shown}"
        # The early line names the signal and the copy, and precedes the final line.
        stops = [i for i, x in enumerate(result["lines"]) if x.startswith("STOP   ")]
        assert len(stops) == 1, (
            f"{name}: the STOP line must print exactly once\n{shown}"
        )
        assert result["lines"][stops[0]].startswith(
            f"STOP   {name_of} received, copy at "
        ), f"{name}: the STOP line is wrong\n{shown}"
        assert stops[0] < result["lines"].index(said[0]), (
            f"{name}: the STOP line must come before the final line\n{shown}"
        )


@pytest.mark.serial_only
def test_t19b_signals_leave_nothing_behind(signal_runs, tmp_path):
    for name, result in signal_runs.items():
        shown = "\n".join(result["lines"])
        assert result["marker"] == "", (
            f"{name}: a suite process outlived the run: {result['marker']}\n{shown}"
        )
        assert result["dirs"] == [], (
            f"{name}: the temp directory was left behind: {result['dirs']}\n{shown}"
        )
        if not result["readable"]:
            continue  # the line went to a pipe nobody reads: the two checks above stand
        said = _interrupted_lines(result)
        assert (
            said
            and f": {result['in_flight']} suite groups killed, copy removed"
            in (said[0])
        ), f"{name}: wanted {result['in_flight']} groups killed, copy removed\n{shown}"

    # The same cleanup after an exit that is NOT a signal: the main thread fails while a
    # sibling worker's suite is still running (block 1 is released only once mutant 2 is in
    # flight, and the injected failure is raised when it is released). That suite's group
    # must be killed and the copy removed, with no signal involved.
    marker = f"ksmark-{os.getpid()}-sibling-{secrets.token_hex(4)}"
    private = tmp_path / "sibling-tmp"
    private.mkdir()
    suite = (
        T19_SUITE.replace("@MARK@", marker)
        .replace("@LOG@", str(private / "slow.log"))
        .replace("@READY@", str(private / "ready"))
    )
    root = make_root(
        tmp_path,
        name="sibling",
        suite_text=suite,
        mutations=[
            ("waits for its sibling", "ok1\n", "no1\nwaitready\n"),
            ("slow sibling", "ok2\n", "ok2\nslow\n"),
        ],
    )
    tool = _injected(
        tmp_path,
        "sibling-tool",
        "for line in outcome.lines:",
        'if outcome.n == 1:\n    raise RuntimeError("kill-sets test: release failed")',
    )
    started = time.monotonic()
    try:
        run = run_tool(
            root, "--jobs", "2", tool=tool, env=dict(os.environ, TMPDIR=str(private))
        )
        leftover = _pgrep(marker)
    finally:
        subprocess.run(["pkill", "-9", "-f", marker], capture_output=True)
    elapsed = time.monotonic() - started
    assert run.has_line("ERROR  internal exception"), run
    assert any("kill-sets test: release failed" in x for x in run.lines), run
    assert run.status == "ERROR" and run.rc == 2, run
    assert not run.has_line("ERROR  worker thread(s)"), (
        f"a worker was still inside its suite when the pool closed\n{run!r}"
    )
    assert elapsed < 40, (
        f"the run took {elapsed:.0f}s: the sibling suite was not killed"
    )
    assert leftover == "", f"the sibling's suite outlived the run: {leftover}\n{run!r}"
    assert not list(private.glob("kill-sets.*")), run


@pytest.mark.serial_only
def test_t19c_signal_after_result(tmp_path):
    # The copy of the tool sleeps three seconds right after it prints the RESULT line. The
    # signal is sent once that line has been READ on the pipe, so it lands inside the sleep.
    line, indent = _anchor_line(
        "emit(result_line(state.counts, status, rc, state.jobs))"
    )
    tool = _tool_copy(
        tmp_path, "after-tool", None, edit=(line, line + f"{indent}time.sleep(3)\n")
    )
    root = make_root(tmp_path, name="after", mutations=KILL_THREE)
    private = tmp_path / "after-tmp"
    private.mkdir()
    lines: list = []
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            T19_SHIM,
            str(tool),
            "--campaign",
            str(root / "scripts" / "tests" / "mutate_fix.py"),
            "--root",
            str(root),
        ],
        cwd=str(tmp_path),
        env=dict(os.environ, TMPDIR=str(private)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="replace",
    )
    reader = _t19_read(proc, lines)
    try:
        _t19_wait_for(
            proc,
            lines,
            lambda: any(x.startswith("RESULT:") for x in lines),
            "the RESULT line was printed",
        )
        os.kill(proc.pid, signal.SIGTERM)
        rc = proc.wait(timeout=60)
        reader.join(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    run = Run(subprocess.CompletedProcess([], rc, "\n".join(lines) + "\n", ""))
    assert run.result is not None, run
    assert rc == run.result["rc"] >= 0, (
        f"exit {rc}: the verdict's own code was {run.result['rc']}\n{run!r}"
    )
    assert not any("interrupted by" in x for x in lines), (
        f"a signal after the RESULT line changed the outcome\n{run!r}"
    )
    assert not list(private.glob("kill-sets.*")), run


# A copy seam for tool copies. It wraps `shutil.copytree` so that, on a worker thread only:
# while the HOLD file exists the copy does not start (a copy the tool cannot cut short:
# nothing in it reads a stop flag); and, while the DELAY file exists, each directory visited
# waits STEP seconds before the tool's own `ignore` callback is asked (a copy the tool CAN cut
# short, only slowly). Either one writes the STARTED file first, so a row knows a worker is
# inside it. The main thread's copies (the master, the first working copy) are never slowed.
COPY_SEAM = """
import os as _kos, threading as _kth, time as _kti
_kreal_copytree = shutil.copytree


def _kcopytree(src, dst, symlinks=False, ignore=None, *a, **kw):
    if getattr(ignore, "_kwrapped", False):  # shutil's own recursion into a directory
        return _kreal_copytree(src, dst, symlinks, ignore, *a, **kw)
    worker = _kth.current_thread() is not _kth.main_thread()
    if worker and _kos.path.exists(@HOLD@):
        open(@STARTED@, "w").close()
        while _kos.path.exists(@HOLD@):
            _kti.sleep(0.05)

    def slowed(d, names):
        if worker and _kos.path.exists(@DELAY@):
            open(@STARTED@, "w").close()
            _kti.sleep(@STEP@)
        return ignore(d, names) if ignore else ()

    slowed._kwrapped = True
    return _kreal_copytree(src, dst, symlinks, slowed, *a, **kw)


shutil.copytree = _kcopytree
"""


def _seamed_tool(tmp_path, name, hold, started, delay, step) -> Path:
    seam = (
        COPY_SEAM.replace("@HOLD@", repr(str(hold)))
        .replace("@STARTED@", repr(str(started)))
        .replace("@DELAY@", repr(str(delay)))
        .replace("@STEP@", repr(step))
    )
    line, _indent = _anchor_line("import shutil")
    return _tool_copy(tmp_path, name, None, edit=(line, line + seam))


def _launch(tool, root, private, *flags):
    """The tool through the exec shim with a private TMPDIR; returns (proc, lines, reader)."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            T19_SHIM,
            str(tool),
            "--campaign",
            str(root / "scripts" / "tests" / "mutate_fix.py"),
            "--root",
            str(root),
            *flags,
        ],
        cwd=str(private.parent),
        env=dict(os.environ, TMPDIR=str(private)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="replace",
    )
    lines: list = []
    return proc, lines, _t19_read(proc, lines)


@pytest.mark.serial_only
def test_t19d_stop_line_before_a_hard_kill(tmp_path):
    # A worker is held inside a copy the tool cannot cut short, so the join takes its whole
    # bound; the gate's SIGKILL (emulated here) lands before the final line. What must
    # already be on the artifact is the STOP line naming the signal and the copy, and no
    # suite may be left running, since the groups are killed before that line prints.
    marker = f"ksmark-{os.getpid()}-hard-{secrets.token_hex(4)}"
    private = tmp_path / "hard-tmp"
    private.mkdir()
    log, ready, go = private / "slow.log", private / "ready", private / "go"
    hold, started = private / "hold", private / "started"
    suite = (
        T19_SUITE.replace("@MARK@", marker)
        .replace("@LOG@", str(log))
        .replace("@READY@", str(ready))
        .replace("@GO@", str(go))
    )
    root = make_root(
        tmp_path,
        name="hard",
        suite_text=suite,
        mutations=[
            ("slow one", "ok1\n", "ok1\nslow\n"),
            ("waits for go", "ok2\n", "no2\nwaitgo\n"),
            ("kill row3", "ok3\n", "no3\n"),
        ],
    )
    tool = _seamed_tool(tmp_path, "hard-tool", hold, started, private / "no-delay", 0.0)
    proc, lines, reader = _launch(tool, root, private, "--jobs", "2")
    try:
        # Mutant 1's suite is in flight; mutant 2's suite is released only after the hold
        # exists, so the copy for mutant 3 is the one that blocks on it.
        _t19_wait_for(
            proc, lines, lambda: _t19_log_count(log) >= 1, "a slow suite was in flight"
        )
        hold.write_text("x")
        go.write_text("x")
        _t19_wait_for(proc, lines, started.exists, "a worker was held inside a copy")
        os.kill(proc.pid, signal.SIGTERM)
        _t19_wait_for(
            proc,
            lines,
            lambda: any(x.startswith("STOP   ") for x in lines),
            "the STOP line was printed",
        )
        proc.kill()  # the gate's SIGKILL
        proc.wait(timeout=30)
        reader.join(timeout=30)
        shown = "\n".join(lines)
        stops = [x for x in lines if x.startswith("STOP   ")]
        m = re.fullmatch(r"STOP   SIGTERM received, copy at (\S+)", stops[0])
        assert len(stops) == 1 and m, f"the STOP line is wrong\n{shown}"
        assert Path(m.group(1)).is_dir(), f"the STOP line names no copy: {m.group(1)}"
        assert not _interrupted_lines({"lines": lines}), (
            f"the final line printed before the kill\n{shown}"
        )
        assert not any(x.startswith("RESULT:") for x in lines), shown
        deadline = time.monotonic() + 10
        while _pgrep(marker) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert _pgrep(marker) == "", f"a suite outlived the stop\n{shown}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        subprocess.run(["pkill", "-9", "-f", marker], capture_output=True)
        shutil.rmtree(private, ignore_errors=True)


@pytest.mark.serial_only
def test_t19e_a_copy_in_flight_does_not_delay_the_stop(tmp_path):
    # A worker is inside a copy of 150 directories at 0.4 s each (60 s in all, three times
    # the join bound). The stop must cut the copy short: the STOP line, then the final line
    # with the copy removed, and the exit comes in well under the bound (a coarse limit by
    # design: a copy that is not cut short makes the join alone take JOIN_SECONDS).
    private = tmp_path / "copies-tmp"
    private.mkdir()
    started, delay = private / "started", private / "delay"
    root = make_root(tmp_path, name="copies", mutations=KILL_THREE)
    for i in range(150):
        (root / "scripts" / f"d{i:03d}").mkdir()
    tool = _seamed_tool(
        tmp_path, "copies-tool", private / "no-hold", started, delay, 0.4
    )
    delay.write_text("x")
    proc, lines, reader = _launch(tool, root, private)
    try:
        _t19_wait_for(proc, lines, started.exists, "a worker was inside a copy")
        sent = time.monotonic()
        os.kill(proc.pid, signal.SIGTERM)
        rc = proc.wait(timeout=90)
        took = time.monotonic() - sent
        reader.join(timeout=30)
        shown = "\n".join(lines)
        assert rc == -signal.SIGTERM, f"exit {rc}\n{shown}"
        stops = [i for i, x in enumerate(lines) if x.startswith("STOP   SIGTERM ")]
        said = [i for i, x in enumerate(lines) if "interrupted by" in x]
        assert len(stops) == 1 and len(said) == 1 and stops[0] < said[0], (
            f"want one STOP line before one interrupted line\n{shown}"
        )
        assert lines[said[0]].endswith("0 suite groups killed, copy removed"), shown
        assert not any(x.startswith("RESULT:") for x in lines), shown
        assert not list(private.glob("kill-sets.*")), f"the copy was left\n{shown}"
        assert took < JOIN_SECONDS, (
            f"the stop took {took:.0f}s: a copy in flight held up the join\n{shown}"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        shutil.rmtree(private, ignore_errors=True)


ENV_HELPERS = {"_child_env", "_env_header", "_nested_pools_header"}


def test_t13b_env_reads_only_in_the_named_helpers():
    source = TOOL.read_text()
    tree = ast.parse(source)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ENV_HELPERS:
            allowed.update(range(node.lineno, node.end_lineno + 1))
    assert len(
        {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)} & ENV_HELPERS
    ) == len(ENV_HELPERS), "a named env helper is missing"

    # Every spelling of the os module: `import os`, `import os as x`.
    os_names = {"os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            os_names |= {a.asname or a.name for a in node.names if a.name == "os"}
    bad = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if line in allowed:
            continue
        if (
            isinstance(node, ast.Attribute)
            and node.attr in ("environ", "environb", "getenv")
            and isinstance(node.value, ast.Name)
            and node.value.id in os_names
        ):
            bad.append(f"line {line}: {node.value.id}.{node.attr}")
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            names = {a.name for a in node.names}
            if names & {"environ", "environb", "getenv", "*"}:
                bad.append(f"line {line}: from os import {sorted(names)}")
    # The text, too: an alias the AST walk does not model still spells the word.
    for n, text in enumerate(source.splitlines(), 1):
        if n not in allowed and re.search(r"environ|getenv", text):
            bad.append(f"line {n}: {text.strip()}")
    assert not bad, f"environment read outside {sorted(ENV_HELPERS)}: {bad}"
