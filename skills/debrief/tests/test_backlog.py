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

import pytest
from backlog import (
    AmbiguousEntry,
    Backlog,
    EntryNotFound,
    InvalidNote,
    ShapeViolation,
    entry_blocks,
)

# ---------- document builders ----------


def entry(token, date="2026-07-01", closed=False, body=()):
    box = "- [x]" if closed else "- [ ]"
    return [f"{box} {date} — **{token} — headline for {token}.**", *body]


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
    op = rng.choice(["append", "stamp", "close"])
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
    else:
        bl.close_entry(target, note)
        expect_lost = collections.Counter([head])
        expect_gained = collections.Counter([head.replace("- [ ]", "- [x]", 1), note])
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
