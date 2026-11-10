"""Dependency-only Python artifacts for untrusted, source-owned Harness candidates.

Preparation installs wheels, never source distributions or candidate code. Python
startup is probed with ``-I -S`` against the physical base interpreter; installed
metadata is read as data. Candidate package startup happens only at actor launch.
"""
from __future__ import annotations

import hashlib
import json
import math
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
import time

from eval.ckpt.source_tree import PYTHON_BYTECODE, PYTHON_GENERATED, python_spec, runtime_spec
from harnessgrad.paths import configured_path, main_checkout
from harnessgrad.python_artifacts import (
    atomic_json, canonical, digest, freeze, inventory, locked, remove_instance,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_SCHEMA = "harnessgrad.harness-python-artifact.v1"
SOURCE_SCHEMA = "harnessgrad.harness-python-source.v3"
BINDING_VERSION = 3
RECIPE_VERSION = 1
SCOPE = "venv-files-and-base-executable; full-stdlib-and-system-libraries-not-sealed"
MAX_LOCK_BYTES = 64 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)\Z")
_ARTIFACT_FIELDS = {"runtime_id", "manifest_path", "python", "path", "runtime_paths", "dependency_key"}
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy")


class RuntimeUnavailable(ValueError):
    """Expected preparation/admission failure, with a private lifecycle receipt."""

    def __init__(self, message, *, receipt=None):
        super().__init__(message)
        self.receipt = receipt


class RuntimeNotPrepared(RuntimeUnavailable):
    """The selected dependency store has no runtime publication."""


def _package(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_LOCK_BYTES:
        raise ValueError("Python lock must be at most 64 KiB")
    pins = {}
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.fullmatch(line)
        if match is None or _package(match[1]) in pins:
            raise ValueError("Python lock requires unique exact full pins; options, URLs and local dependencies are forbidden")
        pins[_package(match[1])] = match[2]
    return pins


def declaration_identity(manifest, requirements):
    """Pure declaration hash for checkpoint readers; executes and imports nothing."""
    if not isinstance(manifest, dict):
        raise ValueError("Harness manifest must be an object")
    spec = python_spec(manifest.get("runtime"), manifest.get("install"))
    _pins(requirements)
    return hashlib.sha256(canonical({"runtime": spec,
        "requirements_sha256": hashlib.sha256(requirements).hexdigest()})).hexdigest()


def descriptor(repo):
    repo = Path(repo).resolve(strict=True)
    spec = runtime_spec(repo)
    if spec is None or spec["kind"] != "python":
        return None
    raw = (repo / spec["requirements"]).read_bytes()
    manifest = json.loads((repo / "harness.json").read_bytes())
    return {**spec, "declaration_sha256": declaration_identity(manifest, raw),
            "requirements_sha256": hashlib.sha256(raw).hexdigest(),
            "requirements_path": str(repo / spec["requirements"]), "pins": _pins(raw)}


def source_fingerprint(repo):
    """Hash framed file descriptors, including ignored source and executable flags.

    File bytes are represented by their digest inside canonical JSON. Arbitrary
    binary content can therefore never be mistaken for another file's record.
    Source directories must be parents of retained files: Git and portable
    snapshots cannot preserve empty directories or generated-only subtrees.
    """
    root = Path(repo).resolve(strict=True)
    rows = []
    directories, parents = set(), set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in PYTHON_GENERATED)
        for name in dirs:
            path = Path(directory) / name
            if path.is_symlink():
                raise RuntimeUnavailable("Managed Python source must not contain directory aliases")
            directories.add(path.relative_to(root).as_posix())
        for name in sorted(files):
            if name in PYTHON_GENERATED or name.endswith(PYTHON_BYTECODE):
                continue
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                raise RuntimeUnavailable("Managed Python source must contain only owned regular files")
            relative = path.relative_to(root)
            rows.append((relative.as_posix(), path))
            parents.update(parent.as_posix() for parent in relative.parents if parent != Path("."))
    empty = sorted(directories - parents)
    if empty:
        raise RuntimeUnavailable("Managed Python source contains an empty source directory: "
                                 f"{empty[0]}. Remove it or add an owned source file; "
                                 "generated state and bytecode do not preserve directories")
    files = [{"path": relative, "executable": bool(path.stat().st_mode & 0o111),
              "sha256": digest(path)} for relative, path in sorted(rows)]
    return hashlib.sha256(canonical({"schema": SOURCE_SCHEMA, "files": files})).hexdigest()


