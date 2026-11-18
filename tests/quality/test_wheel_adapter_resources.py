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
import subprocess
probe = ('import sys; from harnessgrad.improvement import stages; '
         'assert not any(n == "methods" or n.startswith("methods.") for n in sys.modules)')
result = subprocess.run([sys.executable, '-c', probe], cwd=Path.cwd(),
                        env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
                        capture_output=True, text=True, timeout=20)
assert result.returncode == 0, result.stderr
from harnessgrad.improvement import Feedback, gepa_proposal
proposal = gepa_proposal('prompt.md', 'before', [Feedback('one', 'failure', 'try tests')],
                         ask=lambda prompt: {'instruction': 'after'})
assert proposal.files == {'prompt.md': 'after'} and proposal.feedback_ids == ('one',)
assert Path(sys.modules['methods.policy_ports.gepa_proposal'].__file__).resolve().is_relative_to(installed)
preflight = subprocess.run([sys.executable, '-B', '-m', 'harnessgrad.datasets.runtime_identity'],
    input='{}', cwd=Path.cwd(), env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
    capture_output=True, text=True, timeout=20)
assert preflight.returncode == 2 and not preflight.stdout and 'KeyError' in preflight.stderr
try:
    importlib.import_module('harnessgrad.dataset_runtime_identity')
except ImportError as exc:
    assert 'harnessgrad.datasets.runtime_identity' in str(exc)
else:
    raise AssertionError('removed dataset runtime identity import unexpectedly succeeded')
for name in ('dataset_classes', 'datasets_doc', 'datasets', 'prepare_dataset', 'split_inventory'):
    old = importlib.import_module('tools.' + name)
    current = importlib.import_module('tools.dataset_maintenance.' + name)
    assert old is current and Path(current.__file__).resolve().is_relative_to(installed)
    assert current.ROOT == root
for name in ('new_dataset', 'dataset_lint'):
    try:
        importlib.import_module('tools.' + name)
    except ImportError as exc:
        assert 'tools.dataset_maintenance.' + name in str(exc)
    else:
        raise AssertionError('removed maintenance import unexpectedly succeeded: ' + name)
    current = importlib.import_module('tools.dataset_maintenance.' + name)
    assert Path(current.__file__).resolve().is_relative_to(installed) and current.ROOT == root
for name, args, expected in (
    ('datasets', ['--list', '--dataset', 'gsm8k', '--json'], 0),
    ('datasets', ['--check', '--dataset', 'gsm8k'], 1),
    ('prepare_dataset', ['--dataset', 'not_a_dataset_fixture', '--runtime', 'execution',
                         '--check', '--json'], 2),
):
    results = [subprocess.run([sys.executable, '-m', module, *args], cwd=Path.cwd(),
                              env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
                              capture_output=True, text=True, timeout=30)
               for module in ('tools.' + name, 'tools.dataset_maintenance.' + name)]
    assert results[0].returncode == results[1].returncode == expected
    assert (results[0].stdout, results[0].stderr) == (results[1].stdout, results[1].stderr)
assert not Path(os.environ['HG_DATA_ROOT']).exists()
# The installed split reader must not infer a fixed value through a shadowing binding.
import tempfile
from tools.dataset_maintenance import split_inventory as split_doc
original_split_root = split_doc.ROOT
try:
    with tempfile.TemporaryDirectory(prefix='installed-split-', dir=Path.cwd()) as directory:
        split_doc.ROOT = Path(directory)
        sources = split_doc.ROOT / 'data/datasets'
        sources.mkdir(parents=True)
        source = sources / 'synthetic.py'
        source.write_text("SPLIT_SOURCE = compute()\n" +
                          "def __getattr__(name):\n    if name == 'SPLIT_SOURCE':\n        return 'none'\n")
        declaration = split_doc._source_adapter('synthetic', error=RuntimeError('unavailable'))
        assert not hasattr(declaration, 'SPLIT_SOURCE')
        assert split_doc._state(declaration, split_doc._MISSING) == 'undeclared'
        source.write_text("def __getattr__(name):\n    if name == 'SPLIT_SOURCE':\n        return 'none'\n")
        declaration = split_doc._source_adapter('synthetic', error=RuntimeError('unavailable'))
        assert declaration.SPLIT_SOURCE == 'none'
finally:
    split_doc.ROOT = original_split_root
from harnessgrad.orchestration import execution_policy, preflight, treatment_workflow
assert Path(execution_policy.__file__).resolve().is_relative_to(installed)
assert Path(preflight.__file__).resolve().is_relative_to(installed)
assert Path(treatment_workflow.__file__).resolve().is_relative_to(installed)
from harnessgrad.experiment import policies as experiment_policies
assert Path(experiment_policies.__file__).resolve().is_relative_to(installed)
assert abs(experiment_policies.geomean([1.0, .25]) - .5) < 1e-12
try:
    importlib.import_module('harnessgrad.policies')
