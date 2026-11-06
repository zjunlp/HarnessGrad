"""Synthetic offline coverage for candidate file reads."""
import importlib.util
import os
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[2] / "console" / "server.py"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("candidate_file_server", SERVER)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    root = tmp_path / "candidates"
    candidate = root / "good"
    candidate.mkdir(parents=True)
    (candidate / "harness.json").write_text("{}")
    (candidate / "ok").write_bytes(b"hello\r\nworld\r\xff\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("SENSITIVE_PAYLOAD")
    (candidate / "leaf").symlink_to(outside / "secret")
    (candidate / "nested").symlink_to(outside, target_is_directory=True)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(server, "CANDIDATES", root)
    return server, root, outside


def test_valid_content_and_replacement(setup):
    server, _, _ = setup
    assert server.harness_candidate_file("good", "ok") == {
        "candidate": "good", "path": "ok", "text": "hello\nworld\n�\n",
        "lines": 3, "size": 15,
    }


def test_get_route_blocks_escape(setup):
    server, _, _ = setup
    class Fake:
        path = "/api/harness/file?candidate=..&path=outside/secret"
        _api_guard = lambda self: True
        _send = lambda self, payload, code=200: (code, payload)
    code, result = server.Handler.do_GET(Fake())
    assert code == 200
    assert result["error"] == "invalid-candidate"
    assert "SENSITIVE_PAYLOAD" not in str(result)


@pytest.mark.parametrize("name,path", [
    (".", "outside/secret"), ("..", "outside/secret"), ("/tmp", "secret"),
    ("bad/name", "secret"), ("bad\x00name", "secret"), (None, "ok"),
    ("good", "../outside/secret"), ("good", "/etc/passwd"),
    ("good", "a\x00b"), ("good", ""), ("good", None),
    ("good", "leaf"), ("good", "nested/secret"), ("linked", "secret"),
])
def test_unsafe_inputs_do_not_disclose(setup, name, path):
    server, _, _ = setup
    result = server.harness_candidate_file(name, path)
    assert "error" in result
    assert "SENSITIVE_PAYLOAD" not in str(result)
    assert "text" not in result


def test_root_symlink_fifo_and_size_cap(setup, tmp_path, monkeypatch):
    server, root, outside = setup
    os.mkfifo(root / "good" / "fifo")
    assert server.harness_candidate_file("good", "fifo")["error"] == "not-regular-file"
    (root / "good" / "large").write_bytes(b"x" * (server.CANDIDATE_FILE_MAX_BYTES + 1))
    assert server.harness_candidate_file("good", "large")["error"] == "file-too-large"
    (root / "good" / "exact").write_bytes(b"x" * server.CANDIDATE_FILE_MAX_BYTES)
    assert server.harness_candidate_file("good", "exact")["size"] == server.CANDIDATE_FILE_MAX_BYTES
    link = tmp_path / "root-link"
    link.symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(server, "CANDIDATES", link)
    assert "error" in server.harness_candidate_file("good", "ok")


@pytest.mark.parametrize("missing", ["O_NOFOLLOW", "dir_fd", "platform"])
def test_unsupported_fails_closed(setup, monkeypatch, missing):
    server, _, _ = setup
    if missing == "O_NOFOLLOW":
        monkeypatch.delattr(server.os, "O_NOFOLLOW")
    elif missing == "dir_fd":
        monkeypatch.setattr(server.os, "supports_dir_fd", set())
    else:
        monkeypatch.setattr(server.sys, "platform", "unsupported")
    assert server.harness_candidate_file("good", "ok")["error"] == "unsupported"
