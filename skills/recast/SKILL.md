---
name: recast
description: Re-develop a git source repo into a target as a genuine ground-up, proven-per-commit history converging to functional equivalence (never copies the tree, never pushes)
disable-model-invocation: true
---

# /recast — Ground-Up Re-Development of a Repo

Re-develop a **source** repo's functionality into a **target** repo as a genuine, ground-up git
history — every functional commit a runnable increment **proven before it lands**, converging to
**functional equivalence** with the source. The source (frozen at a ref) is the *specification of
the destination*, **not a tree to copy**. User-invoked only; **never auto-pushes**.

This is **complementary to `/propagate`**, not a replacement: `/propagate` fast-forward-mirrors a
**shared** history; `/recast` founds & grows a **divorced** history (ground-up rebuild + scrub).
**Update path:** if the source advances after the target's recast history has been **published
(pushed)**, a full recast is refused — re-run `/recast` in **resume mode** to append the new
capabilities incrementally (or re-found a fresh target); a completed-but-local target is still
`UNPUBLISHED` and may be fully recast.

## Instructions

The caller wants the source's functionality rebuilt in the target as a clean, walkable
`v0.0.1`→… history where each commit is demonstrated, forward-reference-free, and free of
AI/provenance traces. Drive the interactive procedure below; the bundled helpers do the
mechanical, testable parts and are **read-only** — all mutation (author/commit/tag) is yours.

### Arguments

Required:
- `--target <path>` — the repo to build into.
- `--verify <command>` — the per-brick proof (see Process step 4.2, **Prove**; a *brick* — one
  proven runnable increment — is defined in Process step 3). If the caller genuinely has no
  check, they pass `--verify true` explicitly; each such brick is committed and logged
  **"declared, not proven"** (the skill never claims "proven" without a real verify). There is **no
  implicit test discovery**.

Optional (with defaults):
- `--source <path>` — default cwd. **Must be a git repo** — extraction is `git show <ref>:<path>`.
  If `--source` has no `.git`, **fail fast** with a clear error (non-git sources are out of scope).
- `--ref <tag|commit|branch>` — default `HEAD`; **frozen for the whole run** (the oracle for final
  behaviour, never lifted mid-build).
- `--strip <glob>…` — opt-in removals (**keep-all default**); confirm before applying.
- `--redact <file>` — caller-owned, **gitignored** pattern file (one regex per line; blank lines and
  `#` comments ignored). Never inline or echo patterns.
- `--templates <dir>` — source of *standard* files (CONTRIBUTING, license) — taken from here, **not**
  the source.
- `--no-tag` — opt out of per-brick tagging (**tagging is ON by default**; every commit gets an
  annotated semver tag; bump `feat`→minor, `!`→major, else patch — but **before v1.0.0 a breaking
  `!`→minor**, per CONTRIBUTING's 0.x rule, which applies to nearly every recast).
- `--fresh-every <N>` — from-scratch verify cadence (default: each subsystem boundary).
- `--keep-code` — keep tool-**name** mentions (Claude/Anthropic/GEMINI) in the recast while **still
  scrubbing** the generation-trace "marketing" (credits, footers, `🤖`); recons `--traces-only`. For a
  source that *legitimately discusses* these tools, not one attributing authorship to them. Identity
  fields (commit author/committer, tagger) are **always** scrubbed comprehensively — `--keep-code`
  keeps names only in **textual** surfaces (contents, file/dir names, commit messages, tag bodies).
  **Mutually exclusive with `--no-scrub`.**
- `--no-scrub` — **explicit opt-in (default is scrub).** Reproduce content **verbatim**: AI/tool
  provenance, secrets, and commit trailers are all KEPT, and `--redact` is **not** removed. The
  provenance/leak sweep still runs but **reports only** (`path:line`, never content) and does not
  halt — **except** that secret-pattern (`--redact`) hits require a one-time explicit confirmation
  before proceeding. Correctness gates (`--verify`, linters, no-forward-reference) **still apply**.
  No env var or config can enable this — the default path stays fully safe.

### Process

**1. Inspect target state** — run `skills/recast/recast-state.sh <target>` and branch on it:

| State | Action |
|---|---|
| `EMPTY` | Fresh ground-up build. |
| `DETACHED` | **Abort** — ask the caller to check out a branch. |
| `UNPUBLISHED` | **Halt and ask**: resume (append on top), full recast (rewrite — valid **only** here), or abort. Never wipe silently. |
| `PUBLISHED` | **Append + resume only.** Refuse full recast — never rewrite a published frontier. |

**Resume mechanism (no state file — the commit history is the ledger):** the approved brick-plan
(see step 3) is an ordered list of capabilities, each mapped to its planned conventional-commit subject. On resume,
read `git -C <target> log --oneline` and continue from the **first plan entry whose subject has no
matching commit**; earlier bricks are kept untouched. **Mode-mismatch guard:** the scrub mode
(default / `--keep-code` / `--no-scrub`) is not persisted, so on resume recon the already-committed
bricks — if their provenance state disagrees with this run's mode (a comprehensively-scrubbed history
resumed under `--keep-code` or `--no-scrub`, a names-kept history resumed under the strict default, or
a verbatim history resumed scrubbing), warn prominently and confirm before continuing — never silently
mix keep-levels across bricks.

