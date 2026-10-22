"""BFCL selection/privacy regression: fabricated bytes and guarded reads only."""
from __future__ import annotations

import builtins
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from data.datasets import bfcl_v3 as dataset
from data.scaffolds.qa import control
from data.scaffolds.qa.verify import check

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_NAME = "fabricated_private_reference"


@pytest.fixture(autouse=True)
def forbid_release_and_full_pool(monkeypatch):
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert "/datasets/" not in str(path), "release payload access"
        return original_open(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("selected execution accessed full pool")

    monkeypatch.setattr(Path, "open", guarded_open)
    for name in ("_read_jsonl", "_rows", "load"):
        monkeypatch.setattr(dataset, name, forbidden)
    monkeypatch.setattr(dataset._SPEC, "records", forbidden)


@pytest.fixture
def synthetic_index(monkeypatch, tmp_path):
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)
    manifest = {"schema_version": 1, "repo_tag": dataset.REPO_TAG,
                "tarball_sha256": dataset.TARBALL_SHA256, "tasks": {}}
    selected = {"simple": {0, 2}, "irrelevance": {1}}
    spans = {}
    for subset, relative_path in dataset.SUBSETS.items():
        payload, answers = bytearray(), bytearray()
        for index in range(4):
            question_id = f"fabricated-{subset}-{index}"
            row = {"id": question_id,
                   "question": [[{"role": "user", "content": f"fabricated request {index}"}]],
                   "function": [{"name": PRIVATE_NAME, "description": "fabricated",
                                 "parameters": {"type": "dict"}}]}
            raw = (json.dumps(row).encode() + b"\n" if index in selected[subset]
                   else b"unselected malformed private bytes\xff\n")
            question = {"offset": len(payload), "length": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest()}
            payload.extend(raw)
            entry = {"question_id": question_id, "question": question}
            if subset == "simple":
                answer_raw = (json.dumps({"id": question_id,
                    "ground_truth": [{PRIVATE_NAME: {"x": ["synthetic argument"]}}]}).encode()
                    + b"\n" if index in selected[subset] else b"unselected gold\xff\n")
                entry["answer"] = {"offset": len(answers), "length": len(answer_raw),
                                   "sha256": hashlib.sha256(answer_raw).hexdigest()}
                answers.extend(answer_raw)
            manifest["tasks"][f"{subset}/{index}"] = entry
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        spans[path] = [manifest["tasks"][f"{subset}/{i}"]["question"] for i in selected[subset]]
        if subset == "simple":
            path = tmp_path / dataset.SIMPLE_ANSWERS
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(answers)
            spans[path] = [manifest["tasks"][f"simple/{i}"]["answer"] for i in selected[subset]]
    (tmp_path / dataset.SELECTED_INDEX).write_text(json.dumps(manifest))
    original_open = Path.open
    reads = []

    class SelectedReader:
        def __init__(self, path, handle):
            self.path, self.handle = path, handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        @property
        def closed(self):
            return self.handle.closed

        def seek(self, offset):
            assert offset in {span["offset"] for span in spans[self.path]}
            return self.handle.seek(offset)

        def read(self, length):
            offset = self.handle.tell()
            assert (offset, length) in [(span["offset"], span["length"]) for span in spans[self.path]]
            reads.append((self.path, offset, length))
            return self.handle.read(length)

    def selected_open(path, *args, **kwargs):
        if path in spans:
            assert args == ("rb",) and kwargs == {"buffering": 0}
            return SelectedReader(path, original_open(path, *args, **kwargs))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", selected_open)
    return manifest, reads


