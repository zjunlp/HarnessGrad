"""Explicit close order, returned receipt identity and acquired-only process/dir ownership."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eval.containers import responses_overlay
from eval.run import task_session
from eval.run.task_workspace import TaskWorkspace


class OwnedProcess:
    def __init__(self, name, events):
        self.name, self.events = name, events
        self.process = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time; time.sleep(60)'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.http = None
        self.diagnostic = {'authored': name}

    def stop(self):
        self.events.append(self.name + '-stop')
        self.reap()

    def cleanup(self):
        self.events.append(self.name + '-cleanup')
        self.reap()

    def report(self):
        assert self.process.poll() is not None
        self.events.append(self.name + '-report')
        return {'authored': self.name, 'stopped': True}

    def reap(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=5)


def test_close_order_and_shared_late_receipt_are_visible_after_original_exception(tmp_path, monkeypatch):
    events, handles = [], []
    workspace = TaskWorkspace()
    workspace.allocate_stepwise()
    workspace.stage_runtime({}, stage=lambda _, path: path)
    receipt, evidence = {}, {}
    session = task_session.TaskSession(workspace, receipt)
    assert session.receipt is receipt
    for attribute, name in [('tool', 'tool'), ('stepwise', 'stepwise'), ('model', 'model'),
                            ('web', 'web'), ('services', 'services')]:
        handle = OwnedProcess(name, events)
        handles.append(handle)
        setattr(session, attribute, handle)
    def http_report():
        assert session.model.process.poll() is not None
        events.append('http-report')
        return {'complete': True}
    session.model.http = SimpleNamespace(report=http_report)
    session.network_name, session.owns_network = 'owned', True
    monkeypatch.setattr(task_session.services, 'remove_network', lambda _: events.append('network-remove'))
    monkeypatch.setattr(responses_overlay, 'collect_capture', lambda _: events.append('bun') or {'complete': True})
    for name, label in [('remove_stepwise', 'stepwise-dir'), ('remove_task', 'task-dir'),
                        ('remove_runtime', 'runtime-dir')]:
        original = getattr(workspace, name)
        def close(original=original, label=label):
            events.append(label)
            original()
        monkeypatch.setattr(workspace, name, close)
    output = SimpleNamespace(evidence=evidence, capture_pending=lambda: events.append('evidence'))
    failure = KeyboardInterrupt('authored post-acquisition cancellation')
    try:
        with pytest.raises(KeyboardInterrupt) as raised:
            try:
                raise failure
            finally:
                session.close(output, responses_enabled=lambda: True, native_model=lambda _: True,
                              task_label=lambda: 'authored')
        assert raised.value is failure
        assert events == ['evidence', 'bun', 'tool-cleanup', 'tool-report', 'stepwise-stop',
                          'stepwise-dir', 'model-stop', 'http-report', 'web-stop', 'network-remove',
                          'services-stop', 'task-dir', 'runtime-dir']
        assert receipt == {'responses_capture': {'complete': True},
                           'tool_relay': {'authored': 'tool', 'stopped': True},
                           'agent_model_http': {'complete': True}}
        assert evidence['tool_relay'] == {'authored': 'tool'}
        assert all(handle.process.poll() is not None for handle in handles)
        assert not workspace.path.exists() and not workspace.runtime_dir.exists() and not workspace.stepwise_dir.exists()
    finally:
        for handle in handles:
            handle.reap()
        workspace.remove_stepwise()
        workspace.remove_task()
        workspace.remove_runtime()


@pytest.mark.parametrize('acquired', range(9))
@pytest.mark.parametrize('error_type', [OSError, KeyboardInterrupt])
def test_failure_after_each_acquired_prefix_closes_only_owned_resources(tmp_path, monkeypatch, acquired, error_type):
    workspace = TaskWorkspace()
    retained = tmp_path / 'operator'
    retained.mkdir()
    (retained / 'sentinel').write_text('retained')
    state = tmp_path / '.state'
    workspace.ensure_state(state)
    (state / 'sentinel').write_text('retained checkpoint')
    archive = tmp_path / 'private-archive'
    archive.mkdir()
    (archive / 'sentinel').write_text('retained private archive')
    session = task_session.TaskSession(workspace, {})
    events, handles = [], []
    if acquired >= 1:
        workspace.stage_runtime({}, stage=lambda _, path: path)
    if acquired >= 2:
        workspace.allocate_stepwise()
    for threshold, attribute in [(3, 'services'), (5, 'model'), (6, 'web'), (7, 'stepwise'), (8, 'tool')]:
        if acquired >= threshold:
            handle = OwnedProcess(attribute, events)
            handles.append(handle)
            setattr(session, attribute, handle)
    if acquired >= 4:
        session.network_name, session.owns_network = 'owned', True
    monkeypatch.setattr(task_session.services, 'remove_network', lambda name: events.append(('network', name)))
    output = SimpleNamespace(evidence=None, capture_pending=lambda: None)
    failure = error_type('authored acquisition-prefix fault')
    try:
        with pytest.raises(error_type) as raised:
            try:
                raise failure
            finally:
                session.close(output, responses_enabled=lambda: False, native_model=lambda _: False,
                              task_label=lambda: 'authored')
        assert raised.value is failure
        assert all(handle.process.poll() is not None for handle in handles)
        assert (('network', 'owned') in events) == (acquired >= 4)
        assert not workspace.path.exists()
        assert workspace.runtime_dir is None or not workspace.runtime_dir.exists()
        assert workspace.stepwise_dir is None or not workspace.stepwise_dir.exists()
        assert (retained / 'sentinel').read_text() == 'retained'
        assert (state / 'sentinel').read_text() == 'retained checkpoint'
        assert (archive / 'sentinel').read_text() == 'retained private archive'
    finally:
        for handle in handles:
            handle.reap()
        workspace.remove_stepwise()
        workspace.remove_task()
        workspace.remove_runtime()


def test_borrowed_network_and_no_warning_do_not_touch_operator_label(monkeypatch):
    workspace = TaskWorkspace()
    session = task_session.TaskSession(workspace, {})
    session.network_name = 'operator-network'
    monkeypatch.setattr(task_session.services, 'remove_network',
                        lambda _: pytest.fail('borrowed network was removed'))
    try:
        session.close(SimpleNamespace(evidence=None, capture_pending=lambda: None),
            responses_enabled=lambda: False, native_model=lambda _: False,
            task_label=lambda: (_ for _ in ()).throw(AssertionError('unused label read')))
    finally:
        workspace.remove_task()
