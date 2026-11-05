"""Explicit Python dependency environments for dataset providers.

Discovery and inspection use only the standard library.  Preparation is the only
operation allowed to install anything.  A venv is built at its final physical
path; publication atomically exposes a verified inventory, never moves the venv.
The inventory covers its files and the base interpreter executable.  Host system
libraries and the base standard library are compatibility inputs, not a sealed OS.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

from harnessgrad.paths import configured_path, main_checkout

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "harnessgrad.dataset-runtime.v1"
ARTIFACT_SCHEMA = "harnessgrad.dataset-runtime-artifact.v1"
SCOPE = "dataset-provider-runtime"
RECIPE_VERSION = 1
_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)\Z")


class DatasetRuntimeUnavailable(RuntimeError):
    """A selected provider runtime is missing, incompatible or changed."""

    def __init__(self, message, *, status="unavailable"):
        super().__init__(message)
        self.status = status


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _package(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def descriptor(name: str) -> dict | None:
    """Read a static opt-in declaration without importing any dataset adapter."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("invalid dataset runtime name")
    folder = ROOT / "data" / "datasets" / name
    declaration = folder / "runtime.json"
    if not declaration.exists():
        return None
    if declaration.is_symlink() or not declaration.is_file():
        raise ValueError("dataset runtime declaration must be a regular file")
    raw = declaration.read_bytes()
    value = json.loads(raw)
    if (not isinstance(value, dict)
            or set(value) != {"schema", "dataset", "provider", "python", "requirements"}
            or value["schema"] != SCHEMA or value["dataset"] != name
            or value["provider"] != "qa"
            or not isinstance(value["python"], str)
            or not re.fullmatch(r"3\.[0-9]+", value["python"])):
        raise ValueError("invalid dataset runtime declaration")
    relative = value["requirements"]
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
            or str(PurePosixPath(relative)) != relative):
        raise ValueError("requirements must be an owned normalized relative file")
    requirements = folder / relative
    if (requirements.is_symlink() or not requirements.is_file()
            or not requirements.resolve().is_relative_to(folder.resolve())):
        raise ValueError("requirements must be an owned regular file")
    pins = {}
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pin = _PIN.fullmatch(line)
        if not pin or _package(pin[1]) in pins:
            raise ValueError("provider requirements must contain unique exact package pins")
        pins[_package(pin[1])] = pin[2]
    return {**value, "descriptor_sha256": hashlib.sha256(raw).hexdigest(),
            "requirements_path": str(requirements.resolve()),
            "requirements_sha256": _digest(requirements), "pins": pins,
            "source_root": str(ROOT.resolve())}


def _store(root=None):
    default = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "harnessgrad/dataset-runtimes"
    path = Path(root).expanduser() if root is not None else Path(configured_path("HG_DATASET_RUNTIME_ROOT", default))
    if not path.is_absolute():
        raise ValueError("dataset runtime store must be an absolute path")
    path = path.resolve()
    for source in {ROOT.resolve(), main_checkout(ROOT).resolve()}:
        if path == source or path.is_relative_to(source):
            raise ValueError("dataset runtime store must be outside platform source trees")
    return path


def _key(spec):
    return hashlib.sha256(_canonical({"descriptor": spec["descriptor_sha256"],
        "requirements": spec["requirements_sha256"], "platform": sys.platform,
        "machine": platform.machine(), "recipe": RECIPE_VERSION})).hexdigest()


def _unavailable(name, reason, *, status="unavailable"):
    return DatasetRuntimeUnavailable(
        f"{name}: {reason}. Prepare explicitly with `python tools/prepare_dataset.py --dataset {name}`",
        status=status)


