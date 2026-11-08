"""Candidate file custody, bounded save/archive and provenance operations.

The configured root/ancestors are trusted as in the existing fd contracts. The
store never discovers another root, imports candidate code or runs a process.
Descriptor-relative nofollow checks, publication and cleanup stay together.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shlex
import shutil
import stat
import sys
import time
from dataclasses import dataclass
if __package__:
    from .record_reader import read_json
else:
    from record_reader import read_json

CANDIDATE_NAME = re.compile(r"[A-Za-z0-9._-]{1,48}")

@dataclass(frozen=True)
class CandidateLimits:
    file_bytes: int = 2 * 1024 * 1024
    selection_bytes: int = 1024 * 1024
    selection_lines: int = 10000
    source_bytes: int = 200 * 1024 * 1024
    overlay_bytes: int = 2 * 1024 * 1024

def valid_workbench_candidate_name(name: str) -> bool:
    return isinstance(name, str) and name not in (".", "..") and bool(CANDIDATE_NAME.fullmatch(name))


class CandidateReadError(ValueError):
    """A sanitized, typed failure from the descriptor-relative candidate reader."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _candidate_tree_sha(root: pathlib.Path) -> dict[str, str]:
    """目录里每个文件的 sha256。跳过缓存与 VCS，迁移不算改动。"""
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        try:
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def _candidate_record_source(record: dict) -> str:
    """账本里这一版是哪个来源：`state` / `snapshot` / `replay`。

    新账本直接写 `source`。旧账本的 `via` 只有 `replay` 与非 replay 两种，而非 replay 的
    副本都来自 `state_after_roundN/`（那时还没有快照这一路），所以按 `state` 读。
    """
    source = record.get("source")
    if source in ("state", "snapshot", "replay"):
        return str(source)
    return "replay" if record.get("via") == "replay" else "state"


