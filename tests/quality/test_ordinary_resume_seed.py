"""The real driver records the same seed that resumed ordinary trials execute."""
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

import driver
from eval.run import repeated_trials
from harnessgrad import config, candidate_runtime, python_harness_runtime, verification_context


@pytest.fixture
def interrupted_driver(tmp_path, monkeypatch, request):
    ids = ['costbench/test/Query00001', 'costbench/test/Query00002']
    module = ModuleType('data.demo')
    module.KIND = 'fixture'
    module.ORACLE_SOURCE = 'released'
    tasks = [{'task_id': tid, 'goal': 'Write ok to answer.txt.'} for tid in ['train', *ids]]
    module.load = lambda: (tasks, {t['task_id']: 'ok' for t in tasks})
    module.SPLIT = {'train': ['train'], 'eval': ids}
    module.SETUP = {t['task_id']: {'files': []} for t in tasks}
    module.VERIFY = {t['task_id']: {'kind': 'answer', 'expected': 'ok',
                                   'trial_evidence': {'version': 1}} for t in tasks}
    module.ENV = {t['task_id']: {'kind': 'files', 'network': 'none'} for t in tasks}
    monkeypatch.setitem(sys.modules, 'data.demo', module)
    source = tmp_path / 'seed-source'
    source.mkdir(parents=True)
    (source / 'harness.json').write_text(json.dumps({
        'name': 'seed-control', 'version': '1', 'entrypoint': 'agent.py',
        'runtime': {'kind': 'python', 'python': f'{sys.version_info.major}.{sys.version_info.minor}',
                    'requirements': 'requirements.lock'}}))
    (source / 'requirements.lock').write_text('# authored stdlib actor: no third-party dependencies\n')
    (source / 'agent.py').write_text(
        "import argparse,json,sys\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()\n"
        "Path(a.workdir,'answer.txt').write_text('ok')\n"
        "task=json.loads(Path(a.task).read_text())\n"
        f"if task['task_id'].endswith('Query00001'): sys.exit({getattr(request, 'param', 0)})\n")
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    monkeypatch.setattr(driver, 'load_env', lambda *_: {})
    monkeypatch.setattr(driver, '_probe_tool_calls', lambda *a, **k: pytest.fail('no model calls'))
    # A bare host runtime_paths declaration is deliberately noncacheable. Use
    # an actual admitted offline Python artifact and the real train publication
    # path, rather than inventing a checkpoint identity for resume.
    store = tmp_path / 'runtime-store'
    monkeypatch.setenv('HG_HARNESS_RUNTIME_ROOT', str(store))
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'wheel-cache'))
    monkeypatch.setenv('UV_OFFLINE', '1')
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', 'never')
    request.addfinalizer(lambda: python_harness_runtime.remove_instance(store) if store.exists() else None)
    original_harness = driver._harness_by_name
    monkeypatch.setattr(driver, '_harness_by_name', lambda name:
                        source if name == 'seed-control' else original_harness(name))
    monkeypatch.setattr(sys, 'argv', ['driver.py', '--harness', 'seed-control',
        '--dataset', 'demo', '--sampling', 'all', '--rounds', '0', '--improver', 'none',
        '--trials', '1', '--run-id', 'train', '--seed', 'authored-train-seed',
        '--runs-root', str(tmp_path / 'runs'), '--work-root', str(tmp_path / 'work'),
        '--gpu-budget-dir', str(tmp_path / 'slots')])
    config.reset()
    assert driver.main() == 0
    train_meta = json.loads((tmp_path / 'runs/train/run_meta.json').read_text())
    binding = train_meta['harness_runtime_identity']
    assert candidate_runtime.admit(tmp_path / 'runs/train/state_after_round0', binding) == binding
    state = SimpleNamespace(run=tmp_path / 'runs/eval', interrupt=True, started=[], binding=binding)
    start_task = repeated_trials.start_task

    def start(root, plan):
        tid = plan['manifest'][0]['task_id']
        if state.interrupt and tid == ids[1]:
            raise KeyboardInterrupt('between completed and unstarted tasks')
        state.started.append(tid)
        return start_task(root, plan)

    monkeypatch.setattr(repeated_trials, 'start_task', start)
    argv = ['driver.py', '--dataset', 'demo', '--side', 'eval', '--from-run', 'train',
            '--sampling', 'all', '--rounds', '0', '--improver', 'none', '--trials', '1',
            '--run-id', 'eval', '--runs-root', str(tmp_path / 'runs'),
            '--work-root', str(tmp_path / 'work'), '--gpu-budget-dir', str(tmp_path / 'slots')]

    def execute(*extra):
        monkeypatch.setattr(sys, 'argv', [*argv, *extra])
        config.reset()
        return driver.main()

    state.execute = execute
    with pytest.raises(KeyboardInterrupt, match='between completed'):
        execute()
    state.interrupt = False
    state.metadata = (state.run / 'run_meta.json').read_bytes()
    current = json.loads(state.metadata)
    state.seed = current['config']['run']['seed']
    assert current['harness_runtime_identity'] == state.binding
    progress = json.loads((state.run / 'progress/round-0' / (ids[0] + '.json')).read_text())
    assert progress['harness_runtime_identity'] == state.binding
    assert progress['verification_context'] == verification_context.project(
        current['verification_context'], [ids[0]])
    state.saved = {p: p.read_bytes() for p in (state.run / 'trial-evidence').rglob('*.json')}
    assert state.started == [ids[0]]
    yield state
    config.reset()


