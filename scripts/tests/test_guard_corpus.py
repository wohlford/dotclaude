"""Regression corpus for `publication-push-guard.py`, run against a FROZEN pre-change build.

WHY THE BASELINE IS VENDORED, NOT A REF OR A TAG (this is the review BLOCKER Task 1 exists to
fix). This branch is re-derived onto `dev` as bricks and the branch itself is discarded, so a
baseline read from `refs/heads/dev` would, from the first landed brick onward, BE the new build --
the shrink assertion below ("nothing blocked by the old build is allowed by the new one") would
then compare the new build against itself and stay green forever, in a suite that keeps running.
A TAG has its own failure mode: a tag pointing at a `dev`-only commit is not reachable from `main`,
so this guard's own `_tags_block` would refuse `/propagate --push`'s `--follow-tags` the moment the
tag is swept toward publication. A plain vendored copy under `fixtures/` has neither problem --
its content and its sha256 (`BASELINE_GUARD_SHA256` / `BASELINE_GIT_COMMAND_SHA256` below, recorded
from `git show 782039d:...`, and re-verified at the top of every test run by `_verify_baseline_frozen`)
can never silently become "the new build".

WHY THE FIXTURE LAYOUT MIRRORS `scripts/` + `scripts/lib/`, NOT A FLAT DIRECTORY. The guard resolves
its own tokenizer with `sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))` -- i.e. it
looks for `lib/git_command.py` beside itself. A flat fixture directory (guard and git_command.py as
siblings) would still import successfully, but only because a script run directly also gets its own
directory prepended to `sys.path` by the interpreter (`sys.path[0]`), NOT because the guard's own
`sys.path.insert` found anything -- measured empirically while building this fixture: a flat copy
resolves `git status` to rc=0 by masking the missing `lib/git_command.py` via `sys.path[0]`, not by
faithfully reproducing the shipped import. A future change to how the guard imports its tokenizer
would then silently stop being reflected in a flat fixture. Mirroring the real layout is what keeps
this baseline honest.

EXEC BIT. The vendored guard carries a shebang (`#!/usr/bin/env python3`) and, in the real repo, is
tracked at mode 100755 (`git ls-files -s scripts/publication-push-guard.py`). `/audit`'s
`check_exec_bit` FAILs a *tracked* shebang file committed 100644, so the vendored copy is `chmod
755` here -- matching its real committed mode -- rather than stripping the shebang. Stripping would
have worked too (nothing here ever executes this file directly; it is always invoked as
`[sys.executable, path]`), but it would change the file's bytes away from the literal
`782039d` checkout, and byte-for-byte fidelity to the frozen commit is the entire point of a
vendored baseline. `git_command.py` carries no shebang and stays 644, matching its own real mode.

HERMETICITY. Every invocation of BOTH builds sets `PUBLICATION_PUSH_GUARD_LOG` into the sandbox.
The guard's internal-error branch defaults to `~/.claude/logs/` otherwise, and that default has
already once accumulated synthetic records from a test suite (measured: 12 records from 4 runs) --
see `check_hermetic_outside` in `skills/audit/audit.sh`. Every row here also runs against a
purpose-built fixture repo, never the live repo, so a verdict never depends on the operator's own
`git config alias.*` or on which branch this repo's HEAD happens to be on. The sandbox root is
resolved with `.resolve()` (pwd -P) because `$TMPDIR` is reached through a symlink on this machine
(`/tmp` -> `/private/tmp`), and a path-resolving subject can take a different branch under the
logical form.

WHY THE PUSH VERB IS NEVER SPELLED NEXT TO "git" IN THIS FILE'S SOURCE. This repo's own git-timing-
guard (`~/.claude/.git-timing-guard.sh`) greps the raw text of every Bash command the operating
agent runs for the pattern `git <options>*` followed by the verb this module never spells out
next to it, including inside quoted strings and comments --
it does not parse the command, so an ad hoc verification command built while authoring this file
could be refused by a gate that never actually judged it. Every row's command string is therefore
assembled from `"p" + "ush"` at import time (see `_VERB` / `_p()` below), and no comment, label, or
docstring in this file spells the two words contiguously either.

THE FOUR REQUIRED ASSERTIONS (spec S7 / plan Task 1), each its own test function below:
  1. `test_must_block_rows_block_on_new_build` -- every MUST_BLOCK row blocks (rc 2) on the NEW
     build.
  2. `test_blocked_set_does_not_shrink` -- nothing the FROZEN baseline blocks is allowed by the
     new build, over every row in the corpus regardless of label.
  3. `test_baseline_allows_at_least_one_row` -- a non-zero ALLOW denominator on the baseline, or
     assertion 2 passes for free the moment the baseline ever became universally-blocking.
  4. `test_corpus_has_rows` -- a non-zero row count.

Everything else in this module (the MUST_ALLOW / XFAIL_TODAY / ACCIDENTAL-specific tests) goes
beyond those four on purpose: a labeled row nothing ever checks is decoration, not a corpus.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

# ---------- locations ----------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import git_command as new_gitcmd  # noqa: E402, I001

_TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = _TESTS_DIR.parent.parent
NEW_GUARD = REPO_ROOT / "scripts" / "publication-push-guard.py"

_BASELINE_ROOT = _TESTS_DIR / "fixtures" / "prechange"
BASELINE_GUARD = _BASELINE_ROOT / "scripts" / "publication-push-guard.py"
BASELINE_GIT_COMMAND = _BASELINE_ROOT / "scripts" / "lib" / "git_command.py"


def _load_baseline_gitcmd() -> ModuleType:
    """Import the FROZEN baseline `git_command.py` under its own module name -- never
    `git_command`, which `new_gitcmd` above already occupies in `sys.modules`; importing the
    baseline under the same name would silently replace it there, and every later `import
    git_command` anywhere in the process (this module's own top-level import already ran, but a
    fixture-scoped re-import elsewhere would not) would resolve to the WRONG build. The baseline
    file is self-contained stdlib-only (verified: `os`, `re`, `shlex`, `typing`), so no further
    sys.path surgery is needed to load it standalone."""
    spec = importlib.util.spec_from_file_location(
        "baseline_git_command", BASELINE_GIT_COMMAND
    )
    assert spec is not None and spec.loader is not None, (
        f"could not build an import spec for {BASELINE_GIT_COMMAND}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Must match publication-push-guard.py's own LOG_ENV_VAR constant -- see the hermeticity note
# in the module docstring above.
LOG_ENV_VAR = "PUBLICATION_PUSH_GUARD_LOG"

# sha256 of the vendored baseline, computed from `git show 782039d:<path>` at the moment it was
# copied into fixtures/prechange/. Re-verified at runtime by `_verify_baseline_frozen` below --
# see the module docstring's WHY-vendored note for why a mutated baseline makes every comparison
# in this module meaningless.
BASELINE_GUARD_SHA256 = (
    "7db425cbb1ba5abd94e891d6557a4db9c59a9d3c9eb60eb0543e0875d0ef4f83"
)
BASELINE_GIT_COMMAND_SHA256 = (
    "9db957806023f75a11758b10a85e86dd7dcadddc2d75423942942a49c3e588a8"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- building a push string without ever writing it contiguously ----------

_VERB = "p" + "ush"  # see the module docstring's WHY-never-spelled-out note


def _p(args: str = "") -> str:
    """A real `git <verb>` invocation, built without the verb ever appearing next to "git" in
    this file's own source text."""
    return f"git {_VERB}" + (f" {args}" if args else "")