def test_import_and_score_do_not_read_release(tmp_path):
    code = '''
import pathlib
original_open = pathlib.Path.open
def guarded_open(path, *args, **kwargs):
    assert "/datasets/" not in str(path), "release read during import/scoring"
    return original_open(path, *args, **kwargs)
pathlib.Path.open = guarded_open
from data.datasets import bfcl_v3
assert bfcl_v3._RECORDS == []
assert bfcl_v3.score("other", {"functions":["fabricated_private_reference"]})["reward"] == 0
assert bfcl_v3._RECORDS == []
'''
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                          env={**os.environ, "PYTHONPATH": str(ROOT)},
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("selection", [
    None, [], ["unknown/0"], ["simple/-1"], ["simple/400"], ["irrelevance/240"],
    ["simple/01"], ["simple/+1"], ["simple/0/trailing"], [None],
    ["simple/0", "irrelevance/240"],
])
def test_selection_rejected_before_index_or_payload_read(monkeypatch, selection):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid selection opened a file")

    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ValueError):
        dataset.load_selected(selection)


def test_missing_index_never_falls_back_to_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)
    with pytest.raises(ValueError, match="selected index is unavailable"):
        dataset.load_selected(["simple/0"])


@pytest.mark.parametrize("prediction,reward", [
    ("synthetic.compute", 1.0), ("compute", 1.0), ("compute(x=3)", 0.0),
    ("compute\ncompute", 0.0), ("NONE", 0.0),
])
def test_function_name_metric_remains_an_explicit_nonofficial_diagnostic(prediction, reward):
    result = dataset.score(prediction, {"functions": ["synthetic.compute"], "subset": "simple"})
    assert result["reward"] == reward
    assert "not the official BFCL score" in result["official"]["metric"]
    assert result["official"]["expected_functions"] == "<redacted>"
    assert result["official"]["evidence_redacted"] is True


def test_current_record_conversion_discards_reference_alternatives_without_release_reads():
    row = {"id": "fabricated-simple", "question": [[{"role": "user", "content": "fabricated"}]],
           "function": [{"name": "synthetic.compute", "parameters": {
               "type": "dict", "properties": {"x": {"type": "integer"}}}}]}
    first = dataset._record_from_row("simple", 0, row, [{"compute": {"x": [3]}}])
    second = dataset._record_from_row("simple", 0, row, [{"compute": {"x": [99]}}])
    assert first["gold"] == second["gold"] == {
        "answer": "compute", "functions": ["compute"], "subset": "simple"}
    # This exposes the integration gap; it is not an assertion about official AST rules.
    assert dataset.score("compute", first["gold"])["reward"] == 1.0


def test_full_pool_verifier_map_also_requests_early_redaction(monkeypatch, synthetic_index):
    _, reads = synthetic_index
    record = dataset.control_task("simple/2")
    monkeypatch.setattr(dataset, "_RECORDS", [record])
    verifier = dataset.VERIFY[record["task_id"]]
    assert "--redact-gold" in verifier["argv"]
    assert len(reads) == 2


@pytest.mark.parametrize("failure", ["release", "missing", "span", "later_span"])
def test_all_selected_metadata_validated_before_payload_read(synthetic_index, failure):
    manifest, reads = synthetic_index
    if failure == "release":
        manifest["tarball_sha256"] = "0" * 64
    elif failure == "missing":
        del manifest["tasks"]["simple/2"]
    else:
        manifest["tasks"]["simple/2" if failure == "later_span" else "simple/0"]["answer"]["offset"] = -1
    (dataset.DATASET_ROOT / dataset.SELECTED_INDEX).write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        dataset.load_selected(["simple/0", "simple/2"])
    assert reads == []


@pytest.mark.parametrize("field,value", [
    ("length", dataset.MAX_SELECTED_ROW_BYTES + 1),
    ("offset", dataset._MAX_SELECTED_POSITION + 1),
    ("offset", dataset._MAX_SELECTED_POSITION),
    ("offset", True),
    ("length", True),
])
def test_selected_span_limits_reject_before_source_open(monkeypatch, synthetic_index, field, value):
    manifest, reads = synthetic_index
    manifest["tasks"]["simple/2"]["answer"][field] = value
    (dataset.DATASET_ROOT / dataset.SELECTED_INDEX).write_text(json.dumps(manifest))
    original_open = Path.open

    def metadata_only(path, *args, **kwargs):
        assert path == dataset.DATASET_ROOT / dataset.SELECTED_INDEX
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", metadata_only)
    with pytest.raises(ValueError, match="invalid selected answer span"):
        dataset.load_selected(["simple/0", "simple/2"])
    assert reads == []


