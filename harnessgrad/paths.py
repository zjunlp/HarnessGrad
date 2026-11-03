"""Portable external dataset locations, shared by adapters and preparation tools.

Paths are resolved without creating directories or importing dataset adapters.
The main checkout's .env is shared by linked worktrees; a worktree-local .env may
override it, and process environment variables take precedence over both files.
Relative paths always use the main checkout as their base, independent of cwd.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
import re
from pathlib import Path

from harnessgrad.env_file import read_env_file

CHECKOUT = Path(__file__).resolve().parents[1]
_SCORER_PATHS_ACTIVE = ContextVar("harnessgrad_scorer_paths", default=False)
# A trusted source file cannot also be a dataset directory. This placeholder
# keeps adapter imports independent of host storage and task-created corpus files.
_SCORER_DATA_ROOT = Path(__file__) / "datasets"


@contextmanager
def scorer_paths():
    """Disable storage discovery for the duration of a trusted scoring call.

    The check enters this context before importing its adapter. No environment
    variable enables it, and no .env, .git, home or dataset directory is discovered.
    Adapters still import their original scoring code, but cannot load a corpus
    through these paths. Host loading/preparation keeps its normal configuration.
    """
    token = _SCORER_PATHS_ACTIVE.set(True)
    try:
        yield
    finally:
        _SCORER_PATHS_ACTIVE.reset(token)


def main_checkout(checkout: Path | None = None) -> Path:
    root = (checkout or CHECKOUT).resolve()
    if _SCORER_PATHS_ACTIVE.get():
        return root
    marker = root / ".git"
    if marker.is_file():
        value = marker.read_text(encoding="utf-8").strip()
        if value.startswith("gitdir:"):
            git_dir = Path(value.partition(":")[2].strip())
            if not git_dir.is_absolute():
                git_dir = root / git_dir
            common = git_dir / "commondir"
            if common.is_file():
                common_dir = (git_dir / common.read_text().strip()).resolve()
                if common_dir.name == ".git":
                    return common_dir.parent
    return root


def _settings() -> dict[str, str]:
    if _SCORER_PATHS_ACTIVE.get():
        return {}
    primary = main_checkout()
    values = read_env_file(primary / ".env")
    if CHECKOUT.resolve() != primary:
        values.update(read_env_file(CHECKOUT / ".env"))
    values.update(os.environ)
    return values


def _absolute(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = main_checkout() / path
    return path.resolve()


def configured_path(variable: str, default: Path | DatasetPath) -> Path | DatasetPath:
    """Resolve an optional path override from the same .env/environment settings."""
    if _SCORER_PATHS_ACTIVE.get():
        return Path(default)
    value = _settings().get(variable, "").strip()
    if not value and isinstance(default, DatasetPath):
        return default
    return _absolute(value or default)


class DataRootNotConfigured(ValueError):
    """External storage needs an explicit location before it can be accessed."""


def _missing(directory: str | None = None) -> DataRootNotConfigured:
    override = f" or {env_name(directory)} for this dataset" if directory else ""
    return DataRootNotConfigured(
        f"Dataset storage is not configured. Set HG_DATA_ROOT in the environment or .env"
        f"{override} (for example: HG_DATA_ROOT=/path/to/datasets).")


class DatasetPath(os.PathLike):
    """An unconfigured declaration, resolved only when used as a filesystem path.

    This is not a fallback directory. Joins remain declarations, while filesystem
    operations and conversion to Path/string require explicit storage configuration.
    """

    def __init__(self, directory: str, parts: tuple = ()):
        self.directory = directory
        self._parts = parts

    def __truediv__(self, part):
        return DatasetPath(self.directory, (*self._parts, part))

    def resolve(self, *args, **kwargs) -> Path:
        root = dataset_root(self.directory)
        if isinstance(root, DatasetPath):
            raise _missing(self.directory)
        return root.joinpath(*self._parts).resolve(*args, **kwargs)

    def __fspath__(self):
        return os.fspath(self.resolve())

    def __str__(self):
        return str(self.resolve())

    def __repr__(self):
        return f"DatasetPath({self.directory!r}, {self._parts!r}; configuration required)"

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.resolve(), name)


def _base(values: dict[str, str]) -> Path | None:
    value = values.get("HG_DATA_ROOT", "").strip()
    return _absolute(value) if value else None


def datasets_root(*, required: bool = True) -> Path | None:
    """The effective shared directory, also recorded in the run configuration."""
    if _SCORER_PATHS_ACTIVE.get():
        return _SCORER_DATA_ROOT
    root = _base(_settings())
    if root is None and required:
        raise _missing()
    return root


def env_name(directory: str) -> str:
    """The sole per-dataset spelling: HG_DATA_<normalized directory name>."""
    return "HG_DATA_" + re.sub(r"[^0-9A-Za-z]+", "_", directory).strip("_").upper()


def dataset_root(directory: str) -> Path | DatasetPath:
    """Per-dataset override > HG_DATA_ROOT/directory; no implicit storage directory.

    `directory` preserves the existing on-disk layout (including shared directories
    and version subdirectories). Retired variable spellings are not read.
    """
    relative = Path(directory)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("dataset directory must be a nonempty relative path without '..'")
    if _SCORER_PATHS_ACTIVE.get():
        return _SCORER_DATA_ROOT / relative
    values = _settings()
    override = values.get(env_name(directory), "").strip()
    base = _base(values) if not override else None
    if not override and base is None:
        return DatasetPath(directory)
    root = (_absolute(override) if override else base / relative).resolve()
    for platform in {CHECKOUT.resolve(), main_checkout()}:
        if root.is_relative_to(platform):
            raise ValueError(f"Dataset directory {root} must be outside the platform checkout {platform}")
    return root