**2. Freeze + recon the source.** Pin `--ref`. Run `skills/recast/recast-recon.sh [--traces-only]
<source-tree> [<redact-file>]` (the `--redact` file is passed **positionally**). The **default sweep
is comprehensive** — generation **traces** (`Co-Authored-By`, "generated with", `🤖`) *and*
tool-**name** mentions (Claude/Anthropic/GEMINI) — so the recast reveals no tell that AI was used.
Bare names over-match (`.claude`, model ids, AI-topic prose): if the source **legitimately discusses**
these tools rather than attributing authorship, run `/recast --keep-code`, which recons `--traces-only`
(keeps the mentions, still scrubs the marketing traces). Names cover Claude/Anthropic/GEMINI only —
**other assistants (Copilot/GPT/Cursor/…) and non-Anthropic model ids need a `--redact` file.** The
sweep now also flags file/dir **names** (a `claude-config.md` file or `.claude/` dir is itself a
tell), reporting `path:line` / `path:name` only (never content). **Default:** halt on hits —
confirm clean before building. **`--no-scrub`:** emit the report as a banner + a one-line summary
("N provenance + M secret-pattern hits carried verbatim") and continue — but if there are any
secret-pattern (`--redact`) hits, require a one-time `type YES` confirmation first (provenance is
reported; secrets are gated). Under `--no-scrub`, `--redact` still **drives** the report but is not
removed — warn that its patterns are reported, not stripped (and are matched case-sensitively).

**3. Classify + order.** Strip globs are evaluated here: paths matching a `--strip` glob are
proposed for omission from the brick plan, and the caller confirms each one before it is excluded.
For each in-scope path, run the classification checklist — *(1) modified by
>1 brick? (2) meaningful partial state? (3) imported/sourced by other in-scope files?* — yes to (1)
or (2) → **evolving** (authored fresh, thin→grown); all no → **atomic** (reproduced from the frozen
source at the brick where built, via `git show <ref>:<path>`); conflicting answers → **AMBIGUOUS**,
surface to the caller. Then build a **dependency-ordered capability-slice brick plan**: each brick is
"the next smallest thing that runs end-to-end," subsystems built **when reached** (never early);
declare inter-subsystem dependencies and **surface any cycles for manual linearization** (the order
assumes a DAG). The caller approves and may override any classification.

**4. Per brick — author → prove → commit:**
1. **Author.** Evolving files fresh (thin→grown); atomic files extracted at this brick. **Adapt to
   the target's conventions** (indentation, hooks, lint — functional equivalence keeps behaviour
   identical); surface genuine **history-wide** convention conflicts to the caller before committing.
2. **Prove** with `--verify` (provisioning-like → apply on a real VM/container, second run reports
   no changes = idempotent; library/tooling → run the tool + its tests **on the runtime that
   component declares** — its own version-pin file (`.tool-versions`, `package.json` engines,
   `pyproject.toml`, shebang), never the caller's ambient environment; other component shapes →
   the most direct functional check the brick supports). **Flaky verify:** on
   failure, re-run **once**; if it fails twice, halt.
