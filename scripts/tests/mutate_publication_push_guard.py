#!/usr/bin/env python3
# Script: mutate_publication_push_guard.py
# Purpose: Mutation campaign for publication-push-guard.py — prove its suite constrains the
#          fail-closed behaviour it exists to guarantee, not merely the happy path
# Usage: scripts/tests/mutate_publication_push_guard.py
"""Mutate the publication push-guard and require its own suite to catch every row.

Written after a measured FAIL-OPEN that no suite caught: an options-only git invocation
(`git --version`) inside a subshell stole the closing `)` from the tokenizer's paren branch, so
`(cd /elsewhere && git --version) && git push origin dev` was ALLOWED against an adopted repo.
The suite already pinned that hazard class — every one of its rows put a NON-git command in the
subshell, so none could reach the theft. Rows that agree with each other are not coverage.

Each row below names one property the guard must hold. A SURVIVOR means the suite does not
constrain that property — not that the mutant is harmless.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "publication-push-guard.py"
SUITE = ["bash", str(REPO / "scripts" / "tests" / "test_publication_push_guard.sh")]

MUTATIONS = [
    # ---- the two-axis refusal split (a refusal must not misdescribe what it judged)
    mutate.Mutation(
        "every refusal blames a push again, including ones that found none",
        "        if reason.is_push:",
        "        if True:",
    ),
    mutate.Mutation(
        "an unjudgeable PUSH is de-alarmed — the direction that softens a real refusal",
        "        if reason.is_push:",
        "        if False:",
    ),
    mutate.Mutation(
        "a --git-dir override stops being reported as a push",
        '            "the command carries --git-dir/--work-tree or a GIT_DIR= assignment — root unknown",\n            sub == "push",',
        '            "the command carries --git-dir/--work-tree or a GIT_DIR= assignment — root unknown",\n            False,',
    ),
    mutate.Mutation(
        "an unresolvable root stops being reported as a push",
        '        return Block(detail, sub == "push")',
        "        return Block(detail, False)",
    ),
    # ---- designed parse ambiguity vs a genuine internal fault
    mutate.Mutation(
        "parse ambiguity is labelled a guard BUG again and pollutes the diagnostic log",
        "    except AmbiguousCommand as exc:",
        "    except _NeverRaised as exc:  # noqa: F821",
    ),
    # This one reproduces a regression the suite ACTUALLY caught during this change: a bare
    # `except ValueError` in main swallows the forced import-time ValueError the internal-error
    # rows depend on, silencing the diagnostic log for a genuine fault. It survived review as a
    # plausible simplification and only the suite said otherwise.
    mutate.Mutation(
        "the ambiguity catch widens to every ValueError, swallowing genuine faults",
        "    except AmbiguousCommand as exc:",
        "    except ValueError as exc:",
    ),
    # ---- the allowlist: neither too narrow (false blocks) nor too wide (a permitted push)
    mutate.Mutation(
        "show-ref leaves the allowlist, so an unexpanded -C refuses it again",
        '        "show-ref",\n        "count-objects",',
        '        "count-objects",',
    ),
    mutate.Mutation(
        "a push-capable command joins the allowlist",
        '        "show-ref",\n        "count-objects",',
        '        "show-ref",\n        "send-pack",\n        "count-objects",',
    ),
    # Deleting `sub != "push"` alone is a NO-OP — `push` is not in the allowlist, so the guard
    # cannot change any outcome on its own, and a row testing it would pass forever while pinning
    # nothing. (Measured: it survived, and the survivor was reporting inert code, not a weak
    # suite.) The mutation that CAN decide something is putting `push` in the set, which is the
    # fail-open that redundant guard exists to survive.
    mutate.Mutation(
        "push itself joins the allowlist, so every push short-circuits unjudged",
        '        "show-ref",\n        "count-objects",',
        '        "push",\n        "show-ref",\n        "count-objects",',
    ),
    # ---- fail-closed posture: each of these turns a refusal into an allow
    mutate.Mutation(
        "an unresolvable repo root stops blocking",
        '        return Block(detail, sub == "push")',
        "        return None",
    ),
    mutate.Mutation(
        "an unknown effective cwd stops blocking",
        '            "the effective working directory could not be resolved (cd/pushd target unknown)",\n            sub == "push",\n        )',
        '            "the effective working directory could not be resolved (cd/pushd target unknown)",\n            sub == "push",\n        ) and None',
    ),
    mutate.Mutation(
        "an unresolvable alias chain is treated as 'not a push'",
        "        return Block(\n            f\"subcommand '{sub}' resolves to an unverifiable or unresolvable alias chain\",\n            False,\n        )",
        "        return None",
    ),
]

if __name__ == "__main__":
    sys.exit(mutate.run(SUBJECT, SUITE, MUTATIONS))
