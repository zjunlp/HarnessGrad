"""Native host compatibility uses capabilities, with authored inputs only."""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from eval.verification import native_runtime, verifier

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native runtimes require Linux")


def bind_manifest(path, manifest):
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return {"dataset": manifest["dataset"], "manifest": str(path),
            "sha256": hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def declaration(tmp_path):
    payload = tmp_path / "payload"
    payload.write_bytes(b"authored snapshot, never executed\n")
    entry = {"source": str(payload), "sha256": hashlib.sha256(payload.read_bytes()).hexdigest()}
    manifest = {"schema": 1, "dataset": "bird_minidev",
                "identity": {"machine": platform.machine(), "kernel": "different-build-kernel"},
                "interpreter": "/runtime/python", "loader": "/runtime/loader",
                "library_path": [], "environment": {}, "python_flags": ["-I", "-S", "-B"],
                "files": {name: dict(entry) for name in
                          ("/runtime/python", "/runtime/loader", "/scorer/check.py")},
                "launcher": {"path": str(payload), "sha256": entry["sha256"]}}
    return tmp_path / "manifest.json", manifest


def test_different_kernel_preserves_build_identity_and_records_host(declaration, monkeypatch):
    path, manifest = declaration
    binding = bind_manifest(path, manifest)
    probes = []

    def probe(runtime):
        probes.append(runtime)
        return {"system": "Linux", "machine": platform.machine(),
                "kernel": platform.release(), "admission_policy": "linux-capabilities-v1"}

    monkeypatch.setattr(native_runtime, "_check_host", probe, raising=False)
    with native_runtime.admit(binding) as runtime:
        assert runtime.receipt["identity"]["kernel"] == "different-build-kernel"
        assert runtime.receipt["host"]["kernel"] == platform.release()
        assert runtime.receipt["sha256"] == binding["sha256"]
        assert probes == [runtime]
        descriptors = [*runtime.fds.values(), runtime.launcher]
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("failure", ["system", "machine", "manifest"])
def test_incompatible_declaration_is_refused_before_payload_reads(declaration, monkeypatch, failure):
    path, manifest = declaration
    if failure == "machine":
        manifest["identity"]["machine"] = "another-architecture"
    binding = bind_manifest(path, manifest)
    if failure == "system":
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
    elif failure == "manifest":
        binding["sha256"] = "0" * 64

    def no_read(*args):
        pytest.fail("incompatible declaration reached payload files")

    monkeypatch.setattr(native_runtime, "_snapshot", no_read)
    with pytest.raises(native_runtime.NativeRuntimeUnavailable, match={
            "system": "Linux", "machine": "machine", "manifest": "manifest digest"}[failure]):
        with native_runtime.admit(binding):
            pytest.fail("incompatible declaration admitted")


@pytest.mark.parametrize("failure", ["exit", "empty-output", "timeout", "launch"])
def test_same_kernel_still_requires_successful_capability_probe(declaration, monkeypatch, failure):
    path, manifest = declaration
    manifest["identity"]["kernel"] = platform.release()
    binding = bind_manifest(path, manifest)

    def fail_probe(self, argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        if failure == "launch":
            raise PermissionError("namespace launcher unavailable")
        return subprocess.CompletedProcess(argv, 1 if failure == "exit" else 0,
                                           "", "namespace feature unavailable")

    monkeypatch.setattr(native_runtime.FrozenRuntime, "run", fail_probe)
    spec = {"kind": "command", "argv": ["python3", "check.py"], "native_runtime": binding,
            "reward_file": "/verifier/reward.txt", "evidence_file": "/verifier/evidence.json"}
    verdict = verifier._verify(path.parent, spec, "", sandbox=True, timeout_s=10)
    assert verdict["score"] is None and verdict["outcome"] == "unmeasured"
    assert "host capability" in verdict["unmeasured"]
    assert verdict["native_runtime"]["verified"] is False


@pytest.mark.parametrize("failure", ["missing-function", "add-seals", "missing-seals"])
def test_missing_memfd_or_seals_is_refused(tmp_path, monkeypatch, failure):
    source = tmp_path / "payload"
    source.write_bytes(b"authored bytes")
    if failure == "missing-function":
        monkeypatch.setattr(native_runtime.ctypes, "CDLL", lambda *a, **kw: SimpleNamespace())
    else:
        original = fcntl.fcntl

        def fcntl_without_seals(fd, command, *args):
            if failure == "add-seals" and command == native_runtime.F_ADD_SEALS:
                raise OSError(errno.EINVAL, "file sealing unsupported")
            if failure == "missing-seals" and command == native_runtime.F_GET_SEALS:
                return 0
            return original(fd, command, *args)

        monkeypatch.setattr(fcntl, "fcntl", fcntl_without_seals)
    fd = None
    try:
        with pytest.raises(native_runtime.NativeRuntimeUnavailable, match="memfd|sealing"):
            fd = native_runtime._snapshot(source, hashlib.sha256(source.read_bytes()).hexdigest())
    finally:
        if fd is not None:
            os.close(fd)


@pytest.mark.parametrize("failure", ["payload", "launcher"])
def test_kernel_change_never_waives_byte_pins(declaration, monkeypatch, failure):
    path, manifest = declaration
    if failure == "payload":
        manifest["files"][manifest["interpreter"]]["sha256"] = "0" * 64
    else:
        manifest["launcher"]["sha256"] = "0" * 64
    binding = bind_manifest(path, manifest)

    def no_probe(*args):
        pytest.fail("changed bytes reached execution")

    monkeypatch.setattr(native_runtime, "_check_host", no_probe, raising=False)
    with pytest.raises(native_runtime.NativeRuntimeUnavailable, match="payload digest mismatch"):
        with native_runtime.admit(binding):
            pytest.fail("changed bytes admitted")


@pytest.fixture(scope="module")
def captured_runtime(tmp_path_factory):
    for tool in ("bwrap", "readelf", "ldd"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for the local sealed-runtime control")
    root = tmp_path_factory.mktemp("native-capability-runtime")
    manifest_path = root / "manifest.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools/build_bird_native_runtime.py"),
         "--dataset", "bird_minidev", "--out", str(root / "capture"),
         "--manifest", str(manifest_path), "--store", str(root / "store")],
        cwd=ROOT, env={"PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    manifest = json.loads(manifest_path.read_text())
    manifest["identity"]["kernel"] = "6.8.0-136-generic"
    if platform.release() == manifest["identity"]["kernel"]:
        manifest["identity"]["kernel"] = "6.8.0-138-generic"
    return bind_manifest(manifest_path, manifest), manifest


@pytest.mark.parametrize('weakened', ['network', 'runtime-mount', 'task-mount'])
def test_real_probe_refuses_weakened_isolation(captured_runtime, monkeypatch, weakened):
    binding, _ = captured_runtime
    original_run = subprocess.run

    def weakened_plan(argv, *args, **kwargs):
        argv = list(argv)
        if argv and argv[0].startswith('/proc/self/fd/'):
            if weakened == 'network':
                argv.insert(argv.index('--unshare-all') + 1, '--share-net')
            elif weakened == 'runtime-mount':
                argv = ['--bind-data' if arg == '--ro-bind-data' else arg for arg in argv]
            else:
                for i in range(len(argv) - 2):
                    if argv[i] == '--ro-bind' and argv[i + 2] == '/task':
                        argv[i] = '--bind'
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', weakened_plan)
    detail = {'network': 'host namespace retained: net',
              'runtime-mount': 'writable native mount: /scorer/check.py',
              'task-mount': 'writable native mount: /task/probe.json'}[weakened]
    with pytest.raises(native_runtime.NativeRuntimeUnavailable, match=detail):
        with native_runtime.admit(binding):
            pytest.fail('weakened native isolation admitted')


def test_different_build_kernel_executes_sealed_authored_scorer(captured_runtime, tmp_path):
    from data.scaffolds.scorer_input import bind
    from harnessgrad.records import _save_verdicts

    binding, manifest = captured_runtime
    database = tmp_path / "source.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("create table numbers (n int)")
        connection.executemany("insert into numbers values (?)", [(1,), (2,)])
    work = tmp_path / "task"
    work.mkdir()
    (work / "sqlite3.py").write_text("raise RuntimeError('actor shadow')")
    spec = {"kind": "command", "native_runtime": binding,
            "argv": ["python3", "check.py", "--db", "database.sqlite", "--gold", "gold.sql",
                     "--pred", "answer.sql", "--reward-file", "verifier/reward.txt",
                     "--evidence-file", "verifier/evidence.json"],
            "reward_file": "/verifier/reward.txt", "evidence_file": "/verifier/evidence.json",
            "inputs": [{"path": "check.py", "from": str(ROOT / "data/datasets/bird_minidev/verify/check.py")},
                       {"path": "database.sqlite", "from": str(database)},
                       {"path": "gold.sql", "content": "select n from numbers"}]}
    spec = bind(spec, "authored-kernel-capabilities", ["database.sqlite", "gold.sql"])
    for sql, score in (("select n from numbers", 1.0), ("select n from numbers where n=1", 0.0)):
        (work / "answer.sql").write_text(sql)
        result = verifier._verify(work, spec, "", sandbox=True, timeout_s=30,
                                  task_id="authored-kernel-capabilities", env={"kind": "files"})
        assert result.get("score") == score and not result.get("unmeasured"), result
        receipt = result["native_runtime"]
        assert receipt["verified"] and receipt["sha256"] == binding["sha256"]
        assert receipt["identity"]["kernel"] == manifest["identity"]["kernel"]
        assert receipt["host"]["kernel"] == platform.release()
        assert receipt["host"]["kernel"] != receipt["identity"]["kernel"]
        assert receipt["host"]["capabilities"]["sealed_memfd"]
        assert receipt["host"]["capabilities"]["private_namespaces"] == ["user", "mnt", "pid", "net", "ipc", "uts"]
        assert receipt["host"]["capabilities"]["child_python"]
    _save_verdicts(tmp_path, 0, {"per_task": {"authored": 0.0}, "verdicts": {"authored": result},
                                "trial_verdicts": {"authored": [result]}}, ["authored"])
    saved = json.loads((tmp_path / "verdicts/round-0/authored.json").read_text())
    assert saved["native_runtime"]["host"] == receipt["host"]
    assert saved["trials"][0]["native_runtime"]["host"] == receipt["host"]


def test_cache_separates_real_sealed_scorers_and_checks_payload_before_lookup(
        captured_runtime, tmp_path, monkeypatch):
    from copy import deepcopy
    from eval.run import runner

    _, base = captured_runtime
    profiles = []
    for label, score in (('A', 1.0), ('B', 0.0)):
        source = tmp_path / (label + '-check.py')
        source.write_text("from pathlib import Path\nimport json\n"
                          f"Path('verifier/reward.txt').write_text({str(score)!r})\n"
                          "Path('verifier/evidence.json').write_text(json.dumps({'measured':True}))\n")
        manifest = deepcopy(base)
        manifest['files']['/scorer/check.py'] = {
            'source': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'size': source.stat().st_size, 'entry': 'authored native cache scorer'}
        binding = bind_manifest(tmp_path / (label + '-manifest.json'), manifest)
        profiles.append((binding, source))
    repo = tmp_path / 'harness'
    repo.mkdir()
    (repo / 'harness.json').write_text(json.dumps({'name': 'authored-native-cache', 'entrypoint': 'agent.py'}))
    task = {'task_id': 'authored-native-cache', 'goal': 'authored native cache control'}
    calls = []

    def grade(repo, task, **kwargs):
        calls.append(task['task_id'])
        work = tmp_path / ('grade-' + str(len(calls)))
        work.mkdir()
        result = verifier._verify(work, kwargs['verifier'], '', sandbox=True, timeout_s=30)
        return {'task_id': task['task_id'], 'answer': 'authored', 'trace': '', 'exit_code': 0,
                'stderr': '', 'verdict': result}

    class Cache(dict):
        reads = 0

        def get(self, *args):
            self.reads += 1
            return super().get(*args)

    cache = Cache()
    monkeypatch.setattr(runner, 'run_one', grade)
    monkeypatch.setattr(runner, 'snapshot', lambda root: {})

    def evaluate(binding):
        spec = {'kind': 'command', 'argv': ['python3', 'check.py'], 'native_runtime': binding,
                'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json'}
        return runner._evaluate(repo, [task], {}, cache=cache, harness_sha='same-source',
                                verifiers={task['task_id']: spec}, persist_trial_report=False)

    a, b = profiles
    first = evaluate(a[0])
    assert first['per_task'] == {task['task_id']: 1.0} and not first['cached_tasks']
    assert evaluate(a[0])['cached_tasks'] == [task['task_id']] and len(calls) == 1
    second = evaluate(b[0])
    assert second['per_task'] == {task['task_id']: 0.0} and not second['cached_tasks']
    assert evaluate(b[0])['cached_tasks'] == [task['task_id']] and len(calls) == 2
    assert len(cache) == 2
    for entry in cache.values():
        if entry['verdict']['native_runtime']['sha256'] == b[0]['sha256']:
            entry['verdict']['native_runtime'] = deepcopy(first['verdicts'][task['task_id']]['native_runtime'])
    assert not evaluate(b[0])['cached_tasks'] and len(calls) == 3
    b[1].write_text('changed after cache population')
    reads = cache.reads
    refused = evaluate(b[0])
    assert not refused['cached_tasks'] and not refused['per_task']
    assert refused['invalid'][task['task_id']]['stage'] == 'native runtime preparation'
    assert 'payload digest mismatch' in refused['invalid'][task['task_id']]['detail']
    assert cache.reads == reads and len(calls) == 3
