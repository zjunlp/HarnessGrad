"""Missing cache evidence must stay unknown independently of total usage."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from methods import editor


@pytest.fixture(autouse=True)
def reset():
    editor.reset_spent()
    editor._PAST_META_USAGE.update(reported_total=0, complete=True)
    yield
    editor.reset_spent()
    editor._PAST_META_USAGE.update(reported_total=0, complete=True)


def cli(**usage):
    return editor._account_cli_observation('dsh-usage ' + json.dumps(usage))


def endpoint(prompt, output, total, cached='missing'):
    usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=output, total_tokens=total)
    if cached != 'missing':
        usage.prompt_tokens_details = {'cached_tokens': cached}
    editor._record_usage(usage, 'fake')


def test_cli_missing_cache_keeps_total_but_not_split():
    obs = cli(input_tokens=100, output_tokens=10, total_tokens=110)
    assert obs['complete'] and not obs['cache_read_complete']
    assert obs['lower_bounds']['input'] == 0
    assert editor.parse_usage_lines('dsh-usage ' + json.dumps(
        {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110})) is None
    assert editor.method_usage()['total_tokens'] == 110
    assert editor.method_usage()['input'] is None
    assert editor.cache_read_tokens() is None
    assert editor.spent_tokens() is None


def test_endpoint_missing_cache_keeps_total_but_not_split():
    endpoint(100, 10, 110)
    assert editor.method_usage()['total_tokens'] == 110
    assert editor.method_usage()['input'] is None
    assert editor.cache_read_tokens() is None


@pytest.mark.parametrize('channel', ['cli', 'endpoint'])
def test_explicit_zero_is_known(channel):
    if channel == 'cli':
        cli(input_tokens=100, output_tokens=10, cache_read_tokens=0, total_tokens=110)
    else:
        endpoint(100, 10, 110, 0)
    assert editor.cache_read_tokens() == 0
    assert editor.method_usage()['input'] == 100
    assert editor.method_usage()['total_tokens'] == 110


@pytest.mark.parametrize('missing_first', [True, False])
def test_mixed_known_and_unknown_never_becomes_zero(missing_first):
    operations = [lambda: endpoint(100, 10, 110),
                  lambda: cli(input_tokens=20, output_tokens=2, cache_read_tokens=3, total_tokens=25)]
    if not missing_first:
        operations.reverse()
    for operation in operations:
        operation()
    assert editor.method_usage()['total_tokens'] == 135
    assert editor.method_usage()['input'] is None
    assert editor.cache_read_tokens() is None


def test_multiple_cli_calls_one_missing_cache_is_unknown():
    cli(input_tokens=10, output_tokens=2, cache_read_tokens=4, total_tokens=16)
    cli(input_tokens=20, output_tokens=3, total_tokens=23)
    assert editor.method_usage()['total_tokens'] == 39
    assert editor.cache_read_tokens() is None and editor.method_usage()['input'] is None


def test_normalized_zero_does_not_override_unknown_wire_presence():
    cli(input_tokens=10, output_tokens=2, cache_read_tokens=0, total_tokens=12,
        usage_evidence={'source': 'pi-ai', 'raw': {'total_tokens': 12},
                        'fields': {'totalTokens': 'observed', 'cacheReadTokens': 'unknown'}})
    assert editor.method_usage()['total_tokens'] == 12
    assert editor.cache_read_tokens() is None and editor.method_usage()['input'] is None


def test_claimed_wire_presence_without_cache_value_stays_unknown():
    cli(input_tokens=10, output_tokens=2, cache_read_tokens=0, total_tokens=12,
        usage_evidence={'source': 'pi-ai', 'raw': {'total_tokens': 12},
                        'fields': {'totalTokens': 'observed', 'cacheReadTokens': 'observed'}})
    assert editor.method_usage()['total_tokens'] == 12
    assert editor.cache_read_tokens() is None and editor.method_usage()['input'] is None


def test_wire_explicit_zero_is_known_even_without_normalized_field():
    cli(input_tokens=10, output_tokens=2, total_tokens=12,
        usage_evidence={'source': 'openai-completions-wire',
                        'raw': {'total_tokens': 12, 'prompt_cache_hit_tokens': 0},
                        'fields': {'totalTokens': 'observed', 'cacheReadTokens': 'observed'}})
    assert editor.cache_read_tokens() == 0
    assert editor.method_usage()['input'] == 10


def test_endpoint_total_can_be_known_when_split_fields_missing():
    editor._record_usage(SimpleNamespace(total_tokens=110), 'fake')
    assert editor.method_usage()['total_tokens'] == 110
    assert editor.method_usage()['complete'] is True
    assert editor.method_usage()['input'] is None and editor.method_usage()['output'] is None
    assert editor.cache_read_tokens() is None


def test_endpoint_cache_presence_stays_known_even_when_prompt_missing():
    editor._record_usage(SimpleNamespace(total_tokens=110, completion_tokens=10,
        prompt_tokens_details={'cached_tokens': 0}), 'fake')
    assert editor.method_usage()['total_tokens'] == 110
    assert editor.method_usage()['input'] is None
    assert editor.cache_read_tokens() == 0


def test_multiple_endpoint_calls_keep_unknown_cache_split():
    endpoint(100, 10, 110, 30)
    endpoint(30, 4, 34)
    assert editor.method_usage()['total_tokens'] == 144
    assert editor.method_usage()['input'] is None and editor.cache_read_tokens() is None


def test_real_cli_accounting_retains_total_when_cache_field_missing(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'methods'))
    import cli_improver
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    transcript = 'dsh-usage ' + json.dumps({'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110})
    base = tmp_path / 'base'; base.mkdir()
    parent = tmp_path / 'parent'; parent.mkdir()
    def propose(req, parent_path, **kwargs):
        return {'candidate': str(tmp_path / 'candidate'), 'files': [], 'changed': [],
                'exit_code': 0, 'seconds': 1.0, 'note': 'no change',
                'transcript': transcript, 'reported': {}}
    monkeypatch.setattr(cli_improver, 'propose', propose)
    editor._ask_cli('synthetic', base, parent, {'name': 'dsh'}, evidence_channel=False)
    assert editor.method_usage()['total_tokens'] == 110
    assert editor.method_usage()['input'] is None
    assert editor.cache_read_tokens() is None
