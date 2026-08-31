#!/usr/bin/env python3
# Script: explain-git-command.py
# Purpose: Show WHICH byte broke `scripts/lib/git_command.py`'s parse, or what it found if it didn't
# Usage: explain-git-command.py '<command>'   |   printf '%s' "$cmd" | explain-git-command.py -
"""Stand-alone diagnostic for the shared git-invocation tokenizer (`scripts/lib/git_command.py`).

The guards that import that tokenizer fail closed on anything they cannot parse unambiguously, and
until now the refusal named only a CATEGORY ("unterminated backtick substitution") — diagnosing it
meant guessing which quoting confused the scanner and re-running until the guess landed. This tool
answers "which byte" directly: it runs the exact same public parsing primitives the guards use, and
prints either where the parse gave up (with an excerpt and a caret) or what the parse found (every
nested context and every `git` invocation it located, plus the `has_git_word` pre-gate answer that
explains a *policy* block on prose that only mentions git inside a quote).

**READ-ONLY.** This tool parses only — it never executes, evaluates, or shells out to any part of
the command it is handed. It only ever calls pure-text functions from `git_command` (`tokenize`,
`split_command_contexts`, `mask_heredoc_quotes`, `strip_comments`, `fold_continuations`,
`iter_git_invocations_detailed`, `has_git_word`) and this module's own pure-text heredoc scanner —
none of which import `subprocess`, `os.system`, `eval`, or `exec` on the command text. A diagnostic
that could run what it diagnoses would be worse than the ambiguity it explains.

**Positions are in PREPARED, per-context text, not the caller's original.** Earlier passes
(`mask_heredoc_quotes`, `strip_comments`, `fold_continuations`) insert escapes and delete spans, and
at nesting depth > 0 the text is an extracted context body — see `git_command.ParseAmbiguity`'s own
docstring. Every position this tool prints is followed by an excerpt; search for the excerpt in the
caller's own text, never for the raw offset.

Exit codes:
  0 — the command parsed. CONTEXTS + INVOCATIONS + has_git_word are printed.
  2 — the command is ambiguous, or usage was wrong. VERDICT + WHERE (when a position exists) are
      printed, along with whatever CONTEXTS were found before the parse gave up.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import git_command as gitcmd  # noqa: E402

# Guard stderr / a terminal both get unwieldy past this; the caret it exists to surface would be
# buried under a long line otherwise. Matches the plan's "~120 chars" cap for the guard refusals.
_EXCERPT_CAP = 120
# A short, single-line summary for the CONTEXTS list — several may be printed per command.
_SHORT_EXCERPT_CAP = 80


# --------------------------------------------------------------------------------------------
# Context discovery — mirrors `git_command._prepare` + `split_command_contexts` using ONLY that
# module's PUBLIC functions, in the documented order, so it can never disagree with the real walk
# about where a context starts or what its own text is. It stops at the first ambiguity it meets,
# which — because it runs the identical calls in the identical order — is the same point the real
# invocation walk (`iter_git_invocations_detailed`) would stop at, for every category that carries a
# position. It does NOT reach `tokenize()`, so a shlex-origin failure (a trailing backslash, an odd
# number of quotes that still balance) is invisible here and is caught separately, from the real
# walk, in `build_report`.
# --------------------------------------------------------------------------------------------


class ContextInfo:
    """One command context this tool found, mirroring `git_command.CommandContext` plus WHERE it
    was opened, for display only — never fed back into anything that decides ALLOW/BLOCK."""

    def __init__(
        self,
        index: int,
        depth: int,
        raw_text: str,
        parent_index: int | None,
        opener_pos: int | None,
    ) -> None:
        self.index = index
        self.depth = depth
        self.raw_text = raw_text
        self.parent_index = parent_index
        self.opener_pos = opener_pos
        # Filled in once this context's own split succeeds; left None if the split itself failed
        # (nothing past that point exists to fill it with) or was never attempted (unreachable
        # depth).
        self.prepared_text: str | None = None
        self.outer_text: str | None = None


def discover_contexts(
    command: str,
) -> tuple[list[ContextInfo], tuple[int, ValueError] | None]:
    """Walk `command` exactly the way `git_command._prepare` + `split_command_contexts` do, at
    every depth, collecting every context found in source order.

    Returns:
        The contexts found (possibly partial, if a failure stopped the walk), and — if a failure
        occurred — a `(context_index, exception)` pair naming which context's own split raised.
        `None` in the second slot means every context split cleanly (this says nothing about
        `tokenize()`, which this function never calls).
    """
    contexts: list[ContextInfo] = []
    failure: tuple[int, ValueError] | None = None

    def walk(
        raw_text: str, depth: int, parent_index: int | None, opener_pos: int | None
    ) -> None:
        nonlocal failure
        index = len(contexts)
        info = ContextInfo(index, depth, raw_text, parent_index, opener_pos)
        contexts.append(info)
        if failure is not None:
            return
        if depth > gitcmd.MAX_CONTEXT_DEPTH:
            failure = (index, ValueError("maximum command-context depth exceeded"))
            return
        try:
            prepared = gitcmd.fold_continuations(
                gitcmd.strip_comments(gitcmd.mask_heredoc_quotes(raw_text))
            )
            outer, nested = gitcmd.split_command_contexts(prepared, depth)
        except ValueError as exc:
            failure = (index, exc)
            return
        info.prepared_text = prepared
        info.outer_text = outer
        for k, ctx in enumerate(nested):
            marker = f"{gitcmd.PLACEHOLDER_PREFIX}{k}{gitcmd.PLACEHOLDER_SUFFIX}"
            pos = outer.find(marker)
            walk(ctx.text, ctx.depth, index, pos if pos >= 0 else None)
            if failure is not None:
                return

    walk(command, 0, None, None)
    return contexts, failure


# --------------------------------------------------------------------------------------------
# Position / excerpt rendering
# --------------------------------------------------------------------------------------------


def _locate(text: str, pos: int) -> tuple[int, int]:
    """1-based (line, col) for byte offset `pos` in `text`, clamped to the text's bounds."""
    pos = max(0, min(pos, len(text)))
    line_no = text.count("\n", 0, pos) + 1
    last_nl = text.rfind("\n", 0, pos)
    col_no = pos - last_nl
    return line_no, col_no


