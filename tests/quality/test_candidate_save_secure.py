"""Offline, synthetic security and compatibility tests for candidate saves."""
import http.client
import importlib.util
import threading
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def env(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("candidate_save_server", ROOT / "console/server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    (base / "harness.json").write_text(json.dumps({"name": "base", "entrypoint": "agent.py", "version": "1"}))
    (base / "agent.py").write_text("base\n")
    (base / "agent.py").chmod(0o755)
    monkeypatch.setattr(server, "CANDIDATES", candidates)
    monkeypatch.setattr(server, "BASE_HARNESS", base)
    return server, candidates, base


@pytest.mark.parametrize("route", ["/api/harness/save", "/api/launch/preview"])
def test_deep_outer_json_gets_http_400(env, route):
    server, candidates, _ = env
    httpd = server.ConsoleServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        body = ('{"x":' + '[' * 20000 + '0' + ']' * 20000 + '}').encode()
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        try:
            conn.request("POST", route, body, {"Content-Type": "application/json"})
            response = conn.getresponse()
            assert response.status == 400
            assert json.loads(response.read())["error"] == "bad-json"
        finally:
            conn.close()
        assert list(candidates.iterdir()) == []
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_save_preserves_seed_and_mode(env):
    server, candidates, _ = env
    status, result = server.harness_candidate_save("new", {"agent.py": "edited", "sub/x": "x"})
    assert status == 201, result
    target = candidates / "new"
    assert json.loads((target / "harness.json").read_text()) == {"name": "new", "entrypoint": "agent.py", "version": "1"}
    assert (target / "agent.py").read_text() == "edited"
    assert stat.S_IMODE((target / "agent.py").stat().st_mode) == 0o755
    assert (target / "sub/x").read_text() == "x"
    assert server.harness_candidate_save("next", {"sub/x": "next"}, source="new")[0] == 201
    assert (candidates / "next/agent.py").read_text() == "edited"


def test_unsupported_candidate_save_has_actionable_guidance(env, monkeypatch):
    server, candidates, _ = env
    monkeypatch.setattr(server.sys, "platform", "darwin")
    status, result = server.harness_candidate_save("new", {"agent.py": "edited"})
    assert status == 503 and result["error"] == "unsupported-platform"
    assert "Linux" in result["detail"] and "bubblewrap" in result["detail"]
    assert not (candidates / "new").exists()

@pytest.mark.parametrize("name", [".", "..", "../bad"])
def test_bad_name(env, name):
    assert env[0].harness_candidate_save(name, {"x": "x"})[0] == 400

@pytest.mark.parametrize("path", ["", "/tmp/x", "../x", "a/../x", "a//x", "a/./x", "x\x00y"])
def test_bad_path(env, path):
    server, candidates, _ = env
    assert server.harness_candidate_save("new", {path: "x"})[0] == 400
    assert not (candidates / "new").exists()

@pytest.mark.parametrize("kind", ["root", "manifest", "file", "directory", "fifo", "hardlink"])
def test_unsafe_source_is_not_published(env, kind):
    server, candidates, _ = env
    outside = candidates.parent / "outside"
    outside.mkdir()
    (outside / "secret").write_text("sentinel")
    (outside / "harness.json").write_text('{"entrypoint":"secret"}')
    source = candidates / "source"
    if kind == "root":
        source.symlink_to(outside, target_is_directory=True)
    else:
        source.mkdir()
        (source / "harness.json").write_text('{"entrypoint":"agent.py"}')
        if kind == "manifest":
            (source / "harness.json").unlink()
            (source / "harness.json").symlink_to(outside / "harness.json")
        elif kind == "file":
            (source / "link").symlink_to(outside / "secret")
        elif kind == "directory":
            (source / "link").symlink_to(outside, target_is_directory=True)
        elif kind == "fifo":
            os.mkfifo(source / "pipe")
        else:
            os.link(outside / "secret", source / "hardlink")
    assert server.harness_candidate_save("new", {"agent.py": "edited"}, source="source")[0] == 400
    assert not (candidates / "new/harness.json").exists()
    assert (outside / "secret").read_text() == "sentinel"


def test_invalid_manifest_can_be_corrected_and_retried(env):
    server, candidates, _ = env
    assert server.harness_candidate_save("new", {"harness.json": "invalid", "sub/x": "x"})[0] == 400
    assert not (candidates / "new").exists()
    assert not server.harness_candidates()["rows"]
    assert server.harness_candidate_save("new", {"agent.py": "fixed"})[0] == 201


def test_deeply_nested_json_returns_400_and_can_retry(env):
    server, candidates, _ = env
    body = '{"entrypoint":' + '[' * 2000 + '0' + ']' * 2000 + '}'
    assert server.harness_candidate_save("new", {"harness.json": body})[0] == 400
    assert not (candidates / "new").exists()
    assert server.harness_candidate_save("new", {"agent.py": "fixed"})[0] == 201


def test_failed_copy_can_be_retried(env):
    server, candidates, base = env
    (base / "link").symlink_to(candidates.parent / "outside")
    assert server.harness_candidate_save("new", {"agent.py": "x"})[0] == 400
    assert not (candidates / "new").exists()
    (base / "link").unlink()
    assert server.harness_candidate_save("new", {"agent.py": "x"})[0] == 201


def test_collision_including_dangling_symlink(env):
    server, candidates, _ = env
    (candidates / "new").symlink_to(candidates / "missing")
    assert server.harness_candidate_save("new", {"agent.py": "x"})[0] == 409
    assert (candidates / "new").is_symlink()


def test_concurrent_save_one_winner(env):
    server, candidates, _ = env
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda x: (x, server.harness_candidate_save("new", {"agent.py": x})[0]), ["one", "two"]))
    assert sorted(status for _, status in results) == [201, 409]
    assert (candidates / "new/agent.py").read_text() == next(x for x, status in results if status == 201)


