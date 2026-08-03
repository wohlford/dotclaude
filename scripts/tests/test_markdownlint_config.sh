#!/usr/bin/env bash
# shellcheck disable=SC2012  # ls|sort -V mirrors the lint hook's own NVM-dir detection
set -uo pipefail

# Script: test_markdownlint_config.sh
# Purpose: Regression tests for THIS REPO'S .markdownlint-cli2.jsonc — that MD022
#          (blanks-around-headings) catches a heading with no blank line ABOVE it,
#          while leaving the house style (a list run directly BELOW a heading) alone.
# Usage:   bash scripts/tests/test_markdownlint_config.sh
#
# Subject is the CONFIG, not the hook — scripts/tests/test_markdownlint_check.sh owns
# markdownlint-check.sh. The two are separate because the failure this suite exists for
# is a rule being switched off repo-wide, which every hook test passes straight through:
# the hook was working perfectly, faithfully applying a config that had stopped checking.
#
# Why this suite exists: `blanks-around-headings` was disabled REPO-WIDE to accommodate
# the generated CHANGELOG.md's format (`## vX.Y.Z` with its bullet directly beneath).
# A `####` heading then shipped with no blank line above it — /audit reported PASS
# correctly, since the rule was off — and it reached production before being caught by
# eye. The rule is split by SIDE instead: the below-side hits are all house style, the
# above-side ones are defects, so only the above side is enforced.
#
# The rows below are what stop that from silently regressing in either direction — a
# revert to `false` fails the above-side rows, and a blanket `true` fails the below-side
# ones. Both edits look equally innocuous in a diff; neither survives this suite.
#
# The config is COPIED from the repo rather than restated here, so a future edit to it
# is judged by these assertions instead of silently diverging from a hand-made twin.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
config="$repo/.markdownlint-cli2.jsonc"

# PHYSICAL path: on macOS $TMPDIR is reached through a symlink (/tmp -> /private/tmp),
# and a logical cwd that does not physically contain the linted file sends
# markdownlint-cli2 down a different config-resolution path.
sandbox="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$sandbox"' EXIT

pass=0
fail=0

note_pass() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
note_fail() { printf 'FAIL  %s — %s\n' "$1" "$2"; fail=$((fail + 1)); }

if [[ ! -r "$config" ]]; then
  printf 'FAIL  the repo config is unreadable: %s\n' "$config"
  exit 1
fi

# Locate markdownlint-cli2 the same way the hook does.
if ! command -v markdownlint-cli2 > /dev/null 2>&1; then
  nvm_bin=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2> /dev/null | sort -V | tail -1 || true)
  if [[ -n "$nvm_bin" && -x "$nvm_bin/markdownlint-cli2" ]]; then
    PATH="$nvm_bin:$PATH"
    export PATH
  else
    printf 'SKIP  markdownlint-cli2 not installed — config cases cannot run\n'
    printf '\n%d passed, %d failed\n' "$pass" "$fail"
    exit 0
  fi
fi

# ---------- Fixtures, under a copy of the repo's REAL config ----------
cp "$config" "$sandbox/.markdownlint-cli2.jsonc"
mkdir -p "$sandbox/sub"

# Every fixture below steps its heading levels one at a time. Skipping a level (`#` to
# `###`) trips MD001, which has nothing to do with the side under test — and a fixture
# that is dirty for an unrelated reason is how you end up reading someone else's verdict
# as your own. Measured while probing the live hook: an `#` -> `###` fixture came back
# blocked, and the block was MD001.

# The defect that shipped: a heading with no blank line ABOVE it.
printf '# Title\n\nSome prose.\n## Squashed heading\n\nMore prose.\n' \
  > "$sandbox/above.md"

# House style, and the reason the rule was switched off: a list run directly under its
# heading. `blanks-around-lists` already exempts this shape; MD022 must agree.
printf '# Title\n\n## Arguments\n- --foo, which does a thing\n' \
  > "$sandbox/below.md"

# Correctly spaced — the over-blocking guard.
printf '# Title\n\n## Section\n\nText.\n' > "$sandbox/clean.md"

