#!/usr/bin/env bash
set -uo pipefail

# Script: test_audit_name_parity.sh
# Purpose: Fail-closed name-and-count parity between audit.sh (the source of
#          truth) and every prose site that restates its check list — three
#          passages in skills/audit/SKILL.md plus audit.sh's own header
#          paragraph. Ten claims are asserted, each derived from the code
#          rather than from a hand-kept copy: the check-name set, the
#          `.auditignore`-scoped/never-scoped split, and the three derived
#          NUMBERS (named checks, static-sweep total, full-sweep total).
#          Guards the drift measured on 2026-07-29: the count went 13->14->15
#          across four sites, each hand-edited twice, with nothing checking it.
# Usage:   bash scripts/tests/test_audit_name_parity.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
audit_sh="$repo_root/skills/audit/audit.sh"
audit_skill="$repo_root/skills/audit/SKILL.md"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'SKIP  python3 not available\n'
  exit 0
fi

sandbox="$(mktemp -d)"
sandbox="$(cd -P "$sandbox" && pwd)"   # physical: a logical $TMPDIR path can send
trap 'rm -rf "$sandbox"' EXIT          # a path-resolving subject down another branch

pass=0
fail=0

# The parity checker as a standalone python3 script -- a pure function of
# (audit.sh text, SKILL.md text) -> (ok, findings). Written to the sandbox at
# runtime so this test stays self-contained and never touches a tracked file.
checker="$sandbox/name_parity_check.py"
cat > "$checker" << 'PY'
"""Assert every prose restatement of audit.sh's check list against the code.

Nothing here hand-copies a check name or a count. Each fact is DERIVED from
audit.sh and then compared to what the docs claim, so adding, removing, or
renaming a check makes the stale prose fail rather than quietly disagree.

The derivations, and why each is trustworthy:

  checks       the verdict name emitted inside each `check_*()` body -- NOT the
               function name mapped by convention. A check whose emitted name
               stops matching its function name is itself a finding, so the
               convention is asserted rather than assumed.
  gated        invocation sits inside main()'s `if [[ "$run_tests" == true ]]`.
  static       invocation sits outside it.
  scoped       invocation passes "$ignore" -- the mechanical signature of a
               check `.auditignore` can narrow.
  never_scoped checks - scoped.

Two standing guards, both because discovery cannot detect ABSENCE:

  FLOOR        named members whose disappearance must alarm. A parser that
               silently matches fewer names than this is a broken probe, and a
               broken probe reports a clean run.
  every claim  must MATCH its site. An unfindable claim is a FAIL naming that
               claim, never a silent pass -- reworded prose is exactly how a
               postcondition on text goes quiet.
"""

import re
import sys

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20,
}

# Members whose ABSENCE must alarm. Deliberately a subset, not the whole list:
# it is a floor the derivation may exceed (a new check needs no edit here), but
# may never fall below. `hermetic` and `hermetic-outside` are both named on
# purpose -- the first is a strict PREFIX of the second, the live instance of
# the substring-subsumption trap this repo has hit before.
FLOOR = frozenset({
    "format-trailing-ws", "format-crlf", "format-final-newline", "format-tabs",
    "shellcheck", "ruff", "markdownlint", "md-links", "exec-bit", "json",
    "toml", "sync-docs", "tests", "hermetic", "hermetic-outside",
})

DEF_RE = re.compile(r"^(check_[a-z_]+)\(\)")
VERDICT_RE = re.compile(r"verdict_(?:pass|fail|skip)\s+([a-z0-9-]+)")
GATE_OPEN_RE = re.compile(r'^(\s*)if\s+\[\[\s+"\$run_tests"\s+==\s+true\s+\]\]')


def logical_lines(text):
    """Join backslash-continuations so one invocation is one line.

    Without this, `check_hermetic_outside "$scope" ... \\` splits across two
    physical lines and an "$ignore" test could read the wrong half.
    """
    out = []
    buf = ""
    for line in text.split("\n"):
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append(buf + line)
        buf = ""
    if buf:
        out.append(buf)
    return out


