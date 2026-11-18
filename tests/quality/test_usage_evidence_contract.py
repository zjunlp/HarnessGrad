"""Wire presence and raw totals survive independently of normalized CLI counters."""
import json
from pathlib import Path

from methods import editor
from methods.usage_evidence import usage_evidence


def test_explicit_zero_missing_and_inconsistent_raw_total(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    base = {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12,
        'session_id': 'session-1', 'attempt_id': 'attempt-1',
        'usage_evidence': {'source': 'openai-completions-wire',
          'raw': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 99,
                  'prompt_cache_hit_tokens': 0, 'api_key': 'DO_NOT_COPY'},
          'fields': {'inputTokens': 'derived', 'outputTokens': 'observed',
                     'totalTokens': 'derived', 'cacheReadTokens': 'observed'}}}
    missing = json.loads(json.dumps(base))
    missing['attempt_id'] = 'attempt-2'
    missing['usage_evidence']['raw'].pop('prompt_cache_hit_tokens')
    missing['usage_evidence']['fields']['cacheReadTokens'] = 'unknown'
    transcript = '\n'.join('dsh-usage ' + json.dumps(p) for p in [base, missing])
    names = editor._keep_improver_audit({'transcript': transcript}, Path('two'))
    raw = (tmp_path / names[1]).read_text()
    ledger = json.loads(raw)
    a, b = ledger['records']
    assert a['usage_evidence']['raw']['prompt_cache_hit_tokens'] == 0
    assert 'prompt_cache_hit_tokens' not in b['usage_evidence']['raw']
    assert b['usage_evidence']['fields']['cacheReadTokens'] == 'unknown'
    assert a['usage_evidence']['raw']['total_tokens'] == 99
    assert a['session_id'] == b['session_id'] == 'session-1'
    assert [a['attempt_id'], b['attempt_id']] == ['attempt-1', 'attempt-2']
    assert a['request_id'] is None and a['retry_of'] is None and a['provider_calls'] is None
    assert 'DO_NOT_COPY' not in raw
    assert ledger['canonical_usage_aggregate'] is None
    assert ledger['reported_usage_lower_bounds']['input'] == 10
    assert ledger['reported_usage_lower_bounds']['output'] == 4
    assert ledger['provider_completeness'] == 'unknown'


def test_old_normalized_zero_does_not_invent_provider_presence(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    names = editor._keep_improver_audit({'transcript':
        'dsh-usage {"input_tokens":1,"output_tokens":2,"cache_read_tokens":0}'}, Path('old'))
    row = json.loads((tmp_path/names[1]).read_text())['records'][0]
    assert row['cache_read'] == 0
    assert row['usage_evidence'] is None
    assert row['provider_calls'] is None


def test_pi_layer_and_nested_cached_tokens_keep_observation_scope():
    e = usage_evidence({'usage_evidence': {'source': 'pi-ai',
        'raw': {'cacheReadTokens': 0, 'prompt_tokens_details': {'cached_tokens': 0, 'secret': 'NO'}},
        'fields': {'cacheReadTokens': 'unknown'}}})
    assert e['source'] == 'pi-ai'
    assert e['raw']['cacheReadTokens'] == 0 and e['fields']['cacheReadTokens'] == 'unknown'
    assert e['raw']['prompt_tokens_details'] == {'cached_tokens': 0}


def test_malformed_evidence_is_unknown_instead_of_a_new_dispatch_error(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    lines = ['dsh-usage []', 'dsh-usage {"usage_evidence":{"source":[]}}',
        'dsh-usage {"usage_evidence":{"source":"pi-ai","raw":{"total_tokens":7},"fields":{"totalTokens":[]}}}']
    names = editor._keep_improver_audit({'transcript': '\n'.join(lines)}, Path('malformed'))
    rows = json.loads((tmp_path/names[1]).read_text())['records']
    assert rows[0]['status'] == 'malformed'
    assert rows[0]['request_id'] is None and rows[1]['usage_evidence'] is None
    assert rows[2]['usage_evidence']['fields']['totalTokens'] == 'unknown'


def test_duplicate_attempt_observations_not_double_counted_or_trusted(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    row = {'session_id': 'session-1', 'attempt_id': 'attempt-1',
           'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}
    transcript = '\n'.join('dsh-usage ' + json.dumps(row) for _ in range(2))
    observation = editor._cli_usage_observation(transcript)
    assert observation['lower_bounds']['input'] == 0
    assert observation['lower_bounds']['output'] == 2
    assert observation['lower_bounds']['calls'] == 1
    assert observation['total_tokens'] is None and not observation['complete']
    assert editor.parse_usage_lines(transcript) is None
    names = editor._keep_improver_audit({'transcript': transcript}, Path('duplicate'))
    ledger = json.loads((tmp_path/names[1]).read_text())
    assert len(ledger['records']) == 2 and ledger['records'][1]['duplicate_of'] == 1
    assert all(x is None for x in ledger['usage'].values())
    assert ledger['reported_usage_lower_bounds']['input'] == 0


def test_conflicting_attempt_observations_keep_safe_lower_bound():
    rows = [
        {'session_id':'s','attempt_id':'a','input_tokens':10,'output_tokens':3,'total_tokens':13},
        {'session_id':'s','attempt_id':'a','input_tokens':20,'output_tokens':1,'total_tokens':21},
    ]
    observation = editor._cli_usage_observation('\n'.join('dsh-usage '+json.dumps(x) for x in rows))
    assert observation['lower_bounds']['input'] == 0
    assert observation['lower_bounds']['output'] == 3
    assert observation['lower_bounds']['calls'] == 1
    assert observation['total_tokens'] is None and not observation['complete']
