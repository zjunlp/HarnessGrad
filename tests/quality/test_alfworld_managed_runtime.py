"""ALFWorld's loader/client remain stdlib; only its trusted service is bound."""
from pathlib import Path
import json
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_service_requires_prepared_runtime(monkeypatch):
    from data.datasets import alfworld
    from harnessgrad import dataset_runtime
    def missing(name):
        assert name == 'alfworld'
        raise dataset_runtime.DatasetRuntimeUnavailable('explicit preparation required')
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', missing)
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match='explicit preparation'):
        alfworld._setup({'stripped_game': 'private selected game'})


def test_selected_service_binding_and_exact_inputs(monkeypatch):
    from data.datasets import alfworld
    from harnessgrad import dataset_runtime
    selected = {'python': '/selected/python'}
    binding = {'python': '/selected/python', 'role': 'service'}
    monkeypatch.setattr(dataset_runtime, 'resolve_execution', lambda name: selected)
    def bind(runtime, role):
        assert runtime is selected
        assert role == 'service'
        return binding
    monkeypatch.setattr(dataset_runtime, 'bind', bind)
    setup = alfworld._setup({'stripped_game': 'private selected game'})
    relay = setup['tool_relay']
    assert relay['python_runtime'] is binding
    assert relay['python'] == '/selected/python'
    assert relay['content'] == 'private selected game'
    paths = {Path(p) for p in relay['read_paths']}
    assert paths == {alfworld.ASSETS / 'relay.py', alfworld.LIB_DIR / 'gym_env.py',
                     alfworld.TRANSPORT_SOURCE}
    assert all(p.is_file() for p in paths)
    assert {p['path'] for p in setup['files']} == {'gym_env.py', 'alf'}
    assert all('private selected game' not in json.dumps(p) for p in setup['files'])


def test_loader_and_client_have_no_third_party_imports(tmp_path):
    script = '''import sys
sys.path.insert(0, sys.argv[1])
class RejectThirdParty:
 def find_spec(self, name, *args):
  if name.split('.')[0] in {'alfworld','textworld','gym','numpy','torch'}:
   raise RuntimeError('unexpected third-party import: '+name)
sys.meta_path.insert(0, RejectThirdParty())
import data.datasets.alfworld as adapter
assert adapter.STEP_BUDGET == 50
assert adapter._GYM_ENV.LOG_FILENAME == 'alf_actions.jsonl'
'''
    # -S supplies an empty platform environment and bypasses user site hooks.
    result = subprocess.run([sys.executable, '-I', '-S', '-c', script, str(ROOT)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_recipe_is_separate_from_native_scorer():
    from harnessgrad.dataset_runtime import execution_descriptor
    declaration = execution_descriptor('alfworld')
    assert declaration['python'] == '3.12'
    assert declaration['pins']['alfworld'] == '0.4.2'
    assert declaration['pins']['textworld'] == '1.7.0'
    assert declaration['pins']['numpy'] == '1.26.4'
    assert declaration['pins']['jericho'] == '3.3.1'
    assert declaration['pins']['tatsu'] == '5.8.3'


def test_real_managed_service_with_authored_engine(tmp_path, monkeypatch):
    """Opt-in native-bwrap transport control; no benchmark data or scoring."""
    import os
    runtime_root = os.environ.get('HG_ALFWORLD_SERVICE_TEST_ROOT')
    if not runtime_root:
        pytest.skip('set HG_ALFWORLD_SERVICE_TEST_ROOT to prepared service artifact')
    monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', runtime_root)
    from data.datasets import alfworld
    from eval.run import tool_relay
    from eval.service.tool_transport import call
    neighbor = tmp_path / 'private-neighbor.txt'
    neighbor.write_text('must never be mounted')
    wrapper = tmp_path / 'authored_engine.py'
    wrapper.write_text(f'''import runpy, sys
sys.path.insert(0, {str(ROOT)!r})
from pathlib import Path
assert not Path({str(neighbor)!r}).exists(), 'private neighbor visible'
import importlib.util
spec=importlib.util.spec_from_file_location('authored_relay', {str(alfworld.ASSETS / 'relay.py')!r})
r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
class Engine:
 def close(self): pass
r.gym_env.build=lambda *args: Engine()
r.gym_env.reset=lambda env: dict(observation='Authored room', admissible=['finish'], won=False, done=False)
r.gym_env.step=lambda env, action: dict(observation='Finished' if action=='finish' else 'Nothing happens.', admissible=['finish'], won=action=='finish', done=action=='finish')
r.main(sys.argv[1:])
''')
    setup = alfworld._setup({'stripped_game': 'PRIVATE PDDL AND WALKTHROUGH'})
    setup['tool_relay']['program'] = str(wrapper)
    setup['tool_relay']['read_paths'].append(str(wrapper))
    handle = tool_relay.start(setup, ready_timeout_s=20)
    try:
        reply = call(handle.path, 'look', {'step': 0, 'budget': 50})
        assert reply['obs'] == 'Authored room'
        assert 'PRIVATE' not in json.dumps(reply)
        tool_dir = tmp_path / 'client_transport'
        tool_dir.mkdir()
        (tool_dir / 'tool_transport.py').write_bytes(alfworld.TRANSPORT_SOURCE.read_bytes())
        (tool_dir / 'tool.sock').symlink_to(handle.path)
        monkeypatch.setenv('HG_ALF_TOOL_DIR', str(tool_dir))
        (tmp_path / 'gym_env.py').write_bytes((alfworld.LIB_DIR / 'gym_env.py').read_bytes())
        (tmp_path / 'alf').write_bytes((alfworld.CLI_DIR / 'alf').read_bytes())
        result = subprocess.run([sys.executable, '-S', str(tmp_path / 'alf'), 'do', 'finish'],
                                cwd=tmp_path, env=dict(os.environ), capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        records = [json.loads(line) for line in (tmp_path / 'alf_actions.jsonl').read_text().splitlines()]
        assert records[0]['action'] == 'finish'
        assert call(handle.path, 'look', {'step': 1, 'budget': 50})['won'] is True
    finally:
        handle.stop()
    assert handle.report()['python_runtime']['role'] == 'service'