def function_bodies(lines):
    """name -> body text, bounded by the bare `}` that closes each function.

    Bounding on the NEXT definition instead would run the last function's body
    to EOF and swallow main() -- measured while building this: it attributed
    main()'s `auditignore` verdict to check_hermetic_outside.
    """
    bodies = {}
    for i, line in enumerate(lines):
        m = DEF_RE.match(line)
        if not m:
            continue
        close = next((j for j in range(i + 1, len(lines)) if lines[j] == "}"), None)
        if close is None:
            raise ValueError(f"{m.group(1)}: no closing brace at column 0")
        bodies[m.group(1)] = "\n".join(lines[i:close + 1])
    return bodies


def gate_span(lines):
    """(start, end) line indices of main()'s --tests block, end exclusive."""
    for i, line in enumerate(lines):
        m = GATE_OPEN_RE.match(line)
        if not m:
            continue
        closer = m.group(1) + "fi"
        end = next((j for j in range(i + 1, len(lines)) if lines[j] == closer), None)
        if end is None:
            raise ValueError("--tests gate has no matching fi at its own indent")
        return i, end
    raise ValueError('no `if [[ "$run_tests" == true ]]` gate found in audit.sh')


def derive(audit_text):
    """audit.sh text -> the five derived sets. Raises on an unusable parse."""
    lines = logical_lines(audit_text)
    bodies = function_bodies(lines)
    if not bodies:
        raise ValueError("no check_*() functions found -- parser reached nothing")

    checks = {}
    for fname, body in bodies.items():
        emitted = sorted(set(VERDICT_RE.findall(body)))
        if len(emitted) != 1:
            raise ValueError(
                f"{fname} emits {emitted or 'no'} verdict name(s); expected exactly 1"
            )
        conventional = fname[len("check_"):].replace("_", "-")
        if emitted[0] != conventional:
            raise ValueError(
                f"{fname} emits '{emitted[0]}', breaking the name convention "
                f"(expected '{conventional}') -- update this test deliberately"
            )
        checks[fname] = emitted[0]

    gate_start, gate_end = gate_span(lines)
    inv_re = re.compile(r"^\s*(check_[a-z_]+)\s")
    static, gated, scoped, seen = set(), set(), set(), set()
    for i, line in enumerate(lines):
        m = inv_re.match(line)
        if not m or m.group(1) not in checks:
            continue
        if DEF_RE.match(line):
            continue
        name = checks[m.group(1)]
        seen.add(name)
        (gated if gate_start < i < gate_end else static).add(name)
        if '"$ignore"' in line:
            scoped.add(name)

    all_checks = set(checks.values())
    missing_inv = all_checks - seen
    if missing_inv:
        raise ValueError(f"defined but never invoked: {sorted(missing_inv)}")
    # An empty `scoped` needs no guard of its own: it surfaces as a mismatch
    # against both scoped prose sites, which name five. A guard here only
    # reworded that finding, and a mutation deleting it survived the suite.

    return {
        "checks": all_checks,
        "static": static,
        "gated": gated,
        "scoped": scoped,
        "never_scoped": all_checks - scoped,
    }


def strip_comment_markers(text):
    """Drop the leading `# ` of shell comments, preserving an inline `#`."""
    return "\n".join(
        re.sub(r"^#[ ]?", "", line) for line in text.split("\n")
    )


def flatten(text):
    """Collapse all whitespace, so a claim that WRAPS still matches.

    A line-based search for a phrase that happens to wrap returns nothing, and
    absence then reads as agreement.
    """
    return re.sub(r"\s+", " ", text)


def backticked(span):
    return set(re.findall(r"`([a-z0-9-]+)`", span))


def bare_list(span):
    return {tok for tok in re.split(r"[,\s]+", span.strip()) if tok}


def number(token):
    """A digit string or an English number word; None if neither."""
    if token.isdigit():
        return int(token)
    return WORD_NUMBERS.get(token.lower())