def _outside(path, repo, *, label):
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    path = path.resolve()
    protected = {ROOT.resolve(), main_checkout(ROOT).resolve(), Path(repo).resolve()}
    for key in ("HARNESSGRAD_WORK_ROOT", "HARNESSGRAD_WORKSPACE"):
        if os.environ.get(key):
            protected.add(Path(os.environ[key]).expanduser().resolve())
    if path in {Path("/"), Path("/tmp"), Path("/var/tmp"), Path("/home"), Path.home().resolve()}:
        raise ValueError(f"{label} must be a dedicated external directory")
    if any(path == root or path.is_relative_to(root) or root.is_relative_to(path) for root in protected):
        raise ValueError(f"{label} must be outside candidate, platform and work trees")
    return path


def store_root(repo, root=None):
    default = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "harnessgrad/harness-runtimes"
    return _outside(root if root is not None else configured_path("HG_HARNESS_RUNTIME_ROOT", default),
                    repo, label="Harness runtime store")


def _key(spec):
    return hashlib.sha256(canonical({"declaration": spec["declaration_sha256"],
        "platform": sys.platform, "machine": platform.machine(), "recipe": RECIPE_VERSION})).hexdigest()


def _environment(home, cache=None, *, allow_downloads=False):
    env = {"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "TMPDIR": str(Path(home) / "tmp"), "XDG_CONFIG_HOME": str(Path(home) / "config"),
           "XDG_CACHE_HOME": str(Path(home) / "cache"), "PYTHONDONTWRITEBYTECODE": "1",
           "UV_NO_CONFIG": "1", "UV_PYTHON_DOWNLOADS": "never", "UV_LINK_MODE": "copy",
           "UV_NO_PROGRESS": "1"}
    if cache is not None:
        env["UV_CACHE_DIR"] = str(cache)
    if allow_downloads:
        env.update({key: os.environ[key] for key in _PROXY_KEYS if key in os.environ})
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    env["UV_PYTHON_INSTALL_DIR"] = str(Path(os.environ.get("UV_PYTHON_INSTALL_DIR") or data / "uv/python").resolve())
    return env


def _run(argv, *, cwd, env, timeout_s):
    proc = subprocess.Popen([str(arg) for arg in argv], cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            errors="replace", start_new_session=True)
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
        detail = stderr or stdout
        for key in _PROXY_KEYS:
            if env.get(key):
                detail = detail.replace(env[key], "[redacted proxy]")
        detail = re.sub(r"([a-z][a-z0-9+.-]*://)[^\s/@]+@", r"\1[redacted]@", detail, flags=re.IGNORECASE)
        raise RuntimeUnavailable(f"Python runtime command failed ({proc.returncode}): {detail[-2400:]}")
    return stdout


_BASE_PROBE = """import json,sys,sysconfig
print(json.dumps({'executable':sys.executable,'base_executable':getattr(sys,'_base_executable',sys.executable),
'base_prefix':sys.base_prefix,'base_exec_prefix':sys.base_exec_prefix,'stdlib':sysconfig.get_path('stdlib'),
'implementation':sys.implementation.name,'cache_tag':sys.implementation.cache_tag,
'version':sys.version,'minor':str(sys.version_info.major)+'.'+str(sys.version_info.minor)}))"""


def _base_probe(python, minor, *, timeout_s=120):
    executable = Path(python).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="hg-python-base-") as neutral:
        env = _environment(neutral)
        result = json.loads(_run([executable, "-I", "-S", "-B", "-c", _BASE_PROBE],
                                 cwd=neutral, env=env, timeout_s=timeout_s))
    physical = Path(result["base_executable"]).resolve(strict=True)
    if physical != executable:
        return _base_probe(physical, minor, timeout_s=timeout_s)
    if result["minor"] != minor or result["implementation"] != "cpython":
        raise RuntimeUnavailable("Managed Python requires the declared CPython minor version")
    return {"executable": str(executable), "sha256": digest(executable),
            **{key: str(Path(result[key]).resolve(strict=True)) for key in
               ("base_prefix", "base_exec_prefix", "stdlib")},
            **{key: result[key] for key in ("version", "minor", "implementation", "cache_tag")}}