def load_module(root, name):
    spec = importlib.util.spec_from_file_location('hg_console_' + name, root / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_ledger(root):
    spec = importlib.util.spec_from_file_location('hg_round_ledger', root / 'round_ledger.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CandidateStore:
    def __init__(self, root, base_harness, *, limits, ledger, fingerprint=_candidate_tree_sha):
        self.root, self.base_harness = root, base_harness
        self.limits = limits
        self.ledger = ledger
        self.fingerprint = fingerprint

    def sources(self) -> dict:
        if not self.root.exists():
            return {}
        with self.ledger().locked(self.root) as ledger:
            return ledger.read()


    def remember(self, name: str, run_id: str, round_no: int, via: str,
                            target: pathlib.Path, *, source: str | None = None,
                            from_path: pathlib.Path | None = None) -> bool:
        record = {"run": run_id, "round": int(round_no), "via": via,
                  "created": int(time.time()), "files": self.fingerprint(target)}
        # `source` 是这一版**从哪里读出来的**（state / snapshot / replay），编辑屏用它说明
        # 手里这份候选是怎么来的；`from` 是那一处的路径。旧账本没有这两个字段，读取时有回退。
        if source:
            record["source"] = source
        if from_path is not None:
            record["from"] = str(from_path)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.ledger().locked(self.root) as ledger:
            index = ledger.read()
            # An adoption must never overwrite provenance installed concurrently.
            if name in index:
                return False
            index[name] = record
            ledger.write(index)
            return True


    def reusable(self, name: str, run_id: str, round_no: int) -> bool:
        """Legacy provenance/fingerprint compatibility check.

        Recorded candidates must match run, round and current fingerprint. An unrecorded
        candidate may be adopted under the ledger lock using its current fingerprint;
        this is not proof of the historical source. Current round recovery instead
        publishes a new candidate without replacing an existing directory.
        """
        target = self.root / name
        if not target.is_dir() or not (target / "harness.json").is_file():
            return False
        record = self.sources().get(name)
        if isinstance(record, dict):
            if record.get("run") != run_id or int(record.get("round", -1)) != int(round_no):
                return False
            return record.get("files") == self.fingerprint(target)
        if self.remember(name, run_id, round_no, "adopted", target):
            return True
        # A concurrent publisher installed provenance; evaluate it rather than adopting.
        record = self.sources().get(name)
        return (isinstance(record, dict) and record.get("run") == run_id
                and record.get("round") == int(round_no)
                and record.get("files") == self.fingerprint(target))


    def read_bytes(self, name: str, path: str, limit: int, *, require_manifest=False):
        """Read beneath CANDIDATES on Linux without following any opened symlink.

        The configured root's ancestors, hard links, and concurrent same-UID mutation
        are trusted; this is not a sandbox against a hostile filesystem owner.
        """
        if not valid_workbench_candidate_name(name):
            raise CandidateReadError("invalid-candidate", "invalid candidate")
        if not isinstance(path, str) or not path or "\x00" in path:
            raise CandidateReadError("unsafe-path", "unsafe path")
        rel = pathlib.PurePosixPath(path)
        if rel.is_absolute() or not rel.parts or ".." in rel.parts:
            raise CandidateReadError("unsafe-path", "unsafe path")
        if (sys.platform != "linux" or not hasattr(os, "O_NOFOLLOW")
                or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NONBLOCK")
                or os.open not in os.supports_dir_fd):
            raise CandidateReadError("unsupported", "secure candidate reads unsupported")
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fds = []
        try:
            fds.append(os.open(self.root, directory_flags))
            fds.append(os.open(name, directory_flags, dir_fd=fds[-1]))
            parent = fds[-1]
            if require_manifest:
                manifest = os.open("harness.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                   dir_fd=parent)
                fds.append(manifest)
                if not stat.S_ISREG(os.fstat(manifest).st_mode):
                    raise CandidateReadError("invalid-candidate", "invalid candidate")
            for component in rel.parts[:-1]:
                parent = os.open(component, directory_flags, dir_fd=parent)
                fds.append(parent)
            fd = os.open(rel.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent)
            fds.append(fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CandidateReadError("not-regular-file", "not a regular file")
            with os.fdopen(os.dup(fd), "rb") as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise CandidateReadError("file-too-large", "file exceeds size limit")
            return rel, data
        except OSError as exc:
            raise CandidateReadError("file-not-found", "file unavailable or unsafe") from exc
        finally:
            for fd in reversed(fds):
                os.close(fd)


    def selection(self, name: str, path: str, first: int, last: int) -> str:
        """Read a bounded selection beneath the named candidate without following symlinks."""
        first, last = max(1, int(first)), int(last)
        if first > last or last - first + 1 > self.limits.selection_lines:
            raise ValueError("invalid selection range")
        rel, data = self.read_bytes(name, path, self.limits.selection_bytes, require_manifest=True)
        lines = data.decode("utf-8", errors="replace").splitlines()
        last = min(len(lines), last)
        if first > last:
            raise ValueError("选中的范围为空")
        body = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(first, last + 1))
        return f"{rel}:{first}-{last}\n{body}"


    def archive(self, name: str, *, skip_dirs) -> tuple[int, bytes | None]:
        """Build a bounded submission archive without following candidate links.

        Hard-linked files are rejected: their contents may originate outside the candidate.
        Concurrent writes to an already open regular file cannot be made into a snapshot.
        """
        import io
        import tarfile
        from console import submissions

        class InvalidArchive(Exception):
            pass

        class ArchiveLimit(Exception):
            pass

        class CappedBuffer(io.BytesIO):
            def write(self, data):
                if self.tell() + len(data) > submissions.MAX_PAYLOAD:
                    raise ArchiveLimit()
                return super().write(data)

        if not valid_workbench_candidate_name(name):
            return 404, None
        if (sys.platform != "linux" or not hasattr(os, "O_NOFOLLOW")
                or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NONBLOCK")
                or os.open not in os.supports_dir_fd):
            return 400, None
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        stack = []
        root_fd = candidate_fd = None
        try:
            try:
                root_fd = os.open(self.root, directory_flags)
                candidate_fd = os.open(name, directory_flags, dir_fd=root_fd)
            except FileNotFoundError:
                return 404, None
            try:
                manifest = os.open("harness.json", file_flags, dir_fd=candidate_fd)
            except FileNotFoundError:
                return 404, None
            try:
                info = os.fstat(manifest)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise InvalidArchive()
            finally:
                os.close(manifest)
            buffer = CappedBuffer()
            entries = expanded = metadata = path_budget = 0
            aliases = {}
            manifest_written = False
            # Each frame holds an open directory and a lazy iterator, never a tree listing.
            stack.append((candidate_fd, os.scandir(candidate_fd), ""))
            candidate_fd = None
            with tarfile.open(fileobj=buffer, mode="w|gz", format=tarfile.PAX_FORMAT) as tar:
                while stack:
                    parent, iterator, prefix = stack[-1]
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        iterator.close()
                        os.close(parent)
                        stack.pop()
                        continue
                    entries += 1
                    if entries > submissions.MAX_ENTRIES:
                        raise ArchiveLimit()
                    if entry.name in skip_dirs or entry.name.endswith(".pyc"):
                        continue
                    path = prefix + entry.name
                    path_budget += len(path.encode("utf-8"))
                    if path_budget > submissions._METADATA:
                        raise ArchiveLimit()
                    try:
                        if submissions._member_path(path) != path:
                            raise InvalidArchive()
                    except submissions.SubmissionRejected as exc:
                        raise InvalidArchive() from exc
                    alias = path.casefold()
                    if alias in aliases and aliases[alias] != path:
                        raise InvalidArchive()
                    aliases[alias] = path
                    before = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(before.st_mode):
                        fd = os.open(entry.name, directory_flags, dir_fd=parent)
                        try:
                            after = os.fstat(fd)
                            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                                raise InvalidArchive()
                            iterator = os.scandir(fd)
                        except BaseException:
                            os.close(fd)
                            raise
                        stack.append((fd, iterator, path + "/"))
                    elif stat.S_ISREG(before.st_mode):
                        fd = os.open(entry.name, file_flags, dir_fd=parent)
                        try:
                            info = os.fstat(fd)
                            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                    or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
                                raise InvalidArchive()
                            expanded += info.st_size
                            if expanded > submissions.MAX_EXPANDED:
                                raise ArchiveLimit()
                            member = tarfile.TarInfo(path)
                            member.size = info.st_size
                            member.mode = info.st_mode & 0o777
                            # Admission bounds PAX extension payloads and their cumulative size.
                            header = member.tobuf(format=tarfile.PAX_FORMAT)
                            if len(header) > 512:
                                metadata += len(header) - 1024
                                if len(header) - 1024 > submissions._CHUNK or metadata > submissions._METADATA:
                                    raise ArchiveLimit()
                            with os.fdopen(fd, "rb") as source:
                                fd = None
                                tar.addfile(member, source)
                                if path == "harness.json":
                                    if manifest_written:
                                        raise InvalidArchive()
                                    manifest_written = True
                        finally:
                            if fd is not None:
                                os.close(fd)
                    else:
                        raise InvalidArchive()
                if not manifest_written:
                    raise InvalidArchive()
            return 200, buffer.getvalue()
        except ArchiveLimit:
            return 413, None
        except (OSError, InvalidArchive, ValueError, UnicodeError, tarfile.TarError):
            return 400, None
        finally:
            for fd, iterator, _ in reversed(stack):
                iterator.close()
                os.close(fd)
            if candidate_fd is not None:
                os.close(candidate_fd)
            if root_fd is not None:
                os.close(root_fd)


    def file(self, name: str, rel: str) -> dict:
        """Return a bounded UTF-8 candidate file; unsafe reads fail without path details."""
        try:
            path, data = self.read_bytes(name, rel, self.limits.file_bytes)
        except CandidateReadError as exc:
            return {"error": exc.code, "detail": str(exc)}
        # pathlib.read_text previously used universal newline translation.
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        return {"candidate": name, "path": str(path), "text": text,
                "lines": len(text.splitlines()), "size": len(data)}


    def list(self) -> dict:
        """候选清单。`source` / `from` 来自来源账本，编辑屏据此说明这一版是怎么来的。"""
        rows = []
        index = self.sources()
        if self.root.is_dir():
            for path in sorted(self.root.iterdir()):
                if path.name.startswith("."):
                    continue
                manifest = read_json(path / "harness.json")
                if not isinstance(manifest, dict) or not manifest.get("name"):
                    continue
                files = sorted(p.name for p in path.iterdir() if p.is_file())
                record = index.get(path.name) if isinstance(index.get(path.name), dict) else {}
                rows.append({"dir": path.name, "name": manifest.get("name"),
                             "files": files,
                             "mtime": int(path.stat().st_mtime),
                             # 没有账本记录的老副本：source 为 None，界面不替它猜来源。
                             "source": _candidate_record_source(record) if record else None,
                             "from": record.get("from") if record else None})
        return {"root": str(self.root), "rows": rows}


    def save(self, name: str, files: dict, from_base: bool = True,
                               source: str = "") -> tuple[int, dict]:
        """Save overlays as a new candidate, preserving unedited files from the seed.

        A named source is the seed; only an empty source permits the optional base
        harness fallback. Descriptor custody, byte bounds and publication are owned by
        CandidateSave.
        """
        if not valid_workbench_candidate_name(name):
            return 400, {"error": "bad-name", "detail": "候选名只允许字母数字与 . _ -（≤48）"}
        if not isinstance(files, dict) or not files:
            return 400, {"error": "no-files"}
        if source and not valid_workbench_candidate_name(source):
            return 400, {"error": "bad-source"}
        if (sys.platform != "linux" or not all(hasattr(os, x) for x in
                ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")) or
                os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd):
            return 503, {"error": "unsupported"}

        operation = CandidateSave(self)
        try:
            overlays = operation.prepare(files)
        except (ValueError, UnicodeError) as exc:
            return 400, {"error": str(exc) if isinstance(exc, ValueError) else "bad-file"}
        return operation.run(name, source, from_base, overlays)


def materialize_round(run_id: str, round_no: int, replay: bool = False, *,
                      root, lookup, module_loader, fingerprint, console_root) -> tuple[int, dict]:
    """Recover stored state or snapshot privately; external patch replay is disabled.

    Candidate root and ancestors must be trusted and all ledger writers cooperative.
    Snapshot digest establishes content identity, not authenticity of the run.
    """
    if replay:
        return 409, {"error": "replay-disabled", "detail": "Patch replay is disabled until a sandbox is available."}
    run_path = lookup(run_id)
    if not run_path:
        return 404, {"error": "run-not-found", "detail": run_id}
    try:
        round_no = int(round_no)
    except (TypeError, ValueError, OverflowError):
        return 400, {"error": "bad-round"}
    if round_no < 0:
        return 400, {"error": "bad-round", "detail": round_no}
    base = re.sub(r"[^A-Za-z0-9._-]", "-", f"{str(run_id)[:40]}-r{round_no}")[:40].lstrip(".") or "round"
    recovery, publication = module_loader("round_recovery"), module_loader("round_publish")
    state_name = f"state_after_round{round_no}"
    snapshot_name = f"round-{round_no}.json"
    try:
        root.mkdir(parents=True, exist_ok=True)
        with recovery.open_run_dir(run_path) as run_fd, publication.Publisher(root) as publisher:
            with publisher.stage() as stage:
                stage_path = pathlib.Path(f"/proc/self/fd/{stage.fd}")
                try:
                    recovery.copy_state_into(run_path, state_name, stage.fd, run_fd=run_fd)
                    source, from_path = "state", run_path / state_name
                except FileNotFoundError:
                    try:
                        if str(console_root.parent) not in sys.path:
                            sys.path.insert(0, str(console_root.parent))
                        snapshot_materializer = module_loader("round_snapshot")
                        with recovery.open_snapshot_fd(run_path, snapshot_name, run_fd=run_fd) as snapshot_fd:
                            snapshot_materializer.materialize(snapshot_fd, stage.fd, run_id=str(run_id), round_no=round_no)
                        source, from_path = "snapshot", run_path / "snapshots" / snapshot_name
                    except FileNotFoundError:
                        return 409, {"error": "round-not-stored", "detail": f"No state_after_round{round_no} or snapshots/{snapshot_name}; diffs/round-*.patch replay disabled until sandboxed."}
                    except snapshot_materializer.snapshot.SnapshotInvalid as exc:
                        return 409, {"error": "unsafe-recovery", "detail": f"snapshots/{snapshot_name} invalid: {exc}"}
                manifest_path = stage_path / "harness.json"
                manifest = read_json(manifest_path)
                if (not isinstance(manifest, dict) or
                        any(not isinstance(manifest.get(key), str) or not manifest[key].strip()
                            for key in ("name", "version", "entrypoint"))):
                    return 409, {"error": "no-manifest", "detail": "Missing or invalid harness.json"}
                subpath = manifest.get("path", ".")
                def safe_relative(value):
                    if not isinstance(value, str) or not value or "\\" in value:
                        return False
                    path = pathlib.PurePosixPath(value)
                    return not path.is_absolute() and ".." not in path.parts
                if (not safe_relative(subpath) or not safe_relative(manifest["entrypoint"]) or
                        not (stage_path / subpath).is_dir() or
                        not (stage_path / subpath / manifest["entrypoint"]).is_file()):
                    return 409, {"error": "no-manifest", "detail": "Invalid path or missing entrypoint"}
                def finalize(name):
                    manifest["name"] = name
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                    return {"run": str(run_id), "round": round_no, "via": source,
                            "source": source, "from": str(from_path),
                            "created": int(time.time()), "files": fingerprint(stage_path)}
                name = publisher.publish(stage, base, {}, finalize=finalize)
                return 201, {"candidate": name, "from": str(from_path), "round": round_no,
                             "source": source, "reconstructed": False, "reused": False,
                             "applied": [], "files": sorted(p.name for p in stage_path.iterdir() if p.is_file())}
    except (OSError, ValueError, publication.PublishError, recovery.RecoveryArtifactError) as exc:
        return 409, {"error": "unsafe-recovery", "detail": str(exc)}


class CandidateSave:
    """One save's bounded byte counters and fd-owned publication/cleanup."""
    def __init__(self, store):
        self.store = store
        self.max_depth, self.max_entries = 16, 4096
        self.max_file = self.max_total = store.limits.source_bytes
        self.max_overlay = store.limits.overlay_bytes
        self.total = 0
        self.directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        self.read_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

    def prepare(self, files):
        overlays = []
        for rel, body in files.items():
            if not isinstance(rel, str) or not isinstance(body, str):
                raise ValueError("bad-file")
            parts = rel.split("/")
            if (not rel or any(p in ("", ".", "..") or "\x00" in p for p in parts)
                    or len(parts) > self.max_depth or any(len(os.fsencode(p)) > 255 for p in parts)):
                raise ValueError("unsafe-path")
            data = body.encode("utf-8")
            self.total += len(data)
            if len(data) > self.max_overlay or self.total > self.max_total or len(overlays) >= self.max_entries:
                raise ValueError("resource-limit")
            overlays.append((parts, data))
        return overlays

    def copy_dir(self, src, dst, depth):
        with os.scandir(src) as entries:
            for entry in entries:
                item = entry.name
                self.count += 1
                if self.count > self.max_entries or depth > self.max_depth:
                    raise ValueError("resource-limit")
                if item in ("__pycache__", ".state", ".git") or item.endswith(".pyc"):
                    continue
                fd = os.open(item, self.read_flags, dir_fd=src)
                try:
                    st = os.fstat(fd)
                    if stat.S_ISDIR(st.st_mode):
                        if depth >= self.max_depth:
                            raise ValueError("resource-limit")
                        os.mkdir(item, 0o700, dir_fd=dst)
                        sub = os.open(item, self.directory_flags, dir_fd=dst)
                        try:
                            os.fchmod(sub, 0o700)
                            self.copy_dir(fd, sub, depth + 1)
                        finally:
                            os.close(sub)
                    elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                        is_manifest = depth == 1 and item == "harness.json"
                        chunks, size = [], 0
                        out = None
                        try:
                            if not is_manifest:
                                raw = os.open(item, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, st.st_mode & 0o777, dir_fd=dst)
                                out = os.fdopen(raw, "wb")
                                os.fchmod(out.fileno(), st.st_mode & 0o777)
                            limit = self.max_overlay if is_manifest else self.max_file
                            while True:
                                chunk = os.read(fd, min(65536, limit + 1 - size))
                                if not chunk:
                                    break
                                size += len(chunk)
                                self.total += len(chunk)
                                if size > limit or self.total > self.max_total:
                                    raise ValueError("resource-limit")
                                if is_manifest:
                                    chunks.append(chunk)
                                else:
                                    out.write(chunk)
                            if is_manifest:
                                self.manifest_data = b"".join(chunks)
                        finally:
                            if out is not None:
                                out.close()
                    else:
                        raise ValueError("unsafe-source")
                finally:
                    os.close(fd)


    def clear_owned(self, fd):
        with os.scandir(fd) as entries:
            for entry in entries:
                st = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    child = os.open(entry.name, self.directory_flags, dir_fd=fd)
                    try:
                        actual = os.fstat(child)
                        if (actual.st_dev, actual.st_ino) != (st.st_dev, st.st_ino):
                            raise OSError("directory changed during cleanup")
                        self.clear_owned(child)
                        current_child = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                        if (actual.st_dev, actual.st_ino) != (current_child.st_dev, current_child.st_ino):
                            raise OSError("directory changed during cleanup")
                        os.rmdir(entry.name, dir_fd=fd)
                    finally:
                        os.close(child)
                else:
                    os.unlink(entry.name, dir_fd=fd)


    def apply_overlays(self, dest, overlays):
        for parts, data in overlays:
            if parts == ["harness.json"]:
                self.manifest_data = data
                continue
            parent = os.dup(dest)
            try:
                for component in parts[:-1]:
                    created = False
                    try:
                        os.mkdir(component, 0o700, dir_fd=parent)
                        created = True
                    except FileExistsError:
                        pass
                    child = os.open(component, self.directory_flags, dir_fd=parent)
                    if created:
                        os.fchmod(child, 0o700)
                    os.close(parent)
                    parent = child
                out = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644, dir_fd=parent)
                try:
                    st = os.fstat(out)
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                        raise ValueError("unsafe-target")
                    os.ftruncate(out, 0)
                    with os.fdopen(os.dup(out), "wb") as stream:
                        stream.write(data)
                finally:
                    os.close(out)
            finally:
                os.close(parent)

    def publish_manifest(self, dest, name):
        try:
            manifest = json.loads(self.manifest_data) if self.manifest_data is not None else None
        except (ValueError, UnicodeError, RecursionError):
            manifest = None
        if (not isinstance(manifest, dict) or not isinstance(manifest.get("entrypoint"), str)
                or not manifest["entrypoint"].strip()):
            raise ValueError("manifest-missing")
        manifest["name"] = name
        data = (json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        if len(data) > self.max_overlay or self.total + len(data) > self.max_total:
            raise ValueError("resource-limit")
        # Publish the complete manifest atomically as the last visible step.
        import secrets
        temporary_manifest = ".harness-save-" + secrets.token_hex(16)
        out = os.open(temporary_manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dest)
        with os.fdopen(out, "wb") as stream:
            stream.write(data)
        os.link(temporary_manifest, "harness.json", src_dir_fd=dest, dst_dir_fd=dest, follow_symlinks=False)
        self.published = True
        # Publication succeeded; cleanup failure must not turn success into failure.
        try:
            os.unlink(temporary_manifest, dir_fd=dest)
        except OSError:
            pass

    def run(self, name, source, from_base, overlays):
        fds = []
        target = self.store.root / name
        written = ["/".join(parts) for parts, _ in overlays]
        self.count = len(overlays)
        self.manifest_data = None
        dest = None
        self.published = False
        try:
            self.store.root.mkdir(parents=True, exist_ok=True)
            root = os.open(self.store.root, self.directory_flags)
            fds.append(root)
            seed = None
            if source:
                seed = os.open(source, self.directory_flags, dir_fd=root)
            elif from_base:
                try:
                    seed = os.open(self.store.base_harness, self.directory_flags)
                except FileNotFoundError:
                    pass
            if seed is not None:
                fds.append(seed)
                # A named source must have a regular, non-linked manifest.
                if source:
                    mfd = os.open("harness.json", self.read_flags, dir_fd=seed)
                    try:
                        st = os.fstat(mfd)
                        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                            raise ValueError("bad-source")
                    finally:
                        os.close(mfd)
            try:
                os.mkdir(name, 0o700, dir_fd=root)
            except FileExistsError:
                return 409, {"error": "duplicate-candidate", "detail": f"{target} 已存在"}
            dest = os.open(name, self.directory_flags, dir_fd=root)
            fds.append(dest)
            os.fchmod(dest, 0o700)

            if seed is not None:
                self.copy_dir(seed, dest, 1)
            self.apply_overlays(dest, overlays)
            self.publish_manifest(dest, name)
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            return 400, {"error": str(exc) if isinstance(exc, ValueError) else "unsafe-or-unavailable"}
        finally:
            if dest is not None and not self.published:
                # Only remove the directory we opened after our successful mkdir.
                # As above, same-UID mutation/rename of the trusted root is excluded.
                # Any failed identity check or cleanup leaves the incomplete tree.
                try:
                    owned = os.fstat(dest)
                    current = os.stat(name, dir_fd=root, follow_symlinks=False)
                    if (owned.st_dev, owned.st_ino) == (current.st_dev, current.st_ino):
                        self.clear_owned(dest)
                        current = os.stat(name, dir_fd=root, follow_symlinks=False)
                        if (owned.st_dev, owned.st_ino) == (current.st_dev, current.st_ino):
                            os.rmdir(name, dir_fd=root)
                except OSError:
                    pass
            for fd in reversed(fds):
                os.close(fd)
        return 201, {"candidate": name, "path": str(target), "written": written,
                     "trial": (f"python3 driver.py --harness-path {shlex.quote(str(target))} --dataset "
                               "authored_example_fix_bug --sampling all --rounds 0 "
                               f"--run-id manual-{name}-01")}
