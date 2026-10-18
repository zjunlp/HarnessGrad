"""Native adapter/verifier boundaries on synthetic conversations; no live model."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from data.datasets import locomo
from data.scaffolds.command_control import materialize, solve, run_check

summarize = locomo._load_asset("report.py").summarize

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def synthetic(monkeypatch):
    row = {'sample_id': 'conv-999', 'conversation': {'speaker_a': 'Ada', 'speaker_b': 'Bo',
           'session_1_date_time': '1 January 2024',
           'session_1': [{'speaker': 'Ada', 'text': 'I live in Paris.'}]},
           'qa': [{'question': 'Where does Ada live?', 'answer': 'Paris', 'category': 4,
                   'evidence': ['SECRET_EVIDENCE']},
                  {'question': 'Where was Ada born?', 'adversarial_answer': 'Rome',
                   'category': 5, 'evidence': []}]}
    pool = {'locomo/conv-999': row}
    monkeypatch.setattr(locomo, 'rows', lambda: pool)
    return locomo.control_task('locomo/conv-999')


def test_tasks_and_setup_contain_only_public_inputs(synthetic):
    task, metadata = locomo.load()
    assert task == [synthetic]
    assert metadata == {synthetic['task_id']: locomo.METRIC}
    public = json.dumps([task, locomo.SETUP[synthetic['task_id']]])
    for private in ('SECRET_EVIDENCE', 'refusal_letter', 'gold.json', 'score_sum'):
        assert private not in public
    assert 'conversation.md' in public and 'answers.json' in public
    assert locomo.ENV[synthetic['task_id']]['network'] == 'none'
    assert locomo.ENV[synthetic['task_id']]['kind'] == 'files'
    assert not hasattr(locomo, 'SPLIT')


@pytest.mark.parametrize('oracle,expected', [(True, 1.0), (False, 0.0)])
def test_command_controls_and_report(synthetic, tmp_path, oracle, expected):
    materialize(locomo, synthetic, tmp_path)
    assert not (tmp_path / 'locomo_verify/gold.json').exists()
    if oracle:
        solve(locomo, synthetic, tmp_path)
    reward, detail = run_check(locomo, synthetic, tmp_path)
    assert reward == expected, detail
    evidence = json.loads((tmp_path / 'verifier/evidence.json').read_text())
    assert evidence['measured'] is True
    assert evidence['scorer_input'] == {**locomo.VERIFY[synthetic['task_id']]['scorer_input'],
                                       'expected_sha256': locomo.VERIFY[synthetic['task_id']]['scorer_input']['sha256'],
                                       'verified': True}
    assert 'SECRET_EVIDENCE' not in json.dumps(evidence)
    result = summarize([evidence], [synthetic['task_id']])
    assert result['micro_avg_reward'] == expected
    assert result['expected_questions'] == 2


def test_malformed_answers_follow_harbor_zero(synthetic, tmp_path):
    materialize(locomo, synthetic, tmp_path)
    (tmp_path / 'answers.json').write_text('```json\n{}\n```')
    assert run_check(locomo, synthetic, tmp_path)[0] == 0


def test_verifier_refuses_changed_gold_and_clears_stale_reward(synthetic, tmp_path):
    materialize(locomo, synthetic, tmp_path)
    solve(locomo, synthetic, tmp_path)
    assert run_check(locomo, synthetic, tmp_path)[0] == 1
    (tmp_path / 'locomo_verify/gold.json').write_text('{}')
    spec = locomo.VERIFY[synthetic['task_id']]
    result = subprocess.run([sys.executable, *spec['argv'][1:]], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 3
    assert not (tmp_path / 'verifier/reward.txt').exists()
    evidence = json.loads((tmp_path / 'verifier/evidence.json').read_text())
    assert evidence['measured'] is False


def test_missing_and_duplicate_reports(synthetic, tmp_path):
    tid = synthetic['task_id']
    result = summarize([], [tid])
    assert 'micro_avg_reward' not in result and result['unmeasured_tasks'] == 1
    materialize(locomo, synthetic, tmp_path)
    solve(locomo, synthetic, tmp_path)
    run_check(locomo, synthetic, tmp_path)
    evidence = json.loads((tmp_path / 'verifier/evidence.json').read_text())
    with pytest.raises(ValueError, match='duplicate'):
        summarize([evidence, evidence], [tid])
    evidence['num_questions'] = 1
    with pytest.raises(ValueError, match='coverage'):
        summarize([evidence], [tid])


def test_import_does_not_read_data(tmp_path):
    env = {**os.environ, 'HG_LOCOMO_DATA': str(tmp_path / 'absent')}
    result = subprocess.run([sys.executable, '-c',
        'from data.datasets import locomo; assert locomo.KIND == "dataset"; '
        'assert not locomo.provision_status()["present"]'], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_pinned_download_and_invalid_data(monkeypatch, tmp_path):
    info = locomo.downloads()[0]
    assert info['version'] in info['url'] and info['sha256'] == locomo.DATA_SHA256
    path = tmp_path / 'locomo10.json'
    path.write_text('[]')
    monkeypatch.setattr(locomo, 'DATA_FILE', path)
    locomo.rows.cache_clear()
    with pytest.raises(ValueError, match='digest'):
        locomo.rows()
    locomo.rows.cache_clear()