except ImportError as exc:
    assert 'harnessgrad.experiment.policies' in str(exc)
else:
    raise AssertionError('removed policies import unexpectedly succeeded')
from harnessgrad.datasets import task_python
assert Path(task_python.__file__).resolve().is_relative_to(installed)
assert task_python.validate({'kind': 'files'}) is None
try:
    importlib.import_module('harnessgrad.task_python')
except ImportError as exc:
    assert 'harnessgrad.datasets.task_python' in str(exc)
else:
    raise AssertionError('removed task Python import unexpectedly succeeded')
from harnessgrad.core.readiness import Readiness
from harnessgrad.datasets.provisioning import normalize_provision_status
assert type(normalize_provision_status({'present': True})) is Readiness
try:
    importlib.import_module('harnessgrad.provisioning')
except ImportError as exc:
    assert 'harnessgrad.core.readiness' in str(exc) and 'harnessgrad.datasets.provisioning' in str(exc)
else:
    raise AssertionError('removed readiness import unexpectedly succeeded')
from harnessgrad.core import snapshot as core_snapshot
assert Path(core_snapshot.__file__).resolve().is_relative_to(installed)
assert core_snapshot.SCHEMA == 'harnessgrad.snapshot.v1'
try:
    importlib.import_module('harnessgrad.snapshot')
except ImportError as exc:
    assert 'harnessgrad.core.snapshot' in str(exc)
else:
    raise AssertionError('removed snapshot import unexpectedly succeeded')
from harnessgrad.core import code_metrics as core_code_metrics
assert Path(core_code_metrics.__file__).resolve().is_relative_to(installed)
assert core_code_metrics.measure(Path.cwd() / 'no-such-harness')['language'] == 'other'
try:
    importlib.import_module('harnessgrad.code_metrics')
except ImportError as exc:
    assert 'harnessgrad.core.code_metrics' in str(exc)
else:
    raise AssertionError('removed code metrics import unexpectedly succeeded')
from harnessgrad.core import components as core_components
from harnessgrad.core.harness import describe_harness
try:
    importlib.import_module('harnessgrad.components')
except ImportError as exc:
    assert 'harnessgrad.core.components' in str(exc)
else:
    raise AssertionError('removed component import unexpectedly succeeded')
assert Path(core_components.__file__).resolve().is_relative_to(installed)
assert describe_harness(root / 'base_harness' / 'codex').name == 'codex'
from harnessgrad.core.methods import discover_methods
methods = {entry.name: entry for entry in discover_methods(root / 'methods')}
assert methods['skillopt'].declared_requirements['instruction_file'] is True
assert Path(methods['skillopt'].entrypoint).resolve().is_relative_to(installed)
from harnessgrad.core.method_transport import encode_request, decode_response
from subprocess import CompletedProcess
assert encode_request({'task': 'example'}) == '{"task": "example"}'
assert decode_response(CompletedProcess([], 0, '{"changed": false}', '')) == {'changed': False}
from methods.policy_ports.skillopt_fast import propose_fast
assert Path(sys.modules[propose_fast.__module__].__file__).resolve().is_relative_to(installed)
from methods.policy_ports.skillopt_patch import apply_skill_patch
assert apply_skill_patch({'skills/main.md': 'original'}, [], 1) == {'skills/main.md': 'original'}
from methods.policy_ports.ace_proposal import propose_playbook
assert Path(sys.modules[propose_playbook.__module__].__file__).resolve().is_relative_to(installed)
from harnessgrad.improvement.contracts import Feedback as SharedFeedback
from harnessgrad.improvement.stages import skillopt_proposal, evo_harness_proposal
from harnessgrad.improvement import Feedback as PublicFeedback, skillopt_proposal as public_skillopt
from harnessgrad.customization.proposals import Feedback, propose_customization
assert SharedFeedback is PublicFeedback is Feedback and public_skillopt is skillopt_proposal
try:
    importlib.import_module('harnessgrad.customization.contracts')
except ImportError as exc:
    assert 'harnessgrad.improvement.contracts' in str(exc)
else:
    raise AssertionError('removed customization contract import unexpectedly succeeded')
shared_skill = evo_harness_proposal({}, [Feedback('one', 'success', 'already works')],
                                   ask=lambda _: (_ for _ in ()).throw(AssertionError('unexpected model call')))
assert shared_skill.files == {} and shared_skill.feedback_ids == ()
candidate = propose_customization([Feedback('one', 'failure', 'missing check')],
    skills={'skills/main.md': 'Existing'},
    ask=lambda prompt: {'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Verify.'}]})
