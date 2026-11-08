"""Managed source must round-trip through the existing file-only snapshots."""
import json
from unittest.mock import Mock

import pytest

from eval.ckpt.git_state import commit_state, init_repo, stage_workspace
from eval.ckpt.source_tree import copy_harness
from eval.run import runner
from harnessgrad import candidate_runtime, snapshot
from harnessgrad import python_harness_runtime as runtime
from tools import prepare_h0
from tools.validate_harness import check
from test_python_candidate_fingerprint import source, require_preparation


@pytest.mark.parametrize('directory,generated', [
    ('assets', None), ('nested/assets', None),
    ('assets', '.state/value.txt'), ('assets', '__pycache__/cache.pyc'),
    ('assets', 'ignored.pyc'), ('assets', 'venv/pyvenv.cfg'),
])
def test_empty_or_generated_only_source_is_refused_before_staging(source, monkeypatch, directory, generated):
    empty = source / directory
    empty.mkdir(parents=True)
    if generated:
        path = empty / generated
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'generated fixture')
    build = Mock(side_effect=AssertionError('invalid source reached dependency construction'))
    monkeypatch.setattr(runtime, '_build', build)
    with pytest.raises(ValueError, match='empty source directory'):
        runtime.source_fingerprint(source)
    assert any('empty source directory' in item['detail'] for item in check(source))
    with pytest.raises(ValueError, match='empty source directory'):
        runtime.prepare(source)
    destination = source.parent / 'old-workspace'
    destination.mkdir()
    (destination / 'sentinel').write_text('preserved')
    with pytest.raises(ValueError, match='empty source directory'):
        stage_workspace(source, destination)
    assert (destination / 'sentinel').read_text() == 'preserved'
    assert empty.is_dir() and not (source / '.git').exists()
    assert not (source.parent.parent / 'store').exists()
    build.assert_not_called()


@pytest.mark.parametrize('entry', ['prepare', 'identity', 'candidate-prepare', 'candidate-admit', 'h0'])
def test_direct_preparation_and_lookup_cannot_bypass_the_directory_rule(source, monkeypatch, entry):
    (source / 'assets').mkdir()
    build = Mock(side_effect=AssertionError('directory refusal reached a builder'))
    monkeypatch.setattr(runtime, '_build', build)
    actions = {'prepare': lambda: runtime.prepare(source),
               'identity': lambda: runtime.identity(source),
               'candidate-prepare': lambda: candidate_runtime.prepare(source),
               'candidate-admit': lambda: candidate_runtime.admit(source),
               'h0': lambda: prepare_h0.harness_context(source)}
    with pytest.raises(ValueError, match='empty source directory'):
        actions[entry]()
    build.assert_not_called()
    assert not (source.parent.parent / 'store').exists()


def test_platform_state_and_filtered_local_environments_are_not_source_directories(source):
    before = runtime.source_fingerprint(source)
    for name in ('.git', '.state', '_harnessgrad', '.opencode-state', '.venv',
                 'venv', 'node_modules', '__pycache__'):
        (source / name).mkdir()
    assert runtime.source_fingerprint(source) == before
    assert check(source) == []
    staged = source.parent / 'staged'
    stage_workspace(source, staged)
    assert (staged / '.state').is_dir()
    assert not (staged / '.venv').exists()
    require_preparation()
    assert candidate_runtime.prepare(staged)['identity']['version'] == 3


def test_unopted_source_copy_preserves_its_legacy_directory_behavior(source):
    path = source / 'harness.json'
    manifest = json.loads(path.read_text())
    manifest.pop('runtime')
    path.write_text(json.dumps(manifest))
    (source / 'assets').mkdir()
    copied = source.parent / 'legacy-copy'
    copy_harness(source, copied)
    assert (copied / 'assets').is_dir() and check(copied) == []
    assert candidate_runtime.prepare(copied) is None


def test_owned_directory_marker_survives_snapshot_and_removal_cannot_reuse_a_score(source, monkeypatch):
    require_preparation()
    (source / '.gitignore').write_text('assets/\n')
    assets = source / 'assets'
    assets.mkdir()
    (assets / '.keep').write_text('owned directory marker\n')
    (source / 'agent.py').write_text('''
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
answer='initial' if (Path(__file__).parent/'assets').is_dir() else 'changed'
(Path(a.workdir)/'answer.txt').write_text(answer)
(Path(a.workdir)/'trace.jsonl').write_text(json.dumps({'answer':answer})+'\\n')
''')
    init_repo(source)
    sha = commit_state(source, 'owned directory control')
    original = candidate_runtime.prepare(source)['identity']
    calls = Mock(wraps=runner.run_one)
    monkeypatch.setattr(runner, 'run_one', calls)
    cache = {}
    task = {'task_id': 'control'}

    def evaluate(binding):
        return runner._evaluate(source, [task], {'control': 'initial'}, cache=cache,
            harness_sha=sha, harness_runtime_identity=binding,
            envs={'control': {'kind': 'files', 'network': 'none'}},
            verifiers={'control': {'kind': 'answer', 'expected': 'initial'}})

    assert evaluate(original)['per_task'] == {'control': 1.0}
    assert evaluate(original)['cached_tasks'] == ['control']
    assert calls.call_count == 1
    run = source.parent.parent / 'retained-run'
    path = snapshot.write(run, 0, source, run_id='directory-control', harness_sha=sha)
    restored = source.parent / 'restored'
    snapshot.check_out(path, restored)
    assert (restored / 'assets/.keep').read_bytes() == (assets / '.keep').read_bytes()
    assert candidate_runtime.admit(restored, original) == original
    point = {'round': 0, 'identity': {'harness_sha': sha}, candidate_runtime.KEY: original}
    assert candidate_runtime.checkpoint_binding(run, point) == original
    metadata = {'side': 'train', candidate_runtime.KEY: original}
    assert candidate_runtime.validate_resume(run, metadata, [point], source) == {
        'initial': original, 'current': original}
    result = runner.run_one(restored, task, harness_runtime_identity=original,
        env={'kind': 'files', 'network': 'none'}, verifier={'kind': 'answer', 'expected': 'initial'})
    assert not result.get('invalid') and result['verdict']['passed']
    launches = calls.call_count

    (assets / '.keep').unlink()
    with pytest.raises(ValueError, match='empty source directory'):
        candidate_runtime.prepare(source)
    refused = evaluate(original)
    assert refused['invalid'] and not refused['per_task'] and not refused['cached_tasks']
    assert calls.call_count == launches
    assets.rmdir()
    assert commit_state(source, 'remove ignored source directory') == sha
    refused = evaluate(original)
    assert refused['invalid'] and not refused['cached_tasks'] and calls.call_count == launches
    prepared = candidate_runtime.prepare(source)
    current = prepared['identity']
    assert prepared['cache_hit'] and current['artifact'] == original['artifact'] and current != original
    changed = evaluate(current)
    assert not changed['invalid'] and not changed['cached_tasks']
    assert changed['per_task'] == {'control': 0.0} and calls.call_count == launches + 1
