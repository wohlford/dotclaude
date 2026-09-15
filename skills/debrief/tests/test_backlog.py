"""Properties of the BACKLOG.md mutation helper.

Five ad-hoc scripts were written against BACKLOG.md before this module existed. One of them
corrupted the file — it located an entry by its first line, then scanned forward for a
`→ [[link]]` sentinel to find its last, and the sentinel sits inline at the end of a prose line,
so the scan ran past its target and moved three entries. Every assertion in that script passed,
because all of them constrained where the edit STARTED and none constrained how far it REACHED.

So the assertions here are mostly about SHAPE — quantified over generated documents rather than
over a handful of hand-written witnesses — plus one negative-firing test that proves the shape
check actually blocks a write instead of merely being present.
"""

from __future__ import annotations

import collections
import random

import backlog
import pytest
from backlog import (
    AmbiguousEntry,
    Backlog,
    BacklogError,
    EntryNotFound,
    InvalidNote,
    ShapeViolation,
    entry_blocks,
)

# ---------- document builders ----------


def entry(token, date="2026-07-01", closed=False, body=()):
    box = "- [x]" if closed else "- [ ]"
    return [f"{box} {date} — **{token} — headline for {token}.**", *body]


def undated_entry(token, body=()):
    # Shaped like the real defect `amend_head --date` exists to repair: a head with no date.
    return [f"- [ ] **{token} — headline for {token}.**", *body]


def tiered_entry(token, tier, date="2026-07-01", body=()):
    # A head that already carries a tier, for the "refuse rather than overwrite" tests.
    return [f"- [ ] {date} — **{tier} — {token} — headline for {token}.**", *body]


def make_doc(open_entries, closed_entries=()):
    parts = [
        "---",
        "name: BACKLOG",
        "---",
        "",
        "Prose about the backlog.",
        "",
        "## Open",
        "",
    ]
    for e in open_entries:
        parts.extend(e)
        parts.append("")
    parts += ["## Closed", ""]
    for e in closed_entries:
        parts.extend(e)
        parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"


def write_doc(tmp_path, text, name="BACKLOG.md"):
    path = tmp_path / name
    path.write_text(text)
    return path


def nonblank(text):
    return collections.Counter(ln for ln in text.split("\n") if ln.strip())


def blocks_of(text):
    """Map each entry head to its exact block of lines, keyed by the head line."""
    lines = text.split("\n")
    return {lines[s]: lines[s:e] for s, e in entry_blocks(lines)}


# ---------- span derivation ----------


def test_indented_checkbox_is_prose_not_a_head():
    # The real file has entries that QUOTE other entries, indented two spaces. Treating one as a
    # head would split an entry in half and let a later edit reattach prose to the wrong item.
    body = [
        "  It quotes another item:",
        "  - [ ] 2026-07-02 — **quoted thing**",
        "  ...end.",
    ]
    lines = make_doc([entry("A", body=body), entry("B")]).split("\n")
    heads = [lines[s] for s, _ in entry_blocks(lines)]
    assert len(heads) == 2
    assert all("quoted thing" not in h for h in heads)


def test_block_ends_at_the_next_heading_not_at_end_of_file():
    lines = make_doc([entry("A", body=["  tail"])], [entry("Z", closed=True)]).split(
        "\n"
    )
    first_start, first_end = entry_blocks(lines)[0]
    assert "## Closed" not in lines[first_start:first_end]


def test_sentinel_at_end_of_a_prose_line_does_not_end_the_block():
    # The exact shape that broke the corrupting script.
    body = [
        "  Some reasoning here. → [[some-slug]]",
        "  A further line after the sentinel.",
    ]
    lines = make_doc([entry("A", body=body), entry("B")]).split("\n")
    start, end = entry_blocks(lines)[0]
    assert "  A further line after the sentinel." in lines[start:end]


# ---------- add_entry ----------


def test_add_entry_is_insert_only_and_lands_in_the_open_half(tmp_path):
    doc = make_doc([entry("A")], [entry("Z", closed=True)])
    path = write_doc(tmp_path, doc)
    before = nonblank(doc)
    new = ["- [ ] 2026-07-29 — **NEW — a deferral.** → [[slug]]", "  detail line"]

    bl = Backlog(path)
    bl.add_entry("\n".join(new))
    report = bl.save()

    after = nonblank(path.read_text())
    assert before - after == collections.Counter()
    assert after - before == collections.Counter(new)
    lines = path.read_text().split("\n")
    boundary = lines.index("## Closed")
    assert new[0] in lines[:boundary]
    assert report.open_count == 2