assert 'Verify.' in candidate.files['skills/main.md']
assert candidate.stages == ('skillopt-fast',)
from dataclasses import asdict
from harnessgrad.customization.proposals import propose_with_memory
from harnessgrad.customization.review import input_digest, review_proposal
memory_feedback = [Feedback('one', 'failure', 'missing check')]
def scripted_memory(prompt):
    if prompt.startswith('[SkillOpt: failure]'):
        return {'edits': []}
    if prompt.startswith('[EvoHarness: reflect]'):
        return {'lesson': 'check', 'trigger': 'change', 'evidence': 'missing check', 'scope_hint': 'task'}
    if prompt.startswith('[EvoHarness: compile topic]'):
        return {'skills': [{'path': 'skills/verify/SKILL.md', 'name': 'verify', 'description': 'verify',
                            'source_task_ids': ['one'], 'content': 'Check.'}]}
    raise AssertionError('unexpected scripted prompt')
with_memory, memory_rows = propose_with_memory(memory_feedback, skills={'skills/main.md': 'Existing'}, ask=scripted_memory)
request = {'feedback': memory_feedback, 'skills': {'skills/main.md': 'Existing'}, 'instruction': None}
document = json.loads(json.dumps({'status': 'unevaluated', 'input_sha256': input_digest(request),
                                  'proposal': asdict(with_memory), 'memories': memory_rows}))
assert 'skills/verify/SKILL.md' in with_memory.files
assert review_proposal(request, document)['source_check'] == 'not_checked'
def scripted_refinement(prompt):
    if prompt.startswith('[ACE: reflect]'):
        return {'key_insight': 'check', 'bullet_tags': []}
    if prompt.startswith('[ACE: curate]'):
        return {'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Check.'}]}
    if prompt.startswith('[GEPA: reflect-and-propose]'):
        return {'instruction': 'Check before finishing.\n'}
    raise AssertionError('unexpected scripted prompt')
refined = propose_customization(memory_feedback, instruction=('prompt.md', 'Original\n'),
                                 ask=scripted_refinement, refine_instruction=True)
refine_request = {'feedback': memory_feedback, 'skills': None, 'instruction': ('prompt.md', 'Original\n')}
refine_doc = json.loads(json.dumps({'status': 'unevaluated', 'input_sha256': input_digest(refine_request),
                                    'proposal': asdict(refined)}))
assert refined.stages == ('ace-reflect-curate', 'gepa-reflect-propose')
assert review_proposal(refine_request, refine_doc)['changed_paths'] == ['prompt.md']
from harnessgrad.entrypoints.customization import main as customization_main
assert callable(customization_main)
from harnessgrad.customization.review import review_proposal
assert callable(review_proposal)
from harnessgrad.customization.inputs import collect_inputs
selected = Path.cwd() / 'selected-harness'
(selected / 'skills').mkdir(parents=True)
(selected / 'skills/main.md').write_text('fixture')
collected = collect_inputs(selected, ['skills/main.md'], None, [Feedback('a', 'failure', 'check')])
assert collected['skills']['skills/main.md'] == 'fixture'
from harnessgrad.customization.patch import export_patch
from harnessgrad.improvement import text_diff, index_feedback, is_relative_path, selected_surfaces
patch_request = {'feedback': [Feedback('a', 'failure', 'check')],
                 'skills': {'skills/main.md': 'fixture'}, 'instruction': None}
assert list(index_feedback(patch_request['feedback'])) == ['a']
assert is_relative_path('skills/main.md') and selected_surfaces(patch_request['skills'], None) == patch_request['skills']
patch_files = {'skills/main.md': 'updated\n'}
patch_doc = {'status': 'unevaluated', 'input_sha256': input_digest(patch_request),
             'proposal': {'files': patch_files, 'diff': text_diff(patch_request['skills'], patch_files),
                          'feedback_ids': ['a'], 'stages': ['skillopt-fast']}}
assert 'updated' in export_patch(patch_request, patch_doc, root=selected)
assert (selected / 'skills/main.md').read_text() == 'fixture'
(selected / 'harness.json').write_text(json.dumps({'name': 'fixture', 'components': {'context': ['skills/main.md']}}))
import contextlib, io
inspect_output = io.StringIO()
with contextlib.redirect_stdout(inspect_output):
    assert customization_main(['inspect', '--root', str(selected)]) == 0
assert json.loads(inspect_output.getvalue())['components'] == {'context': ['skills/main.md']}
component_review = review_proposal(patch_request, patch_doc, root=selected, include_components=True)
assert component_review['component_attribution']['components'] == ['context']
from tools.diagnostics import check_first_use, probe_tool_calls
from harnessgrad.improvers import resolver as improver_resolver
assert Path(improver_resolver.__file__).resolve().is_relative_to(installed)
assert importlib.import_module('tools.improver') is improver_resolver
assert callable(improver_resolver.resolve) and callable(improver_resolver.declared_editor_binding)
from data.scaffolds.native_research import conversion as native_conversion
assert Path(native_conversion.__file__).resolve().is_relative_to(installed)
assert importlib.import_module('tools.prepare_native_research') is native_conversion
worker = root / 'data/scaffolds/native_research/prepare_worker.py'
worker_probe = subprocess.run([sys.executable, '-I', '-B', str(worker)], input='{}',
    cwd=Path.cwd(), env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
    capture_output=True, text=True, timeout=20)
