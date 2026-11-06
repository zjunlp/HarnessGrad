"""Offline, synthetic tests for the workbench selection boundary."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

SERVER = Path(__file__).resolve().parents[2] / "console" / "server.py"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("selection_server", SERVER)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    root = tmp_path / "candidates"
    candidate = root / "good"
    candidate.mkdir(parents=True)
    (candidate / "harness.json").write_text("{}")
    (candidate / "ok.txt").write_bytes("一\r\n二\n".encode() + b"\xff\n")
    sibling = root / "sibling"
    sibling.mkdir()
    (sibling / "secret").write_text("SENSITIVE_PAYLOAD")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "harness.json").write_text("{}")
    (outside / "secret").write_text("SENSITIVE_PAYLOAD")
    (candidate / "leaf").symlink_to(outside / "secret")
    (candidate / "nested").symlink_to(outside, target_is_directory=True)
    (candidate / "sibling").symlink_to(sibling, target_is_directory=True)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(server, "CANDIDATES", root)
    assistant = Mock(return_value=(200, {"ok": True}))
    monkeypatch.setattr(server, "workbench_edit", assistant)
    return server, root, outside, assistant


def request(server, body):
    class Fake:
        path = "/api/workbench/edit"
        _api_guard = lambda self: True
        _post_framing = lambda self, **kw: True
        _json_body = lambda self: body
        _send = lambda self, payload, code=200: (code, payload)
    return server.Handler.do_POST(Fake())


@pytest.mark.parametrize("name,path,code", [
    (".", "ok.txt", 404), ("..", "outside/secret", 404),
    ("../outside", "secret", 404), ("/tmp", "secret", 404),
    ("bad/name", "secret", 404), ("good", "../sibling/secret", 400),
    ("good", "/etc/passwd", 400), ("good", "leaf", 400),
    ("good", "nested/secret", 400), ("good", "sibling/secret", 400),
    ("linked", "secret", 400),
])
def test_rejects_escape_before_assistant(setup, name, path, code):
    server, _, _, assistant = setup
    result = request(server, {"name": name, "instruction": "edit", "selection": {"path": path}})
    assert result[0] == code
    assert result[1]["error"] == ("candidate-not-found" if code == 404 else "bad-selection")
    assert "SENSITIVE_PAYLOAD" not in str(result)
    assistant.assert_not_called()


def test_valid_unicode_selection_only_reaches_mock(setup):
    server, _, _, assistant = setup
    code, _ = request(server, {"name": "good", "instruction": "edit", "selection":
                              {"path": "ok.txt", "from": 1, "to": 3}})
    assert code == 200
    assert assistant.call_args.args[2] == "ok.txt:1-3\n    1  一\n    2  二\n    3  �"


def test_limits_and_special_files(setup):
    server, root, _, assistant = setup
    (root / "good" / "large").write_bytes(b"a" * (server.SELECTION_MAX_BYTES + 1))
    for path, start, end in [("large", 1, 1), ("ok.txt", 1, 10001), (".", 1, 1)]:
        code, result = request(server, {"name": "good", "selection":
                                      {"path": path, "from": start, "to": end}})
        assert (code, result["error"]) == (400, "bad-selection")
    assistant.assert_not_called()


def test_no_secure_open_support_fails_closed(setup, monkeypatch):
    server, _, _, assistant = setup
    monkeypatch.setattr(server.os, "supports_dir_fd", set())
    code, result = request(server, {"name": "good", "selection": {"path": "ok.txt"}})
    assert (code, result["error"]) == (400, "bad-selection")
    assistant.assert_not_called()
