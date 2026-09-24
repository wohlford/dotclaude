#!/usr/bin/env bash
set -uo pipefail

# Script: test_push_guard.sh
# Purpose: Regression tests for push-guard.py — a push segment is blocked unless it ITSELF leads
#          with ALLOW_PUSH=1; detection is a git-command-position SUBCOMMAND match (`push`, or
#          `subtree` with `push` among its args), not a raw git-word+push-word text match;
#          non-push, wrapper/auth-asymmetry, newline, and fail-open paths all pass.
# Usage:   bash scripts/tests/test_push_guard.sh

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$here/../push-guard.py"

pass=0
fail=0
run() { # command -> prints exit code, given a JSON {tool_input:{command}}
  local got=0
  printf '%s' "$(python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":sys.argv[1]}}))' "$1")" \
    | python3 "$guard" >/dev/null 2>&1 || got=$?
  printf '%s' "$got"
}
assert() { # cmd want label
  local got; got="$(run "$1")"
  if [[ "$got" -eq "$2" ]]; then
    printf 'PASS  %s (exit %d)\n' "$3" "$got"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$3" "$2" "$got"; fail=$((fail + 1))
  fi
}

# --- blocked (exit 2): an unauthorized push segment ---
assert 'git push' 2 'bare git push'
assert 'git push origin main --follow-tags' 2 'git push with args'
assert 'FOO=1 git push' 2 'env-prefixed push, no ALLOW'
assert 'git -C /some/repo push' 2 'git -C <repo> push'
assert 'git -C "/repo with spaces" push' 2 'quoted -C with spaces still blocked'
assert 'git add -A && git push' 2 'push in a compound segment'
assert 'ALLOW_PUSH=1 git add -A && git push' 2 'override on the WRONG segment -> push still blocked'
assert 'ALLOW_PUSH=1 git fetch && git push' 2 'override scoped to fetch -> push blocked'
assert 'git push; ALLOW_PUSH=1 true' 2 'override after the push -> blocked'