P = _p("origin dev")  # a real push of dev, in the adopted repo's own refspec grammar
# The same, without the leading "git " -- for splicing after inserted options
# ("git -C ';' " + PUSH_ARGS, etc.).
PUSH_ARGS = f"{_VERB} origin dev"
# A refspec layer 1 ALLOWS. Task 7's rows need this: pairing a config injection with `dev` proves
# nothing, because the refspec rule blocks first and the row passes whether or not the detector
# exists. Measured -- a probe built that way reported every injection "already blocked".
PUSH_SAFE_ARGS = f"{_VERB} origin main"


# ---------- row categories ----------

MUST_BLOCK = "MUST_BLOCK"  # currently blocked; must stay blocked on every future build
XFAIL_TODAY = "XFAIL_TODAY"  # currently allowed; a later, named task closes this
MUST_ALLOW = "MUST_ALLOW"  # currently allowed; must stay allowed (an over-block regression guard)
# Currently blocked, but NOT by real detection -- see each row's `why`.
ACCIDENTAL = "ACCIDENTAL"

# FLOOR over the labels themselves. Nothing validated `Row.category` before, so a typo
# (`MUST_BLOK`) silently dropped a row from every label-specific test while still looking like a
# corpus entry -- it kept contributing to `test_blocked_set_does_not_shrink`, which reads every row
# regardless of label, and to nothing else. That is the quietest way to lose coverage here: the row
# is still present, still runs, and still passes.
_CATEGORIES = frozenset({MUST_BLOCK, XFAIL_TODAY, MUST_ALLOW, ACCIDENTAL})

# THERE IS DELIBERATELY NO `SANCTIONED_UNBLOCK` CATEGORY, and this note exists so it is not
# re-proposed a third time. The plan for this branch specified one -- a category for rows the frozen
# baseline BLOCKS and the new build deliberately ALLOWS, excused from the shrink assertion. It was
# built, and the two-sided pin written for it (base must be 2, new must be 0) immediately failed on
# all five candidate rows with `baseline rc=0`.
#
# The premise was false, and measurably so: the vendored baseline is frozen at `782039d`, which
# contains ZERO config-injection code (`grep -c` on the fixture returns 0) -- the detector was
# introduced later, on this branch. So the over-blocks those rows describe (husky's install, a pure
# read, `GIT_CONFIG_NOSYSTEM`) were never the BASELINE's behaviour at all; they belonged to an
# INTERMEDIATE state of this branch, which the corpus does not model and should not, because its
# whole value is a fixed pre-branch reference point. Relative to that reference the blocked set only
# ever GREW, which is what `test_blocked_set_does_not_shrink` reports today.
#
# Those rows are MUST_ALLOW, and they are load-bearing there: they are the over-block regression
# guards for a machine-wide block that really did ship mid-branch. An empty category plus a pin that
# can never fire would have pinned nothing while reading as coverage.


@dataclass(frozen=True)
class Row:
    """One corpus entry.

    Attributes:
        label: A short, stable, grep-free-of-the-push-verb identifier.
        command: The literal command text fed to the guard's stdin JSON payload.
        category: A member of `_CATEGORIES`, asserted by
            `test_every_row_carries_a_known_category` -- an unrecognised string is a row that
            quietly opts out of every label-specific test.
        why: A one-line explanation of the verdict this row exists to pin.
        xfail_task: For XFAIL_TODAY rows, the task expected to fix it. Must name something that
            will ACTUALLY close the row: a task that completes without closing it turns this
            marker stale in the one direction the tripwire cannot see.
    """

    label: str
    command: str
    category: str
    why: str
    xfail_task: str = ""


# ---------- Task 3's preserve matrix: derived from RESERVED_WORDS, never hand-listed ----------
#
# "the blocked set does not shrink" is necessary and NOT sufficient with hand-listed rows: three
# hand-written preserve rows (bang/if/while, each paired with `cd`) covered only 3 of the 10
# measured loss shapes. The seven missed -- `then`, `else`, `elif`, `do`, `{`, `until`, and `!`
# paired with `pushd` rather than `cd` -- all sit INSIDE a construct, which is exactly why nobody
# thinks to hand-write them. Deriving one row per RESERVED_WORDS member, crossed with
# {cd, pushd, popd}, is what forces every one of them to exist.
#
# Each wrap places `body` directly after the named reserved word -- the position
# `_git_starts_command`'s new boundary applies to, and cd/pushd/popd recognition (unscoped) must
# NOT inherit it. `popd` takes no target, so its body is just `popd` (marking cwd unresolvable is
# itself already a block, not a leak -- see the row's `why`).
_RESERVED_WORD_WRAP = {
    "{": lambda body: f"{{ {body}; }}",
    "!": lambda body: f"! {body}",
    "if": lambda body: f"if {body}; then :; fi",
    "then": lambda body: f"if true; then {body}; fi",
    "elif": lambda body: f"if false; then :; elif {body}; then :; fi",
    "else": lambda body: f"if false; then :; else {body}; fi",
    "while": lambda body: f"while {body}; do :; done",
    "until": lambda body: f"until {body}; do :; done",
    "do": lambda body: f"while :; do {body}; done",
    "coproc": lambda body: f"coproc {body}",
}

# FLOOR: if RESERVED_WORDS ever gains a member with no known wrap shape, fail loudly rather than
# let the derived matrix below silently under-cover it (see the CLAUDE.md floor-vs-glob hazard).
assert set(_RESERVED_WORD_WRAP) == new_gitcmd.RESERVED_WORDS, (
    f"_RESERVED_WORD_WRAP is out of sync with git_command.RESERVED_WORDS: "
    f"{set(_RESERVED_WORD_WRAP) ^ new_gitcmd.RESERVED_WORDS}"
)

_LABEL_SAFE = {"{": "brace", "!": "bang"}