def _run(argv, *, timeout_s=None):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.update(PYTHONDONTWRITEBYTECODE="1", UV_LINK_MODE="copy")
    proc = subprocess.Popen([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors="replace", env=environment, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise
    if proc.returncode:
        raise ValueError(f"runtime command failed ({proc.returncode}): {stderr[-2000:] or stdout[-2000:]}")
    return stdout


def _probe(python, *, timeout_s=None):
    code = """import importlib.metadata,json,sys,sysconfig
print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,
'version':sys.version,'minor':str(sys.version_info.major)+'.'+str(sys.version_info.minor),
'stdlib':sysconfig.get_path('stdlib'),'paths':sys.path,
'packages':{d.metadata['Name']:d.version for d in importlib.metadata.distributions()}}))"""
    return json.loads(_run([python, "-B", "-I", "-c", code], timeout_s=timeout_s))


def _base(probe):
    executable = Path(probe["executable"]).resolve(strict=True)
    return {"executable": str(executable), "sha256": _digest(executable),
            "version": probe["version"], "base_prefix": probe["base_prefix"],
            "stdlib": probe["stdlib"]}


def _inventory(venv):
    result = {}
    interpreter = (venv / "bin/python").resolve(strict=True)
    for path in sorted(venv.rglob("*")):
        relative = str(path.relative_to(venv))
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(venv) and not (
                    relative.startswith("bin/python") and resolved == interpreter):
                raise ValueError(f"runtime symlink escapes the venv: {relative}")
            result[relative] = {"kind": "link", "target": os.readlink(path)}
        elif path.is_file():
            result[relative] = {"kind": "file", "sha256": _digest(path),
                                "mode": stat.S_IMODE(path.stat().st_mode)}
        elif not path.is_dir():
            raise ValueError(f"runtime contains a non-regular entry: {relative}")
    return result


def _freeze(venv):
    for path in sorted(venv.rglob("*"), reverse=True):
        if not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    venv.chmod(stat.S_IMODE(venv.stat().st_mode) & ~0o222)


def _remove_instance(instance):
    for parent, dirs, _ in os.walk(instance):
        Path(parent).chmod(0o700)
        for name in dirs:
            path = Path(parent) / name
            if not path.is_symlink():
                path.chmod(0o700)
    shutil.rmtree(instance)


@contextmanager
def _locked(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".prepare.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _atomic_json(path, value):
    handle = tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".publish-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o444)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _admit(name, spec, folder):
    ready = json.loads((folder / "ready.json").read_bytes())
    if set(ready) != {"instance", "runtime_id"} or not re.fullmatch(r"[A-Za-z0-9_-]+", ready["instance"]):
        raise ValueError("invalid runtime publication")
    instance = folder / "instances" / ready["instance"]
    if instance.is_symlink():
        raise ValueError("runtime instance must not be a symlink")
    manifest_path = instance / "artifact.json"
    manifest = json.loads(manifest_path.read_bytes())
    if hashlib.sha256(_canonical(manifest)).hexdigest() != ready["runtime_id"]:
        raise ValueError("runtime manifest identity changed")
    if (manifest.get("schema") != ARTIFACT_SCHEMA or manifest.get("key") != _key(spec)
            or manifest.get("dataset") != name or manifest.get("descriptor_sha256") != spec["descriptor_sha256"]
            or manifest.get("requirements_sha256") != spec["requirements_sha256"]):
        raise ValueError("runtime declaration or target changed")
    venv = instance / "venv"
    if venv.is_symlink() or str(venv) != manifest["path"]:
        raise ValueError("runtime physical path changed")
    if _inventory(venv) != manifest["files"]:
        raise ValueError("runtime file inventory changed")
    probe = _probe(venv / "bin/python")
    if _base(probe) != manifest["base_python"]:
        raise ValueError("runtime base interpreter changed")
    _validate_probe(probe, spec, venv)
    return {"python": str(venv / "bin/python"), "path": str(venv),
            "runtime_id": ready["runtime_id"], "manifest_path": str(manifest_path),
            "descriptor_sha256": spec["descriptor_sha256"],
            "requirements_sha256": spec["requirements_sha256"], "scope": SCOPE}


def _validate_probe(probe, spec, venv):
    if probe["minor"] != spec["python"] or Path(probe["prefix"]).resolve() != venv:
        raise ValueError("runtime interpreter/version does not match its declaration")
    packages = {_package(k): v for k, v in probe["packages"].items()}
    if packages != spec["pins"]:
        raise ValueError("runtime packages do not exactly match its locked requirements")
    stdlib = Path(probe["stdlib"]).resolve()
    stdlib_zip = stdlib.parent / ("python" + spec["python"].replace(".", "") + ".zip")
    for entry in probe["paths"]:
        path = Path(entry).resolve()
        if path.is_relative_to(venv):
            continue
        if (("site-packages" in path.parts or "dist-packages" in path.parts)
                or not (path.is_relative_to(stdlib) or path == stdlib_zip)):
            raise ValueError("runtime exposes a base or foreign package directory")


def resolve(name: str, *, root=None) -> dict:
    """Verify one published runtime. Never build or silently select a fallback."""
    try:
        spec = descriptor(name)
        folder = _store(root) / _key(spec) if spec is not None else None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise _unavailable(name, str(exc), status="invalid") from exc
    if spec is None:
        raise _unavailable(name, "no dataset runtime declaration")
    published = (folder / "ready.json").exists()
    try:
        return _admit(name, spec, folder)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise _unavailable(name, str(exc), status="invalid" if published else "unavailable") from exc


def check(name: str, *, root=None) -> dict:
    """A fresh read-only fact about the provider dependency runtime, not its data."""
    try:
        runtime = resolve(name, root=root)
    except DatasetRuntimeUnavailable as exc:
        return {"present": False, "status": exc.status, "scope": SCOPE, "detail": str(exc)}
    return {"present": True, "status": "ready", "scope": SCOPE,
            "detail": "verified provider runtime", "runtime": runtime}


def prepare(name: str, *, python=None, root=None, timeout_s=None) -> dict:
    """Explicitly install and verify a runtime, or verify an existing publication."""
    spec = descriptor(name)
    if spec is None:
        raise _unavailable(name, "no dataset runtime declaration")
    folder = _store(root) / _key(spec)
    with _locked(folder):
        if (folder / "ready.json").exists():
            runtime = resolve(name, root=root)
            if python is not None:
                requested = _probe(python, timeout_s=timeout_s)
                actual = _probe(runtime["python"], timeout_s=timeout_s)
                if _base(requested) != _base(actual):
                    raise _unavailable(name, "prepared runtime uses a different base interpreter")
            return {**runtime, "reused": True}
        uv = shutil.which("uv")
        if uv is None:
            raise _unavailable(name, "uv is required to prepare the runtime")
        instances = folder / "instances"
        instances.mkdir(exist_ok=True)
        instance = Path(tempfile.mkdtemp(prefix="build-", dir=instances))
        venv = instance / "venv"
        try:
            _run([uv, "venv", "--no-project", "--python", python or spec["python"], venv], timeout_s=timeout_s)
            _run([uv, "pip", "install", "--python", venv / "bin/python", "-r", spec["requirements_path"]], timeout_s=timeout_s)
            probe = _probe(venv / "bin/python", timeout_s=timeout_s)
            _validate_probe(probe, spec, venv)
            _freeze(venv)
            manifest = {"schema": ARTIFACT_SCHEMA, "dataset": name, "key": _key(spec),
                "descriptor_sha256": spec["descriptor_sha256"],
                "requirements_sha256": spec["requirements_sha256"], "path": str(venv),
                "target": {"platform": sys.platform, "machine": platform.machine()},
                "recipe": {"version": RECIPE_VERSION, "uv": _run([uv, "--version"]).strip()},
                "base_python": _base(probe), "files": _inventory(venv),
                "integrity_scope": "venv-files-and-base-interpreter; host-system-libraries-not-sealed"}
            _atomic_json(instance / "artifact.json", manifest)
            runtime_id = hashlib.sha256(_canonical(manifest)).hexdigest()
            _atomic_json(folder / "ready.json", {"instance": instance.name, "runtime_id": runtime_id})
        except BaseException:
            _remove_instance(instance)
            raise
    return {**resolve(name, root=root), "reused": False}
