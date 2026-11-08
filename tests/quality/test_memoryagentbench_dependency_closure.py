"""Distinct identities and a real stdlib-only synthetic serving path, without data/models."""
import json
from pathlib import Path
import subprocess
import sys

from data.datasets.memoryagentbench.lib import dependencies, inputs


def test_prepare_requirements_retain_frozen_tokenizer_pins():
    root = Path(inputs.__file__).resolve().parents[1]
    requirements = (root / "requirements-prepare.txt").read_text().splitlines()
    pins = {line.split("==")[0]: line.split("==")[1] for line in requirements if "==" in line and not line.startswith("#")}
    assert pins == {**inputs.read_spec("chunk-policy.json")["dependencies"], **dependencies.PREPARE_READER_PINS}
    assert "rouge-score" not in pins and "numpy" not in pins


def test_identity_does_not_claim_service_or_model_readiness(monkeypatch):
    monkeypatch.setattr(dependencies.importlib.metadata, "version", lambda package: "wrong")
    preparation = dependencies.identity("prepare")
    runtime = dependencies.identity("runtime")
    assert not preparation["dependencies_match"]
    assert runtime["required_distributions"] == {}
    assert runtime["role"] == "runtime" and preparation["role"] == "prepare"
    assert "no corpus" in runtime["validation"]


def test_synthetic_service_and_score_question_without_site_packages():
    root = Path(__file__).resolve().parents[2]
    script = '''
import sys, json
sys.path.insert(0, sys.argv[1])
from data.datasets.memoryagentbench.prepare import build_artifacts
from data.datasets.memoryagentbench.relay import Session
from data.datasets.memoryagentbench.lib.scoring import score_question
class Sentences:
    def tokenize(self, text): return [text]
class Encoding:
    def encode(self, text, **kwargs): return list(text)
_, service, gold = build_artifacts("Conflict_Resolution", 0,
    {"context": "The key is blue.", "questions": ["What color?"],
     "answers": [["blue"]], "metadata": {"source": "ruler_qa1_197K"}}, Sentences(), Encoding())
assert "gold_by_id" not in service and "answers" not in service
session = Session(service)
chunk = session.handle("next", {})
assert chunk
assert session.handle("ack", {"chunk_id": "chunk:0", "request_id": "ack-0"})["accepted"]
question = session.handle("next", {})
qid = service["questions"][0]["id"]
assert session.handle("submit", {"question_id": qid, "answer": "blue", "request_id": "submit-0"})["accepted"]
assert session.record()["seal"]["status"] == "complete"
print(json.dumps({"stdlib_only": True, "score": score_question(service["source"], "blue", gold["gold_by_id"][qid])["score"]}))
'''
    result = subprocess.run([sys.executable, "-I", "-S", "-c", script, str(root)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"stdlib_only": True, "score": 1.0}


def test_adapter_pool_cache_cannot_hide_corruption_or_recovery(tmp_path, monkeypatch):
    from data.datasets import memoryagentbench as dataset
    from data.datasets.memoryagentbench.lib import storage
    calls = []
    def load_pool(root):
        calls.append(root)
        if len(calls) == 2:
            raise ValueError("artifact checksum mismatch")
        return {"tasks": [{"task_id": "synthetic"}]}
    monkeypatch.setattr(storage, "load_pool", load_pool)
    monkeypatch.setattr(dataset, "PREPARED_ROOT", tmp_path)
    assert dataset._rows() == {"synthetic": {"task_id": "synthetic"}}
    import pytest
    with pytest.raises(ValueError, match="checksum"):
        dataset._rows()
    assert dataset._rows() == {"synthetic": {"task_id": "synthetic"}}
    assert len(calls) == 3


def test_preparation_identity_rejects_reader_version_mismatch(monkeypatch):
    import pytest
    pins = {**inputs.read_spec("chunk-policy.json")["dependencies"], **dependencies.PREPARE_READER_PINS}
    monkeypatch.setattr(dependencies.importlib.metadata, "version", lambda name: "wrong" if name == "pyarrow" else pins[name])
    with pytest.raises(ValueError, match=r"pyarrow==23.0.1.*installed wrong"):
        dependencies.validate_preparation()
