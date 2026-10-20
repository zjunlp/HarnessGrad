#!/usr/bin/env python3
"""Prepare text-32k episodes from pinned CSV rows and their exact history files."""
import argparse
import ast
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.datasets.personamem_v2.lib.inputs import history_chunks, question, question_id
from data.datasets.personamem_v2.lib.protocol import REVISION, CODE_REVISION, METRIC, SPLITS, canonical, digest
from data.datasets.personamem_v2.lib.storage import file_sha, task_id

ASSETS = Path(__file__).resolve().parent
COUNTS = {"train": 18549, "val": 2061, "benchmark": 5000}


def source_pins():
    raw = (ASSETS / "specs/source.json").read_bytes()
    pins = json.loads(raw)
    if pins["revision"] != REVISION or pins["code_revision"] != CODE_REVISION:
        raise ValueError("source revision mismatch")
    return pins, hashlib.sha256(raw).hexdigest()


def history_path(value):
    # Native CSV paths are data/chat_history_32k/<filename>.json.
    path = PurePosixPath(value)
    if (len(path.parts) != 3 or path.parts[:2] != ("data", "chat_history_32k")
            or path.suffix != ".json" or path.is_absolute()):
        raise ValueError("invalid native text-32k history path")
    return str(path)


def prepare(raw, output, selected=None):
    raw, output = Path(raw).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("prepared output already exists")
    pins, source_sha = source_pins()
    groups = defaultdict(list)
    for split in SPLITS:
        name = f"benchmark/text/{split}.csv"
        path = raw / name
        if file_sha(path) != pins["files"][name]["sha256"]:
            raise ValueError("source CSV digest mismatch: " + name)
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != COUNTS[split]:
            raise ValueError("native question inventory mismatch")
        for index, row in enumerate(rows):
            persona = int(row["persona_id"])
            groups[task_id(split, persona)].append((split, index, row))
    requested = set(groups) if selected is None else set(selected)
    if not requested or requested - groups.keys():
        raise ValueError("unknown or empty selected episode set")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".personamem-prepare-", dir=output.parent))
    try:
        artifacts, pool = {}, []
        history_cache = {}
        def write(name, value):
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(canonical(value) + "\n")
            artifacts[name] = file_sha(path)
        for tid in sorted(requested):
            entries = groups[tid]
            split, _, first = entries[0]
            histories = {history_path(row["chat_history_32k_link"]) for _, _, row in entries}
            if len(histories) != 1:
                raise ValueError("one persona/split references multiple histories")
            history = histories.pop()
            if history not in history_cache:
                if history not in pins["files"] or file_sha(raw / history) != pins["files"][history]["sha256"]:
                    raise ValueError("source history digest mismatch: " + history)
                data = json.loads((raw / history).read_text())
                if isinstance(data, dict) and data.get("metadata", {}).get("persona_id") != int(first["persona_id"]):
                    raise ValueError("history persona mismatch")
                history_cache[history] = history_chunks(data)
            chunks = history_cache[history]
            public_questions, answers, source_errors, normalizations = [], [], [], []
            for _, index, row in entries:
                text, letter = question(row, index)
                qid = question_id(tid, row["user_query"])
                if letter is None:
                    source_errors.append(qid)
                else:
                    try:
                        ast.literal_eval(row["incorrect_answers"])
                    except (ValueError, SyntaxError):
                        normalizations.append(qid)
                public_questions.append({"id": qid, "text": text})
                answers.append({"id": qid, "correct_letter": letter, "row_index": index,
                                "pref_type": row["pref_type"], "updated": row["updated"],
                                "sensitive_info": row["sensitive_info"]})
            if len({q["id"] for q in public_questions}) != len(public_questions):
                raise ValueError("duplicate native query within one episode")
            payload = {"schema": "personamem-v2-input-v1", "task_id": tid,
                       "chunks": chunks, "questions": public_questions, "source_errors": source_errors}
            payload["input_sha256"] = digest(payload)
            gold = {"schema": "personamem-v2-gold-v1", "task_id": tid, "metric": METRIC,
                    "split": split, "persona_id": int(first["persona_id"]),
                    "input_sha256": payload["input_sha256"], "chunk_count": len(chunks),
                    "questions": answers, "history_sha256": pins["files"][history]["sha256"],
                    "source_manifest_sha256": source_sha, "source_errors": source_errors,
                    "normalized_json_distractors": normalizations}
            write(f"service/{tid}.json", payload)
            write(f"gold/{tid}.json", gold)
            pool.append({"task_id": tid, "split": split, "persona_id": int(first["persona_id"]),
                         "question_count": len(answers), "chunk_count": len(chunks),
                         "input_sha256": payload["input_sha256"], "source_errors": source_errors,
                         "normalized_json_distractors": normalizations})
        write("pool.json", pool)
        metadata = {"schema": "personamem-v2-manifest-v1", "dataset_revision": REVISION,
                    "code_revision": CODE_REVISION, "metric": METRIC,
                    "source_manifest_sha256": source_sha, "artifacts": artifacts,
                    "native_question_counts": COUNTS, "selected_episodes": len(pool)}
        (staging / "manifest.json").write_text(canonical(metadata) + "\n")
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging)
        raise
    return {"episodes": len(pool), "questions": sum(r["question_count"] for r in pool),
            "native_episodes": dict(Counter(row["split"] for row in pool)),
            "output": str(output), "manifest_sha256": file_sha(output / "manifest.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task-id", action="append")
    args = parser.parse_args()
    print(json.dumps(prepare(args.raw, args.output, args.task_id)))


if __name__ == "__main__":
    main()