def test_add_entry_body_stays_inside_the_new_entry(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("A", body=["  a1 → [[s]]"])]))
    bl = Backlog(path)
    head = "- [ ] 2026-07-29 — **NEW — a deferral.**"
    bl.add_entry(f"{head}\n  first detail\n  second detail")
    bl.save()
    block = next(b for h, b in blocks_of(path.read_text()).items() if h == head)
    assert "  first detail" in block
    assert "  second detail" in block


def test_add_entry_is_addressable_afterwards(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("A")]))
    bl = Backlog(path)
    bl.add_entry("- [ ] 2026-07-29 — **NEW — a deferral.**")
    bl.save()
    bl = Backlog(path)
    bl.stamp_promoted("NEW —", "2026-07-30")
    bl.save()
    assert "*promoted 2026-07-30* — **NEW" in path.read_text()


def test_add_entry_refuses_a_head_that_would_not_be_uniquely_addressable(tmp_path):
    # Two entries whose heads contain one another leave a pair no later needle can separate,
    # which is how a batch quietly starts editing the wrong item.
    head = "- [ ] 2026-07-29 — **NEW — a deferral.**"
    path = write_doc(tmp_path, make_doc([[head], entry("A")]))
    doc = path.read_text()
    bl = Backlog(path)
    bl.add_entry(head)
    with pytest.raises(ShapeViolation):
        bl.save()
    assert path.read_text() == doc


def test_add_entry_rejects_text_that_is_not_an_open_entry(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("A")]))
    for bad in [
        "  no head at all",
        "- [x] 2026-07-29 — **already closed**",
        "- [ ] 2026-07-29 — **NEW**\n- [ ] 2026-07-29 — **a smuggled second entry**",
        "- [ ] 2026-07-29 — **NEW**\n## Closed",
    ]:
        with pytest.raises(InvalidNote):
            Backlog(path).add_entry(bad)


def test_add_entry_refuses_a_head_with_no_date(tmp_path):
    # Shaped like the real defect: entry 23's head carries no date, which makes it permanently
    # unpromotable (`promote` requires `- [ ] YYYY-MM-DD — `). `add` must refuse it up front
    # rather than accept input it can never act on.
    path = write_doc(tmp_path, make_doc([entry("A")]))
    bad = "- [ ] **`install.sh --check` dies before it can report** — the rest"
    with pytest.raises(InvalidNote):
        Backlog(path).add_entry(bad)


# ---------- append_note ----------


def test_append_note_is_insert_only(tmp_path):
    note = ["  **NOTE** — something measured.", "  A second line."]
    doc = make_doc([entry("A", body=["  a tail"]), entry("B"), entry("C")])
    path = write_doc(tmp_path, doc)
    before = nonblank(doc)

    bl = Backlog(path)
    bl.append_note("B —", "\n".join(note))
    bl.save()

    after = nonblank(path.read_text())
    assert before - after == collections.Counter()  # nothing lost
    assert after - before == collections.Counter(note)  # exactly the note gained


def test_append_note_lands_inside_its_own_entry_and_moves_no_other(tmp_path):
    # Regression for the corrupting script: sentinels inline at the end of prose lines.
    entries = [
        entry("A", body=["  reasoning → [[slug-a]]", "  more reasoning"]),
        entry("B", body=["  reasoning → [[slug-b]]"]),
        entry("C", body=["  reasoning → [[slug-c]]", "  trailing"]),
    ]
    doc = make_doc(entries)
    path = write_doc(tmp_path, doc)
    untouched = {h: b for h, b in blocks_of(doc).items() if "**B —" not in h}

    bl = Backlog(path)
    bl.append_note("B —", "  **NOTE** — landed here.")
    bl.save()

    after_blocks = blocks_of(path.read_text())
    target = next(b for h, b in after_blocks.items() if "**B —" in h)
    assert "  **NOTE** — landed here." in target
    for head, block in untouched.items():
        assert after_blocks[head] == block, f"unrelated entry moved: {head}"


def test_append_note_rejects_a_note_line_that_would_parse_as_a_head(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("A")]))
    bl = Backlog(path)
    for bad in [
        "- [ ] 2026-07-02 — **new**",
        "- [x] 2026-07-02 — **new**",
        "## Closed",
    ]:
        with pytest.raises(InvalidNote):
            bl.append_note("A —", bad)


