"""The BFCL-v3 index builder: derived from the pin, refused when it is not, loadable when done.

`selected-index.v1.json` is what makes the selected-only route (and the control) reachable,
and nothing produced it until `data/datasets/bfcl_v3/prepare.py` existed. This module tests
that builder rather than the reader: a synthetic tarball in `tmp_path` stands in for the tag
so no released byte is read, the production pin is the one under test, and the last test
proves the artifact end to end through `bfcl_v3.load()` -- the caller that was failing.

The synthetic tarball keeps the release's row counts (400 + 240) because both the builder
and the full-pool reader refuse a different count; it is still ~100 KB and built with
`tarfile` in the test, never downloaded.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

from data.datasets import bfcl_v3 as dataset

ROOT = Path(__file__).resolve().parents[2]
FETCH = "python3 tools/datasets.py --fetch --dataset bfcl_v3"


def _load_prepare():
    """Import the builder by path: it is a script under `data/datasets/bfcl_v3/`, not a module."""
    path = ROOT / "data" / "datasets" / "bfcl_v3" / "prepare.py"
    spec = importlib.util.spec_from_file_location("bfcl_v3_prepare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = _load_prepare()


def _question_row(subset: str, index: int) -> dict:
    return {"id": f"{subset}_{index}",
            "question": [[{"role": "user", "content": f"synthetic {subset} request {index}"}]],
            "function": [{"name": f"tool_{subset}_{index}", "description": "synthetic",
                          "parameters": {"type": "dict"}}]}


def _answer_row(index: int) -> dict:
    return {"id": f"simple_{index}",
            "ground_truth": [{f"tool_simple_{index}": {"x": [index]}}]}


def _add(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(payload))


def _synthetic(root: Path) -> Path:
    """The tag's three members with the release's row counts, under the pinned names.

    The blank line after row 0 is there because `_read_jsonl()` skips whitespace-only
    lines: the positional task id must count non-blank lines, and a blank line is the
    cheapest way to make a newline-counting implementation produce a shifted index.
    """
    simple = b"".join(json.dumps(_question_row("simple", i)).encode() + b"\n"
                      for i in range(dataset.N_SIMPLE))
    first, rest = simple.split(b"\n", 1)
    simple = first + b"\n\n" + rest
    # No trailing newline on the two question files, exactly like tag v1.3: the final span
    # then has to reach EOF without a separator to measure.
    simple = simple.rstrip(b"\n")
    irrelevance = b"\n".join(json.dumps(_question_row("irrelevance", i)).encode()
                             for i in range(dataset.N_IRRELEVANCE))
    # The answers file does end with a newline, so both offset computations are covered.
    answers = b"".join(json.dumps(_answer_row(i)).encode() + b"\n"
                       for i in range(dataset.N_SIMPLE))

    tarball = root / "_src" / dataset.TARBALL_NAME
    tarball.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "w:gz") as archive:
        _add(archive, dataset.SIMPLE, simple)
        _add(archive, dataset.IRRELEVANCE, irrelevance)
        _add(archive, dataset.SIMPLE_ANSWERS, answers)
    return tarball


def _pin(monkeypatch, tarball: Path) -> str:
    """Point the adapter's pin at the synthetic tarball, so the production check runs on it."""
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    monkeypatch.setattr(dataset, "TARBALL_SHA256", digest)
    return digest


def _chunk(root: Path, relative: str, span: dict) -> bytes:
    payload = (root / relative).read_bytes()
    return payload[span["offset"]:span["offset"] + span["length"]]


def test_derives_the_documented_v1_schema(tmp_path, monkeypatch):
    tarball = _synthetic(tmp_path)
    digest = _pin(monkeypatch, tarball)

    manifest = prepare.build(tmp_path)
    index = json.loads((tmp_path / dataset.SELECTED_INDEX).read_text())

    assert index == manifest
    # `bfcl_v3.py:383-388` compares with `type(...) is int`, so `true` is not a schema version.
    assert type(index["schema_version"]) is int and index["schema_version"] == 1
    # The release binding is the adapter's own pin, not a copy of it.
    assert index["repo_tag"] == dataset.REPO_TAG
    assert index["tarball_sha256"] == digest
    assert index["derived_from"]["tarball_sha256"] == digest
    # The digests the index claims to be derived from are the ones actually on disk.
    for relative, recorded in index["derived_from"]["files"].items():
        payload = (tmp_path / relative).read_bytes()
        assert recorded["sha256"] == hashlib.sha256(payload).hexdigest()
        assert recorded["bytes"] == len(payload)
    assert len(index["tasks"]) == dataset.N_SIMPLE + dataset.N_IRRELEVANCE

    # The schema's shape, and the fact that both subsets are addressed by their own file.
    simple = index["tasks"]["simple/7"]
    assert simple["question_id"] == "simple_7"
    assert json.loads(_chunk(tmp_path, dataset.SIMPLE, simple["question"]))["id"] == "simple_7"
    # A span that named the wrong row would still be 64 hex characters, so resolve it.
    # The row is identified by a per-row function name, which the goal carries.
    assert b"tool_simple_7" in _chunk(tmp_path, dataset.SIMPLE, simple["question"])
    assert json.loads(
        _chunk(tmp_path, dataset.SIMPLE_ANSWERS, simple["answer"]))["id"] == "simple_7"
    assert hashlib.sha256(
        _chunk(tmp_path, dataset.SIMPLE, simple["question"])).hexdigest() == \
        simple["question"]["sha256"]
    # `bfcl_v3.py:396` reads no answer span for `irrelevance`: its gold is the empty set.
    assert "answer" not in index["tasks"]["irrelevance/0"]
    # The blank line is skipped, so the last id addresses the last row and its span ends at EOF.
    last = index["tasks"][f"simple/{dataset.N_SIMPLE - 1}"]
    assert last["question"]["offset"] + last["question"]["length"] == \
        (tmp_path / dataset.SIMPLE).stat().st_size
    # Every span was checked by the builder's own re-read, against the files on disk, and
    # the bytes on disk equal the bytes `build()` would write now.
    assert len(prepare.check(tmp_path)["tasks"]) == len(index["tasks"])


