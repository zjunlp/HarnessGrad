"""Offline namespace reservation checks; all files are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json

import pytest

from harnessgrad import run_namespace as ns


@pytest.fixture
def args(tmp_path):
    return SimpleNamespace(runs_root=tmp_path / 'runs', work_root=tmp_path / 'work')


def test_existing_partial_namespaces_are_never_modified(args):
    for root in (args.runs_root, args.work_root):
        path = root / 'same'
        path.mkdir(parents=True)
        (path / 'evidence').write_bytes(b'original\x00')
        with pytest.raises(ns.NamespaceRefused):
            ns.claim(args, 'same', 'config')
        assert (path / 'evidence').read_bytes() == b'original\x00'
        (path / 'evidence').unlink()
        path.rmdir()


def test_prepared_claim_requires_exact_configuration_and_unchanged_record(args):
    run = ns.reserve(args, 'same', prepare=True)
    (run / 'run_meta.json').write_text('{"launched": false}')
    ns.finish_preparation(run, 'config')
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    with pytest.raises(ns.NamespaceRefused):
        ns.claim(args, 'same', 'different')
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    assert ns.claim(args, 'same', 'config') == run
    with pytest.raises(ns.NamespaceRefused):
        ns.claim(args, 'same', 'config')


def test_extra_or_altered_prepared_evidence_is_refused(args):
    run = ns.reserve(args, 'same', prepare=True)
    meta = run / 'run_meta.json'
    meta.write_text('{}')
    ns.finish_preparation(run, 'config')
    meta.write_text('{"changed": true}')
    with pytest.raises(ns.NamespaceRefused):
        ns.claim(args, 'same', 'config')
    assert json.loads(meta.read_text()) == {'changed': True}


def test_concurrent_reservations_have_one_winner(args):
    def attempt(_):
        try:
            ns.claim(args, 'same', 'config')
            return True
        except ns.NamespaceRefused:
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(attempt, range(24))) == 1


def test_shared_work_root_blocks_other_run_root(args, tmp_path):
    ns.reserve(args, 'same')
    other = SimpleNamespace(runs_root=tmp_path / 'other', work_root=args.work_root)
    with pytest.raises(ns.NamespaceRefused):
        ns.claim(other, 'same', 'config')
    assert not (other.runs_root / 'same').exists()


def test_driver_refuses_existing_namespace_before_checks_or_execution(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    run = tmp_path / 'runs' / 'existing'
    run.mkdir(parents=True)
    evidence = run / 'evidence'
    evidence.write_bytes(b'keep exactly\x00')
    base = [sys.executable, str(repo / 'driver.py'), '--dataset', 'demo', '--harness', 'loop',
            '--run-id', 'existing', '--sampling', 'all', '--runs-root', str(tmp_path / 'runs'),
            '--work-root', str(tmp_path / 'work')]
    for extra in ([], ['--check-only'], ['--prepare-only']):
        result = subprocess.run(base + extra, cwd=repo, capture_output=True, text=True)
        assert result.returncode == 2, result.stderr
        assert 'fresh --run-id' in result.stderr
        assert evidence.read_bytes() == b'keep exactly\x00'
        assert sorted(p.name for p in run.iterdir()) == ['evidence']


def test_concurrent_prepared_claims_have_one_winner(args):
    run = ns.reserve(args, 'same', prepare=True)
    (run / 'run_meta.json').write_text('{}')
    ns.finish_preparation(run, 'config')
    def attempt(_):
        try:
            ns.claim(args, 'same', 'config')
            return True
        except ns.NamespaceRefused:
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(attempt, range(24))) == 1


def test_prepared_driver_binds_effective_environment_without_storing_keys(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    base = [sys.executable, str(repo / 'driver.py'), '--dataset', 'demo', '--harness', 'loop',
            '--run-id', 'prepared', '--sampling', 'all', '--runs-root', str(tmp_path / 'runs'),
            '--work-root', str(tmp_path / 'work')]
    env = {**os.environ, 'HG_AGENT_MODEL': 'synthetic-model-a',
           'HG_AGENT_BACKEND': 'mock', 'HG_AGENT_API_KEY': 'synthetic-secret-one',
           'HG_METHOD_MODEL': 'synthetic-method-a'}
    result = subprocess.run(base + ['--prepare-only'], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    run = tmp_path / 'runs' / 'prepared'
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    assert all(b'synthetic-secret-one' not in data for data in before.values())
    for changed in ({'HG_AGENT_MODEL': 'synthetic-model-b'},
                    {'HG_METHOD_MODEL': 'synthetic-method-b'},
                    {'HG_AGENT_BASE_URL': 'https://synthetic.invalid'},
                    {'HG_ENV_MODEL': 'synthetic-env-b'},
                    {'HG_JUDGE_MODEL': 'synthetic-judge-b'}):
        for extra in ([], ['--check-only']):
            result = subprocess.run(base + extra, cwd=repo, env={**env, **changed},
                                    capture_output=True, text=True)
            assert result.returncode == 2, result.stderr
            assert 'preparation differs' in result.stderr
            assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    # Credential values are intentionally not recorded or compared; only presence is.
    from harnessgrad import config
    first = config.dump(config.load(env))
    rotated = config.dump(config.load({**env, 'HG_AGENT_API_KEY': 'synthetic-secret-two'}))
    assert first == rotated


def test_preparation_binds_untyped_runtime_environment(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    base = [sys.executable, str(repo / 'driver.py'), '--dataset', 'demo', '--harness', 'loop',
            '--run-id', 'environment', '--sampling', 'all', '--runs-root', str(tmp_path / 'runs'),
            '--work-root', str(tmp_path / 'work')]
    env = {**os.environ, 'HG_REVIEWED_ENVIRONMENT_ONLY': '1', 'HG_AGENT_BACKEND': 'mock',
           'HG_AGENT_API_KEY': 'synthetic-key-never-written', 'HG_DATA_GSM8K': str(tmp_path / 'data-a'),
           'HG_DATASET_RUNTIME_ROOT': str(tmp_path / 'dataset-runtime-a'),
           'HG_HARNESS_RUNTIME_ROOT': str(tmp_path / 'harness-runtime-a'),
           'HG_APPWORLD_POOL': 'train'}
    result = subprocess.run(base + ['--prepare-only'], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    run = tmp_path / 'runs' / 'environment'
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    assert all(b'synthetic-key-never-written' not in value for value in before.values())
    for key, value in [('HG_DATA_GSM8K', str(tmp_path / 'data-b')),
                       ('HG_DATASET_RUNTIME_ROOT', str(tmp_path / 'dataset-runtime-b')),
                       ('HG_HARNESS_RUNTIME_ROOT', str(tmp_path / 'harness-runtime-b')),
                       ('HG_APPWORLD_POOL', 'test')]:
        for extra in ([], ['--check-only']):
            result = subprocess.run(base + extra, cwd=repo, env={**env, key: value},
                                    capture_output=True, text=True)
            assert result.returncode == 2, result.stderr
            assert 'preparation differs' in result.stderr
            assert {p.name: p.read_bytes() for p in run.iterdir()} == before


def test_hg_snapshot_redacts_secrets_and_ignores_console_environment():
    from harnessgrad.run_namespace import effective_hg_environment
    a = effective_hg_environment({'HG_AGENT_API_KEY': 'sensitive-a',
        'HG_JUDGE_TOKEN': 'secret-a', 'HG_CONSOLE_TOKEN': 'a', 'HG_APPWORLD_POOL': 'test',
        'HG_JUDGE_MAX_OUTPUT_TOKENS': '42'})
    b = effective_hg_environment({'HG_AGENT_API_KEY': 'sensitive-b',
        'HG_JUDGE_TOKEN': 'secret-b', 'HG_CONSOLE_TOKEN': 'b', 'HG_APPWORLD_POOL': 'test',
        'HG_JUDGE_MAX_OUTPUT_TOKENS': '42'})
    assert a == b
    assert a['HG_AGENT_API_KEY'] == {'present': True}
    assert a['HG_JUDGE_MAX_OUTPUT_TOKENS'] == '42'
    assert 'HG_CONSOLE_TOKEN' not in a
    assert 'sensitive' not in repr(a) and 'secret-a' not in repr(a)
