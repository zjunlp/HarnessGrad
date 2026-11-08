"""Manifest runtime declarations and source copying; stdlib-only checkpoint helpers."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path, PurePosixPath

BUN_GENERATED = frozenset({
    ".git", "node_modules", ".state", "__pycache__",
    ".opencode-state", "_harnessgrad",
})
PYTHON_GENERATED = BUN_GENERATED | frozenset({".venv", "venv"})
PYTHON_BYTECODE = (".pyc", ".pyo")


def python_spec(spec, install=None):
    """Validate the data-only managed Python declaration, without imports/builds."""
    if (not isinstance(spec, dict) or set(spec) not in (
            {"kind", "python", "requirements"}, {"kind", "python", "requirements", "exec"})
            or spec.get("kind") != "python"
            or not isinstance(spec.get("python"), str)
            or not re.fullmatch(r"3\.[0-9]+", spec["python"])
            or int(spec["python"].split(".")[1]) < 8):
        raise ValueError("Python runtime requires kind, Python 3.8+ minor and requirements")
    if "exec" in spec:
        execution = spec["exec"]
        if (not isinstance(execution, dict) or set(execution) != {"requirements"}
                or not isinstance(execution["requirements"], dict) or not execution["requirements"]
                or any(not isinstance(minor, str) or not re.fullmatch(r"3\.[0-9]+", minor)
                       or int(minor.split('.')[1]) < 8
                       for minor in execution["requirements"])):
            raise ValueError("Python exec requires an explicit minor-version to lock-file map")
        for path in execution["requirements"].values():
            python_spec({"kind": "python", "python": spec["python"], "requirements": path})
    relative = spec["requirements"]
    if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
            or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts
            or any(part in PYTHON_GENERATED for part in PurePosixPath(relative).parts)
            or str(PurePosixPath(relative)) != relative):
        raise ValueError("Python requirements must be a normalized owned relative file")
    if install is not None and install != relative:
        raise ValueError("Python runtime and legacy install must name the same lock file")
    return dict(spec)


def runtime_spec(root: Path) -> dict | None:
    """Read an explicit runtime contract, never infer capabilities from identity."""
    path = Path(root) / "harness.json"
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid harness manifest") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Harness manifest must be an object")
    spec = manifest.get("runtime")
    if spec is None:
        return None
    if isinstance(spec, dict) and spec.get("kind") == "python":
        spec = python_spec(spec, manifest.get("install"))
        if manifest.get("runtime_paths") not in (None, []):
            raise ValueError("Managed Python runtime_paths must be empty; external runtime roots are not supported")
        for relative in {spec["requirements"], *(spec.get("exec", {}).get("requirements", {}).values())}:
            lock = Path(root) / relative
            if (lock.is_symlink() or not lock.is_file()
                    or lock.resolve() != Path(root).resolve() / relative):
                raise ValueError("Python requirements must be an owned regular file without directory aliases")
        entry = manifest.get("entrypoint")
        if (not isinstance(entry, str) or not entry or "\\" in entry or ":" in entry
                or PurePosixPath(entry).is_absolute() or ".." in PurePosixPath(entry).parts
                or any(part in PYTHON_GENERATED for part in PurePosixPath(entry).parts)
                or entry.endswith(PYTHON_BYTECODE) or str(PurePosixPath(entry)) != entry):
            raise ValueError("Python entrypoint must be a normalized owned source path outside generated environments")
        executable = Path(root) / entry
        if (executable.is_symlink() or not executable.is_file()
                or executable.resolve() != Path(root).resolve() / entry):
            raise ValueError("Python entrypoint must be an owned regular source file without aliases")
        return spec
    if not isinstance(spec, dict) or spec.get("kind") != "bun":
        raise ValueError("runtime must declare supported kind 'bun' or 'python'")
    if set(spec) != {"kind", "source", "entrypoint", "package"}:
        raise ValueError("Bun runtime requires kind, source, entrypoint and package")
    for name in ("source", "entrypoint"):
        value = spec[name]
        if (not isinstance(value, str) or not value or "\\" in value or ":" in value
                or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts
                or any(p in BUN_GENERATED for p in PurePosixPath(value).parts)
                or str(PurePosixPath(value)) != value):
            raise ValueError(f"Bun runtime {name} must be a normalized owned relative path")
    package = spec["package"]
    if (not isinstance(package, str) or not package or package.startswith("-")
            or any(c.isspace() for c in package) or any(c in package for c in "*[]!\\")):
        raise ValueError("Bun runtime package must name one workspace package")
    return dict(spec)


def has_bun_runtime(root: Path) -> bool:
    spec = runtime_spec(root)
    return spec is not None and spec["kind"] == "bun"


def has_python_runtime(root: Path) -> bool:
    spec = runtime_spec(root)
    return spec is not None and spec["kind"] == "python"


def copy_harness(source, target, **kwargs):
    """Keep managed source separate from dependencies and generated state."""
    spec = runtime_spec(Path(source))
    if spec is not None:
        return _copy_source_only(source, target, python=spec["kind"] == "python", **kwargs)
    return shutil.copytree(source, target, **kwargs)


def copy_rejected_harness(source, target, **kwargs):
    """Preserve a rejected candidate as evidence, without runtime admission.

    Its manifest may not parse. Use the conservative source-only policy even
    then, retaining symlinks rather than dereferencing method-supplied targets.
    Callers must keep this tree unmeasured; normal copies remain strict.
    """
    return _copy_source_only(source, target, python=True, **kwargs)


def _copy_source_only(source, target, *, python=False, **kwargs):
    previous = kwargs.get("ignore")
    kwargs["ignore"] = lambda directory, names: (
        set(names) & (PYTHON_GENERATED if python else BUN_GENERATED)
    ) | (
        {name for name in names if name.endswith(PYTHON_BYTECODE)} if python else set()
    ) | (set(previous(directory, names)) if previous else set())
    kwargs["symlinks"] = True
    return shutil.copytree(source, target, **kwargs)
