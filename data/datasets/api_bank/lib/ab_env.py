r"""The vendored API-Bank code, loaded lazily and with its absent dependencies stubbed.

Why anything has to be vendored at all
--------------------------------------
API-Bank's grade is decided by **executing the API the agent asked for** and comparing the
result with the recorded one (`evaluator.py::Evaluator.evaluate`). The APIs are ordinary Python
classes in the dataset's repository, and their `check_api_call_correctness` is a plain `dict`
comparison -- so grading needs no model, no API key and no network. It does need the classes.

Why a loader rather than `import tool_manager`
----------------------------------------------
Measured: `ToolManager.__init__` walks `apis/*.py` and calls `importlib.import_module` on each
file, and the very first unimportable module aborts the whole manager:

    File "tool_manager.py", line 17, in __init__
      importlib.import_module(f'{basename}.{api_file}')
    File "apis/translate.py", line 3, in <module>
    ModuleNotFoundError: No module named 'googletrans'

`apis/__init__.py`'s own `try/except ImportError` does **not** cover this: it guards the
package's import of each module, and this failure happens while `apis` itself is initializing.

Across the 50 APIs the samples actually use, the absent third-party packages are exactly three --
`googletrans`, `nltk`, `rank_bm25` -- and the alternatives to stubbing them are both worse:
`pip install` puts three packages into the interpreter the whole test suite runs on for five APIs
out of fifty, and hand-picking "the APIs that import cleanly" silently drops tasks.

So: `_ensure_stubs()` registers a module-like placeholder for each absent package *before*
`tool_manager` is imported, and `api_class()` resolves a class from the loaded registry. A class
whose **call** touches a stub raises `AttributeError`, which the check maps to an *unmeasured*
task with the reason named -- never a 0 (§19's distinction, and §5's).
"""
from __future__ import annotations

import re
import sys
import tarfile
import types
from pathlib import Path

#: Packages the vendored APIs import that this host does not have. Each is replaced by a
#: placeholder whose attribute access raises `StubUsed`, so the failure names the dependency
#: instead of surfacing as a bare `AttributeError` from inside someone else's library.
ABSENT_PACKAGES = ("googletrans", "nltk", "rank_bm25")

#: Where the vendored tree is unpacked. Stable, so `ToolManager`'s relative `./apis` and
#: `./init_database` paths work: it is called with the *cwd* set to the checkout.
VENDOR_DIRNAME = "vendor"

TARBALL_NAME = "api-bank-vendor.tar.gz"


class StubUsed(RuntimeError):
    """A vendored API tried to use one of the stubbed, absent packages."""


def _make_stub(name: str) -> types.ModuleType:
    """A module whose every attribute is a placeholder that raises `StubUsed` when *used*.

    The placeholder is a **class with module metadata**, and that detail is measured: it is
    merged into the `apis` package namespace by `apis/__init__.py`'s `globals().update(...)`
    loop, and `inspect.getmodule` then asks it for `__file__`. A bare class raises
    `AttributeError: type object '_Unavailable' has no attribute 'endswith'` from inside
    `inspect`, which aborts the whole import -- the failure mode this module exists to avoid.
    """
    module = types.ModuleType(name)

    class _Unavailable:
        __module__ = name
        __qualname__ = "_Unavailable"
        __file__ = f"<{name} (stub)>"

        def __init__(self, *args, **kwargs) -> None:
            raise StubUsed(
                f"the vendored API-Bank code needs the {name!r} package, which is not "
                f"installed on this host; tasks that call it are reported as *unmeasured* "
                f"rather than as a 0 the agent earned")

        def __getattr__(self, item):
            raise StubUsed(f"{name}.{item} is unavailable: {name!r} is not installed")

    module._Unavailable = _Unavailable
    module.__getattr__ = lambda item: _Unavailable  # type: ignore[attr-defined]
    return module


def missing_packages() -> list[str]:
    """Which of the packages the vendored APIs import are **absent** on this host.

    Reported by `provision_status()` and by the check, because a task whose API module did not
    import is *unmeasurable*, and that has to be visible rather than scored 0.
    """
    absent = []
    for name in ABSENT_PACKAGES:
        try:
            __import__(name)
        except ImportError:
            absent.append(name)
    return absent


def vendor_dir(root: Path) -> Path:
    return root / VENDOR_DIRNAME