class Report:
    def __init__(self):
        self.findings = []

    def add(self, claim, detail):
        self.findings.append((claim, detail))

    def match(self, claim, pattern, text, site):
        """Require a claim's site to be findable. An unmatched claim FAILs."""
        m = re.search(pattern, text)
        if m is None:
            self.add(claim, f"claim not found in {site} -- reworded or removed?")
        return m

    def num(self, claim, token, want, what):
        got = number(token)
        if got is None:
            self.add(claim, f"unparseable number {token!r} for {what}")
        elif got != want:
            self.add(claim, f"says {got} {what}, code has {want}")

    def names(self, claim, got, want, what):
        extra = sorted(got - want)
        absent = sorted(want - got)
        if extra or absent:
            self.add(
                claim,
                f"{what} mismatch -- prose has extra {extra or 'none'}, "
                f"missing {absent or 'none'}",
            )


def check(audit_text, skill_text):
    rep = Report()
    try:
        d = derive(audit_text)
    except ValueError as exc:
        return False, [("derive", str(exc))]

    checks = d["checks"]

    # Floor: discovery cannot detect ABSENCE, so a derived set that quietly lost
    # a named member is a broken probe, and a broken probe reports a clean run.
    # The non-zero DENOMINATOR is already assured upstream and is deliberately
    # not re-asserted here: derive() raises when it finds no check bodies, and
    # every body must emit exactly one name, so a second guard at this point
    # would be unreachable. Measured -- a mutation deleting one survived the
    # entire suite, which is what unreachable code looks like from outside.
    below = sorted(FLOOR - checks)
    if below:
        return False, [(
            "floor",
            f"declared floor members absent from the derived set: {below}",
        )]

    skill = flatten(skill_text)
    header = flatten(strip_comment_markers(audit_text[:4000]))

    # --- SKILL.md: the "runs N checks" sentence (name list + gated count) ---
    m = rep.match(
        "skill-runs-count",
        r"The sweep runs (\S+) checks: (.*?)\. The last (\S+) run only with",
        skill,
        "SKILL.md",
    )
    if m:
        rep.num("skill-runs-count", m.group(1), len(checks), "checks run")
        rep.names("skill-name-list", backticked(m.group(2)), checks, "check-name list")
        rep.num("skill-gated-count", m.group(3), len(d["gated"]), "--tests-gated checks")

    # --- SKILL.md: the .auditignore scoped / never-scoped split ---
    m = rep.match(
        "skill-scoped",
        r"It scopes ONLY the (\S+) text-content checks: (.*?)\. "
        r"Code/config checks \((.*?)\) are deliberately never scoped",
        skill,
        "SKILL.md",
    )
    if m:
        rep.num("skill-scoped", m.group(1), len(d["scoped"]), "scoped checks")
        rep.names("skill-scoped", backticked(m.group(2)), d["scoped"], "scoped list")
        rep.names(
            "skill-never-scoped",
            backticked(m.group(3)),
            d["never_scoped"],
            "never-scoped list",
        )

    # --- SKILL.md: the checks= note, three numbers on two different axes ---
    m = rep.match(
        "skill-named-total",
        r"counts emitted verdict lines, not the (\S+) named checks",
        skill,
        "SKILL.md",
    )
    if m:
        rep.num("skill-named-total", m.group(1), len(checks), "named checks")

    m = rep.match(
        "skill-differ-from",
        r"make the totals differ from (\S+):",
        skill,
        "SKILL.md",
    )
    if m:
        rep.num("skill-differ-from", m.group(1), len(checks), "named checks")

    m = rep.match(
        "skill-gated-names",
        r"without `--tests` none of (.*?) emits a line",
        skill,
        "SKILL.md",
    )
    if m:
        rep.names(
            "skill-gated-names", backticked(m.group(1)), d["gated"], "gated-check list"
        )

    m = rep.match(
        "skill-sweep-totals",
        r"a static sweep totals (\S+) and a full one (\S+)\.",
        skill,
        "SKILL.md",
    )
    if m:
        rep.num("skill-static-total", m.group(1), len(d["static"]), "static-sweep total")
        rep.num("skill-full-total", m.group(2), len(checks), "full-sweep total")

    # --- audit.sh's own header paragraph (bare names, not backticked) ---
    m = rep.match(
        "header-scoped",
        r"excludes matching paths from the (\S+) text-content checks \((.*?)\) only",
        header,
        "audit.sh header",
    )
    if m:
        rep.num("header-scoped", m.group(1), len(d["scoped"]), "scoped checks")
        rep.names("header-scoped", bare_list(m.group(2)), d["scoped"], "scoped list")

    m = rep.match(
        "header-never-scoped",
        r"can never silence a code/config check \((.*?)\)",
        header,
        "audit.sh header",
    )
    if m:
        rep.names(
            "header-never-scoped",
            bare_list(m.group(1)),
            d["never_scoped"],
            "never-scoped list",
        )

    return len(rep.findings) == 0, rep.findings


