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
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "scripts" / "kill-sets.py"
MUTATE = REPO / "scripts" / "lib" / "mutate.py"

EXPECT = (
    r"^RESULT: (PASS|FAIL|ERROR) rc=[0-9]+ mutants=[0-9]+ "
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
# turns it into a skip under exit 0. Run-level directives follow the loop.
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


T13_SUITE = r"""#!/usr/bin/env bash
here="$(cd "$(dirname "$0")/.." && pwd)"
subj="$here/subject.sh"
has() { grep -qx -- "$1" "$subj"; }
printf '%s\n' "${PYTHONDONTWRITEBYTECODE-unset}" >> "@SEEN@"
fails=0
if [ -e "$here/marker" ]; then echo "FAIL  no marker"; fails=1; else echo "PASS  no marker"; fi
if has writemark; then : > "$here/marker"; fi
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

    # The same leak, then an internal exception inside the mutant loop: the leak check runs
    # on that ERROR path too, and does not mask the exception.
    live_subject = tmp_path / "leakexc" / "scripts" / "subject.sh"
    root = t13_root(
        "leakexc",
        [("leaks, then the tool raises", "ok3\n", "no3\nleakme\n")],
        live_subject,
    )
    raising = _tool_copy(
        tmp_path,
        "raising",
        None,
        edit=(
            "            self._resolve(state, head, n, label, old, run)\n",
            '            raise RuntimeError("kill-sets test: injected")\n',
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


ENV_HELPERS = {"_child_env", "_env_header"}


def test_t13b_env_reads_only_in_two_helpers():
    source = TOOL.read_text()
    tree = ast.parse(source)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ENV_HELPERS:
            allowed.update(range(node.lineno, node.end_lineno + 1))
    assert (
        len(
            {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
            & ENV_HELPERS
        )
        == 2
    ), "a named env helper is missing"

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
