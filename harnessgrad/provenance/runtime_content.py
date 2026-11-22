"""Opt-in content keys for locally admitted runtime manifests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _base(manifest):
    base = manifest.get("base_python", manifest.get("base"))
    if (not isinstance(base, dict) or not re.fullmatch(r"[0-9a-f]{64}", base.get("sha256", ""))
            or not isinstance(base.get("version"), str) or not base["version"]):
        raise ValueError("runtime content lacks a base interpreter digest and version")
    return base


def artifact(reference, *, artifact_canonical=canonical):
    """Use exact admitted manifest bytes and normalize only known installation fields."""
    path = Path(reference["manifest_path"])
    manifest = json.loads(path.read_bytes())
    if hashlib.sha256(artifact_canonical(manifest)).hexdigest() != reference["runtime_id"]:
        raise ValueError("runtime manifest changed during content capture")
    if manifest["path"] != reference["path"]:
        raise ValueError("runtime manifest does not describe the admitted installation")

    base = _base(manifest)
    venv = Path(reference["path"])
    files = {}
    for name, item in manifest["files"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
            raise ValueError("invalid runtime inventory path")
        item = dict(item)
        if item.get("kind") == "link":
            target = Path(item["target"])
            if target.is_absolute():
                if target.is_relative_to(venv):
                    item["target"] = {"runtime_relative": target.relative_to(venv).as_posix()}
                elif str(target) == base.get("executable") and name.startswith("bin/python"):
                    item["target"] = {"base_executable_sha256": base["sha256"]}
                # Unknown targets remain exact.
        if name == "pyvenv.cfg" and item.get("kind") == "file":
            raw = (venv / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("pyvenv.cfg changed during content capture")
            lines = []
            for line in raw.decode("utf-8").splitlines():
                key, sep, val = line.partition("=")
                val = val.strip()
                if sep and key.strip() == "home" and val == str(Path(base["executable"]).parent):
                    line = "home = ${base_executable_dir}"
                elif sep and key.strip() == "executable" and val == base["executable"]:
                    line = "executable = ${base_executable}"
                elif sep and key.strip() == "uv" and re.fullmatch(r"[0-9.]+", val):
                    continue
                lines.append(line)
            item["sha256"] = digest(lines)
        if relative.parts[0] == "bin" and item.get("kind") == "file":
            with (venv / name).open("rb") as stream:
                first = stream.readline(4096)
                shebang = f"#!{venv}/bin/python".encode()
                if first.startswith(shebang) and first[len(shebang):len(shebang)+1] in (b" ", b"\n"):
                    body = stream.read()
                    if hashlib.sha256(first + body).hexdigest() != item["sha256"]:
                        raise ValueError("runtime console script changed during content capture")
                    item["sha256"] = hashlib.sha256(
                        b"#!${runtime}/bin/python" + first[len(shebang):] + body).hexdigest()
        files[name] = item

    kept = {k: v for k, v in manifest.items()
            if k not in {"path", "runtime_paths", "key", "dependency_key", "base_python", "base", "recipe", "files"}}
    recipe = {k: v for k, v in (manifest.get("recipe") or {}).items()
              if k not in {"uv", "uv_sha256", "binary_sha256", "downloads_allowed"}}
    base = {k: v for k, v in base.items()
            if k not in {"executable", "base_prefix", "base_exec_prefix", "stdlib"}}
    return {**kept, "base_python": base, "recipe": recipe, "files_sha256": digest(files)}
