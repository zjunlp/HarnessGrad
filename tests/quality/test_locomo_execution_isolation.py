"""Authored LoCoMo cases and mock identities; no benchmark data, model or Docker."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

from data.datasets import locomo
from harnessgrad import dataset_runtime, dataset_runtime_identity

ROOT = Path(__file__).resolve().parents[2]


def fake_artifact(root, char):
    instance = root / ('build-' + char)
    path = instance / 'venv'
    return {'dataset': 'locomo', 'profile': 'execution', 'runtime_id': char * 64,
            'manifest_path': str(instance / 'artifact.json'), 'path': str(path),
            'python': str(path / 'bin/python'), 'runtime_paths': [str(path)]}


def fixture_row():
    return {'sample_id': 'conv-999', 'conversation': {'speaker_a': 'Ada', 'speaker_b': 'Bo',
        'session_1_date_time': '1 January 2024',
        'session_1': [{'speaker': 'Ada', 'text': 'I live in Paris.'}]},
        'qa': [{'question': 'Where does Ada live?', 'answer': 'Paris', 'category': 4,
                'evidence': ['SYNTHETIC_PRIVATE_GOLD']},
               {'question': 'Where was Ada born?', 'adversarial_answer': 'Rome',
                'category': 5, 'evidence': []}]}


def test_selected_verifier_identity_tracks_publication_and_stays_private(monkeypatch, tmp_path):
    tid = 'locomo/conv-999'
    row = fixture_row()
    monkeypatch.setattr(locomo, 'rows', lambda: {tid: row})
    current = [fake_artifact(tmp_path, 'a')]
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: current[0])
    before = locomo.VERIFY[tid]
    assert before['argv'][:3] == [current[0]['python'], '-I', '-B']
    assert before['python_runtime']['role'] == 'verifier'
    assert 'SYNTHETIC_PRIVATE_GOLD' not in json.dumps(locomo.SETUP[tid])
    first = dataset_runtime_identity._bindings([tid], {}, {tid: before})[tid]['verifier']
    current[0] = fake_artifact(tmp_path, 'b')
    after = locomo.VERIFY[tid]
    second = dataset_runtime_identity._bindings([tid], {}, {tid: after})[tid]['verifier']
    assert first['runtime_id'] == 'a' * 64 and second['runtime_id'] == 'b' * 64
    assert before['python_runtime'] == first and after['python_runtime'] == second
    assert dataset_runtime_identity.problems(dataset_runtime_identity.create(tasks={tid: {'verifier': first}}),
                                             dataset_runtime_identity.create(tasks={tid: {'verifier': second}}))


def test_loader_and_report_do_not_import_nltk(tmp_path):
    # A truly cold platform interpreter: no system or user site packages.
    platform = tmp_path / 'platform'
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(platform)
    code = '''import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, name, path=None, target=None):
  if name == "nltk" or name.startswith("nltk."): raise AssertionError("platform imported nltk")
sys.meta_path.insert(0, Block()); sys.path.insert(0, sys.argv[1])
from data.datasets import locomo
report = locomo._load_asset("report.py")
assert report.aggregate_scores([{"score_sum": 1, "num_questions": 2}])["micro_avg_reward"] == .5
assert "nltk" not in sys.modules
'''
    env = {'HOME': str(tmp_path), 'PATH': os.defpath, 'HG_REVIEWED_ENVIRONMENT_ONLY': '1',
           'HG_DATA_LOCOMO': str(tmp_path / 'no-data')}
    result = subprocess.run([str(platform / 'bin/python'), '-I', '-B', '-c', code, str(ROOT)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_locked_runtime_declares_complete_dependency_closure():
    descriptor = dataset_runtime.execution_descriptor('locomo')
    assert descriptor['pins'] == {'nltk': '3.10.4', 'click': '8.3.3', 'defusedxml': '0.7.1',
                                  'joblib': '1.4.2', 'regex': '2023.10.3', 'tqdm': '4.66.4'}


def test_scoring_profile_distinguishes_curve_and_report_aggregation(monkeypatch):
    from harnessgrad import scoring_identity
    monkeypatch.setattr(locomo, 'rows', lambda: pytest.fail('profile must not load task data'))
    profile = scoring_identity.resolve('locomo')['profile']
    assert profile['metric'] == locomo.METRIC
    assert profile['platform_aggregation'] != profile['report_aggregation']
    assert 'data/datasets/locomo/lib/scoring.py' in profile['source_sha256']


def test_materialized_scorer_changes_invalidate_context(tmp_path, monkeypatch):
    import shutil
    from harnessgrad import verification_context
    assets = tmp_path / 'assets'
    for relative in ('lib/scoring.py', 'verify/check.py'):
        target = assets / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(locomo.ASSETS / relative, target)
    tid = 'locomo/conv-999'
    monkeypatch.setattr(locomo, 'rows', lambda: {tid: fixture_row()})
    monkeypatch.setattr(locomo, 'ASSETS', assets)
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda _: fake_artifact(tmp_path, 'a'))
    task = locomo.control_task(tid)
    def context():
        return verification_context.capture([task], {tid: locomo.METRIC},
            {tid: locomo.SETUP[tid]}, {tid: locomo.VERIFY[tid]})
    before = context()
    with (assets / 'lib/scoring.py').open('a') as stream:
        stream.write('\n# authored scorer revision\n')
    assert verification_context.problems(before, context())


@pytest.mark.skipif(not os.environ.get('HG_TEST_LOCOMO_RUNTIME_ROOT'),
                    reason='set HG_TEST_LOCOMO_RUNTIME_ROOT to an already prepared offline store')
def test_cold_platform_authored_control_uses_selected_runtime(tmp_path):
    platform = tmp_path / 'platform'
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(platform)
    runtime_root = os.environ['HG_TEST_LOCOMO_RUNTIME_ROOT']
    code = r'''import importlib.abc, json, shutil, subprocess, sys
from pathlib import Path
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, name, path=None, target=None):
  if name == "nltk" or name.startswith("nltk."): raise AssertionError("platform imported nltk")
sys.meta_path.insert(0, Block()); sys.path.insert(0, sys.argv[1])
from data.datasets import locomo
from harnessgrad import dataset_runtime_identity
from data.scaffolds.command_control import materialize, solve, run_check
row = {"sample_id":"conv-999", "conversation":{"speaker_a":"Ada","speaker_b":"Bo",
 "session_1_date_time":"1 January 2024","session_1":[{"speaker":"Ada","text":"I live in Paris."}]},
 "qa":[{"question":"Where does Ada live?","answer":"Paris","category":4,"evidence":["PRIVATE_SENTINEL"]},
       {"question":"Where was Ada born?","adversarial_answer":"Rome","category":5,"evidence":[]}]}
tid = "locomo/conv-999"; locomo.rows=lambda:{tid:row}
task = locomo.control_task(tid); work=Path(sys.argv[2]); work.mkdir()
materialize(locomo, task, work); solve(locomo, task, work)
assert "PRIVATE_SENTINEL" not in json.dumps([task, locomo.SETUP[tid]])
spec = locomo.VERIFY[tid]
assert spec["argv"][:3] == [spec["python_runtime"]["python"], "-I", "-B"]
identity = dataset_runtime_identity.for_evaluation(None, [task], {tid: locomo.SETUP[tid]}, {tid: spec})
assert identity is not None and tid in identity["tasks"]
assert dataset_runtime_identity.project_many(identity, [tid])[tid] == identity
for entry in spec["inputs"]:
 dest=work/entry["path"]; dest.parent.mkdir(parents=True,exist_ok=True)
 if "content" in entry: dest.write_text(entry["content"])
 else: shutil.copyfile(entry["from"],dest)
def check():
 return subprocess.run(spec["argv"],cwd=work,capture_output=True,text=True,timeout=30)
r=check(); assert r.returncode==0, r.stderr
assert float((work/"verifier/reward.txt").read_text())==1.0
assert json.loads((work/"verifier/evidence.json").read_text())["scorer_input"]["verified"]
reward, detail = run_check(locomo, task, work)
assert reward == 1.0, detail
(work/"answers.json").unlink(); r=check(); assert r.returncode==0
assert float((work/"verifier/reward.txt").read_text())==0.0
reward, detail = run_check(locomo, task, work)
assert reward == 0.0, detail
(work/"locomo_verify/gold.json").write_text("tampered"); r=check(); assert r.returncode==3
assert not (work/"verifier/reward.txt").exists()
for entry in spec["inputs"]:
 if entry["path"].endswith("gold.json"): (work/entry["path"]).write_text(entry["content"])
(work/"locomo_verify/scoring.py").write_text("# tampered scorer")
r=check(); assert r.returncode==3 and not (work/"verifier/reward.txt").exists()
assert "nltk" not in sys.modules
'''
    env = {'HOME': str(tmp_path), 'PATH': os.defpath, 'HG_REVIEWED_ENVIRONMENT_ONLY': '1',
           'HG_DATA_LOCOMO': str(tmp_path / 'no-data'), 'HG_DATASET_RUNTIME_ROOT': runtime_root}
    result = subprocess.run([str(platform / 'bin/python'), '-I', '-B', '-c', code, str(ROOT),
                             str(tmp_path / 'work')], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not os.environ.get('HG_TEST_LOCOMO_RUNTIME_ROOT'),
                    reason='set HG_TEST_LOCOMO_RUNTIME_ROOT to an already prepared offline store')
def test_unchecked_pyc_and_nltk_shadow_cannot_change_synthetic_score(tmp_path, monkeypatch):
    import importlib.util
    import py_compile
    import shutil
    tid = 'locomo/conv-999'
    row = fixture_row()
    monkeypatch.setattr(locomo, 'rows', lambda: {tid: row})
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', os.environ['HG_TEST_LOCOMO_RUNTIME_ROOT'])
    spec = locomo.VERIFY[tid]
    work = tmp_path / 'work'
    work.mkdir()
    for entry in spec['inputs']:
        target = work / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        if 'content' in entry:
            target.write_text(entry['content'])
        else:
            shutil.copyfile(entry['from'], target)
    scorer = work / 'locomo_verify/scoring.py'
    marker = work / 'malicious-pyc-ran'
    malicious = work / 'authored-malicious.py'
    malicious.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
                         "def score_answers(*args): return {'reward': 1.0}\n")
    bytecode = Path(importlib.util.cache_from_source(str(scorer)))
    bytecode.parent.mkdir()
    py_compile.compile(str(malicious), cfile=str(bytecode), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    shadow_marker = work / 'malicious-nltk-ran'
    (scorer.parent / 'nltk.py').write_text(
        f"from pathlib import Path\nPath({str(shadow_marker)!r}).write_text('executed')\n")
    (work / 'answers.json').write_text(json.dumps({'0': 'wrong answer', '1': '(wrong)'}))
    env = {'HOME': str(tmp_path), 'PATH': os.defpath, 'HG_REVIEWED_ENVIRONMENT_ONLY': '1'}
    # Positive controls prove both payloads are executable under the former unsafe path insertion.
    probe = subprocess.run([spec['argv'][0], '-I', '-B', '-c',
        'import sys;sys.path.insert(0,sys.argv[1]);import scoring', str(scorer.parent)],
        cwd=work, env=env, capture_output=True, text=True, timeout=30)
    assert probe.returncode == 0 and marker.is_file(), probe.stderr
    marker.unlink()
    probe = subprocess.run([spec['argv'][0], '-I', '-B', '-c',
        'import sys;sys.path.insert(0,sys.argv[1]);import nltk', str(scorer.parent)],
        cwd=work, env=env, capture_output=True, text=True, timeout=30)
    assert probe.returncode == 0 and shadow_marker.is_file(), probe.stderr
    shadow_marker.unlink()
    result = subprocess.run(spec['argv'], cwd=work, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert float((work / 'verifier/reward.txt').read_text()) == 0.0
    assert not marker.exists() and not shadow_marker.exists()
    evidence = json.loads((work / 'verifier/evidence.json').read_text())
    assert evidence['measured'] is True and evidence['scorer_input']['verified'] is True
    assert 'SYNTHETIC_PRIVATE_GOLD' not in json.dumps(evidence)
    (work / 'verifier/reward.txt').unlink()
    unisolated = [spec['argv'][0], *spec['argv'][2:]]
    result = subprocess.run(unisolated, cwd=work, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and 'isolated mode' in result.stderr
    assert not (work / 'verifier/reward.txt').exists()
