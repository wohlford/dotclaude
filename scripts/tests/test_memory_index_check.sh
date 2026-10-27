#!/usr/bin/env bash
set -uo pipefail

# Script: test_memory_index_check.sh
# Purpose: Regression tests for memory-index-check.py — per-entry byte cap, the boundary
#          (> not >=), byte-vs-character counting, the hard-wrap evasion, the bare-dash evasion,
#          the report cap, tail-anchored scope (projects/*/memory/MEMORY.md, symlink
#          tail-survival), the content opt-in gate, the argv/TTY refusal, the fail-open
#          paths (malformed JSON, absent file), and the whole-file aggregate-size check
#          (over/under/boundary, the small-entries discriminator, the opt-in gate on the
#          aggregate path, and non-entry content), the 190-line check, the true breach
#          wording (no "archive", no "ALREADY invisible"), exact basename membership, and that `MEMORY-SHIPPED.md` is no longer a covered name.
# Usage:   bash scripts/tests/test_memory_index_check.sh
#
# The byte cap and the line threshold are READ from the checker (`AGG_MAX_BYTES`,
# `LINE_MAX`) and every fixture is built from them, so raising a cap changes
# no row. One row bounds the byte cap below the client's own cut, which a raise must stay under.
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
# A needle written with a leading "!" must be ABSENT instead.
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
    if [[ "$needle" == '!'* ]]; then
      # A leading "!" asserts ABSENCE: the true-wording rows must prove the old text is gone.
      if contains "${needle#!}" "$sandbox/_err"; then
        printf 'FAIL  %s (stderr still contains forbidden text: %s)\n' "$label" "${needle#!}"
        sed 's/^/      stderr: /' "$sandbox/_err"
        fail=$((fail + 1))
        return
      fi
    elif ! contains "$needle" "$sandbox/_err"; then
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
python3 - "$sandbox" "$script" <<'PY'
import re
import sys
from pathlib import Path

sandbox = Path(sys.argv[1])
SRC = Path(sys.argv[2]).read_text(encoding="utf-8")


def constant(name: str, default: int) -> int:
    """An integer constant read from the checker's own source, so no row pins a cap's value.

    DEFAULT stands in only when the checker does not declare the name; the
    `cap-constants` row below fails in that case, so a rename cannot hide behind the default.
    """
    found = re.search(rf"^{name} = (\d+)", SRC, re.M)
    return int(found.group(1)) if found else default


AGG = constant("AGG_MAX_BYTES", 24000)
LINES = constant("LINE_MAX", 190)


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

# ---- aggregate-size fixtures ---------------------------------------------------------------
# Every size below is derived from the cap READ above (AGG), never typed, so a raise of the cap
# moves these fixtures with it and changes no row. Every entry stays at or under 960B, comfortably
# under MAX_ENTRY_BYTES (1000), so each "must trip" row trips on the aggregate alone.


