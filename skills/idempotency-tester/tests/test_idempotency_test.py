"""Contract tests for idempotency-test.sh — drives the real CLI via subprocess.

Asserts on exit code/verdict only (never diff prose: BSD/GNU text differs).
Run: python3 -m pytest skills/idempotency-tester/tests/ -q
"""
import os
import subprocess
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
SKILL = HERE.parents[1]
REPO = HERE.parents[3]
HELPER = SKILL / "idempotency-test.sh"
SYNC = REPO / "skills" / "sync-docs" / "sync_docs.py"
FIXTURES = REPO / "skills" / "sync-docs" / "tests" / "fixtures"


def run_it(*opts, target, targs=(), timeout=60):
  cmd = ["bash", str(HELPER), *opts, "--", str(target), *map(str, targs)]
  return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def script(tmp_path, body, name="t.sh"):
  p = tmp_path / name
  p.write_text("#!/usr/bin/env bash\n" + body)
  p.chmod(0o755)
  return p


# ---------- core verdicts ----------

def test_idempotent_fixed_write(tmp_path):
  t = script(tmp_path, "printf hello > out.txt\n")
  assert run_it(target=t).returncode == 0


def test_nonidempotent_append(tmp_path):
  t = script(tmp_path, "printf x >> log.txt\n")
  assert run_it(target=t).returncode == 1


def test_idempotent_mkdir_and_write(tmp_path):
  t = script(tmp_path, "mkdir -p d; printf k > d/f\n")
  assert run_it(target=t).returncode == 0


# ---------- real target: supersedes sync-docs' own test_idempotence_across_fixtures ----------

@pytest.mark.parametrize("fixture", ["dotclaude-shaped", "dirty-edit", "no-markers"])
def test_sync_docs_idempotent_across_fixtures(fixture):
  r = run_it("--seed", str(FIXTURES / fixture), target=SYNC, targs=["--scope", "."])
  assert r.returncode == 0, r.stderr


# ---------- exit-code gates ----------

def test_broken_target_aborts_2(tmp_path):
  t = script(tmp_path, "exit 3\n")
  assert run_it(target=t).returncode == 2


def test_broken_with_allow_nonzero_is_idempotent(tmp_path):
  t = script(tmp_path, "exit 3\n")
  assert run_it("--allow-nonzero", target=t).returncode == 0


def test_exit_parity_state_neutral(tmp_path):
  # tmp/ is unmanifested, so state is identical; only exit parity (0 then 1) trips it.
  t = script(tmp_path, 'f="$TMPDIR/flag"; if [ -e "$f" ]; then exit 1; fi; touch "$f"\n')
  assert run_it(target=t).returncode == 1


# ---------- symlinks (no-dereference invariant) ----------

def test_symlink_create_idempotent(tmp_path):
  t = script(tmp_path, "ln -sfn /etc/hosts link\n")
  assert run_it(target=t).returncode == 0


def test_symlink_repoint_nonidempotent(tmp_path):
  t = script(tmp_path, 'if [ -e link ]; then ln -sfn /tmp/o link; else ln -sfn /etc/hosts link; fi\n')
  assert run_it(target=t).returncode == 1


def test_find_poisoning_does_not_traverse_symlink(tmp_path):
  # Seed a dir-symlink into a big out-of-sandbox tree; a no-op target must stay fast + idempotent
  # without manifesting the link's resolved contents.
  seed = tmp_path / "seed"
  seed.mkdir()
  (seed / "huge").symlink_to("/usr")
  t = script(tmp_path, "true\n")
  assert run_it("--seed", str(seed), target=t, timeout=20).returncode == 0


# ---------- setup ----------

def test_setup_failure_aborts_2(tmp_path):
  t = script(tmp_path, "printf hi > out.txt\n")
  assert run_it("--setup", "exit 5", target=t).returncode == 2


def test_setup_output_is_baseline(tmp_path):
  # setup writes a file (once); an idempotent target on top is still idempotent.
  t = script(tmp_path, "printf hello > out.txt\n")
  assert run_it("--setup", "printf seeded > pre.txt", target=t).returncode == 0


# ---------- stdin (no hang) ----------

def test_prompting_target_does_not_hang(tmp_path):
  t = script(tmp_path, "read -r x || true\n")  # would hang on inherited tty; stdin=/dev/null
  assert run_it(target=t, timeout=15).returncode == 0


def test_stdin_replayed(tmp_path):
  stdin = tmp_path / "in.txt"
  stdin.write_text("payload\n")
  t = script(tmp_path, "read -r x; printf '%s' \"$x\" > got.txt\n")
  assert run_it("--stdin", str(stdin), target=t).returncode == 0


# ---------- git ----------

def test_git_double_commit_nonidempotent(tmp_path):
  t = script(tmp_path, "git -c user.name=t -c user.email=t@t -c commit.gpgsign=false "
                       "commit --allow-empty -q -m x\n")
  assert run_it("--git", target=t).returncode == 1


# ---------- ignore ----------

def test_ignore_masks_a_log(tmp_path):
  t = script(tmp_path, "printf x >> app.log\n")
  assert run_it("--ignore", "*.log", target=t).returncode == 0


# ---------- containment ----------

def test_containment_real_home_untouched(tmp_path):
  token = f"it_canary_{uuid.uuid4().hex}"
  t = script(tmp_path, f'printf c > "$HOME/{token}"; printf c > "$XDG_CONFIG_HOME/{token}"\n')
  try:
    assert run_it(target=t).returncode == 0          # same writes both runs → idempotent
    assert not (Path(os.path.expanduser("~")) / token).exists()  # redirected, not real HOME
  finally:
    leaked = Path(os.path.expanduser("~")) / token
    if leaked.exists():
      leaked.unlink()


# ---------- usage ----------

def test_no_target_is_error(tmp_path):
  assert subprocess.run(["bash", str(HELPER), "--"], capture_output=True).returncode == 2
