#!/usr/bin/env bash
# shellcheck disable=SC2016  # fixtures contain literal backticks in single quotes on purpose
set -uo pipefail

# Script: test_md_links_check.sh
# Purpose: Regression tests for md-links-check.py — link/anchor resolution, fence and
#          code-span masking, slugification (em-dash, duplicates), carve-outs, and the
#          fail-open paths (garbage stdin, non-md, missing file).
# Usage:   bash scripts/tests/test_md_links_check.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="$here/../md-links-check.py"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'SKIP  python3 not available\n'
  exit 0
fi

sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT

pass=0
fail=0

# run <file_path> <expected_exit> <label>
run() {
  local file="$1" want="$2" label="$3" got=0 out err
  out="$sandbox/out"; err="$sandbox/err"
  printf '{"tool_input":{"file_path":"%s"}}' "$file" \
    | python3 "$script" >"$out" 2>"$err" || got=$?
  if [[ "$got" -ne "$want" ]]; then
    printf 'FAIL  %s (want %d, got %d)\n' "$label" "$want" "$got"
    [[ -s "$err" ]] && sed 's/^/      /' "$err"
    fail=$((fail + 1))
    return
  fi
  # A flag must explain itself on stderr and stay silent on stdout.
  if [[ "$want" -eq 2 ]] && [[ ! -s "$err" || -s "$out" ]]; then
    printf 'FAIL  %s (flag must write stderr only)\n' "$label"
    fail=$((fail + 1))
    return
  fi
  printf 'PASS  %s (exit %d)\n' "$label" "$got"
  pass=$((pass + 1))
}

# ---------- Fixtures ----------
docs="$sandbox/docs"
mkdir -p "$docs/sub"
printf '# Target Doc\n\n## Section One\n\n## /debrief — End-of-Session\n\n## Dup\n\n## Dup\n\ntext\n' > "$docs/target.md"
printf 'plain file\n' > "$docs/asset.txt"

# ---------- Resolution basics ----------
printf '# T\n\n[ok](target.md) and [ok2](./asset.txt) and [dir](sub/)\n' > "$docs/good.md"
run "$docs/good.md" 0 "valid file, asset, and directory links"

printf '# T\n\n[broken](missing.md)\n' > "$docs/broken.md"
run "$docs/broken.md" 2 "broken relative link flagged"

printf '# T\n\n![img](missing.png)\n' > "$docs/img.md"
run "$docs/img.md" 2 "broken image target flagged"

printf '# T\n\n[ref]: missing-ref.md\n' > "$docs/refdef.md"
run "$docs/refdef.md" 2 "broken reference definition flagged"

printf '# T\n\n[ext](https://example.com/x) [m](mailto:a@b.c) [abs](/etc/hosts) [pr](//cdn/x)\n' > "$docs/ext.md"
run "$docs/ext.md" 0 "external, absolute, and protocol-relative targets skipped"

# ---------- Masking ----------
printf '# T\n\n```markdown\n[example](does-not-exist.md)\n```\n\ntext\n' > "$docs/fence.md"
run "$docs/fence.md" 0 "link inside code fence ignored"

printf '# T\n\nUse `[x](does-not-exist.md)` inline.\n' > "$docs/span.md"
run "$docs/span.md" 0 "link inside inline code ignored"

printf '# T\n\n    see [cfg](does-not-exist.md) in indented code\n' > "$docs/indent.md"
run "$docs/indent.md" 0 "link inside indented code ignored"

printf '# T\n\n[Note]: This callout is prose, not a ref-def\n' > "$docs/callout.md"
run "$docs/callout.md" 0 "non-path ref-def callout ignored"

printf '# T\n\nindex element[i](j) in prose\n' > "$docs/prose.md"
run "$docs/prose.md" 0 "bracket-paren prose ignored"

printf -- '---\ntitle: x\nnote: "[y](does-not-exist.md)"\n---\n\n# T\n\ntext\n' > "$docs/fm.md"
run "$docs/fm.md" 0 "frontmatter content not scanned for links"

# ---------- Anchors: same-file ----------
printf '# Alpha\n\n## Beta Section\n\n[jump](#beta-section)\n' > "$docs/self-ok.md"
run "$docs/self-ok.md" 0 "valid same-file anchor"

printf '# Alpha\n\n[jump](#nope)\n' > "$docs/self-bad.md"
run "$docs/self-bad.md" 2 "broken same-file anchor flagged"

# ---------- Anchors: cross-file ----------
printf '# T\n\n[s](target.md#section-one)\n' > "$docs/x-ok.md"
run "$docs/x-ok.md" 0 "valid cross-file anchor"

printf '# T\n\n[s](target.md#no-such-anchor)\n' > "$docs/x-bad.md"
run "$docs/x-bad.md" 2 "broken cross-file anchor flagged"

printf '# T\n\n[d](target.md#debrief--end-of-session)\n' > "$docs/emdash.md"
run "$docs/emdash.md" 0 "em-dash heading slug (double hyphen)"

printf '# T\n\n[d2](target.md#dup-1)\n' > "$docs/dup.md"
run "$docs/dup.md" 0 "duplicate heading -1 suffix"

printf '# T\n\n[s](target.md#Section-One)\n' > "$docs/case.md"
run "$docs/case.md" 0 "anchor match is case-insensitive"

printf '# T\n\n<a id="explicit-spot"></a>\n\n[e](#explicit-spot)\n' > "$docs/htmlid.md"
run "$docs/htmlid.md" 0 "HTML id= anchor accepted"

setext="$docs/setext.md"
printf 'Setext Title\n===\n\ntext\n' > "$setext"
printf '# T\n\n[s](setext.md#setext-title)\n' > "$docs/se-link.md"
run "$docs/se-link.md" 0 "setext heading collected"

# ---------- Leniency ----------
printf '# T\n\n![sized](img.png =100x)\n' > "$docs/malformed.md"
run "$docs/malformed.md" 0 "malformed target with whitespace skipped"

printf '# T\n\n[sp](<target file.md>)\n' > "$docs/angle.md"
printf '# spaced\n' > "$docs/target file.md"
run "$docs/angle.md" 0 "angle-bracketed target with space resolves"

pct="$docs/pct.md"
printf '# T\n\n[sp](target%%20file.md)\n' > "$pct"
run "$pct" 0 "percent-encoded target resolves"

# ---------- Carve-outs and guards ----------
mkdir -p "$sandbox/repo/plans" "$sandbox/repo/specs"
printf '# Plan\n\n[future](scripts/not-yet-written.sh)\n' > "$sandbox/repo/plans/p.md"
run "$sandbox/repo/plans/p.md" 0 "plans/ carve-out"
printf '# Spec\n\n[future](scripts/not-yet-written.sh)\n' > "$sandbox/repo/specs/s.md"
run "$sandbox/repo/specs/s.md" 0 "specs/ carve-out"

printf 'x\n' > "$sandbox/notmd.txt"
run "$sandbox/notmd.txt" 0 "non-markdown file ignored"
run "$sandbox/absent.md" 0 "missing file ignored"

# ---------- Fail-safe stdin ----------
got=0
printf 'not json' | python3 "$script" >/dev/null 2>&1 || got=$?
if [[ "$got" -eq 0 ]]; then
  printf 'PASS  garbage stdin -> 0 (fail-safe)\n'; pass=$((pass + 1))
else
  printf 'FAIL  garbage stdin (want 0, got %d)\n' "$got"; fail=$((fail + 1))
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
