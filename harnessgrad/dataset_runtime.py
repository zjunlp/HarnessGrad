"""Explicit Python dependency artifacts for dataset loading and execution.

Discovery and inspection use only the standard library.  Preparation is the only
operation allowed to install anything.  A venv is built at its final physical
path; publication atomically exposes a verified inventory, never moves the venv.
The inventory covers its files and the base interpreter executable.  Host system
libraries and the base standard library are compatibility inputs, not a sealed OS.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile

from harnessgrad.paths import configured_path, main_checkout
from harnessgrad.python_artifacts import (
    atomic_json as _atomic_json, canonical as _canonical, digest as _digest,
    freeze as _freeze, inventory as _inventory, locked as _locked,
    remove_instance as _remove_instance,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "harnessgrad.dataset-runtime.v1"
EXECUTION_SCHEMA = "harnessgrad.dataset-execution-runtime.v1"
ARTIFACT_SCHEMA = "harnessgrad.dataset-runtime-artifact.v1"
BINDING_SCHEMA = "harnessgrad.python-runtime-binding.v1"
SCOPE = "dataset-provider-runtime"
EXECUTION_SCOPE = "dataset-execution-runtime"
RECIPE_VERSION = 1
_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
_EXECUTION_SNAPSHOT = ContextVar("dataset_execution_snapshot", default=None)
_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)\Z")


class DatasetRuntimeUnavailable(RuntimeError):
    """A selected provider runtime is missing, incompatible or changed."""

    def __init__(self, message, *, status="unavailable"):
        super().__init__(message)
        self.status = status


def _package(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _descriptor(name, filename, schema, fields):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("invalid dataset runtime name")
    folder = ROOT / "data" / "datasets" / name
    declaration = folder / filename
    if not declaration.exists():
        return None
    if declaration.is_symlink() or not declaration.is_file():
        raise ValueError("dataset runtime declaration must be a regular file")
    raw = declaration.read_bytes()
    value = json.loads(raw)
    if (not isinstance(value, dict)
            or set(value) != fields
            or value["schema"] != schema or value["dataset"] != name
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


def descriptor(name: str) -> dict | None:
    """Read a provider opt-in without importing its adapter (the v1 API)."""
    value = _descriptor(name, "runtime.json", SCHEMA,
                        {"schema", "dataset", "provider", "python", "requirements"})
    if value is not None and value["provider"] not in {"qa", "adapter"}:
        raise ValueError("invalid dataset runtime declaration")
    return value


def execution_descriptor(name: str) -> dict | None:
    """Read the separate shared service/verifier dependency declaration."""
    value = _descriptor(name, "execution-runtime.json", EXECUTION_SCHEMA,
                        {"schema", "dataset", "python", "requirements"})
    return None if value is None else {**value, "profile": "execution"}


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


def _unavailable(name, reason, *, status="unavailable", execution=False):
    profile = " --runtime execution" if execution else ""
    return DatasetRuntimeUnavailable(
        f"{name}: {reason}. Prepare explicitly with `python tools/prepare_dataset.py --dataset {name}{profile}`",
        status=status)


# Only explicit preparation steps may receive network/index configuration.
_BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP",
             "XDG_CACHE_HOME", "XDG_DATA_HOME", "UV_CACHE_DIR", "UV_OFFLINE",
             "UV_PYTHON_INSTALL_DIR", "UV_PYTHON", "UV_PYTHON_PREFERENCE",
             "UV_NO_MANAGED_PYTHON", "PIP_NO_INDEX")
_DOWNLOAD_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy",
                 "https_proxy", "all_proxy", "no_proxy", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL",
                 "UV_DEFAULT_INDEX", "UV_INDEX", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
                 "PIP_FIND_LINKS", "PIP_TRUSTED_HOST", "UV_INSECURE_HOST", "UV_INDEX_STRATEGY",
                 "UV_NATIVE_TLS", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
                 "CURL_CA_BUNDLE", "PIP_CERT", "PIP_CLIENT_CERT")


def _child_environment(*, allow_downloads=False):
    keys = list(_BASE_ENV)
    if allow_downloads:
        keys.extend(_DOWNLOAD_ENV)
        keys.extend(name for name in os.environ
                    if re.fullmatch(r"UV_INDEX_[A-Z0-9_]+_(?:USERNAME|PASSWORD|TOKEN)", name))
    environment = {name: os.environ[name] for name in keys if name in os.environ}
    # Do not consult user configuration files for undeclared credentials or indexes.
    environment.update(PYTHONDONTWRITEBYTECODE="1", UV_LINK_MODE="copy",
                       UV_NO_CONFIG="1", PIP_CONFIG_FILE=os.devnull)
    return environment


def _redact_diagnostic(detail, environment):
    sensitive = {value for name, value in os.environ.items() if value and (
        re.search(r"(?:^|_)(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)(?:_|$)", name, re.I)
        or name in _DOWNLOAD_ENV and ("PROXY" in name.upper() or "INDEX" in name.upper()))}
    sensitive.update(value for name, value in environment.items() if value and (
        "PROXY" in name.upper() or "INDEX" in name.upper()
        or re.search(r"(?:PASSWORD|TOKEN|USERNAME)$", name, re.I)))
    for value in sorted(sensitive, key=len, reverse=True):
        detail = detail.replace(value, "[redacted]")
    return re.sub(r"([a-z][a-z0-9+.-]*://)[^\s/@]+@", r"\1[redacted]@", detail,
                  flags=re.IGNORECASE)


def _run(argv, *, timeout_s=None, allow_downloads=False):
    environment = _child_environment(allow_downloads=allow_downloads)
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
        detail = _redact_diagnostic(stderr or stdout, environment)
        raise ValueError(f"runtime command failed ({proc.returncode}): {detail[-2000:]}")
    return stdout


def _probe(python, *, timeout_s=None):
    code = """import importlib.metadata,json,sys,sysconfig
