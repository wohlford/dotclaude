#!/usr/bin/env python3
"""Mutation-test the DEV mode of scripts/publish-brick.sh.

Run on demand: `python3 scripts/tests/mutate_publish_brick_dev.py`. Deliberately NOT named `test_*`,
so pytest never collects it — it mutates a tracked file in place (restored in a `finally`).

Dev mode exists because the same brick assertions were hand-written five times, each copy able to
drop a property without a sound. That argument is only worth anything if the properties are pinned.
Every row below removes one, and the suite must notice. The progress invariant carries the most
weight: it is the one failure the tip convergence check cannot see, because convergence still passes
by reverting whatever landed on dev after the branch was cut.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "publish-brick.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_publish_brick.sh")]

MUTATIONS = [
    mutate.Mutation(
        "pathspecs are globs again, so a brick file named with glob characters drags its "
        "lookalike into the brick",
        'git_lit() { git --literal-pathspecs -C "$scope" "$@"; }',
        'git_lit() { git -C "$scope" "$@"; }',
    ),
    mutate.Mutation(
        "THE plan-review BLOCKER, restored — literal pathspecs are EXPORTED, reaching the audit, "
        "which then skips every file-discovering check and still prints PASS",
        'git_lit() { git --literal-pathspecs -C "$scope" "$@"; }',
        'git_lit() { git --literal-pathspecs -C "$scope" "$@"; }\nexport GIT_LITERAL_PATHSPECS=1',
    ),
    mutate.Mutation(
        "THE load-bearing dev property — a path that no longer matches the oracle is ignored, so "
        "a dev that moved after the branch was cut is silently reverted at convergence",
        '  [ "${#diverged_files[@]}" -gt 0 ] || return 0',
        "  return 0",
    ),
    mutate.Mutation(
        "the progress invariant never looks at what dev changed, so it can never fire",
        '  [ "${#progress_files[@]}" -gt 0 ] || return 0',
        "  return 0",
    ),
    mutate.Mutation(
        "a listed file that already matches HEAD — a typo, a path in neither tree — becomes a "
        "silently smaller brick instead of a refusal",
        '      if git_lit diff --quiet --no-renames HEAD "$endpoint" -- "$f"; then',
        "      if false; then",
    ),
    mutate.Mutation(
        "a non-conventional subject reaches dev, where no subject guard will ever see it",
        '  [[ "$subject" =~ $re ]] \\',
        "  true \\",
    ),
    mutate.Mutation(
        "a subject in the ADVISE band is no longer refused by name",
        "    advise|block)",
        "    block)",
    ),
    mutate.Mutation(
        "a repo with no policy file refuses every subject — an inert policy read as a block",
        "    inert|ok) ;;",
        "    ok) ;;",
    ),
    mutate.Mutation(
        "--final no longer asserts anything, so a re-derivation short of the oracle reads as done",
        '  [ "$final" = yes ] || return 0',
        "  return 0",
    ),
    mutate.Mutation(
        "a dev brick falls through into the tag step and mints a tag on dev",
        '    rm -f "$files_list"\n    result_line PASS 0\n    return 0\n  fi',
        "  fi",
    ),
    mutate.Mutation(
        "a dev brick writes a CHANGELOG entry that versioning on main never asked for (caught "
        "because changelog_entry.py refuses the version `dev` and every dev brick goes red — not "
        "by the no-CHANGELOG row alone)",
        '  if [ "$mode" = dev ]; then\n    printf \'  changelog: dev mode writes none',
        "  if false; then\n    printf '  changelog: dev mode writes none",
    ),
    mutate.Mutation(
        "a dev brick is accepted on a branch other than dev",
        '      || fail_brick "not on branch $WORKING_BRANCH',
        '      || true "not on branch $WORKING_BRANCH',
    ),
    mutate.Mutation(
        "a dev brick falls back silently to the sibling audit when the scope has none, so a repo "
        "with no audit is judged by a gate nobody named (m1)",
        '    if [ ! -e "$audit_path" ] && [ ! -L "$audit_path" ]; then',
        '    if [ ! -e "$audit_path" ] && [ ! -L "$audit_path" ]; then\n'
        '      audit_path="$installed_audit"\n'
        "    elif false; then",
    ),
    mutate.Mutation(
        "--audit is accepted in a scope that has its own audit file, so the override can replace "
        "a tree's own gate (m2)",
        '  if [ "$present" = yes ]; then',
        "  if false; then",
    ),
    mutate.Mutation(
        "an exported PUBLISH_BRICK_AUDIT is read when no flag was given, so a variable chooses "
        "the gate (m3)",
        '  [ "$audit_given" = yes ] || return 0',
        '  if [ "$audit_given" != yes ] && [ -n "${PUBLISH_BRICK_AUDIT:-}" ]; then\n'
        "    audit_given=yes\n"
        '    audit_override="$PUBLISH_BRICK_AUDIT"\n'
        "  fi\n"
        '  [ "$audit_given" = yes ] || return 0',
    ),
    mutate.Mutation(
        "the not-inside-the-scope verdict is read as outside, so an audit inside the tree being "
        "built is accepted (m4)",
        '0) fatal "the --audit path resolves inside the scope',
        '0) ;; 9) fatal "the --audit path resolves inside the scope',
    ),
    mutate.Mutation(
        "the audit path is resolved by directory only, so a symlinked FINAL component escapes "
        "both the inside-scope test and the recorded physical path (m4a)",
        '  override_real="$(real_path "$audit_override")"',
        '  override_real="$(cd "$(dirname "$audit_override")" 2>/dev/null '
        '&& printf \'%s/%s\' "$(pwd -P)" "$(basename "$audit_override")")"',
    ),
    mutate.Mutation(
        "the audit path is tested and run exactly as given, with no resolution at all (m4b)",
        '  override_real="$(real_path "$audit_override")"',
        '  override_real="$audit_override"',
    ),
    mutate.Mutation(
        "the absolute-path test is dropped, so a relative path resolves against whatever the "
        "working directory was (m5)",
        '    *) fatal "the --audit path must be absolute, got: $audit_override" ;;',
        "    *) ;;",
    ),
    mutate.Mutation(
        "a present-but-unusable audit is reported as absent, conflating a broken tree with a "
        "missing file (m6)",
        '    if [ ! -e "$audit_path" ] && [ ! -L "$audit_path" ]; then',
        '    if [ ! -f "$audit_path" ] || [ ! -x "$audit_path" ]; then',
    ),
    mutate.Mutation(
        "the artifact no longer records the audit SOURCE (m7)",
        "      printf 'AUDIT_SOURCE=%s\\n' \"$audit_source\"",
        "      :",
    ),
    mutate.Mutation(
        "the signing pre-assertion is dropped, so a dev brick can land unsigned by accident (m8)",
        "  signing_requested && return 0",
        "  return 0",
    ),
    mutate.Mutation(
        "--allow-unsigned also skips the readback, so a configured signature that never "
        "appeared passes (m9)",
        '  if signing_requested || [ "$signing_asserted" = yes ]; then',
        '  if { signing_requested || [ "$signing_asserted" = yes ]; } '
        '&& [ "$allow_unsigned" = no ]; then',
    ),
    mutate.Mutation(
        "the readback accepts an unsigned commit (%G? N) as signed (m10)",
        "      G|U) printf",
        "      G|U|N) printf",
    ),
    mutate.Mutation(
        "the pre-assertion compares the raw string with true instead of the shared predicate, "
        "so yes, on and 1 read as not signing (m11)",
        "  signing_requested && return 0",
        '  [ "$raw" = true ] && return 0',
    ),
    mutate.Mutation(
        "the audit path is exported, so every child inherits a variable naming the audit (m12)",
        "  audit_source=override",
        "  audit_source=override\n  export audit_path",
    ),
    mutate.Mutation(
        "the per-brick %G? print is dropped on the unsigned path (m13)",
        "    printf '  %%G?=%s\\n' \"$(git -C \"$scope\" log -1 --format='%G?')\"",
        "    :",
    ),
    mutate.Mutation(
        "the artifact no longer records the audit's SHA-256 (m14)",
        "      printf 'AUDIT_SHA256=%s\\n' \"$digest\"",
        "      :",
    ),
    mutate.Mutation(
        "the recorded digest is of a different file than the one run (m15)",
        '    digest="$(audit_digest "$audit_path")"',
        '    digest="$(audit_digest "$0")"',
    ),
    mutate.Mutation(
        "the recorded digest is the PRE-brick one (the parent commit's audit blob), not the one "
        "taken immediately before the run (m20)",
        '    digest="$(audit_digest "$audit_path")"',
        '    digest="$(git -C "$scope" show "HEAD~1:skills/audit/audit.sh" 2>/dev/null '
        "| python3 -c 'import hashlib, sys; "
        "print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')\"\n"
        '    git -C "$scope" cat-file -e "HEAD~1:skills/audit/audit.sh" 2>/dev/null '
        '|| digest="$(audit_digest "$audit_path")"',
    ),
    mutate.Mutation(
        "the publish-mode readback goes back to the literal true comparison (m16)",
        '  if signing_requested || [ "$signing_asserted" = yes ]; then',
        '  if [ "$(git -C "$scope" config --get commit.gpgsign 2>/dev/null)" = true ] '
        '|| [ "$signing_asserted" = yes ]; then',
    ),
    mutate.Mutation(
        "the audit refusal and the signing assertion swap places (m17)",
        "    check_dev_audit\n    check_dev_signing\n",
        "    check_dev_signing\n    check_dev_audit\n",
    ),
    mutate.Mutation(
        "the signing assertion moves after the progress check (m18)",
        "    check_dev_signing\n"
        '    git -C "$scope" rev-parse --verify -q "$endpoint^{commit}" >/dev/null \\\n'
        '      || fail_brick "the oracle $endpoint is not a commit in $scope"\n'
        "    check_dev_subject\n"
        "    assert_dev_progress\n",
        '    git -C "$scope" rev-parse --verify -q "$endpoint^{commit}" >/dev/null \\\n'
        '      || fail_brick "the oracle $endpoint is not a commit in $scope"\n'
        "    check_dev_subject\n"
        "    assert_dev_progress\n"
        "    check_dev_signing\n",
    ),
    mutate.Mutation(
        "an empty digest is accepted at the precondition site, so an unreadable audit is found "
        "only after the commit (m19)",
        '  [ -n "$pre_digest" ] ||',
        "  true ||",
    ),
    mutate.Mutation(
        "the scope judge's failure status 1 is accepted as outside the scope, so a python3 "
        "that dies without judging lets the override through (m21)",
        "    3) ;;",
        "    1|3) ;;",
    ),
    mutate.Mutation(
        "the audit tracked at HEAD but absent from the worktree is not refused, so --audit "
        "replaces a tree's own gate whenever the file was removed under skip-worktree",
        '    tracked-missing) fatal "HEAD tracks skills/audit/audit.sh but the worktree does not have it',
        '    tracked-missing) ;; tracked-missing-x) fatal "HEAD tracks skills/audit/audit.sh but the worktree does not have it',
    ),
    mutate.Mutation(
        "an audit path that lstat could not examine is read as absent, so --audit is accepted "
        "under a directory the engine cannot search",
        '  if [ "$rc" -ne 3 ]; then',
        "  if false; then",
    ),
    mutate.Mutation(
        "a HEAD that git could not list is read as holding no audit, so --audit is accepted on "
        "an unborn branch",
        "    *) own_state=unknown ;;",
        "    *) own_state=absent ;;",
    ),
    mutate.Mutation(
        "without the flag an audit tracked at HEAD but absent from the worktree is reported "
        "as absent and the refusal advises the override",
        '      tracked-missing) fail_brick "no executable audit at $audit_path: HEAD tracks it',
        '      tracked-missing) : ;; tracked-missing-x) fail_brick "no executable audit at $audit_path: HEAD tracks it',
    ),
    mutate.Mutation(
        "without the flag an audit path that could not be examined is reported as absent and "
        "the refusal advises the override",
        '      unknown) fail_brick "could not judge whether $audit_path exists',
        '      unknown) : ;; unknown-x) fail_brick "could not judge whether $audit_path exists',
    ),
    mutate.Mutation(
        "the readback re-asks the config alone, so a brick whose own checkout turns signing "
        "off after the assertion lands unsigned and passes",
        '  if signing_requested || [ "$signing_asserted" = yes ]; then',
        "  if signing_requested; then",
    ),
    mutate.Mutation(
        "the digest helper runs a bare python3, so a hashlib.py in the working directory "
        "forges the recorded digest",
        "  d=\"$(python3 -I -c 'import hashlib, sys",
        "  d=\"$(python3 -c 'import hashlib, sys",
    ),
    mutate.Mutation(
        "the path resolver runs a bare python3, so the working directory is on its import path",
        "  python3 -I -c 'import os, sys\np = os.path.realpath(sys.argv[1])",
        "  python3 -c 'import os, sys\np = os.path.realpath(sys.argv[1])",
    ),
    mutate.Mutation(
        "the scope judge runs a bare python3, so the working directory is on its import path",
        "  python3 -I -c 'import os, sys\ntry:\n    p = sys.argv[1]",
        "  python3 -c 'import os, sys\ntry:\n    p = sys.argv[1]",
    ),
    mutate.Mutation(
        "the audit-path state helper runs a bare python3, so the working directory is on its "
        "import path",
        "  python3 -I -c 'import os, sys\ntry:\n    os.lstat",
        "  python3 -c 'import os, sys\ntry:\n    os.lstat",
    ),
    mutate.Mutation(
        "the subject policy check runs a bare python3, so the working directory is on its "
        "import path",
        '  grade="$(python3 -I - "$scope"',
        '  grade="$(python3 - "$scope"',
    ),
    mutate.Mutation(
        "a digest holding anything but lowercase hex digits is accepted, so an uppercase value "
        "is recorded",
        "    ''|*[!0-9a-f]*) return 0 ;;",
        "    '') return 0 ;;",
    ),
    mutate.Mutation(
        "a digest of the wrong length is accepted, so a truncated value is recorded",
        '  [ "${#d}" -eq 64 ] || return 0',
        "  :",
    ),
    mutate.Mutation(
        "the caller ignores the resolver's newline status, so the refusal names an unresolved "
        "path instead of the newline",
        '  [ "$real_rc" -ne 3 ] ||',
        "  true ||",
    ),
    mutate.Mutation(
        "the resolver prints a resolved path that holds a newline, so a symlink to a name "
        "ending in one runs a different file",
        'if "\\n" in p:',
        "if False:",
    ),
    mutate.Mutation(
        "a symbolic link at skills or skills/audit in the worktree is admitted as a directory, "
        "so an audit behind the link reads as no audit and --audit replaces it",
        '      4) own_state=unknown; own_why="$c is a symbolic link in the worktree',
        '      4) ;; 9) own_state=unknown; own_why="$c is a symbolic link in the worktree',
    ),
    mutate.Mutation(
        "a regular file where skills or skills/audit belongs is admitted as a directory, so the "
        "refusal is worded as an unexaminable path instead of what was found",
        '      5) own_state=unknown; own_why="$c is not a directory in the worktree"',
        '      5) ;; 9) own_state=unknown; own_why="$c is not a directory in the worktree"',
    ),
    mutate.Mutation(
        "a component helper that failed without judging is read as a directory, so --audit is "
        "accepted although nothing looked at the path",
        '      *) own_state=unknown; own_why="$c could not be examined in the worktree"; return 0 ;;',
        "      *) ;;",
    ),
    mutate.Mutation(
        "the worktree allowlist no longer looks at skills itself, so a link there is followed",
        "  for c in skills skills/audit; do\n    worktree_component_state",
        "  for c in skills/audit; do\n    worktree_component_state",
    ),
    mutate.Mutation(
        "HEAD's entry at skills or skills/audit is accepted whatever its mode, so a tracked "
        "link there reads as no audit",
        '    if [ -n "$listed" ] && [ "${listed%% *}" != 040000 ]; then return 4; fi',
        "    if false; then return 4; fi",
    ),
    mutate.Mutation(
        "the HEAD allowlist no longer looks at skills itself, so a link tracked there is followed",
        '  for c in skills skills/audit; do\n    listed="$(git -C "$scope" ls-tree HEAD -- "$c"',
        '  for c in skills/audit; do\n    listed="$(git -C "$scope" ls-tree HEAD -- "$c"',
    ),
    mutate.Mutation(
        "the component helper runs a bare python3, so the working directory is on its import path",
        "  python3 -I -c 'import os, stat, sys",
        "  python3 -c 'import os, stat, sys",
    ),
    mutate.Mutation(
        "a skip-worktree entry anywhere in the index is admitted, so a removed audit that "
        "git status cannot see reads as no audit",
        '      S*|s*)\n        own_why="the index holds skip-worktree entries',
        '      S*|s*) ;;\n      9)\n        own_why="the index holds skip-worktree entries',
    ),
    mutate.Mutation(
        "an index entry tagged other than plain, such as assume-unchanged, is admitted, so a "
        "file git status was told to ignore reads as no audit",
        '      *)\n        own_why="the index holds entries tagged other than plain tracked',
        '      *) ;;\n      9)\n        own_why="the index holds entries tagged other than plain tracked',
    ),
    mutate.Mutation(
        "a sparse checkout is admitted when it excludes nothing, so the configuration is "
        "never the reason to refuse",
        '  if [ "$cfg" = true ]; then',
        "  if false; then",
    ),
    mutate.Mutation(
        "the status that trusts no fsmonitor is never compared with the configured one, so a "
        "lying hook makes a removed audit read as clean",
        '  if [ "$plain" != "$forced" ]; then',
        "  if false; then",
    ),
    mutate.Mutation(
        "an index that could not be read is admitted, so an unreadable index is vouched for",
        '    own_why="the index could not be read"\n    return 1',
        '    own_why="the index could not be read"\n    :',
    ),
    mutate.Mutation(
        "the precondition also runs when an audit is present, so a tag elsewhere in the index "
        "stops a repo from running its own audit",
        "    yes) own_state=present ;;",
        "    yes) own_state=present; index_faithful || own_state=unknown ;;",
    ),
    mutate.Mutation(
        "the precondition is never applied to an absent verdict, so none of its clauses can "
        "refuse",
        '  if [ "$own_state" = absent ] && ! index_faithful; then',
        "  if false; then",
    ),
    mutate.Mutation(
        "a submodule that is the same directory as skills or skills/audit under another spelling "
        "is admitted, so an audit behind a case-variant submodule reads as no audit",
        '    4) own_why="a submodule entry of the index is the same directory',
        '    4) return 0 ;; 9) own_why="a submodule entry of the index is the same directory',
    ),
    mutate.Mutation(
        "a submodule whose directory is missing from disk is admitted, so a deleted submodule "
        "that status was told to ignore reads as no audit",
        '    5) own_why="a submodule entry of the index has no directory on disk',
        '    5) return 0 ;; 9) own_why="a submodule entry of the index has no directory on disk',
    ),
    mutate.Mutation(
        "a submodule comparison that failed without judging is admitted, so nothing looked at "
        "the submodule entries",
        '    *) own_why="the submodule entries of the index could not be compared',
        '    *) return 0 ;; 9) own_why="the submodule entries of the index could not be compared',
    ),
    mutate.Mutation(
        "the submodule check is never applied to an absent verdict, so none of its clauses can "
        "refuse",
        '  if [ "$own_state" = absent ] && ! gitlinks_clear; then',
        "  if false; then",
    ),
    mutate.Mutation(
        "the submodule comparison no longer looks at skills/audit, so a case-variant submodule "
        "there is followed",
        'sys.exit(3)\' "$tmp" "$scope" skills skills/audit 2>/dev/null; rc=$?',
        'sys.exit(3)\' "$tmp" "$scope" skills 2>/dev/null; rc=$?',
    ),
    mutate.Mutation(
        "the submodule comparison no longer looks at skills, so a case-variant submodule there "
        "is followed",
        'sys.exit(3)\' "$tmp" "$scope" skills skills/audit 2>/dev/null; rc=$?',
        'sys.exit(3)\' "$tmp" "$scope" skills/audit 2>/dev/null; rc=$?',
    ),
    mutate.Mutation(
        "the submodule comparison runs a bare python3, so the working directory is on its import path",
        "  python3 -I -c 'import os, sys\nscope = sys.argv[2]",
        "  python3 -c 'import os, sys\nscope = sys.argv[2]",
    ),
    mutate.Mutation(
        "an index entry tagged other than H, S or h, such as an unmerged path, is admitted, so "
        "an entry git status does not vouch for reads as plain",
        '      [!h]*)\n        own_why="the index holds an entry with a tag other than H',
        '      [!h]*) ;;\n      9)\n        own_why="the index holds an entry with a tag other than H',
    ),
]


def main() -> int:
    baseline = subprocess.run(SUITE, capture_output=True, text=True)
    print(
        f"pre-flight suite: rc={baseline.returncode}  {baseline.stdout.strip()[-60:]}"
    )

    report = mutate.run(SUBJECT, SUITE, MUTATIONS)
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