# --- the ambiguity refusal must locate the construct (Task 2, U3) ---
# `run` deliberately discards stderr, so this needs its own capturing helper.
stderr_of_pg() { # command -> the guard's stderr ONLY
  # shellcheck disable=SC2069  # the order is deliberate and the suggested fix would break it
  # `2>&1 >/dev/null` binds stderr to the pipe FIRST, then sends stdout to /dev/null -- which is
  # how you capture stderr alone. shellcheck flags it because the common MISTAKE is writing this
  # when `>/dev/null 2>&1` was meant. Measured both: this order captures the message, the
  # suggested order captures an empty string, which would make every row below pass vacuously.
  printf '%s' "$(python3 -c 'import json,sys;print(json.dumps({"tool_input":{"command":sys.argv[1]}}))' "$1")" \
    | python3 "$guard" 2>&1 >/dev/null
}
contains_pg() { # haystack needle label
  if grep -qF -- "$2" <<<"$1"; then
    printf 'PASS  %s\n' "$3"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (missing: %s)\n' "$3" "$2"; fail=$((fail + 1))
  fi
}
pg_amb="$(stderr_of_pg 'git status "a ` stray backtick"')"
contains_pg "$pg_amb" 'unterminated backtick substitution' \
  'push-guard names the CATEGORY it discards today'
contains_pg "$pg_amb" 'at line ' \
  'push-guard LOCATES the construct'
contains_pg "$pg_amb" 'explain-git-command.py' \
  'push-guard names the tool that shows more'
contains_pg "$pg_amb" 'refused rather than allowed unchecked' \
  'push-guard still says it refused rather than allowed -- its OWN anchor phrase'
assert 'git status; git push' 2 'semicolon-separated push'
assert 'git subtree push origin main' 2 'git subtree push (subcommand+arg match)'

# --- allowed (exit 0): the push segment itself leads with ALLOW_PUSH=1 ---
assert 'ALLOW_PUSH=1 git push' 0 'ALLOW_PUSH=1 git push'
assert 'ALLOW_PUSH=1 git push origin main --follow-tags' 0 'ALLOW_PUSH=1 push with args'
assert 'ALLOW_PUSH=1 git -C /some/repo push' 0 'ALLOW_PUSH=1 git -C push'
assert 'FOO=1 ALLOW_PUSH=1 git push' 0 'tolerates a preceding assignment before ALLOW_PUSH'
assert 'git add -A && ALLOW_PUSH=1 git push' 0 'override leads the push segment in a compound'

# --- non-push git and non-git: pass (exit 0) ---
assert 'git fetch origin' 0 'git fetch'
assert 'git pull' 0 'git pull'
assert 'git commit -m x' 0 'git commit'
assert 'git status' 0 'git status'
assert 'ls -la' 0 'non-git command'

# --- fail-safe (exit 0) ---
failsafe() { # raw-stdin label
  local got=0
  printf '%s' "$1" | python3 "$guard" >/dev/null 2>&1 || got=$?
  if [[ "$got" -eq 0 ]]; then
    printf 'PASS  %s -> 0\n' "$2"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (got %d)\n' "$2" "$got"; fail=$((fail + 1))
  fi
}
failsafe 'not-json' 'garbage stdin'
failsafe '{"tool_input":{}}' 'JSON without .command'
failsafe '{"tool_input":{"command":""}}' 'empty command'

# --- newline handling (segment boundary; auth must not leak across a newline) ---
assert $'git add -A\ngit push' 2 'newline-joined push is a segment boundary -> blocked'
assert $'ALLOW_PUSH=1 git status\ngit push' 2 'ALLOW_PUSH=1 on one newline-segment does not authorize the next'

# --- INVERT (was blocked under the old raw-word match; the tokenizer sees no `push` subcommand) ---
assert 'git commit -m "git push docs"' 0 'push word inside a commit message is not a push subcommand'

# --- new false-positive regressions the old raw-word match tripped on ---
assert 'git add scripts/publication-push-guard.py' 0 'push word inside a pathspec is not a push subcommand'
assert 'git commit -m "convert the guard to python"' 0 'no push word at all, plain commit'
assert 'git tag -a v1.0.0 -m "push guard tokenizer"' 0 'push word inside a tag message is not a push subcommand'
assert 'git commit -m "git push origin main"' 0 'quote-awareness: a full push invocation quoted as a message is not a subcommand'

# --- wrapper / auth asymmetry (all three corners) ---
assert 'sudo git push' 2 'bare wrapper: still detected as a push'
assert 'ALLOW_PUSH=1 sudo git push' 0 'ALLOW_PUSH=1 ahead of a bare wrapper authorizes it'
assert 'sudo ALLOW_PUSH=1 git push' 2 'wrapper before ALLOW_PUSH=1 breaks the env run (load-bearing)'
assert 'env ALLOW_PUSH=1 git push' 2 'env(1) is itself a wrapper, not an assignment -> breaks the run'

# --- detection internals ---
assert 'git -c foo=bar push' 2 'a -c global option is skipped to reach the push subcommand'
assert 'git --git-dir=/x push' 2 'a --git-dir= global option is skipped to reach the push subcommand'
assert 'ALLOW_PUSH=1 git subtree push origin main' 0 'authorized git subtree push'
assert 'git subtree pull origin main' 0 'git subtree pull: "push" not among its args -> not a push op'
assert 'ALLOW_PUSH=12 git push' 2 'ALLOW_PUSH=12 is not the exact token ALLOW_PUSH=1'
# D1 (2026-07-26): this used to fail OPEN. An unparseable command that MENTIONS git is now refused
# rather than allowed unchecked -- that swallow was the same fail-open class as the nested-context
# bypasses. The non-git counterpart below pins the unchanged fail-open posture, which is what keeps
# the blast radius push-shaped.
assert 'git push "oops' 2 'unbalanced quote + git word -> fail closed'

# --- CONCEDED RESIDUAL: an opaque string hides `push` from the tokenizer entirely ---
assert "bash -c 'git push'" 0 'CONCEDED RESIDUAL: push hidden inside an opaque shell -c string'
# --- CONCEDED RESIDUAL: a wrapper WITH its own arguments is not stepped over by starts_command ---
assert 'sudo -u deploy git push' 0 'CONCEDED RESIDUAL: wrapper-with-args is not recognized as a bare wrapper'

# --- DELIBERATE OVER-BLOCK (was a CONCEDED RESIDUAL until the indeterminate-subcommand rule
# closed it) ---
# `git $s` cannot be recognized as literally `push` without expansion, but an INDETERMINATE
# subcommand -- opaque, or itself git-like -- is now blocked here too, whatever it turns out to
# be: "over-block on ambiguity; never disappear" is git_command.py's own posture for the
# ambiguous value slot, via the shared `subcommand_is_indeterminate` predicate. Measured cost: 3
# of 32,255 historical commands carry an opaque subcommand; no documented command in skills/ or
# scripts/ uses `git $SUB`.
# shellcheck disable=SC2016
assert 's=push; git $s origin dev' 2 \
  'DELIBERATE over-block: a non-literal subcommand is now indeterminate, not resolved'

# --- opaque command WORD (the widened `is_git`, 2026-09-18): git_command.py already recognizes
# these in command position, so these block via the widened `is_git` itself -- they pin the
# widened `is_git` at this guard, not push-guard's indeterminate-subcommand rule.
# The push verb is assembled ($V), never spelled contiguously after `git` in this file's own
# source, and interpolated into a double-quoted row so the LITERAL command text the guard
# receives carries a real, resolved `push` token -- everything else that must reach the guard
# literally (a bare `$`, `$(true)`, `${X}`, …) is backslash-escaped so THIS script's own shell
# does not resolve it first.
V=pu""sh
assert "\$(true)git $V origin dev" 2 \
  'opaque command word $(true)git is recognized (the widened is_git)'
assert "\"\$X\"git $V origin dev" 2 \
  'opaque command word "$X"git (quote-merged) is recognized'
assert "g\$(true)it $V origin dev" 2 \
  'opaque command word g$(true)it (mid-word substitution) is recognized'
assert "git\$X $V origin dev" 2 \
  'opaque command word git$X (trailing expansion) is recognized'
assert "REPO=\$BASE/foo.git git $V origin dev" 2 \
  'an assignment ending in /git no longer steals the invocation'
assert "X=/git git $V origin dev" 2 \
  'an assignment X=/git no longer steals the invocation'

# --- indeterminate SUBCOMMAND (push-guard's indeterminate-subcommand rule): blocked by design,
# whatever follows it. These rows depend on that rule rather than the widened `is_git` alone --
# they pin push-guard's indeterminate-subcommand rule at this guard. Measured cost: 3 of
# 32,255 historical commands; no documented command in skills/ or scripts/ uses `git $SUB`.
assert "git \$(true)git origin dev" 2 \
  'DELIBERATE over-block: an opaque, git-like subcommand $(true)git (the option-run-stop fix) is indeterminate'
assert "git -c alias.git=$V git origin dev" 2 \
  'DELIBERATE over-block: an alias named git makes the second git token an indeterminate subcommand'
assert "git \$V2 origin dev" 2 \
  'DELIBERATE over-block: a wholly opaque subcommand $V2 is indeterminate'
assert "git \$(true)git status" 2 \
  'DELIBERATE over-block: an indeterminate subcommand blocks whatever follows it, even a benign-looking status'

# --- unaffected: authorization and argument position still apply normally ---
assert "ALLOW_PUSH=1 \$(true)git $V origin dev" 0 \
  'ALLOW_PUSH=1 authorizes an opaque-command-word push'
assert "echo \$(true)git $V" 0 \
  'opaque command word in argument position is still not a command'

# --- regression: a line continuation must not hide the subcommand ---
# `\` + newline is how any long git command is written. Newlines were rewritten to ` ; ` BEFORE
# shlex saw the backslash, so the escaped space became the subcommand and this exited 0.
assert "$(printf 'git \\\n  push origin dev')" 2 'regression: continuation before the subcommand'
assert "$(printf 'git push \\\n  origin dev')" 2 'regression: continuation mid-arguments'
assert "$(printf 'ALLOW_PUSH=1 git \\\n  push origin dev')" 0 'continuation + override still authorized'
# The fold must not swallow a real newline separating two commands.
assert "$(printf 'git status\ngit push origin dev')" 2 'newline-separated push still blocked'

# --- a blocked push must emit the EXACT stderr message (full string compare, not a glob) ---
want_msg='blocked by push-guard: pushing is explicit-only. Lead the push segment with ALLOW_PUSH=1 (e.g. ALLOW_PUSH=1 git push ...) to authorize it.'
got_msg="$(printf '%s' "$(python3 -c 'import json;print(json.dumps({"tool_input":{"command":"git push"}}))')" | python3 "$guard" 2>&1 1>/dev/null)"
if [[ "$got_msg" == "$want_msg" ]]; then
  printf 'PASS  block stderr is byte-exact\n'; pass=$((pass + 1))
else
  printf 'FAIL  block stderr mismatch\n  want: %s\n  got:  %s\n' "$want_msg" "$got_msg"; fail=$((fail + 1))
fi

# --- nested command contexts: an unauthorized push is still a push (exit 2) ---
assert 'x="$(git push origin dev)"' 2 'push inside quoted $( )'
assert 'x=`git push origin dev`' 2 'push inside backticks'
assert 'x="$(git push origin dev)" && git status' 2 'push in $( ) with trailing git'
assert 'x="$(echo `git push origin dev`)"' 2 'backtick nested inside $( )'
assert 'cat <(git push origin dev)' 2 'push inside process substitution'
assert 'x=`echo \`git push origin dev\`` ' 2 'escaped backticks at depth 2'

