"""Original-pin recovery checks; no replacement interpreter or scorer mock."""
import json
import os
from pathlib import Path

import pytest

from tools import prepare_chessbench_native as prepare

ROOT = Path(__file__).resolve().parents[2]


def test_reviewed_pin_and_exact_retained_qa_source():
    report, ledger = prepare.discover(files=[(
        Path('/scorer/data/scaffolds/qa/base.py'),
        ROOT / 'tests/fixtures/chessbench_native_sources/qa_base.py')])
    assert report['manifest_sha256'] == '1dd47b9a27c0598c89c55c08579e7f85655b821166bc8a8150645e9379479599'
    assert report['matched_blobs'] == 1 and report['candidate_complete'] is False
    assert report['verified'] is False
    expected = '3482024468ba85bda8c3e436e088077903c731d46f9216bfd36bd1060bc1808a'
    assert ledger['files']['blobs/' + expected]['sha256'] == expected
    interpreter, = [item for item in report['missing'] if '/opt/hg-python/bin/python3.12' in item['targets']]
    assert interpreter['sha256'] == '23bc148d2b48964b621c5561ff2b81c1d9c7b717967a7491efbf12f4304882b5'
    assert interpreter['size'] == 31028464


def test_inventory_and_incomplete_prepare_do_not_create_a_store(tmp_path, capsys):
    root = tmp_path / 'absent-store'
    source = ROOT / 'tests/fixtures/chessbench_native_sources/qa_base.py'
    args = ['--root', str(root), '--file', '/scorer/data/scaffolds/qa/base.py=' + str(source)]
    assert prepare.main(['inventory', *args]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['verified'] is False and report['installed']['missing']
    assert not root.exists()
    assert prepare.main(['prepare', *args]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['missing'] and not root.exists()


def test_wrong_interpreter_is_reported_not_accepted(tmp_path):
    candidate = tmp_path / 'retained/bin/python3.12'
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b'not the independently pinned interpreter')
    before = candidate.read_bytes(), candidate.stat().st_mtime_ns
    report, _ = prepare.discover(prepare.mappings(['/opt/hg-python=' + str(tmp_path / 'retained')]))
    assert {'target': '/opt/hg-python/bin/python3.12', 'reason': 'size mismatch'} in report['rejected_candidates']
    assert any('/opt/hg-python/bin/python3.12' in row['targets'] for row in report['missing'])
    assert before == (candidate.read_bytes(), candidate.stat().st_mtime_ns)


@pytest.mark.parametrize('mapping', ['/opt/hg-python=relative', 'relative=/absolute', '/opt/../bad=/absolute'])
def test_mapping_refuses_unowned_or_ambiguous_paths(mapping):
    with pytest.raises(ValueError, match='absolute'):
        prepare.mappings([mapping])


def test_check_missing_native_package_never_accepts_provider(tmp_path, capsys):
    root = tmp_path / 'provider-only'
    root.mkdir()
    (root / 'ready.json').write_text('{"profile":"provider"}')
    assert prepare.main(['check', '--root', str(root)]) == 1
    result = json.loads(capsys.readouterr().err)
    assert result['verified'] is False
    assert result['manifest_sha256'] == prepare.BINDING['sha256']


@pytest.mark.parametrize('prediction,expected', [('e7e5 b8c6', 1), ('e7e6 b8c6', 0), ('e7e5', 0), ('', 0)])
def test_actual_original_native_scorer_authored_trajectories(tmp_path, monkeypatch, prediction, expected):
    root = os.environ.get('HG_CHESS_NATIVE_RUNTIME_ROOT')
    if not root:
        pytest.skip('set HG_CHESS_NATIVE_RUNTIME_ROOT to a complete original-pin native scorer package')
    from harnessgrad import native_runtime_store
    try:
        native_runtime_store.verify_package(prepare.BINDING, Path(root))
    except FileNotFoundError:
        pytest.skip('independently pinned Chess native scorer bytes are missing; provider is insufficient')
    from data.datasets import chessbench_puzzles as dataset
    from eval.verification.verifier import _verify
    gold = {'fen': 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
            'moves': ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'],
            'answer': 'e7e5 b8c6', 'puzzle_id': 'PRIVATE-AUTHORED-REFERENCE'}
    spec = dataset.verify_for({'task_id': 'chessbench_puzzles/test/0', 'gold': gold})
    assert spec['native_runtime'] == prepare.BINDING
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'answer.txt').write_text(prediction)
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES', root)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    result = _verify(work, spec, '', sandbox=True, timeout_s=30, env={'kind': 'files'})
    assert result.get('score') == expected, result
    assert result['evidence']['scorer_input']['verified'] is True
    assert 'PRIVATE-AUTHORED-REFERENCE' not in json.dumps(result)


def test_duplicate_source_mapping_is_not_silently_selected():
    with pytest.raises(ValueError, match='duplicate'):
        prepare.mappings(['/opt/hg-python=/retained/a', '/opt/hg-python=/retained/b'])