# ---------- stamp_promoted ----------


def test_stamp_rewrites_exactly_one_line(tmp_path):
    doc = make_doc([entry("A"), entry("B", body=["  tail"]), entry("C")])
    path = write_doc(tmp_path, doc)
    before = nonblank(doc)

    bl = Backlog(path)
    bl.stamp_promoted("B —", "2026-07-28")
    bl.save()

    after = nonblank(path.read_text())
    assert sum((before - after).values()) == 1
    assert sum((after - before).values()) == 1
    assert "*promoted 2026-07-28*" in "\n".join((after - before).elements())


def test_stamp_with_a_reason_keeps_the_rest_of_the_head(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("A")]))
    bl = Backlog(path)
    bl.stamp_promoted("A —", "2026-07-28", "IT FIRED AGAIN")
    bl.save()
    head = next(ln for ln in path.read_text().split("\n") if ln.startswith("- [ ]"))
    assert head.startswith(
        "- [ ] 2026-07-01 — *promoted 2026-07-28 — IT FIRED AGAIN* — "
    )
    assert head.endswith("**A — headline for A.**")


def test_stamp_is_idempotent(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("A"), entry("B")]))
    bl = Backlog(path)
    bl.stamp_promoted("A —", "2026-07-28", "R")
    bl.save()
    once = path.read_text()

    bl = Backlog(path)
    bl.stamp_promoted("A —", "2026-07-28", "R")
    bl.save()
    assert path.read_text() == once


def test_restamping_replaces_rather_than_accumulates(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("A")]))
    bl = Backlog(path)
    bl.stamp_promoted("A —", "2026-07-27")
    bl.save()
    bl = Backlog(path)
    bl.stamp_promoted("A —", "2026-07-28", "SECOND STALL")
    bl.save()
    head = next(ln for ln in path.read_text().split("\n") if ln.startswith("- [ ]"))
    assert head.count("*promoted") == 1
    assert "2026-07-28 — SECOND STALL" in head


def test_prose_mentioning_promoted_is_not_read_as_a_stamp(tmp_path):
    # A real entry reads "(was LOW; promoted by its own two-strikes rule 2026-07-28)" in its
    # headline. A substring test for "promoted" would treat that as an existing stamp.
    head = "- [ ] 2026-07-01 — **X — was LOW; promoted by its own rule 2026-07-28.**"
    path = write_doc(tmp_path, make_doc([[head]]))
    bl = Backlog(path)
    bl.stamp_promoted("X —", "2026-07-29")
    bl.save()
    out = next(ln for ln in path.read_text().split("\n") if ln.startswith("- [ ]"))
    assert out == (
        "- [ ] 2026-07-01 — *promoted 2026-07-29* — "
        "**X — was LOW; promoted by its own rule 2026-07-28.**"
    )


def test_stamping_preserves_a_headline_carrying_its_own_italics(tmp_path):
    # The stamp must be recognised only in its ANCHORED position. A looser match would find the
    # `*…* — ` further along this headline and swallow everything before it — and the multiset
    # check cannot see that, since one line still goes out and one comes back.
    head = "- [ ] 2026-07-01 — **X** — see *promoted work* — details"
    path = write_doc(tmp_path, make_doc([[head]]))
    bl = Backlog(path)
    bl.stamp_promoted("**X**", "2026-07-29")
    bl.save()
    out = next(ln for ln in path.read_text().split("\n") if ln.startswith("- [ ]"))
    assert out == (
        "- [ ] 2026-07-01 — *promoted 2026-07-29* — **X** — see *promoted work* — details"
    )


# ---------- amend_head ----------


def test_amend_head_inserts_a_date_only(tmp_path):
    doc = make_doc([undated_entry("A"), entry("B")])
    path = write_doc(tmp_path, doc)
    before = nonblank(doc)
    old_head = next(ln for ln in doc.split("\n") if "**A —" in ln)

    bl = Backlog(path)
    bl.amend_head("A —", date="2026-08-24")
    bl.save()

    after = nonblank(path.read_text())
    assert before - after == collections.Counter({old_head: 1})
    new_head = next(ln for ln in path.read_text().split("\n") if "**A —" in ln)
    assert after - before == collections.Counter({new_head: 1})
    assert new_head == "- [ ] 2026-08-24 — **A — headline for A.**"


