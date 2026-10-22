"""Materialize GAIA's validation split: the pool, and the attachments it names.

    python3 data/datasets/gaia/prepare.py            # fetch + write pool.jsonl and attachments/
    python3 data/datasets/gaia/prepare.py --check    # re-digest what is on disk

Why a pool file
---------------
The release is a Hugging Face dataset: one `2023/validation/metadata.parquet` (116,994 bytes)
plus **42 attachment files** (10.3 MB) in the same tree, and the same again under
`2023/test/`. Reading a parquet at import would put `pyarrow` between the adapter and every
other dataset in `registry.published()`, and the attachments have to be addressed one file at a
time because a task ships at most one. So this script reads the parquet once, writes a 165-row
`pool.jsonl` (question, level, gold, the attachment's name/size/digest), and copies the
attachments flat into `attachments/`.

The three traps this script closes
----------------------------------
1. **The test split's gold is the placeholder `"?"`.** Measured: `2023/test/metadata.parquet`
   has 301 rows and **301 of them carry `"?"`** rather than an answer. A scorer run over that
   split would happily compare a submission against `"?"` and call it a gold. So the pool is
   the **validation** split only, and every row written here is checked to have a real answer.
2. **`Annotator Metadata` is the solution path, not metadata.** It is a dict whose `Steps` field
   walks through the search the annotator performed (the first row names the arXiv query, the
   date range and the article to open). It is **dropped here** and never reaches the pool; a test
   asserts the string cannot be found anywhere in a task's `SETUP` or goal.
3. **`file_path` is a repository-internal path.** Only the basename is kept, and the file is
   copied to `attachments/`, so nothing in the pool points back into the release tree.

The token
---------
The dataset is gated (`gated: "auto"`: accepting the terms authorises immediately) and its
files return 403 without one. The token is read from `HF_TOKEN`, else from the standard
`~/.cache/huggingface/token`, and is never written anywhere by this script. `huggingface.co`
is unreachable from this host, so downloads go through `hf-mirror.com`, which honours the same
token.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import data.datasets.gaia as adapter  # noqa: E402

POOL = "pool.jsonl"
ENDPOINT = "https://hf-mirror.com"
METADATA = "2023/validation/metadata.parquet"
METADATA_TEST = "2023/test/metadata.parquet"
README = "README.md"
#: The release tree has `metadata.parquet` under **two** directories, so a local name taken from
#: the basename is not unique: the first version of this script downloaded validation and then
#: test over it, and the "301 rows, all placeholder" reading that followed was the *test* file.
#: Measured, and the reason these names are spelled out.
LOCAL = {METADATA: "validation-metadata.parquet", METADATA_TEST: "test-metadata.parquet",
         README: "README.md"}


def token() -> str:
    value = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if value:
        return value.strip()
    path = pathlib.Path.home() / ".cache" / "huggingface" / "token"
    if path.is_file():
        return path.read_text().strip()
    raise SystemExit(
        "gaia: no Hugging Face token. The release is gated; export HF_TOKEN or write the token "
        "to ~/.cache/huggingface/token (the dataset is `gated: auto`, so accepting its terms on "
        "huggingface.co is enough).")


def _download(relative: str, destination: pathlib.Path) -> bytes:
    url = f"{ENDPOINT}/datasets/{adapter.REPO}/resolve/{adapter.REVISION}/{relative}"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token()}", "User-Agent": "harnessgrad-gaia"})
    with urllib.request.urlopen(request, timeout=300) as response:
        body = response.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    return body


def _download_json(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token()}", "User-Agent": "harnessgrad-gaia"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def fetch() -> dict:
    """Download the metadata and the validation attachments; return the measured provenance."""
    src = adapter.DATASET_ROOT / "_src"
    provenance: dict = {"parquets": {}}
    for relative in (METADATA, METADATA_TEST, README):
        body = _download(relative, src / LOCAL[relative])
        provenance["parquets"][relative] = {"bytes": len(body), "sha256": _sha256(body)}
    # The release's own file listing gives every attachment's size, which turns "did this download
    # work" into a comparison instead of a heuristic. The first version of this script guessed
    # (`len(body) < 1000` means an error page) and flagged three real files -- a .txt, a .py and a
    # .png that are simply small -- so the guess was replaced by the declared number.
    listing = json.loads(_download_json(f"{ENDPOINT}/api/datasets/{adapter.REPO}"))
    declared = {entry["rfilename"]: entry.get("size") for entry in listing.get("siblings", [])}
    import pyarrow.parquet as pq

    table = pq.read_table(src / LOCAL[METADATA]).to_pylist()
    table_test = pq.read_table(src / LOCAL[METADATA_TEST]).to_pylist()
    provenance["validation_rows"] = len(table)
    provenance["test_rows"] = len(table_test)
    provenance["test_placeholder_golds"] = sum(
        1 for row in table_test if str(row["Final answer"]).strip() in ("", "?"))
    provenance["attachments"] = {}
    rows, missing = [], []
    for index, row in enumerate(table):
        gold = str(row["Final answer"]).strip()
        if not gold or gold == "?":
            raise SystemExit(f"gaia: row {row['task_id']} has a placeholder gold {gold!r}; the "
                             f"pool must be the split whose answers are real")
        name = str(row["file_name"] or "").strip()
        entry = {"task_id": str(row["task_id"]), "index": index,
                 "question": str(row["Question"]), "level": str(row["Level"]),
                 "gold": gold, "attachment": name or None}
        if name:
            relative = str(row["file_path"]).strip()
            if not relative.endswith(name):
                raise SystemExit(f"gaia: file_path {relative!r} does not end in {name!r}")
            destination = adapter.DATASET_ROOT / adapter.ATTACHMENTS / name
            if destination.is_file():
                body = destination.read_bytes()
            else:
                body = _download(relative, destination)
            if declared.get(relative) not in (None, len(body)):
                missing.append(f"{name} ({len(body)} bytes, release says {declared[relative]})")
            entry["attachment_bytes"] = len(body)
            entry["attachment_sha256"] = _sha256(body)
            provenance["attachments"][name] = {"bytes": len(body), "sha256": _sha256(body)}
        rows.append(entry)
    if missing:
        raise SystemExit(f"gaia: {len(missing)} attachment(s) do not match the release's declared "
                         f"sizes: {missing[:3]}")
    provenance["attachment_count"] = len(provenance["attachments"])
    provenance["attachment_bytes"] = sum(v["bytes"] for v in provenance["attachments"].values())
    provenance["levels"] = {}
    for row in rows:
        provenance["levels"][row["level"]] = provenance["levels"].get(row["level"], 0) + 1
    return {"rows": rows, "provenance": provenance}


def digest() -> str:
    return _sha256((adapter.DATASET_ROOT / POOL).read_bytes())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    path = adapter.DATASET_ROOT / POOL
    if args.check:
        if not path.is_file():
            print(f"no pool at {path}", file=sys.stderr)
            return 1
        header = json.loads(path.open(encoding="utf-8").readline())
        print(f"{path}\n  rows {header['rows']}\n  sha256 {digest()}")
        return 0
    built = fetch()
    rows, provenance = built["rows"], built["provenance"]
    if len(rows) != adapter.ROWS:
        raise SystemExit(f"gaia: expected {adapter.ROWS} validation rows, got {len(rows)}")
    header = {"kind": "gaia-pool", "repo": adapter.REPO, "revision": adapter.REVISION,
              "split": "validation", "rows": len(rows), **provenance}
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header, sort_keys=True) + "\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"wrote {path} ({len(rows)} rows, {path.stat().st_size} bytes)")
    print(f"  attachments {provenance['attachment_count']} "
          f"({provenance['attachment_bytes'] / 1e6:.1f} MB) under {adapter.ATTACHMENTS}/")
    print(f"  test split: {provenance['test_rows']} rows, "
          f"{provenance['test_placeholder_golds']} placeholder golds -> excluded, asserted here")
    print(f"  validation levels {provenance['levels']}")
    print(f"  sha256 {digest()}")
    print("record that digest in POOL_SHA256 (data/datasets/gaia.py); the test asserts it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