@pytest.mark.parametrize("kind", ["question", "answer"])
@pytest.mark.parametrize("field", ["offset", "length"])
def test_later_selected_span_eof_bounds_reject_before_any_read(synthetic_index, kind, field):
    manifest, reads = synthetic_index
    relative = dataset.SIMPLE if kind == "question" else dataset.SIMPLE_ANSWERS
    size = (dataset.DATASET_ROOT / relative).stat().st_size
    manifest["tasks"]["simple/2"][kind][field] = size + 1
    (dataset.DATASET_ROOT / dataset.SELECTED_INDEX).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="selected span exceeds source bounds"):
        dataset.load_selected(["simple/0", "simple/2"])
    assert reads == []


def test_selected_span_can_end_exactly_at_eof(synthetic_index):
    manifest, reads = synthetic_index
    span = manifest["tasks"]["irrelevance/1"]["question"]
    os.truncate(dataset.DATASET_ROOT / dataset.IRRELEVANCE, span["offset"] + span["length"])
    tasks, _ = dataset.load_selected(["irrelevance/1"])
    assert tasks[0]["task_id"] == "irrelevance/1"
    assert len(reads) == 1


@pytest.fixture
def opened_sources(monkeypatch, synthetic_index):
    original_open = Path.open
    opened = []

    def tracked_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if args == ("rb",):
            opened.append((path, handle))
        return handle

    monkeypatch.setattr(Path, "open", tracked_open)
    return opened


def test_each_selected_source_descriptor_is_reused_and_closed(opened_sources):
    dataset.load_selected(["simple/0", "irrelevance/1", "simple/2"])
    assert [path for path, _ in opened_sources] == [
        dataset.DATASET_ROOT / dataset.SIMPLE,
        dataset.DATASET_ROOT / dataset.SIMPLE_ANSWERS,
        dataset.DATASET_ROOT / dataset.IRRELEVANCE,
    ]
    assert all(handle.closed for _, handle in opened_sources)


def test_selected_bounds_use_descriptor_stat(monkeypatch, opened_sources):
    def forbidden(*args, **kwargs):
        pytest.fail("selected bounds used a path stat instead of the opened descriptor")

    monkeypatch.setattr(Path, "stat", forbidden)
    dataset.load_selected(["simple/0", "simple/2"])
    assert len(opened_sources) == 2


def test_nonregular_selected_source_rejects_before_reads(
    monkeypatch, synthetic_index, opened_sources,
):
    _, reads = synthetic_index
    monkeypatch.setattr(os, "fstat", lambda fd: SimpleNamespace(st_mode=0))
    with pytest.raises(ValueError, match="selected source must be a regular file"):
        dataset.load_selected(["simple/0"])
    assert reads == []
    assert len(opened_sources) == 1
    assert opened_sources[0][1].closed


@pytest.mark.parametrize("failure", ["bounds", "digest"])
def test_selected_failure_closes_all_opened_sources(synthetic_index, opened_sources, failure):
    manifest, _ = synthetic_index
    span = manifest["tasks"]["simple/2"]["answer"]
    if failure == "bounds":
        span["offset"] = (dataset.DATASET_ROOT / dataset.SIMPLE_ANSWERS).stat().st_size
    else:
        span["sha256"] = "0" * 64
    (dataset.DATASET_ROOT / dataset.SELECTED_INDEX).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="source bounds|digest mismatch"):
        dataset.load_selected(["simple/0", "simple/2"])
    assert len(opened_sources) == 2
    assert all(handle.closed for _, handle in opened_sources)