def _line_text(text: str, line_no: int) -> str:
    lines = text.split("\n")
    idx = line_no - 1
    return lines[idx] if 0 <= idx < len(lines) else ""


def _excerpt_and_caret(
    line: str, col_1based: int, cap: int = _EXCERPT_CAP
) -> tuple[str, str]:
    """A (possibly truncated) copy of `line` and a caret line aligned under `col_1based`."""
    idx = max(0, min(col_1based - 1, len(line)))
    if len(line) <= cap:
        return line, " " * idx + "^"
    half = cap // 2
    start = max(0, idx - half)
    end = min(len(line), start + cap)
    start = max(0, end - cap)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(line) else ""
    excerpt = f"{prefix}{line[start:end]}{suffix}"
    caret_idx = idx - start + len(prefix)
    return excerpt, " " * caret_idx + "^"


def _short_excerpt(text: str, cap: int = _SHORT_EXCERPT_CAP) -> str:
    first = text.split("\n", 1)[0]
    return first if len(first) <= cap else first[:cap] + "…"


# --------------------------------------------------------------------------------------------
# Heredoc advisory — best-effort, ADVISORY ONLY. Nothing here feeds ALLOW/BLOCK; it only decides
# whether one extra line of prose is worth printing. A minimal reimplementation rather than a call
# into `git_command`'s own heredoc reader, because that reader is private and mixes body-collection
# (which mutates text) with delimiter-reading — this only needs to know WHERE bodies are and
# whether their delimiter was quoted, over text this tool already has in hand for display.
# --------------------------------------------------------------------------------------------

_HEREDOC_DELIM_RE = re.compile(r"<<-?\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s;&|()<>]+))")


def _find_heredoc_body_spans(text: str) -> list[tuple[int, int, str, bool]]:
    """Best-effort `(body_start_line, body_end_line, delimiter, quoted)`, 1-based inclusive line
    numbers for the body. `quoted` covers both quote forms and a backslash-escaped delimiter
    (`<<\\EOF`) — bash treats either as making the body literal."""
    lines = text.split("\n")
    spans: list[tuple[int, int, str, bool]] = []
    i = 0
    n = len(lines)
    while i < n:
        pending: list[tuple[str, bool]] = []
        for m in _HEREDOC_DELIM_RE.finditer(lines[i]):
            if m.group(1) is not None:
                pending.append((m.group(1), True))
            elif m.group(2) is not None:
                pending.append((m.group(2), True))
            elif m.group(3) is not None:
                raw = m.group(3)
                pending.append((raw.replace("\\", ""), "\\" in raw))
        if pending:
            strip_tabs = "<<-" in lines[i]
            body_start = i + 1  # 0-based index of the first body line
            j = body_start
            target = pending[0][0]
            while j < n:
                candidate = lines[j].lstrip("\t") if strip_tabs else lines[j]
                if candidate == target:
                    break
                j += 1
            body_end = j  # 0-based index just past the last body line (exclusive), or n
            for delim, quoted in pending:
                # 1-based inclusive: body occupies lines body_start+1 .. body_end (both 1-based).
                spans.append((body_start + 1, body_end, delim, quoted))
            i = j + 1
            continue
        i += 1
    return spans