def test_execute_mode_preserved_under_restrictive_umask(env):
    server, candidates, _ = env
    previous = os.umask(0o111)
    try:
        status, result = server.harness_candidate_save("new", {"agent.py": "edited", "nested/x": "x"})
    finally:
        os.umask(previous)
    assert status == 201, result
    assert stat.S_IMODE((candidates / "new/agent.py").stat().st_mode) == 0o755
    assert (candidates / "new/nested/x").read_text() == "x"


def test_large_seed_file_streamed(env):
    server, candidates, base = env
    data = b"abc" * (1024 * 1024)
    (base / "large.bin").write_bytes(data)
    assert server.harness_candidate_save("new", {"agent.py": "edited"})[0] == 201
    assert (candidates / "new/large.bin").read_bytes() == data


def test_source_budget_enforced_and_retry(env, monkeypatch):
    server, candidates, base = env
    (base / "large.bin").write_bytes(b"a" * 4096)
    monkeypatch.setattr(server, "CANDIDATE_SAVE_MAX_SOURCE_BYTES", 1024)
    assert server.harness_candidate_save("new", {"agent.py": "x"})[0] == 400
    assert not (candidates / "new").exists()
    monkeypatch.setattr(server, "CANDIDATE_SAVE_MAX_SOURCE_BYTES", 8192)
    assert server.harness_candidate_save("new", {"agent.py": "x"})[0] == 201


@pytest.mark.parametrize("entrypoint", [[1], 42, {}, "", "   "])
def test_invalid_entrypoint_type(env, entrypoint):
    server, candidates, _ = env
    status, _ = server.harness_candidate_save("new", {"harness.json": json.dumps({"entrypoint": entrypoint})})
    assert status == 400
    assert not (candidates / "new").exists()


def test_size_limit(env):
    server, candidates, _ = env
    assert server.harness_candidate_save("new", {"agent.py": "x" * (2 * 1024 * 1024 + 1)})[0] == 400
    assert not (candidates / "new").exists()
