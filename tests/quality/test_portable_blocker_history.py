"""Portable citation views preserve the original independently pinned record."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
from tools import check_dataset_blockers as checker


def test_original_and_portable_have_distinct_bound_identities():
    assert checker.HISTORY_SHA256 == '4c778d435f4147eb04dbcee4e37a2bac3f0ac5a55584e3adf252ab12972d1300'
    original = checker.historical_baseline(1)
    view = checker.historical_baseline(2)
    assert original['schema_version'] == 1 and view['schema_version'] == 2
    assert view['records'] == checker.portable_citations(original['records'])
    assert checker.PORTABLE_HISTORY_SHA256 != checker.HISTORY_SHA256
    for before, after in zip(original['records'], view['records']):
        for key in ('record_id', 'run_id', 'status', 'effect', 'observation'):
            assert before['record'][key] == after['record'][key]


def test_check_never_stats_historical_host_paths(monkeypatch):
    original = checker.historical_baseline(1)
    host_paths = {item['path'] for row in original['records']
                  for item in row['record']['evidence'] if item['kind'] == 'external-reference'}
    real_stat = Path.stat
    def stat(path, *args, **kwargs):
        assert str(path) not in host_paths, 'historical citation was treated as a filesystem input'
        return real_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat)
    result = checker.validate(checker.read_json(checker.REGISTRY))
    assert result['historical_records'] == len(original['records'])


def test_missing_portable_file_is_not_regenerated(tmp_path, monkeypatch):
    missing = tmp_path / 'not-created' / checker.PORTABLE_HISTORY.name
    monkeypatch.setattr(checker, 'PORTABLE_HISTORY', missing)
    with pytest.raises(checker.RegistryError, match='missing.*no automatic regeneration'):
        checker.historical_baseline(2)
    assert not missing.parent.exists()


def test_original_byte_drift_is_rejected_before_portable_view(tmp_path, monkeypatch):
    path = tmp_path / checker.HISTORY.name
    path.write_bytes(checker.HISTORY.read_bytes() + b' ')
    monkeypatch.setattr(checker, 'HISTORY', path)
    with pytest.raises(checker.RegistryError, match='historical baseline pin changed'):
        checker.historical_baseline(2)


def test_semantic_drift_cannot_be_approved_by_only_repinning_view(tmp_path, monkeypatch):
    value = deepcopy(checker.historical_baseline(2))
    value['records'][0]['record']['effect'] = 1
    import json
    path = tmp_path / checker.PORTABLE_HISTORY.name
    path.write_text(json.dumps(value))
    monkeypatch.setattr(checker, 'PORTABLE_HISTORY', path)
    monkeypatch.setattr(checker, 'PORTABLE_HISTORY_SHA256', hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(checker.RegistryError, match='not the original projection'):
        checker.historical_baseline(2)


def test_schema2_external_citation_cannot_smuggle_a_host_path(tmp_path):
    file = tmp_path / 'observation.md'; file.write_text('checked observation')
    evidence = [{'kind': 'repository', 'path': file.name, 'locator': 'checked observation'},
                {'kind': 'external-reference', 'locator': 'historical text only'}]
    checker._evidence(evidence, tmp_path, 'authored', schema_version=2)
    evidence[-1]['path'] = '/must-not-be-opened/receipt.json'
    with pytest.raises(checker.RegistryError, match='fields must be'):
        checker._evidence(evidence, tmp_path, 'authored', schema_version=2)