if __name__ == "__main__":
    audit_text = open(sys.argv[1], encoding="utf-8").read()
    skill_text = open(sys.argv[2], encoding="utf-8").read()
    ok, findings = check(audit_text, skill_text)
    if ok:
        print("PASS")
        sys.exit(0)
    for claim, detail in findings:
        print(f"claim {claim}: {detail}")
    sys.exit(1)
PY

# claim_set_matches <checker_output> <expected_claim...>
# Exit 0 iff the set of claim ids the checker reported equals the expected set
# exactly -- order and dupes ignored. Deliberately equality, not containment:
# a subset test passes green when the checker reports an UNEXPECTED extra
# finding, which is precisely how a fixture stops testing what it names.
claim_set_matches() {
  local output="$1"; shift
  local actual expected
  actual="$(sed -nE 's/^claim ([^:]+):.*/\1/p' <<< "$output" | sort -u)"
  if [[ "$#" -eq 0 ]]; then
    expected=""
  else
    expected="$(printf '%s\n' "$@" | sort -u)"
  fi
  [[ "$actual" == "$expected" ]]
}

# check_case <label> <audit_file> <skill_file> <want_exit> [expected_claim...]
check_case() {
  local label="$1" audit_file="$2" skill_file="$3" want="$4"
  shift 4
  local expected_claims=("$@")
  local output got=0

  output="$(python3 "$checker" "$audit_file" "$skill_file" 2>&1)" || got=$?

  if [[ "$got" -ne "$want" ]]; then
    printf 'FAIL  %s (want exit %d, got %d)\n%s\n' "$label" "$want" "$got" "$output"
    fail=$((fail + 1))
    return
  fi

  if claim_set_matches "$output" "${expected_claims[@]}"; then
    printf 'PASS  %s (exit %d)\n' "$label" "$got"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s (claim-set mismatch)\n%s\n' "$label" "$output"
    fail=$((fail + 1))
  fi
}

# mutate <infile> <outfile> <old> <new>
# Literal single-occurrence substitution. Refuses unless the target appears
# EXACTLY once, so a fixture whose anchor drifted fails loudly instead of
# silently producing an unmutated copy that then passes for the wrong reason.
mutate() {
  python3 - "$@" << 'PY'
import sys
path_in, path_out, old, new = sys.argv[1:5]
text = open(path_in, encoding="utf-8").read()
n = text.count(old)
if n != 1:
    sys.exit(f"mutate: target appears {n}x, need exactly 1: {old!r}")
open(path_out, "w", encoding="utf-8").write(text.replace(old, new, 1))
PY
}

# mutate_all <infile> <outfile> <old> <new> <expected_count>
# Global literal substitution that asserts the occurrence count EXACTLY. Used
# for a token rename, where one-occurrence is the wrong precondition but "at
# least one" would let a drifted anchor rename fewer sites than intended and
# still look successful.
mutate_all() {
  python3 - "$@" << 'PY'
import sys
path_in, path_out, old, new, want = sys.argv[1:6]
text = open(path_in, encoding="utf-8").read()
n = text.count(old)
if n != int(want):
    sys.exit(f"mutate_all: target appears {n}x, declared {want}: {old!r}")
open(path_out, "w", encoding="utf-8").write(text.replace(old, new))
PY
}

