"""Tests for scripts/script-header-check.py — the sync-docs header-window gate.

The defect it exists for: `BashHeaderExtractor` (skills/sync-docs/extractors.py) reads only the
first `HEADER_WINDOW_LINES` lines of a script and binds `description` to the LAST `# Purpose:`
match in that window, taking only that match's own physical line. Two scripts shipped a
continuation on the NEXT physical line and the rendered `scripts/README.md` row silently
truncated — `propagate-postcheck.sh`'s row dropped two of its three documented jobs. This suite
is deliberately adversarial about false positives: several fixtures below are shapes that a naive
"does the next line start with `#`?" predicate would wrongly flag, and each must stay green.

Every fixture scope carries its own copy of the real `skills/sync-docs` modules, so the checker
under test always runs against the SAME extractor logic it exists to protect — never a
hand-simplified stand-in that could quietly drift from it.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "scripts" / "script-header-check.py"
SYNC_DOCS_SRC = REPO_ROOT / "skills" / "sync-docs"
SYNC_DOCS_FILES = (
    "extractors.py",
    "formatters.py",
    "handlers.py",
    "markers.py",
    "sync_docs.py",
)


def copy_sync_docs(root: Path) -> None:
    """Copy the real sync-docs modules into a fixture root, so the checker under test always
    runs against the SAME extractor logic it exists to protect."""
    dest = root / "skills" / "sync-docs"
    dest.mkdir(parents=True)
    for name in SYNC_DOCS_FILES:
        (dest / name).write_text((SYNC_DOCS_SRC / name).read_text(encoding="utf-8"))


def write_script(root: Path, relpath: str, body: str) -> Path:
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


@pytest.fixture
def bare_scope(tmp_path):
    # pwd -P equivalent: on macOS $TMPDIR is reached through a symlink, and a logical path that
    # does not physically contain the file can send path resolution down a different branch.
    root = Path(tmp_path).resolve()
    copy_sync_docs(root)
    return root


@pytest.fixture
def scope(bare_scope):
    (bare_scope / "scripts").mkdir()
    return bare_scope


def run_checker(scope_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--scope", str(scope_dir), *extra],
        capture_output=True,
        text=True,
    )


def offender_line(stdout: str, relpath: str) -> str:
    """The single offender line for `relpath`, asserting there is exactly one."""
    lines = [ln for ln in stdout.splitlines() if ln.startswith(relpath + ":")]
    assert len(lines) == 1, (
        f"expected exactly one offender line for {relpath}, got {lines!r}"
    )
    return lines[0]


CLEAN_SCRIPT = (
    "#!/usr/bin/env bash\n"
    "# Script: clean.sh\n"
    "# Purpose: A perfectly fine, self-contained one-line purpose for this script\n"
    "# Usage: clean.sh\n"
    "echo hi\n"
)


# ---------- the three violation reasons, each pinned in isolation ----------


def test_missing_no_purpose_line_anywhere(scope):
    write_script(scope, "scripts/nohead.sh", "#!/usr/bin/env bash\necho hi\n")
    result = run_checker(scope)
    assert result.returncode == 1, result.stdout + result.stderr
    line = offender_line(result.stdout, "scripts/nohead.sh")
    assert "missing" in line
    assert "out-of-window" not in line
    assert "wrapped" not in line


def test_out_of_window_purpose_exists_but_past_line_ten(scope):
    lines = ["#!/usr/bin/env bash"] + [f"# filler {i}" for i in range(11)]
    lines.append("# Purpose: arrives too late for the extractor's ten-line window")
    write_script(scope, "scripts/late.sh", "\n".join(lines) + "\n")
    result = run_checker(scope)
    assert result.returncode == 1, result.stdout + result.stderr
    line = offender_line(result.stdout, "scripts/late.sh")
    assert "out-of-window" in line
    assert "missing" not in line
    assert "wrapped" not in line


def test_wrapped_continuation_on_the_next_physical_line(scope):
    content = (
        "#!/usr/bin/env bash\n"
        "# Script: wrapped.sh\n"
        "# Purpose: this line is not self-contained\n"
        "#   it continues right here in lowercase prose\n"
        "echo hi\n"
    )
    write_script(scope, "scripts/wrapped.sh", content)
    result = run_checker(scope)
    assert result.returncode == 1, result.stdout + result.stderr
    line = offender_line(result.stdout, "scripts/wrapped.sh")
    assert "wrapped" in line
    assert "missing" not in line
    assert "out-of-window" not in line


# ---------- cleared shapes: the review's constructed false positives, clauses 1-6 ----------


def test_cleared_purpose_at_eof_is_not_an_index_error(scope):
    content = "#!/usr/bin/env bash\n# Script: eof.sh\n# Purpose: the last line of the file, period"
    write_script(scope, "scripts/eof.sh", content)  # note: no trailing newline
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/eof.sh:" not in result.stdout


def test_cleared_next_line_is_not_a_comment(scope):
    content = (
        "#!/usr/bin/env bash\n"
        "# Script: code.sh\n"
        "# Purpose: a perfectly self-contained purpose line\n"
        "echo not a comment, so no continuation\n"
    )
    write_script(scope, "scripts/code.sh", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/code.sh:" not in result.stdout


def test_cleared_bare_hash_separator(scope):
    content = (
        "#!/usr/bin/env bash\n"
        "# Script: sep.sh\n"
        "# Purpose: a self-contained purpose followed by a deliberate separator\n"
        "#\n"
        "# More elaboration lives here but does not truncate the bound line.\n"
    )
    write_script(scope, "scripts/sep.sh", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/sep.sh:" not in result.stdout


def test_cleared_key_header_usage(scope):
    content = (
        "#!/usr/bin/env bash\n"
        "# Script: usage.sh\n"
        "# Purpose: a self-contained purpose immediately followed by a Usage header\n"
        "# Usage: usage.sh [--flag]\n"
    )
    write_script(scope, "scripts/usage.sh", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/usage.sh:" not in result.stdout


def test_cleared_lowercase_key_header_noqa(scope):
    """`# Usage:` is capitalised, so clause 6 (not-lowercase) already clears it and clause 4
    never gets to decide that row. This fixture uses a LOWERCASE `Key:` continuation so clause 4
    is the ONLY clause that can clear it — pinning it as genuinely load-bearing, not merely
    redundant with clause 6."""
    content = (
        "#!/usr/bin/env python3\n"
        "# Script: noqa.py\n"
        "# Purpose: a self-contained purpose immediately followed by a noqa directive\n"
        "# noqa: E501\n"
    )
    write_script(scope, "scripts/noqa.py", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/noqa.py:" not in result.stdout


def test_cleared_lowercase_key_header_type_ignore(scope):
    """Same rationale as test_cleared_lowercase_key_header_noqa: a lowercase `Key:` continuation
    that only clause 4 can clear."""
    content = (
        "#!/usr/bin/env python3\n"
        "# Script: typeignore.py\n"
        "# Purpose: a self-contained purpose immediately followed by a type directive\n"
        "# type: ignore\n"
    )
    write_script(scope, "scripts/typeignore.py", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/typeignore.py:" not in result.stdout


def test_cleared_shellcheck_directive_no_colon(scope):
    content = (
        "#!/usr/bin/env bash\n"
        "# Script: sc.sh\n"
        "# Purpose: a self-contained purpose right before a shellcheck directive\n"
        "# shellcheck disable=SC2034\n"
    )
    write_script(scope, "scripts/sc.sh", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/sc.sh:" not in result.stdout


def test_cleared_coding_cookie(scope):
    content = (
        "#!/usr/bin/env bash\n"
        "# Script: coding.sh\n"
        "# Purpose: a self-contained purpose right before a coding cookie\n"
        "# -*- coding: utf-8 -*-\n"
    )
    write_script(scope, "scripts/coding.sh", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/coding.sh:" not in result.stdout


def test_cleared_divider_line(scope):
    content = (
        "#!/usr/bin/env bash\n"
        "# Script: div.sh\n"
        "# Purpose: a self-contained purpose right before a divider\n"
        "# ------------------------------\n"
    )
    write_script(scope, "scripts/div.sh", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/div.sh:" not in result.stdout


def test_cleared_capitalised_continuation(scope):
    content = (
        "#!/usr/bin/env bash\n"
        "# Script: cap.sh\n"
        "# Purpose: a self-contained purpose right before a new sentence\n"
        "# Capitalised sentences read as unrelated prose, not a continuation.\n"
    )
    write_script(scope, "scripts/cap.sh", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/cap.sh:" not in result.stdout


# ---------- the py-docstring trap, and the whole-file-scan trap ----------


def test_py_docstring_but_no_purpose_header_is_missing(scope):
    content = (
        "#!/usr/bin/env python3\n"
        '"""A perfectly nice module docstring.\n'
        "\n"
        "With its own description paragraph, which py-docstring will happily fill into the\n"
        "rendered index cell even though no # Purpose: header was ever written.\n"
        '"""\n'
        "print('hi')\n"
    )
    write_script(scope, "scripts/nohead.py", content)
    result = run_checker(scope)
    assert result.returncode == 1, result.stdout + result.stderr
    line = offender_line(result.stdout, "scripts/nohead.py")
    assert "missing" in line