def _physical_python(uv, spec, requested, timeout_s):
    if (requested is None and sys.implementation.name == "cpython"
            and f"{sys.version_info.major}.{sys.version_info.minor}" == spec["python"]):
        return _base_probe(sys.executable, spec["python"], timeout_s=timeout_s)
    with tempfile.TemporaryDirectory(prefix="hg-python-find-") as neutral:
        found = _run([uv, "--no-config", "--no-python-downloads", "python", "find", requested or spec["python"]],
                     cwd=neutral, env=_environment(neutral), timeout_s=timeout_s).strip()
    return _base_probe(Path(found), spec["python"], timeout_s=timeout_s)


def _base_runtime_paths(repo, base):
    paths = [_outside(base[key], repo, label="Python " + key)
             for key in ("base_prefix", "base_exec_prefix")]
    if any(not path.is_dir() for path in paths):
        raise RuntimeUnavailable("Managed Python requires physical, non-root interpreter prefixes")
    return paths


def _runtime_paths(repo, venv, base):
    venv = _outside(venv, repo, label="Python venv")
    if not venv.is_dir():
        raise RuntimeUnavailable("Managed Python requires a physical venv prefix")
    return list(dict.fromkeys(map(str, (venv, *_base_runtime_paths(repo, base)))))


_PACKAGE_PROBE = """import importlib.metadata,json,sys
rows=[]
for d in importlib.metadata.distributions(path=[sys.argv[1]]):
 rows.append([d.metadata['Name'],d.version])
print(json.dumps(rows))"""


def _packages(venv, base, *, timeout_s=120):
    # Never execute the installed venv: -I alone would still execute .pth hooks.
    cfg = {}
    for line in (venv / "pyvenv.cfg").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            cfg[key.strip().lower()] = value.strip()
    if (cfg.get("include-system-site-packages", "").lower() != "false"
            or Path(cfg.get("home", "")).resolve() != Path(base["executable"]).parent):
        raise RuntimeUnavailable("Managed venv exposes system packages or a different base interpreter")
    if (venv / "bin/python").resolve(strict=True) != Path(base["executable"]):
        raise RuntimeUnavailable("Managed venv interpreter differs from its recorded base")
    site = venv / ("lib/python" + base["minor"]) / "site-packages"
    if not site.is_dir() or site.resolve() != site:
        raise RuntimeUnavailable("Managed venv has no physical site-packages directory")
    with tempfile.TemporaryDirectory(prefix="hg-python-metadata-") as neutral:
        rows = json.loads(_run([base["executable"], "-I", "-S", "-B", "-c", _PACKAGE_PROBE, site],
                              cwd=neutral, env=_environment(neutral), timeout_s=timeout_s))
    packages = {}
    for name, version in rows:
        normalized = _package(name)
        if normalized in packages:
            raise RuntimeUnavailable("Managed venv contains duplicate distribution metadata")
        packages[normalized] = version
    return packages