def test_amend_head_inserts_a_tier_only(tmp_path):
    doc = make_doc([entry("A"), entry("B")])
    path = write_doc(tmp_path, doc)
    before = nonblank(doc)
    old_head = next(ln for ln in doc.split("\n") if "**A —" in ln)

    bl = Backlog(path)
    bl.amend_head("A —", tier="HIGH")
    bl.save()

    after = nonblank(path.read_text())
    assert before - after == collections.Counter({old_head: 1})
    new_head = next(ln for ln in path.read_text().split("\n") if "HIGH — A —" in ln)
    assert after - before == collections.Counter({new_head: 1})
    assert new_head == "- [ ] 2026-07-01 — **HIGH — A — headline for A.**"


def test_amend_head_inserts_date_and_tier_together_with_one_save(tmp_path):
    # The whole point of "both insertions compute in memory, then stage ONCE": staging twice
    # would declare an intermediate line that never exists on disk.
    doc = make_doc([undated_entry("A"), entry("B")])
    path = write_doc(tmp_path, doc)

    bl = Backlog(path)
    bl.amend_head("A —", date="2026-08-24", tier="MEDIUM")
    bl.save()

    new_head = next(ln for ln in path.read_text().split("\n") if "MEDIUM — A —" in ln)
    assert new_head == "- [ ] 2026-08-24 — **MEDIUM — A — headline for A.**"


def test_amend_head_requires_at_least_one_of_date_or_tier(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("A")]))
    doc = path.read_text()
    with pytest.raises(BacklogError):
        Backlog(path).amend_head("A —")
    assert path.read_text() == doc


def test_amend_head_refuses_a_date_when_one_already_exists(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("A")]))
    doc = path.read_text()
    with pytest.raises(BacklogError):
        Backlog(path).amend_head("A —", date="2026-08-24")
    assert path.read_text() == doc


def test_amend_head_refuses_a_tier_when_one_already_exists(tmp_path):
    path = write_doc(tmp_path, make_doc([tiered_entry("A", "HIGH")]))
    doc = path.read_text()
    with pytest.raises(BacklogError):
        Backlog(path).amend_head("A —", tier="MEDIUM")
    assert path.read_text() == doc


def test_amend_head_refuses_a_tier_when_rest_has_no_bold_delimiter(tmp_path):
    # "A `rest` that does not start with `**` has no canonical insertion point: refuse, do not
    # guess" — never fall back to scanning for the first `**` anywhere in the headline.
    #
    # `match=` is load-bearing, not decoration. This fixture is ALSO refused by the subsequence
    # postcondition, so a bare `pytest.raises(BacklogError)` is satisfied by whichever check
    # happens to raise first and stays green when the guard this row is named for is deleted —
    # measured by mutation, this row's earlier form survived `no-bold-guard-off`. Assert the
    # guard's own message, and see the row below for the case where it is the ONLY refusal.
    head = "- [ ] 2026-07-01 — plain text with no bold delimiter"
    path = write_doc(tmp_path, make_doc([[head]]))
    doc = path.read_text()
    with pytest.raises(
        BacklogError, match=r"rest has no '\*\*' to insert a tier inside"
    ):
        Backlog(path).amend_head("plain text", tier="HIGH")
    assert path.read_text() == doc


@pytest.mark.parametrize("tier", ["HIGH", "MEDIUM", "LOW"])
def test_amend_head_bold_guard_is_the_only_refusal_for_a_plain_tier_head(
    tmp_path, tier
):
    """The `**` guard is decisive here, and its absence CORRUPTS rather than refuses.

    A head carrying a tier in the plain `TIER — ` form (no `**`) is not hypothetical: 23 of the 91
    open entries in the live backlog are written that way, 2 of them HIGH and 1 LOW. Inserting a
    tier into one does `rest[2:]`, which eats the first two characters of real text.

    When the inserted tier EQUALS the one already spelled out, the subsequence postcondition no
    longer catches it — the inserted token supplies exactly the characters that were dropped — and
    the length delta is unchanged, so every other postcondition passes too. Measured with the guard
    removed from the real module: `- [ ] … — HIGH — text` + `--tier HIGH` writes
    `- [ ] … — **HIGH — GH — text`, silently.

    So this fixture is built at the smallest case where the two readings DIVERGE, not at the
    smallest one that runs the code. The row above uses a head with no tier at all and is caught by
    a different check; only this one can go red when the guard is deleted.
    """
    head = f"- [ ] 2026-08-31 — {tier} — a plain-form head carrying its tier unbolded"
    path = write_doc(tmp_path, make_doc([[head]]))
    doc = path.read_text()
    with pytest.raises(
        BacklogError, match=r"rest has no '\*\*' to insert a tier inside"
    ):
        Backlog(path).amend_head("plain-form head", tier=tier)
    assert path.read_text() == doc