assert worker_probe.returncode != 0 and 'KeyError' in worker_probe.stderr and "'runtime'" in worker_probe.stderr
from eval.service import web_gateway
assert Path(web_gateway.__file__).resolve().is_relative_to(installed)
assert importlib.import_module('tools.web_gateway') is web_gateway
assert callable(web_gateway.WebGateway)
relay_help = subprocess.run([sys.executable, '-I', '-B',
    str(root / 'data/scaffolds/native_research/web_relay.py'), '--help'],
    cwd=Path.cwd(), env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
    capture_output=True, text=True, timeout=20)
assert relay_help.returncode == 0 and 'usage:' in relay_help.stdout.lower(), relay_help.stderr
from harnessgrad.execution import harness_validation
assert Path(harness_validation.__file__).resolve().is_relative_to(installed)
assert importlib.import_module('tools.validate_harness') is harness_validation
assert callable(harness_validation.check) and callable(harness_validation.source_identity)
from harnessgrad.execution import tool_call_probe
assert probe_tool_calls.probe is tool_call_probe
assert Path(tool_call_probe.__file__).resolve().is_relative_to(installed)
assert callable(tool_call_probe.probe_chat) and callable(tool_call_probe.probe_responses)
assert Path(check_first_use.__file__).resolve().is_relative_to(installed)
assert Path(probe_tool_calls.__file__).resolve().is_relative_to(installed)
assert callable(check_first_use.main) and callable(probe_tool_calls.main)
from tools.validation import check_trace_contract, validate_run
assert Path(check_trace_contract.__file__).resolve().is_relative_to(installed)
assert Path(validate_run.__file__).resolve().is_relative_to(installed)
assert callable(check_trace_contract.main) and callable(validate_run.main)
from tools.analysis import plot_curve as canonical_plot, results_matrix as canonical_matrix, run_summary as canonical_summary
from harnessgrad.evidence_reports import run_summary as evidence_summary
assert canonical_summary is evidence_summary
assert Path(evidence_summary.__file__).resolve().is_relative_to(installed)
assert Path(canonical_summary.__file__).resolve().is_relative_to(installed)
from tools.analysis import evidence_export as canonical_evidence_cli
assert Path(canonical_evidence_cli.__file__).resolve().is_relative_to(installed)
from tools.visualization import render_trends as trend_impl, render_demo_figure as demo_impl
assert Path(trend_impl.__file__).resolve().is_relative_to(installed)
assert Path(demo_impl.__file__).resolve().is_relative_to(installed)
import importlib
for name in ('plot_curve', 'results_matrix', 'run_summary', 'evidence_export', 'component_attribution',
             'report_appworld', 'report_livemathbench', 'tau3_report'):
    module = importlib.import_module('tools.analysis.' + name)
    assert Path(module.__file__).resolve().is_relative_to(installed)
    assert callable(runpy.run_path(str(root / 'tools/analysis' / (name + '.py')))['main'])
    assert not (root / 'tools' / (name + '.py')).exists()
for name in ('render_trends', 'render_demo_figure'):
    assert callable(runpy.run_path(str(root / 'tools/visualization' / (name + '.py')))['main'])
    assert not (root / 'tools' / (name + '.py')).exists()
for name in ('run_tests', 'run_selected', 'ci_select', 'quality_sweep'):
    new = importlib.import_module('tools.testing.' + name)
    assert Path(new.__file__).resolve().is_relative_to(installed)
    assert callable(runpy.run_path(str(root / 'tools/testing' / (name + '.py')))['main'])
    assert not (root / 'tools' / (name + '.py')).exists()
assert (root / 'tools/testing/test_costs.json').is_file()
from tools.testing import lane as testing_lane, scope as testing_scope
assert testing_lane.ROOT == testing_scope.ROOT == root
assert Path(testing_lane.__file__).resolve().is_relative_to(installed)
assert Path(testing_scope.__file__).resolve().is_relative_to(installed)
assert not (root / 'tools/test_lane.py').exists() and not (root / 'tools/test_scope.py').exists()
for name in ('check_dataset_blockers', 'apply_blocker_proposal', 'merge_blocker_registry'):
    old = importlib.import_module('tools.' + name)
    new = importlib.import_module('tools.registry_ops.' + name)
    assert old is new and Path(new.__file__).resolve().is_relative_to(installed)
    assert callable(new.main)
from tools.analysis.report_livemathbench import aggregate as gpass
assert gpass({'per_task': {'synthetic': .5}, 'per_task_trials': {'synthetic': [1, 0]},
              'n_invalid': 0}, n=2, ks=(1,))['complete']
loaded_gpass = runpy.run_path(str(root / 'tools/analysis/report_livemathbench.py'))['aggregate']
assert loaded_gpass({'per_task': {'synthetic': .5}, 'per_task_trials': {'synthetic': [1, 0]},
                     'n_invalid': 0}, n=2, ks=(1,))['complete']
