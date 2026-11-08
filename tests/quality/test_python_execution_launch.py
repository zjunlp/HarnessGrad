"""Real, model-free launches of role-bound Python services and verifiers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eval.run import tool_relay
from eval.service.tool_transport import call
from eval.verification import verifier
from harnessgrad import dataset_runtime


ROOT = Path(__file__).resolve().parents[2]
TRANSPORT = ROOT / "eval/service/tool_transport.py"


@pytest.fixture(scope="module")
def execution_artifact():
    if shutil.which("uv") is None or shutil.which("bwrap") is None:
        pytest.skip("real execution launch controls need uv and bubblewrap")
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="hg-python-execution-fixture-", dir=cache))
    patch = pytest.MonkeyPatch()
    try:
        source, store, home = (directory / name for name in ("source", "store", "home"))
        folder = source / "data/datasets/control"
        folder.mkdir(parents=True)
        home.mkdir()
        (folder / "empty.lock").write_text("# Empty dependencies: no package index access.\n")
        (folder / "execution-runtime.json").write_text(json.dumps({
            "schema": dataset_runtime.EXECUTION_SCHEMA, "dataset": "control",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "requirements": "empty.lock"}))
        patch.setattr(dataset_runtime, "ROOT", source)
        patch.setenv("HOME", str(home))
        patch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
        patch.setenv("UV_OFFLINE", "1")
        artifact = dataset_runtime.prepare_execution("control", python=sys.executable,
                                                    root=store, timeout_s=60)
        yield artifact
    finally:
        patch.undo()
        # This fixture owns the whole tree, including the intentionally frozen venv.
        dataset_runtime._remove_instance(directory)


_SERVICE = r'''
import argparse, json, os, signal, socket, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from transport import Relay

parser = argparse.ArgumentParser()
parser.add_argument('--socket'); parser.add_argument('--receipt'); parser.add_argument('--game')
args = parser.parse_args()
counter = json.loads(Path(args.game).read_text())['initial']
done = False
def stop(*_):
    global done
    done = True
signal.signal(signal.SIGTERM, stop)

def inspect():
    rejected = False
    try:
        Path(sys.prefix, 'forbidden-service-write').write_text('changed')
    except OSError:
        rejected = True
    model = None
    if os.environ.get('HG_ENV_MODEL_SOCKET'):
        path = os.environ['HG_ENV_MODEL_SOCKET']
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(path)
            model = {'socket': path, 'visible': Path(path).is_socket(),
                     'reply': client.recv(128).decode()}
    recording_handles = {key: os.environ.get(key)
                         for key in ('HG_RELAY_CONTEXT_PATH', 'HG_RELAY_RECORD_PATH')}
    context_path = recording_handles['HG_RELAY_CONTEXT_PATH']
    return {'prefix': sys.prefix, 'python': sys.executable, 'isolated': bool(sys.flags.isolated),
            'no_bytecode': sys.dont_write_bytecode,
            'readonly': bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY),
            'runtime_write_rejected': rejected,
            'sitecustomize_loaded': 'sitecustomize' in sys.modules,
            'model': model,
            'recording_handles': recording_handles,
            'recording_context': json.loads(Path(context_path).read_text()) if context_path else None,
            'unselected_source_visible': Path(__file__).with_name('unselected.txt').exists(),
            'ambient': {key: value for key, value in os.environ.items()
                        if key in ('PYTHONPATH', 'PYTHONHOME', 'OPENAI_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'HG_ENV_MODEL_SOCKET')
                        or key.endswith('API_KEY')},
            'paths': {key: os.environ[key] for key in ('HOME', 'TMPDIR', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME')},
            'cwd': str(Path.cwd()), 'counter': counter,
            'markers': {key: (Path(os.environ[key], 'counter').read_text()
                              if Path(os.environ[key], 'counter').exists() else None)
                        for key in ('HOME', 'TMPDIR', 'XDG_CACHE_HOME')}}

def handle(op, values):
    global counter
    if op == 'increment':
        counter += values.get('amount', 1)
        for key in ('HOME', 'TMPDIR', 'XDG_CACHE_HOME'):
            Path(os.environ[key], 'counter').write_text(str(counter))
    elif op != 'inspect':
        raise ValueError('unsupported fixture operation')
    return inspect()

with Relay(args.socket, handle, max_calls=None, max_line_bytes=None) as relay:
    while not done:
        time.sleep(.01)
# A bwrap leader can exit before its child finishes. Deliberately make receipt
# finalization observable instead of relying on a tiny write winning that race.
time.sleep(.1)
if os.environ.get('HG_RELAY_RECORD_PATH'):
    Path(os.environ['HG_RELAY_RECORD_PATH']).write_text(json.dumps({
        'counter': counter, 'observation': inspect(), 'padding': 'complete-record-' * 4096}))
Path(args.receipt).write_text(json.dumps(relay.receipt))
'''


@pytest.fixture
def service_program(tmp_path):
    source = tmp_path / "service-source"
    source.mkdir()
    program = source / "service.py"
    program.write_text(_SERVICE)
    transport = source / "transport.py"
    shutil.copyfile(TRANSPORT, transport)
    (source / "unselected.txt").write_text("not a declared service input")
    return program, transport


def service_setup(artifact, program):
    service, transport = program
    return {"tool_relay": {"program": str(service), "python": artifact["python"],
                           "python_runtime": dataset_runtime.bind(artifact, "service"),
                           "read_paths": [str(service), str(transport)],
                           "content": json.dumps({"initial": 0}),
                           "record": {"path": "trusted_episode"}}}


def context(task="control"):
    return {"task_id": task, "run_id": "execution-launch", "trial_id": "trial-1", "seed": "1"}


def seal(handle):
    handle.stop()
    assert handle.proc.poll() is not None
    assert handle.fault() is None, handle.report()
    assert handle.receipt["stopped_utc"] and handle.receipt["errors"] == 0
    projection = handle.recording_input()
    assert projection["immutable"] is True
    envelope = json.loads((Path(projection["from"]) / "episode.json").read_text())
    assert envelope["binding"] == handle.context
    assert envelope["payload"]["padding"] == "complete-record-" * 4096
    return envelope["payload"]


def test_bound_service_uses_readonly_artifact_and_scrubs_ambient_environment(
        execution_artifact, service_program, tmp_path, monkeypatch):
    poison = tmp_path / "ambient"
    poison.mkdir()
    marker = tmp_path / "ambient-hook-entered"
    (poison / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('entered')\n")
    monkeypatch.setenv("PYTHONPATH", str(poison))
    monkeypatch.setenv("PYTHONHOME", str(poison))
    monkeypatch.setenv("HG_ENV_MODEL_SOCKET", "/ambient-undeclared-model.sock")
    for name in ("OPENAI_API_KEY", "HG_AGENT_API_KEY", "HG_METHOD_API_KEY", "HG_JUDGE_API_KEY",
                 "HG_ENV_API_KEY", "HG_ARBITRARY_API_KEY", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "authored-secret-control")
    handle = tool_relay.start(service_setup(execution_artifact, service_program),
                              context=context(), ready_timeout_s=10)
    try:
        observed = call(str(handle.path), "inspect", {})
        assert observed["prefix"] == execution_artifact["path"]
        assert observed["python"] == execution_artifact["python"]
        assert observed["isolated"] and observed["no_bytecode"] and observed["readonly"]
        assert observed["runtime_write_rejected"] and not observed["sitecustomize_loaded"]
        assert observed["ambient"] == {} and not observed["unselected_source_visible"]
        assert not marker.exists()
        for name, value in observed["paths"].items():
            assert Path(value).parent == handle.directory
        assert observed["cwd"] == str(handle.directory / "state")
        assert observed["counter"] == 0 and set(observed["markers"].values()) == {None}
        assert seal(handle)["counter"] == 0
    finally:
        handle.cleanup()
    assert handle.report()["cleanup"] == {"complete": True, "errors": []}
    assert handle.report()["python_runtime"]["runtime_id"] == execution_artifact["runtime_id"]


def test_concurrent_bound_relays_have_independent_state_and_complete_delayed_records(
        execution_artifact, service_program):
    handles, lock = [], threading.Lock()
    def launch(name):
        handle = tool_relay.start(service_setup(execution_artifact, service_program),
                                  context=context(name), ready_timeout_s=10)
        with lock:
            handles.append(handle)
        return handle
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = [future.result(timeout=20) for future in
                             (executor.submit(launch, "A"), executor.submit(launch, "B"))]
            # Keep launcher threads alive for the full lifetime of bwrap's
            # --die-with-parent guard, including delayed receipt finalization.
            changed = call(str(first.path), "increment", {"amount": 7})
            untouched = call(str(second.path), "inspect", {})
            assert changed["counter"] == 7 and set(changed["markers"].values()) == {"7"}
            assert untouched["counter"] == 0 and set(untouched["markers"].values()) == {None}
            assert first.python_runtime["runtime_id"] == second.python_runtime["runtime_id"]
            for name in changed["paths"]:
                assert changed["paths"][name] != untouched["paths"][name]
            assert changed["cwd"] != untouched["cwd"]
            assert seal(first)["counter"] == 7
            assert seal(second)["counter"] == 0
    finally:
        for handle in handles:
            handle.cleanup()
    assert len(handles) == 2 and all(handle.report()["cleanup"]["complete"] for handle in handles)


@pytest.mark.parametrize('with_model', [False, True])
@pytest.mark.parametrize('record', [False, True])
def test_bound_service_only_receives_current_model_and_recording_handles(
        execution_artifact, service_program, monkeypatch, with_model, record):
    from eval.service import judge

    instances = []
    with tempfile.TemporaryDirectory(prefix="hg-model-socket-control-") as temporary:
        class SocketJudge:
            """An actual AF_UNIX handle, without an inference API or model calls."""
            def __init__(self, specification, *, role, trial_seed):
                assert specification["model"] == "authored-socket-control"
                assert role == "environment" and trial_seed == "1"
                self.path = Path(temporary) / "model.sock"
                self.closed = False
                self.connections = 0
                self._stop = threading.Event()
                self._thread = None
                self._socket = None
                instances.append(self)

            def __enter__(self):
                self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._socket.bind(str(self.path))
                self._socket.listen(4)
                self._socket.settimeout(.1)
                self._thread = threading.Thread(target=self._serve, daemon=True)
                self._thread.start()
                return self

            def _serve(self):
                while not self._stop.is_set():
                    try:
                        connection, _ = self._socket.accept()
                    except (OSError, TimeoutError):
                        continue
                    with connection:
                        self.connections += 1
                        connection.sendall(b"explicit-fixture-socket")

            def report(self):
                return {"faults": [], "connections": self.connections}

            def close(self):
                if self.closed:
                    return
                self.closed = True
                self._stop.set()
                if self._socket is not None:
                    self._socket.close()
                if self._thread is not None:
                    self._thread.join(timeout=2)
                    assert not self._thread.is_alive()
                self.path.unlink(missing_ok=True)

        monkeypatch.setattr(judge, "Judge", SocketJudge)
        monkeypatch.setenv("HG_ENV_MODEL_SOCKET", "/ambient-must-not-win.sock")
        setup = service_setup(execution_artifact, service_program)
        stale_context = service_program[0].with_name('stale-context.json')
        stale_record = service_program[0].with_name('stale-episode.json')
        stale_context.write_text(json.dumps({'task_id': 'another-trial'}))
        stale_record.write_text('another trial recording')
        monkeypatch.setenv('HG_RELAY_CONTEXT_PATH', str(stale_context))
        monkeypatch.setenv('HG_RELAY_RECORD_PATH', str(stale_record))
        # The stale context is readable: sanitization must prevent adopting it.
        setup['tool_relay']['read_paths'] += [str(stale_context), str(stale_record)]
        if not record:
            del setup['tool_relay']['record']
        if with_model:
            setup["tool_relay"]["model"] = {"model": "authored-socket-control", "max_calls": 1,
                                             "max_output_tokens": 1, "timeout_s": 1, "parameters": {}}
        handle = None
        try:
            handle = tool_relay.start(setup, context=context(), ready_timeout_s=10)
            observed = call(str(handle.path), "inspect", {})
            assert observed['recording_handles'] == {
                'HG_RELAY_CONTEXT_PATH': str(handle.directory / 'context.json') if record else None,
                'HG_RELAY_RECORD_PATH': str(handle.directory / 'episode.json') if record else None}
            assert observed['recording_context'] == (handle.context if record else None)
            expected = str(instances[0].path) if with_model else None
            assert observed["ambient"] == ({"HG_ENV_MODEL_SOCKET": expected} if with_model else {})
            assert observed["model"] == ({"socket": expected, "visible": True,
                                           "reply": "explicit-fixture-socket"} if with_model else None)
            if record:
                assert seal(handle)['observation']['model'] == observed['model']
            else:
                handle.stop()
                assert handle.fault() is None, handle.report()
                assert handle.receipt['stopped_utc'] and handle.recording_input() is None
                assert not (handle.directory / 'episode.json').exists()
            if with_model:
                assert instances[0].connections >= 1
            assert stale_context.read_text() == json.dumps({'task_id': 'another-trial'})
            assert stale_record.read_text() == 'another trial recording'
        finally:
            if handle is not None:
                handle.cleanup()
            for instance in instances:
                instance.close()
        assert len(instances) == int(with_model) and all(instance.closed for instance in instances)
        assert handle.report()["cleanup"] == {"complete": True, "errors": []}


@contextmanager
def changed_artifact(artifact):
    path = Path(artifact["path"]) / "pyvenv.cfg"
    original, mode = path.read_bytes(), stat.S_IMODE(path.stat().st_mode)
    try:
        path.chmod(mode | stat.S_IWUSR)
        path.write_bytes(original + b"\n# deliberate fixture tamper\n")
        path.chmod(mode)
        yield
    finally:
        path.chmod(mode | stat.S_IWUSR)
        path.write_bytes(original)
        path.chmod(mode)


@pytest.mark.parametrize("change", ["role", "python", "artifact", "undeclared-program"])
def test_bound_service_rejects_bad_launch_before_popen_and_cleans_private_state(
        execution_artifact, service_program, tmp_path, monkeypatch, change):
    setup = service_setup(execution_artifact, service_program)
    declaration = setup["tool_relay"]
    if change == "role":
        declaration["python_runtime"] = dataset_runtime.bind(execution_artifact, "verifier")
    elif change == "python":
        declaration["python"] = sys.executable
    elif change == "undeclared-program":
        declaration["read_paths"] = [str(service_program[1])]
    launch = Mock(side_effect=AssertionError("invalid service reached Popen"))
    monkeypatch.setattr(tool_relay, "subprocess", SimpleNamespace(
        Popen=launch, DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT,
        TimeoutExpired=subprocess.TimeoutExpired))
    created, allocate = [], tempfile.mkdtemp
    def temporary(*args, **kwargs):
        kwargs["dir"] = tmp_path
        value = allocate(*args, **kwargs)
        created.append(Path(value))
        return value
    monkeypatch.setattr(tool_relay.tempfile, "mkdtemp", temporary)
    with changed_artifact(execution_artifact) if change == "artifact" else nullcontext():
        with pytest.raises(tool_relay.ToolRelayStartError) as failure:
            tool_relay.start(setup, context=context(), ready_timeout_s=1)
    launch.assert_not_called()
    assert created and all(not directory.exists() for directory in created)
    assert failure.value.report["cleanup"]["complete"] is True


_CHECK = '''
import json, os, sys
from pathlib import Path
rejected = False
try:
    Path(sys.prefix, 'forbidden-verifier-write').write_text('changed')
except OSError:
    rejected = True
Path('verifier/evidence.json').write_text(json.dumps({
    'python': sys.executable, 'prefix': sys.prefix, 'isolated': bool(sys.flags.isolated),
    'no_bytecode': sys.dont_write_bytecode, 'runtime_write_rejected': rejected,
    'readonly': bool(os.statvfs(sys.prefix).f_flag & os.ST_RDONLY)}))
Path('verifier/reward.txt').write_text('1')
'''


def verifier_spec(artifact):
    return {"kind": "command", "argv": [artifact["python"], "-I", "-B", "check.py"],
            "python_runtime": dataset_runtime.bind(artifact, "verifier"),
            "runtime_paths": list(artifact["runtime_paths"]),
            "inputs": [{"path": "check.py", "content": _CHECK}],
            "reward_file": "/verifier/reward.txt", "evidence_file": "/verifier/evidence.json",
            "result_contract": {"version": 1, "score_range": [0, 1], "require_exit_zero": True}}


def verify(work, spec):
    return verifier._verify(work, spec, "", sandbox=True, timeout_s=20,
                            env={"kind": "files"}, task_id="control")


def test_bound_verifier_uses_the_same_execution_artifact_readonly(execution_artifact, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    result = verify(tmp_path, verifier_spec(execution_artifact))
    assert result.get("score") == 1, result
    evidence = result["evidence"]
    assert evidence["python"] == execution_artifact["python"] and evidence["prefix"] == execution_artifact["path"]
    assert all(evidence[key] for key in ("isolated", "no_bytecode", "readonly", "runtime_write_rejected"))
    assert result["python_runtime"]["role"] == "verifier"
    assert result["python_runtime"]["runtime_id"] == execution_artifact["runtime_id"]


@pytest.mark.parametrize("change", ["missing-I", "missing-B", "role", "python", "paths", "artifact"])
def test_bound_verifier_refuses_invalid_runtime_before_dispatch(
        execution_artifact, tmp_path, monkeypatch, change):
    spec = verifier_spec(execution_artifact)
    if change == "missing-I":
        spec["argv"].remove("-I")
    elif change == "missing-B":
        spec["argv"].remove("-B")
    elif change == "role":
        spec["python_runtime"] = dataset_runtime.bind(execution_artifact, "service")
    elif change == "python":
        spec["argv"][0] = sys.executable
    elif change == "paths":
        spec["runtime_paths"] = [*spec["runtime_paths"], "/usr"]
    dispatch = Mock(side_effect=AssertionError("invalid verifier reached dispatch"))
    monkeypatch.setattr(verifier, "_verify_dispatch", dispatch)
    with changed_artifact(execution_artifact) if change == "artifact" else nullcontext():
        result = verify(tmp_path, spec)
    assert result.get("score") is None and result["outcome"] == "unmeasured", result
    assert "Python runtime" in result["unmeasured"]
    dispatch.assert_not_called()


@pytest.mark.parametrize("code", [0, 1])
def test_unbound_verifier_keeps_legacy_launch_without_runtime_admission(tmp_path, monkeypatch, code):
    spec = {"kind": "command", "argv": ["legacy-command", "argument"]}
    admit = Mock(side_effect=AssertionError("legacy route acquired a runtime binding"))
    monkeypatch.setattr(dataset_runtime, "admit_binding", admit)
    launch = Mock(return_value=subprocess.CompletedProcess(spec["argv"], code, "", ""))
    monkeypatch.setattr(verifier.sandbox_mod, "run", launch)
    result = verify(tmp_path, spec)
    assert result["passed"] is (code == 0), result
    assert launch.call_args.args[0] == spec["argv"]
    assert "runtime_paths" not in launch.call_args.kwargs and "python_runtime" not in result
    admit.assert_not_called()
