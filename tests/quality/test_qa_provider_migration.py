"""Authored Parquet controls for TriviaQA/HotpotQA with an empty platform venv.

Only the trusted fixture writer/provider imports Arrow. The actual MiniAgent
uses cached wheels and MockTransport; no released tasks or model are evaluated.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import venv

import pytest

from harnessgrad import config, dataset_runtime
from harnessgrad.python_artifacts import remove_instance
from test_miniagent_harness import managed_miniagent, miniagent_store  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('triviaqa', 'hotpotqa')
GUARD = '''
import importlib.abc, importlib.util, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
for name in ('pip','pyarrow','openai','httpx','yaml','requests'):
    assert importlib.util.find_spec(name) is None, name + ' entered platform'
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('data.datasets.gsm8k','data.datasets.triviaqa','data.datasets.hotpotqa') or fullname.split('.')[0] == 'pyarrow':
            raise AssertionError('platform imported provider-owned module: ' + fullname)
sys.meta_path.insert(0, Guard())
def assert_platform_imports():
    assert not any(name in sys.modules for name in ('pyarrow','data.datasets.gsm8k','data.datasets.triviaqa','data.datasets.hotpotqa'))
'''


@pytest.fixture(scope='module')
def qa_platform(tmp_path_factory):
    if not shutil.which('uv'):
        pytest.skip('QA provider controls require uv and cached pyarrow 23 wheels')
    root = Path(tempfile.mkdtemp(prefix='hg-qa-provider-test-', dir=Path.home() / '.cache'))
    directory = tmp_path_factory.mktemp('authored-qa')
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv('HG_REVIEWED_ENVIRONMENT_ONLY', '1')
            patch.setenv('UV_OFFLINE', '1')
            patch.setenv('UV_PYTHON_DOWNLOADS', 'never')
            config.reset()
            try:
                runtimes = {name: dataset_runtime.prepare(name, root=root / 'providers', timeout_s=120)
                            for name in (*NAMES, 'gsm8k')}
            except (dataset_runtime.DatasetRuntimeUnavailable, ValueError) as exc:
                if any(text in str(exc) for text in ('not found in the cache', 'not cached', 'No interpreter found')):
                    pytest.skip('QA controls need installed CPython 3.12 and cached pyarrow 23: ' + str(exc))
                raise
        platform = root / 'platform'
        venv.EnvBuilder(with_pip=False, system_site_packages=False, symlinks=True).create(platform)
        # Only this trusted fixture writer enters an Arrow environment. The
        # platform and actor remain unable to import the loader dependency.
        writer = r'''
import sys
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
root = Path(sys.argv[1])
for split, count in (('validation',4),('train',4003)):
    path=root/'triviaqa/rc.nocontext';path.mkdir(parents=True,exist_ok=True)
    rows=[{'question':f'Authored TriviaQA {split} row {i}.',
           'answer':{'value':f'Authored canonical {i}',
                     'normalized_aliases':['miniagent mock',f'authored canonical {i}',f'AUTHORED_PRIVATE_ALIAS_{split}_{i}']}}
          for i in range(count)]
    pq.write_table(pa.Table.from_pylist(rows),path/f'{split}-00000-of-00001.parquet')
path=root/'hotpotqa/fullwiki';path.mkdir(parents=True)
rows=[{'question':f'Authored HotpotQA row {i}.','answer':'MINIAGENT_MOCK',
       'supporting_facts':{'title':[f'AUTHORED_PRIVATE_EVIDENCE_{i}'],'sent_id':[i]}}
      for i in range(8)]
pq.write_table(pa.Table.from_pylist(rows),path/'validation-00000-of-00001.parquet')
path=root/'gsm8k/main';path.mkdir(parents=True)
for split in ('train','test'):
    pq.write_table(pa.Table.from_pylist([{'question':'Authored two plus two?',
        'answer':'AUTHORED_PRIVATE_REASONING\n#### 4'}]),path/f'{split}-00000-of-00001.parquet')
'''
        result = subprocess.run([runtimes['triviaqa']['python'],'-I','-B','-c',writer,
                                 str(directory / 'datasets')], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        environment = {key:value for key,value in os.environ.items()
                       if not key.startswith(('HG_', 'HARNESSGRAD_', 'PYTHON'))}
        environment.update(PATH=str(platform/'bin') + os.pathsep + os.environ['PATH'],
            HG_REVIEWED_ENVIRONMENT_ONLY='1', HG_DATASET_RUNTIME_ROOT=str(root/'providers'),
            HG_DATA_ROOT=str(directory/'datasets'), HG_AGENT_BACKEND='mock',
            UV_OFFLINE='1', UV_PYTHON_DOWNLOADS='never', PYTHONDONTWRITEBYTECODE='1')
        (directory/'provider-runtimes.json').write_text(json.dumps(runtimes, indent=2))
        yield SimpleNamespace(root=root, directory=directory, env=environment,
                              python=platform/'bin/python', runtimes=runtimes)
    finally:
        config.reset()
        remove_instance(root)


def run(platform, code, *args, extra_env=None, label='control'):
    result = subprocess.run([str(platform.python),'-I','-B','-c',GUARD + code,str(ROOT),*map(str,args)],
        env={**platform.env, **(extra_env or {})}, cwd=platform.directory,
        capture_output=True, text=True, errors='replace', timeout=180)
    (platform.directory / (label + '.log')).write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize('name', NAMES)
def test_provider_keeps_rows_aliases_evidence_and_selected_declarations_private(qa_platform, name):
    result = run(qa_platform, '''
from data.scaffolds import registry
name=sys.argv[2]
with registry.provider_session():
    tasks,scorable,split=registry.load_split(name)
    runtime=registry.provider_runtime(name)
    if name=='triviaqa':
        want=[f'triviaqa/train/{i * 4003 // 2000}' for i in range(2000)]
        assert split == {'train':want,'eval':[f'triviaqa/validation/{i}' for i in range(4)]}
        assert registry.official_split(name,tasks)==split
        selected=want[-1]
        expected_private='AUTHORED_PRIVATE_ALIAS_train_4000'
        assert len(tasks)==2004 and scorable[selected]=='Authored canonical 4000'
    else:
        assert split is None and len(tasks)==8
        selected='hotpotqa/validation/0'
        expected_private='AUTHORED_PRIVATE_EVIDENCE_0'
    chosen,setups,verifiers=registry.load_tasks(name,task_ids=[selected])
    envs=registry.load_envs(name,task_ids=[selected])
    assert len(chosen)==1 and chosen[0]['task_id']==selected
    assert set(setups)==set(verifiers)==set(envs)=={selected}
    assert 'AUTHORED_PRIVATE_' not in json.dumps(tasks)+json.dumps(setups)
    gold=next(item for item in verifiers[selected]['inputs'] if item['path']=='gold.json')
    assert expected_private in gold['content']
    assert Path(runtime['python']) != Path(sys.executable)
    assert runtime['runtime_id'] == registry.provider_runtime(name)['runtime_id']
assert_platform_imports()
print(json.dumps({'dataset':name,'tasks':len(tasks),'selected':selected,'runtime_id':runtime['runtime_id']}))
''', name, label='catalog-' + name)
    assert json.loads(result.stdout)['runtime_id'] == qa_platform.runtimes[name]['runtime_id']


@pytest.mark.parametrize('name', NAMES)
def test_unprepared_provider_never_falls_back_to_the_empty_platform(qa_platform, tmp_path, name):
    store = tmp_path/'unprepared'
    run(qa_platform, '''
from data.scaffolds import registry
from harnessgrad.dataset_provider import DatasetProvider
from harnessgrad.dataset_runtime import DatasetRuntimeUnavailable
name,store=sys.argv[2:4]
state=DatasetProvider(name).check()
assert not state['present'] and state['scope']=='dataset-provider-runtime',state
assert f'prepare_dataset.py --dataset {name}' in state['detail']
try:
    registry.load(name)
except DatasetRuntimeUnavailable:
    pass
else:
    raise AssertionError('unprepared provider fell back to platform')
assert not Path(store).exists(), 'read-only discovery created runtime storage'
assert_platform_imports()
''', name, store, extra_env={'HG_DATASET_RUNTIME_ROOT':str(store)}, label='unprepared-' + name)


@pytest.mark.parametrize('name', NAMES)
def test_static_tools_and_dependency_checks_use_the_provider_environment(qa_platform, name):
    run(qa_platform, '''
from tools import datasets,datasets_doc,prepare_dataset,dataset_lint
name=sys.argv[2]
module=datasets._download_adapter(name)
entries=module.downloads()
package=next(item for item in entries if item['kind']=='pip')
assert package['package']=='pyarrow' and package['version']=='23.0.0'
assert f'prepare_dataset.py --dataset {name}' in package['note']
checked=datasets.check_dataset(name,entries)
assert next(item for item in checked['entries'] if item['kind']=='pip')['present']
assert not checked['data_present'], 'authored Parquet must not match released-data hashes'
assert 'prepare_dataset.py' in datasets_doc._provision(name,datasets_doc._import_adapter(name),refused=False)
assert prepare_dataset.main(['--dataset',name,'--check','--json'])==0
assert dataset_lint.main(['--dataset',name,'--inspect','--json'])==0
assert_platform_imports()
''', name, label='tools-' + name)


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('failure', ['missing', 'corrupt'])
def test_missing_data_and_corrupt_data_keep_distinct_readiness_states(qa_platform, tmp_path, name, failure):
    path = tmp_path / 'bad-data'
    folder = path / ('rc.nocontext' if name == 'triviaqa' else 'fullwiki')
    folder.mkdir(parents=True)
    if failure == 'corrupt':
        (folder/'validation-00000-of-00001.parquet').write_bytes(b'authored corrupt Parquet')
    run(qa_platform, '''
from harnessgrad.dataset_provider import DatasetProvider
from tools import dataset_lint
name,failure=sys.argv[2:4]
state=DatasetProvider(name).check()
assert not state['present'] and state['status']==('data_missing' if failure=='missing' else 'invalid'),state
status=dataset_lint.main(['--dataset',name,'--json','--allow-unprovisioned'])
assert status == (0 if failure=='missing' else 1),status
assert_platform_imports()
''', name, failure, extra_env={'HG_DATA_' + name.upper():str(path)}, label=name + '-' + failure)


@pytest.mark.parametrize('name', NAMES)
def test_miniagent_scores_authored_parquet_without_platform_loader_or_sdk(
        qa_platform, name, tmp_path, managed_miniagent):
    if not shutil.which('bwrap'):
        pytest.skip('combined MiniAgent control requires bubblewrap')
    source = tmp_path/'harness'
    shutil.copytree(ROOT/'base_harness/miniagent', source)
    tool = source/'tools/shell.py'
    probe = '''import importlib.util, sys
    facts={'arrow_visible':importlib.util.find_spec('pyarrow') is not None,
           'provider_visible':Path(PROVIDER_PATH).exists(),
           'private_visible':(ctx.working_dir/'gold.json').exists(), 'python':sys.executable}
    with (ctx.working_dir/'trace.jsonl').open('a') as trace:
        trace.write(json.dumps({'qa_runtime_control':facts})+'\\n')
    command = args.get("command")'''.replace('PROVIDER_PATH', repr(qa_platform.runtimes[name]['path']))
    tool.write_text(tool.read_text().replace('command = args.get("command")', probe))
    run(qa_platform, '''
from data.scaffolds import registry
import driver
name,output,actor_source=sys.argv[2:5]
tasks,_,split=registry.load_split(name)
chosen=(split or registry.default_split(tasks))['train'][0]
sys.argv=['driver.py','--dataset',name,'--harness-path',actor_source,'--tasks',chosen,
          '--split','default' if name=='hotpotqa' else 'declared',
          '--side','train','--sampling','all','--rounds','0','--improver','none',
          '--jobs','1','--trials','1','--no-summary','--fail-on-incomplete',
          '--run-id','combined-'+name,'--runs-root',str(Path(output)/'runs'),
          '--work-root',str(Path(output)/'work')]
assert driver.main()==0
assert_platform_imports()
''', name, tmp_path, source,
        extra_env={'HG_HARNESS_RUNTIME_ROOT':str(managed_miniagent.root/'store'),
                   'HG_AGENT_MODEL':'miniagent-mock','HG_AGENT_MAX_STEPS':'3'}, label='driver-' + name)
    directory = tmp_path/'runs'/('combined-' + name)
    point = json.loads((directory/'curve.jsonl').read_text())
    assert list(point['per_task'].values()) == [1.0]
    assert point['n_invalid'] == point['n_harness_failed'] == 0
    assert point['dataset_runtime_identity']['provider']['runtime_id'] == qa_platform.runtimes[name]['runtime_id']
    assert point['harness_runtime_identity']['artifact'] == managed_miniagent.binding['artifact']
    assert point['cost']['harness_model_calls'] == 2
    facts = [json.loads(line)['qa_runtime_control'] for path in (directory/'progress').rglob('*.jsonl')
             for line in path.read_text().splitlines() if 'qa_runtime_control' in json.loads(line)]
    assert len(facts) == 1
    assert facts[0] == {'arrow_visible':False, 'provider_visible':False, 'private_visible':False,
                        'python':managed_miniagent.binding['artifact']['python']}