def _cache(repo, store):
    default = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "uv"
    cache = _outside(os.environ.get("UV_CACHE_DIR") or default, repo, label="Wheel cache")
    if cache == store or cache.is_relative_to(store) or store.is_relative_to(cache):
        raise RuntimeUnavailable("Wheel cache and immutable Harness store must not overlap")
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _builder(argv, *, instance, uv, base, cache, allow_downloads, timeout_s, wheelhouse=None):
    bwrap = shutil.which("bwrap", path=os.defpath)
    if bwrap is None:
        raise RuntimeUnavailable("Managed Python preparation requires bubblewrap")
    home = instance / "home"
    for path in (home, home / "tmp", home / "config", home / "cache"):
        path.mkdir(exist_ok=True)
    plan = [bwrap, "--die-with-parent", "--unshare-pid", "--as-pid-1", "--proc", "/proc", "--dev", "/dev"]
    if not allow_downloads:
        plan.append("--unshare-net")
    mounts = [Path(name) for name in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc") if Path(name).exists()]
    if allow_downloads:
        resolver = Path('/etc/resolv.conf').resolve(strict=True)
        if not resolver.is_relative_to('/etc'):
            mounts.append(resolver)
    mounts += [uv, Path(base["base_prefix"]), Path(base["base_exec_prefix"])]
    if wheelhouse is not None:
        mounts.append(wheelhouse)
    for path in dict.fromkeys(mounts):
        plan += ["--ro-bind", str(path), str(path)]
    plan += ["--bind", str(instance), str(instance), "--bind", str(cache), str(cache),
             "--chdir", str(home), "--", *map(str, argv)]
    return _run(plan, cwd=home, env=_environment(home, cache, allow_downloads=allow_downloads), timeout_s=timeout_s)


def _read_artifact(repo, spec, reference):
    artifact = reference
    manifest_path = Path(artifact["manifest_path"])
    if (manifest_path.is_symlink() or manifest_path.resolve(strict=True) != manifest_path
            or manifest_path.parent.is_symlink()
            or len(manifest_path.parents) < 4 or manifest_path.parent.parent.name != "instances"):
        raise RuntimeUnavailable("Harness artifact must have its exact physical manifest path")
    store = store_root(repo, manifest_path.parents[3])
    if manifest_path.parents[2] != store / artifact["dependency_key"]:
        raise RuntimeUnavailable("Harness artifact is outside its declared dependency store")
    manifest = json.loads(manifest_path.read_bytes())
    if hashlib.sha256(canonical(manifest)).hexdigest() != artifact["runtime_id"]:
        raise RuntimeUnavailable("Harness runtime manifest identity changed")
    if (manifest.get("schema") != ARTIFACT_SCHEMA or manifest.get("dependency_key") != _key(spec)
            or manifest.get("declaration_sha256") != spec["declaration_sha256"]
            or manifest.get("requirements_sha256") != spec["requirements_sha256"]
            or manifest.get("target") != {"platform": sys.platform, "machine": platform.machine()}
            or (manifest.get("recipe") or {}).get("version") != RECIPE_VERSION
            or (manifest.get("recipe") or {}).get("complete_lock") is not True):
        raise RuntimeUnavailable("Harness runtime declaration, target or build recipe changed")
    venv = manifest_path.parent / "venv"
    for key, expected in (("path", str(venv)), ("runtime_paths", artifact["runtime_paths"])):
        if manifest.get(key) != expected or artifact[key] != expected:
            raise RuntimeUnavailable("Harness runtime physical paths changed")
    if _runtime_paths(repo, venv, manifest["base_python"]) != artifact["runtime_paths"]:
        raise RuntimeUnavailable("Harness runtime base interpreter paths changed")
    if inventory(venv) != manifest.get("files"):
        raise RuntimeUnavailable("Harness runtime file inventory changed")
    if digest(manifest["base_python"]["executable"]) != manifest["base_python"]["sha256"]:
        raise RuntimeUnavailable("Harness runtime base executable changed")
    base = _base_probe(manifest["base_python"]["executable"], spec["python"])
    if base != manifest["base_python"] or _runtime_paths(repo, venv, base) != artifact["runtime_paths"]:
        raise RuntimeUnavailable("Harness runtime base interpreter changed")
    if _packages(venv, base) != spec["pins"] or manifest.get("packages") != spec["pins"]:
        raise RuntimeUnavailable("Harness runtime packages differ from its complete lock")
    return artifact


def validate_binding(binding):
    if (not isinstance(binding, dict) or set(binding) != {"version", "kind", "source_sha256", "declaration_sha256", "artifact", "sha256"}
            or binding["kind"] != "python"):
        raise ValueError("Invalid managed Harness runtime binding")
    if type(binding["version"]) is not int or binding["version"] != BINDING_VERSION:
        raise ValueError("Unsupported managed Harness binding version; version 3 is required. "
                         "Earlier bindings cannot certify the source directory policy "
                         "and cannot be safely upgraded for score reuse")
    for key in ("source_sha256", "declaration_sha256", "sha256"):
        if not isinstance(binding[key], str) or not _HASH.fullmatch(binding[key]):
            raise ValueError("Invalid managed Harness identity digest")
    artifact = binding["artifact"]
    if not isinstance(artifact, dict) or set(artifact) != _ARTIFACT_FIELDS:
        raise ValueError("Invalid managed Harness artifact reference")
    for key in ("runtime_id", "dependency_key"):
        if not isinstance(artifact[key], str) or not _HASH.fullmatch(artifact[key]):
            raise ValueError("Invalid managed Harness artifact digest")
    paths = artifact["runtime_paths"]
    if (not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths)
            or len(paths) != len(set(paths))):
        raise ValueError("Managed Harness requires unique runtime prefixes")
    for value in (artifact["manifest_path"], artifact["python"], artifact["path"], *paths):
        if (not isinstance(value, str) or not value or "\\" in value or "\x00" in value
                or not PurePosixPath(value).is_absolute() or value.startswith("//")
                or PurePosixPath(value) == PurePosixPath("/") or ".." in PurePosixPath(value).parts
                or str(PurePosixPath(value)) != value):
            raise ValueError("Managed Harness paths must be normalized absolute paths")
    venv = PurePosixPath(artifact["path"])
    if (artifact["python"] != str(venv / "bin/python") or paths[0] != str(venv)
            or artifact["manifest_path"] != str(venv.parent / "artifact.json")):
        raise ValueError("Managed Harness paths do not name one exact runtime")
    value = {key: item for key, item in binding.items() if key != "sha256"}
    if hashlib.sha256(canonical(value)).hexdigest() != binding["sha256"]:
        raise ValueError("Managed Harness binding digest mismatch")
    return json.loads(canonical(binding))