# --- the span rule (spec 4.6, evidence rows 10-12) ---
assert 'git commit -m "$(git push origin dev)"' 2 'push in commit'"'"'s own arg span'
assert 'git tag -a v1 -m "$(git push origin dev)"' 2 'push in tag'"'"'s own arg span'
assert 'git -c x="$(git push origin dev)" status' 2 'push in the global-option run'
assert 'git status > "$(git push origin dev)"' 2 'push in a redirect target'

# --- evidence row 13: a trailing # comment must not swallow the next line ---
assert 'git status  # note
git push origin dev' 2 'comment truncation no longer hides the push'

# --- D1: ambiguity now fails CLOSED, but ONLY when the command mentions git ---
assert 'x="$(git push origin dev' 2 'unterminated context fails closed'

# Depth overflow must also fail closed at the GATE, not only in the unit tests. Build the nested
# string in bash rather than writing ten literal levels by hand.
deep='git push origin dev'
for _ in 1 2 3 4 5 6 7 8 9 10; do deep="x=\"\$($deep)\""; done
assert "$deep" 2 'nesting past the depth limit fails closed'

# BLAST-RADIUS BOUND: an unparseable command with NO git word must still fail OPEN. Without this,
# D1 turns push-guard from a push gate into a gate on every malformed command in the session.
assert "echo 'unterminated" 0 'unparseable NON-git command still fails open'
assert 'sed -nE '"'"'s/a"b"c"d/\1/p'"'"' /dev/null' 0 'quote-heavy non-git command still allowed'

