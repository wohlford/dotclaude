#!/usr/bin/env bash
set -uo pipefail

# Script: hermetic_gate.sh
# Purpose: Sourced library — keep a test suite off the operator's real heavy-gate state
# Usage: . "$(dirname "$0")/../lib/hermetic_gate.sh"; hermetic_gate_env "$tmp" from a suite's preamble
#
# hermetic_gate_env DIR exports GATE_DIR="DIR/gate" (created), GATE_WIRING=off,
# GATE_FAKE_THROTTLE=0 and GATE_POLL=1, and unsets HEAVY_GATE_HELD so a suite run from inside a
# held turn neither joins that turn nor queues behind the real gate. It prints nothing and returns
# 2, with one stderr line, when DIR is empty or not a directory. Executing this file only defines
# the function.

hermetic_gate_env() {
  local dir="${1-}"
  if [[ -z "$dir" || ! -d "$dir" ]]; then
    printf 'hermetic_gate_env: need an existing directory, got [%s]\n' "$dir" >&2
    return 2
  fi
  mkdir -p "$dir/gate" || return 2
  export GATE_DIR="$dir/gate"
  export GATE_WIRING=off
  export GATE_FAKE_THROTTLE=0
  export GATE_POLL=1
  unset HEAVY_GATE_HELD
}