def _bind(repo, spec, artifact, source_sha):
    value = {"version": BINDING_VERSION, "kind": "python", "source_sha256": source_sha,
             "declaration_sha256": spec["declaration_sha256"], "artifact": artifact}
    return {**value, "sha256": hashlib.sha256(canonical(value)).hexdigest()}


def admit_binding(repo, binding):
    started = time.monotonic()
    try:
        binding = validate_binding(binding)
        spec = descriptor(repo)
        if spec is None or spec["declaration_sha256"] != binding["declaration_sha256"]:
            raise RuntimeUnavailable("Managed Harness dependency declaration changed or disappeared")
        if source_fingerprint(repo) != binding["source_sha256"]:
            raise RuntimeUnavailable("Managed Harness source differs from its frozen binding")
        if binding["artifact"]["dependency_key"] != _key(spec):
            raise RuntimeUnavailable("Managed Harness dependency target changed")
        _read_artifact(repo, spec, binding["artifact"])
        if source_fingerprint(repo) != binding["source_sha256"]:
            raise RuntimeUnavailable("Managed Harness source changed during admission")
        return binding
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        message = str(exc) if isinstance(exc, RuntimeUnavailable) else f"Managed Harness runtime unavailable: {exc}"
        receipt = {"status": "invalid", "offline": True, "installed": False, "cache_hit": False,
                   "seconds": round(time.monotonic() - started, 6),
                   "error_type": type(exc).__name__, "detail": message}
        raise RuntimeUnavailable(message, receipt=receipt) from exc


def _published(repo, spec, folder):
    ready_path = folder / "ready.json"
    if ready_path.is_symlink():
        raise RuntimeUnavailable("Harness runtime publication must not be a link")
    if not ready_path.exists():
        raise RuntimeNotPrepared("Prepared Python Harness runtime missing; run python tools/configure_harness.py --harness <dir> first")
    ready = json.loads(ready_path.read_bytes())
    if (not isinstance(ready, dict) or set(ready) != {"instance", "runtime_id"}
            or not isinstance(ready["instance"], str)
            or not re.fullmatch(r"build-[A-Za-z0-9_-]+", ready["instance"])
            or not isinstance(ready["runtime_id"], str) or not _HASH.fullmatch(ready["runtime_id"])):
        raise RuntimeUnavailable("Invalid managed Harness publication")
    venv = folder / "instances" / ready["instance"] / "venv"
    manifest = json.loads((venv.parent / "artifact.json").read_bytes())
    reference = {"runtime_id": ready["runtime_id"], "manifest_path": str(venv.parent / "artifact.json"),
                 "python": str(venv / "bin/python"), "path": str(venv),
                 "runtime_paths": manifest["runtime_paths"], "dependency_key": _key(spec)}
    return _read_artifact(repo, spec, reference)


