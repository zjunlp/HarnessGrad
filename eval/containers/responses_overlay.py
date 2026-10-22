"""Explicit, offline preparation and read-only admission of a Responses transport.

This is platform runtime infrastructure, not a candidate source modification.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
MODULES = ("responses_transport.mjs", "responses_capture.mjs", "responses_preload.mjs")
LIMITS = {"maxRequestBytes": 4*1024*1024, "maxRunBytes": 16*1024*1024,
          "maxEventBytes": 1024*1024, "maxEvents": 20000}
ARTIFACTS = (".responses/http.jsonl", ".responses/status.json", "opencode.events.jsonl",
             "opencode.stderr.log", "opencode.session.json")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def _spec(bun, route, mode, allow_loopback):
    parsed = urlsplit(route)
    local = (allow_loopback and parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
             and parsed.port is not None and parsed.path == "/v1/responses"
             and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
    if route != "https://coding.tu-zi.com/v1/responses" and not local:
        raise ValueError("Responses overlay route must be the exact approved gateway or explicit offline loopback")
    if mode not in ("observe", "repair"):
        raise ValueError("Responses overlay mode must be observe or repair")
    bun = Path(bun).resolve(strict=True)
    # /bin/sh resets inherited PWD to cwd. OpenCode deliberately launches from
    # its package directory with PWD naming the task. Recover that trusted task
    # directory from the runner-owned capture path before executing real Bun.
    launcher = ('#!/bin/sh\ncase "$HG_RESPONSES_ARTIFACT_DIR" in\n'
                '  /*/.responses) export PWD="${HG_RESPONSES_ARTIFACT_DIR%/.responses}" ;;\n'
                '  *) echo "Responses launcher requires a task capture directory" >&2; exit 64 ;;\n'
                'esac\nexec ' + shlex.quote(str(bun))
                + ' --preload "$(dirname -- "$0")/responses_preload.mjs" "$@"\n').encode()
    files = {name: (ROOT / "eval/service" / name).read_bytes() for name in MODULES}
    files["launcher"] = launcher
    identity = {"schema": "responses.overlay.v1", "binary_sha256": digest(bun.read_bytes()),
                "mode": mode, "route": route, "offline_loopback": bool(local),
                "repair_events": ["response.output_text.delta"], "limits": LIMITS,
                "files": {k: {"sha256": digest(v), "mode": 0o555 if k == "launcher" else 0o444}
                          for k, v in files.items()}}
    identity["sha256"] = digest(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())
    return identity, files


def configure(bun: Path, root: Path, *, route: str, mode="observe", allow_loopback=False) -> dict:
    """Publish immutable files only; never execute Bun, install packages or read keys."""
    identity, files = _spec(bun, route, mode, allow_loopback)
    root = Path(root).resolve()
    if root.is_relative_to(ROOT) or ROOT.is_relative_to(root):
        raise ValueError("Responses overlay must live outside the platform tree")
    installation = Path(bun).resolve().parent
    if root.is_relative_to(installation) or installation.is_relative_to(root):
        raise ValueError("Responses overlay must live outside the Bun installation")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / identity["sha256"]
    if destination.exists():
        return lookup(destination, bun)
    staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=root))
    try:
        for name, data in files.items():
            target = staging / name
            target.write_bytes(data)
            target.chmod(identity["files"][name]["mode"])
        manifest = staging / "manifest.json"
        manifest.write_text(json.dumps({"identity": identity}, sort_keys=True) + "\n")
        manifest.chmod(0o444)
        staging.chmod(0o555)
        try:
            staging.rename(destination)
        except FileExistsError:
            pass
    finally:
        if staging.exists():
            staging.chmod(0o755)
            shutil.rmtree(staging)
    return lookup(destination, bun)


def lookup(directory: Path, bun: Path) -> dict:
    """Verify against current platform code and binary; no repair or setup on lookup."""
    try:
        directory = Path(directory)
        if directory.is_symlink() or directory.resolve() != directory.absolute():
            raise ValueError("overlay directory alias")
        if stat.S_IMODE(directory.stat().st_mode) != 0o555:
            raise ValueError("overlay directory permissions")
        manifest = directory / "manifest.json"
        if manifest.is_symlink() or manifest.stat().st_nlink != 1 or stat.S_IMODE(manifest.stat().st_mode) != 0o444:
            raise ValueError("overlay manifest ownership")
        if manifest.stat().st_size > 32768:
            raise ValueError("overlay manifest size")
        actual = json.loads(manifest.read_text())["identity"]
        expected, _ = _spec(bun, actual["route"], actual["mode"], actual["offline_loopback"])
        if expected != actual or directory.name != expected["sha256"]:
            raise ValueError("overlay identity mismatch")
        if {p.name for p in directory.iterdir()} != set(MODULES) | {"launcher", "manifest.json"}:
            raise ValueError("overlay unexpected files")
        for name, info in expected["files"].items():
            p = directory / name
            if (p.is_symlink() or not p.is_file() or p.stat().st_nlink != 1
                    or stat.S_IMODE(p.stat().st_mode) != info["mode"] or digest(p.read_bytes()) != info["sha256"]):
                raise ValueError("overlay file identity mismatch")
        return {"path": str(directory), "launcher": str(directory / "launcher"), "identity": expected}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Responses overlay unavailable or changed; explicitly prepare a fresh overlay") from exc


def selected(bun: Path, repo: Path) -> dict | None:
    value = os.environ.get("HG_RESPONSES_OVERLAY")
    if not value:
        return None
    path = Path(value).absolute()
    if path.is_relative_to(Path(repo).resolve()) or path.is_relative_to(ROOT):
        raise ValueError("Responses overlay must live outside candidate and platform")
    return lookup(path, bun)


def require_comparable(left: list[dict], right: list[dict]):
    """Require every trial's overlay identity, including explicit disabled identity."""
    values = []
    for record in [*left, *right]:
        bun = record.get("bun")
        if not isinstance(bun, dict) or "responses_overlay" not in bun:
            raise ValueError("Missing Responses overlay comparison identity")
        values.append(bun["responses_overlay"])
    if not left or not right or any(value != values[0] for value in values):
        raise ValueError("Mixed Responses overlay comparison identities")


def collect_capture(workdir: Path) -> dict:
    """Bound each untrusted file independently; errors must not lose sibling evidence."""
    from eval.run.artifacts import collect
    artifacts = {}
    for name in ARTIFACTS:
        limit = 16*1024*1024 if name.endswith("http.jsonl") else 2*1024*1024
        try:
            artifacts.update(collect(workdir, [name], max_bytes=limit))
        except (OSError, ValueError) as exc:
            artifacts[name] = {"status": "invalid", "reason": type(exc).__name__}
    complete = False
    try:
        raw = artifacts[".responses/http.jsonl"]
        status = json.loads(base64.b64decode(artifacts[".responses/status.json"]["base64"]))
        events = [json.loads(line) for line in base64.b64decode(raw["base64"]).splitlines()]
        if not isinstance(status, dict) or any(not isinstance(event, dict) for event in events):
            raise ValueError("Malformed capture records")
        requests = {e["request_id"] for e in events if e.get("kind") == "request"}
        ends = {e["request_id"] for e in events if e.get("kind") == "end" and e.get("complete")}
        complete = bool(requests) and requests == ends and status["truncated"] is False
    except (KeyError, ValueError, TypeError):
        pass
    return {"schema": "responses.capture.v1", "complete": complete, "artifacts": artifacts}
