"""Native bound-service launch with original relay and authored dependency stubs only."""
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sys

import pytest

from eval.service.tool_transport import call


@pytest.mark.parametrize('version', [2, 3])
def test_native_bound_tau_relay_reads_only_declared_inputs(tmp_path, monkeypatch, version):
    from eval.run import tool_relay
    from harnessgrad import dataset_runtime
    adapter = importlib.import_module(f'data.datasets.tau{version}_bench')
    fixture = tmp_path / 'fixture'
    assets = fixture / f'data/datasets/tau{version}_bench'
    lib = assets / 'lib'
    lib.mkdir(parents=True)
    shutil.copyfile(adapter.ASSETS / 'relay.py', assets / 'relay.py')
    transport = fixture / 'eval/service/tool_transport.py'
    transport.parent.mkdir(parents=True)
    shutil.copyfile(adapter.TRANSPORT, transport)
    root = tmp_path / 'prepared'
    case = root / 'cases/authored'
    case.mkdir(parents=True)
    bundle = root / 'bundle'
    bundle.mkdir()
    (bundle / 'marker').write_text('authored source bundle')
    private = {'task_id': 'authored', 'task': {'id': 'authored'},
               'domain': 'authored', 'release': 'authored', 'revision': 'authored',
               'gold': 'PRIVATE-GOLD-ONLY'}
    raw = json.dumps(private).encode()
    (case / 'private.json').write_bytes(raw)
    (case.parent / 'unselected-private.json').write_text('UNSELECTED-PRIVATE')
    public = json.dumps({'policy': 'Authored policy', 'user_tools': []}).encode()
    (case / 'public.json').write_bytes(public)
    # This stub only checks declared mount access. It does not simulate a released case.
    (lib / 'bootstrap.py').write_text('''from pathlib import Path

def bootstrap(bundle):
    assert (Path(bundle)/'marker').read_text() == 'authored source bundle'
    import json
    private = Path(json.loads(Path('case-spec.json').read_text())['case'])
    assert not (private.parent.parent / 'unselected-private.json').exists()
    transport = Path(__file__).resolve().parents[4] / 'eval/service/tool_transport.py'
    for path in [Path(bundle)/'marker', Path(__file__), private, transport]:
        try:
            path.write_text('MUST NOT WRITE')
        except OSError as exc:
            assert exc.errno in (13, 30)
        else:
            raise AssertionError('trusted source was writable')
''')
    (lib / 'model.py').write_text('def install(*args): pass\n')
    (lib / 'runtime.py').write_text('''import json
from pathlib import Path

def validate_assistant_calls(calls): pass

class Runtime:
    termination_reason = None
    bootstrap_complete = False
    def __init__(self, path): self.config = json.loads(Path(path).read_text())
    def start_conversation(self):
        self.bootstrap_complete = True
        return {'message':'authored hello'}
    def submit_assistant_message(self, message): return {'message': message}
    def call_assistant_tool(self, **kwargs): return {}
    def call_assistant_tools(self, **kwargs): return {}
    def submit_assistant_tool_calls(self, **kwargs): return {}
    def get_runtime_status(self): return {'started': self.bootstrap_complete}
    def episode(self): return {'task_id':self.config['task']['id'],
        'domain':self.config['domain'], 'seed': self.config['seed']}
Tau2Runtime = Tau3Runtime = Runtime
''')
    monkeypatch.setattr(adapter, 'ASSETS', assets)
    monkeypatch.setattr(adapter, 'TRANSPORT', transport)
    monkeypatch.setattr(adapter, 'DATASET_ROOT', root)
    runtime = {'python': str(Path(sys.executable).resolve()),
               'runtime_paths': [str(Path(sys.prefix).resolve())]}
    monkeypatch.setattr(adapter, '_execution_runtime', lambda: runtime)
    monkeypatch.setattr(adapter, 'model_spec', lambda *args: {'model': 'authored-no-inference'})
    monkeypatch.setattr(dataset_runtime, 'bind', lambda *args: {'authored': True})
    monkeypatch.setattr(dataset_runtime, 'admit_binding', lambda *args, **kwargs: runtime)
    row = {'path': 'cases/authored', 'user_tools': [],
           'public_sha256': hashlib.sha256(public).hexdigest(),
           'private_sha256': hashlib.sha256(raw).hexdigest()}
    setup = adapter._setup('authored', row)
    assert 'PRIVATE-GOLD-ONLY' not in json.dumps(setup['files'])
    assert str(case / 'private.json') not in json.dumps(setup['files'])
    assert set(setup['tool_relay']['read_paths']) == {
        str(assets), str(transport.parent), str(bundle), str(case / 'private.json')}
    # Authored stubs make no model calls; do not start a real inference broker.
    setup['tool_relay'].pop('model')
    handle = tool_relay.start(setup, context={'task_id': 'authored', 'run_id': 'authored',
        'trial_id': 'authored', 'seed': 7}, private_diagnostics=True)
    try:
        assert call(handle.path, 'start') == {'result': {'message': 'authored hello'}}
        assert call(handle.path, 'status') == {'result': {'started': True}}
        assert call(handle.path, 'reset') == {'error': 'unsupported_operation_or_fields'}
    finally:
        handle.stop()
        handle.cleanup()
    assert handle.report()['cleanup']['complete']
    assert (case / 'private.json').read_bytes() == raw
    assert (bundle / 'marker').read_text() == 'authored source bundle'
