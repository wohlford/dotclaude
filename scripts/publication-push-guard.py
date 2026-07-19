#!/usr/bin/env python3
# Script: publication-push-guard.py
# Purpose: PreToolUse hook — fail-closed dev-block keeping `dev` private in a repo that adopted the dev/main publication model
"""PreToolUse hook — keep `dev` local in a repo that has adopted the dev/main publication model.

Called by Claude Code hooks with the tool-call JSON on stdin. When a `git push` (or an alias that
expands to one) is about to run and the target repo has a `.publication.toml` marker at its root
(the "adopted" signal — see specs/2026-07-18-publication-model-foundation.md), this blocks (exit 2)
any push that could publish `dev` — as a plain branch refspec, via a bare push whose current branch
is `dev`, or via a tag reachable only from `dev`. An ordinary push in a repo with no marker is
untouched (the mechanism is dormant until a repo adopts) — EXCEPT the one bounded case described
below where the target repo cannot be determined at all: root-unknown blocks regardless of marker,
in an adopted repo or not, because "unknown repo" means adoption can't be confirmed either way.

THIS IS A FAIL-CLOSED SECURITY GATE, not a deliberateness nudge like push-guard.py: it is NOT
overridable by `ALLOW_PUSH=1`, and ambiguity of any kind — an unparseable command, an unresolvable
repo root, a `--git-dir`/`--work-tree`/`GIT_DIR=` override, an unresolvable refspec — blocks rather
than allows. Detection is an ALLOWLIST: a push is judged safe only when every refspec it carries
matches the narrow `[+]<plain-branch>[:<plain-branch>]` grammar (or is a tag provably reachable
from `main`) and none of it is `dev`. Anything that doesn't fit that grammar is ambiguous and is
blocked, even where the equivalent push would in fact be safe — over-blocking an exotic push is
cheap; a parsing bug that lets `dev` through is not.

Cheap pre-filter, then fail closed: a command that does not even contain the word `git` returns 0
immediately (this hook is a global PreToolUse(Bash) hook and must be free for the overwhelming
majority of commands that touch no git invocation at all). Past that point — once the command is
plausibly git-related — `scripts/lib/git_command` is imported LAZILY, inside the same try/except
that wraps all further evaluation, so that even an ImportError (a corrupted install, a moved
module) blocks rather than silently passing every push through. This means a command containing the
word "git" that is genuinely unparseable (unbalanced quotes) also blocks, even if it turns out not
to be a push and even in a non-adopted repo — parsing must succeed before adoption can even be
checked, and there is no safe way to tell "not a push" from "can't tell if it's a push" once
tokenizing itself fails.

Known, documented residuals (bounded, never silent — same posture as recast-commit-gate.py):
  - `sh -c "git push …"` / `eval "git push …"`: the nested command string is opaque to the
    tokenizer, so these are NOT detected (same accepted gap as git_command's own WRAPPERS scope).
  - Alias resolution chases the chain recursively (matching real git), bounded by a depth cap and
    cycle guard (both fail closed if hit). A chain that resolves — at ANY depth — to `push` is
    BLOCKED unconditionally: this hook does not attempt to reproduce git's own alias-argument
    substitution well enough to prove such a push safe, so it never re-verifies an alias-resolved
    push against the refspec allowlist the way a literal `git push` invocation is. A shell alias
    (`alias.x = "!…"`), an unparseable expansion, or a hop that lands on a subcommand that is
    neither a known-safe built-in nor itself a resolvable alias is treated as ambiguous → blocked,
    rather than silently allowed.
  - `cd`/`pushd` targets are tracked textually across the whole token stream to compute each push's
    effective root (iter_git_invocations exposes per-invocation `-C` but not `cd` state). A `cd`
    target that isn't a static path — `cd -`, a bare `cd`, anything containing `$` or `~` — makes
    the working directory unresolvable, and `popd` is not tracked as a stack (any `popd` marks the
    directory unresolvable too) — all of which force a block on any push that follows, per the
    root-must-resolve-or-block rule; this trades a few false blocks in scripts that legitimately use
    `popd`/`~` for never silently losing track of an adopted repo's root.
  - `--git-dir`/`--work-tree`/`GIT_DIR=` detection is COARSE (a whole-command substring check, not
    scoped to the specific invocation that carries it) — a command containing any of these anywhere
    forces every push candidate in that command to block, which can over-block a compound command
    where the override applies to an unrelated git invocation. This check runs BEFORE the
    `.publication.toml` adoption check (see `_judge_invocation`), so it fires in a non-adopted repo
    too, not only an adopted one: root-unknown blocks regardless of marker, by design, because a
    repo whose identity can't be pinned down is one whose adoption can't be confirmed either way.
    Accepted: precise per-invocation scoping would require re-deriving the exact global-option
    token span this hook otherwise avoids duplicating from git_command, and over-blocking is the
    safe direction.
  - `remote.<name>.push` is consulted only when the command names its remote explicitly
    (`git push <remote>`, no refspec); a fully bare `git push` with no remote does not resolve the
    implicit default remote to check its `remote.push` config (this hook still checks HEAD's branch
    directly, which covers the common case `remote.push` is meant to override).
  - A hook process that is killed outright (a wall-clock timeout) exits via signal, bypassing the
    try/except below entirely — Task 3's settings.json wiring is responsible for giving this hook a
    generous timeout so a killed-hook window does not become a silent bypass.

Exit codes:
  0 — allow: no git push found, the repo is not adopted, or every push found is unambiguously safe.
  2 — block: EITHER an adopted repo's push targets `dev` (directly, via a tag, or ambiguously) OR
      the repo the push targets could not be determined at all (root-unknown blocks regardless of
      marker, adopted or not) — stderr names this guard so a runbook can grep for it specifically.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

PREFIX = "publication-push-guard:"

# Cheap pre-filter: a standalone "git" word anywhere in the command. Deliberately broad — it
# only decides whether the more expensive,
# fail-closed evaluation below is worth entering, never whether to block. Matched against the
# DEQUOTED view of the command (see _dequote below), not the raw string — a raw-text match would
# miss a quote-split `gi''t`/`g""it`/`gi\t`, which shlex (and a real shell) collapse to `git` before
# execution, so a raw-only check could fail-OPEN on an obfuscated push. Dequoting only removes
# characters, so it can reveal a hidden `git` but can never manufacture one that isn't there.
GIT_WORD_RE = re.compile(r"(?:^|[^A-Za-z0-9_])git(?:[^A-Za-z0-9_]|$)")

# Coarse, whole-command detection of a repo-root override. See the "COARSE" residual above. Also
# matched against the DEQUOTED view (see _dequote below) so a quote-split `--git-di''r=`/
# `GIT_DI''R=` can't hide a real override from this check — same reasoning as GIT_WORD_RE.
GITDIR_RE = re.compile(r"--git-dir|--work-tree|(?<![A-Za-z0-9_])GIT_DIR=")

# Shell quoting/escaping metacharacters stripped to produce the dequoted view GIT_WORD_RE and
# GITDIR_RE actually match against. This mirrors (approximately, and only in the direction that
# matters here) the quote-removal a real shell — and this hook's own shlex tokenizer — performs
# before a command runs: `'`, `"`, and `\` are removed outright, so `gi''t` reads as `git` and
# `--git-di''r=` reads as `--git-dir=`. It is intentionally NOT a full shell-quoting parser (it does
# not track quote state, so it also collapses quote characters that a real shell would keep as
# literal content); that is safe here because dequoting is used only to WIDEN these two detectors,
# never to decide what actually gets tokenized/executed — removing extra characters can make a
# detector fire on something it previously missed, never suppress a real match, so over-collapsing
# only trades a few more false blocks for zero missed obfuscated pushes.
_DEQUOTE_CHARS = str.maketrans("", "", "'\"\\")


def _dequote(command: str) -> str:
    """Strip quote/backslash characters so quote-split obfuscation reads as the plain word it
    resolves to at shell-execution time. Used only to feed GIT_WORD_RE/GITDIR_RE — see their
    comments and _DEQUOTE_CHARS above for why this is safe as a widen-only transform."""
    return command.translate(_DEQUOTE_CHARS)


# git subcommands that are never `push` and never worth an alias-config lookup (the common case,
# kept fast). Anything NOT in this set — including genuinely unknown words — is treated as a
# possible custom alias and resolved via `git config alias.<sub>` once the repo is confirmed
# adopted (see _find_block_reason). "push" itself is deliberately absent: it is always a candidate.
KNOWN_SAFE_SUBCOMMANDS = frozenset(
    {
        "status",
        "commit",
        "add",
        "rm",
        "mv",
        "fetch",
        "pull",
        "log",
        "diff",
        "show",
        "branch",
        "checkout",
        "switch",
        "restore",
        "reset",
        "stash",
        "tag",
        "clone",
        "init",
        "config",
        "remote",
        "merge",
        "rebase",
        "cherry-pick",
        "revert",
        "blame",
        "grep",
        "describe",
        "shortlog",
        "reflog",
        "gc",
        "fsck",
        "clean",
        "submodule",
        "worktree",
        "apply",
        "am",
        "format-patch",
        "send-email",
        "bisect",
        "archive",
        "bundle",
        "notes",
        "request-pull",
        "verify-commit",
        "verify-tag",
        "whatchanged",
        "help",
        "version",
        "rev-parse",
        "rev-list",
        "symbolic-ref",
        "merge-base",
        "ls-files",
        "ls-remote",
        "ls-tree",
        "cat-file",
        "diff-index",
        "diff-tree",
        "name-rev",
        "update-ref",
        "update-index",
        "write-tree",
        "read-tree",
        "commit-tree",
        "hash-object",
        "prune",
        "repack",
        "fast-export",
        "fast-import",
        "instaweb",
        "mergetool",
        "difftool",
        "annotate",
        "range-diff",
        "sparse-checkout",
        "maintenance",
        "credential",
        "credential-cache",
        "credential-store",
        "for-each-ref",
        "check-ignore",
        "check-attr",
        "check-ref-format",
    }
)

# `git push` long options that take a value token when not attached via `=`.
PUSH_VALUE_LONG = {"--repo", "--receive-pack", "--exec", "--push-option"}
# Short option characters (after the leading `-`) that take a value token.
PUSH_VALUE_SHORT = {"o"}

# A ref side must be this shape once `refs/heads/`/`refs/tags/` is stripped and any leading `+`
# force-marker removed: plain identifier characters only — no `~`, `^`, `@`, `*`, `?`, `:`, `[`,
# `\`, which is exactly what excludes revision suffixes, wildcards, and `@{...}` forms.
PLAIN_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _valid_plain_name(name: str) -> bool:
    """True if name is an unambiguous plain ref name (not a SHA, revision expression, or wildcard)."""
    if not name or not PLAIN_REF_RE.match(name):
        return False
    if ".." in name or name.endswith(".lock") or name.endswith("/"):
        return False
    return not SHA_RE.match(name)


def _combine(base: str | None, sub: str | None) -> str | None:
    """Join a possibly-relative `sub` (a `-C`/`cd` target) onto `base`; None propagates (an
    unresolved base or target stays unresolved, never silently defaulting to somewhere else)."""
    if base is None:
        return base
    if not sub:
        return base
    return sub if sub.startswith("/") else str(Path(base) / sub)


def _cwd_overrides_by_invocation_index(
    command: str, base_cwd: str, gitcmd: ModuleType
) -> list[str | None]:
    """One entry per `git` invocation in command position, in the same left-to-right order
    git_command.iter_git_invocations walks the same token stream — so the Nth entry here lines up
    with the Nth tuple that function returns.

    Deliberately does NOT re-implement iter_git_invocations' global-option/segment parsing — it only
    needs to recognize a `git` token in command position (identical predicate,
    `git_command.is_git` + `git_command.starts_command`) to record the cwd state at that point, and
    the actual (cdir, subcommand, args) triple is fetched from iter_git_invocations itself.

    Args:
        command: The raw shell-command string to walk.
        base_cwd: The working directory the command starts in.
        gitcmd: The lazily-imported `git_command` module, supplying the tokenizer and predicates.

    Returns:
        One entry per `git` invocation in command position, in command order. Each entry is the
        effective working directory in force at that invocation's START, after applying every
        `cd`/`pushd` target seen earlier in the stream (or None once a target becomes unresolvable).
        iter_git_invocations exposes a per-invocation `-C` but has no notion of `cd` state, which is
        a shell-wide effect that persists across `;`/`&&` boundaries, unlike `-C`.

    Raises:
        ValueError: On tokenizing ambiguity — propagated, uncaught, so the caller's outer
            try/except turns it into a block. Unlike iter_git_invocations, this function does NOT
            swallow that error, because for THIS gate "can't tell" must fail closed.
    """
    normalized = command.replace("\n", " ; ").replace("\r", " ")
    tokens = gitcmd.strip_redirects(gitcmd.tokenize(normalized))

    overrides: list[str | None] = []
    cwd_state: str | None = base_cwd
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if tok in ("cd", "pushd") and gitcmd.starts_command(tokens, i):
            j = i + 1
            target = None
            while j < n and not gitcmd.is_op(tokens[j]):
                if not tokens[j].startswith("-"):
                    target = tokens[j]
                    break
                j += 1
            if cwd_state is not None:
                if target is None or target == "-" or "$" in target or "~" in target:
                    cwd_state = (
                        None  # unresolvable target -> everything after it is unresolved
                    )
                else:
                    cwd_state = _combine(cwd_state, target)
            i += 1
            continue
        if tok == "popd" and gitcmd.starts_command(tokens, i):
            cwd_state = (
                None  # no stack tracked — see the popd residual in the module docstring
            )
            i += 1
            continue
        if gitcmd.is_git(tok) and gitcmd.starts_command(tokens, i):
            overrides.append(cwd_state)
            i += 1
            continue
        i += 1
    return overrides


def _resolve_root(effective_dir: str) -> str | None:
    """The toplevel of the repo containing effective_dir, or None if it cannot be resolved."""
    out = subprocess.run(
        ["git", "-C", effective_dir, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _head_branch(root: str) -> str | None:
    """The short name of the branch HEAD points to, or None if detached/unresolvable."""
    out = subprocess.run(
        ["git", "-C", root, "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _ref_exists(root: str, ref: str) -> bool:
    out = subprocess.run(
        ["git", "-C", root, "rev-parse", "--quiet", "--verify", ref],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return out.returncode == 0


def _tag_reachable_from_main(root: str, tag_name: str) -> bool:
    """True only if refs/tags/<tag_name> resolves AND its commit is an ancestor of `main`. A repo
    with no `main` branch, or an unresolvable tag, is NOT reachable (fail closed — we cannot prove
    safety, so we do not assume it)."""
    commit_out = subprocess.run(
        [
            "git",
            "-C",
            root,
            "rev-parse",
            "--quiet",
            "--verify",
            f"refs/tags/{tag_name}^{{commit}}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if commit_out.returncode != 0:
        return False
    commit = commit_out.stdout.strip()
    ancestor = subprocess.run(
        ["git", "-C", root, "merge-base", "--is-ancestor", commit, "main"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return ancestor.returncode == 0


def _classify_ref(name: str, root: str) -> tuple[str, str] | None:
    """Classify one side of a refspec as ("branch", short_name) or ("tag", short_name); None means
    ambiguous/unresolvable (the caller must block)."""
    if name == "HEAD":
        branch = _head_branch(root)
        return ("branch", branch) if branch else None
    if name.startswith("refs/heads/"):
        rest = name[len("refs/heads/") :]
        return ("branch", rest) if _valid_plain_name(rest) else None
    if name.startswith("refs/tags/"):
        rest = name[len("refs/tags/") :]
        return ("tag", rest) if _valid_plain_name(rest) else None
    if not _valid_plain_name(name):
        return None
    if _ref_exists(root, f"refs/heads/{name}"):
        return ("branch", name)
    if _ref_exists(root, f"refs/tags/{name}"):
        return ("tag", name)
    return None  # a plain, syntactically valid name that names nothing we can find — unresolvable


def _refspec_blocks(spec: str, root: str) -> bool:
    """True if this single refspec must block: `dev` appears (as a branch, on either side) or
    resolves as a tag NOT reachable from `main`, or the refspec is ambiguous in any way."""
    body = spec[1:] if spec.startswith("+") else spec
    if not body:
        return True  # e.g. a bare "+" or ":" — ambiguous
    src, _, dst = body.partition(":")
    if ":" not in body:
        dst = src
    sides = []
    for side in (src, dst):
        if not side:
            return True  # an empty side (a delete refspec) — treated conservatively as ambiguous
        classified = _classify_ref(side, root)
        if classified is None:
            return True
        sides.append(classified)
    for kind, name in sides:
        if kind == "branch" and name == "dev":
            return True
        if kind == "tag" and not _tag_reachable_from_main(root, name):
            return True
    return False


def _remote_push_blocks(root: str, remote: str) -> bool:
    """True if remote.<remote>.push configures a dev-spanning (or ambiguous) refspec."""
    out = subprocess.run(
        ["git", "-C", root, "config", "--get-all", f"remote.{remote}.push"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if out.returncode != 0:
        return False  # not configured — no opinion, the caller falls back to the HEAD check
    return any(
        _refspec_blocks(line.strip(), root)
        for line in out.stdout.splitlines()
        if line.strip()
    )


def _tags_block(root: str) -> bool:
    """True if any local tag (the set --tags/--follow-tags would sweep) is not main-reachable."""
    out = subprocess.run(
        ["git", "-C", root, "tag", "--list"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if out.returncode != 0:
        return True  # can't enumerate tags — can't prove safety
    tags = [t.strip() for t in out.stdout.splitlines() if t.strip()]
    return any(not _tag_reachable_from_main(root, t) for t in tags)


def _split_push_args(args: list[str]) -> tuple[set[str], list[str]]:
    """Split the tokens after `push` into (flag names seen, positional args) — the positional args
    are `[<remote> [<refspec>...]]` per git's own grammar.

    A clustered short option (`-fo blah`, `-vo blah`) must consume its value the same way git does:
    scanning stops at the first value-taking char in the cluster — if it's the LAST char, the next
    token is its value (`-fo` + `blah`); if chars follow it in the same token, those chars ARE the
    attached value (git's `-o<value>` form, e.g. `-oci.skip`) and no next-token is consumed. Getting
    this wrong lets a value token (like `blah` above) fall through as a positional, which shifts the
    real remote into the refspec slot and can route a bare push into the explicit-refspec branch —
    skipping the current-branch-is-dev check entirely. Modeled on recast-commit-gate.py's
    `parse_scope` COMMIT_VALUE_SHORT cluster handling."""
    flags: set[str] = set()
    positional: list[str] = []
    i, n = 0, len(args)
    while i < n:
        t = args[i]
        if t == "--":
            positional += args[i + 1 :]
            break
        if t.startswith("--") and len(t) > 2:
            name = t.split("=", 1)[0]
            flags.add(name)
            if name in PUSH_VALUE_LONG and "=" not in t:
                i += 1  # consume the value token (space-separated long form)
        elif t.startswith("-") and len(t) > 1:
            cluster = t[1:]
            c = 0
            while c < len(cluster):
                ch = cluster[c]
                flags.add("-" + ch)
                if ch in PUSH_VALUE_SHORT:
                    if c == len(cluster) - 1:
                        i += 1  # value is the next token (e.g. `-fo blah`)
                    break  # any remaining chars are the attached value — stop scanning
                c += 1
        else:
            positional.append(t)
        i += 1
    return flags, positional


def _judge_push(root: str, args: list[str]) -> str | None:
    """The core allowlist: a push is safe only if every refspec it carries is an unambiguous
    non-dev plain branch, or a tag reachable from main, and it carries none of the sweep flags this
    hook cannot evaluate precisely. Returns a block reason, or None if safe."""
    flags, positional = _split_push_args(args)

    if "--all" in flags:
        return "'--all' sweeps every local branch — ambiguous, cannot rule out dev"
    if "--mirror" in flags:
        return "'--mirror' mirrors the whole local ref set — ambiguous, cannot rule out dev"

    if len(positional) >= 2:
        remote, refspecs = positional[0], positional[1:]
        for spec in refspecs:
            if _refspec_blocks(spec, root):
                return f"refspec '{spec}' targets, or ambiguously might target, 'dev'"
    else:
        remote = positional[0] if positional else None
        if remote is not None and _remote_push_blocks(root, remote):
            return f"remote.{remote}.push configures a refspec that targets 'dev'"
        branch = _head_branch(root)
        if branch is None:
            return (
                "HEAD is detached or unresolvable on a bare push — cannot rule out dev"
            )
        if branch == "dev":
            return "a bare push resolves to the current branch 'dev'"

    if ("--tags" in flags or "--follow-tags" in flags) and _tags_block(root):
        return "'--tags'/'--follow-tags' would sweep a tag not reachable from main"

    return None


def _resolve_alias_chain(
    root: str, sub: str, seg: list[str], gitcmd: ModuleType, max_depth: int = 10
) -> tuple[str, list[str] | None]:
    """Chase `git config alias.<X>` recursively — the way real git resolves an alias chain — with a
    depth cap and cycle guard standing in for git's own loop-abort (real git aborts on an alias
    loop; 10 hops is generous headroom for any legitimate chain).

    Args:
        root: The repo toplevel to consult `git config alias.<X>` in.
        sub: The initial subcommand token to resolve.
        seg: The argument tokens following `sub` (folded into diagnostics on a `push` result).
        gitcmd: The lazily-imported `git_command` module, supplying the tokenizer.
        max_depth: Hop cap before a too-deep chain fails closed.

    Returns:
        A ``(kind, args)`` tuple, one of:
          ("none", None)  — `sub` is not configured as an alias at all: not a push, allow.
          ("safe", None)  — the chain resolves (at any depth) to a subcommand in
                             KNOWN_SAFE_SUBCOMMANDS: allow.
          ("push", args)  — the chain resolves (at any depth) to `push`. The caller blocks this
                             UNCONDITIONALLY — reproducing git's own alias-argument substitution well
                             enough to prove such a push safe is not attempted (see module
                             docstring); `args` is returned only for diagnostics.
          ("block", None) — the chain is a shell alias (`!...`), unparseable, cyclic, exceeds
                             max_depth, or a hop lands on a subcommand that is itself neither a
                             resolvable alias nor a known-safe built-in: ambiguous, fails closed.
    """
    current_sub, current_args = sub, seg
    seen: set[str] = set()
    depth = 0
    while True:
        if current_sub == "push":
            return "push", current_args
        if current_sub in KNOWN_SAFE_SUBCOMMANDS:
            return "safe", None
        if current_sub in seen or depth >= max_depth:
            return "block", None  # cycle, or chain too deep to trust
        seen.add(current_sub)
        out = subprocess.run(
            ["git", "-C", root, "config", "--get", f"alias.{current_sub}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if out.returncode != 0:
            # Not configured as an alias. At depth 0 that means `sub` itself was never an
            # alias — not a push, allow. At any deeper hop, it means the chain landed on a
            # subcommand we can neither recognize as safe nor resolve further — ambiguous.
            return ("none", None) if depth == 0 else ("block", None)
        value = out.stdout.strip()
        if not value or value.startswith("!"):
            return "block", None
        try:
            toks = gitcmd.tokenize(value)
        except ValueError:
            return "block", None
        if not toks:
            return "block", None
        alias_sub, alias_args = toks[0], toks[1:]
        current_sub, current_args = alias_sub, alias_args + current_args
        depth += 1


def _judge_invocation(
    effective_dir: str | None,
    sub: str,
    seg: list[str],
    gitcmd: ModuleType,
    gitdir_override: bool,
) -> str | None:
    """Judge one git invocation. Returns a block reason, or None to allow it."""
    if gitdir_override:
        return "the command carries --git-dir/--work-tree or a GIT_DIR= assignment — root unknown"
    if effective_dir is None:
        return "the effective working directory could not be resolved (cd/pushd target unknown)"

    root = _resolve_root(effective_dir)
    if root is None:
        return "the repo root could not be resolved"
    if not (Path(root) / ".publication.toml").is_file():
        return None  # not adopted — dormant

    if sub != "push":
        kind, _chain_args = _resolve_alias_chain(root, sub, seg, gitcmd)
        if kind == "none":
            return None  # not an alias at all — not a push
        if kind == "safe":
            return None  # chain resolves (possibly through multiple hops) to a known-safe built-in
        if kind == "push":
            # _chain_args is diagnostics only — see _resolve_alias_chain: never re-verified.
            return (
                f"subcommand '{sub}' resolves (via an alias chain) to 'push' — alias-based "
                "pushes are always blocked, never re-verified against the refspec allowlist"
            )
        return f"subcommand '{sub}' resolves to an unverifiable or unresolvable alias chain"

    return _judge_push(root, seg)


def _find_block_reason(command: str, cwd: str) -> str | None:
    """Lazily import git_command (an ImportError here is caught by the caller, and blocks), then
    evaluate every git invocation in `command`. Returns the first block reason found, or None."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    import git_command as gitcmd  # noqa: E402

    gitdir_override = bool(GITDIR_RE.search(_dequote(command)))

    # Raises ValueError on tokenizing ambiguity — propagated, uncaught, to the caller's fail-closed
    # try/except. iter_git_invocations tokenizes the identical normalized command with the same
    # primitives, so it is guaranteed to succeed (and align 1:1 by order) once the line above does.
    overrides = _cwd_overrides_by_invocation_index(command, cwd, gitcmd)
    invocations = gitcmd.iter_git_invocations(command)

    for effective_dir, (cdir, sub, seg) in zip(overrides, invocations, strict=True):
        if sub != "push" and sub in KNOWN_SAFE_SUBCOMMANDS:
            continue
        root_dir = _combine(effective_dir, cdir)
        reason = _judge_invocation(root_dir, sub, seg, gitcmd, gitdir_override)
        if reason is not None:
            return reason
    return None