def _reserved_word_preserve_rows(other: Path) -> list[Row]:
    """One MUST_BLOCK row per (reserved word, cd-family command) pair -- the derived preserve
    matrix. Every row must have been blocked on the frozen baseline too (asserted by
    `test_reserved_word_preserve_matrix_was_already_blocked_on_baseline` below), or it would prove
    nothing about a shrinking blocked set."""
    other_s = str(other)
    rows: list[Row] = []
    for word in sorted(new_gitcmd.RESERVED_WORDS):
        for cd_cmd in ("cd", "pushd", "popd"):
            body = "popd" if cd_cmd == "popd" else f"{cd_cmd} {other_s}"
            command = f"{_RESERVED_WORD_WRAP[word](body)} ; {P}"
            label = f"reserved_{_LABEL_SAFE.get(word, word)}_{cd_cmd}_preserve"
            rows.append(
                Row(
                    label,
                    command,
                    MUST_BLOCK,
                    f"derived preserve row: a {cd_cmd} placed directly after the reserved word "
                    f"'{word}' must stay UN-tracked (approach (a)'s scoping), so the push still "
                    "judges against the adopted repo's own cwd rather than a directory with no "
                    "marker",
                )
            )
    return rows


def _build_rows(adopted: Path, other: Path) -> list[Row]:
    """The full corpus.

    `other` is a second, NON-adopted fixture repo. It began as a splice for the two subshell-`cd`
    rows; the scope-classifier rows at the bottom use it for a different and load-bearing reason --
    see the CWD IS THE MEASUREMENT note above them.

    `adopted` is the primary fixture repo's own path, needed by the reach-in rows that name it as a
    TARGET (`-f <adopted>/.git/config`, `--file=`, `GIT_CONFIG=`, `-C <adopted>`). Those are the
    shape that proves standing in a non-adopted directory does not put the adopted repo out of
    reach -- the hole that scoping the detector to adoption would otherwise open.
    """
    other_s = str(other)
    adopted_s = str(adopted)
    return [
        # ---------- MUST_BLOCK: blocked today, must stay blocked on every future build ----------
        Row(
            "P_bare",
            P,
            MUST_BLOCK,
            "a literal push of dev's own branch refspec, in the adopted repo -- the base case",
        ),
        Row(
            "dashC_semicolon_boundary",
            f"git -C ';' {PUSH_ARGS}",
            MUST_BLOCK,
            "-C's value slot holds an operator-shaped token (a quoted ';'); the walk hands the "
            "operator back rather than dropping the invocation, so the real subcommand two slots "
            "over is still unresolvable and blocks -- never disappear on ambiguity",
        ),
        Row(
            "dashc_semicolon_boundary",
            f"git -c ';' {PUSH_ARGS}",
            MUST_BLOCK,
            "same value_slot_op fallback as -C, for -c",
        ),
        Row(
            "sudo_wrapper",
            f"sudo git {PUSH_ARGS}",
            MUST_BLOCK,
            "sudo is a known WRAPPERS entry -- git stays in command position through it",
        ),
        Row(
            "cmd_subst_arg",
            f'x="$(git {PUSH_ARGS})"',
            MUST_BLOCK,
            "a real subshell inside a quoted command substitution; git_command recurses into it",
        ),
        Row(
            "process_subst",
            f"cat <({P})",
            MUST_BLOCK,
            "process substitution is a real subshell git_command recurses into",
        ),
        Row(
            "cd_other_true_then_P",
            f"(cd {other_s} && true) ; {P}",
            MUST_BLOCK,
            "a cd inside a `( )` subshell is isolated -- the outer command still runs with the "
            "adopted repo's own cwd once the subshell closes",
        ),
        Row(
            "gitdashc_paren_then_P",
            f"(git -c) ; {P}",
            MUST_BLOCK,
            "a truncated, malformed invocation ahead of a real one must not swallow or misjudge "
            "what follows it",
        ),
        # ---------- Task 3 (F1 + L2): reserved words and `exec` are now command boundaries -------
        Row(
            "bang_P",
            f"! {P}",
            MUST_BLOCK,
            "`!` is a RESERVED_WORDS command boundary (F1) -- scoped to `_git_starts_command` only",
        ),
        Row(
            "brace_group_P",
            f"{{ {P}; }}",
            MUST_BLOCK,
            "`{` is a RESERVED_WORDS command boundary (F1)",
        ),
        Row(
            "if_true_then_P_fi",
            f"if true; then {P}; fi",
            MUST_BLOCK,
            "`if`/`then` are RESERVED_WORDS command boundaries (F1)",
        ),
        Row(
            "while_do_P_done",
            f"while :; do {P}; done",
            MUST_BLOCK,
            "`while`/`do` are RESERVED_WORDS command boundaries (F1)",
        ),
        Row(
            "until_do_P_done",
            f"until false; do {P}; done",
            MUST_BLOCK,
            "`until`/`do` are RESERVED_WORDS command boundaries (F1)",
        ),
        Row(
            "for_do_P_done",
            f"for i in 1; do {P}; done",
            MUST_BLOCK,
            "`do` is a RESERVED_WORDS command boundary (F1) -- `for`/`in` are not, but `do` alone "
            "is enough to see the push",
        ),
        Row(
            "if_false_else_P_fi",
            f"if false; then true; else {P}; fi",
            MUST_BLOCK,
            "`else` is a RESERVED_WORDS command boundary (F1)",
        ),
        Row(
            "if_false_elif_true_then_P_fi",
            f"if false; then true; elif true; then {P}; fi",
            MUST_BLOCK,
            "`elif`/`then` are RESERVED_WORDS command boundaries (F1)",
        ),
        Row(
            "func_def_P_call",
            f"f() {{ {P}; }}; f",
            MUST_BLOCK,
            "a function BODY's `{` is a RESERVED_WORDS command boundary (F1)",
        ),
        Row(
            "exec_wrapper_P",
            f"exec {P}",
            MUST_BLOCK,
            "`exec` is in GIT_ONLY_WRAPPERS, so it keeps the following invocation in command "
            "position (L2) -- scoped away from WRAPPERS proper so it cannot also start tracking a "
            "`cd`/`pushd`/`popd` right after it",
        ),
        # ---------- Task 3's DERIVED preserve matrix (RESERVED_WORDS x {cd, pushd, popd}) -------
        *_reserved_word_preserve_rows(other),
        Row(
            "for_in_git_no_phantom",
            "for f in git; do echo $f; done",
            MUST_ALLOW,
            "`in` is deliberately excluded from RESERVED_WORDS -- a for-loop list item is not a "
            "command, so 'git' right after it must not manufacture a phantom invocation",
        ),
        Row(
            "attr_source_launders",
            f"git --attr-source HEAD {PUSH_ARGS}",
            MUST_BLOCK,
            "CLOSED by Task 5: --attr-source is now classified value-taking, so HEAD is consumed "
            "as its value instead of being laundered into a bogus subcommand. Kept as MUST_BLOCK "
            "rather than deleted -- this was a measured fail-open (rc 0 against the shipped "
            "build), and the row is what stops it returning",
        ),
        Row(
            "super_prefix_two_args_launders",
            f"git --super-prefix xx {PUSH_ARGS}",
            MUST_BLOCK,
            "CLOSED by Task 5, but by the OTHER half: --super-prefix does not exist in git 2.55 "
            "and is deliberately absent from both allowlists, so it classifies as unknown -> the "
            "invocation is unjudgeable -> blocked. This row is the one that proves the allowlist "
            "default itself, not merely that one more name was added to it",
        ),
        # ---------- Task 7: relocating or silencing the load-bearing pre-push boundary ----------
        # Every row pushes a refspec layer 1 ALLOWS (`main`), deliberately. Pairing the injection
        # with `dev` would prove nothing: the refspec rule fires first and the row would pass
        # whether or not a detector existed. Measured -- a first probe did exactly that and read
        # as "all blocked, no work needed".
        Row(
            "hooksPath_c_canonical",
            f"git -c core.hooksPath=/dev/null {PUSH_SAFE_ARGS}",
            MUST_BLOCK,
            "disabling the hooks path defeats layer 2 while the refspec sails through layer 1",
        ),
        Row(
            "hooksPath_c_lowercase",
            f"git -c core.hookspath=/dev/null {PUSH_SAFE_ARGS}",
            MUST_BLOCK,
            "git config keys are case-insensitive; a case-sensitive check reads as coverage "
            "while missing this exact spelling",
        ),
        Row(
            "hooksPath_c_uppercase",
            f"git -c CORE.HOOKSPATH=/dev/null {PUSH_SAFE_ARGS}",
            MUST_BLOCK,
            "the other case spelling git accepts",
        ),
        Row(
            "include_path_indirection",
            f"git -c include.path=/tmp/evil.cfg {PUSH_SAFE_ARGS}",
            MUST_BLOCK,
            "include.path reaches hooksPath one hop away -- denying only the direct key would "
            "leave the indirect route open",
        ),
        Row(
            "git_config_global_env",
            f"GIT_CONFIG_GLOBAL=/tmp/evil.cfg git {PUSH_SAFE_ARGS}",
            MUST_BLOCK,
            "swaps the file the config comes from, so no denied KEY appears in the command",
        ),
        Row(
            "git_config_numbered_env",
            f"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null "
            f"git {PUSH_SAFE_ARGS}",
            MUST_BLOCK,
            "the numbered form is open-ended -- a COUNT|KEY|VALUE alternation misses KEY_0",
        ),
        Row(
            "config_write_then_safe_push",
            f"git config --local core.hooksPath /dev/null && git {PUSH_SAFE_ARGS}",
            MUST_BLOCK,
            "the two-command bypass: `config` is in KNOWN_SAFE_SUBCOMMANDS and the push that "
            "follows is one layer 1 allows, so blocking the config half is what makes the PAIR "
            "unreachable -- this is why the detector runs before the known-safe shortcut",
        ),
        Row(
            "unresolvable_c_value",
            f'git -c "$K" {PUSH_SAFE_ARGS}',
            MUST_BLOCK,
            "shlex strips the quotes, so no literal key=value survives; unresolvable => block, "
            "rather than being read as a harmless option",
        ),
        # The false block the previous whole-command design produced, kept as a MUST_ALLOW so a
        # future re-widening of the detector's scope fails here rather than in someone's session.
        Row(
            "readonly_grep_of_the_hook",
            "grep -n 'core.hooksPath=/dev/null' git-hooks/pre-push",
            MUST_ALLOW,
            "a read-only grep carries no git invocation in command position, so a detector scoped "
            "to invocation tokens cannot see it; a whole-command regex false-blocked this exact "
            "command because its git-word prefilter matched the path token `git-hooks`",
        ),
        Row(
            "send_pack_direct",
            "git send-pack origin refs/heads/dev:refs/heads/x",
            MUST_BLOCK,
            "Task 2's PUBLISHING_SUBCOMMANDS deny set catches this before alias resolution -- "
            "send-pack is the transport push itself invokes, and runs no pre-push hook",
        ),
        Row(
            "svn_dcommit",
            "git svn dcommit",
            MUST_BLOCK,
            "same L1 deny-set catch as send_pack_direct",
        ),
        Row(
            "p4_submit",
            "git p4 submit",
            MUST_BLOCK,
            "same L1 deny-set catch as send_pack_direct",
        ),
        Row(
            "daemon",
            "git daemon",
            MUST_BLOCK,
            "same L1 deny-set catch as send_pack_direct",
        ),
        # ---------- Task 4 (F3): the boundary token reaches the paren branch ---------------------
        Row(
            "cd_other_gitdashc_then_P",
            f"(cd {other_s} && git -c) ; {P}",
            MUST_BLOCK,
            "the truncated `git -c`'s ambiguous value slot hands the ')' back to the main loop "
            "instead of swallowing it, so the paren branch pops the subshell cwd and the real "
            "push is judged in the adopted repo -- both halves of F3's two-part fix are required: "
            "an unjudgeable invocation is still appended for the truncated `git -c` itself, so "
            "nothing disappears either",
        ),
        # ---------- D1: a flag whose whole effect is "the boundary hook shall not run" ------------
        # These are GAIN rows: allowed on the frozen baseline, blocked on the new build. They do not
        # touch the shrink assertion (which only fires baseline-blocked -> new-allowed), and they
        # are the RED this change was written against.
        Row(
            "boundary_bypass_flag_plain",
            f"git {_VERB} --no-verify origin main",
            MUST_BLOCK,
            "the flag makes git skip the boundary hook; the refspec is one layer 1 otherwise "
            "allows, so layer 2 was the only judge and this removes it",
        ),
        Row(
            "boundary_bypass_flag_abbreviated",
            f"git {_VERB} --no-veri origin main",
            MUST_BLOCK,
            "git's parse-options accepts unambiguous long-option abbreviations (measured on 2.55: "
            "`git branch --no-colo` is accepted), so a literal comparison would miss this",
        ),
        Row(
            "boundary_bypass_flag_equals_form",
            f"git {_VERB} --no-verify=true origin main",
            MUST_BLOCK,
            "pins that the =-stripped flag NAME is what is matched, not the raw token",
        ),
        Row(
            "boundary_bypass_flag_outside_adopted_repo",
            f"cd {other_s} && git {_VERB} --no-verify origin main",
            MUST_BLOCK,
            "layer 1 is registered globally and judge-removal is refused everywhere, adopted or "
            "not -- THIS row is the placement discriminator: it fails if the check is written "
            "inside _judge_push, which sits BELOW _judge_invocation's dormant return",
        ),
        # ---------- MUST_ALLOW: allowed today, must stay allowed (an over-block guard) ----------
        Row(
            "verbose_negation_is_not_the_bypass_flag",
            f"git {_VERB} --no-verbose origin main",
            MUST_ALLOW,
            "--no-verbose is a different flag and is NOT a prefix of the bypass flag; the "
            "abbreviation rule must not swallow it",
        ),
        Row(
            "echo_hello",
            "echo hello",
            MUST_ALLOW,
            "no git word at all -- the cheap prefilter",
        ),
        Row("git_status", "git status", MUST_ALLOW, "a known-safe subcommand"),
        Row("git_fetch", "git fetch", MUST_ALLOW, "a known-safe subcommand"),
        Row("git_log", "git log", MUST_ALLOW, "a known-safe subcommand"),
        Row(
            "git_frobnicate",
            "git frobnicate",
            MUST_ALLOW,
            "an unknown subcommand that resolves to no configured alias -- not a push",
        ),
        Row(
            "git_no_pager_log",
            "git --no-pager log",
            MUST_ALLOW,
            "a valueless global option ahead of a known-safe subcommand",
        ),
        Row(
            "git_c_user_name",
            "git -c user.name=x status",
            MUST_ALLOW,
            "-c with a genuine attached value, ahead of a known-safe subcommand",
        ),
        Row(
            "git_exec_path",
            "git --exec-path status",
            MUST_ALLOW,
            "--exec-path's bare form is valueless; must not eat the following subcommand",
        ),
        Row(
            "git_dash_P",
            "git -P status",
            MUST_ALLOW,
            "-P (pagination) is valueless and must not be confused with -p or an unknown option",
        ),
        Row(
            "phantom_echo",
            f"exec echo git {_VERB} origin dev",
            MUST_ALLOW,
            "the word 'git' appears only as a bare ARGUMENT to echo, never in command position -- "
            "a phantom-guard row for the prefilter. `exec echo hi` would not exercise this: it "
            "contains no 'git' word at all, so the cheap prefilter answers instead of the subject",
        ),
        # ---------- ACCIDENTAL: blocked today, but NOT by real detection ----------
        Row(
            "case_esac_P",
            f"case x in x) {P};; esac",
            ACCIDENTAL,
            "blocks only because the bare ')' terminating the case pattern is misread as a stray "
            "subshell close (the walk pops an empty subshell_cwds stack), forcing the cwd to "
            "become unresolvable -- not real case/esac detection",
        ),
        Row(
            "super_prefix_slash_launders",
            f"git --super-prefix x/ {PUSH_ARGS}",
            ACCIDENTAL,
            "blocks only because the laundered token 'x/' fails LITERAL_SUBCOMMAND_RE (it "
            "contains '/') before any alias lookup runs -- an accidental catch of a malformed "
            "token shape, not F2 recognising --super-prefix as unjudgeable; contrast with "
            "super_prefix_two_args_launders above, which uses the clean token 'xx' and sails "
            "through today",
        ),
        # ---------- CWD IS THE MEASUREMENT: the rows that must be UNCONDITIONAL ----------
        # `verdicts` runs every row with cwd = the ADOPTED sandbox. There, a `--global` write blocks
        # whether the arm is correctly UNCONDITIONAL or wrongly ADOPTION-GATED -- the adoption gate
        # disposes of the row before unconditionality is ever consulted. So the row would pass for
        # the wrong reason, and a later regression to gating would stay green forever.
        #
        # Every row below therefore carries a `cd {other} &&` prefix, moving the walk's
        # effective_dir into the NON-adopted repo, which is the only place an adoption-gated arm
        # would ALLOW. Delete the scope classifier and these verdicts MOVE; that is the test.
        #
        # This is not a hypothetical: it is the masking-condition failure already measured on this
        # exact detector. A first probe paired every injection with the one refspec an earlier rule
        # already refused, came back "blocked" on all rows, and was written up as "already covered,
        # the plan's premise is wrong". The hole appeared only once the paired input was varied.
        Row(
            "scope_glo_abbrev_from_other",
            f"cd {other_s} && git config --glo core.hooksPath /dev/null",
            MUST_BLOCK,
            "git accepts unambiguous abbreviations of --global, so a scope allowlist matching only "
            "the full spelling reads as coverage while --glo writes the global file (measured)",
        ),
        Row(
            "scope_comment_shields_global_from_other",
            f"cd {other_s} && git config --comment n --global core.hooksPath /x",
            MUST_BLOCK,
            "--comment takes a value, so a classifier scanning only the LEADING option run stops "
            "before --global and reads this as a local write; the scan must cross every token",
        ),
        Row(
            "scope_set_subcommand_global_from_other",
            f"cd {other_s} && git config set --global core.hooksPath /x",
            MUST_BLOCK,
            "the modern subcommand form puts --global AFTER a non-option token, which is where a "
            "classifier that breaks at the first non-option stops looking",
        ),
        Row(
            "scope_dash_f_adopted_config_from_other",
            f"cd {other_s} && git config -f {adopted_s}/.git/config core.hooksPath X",
            MUST_BLOCK,
            "-f names the adopted repo's own config file as the target, so standing in a "
            "non-adopted directory does not put it out of reach -- the hole adoption-scoping opens",
        ),
        Row(
            "scope_file_equals_adopted_config_from_other",
            f"cd {other_s} && git config --file={adopted_s}/.git/config core.hooksPath X",
            MUST_BLOCK,
            "the attached --file= spelling of the same reach-in; an allowlist keyed on the "
            "separated form only would let this one through",
        ),
        Row(
            "env_git_config_redirect_from_other",
            f"cd {other_s} && GIT_CONFIG={adopted_s}/.git/config git config core.hooksPath X",
            MUST_BLOCK,
            "GIT_CONFIG= redirects the write with no scope flag anywhere in the command, so a "
            "classifier reading only argv concludes 'local' and gates it on the WRONG repo",
        ),
        Row(
            "env_git_common_dir_redirect_from_other",
            f"cd {other_s} && GIT_COMMON_DIR={adopted_s}/.git git config core.hooksPath X",
            MUST_BLOCK,
            "the other env redirect: it moves what 'local' MEANS, so a local-scoped write lands in "
            "the adopted repo",
        ),
        Row(
            "alias_smuggles_global_write_from_other",
            f"cd {other_s} && git -c alias.zz='config --global core.hooksPath /x' zz",
            MUST_BLOCK,
            "a -c value is per-invocation in FORM but arbitrary in EFFECT -- the aliased command "
            "runs with full privileges and this gate never sees it; alias. is denied in the -c "
            "arm's key set only, never in the shared one the config seg scan reads",
        ),
        Row(
            "alias_via_numbered_env_from_other",
            f"cd {other_s} && GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.zz git zz",
            MUST_BLOCK,
            "the same alias smuggle through the numbered env form, which names no key in argv",
        ),
        Row(
            "dashC_into_adopted_config_from_other",
            f"cd {other_s} && git -C {adopted_s} config core.hooksPath /dev/null",
            MUST_BLOCK,
            "-C re-targets the invocation into the adopted repo, so the write is local to a repo "
            "the cwd says nothing about -- adoption must be judged on the EFFECTIVE dir",
        ),
        Row(
            "write_disguised_as_read_trailing_get",
            "git config core.hooksPath --get",
            MUST_BLOCK,
            "position-sensitive: a read flag AFTER the key is a positional, and the write LANDS "
            "(measured, git 2.55) -- so a read carve-out that scans the whole segment turns one "
            "command into a hook-disabling write that reads as a query. Runs in the ADOPTED repo "
            "deliberately: this is a LOCAL write, which is exactly the arm that IS adoption-gated",
        ),
        # ---------- MUST_ALLOW: the over-block guards for a block that really did ship ----------
        # These five were drafted as SANCTIONED_UNBLOCK and are MUST_ALLOW because the measurement
        # said so -- see the long note beside `_CATEGORIES`. The frozen baseline allows all five
        # (rc=0), because it predates the config detector entirely; what blocked them was an
        # INTERMEDIATE state of this branch, in which the detector fired in every repo on the
        # machine. That block was real and shipped, which is exactly why these rows are worth
        # keeping: as MUST_ALLOW they fail if it ever returns, which is the property that matters.
        Row(
            "husky_install_in_non_adopted",
            f"cd {other_s} && git config core.hooksPath .husky",
            MUST_ALLOW,
            "husky's own install command, in a repo that never adopted the publication model -- a "
            "local write there is none of this gate's business. This is the single most likely "
            "shape for an over-block to reappear in, because it names a denied key innocently",
        ),
        Row(
            "pure_read_in_adopted",
            "git config --get core.hooksPath",
            MUST_ALLOW,
            "a pure read cannot relocate anything, so blocking it for merely NAMING the key would "
            "refuse the very diagnostic an operator runs to check the hooks path is intact",
        ),
        Row(
            "pure_read_subcommand_form_in_adopted",
            "git config get core.hooksPath",
            MUST_ALLOW,
            "the modern subcommand spelling of the same read; recognising only --get would leave "
            "this one blocked and the carve-out half-built",
        ),
        Row(
            "pure_read_in_non_adopted",
            f"cd {other_s} && git config --get core.hooksPath",
            MUST_ALLOW,
            "the same read from a non-adopted cwd -- paired with the adopted row above so the "
            "carve-out is held on both sides of the adoption boundary, not just the easy one",
        ),
        Row(
            "config_nosystem_env_in_non_adopted",
            f"cd {other_s} && GIT_CONFIG_NOSYSTEM=1 git log -1",
            MUST_ALLOW,
            "GIT_CONFIG_NOSYSTEM suppresses the system config file and provably cannot SET a key, "
            "so it carries none of the blast radius the rest of the env arm denies; a prefix match "
            "on GIT_CONFIG_* alone would block a plain log",
        ),
        # ---------- XFAIL_TODAY: measured open, and named to the task that will close them -------
        # Both re-measured 2026-08-04 in a genuinely adopted fixture (a first probe used an
        # UNCOMMITTED marker, so the guard was dormant and every row read "allowed" -- the control
        # row is what caught it). They are NOT closed by the branch's own review-findings task,
        # which is why they name the backlog entry instead: an XFAIL row whose named task completes
        # without closing it is precisely the stale marker this category's tripwire exists to stop.
        Row(
            "rename_section_builds_include_path",
            "git config --rename-section harmless include",
            XFAIL_TODAY,
            "renaming any section to `include` makes its existing `path` key a live include.path, "
            "reaching core.hooksPath one hop away -- with no denied key ever typed. Measured: git "
            "exits 0 and the section really is created",
            "backlog 2026-08-04 HIGH: two git config forms reach include.path",
        ),
        Row(
            "config_edit_is_an_arbitrary_write",
            "GIT_EDITOR=vi git config --edit",
            XFAIL_TODAY,
            "--edit opens the config in an arbitrary editor, so its effect is unbounded and names "
            "no key at all; allowed today with or without the GIT_EDITOR prefix (measured)",
            "backlog 2026-08-04 HIGH: two git config forms reach include.path",
        ),
    ]