# ---------------------------------------------------------------------------
# 1. GREEN -- the real check. This is the row /audit --tests exercises.
# ---------------------------------------------------------------------------
check_case "GREEN: real audit.sh and SKILL.md agree" \
  "$audit_sh" "$audit_skill" 0

# ---------------------------------------------------------------------------
# 2. RED -- the drift that actually happened: the count went stale.
# ---------------------------------------------------------------------------
stale_count="$sandbox/stale-count-skill.md"
mutate "$audit_skill" "$stale_count" \
  'The sweep runs 15 checks:' 'The sweep runs 13 checks:'
check_case "RED: stale 'runs 13 checks' is caught" \
  "$audit_sh" "$stale_count" 1 "skill-runs-count"

# ---------------------------------------------------------------------------
# 3. RED -- substring subsumption. `hermetic` is a strict PREFIX of
#    `hermetic-outside`, so an unanchored membership test would accept the
#    first as evidence for the second. Drop only `hermetic-outside` from the
#    name list, leaving `hermetic` in place: the set comparison must still
#    report it missing. This repo has hit this exact shape before.
# ---------------------------------------------------------------------------
prefix_trap="$sandbox/prefix-trap-skill.md"
# shellcheck disable=SC2016  # backticked names are literal SKILL.md text to match
mutate "$audit_skill" "$prefix_trap" \
  '; and
`hermetic-outside` (the suite wrote nothing under the Claude config root). The last three run' \
  '. The last three run'
check_case "RED: hermetic-outside dropped while hermetic remains" \
  "$audit_sh" "$prefix_trap" 1 "skill-name-list"

# ---------------------------------------------------------------------------
# 4. RED -- fail-closed on a NEW check. Add a 16th check to audit.sh and leave
#    every doc untouched: this is the forward direction of the measured drift,
#    and it must fire on all four sites at once rather than one of them.
# ---------------------------------------------------------------------------
added_check="$sandbox/added-check-audit.sh"
# shellcheck disable=SC2016  # "$scope" is literal audit.sh source, must not expand
mutate "$audit_sh" "$added_check" \
  '  check_sync_docs "$scope"
' '  check_sync_docs "$scope"
  check_brand_new "$scope"
'
mutate "$added_check" "$added_check.2" \
  'check_sync_docs() {' 'check_brand_new() {
  verdict_pass brand-new
}

