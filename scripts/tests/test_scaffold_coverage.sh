#!/usr/bin/env bash
set -uo pipefail

# Script: test_scaffold_coverage.sh
# Purpose: Fail-closed reference-coverage check between templates.md's Bash
#          template and init-bash/SKILL.md's "no arguments expected" strip
#          instruction. Every $VAR the template contains (minus a benign
#          allowlist), plus the two non-variable references `process_file`
#          and `input-file`, must be named in the skill's no-args bullet.
#          Guards against the class of bug fixed four times in one night: a
#          strip instruction whose edit-site list didn't cover every
#          reference the template actually contains.
# Usage:   bash scripts/tests/test_scaffold_coverage.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
templates_md="$repo_root/templates.md"
init_bash_skill="$repo_root/skills/init-bash/SKILL.md"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'SKIP  python3 not available\n'
  exit 0
fi

sandbox="$(mktemp -d)"
trap 'rm -rf "$sandbox"' EXIT

pass=0
fail=0

# The coverage checker as a standalone python3 script -- a pure function of
# (templates.md text, skill text) -> (pass/fail, missing tokens). Written to
# the sandbox at runtime so this test file stays self-contained and never
# touches a tracked file.
checker="$sandbox/coverage_check.py"
cat > "$checker" << 'PY'
import re
import sys

# The eight template variables that have nothing to do with the input-file
# path (verified against templates.md 2026-07-16) -- everything else the
# template references must be named in init-bash's no-args strip.
ALLOWLIST = {
    "cmd", "DEFAULT_TIMEOUT", "dry_run", "exit_code",
    "missing", "SCRIPT_DIR", "temp_file", "verbose",
}
# Non-variable references the strip instruction must also name.
EXTRA_REFS = {"process_file", "input-file"}
VAR_RE = re.compile(r'\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?')


def extract_bash_template(templates_text):
    m = re.search(r'```bash\n(.*?)\n```', templates_text, re.S)
    if not m:
        raise ValueError("no ```bash fence found in templates.md")
    return m.group(1)


def derive_required_tokens(template_src):
    found = set(VAR_RE.findall(template_src))
    variable_tokens = found - ALLOWLIST
    return variable_tokens, EXTRA_REFS


def extract_no_args_bullet(skill_text):
    lines = skill_text.splitlines()
    start = next(
        (i for i, line in enumerate(lines)
         if "If no arguments are expected" in line),
        None,
    )
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r'^\d+\.\s', lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def is_covered(token, bullet, variable_tokens):
    """Variable tokens (derived from the template's $VAR scan) must appear in
    their actual shell-reference form (`$token` or `${token}`); literal
    tokens (EXTRA_REFS -- a function name and a doc placeholder, not shell
    variables) are matched as a bare substring. Without the $-anchor, a
    short variable token like `input` is unfalsifiable: it is a substring of
    the literal `input-file`, which is itself required and anchored by the
    bullet's stable step-1 boilerplate, so `input` could never independently
    be reported missing."""
    if token in variable_tokens:
        return ("$" + token) in bullet or ("${" + token) in bullet
    return token in bullet


def assert_covered(templates_text, skill_text):
    """Pure function: (templates.md text, skill text) -> (ok, missing)."""
    variable_tokens, literal_tokens = derive_required_tokens(
        extract_bash_template(templates_text)
    )
    required = variable_tokens | literal_tokens
    bullet = extract_no_args_bullet(skill_text)
    missing = sorted(
        token for token in required if not is_covered(token, bullet, variable_tokens)
    )
    return (len(missing) == 0, missing)


if __name__ == "__main__":
    templates_text = open(sys.argv[1], encoding="utf-8").read()
    skill_text = open(sys.argv[2], encoding="utf-8").read()
    ok, missing = assert_covered(templates_text, skill_text)
    if ok:
        print("PASS")
        sys.exit(0)
    for token in missing:
        print(f"templates.md references {token}; "
              f"init-bash's no-args strip never mentions it")
    sys.exit(1)
PY

# missing_set_matches <checker_output> <expected_token...>
# Exit 0 iff the set of tokens the checker reported missing (the `<tok>` in each
# `references <tok>;` line) equals the expected set exactly -- order and dupes
# ignored. This is stricter than "every expected token appears": it also fails
# when the checker reports a token the caller did NOT expect, so a future
# allowlist/template edit that introduces a surprise miss cannot pass green.
missing_set_matches() {
  local output="$1"; shift
  local actual expected
  actual="$(sed -nE 's/^.*references ([^;]+);.*/\1/p' <<< "$output" | sort -u)"
  if [[ "$#" -eq 0 ]]; then
    expected=""
  else
    expected="$(printf '%s\n' "$@" | sort -u)"
  fi
  [[ "$actual" == "$expected" ]]
}

