"""Authored Node preload controls: preparation refuses and children omit ambient loaders."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

import pytest

from base_harness.codex import codex_runtime
from methods import cli_improver
from tools import configure_codex_harness as codex_preparation

ROOT = Path(__file__).resolve().parents[2]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def canary(tmp_path):
    modules = tmp_path / 'external-modules'
    modules.mkdir()
    marker = tmp_path / 'injected.txt'
    loader = modules / 'canary.js'
    loader.write_text('require("fs").writeFileSync(' + json.dumps(str(marker)) + ', "injected");')
    return {'NODE_OPTIONS': '--require ' + shlex.quote(str(loader)), 'NODE_PATH': str(modules)}, marker


@pytest.mark.parametrize('kind', ['native', 'npm'])
@pytest.mark.parametrize('variable', ['NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH'])
def test_codex_preparation_and_resolver_refuse_loader_before_process(tmp_path, monkeypatch, canary, kind, variable):
    env, marker = canary
    env = {**env, variable: env.get(variable, '/authored/private-loader-value')}
    with tempfile.TemporaryDirectory(prefix='hg-loader-codex-', dir='/var/tmp') as directory:
        base = Path(directory)
        program = base / ('node/bin/node' if kind == 'npm' else 'codex')
        program.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile('/bin/true', program)
        program.chmod(0o755)
        argv = [str(program)]
        if kind == 'npm':
            package = base / 'package'
            package.mkdir()
            (package / 'package.json').write_text('{"name":"@openai/codex"}')
            script = package / 'codex.js'
            script.write_text('// no process is launched')
            argv.append(str(script))
        selected = codex_runtime.installation(argv)
        harness = tmp_path / 'candidate'
        harness.mkdir()
        shutil.copyfile(ROOT / 'base_harness/codex/agent.py', harness / 'agent.py')
        shutil.copyfile(ROOT / 'base_harness/codex/codex_runtime.py', harness / 'codex_runtime.py')
        manifest_path = harness / 'harness.json'
        manifest_path.write_text(json.dumps({'name': 'codex', 'entrypoint': 'agent.py',
            'runtime_paths': selected['runtime_paths'], '_codex_runtime': {
                'schema': 1, 'version': 'fixture', 'selection': argv, **selected}}))
        before = manifest_path.read_bytes()
        monkeypatch.setattr(codex_preparation, '_version', lambda *a, **k: pytest.fail('started version process'))
        with pytest.raises(ValueError) as refusal:
            codex_preparation.configure(harness, command=shlex.join(argv), env={variable: env[variable]})
        assert variable in str(refusal.value) and env[variable] not in str(refusal.value)
        with pytest.raises(ValueError) as refusal:
            codex_runtime.resolve(harness, {variable: env[variable]})
        assert variable in str(refusal.value) and env[variable] not in str(refusal.value)
        assert manifest_path.read_bytes() == before and not marker.exists()


def test_improver_restricted_child_does_not_execute_canary(tmp_path, canary):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node executable unavailable for authored preload control')
    injected, marker = canary
    env = {'PATH': os.defpath, 'HOME': str(tmp_path), 'FIXTURE_KEY': 'authored', **injected}
    dropped = cli_improver._restrict_env(env, {'api_key_env': 'FIXTURE_KEY'})
    assert {'NODE_OPTIONS', 'NODE_PATH'} <= set(dropped)
    done = subprocess.run([node, '-e', 'process.stdout.write("clean")'], env=env,
                          cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert done.returncode == 0 and done.stdout == 'clean', done.stderr
    assert not marker.exists()


def test_codex_child_omits_loader_even_on_opaque_diagnostic(tmp_path, monkeypatch, canary):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node executable unavailable for authored preload control')
    injected, marker = canary
    adapter = load(ROOT / 'base_harness/codex/agent.py', '_loader_codex_agent')
    script = tmp_path / 'authored-cli.js'
    script.write_text('const fs=require("fs"); fs.writeFileSync(process.argv[process.argv.indexOf("-o")+1], "clean");'
                      'console.log(JSON.stringify({type:"turn.completed",usage:{input_tokens:1,output_tokens:1}}));')
    monkeypatch.setattr(adapter, '_runtime', lambda: {'argv': [node, str(script)], 'path': [], 'cacheable': False})
    for key, value in injected.items():
        monkeypatch.setenv(key, value)
    work = tmp_path / 'task'
    assert adapter.run({'task_id': 'authored', 'goal': 'Use only authored local output'}, work) == 0
    assert (work / 'answer.txt').read_text() == 'clean'
    assert not marker.exists()


def test_dsh_legacy_diagnostic_child_omits_loader(tmp_path, monkeypatch, canary):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node executable unavailable for authored preload control')
    injected, marker = canary
    adapter = load(ROOT / 'base_harness/deepseek-harness/agent.py', '_loader_dsh_agent')
    script = tmp_path / 'authored-dsh.js'
    script.write_text('console.log(process.argv.includes("--dump-config") ? "- id: authored" : "clean");')
    for key, value in injected.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('HG_AGENT_DSH_COMMAND', shlex.join([node, str(script)]))
    monkeypatch.delenv('HG_AGENT_DSH_ROOT', raising=False)
    monkeypatch.delenv('HG_AGENT_DSH', raising=False)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('TMPDIR', str(tmp_path / 'tmp'))
    monkeypatch.setenv('HG_AGENT_MODEL', '')
    monkeypatch.setenv('HG_AGENT_BASE_URL', '')
    work = tmp_path / 'task'
    work.mkdir()
    assert adapter.run({'goal': 'Use authored local output'}, work) == 0
    assert 'clean' in (work / 'answer.txt').read_text()
    assert not marker.exists()


LOADER_KEYS = ('NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH')


@pytest.mark.parametrize('declared', [False, True])
def test_improver_child_drops_all_loader_keys_and_preserves_credential_shape(tmp_path, declared):
    safe = {'PATH': os.defpath, 'HOME': str(tmp_path), 'FIXTURE_KEY': 'authored credential'}
    if not declared:
        safe.update({'CODEX_HOME': '/authored/codex-home', 'OTHER_CREDENTIAL': 'authored other key',
                     'CUSTOM_CONFIGURATION': 'kept'})
    env = {**safe, **{name: '/authored/unloaded-' + name for name in LOADER_KEYS}}
    dropped = cli_improver._restrict_env(env, {'api_key_env': 'FIXTURE_KEY'} if declared else {})
    assert set(LOADER_KEYS) <= set(dropped)
    assert env == safe
    done = subprocess.run(['/bin/true'], env=env, cwd=tmp_path, capture_output=True, timeout=10)
    assert done.returncode == 0 and not done.stderr


@pytest.mark.parametrize('kind', ['codex', 'dsh'])
def test_actual_task_launch_env_omits_all_loader_keys(tmp_path, monkeypatch, kind):
    class LaunchObserved(Exception):
        pass

    path = ROOT / ('base_harness/codex/agent.py' if kind == 'codex' else
                   'base_harness/deepseek-harness/agent.py')
    adapter = load(path, '_all_loader_' + kind)
    for name in LOADER_KEYS:
        monkeypatch.setenv(name, '/authored/unloaded-' + name)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('TMPDIR', str(tmp_path / 'tmp'))
    monkeypatch.setenv('HG_AGENT_MODEL', '')
    monkeypatch.setenv('HG_AGENT_BASE_URL', '')
    selected = {'argv': ['/bin/true'], 'path': [], 'cacheable': False,
                'root': None, 'node': None, 'cli': None}
    if kind == 'codex':
        monkeypatch.setattr(adapter, '_runtime', lambda: selected)
    else:
        monkeypatch.setattr(adapter, 'runtime', lambda env: selected)
        monkeypatch.setattr(adapter, 'facts', lambda *a: {
            'prompt_sha': None, 'config_sha': None, 'profile_sha': None, 'knobs_sha': None})
        monkeypatch.setattr(adapter, '_loader_probe', lambda *a: {})

    def observe(*args, **kwargs):
        assert not set(LOADER_KEYS) & kwargs['env'].keys()
        raise LaunchObserved

    monkeypatch.setattr(adapter.subprocess, 'Popen', observe)
    work = tmp_path / 'task'
    work.mkdir()
    with pytest.raises(LaunchObserved):
        adapter.run({'task_id': 'authored', 'goal': 'Observe launch environment only'}, work)


@pytest.mark.parametrize('name', LOADER_KEYS)
def test_whitespace_loader_values_are_nonempty_runtime_inputs(name):
    dsh = load(ROOT / 'base_harness/deepseek-harness/dsh_runtime.py', '_whitespace_dsh_runtime')
    for helper in (codex_runtime, dsh):
        with pytest.raises(ValueError, match=name):
            helper.require_clean_startup({name: ' '})
        helper.require_clean_startup({name: ''})
