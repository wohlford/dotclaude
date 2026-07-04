#!/usr/bin/env bash
set -euo pipefail

# Script: push-guard.sh
# Purpose: PreToolUse hook — block `git push` unless the push segment leads with an ALLOW_PUSH=1 override
# Usage: Called by Claude Code hooks with JSON on stdin
#
# Exit codes:
#   0 — allow (no push segment, the push segment is authorized, or any internal error → fail open)
#   2 — blocked: an unauthorized `git push` segment (stderr fed back to Claude)
#
# A DELIBERATENESS gate, not an adversarial defense. It forces every push to be an explicit, per-push
# decision: the command segment that runs `git push` must ITSELF lead with `ALLOW_PUSH=1` (other env
# assignments may precede it) — an ALLOW_PUSH=1 on some other
# segment does not authorize it. Detection is a deliberately broad "a git word AND a push word in the
# same segment" match: it over-blocks a few harmless shapes (e.g. `git commit -m "... git push ..."`)
# rather than risk missing a real push behind quoting/globals. An author bent on evasion can always
# prepend ALLOW_PUSH=1, alias push, or use plumbing (git send-pack, git svn dcommit) — a regex denylist
# cannot stop that, and the override concedes it by design. Fails OPEN on malformed input / missing jq.
# Global PreToolUse(Bash) hook: cheap for the common non-push case.

command -v jq >/dev/null 2>&1 || exit 0
input=$(cat) || exit 0
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[ -n "$cmd" ] || exit 0

git_re='(^|[^A-Za-z0-9_])git([^A-Za-z0-9_]|$)'
push_re='(^|[^A-Za-z0-9_])push([^A-Za-z0-9_]|$)'
# Authorized push segment: leads with ALLOW_PUSH=1, tolerating other NAME=value assignments before it.
allow_re='^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*ALLOW_PUSH=1([[:space:]]|$)'

blocked=0
while IFS= read -r seg; do
  # A push segment = both a `git` word and a `push` word appear in it.
  if printf '%s' "$seg" | grep -qE "$git_re" && printf '%s' "$seg" | grep -qE "$push_re"; then
    # Allowed only when THIS segment is authorized — a leading override elsewhere does not count.
    printf '%s' "$seg" | grep -qE "$allow_re" || blocked=1
  fi
done < <(printf '%s\n' "$cmd" | sed -E 's/(&&|[|][|]|;|[|])/\n/g')

if [ "$blocked" = 1 ]; then
  printf 'blocked by push-guard: pushing is explicit-only. Lead the push segment with ALLOW_PUSH=1 (e.g. ALLOW_PUSH=1 git push ...) to authorize it.\n' >&2 || true
  exit 2
fi
exit 0
