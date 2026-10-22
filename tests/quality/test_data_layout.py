"""Acceptance contract for the completed data split (Stages A/C/D).

Only source and filesystem metadata are read. Registry discovery receives literal
KIND declarations in place of adapter imports; task pools are never opened.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DATASETS = DATA / "datasets"
SCAFFOLDS = DATA / "scaffolds"

# Independent, reviewed inventory: do not derive this expectation from discovery.
EXPECTED_DATASETS = (
    "agentdojo", "aime", "alfworld", "api_bank", "appworld", "bfcl_v3",
    "bird_minidev", "chessbench_puzzles", "cooperbench", "costbench", "costbench_dynamic", "ds1000", "fever", "gaia",
    "gsm8k", "hotpotqa", "jericho", "kumo", "livecodebench", "locomo",
    "memoryagentbench", "musique", "nestful", "personamem_v2",
    "physicsquestions", "reasoning_gym", "scienceworld", "sotuqa",
    "sportsunderstanding", "spreadsheetbench", "strategyqa", "strongreject", "tau2_bench",
    "tau3_bench", "tau_bench", "terminal_bench", "triviaqa", "twowikimultihopqa",
)
FIXTURES = (
    "demo.py", "probe_set.py", "broken_service.py", "verify_demo.py",
    "verify_exec.py", "verify_service.py",
)
# Reviewed adapter assets stay beside their declarations.
# unused-inventory is not a published adapter and is deliberately not classified here.
EXPECTED_ASSET_DIRS = (
    "agentdojo", "alfworld", "api_bank", "appworld", "bird_minidev", "cooperbench", "costbench", "costbench_dynamic", "ds1000",
    "gaia", "jericho", "kumo", "livecodebench", "locomo", "memoryagentbench", "nestful",
    "personamem_v2", "reasoning_gym",
    "scienceworld", "spreadsheetbench", "strongreject", "tau3_bench", "tau_bench",
)


def _relative(path):
    return path.relative_to(ROOT).as_posix()


def _source(path):
    assert path.is_file(), f"Missing source file: {_relative(path)}"
    text = path.read_text(encoding="utf-8")
    return text, ast.parse(text, filename=_relative(path))


def _kind(path, *, require_declaration=False):
    """Read declarations for every adapter, including AgentDojo, without imports."""
    _, tree = _source(path)
    kind = None if require_declaration else "fixture"
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign) else
                   [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(target, ast.Name) and target.id == "KIND"
               for target in targets):
            try:
                kind = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                raise AssertionError(
                    f"{_relative(path)}:{node.lineno}: KIND must be literal") from None
    return kind


def _check(errors):
    assert not errors, "\n" + "\n".join(errors)


def test_scaffolds_contain_shared_code_and_no_adapters():
    errors = []
    for relative in ("", "qa", "command_oracle_harness"):
        path = SCAFFOLDS / relative
        if not path.is_dir():
            errors.append(f"Missing scaffold directory: {_relative(path)}/")
    for relative in ("registry.py", "command_control.py", "qa/base.py",
                     "command_oracle_harness/agent.py",
                     "command_oracle_harness/harness.json"):
        path = SCAFFOLDS / relative
        if not path.is_file():
            errors.append(f"Missing shared scaffold: {_relative(path)}")
    for path in sorted(SCAFFOLDS.rglob("*.py")):
        if path.stem in EXPECTED_DATASETS or _kind(path) == "dataset":
            errors.append(f"Dataset adapter inside scaffolds: {_relative(path)}")
    for name in EXPECTED_DATASETS:
        if (SCAFFOLDS / name).exists():
            errors.append(f"Adapter assets inside scaffolds: data/scaffolds/{name}/")
    _check(errors)


def test_reviewed_adapters_live_in_datasets():
    errors = []
    if not DATASETS.is_dir():
        errors.append("Missing adapter directory: data/datasets/")
    expected = {f"{name}.py" for name in EXPECTED_DATASETS}
    actual = {path.name for path in DATASETS.glob("*.py")
              if path.name != "__init__.py"}
    for name in sorted(expected - actual):
        errors.append(f"Missing adapter: data/datasets/{name}")
    for name in sorted(actual - expected):
        errors.append(f"Unexpected adapter/module: data/datasets/{name}")
    for name in sorted(expected & actual):
        path = DATASETS / name
        if _kind(path, require_declaration=True) != "dataset":
            errors.append(f"{_relative(path)} must declare KIND = 'dataset'")
    _check(errors)


def test_registry_publishes_exactly_the_reviewed_datasets_without_loading_adapters(monkeypatch):
    path = SCAFFOLDS / "registry.py"
    assert path.is_file(), f"Missing registry: {_relative(path)}"
    errors = []
    real_import = importlib.import_module

    def declared_module(name, package=None):
        if name.startswith("."):
            name = importlib.util.resolve_name(name, package)
        if name != "data" and not name.startswith("data."):
            return real_import(name, package)
        source = ROOT.joinpath(*name.split(".")).with_suffix(".py")
        if not source.is_file():
            errors.append(f"{_relative(path)} imports missing module {name!r}: "
                          f"{_relative(source)}")
            return SimpleNamespace(KIND="fixture")
        # Return metadata only, even if discovery asks for a fixture or a shim.
        return SimpleNamespace(KIND=_kind(source))

    # Load only the registry, in isolation from cached production modules. Patch
    # before execution so import_module cannot execute an adapter at any point.
    registry = ModuleType("_data_layout_registry")
    registry.__file__ = str(path)
    with monkeypatch.context() as patch:
        patch.setattr(importlib, "import_module", declared_module)
        # Compile source directly: a file loader could write __pycache__ in data/.
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"),
             registry.__dict__)
        published = tuple(registry.published())
    _check(errors)
    assert published == EXPECTED_DATASETS, (
        f"{_relative(path)} published {published!r}; expected exactly "
        f"{EXPECTED_DATASETS!r}")


def test_fixtures_stay_at_the_data_top_level():
    errors = []
    for name in FIXTURES:
        if not (DATA / name).is_file():
            errors.append(f"Missing top-level fixture: data/{name}")
        if (DATASETS / name).exists():
            errors.append(f"Fixture incorrectly moved to data/datasets/{name}")
    _check(errors)


def test_flat_paths_and_migration_shims_are_gone():
    old = [DATA / name for name in
           ("qa", "registry.py", "command_control.py", "command_oracle_harness")]
    old.extend(DATA / f"{name}.py" for name in EXPECTED_DATASETS)
    _check([f"Legacy path/shim must be removed: {_relative(path)}"
            for path in old if path.exists() or path.is_symlink()])


def test_adapter_imports_do_not_cross_dataset_boundaries():
    """An adapter may reach `data.scaffolds.*`; it may not reach another adapter.

    Deliberately narrower than "any import whose root is a local name", which is what
    this check first did and why it reported four false violations. Three of them were
    **third-party packages that happen to share a dataset's name** -- `jericho`,
    `reasoning_gym` and `tau_bench` are upstream distributions the adapters legitimately
    import -- and the fourth was `eval.policy.appworld_reporting`, a `data -> eval` edge
    that `tests/quality/test_import_direction.py` governs with a reviewed exception list.
    A root-name rule cannot tell those apart from an adapter importing a sibling
    adapter, so it tested the wrong thing.

    What this pins is the boundary the split exists to create: no adapter imports
    another adapter. Shared `data.scaffolds.*` and `data.runtimes.*` plus an
    adapter's own private modules under `data.datasets.<name>.*` are permitted.
    """
    errors = []
    for name in EXPECTED_DATASETS:
        path = DATASETS / f"{name}.py"
        if not path.is_file():
            errors.append(f"Cannot check adapter imports: missing {_relative(path)}")
            continue
        text, tree = _source(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    if node.level > 2:
                        base = "<relative import beyond data>"
                    else:
                        parts = ["data", "datasets"][:3 - node.level]
                        base = ".".join(parts + ([base] if base else []))
                targets = [f"{base}.{alias.name}" for alias in node.names]
            else:
                continue
            for target in targets:
                statement = ast.get_source_segment(text, node)
                if target.startswith("data.datasets."):
                    other = target.split(".")[2]
                    if other == name:
                        # Dataset-private helpers are part of this adapter, not a sibling.
                        continue
                    errors.append(
                        f"{_relative(path)}:{node.lineno}: {statement} "
                        f"[forbidden dependency: adapter {name!r} imports adapter {other!r}]")
                elif (target.startswith("data.")
                      and not target.startswith(("data.scaffolds.", "data.runtimes."))):
                    # `data.runtimes` is pure shared platform data (sealed-runtime pins), the same
                    # category as `data.scaffolds`; adapters may import either, nothing else under data.
                    errors.append(
                        f"{_relative(path)}:{node.lineno}: {statement} "
                        f"[forbidden dependency: {target} is not data.scaffolds.* or data.runtimes.*]")
    _check(errors)


def test_adapter_asset_directories_move_beside_their_adapters():
    errors = []
    for name in EXPECTED_ASSET_DIRS:
        if not (DATASETS / name).is_dir():
            errors.append(f"Missing relocated adapter assets: data/datasets/{name}/")
    for name in EXPECTED_DATASETS:
        path = DATA / name
        if path.exists() or path.is_symlink():
            errors.append(f"Orphaned top-level adapter assets: {_relative(path)}/; "
                          f"move beside data/datasets/{name}.py")
    for path in sorted(DATASETS.glob("*")):
        if path.is_dir() and path.name != "__pycache__":
            adapter = DATASETS / f"{path.name}.py"
            if not adapter.is_file():
                errors.append(f"Orphaned adapter assets: {_relative(path)}/; "
                              f"missing {_relative(adapter)}")
    _check(errors)