def _build(repo, spec, folder, *, python, allow_downloads, timeout_s):
    uv_path = shutil.which("uv")
    if not uv_path:
        raise RuntimeUnavailable("uv is required to prepare a managed Harness runtime")
    uv = Path(uv_path).resolve(strict=True)
    base = _physical_python(uv, spec, python, timeout_s)
    _base_runtime_paths(repo, base)  # Reject protected roots before any builder mounts them.
    store = folder.parent
    cache = _cache(repo, store)
    wheelhouse = None
    if os.environ.get("HG_HARNESS_WHEELHOUSE"):
        wheelhouse = _outside(os.environ["HG_HARNESS_WHEELHOUSE"], repo, label="Wheelhouse")
        if not wheelhouse.is_dir():
            raise RuntimeUnavailable("Configured Harness wheelhouse does not exist")
    instances = folder / "instances"
    if instances.is_symlink():
        raise RuntimeUnavailable("Harness instances directory must not be a link")
    instances.mkdir(exist_ok=True)
    instance = Path(tempfile.mkdtemp(prefix="build-", dir=instances))
    venv = instance / "venv"
    try:
        lock = instance / "requirements.lock"
        lock.write_text("\n".join(name + "==" + version for name, version in sorted(spec["pins"].items())) + "\n")
        # Resolve while the target is the trusted base, before any candidate wheel
        # is installed. A no-deps install alone would accept an incomplete lock.
        resolved = instance / "resolved.lock"
        resolve = [uv, "--no-config", "--no-python-downloads", "pip", "compile", lock,
                   "--python", base["executable"], "--only-binary", ":all:", "--no-header",
                   "--no-annotate", "--no-emit-index-url", "--no-emit-find-links", "-o", resolved]
        if not allow_downloads:
            resolve.append("--offline")
        if wheelhouse is not None:
            resolve += ["--no-index", "--find-links", wheelhouse]
        _builder(resolve, instance=instance, uv=uv, base=base, cache=cache,
                 allow_downloads=allow_downloads, timeout_s=timeout_s, wheelhouse=wheelhouse)
        if _pins(resolved.read_bytes()) != spec["pins"]:
            raise RuntimeUnavailable("Python lock omits or conflicts with required transitive dependencies")
        _builder([uv, "--no-config", "--no-python-downloads", "venv", "--no-project", "--python", base["executable"], venv],
                 instance=instance, uv=uv, base=base, cache=cache, allow_downloads=allow_downloads, timeout_s=timeout_s,
                 wheelhouse=wheelhouse)
        command = [uv, "--no-config", "--no-python-downloads", "pip", "install", "--python", venv / "bin/python",
                   "--no-deps", "--only-binary", ":all:", "--link-mode", "copy", "-r", lock]
        if not allow_downloads:
            command.append("--offline")
        if wheelhouse is not None:
            command += ["--no-index", "--find-links", wheelhouse]
        _builder(command, instance=instance, uv=uv, base=base, cache=cache,
                 allow_downloads=allow_downloads, timeout_s=timeout_s, wheelhouse=wheelhouse)
        inventory(venv)  # Refuse links before any metadata is read through them.
        packages = _packages(venv, base, timeout_s=timeout_s)
        if packages != spec["pins"]:
            raise RuntimeUnavailable("Prepared Harness packages do not exactly match its complete lock")
        freeze(venv)
        # The published instance contains dependencies and their manifest only.
        # Installer HOME/tmp and resolver inputs are preparation state.
        shutil.rmtree(instance / "home")
        lock.unlink()
        resolved.unlink()
        manifest = {"schema": ARTIFACT_SCHEMA, "dependency_key": _key(spec),
            "declaration_sha256": spec["declaration_sha256"], "requirements_sha256": spec["requirements_sha256"],
            "path": str(venv), "runtime_paths": _runtime_paths(repo, venv, base),
            "target": {"platform": sys.platform, "machine": platform.machine()},
            "recipe": {"version": RECIPE_VERSION, "installer": "uv", "wheel_only": True,
                       "no_deps": True, "complete_lock": True,
                       "downloads_allowed": bool(allow_downloads), "binary_sha256": digest(uv)},
            "base_python": base, "packages": packages, "files": inventory(venv), "scope": SCOPE}
        atomic_json(instance / "artifact.json", manifest)
        reference = {"runtime_id": hashlib.sha256(canonical(manifest)).hexdigest(),
            "manifest_path": str(instance / "artifact.json"), "python": str(venv / "bin/python"),
            "path": str(venv), "runtime_paths": manifest["runtime_paths"], "dependency_key": _key(spec)}
        return reference, instance
    except BaseException:
        remove_instance(instance)
        raise