def unpack(root: Path) -> Path:
    """Unpack the vendored tree once, under `root`. Returns the checkout directory.

    The tarball's members are all under `api-bank/`, so the checkout is `root/vendor/api-bank`.
    Idempotent -- the harness and the check both call it.
    """
    target = vendor_dir(root)
    checkout = target / "api-bank"
    if (checkout / "tool_manager.py").is_file():
        return checkout
    tarball = root / "_src" / TARBALL_NAME
    if not tarball.is_file():
        raise FileNotFoundError(
            f"no vendored API-Bank tree at {tarball}; provision it with "
            f"`python3 tools/datasets.py --fetch --dataset api_bank`")
    target.mkdir(parents=True, exist_ok=True)
    base = target.resolve()
    with tarfile.open(tarball, "r:gz") as archive:
        for member in archive.getmembers():
            destination = (base / member.name).resolve()
            if not str(destination).startswith(str(base) + "/"):
                raise RuntimeError(f"refusing to extract {member.name!r} outside {base}")
        archive.extractall(base)
    if not (checkout / "tool_manager.py").is_file():
        raise RuntimeError(f"the vendored tarball did not contain api-bank/tool_manager.py")
    return checkout


class ApiBank:
    """The vendored APIs, instantiated exactly as `ToolManager` instantiated them.

    **Not** by calling `ToolManager`. Its `__init__` imports every file under `apis/`, so one
    unimportable module aborts the manager (measured: `googletrans`), and stubbing that module
    then breaks `ToolManager`'s own `inspect.getmodule` walk (measured: the placeholder's class
    has no `__file__`). The parts that matter are small, so they are reproduced here from
    `tool_manager.py`:

        init_tool:   `cls(init_database)` when the class names a `database_name` the release
                     ships, plus the `CheckToken` instance when the class takes a `token`
        api_call:    coerce each argument by the class's declared `input_parameters` type
                     (`int`, `float`, `bool`, `str`, and the two list shapes; anything else
                     raises `invalid parameter type.`, as upstream does), then call `tool.call`

    That is the whole of the semantics a grade depends on, and reproducing it keeps the loader
    independent of the one file whose import list is the problem.
    """

    def __init__(self, root: Path, *, used: list[str] | None = None) -> None:
        self.root = root
        self.checkout = unpack(root)
        self.stubbed = missing_packages()
        self.databases = _load_databases(self.checkout / "init_database")
        self.classes: dict[str, type] = {}
        self.unavailable: dict[str, str] = {}
        self._inited: dict[str, object] = {}
        self._load(used)

    # ---------------------------------------------------------------- loading ---

    def _module_files(self, used: list[str] | None) -> list[Path]:
        """The `apis/*.py` files to load. `used` narrows to the classes the samples name."""
        apis_dir = self.checkout / "apis"
        skip = {"__init__.py", "api.py", "tool_search.py"}
        files = [p for p in sorted(apis_dir.glob("*.py")) if p.name not in skip]
        if not used:
            return files
        wanted = set(used)
        keep = []
        for path in files:
            source = path.read_text(errors="replace")
            if any(re.search(rf"class\s+{re.escape(name)}\s*\(\s*API\s*\)", source)
                   for name in wanted):
                keep.append(path)
        return keep

    def _load(self, used: list[str] | None) -> None:
        """Import each `apis/<stem>.py` **through the package**, one at a time.

        Through the package, not by file path under a synthetic module name. Measured, the
        synthetic-name version (a) re-executes every transitive import under a fresh name --
        which re-imports `torch` and dies on `RuntimeError: Only a single TORCH_LIBRARY can be
        used to register the namespace prims` -- and (b) breaks the modules' own relative
        imports. `apis.<stem>` is the name they are written for and it is cached like any import.

        A module that will not import is recorded in `unavailable` with its exception rather
        than silently skipped: a task whose API did not load must be reported as unmeasured, and
        that needs the reason.
        """
        import importlib
        import os

        previous = os.getcwd()
        os.chdir(self.checkout)
        sys.path.insert(0, str(self.checkout))
        try:
            # `apis/__init__.py` imports every module in the directory in a loop whose
            # `try/except ImportError` is *inside* the package's own initialization, so a module
            # that raises while being imported **aborts the package** and leaves `API` undefined
            # (measured: `translate.py` -> `googletrans`). Importing the base class by its own
            # path first makes the package's failure recoverable instead of fatal.
            importlib.import_module("apis.api")
            try:
                importlib.import_module("apis")
            except Exception as exc:  # noqa: BLE001 - the package's own sweep is best-effort
                self.unavailable["__package__"] = f"{type(exc).__name__}: {exc}"
            for path in self._module_files(used):
                try:
                    importlib.import_module(f"apis.{path.stem}")
                except Exception as exc:  # noqa: BLE001 - named, not swallowed
                    self.unavailable[path.stem] = f"{type(exc).__name__}: {exc}"
                    continue
            for value in list(vars(sys.modules["apis"]).values()):
                if isinstance(value, type) and _is_api_subclass(value):
                    self.classes[value.__name__] = value
        finally:
            os.chdir(previous)
            if str(self.checkout) in sys.path:
                sys.path.remove(str(self.checkout))

    # ------------------------------------------------------------------- use ---

    def api_class(self, name: str):
        return self.classes.get(name)

    def description(self, name: str):
        cls = self.api_class(name)
        if cls is None:
            return None
        return {
            "name": name,
            "description": getattr(cls, "description", ""),
            "input_parameters": getattr(cls, "input_parameters", {}),
            "output_parameters": getattr(cls, "output_parameters", {}),
        }

    def _init_tool(self, name: str):
        """`ToolManager.init_tool`, reproduced: the database first, then the token checker."""
        if name in self._inited:
            return self._inited[name]
        cls = self.api_class(name)
        if cls is None:
            raise KeyError(f"no API class named {name!r} in the vendored tree")
        args = []
        database_name = getattr(cls, "database_name", None)
        if database_name and database_name in self.databases:
            args.append(self.databases[database_name])
        inputs = getattr(cls, "input_parameters", {}) or {}
        if name != "CheckToken" and "token" in inputs:
            args.append(self._init_tool("CheckToken"))
        tool = cls(*args)
        self._inited[name] = tool
        return tool

    def api_call(self, name: str, param_dict: dict) -> dict:
        """`ToolManager.api_call`, reproduced: coerce by declared type, then call.

        The coercion set is upstream's, branch for branch (`tool_manager.py:150-169`):

            int          `int(value)`
            float        `float(value)`
            bool         `value == 'True'`  -- a *string* comparison, so "True" is True and
                         every other value, including "False", "false", "", and a real bool,
                         is False. The gold rows store a JSON boolean for these
                         (`TimedSwitch`), so this branch decides a grade.
            str          unchanged
            list(str)    unchanged
            list         unchanged
            anything else
                         `raise Exception('invalid parameter type.')`, as upstream does. No
                         declared type in the vendored tree falls outside the set above
                         (`str` 121, `list(str)` 4, `int` 4, `list` 3, `bool` 1), so this
                         branch cannot cost a sample.

        One difference predates this fix and is left alone: upstream asserts that a *string*
        `int`/`float` is digit-shaped (`isdigit`) before converting, while this helper calls
        `int()`/`float()` directly -- so `'-7'` and `' 42 '` are accepted here and rejected
        there. That can only turn a raise into a comparison, never the reverse, and no gold row
        carries a non-digit string for a declared `int` or `float`.
        """
        info = self.description(name)
        if info is None:
            raise KeyError(f"no API class named {name!r} in the vendored tree")
        declared = info["input_parameters"] or {}
        processed = {}
        for key, value in (param_dict or {}).items():
            if key not in declared:
                raise AssertionError(f"invalid parameter name: {key!r}")
            kind = declared[key].get("type")
            if kind == "int":
                processed[key] = int(value)
            elif kind == "float":
                processed[key] = float(value)
            elif kind == "bool":
                processed[key] = value == "True"
            elif kind in ("str", "list(str)", "list"):
                processed[key] = value
            else:
                raise Exception("invalid parameter type.")
        tool = self._init_tool(name)
        return tool.call(**processed)


def _is_api_subclass(value) -> bool:
    from apis import API  # noqa: PLC0415 - imported after `sys.path` is prepared

    return isinstance(value, type) and issubclass(value, API) and value is not API


def _load_databases(directory: Path) -> dict:
    import json

    out = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        out[path.stem] = json.loads(path.read_text())
    return out


__all__ = ["ApiBank", "StubUsed", "unpack", "vendor_dir", "ABSENT_PACKAGES",
           "TARBALL_NAME", "VENDOR_DIRNAME"]