def entry_sizes(budget: int, entry_max: int, sep: int = 2) -> list[int]:
    """Entry sizes (each <= entry_max) whose lines plus SEP-byte separators fill BUDGET bytes."""
    n = -(-(budget + sep) // (entry_max + sep))
    base, extra = divmod(budget - sep * (n - 1), n)
    sizes = [base + 1] * extra + [base] * (n - extra)
    assert max(sizes) <= entry_max and min(sizes) >= 30, (sizes[:3], sizes[-3:])
    return sizes


def fill_index(header: str, total: int, entry_max: int) -> str:
    """An index of exactly TOTAL bytes: HEADER, blank-separated entries, one final newline."""
    budget = total - len(header.encode("utf-8")) - 1
    lines = [pad_line(f"- [E{i}](t{i}.md) - ", sz) for i, sz in enumerate(entry_sizes(budget, entry_max))]
    content = header + "\n\n".join(lines) + "\n"
    assert len(content.encode("utf-8")) == total, (len(content.encode("utf-8")), total)
    assert all(len(ln.encode("utf-8")) <= entry_max for ln in lines)
    return content


def lines_index(header: str, n_lines: int, entry_bytes: int) -> str:
    """An index whose `wc -l` is exactly N_LINES: HEADER, then one entry per line."""
    entries = [
        pad_line(f"- [L{i}](t{i}.md) - ", entry_bytes)
        for i in range(n_lines - header.count("\n"))
    ]
    content = header + "\n".join(entries) + "\n"
    assert content.count("\n") == n_lines, (content.count("\n"), n_lines)
    return content


def at(name: str, sub: str = "projects/proj1/memory") -> Path:
    """Fixture path `<sandbox>/<name>/<sub>/MEMORY.md` (the file name is set by the caller)."""
    return sandbox / name / sub


# agg-over: 268B past the cap. Plain "over the aggregate, trips".
write(at("agg-over") / "MEMORY.md", fill_index(HEADER, AGG + 268, 960))
# agg-under: 60% of the cap, comfortably under.
agg_under_total = AGG * 3 // 5
write(at("agg-under") / "MEMORY.md", fill_index(HEADER, agg_under_total, 900))
# agg-boundary: EXACTLY the cap. Pins the comparison's direction: the aggregate uses >=, not >
# (unlike the per-entry check above, which deliberately uses >). The opposite choice ("does not trip
# at the boundary") can never be RED, since it is true with no aggregate logic at all.
write(at("agg-boundary") / "MEMORY.md", fill_index(HEADER, AGG, 960))
# agg-small-entries: 1486B past the cap via ~100B entries (all far under the 1000B per-entry cap).
# The discriminator: the aggregate check is not the per-entry check restated.
write(at("agg-small-entries") / "MEMORY.md", fill_index(HEADER, AGG + 1486, 100))
# agg-no-optin: over the cap under a header that does NOT declare the opt-in rule -> must NOT trip.
# Measured justification: wohlford-court is 21555B, unopted-in, on this machine.
write(at("agg-no-optin") / "MEMORY.md", fill_index(NO_OPTIN_HEADER, AGG + 217, 960))
# agg-nonentry-content: 3 small entries plus one PROSE paragraph that does NOT start "- [" (so
# entry_blocks() never sees it), over the cap by 500B. Pins that the aggregate is measured over RAW
# FILE BYTES, not the sum of entry_blocks().
ne_entries = [pad_line(f"- [E{i}](t{i}.md) - ", 300) for i in range(3)]
ne_fixed = len(HEADER.encode("utf-8")) + sum(len(e.encode("utf-8")) for e in ne_entries) + 2 * 2 + 2 + 1
ne_prose = pad_line("Prose paragraph, not a bullet - ", AGG + 500 - ne_fixed)
assert not ne_prose.startswith("- [")
ne_content = HEADER + "\n\n".join(ne_entries) + "\n\n" + ne_prose + "\n"
assert len(ne_content.encode("utf-8")) == AGG + 500, len(ne_content.encode("utf-8"))
write(at("agg-nonentry-content") / "MEMORY.md", ne_content)

# ---- the cap sits below the client's own cut (a bound the value must stay inside) ----
(sandbox / "caps.txt").write_text(f"{AGG} {LINES}\n")

# ---- the 190-line check and the true breach wording --------------------------------------------
# Line fixtures use 40B entries, so they stay far under every byte cap and trip on lines alone.
# `lines` counts `wc -l`, i.e. the newline count the checker reads.
for n in (LINES - 1, LINES, LINES + 25):
    write(at(f"lines-{n}") / "MEMORY.md", lines_index(HEADER, n, 40))
    write(at(f"lines-{n}-q", "projects/q/memory") / "MEMORY.md", lines_index(HEADER, n, 40))
# wording-over: over the byte cap; wording-over-q: another opted-in project, over the cap. 900B
# entries keep the line count far under the line threshold, so these rows reach the byte cap alone.
write(at("wording-over") / "MEMORY.md", fill_index(HEADER, AGG + 9000, 900))
write(at("wording-over-q", "projects/q/memory") / "MEMORY.md", fill_index(HEADER, AGG + 9000, 900))
# both-breach: over the byte cap AND over the line threshold at once (100B blank-separated entries
# give two lines per entry). Both sections must be reported, not one suppressing the other.
write(at("both-breach") / "MEMORY.md", fill_index(HEADER, AGG + 1500, 100))
assert (at("both-breach") / "MEMORY.md").read_bytes().count(b"\n") >= LINES
# mailhost-like: 106 lines, 15347B, opted in. Must stay silent.
mail = lines_index(HEADER, 106, 144)
mail_pad = 15347 - len(mail.encode("utf-8"))
write(at("mailhost", "projects/q/memory") / "MEMORY.md", mail[:-1] + "x" * mail_pad + "\n")
assert (sandbox / "mailhost/projects/q/memory/MEMORY.md").read_bytes().count(b"\n") == 106
# x-memory: an oversized X-MEMORY.md passes the cheap suffix test and must be declined by exact
# basename membership.
write(at("x-memory") / "X-MEMORY.md", fill_index(HEADER, AGG + 5000, 900))
# shipped-declined: MEMORY-SHIPPED.md is no longer a covered name, so an opted-in file over EVERY cap (1500B entry, 2 * AGG + 1000 B, past LINES lines) is declined.
sd_total = 2 * AGG + 1000
sd_big = pad_line("- [Big](big.md) - ", 1500)
sd_rest = fill_index("", sd_total - len(HEADER.encode("utf-8")) - len(sd_big) - 2, 100)
sd_content = HEADER + sd_big + "\n\n" + sd_rest
assert len(sd_content.encode("utf-8")) == sd_total, len(sd_content.encode("utf-8"))
assert sd_content.count("\n") >= LINES
write(at("shipped-declined") / "MEMORY-SHIPPED.md", sd_content)

print("FIXTURES_OK")
PY
fixtures_rc=$?

if [[ "$fixtures_rc" -ne 0 ]]; then
  printf 'FAIL  fixture construction (python3 exited %d building the sandbox — see output above)\n' "$fixtures_rc"
  printf '\nFAIL 0/0 (no row ran)\n'
  exit 1
fi

# ============================================================================================
# The rows
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

# ---- aggregate-size rows. AGG and LINE_CAP are the checker's own constants, read by the
#      fixture builder above; every size a row names is computed from them.
read -r AGG LINE_CAP < "$sandbox/caps.txt"
m="projects/proj1/memory"

# The fixtures above read two constants from the checker. A name the checker does not declare
# falls back to a default there, so this row is what makes a missing or renamed constant a FAIL.
declared=0
for const in AGG_MAX_BYTES LINE_MAX; do
  grep -q "^${const} = [0-9]" "$script" && declared=$((declared + 1))
done
if [[ "$declared" -eq 2 ]]; then
  printf 'PASS  cap-constants: AGG_MAX_BYTES and LINE_MAX are declared\n'
  pass=$((pass + 1))
else
  printf 'FAIL  cap-constants: only %d of AGG_MAX_BYTES, LINE_MAX are declared\n' "$declared"
  fail=$((fail + 1))
fi

run_block "agg-over: ${AGG}B cap + 268B in 960B entries — trips, names size + threshold" \
  "$sandbox/agg-over/$m/MEMORY.md" \
  'memory-index-check:' "$((AGG + 268))" "${AGG}B"

run_ok 'agg-under: 60% of the cap — no trip' \
  "$sandbox/agg-under/$m/MEMORY.md"

run_block 'agg-boundary: exactly the cap — trips (aggregate uses >=, not >)' \
  "$sandbox/agg-boundary/$m/MEMORY.md" \
  'memory-index-check:' "${AGG}B"

run_block 'agg-small-entries: over the cap via ~100B entries (all far under the 1000B cap) — still trips, not the per-entry check restated' \
  "$sandbox/agg-small-entries/$m/MEMORY.md" \
  'memory-index-check:' "$((AGG + 1486))" "${AGG}B"

run_ok 'agg-no-optin: over the cap but the header does NOT declare the opt-in rule — must NOT trip' \
  "$sandbox/agg-no-optin/$m/MEMORY.md"

run_block 'agg-nonentry-content: over the cap via a long prose paragraph, all 3 entries small — trips on raw file bytes, not summed entry_blocks' \
  "$sandbox/agg-nonentry-content/$m/MEMORY.md" \
  'memory-index-check:' "$((AGG + 500))" "${AGG}B"

# The byte cap stays below the client's own cut (25,000 UTF-16 units) and the line threshold below
# its 200-line cut, each above a sane floor, so a raise has a ceiling it cannot cross and a typo
# (200000, or 2000) has a floor it cannot fall under.
if [[ "$AGG" -ge 10000 && "$AGG" -lt 25000 && "$LINE_CAP" -ge 100 && "$LINE_CAP" -lt 200 ]]; then
  printf 'PASS  cap-bounds: AGG_MAX_BYTES=%d in [10000, 25000) and LINE_MAX=%d in [100, 200)\n' "$AGG" "$LINE_CAP"
  pass=$((pass + 1))
else
  printf 'FAIL  cap-bounds: AGG_MAX_BYTES=%d or LINE_MAX=%d is outside its bound — the client cuts at 25,000 units and 200 lines\n' "$AGG" "$LINE_CAP"
  fail=$((fail + 1))
fi

# ---- the true breach wording ----
run_block 'wording: over cap names the real cap and the client cut, never "archive" or "ALREADY invisible"' \
  "$sandbox/wording-over/$m/MEMORY.md" \
  'memory-index-check:' "at or over the ${AGG}B cap" '25,000 UTF-16 units' \
  'or hold the pointer' '!archive' '!ALREADY' '!hub files'

run_block 'wording: another opted-in project over cap — the same text' \
  "$sandbox/wording-over-q/projects/q/memory/MEMORY.md" \
  'memory-index-check:' 'or hold the pointer' '!archive' '!ALREADY'

# One row per sentence, so each reverted sentence fails a row the other one does not. The two
# rows above bundle both forbidden words and so fail together under either revert.
run_block 'wording: the aggregate headline says entries are not silently dropped' \
  "$sandbox/wording-over/$m/MEMORY.md" \
  'memory-index-check:' 'so entries are not silently dropped'

run_block 'wording: the aggregate remedy says not to narrow the check' \
  "$sandbox/wording-over/$m/MEMORY.md" \
  'memory-index-check:' 'do not narrow this check'

# ---- the line check ----
run_block 'both: over the byte cap AND the line threshold — both sections are reported' \
  "$sandbox/both-breach/$m/MEMORY.md" \
  'memory-index-check:' "${LINE_CAP}-line threshold" "${AGG}B cap"

run_ok "lines: $((LINE_CAP - 1)) lines (wc -l) — under the threshold" \
  "$sandbox/lines-$((LINE_CAP - 1))/$m/MEMORY.md"

run_block "lines: exactly $LINE_CAP lines — trips (>=), names the threshold and the remedy" \
  "$sandbox/lines-$LINE_CAP/$m/MEMORY.md" \
  'memory-index-check:' "${LINE_CAP}-line threshold" 'Hold a pointer, or shorten the file.' \
  '!archive'

run_block "lines: $((LINE_CAP + 25)) lines — trips, and reports its own line count" \
  "$sandbox/lines-$((LINE_CAP + 25))/$m/MEMORY.md" \
  'memory-index-check:' "has $((LINE_CAP + 25)) lines" "${LINE_CAP}-line threshold"

# Deliberately does NOT name the threshold: every other line row that asserts a message names it,
# so a mutant that only renames the word and one that suppresses the breach altogether would fail
# the same four rows. This row fails only when the breach itself goes unreported.
run_block "lines: exactly $LINE_CAP lines — trips, and reports the count without needing the threshold's name" \
  "$sandbox/lines-$LINE_CAP/$m/MEMORY.md" \
  'memory-index-check:' "has ${LINE_CAP} lines"

run_ok "lines: $((LINE_CAP - 1)) lines in another opted-in project" \
  "$sandbox/lines-$((LINE_CAP - 1))-q/projects/q/memory/MEMORY.md"

run_block "lines: exactly $LINE_CAP lines in another opted-in project" \
  "$sandbox/lines-$LINE_CAP-q/projects/q/memory/MEMORY.md" \
  'memory-index-check:' "${LINE_CAP}-line threshold" 'Hold a pointer, or shorten the file.'

run_ok 'lines: the mailhost-shaped index (106 lines, 15347B, opted in) stays silent' \
  "$sandbox/mailhost/projects/q/memory/MEMORY.md"

run_ok 'x-memory: an oversized X-MEMORY.md is declined by exact basename membership' \
  "$sandbox/x-memory/$m/X-MEMORY.md"

run_ok 'shipped-declined: an over-cap MEMORY-SHIPPED.md is not a covered name and is declined (rc 0, silent)' \
  "$sandbox/shipped-declined/$m/MEMORY-SHIPPED.md"

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
