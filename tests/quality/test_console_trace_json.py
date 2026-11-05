"""Console trace API retains structured and malformed JSONL without trusting its contents."""
import json

from console import server


def test_trace_preserves_unknown_json_primitives_and_raw_lines(tmp_path, monkeypatch):
    root = tmp_path / 'runs'
    traces = root / 'demo' / 'round0_traces'
    traces.mkdir(parents=True)
    (traces / 'task.jsonl').write_text('\n'.join([
        json.dumps({'reply': {'tool': 'bash', 'extra': '<script>'}, 'step': 1}),
        json.dumps({'event': 'new_schema', 'payload': {'nested': [1, False]}}),
        'null', '[1, 2]', 'not valid JSON <b>',
        json.dumps({'command': 'ls', 'exit': 0, 'output': 'line\n'}),
    ]) + '\n')
    monkeypatch.setattr(server, 'RUNS', root)
    listing = server.api_traces('demo', 0)
    assert listing['count'] == 1
    result = server.api_trace('demo', 0, 'task.jsonl')
    assert result['events'][0]['kind'] == 'reply'
    assert json.loads(result['events'][0]['text'])['extra'] == '<script>'
    assert result['events'][1] == {'kind': 'structured', 'line': 2,
                                   'value': {'event': 'new_schema', 'payload': {'nested': [1, False]}}}
    assert result['events'][2]['value'] is None
    assert result['events'][3]['value'] == [1, 2]
    assert result['events'][4] == {'kind': 'raw', 'line': 5, 'text': 'not valid JSON <b>'}
    assert result['events'][5]['output'] == 'line\n'
    assert server.api_trace('demo', 0, '../task.jsonl') is None


def test_reply_truncation_is_explicit(tmp_path, monkeypatch):
    root = tmp_path / 'runs'
    traces = root / 'demo' / 'round0_traces'
    traces.mkdir(parents=True)
    (traces / 'task.jsonl').write_text(json.dumps({'reply': 'x' * 8001}) + '\n')
    monkeypatch.setattr(server, 'RUNS', root)
    event = server.api_trace('demo', 0, 'task.jsonl')['events'][0]
    assert event['truncated'] is True and len(event['text']) == 8000
    assert '8001' in event['note']


def test_unknown_or_malformed_lines_obey_event_limit(tmp_path, monkeypatch):
    root = tmp_path / 'runs'
    traces = root / 'demo' / 'round0_traces'
    traces.mkdir(parents=True)
    (traces / 'task.jsonl').write_text(('not-json\n' * 1201))
    monkeypatch.setattr(server, 'RUNS', root)
    events = server.api_trace('demo', 0, 'task.jsonl')['events']
    assert len(events) == 1201
    assert events[-1]['label'] == '截断'