# --- the ALLOW_PUSH override survives, at top level and inside a context ---
assert 'ALLOW_PUSH=1 git push origin dev' 0 'override at top level is honored'
assert 'x="$(ALLOW_PUSH=1 git push origin dev)"' 0 'override INSIDE a context is honored'
assert 'ALLOW_PUSH=1 git push origin dev && git status' 0 'override + trailing benign git'

# --- the eval family (fix/eval-wrapper-bypass): git reached THROUGH eval/builtin or a wrapper's `--` ---
assert 'eval git push origin main' 2 'eval: the push behind it is found'
assert 'builtin eval git push origin main' 2 'builtin eval'
assert 'eval -- git push origin main' 2 'eval --'
assert 'command -- git push origin main' 2 'command --'
assert 'exec -- git push origin main' 2 'exec --'
assert 'sudo -- git push origin main' 2 'an external wrapper -- is stepped too'
assert 'ALLOW_PUSH=1 eval git push origin main' 0 'override BEFORE eval authorizes: bash passes it to the eval-ed push (measured)'
assert 'eval ALLOW_PUSH=1 git push origin main' 2 'DECIDED over-block: the authorization walk never skips a WRAPPERS member, as with sudo, although bash would authorize'
assert 'echo eval git push origin main' 0 'eval in argument position is not a command'

# --- CONCEDED RESIDUALS (spec 7b): still ALLOW; pinning current behavior, not an aspiration ---
# If one goes red, something CLOSED it -- investigate and move the row, never invert the assertion.
assert 'sh -c "git push origin dev"' 0 'RESIDUAL: sh -c still allows'
assert "bash -c 'git push origin dev'" 0 'RESIDUAL: bash -c still allows'
assert 'eval "git push origin dev"' 0 'RESIDUAL: eval still allows'
assert "bash -lc 'git push origin dev'" 0 'RESIDUAL: bash -lc still allows'
assert "/bin/sh -c 'git push origin dev'" 0 'RESIDUAL: path-qualified shell still allows'
assert "echo 'git push origin dev' | sh" 0 'RESIDUAL: pipe-into-shell still allows'
assert 'sh <<< "git push origin dev"' 0 'RESIDUAL: herestring still allows'

# --- NOT residuals: green today, must stay green ---
assert 'echo origin dev | xargs git push' 2 'bare xargs git push still blocks'
assert 'git \
 push origin dev' 2 'backslash-newline continuation still blocks (v0.49.7)'

# --- protected baselines: unchanged ---
assert "echo 'git push origin dev'" 0 'single-quoted literal is not a push'
assert 'x="$(( 1 + 2 ))" && git status' 0 'arithmetic expansion is not a push'
assert 'x="$(git status)"' 0 'non-push git inside a substitution stays allowed'

# --- RESERVED WORDS put git in command position, exactly as an operator does -----------------
# Measured, NOT hypothesized: `git push`, `if true; then git push origin main; fi`, `{ git push
# origin main; }`, `! git push origin main`, `while false; do git push origin main; done`,
# `exec git push origin main`, `f() { git push origin main; }; f` were run through the real hook
# before this fix -- only the bare form (rc=2) and the ALLOW_PUSH=1-authorized form (rc=0) behaved
# correctly; all six of the reserved-word/exec shapes below read rc=0 (unblocked). These are NOT
# the CONCEDED RESIDUAL class the module docstring names (nested shell strings, wrapper-with-args):
# the push is a bare `git` token in genuine command position in the segment's own token stream, and
# the tokenizer finds it once `starts_command` is told reserved words count. RED before the fix.
assert '{ git push origin main; }' 2 'reserved: a brace group is command position'
assert 'if true; then git push origin main; fi' 2 'reserved: if/then is command position'
assert 'while false; do git push origin main; done' 2 'reserved: while/do is command position'
assert 'f() { git push origin main; }; f' 2 'reserved: a function body is command position'
assert 'exec git push origin main' 2 'reserved: exec is command position'
assert '! git push origin main' 2 'reserved: ! negation is command position'
assert 'git status && if true; then git push origin main; fi' 2 'reserved: a compound later in a chain'

