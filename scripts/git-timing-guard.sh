#!/usr/bin/env bash
set -euo pipefail

# Script: git-timing-guard.sh
# Purpose: TRANSITIONAL shim — exec's the real hook, scripts/git-timing-guard.py
# Usage: Called by Claude Code hooks with JSON on stdin
#
# THIS FILE IS NOT THE GATE ANYMORE. The logic lives in scripts/git-timing-guard.py; this file
# exists ONLY because ~/.claude/settings.json is a live snapshot loaded once per session, and
# every session that started before this change was promoted still has THIS PATH registered.
# Renaming the registration outright would leave those sessions invoking a path that no longer
# resolves the moment the rename lands — a bash exec/exit-127 fail-open, silently, for the
# duration of every one of those sessions.
#
# `exec` replaces this process with the `.py` one, so fd 0 (stdin) and argv both pass through
# completely untouched — this is why `"$@"` below is load-bearing: dropping it would make the
# invocation contract test (scripts/tests/test_hook_argv_refusal.py) exercise the ARGV-REFUSAL
# path against zero arguments, always, regardless of what this shim itself was called with.
#
# RETIRABLE, in a later change, once no running session still holds a registration pointing
# here. Not now: doing so today would silently disable the gate in every currently-open session.
# A SECOND retirement site, easy to miss: scripts/tests/test_git_timing_guard.sh's own
# `guard="$here/../git-timing-guard.sh"` invokes THIS file by bare path (the only thing that
# exercises `"$@"` above, not just the .py's own decisions), and its `check_diff` row asserts
# this shim's rc against a direct `python3 git-timing-guard.py` invocation's rc. Flipping that
# suite's subject to the `.py` directly, once this shim retires, would silently stop testing the
# shim itself before it is gone -- retire that row (or repoint `check_diff` at nothing) in the
# SAME change that deletes this file, not before.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$here/git-timing-guard.py" "$@"
