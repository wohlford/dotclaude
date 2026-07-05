"""Shared helpers for /recast tests — `run`, `git`, and the helper-script paths.

Lives in a uniquely-named module (not `conftest`) so a combined `pytest` run across skills —
each with its own `conftest.py` — does not collide on the `conftest` module name. Fixtures stay
in conftest.py; everything imported with `from … import` lives here.
"""

import subprocess
from pathlib import Path

HERE = Path(__file__).resolve()
SKILL = HERE.parents[1]  # skills/recast
REPO = HERE.parents[3]  # repo root
STATE_SH = SKILL / "recast-state.sh"
RECON_SH = SKILL / "recast-recon.sh"
HISTORY_SH = SKILL / "recast-recon-history.sh"
CONTRACT_SH = SKILL / "recast-contract.sh"
VERIFY_SH = SKILL / "recast-verify.sh"

_GIT_CFG = [
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
    "-c",
    "user.name=recast-test",
    "-c",
    "user.email=recast@example.invalid",
    "-c",
    "init.defaultBranch=main",
]


def run(script, *args, timeout=30):
    """Run a helper script via bash; capture both streams in text mode."""
    cmd = ["bash", str(script), *map(str, args)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def git(repo, *args, check=True, env=None):
    """Run git in `repo` with signing off and a fixed identity; optional env override."""
    cmd = ["git", "-C", str(repo), *_GIT_CFG, *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check, env=env)
