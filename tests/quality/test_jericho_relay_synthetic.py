"""Authored relay protocol; no ROM, model, Docker, network or installed Jericho."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from eval.service.tool_transport import Relay, ToolTransportError, call

ROOT = Path(__file__).resolve().parents[2]


def load_file(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


relay_module = load_file('synthetic_jericho_relay', ROOT / 'data/datasets/jericho/relay.py')
client = load_file('synthetic_jericho_client', ROOT / 'data/datasets/jericho/cli/jer-relay')


class FakeEnvironment:
    def __init__(self, rom, *, seed):
        assert seed == 0 and Path(rom).read_bytes() == b'authored-not-a-rom'
        self.actions = []
    def step(self, action):
        self.actions.append(action)
        return (f'observed {action}', 0, False, {'moves': len(self.actions), 'score': len(self.actions)})
    def get_max_score(self): return 10
    def get_inventory(self): return 'a synthetic key'


def test_relay_client_preserves_action_log_and_ephemeral_look(tmp_path, monkeypatch, capsys):
    fake_rom = tmp_path / 'authored'; fake_rom.write_bytes(b'authored-not-a-rom')
    game = relay_module.Game(fake_rom, 0, make_env=FakeEnvironment)
    socket = tmp_path / 'tool.sock'
    monkeypatch.setattr(client, 'SOCKET', str(socket))
    with Relay(socket, game.handle, max_calls=None, max_line_bytes=None):
        assert client.main(['--workdir', str(tmp_path), 'look'], transport=call) == 0
        assert 'move 0; score 0/10' in capsys.readouterr().out
        assert not (tmp_path / 'jer_actions.jsonl').exists()
        assert client.main(['--workdir', str(tmp_path), 'do', 'open', 'door'], transport=call) == 0
        assert 'observed open door' in capsys.readouterr().out
        assert client.main(['--workdir', str(tmp_path), 'score'], transport=call) == 0
        assert 'score 1/10; moves 1; actions logged 1' in capsys.readouterr().out
        assert client.main(['--workdir', str(tmp_path), 'inventory'], transport=call) == 0
        assert capsys.readouterr().out.strip() == 'a synthetic key'
        assert client.main(['--workdir', str(tmp_path), 'done'], transport=call) == 0
        assert client.main(['--workdir', str(tmp_path), 'done'], transport=call) == 0
    rows = [json.loads(line) for line in (tmp_path / 'jer_actions.jsonl').read_text().splitlines()]
    assert rows == [{'kind':'action', 'step':0, 'action':'open door'}, {'kind':'done', 'step':1}]


def test_relay_failure_does_not_append_or_expose_private_exception(tmp_path, monkeypatch, capsys):
    def broken(*args, **kwargs): raise RuntimeError('SECRET-WALKTHROUGH')
    game = relay_module.Game(tmp_path / 'synthetic', 0, make_env=broken)
    socket = tmp_path / 'tool.sock'; monkeypatch.setattr(client, 'SOCKET', str(socket))
    with Relay(socket, game.handle) as server:
        with pytest.raises(ToolTransportError) as exc:
            client.main(['--workdir', str(tmp_path), 'do', 'go'], transport=call)
        assert 'SECRET-WALKTHROUGH' not in str(exc.value)
        assert server.receipt['errors'] == 1
    assert not (tmp_path / 'jer_actions.jsonl').exists()
    assert 'SECRET-WALKTHROUGH' not in capsys.readouterr().err


def test_actor_binding_is_empty_and_zero_seed_is_preserved(monkeypatch):
    from types import ModuleType, SimpleNamespace
    fake = ModuleType('jericho'); fake.defines = SimpleNamespace(BINDINGS_DICT={'private': 'secret'})
    class Base:
        def __init__(self, rom, seed): self.value = self.seed(seed)
        def reset(self): pass
    fake.FrotzEnv = Base
    monkeypatch.setitem(sys.modules, 'jericho', fake)
    env = relay_module._make_public_env('/synthetic', seed=0)
    assert env.value == 0 and fake.defines.BINDINGS_DICT == {}


def test_declared_runtime_source_and_native_pin_identity():
    import hashlib
    from harnessgrad.dataset_runtime import execution_descriptor
    descriptor = execution_descriptor('jericho')
    assert descriptor['pins']['jericho'] == '3.3.1'
    assert all(name in descriptor['pins'] for name in ('numpy', 'spacy', 'termcolor'))
    source = ROOT / 'data/datasets/jericho'
    provenance = json.loads((source / 'RUNTIME_PROVENANCE.json').read_text())
    assert provenance['license'] == 'GPL-2.0-or-later'
    assert hashlib.sha256((source / 'LICENSE.upstream').read_bytes()).hexdigest() == provenance['license_sha256']
    assert provenance['native_source_comparison']['native_scorer_manifest_sha256'] == \
        hashlib.sha256((ROOT / 'data/runtimes/jericho-native.json').read_bytes()).hexdigest()


def test_prepared_engine_import_and_native_library_without_game(tmp_path):
    import os
    import subprocess
    from harnessgrad.dataset_runtime import resolve_execution
    store = os.environ.get('HG_TEST_JERICHO_RUNTIME_ROOT')
    if not store:
        pytest.skip('set HG_TEST_JERICHO_RUNTIME_ROOT to a prepared external artifact')
    runtime = resolve_execution('jericho', root=store)
    code = '''import json,sys
import jericho,numpy
from jericho.jericho import _load_frotz_lib
assert jericho.__version__ == '3.3.1'
assert hasattr(_load_frotz_lib(), 'get_max_score')
assert 'spacy' not in sys.modules
print(json.dumps({'version': jericho.__version__, 'numpy':numpy.__version__}))
'''
    env = {'HOME': str(tmp_path), 'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE':'1', 'LANG':'C.UTF-8'}
    result = subprocess.run([runtime['python'], '-I', '-B', '-c', code], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['version'] == '3.3.1'


@pytest.mark.parametrize('operation', ['score', 'inventory'])
def test_native_environment_closes_under_request_lock_on_success_and_failure(tmp_path, operation):
    rom = tmp_path / 'authored'
    rom.write_bytes(b'authored-not-a-rom')
    closed = []
    class Environment(FakeEnvironment):
        def close(self):
            assert game.lock.locked()
            closed.append(True)
        def get_inventory(self):
            raise RuntimeError('PRIVATE inventory failure')
    game = relay_module.Game(rom, 0, make_env=Environment)
    if operation == 'inventory':
        with pytest.raises(RuntimeError, match='^Jericho environment unavailable$'):
            game.handle(operation, {'actions': []})
    else:
        assert game.handle(operation, {'actions': []})['score'] == 0
    assert closed == [True]