def prepare(repo, *, binding=None, build=True, allow_downloads=False, python=None,
            root=None, timeout_s=120, force=False):
    """Prepare once after source staging, or admit an exact recorded binding.

    Automatic callers retain the default offline wheel-only policy. Explicit
    configuration may opt into downloads; neither route permits source builds.
    """
    started = time.monotonic()
    receipt = {"status": "unavailable", "offline": not allow_downloads, "installed": False,
               "cache_hit": False, "seconds": 0.0}
    instance = None
    try:
        repo = Path(repo).resolve(strict=True)
        spec = descriptor(repo)
        if spec is None:
            if binding is not None:
                raise RuntimeUnavailable("Managed Harness runtime declaration disappeared")
            return None
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not math.isfinite(timeout_s) or timeout_s <= 0):
            raise RuntimeUnavailable("Python runtime timeout must be finite and positive")
        source_sha = source_fingerprint(repo)
        receipt.update(source_sha256=source_sha, declaration_sha256=spec["declaration_sha256"])
        if binding is not None:
            if force or python is not None:
                raise RuntimeUnavailable("An exact Harness binding cannot be reconfigured during admission")
            selected = admit_binding(repo, binding)
            receipt["cache_hit"] = True
        else:
            folder = store_root(repo, root) / _key(spec)
            if folder.is_symlink():
                raise RuntimeUnavailable("Harness dependency directory must not be a link")
            if not build:
                reference = _published(repo, spec, folder)
                receipt["cache_hit"] = True
            else:
                with locked(folder, timeout_s=timeout_s):
                    publication = folder / "ready.json"
                    if not force and (publication.is_symlink() or publication.exists()):
                        reference = _published(repo, spec, folder)
                        if python is not None:
                            uv = shutil.which("uv")
                            if uv is None:
                                raise RuntimeUnavailable("uv is required to select an explicit base interpreter")
                            expected = _physical_python(Path(uv).resolve(), spec, python, timeout_s)
                            manifest = json.loads(Path(reference["manifest_path"]).read_bytes())
                            if expected != manifest["base_python"]:
                                raise RuntimeUnavailable("Prepared Harness uses a different base; configure with --force")
                        receipt["cache_hit"] = True
                    else:
                        reference, instance = _build(repo, spec, folder, python=python,
                            allow_downloads=allow_downloads, timeout_s=timeout_s)
                        receipt["installed"] = True
                        if source_fingerprint(repo) != source_sha or descriptor(repo) != spec:
                            raise RuntimeUnavailable("Harness source changed during preparation")
                        atomic_json(publication, {"instance": instance.name,
                                                  "runtime_id": reference["runtime_id"]})
                        instance = None  # Published immutable dependencies survive later source failures.
            selected = _bind(repo, spec, reference, source_sha)
            admit_binding(repo, selected)
        if source_fingerprint(repo) != source_sha:
            raise RuntimeUnavailable("Harness source changed during preparation")
        receipt.update(status="ready", identity=selected, python=selected["artifact"]["python"],
                       runtime_paths=list(selected["artifact"]["runtime_paths"]))
        return receipt
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        if instance is not None and instance.exists():
            remove_instance(instance)
        receipt.update(status="invalid", error_type=type(exc).__name__, detail=str(exc))
        raise RuntimeUnavailable(f"Managed Python preparation unavailable: {exc}", receipt=receipt) from exc
    finally:
        receipt["seconds"] = round(time.monotonic() - started, 6)


def identity(repo, binding=None):
    receipt = prepare(repo, binding=binding, build=False)
    return receipt["identity"] if receipt is not None else None