for name in ('configure_harness', 'configure_codex_harness', 'configure_dsh_harness', 'configure_opencode_harness',
             'configure_openclaw_harness', 'configure_bun_harness', 'configure_responses_overlay'):
    old = importlib.import_module('tools.' + name)
    new = importlib.import_module('tools.harness_setup.' + name)
    assert old is new and Path(new.__file__).resolve().is_relative_to(installed)
    assert callable(runpy.run_path(str(root / 'tools' / (name + '.py')))['main'])
from harnessgrad.orchestration import environments as orchestration_environments
try:
    importlib.import_module('harnessgrad.environments')
except ImportError as exc:
    assert 'harnessgrad.orchestration.environments' in str(exc)
else:
    raise AssertionError('removed environments import unexpectedly succeeded')
assert Path(orchestration_environments.__file__).resolve().is_relative_to(installed)
from harnessgrad.orchestration import readiness as orchestration_readiness
try:
    importlib.import_module('harnessgrad.readiness')
except ImportError as exc:
    assert 'harnessgrad.orchestration.readiness' in str(exc)
else:
    raise AssertionError('removed readiness import unexpectedly succeeded')
assert Path(orchestration_readiness.__file__).resolve().is_relative_to(installed)
from harnessgrad.execution import method_runner
try:
    importlib.import_module('harnessgrad.methods')
except ImportError as exc:
    assert 'harnessgrad.execution.method_runner' in str(exc)
else:
    raise AssertionError('removed method runner import unexpectedly succeeded')
assert Path(method_runner.__file__).resolve().is_relative_to(installed)
from harnessgrad.provenance.source_declarations import literal_assignments
assert literal_assignments('PIN = 1', {'PIN'}) == {'PIN': 1}
assert Path(sys.modules[literal_assignments.__module__].__file__).resolve().is_relative_to(installed)
from harnessgrad.provenance import measurement_components as measurement_components_mod, scoring_identity as scoring_identity_mod
agentdojo_components = measurement_components_mod.resolve('agentdojo')
measurement_components_mod.require_scoring_alignment(scoring_identity_mod.resolve('agentdojo'), agentdojo_components)
assert agentdojo_components['data_source']['details']['dataset'] == 'agentdojo'
alfworld_components = measurement_components_mod.resolve('alfworld')
measurement_components_mod.require_scoring_alignment(scoring_identity_mod.resolve('alfworld'), alfworld_components)
assert alfworld_components['data_source']['details']['dataset'] == 'alfworld'
jericho_components = measurement_components_mod.resolve('jericho')
measurement_components_mod.require_scoring_alignment(scoring_identity_mod.resolve('jericho'), jericho_components)
assert jericho_components['data_source']['details']['dataset'] == 'jericho'
scienceworld_components = measurement_components_mod.resolve('scienceworld')
measurement_components_mod.require_scoring_alignment(scoring_identity_mod.resolve('scienceworld'), scienceworld_components)
assert scienceworld_components['data_source']['details']['dataset'] == 'scienceworld'
from harnessgrad.experiment import channel as experiment_channel
assert Path(experiment_channel.__file__).resolve().is_relative_to(installed)
try:
    importlib.import_module('harnessgrad.channel')
except ImportError as exc:
    assert 'harnessgrad.experiment.channel' in str(exc)
else:
    raise AssertionError('removed method channel import unexpectedly succeeded')
from harnessgrad.experiment.method_attempts import MethodAttempts
assert Path(sys.modules[MethodAttempts.__module__].__file__).resolve().is_relative_to(installed)
try:
    importlib.import_module('harnessgrad.method_attempts')
except ImportError as exc:
    assert 'harnessgrad.experiment.method_attempts' in str(exc)
else:
    raise AssertionError('removed method attempt import unexpectedly succeeded')
from harnessgrad.experiment import records as new_records
try:
    importlib.import_module('harnessgrad.records')
except ImportError as exc:
    assert 'harnessgrad.experiment.records' in str(exc)
else:
    raise AssertionError('removed record import unexpectedly succeeded')
assert Path(new_records.__file__).resolve().is_relative_to(installed)
from harnessgrad.experiment import config as new_experiment_config, completion as new_completion, splits as new_splits
for old, new in (('experiments','config'), ('completion','completion'), ('splits','splits')):
    try:
        importlib.import_module('harnessgrad.' + old)
    except ImportError as exc:
        assert 'harnessgrad.experiment.' + new in str(exc)
    else:
        raise AssertionError('removed experiment import unexpectedly succeeded')
assert Path(new_experiment_config.__file__).resolve().is_relative_to(installed)
assert new_experiment_config.PLATFORM_ROOT.is_relative_to(installed)
assert new_completion.describe([{'round': 0, 'score': 1, 'n_scored': 1, 'measured_by_platform': True}],
                               requested_rounds=0)['status'] == 'complete'