check_sync_docs() {'
mv "$added_check.2" "$added_check"
check_case "RED: a 16th check with no doc update fires on every count site" \
  "$added_check" "$audit_skill" 1 \
  "skill-runs-count" "skill-name-list" "skill-named-total" \
  "skill-differ-from" "skill-static-total" "skill-full-total" \
  "skill-never-scoped" "header-never-scoped"

# ---------------------------------------------------------------------------
# 5. RED -- the static total drifts on a DIFFERENT axis from the named count.
#    Moving a check inside the --tests gate leaves 15 named but makes the
#    static sweep 11, so the two numbers must be asserted independently.
# ---------------------------------------------------------------------------
regated="$sandbox/regated-audit.sh"
# shellcheck disable=SC2016  # "$scope"/"$run_tests" are literal audit.sh source
mutate "$audit_sh" "$regated" \
  '  check_sync_docs "$scope"
  if [[ "$run_tests" == true ]]; then' \
  '  if [[ "$run_tests" == true ]]; then
    check_sync_docs "$scope"'
check_case "RED: a check moved behind --tests changes only the static total" \
  "$regated" "$audit_skill" 1 \
  "skill-gated-count" "skill-gated-names" "skill-static-total"

# ---------------------------------------------------------------------------
# 6. RED -- a reworded claim must FAIL, never quietly stop being checked. This
#    is the failure mode of every postcondition on prose: the regex misses,
#    finds nothing, and nothing is what a clean run also looks like.
# ---------------------------------------------------------------------------
reworded="$sandbox/reworded-skill.md"
# The anchor deliberately spans a LINE BREAK -- this claim wraps between "and a"
# and "full one". Written as a single line it matched 0x, and `mutate`'s
# uniqueness guard is what reported that rather than silently handing the
# checker an unmutated copy. The checker itself is immune (it flattens
# whitespace before matching); it was the fixture that had to learn this.
mutate "$audit_skill" "$reworded" \
  'so a static sweep totals 12 and a
  full one 15.' \
  'so the totals depend on which flags you passed.'
check_case "RED: a reworded claim is reported unfindable, not skipped" \
  "$audit_sh" "$reworded" 1 "skill-sweep-totals"

# ---------------------------------------------------------------------------
# 7. RED -- a DEFINITION renamed without its invocation, which severs the link
#    between the two. Named for what it actually exercises: the never-invoked
#    assertion, not the floor. It was originally written as the floor's row and
#    was not -- the severed link raises first, so the floor never ran. Row 9 is
#    the floor's real row; this one stays because a half-applied rename is its
#    own plausible drift and nothing else covers it.
# ---------------------------------------------------------------------------
half_rename="$sandbox/half-rename-audit.sh"
mutate "$audit_sh" "$half_rename" \
  'check_hermetic_outside() {' 'check_hermetic_elsewhere() {'
check_case "RED: a definition renamed without its invocation is rejected" \
  "$half_rename" "$audit_skill" 1 "derive"

# ---------------------------------------------------------------------------
# 8. RED -- the scoped split is derived from "$ignore", so handing a
#    never-scoped check the ignore list must contradict both scoped sites.
# ---------------------------------------------------------------------------
newly_scoped="$sandbox/newly-scoped-audit.sh"
# shellcheck disable=SC2016  # "$scope"/"$ignore" are literal audit.sh source
mutate "$audit_sh" "$newly_scoped" \
  '  check_exec_bit "$scope"' '  check_exec_bit "$scope" "$ignore"'
check_case "RED: a check newly given \$ignore contradicts both scoped sites" \
  "$newly_scoped" "$audit_skill" 1 \
  "skill-scoped" "skill-never-scoped" "header-scoped" "header-never-scoped"

# ---------------------------------------------------------------------------
# 9. RED -- the FLOOR, actually reached. An earlier version of this row renamed
#    only the function and claimed to test the floor; it did not. That rename
#    left main() calling a name no longer defined, so the never-invoked
#    assertion fired first and the floor was never consulted -- a mutation
#    deleting the floor survived the whole suite while this row stayed green.
#    Renaming the verdict token AND the function together keeps every internal
#    assertion satisfied, so the derived set is a coherent 15 that simply no
#    longer contains `hermetic-outside`. Only the floor can object.
# ---------------------------------------------------------------------------
floor_reached="$sandbox/floor-reached-audit.sh"
mutate_all "$audit_sh" "$floor_reached" 'hermetic-outside' 'hermetic-elsewhere' 10
mutate_all "$floor_reached" "$floor_reached.2" \
  'check_hermetic_outside' 'check_hermetic_elsewhere' 2
mv "$floor_reached.2" "$floor_reached"
check_case "RED: a check renamed out of the floor is caught BY the floor" \
  "$floor_reached" "$audit_skill" 1 "floor"

# ---------------------------------------------------------------------------
# 10. RED -- a check body emitting TWO verdict names. This is the shape that
#     produced a false reading while this test was being written (a body bound
#     that ran to EOF attributed main()'s `auditignore` to the last check), so
#     the assertion that each body emits exactly one name is load-bearing.
# ---------------------------------------------------------------------------
two_verdicts="$sandbox/two-verdicts-audit.sh"
mutate "$audit_sh" "$two_verdicts" \
  'check_json() {' 'check_json() {
  verdict_pass json-surprise'
check_case "RED: a check emitting two verdict names is rejected" \
  "$two_verdicts" "$audit_skill" 1 "derive"

# ---------------------------------------------------------------------------
# 11. RED -- a check defined but never invoked. It would be counted in every
#     prose total while never running, so the derivation must refuse it.
# ---------------------------------------------------------------------------
orphan="$sandbox/orphan-audit.sh"
mutate "$audit_sh" "$orphan" \
  'check_json() {' 'check_orphan() {
  verdict_pass orphan
}

