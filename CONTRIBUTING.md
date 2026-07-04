# Contributing

Thank you for contributing. This guide covers two practices that keep a project's history clean and its releases predictable: **semantic commit messages** and **semantic versioning**. Both are mechanical once learned, and both pay off whenever you — or a tool — need to read history, generate a changelog, or cut a release.

## Semantic commit messages

Every commit message is a single line in this form:

```text
<type>[(scope)][!]: <subject>
```

- **type** — one of the categories below. Required.
- **scope** — the area of the codebase affected (`auth`, `parser`, `build`). Lowercase. Optional but encouraged.
- **!** — placed immediately before the colon to flag a breaking change. Required whenever the change breaks backward compatibility; absent otherwise.
- **subject** — a brief description of the change, in the imperative mood.

### Types

| Type | Use for |
|------|---------|
| `feat` | A new feature or capability |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `style` | Formatting that doesn't change behavior (whitespace, semicolons) |
| `refactor` | A code change that neither fixes a bug nor adds a feature |
| `perf` | A change that improves performance |
| `test` | Adding or correcting tests |
| `chore` | Routine maintenance: dependencies, tooling, housekeeping |
| `ci` | Continuous-integration configuration |
| `revert` | Reverting a previous commit |

For Markdown and other text files, pick the type by how the file is *used*, not by its extension:

- `docs` — human-facing project documentation (README, CONTRIBUTING, setup guides).
- `feat` — files consumed as configuration or runtime logic (agent and skill definitions, prompt templates, scoring rubrics).

### Format rules

- **One line.** No body, no footer.
- **Lowercase** throughout — type, scope, and subject — except proper nouns, acronyms, and code identifiers (`OAuth2`, `API`, `Windows`).
- **Imperative mood** in the subject — "add", not "added" or "adds". Read it as "this commit will *add user auth*".
- **No trailing period.**
- **Under 72 characters** for the whole line, type and scope included.

### Examples

```text
feat: add user authentication system
feat(auth): add OAuth2 support
fix: handle null values in data parser
perf(parser): cache compiled regexes
refactor(parser): extract validation into a module
style: align struct field tags
docs: clarify install steps for Windows
test(auth): cover the expired-token path
ci: run the linter on pull requests
revert: revert "feat: add experimental cache" (a1b2c3d)
feat!: remove legacy v1 API endpoints
chore(build)!: drop support for Node 6
```

## Semantic versioning

Releases follow [Semantic Versioning 2.0.0](https://semver.org/). A version is `MAJOR.MINOR.PATCH`, and each release is recorded as an annotated git tag (`v1.2.3`).

| Bump | When |
|------|------|
| **MAJOR** | A breaking `!` commit — **but see the 0.x rule below** |
| **MINOR** | A `feat` commit (new, backward-compatible functionality) |
| **PATCH** | Every other type (`fix`, `perf`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `revert`) |

**Before v1.0.0 (the `0.x` line):** a breaking `!` bumps **MINOR**, not MAJOR — per SemVer, "anything MAY change" in `0.x`, so `v0.4.0` + `feat!` → `v0.5.0`. Reaching **1.0.0 is a deliberate decision**, made with an explicit tag, never an automatic side effect of the first breaking change. (The `/commit` skill applies this rule.)

When several logical changes land together, the highest-ranked change sets the bump: a single `feat!` outranks any number of patches.

Because the types map directly onto these rules, the commit history is usually enough to determine the next version — by hand or with a tool that reads the log. Reverts and other edge cases still warrant a human glance before tagging.

## Changelog

`CHANGELOG.md` keeps a **living entry per release**, newest-first, each mirroring that release's annotated tag (the same `<type>(scope): subject`). The entry lands in the **same commit** as the change it describes — a release and its changelog line together, never batched at the end. Where a `/commit`-style skill is used, it adds the entry automatically.

The annotated tags remain the canonical, full-granularity history; the inline changelog is the human-readable digest.

## Branches and merges

Feature work goes on a `<type>/<kebab-name>` branch (`feat/oauth-support`, `fix/null-parse`) and merges back when done. A merge commit — when one is created — is the one exception to the single-line semantic format: use `Merge <branch>: <summary>`. It never sets the version bump; the bump comes from the commits it brings in.

## Never commit

Never commit: secrets (`.env`, `*.key`, `*.pem`), dependencies (`node_modules/`, `.venv/`),
generated files (`.pyc`, `dist/`), OS files (`.DS_Store`), logs, or temp files.

## Principles

Three habits make the conventions above worth the effort:

1. **One logical change per commit.** Unrelated changes belong in separate commits — it keeps history readable and makes reverts surgical.
2. **Write for the reader.** The next person to run `git log` is often you, six months on. The type and subject should convey intent without opening the diff.
3. **Adapt deliberately.** These conventions serve the project, not the reverse. If a rule stops earning its keep, change it on purpose and record the change here — don't let the standard erode by drift.