split_help = subprocess.run([sys.executable, '-m', 'harnessgrad.experiment.splits', '--help'], cwd=Path.cwd(),
                            env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
                            capture_output=True, text=True, timeout=20)
assert split_help.returncode == 0 and 'create' in split_help.stdout
old_split_help = subprocess.run([sys.executable, '-m', 'harnessgrad.splits', '--help'], cwd=Path.cwd(),
                                env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
                                capture_output=True, text=True, timeout=20)
assert old_split_help.returncode != 0 and 'harnessgrad.experiment.splits' in old_split_help.stderr
# Installed wheels must keep command delegates without resurrecting old import aliases.
for old, new in (
    ('runner', 'run.runner'), ('container', 'containers.container'),
    ('container_state', 'containers.container_state'), ('sandbox', 'containers.sandbox'),
    ('verifier', 'verification.verifier'), ('gaia_preflight', 'policy.gaia_preflight'),
    ('harness_runtime', 'containers.harness_runtime'), ('scorer_bundle', 'admission.scorer_bundle'),
    ('separated_stdio', 'service.separated_stdio'),
):
    try:
        importlib.import_module('eval.' + old)
    except ImportError as exc:
        assert 'eval.' + new in str(exc)
    else:
        raise AssertionError('removed evaluation alias unexpectedly succeeded: ' + old)
    implementation = importlib.import_module('eval.' + new)
    assert Path(implementation.__file__).resolve().is_relative_to(installed)
for module in ('gaia_preflight', 'harness_runtime', 'scorer_bundle', 'separated_stdio'):
    command = subprocess.run([sys.executable, '-m', 'eval.' + module, '--help'],
                             cwd=Path.cwd(), env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
                             capture_output=True, text=True, timeout=20)
    assert command.returncode == 0, (module, command.stderr)
from harnessgrad.datasets import provider as dataset_provider
from harnessgrad.datasets.provider import DatasetProvider
assert dataset_provider.ROOT == root
assert Path(sys.modules[DatasetProvider.__module__].__file__).resolve().is_relative_to(installed)
try:
    importlib.import_module('harnessgrad.dataset_provider')
except ImportError as exc:
    assert 'harnessgrad.datasets.provider' in str(exc)
else:
    raise AssertionError('removed dataset provider import unexpectedly succeeded')
from harnessgrad.execution import candidate_runtime as new_candidate_runtime
assert Path(new_candidate_runtime.__file__).resolve().is_relative_to(installed)
try:
    importlib.import_module('harnessgrad.candidate_runtime')
except ImportError as exc:
    assert 'harnessgrad.execution.candidate_runtime' in str(exc)
else:
    raise AssertionError('removed candidate runtime import unexpectedly succeeded')
from harnessgrad.evidence_reports import evidence as ev, evidence_export as exp, evidence_stats as stats, receipt_verification as receipt
for name, module in (('evidence', ev), ('evidence_export', exp), ('evidence_stats', stats), ('receipt_verification', receipt)):
    assert Path(module.__file__).resolve().is_relative_to(installed)
    try:
        importlib.import_module('harnessgrad.' + name)
    except ImportError as exc:
        assert 'harnessgrad.evidence_reports.' + name in str(exc)
    else:
        raise AssertionError('removed evidence import unexpectedly succeeded')
from harnessgrad.provenance import identity as canonical_identity, scoring_identity as canonical_scoring, verification_context as canonical_context
assert Path(canonical_identity.__file__).resolve().is_relative_to(installed)
for name in ('identity', 'h0_identity', 'scoring_identity', 'verification_context', 'native_judge_identity'):
    try:
        importlib.import_module('harnessgrad.' + name)
    except ImportError as exc:
        assert 'harnessgrad.provenance.' + name in str(exc)
    else:
        raise AssertionError('removed provenance import unexpectedly succeeded')
assert canonical_context.ROOT.is_relative_to(installed)
assert canonical_scoring.PROVIDERS.is_relative_to(installed)
from harnessgrad.provenance import native_judge_identity as judge
from data.scaffolds.native_harbor_runtime import harbor_entry
assert Path(judge.__file__).resolve().is_relative_to(installed)
assert Path(harbor_entry.__file__).resolve().is_relative_to(installed)
harbor_probe_code = ("import sys\n"
                     "sys.path.insert(0,sys.argv[1])\n"
                     "from data.scaffolds.native_harbor_runtime.python_runtime import admit\n"
                     "try:\n"
                     " admit({'harbor_python':sys.executable},python=sys.executable)\n"
                     "except ValueError as exc:\n"
                     " assert 'path-only runtime is unbound' in str(exc)\n"
                     "else:\n"
                     " raise AssertionError('unbound runtime admitted')\n"
                     "assert 'harbor' not in sys.modules\n")
