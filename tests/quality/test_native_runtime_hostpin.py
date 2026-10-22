"""BIRD native pin regressions: authored SQL only, no dataset or model runs."""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eval.verification import verifier

MANIFEST = ROOT / 'data/runtimes/bird_minidev-native.json'


def binding():
    return {'dataset': 'bird_minidev', 'manifest': str(MANIFEST),
            'sha256': hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}


def spec():
    return {'kind': 'command', 'argv': ['python3', 'check.py'],
            'native_runtime': binding(), 'reward_file': '/verifier/reward.txt',
            'evidence_file': '/verifier/evidence.json'}


def test_mismatched_manifest_is_unmeasured_before_command(tmp_path):
    selected = spec()
    selected['native_runtime']['sha256'] = hashlib.sha256(b'foreign closure').hexdigest()
    got = verifier._verify(tmp_path, selected, '', sandbox=True, timeout_s=10)
    assert got.get('score') is None and got.get('unmeasured'), got
    assert got['native_runtime']['verified'] is False
    assert 'manifest digest mismatch' in got['unmeasured']


def test_pin_cannot_be_bypassed_by_no_sandbox(tmp_path):
    got = verifier._verify(tmp_path, spec(), '', sandbox=False, timeout_s=10)
    assert got.get('score') is None and 'sandbox' in got.get('unmeasured', ''), got


