#!/usr/bin/env python3
"""Mutation campaign for scripts/propagate-postcheck.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand — `./scripts/tests/mutate_propagate_postcheck.py` — and never while editing
the subject, since the restore would clobber your edits.

The subject is a CHECK, which is where mutation testing earns the most: every failure mode below
turns it into an instrument that reports a verified promote while a dead gate ships. None of them
produce an error, diff noise, or a red suite on their own — a weakened checker looks exactly like
a working one.

Two mutations deserve naming, because they are the ones the suite was initially blind to and
whose rows had to be written before the campaign could grade them:

* **hooks-registered gated on the branch.** Asserting `PASS hooks-registered` on a healthy strict
  promote does not test that the check RAN — a version that skipped it and printed PASS satisfies
  that row too. Only a strict-branch promote carrying a genuinely dropped registration can tell
  the two apart.
* **an undeterminable range failing OPEN.** The fail-closed direction is a design decision, not
  an accident of control flow, so it gets an explicit mutation rather than trusting that the
  `unknown` value happens to fall through the right way.

The repo root is derived from __file__, which means invoking this through a symlinked
`~/.claude/scripts/tests/` would resolve into PRODUCTION's tree and mutate that instead. Run it
from the working copy you intend to grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "propagate-postcheck.sh"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_propagate_postcheck.sh")]

MUTATIONS = [
    mutate.Mutation(
        "hooks-registered is gated on the branch, so a mis-determined range hides a dead gate",
        '  if [[ ! -f "$helper" ]]; then',
        '  if [[ "$in_range" != yes ]]; then verdict_pass hooks-registered\n'
        '  elif [[ ! -f "$helper" ]]; then',
    ),
    mutate.Mutation(
        "byte-identity is asserted on the in-range branch, where the hand-add changes it by design",
        '  if [[ "$in_range" == yes ]]; then',
        '  if [[ "$in_range" == never ]]; then',
    ),
    mutate.Mutation(
        "the identity comparison always holds, so a clobbered runtime file reads clean",
        '  elif [[ "$(lower "$got")" == "$(lower "$before_sha")" ]]; then\n'
        "    verdict_pass settings-identical",
        "  elif true; then\n    verdict_pass settings-identical",
    ),
    mutate.Mutation(
        "an undeterminable range fails OPEN — identity stops being required",
        "    verdict_fail range \\\n"
        '      "no usable pre-merge HEAD ($head_src unset or unresolvable)',
        "    in_range=yes\n"
        "    verdict_fail range \\\n"
        '      "no usable pre-merge HEAD ($head_src unset or unresolvable)',
    ),
    mutate.Mutation(
        "a non-ancestor pre-merge HEAD is believed rather than refused",
        '  elif ! git -C "$scope" merge-base --is-ancestor "$base" "$head_sha" 2>/dev/null; then',
        "  elif false; then",
    ),
    mutate.Mutation(
        "a parked stash stops being a failure, so an unrestored promote passes",
        '  if [[ -z "$out" ]]; then\n    verdict_pass stash-empty',
        "  if true; then\n    verdict_pass stash-empty",
    ),
    mutate.Mutation(
        "a cleared skip-worktree flag is accepted, exposing the runtime file to every checkout",
        "    S*) verdict_pass skip-worktree ;;",
        "    S*|H*) verdict_pass skip-worktree ;;",
    ),
    mutate.Mutation(
        "merge-applied always passes, so a promote that never landed reads as verified",
        '  elif [[ "$head_sha" == "$ref_sha" ]]; then\n    verdict_pass merge-applied',
        "  elif true; then\n    verdict_pass merge-applied",
    ),
    mutate.Mutation(
        "the hooks-check allowlist widens to any RESULT line, so its FAIL reads as clean",
        "      'RESULT: PASS'*) verdict_pass hooks-registered ;;",
        "      'RESULT: '*) verdict_pass hooks-registered ;;",
    ),
    mutate.Mutation(
        "a missing helper passes instead of failing closed — the gate becomes a rubber stamp",
        "    verdict_fail hooks-registered \\\n"
        '      "$helper is missing — the registration check could not run, '
        'which is not the same as passing"',
        "    verdict_pass hooks-registered",
    ),
    mutate.Mutation(
        "the unrecoverable BEFORE digest becomes optional, and is quietly defaulted",
        '  [[ -n "$before_sha" ]] \\',
        '  [[ -n "${before_sha:-x}" ]] \\',
    ),
    mutate.Mutation(
        "an absent runtime settings.json stops being an ERROR",
        '  [[ -f "$scope/$SETTINGS" ]] \\',
        '  [[ -d "$scope" ]] \\',
    ),
    mutate.Mutation(
        "an unknown flag is silently ignored, so a typo'd argument runs the tool's own defaults",
        '      *) fatal "unknown argument: $1" ;;',
        "      *) shift ;;",
    ),
    mutate.Mutation(
        "the verdict decouples from the findings — every run reports PASS",
        '  if [[ "$fail_count" -eq 0 ]]; then\n    result_line PASS 0',
        "  if true; then\n    result_line PASS 0",
    ),
    mutate.Mutation(
        "the adoption predicate is inverted, so an adopted repo skips the check entirely",
        '  if [[ "$adopted" == no ]]; then',
        '  if [[ "$adopted" == yes ]]; then',
    ),
    mutate.Mutation(
        "an armed repo with no refs/heads/main passes, disagreeing with audit.sh about one repo",
        '  elif ! git -C "$scope" rev-parse --quiet --verify refs/heads/main >/dev/null 2>&1; then',
        "  elif false; then",
    ),
    mutate.Mutation(
        "a symlinked destination is judged by its TARGET's bytes, so a dead hook reads as installed",
        '  elif [[ -L "$hook_dest" ]]; then',
        "  elif false; then",
    ),
    mutate.Mutation(
        "the tracked source can be missing and the check still reaches a digest verdict",
        '  elif [[ ! -f "$scope/$TRACKED_HOOK" ]]; then',
        "  elif false; then",
    ),
    mutate.Mutation(
        "a non-executable hook reads as installed, which is the silent fail-open git itself has",
        '  elif [[ ! -x "$hook_dest" ]]; then',
        "  elif false; then",
    ),
    mutate.Mutation(
        "the digest comparison always holds, so a stale boundary hook reads as current",
        '  elif [[ "$(file_sha256 "$hook_dest")" == "$(file_sha256 "$scope/$TRACKED_HOOK")" ]]; then',
        "  elif true; then",
    ),
    mutate.Mutation(
        "a missing hook falls through to the wrong diagnosis, so the operator is told the wrong thing",
        '  elif [[ ! -e "$hook_dest" ]]; then',
        "  elif false; then",
    ),
    mutate.Mutation(
        "a stale hook is diagnosed as foreign, so the operator is not told the remedy that works",
        '  elif hook_is_a_tracked_version "$scope" "$hook_dest"; then',
        "  elif false; then",
    ),
    mutate.Mutation(
        "the farm check runs THIS copy's installer, which grades its own tree, not the promoted one",
        '  farm_installer="$scope/$FARM_INSTALLER"',
        '  farm_installer="$script_dir/../$FARM_INSTALLER"',
    ),
    mutate.Mutation(
        "the farm row is skipped exactly when a farm exists, so the check never runs",
        '  if [[ ! -f "$farm_installer" ]]; then',
        '  if [[ -f "$farm_installer" ]]; then',
    ),
    mutate.Mutation(
        "an absent RESULT: line is read as drift, so a toolchain refusal reads as a broken promote",
        '    if [[ -z "$farm_verdict" ]]; then',
        '    if [[ -z "$farm_verdict" ]] && false; then',
    ),
    mutate.Mutation(
        "the echoed source= stops being asserted, so a verdict about ANOTHER repo clears the row",
        '    elif [[ "$farm_source" != "$scope_phys" ]]; then',
        "    elif false; then",
    ),
    mutate.Mutation(
        "the farm allowlist widens to any RESULT line, so a drift report reads as clean",
        "    elif [[ \"$farm_verdict\" == 'RESULT: PASS'* ]]; then",
        "    elif [[ \"$farm_verdict\" == 'RESULT: '* ]]; then",
    ),
    mutate.Mutation(
        "the installer's drift output is not attached, so the operator is not told WHICH member",
        "      while IFS= read -r line; do\n"
        "        printf '  %s\\n' \"$line\"\n"
        '        case "$line" in\n'
        "          'DRIFT: '*)\n"
        "            printf '    %s\\n' \\\n"
        '              "$(farm_drift_label "$line" "$farm_before" "$farm_undet" "$base")"\n'
        "            ;;\n"
        "        esac\n"
        '      done <<<"$farm_out"',
        "      :",
    ),
    # ---- the drift classification (part 3) ----
    mutate.Mutation(
        "the classification is never attached, so a drift failure reads the same whether this "
        "promote caused it or tripped over one that predates it",
        '        case "$line" in\n'
        "          'DRIFT: '*)\n"
        "            printf '    %s\\n' \\\n"
        '              "$(farm_drift_label "$line" "$farm_before" "$farm_undet" "$base")"\n'
        "            ;;\n"
        "        esac",
        "        :",
    ),
    mutate.Mutation(
        "the membership test is inverted, so every label names the opposite cell",
        '  if grep -qxF -- "$name" <<<"$names"; then',
        '  if ! grep -qxF -- "$name" <<<"$names"; then',
    ),
    mutate.Mutation(
        "membership is matched as a REGEX, so a dotted name matches an unrelated root entry",
        '  if grep -qxF -- "$name" <<<"$names"; then',
        '  if grep -qx -- "$name" <<<"$names"; then',
    ),
    mutate.Mutation(
        "shape detection collapses to one branch, so a live foreign link is read as a missing one",
        "    *' (resolved to '*) shape=resolved ;;",
        "    *' (resolved to '*) shape=missing ;;",
    ),
    mutate.Mutation(
        "an unrecognised DRIFT shape falls through into a cell instead of reporting UNDETERMINED",
        "    *)\n      # An allowlist on the SHAPE:",
        "    *) shape=missing ;;\n"
        "    'never happens')\n"
        "      # An allowlist on the SHAPE:",
    ),
    mutate.Mutation(
        "an UNDETERMINED reason is discarded and a label is produced anyway — the whole preimage "
        "of 'could not measure' silently acquires a positive verdict",
        '  if [[ -n "$reason" ]]; then',
        "  if false; then",
    ),
    mutate.Mutation(
        "the install.sh-changed guard is removed, so a moved EXCLUDE list still yields a label",
        '      elif [[ -n "$farm_diff" ]]; then',
        "      elif false; then",
    ),
    mutate.Mutation(
        "a pre-merge HEAD the range row REFUSED still decides labels here",
        '      if [[ "$base_ok" != yes ]]; then',
        "      if false; then",
    ),
    mutate.Mutation(
        "an unlistable before-tree is read as an EMPTY one, so every drifting member reads as new",
        '      elif ! farm_before="$(git -C "$scope" ls-tree --name-only "$base" 2>/dev/null)"; then',
        '      elif farm_before="$(git -C "$scope" ls-tree --name-only "$base" 2>/dev/null)"'
        " && false; then",
    ),
    mutate.Mutation(
        "an uncomputable install.sh diff reads as 'unchanged', so the guard silently stops firing",
        '      elif ! farm_diff="$(git -C "$scope" diff --name-only "$base" "$farm_after" '
        '-- "$FARM_INSTALLER" 2>/dev/null)"; then',
        '      elif farm_diff="$(git -C "$scope" diff --name-only "$base" "$farm_after" '
        '-- "$FARM_INSTALLER" 2>/dev/null)" && false; then',
    ),
    mutate.Mutation(
        "base_ok is set unconditionally, so the range row's refusal never reaches the classifier",
        "  base_ok=no",
        "  base_ok=yes",
    ),
    # The three below revert ONE axis each of the range match and keep the others, so a survivor
    # names exactly which axis is untested. A mutant reverting more than one would not distinguish
    # them. Note the -F and -x mutants produce the SAME three-row failure signature, so the suite
    # catches both but cannot say which broke -- these labels are what does that job.
    mutate.Mutation(
        "the range match goes back through a pipe, so a large incoming range inverts its verdict",
        '    if grep -qxF -- "$SETTINGS" <<<"$changed"; then',
        '    if printf \'%s\\n\' "$changed" | grep -qxF -- "$SETTINGS"; then',
    ),
    mutate.Mutation(
        "the range match drops -F, so a path resembling settings.json silently skips byte-identity",
        '    if grep -qxF -- "$SETTINGS" <<<"$changed"; then',
        '    if grep -qx -- "$SETTINGS" <<<"$changed"; then',
    ),
    # The third axis, and the one the first draft of this change left unpinned: -x. Without it the
    # match is a SUBSTRING one, so a NESTED settings.json selects the in-range arm and suppresses
    # byte-identity -- the same silent direction as dropping -F, which is why it needs its own
    # mutant rather than being assumed covered by the two above.
    mutate.Mutation(
        "the range match drops -x, so a NESTED settings.json silently skips byte-identity",
        '    if grep -qxF -- "$SETTINGS" <<<"$changed"; then',
        '    if grep -qF -- "$SETTINGS" <<<"$changed"; then',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
