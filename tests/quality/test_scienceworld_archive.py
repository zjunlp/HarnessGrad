"""Authored source archives exercise portable lookup and checksum refusal; no JVM/data."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from harnessgrad.paths import DataRootNotConfigured
from tools import scienceworld_study_sample as tool


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch):
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    for key in list(os.environ):
        if key.startswith("HG_DATA_"):
            monkeypatch.delenv(key)


def zipped(member, content):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, content)
    return buffer.getvalue()


@pytest.fixture
def authored(tmp_path, monkeypatch):
    table = {"authored": {"taskName": "task-authored", "goldActionSequences": [
        {"fold": fold, "variationIdx": offset * 100 + i, "path": ["look"]}
        for offset, fold in enumerate(("train", "dev", "test")) for i in range(100)
    ]}}
    raw = zipped("authored.json", json.dumps(table))
    digest = hashlib.sha256(raw).hexdigest()
    record = tmp_path / "authored-fold.json"
    record.write_text(json.dumps({"archive": {"sha256": digest,
        "folds": {"train": 100, "dev": 100, "test": 100}}}))
    monkeypatch.setattr(tool, "TEST_FOLD", record)
    return raw, digest


@pytest.mark.parametrize("override", [False, True])
def test_configured_root_and_dataset_override(tmp_path, monkeypatch, authored, override):
    root = tmp_path / "collection"
    monkeypatch.setenv("HG_DATA_ROOT", str(root))
    dataset = root / "scienceworld"
    if override:
        dataset = tmp_path / "separate-scienceworld"
        monkeypatch.setenv("HG_DATA_SCIENCEWORLD", str(dataset))
    archive = dataset / "_src/goldpaths-all.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(authored[0])
    assert tool.archive_path() == archive
    assert tool.archive_bytes() == (authored[0], "authored.json")


@pytest.mark.parametrize("wrapped", [False, True])
def test_explicit_archive_works_without_storage_configuration(tmp_path, authored, wrapped):
    raw, _ = authored
    archive = tmp_path / "source.zip"
    archive.write_bytes(zipped("ScienceWorld-snapshot/goldpaths/goldpaths-all.zip", raw)
                        if wrapped else raw)
    assert tool.archive_bytes(archive) == (raw, "authored.json")


def test_missing_explicit_archive_does_not_fall_back(tmp_path, monkeypatch, authored):
    monkeypatch.setenv("HG_DATA_SCIENCEWORLD", str(tmp_path / "dataset"))
    default = tool.archive_path()
    default.parent.mkdir(parents=True)
    default.write_bytes(authored[0])
    with pytest.raises(FileNotFoundError):
        tool.archive_bytes(tmp_path / "missing.zip")


def test_default_requires_storage_configuration():
    with pytest.raises(DataRootNotConfigured, match="HG_DATA_SCIENCEWORLD"):
        tool.archive_path()


@pytest.mark.parametrize("wrapped", [False, True])
def test_wrong_archive_hash_is_rejected(tmp_path, authored, wrapped):
    wrong = zipped("authored.json", b'{"wrong": true}')
    archive = tmp_path / "wrong.zip"
    archive.write_bytes(zipped("source/goldpaths/goldpaths-all.zip", wrong)
                        if wrapped else wrong)
    with pytest.raises(ValueError, match="archive sha256"):
        tool.archive_bytes(archive)


@pytest.mark.parametrize("explicit", [False, True])
def test_cli_rebuilds_and_checks_authored_study(tmp_path, monkeypatch, authored, capsys, explicit):
    monkeypatch.chdir(tmp_path)
    if explicit:
        archive = Path("custom-source.zip")
        args = ["--archive", str(archive)]
    else:
        monkeypatch.setenv("HG_DATA_ROOT", str(tmp_path / "collection"))
        archive = tool.archive_path()
        archive.parent.mkdir(parents=True)
        args = []
    archive.write_bytes(authored[0])
    output = tmp_path / "authored-study.json"
    monkeypatch.setattr(tool, "OUT", output)
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    assert tool.main([*args, "--write"]) == 0
    study = json.loads(output.read_text())
    assert study["archive"]["sha256"] == authored[1]
    assert {k: len(v) for k, v in study["pools"].items()} == {
        "train": 100, "dev": 100, "test": 100}
    assert tool.main([*args, "--check"]) == 0
    assert "authored-study.json is unchanged" in capsys.readouterr().out


@pytest.mark.parametrize("configured", [False, True])
def test_cli_missing_archive_explains_both_inputs(tmp_path, monkeypatch, capsys, configured):
    if configured:
        monkeypatch.setenv("HG_DATA_ROOT", str(tmp_path / "missing-data"))
    with pytest.raises(SystemExit) as exc:
        tool.main(["--check"])
    assert exc.value.code == 2
    message = capsys.readouterr().err
    assert "--archive PATH" in message and "HG_DATA_SCIENCEWORLD" in message
    assert "Traceback" not in message


def test_script_entrypoint_from_unrelated_working_directory(tmp_path):
    proc = subprocess.run([sys.executable, str(Path(tool.__file__).resolve()),
                           "--check"], cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 2
    assert "--archive PATH" in proc.stderr and "HG_DATA_ROOT" in proc.stderr
    assert "Traceback" not in proc.stderr
