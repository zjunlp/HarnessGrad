"""The real driver records the same seed that resumed ordinary trials execute."""
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

import driver
from eval.run import repeated_trials
from harnessgrad import config


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
    source = tmp_path / 'runs/train/state_after_round0'
    source.mkdir(parents=True)
    (source / 'harness.json').write_text(json.dumps({
        'name': 'seed-control', 'version': '1', 'entrypoint': 'agent.py',
        'runtime_paths': list(dict.fromkeys([sys.prefix, sys.base_prefix,
            str(Path(sys._base_executable).parent.parent)]))}))
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
    state = SimpleNamespace(run=tmp_path / 'runs/eval', interrupt=True, started=[])
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
    state.seed = json.loads(state.metadata)['config']['run']['seed']
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