def test_heredoc_purpose_lookalike_does_not_flag_a_compliant_file(scope):
    """The whole-file scan may only choose WORDING (missing vs out-of-window), never create a
    violation. A valid in-window header stays clean no matter what a heredoc or string literal
    contains later in the file.

    The lookalike line must sit PAST the window (physical line > 10), or this fixture is
    vacuous: `_find_last_match_index` binds whichever `# Purpose:` line is LAST within
    `lines[:window]`, so a lookalike still inside the window would simply become the bound
    line (and, here, would itself read clean because the very next line is `EOF`, satisfying
    clause 2's "next line isn't even a comment"). Only a lookalike outside the window can
    exercise the whole-file scan's "wording only, never a violation" property at all."""
    content = (
        "#!/usr/bin/env bash\n"
        "# Script: heredoc.sh\n"
        "# Purpose: a perfectly fine one-line purpose for this script\n"
        "# Usage: heredoc.sh\n"
        + "".join(
            f"# filler {i}\n" for i in range(6)
        )  # pushes the lookalike past line 10
        + "cat <<'EOF'\n"
        "# Purpose: this looks like a header but lives inside a heredoc body\n"
        "EOF\n"
    )
    write_script(scope, "scripts/heredoc.sh", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/heredoc.sh:" not in result.stdout


# ---------- the last-in-window-match rule ----------


def test_last_in_window_match_wins_over_a_wrapped_first_match(scope):
    """Two in-window # Purpose: lines where only the FIRST wraps must read clean, because the
    extractor binds the LAST match — exactly like BashHeaderExtractor's overwrite-on-each-match
    loop."""
    content = (
        "#!/usr/bin/env bash\n"
        "# Script: dual.sh\n"
        "# Purpose: an abandoned first attempt that would wrap\n"
        "#   this continuation would flag wrapped if it were the bound line\n"
        "# Purpose: the second and final purpose, fully self-contained\n"
        "# Usage: dual.sh\n"
    )
    write_script(scope, "scripts/dual.sh", content)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts/dual.sh:" not in result.stdout


# ---------- exit codes and the zero-denominator guard ----------


def test_clean_scope_exits_zero_with_nonzero_checked(scope):
    write_script(scope, "scripts/clean.sh", CLEAN_SCRIPT)
    result = run_checker(scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checked=1 violations=0" in result.stdout


def test_empty_population_exits_two_not_zero(bare_scope):
    # No scripts/ directory at all: discover() returns [] rather than raising, and an empty
    # population must never read as a clean pass.
    result = run_checker(bare_scope)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "instrument failure" in result.stderr


# ---------- an unreadable population member is an instrument failure, not a finding ----------
#
# discover() itself tolerates each of these (extract_chain swallows OSError per extractor), so
# they reach check_file() as genuine population members — the crash, if any, is ours.


def test_unreadable_member_directory_named_like_a_script(scope):
    # `glob("scripts/*.py")` matches a DIRECTORY named like a script just as readily as a
    # file; `Path.read_text()` on it raises IsADirectoryError.
    (scope / "scripts" / "dir.py").mkdir()
    result = run_checker(scope)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "scripts/dir.py" in result.stderr
    assert "instrument failure" in result.stderr
    # Never reads as a clean pass, and never reported as a plain violation either.
    assert "checked=" not in result.stdout
    assert "dir.py:" not in result.stdout


def test_unreadable_member_dangling_symlink(scope):
    # A symlink whose target does not exist: read_text() raises FileNotFoundError, distinct
    # from the ordinary "file does not exist" case since the glob DID find a directory entry.
    (scope / "scripts" / "dangling.sh").symlink_to(
        scope / "scripts" / "nonexistent-target"
    )
    result = run_checker(scope)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "scripts/dangling.sh" in result.stderr
    assert "instrument failure" in result.stderr


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores permission bits — the read would succeed"
)
def test_unreadable_member_permission_denied(scope):
    path = write_script(scope, "scripts/noperm.sh", CLEAN_SCRIPT)
    path.chmod(0o000)
    try:
        result = run_checker(scope)
    finally:
        path.chmod(0o644)  # restore so tmp_path cleanup can remove it
    assert result.returncode == 2, result.stdout + result.stderr
    assert "scripts/noperm.sh" in result.stderr
    assert "instrument failure" in result.stderr


# ---------- moved parameter: extractors.HEADER_WINDOW_LINES, read as a module attribute ----------


def test_checker_tracks_extractors_header_window_lines_module_attribute(
    bare_scope, monkeypatch
):
    """A `from extractors import HEADER_WINDOW_LINES` binding would snapshot the value at import
    time and never move under this patch. The checker must read `extractors.HEADER_WINDOW_LINES`
    as a module attribute so a patched window actually changes its behavior."""
    (bare_scope / "scripts").mkdir()
    lines = ["#!/usr/bin/env bash"] + [f"# filler {i}" for i in range(10)]
    lines.append(
        "# Purpose: sits at physical line twelve, past the default window of ten"
    )
    write_script(bare_scope, "scripts/moved.sh", "\n".join(lines) + "\n")

    sys.path.insert(0, str(bare_scope / "skills" / "sync-docs"))
    import extractors  # noqa: E402  (fresh copy resolved from bare_scope via sys.path)

    spec = importlib.util.spec_from_file_location("script_header_check_inproc", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    monkeypatch.setattr(extractors, "HEADER_WINDOW_LINES", 20)
    rc = mod.main(["--scope", str(bare_scope)])
    assert rc == 0, "line 12 must be clean once the window is patched to 20"

    monkeypatch.setattr(extractors, "HEADER_WINDOW_LINES", 10)
    rc = mod.main(["--scope", str(bare_scope)])
    assert rc == 1, (
        "line 12 must read out-of-window again once the window is patched back to 10"
    )


# ---------- config override of the scripts source glob ----------


def test_config_override_of_scripts_source_glob(bare_scope):
    write_script(bare_scope, "elsewhere/moved.sh", CLEAN_SCRIPT)
    (bare_scope / ".claude").mkdir()
    (bare_scope / ".claude" / "sync-docs.yaml").write_text(
        "handlers:\n  scripts:\n    source: elsewhere/*.sh\n"
    )
    result = run_checker(bare_scope)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checked=1 violations=0" in result.stdout


# ---------- the real repo: RED was observed before T1b landed; this pins the GREEN end state ----------


def test_real_repo_has_zero_violations():
    result = run_checker(REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "violations=0" in result.stdout
    assert "install-git-hooks.sh" not in result.stdout
    assert "propagate-postcheck.sh" not in result.stdout