def test_amend_head_ambiguous_needle_raises(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("dup1"), entry("dup2")]))
    with pytest.raises(AmbiguousEntry):
        Backlog(path).amend_head("headline for dup", tier="HIGH")


def test_amend_head_shape_violation_leaves_file_byte_identical(tmp_path):
    # A silent pass and a check that never ran are indistinguishable, so force a NEGATIVE
    # firing on the SAME staging path amend_head uses, proving it is not exempt from the
    # shared postcondition every other operation is held to.
    doc = make_doc([undated_entry("A"), entry("B")])
    path = write_doc(tmp_path, doc)
    bl = Backlog(path)
    bl.amend_head("A —", date="2026-08-24")
    bl._expected_gained["bogus injected line"] += 1

    with pytest.raises(ShapeViolation):
        bl.save()
    assert path.read_text() == doc


# ---------- amend_head: the four postconditions are actually consulted ----------
#
# All four run only AFTER a correct derivation, so no realistic input reaches them — the
# refusal tests above exercise the GUARD CLAUSES (already-dated, already-tiered, no `**`),
# which are a different thing. To prove each postcondition is not dead code, lie to
# `amend_head` about the exact fact it examines — via monkeypatch — while leaving the real
# derivation untouched, so only the one targeted check can possibly fire.


def test_amend_head_subsequence_postcondition_actually_fires(tmp_path, monkeypatch):
    path = write_doc(tmp_path, make_doc([entry("A"), entry("B")]))
    doc = path.read_text()
    monkeypatch.setattr(backlog, "_is_subsequence", lambda old, new: False)

    with pytest.raises(BacklogError, match="must only insert characters"):
        Backlog(path).amend_head("A —", tier="HIGH")
    assert path.read_text() == doc


def test_amend_head_length_delta_postcondition_actually_fires(tmp_path, monkeypatch):
    path = write_doc(tmp_path, make_doc([undated_entry("A"), entry("B")]))
    doc = path.read_text()
    real_len = len
    stamp = "2026-08-24 — "

    def lying_len(x):
        # Lie only about the exact string `amend_head` accumulates into `inserted_len` for
        # the date insertion. Every other len() call in the method — slicing the head apart,
        # then computing len(new)/len(old) — sees the truth, so only the DECLARED insertion
        # length goes wrong; the ACTUAL characters written are unaffected.
        if x == stamp:
            return real_len(x) + 1
        return real_len(x)

    monkeypatch.setattr(backlog, "len", lying_len, raising=False)
    with pytest.raises(
        BacklogError, match="changed more or less than what it declared"
    ):
        Backlog(path).amend_head("A —", date="2026-08-24")
    assert path.read_text() == doc


def test_amend_head_wellformed_postcondition_actually_fires(tmp_path, monkeypatch):
    path = write_doc(tmp_path, make_doc([entry("A"), entry("B")]))
    doc = path.read_text()

    class _NeverMatches:
        def match(self, s):
            return None

    # date=None here, so the OTHER use of DATED_HEAD_RE (the "already carries a date"
    # guard, only reached when a date is being inserted) never runs — isolating the lie to
    # the postcondition alone.
    monkeypatch.setattr(backlog, "DATED_HEAD_RE", _NeverMatches())
    with pytest.raises(BacklogError, match="well-formed date prefix"):
        Backlog(path).amend_head("A —", tier="HIGH")
    assert path.read_text() == doc


def test_amend_head_tier_position_postcondition_actually_fires(tmp_path, monkeypatch):
    path = write_doc(tmp_path, make_doc([entry("A"), entry("B")]))
    doc = path.read_text()
    real_re = backlog.STAMPED_HEAD_RE

    class _LiesOnlyAfterInsertion:
        # `STAMPED_HEAD_RE` is called twice in the tier path: once to extract `rest` BEFORE
        # the tier lands (delegate faithfully, so the real edit is untouched), and once more
        # by the postcondition to re-check `new` AFTER the tier landed (lie only there, by
        # keying on the tier text the real edit would have just inserted).
        def match(self, s):
            m = real_re.match(s)
            if m is not None and "**HIGH — " in s:
                return {"rest": "**NOT-THE-TIER — lied about it"}
            return m

    monkeypatch.setattr(backlog, "STAMPED_HEAD_RE", _LiesOnlyAfterInsertion())
    with pytest.raises(
        BacklogError, match="did not land the tier at the front of rest"
    ):
        Backlog(path).amend_head("A —", tier="HIGH")
    assert path.read_text() == doc