harbor_probe = subprocess.run([sys.executable, '-I', '-B', '-c', harbor_probe_code, str(root)],
    cwd=Path.cwd(), env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
    capture_output=True, text=True, timeout=20)
assert harbor_probe.returncode == 0, harbor_probe.stderr

assert judge.normalize_endpoint('http://127.0.0.1:80/v1/') == 'http://127.0.0.1/v1'
from harnessgrad.execution import bubblewrap as eb, slots as es, run_namespace as en
for name, module in (('bubblewrap', eb), ('slots', es), ('run_namespace', en)):
    assert Path(module.__file__).resolve().is_relative_to(installed)
    try:
        importlib.import_module('harnessgrad.' + name)
    except ImportError as exc:
        assert 'harnessgrad.execution.' + name in str(exc)
    else:
        raise AssertionError('removed execution import unexpectedly succeeded')
from harnessgrad.execution import budget_dispatch as bd, budget_integration as bi
assert Path(bd.__file__).resolve().is_relative_to(installed)
assert Path(bi.__file__).resolve().is_relative_to(installed)
for name in ('budget_dispatch', 'budget_integration'):
    try:
        importlib.import_module('harnessgrad.' + name)
    except ImportError as exc:
        assert 'harnessgrad.execution.' + name in str(exc)
    else:
        raise AssertionError('removed budget import unexpectedly succeeded')
from harnessgrad.execution.python_runtime import python_artifacts as pa, python_harness_runtime as ph, python_exec_runtime as pe
for name, module in (('python_artifacts', pa), ('python_harness_runtime', ph), ('python_exec_runtime', pe)):
    assert Path(module.__file__).resolve().is_relative_to(installed)
    try:
        importlib.import_module('harnessgrad.' + name)
    except ImportError as exc:
        assert 'harnessgrad.execution.python_runtime.' + name in str(exc)
    else:
        raise AssertionError('removed Python runtime import unexpectedly succeeded')
assert ph.ROOT.is_relative_to(installed)
from base_harness.openclaw import task_gateway
gateway_config = {}
task_gateway.configure(gateway_config, profile='entries')
plugin_path, = gateway_config['plugins']['load']['paths']
plugin = Path(plugin_path)
assert plugin.is_relative_to(installed), plugin
assert plugin.is_file(), plugin
assert not plugin.with_name('runtime-identity.json').exists()
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
from harnessgrad.datasets import runtime as dataset_runtime
canonical_dataset_runtime = dataset_runtime
try:
    importlib.import_module('harnessgrad.dataset_runtime')
except ImportError as exc:
    assert 'harnessgrad.datasets.runtime' in str(exc)
else:
    raise AssertionError('removed dataset runtime import unexpectedly succeeded')
assert Path(canonical_dataset_runtime.__file__).resolve().is_relative_to(installed)
assert canonical_dataset_runtime.ROOT == root
reasoning_prepare = runpy.run_path(str(root / 'data/datasets/reasoning_gym/prepare.py'))
old_resolve = canonical_dataset_runtime.resolve_execution
def refuse_reasoning_runtime(name):
    raise RuntimeError('authored runtime refusal')
canonical_dataset_runtime.resolve_execution = refuse_reasoning_runtime
try:
    try:
        reasoning_prepare['build']()
    except RuntimeError as exc:
        assert 'authored runtime refusal' in str(exc)
    else:
        raise AssertionError('generation continued without its runtime')
finally:
    canonical_dataset_runtime.resolve_execution = old_resolve

from data.scaffolds import command_control
from types import SimpleNamespace
old_admit = canonical_dataset_runtime.admit_binding
def reject_authored_binding(*args, **kwargs):
    raise ValueError('authored admission refusal')
canonical_dataset_runtime.admit_binding = reject_authored_binding
try:
    fake_verifier = {'argv': ['/authored/python', '-I', '-B', 'check.py'],
                     'python_runtime': {'authored': True}, 'runtime_paths': ['/authored']}
    fake_module = SimpleNamespace(VERIFY={'authored': fake_verifier})
    try:
        command_control.run_check(fake_module, {'task_id': 'authored'}, Path.cwd())
    except ValueError as exc:
        assert 'authored admission refusal' in str(exc)
    else:
        raise AssertionError('unadmitted verifier was launched')
finally:
    canonical_dataset_runtime.admit_binding = old_admit

from data.scaffolds.spreadsheet_runtime import actor as spreadsheet_actor
from data.scaffolds import runtime_extensions
assert callable(runtime_extensions.validate_native_benchmark)
actor_root = '/authored/instances/control'
actor_runtime = {'dataset': 'spreadsheetbench', 'profile': 'execution', 'runtime_id': 'a' * 64,
                 'manifest_path': actor_root + '/artifact.json', 'path': actor_root + '/venv',
                 'python': actor_root + '/venv/bin/python', 'runtime_paths': [actor_root + '/venv']}