@pytest.mark.parametrize("field", ["st_size", "st_mtime_ns", "st_ctime_ns"])
def test_selected_source_stat_change_rejects_records(
    monkeypatch, synthetic_index, opened_sources, field,
):
    original_fstat = os.fstat
    observed = set()

    def changed_fstat(fd):
        info = original_fstat(fd)
        if fd in observed:
            fields = {name: getattr(info, name) for name in (
                "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")}
            fields[field] += 1
            return SimpleNamespace(**fields)
        observed.add(fd)
        return info

    monkeypatch.setattr(os, "fstat", changed_fstat)
    with pytest.raises(ValueError, match="selected source changed during loading"):
        dataset.load_selected(["simple/0", "simple/2"])
    assert all(handle.closed for _, handle in opened_sources)


def test_selected_spans_preserve_order_and_cache(monkeypatch, synthetic_index):
    _, reads = synthetic_index
    cache = [{"task_id": "unrelated cache"}]
    monkeypatch.setattr(dataset, "_RECORDS", cache)
    monkeypatch.setattr(dataset, "_LOAD_ERROR", "prior pool failure")
    ids = ["simple/2", "irrelevance/1", "simple/0"]
    tasks, answers = dataset.load_selected([*ids, ids[0]])
    assert [task["task_id"] for task in tasks] == ids
    assert answers == {"simple/2": PRIVATE_NAME, "irrelevance/1": "NONE", "simple/0": PRIVATE_NAME}
    assert len(reads) == 5
    assert dataset._RECORDS is cache
    assert dataset._LOAD_ERROR == "prior pool failure"
    assert all(set(task) == {"task_id", "goal", "inputs"} for task in tasks)


def test_irrelevance_never_opens_simple_or_answers(synthetic_index):
    _, reads = synthetic_index
    tasks, answers = dataset.load_selected(["irrelevance/1"])
    assert tasks[0]["task_id"] == "irrelevance/1"
    assert answers == {"irrelevance/1": "NONE"}
    assert [path for path, _, _ in reads] == [dataset.DATASET_ROOT / dataset.IRRELEVANCE]


@pytest.mark.parametrize("failure", ["question_digest", "answer_digest", "question_id", "answer_id"])
def test_selected_digest_and_identity_binding_fail_closed(synthetic_index, failure):
    manifest, _ = synthetic_index
    entry = manifest["tasks"]["simple/0"]
    if failure.endswith("digest"):
        entry[failure.split("_")[0]]["sha256"] = "0" * 64
    elif failure == "question_id":
        entry["question_id"] = "wrong synthetic identity"
    else:
        entry["answer"] = manifest["tasks"]["simple/2"]["answer"]
    (dataset.DATASET_ROOT / dataset.SELECTED_INDEX).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="digest mismatch|identity mismatch"):
        dataset.load_selected(["simple/0"])


@pytest.mark.parametrize("task_id", [None, "simple/2", "irrelevance/1"])
def test_control_record_reads_each_selected_span_once(synthetic_index, task_id):
    _, reads = synthetic_index
    record = control._record(dataset, task_id)
    assert record["task_id"] == ("simple/0" if task_id is None else task_id)
    assert len(reads) == (1 if task_id == "irrelevance/1" else 2)


@pytest.fixture
def guarded_control_check(monkeypatch):
    original_check = control.run_check

    def run_check(module, record, workdir):
        (workdir / "sitecustomize.py").write_text(
            'import pathlib\noriginal_open = pathlib.Path.open\n'
            'def guarded_open(path, *args, **kwargs):\n'
            '    assert "/datasets/" not in str(path), "release read in verifier"\n'
            '    return original_open(path, *args, **kwargs)\n'
            'pathlib.Path.open = guarded_open\n')
        return original_check(module, record, workdir)

    monkeypatch.setattr(control, "run_check", run_check)


