"""Prepare pinned MemoryAgentBench inputs offline, outside the platform tree.

Example: python data/datasets/memoryagentbench/prepare.py --raw /data/mab/raw \
    --resources /data/mab/resources --output /data/mab/prepared
Selection uses --task-id repeatedly. No learning/evaluation partition is made.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

# Support the standalone CLI without importing platform dependencies.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data.datasets.memoryagentbench.lib.inputs import SPEC_SHA256, initialize, read_spec, render_question, split_context
from data.datasets.memoryagentbench.lib.storage import (DATASET_REVISION, canonical_sha256, file_sha256,
    load_gold, load_pool, load_service, question_id, task_id, validate_task_id)


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def validate_raw(raw):
    raw = Path(raw)
    source = read_spec("source.json")
    for relative, entry in source["files"].items():
        path = raw / relative
        if not path.is_file():
            raise ValueError("raw_missing:" + relative)
        if path.stat().st_size != entry["bytes"] or file_sha256(path) != entry["sha256"]:
            raise ValueError("raw_hash_mismatch:" + relative)
    return source


def _aligned(metadata, field, index, count):
    value = metadata.get(field)
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != count:
        raise ValueError("metadata not aligned with questions: " + field)
    return value[index]


def build_artifacts(split, row_index, row, sentence_tokenizer, encoding):
    """Create disjoint service and verifier payloads for one original row."""
    identifier = task_id(split, row_index)
    source = row["metadata"]["source"]
    questions, answers = row["questions"], row["answers"]
    if not isinstance(questions, list) or not questions or not isinstance(answers, list) or len(questions) != len(answers):
        raise ValueError("unaligned questions and answers")
    service = {"schema": "hg-mab-service-input-v1", "task_id": identifier,
               "episode_id": identifier, "source": source,
               "chunks": split_context(row["context"], sentence_tokenizer, encoding),
               "questions": [{"id": question_id(identifier, i), "text": render_question(source, text)}
                             for i, text in enumerate(questions)]}
    service["input_sha256"] = canonical_sha256(service)
    gold = {"schema": "hg-mab-gold-v1", "task_id": identifier, "episode_id": identifier,
            "source": source, "input_sha256": service["input_sha256"],
            "gold_by_id": {}, "judge_metadata_by_id": {}}
    for index, raw_question in enumerate(questions):
        qid = question_id(identifier, index)
        gold["gold_by_id"][qid] = answers[index]
        gold["judge_metadata_by_id"][qid] = {
            "question": raw_question,
            "question_type": _aligned(row["metadata"], "question_types", index, len(questions)),
            "question_id": _aligned(row["metadata"], "question_ids", index, len(questions)),
            "qa_pair_id": _aligned(row["metadata"], "qa_pair_ids", index, len(questions)),
            # Summary keypoints describe the whole reference, not a question-aligned array.
            "keypoints": row["metadata"].get("keypoints") if source == "infbench_sum_eng_shots2" else None,
        }
    public = {key: service[key] for key in ("task_id", "episode_id", "source", "input_sha256")}
    public.update(split=split, row_index=row_index, chunk_count=len(service["chunks"]), question_count=len(questions))
    return public, service, gold


def _check_frozen(split, row_index, row, service, frozen):
    expected = frozen[f"{DATASET_REVISION}/{split}/{row_index}"]
    ordered = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in service["chunks"]]
    ordered += [hashlib.sha256(q["text"].encode("utf-8")).hexdigest() for q in service["questions"]]
    if (len(service["chunks"]) != expected["chunks"] or len(service["questions"]) != expected["questions"]
            or service["source"] != expected["source"]
            or hashlib.sha256(row["context"].encode("utf-8")).hexdigest() != expected["context_sha256"]
            or canonical_sha256(ordered) != expected["ordered_chunk_then_query_hashes_sha256"]):
        raise ValueError(f"prepared input differs from frozen official comparison: {split}/{row_index}")


def prepare(raw, resources, output, selected_task_ids=None):
    """Prepare to a new directory, atomically publishing only after validation."""
    raw, resources, output = Path(raw).resolve(), Path(resources).resolve(), Path(output).resolve()
    platform = Path(__file__).resolve().parents[3]
    if output.is_relative_to(platform):
        raise ValueError("prepared output must be outside the platform tree")
    if output.exists():
        raise FileExistsError("prepared output already exists; choose a fresh path")
    selected = None
    if selected_task_ids is not None:
        selected_list = list(selected_task_ids)
        if not selected_list or len(set(selected_list)) != len(selected_list):
            raise ValueError("task selection must be nonempty and contain no duplicates")
        selected = {validate_task_id(value) for value in selected_list}
    source = validate_raw(raw)
    sentence_tokenizer, encoding = initialize(resources)
    import pyarrow.parquet as pq
    rows = []
    for relative in sorted(source["files"]):
        if relative.endswith(".parquet"):
            split = Path(relative).name.split("-00000")[0]
            for index, row in enumerate(pq.read_table(raw / relative).to_pylist()):
                if selected is None or task_id(split, index) in selected:
                    rows.append((split, index, row))
    if not rows or (selected is not None and {task_id(s, i) for s, i, _ in rows} != selected):
        raise ValueError("unknown or empty selected task IDs")
    frozen = {e["episode_id"]: e for e in read_spec("frozen-episodes.json")["episodes"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".mab-prepare-", dir=output.parent))
    try:
        for kind in ("service", "gold"):
            (temporary / kind).mkdir(mode=0o700)
        tasks = []
        for split, index, row in rows:
            public, service, gold = build_artifacts(split, index, row, sentence_tokenizer, encoding)
            _check_frozen(split, index, row, service, frozen)
            identifier = public["task_id"]
            _write(temporary / "service" / (identifier + ".json"), service)
            _write(temporary / "gold" / (identifier + ".json"), gold)
            tasks.append(public)
        shutil.copyfile(raw / "entity2id.json", temporary / "entity2id.json")
        (temporary / "entity2id.json").chmod(0o600)
        _write(temporary / "pool.json", {"schema": "hg-mab-pool-v1", "dataset_revision": DATASET_REVISION, "tasks": tasks})
        artifacts = {str(p.relative_to(temporary)): file_sha256(p) for p in sorted(temporary.rglob("*.json"))}
        code = [Path(__file__), Path(__file__).parent / "lib/inputs.py", Path(__file__).parent / "lib/storage.py"]
        manifest = {"schema": "hg-mab-manifest-v1", "dataset_revision": DATASET_REVISION,
                    "upstream_revision": source["upstream_revision"], "artifacts": artifacts,
                    "source_files": source["files"], "spec_sha256": SPEC_SHA256,
                    "implementation_sha256": {str(p.relative_to(Path(__file__).parent)): file_sha256(p) for p in code},
                    "resources": read_spec("resources.lock.json")["files"],
                    "input_protocol": "hg-mab-input-v1", "chunk_protocol": "hg-mab-chunk-v1",
                    "frozen_inputs_verified": True, "learning_evaluation_partition_created": False,
                    "counts": {"tasks": len(tasks), "chunks": sum(t["chunk_count"] for t in tasks),
                               "questions": sum(t["question_count"] for t in tasks)}}
        _write(temporary / "manifest.json", manifest)
        load_pool(temporary)
        for item in tasks:
            load_service(temporary, item["task_id"])
            load_gold(temporary, item["task_id"])
        if output.exists():
            raise FileExistsError("prepared output appeared during preparation")
        os.rename(temporary, output)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--resources", required=True, type=Path, help="Directory containing nltk/ and tiktoken/")
    parser.add_argument("--output", required=True, type=Path, help="Fresh directory outside the platform checkout")
    parser.add_argument("--task-id", action="append", dest="task_ids", help="Original stable task ID; repeat to select rows")
    args = parser.parse_args(argv)
    manifest = prepare(args.raw, args.resources, args.output, args.task_ids)
    print(json.dumps({"output": str(args.output.resolve()), **manifest["counts"], "frozen_inputs_verified": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
