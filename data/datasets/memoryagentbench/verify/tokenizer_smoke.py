"""Explicit offline tokenizer microepisode, without benchmark data or model calls.

Run with the isolated preparation interpreter and --resources ROOT. The encoding
path is exercised even if Punkt tables are missing. The receipt distinguishes
real sentence segmentation from an explicit synthetic boundary in that case.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import sysconfig

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from data.datasets.memoryagentbench.lib.dependencies import validate_preparation
from data.datasets.memoryagentbench.lib.inputs import initialize, initialize_encoding
from data.datasets.memoryagentbench.lib.scoring import score_question
from data.datasets.memoryagentbench.prepare import build_artifacts
from data.datasets.memoryagentbench.relay import Session


class SyntheticSentenceBoundary:
    def tokenize(self, text):
        return [text]


def smoke(resources):
    environment = validate_preparation()
    encoding = initialize_encoding(resources)
    text = "Dr. Smith said: The key is blue. 你好世界!"
    assert encoding.decode(encoding.encode(text)) == text
    try:
        sentences, encoding = initialize(resources)
        full_ready, error = True, None
    except ValueError as exc:
        if not str(exc).startswith("resource_missing:"):
            raise
        sentences, full_ready, error = SyntheticSentenceBoundary(), False, str(exc)
    _, service, gold = build_artifacts("Conflict_Resolution", 0,
        {"context": text, "questions": ["What color?"], "answers": [["blue"]],
         "metadata": {"source": "ruler_qa1_197K"}}, sentences, encoding)
    session = Session(service)
    for index in range(len(service["chunks"])):
        session.handle("next", {})
        assert session.handle("ack", {"chunk_id": f"chunk:{index}", "request_id": f"ack:{index}"})["accepted"]
    session.handle("next", {})
    qid = service["questions"][0]["id"]
    assert session.handle("submit", {"question_id": qid, "answer": "blue", "request_id": "submit:0"})["accepted"]
    assert session.record()["seal"]["status"] == "complete"
    import tiktoken._tiktoken as native
    assets = Path(__file__).resolve().parents[1]
    sources = [assets / name for name in ("lib/inputs.py", "specs/resources.lock.json",
               "specs/chunk-policy.json", "specs/tiktoken-LICENSE.txt", "licenses/MemoryAgentBench-MIT.txt")]
    return {"schema": "hg-mab-tokenizer-smoke-v1", "environment": environment,
            "abi": sysconfig.get_config_var("SOABI"),
            "native_tiktoken": {"path": native.__file__, "sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest()},
            "source_sha256": {str(path.relative_to(assets)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
            "real_locked_bpe_roundtrip": True, "token_count": len(encoding.encode(text)),
            "full_tokenizer_ready": full_ready, "full_tokenizer_error": error,
            "synthetic_sentence_boundary": not full_ready, "dataset_prepared": False,
            "score": score_question(service["source"], "blue", gold["gold_by_id"][qid])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(smoke(args.resources), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
