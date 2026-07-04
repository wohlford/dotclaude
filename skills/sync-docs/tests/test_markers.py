"""Tests for markers.py — state machine, directive parsing, render round-trip."""

from __future__ import annotations

import markers


def test_parse_no_markers():
    doc = markers.parse("# Title\n\nNothing to see here.\n")
    assert doc.blocks == []
    assert doc.errors == []


def test_parse_one_marker():
    text = "before\n<!-- sync:skills -->\nbody\n<!-- /sync:skills -->\nafter\n"
    doc = markers.parse(text)
    assert len(doc.blocks) == 1
    assert doc.blocks[0].handler == "skills"
    assert doc.blocks[0].body_lines == ["body"]
    assert doc.blocks[0].open_line == 2
    assert doc.blocks[0].close_line == 4
    assert doc.errors == []


def test_parse_directives():
    text = "<!-- sync:skills filter=category:foo limit=30 -->\n<!-- /sync:skills -->\n"
    doc = markers.parse(text)
    assert doc.blocks[0].directives == {"filter": "category:foo", "limit": "30"}


def test_parse_quoted_directive():
    text = (
        '<!-- sync:agents cols=Agent:key,"Used by":manual -->\n<!-- /sync:agents -->\n'
    )
    doc = markers.parse(text)
    assert doc.blocks[0].directives == {
        "cols": "Agent:key,Used by:manual"
    } or doc.blocks[0].directives == {"cols": 'Agent:key,"Used by":manual'}


def test_marker_in_code_block_is_inert():
    text = "before\n```\n<!-- sync:skills -->\n<!-- /sync:skills -->\n```\nafter\n"
    doc = markers.parse(text)
    assert doc.blocks == []
    assert doc.errors == []


def test_unclosed_marker_error():
    text = "<!-- sync:skills -->\nbody\n"
    doc = markers.parse(text)
    assert len(doc.errors) == 1
    assert "unclosed" in doc.errors[0].message.lower()


def test_close_without_open_error():
    text = "<!-- /sync:skills -->\n"
    doc = markers.parse(text)
    assert len(doc.errors) == 1
    assert "without" in doc.errors[0].message.lower()


def test_mismatched_close_error():
    text = "<!-- sync:skills -->\nbody\n<!-- /sync:agents -->\n"
    doc = markers.parse(text)
    assert any("mismatched" in e.message.lower() for e in doc.errors)


def test_render_unchanged_roundtrips():
    text = "before\n<!-- sync:skills -->\nbody\n<!-- /sync:skills -->\nafter\n"
    doc = markers.parse(text)
    assert markers.render(doc) == text


def test_render_replaces_body():
    text = "before\n<!-- sync:skills -->\nold\n<!-- /sync:skills -->\nafter\n"
    doc = markers.parse(text)
    out = markers.render(doc, {0: ["new1", "new2"]})
    assert (
        out
        == "before\n<!-- sync:skills -->\nnew1\nnew2\n<!-- /sync:skills -->\nafter\n"
    )


def test_directive_parser_bare():
    d = markers.parse_directives("a=1 b=foo")
    assert d == {"a": "1", "b": "foo"}


def test_directive_parser_quoted():
    d = markers.parse_directives('a="hello world" b=42')
    assert d == {"a": "hello world", "b": "42"}


def test_directive_parser_unbalanced_quote_raises():
    import pytest

    with pytest.raises(ValueError, match="unbalanced"):
        markers.parse_directives('a="oops')


def test_two_markers_one_file():
    text = (
        "<!-- sync:skills -->\nA\n<!-- /sync:skills -->\n"
        "middle\n"
        "<!-- sync:agents -->\nB\n<!-- /sync:agents -->\n"
    )
    doc = markers.parse(text)
    assert len(doc.blocks) == 2
    assert doc.blocks[0].handler == "skills"
    assert doc.blocks[1].handler == "agents"


def test_render_preserves_missing_final_newline():
    # A marker file with no trailing newline must round-trip byte-identically (no false drift).
    src = "# T\n\n<!-- sync:x -->\n| a |\n<!-- /sync:x -->"
    doc = markers.parse(src)
    assert markers.render(doc) == src


def test_render_preserves_crlf():
    src = "# T\r\n\r\n<!-- sync:x -->\r\n| a |\r\n<!-- /sync:x -->\r\n"
    doc = markers.parse(src)
    assert markers.render(doc) == src


def test_tilde_fence_does_not_close_backtick_fence():
    # A marker between a ``` opener and a ~~~ line must stay inert (fence not closed by wrong char).
    doc = markers.parse("```\n<!-- sync:x -->\n~~~\n| a |\n```\n")
    assert doc.blocks == []


def test_matching_fence_still_closes():
    doc = markers.parse("```\ncode\n```\n<!-- sync:y -->\n| b |\n<!-- /sync:y -->\n")
    assert len(doc.blocks) == 1