def test_changed_payload_is_refused(tmp_path):
    from eval.verification import native_runtime
    manifest = json.loads(MANIFEST.read_text())
    target = manifest['interpreter']
    wrong = tmp_path / 'wrong-python'
    wrong.write_bytes(b'wrong interpreter')
    manifest['files'][target]['source'] = str(wrong)
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    selected = {**binding(), 'manifest': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(native_runtime.NativeRuntimeUnavailable, match='digest'):
        with native_runtime.admit(selected):
            pytest.fail('modified interpreter admitted')


def test_admitted_bytes_are_sealed_and_survive_source_replacement(tmp_path):
    from eval.verification import native_runtime
    manifest = json.loads(MANIFEST.read_text())
    target = manifest['interpreter']
    source = tmp_path / 'python'
    source.write_bytes(Path(manifest['files'][target]['source']).read_bytes())
    manifest['files'][target]['source'] = str(source)
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    selected = {**binding(), 'manifest': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    with native_runtime.admit(selected) as runtime:
        source.write_bytes(b'replaced after admission')
        fd = runtime.fds[target]
        assert fcntl.fcntl(fd, native_runtime.F_GET_SEALS) & native_runtime.F_SEAL_WRITE
        with pytest.raises(OSError):
            import os
            os.pwrite(fd, b'X', 0)
        (tmp_path / 'verifier').mkdir()
        proc = runtime.run(['python3', '-c', 'import sys; print(sys.version)'],
                           workdir=tmp_path, reward_dir=tmp_path / 'verifier', timeout_s=20)
        assert proc.returncode == 0, proc.stderr
        assert manifest['identity']['version'] in proc.stdout


def test_namespace_closes_resolution_and_records_child_maps(tmp_path, monkeypatch):
    from eval.verification import native_runtime
    monkeypatch.setenv('LD_PRELOAD', '/task/evil.so')
    monkeypatch.setenv('LD_LIBRARY_PATH', '/task')
    monkeypatch.setenv('PYTHONPATH', '/task')
    (tmp_path / 'sqlite3.py').write_text('raise RuntimeError("actor import")')
    (tmp_path / 'verifier').mkdir()
    code = '''import json,os,pathlib,sqlite3,subprocess,sys
assert sys.flags.isolated and sys.flags.no_site
assert not pathlib.Path('/etc/ld.so.cache').exists()
assert not pathlib.Path('/usr/bin/sh').exists()
assert 'LD_PRELOAD' not in os.environ and 'PYTHONPATH' not in os.environ
try:
    sqlite3.connect(':memory:').execute("select load_extension('/task/evil.so')")
except sqlite3.OperationalError as exc:
    assert 'not authorized' in str(exc)
else:
    raise AssertionError('SQLite extensions enabled')
child = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
    "import sqlite3,pathlib; print(pathlib.Path('/proc/self/maps').read_text())"],
    capture_output=True, text=True, check=True)
print(json.dumps(dict(parent=pathlib.Path('/proc/self/maps').read_text(),child=child.stdout,
    executable=sys.executable,sqlite=sqlite3.sqlite_version)))
'''
    with native_runtime.admit(binding()) as runtime:
        proc = runtime.run(['python3', '-c', code], workdir=tmp_path,
                           reward_dir=tmp_path / 'verifier', timeout_s=30)
        assert proc.returncode == 0, proc.stderr
        record = json.loads(proc.stdout)
        for maps in (record['parent'], record['child']):
            loaded = {line.split(maxsplit=5)[5].removesuffix(' (deleted)')
                      for line in maps.splitlines() if len(line.split(maxsplit=5)) == 6
                      and line.split(maxsplit=5)[5].startswith('/')}
            assert runtime.manifest['interpreter'] in loaded
            assert any('libsqlite3' in name for name in loaded)
            assert loaded <= runtime.fds.keys(), loaded - runtime.fds.keys()
        print(json.dumps({'closure': runtime.receipt, **record}, sort_keys=True))


def test_actual_bound_bird_check_records_pin_and_preserves_ex_and_wal(tmp_path):
    from data.scaffolds.scorer_input import bind
    work = tmp_path / 'task'
    work.mkdir()
    database = tmp_path / 'source.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('create table numbers (n int)')
        db.executemany('insert into numbers values (?)', [(1,), (2,)])
    selected = {'kind': 'command', 'native_runtime': binding(),
                'argv': ['python3', 'check.py', '--db', 'database.sqlite', '--gold', 'gold.sql',
                         '--pred', 'answer.sql', '--reward-file', 'verifier/reward.txt',
                         '--evidence-file', 'verifier/evidence.json'],
                'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
                'inputs': [{'path': 'check.py', 'from': str(ROOT / 'data/datasets/bird_minidev/verify/check.py')},
                           {'path': 'database.sqlite', 'from': str(database)},
                           {'path': 'gold.sql', 'content': 'select n from numbers'}]}
    selected = bind(selected, 'authored-hostpin', ['database.sqlite', 'gold.sql'])
    for query, score in [('select n from numbers', 1.0), ('select n from numbers where n=1', 0.0),
                         ("select load_extension('/task/evil.so')", 0.0)]:
        (work / 'answer.sql').write_text(query)
        # An actor's shadow module must be invisible to trusted helper imports.
        (work / 'random.py').write_text('raise RuntimeError("actor shadow")')
        got = verifier._verify(work, selected, '', sandbox=True, timeout_s=30,
                               task_id='authored-hostpin', env={'kind': 'files', 'network': 'none'})
        assert got.get('score') == score and not got.get('unmeasured'), got
        assert got['native_runtime']['sha256'] == binding()['sha256']
        assert got['native_runtime']['verified'] is True
        assert got['evidence']['scorer_input']['verified'] is True
        print(json.dumps({'query_kind': 'authored SQL', 'verdict': got}, sort_keys=True))
    with sqlite3.connect(database) as db:
        db.execute('pragma journal_mode=wal')
    selected = bind({**selected, 'argv': selected['argv'][:12],
                     'inputs': selected['inputs'][:3]}, 'authored-hostpin', ['database.sqlite', 'gold.sql'])
    got = verifier._verify(work, selected, '', sandbox=True, timeout_s=30,
                           task_id='authored-hostpin', env={'kind': 'files', 'network': 'none'})
    assert got.get('unmeasured') and not (work / 'verifier/reward.txt').exists(), got


def test_missing_payload_cannot_fall_back_to_host(tmp_path):
    manifest = json.loads(MANIFEST.read_text())
    manifest['files'][manifest['interpreter']]['source'] = str(tmp_path / 'missing')
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    selected = spec()
    selected['native_runtime'] = {**binding(), 'manifest': str(path),
                                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    got = verifier._verify(tmp_path, selected, '', sandbox=True, timeout_s=10)
    assert got.get('unmeasured') and got.get('score') is None
    assert got['native_runtime']['verified'] is False


def test_adapter_declares_independently_fixed_runtime_without_reading_dataset(tmp_path, monkeypatch):
    import data.datasets.bird_minidev as adapter
    dbroot = tmp_path / 'dbs'
    (dbroot / 'authored').mkdir(parents=True)
    (dbroot / 'authored/authored.sqlite').write_bytes(b'authored input pin only')
    monkeypatch.setattr(adapter, 'DBS', dbroot)
    selected = adapter._verify({'task_id': 'authored', 'db_id': 'authored', 'gold_sql': 'select 1'})
    assert selected['native_runtime']['sha256'] == binding()['sha256']
    assert selected['native_runtime']['dataset'] == 'bird_minidev'


def test_saved_verdict_keeps_native_identity_for_every_trial(tmp_path):
    from harnessgrad.records import _save_verdicts
    receipt = {'verified': True, 'sha256': binding()['sha256'], 'dataset': 'bird_minidev'}
    result = {'per_task': {'authored': 1.0},
              'verdicts': {'authored': {'passed': True, 'native_runtime': receipt}},
              'trial_verdicts': {'authored': [{'passed': True, 'native_runtime': receipt}]}}
    _save_verdicts(tmp_path, 0, result, ['authored'])
    saved = json.loads((tmp_path / 'verdicts/round-0/authored.json').read_text())
    assert saved['native_runtime'] == receipt
    assert saved['trials'][0]['native_runtime'] == receipt