# THE ROW THAT CATCHES HALF A FIX. Teaching `_segment_has_unauthorized_push` about reserved words
# WITHOUT also teaching `_leading_env_authorized` about them turns this from allow into block:
# that walker starts at seg[0], which is now the reserved word, and breaks before it ever reaches
# the assignment. So this row is GREEN before the change and GREEN after -- and RED for anything
# in between (specifically: RED if only the detection call sites are fixed). It is the control
# proving the two halves landed together, and it is the only row that can say so.
assert 'if true; then ALLOW_PUSH=1 git push origin main; fi' 0 \
  'reserved: the override still authorizes inside a compound'

# The stricter wrapper rule must SURVIVE the reserved-word widening: a wrapper is not a reserved
# word, so an assignment after one still does not authorize.
assert 'sudo ALLOW_PUSH=1 git push origin main' 2 'reserved: a wrapper is still not a reserved word'

# --- fold-continuation parity (2026-09-18): fold_continuations used to remove ANY backslash-
# --- newline pair, whatever preceded it -- the escaped-backslash and CRLF rows below were RED
# --- against dev's tokenizer (rc=0, the push never even recorded) because that naive fold glued
# --- the two commands into one `echo` invocation. bs/cr/lf build the exact bytes without
# --- hand-counting escape sequences in a printf/$'...' literal.
# shellcheck disable=SC1003  # `'\'` is a single-quoted ONE-character string (a literal
# backslash), not an attempt to escape the closing quote -- shellcheck's heuristic misreads it.
bs='\'
cr=$'\r'
lf=$'\n'
assert "echo a${bs}${bs}${lf}git push origin dev" 2 \
  'an ESCAPED backslash before LF is bash literal, not a continuation -- two commands, the second is the push (was RED: dev folded the pair and recorded no invocation)'
assert "echo a${bs}${cr}${lf}git push origin dev" 2 \
  'a backslash before CRLF never folds in bash (the backslash escapes the CR, the LF still ends the command) -- the push runs as a second command (was RED on dev)'
assert "bash <<EOF${lf}git pu${bs}${bs}${lf}sh origin dev${lf}EOF" 2 \
  'an unquoted heredoc body gets bash'"'"'s own backslash pass: \\ becomes \ before the consumer shell folds the continuation, reassembling the push from pu\ + sh (already blocked on dev by the accidental old fold; preserved here for the modeled reason)'
assert "bash <<EOF${lf}echo \"${bs}${bs}\$(git push origin dev)\"${lf}EOF" 2 \
  'a \\ pair directly before $( is emitted as a pair, so the opener stays visible to the outer shell and the substitution runs the push (already blocked on dev; preserved here)'
# shellcheck disable=SC2016  # the label names $( ) literally; nothing here should expand
assert "x=\$(bash <<'EOF'${lf}git push${bs}${lf}EOF${lf})" 2 \
  'a quoted heredoc body inside $( ) ending in a continuation: bash 3.2 joins it onto the terminator and 5.3 drops it, so both readings are recorded and the push is judged in each (was RED: dev picked one reading silently and let this through)'

# --- heredoc context inside a SUBSTITUTION or BACKTICKS: the body's final continuation is read
# --- both ways (joined onto the terminator AND dropped), never refused. Refusing used to mean
# --- ParseAmbiguity, and this guard fails OPEN on an unparseable command with no visible git
# --- word -- so an opaque command word (g$(true)it) inside such a body walked straight through.
# --- Same for heredocs nested past the tokenizer's depth bound: the innermost body is now copied
# --- through verbatim and still walked. The push verb is $V (defined above), never spelled after
# --- the opaque word in this file's source.
# Backticks: bash 3.2 and 5.3 both JOIN the final continuation onto the terminator, so this runs
# `git <verb> origin dev`. Already blocked on HEAD for this guard -- the dropped reading is still a
# bare push, and any push is refused here -- so it is a preserve row for this guard; the
# publication guard's suite carries the same shape as a discriminating row (ref `dev` arrives only
# through the join).
assert "x=\`bash <<'dev'${lf}git $V origin ${bs}${lf}dev${lf}\`" 2 \
  'a quoted heredoc body inside backticks is NOT top-level: its final continuation joins onto the terminator (preserve here -- the dropped reading is already a push)'
# shellcheck disable=SC2016  # the label names $( ) literally; nothing here should expand
assert "x=\$(bash <<'EOF'${lf}g\$(true)it $V origin dev${bs}${lf}EOF${lf})" 2 \
  'an opaque command word in a quoted $( ) heredoc body ending in a continuation is walked, not refused (was RED: the tokenizer raised and the no-git-word command failed open)'
nest_open='' nest_close=''
for k in 1 2 3 4 5 6 7 8 9; do
  nest_open+="bash <<E${k}${lf}"
  nest_close="${lf}E${k}${nest_close}"