3. **Gate** (the history is the artifact): `--verify` passed; linters clean; **no forward references**;
   and — **default** — the provenance + leak sweep (`recast-recon.sh` on the delta, **passing the same
   `--redact` file**; **comprehensive** — traces + names — unless `--keep-code`, which recons
   `--traces-only` to keep the mentions) clean (halt on hits). **`--no-scrub`:** that sweep is
   report-only here too (provenance reported; secret-pattern
   hits already consented at step 2) — the `--verify`/lint/forward-ref gates are **unchanged**.
   - **No-forward-ref check:** after staging, `grep` the committed tree for references to
     files/paths/identifiers **not present in the current HEAD tree** (`git ls-files`); any hit →
     halt (a brick must not name what isn't built yet). The **seed commit is exempt** — it is the
     docs/config seed, runs nothing, and states the project's *intent*, not an inventory.
4. **Commit** with a conventional-commit subject + an **annotated semver tag** (default on;
   `--no-tag` to skip) + a **living CHANGELOG entry** (append `[declared, not proven]` when the brick
   used `--verify true`). **Never via `/commit`** — recast owns this discipline itself: brick versions
   come from the approved brick-plan rather than being derived from the message type, entries carry the
   `[declared, not proven]` suffix, and the target is a *different* repo (commit with
   `git -C <target>`; `/commit` acts on the current one). `/commit`'s SKILL.md carves this out
   reciprocally. Any date in that CHANGELOG entry is **date-only** (`YYYY-MM-DD`), taken from the brick's
   own commit rather than an independent clock call — so every entry is reproducible from the
   history alone and carries no time-of-day noise. Grow docs to describe only what now works, and **regenerate any
   generated index/manifest this brick affects — the target's `<!-- sync:* -->` tables, a plugin
   manifest, an autoloader list — in this same commit** (a brick that adds a capability must register
   it; never batch index regeneration for the end, or every intermediate commit carries a drifted
   index). Pull standard files from `--templates` (if not given, author standard files from scratch).
   Author the commit and tag under the human's git identity — **no** `Co-Authored-By`, generation
   footer, `🤖`, or AI author. **Immediately after the commit and tag land,** run the metadata gate
   `skills/recast/recast-recon-history.sh <target> HEAD~1..HEAD` (seed/root brick has no parent →
   `recast-recon-history.sh <target> HEAD`), same `--redact` file and `--keep-code`→`--traces-only`
   mapping — it sweeps the new commit's message, its tag body, and author/committer/tagger identity.
   **Default:** halt on a hit (identity comprehensive even under `--keep-code`; message/tag text
   honors it) — amend the just-landed commit/tag to scrub the text or correct the identity, then
   re-run the gate before proceeding (recast never pushes, so the offending commit is still local
   and safe to amend); **`--no-scrub`:** report-only.
5. **From-scratch verify** at the seed, every `--fresh-every` bricks (default each subsystem
   boundary), on any **high-risk** brick (touches shared state / adds an external dependency /
   cumulative run reports unexpected changes), and at the end — via
   `skills/recast/recast-contract.sh`, which archives each selected commit to an isolated dir and
   runs `--verify` there (checkpoints may scope with `--range <last-checkpoint>..HEAD`; the final
   sweep belongs to the top-level **Final audit** step). A late from-scratch failure is fixed by re-bricking from the offending
   commit.

**5. Final audit.** Full per-commit proof: `skills/recast/recast-contract.sh --tags <target>
'<verify-cmd>'` — every brick's tagged tree is archived and must pass `--verify` (under `--no-tag`,
use `--range <tip>`, which sweeps every commit including the root; zero selected commits is a loud
error, never a pass). Then `skills/recast/recast-verify.sh <target> <source>
<ref> [deviation-file]` for the **structure** check (functional equivalence, **not** a byte diff);
**full-history** provenance + leak sweep (every tree, not just the tip — a trace can appear in an
intermediate commit and be deleted later): `skills/recast/recast-contract.sh --range HEAD <target>
'<abs-path>/recast-recon.sh [--traces-only] . [<abs-redact-file>]'` — contract archives every
commit (a single ref as `--range` sweeps the whole history including the root) and runs recon in
each archived tree (cwd is that tree, so recon and the redact file need **absolute** paths; recon's
exit 1 on a hit fails that commit) — **plus `skills/recast/recast-recon-history.sh <target>`
over the full history** (commit messages, tag annotations, and author/committer/tagger identity),
**passing the same `--redact` file**. **Default:** comprehensive (traces + names) — must be **clean**
(halt) so no tree *or* commit betrays AI involvement. Under `--keep-code`, recon `--traces-only`
(names intentionally kept in textual surfaces; author/committer/tagger identity is still swept
comprehensively); under `--no-scrub`, the whole sweep is report-only. Report. **Do not push.**

### Rules

- **Never rewrite published history**; full recast only when nothing is published.
- **Never wipe a non-empty branch** without explicit caller confirmation.
- **Never auto-push.**
- Default **keep-all / redact-none**; confirm strips; redaction patterns only from a gitignored file
  (never inline).
- **`--no-scrub` is explicit-only and never the default** (no env/config). Under it, content is
  verbatim and the provenance/leak sweep is report-only — but secret-pattern (`--redact`) hits still
  require explicit confirmation, and the correctness gates always apply.
- **`--keep-code` and `--no-scrub` are mutually exclusive** — refuse the combination (contradictory
  keep-levels: `--keep-code` still scrubs the marketing traces, `--no-scrub` keeps everything).
- **Target commits carry no AI provenance.** No `Co-Authored-By`, generation footer, `🤖`, or AI
  identity in any brick's commit message; author **and** tagger identity is the human's configured git
  identity (never an AI identity, never hardcoded). The driving agent is the likeliest source — the
  metadata gate (steps 4/5, `recast-recon-history.sh`) halts on a hit.
- **Tag every brick by default** (`--no-tag` opts out); semver bump `feat`→minor, `!`→major, else
  patch — with the **0.x rule** (before v1.0.0, `!`→minor; see CONTRIBUTING).
- **Byte-identity is not a goal — functional equivalence is the gate.** The reconstruction has its
  own semver line.
- `--verify true` commits are logged "declared, not proven" — never claim "proven" without a real
  check.
