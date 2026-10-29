"""Real CostBench failures keep their cause and actor output after cleanup."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

from data.datasets import costbench, costbench_dynamic
from eval.run import runner
from harnessgrad.config import reset
from test_costbench_dataset import asset
from test_shared_dataset_runtime import harness


@pytest.fixture(params=[costbench, costbench_dynamic], ids=['static', 'dynamic'])
def selected(request):
    dataset = request.param
    if not dataset.provision_status()['present']:
        pytest.skip('pinned CostBench preparation required')
    prefix = 'costbench' if dataset is costbench else 'costbench_dynamic/cost_change'
    tid = prefix + '/test/Query00001'
    case = json.loads((dataset.PREPARED / dataset.roster()[tid]['path'] / 'private.json').read_text())
    return dataset, tid, case


def corrupt_shard(setup, dataset, case, root):
    index = root / case['search_space']
    index.parent.mkdir(parents=True)
    index.write_bytes((dataset.DATASET_ROOT / case['search_space']).read_bytes())
    shard = index.parent / json.loads(index.read_text())['locations']['path']
    shard.parent.mkdir(parents=True, exist_ok=True)
    shard.write_text('{}')
    spec = json.loads(setup['tool_relay']['content'])
    spec['dataset_root'] = str(root)
    setup['tool_relay']['content'] = json.dumps(spec)


@pytest.mark.parametrize('kind', ['runtime', 'startup', 'timeout', 'healthy', 'zero'])
def test_ordinary_trials_archive_real_failure_and_output(selected, tmp_path, monkeypatch, kind):
    dataset, tid, case = selected
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + ':/usr/bin:/bin')
    repo = harness(work, f'''import json, time
from costbench_transport import call, ToolTransportError
(w/'trace.jsonl').write_text('trace before fault\\n')
(w/'answer.txt').write_text('answer before fault\\n')
print('stdout start ' + 'x' * 5000 + ' stdout end', flush=True)
print('stderr start ' + 'y' * 5000 + ' stderr end', file=sys.stderr, flush=True)
kind = {kind!r}
if kind == 'timeout': time.sleep(60)
if kind in ('runtime', 'healthy'):
    oracle = json.loads((w/'.oracle/control.json').read_text())
    try:
        for action in oracle['actions']:
            call('/run/harnessgrad-tool/tool.sock', 'turn', {{'content': None, 'calls': [action]}})
        call('/run/harnessgrad-tool/tool.sock', 'turn', {{
            'content': '<answer> ' + oracle['answer'] + ' </answer>', 'calls': []}})
    except ToolTransportError:
        # Deliberately hide the exception from Harness output. The platform's
        # private relay diagnostics must still explain the actual failure.
        sys.exit(19)
''')
    manifest_path = repo / 'harness.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['runtime_paths'] = list(dict.fromkeys([
        sys.prefix, sys.base_prefix, str(Path(sys._base_executable).parent.parent)]))
    manifest_path.write_text(json.dumps(manifest))
    setup = deepcopy(dataset.SETUP[tid])
    setup['files'].append({'path': '.oracle/control.json', 'content': json.dumps({
        'actions': case['oracle_actions'], 'answer': case['oracle_answer']})})
    if kind == 'runtime':
        corrupt_shard(setup, dataset, case, tmp_path / 'bad-database')
    elif kind == 'startup':
        spec = json.loads(setup['tool_relay']['content'])
        spec['sha256'] = '0' * 64
        setup['tool_relay']['content'] = json.dumps(spec)
    verifier = dataset.VERIFY[tid]
    assert verifier['trial_evidence'] == {'version': 1}
    assert 'trial_protocol' not in verifier
    progress = tmp_path / 'run'
    reset()
    try:
        result = runner.evaluate(repo, [dataset.control_task(tid)], {tid: 'fixture'},
            setups={tid: setup}, verifiers={tid: verifier},
            envs={tid: {**dataset.ENV[tid], 'agent_timeout_s': 2 if kind == 'timeout' else 30,
                       'verify_timeout_s': 60}},
            sandbox=True, trials=2, jobs=1, run_id='costbench-p2-control', run_seed='same-seed',
            harness_sha='fixture-harness', progress_dir=progress,
            recordings_root=progress / 'recordings')
    finally:
        reset()
    records = [json.loads(path.read_text()) for path in (progress / 'trial-evidence').rglob('*.json')]
    records = [row for row in records if row.get('schema') == 'harnessgrad.trial-evidence.v1']
    failed = kind in ('runtime', 'startup')
    # A declared Harness timeout is an ordinary budget failure; relay faults
    # stop the task as unmeasured. Archival must preserve both classifications.
    assert len(records) == (1 if failed else 2)
    assert all(row['binding']['task_id'] == tid and row['binding']['seed'] == 'same-seed'
               and row['binding']['run_id'] == 'costbench-p2-control'
               and row['binding']['harness_sha'] == 'fixture-harness' for row in records)
    if failed:
        assert tid in result['invalid'] and tid not in result['per_task']
    else:
        assert tid not in result['invalid']
        assert result['per_task'][tid] == float(kind == 'healthy')
    for row in records:
        raw = row['result']
        output = raw['trial_evidence_output']
        relay = raw['runtime_services']['tool_relay']
        assert relay['cleanup']['complete']
        if kind == 'startup':
            assert output['launch_attempted'] is False
            assert output['trace_status'] == 'not_started'
            assert output['stdout'] is None
        else:
            assert output['trace'] == 'trace before fault\n'
            assert output['answer'] == 'answer before fault\n'
            assert output['stdout'].startswith('stdout start ') and output['stdout'].endswith(' stdout end\n')
            assert output['stderr'].startswith('stderr start ') and output['stderr'].endswith(' stderr end\n')
            assert len(output['stdout']) > 5000 and len(output['stderr']) > 5000
        if kind == 'timeout':
            assert output['launch_error']['type'] == 'TimeoutExpired'
            assert raw['exit_code'] == -1 and raw['stderr'] == 'timeout after 2s'
        if kind not in ('runtime', 'startup'):
            continue
        assert raw.get('verdict') is None
        assert relay['recording'] is None
        diagnostic = output['tool_relay']
        assert diagnostic['scorable'] is False
        log = diagnostic['files']['relay.log']
        assert log['state'] == 'captured'
        assert hashlib.sha256(log['content'].encode()).hexdigest() == log['sha256']
        cause = ('CostBench database shard digest mismatch' if kind == 'runtime'
                 else 'selected case digest mismatch')
        assert cause in log['content'] and 'Traceback' in log['content']
        assert 'content' not in json.dumps(relay.get('diagnostic'))
        assert cause not in json.dumps(relay), 'raw diagnostics must stay in the private archive'
        assert cause not in json.dumps(result), 'raw diagnostics must not enter evaluation feedback'
        if kind == 'runtime':
            assert output['exit_code'] == 19
            receipt = json.loads(diagnostic['files']['receipt.json']['content'])
            assert receipt['failure'] == 'RuntimeError'
            assert receipt['failure_detail']['message'] == cause
            assert receipt['failure_detail']['operation'] == 'turn'
            assert receipt['failure_detail']['step'] == 1
            assert receipt['shutdown_failure'] == 'transport_failure'
            episode = json.loads(diagnostic['files']['episode.json']['content'])
            assert episode['failure'] == 'RuntimeError'
            assert cause in episode['failure_detail']['traceback']
        else:
            assert diagnostic['files']['episode.json']['state'] == 'missing'


def test_first_exception_survives_a_later_failure(monkeypatch):
    relay = asset('relay')

    class State:
        steps = 0

        def __init__(self, *_):
            pass

        def turn(self, **_):
            self.steps += 1
            if self.steps == 1:
                raise RuntimeError('first cause')
            raise OSError('later failure')

    monkeypatch.setattr(relay, 'CostBenchRuntime', State)
    service = relay.Service({}, {})
    for exception in (RuntimeError, OSError):
        with pytest.raises(exception):
            service.handle('turn', {'content': None, 'calls': []})
    assert service.failure == 'RuntimeError'
    assert service.failure_detail['message'] == 'first cause'
    assert service.failure_detail['step'] == 1
    assert 'later failure' not in service.failure_detail['traceback']