# ---------- close_entry ----------


def test_close_entry_is_a_reordering_plus_one_head_plus_the_note(tmp_path):
    doc = make_doc(
        [entry("A", body=["  a1"]), entry("B", body=["  b1", "  b2"]), entry("C")],
        [entry("Z", closed=True)],
    )
    path = write_doc(tmp_path, doc)
    before = nonblank(doc)
    old_head = next(ln for ln in doc.split("\n") if "**B —" in ln)

    bl = Backlog(path)
    bl.close_entry("B —", "  **DONE** — shipped.")
    bl.save()

    after = nonblank(path.read_text())
    assert before - after == collections.Counter({old_head: 1})
    assert after - before == collections.Counter(
        {old_head.replace("- [ ]", "- [x]", 1): 1, "  **DONE** — shipped.": 1}
    )


def test_close_entry_moves_it_across_the_boundary_with_its_body(tmp_path):
    doc = make_doc(
        [entry("A"), entry("B", body=["  b1", "  b2 → [[s]]"])],
        [entry("Z", closed=True)],
    )
    path = write_doc(tmp_path, doc)

    bl = Backlog(path)
    bl.close_entry("B —", "  **DONE**")
    report = bl.save()

    lines = path.read_text().split("\n")
    boundary = lines.index("## Closed")
    open_half, closed_half = lines[:boundary], lines[boundary:]
    assert not any("**B —" in ln for ln in open_half)
    assert any("**B —" in ln for ln in closed_half)
    for body_line in ("  b1", "  b2 → [[s]]", "  **DONE**"):
        assert body_line in closed_half
    assert report.open_count == 1
    assert report.closed_count == 2


def test_close_entry_keeps_the_section_invariant(tmp_path):
    path = write_doc(
        tmp_path, make_doc([entry("A"), entry("B")], [entry("Z", closed=True)])
    )
    bl = Backlog(path)
    bl.close_entry("A —", "  **DONE**")
    bl.save()
    lines = path.read_text().split("\n")
    boundary = lines.index("## Closed")
    assert not [ln for ln in lines[:boundary] if ln.startswith("- [x]")]
    assert not [ln for ln in lines[boundary:] if ln.startswith("- [ ]")]


# ---------- locator contract ----------


def test_a_needle_matching_nothing_raises(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("A")]))
    with pytest.raises(EntryNotFound):
        Backlog(path).append_note("nope", "  x")


def test_a_needle_matching_two_entries_raises(tmp_path):
    path = write_doc(tmp_path, make_doc([entry("dup1"), entry("dup2")]))
    with pytest.raises(AmbiguousEntry):
        Backlog(path).append_note("headline for dup", "  x")


def test_a_needle_matching_only_a_closed_entry_raises(tmp_path):
    # Every operation targets OPEN entries; silently editing a closed one would be a surprise.
    path = write_doc(tmp_path, make_doc([entry("A")], [entry("Z", closed=True)]))
    with pytest.raises(EntryNotFound):
        Backlog(path).append_note("Z —", "  x")


# ---------- the check must actually fire ----------


def test_a_shape_violation_raises_and_leaves_the_file_byte_identical(tmp_path):
    # A silent pass and a check that never ran are indistinguishable, so force a NEGATIVE firing:
    # stage a mutation that deletes an unrelated line while declaring no loss.
    doc = make_doc([entry("A", body=["  keep me"]), entry("B")])
    path = write_doc(tmp_path, doc)
    bl = Backlog(path)
    corrupted = [ln for ln in bl.lines if ln != "  keep me"]
    bl._stage(corrupted, collections.Counter(), collections.Counter())

    with pytest.raises(ShapeViolation):
        bl.save()
    assert path.read_text() == doc


