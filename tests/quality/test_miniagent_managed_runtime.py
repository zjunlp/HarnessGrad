"""Actual MiniAgent wheels, native loop, H0 and checkpoint controls, offline.

The model is an authored MockTransport response; demo scores are valid zeros,
not benchmark or model capability measurements. SDKs never enter the platform.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from eval.run import runner
from harnessgrad import candidate_runtime, snapshot
from harnessgrad import python_harness_runtime as runtime
from test_miniagent_harness import HARNESS, ROOT, managed_miniagent, miniagent_store  # noqa: F401


def test_native_worker_and_shell_share_only_the_bound_readonly_environment(
        tmp_path, monkeypatch, managed_miniagent):
    source = tmp_path / 'source'
    shutil.copytree(HARNESS, source)
    tool = source / 'tools/shell.py'
    # Candidate instrumentation executes inside the actual native tool function,
    # after SDK/config imports. No substitute actor or loop is used.
    probe = '''import importlib.util, subprocess, sys
    import openai, yaml, requests
    state = Path(os.environ['HARNESSGRAD_STATE']) / 'counter'
    home = Path(os.environ['HOME']) / 'counter'
    previous = [p.exists() for p in (state, home)]
    for p in (state, home): p.write_text('task-private')
    denied = False
    try: Path(openai.__file__).write_text('modified')
    except OSError: denied = True
    child = subprocess.check_output(['python', '-I', '-B', '-c',
                                    'import sys;print(sys.executable)'], text=True).strip()
    row = {'python':sys.executable, 'prefix':sys.prefix, 'isolated':bool(sys.flags.isolated),
           'child_python':child, 'home':str(home.parent), 'previous':previous,
           'readonly':bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY), 'write_denied':denied,
           'packages':[m.__file__ for m in (openai,yaml,requests)],
           'pytest_visible':importlib.util.find_spec('pytest') is not None,
           'platform_visible':Path(PLATFORM_PREFIX).exists(),
           'private_visible':(ctx.working_dir/'verifier-private.txt').exists(),
           'python_env':{k:v for k,v in os.environ.items() if k in ('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE')}}
    with (ctx.working_dir/'trace.jsonl').open('a') as trace:
        trace.write(json.dumps({'runtime_control':row})+'\\n')
    command = args.get("command")'''.replace('PLATFORM_PREFIX', repr(sys.prefix))
    tool.write_text(tool.read_text().replace('command = args.get("command")', probe))
    receipt = candidate_runtime.prepare(source)
    assert receipt['cache_hit'] and not receipt['installed']
    binding = receipt['identity']
    assert binding['artifact'] == managed_miniagent.binding['artifact']
    assert binding['source_sha256'] != managed_miniagent.binding['source_sha256']
    monkeypatch.setenv('PYTHONPATH', str(tmp_path / 'poison'))
    monkeypatch.setenv('PYTHONHOME', str(tmp_path / 'poison'))
    monkeypatch.setenv('PYTHONUSERBASE', str(tmp_path / 'poison'))
    verifier_code = '''
import importlib.util,json
from pathlib import Path
facts={'actor_importable':importlib.util.find_spec('openai') is not None,
       'actor_visible':Path(ACTOR_PATH).exists()}
assert Path('verifier-private.txt').read_text() == 'private-control'
assert Path('answer.txt').read_text().strip() == 'MINIAGENT_MOCK'
Path('verifier/evidence.json').write_text(json.dumps(facts))
Path('verifier/reward.txt').write_text('1' if not any(facts.values()) else '0')
'''.replace('ACTOR_PATH', repr(binding['artifact']['path']))
    verifier = {'kind':'command', 'argv':[sys.executable, '-I', '-B', 'check.py'],
        'inputs':[{'path':'check.py','content':verifier_code},
                  {'path':'verifier-private.txt','content':'private-control'}],
        'reward_file':'/verifier/reward.txt', 'evidence_file':'/verifier/evidence.json',
        'result_contract':{'version':1,'score_range':[0,1],'require_exit_zero':True}}

    def launch(index):
        return runner.run_one(source, {'task_id':f'authored-miniagent-{index}', 'goal':'Write answer.txt.'},
            env={'kind':'files','network':'none'}, verifier=verifier, timeout_s=60,
            harness_runtime_identity=binding)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(launch, range(2)))
    (tmp_path / 'runtime-controls.json').write_text(json.dumps(results, indent=2))
    rows = []
    for result in results:
        assert not result.get('invalid'), result
        assert result['exit_code'] == 0, result.get('stderr')
        assert result['verdict']['score'] == 1, result['verdict']
        assert result['harness_runtime_identity'] == binding
        rows.append(next(json.loads(line)['runtime_control'] for line in result['trace'].splitlines()
                         if 'runtime_control' in json.loads(line)))
    artifact = binding['artifact']
    assert len({row['home'] for row in rows}) == 2
    for row in rows:
        assert row['python'] == row['child_python'] == artifact['python']
        assert row['prefix'] == artifact['path'] and row['isolated']
        assert row['readonly'] and row['write_denied']
        assert all(Path(path).is_relative_to(artifact['path']) for path in row['packages'])
        assert row['previous'] == [False, False] and row['python_env'] == {}
        assert not row['pytest_visible'] and not row['platform_visible'] and not row['private_visible']
        assert not Path(row['home']).exists()


def test_shipped_miniagent_h0_reuse_and_snapshot_resume(tmp_path, managed_miniagent, request):
    ids = tmp_path / 'ids.txt'
    ids.write_text('t01\n')
    runs, work = tmp_path / 'runs', tmp_path / 'work'
    env = dict(os.environ)
    env.update(HG_AGENT_MODEL='miniagent-mock', HG_AGENT_BASE_URL='')
    h0_command = [sys.executable, '-B', str(ROOT / 'tools/prepare_h0.py'),
        '--dataset','demo','--harness','miniagent','--study-ids',str(ids),
        '--jobs','1','--model','miniagent-mock','--endpoint','','--runs-root',str(runs)]

    def call(argv, name):
        proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=150)
        (tmp_path / (name + '.log')).write_text(proc.stdout + proc.stderr)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc

    call(h0_command, 'h0-create')
    donor = next(path for path in runs.glob('h0-demo-*') if path.is_dir())
    request.addfinalizer(lambda: shutil.rmtree(Path('/tmp/hg-h0-work') / donor.name, ignore_errors=True))
    h0 = json.loads((donor / 'h0.json').read_text())
    assert h0['harness_runtime_identity']['artifact'] == managed_miniagent.binding['artifact']
    original_h0 = (donor / 'h0.json').read_bytes(), (donor / 'curve.jsonl').read_bytes()
    assert 'reusing' in call(h0_command, 'h0-reuse').stdout
    assert original_h0 == ((donor / 'h0.json').read_bytes(), (donor / 'curve.jsonl').read_bytes())
    common = [sys.executable,'-B',str(ROOT / 'driver.py'),'--harness','miniagent',
        '--dataset','demo','--sampling','all','--jobs','1','--trials','1','--no-summary',
        '--runs-root',str(runs),'--work-root',str(work)]
    train = [*common,'--tasks','t01','--run-id','study','--rounds','1',
        '--method-entrypoint',f'{sys.executable} {ROOT / "methods/noop/run.py"}',
        '--h0-from',donor.name,'--reuse-identical']
    call(train, 'train')
    study = runs / 'study'
    points = [json.loads(line) for line in (study / 'curve.jsonl').read_text().splitlines()]
    assert [point['round'] for point in points] == [0, 1]
    assert points[0]['shared_from']['run_id'] == donor.name
    binding = points[0]['harness_runtime_identity']
    assert all(point['harness_runtime_identity'] == binding for point in points)
    assert all(point['per_task'] == {'t01': 0.0} and point['n_invalid'] == 0 for point in points)
    assert all(not point.get('invalid') and not point.get('harness_failed') for point in points)
    artifacts = [snapshot.load(snapshot.path_for(study, n)) for n in (0, 1)]
    assert artifacts[0]['parent_id'] is None and artifacts[1]['parent_id'] == artifacts[0]['id']
    for n in (0, 1):
        assert 'requirements.lock' in artifacts[n]['files']
        assert not any('site-packages' in name or name.endswith('.pyc') for name in artifacts[n]['files'])
        shutil.rmtree(study / f'state_after_round{n}')
    before = (study / 'curve.jsonl').read_bytes()
    call([*train,'--rounds','2','--resume'], 'train-resume')
    assert (study / 'curve.jsonl').read_bytes().startswith(before)
    resumed = [json.loads(line) for line in (study / 'curve.jsonl').read_text().splitlines()]
    assert [point['round'] for point in resumed] == [0, 1, 2]
    assert resumed[:2] == points and resumed[2]['harness_runtime_identity'] == binding
    assert resumed[2]['per_task'] == {'t01': 0.0} and resumed[2]['n_invalid'] == 0
    assert snapshot.load(snapshot.path_for(study, 2))['parent_id'] == artifacts[1]['id']
    # Training restored source from snapshots and retained a new checkpoint;
    # eval selects that physical checkpoint with the exact original dependencies.
    evaluation = [*common,'--side','eval','--from-run','study','--from-round','2',
        '--tasks','t05','--run-id','exam','--rounds','0']
    call(evaluation, 'eval')
    exam = runs / 'exam'
    point = json.loads((exam / 'curve.jsonl').read_text())
    assert point['harness_runtime_identity'] == binding
    assert point['per_task'] == {'t05': 0.0} and point['n_invalid'] == 0
    assert not point.get('invalid') and not point.get('harness_failed')
    progress = {path: path.read_bytes() for path in (exam / 'progress').rglob('*.json')}
    assert progress
    assert 'scoring the remaining 0' in call([*evaluation,'--resume'], 'eval-resume').stdout
    restored = json.loads((exam / 'curve.jsonl').read_text())
    for key in ('harness_runtime_identity', 'evaluated_from', 'per_task', 'n_invalid', 'harness_failed'):
        assert restored.get(key) == point.get(key)
    assert all(path.read_bytes() == value for path, value in progress.items())


def test_incomplete_native_dependency_lock_is_refused_before_measurement(tmp_path, managed_miniagent):
    source = tmp_path / 'incomplete'
    shutil.copytree(HARNESS, source)
    lock = source / 'requirements.lock'
    lock.write_text(''.join(line for line in lock.read_text().splitlines(keepends=True)
                            if not line.startswith('idna==')))
    with pytest.raises(runtime.RuntimeUnavailable, match='omits or conflicts with required transitive') as failure:
        candidate_runtime.prepare(source)
    assert failure.value.receipt['status'] == 'invalid'
    assert not failure.value.receipt['installed']
    assert runtime.identity(HARNESS) == managed_miniagent.binding
