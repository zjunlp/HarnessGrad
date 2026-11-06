import base64
import json
import os
import subprocess
import sys

import pytest

from console import round_snapshot as rs
from harnessgrad import snapshot


def artifact(files, **changes):
    value = {"schema": snapshot.SCHEMA, "run_id": "run", "round": 2,
             "harness_sha": None, "parent_id": None, "files": files}
    value.update(changes)
    value["id"] = snapshot._digest(value)
    return json.dumps(value).encode()


def entry(data=b"x", executable=False):
    return {"content_b64": base64.b64encode(data).decode(), "executable": executable}


def run(tmp_path, data, *, expected_run="run", expected_round=2):
    source = tmp_path / "source.json"
    source.write_bytes(data)
    dest = tmp_path / "dest"
    dest.mkdir()
    s = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    d = os.open(dest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return rs.materialize(s, d, run_id=expected_run, round_no=expected_round)
    finally:
        os.close(s)
        os.close(d)


def test_roundtrip_existing_writer(tmp_path):
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "script").write_bytes(b"hello\x00")
    (tree / "sub" / "script").chmod(0o755)
    path = snapshot.write(tmp_path / "run_dir", 2, tree, run_id="run", harness_sha="abc")
    result = run(tmp_path, path.read_bytes())
    assert result["id"] == snapshot.load(path)["id"]
    snapshot.verify(path, tmp_path / "dest")


@pytest.mark.parametrize("files", [
    {"../escape": entry()}, {"/absolute": entry()}, {"a//b": entry()},
    {"a": entry(), "a/b": entry()}, {"A/x": entry(), "a/y": entry()},
    {"a": {"content_b64": "!!!", "executable": False}},
    {"/".join(["a"] * 65): entry()},
])
def test_rejects_malicious_files(tmp_path, files):
    with pytest.raises(snapshot.SnapshotInvalid):
        run(tmp_path, artifact(files))
    assert not list((tmp_path / "dest").iterdir())


def test_identity_digest_and_duplicate_keys(tmp_path):
    for i, data in enumerate([artifact({}, run_id="other"),
                              artifact({}).replace(b'"round": 2', b'"round": 3'),
                              b'{"a":1,"a":2}', b'[' * 2000]):
        case = tmp_path / str(i)
        case.mkdir()
        with pytest.raises(snapshot.SnapshotInvalid):
            run(case, data)


def test_source_hardlink_and_nonempty_destination(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(artifact({}))
    os.link(source, tmp_path / "link")
    dest = tmp_path / "dest"
    dest.mkdir()
    s = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    d = os.open(dest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with pytest.raises(snapshot.SnapshotInvalid):
            rs.materialize(s, d, run_id="run", round_no=2)
        os.unlink(tmp_path / "link")
        (dest / "link").symlink_to(tmp_path)
        with pytest.raises(snapshot.SnapshotInvalid):
            rs.materialize(s, d, run_id="run", round_no=2)
    finally:
        os.close(s)
        os.close(d)


def test_prefix_budget_rejects_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "MAX_PREFIXES", 2)
    with pytest.raises(snapshot.SnapshotInvalid, match="prefix limit"):
        run(tmp_path, artifact({"a/b/c": entry()}))
    assert not list((tmp_path / "dest").iterdir())


def test_many_directories_under_low_fd_limit(tmp_path):
    source = tmp_path / "source.json"
    source.write_bytes(artifact({f"dir{i}/file": entry() for i in range(200)}))
    dest = tmp_path / "dest"
    dest.mkdir()
    script = """
import os, resource, sys
from console.round_snapshot import materialize
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
s = os.open(sys.argv[1], os.O_RDONLY)
d = os.open(sys.argv[2], os.O_RDONLY | os.O_DIRECTORY)
materialize(s, d, run_id='run', round_no=2)
"""
    subprocess.run([sys.executable, "-c", script, str(source), str(dest)], check=True)
    assert len(list(dest.iterdir())) == 200


def test_limits_and_io_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "MAX_CONTENT", 2)
    with pytest.raises(snapshot.SnapshotInvalid):
        run(tmp_path, artifact({"a": entry(b"123")}))
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(rs.os, "write", lambda *args: (_ for _ in ()).throw(OSError(28, "full")))
    with pytest.raises(snapshot.SnapshotInvalid):
        run(other, artifact({"a": entry()}))