def _heredoc_advisory_for_line(text: str, line_no: int) -> str | None:
    for body_start, body_end, delim, quoted in _find_heredoc_body_spans(text):
        if body_start <= line_no <= body_end:
            if quoted:
                return (
                    f"note: this position is inside the body of heredoc '{delim}', whose "
                    "delimiter is quoted — bash already treats this body as literal text."
                )
            return (
                f"note: this position is inside the body of heredoc '{delim}', whose "
                f"delimiter is NOT quoted — bash expands it. Quoting the delimiter "
                f"(<<'{delim}') makes the body literal."
            )
    return None


def _heredoc_advisory_any(text: str) -> str | None:
    spans = _find_heredoc_body_spans(text)
    if not spans:
        return None
    _, _, delim, quoted = spans[0]
    if quoted:
        return (
            f"note: this command contains a heredoc (delimiter '{delim}', quoted) — its body is "
            "already literal to bash."
        )
    return (
        f"note: this command contains a heredoc (delimiter '{delim}', NOT quoted) — bash expands "
        f"its body. Quoting the delimiter (<<'{delim}') makes it literal."
    )


# --------------------------------------------------------------------------------------------
# Token rendering
# --------------------------------------------------------------------------------------------


def _render_tokens(tokens: list[str]) -> str:
    """Tokens exactly as the walk recorded them — including any `__GIT_COMMAND_SUBST_N__`
    placeholder standing in for a nested context.

    Deliberately NOT rewritten to a `<context #N>` cross-reference. An earlier version of this
    tool did that using this MODULE's own global context numbering, and it was wrong: `N` in the
    placeholder is LOCAL to whichever context housed this invocation — assigned fresh per
    `split_command_contexts` call — while this tool's own `ContextInfo.index` is GLOBAL across the
    whole walk, and the two only coincide by accident. `iter_git_invocations_detailed` does not
    say which context housed a given invocation, so there is no way to resolve the local index
    correctly from here without re-deriving the library's own traversal — exactly the drift risk
    this tool exists to avoid. See `has_placeholder` below for the pointer this prints instead.
    """
    return "[" + ", ".join(repr(t) for t in tokens) + "]"


def has_placeholder(tokens: list[str]) -> bool:
    return any(gitcmd.PLACEHOLDER_PREFIX in t for t in tokens)


# --------------------------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------------------------


class Report(NamedTuple):
    text: str
    exit_code: int


def _render_where(
    context_index: int | None,
    exc: ValueError,
) -> list[str]:
    lines: list[str] = []
    has_pos = (
        isinstance(exc, gitcmd.ParseAmbiguity) and exc.pos is not None and exc.text
    )
    if not has_pos:
        lines.append(
            "WHERE:       no byte position available for this category — the category"
        )
        lines.append("             above is the whole answer for this ambiguity class.")
        return lines

    text = exc.text
    pos = exc.pos
    line_no, col_no = _locate(text, pos)
    ctx_label = (
        f"context #{context_index}"
        if context_index is not None
        else "context (unresolved)"
    )
    lines.append(f"WHERE:       {ctx_label}, line {line_no}, col {col_no}")
    lines.append(
        "             note: offsets are in the PREPARED text — search for the excerpt below in"
    )
    lines.append("             your own text, never for the raw offset.")
    prev_line = _line_text(text, line_no - 1) if line_no > 1 else None
    target_line = _line_text(text, line_no)
    next_line = _line_text(text, line_no + 1)
    if prev_line is not None:
        lines.append(f"               {_short_excerpt(prev_line, _EXCERPT_CAP)}")
    excerpt, caret = _excerpt_and_caret(target_line, col_no)
    lines.append(f"               {excerpt}")
    lines.append(f"               {caret}")
    if next_line:
        lines.append(f"               {_short_excerpt(next_line, _EXCERPT_CAP)}")
    advisory = _heredoc_advisory_for_line(text, line_no)
    if advisory:
        lines.append(f"             {advisory}")
    return lines


def _render_contexts(contexts: list[ContextInfo]) -> list[str]:
    lines = ["CONTEXTS:"]
    if not contexts:
        lines.append("  (none)")
        return lines
    for info in contexts:
        excerpt = _short_excerpt(info.raw_text)
        if info.parent_index is None:
            where = "depth 0, top level"
        elif info.opener_pos is None:
            where = f"depth {info.depth}, opened in context #{info.parent_index} (position unresolved)"
        else:
            parent = contexts[info.parent_index]
            if parent.outer_text is not None:
                line_no, col_no = _locate(parent.outer_text, info.opener_pos)
                where = (
                    f"depth {info.depth}, opened at line {line_no}, col {col_no} "
                    f"in context #{info.parent_index}"
                )
            else:
                where = f"depth {info.depth}, opened in context #{info.parent_index}"
        lines.append(f"  #{info.index}  {where}")
        lines.append(f"       excerpt: {excerpt}")
    return lines


