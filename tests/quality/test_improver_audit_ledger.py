"""Offline receipts preserve reported usage independently of display tails."""
import json
from pathlib import Path

from methods import editor


def test_long_transcript_usage_precedes_retained_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    secret = 'SYNTHETIC_SECRET_DO_NOT_COPY'
    transcript = ('dsh-usage {"input_tokens": 123,"output_tokens": 7,"cache_read_tokens": 4}\n'
                  + secret + '\n' + '界' * 210_000)
    result = {'transcript': transcript, 'reported': {'environment': secret},
              'audit_identity': {'parent_sha': 'a' * 40, 'evidence_sha': secret}}
    workspace = tmp_path / 'ask'
    editor._keep_improver_transcript(result, workspace)
    names = editor._keep_improver_audit(result, workspace)
    metadata, ledger = [json.loads((tmp_path / name).read_text()) for name in names]
    assert metadata['original_chars'] == len(transcript)
    assert metadata['retained_start'] == len(transcript) - 200_000
    assert metadata['retained_end'] == len(transcript)
    assert metadata['retained_chars'] == 200_000
    assert metadata['truncated'] is True
    assert ledger['usage'] == {'input': 123, 'output': 7, 'cache_read': 4, 'calls': 1}
    assert ledger['parent_sha'] == 'a' * 40
    assert ledger['evidence_sha'] == 'unknown'
    assert ledger['patch_base_sha'] == 'unknown'
    assert ledger['retries'] is None and ledger['provider_seconds'] is None
    assert secret not in ''.join((tmp_path / name).read_text() for name in names)
    assert secret not in (tmp_path / 'improver-ask.log').read_text()


def test_unknown_usage_is_null_not_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    names = editor._keep_improver_audit({'transcript': 'no provider usage'}, Path('short'))
    metadata, ledger = [json.loads((tmp_path / name).read_text()) for name in names]
    assert metadata['truncated'] is False
    assert metadata['retained_start'] == 0
    assert ledger['status'] == 'unknown'
    assert ledger['usage'] is None


def test_per_call_allowlist_coverage_and_aggregate_compatibility(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    transcript = ('ordinary line\n'
                  'dsh-usage {"input_tokens": 12,"output_tokens": 3,"cache_read_tokens": 2,"secret":"NO_COPY"}\n'
                  'dsh-usage {"input_tokens": 5,"output_tokens": true}\n'
                  'dsh-usage MALFORMED_SECRET\n')
    names = editor._keep_improver_audit({'transcript': transcript}, Path('calls'))
    raw = (tmp_path / names[1]).read_text()
    ledger = json.loads(raw)
    assert 'NO_COPY' not in raw and 'MALFORMED_SECRET' not in raw
    records = ledger['records']
    assert [r['ordinal'] for r in records] == [1, 2, 3]
    assert [r['line_index'] for r in records] == [1, 2, 3]
    assert records[1]['output'] is None and records[1]['cache_read'] is None
    assert records[1]['coverage'] == {'input': 'reported', 'output': 'invalid', 'cache_read': 'missing'}
    assert records[2]['status'] == 'malformed'
    assert all(records[2][k] is None for k in ('input', 'output', 'cache_read'))
    assert ledger['canonical_usage_aggregate'] == editor.parse_usage_lines(transcript)
    assert ledger['usage'] == {'input': None, 'output': None, 'cache_read': None, 'calls': None}
    assert ledger['status'] == 'partially_reported'
    for field in ('input', 'output', 'cache_read'):
        assert ledger['canonical_usage_aggregate'][field] == sum(r[field] for r in records if r[field] is not None)
    assert ledger['canonical_usage_aggregate']['calls'] == sum(r['status'] == 'parsed' for r in records)


def test_staged_parent_and_evidence_identities_are_distinct(tmp_path):
    run = tmp_path / 'run'
    channel = run / '_harnessgrad'
    state = channel / 'states/round-1'
    state.mkdir(parents=True)
    (channel / 'round.json').write_text(json.dumps({'round': 3, 'identity': {'harness_sha': 'c' * 40}}))
    (channel / 'states/index.json').write_text(json.dumps({'kept': ['round-1'],
        'round-1': {'sha': 'a' * 40, 'harness_dir': str(state)}}))
    identity = editor._editor_audit_identity(state, channel)
    assert identity == {'selected_parent_round': 1, 'selected_parent_sha': 'a' * 40,
                        'evidence_round': 3, 'evidence_harness_sha': 'c' * 40,
                        'patch_base_sha': 'a' * 40}
    assert editor._editor_audit_identity(run, channel)['selected_parent_sha'] == 'c' * 40
    assert editor._editor_audit_identity(state, None)['selected_parent_sha'] == 'unknown'


def test_negative_reported_usage_remains_unknown_in_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, '_method_workspace', lambda: tmp_path)
    names = editor._keep_improver_audit({'transcript': 'dsh-usage {"input_tokens":-1}\n'}, Path('negative'))
    ledger = json.loads((tmp_path / names[1]).read_text())
    assert ledger['records'][0]['input'] is None
    assert ledger['records'][0]['coverage']['input'] == 'invalid'
