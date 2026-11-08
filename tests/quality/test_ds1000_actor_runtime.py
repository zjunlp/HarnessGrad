"""Authored, model-free DS-1000 actor shell and verifier from a cold platform."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile

import pytest

from harnessgrad import dataset_runtime, dataset_runtime_identity, task_python
from test_ds1000_execution_runtime import (  # noqa: F401 - explicit fixtures
    ROOT, authored_adapter,
)


@pytest.fixture(scope="module")
def cold_actor_platform():
    # The native sandbox deliberately hides /tmp platform interpreters. This
    # owned stdlib-only venv follows the same route as spreadsheet actor controls.
    cache = Path.home() / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="hg-ds1000-cold-actor-", dir=cache))
    try:
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / "venv")],
                       check=True, capture_output=True, text=True)
        yield root / "venv/bin/python"
    finally:
        from harnessgrad.python_harness_runtime import remove_instance
        remove_instance(root)


def test_actor_declaration_is_lazy_and_has_no_private_inputs(authored_adapter, monkeypatch):
    def unavailable(*args, **kwargs):
        raise dataset_runtime.DatasetRuntimeUnavailable("prepare explicitly --runtime execution")
    monkeypatch.setattr(dataset_runtime, "resolve_execution", unavailable)
    assert list(authored_adapter.ENV) == ["ds1000/0"]
    assert len(authored_adapter.ENV) == 1
    for key in ("ds1000/-1", "ds1000/00", "ds1000/1", "other/0"):
        with pytest.raises(KeyError):
            authored_adapter.ENV[key]
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match="prepare explicitly"):
        authored_adapter.ENV["ds1000/0"]


def test_actor_and_verifier_bind_one_artifact_under_separate_roles(authored_adapter):
    environment = authored_adapter.ENV["ds1000/0"]
    verifier = authored_adapter.VERIFY["ds1000/0"]
    actor, judge = environment["task_python"], verifier["python_runtime"]
    assert {key: value for key, value in actor.items() if key != "role"} == {
        key: value for key, value in judge.items() if key != "role"}
    assert actor["role"] == "actor" and judge["role"] == "verifier"
    assert set(environment) == {"kind", "network", "task_python"}
    assert environment["kind"] == "files" and environment["network"] == "none"
    assert not any(name in json.dumps(environment) for name in ("context.json", "execution.py", "check.py", "reference_code"))
    identity = dataset_runtime_identity.for_evaluation(None, [{"task_id": "ds1000/0"}],
        {"ds1000/0": authored_adapter.setup_for(0)}, {"ds1000/0": verifier}, {"ds1000/0": environment})
    assert set(identity["tasks"]["ds1000/0"]) == {"actor", "verifier"}
    assert identity["tasks"]["ds1000/0"]["actor"] == identity["tasks"]["ds1000/0"]["verifier"]
    assert dataset_runtime_identity.validate(identity) == identity
    assert task_python.receipt(actor) == {"dataset": "ds1000", "runtime_id": actor["runtime_id"],
                                         "role": "actor", "readonly": True}
    assert task_python.admit(environment)["runtime_id"] == actor["runtime_id"]
    assert dataset_runtime.admit_binding(judge, "verifier")["runtime_id"] == actor["runtime_id"]


@pytest.mark.parametrize("wrong", [False, True])
def test_cold_actor_shell_uses_readonly_science_then_official_verifier(authored_adapter, cold_actor_platform, tmp_path, wrong):
    environment = authored_adapter.ENV["ds1000/0"]
    verifier = authored_adapter.VERIFY["ds1000/0"]
    request = {"task": authored_adapter.task_for(0), "setup": authored_adapter.setup_for(0),
               "environment": environment, "verifier": verifier,
               "source": str(authored_adapter.EXECUTION_FILE), "wrong": wrong}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    work = tmp_path / "actor-work"
    work.mkdir()
    script = r'''
import importlib.util,json,os,shlex,sys
from pathlib import Path
ROOT,REQUEST,WORK=Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3])
scientific=('numpy','pandas','scipy','sklearn','matplotlib','seaborn','torch')
assert all(importlib.util.find_spec(name) is None for name in scientific)
sys.path.insert(0,str(ROOT))
from harnessgrad import candidate_runtime,dataset_runtime,dataset_runtime_identity
from eval.run import runner
request=json.loads(REQUEST.read_text())
private_source=request['source']
actor=request['environment']['task_python']
verifier=request['verifier']
tid=request['task']['task_id']
identity=dataset_runtime_identity.for_evaluation(None,[request['task']],{tid:request['setup']},
    {tid:verifier},{tid:request['environment']})
assert set(identity['tasks'][tid])=={'actor','verifier'}
repo=WORK/'authored-harness';repo.mkdir()
(repo/'harness.json').write_text(json.dumps({'name':'authored-stdlib-science-shell','version':'1',
    'entrypoint':'agent.py','backend':'cli','env_kinds':['files'],
    'runtime':{'kind':'python','python':'3.12','requirements':'requirements.lock'}}))
(repo/'requirements.lock').write_text('# Authored stdlib-only harness SDK; no packages'+chr(10))
code=r"""import json,os,sys
from pathlib import Path
import numpy as np
import torch
assert np.__version__=='1.26.4' and torch.__version__=='2.5.1+cpu'
assert torch.version.cuda is None
payload=json.loads(Path('task.json').read_text())
assert set(payload)=={'task_id','goal','inputs'},sorted(payload)
assert payload['inputs']==[]
assert str(Path(sys.prefix))==ARTIFACT
assert os.statvfs(sys.prefix).f_flag & os.ST_RDONLY
try:
 Path(np.__file__).write_text('actor cannot modify dependency bytes')