print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,
'base_exec_prefix':sys.base_exec_prefix,
'version':sys.version,'minor':str(sys.version_info.major)+'.'+str(sys.version_info.minor),
'stdlib':sysconfig.get_path('stdlib'),'paths':sys.path,
'packages':{d.metadata['Name']:d.version for d in importlib.metadata.distributions()}}))"""
    return json.loads(_run([python, "-B", "-I", "-c", code], timeout_s=timeout_s))


def _base(probe):
    executable = Path(probe["executable"]).resolve(strict=True)
    return {"executable": str(executable), "sha256": _digest(executable),
            "version": probe["version"], "base_prefix": probe["base_prefix"],
            "stdlib": probe["stdlib"]}


def _execution_base(probe):
    # Keep _base and provider-v1 manifests byte-for-byte compatible. Execution
    # adds the separately allocated executable-prefix needed by readonly binds.
    return {**_base(probe), "base_exec_prefix": str(Path(probe["base_exec_prefix"]).resolve())}


def _normalize_execution_python(venv, selected):
    """Normalize only a new, unpublished venv before probing or inventorying it.

    uv can reintroduce its mutable version alias even when given a physical
    executable. Binding just the resolved base prefix then leaves broken links.
    """
    selected = Path(selected).resolve(strict=True)
    for link in (venv / "bin").glob("python*"):
        if link.is_symlink():
            if link.resolve(strict=True) != selected:
                raise ValueError("execution venv links a different base interpreter")
            link.unlink()
            link.symlink_to(selected)
    config = venv / "pyvenv.cfg"
    rows = []
    for line in config.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip().lower() in {
                "home", "executable", "base-executable", "base-prefix", "base-exec-prefix"}:
            value = str(Path(value.strip()).resolve(strict=True))
            line = key.rstrip() + " = " + value
        rows.append(line)
    config.write_text("\n".join(rows) + "\n")


def _validate_execution_links(venv, paths):
    """Old valid artifacts remain usable; unmounted alias chains fail closed."""
    roots = tuple(Path(path) for path in paths)
    for link in (venv / "bin").glob("python*"):
        current, seen = link, set()
        while current.is_symlink():
            if current in seen:
                raise ValueError("execution interpreter symlink cycle")
            seen.add(current)
            target = Path(os.readlink(current))
            target = target if target.is_absolute() else current.parent / target
            target = Path(os.path.normpath(target))
            if not any(target == root or target.is_relative_to(root) for root in roots):
                raise ValueError("execution interpreter requires an unmounted alias; prepare in a fresh runtime store")
            current = target
    config = {}
    for line in (venv / "pyvenv.cfg").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            config[key.strip().lower()] = value.strip()
    for key in ("home", "executable", "base-executable", "base-prefix", "base-exec-prefix"):
        if key in config:
            path = Path(config[key])
            if not path.is_absolute() or path != path.resolve(strict=True):
                raise ValueError("execution configuration requires a mutable alias; prepare in a fresh runtime store")


def _execution_paths(probe, venv):
    paths = list(dict.fromkeys(str(Path(path).resolve(strict=True)) for path in
                (venv, probe["base_prefix"], probe["base_exec_prefix"])))
    if any(Path(path) == Path("/") or not Path(path).is_dir() for path in paths):
        raise ValueError("execution runtime requires physical non-root directory prefixes")
    return paths


def _admit(name, spec, folder):
    ready = json.loads((folder / "ready.json").read_bytes())
    if set(ready) != {"instance", "runtime_id"} or not re.fullmatch(r"[A-Za-z0-9_-]+", ready["instance"]):
        raise ValueError("invalid runtime publication")
    instance = folder / "instances" / ready["instance"]
    if instance.is_symlink():
        raise ValueError("runtime instance must not be a symlink")
    manifest_path = instance / "artifact.json"
    return _verify_manifest(name, spec, manifest_path, ready["runtime_id"])


def _verify_manifest(name, spec, manifest_path, runtime_id):
    """Validate an exact artifact; the mutable discovery index is not consulted."""
    manifest_path = Path(manifest_path)
    if (not manifest_path.is_absolute() or manifest_path.name != "artifact.json"
            or manifest_path.is_symlink() or manifest_path.resolve(strict=True) != manifest_path):
        raise ValueError("runtime manifest must have its physical absolute path")
    manifest = json.loads(manifest_path.read_bytes())
    if hashlib.sha256(_canonical(manifest)).hexdigest() != runtime_id:
        raise ValueError("runtime manifest identity changed")
    if (manifest.get("schema") != ARTIFACT_SCHEMA or manifest.get("key") != _key(spec)
            or manifest.get("dataset") != name or manifest.get("descriptor_sha256") != spec["descriptor_sha256"]
            or manifest.get("requirements_sha256") != spec["requirements_sha256"]):
        raise ValueError("runtime declaration or target changed")
    execution = spec.get("profile") == "execution"
    if manifest.get("profile") != ("execution" if execution else None):
        raise ValueError("runtime profile changed")
    venv = manifest_path.parent / "venv"
    if venv.is_symlink() or str(venv) != manifest["path"]:
        raise ValueError("runtime physical path changed")
    _store(venv)  # Exact bindings must not bypass the outside-platform rule.
    if _inventory(venv) != manifest["files"]:
        raise ValueError("runtime file inventory changed")
    probe = _probe(venv / "bin/python")
    base = _execution_base(probe) if execution else _base(probe)
    if base != manifest["base_python"]:
        raise ValueError("runtime base interpreter changed")
    _validate_probe(probe, spec, venv)
    result = {"python": str(venv / "bin/python"), "path": str(venv),
            "runtime_id": runtime_id, "manifest_path": str(manifest_path),
            "descriptor_sha256": spec["descriptor_sha256"],
            "requirements_sha256": spec["requirements_sha256"], "scope": SCOPE}
    if execution:
        paths = _execution_paths(probe, venv)
        if paths != manifest.get("runtime_paths"):
            raise ValueError("runtime physical prefixes changed")
        _validate_execution_links(venv, paths)
        result.update(dataset=name, profile="execution", runtime_paths=paths, scope=EXECUTION_SCOPE)
    return result


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


def _resolve_profile(name, getter, root, *, execution=False):
    try:
        spec = getter(name)
        folder = _store(root) / _key(spec) if spec is not None else None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise _unavailable(name, str(exc), status="invalid", execution=execution) from exc
    if spec is None:
        raise _unavailable(name, "no dataset runtime declaration", execution=execution)
    published = (folder / "ready.json").exists()
    try:
        return _admit(name, spec, folder)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise _unavailable(name, str(exc), status="invalid" if published else "unavailable",
                           execution=execution) from exc


def resolve(name: str, *, root=None) -> dict:
    """Verify one published provider runtime. Never build or select a fallback."""
    return _resolve_profile(name, descriptor, root)


def admit_provider(name: str, runtime: dict) -> dict:
    """Recheck an already selected loading artifact without reading ready.json."""
    try:
        spec = descriptor(name)
        if spec is None:
            raise ValueError("provider runtime declaration is absent")
        selected = _verify_manifest(name, spec, runtime["manifest_path"], runtime["runtime_id"])
        for key in ("python", "path", "descriptor_sha256", "requirements_sha256"):
            if runtime[key] != selected[key]:
                raise ValueError(f"bound provider {key} differs from the exact artifact")
        return selected
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise _unavailable(name, str(exc), status="invalid") from exc


def resolve_execution(name: str, *, root=None) -> dict:
    """Select a published service/verifier artifact for a new binding."""
    snapshot = _EXECUTION_SNAPSHOT.get()
    if snapshot is not None:
        if name not in snapshot:
            raise _unavailable(name, "execution artifact was not selected by the provider client", execution=True)
        return admit_binding(snapshot[name], "service")
    return _resolve_profile(name, execution_descriptor, root, execution=True)


@contextmanager
def execution_snapshot(bindings):
    """Restrict worker resolution to exact, parent-selected execution artifacts."""
    selected = {}
    for name, binding in bindings.items():
        validated = validate_binding(binding, "service")
        if validated["dataset"] != name:
            raise ValueError("execution snapshot dataset mismatch")
        admit_binding(validated, "service")
        selected[name] = validated
    token = _EXECUTION_SNAPSHOT.set(selected)
    try:
        yield
    finally:
        _EXECUTION_SNAPSHOT.reset(token)


def check(name: str, *, root=None) -> dict:
    """A fresh read-only fact about the provider dependency runtime, not its data."""
    try:
        runtime = resolve(name, root=root)
    except DatasetRuntimeUnavailable as exc:
        return {"present": False, "status": exc.status, "scope": SCOPE, "detail": str(exc)}
    return {"present": True, "status": "ready", "scope": SCOPE,
            "detail": "verified provider runtime", "runtime": runtime}


def check_execution(name: str, *, root=None) -> dict:
    """Check execution dependencies only; no service, task or scorer is started."""
    try:
        runtime = resolve_execution(name, root=root)
    except DatasetRuntimeUnavailable as exc:
        return {"present": False, "status": exc.status, "scope": EXECUTION_SCOPE, "detail": str(exc)}
    return {"present": True, "status": "ready", "scope": EXECUTION_SCOPE,
            "detail": "verified execution runtime", "runtime": runtime}


def _physical_python(uv, spec, requested, timeout_s):
    # uv may select an active venv or a version alias. Building against its final
    # executable prevents a different driver's aliases being required in bwrap.
    found = _run([uv, "python", "find", requested or spec["python"]], timeout_s=timeout_s).strip()
    executable = Path(found).expanduser().resolve(strict=True)
    probe = _probe(executable, timeout_s=timeout_s)
    if probe["minor"] != spec["python"]:
        raise ValueError("base interpreter version does not match the execution declaration")
    return executable


def _prepare_spec(name, spec, *, python=None, root=None, timeout_s=None, execution=False):
    if spec is None:
        raise _unavailable(name, "no dataset runtime declaration", execution=execution)
    folder = _store(root) / _key(spec)
    resolver = resolve_execution if execution else resolve
    with _locked(folder):
        if (folder / "ready.json").exists():
            runtime = resolver(name, root=root)
            if python is not None:
                if execution:
                    selected = Path(python).expanduser()
                    if not selected.is_file():
                        uv = shutil.which("uv")
                        if uv is None:
                            raise _unavailable(name, "uv is required to resolve the requested base interpreter", execution=True)
                        selected = _physical_python(uv, spec, python, timeout_s)
                    python = selected.resolve(strict=True)
                requested = _probe(python, timeout_s=timeout_s)
                actual = _probe(runtime["python"], timeout_s=timeout_s)
                base = _execution_base if execution else _base
                if base(requested) != base(actual):
                    raise _unavailable(name, "prepared runtime uses a different base interpreter", execution=execution)
            return {**runtime, "reused": True}
        uv = shutil.which("uv")
        if uv is None:
            raise _unavailable(name, "uv is required to prepare the runtime", execution=execution)
        instances = folder / "instances"
        instances.mkdir(exist_ok=True)
        instance = Path(tempfile.mkdtemp(prefix="build-", dir=instances))
        venv = instance / "venv"
        try:
            selected = _physical_python(uv, spec, python, timeout_s) if execution else python or spec["python"]
            _run([uv, "venv", "--no-project", "--python", selected, venv], timeout_s=timeout_s, allow_downloads=True)
            if execution:
                _normalize_execution_python(venv, selected)
            _run([uv, "pip", "install", "--python", venv / "bin/python", "-r", spec["requirements_path"]], timeout_s=timeout_s, allow_downloads=True)
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
            if execution:
                manifest.update(profile="execution", base_python=_execution_base(probe),
                                runtime_paths=_execution_paths(probe, venv))
            _atomic_json(instance / "artifact.json", manifest)
            runtime_id = hashlib.sha256(_canonical(manifest)).hexdigest()
            _atomic_json(folder / "ready.json", {"instance": instance.name, "runtime_id": runtime_id})
        except BaseException:
            _remove_instance(instance)
            raise
    return {**resolver(name, root=root), "reused": False}


def prepare(name: str, *, python=None, root=None, timeout_s=None) -> dict:
    """Explicitly install a provider runtime, or verify an existing publication."""
    return _prepare_spec(name, descriptor(name), python=python, root=root, timeout_s=timeout_s)


def prepare_execution(name: str, *, python=None, root=None, timeout_s=None) -> dict:
    """Explicitly prepare a shared service/verifier dependency artifact."""
    return _prepare_spec(name, execution_descriptor(name), python=python, root=root,
                         timeout_s=timeout_s, execution=True)


def bind(runtime: dict, role: str) -> dict:
    """Capture a verified execution artifact for one trusted launch role.

    The role record does not pin the store's mutable discovery index. Launchers
    must call admit_binding immediately before starting the selected process.
    """
    if role not in {"actor", "service", "verifier"}:
        raise ValueError("Python execution runtime role must be actor, service or verifier")
    if not isinstance(runtime, dict) or runtime.get("profile") != "execution":
        raise ValueError("a role binding requires a verified execution artifact")
    binding = {"schema": BINDING_SCHEMA, "dataset": runtime["dataset"], "profile": "execution",
            "role": role, **{key: runtime[key] for key in
            ("runtime_id", "manifest_path", "python", "path", "runtime_paths")}}
    return validate_binding(binding, role)


def validate_binding(binding: dict, expected_role: str, python=None) -> dict:
    """Validate declaration structure without reading or executing its artifact.

    Registry/lint uses this cheap check while materializing task declarations.
    A launch must still call admit_binding to verify the actual selected bytes.
    """
    fields = {"schema", "dataset", "profile", "role", "runtime_id", "manifest_path",
              "python", "path", "runtime_paths"}
    if (not isinstance(binding, dict) or set(binding) != fields
            or binding["schema"] != BINDING_SCHEMA or binding["profile"] != "execution"
            or not isinstance(expected_role, str) or expected_role not in {"actor", "service", "verifier"}
            or binding["role"] != expected_role
            or not isinstance(binding["dataset"], str) or not _NAME.fullmatch(binding["dataset"])
            or not isinstance(binding["runtime_id"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", binding["runtime_id"])):
        raise ValueError("invalid or mismatched Python runtime role binding")
    paths = binding["runtime_paths"]
    if (not isinstance(paths, list) or not paths
            or any(not isinstance(path, str) for path in paths)
            or len(paths) != len(set(paths))):
        raise ValueError("bound runtime_paths must be a nonempty list of unique directory paths")
    for value in (binding["path"], binding["python"], binding["manifest_path"], *paths):
        if (not isinstance(value, str) or not value or "\x00" in value or "\\" in value
                or value.startswith("//") or not PurePosixPath(value).is_absolute()
                or PurePosixPath(value) == PurePosixPath("/")
                or ".." in PurePosixPath(value).parts or str(PurePosixPath(value)) != value):
            raise ValueError("bound runtime paths must be normalized absolute paths")
    venv = PurePosixPath(binding["path"])
    if PurePosixPath(binding["python"]) != venv / "bin/python":
        raise ValueError("bound runtime python must be its exact venv/bin/python")
    if PurePosixPath(binding["manifest_path"]) != venv.parent / "artifact.json":
        raise ValueError("bound manifest must belong to the exact runtime instance")
    if paths[0] != binding["path"]:
        raise ValueError("bound runtime_paths must start with the exact venv path")
    if python is not None and str(python) != binding["python"]:
        raise ValueError("launch executable differs from the bound runtime Python")
    return {**binding, "runtime_paths": list(paths)}


def admit_binding(binding: dict, expected_role: str, python=None) -> dict:
    """Verify an exact role-bound artifact without reading the latest index."""
    name = binding.get("dataset", "execution") if isinstance(binding, dict) else "execution"
    try:
        binding = validate_binding(binding, expected_role, python)
        spec = execution_descriptor(name)
        if spec is None:
            raise ValueError("execution runtime declaration is absent")
        selected = _verify_manifest(name, spec, binding["manifest_path"], binding["runtime_id"])
        for key in ("python", "path", "runtime_paths"):
            if binding[key] != selected[key]:
                raise ValueError(f"bound runtime {key} differs from the exact artifact")
        return selected
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise _unavailable(name, str(exc), status="invalid", execution=True) from exc
