#!/usr/bin/env bash
set -uo pipefail

# Script: test_memory_index_check.sh
# Purpose: Regression tests for memory-index-check.py — per-entry byte cap, the boundary
#          (> not >=), byte-vs-character counting, the hard-wrap evasion, the bare-dash evasion,
#          the report cap, tail-anchored scope (projects/*/memory/MEMORY.md, symlink
#          tail-survival), the content opt-in gate, the argv/TTY refusal, the fail-open
#          paths (malformed JSON, absent file), and the whole-file aggregate-size check
#          (over/under/boundary, the small-entries discriminator, the opt-in gate on the
#          aggregate path, and non-entry content). NOTE: the aggregate rows are RED against
#          today's checker — the aggregate check does not exist yet; see Task 2.
# Usage:   bash scripts/tests/test_memory_index_check.sh
#
# Every row asserts BOTH the exit code AND stderr: an exit-0 row requires stderr EMPTY (else a
# crashed/fail-open checker would pass every row for free), and an exit-2 row requires stderr to
# contain checker-identifying text (else "no checker at all" — `python3 <missing file>` also
# exits 2 — would pass every row for free too). Neither half alone catches both failure modes.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="$here/../memory-index-check.py"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'SKIP  python3 not available\n'
  exit 0
fi

# ---------- Sandbox: under the system temp dir, then pinned with pwd -P ----------
# A symlinked TMPDIR (macOS: /tmp -> /private/tmp) yields a LOGICAL mktemp path that does not
# physically contain what gets written under it; the checker resolves every path with
# Path.resolve(), so an unpinned sandbox path can silently diverge from what the checker sees
# and a scope/symlink row would never reach the code it claims to test.
sandbox="$(mktemp -d)"
sandbox="$(cd "$sandbox" && pwd -P)"
trap 'rm -rf "$sandbox"' EXIT

pass=0
fail=0

# contains <needle> <file> — whole-text substring check (not line-based: a needle can wrap,
# and the report line contains a multi-byte "…" character grep -F would otherwise treat as
# alternation only if it embedded a newline, which it does not here, but python keeps this
# check uniform for every row regardless of the needle's shape).
contains() {
  python3 -c '
import sys
needle, path = sys.argv[1], sys.argv[2]
data = open(path, encoding="utf-8", errors="replace").read()
sys.exit(0 if needle in data else 1)
' "$1" "$2"
}

# invoke <file_path> — pipes the hook JSON payload naming file_path to the checker; leaves the
# response in $sandbox/_out / $sandbox/_err and returns the checker's exit code.
invoke() {
  local rc=0
  printf '{"tool_input":{"file_path":"%s"}}' "$1" \
    | python3 "$script" >"$sandbox/_out" 2>"$sandbox/_err" || rc=$?
  return "$rc"
}

# run_ok <label> <file_path> — exit-0 row: rc must be 0 AND stderr must be EMPTY.
run_ok() {
  local label="$1" file="$2" rc=0
  invoke "$file" || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    printf 'FAIL  %s (want rc 0, got %d)\n' "$label" "$rc"
    [[ -s "$sandbox/_err" ]] && sed 's/^/      stderr: /' "$sandbox/_err"
    fail=$((fail + 1))
    return
  fi
  if [[ -s "$sandbox/_err" ]]; then
    printf 'FAIL  %s (rc 0 but stderr not empty — a crashed fail-open path reads as clean too)\n' "$label"
    sed 's/^/      stderr: /' "$sandbox/_err"
    fail=$((fail + 1))
    return
  fi
  if [[ -s "$sandbox/_out" ]]; then
    printf 'FAIL  %s (rc 0 but stdout not empty)\n' "$label"
    fail=$((fail + 1))
    return
  fi
  printf 'PASS  %s (rc 0, stderr empty)\n' "$label"
  pass=$((pass + 1))
}

