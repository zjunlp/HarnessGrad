"""Installed source-owned resources; no benchmark data, model, container or download."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[2]
RESOURCES = {
    'costbench': ['source-files.json', 'README.md', 'LIFECYCLE_CONTROL.md'],
    'costbench_dynamic': ['README.md'],
    'ds1000': ['execution-runtime.json', 'requirements.lock',
                'cpu-wheels-cp312-linux-x86_64.json', 'README.md'],
    'strongreject': ['specs/sources.json', 'specs/LICENSE.upstream'],
    'personamem_v2': ['cli/personamem', 'specs/source.json', 'specs/scorer.json', 'README.md'],
    'memoryagentbench': ['cli/mab', 'specs/chunk-policy.json', 'specs/frozen-episodes.json',
                        'specs/resources.lock.json', 'specs/source.json', 'specs/templates.json',
                        'specs/judge-prompts.json', 'licenses/MemoryAgentBench-MIT.txt',
                        'specs/tiktoken-LICENSE.txt', 'licenses/source.json',
                        'requirements-prepare.txt', 'requirements-differential.txt',
                        'TOKENIZER_ACCEPTANCE.md'],
    'kumo': ['source-files.json', 'vendor/LICENSE', 'vendor/PROVENANCE.json', 'README.md'],
    'cooperbench': ['recipes.json', 'hf_mirror/python', 'vendor/LICENSE'],
    'deepswe': ['SOURCE.json'], 'jobbench': ['SOURCE.json'], 'swe_bench_pro': ['SOURCE.json'],
    'agentdojo': ['cli/dojo', 'execution-runtime.json', 'execution-requirements.lock',
                  'source-manifest.json', 'prepared-manifest.json', 'README.md'], 'appworld': ['cli/aw'], 'bird_minidev': ['cli/bird', 'execution-runtime.json', 'scoring-requirements.txt'],
    'gaia': ['cli/ask', 'PREPARATION.md'], 'alfworld': ['cli/alf', 'execution-runtime.json', 'execution-requirements.in',
                 'execution-requirements.txt', 'execution-recipe.md',
                 'native-source-version-evidence.json'],
    'jericho': ['cli/jer', 'cli/jer-relay', 'runtime.json', 'execution-runtime.json',
                'requirements.lock', 'LICENSE.upstream', 'RUNTIME_PROVENANCE.json',
                'ISOLATION_STATUS.md', 'AUTHORED_STORY_LICENSE', 'NATIVE_CONTROL.md'],
    'chessbench_puzzles': ['runtime.json', 'execution-runtime.json', 'requirements-load.txt', 'LOADER_RUNTIME.md',
                           'NATIVE_RUNTIME.md'],
    'reasoning_gym': ['execution-runtime.json', 'requirements.lock', 'LOADER_RUNTIME.md',
                      'SOURCE_LICENSE_AUDIT.json', 'SOURCE_LICENSE_AUDIT.md'],
    'tau_bench': ['PROVENANCE.json', 'PREPARED.md'],
    'native_qa': ['execution-runtime.json', 'requirements.lock', 'README.md'],
    'spreadsheetbench': ['execution-runtime.json', 'requirements.lock', 'runtime.md',
                         'w-spreadcache.md', 'differential.md'],
    'spreadsheetbench2': ['execution-runtime.json', 'requirements.lock', 'LOADER_RUNTIME.md',
                          'requirements.txt', 'PROVENANCE.json'],
    'tau2_bench': ['execution-runtime.json', 'execution-requirements.lock',
                   'requirements.lock', 'LICENSE.harbor', 'manifest.json', 'README.md', 'REAL_PACKAGE_CONTROLS.md'],
    'tau3_bench': ['execution-runtime.json', 'requirements.lock', 'LICENSE.harbor',
                   'specs/task-splits.json', 'README.md'],
    'scienceworld': ['cli/scw', 'cli/scw-relay', 'cli/scw_client.py', 'oracle_worker.py', 'test_fold.json',
                     'study_300.json', 'execution-runtime.json', 'requirements.lock',
                     'ISOLATION_STATUS.md'],
    'harvey_lab': ['LICENSE.upstream', 'README.md', 'execution-runtime.json',
                   'execution-requirements.in', 'execution-requirements.lock', 'execution-recipe.md'],
    'swe_bench': ['execution-runtime.json', 'execution-requirements.in',
                  'execution-requirements.lock', 'execution-recipe.md'],
    'travelplanner': ['execution-runtime.json', 'requirements.in', 'requirements.lock', 'runtime.md'],
    'workbench': ['execution-runtime.json', 'requirements.in', 'requirements.lock', 'runtime.md'],
    'widesearch': ['execution-runtime.json', 'requirements.in', 'requirements.lock', 'runtime.md'],
}
ORACLES = ('alfworld', 'appworld', 'costbench', 'jericho', 'kumo', 'memoryagentbench',
           'personamem_v2', 'scienceworld', 'spreadsheetbench', 'tau2_bench', 'tau3_bench')
NATIVE = ('alfworld', 'bird_minidev', 'jericho', 'scienceworld', 'chessbench_puzzles')
AUTHORED_EXAMPLE = (
    'authored/example-fix-bug/bundle.json',
    'authored/example-fix-bug/materials/t01/broken.py',
    'authored/example-fix-bug/materials/t01/test_broken.py',
    'authored/example-fix-bug/oracle/t01.sh',
)


def test_reviewed_resource_allowlist_and_runtime_references():
    declarations = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['setuptools']['package-data']
    for dataset, resources in RESOURCES.items():
        declared = declarations[f'data.datasets.{dataset}']
        assert not any(token in value for value in declared for token in ('*', '?', '['))
        for resource in resources:
            assert resource in declared, (dataset, resource)
            assert (ROOT / 'data/datasets' / dataset / resource).is_file()
        for descriptor in ('runtime.json', 'execution-runtime.json'):
            if descriptor in resources:
                value = json.loads((ROOT / 'data/datasets' / dataset / descriptor).read_text())
                requirements = value['requirements']
                assert requirements in declared, (dataset, requirements)
    assert declarations['data.datasets.tau_bench'] == ['PROVENANCE.json', 'PREPARED.md']
    assert set(AUTHORED_EXAMPLE) <= set(declarations['data'])
    assert not any(token in path for path in declarations['data'] for token in ('*', '?', '['))


def test_installed_adapter_resource_readers_without_checkout(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    prefixes = ('console/', 'harnessgrad/', 'eval/', 'data/', 'methods/', 'tools/',
                'scenarios/', 'base_harness/', 'experiments/')
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    excluded_parts = {'gt', 'raw', 'gold', 'examples', 'prepared', 'prepared-v1'}
    for name in tracked:
        if (not name or excluded_parts.intersection(Path(name).parts)
                or name.startswith(('data/datasets/tau_bench/db/',
                                    'data/datasets/tau_bench/policy/'))):
            continue
        original = ROOT / name
        if ((name in {'pyproject.toml', 'README.md', 'LICENSE', 'driver.py'} or name.startswith(prefixes))
                and original.is_file()):
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
    # Never copy the real tracked Tau-bench GT into the build fixture. A planted
    # authored sentinel still proves that a broader package-data glob would leak it.
    sentinels = ('data/datasets/tau_bench/gt/private-case.json',
                 'data/datasets/tau_bench/db/private-case.json',
                 'data/datasets/tau_bench/policy/private-case.txt',
                 'data/datasets/costbench/local-private.json',
                 'data/datasets/memoryagentbench/specs/local-private.json',
                 'data/datasets/personamem_v2/raw/local-private.json',
                 'data/authored/example-fix-bug/local-private.json',
                 'data/authored/unreviewed-bundle/private.py')
    for name in sentinels:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"private":"authored sentinel; never distribute"}\n')
    assert list((source / 'data/datasets/tau_bench/gt').iterdir()) == [source / sentinels[0]]

    home = tmp_path / 'home'; home.mkdir()
    env = {'HOME': str(home), 'PATH': os.defpath, 'PYTHONNOUSERSITE': '1',
           'PYTHONDONTWRITEBYTECODE': '1', 'PIP_CONFIG_FILE': os.devnull, 'PIP_NO_INDEX': '1',
           'HG_REVIEWED_ENVIRONMENT_ONLY': '1', 'HG_DATA_ROOT': str(tmp_path / 'absent-data')}

    def run(argv):
        result = subprocess.run([str(v) for v in argv], cwd=tmp_path, env=env,
                                text=True, capture_output=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    wheels = tmp_path / 'wheels'
    run([sys.executable, '-m', 'pip', 'wheel', '--no-index', '--no-deps',
         '--no-build-isolation', '--wheel-dir', wheels, source])
    wheel, = wheels.glob('*.whl')
    expected = {f'data/datasets/{dataset}/{relative}'
                for dataset, paths in RESOURCES.items() for relative in paths}
    expected.update(f'data/datasets/{dataset}/oracle_harness/harness.json' for dataset in ORACLES)
    expected.update(f'data/runtimes/{dataset}-native.json' for dataset in NATIVE)
    expected.update(f'data/{path}' for path in AUTHORED_EXAMPLE)
    expected.update({'base_harness/deepseek-harness/dsh_runtime.py',
                     'data/datasets/jericho/authored_story.py',
                     'tools/check_costbench_lifecycle.py', 'tools/assets/loop-0.2.0.zip',
                     'tools/prepare_chessbench_native.py'})
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata, = (name for name in names if name.endswith('.dist-info/METADATA'))
        assert (ROOT / 'README.md').read_text().strip() in archive.read(metadata).decode()
        assert expected <= names, sorted(expected - names)
        for name in expected:
            assert archive.read(name) == (ROOT / name).read_bytes(), name
            if '/cli/' in name or name.endswith(('/hf_mirror/python', '/oracle/t01.sh')):
                assert (archive.getinfo(name).external_attr >> 16) & 0o111 == (ROOT / name).stat().st_mode & 0o111
        assert not names.intersection(sentinels)
        assert not any(name.startswith(tuple(f'data/datasets/tau_bench/{part}/'
                                            for part in ('gt', 'db', 'policy'))) for name in names)
        assert not any('/raw/' in name or '/gold/' in name or '/examples/' in name for name in names)

    runtime = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(runtime)
    python = runtime / 'bin/python'
    run([python, '-m', 'pip', 'install', '--no-index', '--no-deps', wheel])
    shutil.rmtree(source)
    env['RESOURCE_TEST_VENV'] = str(runtime)
    # Only source-owned metadata/readers and authored local operations are exercised.
    # No pool loader or real external source is invoked, even if another host has data.
    code = r'''
import hashlib, importlib.util, json, os, runpy, shutil, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('this installed resource control must not access a network')

class NoNetworkSocket(socket.socket):
    connect = forbidden
    connect_ex = forbidden

socket.socket = NoNetworkSocket
socket.create_connection = forbidden
from data.datasets import strongreject, memoryagentbench, personamem_v2
installed = Path(os.environ['RESOURCE_TEST_VENV']).resolve()
root = Path(strongreject.__file__).resolve().parents[2]
assert root.is_relative_to(installed)
from base_harness.openclaw import task_gateway
gateway_config = {}
task_gateway.configure(gateway_config, profile='entries')
plugin_path, = gateway_config['plugins']['load']['paths']
plugin = Path(plugin_path)
assert plugin.is_relative_to(installed), plugin
assert plugin.is_file(), plugin
from tools import loop_releases
legacy_loop = loop_releases.install('0.2.0', Path.cwd()/'installed-loop-sources')
assert json.loads((legacy_loop/'harness.json').read_text())['version'] == '0.2.0'
assert {p.name for p in legacy_loop.iterdir()} == loop_releases.LEGACY_FILES
assert loop_releases.ARCHIVE.is_relative_to(installed)
from data.scaffolds import registry
tasks, setups, verifiers = registry.load_tasks('authored_example_fix_bug')
assert [task['task_id'] for task in tasks] == ['t01']
assert {item['path'] for item in setups['t01']['files']} == {'broken.py', 'test_broken.py'}
assert verifiers['t01']['reward_file'] == '/verifier/reward.txt'
from tools import datasets as provisioning
import contextlib, io
catalog_output = io.StringIO()
with contextlib.redirect_stdout(catalog_output):
    catalog_code = provisioning.main(['--check', '--json'])
catalog = json.loads(catalog_output.getvalue())['datasets']
assert catalog_code == 1  # External data and component artifacts were not provisioned.
assert len(catalog) == len({row['dataset'] for row in catalog}) == 56
example = next(row for row in catalog if row['dataset'] == 'authored_example_fix_bug')
assert example['present'] and example['data_present']
assert not Path(os.environ['HG_DATA_ROOT']).exists()
assert strongreject.SOURCES['files']
from harnessgrad import dataset_runtime
for dataset in ('jericho', 'reasoning_gym', 'native_qa', 'ds1000', 'spreadsheetbench',
                'tau2_bench', 'tau3_bench', 'spreadsheetbench2', 'agentdojo',
                'travelplanner', 'workbench', 'widesearch', 'harvey_lab', 'swe_bench',
                'alfworld', 'scienceworld'):
    descriptor = dataset_runtime.execution_descriptor(dataset)
    assert descriptor and descriptor['requirements_sha256']
    assert Path(descriptor['requirements_path']).is_relative_to(installed)
assert dataset_runtime.descriptor('jericho')['pins']
from data.datasets.tau_bench.prepared import inventory
assert inventory()['checkout_commit']
from harnessgrad import scoring_identity
for dataset in ('spreadsheetbench', 'spreadsheetbench2', 'reasoning_gym'):
    profile = scoring_identity.resolve(dataset)
    assert profile and profile['profile']['source_sha256']
    assert scoring_identity.provider_path(dataset).is_relative_to(installed)
from data.datasets.memoryagentbench.lib.dependencies import identity
assert identity('runtime')['dependencies_match']
from harnessgrad import scoring_identity
assert scoring_identity.resolve('strongreject')['sha256']

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

bootstrap = load('installed_costbench_bootstrap', root/'data/datasets/costbench/lib/bootstrap.py')
empty = Path.cwd()/'authored-empty-bundle'; empty.mkdir()
if sys.version_info[:2] == (3, 12):
    try:
        bootstrap.bootstrap(empty)
        raise AssertionError('unprepared source accepted')
    except FileNotFoundError as exc:
        assert Path(exc.filename).is_relative_to(empty), exc
        assert Path(exc.filename).name != 'source-files.json'
else:
    try:
        bootstrap.bootstrap(empty)
        raise AssertionError('undeclared interpreter ABI accepted')
    except ValueError as exc:
        assert 'Python 3.12' in str(exc)

from data.datasets.memoryagentbench.lib import inputs, judge
for name in inputs.SPEC_SHA256:
    assert inputs.read_spec(name)
source_name = next(iter(inputs.read_spec('templates.json')['source_to_family']))
assert 'Authored question?' in inputs.render_question(source_name, 'Authored question?')
assert judge.profile(judge.SUMMARY)['prompts_sha256'] == judge.PROMPTS_SHA256
assert judge.profile(judge.LONGMEM)['prompts_sha256'] == judge.PROMPTS_SHA256
from data.datasets.personamem_v2.prepare import source_pins
pins, source_sha = source_pins()
assert pins['files'] and len(source_sha) == 64

protocol = load('installed_cooperbench_protocol', root/'data/datasets/cooperbench/lib/protocol.py')
recipes = json.loads((root/'data/datasets/cooperbench/recipes.json').read_text())
assert protocol.recipe(next(iter(recipes))) == next(iter(recipes.values()))
for name in ('deepswe', 'jobbench', 'swe_bench_pro'):
    assert json.loads((root/'data/datasets'/name/'SOURCE.json').read_text())['revision']
from data.runtimes import NATIVE_PINS
for value in NATIVE_PINS.values():
    assert hashlib.sha256((root/value['manifest']).read_bytes()).hexdigest() == value['sha256']

# Help must not contact an environment. Companion transport files are staged
# exactly as the public SETUP contract requires, without staging any private case.
stage = Path.cwd()/'authored-clients'; stage.mkdir()
for dataset, client, companion in (
        ('agentdojo','dojo',None), ('appworld','aw',None), ('bird_minidev','bird',None),
        ('gaia','ask',None), ('memoryagentbench','mab','mab_transport.py'),
        ('personamem_v2','personamem','personamem_transport.py')):
    target = stage/client
    shutil.copyfile(root/'data/datasets'/dataset/'cli'/client, target)
    if companion:
        shutil.copyfile(root/'eval/service/tool_transport.py', stage/companion)
    old_path, old_argv = list(sys.path), list(sys.argv)
    try:
        sys.path.insert(0,str(stage)); sys.argv=[str(target),'--help']
        try:
            runpy.run_path(str(target), run_name='__main__')
        except SystemExit as exc:
            assert exc.code in (0,None)
    finally:
        sys.path[:]=old_path; sys.argv[:]=old_argv
bird = runpy.run_path(str(stage/'bird'), run_name='installed_bird_client')
assert bird['main'](['submit','SELECT 1']) == 0
assert Path('answer.sql').read_text() == 'SELECT 1\n'
for module_name in ('tools.prepare_gaia', 'tools.prepare_kumo', 'tools.prepare_chessbench_native',
                    'tools.check_costbench_lifecycle',
                    'tools.prepare_ds1000_runtime',
                    'tools.prepare_costbench', 'tools.prepare_costbench_dynamic',
                    'tools.prepare_tau2_bench', 'tools.prepare_tau3_bench',
                    'tools.prepare_reasoning_gym',
                    'data.datasets.personamem_v2.prepare',
                    'data.datasets.memoryagentbench.prepare'):
    old_argv = sys.argv
    try:
        sys.argv = [module_name, '--help']
        try:
            runpy.run_module(module_name, run_name='__main__')
        except SystemExit as exc:
            assert exc.code in (0, None), module_name
    finally:
        sys.argv = old_argv
assert not Path(os.environ['HG_DATA_ROOT']).exists()
print('installed source resources and authored CLI submission verified; no task pool or service executed')
'''
    assert 'authored CLI submission verified' in run([python, '-I', '-B', '-c', code])
