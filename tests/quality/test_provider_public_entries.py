"""Public QA controls and host inventories use providers on an Arrow-free platform."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_qa_provider_migration import qa_platform, run  # noqa: F401
from test_clean_install_contract import installed  # noqa: F401

NAMES = ('gsm8k', 'triviaqa', 'hotpotqa')


@pytest.fixture(scope='module')
def installed_platform(qa_platform, installed):
    root, _, _ = installed
    wheel, = (root/'wheels').glob('harnessgrad-*.whl')
    result = subprocess.run([shutil.which('uv'),'--no-config','pip','install','--offline',
        '--no-deps','--python',str(qa_platform.python),str(wheel)], env=qa_platform.env,
        capture_output=True,text=True,timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return qa_platform


@pytest.mark.parametrize('name', NAMES)
def test_installed_qa_control_has_provider_routing_and_oracle_resources(installed_platform, tmp_path, name):
    code = '''
import importlib.abc,importlib.util,sys
from pathlib import Path
for name in ('pip','pyarrow','openai'):
    assert importlib.util.find_spec(name) is None
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.startswith('data.datasets.') or fullname=='pyarrow':
            raise AssertionError('installed control imported adapter in platform')
sys.meta_path.insert(0,Guard())
from data.scaffolds.qa import control
assert not Path(control.__file__).is_relative_to(sys.argv[1])
assert Path(control.__file__).with_name('oracle_harness').joinpath('harness.json').is_file()
for mode in ('--oracle','--noop'):
    assert control.main(['--dataset',sys.argv[2],mode,'--workdir',str(Path(sys.argv[3])/mode[2:])])==0
'''
    result = subprocess.run([str(installed_platform.python),'-I','-B','-c',code,
        str(Path(__file__).resolve().parents[2]),name,str(tmp_path)], cwd=installed_platform.directory,
        env=installed_platform.env,capture_output=True,text=True,timeout=120)
    (tmp_path/'installed-control.log').write_text(result.stdout+result.stderr)
    assert result.returncode == 0,result.stdout+result.stderr


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('oracle', [False, True], ids=['noop', 'oracle'])
@pytest.mark.parametrize('platform', [False, True], ids=['local', 'sandbox'])
def test_public_qa_control_uses_the_provider_and_keeps_reference_semantics(
        qa_platform, tmp_path, name, oracle, platform):
    result = run(qa_platform, '''
from data.scaffolds.qa import control
name,mode,platform,work=sys.argv[2:6]
args=['--dataset',name,mode,'--workdir',work]
if platform=='yes':
    args+=['--platform','--task-id',{'gsm8k':'gsm8k/test/0',
            'triviaqa':'triviaqa/validation/0','hotpotqa':'hotpotqa/validation/0'}[name]]
assert control.main(args)==0
assert_platform_imports()
''', name, '--oracle' if oracle else '--noop', 'yes' if platform else 'no', tmp_path/'episode',
        label=f'control-{name}-{oracle}-{platform}')
    if platform:
        assert f'score    {float(oracle)}' in result.stdout
    else:
        assert f'reward  {int(oracle)}' in result.stdout
        evidence = json.loads((tmp_path/'episode/verifier/evidence.json').read_text())
        assert evidence['measured']
        if oracle and name == 'hotpotqa':
            assert evidence['official']['sp_f1'] == 1


def test_public_host_discovery_includes_all_prepared_providers(qa_platform):
    run(qa_platform, '''
from data.scaffolds import registry
from tools import serve_ui,split_inventory,dataset_classes
names=('gsm8k','triviaqa','hotpotqa')
registry.published=lambda:names
serve_ui.model_presets=lambda:{}
serve_ui.improver_options=lambda:[]
serve_ui.method_catalogue=lambda:[]
expected={'gsm8k':2,'triviaqa':2004,'hotpotqa':8}
options=serve_ui.scan_options()['datasets']
assert {row['name']:row['count'] for row in options}==expected,options
for name in names:
    row=split_inventory._host_row(name)
    assert row.state==('none' if name=='hotpotqa' else 'declared'),row
    assert int(row.tasks)==expected[name],row
    classified=dataset_classes.classify(name)
    assert classified['loaded'] and classified['tasks']==expected[name],classified
    assert classified['env_kinds']==['files'] and classified['verifier_kinds']==['command'],classified
assert_platform_imports()
''', label='host-discovery')


def test_static_split_inventory_needs_neither_runtime_nor_data(qa_platform, tmp_path):
    run(qa_platform, '''
from harnessgrad import dataset_runtime
from harnessgrad.dataset_provider import DatasetProvider
from tools import split_inventory
def forbidden(*args,**kwargs):
    raise AssertionError('static inventory entered provider runtime')
dataset_runtime.resolve=forbidden
DatasetProvider.inventory=forbidden
for name in ('gsm8k','triviaqa','hotpotqa'):
    row=split_inventory.row_for(name)
    assert row.state==('none' if name=='hotpotqa' else 'declared'),row
    assert row.tasks==split_inventory.COMPUTED
assert_platform_imports()
''', extra_env={'HG_DATASET_RUNTIME_ROOT':str(tmp_path/'absent-store'),
                 'HG_DATA_ROOT':str(tmp_path/'absent-data')}, label='static-inventory')


@pytest.mark.parametrize('name', NAMES)
def test_public_entries_do_not_fall_back_when_provider_is_unprepared(qa_platform, tmp_path, name):
    run(qa_platform, '''
from data.scaffolds import registry
from data.scaffolds.qa import control
from harnessgrad.dataset_runtime import DatasetRuntimeUnavailable
from tools import split_inventory,dataset_classes
name,store=sys.argv[2:4]
try:
    control.main(['--dataset',name,'--noop'])
except DatasetRuntimeUnavailable:
    pass
else:
    raise AssertionError('control used an unprepared provider')
row=split_inventory._host_row(name)
assert row.state=='not provisioned' and 'prepare_dataset.py' in row.detail,row
row=dataset_classes.classify(name)
assert not row['loaded'] and 'DatasetRuntimeUnavailable' in row['load_error'],row
assert not Path(store).exists()
assert_platform_imports()
''', name, tmp_path/'unprepared', extra_env={'HG_DATASET_RUNTIME_ROOT':str(tmp_path/'unprepared')},
        label='missing-provider-' + name)


@pytest.mark.parametrize('name', NAMES)
def test_unknown_control_task_is_an_explicit_error_before_workdir_creation(qa_platform, tmp_path, name):
    run(qa_platform, '''
from data.scaffolds.qa import control
from harnessgrad.dataset_provider import DatasetProviderError
name,work=sys.argv[2:4]
try:
    control.main(['--dataset',name,'--oracle','--task-id',name+'/unknown/0','--workdir',work])
except DatasetProviderError as exc:
    assert exc.error_type=='ValueError' and 'unknown task id' in str(exc),str(exc)
else:
    raise AssertionError('unknown control task was accepted')
assert not Path(work).exists()
assert_platform_imports()
''', name, tmp_path/'uncreated', label='unknown-control-' + name)