# Exactly what /commit generates. It passes on the RULE's terms, with no file-specific
# carve-out anywhere in the config — which is the point of splitting by side.
changelog=$'# Changelog\n\n## v1.2.0 — 2026-07-31\n- feat(x): add a thing\n\n## v1.1.0 — 2026-07-30\n- fix(y): fix a thing\n'
printf '%s' "$changelog" > "$sandbox/CHANGELOG.md"

# The SAME content under a different name. If this ever diverges from the row above,
# something has grown a name- or path-based exemption that the split makes unnecessary.
printf '%s' "$changelog" > "$sandbox/sub/notes.md"

# lint <relative-path> -> stdout+stderr, from the config's own directory
lint() { (cd "$sandbox" && markdownlint-cli2 "$1" 2>&1); }

# The finding token, not the bare rule id: the banner echoes the glob list, so a bare
# "MD022" could in principle match a path rather than a verdict.
finding='MD022/blanks-around-headings'

# expect_flagged <relative-path> <label>
expect_flagged() {
  local rel="$1" label="$2" out
  out="$(lint "$rel")"
  if ! printf '%s' "$out" | grep -qE '^Linting: [1-9]'; then
    note_fail "$label" "nothing was linted — the assertion never reached a file"
    return
  fi
  if printf '%s' "$out" | grep -qF "$finding"; then
    note_pass "$label"
  else
    note_fail "$label" "expected $finding, got none"
  fi
}

# expect_unflagged <relative-path> <label>
expect_unflagged() {
  local rel="$1" label="$2" out
  out="$(lint "$rel")"
  if ! printf '%s' "$out" | grep -qE '^Linting: [1-9]'; then
    note_fail "$label" "nothing was linted — a skipped file is not a clean file"
    return
  fi
  if printf '%s' "$out" | grep -qF "$finding"; then
    note_fail "$label" "unexpected $finding"
  else
    note_pass "$label"
  fi
}

# ---------- The ABOVE side is enforced: the class that shipped a defect ----------
expect_flagged above.md 'a heading with no blank line above is flagged'

# ---------- The BELOW side is not: it is house style, not a defect ----------
expect_unflagged below.md 'a list run directly under its heading is left alone'
expect_unflagged CHANGELOG.md "/commit's changelog format is left alone"
expect_unflagged sub/notes.md 'that pass comes from the rule, not a name-based carve-out'

# ---------- Neither side over-blocks ----------
expect_unflagged clean.md 'a correctly spaced heading is left alone'

# ---------- Fixture hygiene: each row must fail (or pass) for ITS OWN reason ----------
# Without this, a fixture that quietly starts tripping some unrelated rule turns
# `expect_flagged` into a coin flip that lands right, and `expect_unflagged` into a
# claim about a file nobody is looking at any more.
for f in below.md clean.md CHANGELOG.md sub/notes.md; do
  out="$(lint "$f")"
  if printf '%s' "$out" | grep -qE '^Summary: 0 error'; then
    note_pass "$f is clean for every rule, not just the one under test"
  else
    note_fail "$f is clean for every rule, not just the one under test" \
      "$(printf '%s' "$out" | grep -oE 'MD[0-9]+' | sort -u | tr '\n' ' ')"
  fi
done

out="$(lint above.md)"
if [[ "$(printf '%s' "$out" | grep -oE 'MD[0-9]+' | sort -u | wc -l | tr -d ' ')" == "1" ]]; then
  note_pass 'above.md trips MD022 and nothing else'
else
  note_fail 'above.md trips MD022 and nothing else' \
    "$(printf '%s' "$out" | grep -oE 'MD[0-9]+' | sort -u | tr '\n' ' ')"
fi

# ---------- Splitting the rule must not have loosened anything else ----------
# The whole change is one rule's below-side. Assert an unrelated heading rule still
# fires on the same files, so a future edit cannot quietly widen this into a general
# "headings are not checked here".
printf '%s\n#Bad heading\n' "$changelog" > "$sandbox/CHANGELOG.md"
out="$(lint CHANGELOG.md)"
if printf '%s' "$out" | grep -qE '^Linting: [1-9]' \
  && printf '%s' "$out" | grep -qF 'MD018'; then
  note_pass 'other heading rules still fire'
else
  note_fail 'other heading rules still fire' 'an unrelated heading defect went unreported'
fi
printf '%s' "$changelog" > "$sandbox/CHANGELOG.md"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