def test_refuses_a_tarball_whose_digest_is_not_the_pin(tmp_path, capsys):
    # No monkeypatch: the production pin is under test, and the synthetic bytes are not it.
    _synthetic(tmp_path)
    with pytest.raises(ValueError, match="sha256") as excinfo:
        prepare.build(tmp_path)
    message = str(excinfo.value)
    # A refusal that does not say how to fix it is a dead end; name the fetch command.
    assert FETCH in message
    # `tools/datasets.py --dataset` filters on the registry name, so the dash spelling used
    # in the adapter's own docstring matches nothing and exits 0 -- a no-op, not a fetch.
    assert "--dataset bfcl-v3" not in message
    # The CLI turns the refusal into a non-zero exit and the same sentence on stderr.
    assert prepare.main(["--root", str(tmp_path)]) == 1
    assert FETCH in capsys.readouterr().err


def test_a_missing_tarball_is_refused_by_naming_the_fetch_command(tmp_path, capsys):
    with pytest.raises(ValueError) as excinfo:
        prepare.build(tmp_path)
    message = str(excinfo.value)
    assert FETCH in message
    assert dataset.TARBALL_SHA256 in message, "the refusal names the bytes that are missing"
    assert not (tmp_path / dataset.SELECTED_INDEX).exists()
    assert prepare.main(["--root", str(tmp_path)]) == 1
    assert FETCH in capsys.readouterr().err


def test_an_extracted_file_that_differs_from_the_tarball_is_refused(tmp_path, monkeypatch):
    tarball = _synthetic(tmp_path)
    _pin(monkeypatch, tarball)
    # A stale extraction is the realistic failure: the spans would index bytes that are not
    # the pinned ones, and both the offsets and the digests would be wrong for another reason
    # than the one the reader reports.
    target = tmp_path / dataset.SIMPLE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'{"id": "simple_0"}\n')
    with pytest.raises(ValueError, match="differs from the pinned tarball"):
        prepare.build(tmp_path)
    assert not (tmp_path / dataset.SELECTED_INDEX).exists()


def test_check_reverifies_and_refuses_a_tampered_index(tmp_path, monkeypatch):
    tarball = _synthetic(tmp_path)
    _pin(monkeypatch, tarball)
    prepare.build(tmp_path)
    index = tmp_path / dataset.SELECTED_INDEX
    tampered = json.loads(index.read_text())
    tampered["tasks"]["simple/7"]["question"]["sha256"] = "0" * 64

    # `verify()` re-reads the bytes a span names, so a digest that is not the file's fails
    # before any comparison of index files.
    with pytest.raises(ValueError, match="digest"):
        prepare.verify(tmp_path, tampered)
    index.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="stale"):
        prepare.check(tmp_path)


def test_the_produced_index_makes_the_loader_return_tasks(tmp_path, monkeypatch):
    tarball = _synthetic(tmp_path)
    _pin(monkeypatch, tarball)
    prepare.build(tmp_path)
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)
    # `_RECORDS` and `_LOAD_ERROR` are module state that outlives one test in this process;
    # a stale value would make `load()` return the wrong pool or re-raise an old failure.
    monkeypatch.setattr(dataset, "_RECORDS", [])
    monkeypatch.setattr(dataset, "_LOAD_ERROR", None)

    tasks, gold = dataset.load()
    assert len(tasks) == dataset.N_SIMPLE + dataset.N_IRRELEVANCE
    # The full-pool route enumerates simple then irrelevance, and the index uses the same
    # positional ids -- otherwise the two routes would name different tasks.
    assert [task["task_id"] for task in tasks] == (
        [f"simple/{i}" for i in range(dataset.N_SIMPLE)]
        + [f"irrelevance/{i}" for i in range(dataset.N_IRRELEVANCE)])
    assert gold["simple/7"] == "tool_simple_7"
    assert gold["irrelevance/7"] == "NONE"
    # `provision_status()` requires the index as well as the tarball and the three sources.
    assert dataset.provision_status()["present"] is True

    # The selected-only route is the one the control and a study run take.
    selected, selected_gold = dataset.load_selected(["simple/7"])
    assert selected[0]["task_id"] == "simple/7"
    assert "tool_simple_7" in selected[0]["goal"]
    assert selected_gold == {"simple/7": "tool_simple_7"}