# run_block <label> <file_path> <must_contain...> — exit-2 row: rc must be 2 AND stderr must be
# non-empty AND must contain every listed needle (a missing checker file also exits 2 with
# stderr, so rc+nonempty alone is not enough; the needles pin it to THIS checker's own output).
run_block() {
  local label="$1" file="$2" rc=0
  shift 2
  invoke "$file" || rc=$?
  if [[ "$rc" -ne 2 ]]; then
    printf 'FAIL  %s (want rc 2, got %d)\n' "$label" "$rc"
    [[ -s "$sandbox/_err" ]] && sed 's/^/      stderr: /' "$sandbox/_err"
    fail=$((fail + 1))
    return
  fi
  if [[ ! -s "$sandbox/_err" ]]; then
    printf 'FAIL  %s (rc 2 but stderr empty — a missing checker also exits 2 with nothing to say)\n' "$label"
    fail=$((fail + 1))
    return
  fi
  local needle
  for needle in "$@"; do
    if ! contains "$needle" "$sandbox/_err"; then
      printf 'FAIL  %s (stderr missing required text: %s)\n' "$label" "$needle"
      sed 's/^/      stderr: /' "$sandbox/_err"
      fail=$((fail + 1))
      return
    fi
  done
  if [[ -s "$sandbox/_out" ]]; then
    printf 'FAIL  %s (rc 2 but stdout not empty — flag must write stderr only)\n' "$label"
    fail=$((fail + 1))
    return
  fi
  printf 'PASS  %s (rc 2, stderr checked)\n' "$label"
  pass=$((pass + 1))
}

# ============================================================================================
# Build every fixture up front. Byte-exact lengths matter (the 4,052 B defect, the 1000/1001 B
# boundary, the 400-char/1,100 B multibyte row) — Python owns padding math and asserts every
# length before the file is written, so an "approximately right" fixture can never silently
# pass as the boundary it claims to be.
# ============================================================================================
python3 - "$sandbox" <<'PY'
import sys
from pathlib import Path

sandbox = Path(sys.argv[1])


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def pad_line(prefix: str, target_bytes: int) -> str:
    """An ASCII-padded line starting with prefix, exactly target_bytes bytes long."""
    prefix_bytes = len(prefix.encode("utf-8"))
    body = prefix + "x" * (target_bytes - prefix_bytes)
    actual = len(body.encode("utf-8"))
    assert actual == target_bytes, (prefix, target_bytes, actual)
    return body


HEADER = "# Memory Index — detail lives in the topic file — never here.\n\n"
# Same 2-line prefix as before the opt-in sentence was folded into line 1, so every fixture's
# entry still starts at physical line 3 and no "line N" assertion below needs to move.
NO_OPTIN_HEADER = "# Memory Index\n\n"

# ---- defect: one entry of exactly 4052 B, landing on line 3 ----
defect_entry = pad_line("- [Pointer](topic.md) — ", 4052)
write(
    sandbox / "defect" / "projects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + defect_entry + "\n",
)

