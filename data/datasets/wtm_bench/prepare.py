#!/usr/bin/env python3
"""Materialize the curated Level 5 WTM-Bench evaluation from the pinned release."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from data.datasets import wtm_bench as adapter

DATA = "data/Backward_EnronFuse_v3"
PARQUET = f"{DATA}/enronfuse_150_test.parquet"
PARQUET_SHA256 = "8f669652cc224d250d8fd5d979b0490acd1822b08073ec49590915d33ddac32c"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


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
            catalog = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"),
                                 sort_keys=True).encode("utf-8")
            identity = digest(catalog)
            if identity != adapter.CATALOG_SHA256:
                raise ValueError(f"WTM-Bench prepared catalogue digest mismatch: {identity}")
            (stage / "catalog.json").write_bytes(catalog)
            stage.rename(output)
    return identity


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    root = adapter.DATASET_ROOT.resolve()
    print(prepare(args.archive or root / "source.tar.gz", root / "prepared"))