done
assert "${nest_open}g\$(true)it $V origin dev${nest_close}" 2 \
  'unquoted heredocs nested past the depth bound: the innermost body is copied verbatim and its opaque-word push still walked (was RED: the tokenizer raised and the no-git-word command failed open)'

# --- the raise rule (2026-09-19): one reading unparseable must not discard one that parses.
# --- A heredoc delimiter may be any quoted word, `(x` included, so JOINING a body's final
# --- continuation onto that terminator opens a `$(` that never closes. The all-join reading of
# --- the command below therefore raises, while the drop reading -- what both bashes run at the
# --- top level -- is a plain unauthorized push. The walk unions the readable variants instead of
# --- propagating the raise.
# ---
# --- PRESERVE here, not RED: measured rc=2 on 17417c7 as well, where the single reading taken
# --- was already the drop one. It earns its place as the row that a mutant inverting the raise
# --- rule ("raise as soon as any variant raises") must kill -- this guard fails CLOSED on a
# --- ValueError only while a literal git word is visible, and one is here.
# ---
assert "bash <<'(x'${lf}git $V origin dev${lf}x=\$${bs}${lf}(x" 2 \
  'raise rule: the all-join reading of this command is unparseable and the drop reading is an unauthorized push -- the push must survive the other reading raising (PRESERVE: rc=2 on 17417c7 too)'

# --- the in-band ambiguity MARKER reaches THIS guard (2026-09-19, Task 3b).
# --- This guard consumes `iter_context_token_streams`, and that primitive used to drop a raising
# --- variant with a bare `continue` while `_walk_context` appended an indeterminate INVOCATION.
# --- Measured on c9c4dad with the witness below: the walk recorded
# --- `[('status', []), ('$<ambiguous-heredoc-reading>', [])]`, the streams carried no marker, and
# --- the verdicts split -- publication-push-guard rc=2, push-guard rc=0, git-timing-guard rc=0.
# --- Shipped `dev` blocks this same witness at rc=2 ("mentions git but could not be parsed"), so
# --- the branch had turned a dev BLOCK into an ALLOW. `_indeterminate_stream` closes it.
# ---
# --- RED on c9c4dad (rc=0), and the body is `git status` DELIBERATELY: the push-carrying witness
# --- above blocks with or without the marker, so it cannot pin the marker at any guard. This row
# --- moves only because the marker arrives.
assert "bash <<'(x'${lf}git status${lf}x=\$${bs}${lf}(x" 2 \
  'the ambiguity MARKER: a read-only body whose all-join reading is unreadable is refused here too (RED on c9c4dad: rc=0, because iter_context_token_streams dropped the lost reading silently)'

# --- the MARKER's block must be EXPLAINED, not handed a remedy that cannot work ---
# The rows above pin the VERDICT; these pin the MESSAGE, and the two are not the same question.
# Measured on the witness below -- which carries NO git word and NO push -- before this change:
# rc=2 with "pushing is explicit-only. Lead the push segment with ALLOW_PUSH=1 ...", and rc=2
# again with ALLOW_PUSH=1 actually leading the command. `_indeterminate_stream`'s own docstring
# records why: that record's leading env-assignment run is empty BY CONSTRUCTION, so no env prefix
# can ever authorize it. The spec's Diagnosability residual is why this is not polish -- an
# unexplainable false block is what later gets "fixed" by narrowing a matcher, the repair this
# repo has twice measured as the fail-open it was trying to remove.
lacks_pg() { # haystack needle label -- the mirror of contains_pg, for a phrase that must be GONE
  if grep -qF -- "$2" <<<"$1"; then
    printf 'FAIL  %s (unexpectedly present: %s)\n' "$3" "$2"; fail=$((fail + 1))
  else
    printf 'PASS  %s\n' "$3"; pass=$((pass + 1))
  fi
}
pg_marker="$(stderr_of_pg "bash <<'(x'${lf}echo hello${lf}x=\$${bs}${lf}(x")"
contains_pg "$pg_marker" 'one READING of this command could not be parsed' \
  'push-guard names the lost READING as the cause'
contains_pg "$pg_marker" 'treated as possibly performing a push' \
  'push-guard says WHY a command carrying no push was judged as one'
contains_pg "$pg_marker" 'no push segment for ALLOW_PUSH=1 to lead' \
  'push-guard says no env prefix authorizes THIS one'
contains_pg "$pg_marker" 'simplify the quoting' \
  'push-guard gives a remedy that can actually work for THIS witness (a reading that RAISED)'
contains_pg "$pg_marker" 'heredoc delimiter containing shell metacharacters' \
  'push-guard names the RAISED cause for a witness whose join reading really does raise'
lacks_pg "$pg_marker" 'This one is the parse CAP' \
  'push-guard does not offer the BUDGET remedy for a raised reading'
contains_pg "$pg_marker" 'explain-git-command.py' \
  'push-guard names the tool that shows the full parse'
# The ABSENCE rows match the PRESCRIPTION, never the bare token: the new message names
# ALLOW_PUSH=1 in order to say it will not help, so `lacks_pg "$pg_marker" 'ALLOW_PUSH=1'` would
# fail on the very sentence that fixes the defect. What must be gone is BLOCK_MESSAGE's wording.
lacks_pg "$pg_marker" 'Lead the push segment with ALLOW_PUSH=1' \
  'push-guard no longer PRESCRIBES the override that cannot work'
lacks_pg "$pg_marker" 'pushing is explicit-only' \
  'push-guard no longer calls this a deliberateness decision about a push'

# THE OTHER CAUSE, and the only one that occurs in practice. The marker is emitted both when a
# variant RAISES and when the parse BUDGET stops the enumeration (`git_command._collect`: "Both
# branches that can lose a reading emit it"). Over 90,675 real commands from the local transcripts,
# 6 emit a marker and 6 of 6 are the BUDGET cause -- ordinary 13-47 KB "write a long report via a
# heredoc" commands -- for which "simplify the quoting" is the one remedy that CANNOT work.
# Eight ambiguous heredocs are 2**8 = 256 readings against MAX_TOTAL_PARSES = 128, so the cap
# stops the walk; every delimiter here parses, so nothing raises and the cause is purely
# truncation.
pg_cap_cmd="$(python3 - <<'PY'
import sys
sys.stdout.write("".join("bash <<'E%d'\nline \\\nE%d\n" % (i, i) for i in range(8)))
sys.stdout.write("echo done\n")
PY
)"
pg_cap="$(stderr_of_pg "$pg_cap_cmd")"
contains_pg "$pg_cap" 'one READING of this command could not be parsed' \
  'the BUDGET cause still reaches the ambiguity message'
