"""Synthetic metadata/privacy controls; no task materialization or execution."""
from __future__ import annotations

import builtins
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
METADATA_NAMES = ("ENV", "SETUP", "VERIFY")
DATA_HOOKS = ("records", "load", "control_task", "oracle_files", "_task")
READ_SEAMS = (
    "builtins.open", "io.open", "os.open", "Path.open", "Path.read_text",
    "Path.read_bytes", "json.load", "json.loads", *DATA_HOOKS,
)


class MetadataReadDetected(AssertionError):
    """A guarded metadata request tried to cross the read boundary."""


@pytest.fixture
def adapter_code():
    # Only Python source is read, before the request boundary. Execute a fresh
    # module body in every control so a cached adapter cannot mask eager reads.
    path = ROOT / "data/datasets/agentdojo.py"
    return compile(path.read_text(encoding="utf-8"), str(path), "exec"), path


@contextmanager
def metadata_boundary(monkeypatch, adapter_code):
    code, path = adapter_code
    attempts = []

    def denied(label):
        def forbidden(*args, **kwargs):
            attempts.append(label)
            raise MetadataReadDetected(f"metadata boundary blocked {label}")
        return forbidden

    module = ModuleType("agentdojo_metadata_synthetic_codex180")
    module.__file__ = str(path)
    dependency = ModuleType("data.scaffolds.qa.base")
    dependency.is_eval = denied("split classification")
    original_import = builtins.__import__

    def metadata_import(name, *args, **kwargs):
        # `data.scaffolds.roots` answers "where do this dataset's bytes live": it reads
        # environment variables and stats the legacy path, and it opens nothing. The
        # property here is "computing ENV/SETUP/VERIFY reads no dataset bytes", so a path
        # resolver belongs on the allowlist and a provisioned-pool reader would not.
        # `from data.scaffolds import roots` reaches `__import__` as `data.scaffolds`
        # (the package), not as `data.scaffolds.roots`; both spellings are on the list so
        # the guard keeps refusing a *reader* while allowing a path resolver.
        if name not in {"__future__", "json", "os", "subprocess", "pathlib", "harnessgrad.paths",
                        "harnessgrad.dataset_runtime", "data.scaffolds",
                        "data.scaffolds.qa", "data.scaffolds.qa.base",
                        "data.scaffolds.roots"}:
            denied("non-metadata import")()
        return original_import(name, *args, **kwargs)

    import harnessgrad.paths as paths
    import harnessgrad.dataset_runtime
    # No real QA dependency, provisioned pool, runtime or verifier is imported.
    # Restore the guards before pytest renders results or tears fixtures down.
    with monkeypatch.context() as guard:
        guard.setattr(paths, "dataset_root", lambda name: ROOT / ".scratch/metadata-codex180/source")
        guard.setitem(sys.modules, "data.scaffolds.qa.base", dependency)
        for owner, label in ((builtins, "builtins"), (io, "io"), (os, "os")):
            guard.setattr(owner, "open", denied(f"{label}.open"))
        for method in ("open", "read_text", "read_bytes"):
            guard.setattr(Path, method, denied(f"Path.{method}"))
        for method in ("load", "loads"):
            guard.setattr(json, method, denied(f"json.{method}"))
        guard.setattr(socket, "socket", denied("network"))
        guard.setattr(socket, "create_connection", denied("network"))
        guard.setattr(subprocess, "Popen", denied("subprocess"))
        module.__dict__["__builtins__"] = {**vars(builtins), "__import__": metadata_import}
        exec(code, module.__dict__)
        assert attempts == []
        for hook in DATA_HOOKS:
            guard.setattr(module, hook, denied(hook))
        # Five fixed IDs and inert source paths; no synthetic data files exist.
        # Public directory IDs are the metadata seam; TASK_COUNT is derived lazily
        # from records() and must never be requested here.
        guard.setattr(module, "_indices", lambda: list(range(5)))
        guard.setattr(module, "DATASET_ROOT", ROOT / ".scratch/metadata-codex180/source")
        guard.setattr(module, "ASSETS", ROOT / ".scratch/metadata-codex180/assets")
        runtime = ROOT / '.scratch/metadata-codex180/artifact/venv'
        guard.setattr(module, '_execution_runtime', lambda: {
            'dataset': 'agentdojo', 'profile': 'execution', 'runtime_id': 'a' * 64,
            'manifest_path': str(runtime.parent / 'artifact.json'), 'path': str(runtime),
            'python': str(runtime / 'bin/python'), 'runtime_paths': [str(runtime)]})
        yield module, attempts, guard


