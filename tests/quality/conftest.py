"""Opt-in synthetic GSM8K fixture; never opens a provisioned dataset."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

#: **Tests must not write into the tree they are testing.** Several load a harness's
#: `agent.py` with `spec.loader.exec_module`, which caches bytecode beside the source --
#: `base_harness/<name>/__pycache__/`. That is not cosmetic here: `harness_sha` is a content
#: address of the harness tree, `tools/validate_harness.py:source_identity` counts every
#: regular file including caches, and the integrity check hashes the platform's own files.
#: Measured 2026-11-01: after the adapter tests ran, `base_harness/loop` gained a
#: `__pycache__`, and `test_scenario.py`'s receipt assertion failed with `entries` 3 -> 4
#: while the source was byte-identical.
sys.dont_write_bytecode = True
# ... and for every **child** the lane starts (the driver, a harness, the CLI under test). A
# harness that imports a sibling module -- `base_harness/miniswe/agent.py` imports
# `mock_model.py` -- caches bytecode beside its own source, which is a write into the tree the
# platform is hashing. In a sandboxed run that write simply fails; unsandboxed, it lands.
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def scienceworld_goldpaths_archive():
    """Optional original source for fold/study checks; invalid bytes must fail."""
    from harnessgrad.paths import DataRootNotConfigured
    from tools.scienceworld_study_sample import archive_bytes, archive_path

    try:
        path = archive_path()
    except DataRootNotConfigured as exc:
        pytest.skip(f"ScienceWorld goldpaths archive unavailable: {exc}; place "
                    "_src/goldpaths-all.zip under the configured ScienceWorld dataset root")
    if not path.exists():
        pytest.skip(f"ScienceWorld goldpaths archive missing: {path}; place "
                    "_src/goldpaths-all.zip under the configured ScienceWorld dataset root")
    raw, member = archive_bytes(path)
    return path, raw, member


@pytest.fixture(scope="module")
def captured_native_python_manifest(tmp_path_factory):
    """Local Python/stdlib closure for authored controls; no provisioned payload."""
    import shutil
    import subprocess

    for tool in ("bwrap", "readelf", "ldd"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for the local native-runtime control")
    work = tmp_path_factory.mktemp("captured-native-python")
    manifest = work / "manifest.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools/build_bird_native_runtime.py"),
         "--dataset", "bird_minidev", "--out", str(work / "capture"),
         "--manifest", str(manifest), "--store", str(work / "store")],
        cwd=ROOT, env={"PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return manifest


def optional_storage_import():
    """Explicit import/probe scope for tests requiring external dataset storage.

    Never install a global collector catch: mixed source/synthetic test modules,
    pytest plugin imports and collection hooks must keep normal failure semantics.
    Call only around a known external-data import or existing payload skip probe.
    """
    from contextlib import contextmanager
    from harnessgrad.paths import DataRootNotConfigured

    @contextmanager
    def scope():
        try:
            yield
        except DataRootNotConfigured as exc:
            if type(exc) is not DataRootNotConfigured:
                raise
            pytest.skip(str(exc), allow_module_level=True)

    return scope()


#: A well-formed blocker that is **not** a live registry entry. It exists so the blocker-tooling
#: tests can exercise a real blocker without depending on how many the live registry happens to
#: carry. That dependency kept breaking: the mutation tests were pinned to `jericho`, then to
#: `bfcl_v3`, and the list-record control needed two open non-refusal blockers at once. On
#: 2026-10-16 the operator withdrew `bfcl_v3/simple-ast-contract` and
#: `triviaqa/hub-export-scorer-linkage`, leaving `appworld` as the only one; closing that too
#: would have left none and the controls would have stopped testing anything.
#:
#: It must satisfy `tools/check_dataset_blockers.py::_blocker` exactly - the applier re-validates
#: the whole document after writing - so the evidence entry points at a tracked file and its
#: locator appears in that file verbatim.
SYNTHETIC_BLOCKER = {
    "id": "synthetic-fixture-blocker",
    "family": "measurement-coverage",
    "status": "measurement",
    "observed": "Synthetic fixture entry. It is not a live requirement and is not in the real "
                "registry; it is attached to a published dataset only inside a private copy.",
    "evidence": [{"kind": "repository", "path": "AGENTS.md",
                  "locator": "# Contribution and agent guidance"}],
    "next_action": "Synthetic fixture obligation, used only to exercise the blocker tooling. "
                   "It is not a live requirement and it authorizes nothing.",
}


@pytest.fixture
def synthetic_blocker():
    """A copy of `SYNTHETIC_BLOCKER`, so a test that mutates it cannot leak into another."""
    import copy

    return copy.deepcopy(SYNTHETIC_BLOCKER)


@pytest.fixture
def attach_synthetic_blocker():
    """Attach `SYNTHETIC_BLOCKER` to a **published** dataset inside a private document copy.

    It cannot live on a synthetic dataset: `checker.validate` requires every registry dataset to
    be a published declaration whose `demand`, `surface` and `exercises` match
    `tools/dataset_classes.py`, so an invented name is refused before the blocker is ever read.
    `agentdojo` sorts first among the published names, which keeps the choice deterministic.
    """
    import copy

    def attach(document, dataset="agentdojo"):
        for row in document["datasets"]:
            if row["dataset"] == dataset:
                row["blockers"].append(copy.deepcopy(SYNTHETIC_BLOCKER))
                return (dataset, SYNTHETIC_BLOCKER["id"])
        raise AssertionError(f"{dataset} is not a published dataset in this registry")

    return attach


@pytest.fixture(scope="module")
def synthetic_gsm8k(tmp_path_factory):
    """Build an isolated adapter using three synthetic test and five train rows.

    Split names exercise the adapter contract; these are authored unit-test data,
    not released questions or benchmark measurements.
    """
    sys.path.insert(0, str(ROOT))
    from data.scaffolds.qa import base

    answers = {
        "test": ("18", "3.5", "-3.25"),
        "train": ("42", "1250", "-3", "7000", "18.0"),
    }
    tables = {
        split: [
            {"question": f"Synthetic {split} question {i}: compute a value.",
             "answer": f"Synthetic solution for {split} row {i}.\n#### {answer}"}
            for i, answer in enumerate(values)
        ]
        for split, values in answers.items()
    }
    reads = []

    def synthetic_rows(path):
        split = Path(path).name.split("-", 1)[0]
        assert split in tables, path
        reads.append(split)
        return tables[split]

    spec = importlib.util.spec_from_file_location(
        "gsm8k_synthetic_contract", ROOT / "data" / "datasets" / "gsm8k.py")
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HG_DATA_GSM8K", str(tmp_path_factory.mktemp("synthetic-gsm8k-storage")))
        patch.setattr(base, "parquet_rows", synthetic_rows)
        spec.loader.exec_module(module)
    assert reads == ["test", "train"]
    assert module._LOAD_ERROR is None
    # Keep any later table rebuild confined to the same synthetic rows.
    module._SPEC._rows = lambda: tables
    return module