def test_an_undeclared_insertion_raises_and_leaves_the_file_byte_identical(tmp_path):
    # The mirror of the test above: a deletion firing the lost-side check proves nothing about
    # the gained side, and an edit that smuggles a line IN is just as much a corruption.
    doc = make_doc([entry("A"), entry("B")])
    path = write_doc(tmp_path, doc)
    bl = Backlog(path)
    staged = list(bl.lines)
    staged.insert(len(staged) - 1, "  smuggled line")
    bl._stage(staged, collections.Counter(), collections.Counter())

    with pytest.raises(ShapeViolation):
        bl.save()
    assert path.read_text() == doc


def test_stranding_an_open_entry_below_closed_raises(tmp_path):
    # A pure REORDERING leaves both multisets untouched and involves no note, so the section
    # invariant is the only check standing between this and a silently corrupted document.
    doc = make_doc([entry("A"), entry("B")], [entry("Z", closed=True)])
    path = write_doc(tmp_path, doc)
    bl = Backlog(path)
    start, end = entry_blocks(bl.lines)[0]
    block = [ln for ln in bl.lines[start:end] if ln.strip()]
    rest = bl.lines[:start] + bl.lines[end:]
    boundary = rest.index("## Closed")
    staged = rest[: boundary + 1] + ["", *block, ""] + rest[boundary + 1 :]
    bl._stage(staged, collections.Counter(), collections.Counter())

    with pytest.raises(ShapeViolation):
        bl.save()
    assert path.read_text() == doc


def test_a_misplaced_note_raises_even_when_the_multiset_is_right(tmp_path):
    # The multiset check alone cannot see WHERE a line landed — that is the corrupting script's
    # bug exactly. Stage the note into the wrong entry with a correct-looking delta.
    doc = make_doc([entry("A"), entry("B")])
    path = write_doc(tmp_path, doc)
    bl = Backlog(path)
    note = "  **NOTE** — belongs to B."
    wrong = list(bl.lines)
    wrong.insert(wrong.index(next(ln for ln in wrong if "**A —" in ln)) + 1, note)
    bl._stage(wrong, collections.Counter(), collections.Counter([note]))
    bl._expect_note_inside("B —", note)

    with pytest.raises(ShapeViolation):
        bl.save()
    assert path.read_text() == doc


def test_save_is_required_for_anything_to_reach_disk(tmp_path):
    doc = make_doc([entry("A")])
    path = write_doc(tmp_path, doc)
    bl = Backlog(path)
    bl.append_note("A —", "  **NOTE**")
    assert path.read_text() == doc  # staged, not written


# ---------- batches ----------


def test_a_batch_composes_into_one_write(tmp_path):
    doc = make_doc([entry("A"), entry("B"), entry("C", body=["  c1 → [[s]]"])])
    path = write_doc(tmp_path, doc)
    before = nonblank(doc)
    old_c = next(ln for ln in doc.split("\n") if "**C —" in ln)

    bl = Backlog(path)
    bl.append_note("A —", "  **NOTE-A**")
    bl.stamp_promoted("B —", "2026-07-28")
    bl.close_entry("C —", "  **DONE-C**")
    bl.save()

    after = nonblank(path.read_text())
    old_b = next(ln for ln in doc.split("\n") if "**B —" in ln)
    assert before - after == collections.Counter({old_b: 1, old_c: 1})
    gained = after - before
    assert gained["  **NOTE-A**"] == 1
    assert gained["  **DONE-C**"] == 1
    assert gained[old_c.replace("- [ ]", "- [x]", 1)] == 1


# ---------- properties over generated documents ----------


def random_doc(rng):
    tokens = [f"T{i}" for i in range(rng.randint(2, 6))]
    opens = []
    for tok in tokens:
        body = []
        for _ in range(rng.randint(0, 4)):
            body.append(
                rng.choice(
                    [
                        f"  prose for {tok}",
                        f"  reasoning → [[slug-{tok.lower()}]]",
                        "  - [ ] 2026-07-02 — **a quoted entry**",
                        "  shared line",  # duplicated across entries on purpose
                        "",
                    ]
                )
            )
        opens.append(entry(tok, body=body))
    closed = [entry(f"C{i}", closed=True) for i in range(rng.randint(0, 3))]
    return make_doc(opens, closed), tokens


