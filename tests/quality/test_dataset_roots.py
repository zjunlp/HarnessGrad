"""Portable adapter declarations and the retained root-diagnostic utility.

Adapters must call harnessgrad.paths.dataset_root and must not carry executable
host-path literals. The actual storage precedence, .env and worktree behavior are
covered by test_data_paths.py. Tests of the older roots utility below describe its
diagnostic API, which shares the same explicit configuration rules.

Provisioned-data loading and the independently pinned BFCL source row remain
integration checks; neither is replaced by a source-spelling assertion.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds import registry, roots  # noqa: E402

DATA = ROOT / "data"
CONTAIN = ROOT / "tests" / "fixtures" / "hg_bfclcontain"
HOST_PATH = re.compile(
    r"(?<![\w/])/(?:mnt|home|Users|media|Volumes|scratch)/[^\s/]+"
    r"|(?i:\b[A-Z]:[\\/]Users[\\/][^\\/\s]+)"
)
DEFAULT_COLLECTION = Path(".local/share/harnessgrad/datasets")

#: Retired variables are not aliases. Clear them along with current storage variables
#: so inherited server configuration cannot affect the fresh-clone checks.
OLD_VARIABLES = (
    "HG_ALFWORLD_DATA", "HG_API_BANK_DATA", "HG_COOPERBENCH_DATA", "HG_COSTBENCH_DATA",
    "HG_COSTBENCH_RUNTIME", "HG_JERICHO_DATA", "HG_KUMO_DATA", "HG_LOCOMO_DATA",
    "HG_MAB_DATA", "HG_MAB_PREPARED", "HG_PERSONAMEM_V2_DATA", "HG_SCIENCEWORLD_DATA",
    "HG_SPREADSHEETBENCH_DATA", "HG_STRONGREJECT_DATA", "HG_TAU2_DATA", "HG_TAU3_DATA",
    "HG_TAU3_RUNTIME", "HG_TERMINAL_BENCH", "HG_SEALED_JAVA_HOME",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, request):
    """Every test starts from "nothing configured", the fresh-clone case."""
    if request.node.name == "test_a_provisioned_dataset_still_loads_tasks":
        return
    for key in list(os.environ):
        if key.startswith("HG_DATA_") or key in OLD_VARIABLES:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def roots_checkout(tmp_path, monkeypatch):
    from harnessgrad import paths
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(paths, "CHECKOUT", checkout)
    return checkout


def test_precedence_per_dataset_then_collection_then_unconfigured(roots_checkout, tmp_path, monkeypatch):
    from harnessgrad.paths import DataRootNotConfigured
    collection = tmp_path / "collection"
    override = tmp_path / "override"
    monkeypatch.setenv("HG_DATA_ROOT", str(collection))
    monkeypatch.setenv("HG_DATA_GSM8K", str(override))
    assert roots.dataset_root("gsm8k") == override
    monkeypatch.delenv("HG_DATA_GSM8K")
    assert roots.dataset_root("gsm8k") == collection / "gsm8k"
    monkeypatch.delenv("HG_DATA_ROOT")
    with pytest.raises(DataRootNotConfigured, match="Set HG_DATA_ROOT"):
        roots.dataset_root("gsm8k").resolve()


def test_existing_old_directories_never_supply_an_implicit_default(roots_checkout, tmp_path, monkeypatch):
    from harnessgrad.paths import DataRootNotConfigured
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / DEFAULT_COLLECTION / "gsm8k").mkdir(parents=True)
    (roots_checkout.parent / "datasets/gsm8k").mkdir(parents=True)
    with pytest.raises(DataRootNotConfigured, match="Set HG_DATA_ROOT"):
        roots.dataset_root("gsm8k").is_dir()


def test_describe_reports_configured_and_unconfigured_paths(roots_checkout, tmp_path, monkeypatch):
    unset = roots.describe("gsm8k")
    assert unset["root"] is None and unset["source"] == "unconfigured"
    assert not unset["exists"] and "Set HG_DATA_ROOT" in unset["error"]
    monkeypatch.setenv("HG_DATA_ROOT", str(tmp_path / "collection"))
    assert roots.describe("gsm8k") == {"name": "gsm8k",
        "root": str(tmp_path / "collection/gsm8k"), "source": "HG_DATA_ROOT", "exists": False}
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv("HG_DATA_GSM8K", str(override))
    assert roots.describe("gsm8k")["source"] == "HG_DATA_GSM8K"
    assert roots.describe("gsm8k")["exists"]
    assert roots.describe("gsm8k", tmp_path / "other")["source"] == "explicit"


def _code_constants(path: Path):
    """String constants in executable positions, with their parent nodes.

    Docstrings are `Expr(Constant)` and are skipped on purpose: prose may quote
    historical paths, code may not. Comments never reach the AST at all.
    Container roots such as /opt/hg-python and /root/.cargo are not host homes.
    """
    tree = ast.parse(path.read_bytes(), filename=str(path))
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and HOST_PATH.search(node.value)
                and not isinstance(parent.get(node), ast.Expr)):
            yield node, parent


@pytest.mark.parametrize("literal", [
    "/mnt/example-volume/example-user/datasets/pool.json",
    "/home/example-user/data/pool.json",
    "/Users/example-user/data/pool.json",
    "/media/example-volume/datasets/pool.json",
    "/Volumes/example-volume/datasets/pool.json",
    "/scratch/example-user/datasets/pool.json",
    r"C:\Users\example-user\data\pool.json",
    "C:/Users/example-user/data/pool.json",
    "cat /home/example-user/data/pool.json",
])
def test_code_constants_reject_machine_directories(tmp_path, literal):
    source = tmp_path / "adapter.py"
    source.write_text(f"pool = {literal!r}\n")
    assert [node.value for node, _ in _code_constants(source)] == [literal]


def test_code_constants_preserve_prose_and_container_paths(tmp_path):
    source = tmp_path / "adapter.py"
    source.write_text('''\
"""Historical /home/example-user/data location."""
# Historical /mnt/example-volume/example-user/datasets location.
python = "/opt/hg-python/bin/python3.12"
cargo = "/root/.cargo/bin/cargo"
pool = "/task/pool.json"
relative = "mnt/example-volume/datasets"
url = "https://example.invalid/home/example-user/data"
''')
    assert list(_code_constants(source)) == []


def _portable_root_calls(tree) -> set[str]:
    """Names actually bound to the canonical resolver, including import aliases."""
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module == "harnessgrad.paths":
            calls.update(alias.asname or alias.name for alias in node.names
                         if alias.name == "dataset_root")
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module == "harnessgrad":
            calls.update(f"{alias.asname or alias.name}.dataset_root" for alias in node.names
                         if alias.name == "paths")
        elif isinstance(node, ast.Import):
            calls.update(f"{alias.asname or alias.name}.dataset_root" for alias in node.names
                         if alias.name == "harnessgrad.paths")
    return calls


def test_no_data_module_assigns_a_host_path_to_dataset_root():
    """Require the canonical resolver and reject host literals in executable code."""
    problems = []
    for path in sorted(DATA.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_bytes(), filename=relative)
        helper_calls = _portable_root_calls(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = {node.target.id}
            else:
                continue
            if "DATASET_ROOT" in targets or (path.name == "terminal_bench.py" and "ROOT" in targets):
                value = ast.unparse(node.value)
                if not (isinstance(node.value, ast.Call)
                        and ast.unparse(node.value.func) in helper_calls
                        and len(node.value.args) == 1 and not node.value.keywords):
                    problems.append(f"{relative}:{node.lineno}: dataset root is not a "
                                    f"canonical helper call: {value}")
                else:
                    directory = node.value.args[0]
                    if not (isinstance(directory, ast.Constant) and isinstance(directory.value, str)
                            and Path(directory.value).parts and not Path(directory.value).is_absolute()
                            and ".." not in Path(directory.value).parts):
                        problems.append(f"{relative}:{node.lineno}: dataset directory must be "
                                        f"a relative literal: {value}")
        for node, _ in _code_constants(path):
            problems.append(f"{relative}:{node.lineno}: executable host path: {node.value!r}")
    assert not problems, "\n".join(problems)


def _provisioned():
    """`gsm8k` when it is on this machine, otherwise the first dataset that is."""
    order = ["gsm8k"] + [name for name in registry.published() if name != "gsm8k"]
    for name in order:
        try:
            module = importlib.import_module(registry.module_name(name))
        except (ImportError, ValueError):
            continue
        status = getattr(module, "provision_status", None)
        if not callable(status):
            continue
        try:
            report = status()
        except Exception:  # noqa: BLE001 - a broken status is reported elsewhere
            continue
        if report.get("present"):
            return name, module
    return None, None


def test_a_provisioned_dataset_still_loads_tasks():
    """Resolution after the migration still finds the provisioned bytes."""
    name, module = _provisioned()
    if name is None:
        pytest.skip("no dataset is provisioned on this machine")
    root = Path(getattr(module, "DATASET_ROOT", getattr(module, "ROOT", "")))
    assert root.is_dir(), f"{name}: resolved root {root} is not a directory"
    tasks, _ = module.load()
    assert len(tasks) > 0, f"{name}: load() returned no tasks from {root}"


def test_the_bfclcontain_inventory_row_for_the_migrated_adapter_matches_disk():
    """One pinned row, recomputed from the file that changed: the pin is not stale."""
    rows = json.loads((CONTAIN / "source-inventory.json").read_text())
    row = next(row for row in rows if row["path"] == "data/datasets/bfcl_v3.py")
    raw = (ROOT / row["path"]).read_bytes()
    assert row["bytes"] == len(raw)
    assert row["sha256"] == hashlib.sha256(raw).hexdigest()