@pytest.mark.parametrize("oracle", [True, False])
@pytest.mark.parametrize("task_id", ["simple/2", "irrelevance/1"])
def test_local_controls_use_only_selected_spans(
    synthetic_index, guarded_control_check, tmp_path, oracle, task_id,
):
    _, reads = synthetic_index
    workdir = tmp_path / "local-control"
    assert control.main(["--dataset", "bfcl_v3", "--oracle" if oracle else "--noop",
                         "--task-id", task_id, "--workdir", str(workdir)]) == 0
    evidence = json.loads((workdir / "verifier/evidence.json").read_text())
    assert evidence["measured"] is True
    assert evidence["reward"] == float(oracle or task_id.startswith("irrelevance/"))
    assert evidence["gold"] == {"redacted": True}
    assert evidence["official"]["expected_functions"] == "<redacted>"
    assert len(reads) == (1 if task_id.startswith("irrelevance/") else 2)


@pytest.mark.parametrize("oracle", [True, False])
@pytest.mark.parametrize("task_id", ["simple/2", "irrelevance/1"])
def test_platform_controls_reuse_record_without_second_selected_load(
    monkeypatch, synthetic_index, guarded_control_check, tmp_path, oracle, task_id,
):
    _, reads = synthetic_index
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("control reloaded selected task")

    monkeypatch.setattr(dataset, "load_selected", forbidden)

    def run_one(**kwargs):
        calls.append(kwargs)
        assert kwargs["task"]["task_id"] == task_id
        assert kwargs["env"] == {"kind": "files"}
        assert "--redact-gold" in kwargs["verifier"]["argv"]
        assert not any("gold" in entry["path"] for entry in kwargs["setup"]["files"])
        # Inspect dispatch without running a model, then execute the supplied
        # setup and real verifier against fabricated bytes.
        episode = tmp_path / "episode"
        episode.mkdir()
        for entry in kwargs["setup"]["files"]:
            target = episode / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if "content" in entry:
                target.write_text(entry["content"])
            else:
                shutil.copy2(entry["from"], target)
        if oracle:
            (episode / "answer.txt").write_text((episode / ".oracle/answer.txt").read_text())
        reward, detail = control.run_check(dataset, {"task_id": task_id,
            "gold": json.loads(next(entry["content"] for entry in kwargs["verifier"]["inputs"]
                                    if entry["path"] == "gold.json"))}, episode)
        assert reward == float(oracle or task_id.startswith("irrelevance/")), detail
        return {"exit_code": 0, "verdict": {"score": reward, "passed": bool(reward)}}

    monkeypatch.setitem(sys.modules, "eval.run.runner", SimpleNamespace(run_one=run_one))
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path / "initial-work-root"))
    assert control.main(["--dataset", "bfcl_v3", "--oracle" if oracle else "--noop",
                         "--platform", "--task-id", task_id,
                         "--workdir", str(tmp_path / "platform-control")]) == 0
    assert len(calls) == 1
    assert len(reads) == (1 if task_id.startswith("irrelevance/") else 2)


@pytest.mark.parametrize("prediction", ["other", "", "NONE"])
def test_direct_score_never_echoes_expected_functions(prediction):
    result = dataset.score(prediction, {"subset": "simple", "functions": [PRIVATE_NAME]})
    assert result["reward"] == 0.0
    assert PRIVATE_NAME not in json.dumps(result)
    assert dataset.score("", {"subset": "irrelevance", "functions": []})["reward"] == 1.0