def selected_metadata(module, name, index):
    return getattr(module, name)[f"agentdojo/{index}"]


@pytest.mark.parametrize("name", METADATA_NAMES)
def test_metadata_maps_need_no_dataset_reads(name, monkeypatch, adapter_code):
    with metadata_boundary(monkeypatch, adapter_code) as (module, attempts, _):
        metadata = getattr(module, name)
        assert set(metadata) == {f"agentdojo/{i}" for i in range(5)}
        assert attempts == []


@pytest.mark.parametrize("name", METADATA_NAMES)
@pytest.mark.parametrize("index", [0, 2, 4])
def test_selected_lookup_has_only_fixed_metadata(name, index, monkeypatch, adapter_code):
    with metadata_boundary(monkeypatch, adapter_code) as (module, attempts, _):
        metadata = selected_metadata(module, name, index)
        if name == "ENV":
            assert metadata == {"kind": "files"}
        elif name == "SETUP":
            # `files` is what the agent receives; `tool_relay` is a declaration the platform
            # reads to start the host-side environment (`eval/run/tool_relay.py`). One program
            # and no data: no snapshot, no state file, no per-task runtime to copy.
            assert set(metadata) == {"files", "tool_relay"}
            assert metadata["files"] == [
                {"path": "dojo.py", "from": str(module.ASSETS / "cli/dojo")},
            ]
            relay = metadata["tool_relay"]
            assert set(relay) == {"program", "python", "python_runtime", "read_paths", "content_flag", "filename", "content"}
            assert relay["content_flag"] == "--case"
            assert "/case.json" in relay["content"]
            assert relay["python_runtime"]["role"] == "service"
        else:
            # Check metadata identities and selected paths, leaving argv opaque.
            assert set(metadata) == {"kind", "argv", "inputs", "reward_file", "evidence_file",
                                     "python_runtime", "runtime_paths", "result_contract"}
            assert metadata["kind"] == "command"
            assert {entry['path'] for entry in metadata['inputs']} == {
                'dojo_code', 'verify/case.json', 'agentdojo_source'}
            assert 'trusted_runtime' not in str(metadata['inputs'])
            assert metadata['argv'][1:3] == ['-I', '-B']
            assert metadata['python_runtime']['role'] == 'verifier'
            assert metadata["reward_file"] == "/verifier/reward.txt"
            assert metadata["evidence_file"] == "/verifier/evidence.json"
        assert attempts == []


@pytest.mark.parametrize("name", METADATA_NAMES)
@pytest.mark.parametrize("task_id", ["agentdojo/-1", "agentdojo/5", "agentdojo/02", "other/2"])
def test_unknown_selection_does_not_fall_back_to_task_reads(
    name, task_id, monkeypatch, adapter_code,
):
    with metadata_boundary(monkeypatch, adapter_code) as (module, attempts, _):
        with pytest.raises(KeyError):
            getattr(module, name)[task_id]
        assert attempts == []


@pytest.mark.parametrize("name", METADATA_NAMES)
@pytest.mark.parametrize("seam", READ_SEAMS)
def test_selected_lookup_detects_a_read_before_it_can_happen(
    name, seam, monkeypatch, adapter_code,
):
    with metadata_boundary(monkeypatch, adapter_code) as (module, attempts, guard):
        # Inject one forbidden call at the lookup boundary. Guards must stop it
        # before I/O or a catalog/task/reference hook can run, for every map.
        original_lookup = module.__getattr__
        inert_path = module.DATASET_ROOT / "absent-read-canary"
        probes = {
            "builtins.open": lambda: builtins.open(inert_path),
            "io.open": lambda: io.open(inert_path),
            "os.open": lambda: os.open(inert_path, os.O_RDONLY),
            "Path.open": lambda: inert_path.open(),
            "Path.read_text": lambda: inert_path.read_text(),
            "Path.read_bytes": lambda: inert_path.read_bytes(),
            "json.load": lambda: json.load(None),
            "json.loads": lambda: json.loads("{}"),
        }
        probes.update({hook: getattr(module, hook) for hook in DATA_HOOKS})

        def leaking_lookup(attribute):
            if attribute == name:
                probes[seam]()
            return original_lookup(attribute)

        guard.setattr(module, "__getattr__", leaking_lookup)
        with pytest.raises(MetadataReadDetected) as caught:
            selected_metadata(module, name, 2)
        assert str(caught.value) == f"metadata boundary blocked {seam}"
        assert attempts == [seam]