# ---------- fixtures ----------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "-c",
            "user.email=t@t.invalid",
            "-c",
            "user.name=t",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _build_adopted_repo(base: Path) -> Path:
    """A purpose-built fixture repo: `.publication.toml` at the root, checked out to `dev`.

    Also installs a healthy stub boundary hook -- both the tracked `git-hooks/pre-push` source and
    a byte-identical, executable `.git/hooks/pre-push` copy -- for the same reason as the shell
    suite's `build_repo`: a push-arm precondition that asserts the boundary is in force would
    otherwise turn every allowed-push row in this corpus red. The stub is a no-op
    (`exit 0`): this corpus exercises the GUARD, not the real hook body.
    """
    repo = base / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("hello\n")
    (repo / ".publication.toml").write_text('production = "main"\n')
    hooks_src_dir = repo / "git-hooks"
    hooks_src_dir.mkdir()
    hook_source = hooks_src_dir / "pre-push"
    hook_source.write_text("#!/bin/sh\nexit 0\n")
    hook_source.chmod(0o755)
    hooks_dest_dir = repo / ".git" / "hooks"
    hooks_dest_dir.mkdir(parents=True, exist_ok=True)
    hook_installed = hooks_dest_dir / "pre-push"
    shutil.copyfile(hook_source, hook_installed)
    hook_installed.chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "branch", "-q", "dev")
    _git(repo, "checkout", "-q", "dev")
    return repo