@pytest.mark.parametrize("failure", [
    "prediction_json", "prediction_utf8", "prediction_invalid", "import", "missing_score",
    "scorer", "result", "reward", "gold_utf8", "gold_shape", "answer_utf8", "validator_import",
    "gold_missing", "gold_json",
])
def test_unmeasured_paths_redact_gold_and_private_diagnostics(monkeypatch, tmp_path, capsys, failure):
    gold = {"answer": PRIVATE_NAME, "functions": [PRIVATE_NAME], "subset": "simple"}
    (tmp_path / "gold.json").write_text(json.dumps(gold))
    (tmp_path / "answer.txt").write_text("other")
    if failure == "prediction_json":
        (tmp_path / "prediction.json").write_text('{"private": "' + PRIVATE_NAME)
    elif failure == "prediction_utf8":
        (tmp_path / "prediction.json").write_bytes(b'\xff')
    elif failure == "prediction_invalid":
        (tmp_path / "prediction.json").write_text(json.dumps({"unsupported": PRIVATE_NAME}))
    elif failure == "gold_utf8":
        (tmp_path / "gold.json").write_bytes(PRIVATE_NAME.encode() + b'\xff')
    elif failure == "gold_shape":
        (tmp_path / "gold.json").write_text(json.dumps([PRIVATE_NAME]))
    elif failure == "gold_missing":
        (tmp_path / "gold.json").unlink()
    elif failure == "gold_json":
        (tmp_path / "gold.json").write_text('{"answer":"' + PRIVATE_NAME)
    elif failure == "answer_utf8":
        (tmp_path / "answer.txt").write_bytes(b'\xff')
    original_import = builtins.__import__

    def synthetic_import(name, *args, **kwargs):
        if name == "data.scaffolds.qa.prediction" and failure == "validator_import":
            raise ImportError(PRIVATE_NAME)
        if name == "data.datasets.bfcl_v3":
            if failure == "import":
                raise ImportError(PRIVATE_NAME)
            if failure == "missing_score":
                return SimpleNamespace()
            def score(prediction, gold):
                if failure == "scorer":
                    raise ValueError(gold["answer"])
                if failure == "result":
                    return gold
                if failure == "reward":
                    return {"reward": gold["answer"]}
                return dataset.score(prediction, gold)
            return SimpleNamespace(score=score)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", synthetic_import)
    if failure == "validator_import":
        (tmp_path / "prediction.json").write_text('{"schema_version":1,"answer":"other"}')
    monkeypatch.chdir(tmp_path)
    # Legacy BFCL argv without the new flag must redact failures too.
    assert check.main(["--dataset", "bfcl_v3", "--reward-file", "verifier/reward.txt",
                       "--evidence-file", "verifier/evidence.json"]) == 3
    assert not (tmp_path / "verifier/reward.txt").exists()
    text = (tmp_path / "verifier/evidence.json").read_text()
    evidence = json.loads(text)
    assert evidence["measured"] is False
    assert "traceback" not in evidence
    assert "gold" not in evidence or evidence["gold"] == {"redacted": True}
    output = capsys.readouterr()
    assert PRIVATE_NAME not in text + output.out + output.err


@pytest.mark.parametrize("subset,functions,prediction,reward", [
    ("simple", [PRIVATE_NAME], "other", 0),
    ("irrelevance", [], "", 1), ("irrelevance", [], "other", 0),
])
def test_measured_verifier_paths_redact_gold(monkeypatch, tmp_path, capsys, subset, functions, prediction, reward):
    (tmp_path / "gold.json").write_text(json.dumps({"functions": functions,
                                                   "answer": PRIVATE_NAME, "subset": subset}))
    (tmp_path / "answer.txt").write_text(prediction)
    monkeypatch.chdir(tmp_path)
    assert check.main(["--dataset", "bfcl_v3", "--reward-file", "verifier/reward.txt",
                       "--evidence-file", "verifier/evidence.json"]) == (0 if reward else 1)
    text = (tmp_path / "verifier/evidence.json").read_text()
    evidence = json.loads(text)
    assert evidence["measured"] is True
    assert evidence["reward"] == reward
    assert evidence["gold"] == {"redacted": True}
    output = capsys.readouterr()
    assert PRIVATE_NAME not in text + output.out + output.err