old_resolve = canonical_dataset_runtime.resolve_execution
canonical_dataset_runtime.resolve_execution = lambda name: actor_runtime
try:
    actor_env = spreadsheet_actor.environment('spreadsheetbench')
    assert actor_env['task_python']['role'] == 'actor'
    assert actor_env['task_python']['runtime_id'] == 'a' * 64
finally:
    canonical_dataset_runtime.resolve_execution = old_resolve
from data.scaffolds.native_research import component_runtime, special_runtime
native_runtime = {**actor_runtime, 'dataset': 'swe_bench'}
old_resolve = canonical_dataset_runtime.resolve_execution
canonical_dataset_runtime.resolve_execution = lambda name: native_runtime
try:
    native_binding = special_runtime.binding('swe_bench')
    assert native_binding['role'] == 'verifier' and native_binding['dataset'] == 'swe_bench'
finally:
    canonical_dataset_runtime.resolve_execution = old_resolve
assert callable(component_runtime.apply)

provider_bootstrap = ("import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
                      "runpy.run_module('data.scaffolds.provider_worker',run_name='__main__')")
provider_probe = subprocess.run([sys.executable, '-I', '-B', '-c', provider_bootstrap, str(root)],
    input='{}', cwd=Path.cwd(), env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
    capture_output=True, text=True, timeout=20)
assert provider_probe.returncode == 1
assert json.loads(provider_probe.stdout)['error']['detail'] == 'invalid dataset provider request'
gaia_worker = root / 'data/datasets/gaia/prepare_worker.py'
gaia_probe_code = ("import runpy,sys\n"
                   "try:\n"
                   " runpy.run_path(sys.argv[1],run_name='__main__')\n"
                   "except RuntimeError as exc:\n"
                   " assert 'runtime role binding' in str(exc)\n"
                   "else:\n"
                   " raise AssertionError('unbound runtime admitted')\n"
                   "assert 'pyarrow' not in sys.modules\n")
gaia_probe = subprocess.run([sys.executable, '-I', '-B', '-c', gaia_probe_code, str(gaia_worker)],
    input='{"binding":{},"inputs":[]}', cwd=Path.cwd(),
    env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
    capture_output=True, text=True, timeout=20)
assert gaia_probe.returncode == 0, gaia_probe.stderr
native_qa_source = Path.cwd() / 'authored-native-qa.jsonl'
native_qa_source.write_text('{"id":"authored"}\n')
native_qa_output = Path.cwd() / 'authored-native-qa-output'
native_qa_output.mkdir()
native_qa_request = {'root': str(native_qa_output), 'local': ['test=' + str(native_qa_source)], 'dataset': 'math500'}
native_qa_worker = root / 'data/scaffolds/native_qa/prepare_worker.py'
native_qa_probe = subprocess.run([sys.executable, '-I', '-B', str(native_qa_worker)],
    input=json.dumps(native_qa_request), cwd=Path.cwd(),
    env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'},
    capture_output=True, text=True, timeout=20)
assert native_qa_probe.returncode == 0, native_qa_probe.stderr
assert json.loads((native_qa_output / 'converted/00.jsonl').read_text()) == {'id': 'authored'}


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
from harnessgrad.provenance import scoring_identity
for dataset in ('spreadsheetbench', 'spreadsheetbench2', 'reasoning_gym', 'chessbench_puzzles'):
    profile = scoring_identity.resolve(dataset)
    assert profile and (profile['profile'].get('source_sha256') or profile['profile'].get('scorer_sha256'))
    assert scoring_identity.provider_path(dataset).is_relative_to(installed)
    if dataset == 'chessbench_puzzles':
        assert profile['profile']['scorer_sha256']
from harnessgrad.provenance import measurement_components
components = measurement_components.resolve('chessbench_puzzles')
assert set(components) == {'data_source', 'loader', 'scorer'}
assert components['data_source']['details']['dataset'] == 'chessbench_puzzles'
from data.datasets.memoryagentbench.lib.dependencies import identity
assert identity('runtime')['dependencies_match']
from harnessgrad.provenance import scoring_identity
strongreject_scoring = scoring_identity.resolve('strongreject')
strongreject_components = measurement_components.resolve('strongreject')
measurement_components.require_scoring_alignment(strongreject_scoring, strongreject_components)
assert strongreject_scoring['sha256']

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

costbench_execution = load('installed_costbench_execution', root/'data/datasets/costbench/execution.py')
costbench_controller = load('installed_costbench_controller', root/'data/datasets/costbench/prepare_controller.py')
selected_costbench = {**actor_runtime, 'dataset': 'costbench'}
old_resolve = canonical_dataset_runtime.resolve_execution
canonical_dataset_runtime.resolve_execution = lambda name: selected_costbench
try:
    binding = costbench_execution.binding('service')
    assert binding['dataset'] == 'costbench' and binding['role'] == 'service'
finally:
    canonical_dataset_runtime.resolve_execution = old_resolve
assert len(costbench_controller.source_hash()) == 64
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