# check_case <label> <templates_file> <skill_file> <want_exit> [expected_missing...]
check_case() {
  local label="$1" templates_file="$2" skill_file="$3" want="$4"
  shift 4
  local expected_missing=("$@")
  local output got=0

  output="$(python3 "$checker" "$templates_file" "$skill_file" 2>&1)" || got=$?

  if [[ "$got" -ne "$want" ]]; then
    printf 'FAIL  %s (want exit %d, got %d)\n%s\n' "$label" "$want" "$got" "$output"
    fail=$((fail + 1))
    return
  fi

  if missing_set_matches "$output" "${expected_missing[@]}"; then
    printf 'PASS  %s (exit %d)\n' "$label" "$got"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s (missing-token set mismatch)\n%s\n' "$label" "$output"
    fail=$((fail + 1))
  fi
}

# 1. GREEN -- the real check. Real templates.md + real init-bash/SKILL.md ->
#    PASS. This is the case /audit --tests exercises on every run.
check_case "GREEN: real templates.md covers real init-bash bullet" \
  "$templates_md" "$init_bash_skill" 0

# 2. RED -- proves the checker discriminates. This is the pre-fix bullet
#    text (before tonight's four fixes), which named only INPUT_FILE, not
#    input/process_file/input-file. Hardcoded here deliberately: this
#    fixture tests the CHECKER's discrimination, not the current skill
#    text, so it does not "drift" the way a materialized golden scaffold
#    would -- it is not supposed to track the live skill, only to keep
#    proving the checker still catches this exact historical failure.
red_skill="$sandbox/red-skill.md"
cat > "$red_skill" << 'EOF'
   - If no arguments are expected, simplify `parse_arguments`: remove the
     positional `INPUT_FILE` case and its required-arg check, keeping only the
     `-h`/`-v`/`-n` option handling
EOF
check_case "RED: pre-fix bullet fails, names the misses" \
  "$templates_md" "$red_skill" 1 "INPUT_FILE" "input" "process_file" "input-file"

# 3. Fail-closed -- proves the allowlist can't silently swallow a new
#    reference. Inject a throwaway $src_file into the (real) template; the
#    real skill text obviously never mentions it, so the assertion must
#    fire and name it -- a future template addition can't slip past
#    unnoticed.
injected_templates="$sandbox/injected-templates.md"
python3 - "$templates_md" "$injected_templates" << 'PY'
import sys

path_in, path_out = sys.argv[1], sys.argv[2]
text = open(path_in, encoding="utf-8").read()
text = text.replace(
    'main "$@"\n```',
    'main "$@"\n# throwaway: $src_file\n```',
    1,
)
open(path_out, "w", encoding="utf-8").write(text)
PY
check_case "fail-closed: injected \$src_file is not silently ignored" \
  "$injected_templates" "$init_bash_skill" 1 "src_file"

# 4. bug3-shape -- regression guard for the final-review finding: a variable
#    token must not be coverable by an unrelated longer token that happens
#    to contain it as a substring (`input` inside `input-file`). This
#    fixture names `<input-file>` and `process_file` (both EXTRA_REFS
#    literals) and `$INPUT_FILE`, but never `$input` -- exactly the shape of
#    the historical "bug 3" (dangling `$input` refs). Under the old
#    bare-substring containment test this passed vacuously (`input` was
#    always "found" inside `input-file`), so the checker could never catch
#    a strip instruction that dropped every `$input` reference. With the
#    $-anchored match for variable tokens, this must fail, naming only
#    `input` as missing.
bug3_skill="$sandbox/bug3-skill.md"
cat > "$bug3_skill" << 'EOF'
   - If no arguments are expected, drop `<input-file>` from the usage line,
     delete the `process_file` helper, and remove the `$INPUT_FILE` case
     entirely
EOF
check_case "bug3-shape: input-file/process_file/\$INPUT_FILE present, \$input absent" \
  "$templates_md" "$bug3_skill" 1 "input"

# 5. meta -- the missing-set assertion must be exact equality, not a subset
#    check. The first assertion is the standing regression guard for the Minor
#    fixed here: under the old "every expected token present" logic, a checker
#    output with an UNEXPECTED extra miss passed green (the extra was swallowed).
#    missing_set_matches must reject it. The other two pin the remaining
#    directions (a phantom expected token; the exact order-independent match).
two_miss_output="$(printf '%s\n' \
  'templates.md references input; init-bash never mentions it' \
  'templates.md references src_file; init-bash never mentions it')"

if missing_set_matches "$two_miss_output" "input"; then
  printf 'FAIL  meta: set-equality swallows an unexpected extra miss (subset weakness)\n'
  fail=$((fail + 1))
else
  printf 'PASS  meta: set-equality rejects an unexpected extra miss\n'
  pass=$((pass + 1))
fi

if missing_set_matches "$two_miss_output" "input" "src_file" "phantom"; then
  printf 'FAIL  meta: set-equality accepts a phantom expected token\n'
  fail=$((fail + 1))
else
  printf 'PASS  meta: set-equality rejects a phantom expected token\n'
  pass=$((pass + 1))
fi

if missing_set_matches "$two_miss_output" "src_file" "input"; then
  printf 'PASS  meta: set-equality accepts the exact set (order-independent)\n'
  pass=$((pass + 1))
else
  printf 'FAIL  meta: set-equality rejects the exact set\n'
  fail=$((fail + 1))
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