def _render_invocations(
    has_word: bool,
    invocations: list[gitcmd.Invocation] | None,
) -> list[str]:
    lines = ["INVOCATIONS:"]
    lines.append(f"  has_git_word: {has_word}")
    if invocations is None:
        lines.append("  (the walk did not complete — see VERDICT/WHERE above)")
        return lines
    if not invocations:
        lines.append("  (none found)")
        return lines
    any_placeholder = False
    for n, inv in enumerate(invocations, start=1):
        lines.append(
            f"  #{n}  subcommand={inv.subcommand!r}  cwd={inv.effective_dir!r}  cdir={inv.cdir!r}"
        )
        lines.append(
            f"       env={_render_tokens(inv.tokens.env)}  opts={_render_tokens(inv.tokens.opts)}"
        )
        lines.append(f"       args={_render_tokens(inv.arg_tokens)}")
        any_placeholder = any_placeholder or any(
            has_placeholder(seg)
            for seg in (inv.tokens.env, inv.tokens.opts, inv.arg_tokens)
        )
    if any_placeholder:
        lines.append(
            f"  note: a token containing '{gitcmd.PLACEHOLDER_PREFIX}N{gitcmd.PLACEHOLDER_SUFFIX}' "
            "names a nested context, but N is LOCAL to whichever context housed this invocation — "
            "not the global #N used in CONTEXTS below. See CONTEXTS for what is nested where."
        )
    return lines


def build_report(command: str) -> Report:
    """Assemble the full report for `command`. Never raises — every failure this tool can meet is
    caught and rendered as VERDICT: AMBIGUOUS with whatever detail is available; this function
    itself never executes or evaluates `command`."""
    has_word = gitcmd.has_git_word(command)

    if len(command) > gitcmd.MAX_COMMAND_LENGTH:
        lines = [
            "VERDICT:     AMBIGUOUS — command exceeds the maximum length this scanner will parse",
            "WHERE:       no byte position available for this category — the category above is",
            "             the whole answer for this ambiguity class.",
            "CONTEXTS:",
            "  (not attempted — the length check runs before any context is discovered)",
        ] + _render_invocations(has_word, None)
        return Report("\n".join(lines), 2)

    contexts, discover_failure = discover_contexts(command)

    invocations: list[gitcmd.Invocation] | None
    verdict_exc: ValueError | None
    try:
        invocations = gitcmd.iter_git_invocations_detailed(command, None)
        verdict_exc = None
    except ValueError as exc:
        invocations = None
        verdict_exc = exc

    if verdict_exc is None:
        lines = ["VERDICT:     PARSED"]
        lines += _render_contexts(contexts)
        lines += _render_invocations(has_word, invocations)
        top = contexts[0]
        if top.prepared_text is not None:
            advisory = _heredoc_advisory_any(top.prepared_text)
            if advisory and invocations:
                lines.append(advisory)
        return Report("\n".join(lines), 0)

    # Ambiguous. Prefer the discovery walk's own failure — it names which context raised, which
    # the bare exception from `iter_git_invocations_detailed` cannot, since that walk's contract
    # (see `iter_git_invocations_with_cwd`) returns no context bookkeeping on failure. Both walks
    # run the identical `_prepare`/`split_command_contexts` calls in the identical order, so for
    # every category that carries a position they raise on the same construct; only a
    # `tokenize()`-origin failure (never reached by `discover_contexts`) can leave `discover_failure`
    # unset while `verdict_exc` is not.
    category = (
        verdict_exc.category
        if isinstance(verdict_exc, gitcmd.ParseAmbiguity)
        else str(verdict_exc)
    )
    lines = [f"VERDICT:     AMBIGUOUS — {category}"]
    if discover_failure is not None:
        ctx_index, exc = discover_failure
        lines += _render_where(ctx_index, exc)
    else:
        lines += _render_where(None, verdict_exc)
    lines += _render_contexts(contexts)
    lines += _render_invocations(has_word, None)
    return Report("\n".join(lines), 2)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: explain-git-command.py '<command>'   |   printf '%s' \"$cmd\" | "
            "explain-git-command.py -",
            file=sys.stderr,
        )
        return 2
    arg = argv[1]
    command = sys.stdin.read() if arg == "-" else arg
    if command.endswith("\n"):
        command = command[:-1]
    report = build_report(command)
    print(report.text)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
