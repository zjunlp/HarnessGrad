"""The legacy trace module delegates to one canonical reader without changing results."""
from __future__ import annotations

import json

import pytest

from eval.observe import trace, tracing


def test_legacy_module_reexports_the_canonical_reader():
    assert trace.__all__ == [
        "first_object", "loop_facts", "ENDED_CRASHED", "ENDED_ANSWERED",
        "ENDED_PARSE_ERROR", "ENDED_BUDGET", "ENDED_UNKNOWN",
    ]
    for name in trace.__all__:
        assert getattr(trace, name) is getattr(tracing, name)
    assert trace._ended is tracing._ended
    for name in ("first_object", "loop_facts", "_ended"):
        assert getattr(trace, name).__module__ == "eval.observe.tracing"


def test_consumers_use_the_canonical_reader():
    from eval.containers import container_state
    from eval.run import runner
    from harnessgrad import channel
    from tools import serve_ui

    assert channel._loop_facts is tracing.loop_facts
    assert serve_ui._trace_first_object is tracing.first_object
    assert runner._last_identity is tracing._last_identity
    assert runner._last_usage is tracing._last_usage
    assert container_state._last_identity is tracing._last_identity


def test_legacy_loop_facts_uses_the_canonical_reply_parser(monkeypatch):
    reply = '{"command": "pwd"}}\n{"answer": "done"}'
    body = "\n".join(json.dumps(event) for event in [
        {"reply": reply},
        {"command": "pwd"},
        {"usage": {"input_tokens": 3, "output_tokens": 2}},
    ])
    seen = []
    original = tracing.first_object

    def read_reply(text):
        seen.append(text)
        return original(text)

    monkeypatch.setattr(tracing, "first_object", read_reply)
    facts = trace.loop_facts(body)
    assert seen == [reply]
    assert facts["model_calls"] == facts["commands"] == 1
    assert facts["answers"] == facts["parse_errors"] == 0
    assert facts["multi_object_replies"] == 1
    assert facts["tokens"] == 5
    assert facts["ended"] == tracing.ENDED_BUDGET
    assert tracing.loop_facts(body) == facts


@pytest.mark.parametrize("reply,expected", [
    ('{"command": "pwd"}}\n{"answer": "done"}', {"command": "pwd"}),
    ('```json\n{"answer": "done"}\n```', {"answer": "done"}),
    ('prose {broken} {"answer": "done"} trailing', {"answer": "done"}),
    ("no JSON object", None),
    ("", None),
])
def test_reply_parser_keeps_first_complete_object_rule(reply, expected):
    assert tracing.first_object(reply) == expected
    assert trace.first_object(reply) == expected


@pytest.mark.parametrize("field,reader", [
    ("harness_identity", tracing._last_identity),
    ("usage", tracing._last_usage),
])
def test_metadata_reader_keeps_last_dictionary_and_absence_rules(field, reader):
    assert reader("") is None
    assert reader(json.dumps({field: None})) is None
    last = {"agent_model": "final", "calls": 2}
    body = "\n".join([
        " ", "not JSON", "[]", "null",
        json.dumps({field: {"calls": 1}}),
        '{"' + field + '":',
        json.dumps({field: last}),
        json.dumps({field: "not a dictionary"}),
        "{truncated",
    ])
    assert reader(body) == last
    assert reader(body + "\n" + json.dumps({field: {}})) == {}


def test_loop_facts_and_metadata_reader_use_the_same_final_usage():
    body = "\n".join([
        '{"usage": {"input_tokens": 1, "output_tokens": 1}}',
        "not JSON",
        '{"usage": {"input_tokens": 7, "output_tokens": 5}}',
        '{"usage": null}',
        '{"usage":',
    ])
    usage = tracing._last_usage(body)
    facts = tracing.loop_facts(body)
    assert facts["tokens"] == usage["input_tokens"] + usage["output_tokens"] == 12
    assert trace.loop_facts(body) == facts