def main() -> int:
    """Hook entry point: 2 blocks the push, 0 allows it."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    # This is the FIRST layer — "is there even a parseable command?" — and its ambiguity fails
    # OPEN (exit 0), exactly like the garbage-stdin case above: valid JSON that isn't an object
    # (`42`, `"str"`, `[1,2]`), or a `tool_input` that isn't an object, carries no command to
    # evaluate. Exit 2 here would block every Bash call on an odd payload, not just pushes.
    if not isinstance(data, dict):
        return 0
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    command = tool_input.get("command") or ""
    if not isinstance(command, str):
        command = ""
    cwd = data.get("cwd") or os.getcwd()
    if not isinstance(cwd, str):
        cwd = os.getcwd()
    if not command or not GIT_WORD_RE.search(_dequote(command)):
        return 0  # cheap: nothing resembling a git invocation at all, even after de-quoting

    try:
        reason = _find_block_reason(command, cwd)
    except Exception as exc:  # noqa: BLE001 - deliberate: any crash here must fail CLOSED
        print(
            f"{PREFIX} refusing to allow a git command after an internal error "
            f"({exc.__class__.__name__}) while evaluating it; failing closed.",
            file=sys.stderr,
        )
        return 2

    if reason is not None:
        print(
            f"{PREFIX} refusing to push private 'dev' (or an ambiguous target): {reason}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
