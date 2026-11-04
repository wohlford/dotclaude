"""Tests for the cycle-profile model, synthetic generator and transcript reader.

Every transcript is synthetic (fictional base date 2030-01-01, invented ids, lorem text).
The reader rows drive `cycle_profile_records` through `parse_bytes` and `read_transcript`;
the contract they pin is the `Tok` kinds table: the reader emits every kind and no other,
stores no text on an `Event`, reads exactly the pinned bytes and opens the file once.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import cycle_profile_helpers as h  # noqa: E402, I001
import cycle_profile_model as m  # noqa: E402
import cycle_profile_records as r  # noqa: E402


def _sig(transcript):
    """Order-sensitive signature of the time-sorted events, ignoring file position."""
    return [(e.t_ms, e.rtype, e.toks) for e in r.sorted_events(transcript)]


def _toks(transcript, kind):
    return [t for e in transcript.events for t in e.toks if t.kind == kind]


def _tool_ids(transcript):
    return [
        t.b for e in r.sorted_events(transcript) for t in e.toks if t.kind == "tool_use"
    ]


# ---------------------------------------------------------------------------------------------
# t00: the generator and the token contract
# ---------------------------------------------------------------------------------------------


def test_t00_generator_shape(tmp_path):
    data = h.k1_transcript("skill")
    lines = data.decode().splitlines()
    recs = [json.loads(line) for line in lines]
    assert len(recs) > 20
    assert all(rec["timestamp"].startswith("2030-01-01T") for rec in recs)
    assert data == h.k1_transcript("skill")
    # K1 and K1w differ only in the closing marker record
    other = h.k1_transcript("wrapper").decode().splitlines()
    assert lines[:-1] == other[:-1] and lines[-1] != other[-1]
    with pytest.raises(ValueError):
        h.k1_events("bogus")
    # shuffle keeps the multiset of lines and changes the order; tear cuts the last line
    events = h.k1_events("skill")
    shuffled = h.build_transcript(events, shuffle_seed=3).decode().splitlines()
    assert sorted(shuffled) == sorted(lines) and shuffled != lines
    torn = h.build_transcript(events, tear=True)
    assert not torn.endswith(b"\n")
    with pytest.raises(ValueError):
        json.loads(torn.decode().splitlines()[-1])
    # dropped fields are gone everywhere; a record can lack its timestamp
    no_version = h.build_transcript(events, drop_fields=("version",)).decode()
    assert "version" not in no_version
    assert "timestamp" not in json.loads(
        h.build_transcript([h.say(1, ts=False)]).decode()
    )
    # the delegate directory carries agent-<id>.jsonl files
    out = h.write_delegate_dir(tmp_path / "subagents", h.k1_delegate_files())
    assert [p.name for p in out.iterdir()] == [f"agent-{h.AGENT1}.jsonl"]
    # text comes from the lorem list and gives the leak sampler material
    for n in range(60):
        text = h.lorem(n)
        assert len(text) >= 60 and text.count(" ") >= 3
        assert set(text.split()) <= set(h.LOREM_WORDS)
    # a seeded random cycle is deterministic and carries its own window
    one, two = h.random_cycle(5), h.random_cycle(5)
    assert one.data == two.data and one.wall_s == one.end_s - one.start_s
    assert h.random_cycle(6).data != one.data
    assert 25 <= len(one.data.decode().splitlines()) <= 70
    # the reader sees every generated line as a record
    for blob in (data, one.data, h.kitchen_sink_transcript()):
        parsed = r.parse_bytes(blob)
        assert parsed.snapshot.records == len(blob.decode().splitlines())
        assert parsed.unparseable == 0
    # K1 and K1w parse to the same last timestamp, 2430 s after the base
    for variant in ("skill", "wrapper"):
        parsed = r.parse_bytes(h.k1_transcript(variant))
        assert parsed.snapshot.last_ts == "2030-01-01T00:40:30Z"
        assert parsed.snapshot.records > 20


def test_t00_every_token_kind_and_no_other():
    sink = r.parse_bytes(h.kitchen_sink_transcript())
    assert r.event_kinds(sink.events) == m.TOK_KINDS
    for variant in ("skill", "wrapper"):
        k1 = r.parse_bytes(h.k1_transcript(variant))
        assert r.event_kinds(k1.events) <= m.TOK_KINDS
    # a few exact tokens of the contract, from the kitchen-sink transcript
    assert len(_toks(sink, "plan_only")) == 2
    forms = sorted((t.a, t.b) for t in _toks(sink, "invocation"))
    assert forms == [
        ("commit", "skill"),
        ("feature", "skill"),
        ("feature", "wrapper"),
        ("propagate", "skill"),
        ("propagate", "wrapper"),
    ]
    assert [t.a for t in _toks(sink, "lane_mention")] == ["fast"]
    assert len(_toks(sink, "human_prompt")) >= 3 and len(_toks(sink, "interrupt")) == 1
    assert len(_toks(sink, "auto_continuation")) == 1
    art1 = m.art_key_digest("art1")
    assert [(t.a, t.b) for t in _toks(sink, "runlong")] == [
        ("launch", art1),
        ("wait", art1),
        ("status", art1),
    ]
    # a wait or status call carries, in rl_check, the class a suite launch with the same
    # artifact key would get (the nearest-wait arm of pairing classes an unlabelled suite)
    assert [t.a for t in _toks(sink, "rl_check")] == [
        "suite-feature-tip",
        "suite-unlabelled",
        "suite-unlabelled",
    ]
    assert [t.a for t in _toks(sink, "rl_suite")] == ["l1"]
    assert [t.a for t in _toks(sink, "rl_label_sha")] == ["abc1234"]
    assert [t.a for t in _toks(sink, "rl_launch_with_wait")] == ["l1"]
    assert [(t.a, t.b) for t in _toks(sink, "tool_result") if t.a == "b1"] == [
        ("b1", "PASS")
    ]
    assert [(t.a, t.b) for t in _toks(sink, "agent_dispatch")] == [("a1", "sonnet")]
    assert sorted((t.a, t.b) for t in _toks(sink, "stage_kw")) == [
        ("a1", "author"),
        ("a1", "spec"),
        ("m1", "fold"),
        ("m1", "spec"),
    ]
    assert [(t.a, t.b) for t in _toks(sink, "agent_ack")] == [("a1", h.AGENT1)]
    assert [(t.a, t.b, t.n) for t in _toks(sink, "notif_enqueue")] == [
        ("a1", "t1", 9000)
    ]
    assert [(t.a, t.b, t.n) for t in _toks(sink, "notif_delivery")] == [
        ("a1", "t1", 9000)
    ]
    assert [(t.a, t.n) for t in _toks(sink, "hook_success")] == [("e1", 500)]
    assert [t.n for t in _toks(sink, "turn_duration")] == [90000]
    assert [t.a for t in _toks(sink, "bash_bg")] == ["b1"]
    assert {t.a: t.b for t in _toks(sink, "bash_class")}["b1"] == "other"


def test_t00_events_carry_no_text():
    closed = (
        set(m.CHECK_CLASSES)
        | set(m.ROLES)
        | set(m.STAGE_KEYWORDS)
        | set(m.INVOCATION_NAMES)
        | {"wrapper", "skill", "launch", "wait", "status", "check", "other"}
        | {"fast", "full", "PASS", "FAIL", "ERROR", "INCOMPLETE", "INDETERMINATE"}
    )
    for data in (h.kitchen_sink_transcript(), h.k1_transcript("skill")):
        assert b"lorem" in data  # the fixture really holds text to leak
        parsed = r.parse_bytes(data)
        assert "lorem" not in repr(parsed.events).lower()
        for event in parsed.events:
            assert isinstance(event.toks, tuple)
            assert event.rtype in m.RECORD_TYPES
            for tok in event.toks:
                assert tok.kind in m.TOK_KINDS
                for field in (tok.a, tok.b):
                    # every field is an id (so a bounded token) or a closed-vocabulary word
                    assert field is None or m.ID_RE.fullmatch(field) or field in closed
                assert tok.n is None or isinstance(tok.n, int)
    # a long command and a long description leave only their derived tokens behind
    secret = "unusual-marker " + h.lorem(3)
    data = h.build_transcript(
        [
            h.bash(1, "b1", "pytest " + secret),
            h.agent(2, "a1", "opus", "review " + secret),
        ]
    )
    parsed = r.parse_bytes(data)
    assert "unusual" not in repr(parsed.events)
    assert [(t.a, t.b) for t in _toks(parsed, "bash_class")] == [("b1", "check")]
    assert [t.b for t in _toks(parsed, "stage_kw")] == ["review"]


STAGE_WORDS = (
    "review reviews reviewer rule ruling ruled verify author draft write writer fold "
    "folds folded plan spec spike probe vet audit implement task fix final whole "
    "implementer brick publish commit gate"
).split()


@pytest.mark.parametrize("word", STAGE_WORDS)
def test_t00_stage_words_each_emit_a_stage_kw_token(word):
    # every word the original stage classifier consults, or its keyword set holds
    events = [
        h.agent(1, "a1", "opus", f"lorem {word.upper()} ipsum"),
        h.sendmsg(2, "m1", "a1", f"dolor {word}"),
    ]
    parsed = r.parse_bytes(h.build_transcript(events))
    assert [(t.a, t.b) for t in _toks(parsed, "stage_kw")] == [
        ("a1", word),
        ("m1", word),
    ]


def test_t00_stage_words_unrelated_text_emits_none_and_phrases_keep_every_word():
    for text in ("lorem ipsum dolor", "reviewing planning", "prespec x", ""):
        parsed = r.parse_bytes(h.build_transcript([h.agent(1, "a1", "opus", text)]))
        assert _toks(parsed, "stage_kw") == [], text
    for text, want in (
        ("Plan reviewer", ["plan", "reviewer"]),
        ("folded spec findings", ["folded", "spec"]),
        ("verify the ruled item", ["ruled", "verify"]),
    ):
        parsed = r.parse_bytes(h.build_transcript([h.agent(1, "a1", "opus", text)]))
        assert sorted(t.b for t in _toks(parsed, "stage_kw")) == want, text


def test_t00_runlong_key_is_a_bounded_digest_and_unresolved_keys_stay_distinct():
    long_key = "k" * 300
    events = [
        h.runlong_launch(1, "l1", long_key, "x"),
        h.runlong_wait(2, "w1", long_key),
        h.runlong_launch(3, "l2", "$VAR", "x"),
        h.runlong_launch(4, "l3", "$OTHER", "x"),
        h.runlong_wait(5, "w2", "$VAR", mode="status"),
        h.runlong_launch(6, "l4", "-flag", "x"),
        h.runlong_wait(7, "w3", "dev-art"),
        h.runlong_wait(8, "w4", "tip-art"),
    ]
    parsed = r.parse_bytes(h.build_transcript(events))
    keys = [t.b for t in _toks(parsed, "runlong")]
    digest = m.art_key_digest(long_key)
    assert digest != long_key and long_key not in repr(parsed.events)
    assert re.fullmatch("[0-9a-f]{16}", digest) and m.ID_RE.fullmatch(digest)
    assert keys[0] == keys[1] == digest  # equality is all pairing needs
    assert m.art_key_digest("a") != m.art_key_digest("b")
    # a shell-variable key keeps its identity (two different variables stay different)
    unresolved = [k for k in keys[2:5]]
    assert all(k.startswith(m.ART_UNRESOLVED_PREFIX) for k in unresolved)
    assert all(m.ID_RE.fullmatch(k) for k in unresolved)
    assert unresolved[0] == unresolved[2] != unresolved[1]
    assert keys[5] is None  # an option-like or empty key is absent
    # a wait's rl_check carries the class a suite launch with that key would get
    checks = {t.b: t.a for t in _toks(parsed, "rl_check")}
    assert (checks["w3"], checks["w4"], checks["w1"]) == (
        "suite-dev-tip",
        "suite-feature-tip",
        "suite-unlabelled",
    )


def _kinds_of(record):
    parsed = r.parse_bytes(h.build_transcript([record]))
    (event,) = parsed.events
    return [t.kind for t in event.toks], event


def test_t00_auto_continuation_is_a_token_whatever_the_flags_say():
    auto = "auto_continuation"
    meta_kinds, meta_event = _kinds_of(h.prompt(1, origin=auto, meta=True))
    assert meta_kinds == ["auto_continuation"] and meta_event.meta is True
    head_kinds, _ = _kinds_of(h.prompt(1, "<system-reminder> x", origin=auto))
    assert head_kinds == ["auto_continuation"]
    compact = h.prompt(1, origin=auto, extra={"isCompactSummary": True})
    assert _kinds_of(compact)[0] == ["auto_continuation"]
    side_kinds, side_event = _kinds_of(h.prompt(1, origin=auto, sidechain=True))
    assert side_kinds == ["auto_continuation"] and side_event.sidechain is True
    plain_kinds, plain_event = _kinds_of(h.prompt(1, origin=auto))
    assert plain_kinds == ["auto_continuation"] and plain_event.meta is False
    # a record carrying tool_result blocks and that origin gets the token too, once
    both = h.result(1, "t1", extra={"turnOrigin": auto})
    assert _kinds_of(both)[0] == ["auto_continuation", "tool_result"]


def test_t00_human_prompts_are_unchanged_by_the_auto_continuation_rule():
    assert _kinds_of(h.prompt(1))[0] == ["human_prompt"]
    assert _kinds_of(h.prompt(1, origin="human"))[0] == ["human_prompt"]
    assert _kinds_of(h.prompt(1, "[Request interrupted by user]"))[0] == [
        "human_prompt",
        "interrupt",
    ]
    # a meta or `<`-headed or compact-summary HUMAN-origin record is no prompt at all
    assert _kinds_of(h.prompt(1, origin="human", meta=True))[0] == []
    assert _kinds_of(h.prompt(1, meta=True))[0] == []
    assert _kinds_of(h.prompt(1, "<local-command> x"))[0] == []
    assert _kinds_of(h.prompt(1, extra={"isCompactSummary": True}))[0] == []
    # any other origin is neither
    assert _kinds_of(h.prompt(1, origin="task_notification"))[0] == []


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("DONE", "DONE"),
        ("job status: DONE.", "DONE"),
        ("DONE and DONE", "DONE"),
        ("DONE RUNNING", None),
        ("RUNNING", None),
        ("RUNNING then DONE", None),
        ("RESULT: PASS rc=0 DONE", "PASS"),
        ("DONE\nRESULT: FAIL rc=1", "FAIL"),
        ("RESULT: PARALLEL-PASS rc=0 DONE", "PASS"),
        ("UNDONE", None),
        ("DONEX", None),
        ("RUN_LONG_DONE", None),
        ("done", None),
        ("no verdict here", None),
        ("", None),
    ],
)
def test_t00_tool_result_token_carries_a_result_verdict_or_done(text, want):
    parsed = r.parse_bytes(h.build_transcript([h.result(1, "t1", text)]))
    assert [(t.a, t.b) for t in _toks(parsed, "tool_result")] == [("t1", want)]


def test_t00_tool_result_done_is_read_from_nested_content_and_stays_closed():
    nested = {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": [{"type": "text", "text": "status DONE"}],
    }
    rec = {
        "type": "user",
        "timestamp": h.BASE_ISO,
        "message": {"role": "user", "content": [nested]},
    }
    parsed = r.parse_bytes(json.dumps(rec).encode() + b"\n")
    assert [t.b for t in _toks(parsed, "tool_result")] == ["DONE"]
    for word in ("PASS", "FAIL", "ERROR", "INCOMPLETE", "INDETERMINATE", "DONE"):
        assert word in m.TOOL_RESULT_VALUES
    assert m.TOOL_RESULT_VALUES <= set(m.VERDICTS) | {"INDETERMINATE", "DONE"}


def _ack(at, tool_id, result):
    return h.result(at, tool_id, "launched", extra={"toolUseResult": result})


def _acks(events):
    parsed = r.parse_bytes(h.build_transcript(events))
    return [(t.a, t.b) for t in _toks(parsed, "agent_ack")], parsed


def test_t00_sendmsg_to_token_carries_an_id_shaped_target_only():
    secret = "teammate-name with spaces " + h.lorem(2)
    events = [
        h.sendmsg(1, "m1", h.AGENT1, "fold spec"),
        h.sendmsg(2, "m2", "a" + "0123456789abcdef", "fold spec"),
        h.sendmsg(3, "m3", secret, "fold spec"),
        h.sendmsg(4, "m4", "reviewer@team", "fold spec"),
        h.sendmsg(5, "m5", "", "fold spec"),
        h.sendmsg(6, "m6", "x" * 65, "fold spec"),
        h.agent(7, "a9", "opus", "spec"),
    ]
    parsed = r.parse_bytes(h.build_transcript(events))
    assert [(t.a, t.b) for t in _toks(parsed, "sendmsg_to")] == [
        ("m1", h.AGENT1),
        ("m2", "a" + "0123456789abcdef"),
    ]
    # every SendMessage is still a dispatch, and nothing of a name-shaped target survives
    assert [t.a for t in _toks(parsed, "sendmsg_dispatch")] == [
        "m1",
        "m2",
        "m3",
        "m4",
        "m5",
        "m6",
    ]
    dump = repr(parsed.events)
    assert "teammate" not in dump and "reviewer@" not in dump and "xxxxx" not in dump
    # a non-string target and an Agent dispatch emit none
    odd = h.sendmsg(1, "m7", "a1", "x")
    odd["rec"]["message"]["content"][0]["input"]["to"] = ["a1"]
    again = r.parse_bytes(h.build_transcript([odd, h.agent(2, "a8", "opus", "spec")]))
    assert _toks(again, "sendmsg_to") == []


def test_t00_agent_ack_also_carries_resumed_and_revived_agent_ids():
    got, _ = _acks([_ack(1, "a1", {"agentId": h.AGENT1})])
    assert got == [("a1", h.AGENT1)]
    got, parsed = _acks([_ack(1, "m1", {"resumedAgentId": h.AGENT2})])
    assert got == [("m1", h.AGENT2)] and parsed.shapes["agent_ack"] == 1
    got, _ = _acks([_ack(1, "m1", {"revivedAgentId": "rev01"})])
    assert got == [("m1", "rev01")]
    # an id that is not id-shaped, or no id at all, is no ack
    for result in ({"resumedAgentId": "a b"}, {"revivedAgentId": 5}, {"status": "ok"}):
        assert _acks([_ack(1, "m1", result)])[0] == []
    # the single-tool_result condition still holds
    two = h.result(1, "m1", "x", extra={"toolUseResult": {"resumedAgentId": "r1"}})
    two["rec"]["message"]["content"].append(
        {"type": "tool_result", "tool_use_id": "m2", "content": "y"}
    )
    assert _acks([two])[0] == []


def test_t00_agent_ack_precedence_follows_the_dispatch_kind():
    both = {"agentId": h.AGENT1, "resumedAgentId": h.AGENT2, "revivedAgentId": "rev01"}
    # a SendMessage takes its resumed id, then its revived id, as the original does
    sm = [h.sendmsg(1, "m1", h.AGENT2, "x"), _ack(2, "m1", both)]
    assert _acks(sm)[0] == [("m1", h.AGENT2)]
    sm = [
        h.sendmsg(1, "m1", h.AGENT2, "x"),
        _ack(2, "m1", {**both, "resumedAgentId": 7}),
    ]
    assert _acks(sm)[0] == [("m1", "rev01")]
    # an Agent dispatch takes agentId; so does an ack whose dispatch is not in the prefix
    ag = [h.agent(1, "a1", "opus", "spec"), _ack(2, "a1", both)]
    assert _acks(ag)[0] == [("a1", h.AGENT1)]
    assert _acks([_ack(2, "zz", both)])[0] == [("zz", h.AGENT1)]
    # a SendMessage ack that carries only agentId still yields that id
    only = [h.sendmsg(1, "m1", h.AGENT2, "x"), _ack(2, "m1", {"agentId": h.AGENT1})]
    assert _acks(only)[0] == [("m1", h.AGENT1)]


K1_SHA256 = {
    "skill": "8365560142844f4308d3f6b4496281914d659eac232157dac34190d4e7d1c6cc",
    "wrapper": "d8ea745f34b87f8d00e9632ea469d3c42c3f93285c2be01c13ef7054a7a26b7a",
}


def test_t00_k1_default_bytes_are_pinned():
    for variant, want in K1_SHA256.items():
        assert hashlib.sha256(h.k1_transcript(variant)).hexdigest() == want
        events = h.k1_events(variant)
        assert hashlib.sha256(h.build_transcript(events)).hexdigest() == want
        assert h.k1_transcript(variant, agree_ids=False) == h.k1_transcript(variant)


def test_t00_k1_agree_ids_make_ack_agent_ids_equal_notification_task_ids():
    for variant in ("skill", "wrapper"):
        default = r.parse_bytes(h.k1_transcript(variant))
        agreed = r.parse_bytes(h.k1_transcript(variant, agree_ids=True))
        assert h.k1_transcript(variant, agree_ids=True) != h.k1_transcript(variant)
        assert agreed.snapshot.records == default.snapshot.records
        assert (
            agreed.snapshot.last_ts
            == default.snapshot.last_ts
            == "2030-01-01T00:40:30Z"
        )

        def pairs(parsed, kind):
            return {t.a: t.b for t in _toks(parsed, kind)}

        for parsed, equal in ((agreed, True), (default, False)):
            acks = pairs(parsed, "agent_ack")
            enq = pairs(parsed, "notif_enqueue")
            assert set(acks) == {"a1", "a2"}
            assert all((acks[i] == enq[i]) is equal for i in acks)
        # the delivery of the first dispatch agrees too, and the wait notifications keep
        # their own task ids
        assert pairs(agreed, "notif_delivery")["a1"] == h.AGENT1
        assert pairs(agreed, "notif_enqueue")["w1"] == "t3"
        assert pairs(agreed, "notif_enqueue")["w2"] == "t4"
    with pytest.raises(ValueError):
        h.k1_events("bogus", agree_ids=True)


# ---------------------------------------------------------------------------------------------
# t12: timestamps and order
# ---------------------------------------------------------------------------------------------


def test_t12ra_unique_timestamps_shuffled_give_identical_events():
    events = [h.wrapper(0, "feature")]
    for i in range(1, 31):
        events.append(h.say(10 * i) if i % 2 else h.edit(10 * i, f"e{i}"))
    base = r.parse_bytes(h.build_transcript(events))
    assert base.inversions == 0
    for seed in (1, 2, 3):
        shuffled = r.parse_bytes(h.build_transcript(events, shuffle_seed=seed))
        stamps = [e.t_ms for e in shuffled.events]
        assert (
            stamps != sorted(stamps) and shuffled.inversions > 0
        )  # the fixture is shuffled
        assert _sig(shuffled) == _sig(base)


def test_t12rb_equal_timestamps_follow_file_position():
    def order(ids):
        events = [h.edit(100, i) for i in ids]
        events.insert(0, h.edit(50, "first"))
        return _tool_ids(r.parse_bytes(h.build_transcript(events)))

    assert order(["x1", "x2", "x3"]) == ["first", "x1", "x2", "x3"]
    assert order(["x3", "x2", "x1"]) == ["first", "x3", "x2", "x1"]
    # the contract is on sorted_events itself: handed events out of file order, ties return
    # to file position
    parsed = r.parse_bytes(
        h.build_transcript([h.edit(100, "x1"), h.edit(100, "x2"), h.edit(100, "x3")])
    )
    scrambled = parsed._replace(events=list(reversed(parsed.events)))
    assert _tool_ids(scrambled) == ["x1", "x2", "x3"]


def test_t12rc_records_without_timestamp_are_excluded_and_counted():
    events = [
        h.say(10),
        h.say(30),
        h.say(0, ts=False),
        h.say(20),
        h.say(0, ts_raw="not-a-time"),
        h.say(40),
    ]
    parsed = r.parse_bytes(h.build_transcript(events))
    assert parsed.snapshot.records == 6
    assert parsed.untimed == 2
    assert len(parsed.events) == 6 and len(r.sorted_events(parsed)) == 4
    assert parsed.inversions == 1  # 10, 30, 20, 40: only 30 to 20 goes backwards
    assert parsed.shapes["ts"] == 4
    assert r.timestamped_pct(parsed) == pytest.approx(100 * 4 / 6)
    # distinct inversion counts for distinct orders
    descending = [h.say(t) for t in (40, 30, 20, 10)]
    assert r.parse_bytes(h.build_transcript(descending)).inversions == 3
    assert r.parse_bytes(h.build_transcript(descending[::-1])).inversions == 0
    # nothing at all: no percentage, never 0 percent
    assert r.timestamped_pct(r.parse_bytes(b"")) is None


def test_t12rd_fraction_digits_are_truncated_padded_and_z_is_required():
    cases = [
        ("2030-01-01T00:00:00.123999Z", 123),
        ("2030-01-01T00:00:00.5Z", 500),
        ("2030-01-01T00:00:00.05Z", 50),
        ("2030-01-01T00:00:00Z", 0),
        ("2030-01-01T00:00:01.999Z", 1999),
        ("2030-01-01T00:00:00.1234567Z", 123),
        ("2030-01-01T00:00:00.123", None),
        ("2030-01-01T00:00:00+00:00", None),
        ("2030-02-30T00:00:00Z", None),
        ("2030-01-01T00:00:60Z", None),
        (" 2030-01-01T00:00:00Z", None),
        ("", None),
    ]
    for raw, offset in cases:
        assert r.parse_ts(raw) == (None if offset is None else h.BASE_MS + offset), raw
        parsed = r.parse_bytes(h.build_transcript([h.say(0, ts_raw=raw)]))
        stamp = parsed.events[0].t_ms
        assert stamp == (None if offset is None else h.BASE_MS + offset), raw
    assert r.parse_ts(123) is None and r.parse_ts(None) is None
    assert r.format_ts(h.BASE_MS + 1999) == "2030-01-01T00:00:01.999Z"
    assert r.format_ts(h.BASE_MS) == h.BASE_ISO
    assert (
        r.format_ts(r.parse_ts("2030-12-31T23:59:59.001Z"))
        == "2030-12-31T23:59:59.001Z"
    )


# ---------------------------------------------------------------------------------------------
# t13r: a corrupt line
# ---------------------------------------------------------------------------------------------


def test_t13r_garbage_line_is_counted_and_reading_continues():
    lines = h.build_transcript([h.say(t) for t in (10, 20, 30, 40, 50)]).split(b"\n")
    lines.insert(2, b"{this is not json")
    parsed = r.parse_bytes(b"\n".join(lines))
    assert parsed.unparseable == 1
    assert parsed.snapshot.records == 6
    assert len(parsed.events) == 5
    assert [e.t_ms for e in r.sorted_events(parsed)][-1] == h.BASE_MS + 50000
    assert r.unparseable_pct(parsed) == pytest.approx(100 / 6)
    # not-an-object JSON, invalid UTF-8 and blank lines: the first two count, blanks do not
    lines.insert(1, b"[1, 2]")
    lines.insert(1, b"42")
    lines.insert(1, b"\xff\xfe broken")
    lines.insert(1, b"   ")
    again = r.parse_bytes(b"\n".join(lines))
    assert again.unparseable == 4 and again.snapshot.records == 9
    assert len(again.events) == 5
    assert r.unparseable_pct(r.parse_bytes(b"")) is None


def test_t13rb_records_with_non_dict_members_parse_and_do_not_crash():
    odd = [
        {"type": "user", "message": "x"},
        {"type": "assistant", "message": [1]},
        {"type": "user", "message": None},
        {"type": "user", "message": {"content": 7}},
        {
            "type": "user",
            "message": {"content": ["a", 3, None, {"type": "tool_result"}]},
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": 5}]},
        },
        {
            "type": "user",
            "toolUseResult": "x",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]},
        },
        {
            "type": "user",
            "toolUseResult": [1],
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t2", "content": [5, None]}
                ]
            },
        },
        {"type": "assistant", "message": {"content": "just text"}},
        {
            "type": "assistant",
            "message": {"content": [None, "x", {"type": "tool_use"}]},
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "u1", "name": "Bash", "input": "ls"}
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "u2", "name": "Agent", "input": [1]}
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "u3", "name": ["Skill"], "input": None}
                ]
            },
        },
        {"type": "attachment", "attachment": "x"},
        {"type": "attachment", "attachment": [1]},
        {"type": ["user"], "message": {}},
        {"type": "system", "subtype": ["turn_duration"], "durationMs": "5"},
        {"type": "queue-operation", "operation": "enqueue", "content": 5},
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t3",
                        "content": [{"type": "tool_result", "content": "RESULT: PASS"}],
                    }
                ]
            },
        },
    ]
    good = h.prompt(5)
    data = b"".join(json.dumps(rec).encode() + b"\n" for rec in odd)
    data += h.build_transcript([good])
    parsed = r.parse_bytes(data)
    assert parsed.unparseable == 0
    assert len(parsed.events) == len(odd) + 1 == parsed.snapshot.records
    assert [t.kind for t in parsed.events[-1].toks] == ["human_prompt"]
    # a deeply nested tool_result chain is read to a bounded depth, never a crash
    nest = {"type": "tool_result", "tool_use_id": "d1", "content": "RESULT: FAIL"}
    for _ in range(300):
        nest = {"type": "tool_result", "content": [nest]}
    deep = {"type": "user", "message": {"content": [nest]}}
    assert r.parse_bytes(json.dumps(deep).encode() + b"\n").unparseable == 0
    # a duration with an absurd number of digits does not crash the notification read
    huge = h.enqueue(1, "a1", "t1").copy()
    huge["rec"] = dict(
        huge["rec"],
        content=h.notification_text("t1", "a1", 0).replace(
            "<duration_ms>0", "<duration_ms>" + "9" * 6000
        ),
    )
    big = r.parse_bytes(h.build_transcript([huge]))
    assert big.unparseable == 0 and [t.kind for t in big.events[0].toks] == [
        "notif_enqueue"
    ]


# ---------------------------------------------------------------------------------------------
# t16r: shape drift, per-version census and GAP flags
# ---------------------------------------------------------------------------------------------


def test_t16ra_hook_success_absent_counts_zero_and_agent_without_ack_is_gap():
    events = [
        h.wrapper(0, "feature"),
        h.agent(10, "a1", "opus", "spec"),
        h.enqueue(20, "a1", "t1"),
    ]
    parsed = r.parse_bytes(h.build_transcript(events))
    assert parsed.shapes["hook_success"] == 0 and "hook_success" in parsed.shapes
    assert parsed.shapes["agent_ack"] == 0 and "agent_ack" in parsed.shapes
    assert parsed.gaps == frozenset(
        {"agent_ack"}
    )  # not hook_success: it has no producer rule
    # the same transcript with the ack present, and with two hook records, flags nothing
    fixed = events + [
        h.ack(11, "a1", h.AGENT1),
        h.hook(12, "e1", 5),
        h.hook(13, "e2", 6),
    ]
    again = r.parse_bytes(h.build_transcript(fixed))
    assert again.gaps == frozenset()
    assert again.shapes["agent_ack"] == 1 and again.shapes["hook_success"] == 2
    # no Agent at all: an absent ack is not a gap
    assert (
        r.parse_bytes(h.build_transcript([h.wrapper(0, "feature")])).gaps == frozenset()
    )
    assert r.gap_flags({"agent_ack": 0}, {"Agent": 1}) == frozenset(
        {"agent_ack", "task_enqueue", "task_delivery"}
    )


def test_t16rb_queue_operation_absent_flags_both_notification_shapes():
    base = [
        h.wrapper(0, "feature"),
        h.agent(10, "a1", "opus", "spec"),
        h.ack(11, "a1", h.AGENT1),
    ]
    parsed = r.parse_bytes(h.build_transcript(base))
    assert parsed.shapes["task_enqueue"] == 0 and parsed.shapes["task_delivery"] == 0
    assert parsed.gaps == frozenset({"task_enqueue", "task_delivery"})
    # either notification shape alone clears the gap
    with_delivery = base + [h.delivery(20, "a1", "t1")]
    assert r.parse_bytes(h.build_transcript(with_delivery)).gaps == frozenset()
    with_enqueue = base + [h.enqueue(20, "a1", "t1")]
    only = r.parse_bytes(h.build_transcript(with_enqueue))
    assert only.gaps == frozenset() and only.shapes["task_enqueue"] == 1
    # a queue-operation that is not an enqueue of a notification is not the shape
    other = {
        "at": 30,
        "rec": {"type": "queue-operation", "operation": "dequeue", "sessionId": "s"},
        "ts": True,
        "ts_raw": None,
    }
    assert r.parse_bytes(h.build_transcript(base + [other])).shapes["task_enqueue"] == 0


def test_t16rc_version_census_has_one_row_per_version_with_its_own_gaps():
    events = [
        h.wrapper(0, "feature"),
        h.agent(10, "a1", "opus", "spec"),
        h.ack(11, "a1", h.AGENT1),
        h.enqueue(20, "a1", "t1"),
        h.edit(30, "e1"),
        h.result(31, "e1"),
        h.edit(60, "e2", version="9.9.2"),
        h.say(70, version="9.9.2", ts=False),
        h.say(80, version="10.0.0"),
        h.say(90, version=None),
        h.say(95, version="not-a-version"),
    ]
    parsed = r.parse_bytes(h.build_transcript(events))
    rows = {row.version: row for row in parsed.versions}
    assert [row.version for row in parsed.versions] == [
        "9.9.1",
        "9.9.2",
        "10.0.0",
        "unknown",
    ]
    assert sum(row.records for row in parsed.versions) == len(events)
    assert rows["9.9.1"].gaps == frozenset()
    assert rows["9.9.2"].gaps == frozenset({"tool_result"})
    assert rows["10.0.0"].gaps == frozenset() and rows["unknown"].records == 2
    assert rows["9.9.2"].records == 2 and rows["9.9.2"].timestamped == 1
    assert (
        rows["9.9.1"].shapes["agent_ack"] == 1
        and rows["9.9.2"].shapes["agent_ack"] == 0
    )
    assert rows["9.9.2"].kinds["Agent"] == 0 and rows["9.9.2"].kinds["Edit"] == 1
    assert set(rows["9.9.1"].shapes) == set(m.SHAPES) and set(
        rows["9.9.1"].kinds
    ) == set(m.TOOL_KINDS)
    # the overall flags see the whole file: the first version holds the ack and the result
    assert parsed.gaps == frozenset()
    assert parsed.shapes["agent_ack"] == 1 and parsed.kinds["Agent"] == 1


def test_t16rd_census_rows_plus_unparseable_equal_snapshot_records():
    lines = h.build_transcript(
        [h.say(1), h.say(2, version="9.9.2"), h.say(3, version=None)]
    ).split(b"\n")
    lines.insert(1, b"not json")
    lines.insert(1, b"[1]")
    parsed = r.parse_bytes(b"\n".join(lines))
    assert parsed.unparseable == 2
    assert sum(row.records for row in parsed.versions) + parsed.unparseable == (
        parsed.snapshot.records
    )
    assert sum(row.records for row in parsed.versions) < parsed.snapshot.records


# ---------------------------------------------------------------------------------------------
# t26m: the closed role set and the opaque-id channel
# ---------------------------------------------------------------------------------------------


def test_t26m_role_of_maps_into_the_closed_set_and_id_re_is_a_channel_check():
    cases = [
        (None, "fable", "fable"),
        (None, "opus", "opus"),
        (None, "sonnet", "sonnet"),
        (None, "haiku", "haiku"),
        ("general-purpose", None, "general-purpose"),
        ("prose-writer", "opus", "prose-writer"),
        ("skill-reviewer", None, "skill-reviewer"),
        ("skill-content-reviewer", "haiku", "skill-content-reviewer"),
        ("general-purpose", "opus", "opus"),
        ("totally-unknown", None, "other"),
        ("totally-unknown", "gpt", "other"),
        (None, None, "other"),
        (7, ["opus"], "other"),
    ]
    for subagent_type, model, want in cases:
        assert m.role_of(subagent_type, model) == want, (subagent_type, model)
        assert want in m.ROLES
    ok17 = "a" + "0123456789abcdef"
    for good in ("a1", "aid00000000000001", ok17, "x" * 64, "A_b-9"):
        assert m.ID_RE.fullmatch(good), good
    for bad in ("", "x" * 65, "a/b", "a b", "a\n", "é"):
        assert not m.ID_RE.fullmatch(bad), bad
    # the reader applies both: unknown dispatch types become `other` and are counted
    events = [
        h.agent(1, "a1", None, "spec", subagent_type="totally-unknown"),
        h.agent(2, "a2", "haiku", "spec"),
        h.agent(3, "a3", None, "spec", subagent_type="skill-reviewer"),
        h.agent(4, "bad/id", "opus", "spec"),
    ]
    parsed = r.parse_bytes(h.build_transcript(events))
    assert [(t.a, t.b) for t in _toks(parsed, "agent_dispatch")] == [
        ("a1", "other"),
        ("a2", "haiku"),
        ("a3", "skill-reviewer"),
    ]
    assert parsed.roles_other == 1
    assert (
        parsed.shapes["agent_dispatch"] == 4
    )  # the shape counts the record, id or not
    assert "bad/id" not in repr(parsed.events)


# ---------------------------------------------------------------------------------------------
# t31r: the prefix pin and the single open
# ---------------------------------------------------------------------------------------------


def _first_lines(data, count):
    return b"".join(data.splitlines(keepends=True)[:count])


def test_t31ra_prefix_with_the_right_hash_reads_exactly_that_prefix(tmp_path):
    data = h.k1_transcript("skill")
    prefix = _first_lines(data, 20)
    digest = hashlib.sha256(prefix).hexdigest()
    path = h.write_transcript(tmp_path, data)
    parsed = r.read_transcript(path, prefix_bytes=len(prefix), prefix_sha256=digest)
    assert parsed.snapshot.records == 20 and parsed.snapshot.pinned is True
    assert parsed.snapshot.bytes == len(prefix) and parsed.snapshot.sha256 == digest
    # a record appended afterwards changes nothing
    path.write_bytes(data + h.build_transcript([h.say(9999)]))
    later = r.read_transcript(path, prefix_bytes=len(prefix), prefix_sha256=digest)
    assert later.snapshot == parsed.snapshot and later.events == parsed.events
    # a non-default prefix reads a distinct number of records
    shorter = _first_lines(data, 10)
    other = r.read_transcript(
        path,
        prefix_bytes=len(shorter),
        prefix_sha256=hashlib.sha256(shorter).hexdigest(),
    )
    assert other.snapshot.records == 10
    # without a prefix the whole file is read and nothing is pinned
    plain = r.read_transcript(path)
    assert plain.snapshot.pinned is False
    assert plain.snapshot.records == data.count(b"\n") + 1


def test_t31rb_wrong_hash_is_a_pin_error_that_names_no_content(tmp_path):
    data = h.k1_transcript("skill")
    prefix = _first_lines(data, 12)
    good = hashlib.sha256(prefix).hexdigest()
    path = h.write_transcript(tmp_path, data)
    r.read_transcript(path, prefix_bytes=len(prefix), prefix_sha256=good)  # the control
    wrong = hashlib.sha256(b"something else").hexdigest()
    with pytest.raises(r.PinError) as info:
        r.read_transcript(path, prefix_bytes=len(prefix), prefix_sha256=wrong)
    assert wrong not in str(info.value) and str(path) not in str(info.value)
    # the hash of a different prefix length is wrong for this length
    with pytest.raises(r.PinError):
        r.read_transcript(path, prefix_bytes=len(prefix) - 5, prefix_sha256=good)
    # a byte changed inside the prefix after the hash was taken
    path.write_bytes(b"X" + data[1:])
    with pytest.raises(r.PinError):
        r.read_transcript(path, prefix_bytes=len(prefix), prefix_sha256=good)
    # a prefix longer than the file, and half a pin
    path.write_bytes(data)
    with pytest.raises(r.PinError):
        r.read_transcript(path, prefix_bytes=len(data) + 1, prefix_sha256=good)
    with pytest.raises(ValueError):
        r.read_transcript(path, prefix_bytes=len(prefix))
    with pytest.raises(ValueError):
        r.read_transcript(path, prefix_sha256=good)
    with pytest.raises(r.ReadError):
        r.read_transcript(tmp_path / "absent.jsonl")


def test_t31rc_torn_last_line_is_cut_and_the_file_is_opened_once(tmp_path, monkeypatch):
    events = h.k1_events("skill")
    data = h.build_transcript(events, tear=True)
    cut = data.rfind(b"\n") + 1
    path = h.write_transcript(tmp_path, data)
    opened = []
    real_open = open

    def spy(*args, **kwargs):
        opened.append(args[0])
        return real_open(*args, **kwargs)

    monkeypatch.setattr(r, "open", spy, raising=False)
    parsed = r.read_transcript(path)
    assert opened == [path]
    assert parsed.snapshot.records == len(events) - 1 and parsed.unparseable == 0
    assert parsed.snapshot.bytes == cut
    assert parsed.snapshot.sha256 == hashlib.sha256(data[:cut]).hexdigest()
    assert parsed.snapshot.pinned is False
    # pinned over the torn bytes: the hash covers the pin, the snapshot the parsed bytes
    opened.clear()
    pinned = r.read_transcript(
        path, prefix_bytes=len(data), prefix_sha256=hashlib.sha256(data).hexdigest()
    )
    assert opened == [path] and pinned.snapshot.pinned is True
    assert pinned.snapshot.bytes == cut and pinned.snapshot.records == len(events) - 1
    assert pinned.snapshot.sha256 == parsed.snapshot.sha256
    # a file with no newline holds no complete record
    path.write_bytes(b'{"type":"user"}')
    assert r.read_transcript(path).snapshot.records == 0
    # reread returns the verified bytes and refuses a different hash or a short file
    monkeypatch.undo()
    path.write_bytes(data)
    assert r.reread(path, cut, parsed.snapshot.sha256) == data[:cut]
    with pytest.raises(r.PinError):
        r.reread(path, cut, hashlib.sha256(b"x").hexdigest())
    with pytest.raises(r.PinError):
        r.reread(path, len(data) + 1, parsed.snapshot.sha256)


# ---------------------------------------------------------------------------------------------
# t42: Params defaults and the model's vocabularies
# ---------------------------------------------------------------------------------------------


def test_t42_params_defaults_equal_the_d21_table():
    want = {
        "close_rerun_min": 15,
        "close_post_min": 30,
        "close_min_suite_min": 10,
        "hook_latency_s": 10,
        "long_call_s": 60,
        "lane_window_min": 30,
        "away_fence_k": 1.5,
        "away_min_gaps": 8,
        "min_cycles": 5,
        "max_unparseable_pct": 5,
        "min_timestamped_pct": 20,
        "leak_samples": 300,
        "leak_floor": 100,
        "leak_seed": 0,
    }
    assert m.Params()._asdict() == want
    assert m.Params().leak_floor == 100 and len(m.Params._fields) == 14
    changed = m.Params()._replace(hook_latency_s=5, leak_floor=5)
    assert changed.hook_latency_s == 5 and changed.leak_floor == 5
    assert changed.close_rerun_min == 15
    sources = m.param_sources(["hook_latency_s"])
    assert sources["hook_latency_s"] == "cli"
    assert [k for k, v in sources.items() if v == "cli"] == ["hook_latency_s"]
    assert set(sources) == set(m.Params._fields)
    assert set(m.param_sources().values()) == {"default"}
    with pytest.raises(ValueError):
        m.param_sources(["no_such_parameter"])


def test_t42_model_vocabularies():
    assert (
        len(m.LIMIT_IDS) == 18 and m.LIMIT_IDS[0] == "L01" and m.LIMIT_IDS[-1] == "L18"
    )
    assert m.CLASSES[0] == "CONTROLLER" and set(m.CLASSES) == set(m.PRIORITY)
    assert m.PRIORITY[0] == "MACHINE_WAIT" and m.PRIORITY[-1] == "CONTROLLER"
    assert m.RC == {"PASS": 0, "FAIL": 1, "ERROR": 2, "INCOMPLETE": 3}
    assert set(m.VERDICTS) == set(m.RC)
    assert m.STAGE_BUCKETS == m.STAGES + ("other",) and len(m.STAGES) == 13
    assert not set(m.STAGE_BUCKETS) & set(m.CHECK_CLASSES) - {"other"}
    assert "other" in m.CHECK_CLASSES and m.MISSING_LINKS[0] == "no-suite-launch"
    assert set(m.SHAPES) == set(m.SHAPE_TABLE) and len(m.SHAPES) == 15
    assert {k for k, v in m.SHAPE_TABLE.items() if v[0] == "CRITICAL"} >= {
        "ts",
        "feature_invocation",
        "tool_result",
        "bash_tool_use",
    }
    assert m.DISPATCH_CONDITIONAL <= {
        k for k, v in m.SHAPE_TABLE.items() if v[0] == "CRITICAL"
    }
    assert len(m.TOK_KINDS) == 24
    assert m.ID_RE.pattern == "^[A-Za-z0-9_-]{1,64}$"
    row = m.CheckRow("SUMCHECK", "pass", "ok")
    assert row.verdict in m.CHECK_VERDICTS
    assert m.Tok("invocation", "feature", "wrapper") == (
        "invocation",
        "feature",
        "wrapper",
        None,
    )
