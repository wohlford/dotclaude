#!/usr/bin/env python3
"""Mutation campaign for scripts/memory-status-check.py, driven by scripts/lib/mutate.py.

Deliberately NOT named `test_*`: it mutates a tracked file in place, so pytest must not collect
it. Run it on demand through `scripts/run-long.sh` (the suite takes about three minutes, measured 170 to 210 s, and a campaign runs it
once per row) and never while editing the subject, since the restore would clobber your edits:

    ./scripts/tests/mutate_memory_status_check.py

Every row breaks ONE rule or one piece of plumbing and requires
`scripts/tests/test_memory_status_check.py` to notice. A survivor means the suite would not notice
the tool going quietly optimistic: strengthen the suite row, never reword the mutation. Each `old`
string occurs exactly once in the subject (`scripts/mutation-anchors-check.py` enforces it).

Rows that exist because a measurement or a ruling named the defect:

* the veto row is revision 1 of the evidence rule ("ignore off-dev SHAs"), which a rewound-refs
  measurement showed reading a true "NOT promoted" as stale when only a base commit was visible;
* the range-ancestry and no-op-range rows pin the ruling that equal endpoints are a legal PASS and
  a reversed pair is an error;
* the stdout-failure rows are the exit-120 hazard: a second flush failure at interpreter
  shutdown, after a closed pipe or a read-only descriptor;
* the later rows pin findings from reviewing the finished tool: a base marker followed by a
  non-connector word, an unreadable `merge-base`, a wrapped line that begins `Status`, a Status
  line under a heading or after a fence, an example inside a code fence, digits glued to a
  separator, an unpaired or line-wrapped backtick, an index link that is not a direct child, a
  signal before the header, a git call that outlives a signal, a stdout that cannot encode the
  report, and a documented command run outside the dev repository.

Rows whose outcome depends on elapsed time (a loaded machine can flip them, so read a survivor or
a timeout there against `uptime` first): `quote-spans-rescanned`, `link-regexes-unbounded` and
`per-call-timeout-unclamped`. The signal and reaping rows wait on real processes, and the cost
rows on the clock. Observed: under another user's load (about 8) the baseline of a 158-row suite failed once and
passed on an unchanged re-run, taking 311 s against 118 s idle.

Pieces of the subject deliberately NOT mutated, and why:

* `GIT_TIMEOUT`, `DEFAULT_DEADLINE`, `CLAUSE_REACH`, `ANCHOR_REACH`. Each is a bound whose every
  value yields a truthful report on the fixtures; a row would assert on the number, not on a rule.
* An empty `--range` endpoint accepted: the mutant is inert, because `rev-parse` of an empty name
  already fails, and the ends must be commit ids before it is reached.
* The signal handler's `os.write` (a re-entrant `print` is not reproducible deterministically) and
  the `check_deadline()` call in the judging loop (CPU-bound judging cannot be made to outlast a
  deadline without a clock the suite does not own); both are defences measured by reasoning.
* The `twins` set's `all()` over several equivalent dev commits. The fixture has one twin per
  tree, so the quantifier cannot be observed; the rule is stated in the spec, not exercised here.
* The `and "promoted" in S.classes` guard on the production control in `self_check`. It is
  equivalent: under `--only published` no production head is ever resolved, so `ctx.prod_head`
  is None and the guard cannot change an outcome. Its watermark twin is observable and mutated.
* `GIT_INDEX_FILE` and `GIT_NAMESPACE` in `AMBIENT_GIT_ENV`: no read the tool makes depends on
  either, so no row can tell whether they are scrubbed.
* Letting `PUBLISHED` report as a ceiling label: it takes three coordinated edits (the label
  pattern, the claim map and the higher-label map) and a row is one replacement;
  `test_a_published_label_is_the_top_rung_and_never_reported` pins the behaviour.
* The skills' documented commands. The campaign has one subject, the tool; deleting `--only`,
  `--range` or `--since` from a scratch copy of the skill text turns
  `test_each_documented_block_passes_its_flags_through_to_the_tool` red (measured by hand).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
import mutate  # noqa: E402

SUBJECT = REPO / "scripts" / "memory-status-check.py"
SUITE = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-x",
    "-p",
    "no:cacheprovider",
    str(REPO / "scripts" / "tests" / "test_memory_status_check.py"),
]

M = mutate.Mutation

MUTATIONS = [
    # ---- the evidence rule
    M(
        "veto-dropped: an unlanded commit abstains instead of vetoing (revision 1's rule)",
        "        else:\n            items.append(False)\n        shown.append(oid)",
        "        else:\n            continue\n        shown.append(oid)",
    ),
    M(
        "any-for-all: one attained item makes the claim stale",
        'Verdict("STALE" if all(items) else "LIVE", "", shown, anchors)',
        'Verdict("STALE" if any(items) else "LIVE", "", shown, anchors)',
    ),
    M(
        "no-tree-map: no unlanded commit is ever equivalent",
        '    return [\n        d for d in ctx.trees.get(tree or "", []) if not is_ancestor(ctx.dev, d, oid)\n    ]',
        "    return []",
    ),
    M(
        "equivalence-ignores-ancestry: a tip tree-equal to its own base counts as that base",
        'd for d in ctx.trees.get(tree or "", []) if not is_ancestor(ctx.dev, d, oid)',
        'd for d in ctx.trees.get(tree or "", [])',
    ),
    M(
        "no-subsumption: a pre-rebase commit under the oracle vetoes",
        "        elif any(\n            o != oid and twins[o] and is_ancestor(ctx.dev, oid, o) for o in unlanded\n        ):",
        "        elif False:",
    ),
    M(
        "context-markers-off: a base counts as the work",
        '        is_context = after_base or after.startswith("..")',
        '        is_context = after.startswith("..")',
    ),
    M(
        "range-left-kept: the left side of a..b counts as evidence",
        '        is_context = after_base or after.startswith("..")',
        "        is_context = after_base",
    ),
    M(
        "tree-token-vetoes: a tree or blob is read as a missing commit",
        '        elif kind in ("missing", "ambiguous") and is_backticked(text, tok):',
        '        elif kind != "commit" and is_backticked(text, tok):',
    ),
    M(
        "missing-ignored: a backticked token no object answers to abstains",
        '        elif kind in ("missing", "ambiguous") and is_backticked(text, tok):',
        "        elif False:",
    ),
    M(
        "missing-never-vetoes-in-a-mix: the veto row is dropped",
        "    if missing:\n        items.append(False)",
        "    if missing:\n        pass",
    ),
    M(
        "missing-alone-is-live: a lone unresolved SHA is not skipped by name",
        '    if missing and not items:\n        return Verdict("SKIP", "sha-unresolved", [], [])',
        "    if False:\n        pass",
    ),
    M(
        "context-only-lost: a paragraph naming only a base is not skipped by name",
        '        if saw_context and not tag_tokens(text):\n            return Verdict("SKIP", "context-only", [], [])',
        "        if False:\n            pass",
    ),
    M(
        "main-side-attains-promoted: a main-side commit decides the promoted class",
        '            if klass == "published":\n                items.append(True)\n            else:\n                continue',
        "            items.append(True)",
    ),
    M(
        "tags-judged-against-the-watermark: a tag is tested in the wrong universe",
        "                items.append(oid in ctx.origin_set)",
        "                items.append(oid in ctx.wm_set)",
    ),
    M(
        "classes-swapped: promoted is judged on the watermark and published on production",
        '    return ctx.prod_set if klass == "promoted" else ctx.wm_set',
        '    return ctx.wm_set if klass == "promoted" else ctx.prod_set',
    ),
    M(
        "pure-digit-sha-dropped: a SHA with no letter is not a SHA",
        'HEX_TOKEN = re.compile(r"(?<![0-9A-Za-z_])[0-9a-f]{7,40}(?![0-9A-Za-z_])")',
        'HEX_TOKEN = re.compile(r"(?<![0-9A-Za-z_])(?=[0-9]*[a-f])[0-9a-f]{7,40}(?![0-9A-Za-z_])")',
    ),
    # ---- claims and their exclusions
    M(
        "case-sensitive-promoted: a capitalisation-only match",
        '(?:promoted|propagated)\\b", re.IGNORECASE',
        '(?:promoted|propagated)\\b", 0',
    ),
    M(
        "case-sensitive-published: a capitalisation-only match",
        '(?:published|pushed)\\b", re.IGNORECASE',
        '(?:published|pushed)\\b", 0',
    ),
    M(
        "link-blanking-off: a link target naming a claim is a claim",
        '    scan = LINK_TARGET.sub(lambda m: " " * len(m.group(0)), scan)',
        "    scan = scan",
    ),
    M(
        "count-exclusion-off: 0 unpublished is a claim",
        "    counted = COUNT_BEFORE.search(text[lo : m.start()])",
        "    counted = None",
    ),
    M(
        "count-matches-any-digit-token: 8/0/0); NOT published is a count",
        'COUNT_BEFORE = re.compile(r"(?<![\\w/=.:-])\\d+\\+?\\s+(?:[A-Za-z-]+\\s+)?$")',
        'COUNT_BEFORE = re.compile(r"\\d\\S*\\s+(?:[A-Za-z-]+\\s+)?$")',
    ),
    M(
        "noun-exclusion-off: unpushed tags is a claim",
        '    if text[m.start() : m.end() + 5].lower() == "unpushed tags":',
        "    if False:",
    ),
    M(
        "noun-exclusion-too-wide: unpushed commits is excluded too",
        '    if text[m.start() : m.end() + 5].lower() == "unpushed tags":',
        '    if text[m.start() : m.end() + 5].lower().startswith("unpushed"):',
    ),
    M(
        "quote-exclusion-off: a quoted claim is a claim",
        "    if is_quoted(text, m.start()):",
        "    if False:",
    ),
    M(
        "yaml-unwrap-off: the description's own quotes read as a quotation",
        "    if len(value) > 1 and value[0] == '\"' and value[-1] == '\"':",
        "    if False:",
    ),
    M(
        "anchor-exclusion-off: an anchored claim is judged as live",
        "    if ANCHOR.search(near):",
        "    if False:",
    ),
    M(
        "note-row-dropped: a silenced stale status stays invisible",
        '        noted = class_verdict(klass)\n        if noted.kind == "STALE" and in_range(ctx, noted.anchors):',
        "        noted = class_verdict(klass)\n        if False:",
    ),
    # ---- zones
    M(
        "backlog-scanned: BACKLOG.md is read as a record",
        "p.is_file() and p.name != BACKLOG_NAME",
        "p.is_file()",
    ),
    M(
        "lead-zone-is-the-whole-file: the first paragraph never ends",
        '        end = paragraph_end(lines, k, fenced)\n        zones.append(Zone("lead"',
        '        end = len(lines)\n        zones.append(Zone("lead"',
    ),
    M(
        "leading-heading-not-skipped: a status paragraph after a title is missed",
        '    if k < n and not fenced[k] and lines[k].lstrip().startswith("#"):\n        k += 1',
        "    if False:\n        k += 1",
    ),
    M(
        "status-scan-unbounded: a status section deep in the file is judged",
        "    while j < min(n, body + STATUS_SCAN_LINES):",
        "    while j < n:",
    ),
    M(
        "claim-line-is-the-zone-start: the line reported is the paragraph's first",
        '    return unit.zone.line + unit.zone.text.count("\\n", 0, claim.start)',
        "    return unit.zone.line",
    ),
    M(
        "index-read-as-a-topic: the index header prose is a lead zone",
        "        if path.name == INDEX_NAME:\n            entries = index_entries(text)",
        "        if False:\n            entries = index_entries(text)",
    ),
    M(
        "ceiling-higher-label-ignored: LANDED beside PROMOTED is still a ceiling",
        "        if HIGHER_LABEL[word].search(text):\n            continue",
        "        if False:\n            continue",
    ),
    M(
        "label-without-punctuation: a bare bold PROMOTED is a label",
        'LABEL = re.compile(r"\\*\\*(LANDED|PROMOTED)\\b(?=\\s*[.;:,\\u2014\\u2013])")',
        'LABEL = re.compile(r"\\*\\*(LANDED|PROMOTED)\\b")',
    ),
    M(
        "label-whitespace-intolerant: a space before the dash is not a label",
        'LABEL = re.compile(r"\\*\\*(LANDED|PROMOTED)\\b(?=\\s*[.;:,\\u2014\\u2013])")',
        'LABEL = re.compile(r"\\*\\*(LANDED|PROMOTED)\\b(?=[.;:,\\u2014\\u2013])")',
    ),
    M(
        "index-fix-replaces-the-label: the hint writes a new stored status",
        "            f'delete the label \"{label}\"{closing} (never replace it with another label)'",
        '            f"state PROMOTED instead of {label}"',
    ),
    # ---- verdict mechanics, applicability and the self-check
    M(
        "zones-floor-off: a parse that finds no zone reads as a clean store",
        "    if S.zones == 0 or (topics and S.topic_zones == 0):",
        "    if False:",
    ),
    M(
        "files-floor-off: an empty directory reads as a clean store",
        "    if not files:",
        "    if False:",
    ),
    M(
        "stale-is-a-pass: a stale unit does not fail the run",
        '    return finish("FAIL", 1) if S.stale else finish("PASS", 0)',
        '    return finish("PASS", 0)',
    ),
    M(
        "not-adopted-continues: another project's repo is judged",
        '        raise NotApplicable("not-adopted")',
        "        pass",
    ),
    M(
        "failed-status-read-is-absent: a git read that failed answers like a clean miss",
        "    if proc.returncode not in ok:",
        "    if False:",
    ),
    M(
        "only-ignored: --only does not narrow the classes",
        "    S.classes = (args.only,) if args.only else CLASSES",
        "    S.classes = CLASSES",
    ),
    M(
        "self-check-off: the object store is never probed",
        "    self_check(ctx)\n    return ctx",
        "    return ctx",
    ),
    M(
        "control-type-unchecked: a control that is not a commit is accepted",
        '        if kind != "commit":\n            raise ErrorExit(\n                "object-store-unreadable", f"{oid} is not a commit", S.classes\n            )',
        "        if False:\n            pass",
    ),
    M(
        "count-check-off: a short cat-file answer is accepted",
        "    if proc.returncode != 0 or len(lines) != len(names):",
        "    if False:",
    ),
    M(
        "production-is-dev-passes: the dev repo judges itself as production",
        "    if top_path == dev.resolve():",
        "    if False:",
    ),
    M(
        "root-comparison-dropped: every defaulted unrelated production is not-this-config",
        "    disjoint = not (dev_roots & prod_roots)",
        "    disjoint = True",
    ),
    M(
        "failed-root-read-is-disjoint: an unreadable lineage reads as unrelated",
        "    return found or None",
        "    return found",
    ),
    M(
        "unrelated-always-not-applicable: an explicit unrelated production is skipped",
        "    if disjoint and not args.production:",
        "    if disjoint:",
    ),
    M(
        "watermark-ancestry-dropped: any watermark object is accepted",
        "    if not (ok and is_ancestor(ctx.dev, ctx.watermark, ctx.dev_tip)):",
        "    if not ok:",
    ),
    M(
        "per-call-timeout-unclamped: one git call may outlive the run budget",
        "        out, err = proc.communicate(input=stdin, timeout=min(GIT_TIMEOUT, remaining))",
        "        out, err = proc.communicate(input=stdin, timeout=GIT_TIMEOUT)",
    ),
    M(
        "ambient-git-env-kept: GIT_DIR redirects every -C",
        "    env = {k: v for k, v in os.environ.items() if k not in AMBIENT_GIT_ENV}",
        "    env = dict(os.environ)",
    ),
    M(
        "ambient-scrub-original-three: only the first three variables are cleared",
        '    "GIT_COMMON_DIR",\n    "GIT_OBJECT_DIRECTORY",\n    "GIT_ALTERNATE_OBJECT_DIRECTORIES",\n    "GIT_NAMESPACE",\n    "GIT_REPLACE_REF_BASE",\n    "GIT_NO_REPLACE_OBJECTS",\n',
        "",
    ),
    # ---- range and since
    M(
        "range-ancestry-dropped: a reversed range is accepted",
        "    if not is_ancestor(ctx.dev, ends[0], ends[1]):",
        "    if False:",
    ),
    M(
        "equal-endpoints-an-error: a no-op promote cannot be re-run to PASS",
        '    ctx.range_text = f"{short(ends[0])}..{short(ends[1])}"',
        '    if ends[0] == ends[1]:\n        raise ErrorExit("range-invalid", "equal ends")\n'
        '    ctx.range_text = f"{short(ends[0])}..{short(ends[1])}"',
    ),
    M(
        "range-ignored: every stale unit is listed whatever the range",
        "        if not in_range(ctx, reach):",
        "        if False:",
    ),
    M(
        "outside-counts-toward-rc: a promote reports FAIL with nothing listed",
        "            S.outside += 1\n            return",
        "            S.outside += 1\n            S.stale += 1\n            return",
    ),
    M(
        "since-counts-published: the published class is timed from production's reflog",
        '        if "promoted" in stale:\n            S.promoted_anchors.append(verdicts["promoted"].anchors)',
        "        if stale:\n            S.promoted_anchors.append(verdicts[stale[0]].anchors)",
    ),
    M(
        "since-absent-reflog-is-zero: unknown prints as a clean zero",
        '        if not entries:\n            return "unknown"',
        '        if not entries:\n            return "0"',
    ),
    # ---- rows added by the plan review
    M(
        "wrap-blind-claim: a claim hard-wrapped across lines is missed",
        "(?:not[\\s-]+(?:yet[\\s-]+)?|un-?)(?:promoted",
        "(?:not[ -](?:yet )?|un-?)(?:promoted",
    ),
    M(
        "quote-spans-rescanned: a paragraph of N claims costs N times its length",
        "@lru_cache(maxsize=8)\ndef quote_index(text: str)",
        "def quote_index(text: str)",
    ),
    M(
        "range-names-accepted: a symbolic endpoint resolves in whatever repo it is run in",
        "    if not all(COMMIT_ID.fullmatch(p) for p in parts):",
        "    if False:",
    ),
    M(
        "range-commit-lookup-dropped: a hex end git cannot find falls through to the ancestry test",
        '        oid = out_of(ctx.dev, "rev-parse", "--verify", "-q", f"{name}^{{commit}}")',
        '        oid = out_of(ctx.dev, "rev-parse", "--verify", "-q", f"{name}^{{commit}}") or name',
    ),
    M(
        "range-commit-peel-dropped: a tree or blob id is accepted as a range end",
        '        oid = out_of(ctx.dev, "rev-parse", "--verify", "-q", f"{name}^{{commit}}")',
        '        oid = out_of(ctx.dev, "rev-parse", "--verify", "-q", f"{name}")',
    ),
    M(
        "since-fault-fatal: a git fault while timing the promote discards the verdict",
        '    except ErrorExit:\n        return "unknown"',
        '    except ZeroDivisionError:\n        return "unknown"',
    ),
    M(
        "note-unranged: a no-op promote still prints a NOTE row",
        '        if noted.kind == "STALE" and in_range(ctx, noted.anchors):',
        '        if noted.kind == "STALE":',
    ),
    M(
        "topic-floor-off: an index masks a topic parser that found nothing",
        "    if S.zones == 0 or (topics and S.topic_zones == 0):",
        "    if S.zones == 0:",
    ),
    M(
        "base-marker-any-word: a base marker swallows whatever commit follows it",
        '    r"(?:\\s|[:=@]|\\b(?:dev|main|on|of|at|is|was|commit)\\b){0,8}$",',
        '    r"[^.;]{0,30}$",',
    ),
    M(
        "explicit-dev-typo-is-a-skip: --dev with no repository is NOT-APPLICABLE",
        '        if arg:\n            raise ErrorExit("usage", f"--dev {arg} is not a git repository")',
        '        if False:\n            raise ErrorExit("usage", f"--dev {arg} is not a git repository")',
    ),
    M(
        "header-only-on-success: an error prints no parameter header",
        "        emit_header_once(known_header())\n        flush_rows()\n        emit(error_line(err))",
        "        flush_rows()\n        emit(error_line(err))",
    ),
    M(
        "not-applicable-is-a-pass: a skipped project reads as clean PASS",
        '        emit(f"NOT-APPLICABLE reason={na.reason}")\n        return finish("NOT-APPLICABLE", 0)',
        '        emit(f"NOT-APPLICABLE reason={na.reason}")\n        return finish("PASS", 0)',
    ),
    M(
        "result-before-rows: the verdict line is not last",
        "def finish(status: str, rc: int) -> int:\n    flush_rows()\n    emit(result_line(status, rc))",
        "def finish(status: str, rc: int) -> int:\n    emit(result_line(status, rc))\n    flush_rows()",
    ),
    M(
        "published-runs-the-production-check: --only published reads the production clone",
        '    if "promoted" in S.classes:\n        check_production(ctx, args)',
        "    if True:\n        check_production(ctx, args)",
    ),
    M(
        "watermark-absent-passes: a missing watermark is not named",
        "    if ctx.watermark is None:",
        "    if False:",
    ),
    M(
        "production-absent-passes: a missing production path is not named",
        "        if not path.is_dir():",
        "        if False:",
    ),
    M(
        "dev-ref-absent-passes: a missing dev ref is not named",
        "    if tip is None:",
        "    if False:",
    ),
    # ---- exit behaviour
    M(
        "crash-reads-as-fail: an uncaught exception exits 1",
        '        return finish("INCOMPLETE", 3)\n\n\ndef main',
        '        return finish("INCOMPLETE", 1)\n\n\ndef main',
    ),
    M(
        "stdout-failure-flush-unguarded: the interpreter's exit flush fails too (rc 120)",
        "            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())",
        "            pass",
    ),
    M(
        "writes-into-the-store: the tool leaves a file in the memory directory",
        "    files = topic_files(mem)\n    S.files = len(files)",
        '    (mem / ".touched").write_text("x", encoding="utf-8")\n    files = topic_files(mem)\n    S.files = len(files)',
    ),
    # ---- rows for review findings fixed after the plan
    M(
        "ancestry-failure-is-no: a failed merge-base reads as 'not an ancestor'",
        "    if rc not in (0, 1):",
        "    if False:",
    ),
    M(
        "status-mid-paragraph: a wrapped line beginning Status opens a zone",
        '        first_line = (\n            j == body\n            or not lines[j - 1].strip()\n            or lines[j - 1].lstrip().startswith("#")\n            or fenced[j - 1]\n        )',
        "        first_line = True",
    ),
    M(
        "status-window-one-too-wide: body line 41 opens a Status zone",
        "STATUS_SCAN_LINES = 40",
        "STATUS_SCAN_LINES = 41",
    ),
    M(
        "status-window-one-too-narrow: body line 40 no longer opens a Status zone",
        "STATUS_SCAN_LINES = 40",
        "STATUS_SCAN_LINES = 39",
    ),
    M(
        "count-token-not-whole: digits glued to / = - . : are a count",
        'COUNT_BEFORE = re.compile(r"(?<![\\w/=.:-])\\d+\\+?\\s+(?:[A-Za-z-]+\\s+)?$")',
        'COUNT_BEFORE = re.compile(r"\\b\\d+\\+?\\s+(?:[A-Za-z-]+\\s+)?$")',
    ),
    M(
        "count-window-edge-trusted: a token cut by the 41-character window is a count",
        "    if counted and (lo == 0 or counted.start() > 0):",
        "    if counted:",
    ),
    M(
        "stray-backtick-flips-parity: one unpaired backtick quotes the rest of the zone",
        "        if len(found) % 2:\n            pending = found.pop()",
        "        if False:\n            pending = found.pop()",
    ),
    M(
        'odd-double-quote-pairs: a 5" pipe pairs across the claims after it',
        "        if line.count('\"') % 2 == 0:",
        "        if True:",
    ),
    M(
        "block-quote-prefix-kept: a '> ' prefix breaks a wrapped claim",
        '    scan = QUOTE_PREFIX.sub(lambda m: " " * len(m.group(0)), text)',
        "    scan = text",
    ),
    M(
        "note-cites-first-claim: the NOTE row names a live claim, not the anchored one",
        '                c for c in unit.claims if c.klass == klass and c.excluded == "anchored"',
        "                c for c in unit.claims if c.klass == klass",
    ),
    M(
        "class-evaluated-twice: a class with live and anchored claims is judged twice",
        "        if klass not in cache:",
        "        if True:",
    ),
    M(
        "fix-names-the-lowest-stage: both classes stale, the fix says PROMOTED only",
        "        top = max(stale, key=CLASSES.index)",
        "        top = stale[0]",
    ),
    M(
        "dev-trees-floor-off: a failed commit listing reads as no trees",
        "    if not trees:",
        "    if False:",
    ),
    M(
        "index-link-basename-only: ../x/t.md and URLs bind to a top-level t.md",
        '        if not DIRECT_CHILD.fullmatch(target):\n            target = ""',
        "        if not DIRECT_CHILD.fullmatch(target):\n            target = Path(target).name",
    ),
    M(
        "index-link-fragment-unstripped: t.md#section names no topic file",
        'INDEX_LINK = re.compile(r"\\]\\(([^)\\s#]{1,200}\\.md)(?:#[^)\\s]{0,100})?\\)")',
        'INDEX_LINK = re.compile(r"\\]\\(([^)\\s]+\\.md)\\)")',
    ),
    M(
        "index-hint-half-the-markup: the hint quotes **LANDED without its dash",
        "        label = text[claim.start : tail.end() if tail else claim.end]",
        "        label = text[claim.start : claim.end]",
    ),
    M(
        "index-claims-unsorted: the first claim is not the first in the line",
        "        found = sorted(claims_in(entry.text) + label_claims(entry.text), key=position)",
        "        found = claims_in(entry.text) + label_claims(entry.text)",
    ),
    M(
        "link-regexes-unbounded: unclosed link openers cost quadratic time",
        'LINK_TARGET = re.compile(r"\\]\\([^)\\n]{0,300}\\)|\\[\\[[^\\]\\n]{0,300}\\]\\]")',
        'LINK_TARGET = re.compile(r"\\]\\([^)]*\\)|\\[\\[[^\\]]*\\]\\]")',
    ),
    M(
        "incomplete-without-header: a signal before the header leaves a bare verdict",
        '        head = "" if S.header_done else known_header() + "\\n"',
        '        head = ""',
    ),
    M(
        "promote-time-last-entry: every unit is dated by the newest reflog entry",
        "    return entries[lo][1]",
        "    return entries[-1][1]",
    ),
    M(
        "promote-time-bisect-swapped: the search moves away from the first holder",
        "        if holds(mid):\n            hi = mid\n        else:\n            lo = mid + 1",
        "        if holds(mid):\n            lo = mid + 1\n        else:\n            hi = mid",
    ),
    M(
        "stdout-oserror-only-epipe: a read-only stdout still ends in rc 120",
        "    except (OSError, ValueError) as err:",
        "    except BrokenPipeError as err:",
    ),
    M(
        "git-survives-the-signal: the stalled git call outlives an INCOMPLETE run",
        "        kill_child()\n        try:\n            os.write(1,",
        "        try:\n            os.write(1,",
    ),
    M(
        "watermark-control-ignores-classes: --only promoted fails on a bad watermark",
        '    if ctx.watermark and "published" in S.classes:',
        "    if ctx.watermark:",
    ),
    M(
        "memory-dir-absent-passes: a missing memory directory is not named",
        "    if not mem.is_dir():",
        "    if False:",
    ),
    M(
        "production-not-git-passes: a production path that is no repo is not named",
        '    if top is None:\n        raise ErrorExit("production-not-git"',
        '    if False:\n        raise ErrorExit("production-not-git"',
    ),
    M(
        "production-no-head-passes: a production repo with no commit is not named",
        "    if head is None:",
        "    if False:",
    ),
    M(
        "scripts-missing-passes: a defaulted production with no ~/.claude/scripts is judged",
        "        if not scripts.exists():",
        "        if False:",
    ),
    M(
        "production-unrelated-not-raised: an explicit unrelated production is accepted",
        '    raise ErrorExit(\n        "production-unrelated",\n        f"production HEAD {short(ctx.prod_head)} is not an ancestor of the dev tip",\n        classes,\n    )',
        "    return",
    ),
    M(
        "roots-disjoint-false: a defaulted unrelated production is an error",
        "    disjoint = not (dev_roots & prod_roots)",
        "    disjoint = False",
    ),
    # ---- rows for review findings fixed after the first campaign run
    M(
        "status-after-heading-missed: a Status line directly under a heading is not a zone",
        '            or lines[j - 1].lstrip().startswith("#")\n',
        "",
    ),
    M(
        "status-after-fence-missed: a Status line directly after a closing fence is not a zone",
        "            or fenced[j - 1]\n",
        "",
    ),
    M(
        "fence-ignored-by-zones: an example inside a code fence opens a zone",
        "    fenced = fence_flags(lines, body)",
        "    fenced = [False] * len(lines)",
    ),
    M(
        "wrapped-span-unpaired: a code span wrapped across two lines does not quote its claim",
        "        if pending is not None:\n            if found:",
        "        if False:\n            if found:",
    ),
    M(
        "stray-backtick-pairs-over-lines: a stray backtick pairs with a later line's first one",
        "                found = found[1:]\n            pending = None",
        "                found = found[1:]\n                pending = None",
    ),
    M(
        "rows-popped-after-emit: a failed write leaves its row queued to print twice",
        "        emit(S.rows.pop(0))",
        "        emit(S.rows[0])\n        S.rows.pop(0)",
    ),
    M(
        "stdout-valueerror-escapes: an unencodable report ends in a bare crash",
        "    except (OSError, ValueError) as err:",
        "    except OSError as err:",
    ),
    M(
        "stdout-not-lossless: an ASCII stdout dies half-way through the report",
        "    use_lossless_stdout()\n    install_signal_handlers()",
        "    install_signal_handlers()",
    ),
    M(
        'empty-dev-means-cwd: --dev "" falls back to the working directory',
        '    if arg == "":',
        "    if False:",
    ),
    M(
        "production-header-after-check: a missing explicit production loses its path in the header",
        '        S.hdr["production"] = f"{path}@unresolved"\n        if not path.is_dir():',
        "        if not path.is_dir():",
    ),
    M(
        "base-connectors-without-is-was: Base is X and Base was X are evidence",
        '    r"(?:\\s|[:=@]|\\b(?:dev|main|on|of|at|is|was|commit)\\b){0,8}$",',
        '    r"(?:\\s|[:=@]|\\b(?:dev|main|on|of|at|commit)\\b){0,8}$",',
    ),
]


def main() -> int:
    report = mutate.run(SUBJECT, SUITE, MUTATIONS, cwd=str(REPO))
    print(report.text)
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
