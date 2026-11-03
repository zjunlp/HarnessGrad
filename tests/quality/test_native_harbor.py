"""Native Harbor protocols must not silently become answer/exit-code proxies."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data.scaffolds import native_harbor as native
from data.datasets import spreadsheetbench2 as spreadsheets


def source_task(root, name='task-one', *, dataset='deepswe', split='main'):
    folder = root / 'tasks' / name
    folder.mkdir(parents=True)
    (folder / 'instruction.md').write_text('Repair the public project. Do not inspect private checks.')
    (folder / 'task.toml').write_text(f'''schema_version = "1.3"
[metadata]
split = "{split}"
[agent]
network_mode = "no-network"
timeout_sec = 3000
[environment]
docker_image = "example/native@sha256:{'a'*64}"
cpus = 2
memory_mb = 4096
[verifier]
environment_mode = "separate"
network_mode = "no-network"
[[verifier.collect]]
command = "git diff --binary BASE HEAD > /logs/artifacts/model.patch"
''')
    (folder / 'tests').mkdir()
    (folder / 'tests/test.sh').write_text('echo PRIVATE_EXPECTED_ANSWER')
    return folder


def test_native_separate_verifier_collect_and_network_are_retained(tmp_path):
    folder = source_task(tmp_path)
    rows = native.inventory(tmp_path, 'deepswe')
    assert len(rows) == 1
    row = rows[0]
    assert row.declaration['verifier']['collect'][0]['command'].startswith('git diff')
    assert set(row.required_capabilities()) == {
        'separate-verifier-image', 'native-collect-hooks', 'model-only-container-egress'}
    assert row.public() == {'task_id': 'deepswe/task-one', 'goal': (folder / 'instruction.md').read_text()}
    assert 'PRIVATE_EXPECTED_ANSWER' not in json.dumps(row.public())
    with pytest.raises(native.NativeRuntimeUnavailable, match='native-runtime.json'):
        native.require_execution(rows)
    status = native.status(tmp_path, 'deepswe')
    assert status['source_prepared'] and status['tasks'] == 1
    assert status['present'] is False and status['execution_ready'] is False


def test_deepswe_does_not_accept_weaker_snapshot_task(tmp_path):
    folder = source_task(tmp_path)
    path = folder / 'task.toml'
    path.write_text(path.read_text().replace('environment_mode = "separate"', 'environment_mode = "shared"'))
    with pytest.raises(ValueError, match='separate verifier'):
        native.inventory(tmp_path, 'deepswe')


def test_native_symlink_or_missing_data_never_becomes_empty_benchmark(tmp_path):
    with pytest.raises(ValueError, match='no task.toml'):
        native.inventory(tmp_path, 'deepswe')
    folder = source_task(tmp_path)
    (folder / 'tests/private-link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='symlink'):
        native.inventory(tmp_path, 'deepswe')


def test_jobbench_collections_and_private_judge_requirements(tmp_path):
    source_task(tmp_path, name='main-one', dataset='jobbench', split='main')
    source_task(tmp_path, name='easy-one', dataset='jobbench', split='easy')
    main = native.inventory(tmp_path, 'jobbench', collection='main')
    assert [r.task_id for r in main] == ['jobbench/main-one']
    assert 'independent-judge-route' in main[0].required_capabilities()
    assert len(native.inventory(tmp_path, 'jobbench', collection='all')) == 2


def test_swepro_requires_official_pristine_replay_even_for_shared_task(tmp_path):
    folder = source_task(tmp_path, dataset='swe_bench_pro')
    path = folder / 'task.toml'
    path.write_text(path.read_text().replace('environment_mode = "separate"', 'environment_mode = "shared"'))
    rows = native.inventory(tmp_path, 'swe_bench_pro')
    assert 'pristine-patch-replay-and-regrade' in rows[0].required_capabilities()
    with pytest.raises(native.NativeRuntimeUnavailable, match='native-runtime.json'):
        native.require_execution(rows)


def build_workbook(file, values):
    openpyxl = pytest.importorskip('openpyxl')
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Sheet'
    for cell, value in values.items():
        sheet[cell] = value
    file.parent.mkdir(parents=True, exist_ok=True)
    book.save(file)


@pytest.fixture
def workbook_dataset(tmp_path, monkeypatch):
    root = tmp_path / 'dataset'
    for category in spreadsheets.CATEGORIES:
        folder = root / category
        folder.mkdir(parents=True)
        build_workbook(folder / 'input.xlsx', {'A1': 0, 'A2': 2})
        build_workbook(folder / 'gold.xlsx', {'A1': 1, 'A2': 2})
        (folder / 'dataset.json').write_text(json.dumps([{
            'id': 'case-1', 'instruction': 'Change A1 to 1 and preserve A2.',
            'spreadsheet_path': 'input.xlsx', 'golden_response_path': 'gold.xlsx',
            'answer_position': 'Sheet!A1:A2'}]))
    monkeypatch.setattr(spreadsheets, 'DATASET_ROOT', root)
    monkeypatch.setenv('HG_SPREADSHEETBENCH2_CATEGORY', 'all')
    return root


def test_spreadsheet2_source_roles_and_private_verifier_binding(workbook_dataset):
    tasks, scores = spreadsheets.load()
    assert len(tasks) == 3
    assert len(set(t['task_id'] for t in tasks)) == 3
    rows = spreadsheets.records()
    for task, record in zip(tasks, rows):
        assert task['submission']['paths'] == ['case-1_output.xlsx']
        public = json.dumps(task)
        assert 'golden_response_path' not in public and 'answer_position' not in public
        setup = spreadsheets._setup(record)
        assert [i['path'] for i in setup['files']] == ['input.xlsx']
        verifier = spreadsheets._verify(record)
        assert verifier['result_contract']['require_exit_zero']
        assert verifier['scorer_input']['task_id'] == task['task_id']
        assert {'golden.xlsx', 'record.json'} <= verifier['scorer_input']['files'].keys()
    assert spreadsheets.SPLIT is None and spreadsheets.OFFICIAL_EVAL is None


def materialize(spec, root):
    for item in spec['inputs']:
        target = root / item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        if 'from' in item:
            shutil.copyfile(item['from'], target)
        else:
            target.write_text(item['content'])


def test_spreadsheet2_official_source_bytes_match_pinned_provenance():
    root = ROOT / 'data/datasets/spreadsheetbench2'
    source = json.loads((root / 'PROVENANCE.json').read_text())
    assert source['revision'] == 'a7bdc2d358e052d35a94768e1f487c0d9e9c0011'
    for name, digest in source['files'].items():
        assert hashlib.sha256((root / 'verify' / name).read_bytes()).hexdigest() == digest


@pytest.mark.skipif(shutil.which('soffice') is None, reason='native LibreOffice required')
@pytest.mark.parametrize('output,expected', [('oracle', 1.0), ('wrong', 0.0), ('missing', 0.0)])
def test_official_workbook_oracle_mismatch_and_noop(workbook_dataset, tmp_path, output, expected):
    row = spreadsheets.records()[0]
    spec = spreadsheets._verify(row)
    work = tmp_path / 'verify'
    work.mkdir()
    materialize(spec, work)
    artifact = work / row['output']
    if output == 'oracle':
        shutil.copyfile(row['golden'], artifact)
    elif output == 'wrong':
        shutil.copyfile(row['input'], artifact)
    before = artifact.read_bytes() if artifact.exists() else None
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    proc = subprocess.run([sys.executable, *spec['argv'][1:]], cwd=work, env=env,
                           capture_output=True, text=True, errors='replace', timeout=100)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert float((work / 'verifier/reward.txt').read_text()) == expected
    evidence = json.loads((work / 'verifier/evidence.json').read_text())
    assert evidence['measured'] is True and evidence['scorer_input']['verified'] is True
    assert 'expected' not in evidence and 'error_message' not in evidence
    assert (artifact.read_bytes() if artifact.exists() else None) == before


def test_source_runtime_failure_has_no_reward(workbook_dataset, tmp_path):
    row = spreadsheets.records()[0]
    spec = spreadsheets._verify(row)
    work = tmp_path / 'verify'
    work.mkdir()
    materialize(spec, work)
    native_file = work / 'spreadsheetbench2-check/evaluation.py'
    native_file.write_text(native_file.read_text() + '\n# changed native scorer\n')
    proc = subprocess.run([sys.executable, *spec['argv'][1:]], cwd=work, capture_output=True,
                          text=True, errors='replace', timeout=10)
    assert proc.returncode != 0
    assert not (work / 'verifier/reward.txt').exists()
    assert json.loads((work / 'verifier/evidence.json').read_text())['measured'] is False


def test_explicit_workbook_preparation_preserves_bytes_and_excludes_visualization(tmp_path):
    file = ROOT / 'data/datasets/spreadsheetbench2/prepare.py'
    spec = importlib.util.spec_from_file_location('spreadsheet2_prepare', file)
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    archive = tmp_path / 'native.zip'
    with zipfile.ZipFile(archive, 'w') as source:
        source.writestr('spreadsheetbench-v2/Debugging/dataset.json', '[]')
        source.writestr('spreadsheetbench-v2/Debugging/native.xlsx', b'workbook-byte-fixture')
        source.writestr('spreadsheetbench-v2/Visualization/native.xlsx', b'not-an-equivalent-task')
    root = tmp_path / 'prepared'
    manifest = prepare.extract(archive, root)
    assert (root / 'spreadsheetbench-v2/Debugging/native.xlsx').read_bytes() == b'workbook-byte-fixture'
    assert not (root / 'spreadsheetbench-v2/Visualization').exists()
    assert manifest['revision'] == spreadsheets.REVISION
    assert manifest['excluded_categories'] == ['Visualization']
    with zipfile.ZipFile(archive, 'a') as source:
        source.writestr('../escape', b'bad')
    with pytest.raises(ValueError, match='unsafe'):
        prepare.extract(archive, tmp_path / 'rejected')


def test_native_source_preparation_preserves_executable_tests_and_refuses_mutation(tmp_path):
    file = ROOT / 'data/datasets/deepswe/prepare.py'
    spec = importlib.util.spec_from_file_location('deepswe_prepare', file)
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    source = tmp_path / 'source'
    task = source_task(source)
    (task / 'tests/test.sh').chmod(0o755)
    archive = tmp_path / 'native.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        bundle.add(source, arcname='source-revision')
    root = tmp_path / 'prepared'
    files = prepare.extract(archive, root)
    assert 'tasks/task-one/task.toml' in files
    assert (root / 'tasks/task-one/tests/test.sh').stat().st_mode & 0o111
    (root / 'tasks/task-one/instruction.md').write_text('mutated')
    with pytest.raises(ValueError, match='differs'):
        prepare.extract(archive, root)


@pytest.mark.skipif(shutil.which('soffice') is None or shutil.which('bwrap') is None,
                    reason='native LibreOffice and bubblewrap required')
def test_spreadsheet2_runs_through_platform_sandbox_and_private_native_verifier(
        workbook_dataset, tmp_path, monkeypatch):
    from eval.ckpt.git_state import stage_workspace
    from eval.run.runner import run_one
    source = tmp_path / 'submitted-harness'
    source.mkdir()
    (source / 'harness.json').write_text(json.dumps({
        'name': 'spreadsheet2-synthetic-control', 'version': '1',
        'entrypoint': 'agent.py', 'backend': 'cli', 'env_kinds': ['files']}))
    (source / 'agent.py').write_text('''import argparse,json
from pathlib import Path
import openpyxl
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args()
w=Path(a.workdir);t=json.loads(Path(a.task).read_text());b=openpyxl.load_workbook(w/'input.xlsx')
b['Sheet']['A1']=1;b.save(w/t['submission']['paths'][0])
(w/'trace.jsonl').write_text(json.dumps({'usage':{'calls':0}})+'\\n')
''')
    work_root = tmp_path / 'work'
    repo = work_root / 'run/workspace'
    stage_workspace(source, repo)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work_root))
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    task = spreadsheets.load()[0][0]
    record = spreadsheets.records()[0]
    result = run_one(repo, task, sandbox=True, timeout_s=20,
                     setup=spreadsheets._setup(record), verifier=spreadsheets._verify(record),
                     verify_timeout_s=100, env=dict(spreadsheets.DEFAULT_ENV))
    assert not result.get('invalid'), result
    assert result['exit_code'] == 0, result.get('stderr')
    assert result['verdict']['passed'], result['verdict']
    assert result['verdict']['score'] == 1.0


def test_runtime_absence_is_a_specific_provisioning_condition(tmp_path, monkeypatch):
    row=native.inventory(source_task(tmp_path).parent.parent,'deepswe')[0]
    monkeypatch.setenv('HG_DEEPSWE_RUNTIME',str(tmp_path/'not-prepared.json'))
    with pytest.raises(native.NativeRuntimeUnavailable,match='native-runtime.json'):
        native.runtime(row)


def test_native_artifact_export_checks_bytes_identity_and_custody(tmp_path):
    import base64
    from data.scaffolds.native_harbor_runtime.verify import export_artifacts,sealed_artifacts
    source=tmp_path/'source';source_task(source)
    sha=native._tree_identity(source)
    case={'task_id':'deepswe/control','dataset':'deepswe','source_sha256':sha,'directory':str(source)}
    raw=b'captured committed patch'
    record={'status':'submitted','base64':base64.b64encode(raw).decode(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    payload={'task_id':'deepswe/control','dataset':'deepswe','source_sha256':sha,'finished':True,
             'case_sha256':hashlib.sha256(json.dumps(case,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
             'actor_runtime':{'cleanup':{'complete':True}},'artifacts':{'/logs/artifacts/model.patch':record}}
    root=tmp_path/'verify';(root/'native_episode').mkdir(parents=True)
    envelope={'binding':{'task_id':case['task_id'],'run_id':'r','trial_id':'t'},'payload':payload}
    (root/'native_episode/episode.json').write_text(json.dumps(envelope))
    actual=sealed_artifacts(case,root,task_id=case['task_id'],run_id='r',trial_id='t')
    export_artifacts(actual,root/'export')
    assert (root/'export/files/logs/artifacts/model.patch').read_bytes()==raw
    with pytest.raises(ValueError,match='incomplete or mismatched'):
        sealed_artifacts({**case,'runtime':{'actor':{'image':'different-actor'}}},root,
                         task_id=case['task_id'],run_id='r',trial_id='t')
    with pytest.raises(ValueError,match='another execution'):
        sealed_artifacts(case,root,task_id=case['task_id'],run_id='other',trial_id='t')
    payload['artifacts']['/logs/artifacts/model.patch']['sha256']='0'*64
    with pytest.raises(ValueError,match='bytes changed'):
        export_artifacts(payload,root/'tampered')


def test_native_public_runtime_requires_immutable_identity_and_resource_limits(monkeypatch):
    from data.scaffolds.native_harbor_runtime.public_runtime import validate_public_runtime,PublicRuntime
    actor={'kind':'exec','backend':'native-public','network':'public','image':'sha256:'+'a'*64,
           'workdir':'/workspace','python':'/usr/local/bin/python3','cpus':1,'memory_mb':256,'pids':64}
    validate_public_runtime(actor)
    for key,value in [('image','mutable:latest'),('workdir','/workspace/../etc'),('python','python3'),
                      ('cpus',float('nan')),('memory_mb',0),('pids',-1)]:
        with pytest.raises(ValueError):validate_public_runtime({**actor,key:value})
    monkeypatch.setenv('HG_NATIVE_HARBOR_ALLOW_PUBLIC','native protocol control')
    monkeypatch.setattr('data.scaffolds.native_harbor_runtime.public_runtime.checked',
                        lambda *args:json.dumps([{'Id':'sha256:'+'b'*64,'Config':{}}]))
    instance=PublicRuntime(actor,binding={'task_id':'jobbench/control'},timeout_s=5)
    with pytest.raises(ValueError,match='identity differs'):instance.__enter__()


def test_native_case_keeps_private_collect_and_judge_out_of_public_task(tmp_path,monkeypatch):
    source_task(tmp_path)
    row=native.inventory(tmp_path,'deepswe')[0]
    monkeypatch.setattr(native,'runtime',lambda row:{'actor':{'workdir':'/app'}})
    spec=native.verifier(row)
    case=json.loads(spec['inputs'][0]['content'])
    assert spec['argv']==['harnessgrad-native','deepswe']
    assert spec['native_benchmark']['record_path']=='native_episode'
    assert case['collect']==row.declaration['verifier']['collect']
    assert 'collect' not in native.public_task(row)
    assert 'source_sha256' not in native.public_task(row)


def _control_image(tmp_path, *, verifier=False):
    """A tiny local native fixture: no pulls, no model, no benchmark image download."""
    docker=shutil.which('docker')
    if not docker:pytest.skip('Docker not installed')
    base=subprocess.run([docker,'image','inspect','python:3.12-bookworm'],capture_output=True)
    if base.returncode:pytest.skip('local Python3.12 control base image unavailable')
    folder=tmp_path/('verifier-build' if verifier else 'actor-build');folder.mkdir()
    if verifier:
        script='''#!/bin/bash
set -eu
mkdir -p /logs/verifier
python3 - <<'EOF'
import pathlib,json
p=pathlib.Path('/workspace/output/result.txt')
ok=p.exists() and p.read_text()=='correct'
pathlib.Path('/logs/verifier/reward.txt').write_text(str(int(ok)))
EOF
'''
        (folder/'test.sh').write_text(script)
        recipe='FROM python:3.12-bookworm\nRUN mkdir -p /app /tests /workspace/output\nCOPY test.sh /tests/test.sh\nWORKDIR /app\n'
    else:
        recipe='FROM python:3.12-bookworm\nRUN mkdir -p /app/.logs/artifacts /app/.logs/agent /workspace/output && ln -s /app/.logs /logs\nWORKDIR /app\n'
    (folder/'Dockerfile').write_text(recipe)
    process=subprocess.run([docker,'build','--network=none','-q',str(folder)],capture_output=True,
                           text=True,errors='replace',timeout=60)
    assert process.returncode==0,process.stderr
    return process.stdout.strip()


@pytest.mark.skipif(not Path('/tmp/hg-flash-harbor-runtime/bin/python').is_file(),reason='selected Harbor0.23 control interpreter absent')
def test_real_observed_native_world_exec_and_sealed_patch(tmp_path):
    from data.scaffolds.native_harbor_runtime.session import Session
    folder=source_task(tmp_path/'native')
    image=_control_image(tmp_path)
    actor={'kind':'exec','backend':'observed-v1','image':image,'python':'/usr/local/bin/python3',
           'workdir':'/app','image_workspace':'/app','network':'none','cpus':1,'memory_mb':256,'pids':64,
           'filesystems':{p:{'bytes':64<<20,'inodes':16384} for p in ('/app','/tmp','/dev','/dev/shm')}}
    case={'dataset':'deepswe','task_id':'deepswe/control','directory':str(folder),
          'source_sha256':native._tree_identity(folder),'runtime':{'actor':actor},
          'actor_timeout_s':20,'collect':[],'artifact_paths':['/logs/artifacts/model.patch']}
    binding={'adapter_id':case['task_id'],'run_id':'control','trial_id':'t','manifest_sha256':case['source_sha256']}
    session=Session(case,binding)
    assert session.handle('exec',{'command':"printf 'PATCH_BYTES' > /logs/artifacts/model.patch"})['returncode']==0
    assert session.handle('finish',{})['finished']
    record=session.record()
    assert record['actor_runtime']['cleanup']['complete'],record
    assert record['artifacts']['/logs/artifacts/model.patch']['sha256']==hashlib.sha256(b'PATCH_BYTES').hexdigest()
    with pytest.raises(ValueError,match='already sealed'):
        session.handle('exec',{'command':'true'})


@pytest.mark.skipif(not Path('/tmp/hg-flash-harbor-runtime/bin/python').is_file(),reason='selected Harbor0.23 control interpreter absent')
@pytest.mark.parametrize('answer,expected',[('correct',1.0),('incorrect',0.0)])
def test_real_native_harbor_fresh_separate_verifier(tmp_path,answer,expected):
    actor_image=_control_image(tmp_path)
    verifier_image=_control_image(tmp_path,verifier=True)
    folder=tmp_path/'native/control';folder.mkdir(parents=True);(folder/'tests').mkdir();(folder/'environment').mkdir()
    (folder/'instruction.md').write_text('Produce one final deliverable in /workspace/output.')
    (folder/'tests/test.sh').write_text('# native private tests exist only in verifier image')
    (folder/'task.toml').write_text(f'''schema_version="1.4"
artifacts=["/workspace/output"]
[task]
name="jobbench/control"
[agent]
network_mode="no-network"
timeout_sec=10
[environment]
docker_image="{actor_image}"
workdir="/app"
cpus=1
memory_mb=256
[verifier]
network_mode="no-network"
environment_mode="separate"
timeout_sec=20
[verifier.environment]
docker_image="{verifier_image}"
network_mode="no-network"
cpus=1
memory_mb=256
''')
    exports=tmp_path/'exports';(exports/'files/workspace/output').mkdir(parents=True)
    (exports/'files/workspace/output/result.txt').write_text(answer)
    (exports/'manifest.json').write_text(json.dumps({'files':{'workspace/output/result.txt':{}}}))
    case={'dataset':'jobbench','task_id':'jobbench/control','directory':str(folder),
          'source_sha256':native._tree_identity(folder),'runtime':{'actor':{'workdir':'/workspace'},
          'replay_actor_image':actor_image,'verifier_image':verifier_image,
          'harbor_python':'/tmp/hg-flash-harbor-runtime/bin/python'}}
    request={'case':case,'artifact_root':str(exports),'task_projection':str(folder),'trial_root':str(tmp_path/'trials'),
             'source_job':str(tmp_path/'source-job'),'verifier_env':{}}
    file=tmp_path/'request.json';file.write_text(json.dumps(request));result_path=tmp_path/'result.json'
    command=[case['runtime']['harbor_python'],'-I',str(ROOT/'data/scaffolds/native_harbor_runtime/harbor_entry.py'),str(file),str(result_path)]
    process=subprocess.run(command,capture_output=True,text=True,errors='replace',timeout=100)
    assert process.returncode==0,process.stderr
    result=json.loads(result_path.read_text())
    assert result['reward']==expected
    assert result['official_evaluation_performed']
    assert len(result['runtime_observations'])==2
    assert result['runtime_observations'][0]['container_id']!=result['runtime_observations'][1]['container_id']
    assert all(r['cleanup']['complete'] for r in result['runtime_observations'])
    assert all(not r['mounts'] for r in result['runtime_observations'])


def test_failed_explicit_finish_remains_unscorable_after_teardown(tmp_path):
    from data.scaffolds.native_harbor_runtime.session import Session
    from data.scaffolds.native_harbor_runtime.verify import sealed_artifacts
    source=source_task(tmp_path/'source')
    class FailedCollector:
        def __init__(self,*args,**kwargs):self.receipt={};self.closed=False
        def __enter__(self):return self
        def run(self,argv,**kwargs):
            # Public startup probe completes; source collector fails.
            return subprocess.CompletedProcess(argv, 9 if argv[0]=='/bin/bash' else 0, '', '')
        def close(self):self.closed=True;self.receipt['cleanup']={'complete':True}
    case={'dataset':'deepswe','task_id':'deepswe/control','directory':str(source),
          'source_sha256':native._tree_identity(source),'runtime':{'actor':{'workdir':'/app','python':'python3'}},
          'actor_timeout_s':10,'collect':[{'command':'native collector failed'}],
          'artifact_paths':['/logs/artifacts/model.patch']}
    binding={'adapter_id':case['task_id'],'run_id':'r','trial_id':'t','manifest_sha256':case['source_sha256']}
    session=Session(case,binding,runtime_factory=FailedCollector)
    with pytest.raises(RuntimeError,match='collect command failed'):session.handle('finish',{})
    assert not session.finished and session.failure=='RuntimeError' and session.runtime.closed
    with pytest.raises(RuntimeError,match='previously failed'):session.finish(cleanup=True)
    root=tmp_path/'sealed';(root/'native_episode').mkdir(parents=True)
    (root/'native_episode/episode.json').write_text(json.dumps({'binding':{'task_id':case['task_id'],'run_id':'r','trial_id':'t'},'payload':session.record()}))
    with pytest.raises(ValueError,match='incomplete'):
        sealed_artifacts(case,root,task_id=case['task_id'],run_id='r',trial_id='t')


def test_startup_probe_failure_closes_created_world(tmp_path):
    from data.scaffolds.native_harbor_runtime.session import Session
    source=source_task(tmp_path/'source');instances=[]
    class FailedProbe:
        def __init__(self,*args,**kwargs):self.closed=False;instances.append(self)
        def __enter__(self):return self
        def run(self,*args,**kwargs):raise TimeoutError('startup probe timeout')
        def close(self):self.closed=True
    case={'dataset':'deepswe','task_id':'deepswe/control','directory':str(source),
          'source_sha256':native._tree_identity(source),'runtime':{'actor':{'workdir':'/app','python':'python3'}},
          'actor_timeout_s':10,'collect':[],'artifact_paths':[]}
    with pytest.raises(TimeoutError):Session(case,{},runtime_factory=FailedProbe)
    assert instances[0].closed


def test_unselected_native_runtime_does_not_block_loading_other_tasks(tmp_path,monkeypatch):
    from data.datasets import deepswe
    source_task(tmp_path,name='one');source_task(tmp_path,name='two')
    monkeypatch.setattr(deepswe,'DATASET_ROOT',tmp_path)
    checked=[]
    monkeypatch.setattr(native,'runtime',lambda row:checked.append(row.task_id))
    tasks,_=deepswe.load()
    assert len(tasks)==2 and checked==[]
    mapping=deepswe.ENV
    assert list(mapping)==['deepswe/one','deepswe/two'] and checked==[]
    monkeypatch.setattr(native,'setup',lambda row:{'checked':row.task_id})
    assert deepswe.SETUP['deepswe/one']=={'checked':'deepswe/one'}
    assert checked==[]


@pytest.mark.skipif(not Path('/tmp/hg-flash-harbor-runtime/bin/python').is_file(),reason='selected Harbor0.23 control interpreter absent')
@pytest.mark.parametrize('answer,expected',[('1',1.0),('0',0.0)])
def test_official_pro_patch_replay_source_in_fresh_git_image(tmp_path,answer,expected):
    import base64
    source=Path('/tmp/flash-native-upstream/swe_bench_pro/v2/tooling/patch_replay.py')
    if not source.is_file():pytest.skip('pinned official Pro tooling not acquired')
    pinned=json.loads((ROOT/'data/scaffolds/native_harbor_runtime/PROVENANCE.json').read_text())
    assert hashlib.sha256(source.read_bytes()).hexdigest()==pinned['pro_tooling_sha256']['patch_replay.py']
    folder=tmp_path/'build';folder.mkdir()
    (folder/'Dockerfile').write_text('''FROM python:3.12-bookworm
RUN mkdir -p /app /tests && cd /app && git init -q && git config user.name control && git config user.email control@local && printf '0\\n' > value.txt && git add value.txt && git commit -qm base
WORKDIR /app
''')
    process=subprocess.run(['docker','build','--network=none','-q',str(folder)],capture_output=True,text=True,errors='replace',timeout=60)
    assert process.returncode==0,process.stderr
    image=process.stdout.strip()
    native_dir=tmp_path/'native/control';(native_dir/'tests').mkdir(parents=True);(native_dir/'environment').mkdir()
    (native_dir/'instruction.md').write_text('Change value.txt to 1.')
    (native_dir/'tests/test.sh').write_text('''#!/bin/bash
mkdir -p /logs/verifier
python3 - <<'EOF'
import pathlib
pathlib.Path('/logs/verifier/reward.txt').write_text(str(int(pathlib.Path('/app/value.txt').read_text().strip()=='1')))
EOF
''')
    (native_dir/'task.toml').write_text(f'''schema_version="1.4"
artifacts=[]
[task]
name="swebench-pro/control"
[agent]
network_mode="no-network"
timeout_sec=20
[environment]
docker_image="{image}"
workdir="/app"
cpus=1
memory_mb=256
[verifier]
network_mode="no-network"
timeout_sec=20
''')
    exports=tmp_path/'exports';(exports/'files/logs/agent').mkdir(parents=True)
    patch=(f'diff --git a/value.txt b/value.txt\n--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-0\n+{answer}\n' if answer=='1' else '')
    captured=exports/'files/logs/agent/model.patch';captured.write_text(patch)
    (exports/'manifest.json').write_text(json.dumps({'files':{'logs/agent/model.patch':{}}}))
    source_job=tmp_path/'source-job';agent=source_job/'instance_control/agent';agent.mkdir(parents=True)
    shutil.copyfile(captured,agent/'model.patch');(agent.parent/'result.json').write_text(json.dumps({'task_name':'swebench-pro/control'}))
    case={'dataset':'swe_bench_pro','task_id':'swe_bench_pro/control','runtime':{
        'pro_replay_source':str(source),'pro_replay_source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}}
    request={'case':case,'artifact_root':str(exports),'task_projection':str(native_dir),
             'source_job':str(source_job),'captured_patch':str(agent/'model.patch'),
             'trial_root':str(tmp_path/'trials'),'verifier_env':{}}
    request_path=tmp_path/'request.json';request_path.write_text(json.dumps(request));result_path=tmp_path/'result.json'
    process=subprocess.run(['/tmp/hg-flash-harbor-runtime/bin/python','-I',str(ROOT/'data/scaffolds/native_harbor_runtime/harbor_entry.py'),str(request_path),str(result_path)],capture_output=True,text=True,errors='replace',timeout=100)
    assert process.returncode==0,process.stderr
    result=json.loads(result_path.read_text())
    assert result['reward']==expected
    assert result['official_evaluation_performed'] is True
    assert all(r['cleanup']['complete'] for r in result['runtime_observations'])


def test_original_jobbench_weighted_rubric_judge_with_local_scripted_provider(tmp_path):
    """Original verify.py + judge.py do all weighting, with no paid model call."""
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import threading
    source=Path('/tmp/flash-native-upstream/jobbench')
    required=[source/'harbor/runtime/verify.py',source/'eval/judge.py']
    if not all(p.is_file() for p in required):pytest.skip('pinned original JobBench verifier not acquired')
    pinned=json.loads((ROOT/'data/scaffolds/native_harbor_runtime/PROVENANCE.json').read_text())
    for path in required:
        assert hashlib.sha256(path.read_bytes()).hexdigest()==pinned['jobbench_verifier_sha256'][path.relative_to(source).as_posix()]
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append(body)
            text=json.dumps(body['messages'])
            passed='PASS_RUBRIC' in text
            verdict={'criteria_results':[{'index':0,'passed':passed,'reasoning':'scripted control','evidence':'artifact'}],
                     'rubric_passed':passed,'overall_reasoning':'local protocol control'}
            output={'id':'local-judge','object':'chat.completion','created':0,'model':body['model'],
                    'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(verdict)},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
            raw=json.dumps(output).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    output=tmp_path/'output';output.mkdir();(output/'result.txt').write_text('native final file')
    rubrics=tmp_path/'RUBRICS.json';rubrics.write_text(json.dumps({'rubrics':[
        {'rubric':'PASS_RUBRIC','weight':3,'criterion':['Has evidence']},
        {'rubric':'FAIL_RUBRIC','weight':1,'criterion':['Has missing feature']}]}))
    env={k:v for k,v in os.environ.items() if not k.startswith(('HG_AGENT_','HG_METHOD_','HG_ENV_'))}
    env.update(JUDGE_API_BASE=f'http://127.0.0.1:{server.server_port}/v1',JUDGE_API_KEY='local-control',
               JUDGE_MODEL='local-scripted-judge',JUDGE_MAX_WORKERS='1',JUDGE_MAX_RETRIES='1')
    try:
        process=subprocess.run([sys.executable,str(required[0]),'--output-dir',str(output),'--rubrics-file',str(rubrics),
                                '--judge-path',str(required[1]),'--logs-dir',str(tmp_path/'logs')],env=env,
                               capture_output=True,text=True,errors='replace',timeout=30)
    finally:server.shutdown();server.server_close();thread.join()
    assert process.returncode==0,process.stderr
    reward=json.loads((tmp_path/'logs/reward.json').read_text())
    assert reward=={'reward':0.75}
    assert len(calls)==2 and {r['model'] for r in calls}=={'local-scripted-judge'}
    # This validates the author's native weighting, not an unweighted pass rate.
    assert reward['reward'] != 0.5


@pytest.mark.skipif(not Path('/tmp/hg-flash-harbor-runtime/bin/python').is_file(),reason='selected Harbor0.23 control interpreter absent')
@pytest.mark.parametrize('answer,expected',[('1',1.0),('0',0.0)])
def test_hg_sandbox_relay_collect_and_native_fresh_deepswe_verifier(tmp_path,monkeypatch,answer,expected):
    from eval.ckpt.git_state import stage_workspace
    from eval.run.runner import run_one
    from data.scaffolds.native_harbor_runtime import verify as native_verify
    exported_patches=[]
    original_export=native_verify.export_artifacts
    def capture_export(payload,root):
        files=original_export(payload,root)
        exported_patches.append((root/'files/logs/artifacts/model.patch').read_text())
        return files
    monkeypatch.setattr(native_verify,'export_artifacts',capture_export)
    source=tmp_path/'native/control';(source/'tests').mkdir(parents=True);(source/'environment').mkdir()
    (source/'instruction.md').write_text('Commit a change to value.txt that makes its value 1.')
    actor_dir=tmp_path/'actor-build';actor_dir.mkdir()
    (actor_dir/'Dockerfile').write_text('''FROM python:3.12-bookworm
RUN mkdir -p /app/.logs/artifacts /app/.logs/agent && ln -s /app/.logs /logs && cd /app && git init -q && git config user.name control && git config user.email control@local && printf '0\\n' > value.txt && git add value.txt && git commit -qm base && git tag native-base
WORKDIR /app
''')
    image=subprocess.check_output(['docker','build','--network=none','-q',str(actor_dir)],text=True).strip()
    verifier_dir=tmp_path/'verifier-build';verifier_dir.mkdir()
    (verifier_dir/'Dockerfile').write_text('FROM python:3.12-bookworm\nRUN mkdir -p /tests\nCOPY test.sh /tests/test.sh\n')
    (verifier_dir/'test.sh').write_text('''#!/bin/bash
set -eu
mkdir -p /logs/verifier
python3 - <<'EOF'
import pathlib
p=pathlib.Path('/logs/artifacts/model.patch');s=p.read_text() if p.exists() else ''
pathlib.Path('/logs/verifier/reward.txt').write_text(str(int('+1' in s and '-0' in s)))
EOF
''')
    verifier_image=subprocess.check_output(['docker','build','--network=none','-q',str(verifier_dir)],text=True).strip()
    collect='cd /app && mkdir -p /logs/artifacts && git diff --binary native-base HEAD > /logs/artifacts/model.patch'
    (source/'task.toml').write_text(f'''schema_version="1.4"
artifacts=["/logs/artifacts/model.patch"]
[task]
name="deepswe/control"
[agent]
network_mode="no-network"
timeout_sec=30
[environment]
docker_image="{image}"
workdir="/app"
cpus=1
memory_mb=256
[verifier]
network_mode="no-network"
environment_mode="separate"
timeout_sec=90
[[verifier.collect]]
command={json.dumps(collect)}
timeout_sec=5
[verifier.environment]
docker_image="{verifier_image}"
network_mode="no-network"
cpus=1
memory_mb=256
''')
    (source/'tests/test.sh').write_text('native private verifier is image-only; never supplied to actor')
    row=native.inventory(tmp_path/'native','deepswe')[0]
    actor={'kind':'exec','backend':'observed-v1','image':image,'python':'/usr/local/bin/python3','workdir':'/app','image_workspace':'/app','network':'none','cpus':1,'memory_mb':256,'pids':64,'filesystems':{p:{'bytes':64<<20,'inodes':16384} for p in ('/app','/tmp','/dev','/dev/shm')}}
    config={'source_sha256':row.source_sha256,'actor':actor,'replay_actor_image':image,
            'verifier_image':verifier_image,'harbor_python':'/tmp/hg-flash-harbor-runtime/bin/python'}
    monkeypatch.setattr(native,'runtime',lambda row:config)
    candidate=tmp_path/'candidate';candidate.mkdir()
    (candidate/'harness.json').write_text(json.dumps({'name':'native-protocol-control','version':'1','entrypoint':'agent.py','env_kinds':['files']}))
    command=f"printf '{answer}\\n' > value.txt; git add value.txt; git -c user.name=control -c user.email=control@local commit --allow-empty -qm edit; test ! -e /tests"
    (candidate/'agent.py').write_text('''import argparse,json,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args();w=Path(a.workdir)
'''+f"r=subprocess.run([sys.executable,str(w/'native.py'),'exec',{command!r}],capture_output=True,text=True); assert r.returncode==0,r.stderr\n"+'''r=subprocess.run([sys.executable,str(w/'native.py'),'finish'],capture_output=True,text=True);assert r.returncode==0,r.stderr
(w/'trace.jsonl').write_text(json.dumps({'usage':{'calls':0}})+'\\n')
''')
    work=tmp_path/'work';repo=work/'r/workspace';stage_workspace(candidate,repo)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT',str(work));monkeypatch.setenv('HG_AGENT_BACKEND','mock')
    result=run_one(repo,native.public_task(row),sandbox=True,setup=native.setup(row),verifier=native.verifier(row),
                   env={'kind':'files','network':'none'},timeout_s=40,verify_timeout_s=120,run_id='native-control',trial_id='trial-1')
    assert not result.get('invalid'),result
    assert result['exit_code']==0,result.get('stderr')
    assert exported_patches and (('+1' in exported_patches[0]) == (answer=='1')),exported_patches
    assert result['verdict']['score']==expected,json.dumps(result['verdict'],indent=2)
    assert result['verdict']['evidence']['measured'] is True
    assert result['verdict']['native_harbor']['official_evaluation_performed']
    assert all(r['cleanup']['complete'] for r in result['verdict']['native_harbor']['runtime_observations'])