except OSError:
 pass
else:
 raise AssertionError('task dependency artifact is writable')
for name in ('context.json','execution.py','verify/check.py','data/scaffolds/scorer_input.py'):
 assert not Path(name).exists(),name
assert not (Path(sys.prefix).parent/'artifact.json').exists()
assert not Path(PRIVATE_SOURCE).exists()
assert not Path(OFFICIAL_SOURCE).exists()
assert not (Path(PLATFORM)/'driver.py').exists()
assert not (Path(PLATFORM)/'data/datasets/ds1000/verify/check.py').exists()
value=int(np.array([6]).item()*torch.tensor(7).item())
assert value==42
Path('solution.py').write_text('value = '+str(value-OFFSET)+chr(10)+'# authored'+chr(10))
print('ACTOR_SCIENCE_READY')
""".replace('ARTIFACT',repr(actor['path'])).replace('PRIVATE_SOURCE',repr(private_source)).replace(
    'OFFICIAL_SOURCE',repr(os.environ['HG_DS1000_OFFICIAL_EXECUTION_SOURCE'])).replace(
    'PLATFORM',repr(str(ROOT))).replace('OFFSET','1' if request['wrong'] else '0')
command='python -I -B -c '+shlex.quote(code)
agent="""import argparse,importlib.util,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
assert importlib.util.find_spec('numpy') is None
assert importlib.util.find_spec('torch') is None
r=subprocess.run(['/bin/sh','-c',COMMAND],cwd=a.workdir,capture_output=True,text=True)
Path(a.workdir,'trace.jsonl').write_text(json.dumps({'command':COMMAND,'exit':r.returncode,'output':r.stdout+r.stderr})+chr(10))
assert r.returncode==0,r.stdout+r.stderr
Path(a.workdir,'answer.txt').write_text('solution.py')
""".replace('COMMAND',repr(command))
(repo/'agent.py').write_text(agent)
os.environ.update(HG_AGENT_BACKEND='mock',HARNESSGRAD_WORK_ROOT=str(WORK),UV_OFFLINE='1',UV_PYTHON_DOWNLOADS='never')
sdk=candidate_runtime.prepare(repo,envs={tid:request['environment']},setups={tid:request['setup']})['identity']
result=runner.run_one(repo,request['task'],setup=request['setup'],verifier=verifier,
    env=request['environment'],timeout_s=90,verify_timeout_s=180,dataset_runtime_identity=identity,
    harness_runtime_identity=sdk)
assert not result.get('invalid'),result
assert result['exit_code']==0,result
assert 'ACTOR_SCIENCE_READY' in result['trace'],result
assert result['verdict']['score']==(0 if request['wrong'] else 1),result
assert result['verdict']['evidence']['measured'] is True,result
assert result['verdict']['evidence']['scorer_input']['verified'] is True,result
receipt=result['runtime_services']['task_python']
assert receipt=={'dataset':'ds1000','runtime_id':actor['runtime_id'],'role':'actor','readonly':True}
assert dataset_runtime.admit_binding(actor,'actor')['runtime_id']==actor['runtime_id']
assert all(importlib.util.find_spec(name) is None for name in scientific)
print(json.dumps({'score':result['verdict']['score'],'actor_runtime':receipt,'identity':identity,
                 'trace':result['trace'],'cold_platform_science_present':False}))
'''
    proc = subprocess.run([str(cold_actor_platform), "-I", "-B", "-c", script, str(ROOT), str(path), str(work)],
                          env={**os.environ, "HG_HARNESS_RUNTIME_ROOT": str(cold_actor_platform.parents[2] / "sdk-store")},
                          capture_output=True, text=True, timeout=300)
    (tmp_path / "actor-control.log").write_text(proc.stdout + proc.stderr)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["score"] == (0 if wrong else 1)
    assert result["cold_platform_science_present"] is False
    assert result["actor_runtime"]["readonly"] is True
    assert set(result["identity"]["tasks"]["ds1000/0"]) == {"actor", "verifier"}
