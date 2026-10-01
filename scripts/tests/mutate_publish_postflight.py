#!/usr/bin/env python3
# Script: mutate_publish_postflight.py
# Purpose: Mutation campaign for scripts/publish-postflight.sh, driven by scripts/lib/mutate.py
# Usage: ./scripts/tests/mutate_publish_postflight.py [--only N]   (env KILLSETS=<file> records killed rows)
"""Mutation campaign for scripts/publish-postflight.sh, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand, never while editing the subject (the restore would clobber your edits), and
through `scripts/run-long.sh` — it runs the whole suite once per mutant, which outruns a
foreground call. The repo root is derived from __file__: run it from the working copy you intend
to grade, not through a symlinked `~/.claude/scripts/tests/`.

The subject is a CHECK that grades the END of an irreversible publish, so each mutant below is one
safety property lost — the shape in which a verified publish is reported over a dead gate.

`--only N` (1-based) runs the baseline plus that one mutant, to prove the harness end to end.

KILL SETS. A mutant that kills exactly the rows another kills measures nothing, and
mutate.py reports only caught/survived, never WHICH rows failed. Set `KILLSETS=<file>` (a path
outside the repo) and this campaign wraps the suite so every `FAIL` line it prints is appended to
that file, then, as each mutant resolves, prints the rows it killed and truncates the file. Compare
the printed sets across mutants; identical sets are duplicates to differentiate or drop. Without
KILLSETS nothing extra is written anywhere.

Properties the single-replacement form cannot isolate (two guards cover one failure, so removing
either alone leaves the verdict FAIL and only its reason text moves): a non-zero ls-remote rc
(also FAILs via the empty-output guard), a missing local main (also FAILs via the sha mismatch).
Those mutants survive unless the suite asserts the reason text; a survivor there is the finding.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "publish-postflight.sh"
_SUITE_FILE = str(REPO / "scripts" / "tests" / "test_publish_postflight.sh")
# Plain suite unless KILLSETS is set; the wrapper keeps the suite's stdout and exit status intact.
_WRAPPER = (
    'out="$(bash "$1" 2>&1)"; rc=$?; printf "%s\\n" "$out"; '
    'if [[ -n "${KILLSETS:-}" ]]; then grep "^FAIL" <<<"$out" >>"$KILLSETS"; fi; exit "$rc"'
)
if os.environ.get("KILLSETS"):
    SUITE = ["bash", "-c", _WRAPPER, "_", _SUITE_FILE]
else:
    SUITE = ["bash", _SUITE_FILE]

MUTATIONS = [
    mutate.Mutation(
        "remote-read treats a non-zero ls-remote rc as success",
        '2>"${errfile:-/dev/null}")"; rc=$?\n',
        '2>"${errfile:-/dev/null}")"; rc=0\n',
    ),
    mutate.Mutation(
        "the empty-output guard is removed, so an empty origin passes remote-read",
        '  elif [[ -z "$out" ]]; then',
        "  elif false; then",
    ),
    mutate.Mutation(
        "the remote-refs allowlist becomes a blocklist flagging only dev and published refs",
        "        *) unexpected=\"${unexpected:+$unexpected$'\\n'}$ref\" ;;",
        "        refs/heads/dev|refs/published/*) unexpected=\"${unexpected:+$unexpected$'\\n'}$ref\" ;;\n"
        "        *) ;;",
    ),
    mutate.Mutation(
        "tag parity compares counts, not sets",
        '        elif [[ -z "$only_o" && -z "$only_l" ]]; then',
        '        elif [[ "$(grep -c . <<<"$set_o")" == "$(grep -c . <<<"$set_l")" ]]; then',
    ),
    mutate.Mutation(
        "the only-local half of tag parity (comm -13) is dropped",
        '        elif [[ -z "$only_o" && -z "$only_l" ]]; then',
        '        elif [[ -z "$only_o" ]]; then',
    ),
    mutate.Mutation(
        "the non-empty floor is removed, so two empty tag sets pass",
        '      elif [[ -z "$tags_origin" && -z "$local_tags" ]]; then',
        "      elif false; then",
    ),
    mutate.Mutation(
        "the origin-url fetch-vs-push comparison is removed",
        '  elif [[ "$url" == "$push_url" ]]; then',
        "  elif true; then",
    ),
    mutate.Mutation(
        "LC_ALL=C is dropped from the origin-side sort (an en_US collation stands in)",
        '          set_o="$(LC_ALL=C sort <<<"$tags_origin")"; rc=$?',
        '          set_o="$(LC_ALL=en_US.UTF-8 sort <<<"$tags_origin")"; rc=$?',
    ),
    mutate.Mutation(
        "the CHANGELOG.md exclusion is removed from watermark-tree",
        "\"$PUBLISHED_REF\" -- . ':(exclude)CHANGELOG.md' 2>/dev/null",
        '"$PUBLISHED_REF" -- . 2>/dev/null',
    ),
    mutate.Mutation(
        "watermark-current inverts its zero test, passing when commits trail the watermark",
        '        elif [[ "$count" -eq 0 ]]; then',
        '        elif [[ "$count" -ne 0 ]]; then',
    ),
    mutate.Mutation(
        "a git fetch origin is added, breaking the read-only guarantee",
        "  phase=checking\n",
        '  phase=checking\n  git -C "$scope" fetch origin >/dev/null 2>&1\n',
    ),
    mutate.Mutation(
        "the ^{} peel filter is removed, so peeled tag lines pollute the origin tag set",
        "        refs/tags/*'^{}') ;;\n",
        "",
    ),
    mutate.Mutation(
        "the missing-local-main guard in main-sync is removed",
        '    if [[ -z "$local_main" ]]; then\n      verdict_fail main-sync',
        "    if false; then\n      verdict_fail main-sync",
    ),
    mutate.Mutation(
        "the refs/heads/main exact match becomes a glob, so a lookalike branch is taken as main",
        '        "$PUBLISHED_REF") origin_main="$sha" ;;',
        '        refs/heads/main*) origin_main="$sha" ;;',
    ),
    mutate.Mutation(
        "watermark-current loses its rc/empty-count guard (naive -eq 0)",
        '    case "$count" in\n'
        "      ''|*[!0-9]*) verdict_fail watermark-current "
        '"could not count $WATERMARK_REF..$WORKING_REF (rc=$rc, output [$count])" ;;\n'
        "      *)\n"
        '        if [[ "$rc" -ne 0 ]]; then\n'
        '          verdict_fail watermark-current "counting $WATERMARK_REF..$WORKING_REF exited $rc"\n'
        '        elif [[ "$count" -eq 0 ]]; then',
        '    case "$count" in\n      *)\n        if [[ "$count" -eq 0 ]]; then',
    ),
    mutate.Mutation(
        "the local main rev-parse is spelled `main`, so a tag named main wins",
        '--verify -q "$PUBLISHED_REF"',
        "--verify -q main",
    ),
    mutate.Mutation(
        "the local tag listing drops --merged refs/heads/main, so unmerged tags count",
        'for-each-ref --merged "$PUBLISHED_REF" refs/tags',
        "for-each-ref refs/tags",
    ),
    mutate.Mutation(
        "main-sync compares ancestry instead of equality, so an origin behind local passes",
        '    elif [[ "$local_main" == "$origin_main" ]]; then',
        '    elif git -C "$scope" merge-base --is-ancestor "$origin_main" "$local_main" 2>/dev/null; then',
    ),
    mutate.Mutation(
        "both sort rc/empty guards are removed, so a failing sort reads as two empty sets",
        '          if [[ "$rc" -ne 0 ]]; then tool_bad="sort of origin\'s tags exited $rc"\n'
        '          elif [[ -z "$set_o" ]]; then tool_bad="sort of origin\'s tags printed nothing for a non-empty input"; fi\n'
        "        fi\n"
        '        if [[ -z "$tool_bad" && -n "$local_tags" ]]; then\n'
        '          set_l="$(LC_ALL=C sort <<<"$local_tags")"; rc=$?\n'
        '          if [[ "$rc" -ne 0 ]]; then tool_bad="sort of the local tags exited $rc"\n'
        '          elif [[ -z "$set_l" ]]; then tool_bad="sort of the local tags printed nothing for a non-empty input"; fi\n',
        "        fi\n"
        '        if [[ -z "$tool_bad" && -n "$local_tags" ]]; then\n'
        '          set_l="$(LC_ALL=C sort <<<"$local_tags")"\n',
    ),
    mutate.Mutation(
        "both comm rc guards are removed, so a failing comm reads as no one-sided tags",
        '          only_o="$(LC_ALL=C comm -23 <(if [[ -n "$set_o" ]]; then printf \'%s\\n\' "$set_o"; fi) \\\n'
        '                                       <(if [[ -n "$set_l" ]]; then printf \'%s\\n\' "$set_l"; fi))"; rc=$?\n'
        '          if [[ "$rc" -ne 0 ]]; then tool_bad="comm -23 exited $rc"; fi\n'
        "        fi\n"
        '        if [[ -z "$tool_bad" ]]; then\n'
        '          only_l="$(LC_ALL=C comm -13 <(if [[ -n "$set_o" ]]; then printf \'%s\\n\' "$set_o"; fi) \\\n'
        '                                       <(if [[ -n "$set_l" ]]; then printf \'%s\\n\' "$set_l"; fi))"; rc=$?\n'
        '          if [[ "$rc" -ne 0 ]]; then tool_bad="comm -13 exited $rc"; fi\n',
        '          only_o="$(LC_ALL=C comm -23 <(if [[ -n "$set_o" ]]; then printf \'%s\\n\' "$set_o"; fi) \\\n'
        '                                       <(if [[ -n "$set_l" ]]; then printf \'%s\\n\' "$set_l"; fi))"\n'
        "        fi\n"
        '        if [[ -z "$tool_bad" ]]; then\n'
        '          only_l="$(LC_ALL=C comm -13 <(if [[ -n "$set_o" ]]; then printf \'%s\\n\' "$set_o"; fi) \\\n'
        '                                       <(if [[ -n "$set_l" ]]; then printf \'%s\\n\' "$set_l"; fi))"\n',
    ),
    mutate.Mutation(
        "the HEAD-sha guard is removed, so a HEAD detached at a dev commit is allowed",
        '    if [[ -n "$head_sha" && "$head_sha" != "$origin_main" ]]; then',
        "    if false; then",
    ),
    mutate.Mutation(
        "origin-url reads a single push url, so a second push url is invisible",
        "remote get-url --push --all origin",
        "remote get-url --push origin",
    ),
]


def _progress_factory():
    """Stream mutate's lines; under KILLSETS also print and reset the rows the last mutant killed."""
    killsets = os.environ.get("KILLSETS")

    def progress(line: str) -> None:
        print(line, flush=True)
        if killsets and re.match(r"^\[\d+/\d+\]", line):
            p = Path(killsets)
            rows = sorted(set(p.read_text().splitlines())) if p.exists() else []
            print(
                "          killed rows: " + (" | ".join(rows) if rows else "(none)"),
                flush=True,
            )
            p.write_text("")

    return progress


def main(argv: list[str]) -> int:
    mutations = MUTATIONS
    if argv[:1] == ["--only"] and len(argv) == 2 and argv[1].isdigit():
        n = int(argv[1])
        if not 1 <= n <= len(MUTATIONS):
            print(f"--only: N must be 1..{len(MUTATIONS)}", file=sys.stderr)
            return 2
        mutations = [MUTATIONS[n - 1]]
    elif argv:
        print("usage: mutate_publish_postflight.py [--only N]", file=sys.stderr)
        return 2
    if os.environ.get("KILLSETS"):
        Path(os.environ["KILLSETS"]).write_text("")
    report = mutate.run(
        SUBJECT, SUITE, mutations, cwd=str(REPO), progress=_progress_factory()
    )
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