@pytest.mark.parametrize('requested', [None, 'same', 'different'])
def test_driver_resolves_resume_seed_before_writing_metadata(interrupted_driver, requested):
    state = interrupted_driver
    extra = [] if requested is None else ['--seed', state.seed if requested == 'same' else 'different-seed']
    result = state.execute('--resume', *extra)
    metadata = (state.run / 'run_meta.json').read_bytes()
    assert json.loads(metadata)['config']['run']['seed'] == state.seed
    if requested == 'different':
        assert result == 2
        assert metadata == state.metadata
        assert len(state.started) == 1
    else:
        assert result == 0
        assert state.started == ['costbench/test/Query00001', 'costbench/test/Query00002']
        assert os.environ['HARNESSGRAD_SEED'] == state.seed
        for path in (state.run / 'trial-evidence').rglob('*.json'):
            archive = json.loads(path.read_text())
            if archive.get('schema') == 'harnessgrad.trial-evidence.v1':
                assert archive['binding']['seed'] == state.seed
        plan = json.loads((state.run / 'trial-evidence/ordinary/round-0/resume-plan.json').read_text())
        assert plan['configuration']['run_seed'] == state.seed
    assert all(path.read_bytes() == body for path, body in state.saved.items())


@pytest.mark.parametrize('interrupted_driver', [1], indirect=True)
def test_driver_resume_keeps_an_already_scored_artifact_after_actor_failure(interrupted_driver):
    state = interrupted_driver
    assert state.execute('--resume') == 0
    points = [json.loads(line) for line in (state.run / 'curve.jsonl').read_text().splitlines()]
    assert points[-1]['per_task'] == {'costbench/test/Query00001': 1, 'costbench/test/Query00002': 1}
    assert all(path.read_bytes() == body for path, body in state.saved.items())


@pytest.mark.parametrize('damage', ['missing-plan', 'invalid-seed'])
def test_invalid_resume_seed_plan_cannot_rewrite_metadata(interrupted_driver, damage):
    state = interrupted_driver
    path = state.run / 'trial-evidence/ordinary/round-0/resume-plan.json'
    if damage == 'missing-plan':
        path.unlink()
    else:
        plan = json.loads(path.read_text())
        plan['configuration']['run_seed'] = None
        path.write_text(json.dumps(plan))
    assert state.execute('--resume') == 2
    assert (state.run / 'run_meta.json').read_bytes() == state.metadata
    assert len(state.started) == 1


def test_driver_eval_resume_rejects_drift_before_changing_old_workspace(interrupted_driver, monkeypatch):
    from eval.observe.console import CONSOLE
    from harnessgrad import verification_context
    state = interrupted_driver
    work = state.run.parent.parent / 'work' / 'eval' / 'workspace'
    def snapshot(root):
        return {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    before_run = snapshot(state.run)
    before_work = snapshot(work)
    original = verification_context.capture
    calls = []
    def drift(*args, **kwargs):
        value = original(*args, **kwargs)
        calls.append(value)
        if len(calls) == 2:
            items = dict(value['tasks'])
            key = next(iter(items))
            items[key] = 'f' * 64 if items[key] != 'f' * 64 else 'e' * 64
            return verification_context.create(items)
        return value
    CONSOLE.close()
    monkeypatch.setattr(verification_context, 'capture', drift)
    assert state.execute('--resume') == 2
    CONSOLE.close()
    assert len(calls) == 2
    assert snapshot(state.run) == before_run
    assert snapshot(work) == before_work