# ---- healthy-corpus: 48 entries, sizes spread 100..459 B (max matches the measured corpus) ----
sizes = [100 + (i * 359 // 47) for i in range(48)]
assert len(sizes) == 48 and max(sizes) == 459 and min(sizes) == 100, sizes
hc_lines = [pad_line(f"- [Entry {i}](topic{i}.md) — ", sz) for i, sz in enumerate(sizes)]
write(
    sandbox / "healthy-corpus" / "projects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + "\n\n".join(hc_lines) + "\n",
)

# ---- boundary-under: exactly 1000 B (expect 0: '>' not '>=') ----
write(
    sandbox / "boundary-under" / "projects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + pad_line("- [Entry](topic.md) — ", 1000) + "\n",
)

# ---- boundary-over: exactly 1001 B (expect 2) ----
write(
    sandbox / "boundary-over" / "projects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + pad_line("- [Entry](topic.md) — ", 1001) + "\n",
)

# ---- multibyte: 400 characters / 1100 UTF-8 bytes, single line — proves byte, not
#      character, counting. "- [" (3 ASCII) + 47 ASCII (1 B each) + 350 EURO SIGNs (3 B each):
#      chars = 3+47+350 = 400; bytes = 3+47+1050 = 1100. ----
mb_line = "- [" + "x" * 47 + "€" * 350
assert len(mb_line) == 400, len(mb_line)
assert len(mb_line.encode("utf-8")) == 1100, len(mb_line.encode("utf-8"))
write(
    sandbox / "multibyte" / "projects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + mb_line + "\n",
)

# ---- wrapped-entry: one 1500 B BLOCK hard-wrapped over 6 indented lines, none over 1000 B
#      alone — the evasion the per-line-length approach would miss. head=250 B, five 249 B
#      continuations: 250 + 5*(1+249) = 1500. ----
we_head = pad_line("- [", 250)
we_cont = "    " + "y" * (249 - 4)
assert len(we_cont.encode("utf-8")) == 249, len(we_cont.encode("utf-8"))
we_conts = [we_cont] * 5
we_total = len(we_head.encode("utf-8")) + sum(1 + len(c.encode("utf-8")) for c in we_conts)
assert we_total == 1500, we_total
assert all(len(c.encode("utf-8")) <= 1000 for c in [we_head, *we_conts])
write(
    sandbox / "wrapped-entry" / "projects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + we_head + "\n" + "\n".join(we_conts) + "\n",
)

# ---- prose-long: a 2000 B line that does NOT start "- [", blank-line separated (expect 0) ----
prose = pad_line("Prose paragraph, not a bullet: ", 2000)
assert not prose.startswith("- ["), prose[:5]
write(
    sandbox / "prose-long" / "projects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + prose + "\n",
)

# ---- report-limit: 7 offenders (each > 1000 B); expect the first 5 plus "… and 2 more" ----
rl_lines = [pad_line(f"- [Entry {i}](topic{i}.md) — ", 1010) for i in range(7)]
write(
    sandbox / "report-limit" / "projects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + "\n\n".join(rl_lines) + "\n",
)

# ---- scope-*: the SAME defect content, each varying exactly one path component from
#      <sb>/projects/<slug>/memory/MEMORY.md, so a PASS is attributable to that one component ----
write(
    sandbox / "scope-projects" / "notprojects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + defect_entry + "\n",
)
write(
    sandbox / "scope-memory" / "projects" / "proj1" / "notmemory" / "MEMORY.md",
    HEADER + defect_entry + "\n",
)
write(
    sandbox / "scope-name" / "projects" / "proj1" / "memory" / "NOTES.md",
    HEADER + defect_entry + "\n",
)

# ---- scope-symlink: a real file under .../projects/proj1/memory/MEMORY.md, plus a sibling
#      symlink "link" -> "projects"; the payload path traverses the symlink, pinning that
#      Path.resolve() survives to the real tail rather than declining at the symlink. ----
sym_root = sandbox / "scope-symlink"
write(
    sym_root / "projects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + defect_entry + "\n",
)
(sym_root / "link").symlink_to(sym_root / "projects", target_is_directory=True)

# ---- optin-absent: in-scope, is-file, entry well over cap — but the header does NOT declare
#      the opt-in rule, so this must decline (rc 0, stderr EMPTY) even though the raw content is
#      identical to "defect" apart from the header. ----
write(
    sandbox / "optin-absent" / "projects" / "proj1" / "memory" / "MEMORY.md",
    NO_OPTIN_HEADER + defect_entry + "\n",
)

# ---- bare-dash-evasion: a short "- [" head immediately followed (no blank line) by a bare
#      "- " continuation of 5000 B with no bracket — the cheaper-than-hard-wrapping evasion.
#      head=21B: "- [Pointer](topic.md)". total = 21 + 1(newline) + 5000 = 5022 B, over cap. ----
bde_head = "- [Pointer](topic.md)"
assert len(bde_head.encode("utf-8")) == 21, len(bde_head.encode("utf-8"))
bde_cont = pad_line("- ", 5000)
assert not bde_cont.startswith("- ["), bde_cont[:5]
bde_total = len(bde_head.encode("utf-8")) + 1 + len(bde_cont.encode("utf-8"))
assert bde_total == 5022, bde_total
write(
    sandbox / "bare-dash-evasion" / "projects" / "proj1" / "memory" / "MEMORY.md",
    HEADER + bde_head + "\n" + bde_cont + "\n",
)

# ---- aggregate-size fixtures (memory-index-reader-limit spec, Task 1) ----------------------
# The checker does not implement an aggregate/whole-file check yet — only the per-entry cap
# above. Threshold pinned here for the check-to-be: 20000 raw file bytes (not entries). Rows
# that expect a TRIP are RED against today's checker (it never emits this message); rows that
# expect NO trip are unavoidably already true today too (there is no aggregate logic yet to
# misfire) and serve as controls the new check must not regress.
#
# Every entry in every one of these fixtures stays at or under 960B, comfortably under
# MAX_ENTRY_BYTES(1000), so today's checker finds zero per-entry offenders and returns rc 0 for
# every one of them (except agg-boundary/agg-small-entries etc. — same reasoning). That makes
# every "must trip" row fail today for a single, unambiguous reason: "want rc 2, got 0".


def build_multi_entries(n: int, size: int, prefix_fmt: str = "- [E{i}](t{i}.md) - ") -> str:
    return "\n\n".join(pad_line(prefix_fmt.format(i=i), size) for i in range(n))


# agg-over: 21 entries @ 960B (each < MAX_ENTRY_BYTES) -> total 20268B, over the 20000B
# threshold. Plain "over the aggregate, trips" case.
agg_over_body = build_multi_entries(21, 960)
agg_over_content = HEADER + agg_over_body + "\n"
assert len(agg_over_content.encode("utf-8")) == 20268, len(agg_over_content.encode("utf-8"))
write(sandbox / "agg-over" / "projects" / "proj1" / "memory" / "MEMORY.md", agg_over_content)

# agg-under: 13 entries @ 900B -> total 11792B, comfortably under 20000B.
agg_under_body = build_multi_entries(13, 900)
agg_under_content = HEADER + agg_under_body + "\n"
assert len(agg_under_content.encode("utf-8")) == 11792, len(agg_under_content.encode("utf-8"))
write(sandbox / "agg-under" / "projects" / "proj1" / "memory" / "MEMORY.md", agg_under_content)

# agg-boundary: 21 entries @ 900B + 1 entry @ 990B -> total EXACTLY 20000B. Pins the
# comparison's direction: this row asserts exactly-at-threshold TRIPS, i.e. the aggregate check
# must use ">=", not ">" (unlike the per-entry check above, which deliberately uses ">"). Chosen
# because "err low" (see spec) favours catching the boundary itself, and because the opposite
# choice ("does not trip" at the boundary) can never be RED here — it is already true today with
# no aggregate logic at all, so it would pin nothing.
agg_boundary_lines = [pad_line(f"- [E{i}](t{i}.md) - ", 900) for i in range(21)] + [
    pad_line("- [ELAST](tlast.md) - ", 990)
]
agg_boundary_body = "\n\n".join(agg_boundary_lines)
agg_boundary_content = HEADER + agg_boundary_body + "\n"
assert len(agg_boundary_content.encode("utf-8")) == 20000, len(
    agg_boundary_content.encode("utf-8")
)
write(
    sandbox / "agg-boundary" / "projects" / "proj1" / "memory" / "MEMORY.md",
    agg_boundary_content,
)

# agg-small-entries: 210 entries @ 100B each (all far under the 1000B per-entry cap) -> total
# 21486B, over threshold. The discriminator: proves the aggregate check is not the per-entry
# check restated — nothing here is anywhere near MAX_ENTRY_BYTES, yet the file must still trip.
agg_small_body = build_multi_entries(210, 100)
agg_small_content = HEADER + agg_small_body + "\n"
assert len(agg_small_content.encode("utf-8")) == 21486, len(agg_small_content.encode("utf-8"))
write(
    sandbox / "agg-small-entries" / "projects" / "proj1" / "memory" / "MEMORY.md",
    agg_small_content,
)

# agg-no-optin: same 21x960B body as agg-over (total over threshold), but under a header that
# does NOT declare the opt-in rule -> must NOT trip. Measured justification: wohlford-court is
# 21555B, unopted-in, on this machine; nothing must enroll it by accident.
agg_no_optin_body = build_multi_entries(21, 960)
agg_no_optin_content = NO_OPTIN_HEADER + agg_no_optin_body + "\n"
assert len(agg_no_optin_content.encode("utf-8")) == 20217, len(
    agg_no_optin_content.encode("utf-8")
)
write(
    sandbox / "agg-no-optin" / "projects" / "proj1" / "memory" / "MEMORY.md",
    agg_no_optin_content,
)

# agg-nonentry-content: 3 small entries (300B each) plus one 19526B PROSE paragraph that does
# NOT start "- [" (so entry_blocks() never sees it) -> total 20500B, over threshold, driven
# entirely by non-entry content. Pins that the aggregate is measured over RAW FILE BYTES, not
# the sum of entry_blocks() — an implementation that reuses entry_blocks and sums its sizes
# under-counts by the scaffolding and would read plausible while missing this row.
agg_ne_entries = [pad_line(f"- [E{i}](t{i}.md) - ", 300) for i in range(3)]
agg_ne_prose = pad_line("Prose paragraph, not a bullet - ", 19526)
assert not agg_ne_prose.startswith("- ["), agg_ne_prose[:5]
agg_ne_body = "\n\n".join(agg_ne_entries) + "\n\n" + agg_ne_prose
agg_ne_content = HEADER + agg_ne_body + "\n"
assert len(agg_ne_content.encode("utf-8")) == 20500, len(agg_ne_content.encode("utf-8"))
write(
    sandbox / "agg-nonentry-content" / "projects" / "proj1" / "memory" / "MEMORY.md",
    agg_ne_content,
)

print("FIXTURES_OK")
PY
fixtures_rc=$?

if [[ "$fixtures_rc" -ne 0 ]]; then
  printf 'FAIL  fixture construction (python3 exited %d building the sandbox — see output above)\n' "$fixtures_rc"
  printf '\nFAIL 0/24\n'
  exit 1
fi

# ============================================================================================
# The 24 rows
# ============================================================================================

run_block 'defect: single 4052B entry over cap, names line and byte count' \
  "$sandbox/defect/projects/proj1/memory/MEMORY.md" \
  'memory-index-check:' 'line 3: 4052B'

run_ok 'healthy-corpus: 48 entries, max 459B, all clean' \
  "$sandbox/healthy-corpus/projects/proj1/memory/MEMORY.md"

run_ok 'boundary-under: exactly 1000B entry (> not >=)' \
  "$sandbox/boundary-under/projects/proj1/memory/MEMORY.md"

run_block 'boundary-over: exactly 1001B entry' \
  "$sandbox/boundary-over/projects/proj1/memory/MEMORY.md" \
  'memory-index-check:'

run_block 'multibyte: 400 chars / 1100B — byte, not character, counting' \
  "$sandbox/multibyte/projects/proj1/memory/MEMORY.md" \
  'memory-index-check:'

run_block 'wrapped-entry: 1500B block hard-wrapped over 6 lines (the evasion row)' \
  "$sandbox/wrapped-entry/projects/proj1/memory/MEMORY.md" \
  'memory-index-check:'

run_ok "prose-long: 2000B line not starting '- [', blank-line separated" \
  "$sandbox/prose-long/projects/proj1/memory/MEMORY.md"

run_block 'report-limit: 7 offenders, lists first 5 plus "… and 2 more"' \
  "$sandbox/report-limit/projects/proj1/memory/MEMORY.md" \
  'memory-index-check:' '… and 2 more'

run_ok "scope-projects: 'notprojects' component — out of scope" \
  "$sandbox/scope-projects/notprojects/proj1/memory/MEMORY.md"

run_ok "scope-memory: 'notmemory' component — out of scope" \
  "$sandbox/scope-memory/projects/proj1/notmemory/MEMORY.md"

run_ok "scope-name: filename 'NOTES.md' — out of scope" \
  "$sandbox/scope-name/projects/proj1/memory/NOTES.md"

run_block 'scope-symlink: symlinked "link" resolves to the real "projects" tail' \
  "$sandbox/scope-symlink/link/proj1/memory/MEMORY.md" \
  'memory-index-check:'

run_ok "optin-absent: in-scope, over cap, header does NOT declare the opt-in rule" \
  "$sandbox/optin-absent/projects/proj1/memory/MEMORY.md"

run_block 'bare-dash-evasion: bare "- " continuation folds into its block (no bracket needed)' \
  "$sandbox/bare-dash-evasion/projects/proj1/memory/MEMORY.md" \
  'memory-index-check:' 'line 3: 5022B'

# ---- aggregate-size rows (memory-index-reader-limit spec, Task 1 — RED against today's
#      checker for every "must trip" row below; see the fixture-construction comment above) ----

run_block 'agg-over: total 20268B > 20000B threshold, 21 entries all <=960B — trips, names size + threshold' \
  "$sandbox/agg-over/projects/proj1/memory/MEMORY.md" \
  'memory-index-check:' '20268' '20000'

run_ok 'agg-under: total 11792B comfortably under the 20000B threshold — no trip' \
  "$sandbox/agg-under/projects/proj1/memory/MEMORY.md"

run_block 'agg-boundary: total exactly 20000B == threshold — trips (aggregate uses >=, not >)' \
  "$sandbox/agg-boundary/projects/proj1/memory/MEMORY.md" \
  'memory-index-check:' '20000'

run_block 'agg-small-entries: total 21486B over threshold via 210 entries of 100B each (all far under the 1000B cap) — still trips, not the per-entry check restated' \
  "$sandbox/agg-small-entries/projects/proj1/memory/MEMORY.md" \
  'memory-index-check:' '21486' '20000'

run_ok 'agg-no-optin: total 20217B over threshold but header does NOT declare the opt-in rule — must NOT trip' \
  "$sandbox/agg-no-optin/projects/proj1/memory/MEMORY.md"

run_block 'agg-nonentry-content: total 20500B over threshold via a 19526B prose paragraph, all 3 entries small (300B) — trips on raw file bytes, not summed entry_blocks' \
  "$sandbox/agg-nonentry-content/projects/proj1/memory/MEMORY.md" \
  'memory-index-check:' '20500' '20000'

# ---- argv-refusal: invoked with an argv arg, stdin at EOF ----
argv_rc=0
python3 "$script" some/file.py </dev/null >"$sandbox/_out" 2>"$sandbox/_err" || argv_rc=$?
if [[ "$argv_rc" -ne 2 ]]; then
  printf 'FAIL  argv-refusal: invoked with an arg, stdin EOF (want rc 2, got %d)\n' "$argv_rc"
  fail=$((fail + 1))
elif [[ ! -s "$sandbox/_err" ]] || ! contains 'is a Claude Code hook' "$sandbox/_err" \
    || ! contains 'stdin' "$sandbox/_err"; then
  printf 'FAIL  argv-refusal: rc 2 but stderr does not identify the refusal\n'
  sed 's/^/      stderr: /' "$sandbox/_err"
  fail=$((fail + 1))
else
  printf 'PASS  argv-refusal: invoked with an arg, stdin EOF -> rc 2 + refusal message\n'
  pass=$((pass + 1))
fi

# ---- tty-refusal: invoked on a PTY — must refuse, not hang ----
tty_rc="$(python3 - "$script" "$sandbox/_tty_err" <<'PY'
import os
import pty
import subprocess
import sys

script, errpath = sys.argv[1], sys.argv[2]
master, slave = pty.openpty()
try:
    proc = subprocess.run(
        [sys.executable, script], stdin=slave, capture_output=True, timeout=15
    )
except subprocess.TimeoutExpired:
    open(errpath, "wb").close()
    print(-1)
    sys.exit(0)
finally:
    os.close(master)
    os.close(slave)
with open(errpath, "wb") as f:
    f.write(proc.stderr)
print(proc.returncode)
PY
)"
if [[ "$tty_rc" == "-1" ]]; then
  printf 'FAIL  tty-refusal: blocked on a PTY instead of refusing (the hang mode)\n'
  fail=$((fail + 1))
elif [[ "$tty_rc" != "2" ]]; then
  printf 'FAIL  tty-refusal: invoked on a PTY (want rc 2, got %s)\n' "$tty_rc"
  fail=$((fail + 1))
elif ! contains 'is a Claude Code hook' "$sandbox/_tty_err"; then
  printf 'FAIL  tty-refusal: rc 2 but stderr does not identify the refusal\n'
  fail=$((fail + 1))
else
  printf 'PASS  tty-refusal: invoked on a PTY -> rc 2, did not hang\n'
  pass=$((pass + 1))
fi

# ---- fail-open: malformed JSON on stdin (expect 0, stderr empty) ----
fo_rc=0
printf 'not json' | python3 "$script" >"$sandbox/_out" 2>"$sandbox/_err" || fo_rc=$?
if [[ "$fo_rc" -ne 0 ]]; then
  printf 'FAIL  fail-open: malformed JSON on stdin (want rc 0, got %d)\n' "$fo_rc"
  fail=$((fail + 1))
elif [[ -s "$sandbox/_err" ]]; then
  printf 'FAIL  fail-open: malformed JSON on stdin but stderr not empty\n'
  sed 's/^/      stderr: /' "$sandbox/_err"
  fail=$((fail + 1))
else
  printf 'PASS  fail-open: malformed JSON on stdin -> rc 0, stderr empty\n'
  pass=$((pass + 1))
fi

# ---- absent-file: in-scope path, but the file itself does not exist (expect 0, stderr empty) ----
run_ok 'absent-file: in-scope path, file does not exist' \
  "$sandbox/absent-file/projects/proj1/memory/MEMORY.md"

# ============================================================================================
total=$((pass + fail))
if [[ "$fail" -eq 0 ]]; then
  printf '\nPASS %d/%d\n' "$pass" "$total"
else
  printf '\nFAIL %d/%d\n' "$pass" "$total"
fi
[[ "$fail" -eq 0 ]]
