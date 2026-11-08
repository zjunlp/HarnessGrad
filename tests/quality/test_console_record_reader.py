"""Offline record parsing/projection checks at the canonical reader boundary."""
import json

import pytest

from console import record_reader as reader
from console import server


def test_public_helpers_share_canonical_owner():
    for name in ('read_json', 'read_curve', 'as_int', 'as_num', 'calls_per_task',
                 'model_in_loop', 'dir_count', 'split_id', 'pretty', 'pool_of', 'run_row'):
        assert getattr(server, name) is getattr(reader, name)


def test_curve_states_and_corruption(tmp_path):
    path = tmp_path / 'curve.jsonl'
    assert reader.read_curve(tmp_path) == ([], 'missing', 0)
    path.write_text(' \n')
    assert reader.read_curve(tmp_path) == ([], 'empty', 0)
    path.write_text('broken\n[]\nnull\n')
    assert reader.read_curve(tmp_path) == ([], 'unparseable', 3)
    path.write_bytes(b'{"score": null}\n\xff\n{"score": 0}\n')
    assert reader.read_curve(tmp_path) == ([{'score': None}, {'score': 0}], 'ok', 1)
    path.unlink()
    path.mkdir()
    assert reader.read_curve(tmp_path) == ([], 'unreadable', 0)


@pytest.mark.parametrize('content', ['[]', 'null', 'broken', '\ufffd'])
def test_non_object_json_is_unknown(tmp_path, content):
    path = tmp_path / 'run_meta.json'
    assert reader.read_json(path) == {}
    path.write_text(content)
    assert reader.read_json(path) == {}


def test_record_projection_retains_unknown_and_zero(tmp_path):
    run = tmp_path / 'rsi10-20261027T010203Z-method-valid_unseen'
    run.mkdir()
    (run / 'run_meta.json').write_text(json.dumps({'dataset': 'fixture', 'improver': {'model': 'legacy'}}))
    unknown = reader.run_row(run, {})
    assert unknown['kind'] == 'unknown'
    assert unknown['status'] == 'empty'
    assert unknown['curve_state'] == 'missing'
    assert unknown['last'] is unknown['calls'] is unknown['invalid'] is None
    points = [
        {'round': 0, 'score': None},
        {'round': 1, 'score': float('nan'), 'n_scored': 2, 'n_invalid': 0,
         'cost': {'harness_model_calls': 0}, 'side': 'eval', 'timeout': True},
    ]
    row = reader.run_row(run, {run.name: {'pid': 123}}, points, 'ok', 0)
    assert row['best'] is row['last'] is row['calls'] is row['invalid'] is None
    assert row['calls_per_task'] == 0
    assert row['model_in_loop'] is False
    assert row['pool'] == 'valid_unseen'
    assert row['status'] == 'running'
    assert row['improver'] == 'legacy'
    assert 'timeout' not in row
    points.append({'round': 2, 'score': 0, 'n_scored': 0, 'cost': {'harness_model_calls': None}})
    row = reader.run_row(run, {}, points, 'ok', 0)
    assert row['best'] == row['last'] == 0
    assert row['calls_per_task'] is row['model_in_loop'] is None


def test_projection_reads_from_canonical_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, 'read_curve', lambda path: ([{'round': 4, 'score': 0.5}], 'ok', 0))
    monkeypatch.setattr(reader, 'read_json', lambda path: {'dataset': 'injected'})
    row = server.run_row(tmp_path, {})
    assert row['dataset'] == 'injected'
    assert row['best_round'] == 4
    assert row['last'] == 0.5
