#!/usr/bin/env python3
"""Materialize the curated evaluation or mixed-level WTM-Bench research collection."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data.datasets import wtm_bench as adapter
from data.scaffolds.native_harbor import safe_file
from tools.dataset_maintenance.datasets import _download

DATA = "data/Backward_EnronFuse_v3"
PARQUET = f"{DATA}/enronfuse_150_test.parquet"
PARQUET_SHA256 = "8f669652cc224d250d8fd5d979b0490acd1822b08073ec49590915d33ddac32c"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode_catalog(catalog):
    return json.dumps(catalog, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def read_member(archive, relative):
    name = f"WTM-Bench-{adapter.REVISION}/{relative}"
    member = archive.getmember(name)
    if not member.isfile() or not 0 < member.size <= 32 * 1024 * 1024:
        raise ValueError(f"invalid pinned WTM-Bench source member: {relative}")
    with archive.extractfile(member) as source:
        return source.read()


def build_catalog(archive, stage):
    import pyarrow.parquet as parquet

    raw = read_member(archive, PARQUET)
    if digest(raw) != PARQUET_SHA256:
        raise ValueError("WTM-Bench Level 5 parquet digest mismatch")
    rows = []
    seen = set()
    for native in parquet.read_table(io.BytesIO(raw), columns=["prompt", "extra_info"]).to_pylist():
        extra = native["extra_info"]
        name = extra["task"]
        prompt = native["prompt"]
        if (not re.fullmatch(r"Backward_EnronFuse\.[0-9]+", name) or name in seen
                or extra["level"] != 5 or len(prompt) != 2
                or [item["role"] for item in prompt] != ["system", "user"]
                or not isinstance(extra["query"], str) or not extra["query"].strip()
                or extra["query"] not in prompt[1]["content"]):
            raise ValueError("unexpected task in pinned WTM-Bench Level 5 collection")
        seen.add(name)
        row = {"task_id": f"wtm_bench/{name}", "prompt": prompt[1]["content"]}
        for key, field, relative, target in (
            ("initial", "initial_filepath", f"{DATA}/workbooks/{name}.xlsx", f"initial/{name}.xlsx"),
            ("expected", "gt_filepath", f"{DATA}/metadata/ExpectedWorkbook/{name}-File.xlsx",
             f"expected/{name}.xlsx"),
        ):
            if extra[field] != relative:
                raise ValueError("unexpected workbook path in pinned WTM-Bench collection")
            content = read_member(archive, relative)
            (stage / target).write_bytes(content)
            row.update({key: target, f"{key}_sha256": digest(content)})
        rows.append(row)
    if len(rows) != adapter.TASK_COUNT:
        raise ValueError("unexpected WTM-Bench task count")
    return {"revision": adapter.REVISION, "source_sha256": adapter.ARCHIVE_SHA256,
            "parquet_sha256": PARQUET_SHA256, "rows": rows}


def prepare(source, output):
    with source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != adapter.ARCHIVE_SHA256:
            raise ValueError("WTM-Bench archive digest mismatch")
        stream.seek(0)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError(f"WTM-Bench prepared directory already exists: {output}")
        with tempfile.TemporaryDirectory(prefix=".prepare-wtm-", dir=output.parent) as temp:
            stage = Path(temp) / "prepared"
            stage.mkdir(mode=0o700)
            (stage / "initial").mkdir()
            (stage / "expected").mkdir()
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                scorer = read_member(archive, "grader/scorer_v2.py")
                if digest(scorer) != adapter.SCORER_SHA256:
                    raise ValueError("WTM-Bench scorer digest mismatch")
                (stage / "scorer_v2.py").write_bytes(scorer)
                (stage / "LICENSE").write_bytes(read_member(archive, "LICENSE"))
                manifest = build_catalog(archive, stage)
            catalog = encode_catalog(manifest)
            identity = digest(catalog)
            if identity != adapter.CATALOG_SHA256:
                raise ValueError(f"WTM-Bench prepared catalogue digest mismatch: {identity}")
            (stage / "catalog.json").write_bytes(catalog)
            stage.rename(output)
    return identity


def read_study_source(source, relative, expected):
    content = safe_file(source, relative).read_bytes()
    if digest(content) != expected:
        raise ValueError(f"WTM-Bench study source digest mismatch: {relative}")
    return content


def fetch_study_source(source, endpoint, membership):
    repository = f"{endpoint}/datasets/{adapter.STUDY_REPOSITORY}"
    revision = adapter.STUDY_REVISION
    _download(f"{repository}/resolve/{revision}/{adapter.STUDY_PARQUET}",
              source / adapter.STUDY_PARQUET, timeout=120)
    git_root = source / "repo"
    subprocess.run(["git", "init", "--quiet", "--bare", str(git_root)], check=True)
    git = ["git", "-C", str(git_root)]
    subprocess.run([*git, "fetch", "--quiet", "--depth", "1", "--no-tags", repository, revision],
                   env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, check=True)
    paths = [path for row in membership
             for path in adapter.study_workbook_paths(row["task_id"].split("/", 1)[1]).values()]
    with tempfile.TemporaryFile(dir=source) as stream:
        subprocess.run([*git, "archive", "--format=tar", revision, "--", *paths], stdout=stream, check=True)
        stream.seek(0)
        with tarfile.open(fileobj=stream) as archive:
            for path in paths:
                member = archive.getmember(path)
                if not member.isfile() or not 0 < member.size <= 32 * 1024 * 1024:
                    raise ValueError(f"invalid pinned WTM-Bench study workbook: {path}")
                target = source / path
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as workbook:
                    target.write_bytes(workbook.read())


def build_study_catalog(stage, source, membership):
    import pyarrow.parquet as parquet

    selections = {row["task_id"]: row for row in membership}
    if len(membership) != adapter.STUDY_COUNT or len(selections) != adapter.STUDY_COUNT:
        raise ValueError("WTM-Bench study must select each of 300 underlying tasks exactly once")
    raw = read_study_source(source, adapter.STUDY_PARQUET, adapter.STUDY_PARQUET_SHA256)
    rows = {}
    for native in parquet.read_table(io.BytesIO(raw), columns=["prompt", "extra_info"]).to_pylist():
        extra, prompt = native["extra_info"], native["prompt"]
        name, level = extra["task"], extra["level"]
        task_id = f"wtm_bench/{name}"
        selection = selections.get(task_id)
        if selection is None or level != selection["level"]:
            continue
        paths = adapter.study_workbook_paths(name)
        if (task_id in rows or level not in adapter.STUDY_LEVELS
                or [item["role"] for item in prompt] != ["system", "user"]
                or not isinstance(extra["query"], str) or not extra["query"].strip()
                or extra["query"] not in prompt[1]["content"]
                or extra["initial_filepath"] != "data/" + paths["initial"]
                or extra["gt_filepath"] != "data/" + paths["expected"]):
            raise ValueError(f"unexpected WTM-Bench study source row: {task_id} Level {level}")
        rows[task_id] = {"task_id": task_id, "level": level, "prompt": prompt[1]["content"]}
    if rows.keys() != selections.keys():
        raise ValueError("WTM-Bench study membership does not match the pinned full parquet")

    for task_id in sorted(rows):
        row, selection = rows[task_id], selections[task_id]
        name = task_id.split("/", 1)[1]
        for side, relative in adapter.study_workbook_paths(name).items():
            expected = selection[f"{side}_sha256"]
            content = read_study_source(source, relative, expected)
            target = f"{side}/{name}.xlsx"
            (stage / target).write_bytes(content)
            row.update({side: target, f"{side}_sha256": expected})
    return {"revision": adapter.STUDY_REVISION, "parquet_sha256": adapter.STUDY_PARQUET_SHA256,
            "rows": [rows[task_id] for task_id in sorted(rows)]}


def prepare_study(source, output, endpoint=None):
    if output.exists():
        raise FileExistsError(f"WTM-Bench prepared study directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    membership = json.loads((adapter.ASSETS / "study-300.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix=".prepare-wtm-study-", dir=output.parent) as temp:
        stage = Path(temp) / "prepared-study"
        stage.mkdir(mode=0o700)
        (stage / "initial").mkdir()
        (stage / "expected").mkdir()
        if endpoint:
            source = Path(temp) / "source"
            fetch_study_source(source, endpoint, membership)
        catalogue = encode_catalog(build_study_catalog(stage, source, membership))
        identity = digest(catalogue)
        if identity != adapter.STUDY_CATALOG_SHA256:
            raise ValueError(f"WTM-Bench prepared study catalogue digest mismatch: {identity}")
        (stage / "catalog.json").write_bytes(catalogue)
        stage.rename(output)
    return identity


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    study = parser.add_mutually_exclusive_group()
    study.add_argument("--study", action="store_true", help="Download the pinned mixed-level study sources")
    study.add_argument("--study-source", type=Path, help="Use an existing local Hugging Face study snapshot")
    args = parser.parse_args()
    root = adapter.DATASET_ROOT.resolve()
    if args.study or args.study_source is not None:
        if args.archive is not None:
            parser.error("--archive is for preparing the curated evaluation")
        adapter.records()
        endpoint = (os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/") if args.study else None
        print(prepare_study(args.study_source, root / "prepared-study", endpoint))
    else:
        print(prepare(args.archive or root / "source.tar.gz", root / "prepared"))
