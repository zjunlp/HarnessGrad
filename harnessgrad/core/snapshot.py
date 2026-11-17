"""One round's harness tree as a single, self-verifying JSON file.

Why this exists: a run records every round's *diff* (`diffs/round-N.json` and
`diffs/round-N.patch`) but only sometimes keeps a full tree (`state_after_roundN/`).
So "edit/open round N" could fail with `round-not-stored` for a round the run had
actually measured. This module writes the round's tree as one JSON artifact beside
the diffs: it can be checked out, copied to another machine and verified there,
without git and without the run having kept a state directory.

The artifact is additive -- git still orders the rounds -- and immutable: the same
content written twice is a no-op, different content under an existing name is a
conflict. Its `id` is the sha256 of the canonical JSON of every other field, so a
reader can tell a truncated or edited snapshot from a genuine one, and `parent_id`
threads the rounds into the same chain the curve has.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path

#: What the file says it is. Bumping this is how a future shape is told apart from
#: this one instead of being misread as a corrupt file of the same shape.
SCHEMA = "harnessgrad.snapshot.v1"

#: Directories that are runtime state, not harness source. `.git` for the same reason
#: the record's other tree copies exclude it; `__pycache__`/`*.pyc` because bytecode is
#: a cache of the tree, not part of it.
_SKIPPED_DIRS = frozenset({".git", ".state", "__pycache__"})
_SKIPPED_SUFFIXES = (".pyc",)

#: A recorded file, in the JSON shape the artifact stores. A plain dict rather than a
#: class keeps `collect`'s result directly serialisable and directly comparable.
File = dict[str, object]


class SnapshotError(Exception):
    """Base class for a snapshot that cannot be trusted or cannot be written."""


class SnapshotInvalid(SnapshotError):
    """The tree or artifact is not a snapshot this module can represent or trust.

    Raised for a symlink or an unsafe path in the tree, a file that is not a regular
    file, a malformed or mis-addressed artifact, and a tree that no longer matches a
    stored snapshot.
    """


class SnapshotConflict(SnapshotError):
    """A different snapshot already occupies this round's name.

    A snapshot is immutable: the writer never replaces one. This is a conflict, not a
    corruption -- the caller's content is well formed, it just is not the file there.
    """


def path_for(run_dir, round_no: int) -> Path:
    """Where round `round_no` of the run at `run_dir` keeps its snapshot."""
    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 0:
        raise SnapshotInvalid(f"round must be a non-negative integer, not {round_no!r}")
    return Path(run_dir) / "snapshots" / f"round-{round_no}.json"


def collect(work_dir) -> dict[str, File]:
    """Record every regular file under `work_dir`, keyed by POSIX relative path.

    Skips `.git`, `.state`, `__pycache__` and `*.pyc`. A symlink anywhere in the tree is
    refused rather than followed or skipped: it would make the snapshot depend on
    something outside the tree, which is exactly what it exists not to do. Special
    files (fifos, sockets, devices) are not part of a harness and are skipped.

    A path that cannot be encoded as UTF-8 (a name that survived as a surrogate escape)
    is refused, because an artifact whose keys cannot be written as Unicode is not
    portable.
    """
    root = Path(work_dir)
    if root.is_symlink():
        raise SnapshotInvalid(f"the harness tree is a symlink: {root}")
    if not root.is_dir():
        raise SnapshotInvalid(f"not a directory: {root}")
    files: dict[str, File] = {}
    _walk(root, "", files)
    return files


def _walk(directory: Path, prefix: str, out: dict[str, File]) -> None:
    with os.scandir(directory) as entries:
        ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            name = entry.name
            if name in _SKIPPED_DIRS or name.endswith(_SKIPPED_SUFFIXES):
                continue
            rel = f"{prefix}/{name}" if prefix else name
            if entry.is_symlink():
                raise SnapshotInvalid(
                    f"a symlink has no portable content and would escape the tree: {rel}")
            if entry.is_dir(follow_symlinks=False):
                _walk(Path(entry.path), _check_relpath(rel), out)
            elif entry.is_file(follow_symlinks=False):
                out[_check_relpath(rel)] = _file_entry(Path(entry.path))
            # Else: a fifo, socket or device is not harness source. It is not recorded,
            # and it is not an error -- a run's tree is allowed to have a stray socket.


def _file_entry(path: Path) -> File:
    try:
        data = path.read_bytes()
        mode = os.stat(path, follow_symlinks=False).st_mode
    except OSError as exc:
        raise SnapshotInvalid(f"cannot read {path}: {exc}") from exc
    return {
        "content_b64": base64.b64encode(data).decode("ascii"),
        "executable": bool(mode & 0o111),
    }


def _check_relpath(rel) -> str:
    """Validate one relative POSIX path; return it unchanged when it is safe.

    This is the single gate for paths from the filesystem (`collect`) and from an
    artifact (`load`, and therefore `check_out`): `a/b` is safe, `/a`, `..`, `a/../b`,
    an empty component and a backslash are not.
    """
    if not isinstance(rel, str) or not rel:
        raise SnapshotInvalid(f"snapshot path must be a non-empty string: {rel!r}")
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SnapshotInvalid(f"snapshot path is not UTF-8: {rel!r}") from exc
    if "\x00" in rel or "\\" in rel:
        raise SnapshotInvalid(f"snapshot path is not portable: {rel!r}")
    if rel.startswith("/"):
        raise SnapshotInvalid(f"snapshot path is absolute: {rel!r}")
    for part in rel.split("/"):
        if part in ("", ".", ".."):
            raise SnapshotInvalid(f"snapshot path escapes its root: {rel!r}")
    return rel


def _digest(payload: dict) -> str:
    """The artifact id: sha256 over the canonical JSON of every field but `id`."""
    body = {key: value for key, value in payload.items() if key != "id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot(run_dir, round_no, work_dir, run_id, harness_sha, parent_id) -> dict:
    snapshot = {
        "schema": SCHEMA,
        "run_id": str(run_id),
        "round": round_no,
        "harness_sha": None if harness_sha is None else str(harness_sha),
        "parent_id": None if parent_id is None else str(parent_id),
        "files": collect(work_dir),
    }
    snapshot["id"] = _digest(snapshot)
    return snapshot


def write(run_dir, round_no, work_dir, *, run_id, harness_sha,
          parent_id=None) -> Path:
    """Store the tree at `work_dir` as round `round_no`'s snapshot; return its path.

    `parent_id` is the previous round's snapshot `id` (`load(previous).id`), or `None`
    at the start of a run. Nothing is inferred here: a resumed run that wants the chain
    must pass the id it already has.

    The file is published atomically and never overwritten. A second write of the same
    content is idempotent (the stored file is not touched); a write whose content
    differs from the stored file raises `SnapshotConflict`.
    """
    dest = path_for(run_dir, round_no)
    if not isinstance(run_id, str) or not run_id:
        raise SnapshotInvalid(f"run_id must be a non-empty string, not {run_id!r}")
    snapshot = _snapshot(run_dir, round_no, work_dir, run_id, harness_sha, parent_id)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        return _settled(dest, snapshot["id"])

    data = (json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.",
                                    suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            # `mkstemp` makes the file private; a record is read, not guarded.
            os.fchmod(handle.fileno(), 0o644)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, dest)
        except FileExistsError:
            # Another writer published this name between the check and the link. The
            # comparison below decides whether that is idempotence or a conflict.
            pass
        except OSError:
            # A filesystem without hard links. A rename is still atomic, but only onto
            # a name that is still absent -- the immutability rule is not negotiable.
            if not dest.exists():
                os.replace(tmp, dest)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

    if not dest.exists():
        raise SnapshotInvalid(f"the snapshot was not published: {dest}")
    return _settled(dest, snapshot["id"])


def _settled(dest: Path, expected_id: str) -> Path:
    """Accept the file at `dest` only when it already carries `expected_id`."""
    stored = load(dest)
    if stored["id"] != expected_id:
        raise SnapshotConflict(
            f"{dest} already holds snapshot {stored['id']}, not {expected_id}; "
            "a snapshot is immutable")
    return dest


def load(path) -> dict:
    """Read and validate a snapshot artifact; return its payload.

    Validates the schema, every path, every file entry and the id (recomputing the
    canonical digest), so a caller can trust the result without re-reading the file.
    Raises `SnapshotInvalid` for anything else, including a file that is not there.
    """
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SnapshotInvalid(f"cannot read snapshot {source}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SnapshotInvalid(f"snapshot is not UTF-8: {source}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise SnapshotInvalid(f"snapshot is not JSON: {source}: {exc}") from exc
    _validate(payload, source)
    return payload


def _validate(payload, source: Path) -> None:
    if not isinstance(payload, dict):
        raise SnapshotInvalid(f"snapshot is not an object: {source}")
    if payload.get("schema") != SCHEMA:
        raise SnapshotInvalid(
            f"snapshot schema is {payload.get('schema')!r}, expected {SCHEMA!r}: {source}")
    expected_fields = {"schema", "run_id", "round", "harness_sha", "parent_id", "files", "id"}
    missing = sorted(expected_fields - set(payload))
    extra = sorted(set(payload) - expected_fields)
    if missing or extra:
        raise SnapshotInvalid(f"snapshot fields are wrong ({missing=} {extra=}): {source}")

    if not isinstance(payload["run_id"], str) or not payload["run_id"]:
        raise SnapshotInvalid(f"snapshot run_id must be a non-empty string: {source}")
    if isinstance(payload["round"], bool) or not isinstance(payload["round"], int) \
            or payload["round"] < 0:
        raise SnapshotInvalid(f"snapshot round must be a non-negative integer: {source}")
    if payload["harness_sha"] is not None and not isinstance(payload["harness_sha"], str):
        raise SnapshotInvalid(f"snapshot harness_sha must be a string or null: {source}")
    if payload["parent_id"] is not None and not isinstance(payload["parent_id"], str):
        raise SnapshotInvalid(f"snapshot parent_id must be a string or null: {source}")

    files = payload["files"]
    if not isinstance(files, dict):
        raise SnapshotInvalid(f"snapshot files must be an object: {source}")
    for rel, entry in files.items():
        _check_relpath(rel)
        if not isinstance(entry, dict) or set(entry) != {"content_b64", "executable"}:
            raise SnapshotInvalid(f"snapshot entry for {rel!r} is malformed: {source}")
        if not isinstance(entry["executable"], bool):
            raise SnapshotInvalid(f"snapshot entry for {rel!r} has no executable flag: {source}")
        content = entry["content_b64"]
        if not isinstance(content, str):
            raise SnapshotInvalid(f"snapshot entry for {rel!r} is not base64 text: {source}")
        try:
            base64.b64decode(content, validate=True)
        except ValueError as exc:
            raise SnapshotInvalid(f"snapshot entry for {rel!r} is not base64: {source}") from exc

    ident = payload["id"]
    if not isinstance(ident, str) or len(ident) != 64 \
            or any(char not in "0123456789abcdef" for char in ident):
        raise SnapshotInvalid(f"snapshot id is not a sha256 hex digest: {source}")
    expected = _digest(payload)
    if expected != ident:
        raise SnapshotInvalid(
            f"snapshot id does not match its content (expected {expected}, "
            f"found {ident}): {source}")


def verify(path, work_dir) -> None:
    """Raise `SnapshotInvalid` unless `work_dir` is byte-for-byte the stored tree.

    Extra files, missing files, changed bytes and a changed executable bit are all
    differences: the snapshot claims a state, and this is how that claim is checked.
    """
    snapshot = load(path)
    actual = collect(work_dir)
    stored = snapshot["files"]
    missing = sorted(set(stored) - set(actual))
    extra = sorted(set(actual) - set(stored))
    if missing or extra:
        raise SnapshotInvalid(
            f"{path} does not describe {work_dir}: "
            f"missing {missing[:5]}{'...' if len(missing) > 5 else ''}, "
            f"extra {extra[:5]}{'...' if len(extra) > 5 else ''}")
    for rel in sorted(stored):
        if stored[rel] != actual[rel]:
            raise SnapshotInvalid(f"{path} differs from {work_dir} at {rel!r}")


def check_out(path, dest_dir) -> None:
    """Materialise the snapshot into `dest_dir`, refusing unsafe paths and symlinks.

    Creates parent directories as needed and writes each file with its recorded
    executable bit. Nothing in the artifact is trusted to stay inside `dest_dir`:
    every component is validated, and a pre-existing symlink in the destination is
    refused instead of written through.
    """
    snapshot = load(path)
    dest = Path(dest_dir)
    if dest.is_symlink():
        raise SnapshotInvalid(f"refusing to materialise into a symlink: {dest}")
    if dest.exists() and not dest.is_dir():
        raise SnapshotInvalid(f"not a directory: {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    for rel in sorted(snapshot["files"]):
        parts = rel.split("/")
        directory = dest
        for part in parts[:-1]:
            directory = directory / part
            if directory.is_symlink():
                raise SnapshotInvalid(f"refusing to write through a symlink: {directory}")
            if directory.exists() and not directory.is_dir():
                raise SnapshotInvalid(f"not a directory: {directory}")
            directory.mkdir(exist_ok=True)
        target = directory / parts[-1]
        if target.is_symlink():
            raise SnapshotInvalid(f"refusing to overwrite a symlink: {target}")
        entry = snapshot["files"][rel]
        data = base64.b64decode(entry["content_b64"], validate=True)
        target.write_bytes(data)
        os.chmod(target, 0o755 if entry["executable"] else 0o644)


def last_id(run_dir) -> str | None:
    """The id of the highest-numbered snapshot already stored in `run_dir`, if any.

    A resumed run threads this into its next `write(parent_id=...)`, so the chain a
    snapshot carries is the chain the curve has. An artifact that no longer validates
    is not a parent -- the chain is a convenience, never a reason to fail a run.
    """
    directory = Path(run_dir) / "snapshots"
    if not directory.is_dir():
        return None
    rounds = []
    for candidate in directory.glob("round-*.json"):
        number = candidate.stem[len("round-"):]
        if number.isdigit():
            rounds.append((int(number), candidate))
    if not rounds:
        return None
    try:
        return load(max(rounds)[1])["id"]
    except SnapshotInvalid:
        return None
