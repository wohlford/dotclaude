"""Plan excerpts for test_plan_rehearse.py, copied byte-for-byte from real plans.

**Why this exists.** The live corpus rows read `plans/`, which is gitignored, so in a fresh
clone they SKIP while everything else passes. These excerpts are committed, so shape coverage
never depends on content the repo does not carry.

**Every excerpt below is VERBATIM** — the byte range named in its comment, located by SEARCH
rather than by a remembered line number (four of the previous version's line numbers were off by
1-4). An earlier version made this same claim while 7 of 9 excerpts were paraphrased, and two had
been trimmed in the direction that made them PASS the escape gate. That is the exact failure this
file exists to prevent: a corpus reflecting the author's idea of a plan rather than a plan.

**Where verbatim and publishable conflicted, the excerpt was SELECTED, never edited.** Most real
bash fences in this repo's plans begin by `cd`-ing to an absolute home path, and this fixture is
tracked in a repo that publishes — it would have been the only tracked file carrying one. So
`FENCE_BASH` is a different real fence, not a cleaned-up version of that one. Editing to fit is
what produced the defect above; choosing a different true example does not.

Several excerpts are therefore REFUSED by `escape_reason`, and that is data rather than a
defect: these rows assert what `extract` makes of the text, not what the gate decides.

**This does NOT replace the live rows**, which anchor the shape set to the whole corpus
wherever the working tree carries one.
"""

# plans/2026-09-11-reserved-word-cd.md:132-136 — extracts as 'run-line'
RUN_LINE = """\
- [ ] **Step 3: Run and watch it fail for the RIGHT reason**

Run: `python3 -m pytest scripts/tests/test_git_command.py -k "unresolvable or non_boundary" -v`

Expected: **30 failures** in `test_a_cd_after_a_reserved_word_is_unresolvable`, one per
"""

# plans/2026-09-17-bulk-edit-kit.md:1295-1296 — extracts as 'run-line'
RUN_LINE_WITH_ASIDE = """\
Run: `stat -c %a scripts/lib/bulk_edit.py` (GNU `stat` is first on PATH here; `-f %Lp` is BSD)
Expected: `644`.
"""

# plans/2026-07-04-exec-bit-guard.md:112-112 — extracts as 'inline-run-arrow'
INLINE_RUN_ARROW = """\
Run: `bash scripts/tests/test_exec_bit_integrity.sh` → Expected: `0 failed`, exit 0.
"""

# plans/2026-07-26-commit-integrity.md:1531-1534 — extracts as 'fence'
FENCE_BASH = """\
```bash
python3 -m json.tool settings.json >/dev/null && echo "settings.json valid"
```
Expected: `settings.json valid`. A malformed `settings.json` would break every hook at once.
"""

# plans/2026-07-24-propagate-production-branch-guard.md:78-84 — extracts as 'fence'
FENCE_PYTHON = """\
```python
p = "skills/propagate/SKILL.md"; t = open(p).read()
a = '5. **Fast-forward production (never force).** Fetch from `src` and `--ff-only` merge:'
print("count:", t.count(a))
```

Expected: `count: 1`. If `0`, the file already changed — stop and re-read step 5 before editing.
"""

# plans/2026-09-11-reserved-word-cd.md:291-293 — extracts as 'bare-backtick'
BARE_BACKTICK = """\
`grep -rn "scoped to ._git_starts_command. only" scripts/`

Expected: no output from any. Sites 3-6 live in the test files and are corrected by Tasks 1 and 3,
"""

# plans/2026-08-07-fixture-signing-hang.md:98-100 — extracts as 'trailing-backtick'
TRAILING_BACKTICK = """\
- [ ] **Step 4: Run the suite** — `bash scripts/tests/test_pre_push_hook.sh`

Expected: the suite's own summary line reports zero failures. Read the summary; "no FAIL in the output" is not a pass.
"""

# plans/2026-08-07-fixture-signing-hang.md:94-96 — extracts as 'unpaired'
UNPAIRED_HEADING_DESCRIPTION = """\
- [ ] **Step 3: Re-run the probe with `git -C "$SB/r" config tag.gpgsign false` added**

Expected: `commit.gpgsign=false tag.gpgsign=false`
"""

# plans/2026-09-14-reserved-word-argument-position.md:659-661 — extracts as 'unpaired'
UNPAIRED_PROSE = """\
the campaign owns that file and restores its own snapshot.

Expected: `FINAL: PASS`, 5/5 CAUGHT, `row_check_problems=0`, every `row-check … FOUND`. Afterwards
"""

# Shape -> the excerpt that exercises it. Every member of the module's SHAPES must appear.
BY_SHAPE = {
    "bare-backtick": BARE_BACKTICK,
    "fence": FENCE_BASH,
    "inline-run-arrow": INLINE_RUN_ARROW,
    "run-line": RUN_LINE,
    "trailing-backtick": TRAILING_BACKTICK,
    "unpaired": UNPAIRED_HEADING_DESCRIPTION,
}

EXTRA = {
    "run-line-with-aside": RUN_LINE_WITH_ASIDE,
    "fence-python": FENCE_PYTHON,
    "unpaired-prose": UNPAIRED_PROSE,
}

# SYNTHESIZED, not from a plan: no committed plan pairs an Expected: to a non-shell,
# non-python fence. Kept because the branch must fail closed — such a fence is file
# CONTENT, and running it would execute a config as a script. Marked so nobody cites
# the corpus as evidence that this shape occurs in practice.
FENCE_UNKNOWN_LANGUAGE = """\
```yaml
key: value
```

Expected: the config parses.
"""

BY_SHAPE["fence-unknown-language"] = FENCE_UNKNOWN_LANGUAGE
SYNTHESIZED = frozenset({"fence-unknown-language"})
