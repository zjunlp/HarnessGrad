#!/usr/bin/env python3
r"""Build BFCL-v3's `selected-index.v1.json` from the pinned tag tarball.

    python3 tools/datasets.py --fetch --dataset bfcl_v3      # the tarball, sha256-verified
    python3 data/datasets/bfcl_v3/prepare.py                 # this step: derive the index
    python3 data/datasets/bfcl_v3/prepare.py --check         # re-derive and compare; write nothing

Why this file exists
--------------------
`data/datasets/bfcl_v3.py` reads its rows **by byte span**, not by scanning the files: a
selection is a set of task ids, and each id resolves to an offset/length/sha256 triple in
this index (`_selected_records()`, `bfcl_v3.py:378-458`). That is what makes the
selected-only route cheap and proves it does not read a row it was not asked for. The price
is that the dataset is unusable until the index exists -- `provision_status()` requires it
and the control refuses with "selected index is unavailable or unreadable".

The index cannot ship inside `ShishirPatil/gorilla@v1.3`: it is not upstream's artifact, it
is this platform's mapping onto upstream's bytes. So it is **derived** here, from the same
tarball `downloads()` already pins, and derived again on any machine that has the tarball.
Nothing is downloaded, invented or copied from a fixture.

What the file must contain, and where that is defined
-----------------------------------------------------
The reader is the schema, so it is quoted rather than paraphrased:

* `bfcl_v3.py:383-388` requires a dict with `schema_version` **1** (an `int`, not `True`),
  `repo_tag` equal to `REPO_TAG`, `tarball_sha256` equal to `TARBALL_SHA256`, and `tasks`
  a dict keyed by task id.
* `bfcl_v3.py:393-405` requires every selected task to have a non-empty string
  `question_id`, plus a `question` span, plus an `answer` span **iff** the subset is
  `simple`. A span is `{"offset": int >= 0, "length": int > 0, "sha256": 64 lowercase hex}`,
  the length is capped by `MAX_SELECTED_ROW_BYTES`, and the offset must not overflow
  `offset + length`.
* `bfcl_v3.py:412-421` uses the span as `seek(offset)` / `read(length)`, checks the digest,
  and then requires `json.loads(payload)["id"]` to equal the entry's `question_id`. So a
  span is exactly one JSONL line, and its identity is inside the bytes it names.

Which tasks, and why this is not a selection judgement
-----------------------------------------------------
The index is the **identity map of the pool**: every row of both subsets, keyed by the same
positional id the full-pool reader derives (`_records()`, `bfcl_v3.py:461-479`: `simple/0`
is row 0 of `BFCL_v3_simple.json`, `irrelevance/0` is row 0 of
`BFCL_v3_irrelevance.json`). The pool is exactly those two official evaluation categories
(`OFFICIAL_EVAL = None`, `SPLIT = None`: nothing in it is study material), so there is no
subset to choose and no study/eval division to invent. An index that held fewer tasks would
be a selection decision, and this builder makes none.

The traps this closes
---------------------
1. **BFCL is JSONL, and the last line has no newline.** Measured on tag v1.3: the three
   files are 400/240/400 rows at 280,474/160,120/60,809 bytes, and none ends with `\n`.
   Byte offsets are counted over the raw file here, not over a re-serialised row, so the
   final span reaches EOF exactly.
2. **`_read_jsonl()` skips whitespace-only lines.** A span is therefore built from a
   *non-blank* line scan, and the positional task id counts non-blank lines only. Counting
   newline characters instead would shift every id after a blank line onto the wrong row.
3. **The answer file is a separate file, joined on `id`, not on position.** The answer span
   must name the line whose `id` equals the question's; the digest check would pass on a
   misaligned span and the identity check would not, so the join is explicit here rather
   than assumed.
4. **A span as long as the file is a valid-looking index that reads one giant row.** The
   reader caps a row at `MAX_SELECTED_ROW_BYTES`; the builder applies the same cap at
   derivation time, so a malformed line fails here instead of at every later load.

What the artifact does **not** claim
------------------------------------
This JSON is not authenticated. Its `repo_tag`, `tarball_sha256` and row digests are
caller-authorable assertions that happen to be reproducible from the pinned tarball, which
is why the builder refuses when the tarball's digest differs. External index custody,
release provisioning and immutable source storage remain separate production gates, and a
digest does not make released bytes confidential (the loader says the same at
`bfcl_v3.py:350-361`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import data.datasets.bfcl_v3 as adapter  # noqa: E402

#: The one command that puts the tarball where this builder looks. Spelled the way
#: `data/scaffolds/registry.published()` names the dataset (`bfcl_v3`), because
#: `tools/datasets.py --dataset` filters on that exact string: `bfcl-v3` matches nothing and
#: exits 0, which is a no-op dressed as success.
FETCH = "python3 tools/datasets.py --fetch --dataset bfcl_v3"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(payload: bytes, relative: str) -> list[tuple[int, int, dict]]:
    """`(offset, length, row)` for every non-blank line, in file order.

    Mirrors `_read_jsonl()`: whitespace-only lines are skipped and every other line must
    parse as one JSON object. Offsets are byte counts into `payload`, because that is what
    the reader seeks with.
    """
    out, offset = [], 0
    for line in payload.split(b"\n"):
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{relative}: the line at byte {offset} is not JSON") from exc
            out.append((offset, len(line), row))
        # Every element of `split(b"\n")` except the last is followed by exactly one "\n",
        # so the next row starts after line + 1 -- including when the last line has no
        # trailing newline, which is how all three files in the v1.3 tag end.
        offset += len(line) + 1
    return out


def _span(offset: int, length: int, payload: bytes) -> dict:
    return {"offset": offset, "length": length,
            "sha256": _sha256(payload[offset:offset + length])}


def _table(payload: bytes, relative: str, expected: int, cap: int) -> list[tuple[int, int, dict]]:
    """One source file, checked against the release's row count and the reader's limits."""
    rows = _rows(payload, relative)
    if len(rows) != expected:
        raise ValueError(f"{relative} has {len(rows)} rows, expected {expected}")
    seen = set()
    for offset, length, row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError(f"{relative}: the row at byte {offset} has no string `id`")
        # The reader refuses a row longer than this; refusing it here keeps a malformed span
        # from being written and then failing on every later load.
        if length > cap:
            raise ValueError(f"{relative}: the row at byte {offset} is {length} bytes, "
                             f"over the reader's {cap}-byte row cap")
        if row["id"] in seen:
            raise ValueError(f"{relative}: duplicate id {row['id']!r}")
        seen.add(row["id"])
    return rows


def _member_bytes(archive: tarfile.TarFile, name: str) -> bytes:
    """The bytes of one regular file member, by exact name."""
    try:
        member = archive.getmember(name)
    except KeyError:
        raise ValueError(f"the pinned tarball has no member {name}") from None
    if not member.isfile():
        raise ValueError(f"the pinned tarball's {name} is not a regular file")
    handle = archive.extractfile(member)
    if handle is None:
        raise ValueError(f"the pinned tarball's {name} cannot be read")
    with handle:
        return handle.read()


def _materialise(path: pathlib.Path, payload: bytes) -> None:
    """Put the pinned bytes where the reader seeks, or prove they are already there.

    `tools/datasets.py --fetch` extracts the tag into the root, so this normally finds the
    file and only compares. It writes when the extraction is absent, which keeps this step
    usable from a root that holds only `_src/`; either way the file the spans point into is
    byte-for-byte the pinned member.
    """
    if path.is_file():
        if path.read_bytes() != payload:
            raise ValueError(f"{path} differs from the pinned tarball; re-run `{FETCH}` to "
                             "re-extract the tag")
        return
    if path.exists():
        raise ValueError(f"{path} exists but is not a regular file")
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".part")
    try:
        pending.write_bytes(payload)
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def _sources() -> list[str]:
    """The three release files, read from the adapter at call time (it is the authority)."""
    return [adapter.SIMPLE, adapter.IRRELEVANCE, adapter.SIMPLE_ANSWERS]


def _tarball_path(root: pathlib.Path, tarball) -> pathlib.Path:
    if tarball is not None:
        return pathlib.Path(tarball)
    # Read `TARBALL_NAME` at call time: a re-pin must change the adapter and nothing here.
    return pathlib.Path(root) / "_src" / adapter.TARBALL_NAME


def _pinned_payloads(root: pathlib.Path, tarball) -> tuple[str, dict[str, bytes]]:
    """The tarball's digest and every source member's bytes, after proving the pin.

    The digest is computed over the whole file rather than trusted from `tarfile`, and a
    mismatch is a refusal: an index derived from different bytes would carry a
    `tarball_sha256` field that is true of the pin and false of its own spans.
    """
    path = _tarball_path(root, tarball)
    if not path.is_file():
        raise ValueError(
            f"the pinned tarball is not at {path}. Fetch it with `{FETCH}` "
            f"(sha256 {adapter.TARBALL_SHA256}), then run this again")
    digest = _file_sha256(path)
    if digest != adapter.TARBALL_SHA256:
        raise ValueError(
            f"{path} has sha256 {digest}, but the adapter pins {adapter.TARBALL_SHA256}; "
            f"refusing to derive an index from bytes that are not tag {adapter.REPO_TAG}. "
            f"Re-fetch with `{FETCH} --force`")
    payloads = {}
    with tarfile.open(path, "r:*") as archive:
        for relative in _sources():
            payloads[relative] = _member_bytes(archive, relative)
    return digest, payloads


def _derive(root, tarball=None) -> tuple[dict, dict[str, bytes]]:
    """The index for the pinned tarball under `root`, plus the payloads it was read from."""
    root = pathlib.Path(root)
    cap = adapter.MAX_SELECTED_ROW_BYTES
    digest, payloads = _pinned_payloads(root, tarball)

    simple = _table(payloads[adapter.SIMPLE], adapter.SIMPLE, adapter.N_SIMPLE, cap)
    irrelevance = _table(payloads[adapter.IRRELEVANCE], adapter.IRRELEVANCE,
                         adapter.N_IRRELEVANCE, cap)
    answers = _table(payloads[adapter.SIMPLE_ANSWERS], adapter.SIMPLE_ANSWERS,
                     adapter.N_SIMPLE, cap)
    simple_ids = [row["id"] for _, _, row in simple]
    answer_rows = {row["id"]: (offset, length) for offset, length, row in answers}
    # The reader joins the answer span on the question's `id`, not on position, and checks
    # that identity after reading. A missing or extra answer id is therefore a refusal here
    # rather than a task that only fails when it is selected.
    if set(answer_rows) != set(simple_ids):
        missing = sorted(set(simple_ids) - set(answer_rows))[:3]
        extra = sorted(set(answer_rows) - set(simple_ids))[:3]
        raise ValueError(f"{adapter.SIMPLE_ANSWERS} does not answer the same ids as "
                         f"{adapter.SIMPLE} (missing {missing}, unexpected {extra})")

    tasks: dict[str, dict] = {}
    question_payload = payloads[adapter.SIMPLE]
    answer_payload = payloads[adapter.SIMPLE_ANSWERS]
    for index, (offset, length, row) in enumerate(simple):
        # `simple/<i>` is row i of the question file -- the id `_records()` derives for the
        # full pool, so the selected route and the full route name the same task.
        answer_offset, answer_length = answer_rows[row["id"]]
        tasks[f"simple/{index}"] = {
            "question_id": row["id"],
            "question": _span(offset, length, question_payload),
            "answer": _span(answer_offset, answer_length, answer_payload),
        }
    irrelevance_payload = payloads[adapter.IRRELEVANCE]
    for index, (offset, length, row) in enumerate(irrelevance):
        # `irrelevance` needs no answer file: its gold is the empty set, and the reader
        # requires no `answer` span for it.
        tasks[f"irrelevance/{index}"] = {"question_id": row["id"],
                                         "question": _span(offset, length, irrelevance_payload)}

    return {
        "schema_version": 1,
        "repo_tag": adapter.REPO_TAG,
        "tarball_sha256": adapter.TARBALL_SHA256,
        # The digests this artifact was derived from, so a reader can re-derive it instead
        # of trusting it. `tarball_sha256` above is the pin; this is its measured value.
        "derived_from": {
            "builder": "data/datasets/bfcl_v3/prepare.py",
            "tarball": adapter.TARBALL_NAME,
            "tarball_sha256": digest,
            "files": {
                relative: {"bytes": len(payload), "sha256": _sha256(payload),
                           "rows": len(_rows(payload, relative))}
                for relative, payload in payloads.items()
            },
        },
        "tasks": tasks,
    }, payloads


def derive(root, tarball=None) -> dict:
    """The index for the pinned tarball under `root`. Reads and checks; writes nothing."""
    return _derive(root, tarball)[0]


def _canonical(manifest: dict) -> bytes:
    """Deterministic bytes: same tarball, same file, so `--check` can compare byte for byte."""
    return (json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def verify(root, manifest: dict) -> int:
    """Re-read every span from the files under `root` and check it against `manifest`.

    This is the self-check the artifact exists to make possible: each span is resolved the
    way the loader resolves it -- `seek`, `read`, digest, then the row's own `id` -- and a
    span must be the exact bytes of the row whose positional task id names it, so a shifted
    or wrong-length index cannot pass. Returns the number of tasks verified.
    """
    root = pathlib.Path(root)
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or manifest.get("repo_tag") != adapter.REPO_TAG
            or manifest.get("tarball_sha256") != adapter.TARBALL_SHA256
            or not isinstance(manifest.get("tasks"), dict)):
        raise ValueError("the index does not carry the pinned release binding")
    tasks = manifest["tasks"]
    expected = adapter.N_SIMPLE + adapter.N_IRRELEVANCE
    if len(tasks) != expected:
        raise ValueError(f"the index holds {len(tasks)} tasks, expected {expected}")
    for subset, relative, count, answered in (
            ("simple", adapter.SIMPLE, adapter.N_SIMPLE, True),
            ("irrelevance", adapter.IRRELEVANCE, adapter.N_IRRELEVANCE, False)):
        path = root / relative
        if not path.is_file():
            raise ValueError(f"the index names {relative}, which is not at {path}; "
                             f"re-run `{FETCH}`")
        payload = path.read_bytes()
        rows = _rows(payload, relative)
        if len(rows) != count:
            raise ValueError(f"{relative} has {len(rows)} rows, expected {count}")
        answer_payload = (root / adapter.SIMPLE_ANSWERS).read_bytes() if answered else None
        answer_rows = ({row["id"]: (offset, length)
                        for offset, length, row in _rows(answer_payload, adapter.SIMPLE_ANSWERS)}
                       if answered else {})
        sources = [("question", payload)]
        if answered:
            sources.append(("answer", answer_payload))
        for index, (offset, length, row) in enumerate(rows):
            task_id = f"{subset}/{index}"
            entry = tasks.get(task_id)
            if not isinstance(entry, dict) or entry.get("question_id") != row["id"]:
                raise ValueError(f"the index has no {task_id} for {row['id']!r}")
            wanted = {"question": (offset, length)}
            if answered:
                if row["id"] not in answer_rows:
                    raise ValueError(f"{adapter.SIMPLE_ANSWERS} has no row for {row['id']!r}")
                wanted["answer"] = answer_rows[row["id"]]
            for kind, source in sources:
                span = entry.get(kind)
                if not isinstance(span, dict):
                    raise ValueError(f"the index has no {kind} span for {task_id}")
                if (span.get("offset"), span.get("length")) != wanted[kind]:
                    raise ValueError(f"the index's {kind} span for {task_id} is not this "
                                     f"{row['id']!r} row")
                start = span["offset"]
                chunk = source[start:start + span["length"]]
                if _sha256(chunk) != span["sha256"]:
                    raise ValueError(f"the index's {kind} span digest for {task_id} does not "
                                     "match the bytes on disk")
    return len(tasks)


def build(root, tarball=None) -> dict:
    """Derive, materialise the sources, verify, then write `selected-index.v1.json`."""
    root = pathlib.Path(root)
    manifest, payloads = _derive(root, tarball)
    for relative, payload in payloads.items():
        _materialise(root / relative, payload)
    # Verify against the files the reader will actually open, not against the tarball in
    # memory: the artifact's spans are only useful if they resolve on disk.
    verify(root, manifest)
    index = root / adapter.SELECTED_INDEX
    pending = index.with_name(index.name + ".part")
    try:
        pending.write_bytes(_canonical(manifest))
        pending.replace(index)
    finally:
        pending.unlink(missing_ok=True)
    # What was written is what was derived: re-read it rather than trusting the buffer.
    if index.read_bytes() != _canonical(manifest):
        raise ValueError(f"{index} did not round-trip")
    return manifest


def check(root, tarball=None) -> dict:
    """Re-derive and require the committed index to still be exactly that. Writes nothing."""
    root = pathlib.Path(root)
    manifest = derive(root, tarball)
    index = root / adapter.SELECTED_INDEX
    if not index.is_file():
        raise ValueError(f"no index at {index}; build it with `python3 {_rel()}`")
    verify(root, manifest)
    if index.read_bytes() != _canonical(manifest):
        raise ValueError(f"{index} is stale: it no longer matches the pinned tarball. Rebuild "
                         f"it with `python3 {_rel()}`")
    return manifest


def _rel() -> str:
    return str(pathlib.Path(__file__).resolve().relative_to(ROOT))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=pathlib.Path,
                        help="dataset root (default: the adapter's DATASET_ROOT, which honours "
                             "HG_DATA_ROOT / HG_DATA_BFCL_V3)")
    parser.add_argument("--tarball", type=pathlib.Path,
                        help="the pinned tarball (default: <root>/_src/<TARBALL_NAME>)")
    parser.add_argument("--check", action="store_true",
                        help="re-derive and compare an existing index; write nothing")
    args = parser.parse_args(argv)
    # Read at call time, like every other adapter field: the root follows HG_DATA_ROOT set
    # in this process's environment.
    root = args.root or adapter.DATASET_ROOT
    try:
        manifest = check(root, args.tarball) if args.check else build(root, args.tarball)
    except ValueError as exc:
        print(f"bfcl-v3: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "prepared": not args.check,
        "root": str(root),
        "index": str(pathlib.Path(root) / adapter.SELECTED_INDEX),
        "tasks": len(manifest["tasks"]),
        "repo_tag": manifest["repo_tag"],
        "tarball_sha256": manifest["tarball_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