contains_pg "$pg_cap" 'This one is the parse CAP, not the quoting' \
  'push-guard names the BUDGET cause rather than the delimiter one'
contains_pg "$pg_cap" 'remove the trailing backslash' \
  'push-guard gives the remedy that CAN clear a parse-cap truncation'
lacks_pg "$pg_cap" 'heredoc delimiter containing shell metacharacters' \
  'push-guard does not blame the delimiter for a truncation -- the measured defect'

# --- THE MARKER IS A STRING AN OPERATOR CAN TYPE -----------------------------------------------
# Measured on shipped dev: `git <push> origin dev '<marker>'` drew the AMBIGUITY refusal, whose
# text says NO env prefix authorizes this one and "adding it will not clear this refusal" -- while
# `ALLOW_PUSH=1 git <push> origin dev '<marker>'` returned rc=0. So the message was false, and a
# real push block was relabelled as an ambiguity block. The guard tested marker MEMBERSHIP across
# every token position; the tokenizer only ever emits it as a whole two-token segment.
#
# The marker is DERIVED from the tokenizer, never hand-copied: a hand-copied constant goes stale
# silently and these rows would then measure a string nothing produces.
pg_mk="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import git_command; print(git_command.AMBIGUOUS_READING_SUBCOMMAND)' "$here/../lib")"
[ -n "$pg_mk" ] || { printf 'FAIL  could not derive the ambiguity marker from the tokenizer\n'; fail=$((fail + 1)); }

assert "git push origin dev '$pg_mk'" 2 \
  'a typed-marker push still BLOCKS -- the verdict is unchanged, only the message moves'
pg_typed="$(stderr_of_pg "git push origin dev '$pg_mk'")"
contains_pg "$pg_typed" 'Lead the push segment with ALLOW_PUSH=1' \
  'an operator-TYPED marker gets the ordinary push message, whose remedy that command has'
lacks_pg "$pg_typed" 'one READING of this command could not be parsed' \
  'an operator-TYPED marker is not relabelled as a lost reading'
# The other half of the measurement, and the reason the old wording was FALSE rather than merely
# unhelpful: the override it said could not help does clear this one.
assert "ALLOW_PUSH=1 git push origin dev '$pg_mk'" 0 \
  'ALLOW_PUSH=1 really does clear a typed-marker push -- so "will not clear this refusal" was false'
# The typed marker in the SUBCOMMAND slot: the one shape a structural test alone cannot tell from
# the tokenizer's own two-token record, which is why the raw-text discriminator exists.
pg_typed_bare="$(stderr_of_pg "git '$pg_mk'")"
lacks_pg "$pg_typed_bare" 'one READING of this command could not be parsed' \
  'a typed marker in the subcommand slot is operator text, not a synthesized record'
assert "ALLOW_PUSH=1 git '$pg_mk'" 0 \
  'and the override clears that one too'