@pytest.mark.parametrize("seed", range(200))
def test_every_operation_holds_its_shape_on_generated_documents(seed, tmp_path):
    rng = random.Random(seed)
    doc, tokens = random_doc(rng)
    path = write_doc(tmp_path, doc, name=f"B{seed}.md")
    target = f"{rng.choice(tokens)} —"
    op = rng.choice(["append", "stamp", "close", "amend"])
    note = f"  **NOTE {seed}** — generated."
    before = nonblank(doc)
    head = next(ln for ln in doc.split("\n") if target in ln and ln.startswith("- [ ]"))
    others = {h: b for h, b in blocks_of(doc).items() if target not in h}

    bl = Backlog(path)
    if op == "append":
        bl.append_note(target, note)
        expect_lost, expect_gained = collections.Counter(), collections.Counter([note])
    elif op == "stamp":
        bl.stamp_promoted(target, "2026-07-28")
        expect_lost = collections.Counter([head])
        expect_gained = collections.Counter(
            [head.replace(" — ", " — *promoted 2026-07-28* — ", 1)]
        )
    elif op == "close":
        bl.close_entry(target, note)
        expect_lost = collections.Counter([head])
        expect_gained = collections.Counter([head.replace("- [ ]", "- [x]", 1), note])
    else:
        # Every generated head is already dated (see `entry()`), so only a tier insertion can
        # land here without a guaranteed refusal — `--date` is exercised by its own unit tests.
        bl.amend_head(target, tier="HIGH")
        expect_lost = collections.Counter([head])
        expect_gained = collections.Counter([head.replace("**", "**HIGH — ", 1)])
    bl.save()

    after = nonblank(path.read_text())
    assert before - after == expect_lost
    assert after - before == expect_gained

    # No unrelated entry may be rewritten, whatever the operation was.
    after_blocks = blocks_of(path.read_text())
    for other_head, block in others.items():
        assert after_blocks.get(other_head) == block, (
            f"seed {seed}: {other_head} changed"
        )

    # The section invariant survives every operation.
    lines = path.read_text().split("\n")
    boundary = lines.index("## Closed")
    assert not [ln for ln in lines[:boundary] if ln.startswith("- [x]")]
    assert not [ln for ln in lines[boundary:] if ln.startswith("- [ ]")]


def test_add_entry_refuses_a_head_that_already_claims_promotion(tmp_path):
    # A brand-new entry can never legitimately arrive already stamped: `promote` applies that
    # stamp later, as a disposition. The date-prefix check alone does NOT catch this — it is
    # unanchored, so it matches any head that merely STARTS with a date.
    path = write_doc(tmp_path, make_doc([entry("A")]))
    stamped = "- [ ] 2026-09-04 — *promoted 2026-08-01* — **HIGH — pre-stamped**"
    with pytest.raises(InvalidNote):
        Backlog(path).add_entry(stamped)


def test_amend_head_can_tier_an_already_promoted_entry(tmp_path):
    # `add` must REFUSE a stamped head; `amend_head` must ACCEPT one. Two different questions,
    # and collapsing them into a single regex breaks amending every promoted entry — caught
    # only by running against real data, where exactly one such entry exists.
    promoted = [
        "- [ ] 2026-07-25 — *promoted 2026-07-25* — **PROTOTYPE EXISTS.** body",
        "  a continuation line",
    ]
    path = write_doc(tmp_path, make_doc([promoted]))
    b = Backlog(path)
    b.amend_head("PROTOTYPE EXISTS", tier="MEDIUM")
    b.save()
    text = path.read_text()
    assert "*promoted 2026-07-25* — **MEDIUM — PROTOTYPE EXISTS.**" in text
    assert text.count("MEDIUM") == 1


def test_amend_head_sees_the_date_on_an_already_promoted_head(tmp_path):
    # The mirror of the test above, and it pins the OTHER half of the same split. If the
    # "already carries a date" guard used the strict new-entry regex, a promoted head would read
    # as UNDATED — the lookahead rejects it — and `--date` would insert a SECOND date ahead of
    # the first. Every postcondition would still pass, because the result does begin with a
    # valid date prefix, so this refusal is the only thing standing between that input and a
    # corrupted head.
    promoted = [
        "- [ ] 2026-07-25 — *promoted 2026-07-25* — **PROTOTYPE EXISTS.** body",
        "  a continuation line",
    ]
    path = write_doc(tmp_path, make_doc([promoted]))
    doc = path.read_text()
    with pytest.raises(BacklogError, match="already carries a date"):
        Backlog(path).amend_head("PROTOTYPE EXISTS", date="2026-09-04")
    assert path.read_text() == doc
