"""Final mount admission protects archives, including extra runtimes and aliases."""
import json
from pathlib import Path
import tempfile

import pytest

from eval.containers import private_mounts, sandbox
from eval.run import runner
from test_declared_trials_continue import PROTOCOL
from test_shared_dataset_runtime import harness


@pytest.mark.parametrize('plan,visible', [
    (['--ro-bind', '/private', '/runtime'], True),
    (['--ro-bind', '/', '/host'], True),
    (['--ro-bind', '/private/archive/one.json', '/data/input.json'], True),
    (['--ro-bind', '/private', '/runtime', '--tmpfs', '/runtime'], False),
    (['--ro-bind', '/private', '/runtime', '--tmpfs', '/runtime/archive/subdir'], True),
    (['--ro-bind', '/private', '/runtime', '--tmpfs', '/runtime',
      '--ro-bind', '/private/archive', '/elsewhere'], True),
    (['--ro-bind', '/private', '/private', '--tmpfs', '/private',
      '--ro-bind', '/public', '/private/public'], False),
    (['--ro-bind', '/private/archive', '/alias', '--tmpfs', '/private'], True),
])
def test_ordered_mounts_and_source_destinations(plan, visible):
    if visible:
        with pytest.raises(ValueError, match='trial_evidence.*visible'):
            private_mounts.reject_visible(plan, ['/private/archive'])
    else:
        private_mounts.reject_visible(plan, ['/private/archive'])


def test_symlink_source_cannot_alias_archive(tmp_path):
    root = tmp_path/'private'
    root.mkdir()
    alias = tmp_path/'runtime'
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='visible'):
        private_mounts.reject_visible(['--ro-bind', str(alias), '/lib/runtime'], [root/'trial-evidence'])


@pytest.fixture
def host_storage():
    # /tmp is hidden independently by the native sandbox; use another SSD tree
    # to reproduce a genuinely visible runtime bind, as in the reported defect.
    with tempfile.TemporaryDirectory(prefix='.hg-evidence-visibility-', dir=Path.home()) as root:
        yield Path(root)


@pytest.mark.parametrize('mount_kind', ['runtime', 'immutable_projection'])
def test_evaluate_refuses_extra_runtime_before_actor_launch(tmp_path, monkeypatch, host_storage, mount_kind):
    work = tmp_path/'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    repo = harness(work, "raise AssertionError('must never launch')")
    progress = host_storage/'run'
    private = progress/'trial-evidence'
    private.mkdir(parents=True)
    (private/'private.txt').write_text('synthetic private evidence')
    setup = None
    if mount_kind == 'runtime':
        (repo/'harness.json').write_text(json.dumps({'entrypoint': 'agent.py', 'runtime_paths': [str(host_storage)]}))
    else:
        setup = {'files': [{'path': 'public_alias', 'from': str(private), 'immutable': True}]}
    spec = {'kind': 'answer', 'expected': '', 'trial_protocol': PROTOCOL, 'trial_evidence': {'version': 1}}
    with pytest.raises(ValueError, match='trial_evidence.*visible'):
        runner.evaluate(repo, [{'task_id': 'fixture/t'}], {}, verifiers={'fixture/t': spec},
            setups={'fixture/t': setup}, trials=3, run_id='fixture', harness_sha='fixture', progress_dir=progress)
    assert not list((progress/'trial-evidence').rglob('*.json'))


def test_hidden_storage_allows_all_trials_and_cannot_be_read(tmp_path, monkeypatch, host_storage):
    work = tmp_path/'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    progress = host_storage/'run'
    repo = harness(work, f"(w/'trace.jsonl').write_text(str(Path({str(progress)!r}).exists()))")
    spec = {'kind': 'answer', 'expected': '', 'trial_protocol': PROTOCOL, 'trial_evidence': {'version': 1}}
    result = runner.evaluate(repo, [{'task_id': 'fixture/t'}], {}, verifiers={'fixture/t': spec},
        trials=3, run_id='fixture', harness_sha='fixture', progress_dir=progress)
    assert result['trial_traces']['fixture/t'] == ['False'] * 3
    assert len(list((progress/'trial-evidence').rglob('*.json'))) == 3


def test_default_sandbox_mounts_unchanged_without_private_declaration(tmp_path, host_storage):
    secret = host_storage/'trial-evidence/value'
    secret.parent.mkdir()
    secret.write_text('synthetic-private-fixture')
    work, task = tmp_path/'work', tmp_path/'task'
    work.mkdir(); task.mkdir()
    kwargs = dict(platform=Path(__file__).resolve().parents[2], work_root=work, repo=work,
                  task_dir=task, env={}, runtime_paths=(host_storage,), timeout_s=10)
    command = ['/usr/bin/python3', '-c', f'from pathlib import Path; print(Path({str(secret)!r}).read_text())']
    legacy = sandbox.run(command, **kwargs)
    assert legacy.returncode == 0 and legacy.stdout.strip() == 'synthetic-private-fixture'
    with pytest.raises(ValueError, match='trial_evidence.*visible'):
        sandbox.run(command, **kwargs, private_paths=(secret.parent,))