check_json() {'
check_case "RED: a check defined but never invoked is rejected" \
  "$orphan" "$audit_skill" 1 "derive"

# ---------------------------------------------------------------------------
# 12. RED -- the name convention. Renaming the function AND its invocation but
#     leaving the emitted verdict name alone is the one drift only the
#     convention assertion owns: nothing is broken for a caller, but the
#     function name and the name in the docs have quietly diverged.
# ---------------------------------------------------------------------------
convention="$sandbox/convention-audit.sh"
mutate_all "$audit_sh" "$convention" \
  'check_hermetic_outside' 'check_hermetic_elsewhere' 2
check_case "RED: a function renamed away from its verdict name is caught" \
  "$convention" "$audit_skill" 1 "derive"

# ---------------------------------------------------------------------------
# 13. RED -- "$ignore" arriving on a CONTINUATION line. The scoped split is
#     derived per invocation, and audit.sh already wraps one invocation across
#     two physical lines, so a scoped argument can legitimately land on the
#     second. Without joining continuations the probe reads only the first half
#     and silently misses it.
# ---------------------------------------------------------------------------
continued="$sandbox/continued-audit.sh"
# shellcheck disable=SC2016  # literal audit.sh source; nothing here may expand
mutate "$audit_sh" "$continued" \
  '      "$hermetic_out_status" "$hermetic_marker"' \
  '      "$hermetic_out_status" "$hermetic_marker" "$ignore"'
check_case "RED: \$ignore on a continuation line still counts as scoped" \
  "$continued" "$audit_skill" 1 \
  "skill-scoped" "skill-never-scoped" "header-scoped" "header-never-scoped"

# ---------------------------------------------------------------------------
# 14. meta -- the claim-set assertion must be exact equality. A subset test
#    would swallow an unexpected extra finding, which is how a RED row stops
#    proving the thing it is named for.
# ---------------------------------------------------------------------------
two_claim_output="$(printf '%s\n' \
  'claim skill-runs-count: says 13 checks run, code has 15' \
  'claim skill-name-list: check-name list mismatch')"

if claim_set_matches "$two_claim_output" "skill-runs-count"; then
  printf 'FAIL  meta: claim-set equality swallows an unexpected extra finding\n'
  fail=$((fail + 1))
else
  printf 'PASS  meta: claim-set equality rejects an unexpected extra finding\n'
  pass=$((pass + 1))
fi

if claim_set_matches "$two_claim_output" "skill-runs-count" "skill-name-list" "phantom"; then
  printf 'FAIL  meta: claim-set equality accepts a phantom expected claim\n'
  fail=$((fail + 1))
else
  printf 'PASS  meta: claim-set equality rejects a phantom expected claim\n'
  pass=$((pass + 1))
fi

if claim_set_matches "$two_claim_output" "skill-name-list" "skill-runs-count"; then
  printf 'PASS  meta: claim-set equality accepts the exact set (order-independent)\n'
  pass=$((pass + 1))
else
  printf 'FAIL  meta: claim-set equality rejects the exact set\n'
  fail=$((fail + 1))
fi

# 15. meta -- `mutate` must refuse a target that is not unique, so a drifted
#     fixture anchor can never yield an unmutated copy that passes vacuously.
if mutate "$audit_sh" "$sandbox/never-written.sh" 'zzz-no-such-anchor' 'x' 2>/dev/null; then
  printf 'FAIL  meta: mutate accepted a target that appears 0 times\n'
  fail=$((fail + 1))
else
  printf 'PASS  meta: mutate refuses a target that is not unique\n'
  pass=$((pass + 1))
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
