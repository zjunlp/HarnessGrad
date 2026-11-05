"""H0 tools bind the staged candidate source and the actual actor artifact."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eval.ckpt.git_state import stage_workspace
from eval.ckpt.source_tree import copy_harness
from harnessgrad import candidate_runtime as candidate
from harnessgrad import python_harness_runtime as runtime
from harnessgrad import snapshot
from tools import prepare_h0
from test_prepare_h0 import _write_run

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def managed(tmp_path, monkeypatch):
    if shutil.which('uv') is None or shutil.which('bwrap') is None:
        pytest.skip('managed H0 controls need uv and bubblewrap')
    monkeypatch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('HG_AGENT_MODEL', 'h0-candidate-control')
    monkeypatch.setenv('HG_AGENT_BASE_URL', '')
    monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT', str(tmp_path / 'store'))
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.setenv('UV_OFFLINE', '1')
    source = tmp_path / 'source'
    shutil.copytree(ROOT / 'tests/fixtures/base_harness/loop_plain', source)
    manifest = json.loads((source / 'harness.json').read_text())
    manifest.update(runtime={'kind': 'python', 'python': f'{sys.version_info.major}.{sys.version_info.minor}',
                             'requirements': 'requirements.lock'})
    (source / 'harness.json').write_text(json.dumps(manifest))
    (source / 'requirements.lock').write_text('# empty dependency closure\n')
    staged = tmp_path / 'staged'
    stage_workspace(source, staged)
    receipt = candidate.prepare(staged)
    args = SimpleNamespace(dataset='demo', side='train', study_ids=None,
        model='h0-candidate-control', endpoint='', trials=1, sampling='all', harness_path=source)
    return SimpleNamespace(root=tmp_path, source=source, staged=staged, receipt=receipt, args=args)


def make_h0(control):
    config = prepare_h0.configuration(control.args, study_ids=['t01'])
    run = _write_run(control.root, 'h0-demo-control', per_task={'t01': .5}, config=config)
    copy_harness(control.staged, run / 'state_after_round0')
    for filename in ('h0.json', 'run_meta.json', 'curve.jsonl'):
        data = json.loads((run / filename).read_text())
        data.update(candidate.fields(config[candidate.KEY]))
        if filename == 'curve.jsonl':
            data['round'] = 0
        if filename == 'h0.json':
            data['base'].update(candidate.fields(config[candidate.KEY]))
        (run / filename).write_text(json.dumps(data))
    return config, run


def test_configuration_binds_staged_source_and_does_not_build_again(managed, monkeypatch):
    first = prepare_h0.configuration(managed.args, study_ids=['t01'])
    assert first[candidate.KEY] == managed.receipt['identity']
    original = runtime._build
    monkeypatch.setattr(runtime, '_build', lambda *a, **k: pytest.fail('H0 discovery installed dependencies'))
    assert prepare_h0.configuration(managed.args, study_ids=['t01']) == first
    monkeypatch.setattr(runtime, '_build', original)
    (managed.source / 'helper.py').write_text('VALUE = 1\n')
    changed = prepare_h0.configuration(managed.args, study_ids=['t01'])
    assert changed['harness_sha'] != first['harness_sha']
    assert changed[candidate.KEY]['source_sha256'] != first[candidate.KEY]['source_sha256']
    assert changed[candidate.KEY]['artifact'] == first[candidate.KEY]['artifact']


@pytest.mark.parametrize('location', ['configuration', 'payload', 'base', 'metadata', 'point'])
@pytest.mark.parametrize('damage', ['missing', 'malformed'])
def test_h0_requires_consistent_candidate_binding_in_every_copy(managed, location, damage):
    config, run = make_h0(managed)
    assert prepare_h0.find_existing(config, managed.root)[0] == run
    filenames = {'configuration': 'run_meta.json', 'payload': 'h0.json', 'base': 'h0.json',
                 'metadata': 'run_meta.json', 'point': 'curve.jsonl'}
    target = run / filenames[location]
    body = json.loads(target.read_text())
    record = body['h0_configuration'] if location == 'configuration' else body['base'] if location == 'base' else body
    if damage == 'missing':
        record.pop(candidate.KEY)
    else:
        record[candidate.KEY]['sha256'] = '0' * 64
    target.write_text(json.dumps(body))
    before = {path: path.read_bytes() for path in run.rglob('*') if path.is_file()}
    assert prepare_h0.find_existing(config, managed.root) is None
    with pytest.raises(SystemExit, match='harness_runtime_identity'):
        prepare_h0.verify(run, None, 1, configuration=config)
    assert all(path.read_bytes() == data for path, data in before.items())


def test_h0_checks_live_actor_bytes_even_when_it_can_reuse_scores(managed):
    config, run = make_h0(managed)
    assert prepare_h0.find_existing(config, managed.root)
    path = Path(config[candidate.KEY]['artifact']['path']) / 'pyvenv.cfg'
    original, mode = path.read_bytes(), path.stat().st_mode
    try:
        path.chmod(mode | 0o200)
        path.write_bytes(original + b'\n# corrupted dependency artifact\n')
        assert prepare_h0.find_existing(config, managed.root) is None
    finally:
        path.write_bytes(original)
        path.chmod(mode)


@pytest.mark.parametrize('version', [1, 2])
def test_obsolete_measurements_are_not_upgraded_for_h0_reuse_or_resume(managed, version):
    config, run = make_h0(managed)
    legacy = copy.deepcopy(config[candidate.KEY])
    legacy['version'] = version
    legacy['sha256'] = hashlib.sha256(runtime.canonical({
        key: value for key, value in legacy.items() if key != 'sha256'})).hexdigest()
    old_config = {**config, candidate.KEY: legacy}
    for filename in ('h0.json', 'run_meta.json', 'curve.jsonl'):
        data = json.loads((run / filename).read_text())
        data[candidate.KEY] = legacy
        if filename == 'h0.json':
            data['base'][candidate.KEY] = legacy
        elif filename == 'run_meta.json':
            data['h0_configuration'] = old_config
        (run / filename).write_text(json.dumps(data))
    before = {path: path.read_bytes() for path in run.rglob('*') if path.is_file()}
    assert prepare_h0.find_existing(old_config, managed.root) is None
    assert prepare_h0.find_existing(config, managed.root) is None
    with pytest.raises(SystemExit, match='binding version'):
        prepare_h0.verify(run, None, 1, configuration=old_config)
    point = json.loads((run / 'curve.jsonl').read_text())
    metadata = json.loads((run / 'run_meta.json').read_text())
    with pytest.raises(ValueError, match='binding version'):
        candidate.validate_resume(run, metadata, [point], managed.staged)
    assert all(path.read_bytes() == content for path, content in before.items())


def test_checkpoint_admits_portable_source_and_rejects_damaged_snapshot(managed):
    config, run = make_h0(managed)
    point = json.loads((run / 'curve.jsonl').read_text())
    artifact = snapshot.write(run, 0, managed.staged, run_id=run.name, harness_sha=config['harness_sha'])
    shutil.rmtree(run / 'state_after_round0')
    assert candidate.checkpoint_binding(run, point) == config[candidate.KEY]
    stored = json.loads(artifact.read_text())
    stored['files']['agent.py']['content_b64'] = 'damaged'
    artifact.write_text(json.dumps(stored))
    with pytest.raises(ValueError, match='snapshot'):
        candidate.checkpoint_binding(run, point)


def test_preparation_receipts_are_append_only_and_never_partially_replace_an_attempt(tmp_path):
    first = candidate.record_preparation(tmp_path, 0, {'status': 'invalid', 'seconds': 1})
    before = first.read_bytes()
    second = candidate.record_preparation(tmp_path, 0, {'status': 'ready', 'seconds': 2})
    assert first != second and first.read_bytes() == before
    assert json.loads(second.read_text())['receipt']['status'] == 'ready'


def test_h0_staging_refuses_source_aliases_before_writing_external_files(managed):
    canary = managed.root / 'external-gitignore'
    canary.write_text('owned outside the candidate\n')
    (managed.source / '.gitignore').unlink(missing_ok=True)
    (managed.source / '.gitignore').symlink_to(canary)
    with pytest.raises(ValueError, match='owned regular files'):
        prepare_h0.configuration(managed.args, study_ids=['t01'])
    assert canary.read_text() == 'owned outside the candidate\n'


def test_h0_tool_does_not_reuse_another_harnesss_baseline(tmp_path):
    args = SimpleNamespace(dataset='demo', side='train', study_ids=None,
        model='agent', endpoint='', trials=1, sampling='all')
    config = prepare_h0.configuration(args, env={'HG_AGENT_BACKEND': 'mock'})
    run = _write_run(tmp_path, 'h0-demo-other-source', per_task={'t01': .5}, config=config, sha='another-tree')
    assert prepare_h0.find_existing(config, tmp_path) is None
    with pytest.raises(SystemExit, match='harness source'):
        prepare_h0.verify(run, None, 1, configuration=config)


def test_legacy_h0_without_new_source_key_can_use_its_actual_tree_proof(tmp_path):
    args = SimpleNamespace(dataset='demo', side='train', study_ids=None,
        model='agent', endpoint='', trials=1, sampling='all')
    config = prepare_h0.configuration(args, env={'HG_AGENT_BACKEND': 'mock'})
    run = _write_run(tmp_path, 'h0-demo-legacy-source', per_task={'t01': .5}, config=config)
    metadata = json.loads((run / 'run_meta.json').read_text())
    metadata['h0_configuration'].pop('harness_sha')
    (run / 'run_meta.json').write_text(json.dumps(metadata))
    before = (run / 'run_meta.json').read_bytes()
    assert prepare_h0.find_existing(config, tmp_path)[0] == run
    assert (run / 'run_meta.json').read_bytes() == before


def test_real_h0_cli_binds_external_candidate_and_reuses_it_without_remeasurement(managed):
    ids = managed.root / 'ids.txt'
    ids.write_text('t01\n')
    runs = managed.root / 'cli-runs'
    command = [sys.executable, '-B', str(ROOT / 'tools/prepare_h0.py'), '--dataset', 'demo',
        '--harness-path', str(managed.source), '--study-ids', str(ids), '--jobs', '1',
        '--model', 'h0-candidate-control', '--endpoint', '', '--runs-root', str(runs)]
    first = subprocess.run(command, cwd=ROOT, env=dict(os.environ), capture_output=True, text=True, timeout=120)
    assert first.returncode == 0, first.stdout + first.stderr
    run = next(path for path in runs.glob('h0-demo-*') if path.is_dir())
    payload = json.loads((run / 'h0.json').read_text())
    assert payload[candidate.KEY] == managed.receipt['identity']
    before = (run / 'h0.json').read_bytes(), (run / 'curve.jsonl').read_bytes()
    second = subprocess.run(command, cwd=ROOT, env=dict(os.environ), capture_output=True, text=True, timeout=120)
    assert second.returncode == 0 and 'reusing' in second.stdout, second.stdout + second.stderr
    assert ((run / 'h0.json').read_bytes(), (run / 'curve.jsonl').read_bytes()) == before
