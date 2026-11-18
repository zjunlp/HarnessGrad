"""Invalid worker output must not become readiness, publication or a score."""
import ast
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def malformed(*args, **kwargs):
    raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'authored malformed output')


def test_agentdojo_malformed_probe_is_unavailable(tmp_path, monkeypatch):
    from data.datasets import agentdojo
    monkeypatch.setattr(agentdojo, 'DATASET_ROOT', tmp_path)
    monkeypatch.setattr(agentdojo, '_execution_runtime', lambda: {'python': '/authored/python'})
    monkeypatch.setattr(agentdojo, '_source_root', lambda: tmp_path / 'source')
    monkeypatch.setattr(agentdojo.subprocess, 'run', malformed)
    status = agentdojo.provision_status()
    assert status['present'] is False and 'utf-8' in status['detail']


def test_tau3_malformed_probe_is_unavailable(tmp_path, monkeypatch):
    from data.datasets import tau3_bench
    monkeypatch.setattr(tau3_bench, 'DATASET_ROOT', tmp_path)
    monkeypatch.setattr(tau3_bench, '_execution_runtime', lambda: {'python': '/authored/python'})
    def roster():
        return {}
    roster.cache_clear = lambda: None
    monkeypatch.setattr(tau3_bench, 'roster', roster)
    monkeypatch.setattr(tau3_bench.subprocess, 'run', malformed)
    status = tau3_bench.provision_status()
    assert status['present'] is False and 'utf-8' in status['detail']


def test_costbench_malformed_worker_output_cannot_publish(tmp_path, monkeypatch):
    from harnessgrad.datasets import runtime
    path = ROOT / 'data/datasets/costbench/prepare_controller.py'
    spec = importlib.util.spec_from_file_location('_costbench_decode_control', path)
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    monkeypatch.setattr(runtime, 'resolve_execution', lambda name: {'python': '/authored/python'})
    monkeypatch.setattr(runtime, 'bind', lambda artifact, role: artifact)
    monkeypatch.setattr(runtime, 'admit_binding', lambda *a, **k: None)
    monkeypatch.setattr(controller.subprocess, 'run', malformed)
    root = tmp_path / 'catalog'
    with pytest.raises(UnicodeDecodeError):
        controller.main(['--root', str(root)])
    assert not (root / 'prepared-v1').exists()
    assert list(root.glob('.prepared-v1.prepare-reservation/attempt-*/request.json'))


def test_alfworld_malformed_done_output_cannot_complete_control(tmp_path, monkeypatch):
    from eval.run import tool_relay
    source = (ROOT / 'data/datasets/alfworld/control.py').read_text()
    tree = ast.parse(source)
    drive = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'drive')
    published = tmp_path / 'published'
    relay = tmp_path / 'relay'
    published.mkdir()
    relay.mkdir()
    (relay / 'service.json').write_text(json.dumps({'fixture': True}))
    server = SimpleNamespace(path=tmp_path / 'authored-socket')
    @contextmanager
    def start(_spec):
        yield server
    monkeypatch.setattr(tool_relay, 'start', start)
    monkeypatch.setattr(subprocess, 'run', malformed)
    namespace = {'Path': Path, 'json': json, 'os': __import__('os'), 'sys': sys,
                 'subprocess': subprocess, '_published': lambda work: published,
                 '_relay_dir': lambda work: relay}
    exec(compile(ast.Module(body=[drive], type_ignores=[]), 'authored_control', 'exec'), namespace)
    with pytest.raises(UnicodeDecodeError):
        namespace['drive'](tmp_path, [], budget=1)
    assert not (published / 'tool.sock').exists()