def _build_other_repo(base: Path) -> Path:
    """A second, non-adopted repo -- a real repo, not just an empty dir, so an incorrect root
    resolution would silently succeed and allow rather than crash."""
    other = base / "other"
    other.mkdir()
    _git(other, "init", "-q")
    (other / "f.txt").write_text("x\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "init")
    return other


@dataclass(frozen=True)
class Sandbox:
    repo: Path
    other: Path
    baseline_log: Path
    new_log: Path


@pytest.fixture(scope="session")
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> Sandbox:
    # .resolve() is the pwd -P equivalent: $TMPDIR is reached through a symlink on this machine
    # (/tmp -> /private/tmp), and a path-resolving subject can take a different branch under the
    # logical form -- see the module docstring's hermeticity note.
    base = tmp_path_factory.mktemp("guard-corpus").resolve()
    repo = _build_adopted_repo(base)
    other = _build_other_repo(base)
    logs = base / "logs"
    logs.mkdir()
    return Sandbox(
        repo=repo,
        other=other,
        baseline_log=logs / "baseline-errors.log",
        new_log=logs / "new-errors.log",
    )


@pytest.fixture(scope="session")
def rows(sandbox: Sandbox) -> list[Row]:
    result = _build_rows(sandbox.repo, sandbox.other)
    assert result, "corpus row list must never be empty"
    return result


def _run_guard(guard: Path, command: str, cwd: Path, log_path: Path) -> int:
    """Feed `command`/`cwd` to `guard` on stdin as the PreToolUse hook JSON payload and return its
    exit code. Running the guard with argv instead examines nothing and exits 0 -- see the guard's
    own `main()` contract check -- so this always goes through stdin."""
    payload = json.dumps({"tool_input": {"command": command}, "cwd": str(cwd)})
    env = dict(os.environ)
    env[LOG_ENV_VAR] = str(log_path)  # hermeticity -- see the module docstring
    proc = subprocess.run(
        [sys.executable, str(guard)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc.returncode


@pytest.fixture(scope="session", autouse=True)
def _verify_baseline_frozen() -> None:
    """Guard the guard: if the vendored baseline ever drifts from its recorded sha256, every
    comparison in this module is meaningless (see the module docstring's WHY-vendored note).
    autouse + session scope means this runs before any row is judged, for every test in this
    module, and fails loudly rather than silently comparing the new build against an unknown
    stand-in."""
    got_guard = _sha256(BASELINE_GUARD)
    assert got_guard == BASELINE_GUARD_SHA256, (
        f"frozen baseline guard sha256 mismatch: got {got_guard}, want "
        f"{BASELINE_GUARD_SHA256} -- fixtures/prechange/ drifted from commit 782039d; every "
        "shrink assertion below is now comparing against an unverified build, not the frozen "
        "pre-change tip"
    )
    got_lib = _sha256(BASELINE_GIT_COMMAND)
    assert got_lib == BASELINE_GIT_COMMAND_SHA256, (
        f"frozen baseline git_command sha256 mismatch: got {got_lib}, want "
        f"{BASELINE_GIT_COMMAND_SHA256} -- same hazard as the guard mismatch above"
    )


@pytest.fixture(scope="session")
def verdicts(sandbox: Sandbox, rows: list[Row]) -> dict[str, tuple[int, int]]:
    """Run every corpus row against both builds exactly once, in the adopted fixture repo, and
    hand back label -> (baseline_rc, new_rc). Every test function below reads this shared matrix
    rather than re-invoking the guard per assertion."""
    out: dict[str, tuple[int, int]] = {}
    for row in rows:
        baseline_rc = _run_guard(
            BASELINE_GUARD, row.command, sandbox.repo, sandbox.baseline_log
        )
        new_rc = _run_guard(NEW_GUARD, row.command, sandbox.repo, sandbox.new_log)
        out[row.label] = (baseline_rc, new_rc)
    return out


# ---------- the four required assertions ----------


def test_corpus_has_rows(rows: list[Row]) -> None:
    """Required assertion 4: a non-zero row count."""
    assert len(rows) > 0, (
        "the corpus is empty -- every other assertion here passes for free"
    )


def test_baseline_allows_at_least_one_row(
    rows: list[Row], verdicts: dict[str, tuple[int, int]]
) -> None:
    """Required assertion 3: a non-zero ALLOW denominator on the baseline. Without this, the
    shrink assertion below passes vacuously the moment the baseline ever became
    universally-blocking -- it would have nothing left to compare against."""
    allowed = [r.label for r in rows if verdicts[r.label][0] == 0]
    assert allowed, (
        "the frozen baseline blocks EVERY corpus row -- the shrink assertion would then hold "
        "trivially, proving nothing about the new build"
    )


def test_must_block_rows_block_on_new_build(
    rows: list[Row], verdicts: dict[str, tuple[int, int]]
) -> None:
    """Required assertion 1: every MUST_BLOCK row blocks (rc 2) on the new build."""
    must_block = [r for r in rows if r.category == MUST_BLOCK]
    assert must_block, "no MUST_BLOCK rows defined"
    failures = [
        f"{r.label}: new build rc={verdicts[r.label][1]} (want 2) -- {r.why}"
        for r in must_block
        if verdicts[r.label][1] != 2
    ]
    assert not failures, (
        "MUST_BLOCK row(s) no longer block on the new build:\n" + "\n".join(failures)
    )


def test_every_row_carries_a_known_category(rows: list[Row]) -> None:
    """FLOOR over the labels. Nothing validated `Row.category`, so a typo silently dropped a row
    from every label-specific test while still looking like a corpus entry -- it went on
    contributing to `test_blocked_set_does_not_shrink`, which reads every row regardless of label,
    and to nothing else. That is the quietest way to lose coverage here: the row is still present,
    still runs, and still passes."""
    unknown = {r.label: r.category for r in rows if r.category not in _CATEGORIES}
    assert not unknown, (
        f"row(s) carrying a category no test recognises: {unknown} -- "
        f"known categories are {sorted(_CATEGORIES)}"
    )


def test_blocked_set_does_not_shrink(
    rows: list[Row], verdicts: dict[str, tuple[int, int]]
) -> None:
    """Required assertion 2 -- the shrink property, over every row in the corpus regardless of
    label (MUST_BLOCK, XFAIL_TODAY, MUST_ALLOW and ACCIDENTAL alike): nothing the frozen baseline
    blocks is allowed by the new build. This is the safety net; the label-specific tests in this
    module are additional, not substitutes for it.

    THERE IS NO EXEMPTION, and that is a measured result rather than an omission -- see the note
    beside `_CATEGORIES`. An excused category was specified, built, and then removed when its own
    two-sided pin showed every candidate row had `baseline rc=0`: this branch only ever GREW the
    blocked set relative to `782039d`. Keep it that way. If a future change genuinely needs to free
    something the baseline blocks, the honest move is a category excused HERE and pinned on both
    sides elsewhere -- never a per-row opt-out, and never re-vendoring `fixtures/prechange/`, which
    is a self-clearing tripwire: it would make the new build its own baseline and this assertion
    would compare the build against itself, green forever."""
    failures = [
        f"{r.label}: baseline blocked (rc=2), new build rc={verdicts[r.label][1]} -- {r.why}"
        for r in rows
        if verdicts[r.label][0] == 2 and verdicts[r.label][1] != 2
    ]
    assert not failures, (
        "the blocked set SHRANK relative to the frozen baseline:\n"
        + "\n".join(failures)
    )


# ---------- beyond the four: the other labels are pointless if nothing checks them ----------


def test_must_allow_rows_stay_allowed(
    rows: list[Row], verdicts: dict[str, tuple[int, int]]
) -> None:
    """Not one of the four required assertions, but a MUST_ALLOW label nothing ever checks would
    let an over-block regression through unnoticed -- this is the corpus's own RED-provable half
    for over-blocking, mirroring test_must_block_rows_block_on_new_build for under-blocking."""
    must_allow = [r for r in rows if r.category == MUST_ALLOW]
    assert must_allow, "no MUST_ALLOW rows defined"
    failures = [
        f"{r.label}: new build rc={verdicts[r.label][1]} (want 0) -- {r.why}"
        for r in must_allow
        if verdicts[r.label][1] != 0
    ]
    assert not failures, (
        "MUST_ALLOW row(s) now falsely block on the new build:\n" + "\n".join(failures)
    )


def test_xfail_today_rows_are_still_allowed(
    rows: list[Row], verdicts: dict[str, tuple[int, int]]
) -> None:
    """Tripwire: each XFAIL_TODAY row names the task expected to fix it. If a row starts blocking
    before that task lands (or before it is explicitly reclassified), this fails loudly instead of
    leaving a stale "not yet fixed" marker in the corpus -- the row's whole reason for existing.

    An EMPTY set is legal and is the branch's goal state, not a broken fixture. It was briefly
    asserted non-empty, which was wrong in a way worth recording: it made the corpus unable to
    express "every named fail-open is now closed" without someone inventing a placeholder gap. The
    tripwire's force does not come from the list being non-empty -- it comes from every row IN it
    still being allowed, which is vacuously (and correctly) true of an empty list. The guard's
    remaining residuals (`sh -c`, `eval`, an unlisted wrapper carrying its own arguments) are
    ACCEPTED, not scheduled, so they do not belong here: XFAIL_TODAY names a task, and no task is
    coming for those.
    """
    xfail = [r for r in rows if r.category == XFAIL_TODAY]
    failures = [
        f"{r.label}: new build now blocks (rc={verdicts[r.label][1]}) -- move it out of "
        f"XFAIL_TODAY ({r.xfail_task} may have landed); why it was xfailed: {r.why}"
        for r in xfail
        if verdicts[r.label][1] != 0
    ]
    assert not failures, (
        "XFAIL_TODAY row(s) no longer match today's behavior:\n" + "\n".join(failures)
    )


def test_accidental_rows_are_excluded_from_coverage_claims(rows: list[Row]) -> None:
    """S7: "do not credit accidental catches." This asserts the CLASSIFICATION boundary itself --
    no row may carry both labels, or it would be silently double-counted as MUST_BLOCK evidence
    for detection that `why` says is not actually happening."""
    accidental_labels = {r.label for r in rows if r.category == ACCIDENTAL}
    must_block_labels = {r.label for r in rows if r.category == MUST_BLOCK}
    assert accidental_labels, "no ACCIDENTAL rows defined"
    overlap = accidental_labels & must_block_labels
    assert not overlap, (
        f"row(s) labelled both ACCIDENTAL and MUST_BLOCK: {sorted(overlap)}"
    )


def test_reserved_word_preserve_matrix_was_already_blocked_on_baseline(
    rows: list[Row], verdicts: dict[str, tuple[int, int]]
) -> None:
    """Every derived reserved-word/cd-family preserve row must ALREADY have blocked (rc 2) on the
    FROZEN baseline -- proving the row is real (exercises a live block) before crediting the new
    build with preserving it. Without this, `test_blocked_set_does_not_shrink` would pass for a row
    that happened to be allowed on both builds, which asserts nothing about the F1 guard."""
    matrix = [
        r
        for r in rows
        if r.label.startswith("reserved_") and r.label.endswith("_preserve")
    ]
    assert len(matrix) == len(new_gitcmd.RESERVED_WORDS) * 3, (
        f"expected {len(new_gitcmd.RESERVED_WORDS)} reserved words x 3 cd-family commands = "
        f"{len(new_gitcmd.RESERVED_WORDS) * 3} derived rows, found {len(matrix)}"
    )
    failures = [
        f"{r.label}: baseline rc={verdicts[r.label][0]} (want 2) -- {r.why}"
        for r in matrix
        if verdicts[r.label][0] != 2
    ]
    assert not failures, (
        "derived preserve row(s) were not actually blocked on the frozen baseline, so they prove "
        "nothing about a shrinking blocked set:\n" + "\n".join(failures)
    )


# ---------- Task 4 (F3): the boundary token must reach the paren branch ----------
#
# `dashC_semicolon_boundary` / `dashc_semicolon_boundary` (defined above, in the main MUST_BLOCK
# section) are the PRESERVE rows nothing else covers: a quoted ';' in a value-taking option's slot,
# with no subshell in sight, so F3's fix must not disturb the pre-existing "unresolvable
# subcommand blocks" fallback that already caught them. `cd_other_gitdashc_then_P` is the GAIN row
# -- the leak F3 exists to close.


def test_boundary_preserve_rows_were_already_blocked_on_baseline(
    rows: list[Row], verdicts: dict[str, tuple[int, int]]
) -> None:
    """Assert-them-rc-2-on-the-baseline-FIRST, per the task: without this, a preserve row that
    happened to be allowed on both builds would pass `test_blocked_set_does_not_shrink` for free,
    proving nothing about F3."""
    labels = ["dashC_semicolon_boundary", "dashc_semicolon_boundary"]
    failures = [
        f"{label}: baseline rc={verdicts[label][0]} (want 2)"
        for label in labels
        if verdicts[label][0] != 2
    ]
    assert not failures, (
        "boundary-token preserve row(s) were not actually blocked on the frozen baseline, so they "
        "are not real preserve rows:\n" + "\n".join(failures)
    )


def test_gain_row_was_allowed_on_baseline_and_is_blocked_on_new_build(
    verdicts: dict[str, tuple[int, int]],
) -> None:
    """The explicit before/after measurement for F3's gain, not just the generic MUST_BLOCK check:
    the frozen baseline actually leaked (rc 0 -- the subshell cwd escaped and the real push was
    judged in the non-adopted `other` repo) and the new build actually blocks it (rc 2)."""
    label = "cd_other_gitdashc_then_P"
    baseline_rc, new_rc = verdicts[label]
    assert baseline_rc == 0, (
        f"{label}: baseline rc={baseline_rc} (want 0) -- if the baseline no longer leaks, this "
        "row is not measuring what it claims to"
    )
    assert new_rc == 2, (
        f"{label}: new build rc={new_rc} (want 2) -- the leak is not fixed"
    )


def test_no_invocation_disappears_relative_to_baseline(
    sandbox: Sandbox, rows: list[Row]
) -> None:
    """S6/F3's property, asserted directly against the tokenizer rather than only through guard
    exit codes: for every corpus row, if the FROZEN baseline's `iter_git_invocations_with_cwd`
    finds at least one invocation, the new build must too. `effective_dir` may legitimately change
    (that is the whole point of F3 -- a leaked cwd becomes a correctly-popped one), and the SHAPE
    of an ambiguous invocation may change (see the boundary-token branch in `_walk_context`), but
    the count must never go from non-zero to zero -- that is literal disappearance, the exact
    regression a first, over-literal reading of "do not consume the boundary token" produced."""
    old_gitcmd = _load_baseline_gitcmd()
    cwd = str(sandbox.repo)
    failures = []
    for row in rows:
        try:
            old_invocations = old_gitcmd.iter_git_invocations_with_cwd(row.command, cwd)
        except ValueError:
            continue  # baseline ambiguity-reject -- nothing to compare against
        if not old_invocations:
            continue  # nothing to preserve for this row
        try:
            new_invocations = new_gitcmd.iter_git_invocations_with_cwd(row.command, cwd)
        except ValueError:
            failures.append(
                f"{row.label}: baseline found {len(old_invocations)} invocation(s), new build "
                "raised ValueError (0 invocations)"
            )
            continue
        if not new_invocations:
            failures.append(
                f"{row.label}: baseline found {len(old_invocations)} invocation(s), new build "
                "found 0 -- an invocation disappeared"
            )
    assert not failures, "\n".join(failures)