# PRESERVE: an ordinary unauthorized push is untouched -- a runbook greps for this line.
pg_plain_push="$(stderr_of_pg 'git push origin main')"
contains_pg "$pg_plain_push" 'Lead the push segment with ALLOW_PUSH=1' \
  'an ordinary unauthorized push still gets the standard message'
lacks_pg "$pg_plain_push" 'one READING of this command could not be parsed' \
  'an ordinary unauthorized push is not relabelled as an ambiguity'

# PRECEDENCE: the SAME witness carrying a real push keeps the standard message. That command does
# have a working remedy (lead the push segment with the override), and once it is authorized the
# marker's own message is what surfaces -- two steps, each accurate, instead of one that loops.
pg_both="$(stderr_of_pg "bash <<'(x'${lf}git $V origin dev${lf}x=\$${bs}${lf}(x")"
contains_pg "$pg_both" 'Lead the push segment with ALLOW_PUSH=1' \
  'a REAL push outranks the marker: the standard message wins'
lacks_pg "$pg_both" 'one READING of this command could not be parsed' \
  'a REAL push outranks the marker: the ambiguity wording stays out of it'

# --- a deadline the GUARD PROCESS owns -------------------------------------------------------
# The harness kills a hook that outlives its registered `timeout`, DISCARDS its output and tells
# nobody, so a gate killed there is silent and the command RUNS -- a correct rc 2 that arrives
# after the kill is worth exactly as much as an allow. `scripts/lib/guard_deadline.py` gives the
# process its own, lower bound; these rows WATCH it fire, because a deadline that is merely
# INSTALLED has never run.
#
# The payload is a flat bundle of ambiguous quoted heredocs (last body line ending in a backslash)
# padded to ~20 KB -- one command, well under MAX_COMMAND_LENGTH. Measured through this guard:
# 3.5 s. The override drops the deadline to 1 s, so the fired row has ~3x margin and costs a
# second instead of the 570 s the real deadline would.
pg_slow_json="$(python3 - <<'PY'
import json, sys
parts = ["bash <<'E%d'\nline \\\nE%d\n" % (i, i) for i in range(7)]
parts.append("echo " + "p" * 20000 + "\n")
parts.append("git push origin dev\n")
sys.stdout.write(json.dumps({"tool_input": {"command": "".join(parts)}}))
PY
)"

# ONE run per row: the control takes seconds, so rc and stderr are captured together rather than
# by running the guard twice.
pg_capture() { # env-assignment... -> sets PG_RC and PG_ERR
  local errfile
  errfile="$(mktemp)"
  PG_RC=0
  printf '%s' "$pg_slow_json" | env "$@" python3 "$guard" >/dev/null 2>"$errfile" || PG_RC=$?
  PG_ERR="$(cat "$errfile")"
  rm -f "$errfile"
}
assert_num() { # got want label
  if [[ "$1" -eq "$2" ]]; then
    printf 'PASS  %s (exit %d)\n' "$3" "$1"; pass=$((pass + 1))
  else
    printf 'FAIL  %s (want %d, got %d)\n' "$3" "$2" "$1"; fail=$((fail + 1))
  fi
}

# CONTROL first. Without it the fired row's rc=2 proves nothing: this payload carries a real
# unauthorized push, so rc=2 is ALSO what a run that never hit the deadline returns. The control
# pins that the ordinary verdict is reached, and that its wording is not the deadline's.
pg_capture
assert_num "$PG_RC" 2 'deadline CONTROL: the slow payload reaches an ordinary verdict unaided'
contains_pg "$PG_ERR" 'Lead the push segment with ALLOW_PUSH=1' \
  'deadline CONTROL: unaided, the payload gets the standard push message'
lacks_pg "$PG_ERR" 'deadline' \
  'deadline CONTROL: unaided, nothing claims a deadline fired'

pg_capture GUARD_DEADLINE_SECONDS=1
assert_num "$PG_RC" 2 'deadline FIRES: the handler exits 2, fail CLOSED'
contains_pg "$PG_ERR" 'reached its own 1s deadline' \
  'deadline FIRES: the message names the deadline as the cause, and the value armed'
contains_pg "$PG_ERR" 'GUARD_DEADLINE_SECONDS' \
  'deadline FIRES: the message names the knob that changes it'
lacks_pg "$PG_ERR" 'Lead the push segment with ALLOW_PUSH=1' \
  'deadline FIRES: it is not relabelled as an ordinary deliberateness refusal'

# An override the guard cannot parse must be IGNORED AUDIBLY. A tool that silently discards an
# argument it cannot read answers with its own defaults and the run looks normal.
pg_capture GUARD_DEADLINE_SECONDS=banana
contains_pg "$PG_ERR" "ignoring GUARD_DEADLINE_SECONDS='banana'" \
  'deadline: an unparseable override is named, not silently swallowed'
contains_pg "$PG_ERR" 'Lead the push segment with ALLOW_PUSH=1' \
  'deadline: an unparseable override leaves the real deadline in force (the control verdict)'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
