# The dev/main publication model

An opt-in model for repos that want a private working history and a curated public one. `dev` is the
private, local-only branch where day-to-day work happens; `main` is the curated, published history —
not a raw merge of `dev`, but a ground-up re-derivation of it (see `/recast`) that only the deliberate
promotion step produces. **Only the Foundation is built so far**: the `.publication.toml` marker below
and the `publication-push-guard` hook that keeps `dev` from leaving the machine. Model-aware
`/feature` and `/propagate` (so day-to-day tooling branches from and promotes through `dev`/`main`
correctly), and the one-time orphan cutover that establishes a repo's first published `main`, are
**forthcoming** — not yet built.

## The `.publication.toml` marker

Presence of `.publication.toml` at a repo's root is the adoption signal: a repo with the file has
adopted the model; a repo without it sees no ordinary effect from any of this (the one bounded
exception — a push the dev-block hook cannot attribute to any repo — is described below). Its
contents are one bit — which branch is the production target:

```toml
# .publication.toml — presence signals this repo uses the dev/main publication model
production = "dev"   # "dev" = dogfood the latest working state live before publishing
                     # "main" = production tracks the published branch (the default)
```

An absent file, or a present file that omits `production`, both mean `"main"` — the safe default.
The file is tracked (it lands in both histories), public-safe, and extensible — future keys may be
added without changing this contract. `/audit`'s existing `toml` check already validates its syntax;
no dedicated check was needed.

## The dev-block hook

In a repo that has adopted the model, `scripts/publication-push-guard.py` is a PreToolUse hook that
keeps `dev` private. It differs from the general `push-guard.py` in two ways:

- **Fail-closed.** Any ambiguity — an unparseable command, an unresolvable repo root, a wildcard or
  revision-suffix refspec, an unresolved alias chain — blocks rather than allows.
- **Non-overridable.** It does not honor `ALLOW_PUSH=1`. In an adopted repo it is the only defense
  against publishing `dev`, so the general override has no effect on it.

It is also **marker-gated**: an ordinary push in a repo without `.publication.toml` is untouched by
this hook. The one bounded exception is fail-closed by design, not a gap — if the hook cannot
determine which repo a push targets at all (an unresolvable repo root, a `--git-dir`/`--work-tree`/
`GIT_DIR=` override pointing elsewhere, or an unparseable command), it blocks regardless of any
marker, because an unknown repo means adoption can't be confirmed either way. Where the repo is
known and adopted, the hook works from an allowlist rather than a `dev` denylist: it blocks any push
naming `dev` as a source or destination — including force-push and revision-suffix forms — and
blocks any push it cannot classify precisely, while allowing an ordinary `main` push, a force-push
of `main` (reserved for the one-time cutover), and any tag reachable from `main`.

See [`scripts/HOOKS.md`](scripts/HOOKS.md) for how hooks like this one are built, and
[`ARCHITECTURE.md`](ARCHITECTURE.md) for where the publication model fits among this repo's other
moving parts.

## Limitations

- **Adoption is a one-way door.** There is no automated un-adoption path. Reversing adoption — going
  back to a single published history — is a deliberate manual act, not a flag to flip.
- **Single machine only.** `dev` never leaves the machine by design, so the working history it holds
  is not portable to a second machine.
