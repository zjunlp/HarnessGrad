"""One resolution helper, four rules, and no adapter that can smuggle a host path back.

Why this file exists
--------------------
The release blocker was measured, not suspected: every adapter spelled the operator's
absolute data root, and `tools/datasets.py` resolved `--fetch`'s destination from that
same attribute, so a clone could neither fetch nor load anything. `data/scaffolds/roots.py`
is the single answer now. A helper with a fallback order is only worth what its worst
case does, so these tests pin each rule separately, the rule that must *not* win (a stale
literal), the diagnostic that says which rule won, and the absence of the old spelling
in every `data/**/*.py` -- statically, with `ast`, not by grep, because a docstring is
allowed to quote the literal and an assignment is not.

The last two tests are the other half of "mechanical": a provisioned dataset still
loads, and the inventory the BFCL fixture pins still matches the file on disk. A
migration that changed behaviour would show up there, not in the resolution tests.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.scaffolds import registry, roots  # noqa: E402

DATA = ROOT / "data"
CONTAIN = ROOT / "tests" / "fixtures" / "hg_bfclcontain"
HOST_LITERAL = "/mnt/20t/xubuqiang/datasets/"
DEFAULT_COLLECTION = Path(".local/share/harnessgrad/datasets")

#: The per-dataset variables the old adapters carried. They are kept as the legacy value
#: so a run recorded before the helper still resolves where it did; a test process that
#: inherits one of them must not accidentally exercise it.
OLD_VARIABLES = (
    "HG_ALFWORLD_DATA", "HG_API_BANK_DATA", "HG_COOPERBENCH_DATA", "HG_COSTBENCH_DATA",
    "HG_COSTBENCH_RUNTIME", "HG_JERICHO_DATA", "HG_KUMO_DATA", "HG_LOCOMO_DATA",
    "HG_MAB_DATA", "HG_MAB_PREPARED", "HG_PERSONAMEM_V2_DATA", "HG_SCIENCEWORLD_DATA",
    "HG_SPREADSHEETBENCH_DATA", "HG_STRONGREJECT_DATA", "HG_TAU2_DATA", "HG_TAU3_DATA",
    "HG_TAU3_RUNTIME", "HG_TERMINAL_BENCH", "HG_SEALED_JAVA_HOME",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Every test starts from "nothing configured", the fresh-clone case."""
    for key in list(os.environ):
        if key.startswith("HG_DATA_") or key in OLD_VARIABLES:
            monkeypatch.delenv(key, raising=False)


def test_precedence_per_dataset_then_collection_then_existing_legacy(tmp_path, monkeypatch):
    legacy = tmp_path / "operator" / "gsm8k"
    legacy.mkdir(parents=True)
    collection = tmp_path / "collection"
    (collection / "gsm8k").mkdir(parents=True)
    override = tmp_path / "override"
    override.mkdir()

    # Rule 1: the per-dataset variable, named from the dataset name.
    monkeypatch.setenv("HG_DATA_ROOT", str(collection))
    monkeypatch.setenv(roots.env_name("gsm8k"), str(override))
    assert roots.env_name("gsm8k") == "HG_DATA_GSM8K"
    assert roots.dataset_root("gsm8k", legacy) == override

    # Rule 2: one export moves the whole collection, and it beats the legacy path even
    # though the legacy path exists.
    monkeypatch.delenv(roots.env_name("gsm8k"))
    assert roots.dataset_root("gsm8k", legacy) == collection / "gsm8k"

    # Rule 3: with nothing configured, the existing operator path wins, so every run
    # recorded before this module still reads the same bytes.
    monkeypatch.delenv("HG_DATA_ROOT")
    assert roots.dataset_root("gsm8k", legacy) == legacy

    # Rule 4: nothing configured and no legacy -> the portable per-user default.
    monkeypatch.setenv("HOME", str(tmp_path))
    absent = tmp_path / "not-provisioned" / "gsm8k"
    assert not absent.exists()
    assert roots.dataset_root("gsm8k", absent) == tmp_path / DEFAULT_COLLECTION / "gsm8k"
    assert roots.dataset_root("gsm8k") == tmp_path / DEFAULT_COLLECTION / "gsm8k"


def test_a_stale_literal_is_skipped_instead_of_beating_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    stale = tmp_path / "stale-literal-that-does-not-exist"
    assert not stale.exists()
    resolved = roots.dataset_root("gsm8k", stale)
    assert resolved == tmp_path / DEFAULT_COLLECTION / "gsm8k"
    assert stale not in resolved.parents and resolved != stale


def test_describe_says_which_rule_won_and_whether_it_exists(tmp_path, monkeypatch):
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setenv(roots.env_name("gsm8k"), str(override))
    assert roots.describe("gsm8k", roots.dataset_root("gsm8k", HOST_LITERAL + "gsm8k")) == {
        "name": "gsm8k", "root": str(override), "source": "HG_DATA_GSM8K", "exists": True}

    monkeypatch.delenv(roots.env_name("gsm8k"))
    collection = tmp_path / "collection"
    monkeypatch.setenv("HG_DATA_ROOT", str(collection))
    info = roots.describe("gsm8k", roots.dataset_root("gsm8k", HOST_LITERAL + "gsm8k"))
    assert info["source"] == "HG_DATA_ROOT"
    assert info["root"] == str(collection / "gsm8k")
    assert info["exists"] is False  # rule 2 answers "where", it does not create or check out

    monkeypatch.delenv("HG_DATA_ROOT")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    assert roots.describe("gsm8k", legacy, legacy=legacy)["source"] == "legacy"
    assert roots.describe("gsm8k", legacy, legacy=legacy)["exists"] is True

    monkeypatch.setenv("HOME", str(tmp_path))
    assert roots.describe("gsm8k")["source"] == "default"
    assert roots.describe("gsm8k")["exists"] is False

    # A root supplied by the caller and not produced by any rule is named as such, so a
    # monkeypatched adapter never looks like configuration.
    elsewhere = tmp_path / "elsewhere"
    assert roots.describe("gsm8k", elsewhere)["source"] == "explicit"
    assert roots.describe("gsm8k", elsewhere)["root"] == str(elsewhere)


def _code_constants(path: Path):
    """String constants in executable positions, with their parent nodes.

    Docstrings are `Expr(Constant)` and are skipped on purpose: prose may quote the
    operator's old path, code may not. Comments never reach the AST at all.
    """
    tree = ast.parse(path.read_bytes(), filename=str(path))
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and HOST_LITERAL in node.value
                and not isinstance(parent.get(node), ast.Expr)):
            yield node, parent


def _called_by_data_root(node, parent) -> bool:
    """Is this constant (or the `os.environ.get` around it) the helper's `legacy` argument?"""
    cursor = node
    for _ in range(3):
        owner = parent.get(cursor)
        if owner is None:
            return False
        if isinstance(owner, ast.Call):
            name = ast.unparse(owner.func)
            if name == "roots.dataset_root":
                return True
            if name != "os.environ.get":
                return False
        cursor = owner
    return False


def test_no_data_module_assigns_a_host_path_to_dataset_root():
    """The helper call is required; the old spelling survives only as its `legacy`."""
    problems = []
    for path in sorted(DATA.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_bytes(), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = {node.target.id}
            else:
                continue
            if "DATASET_ROOT" in targets:
                value = ast.unparse(node.value)
                if "Path(" in value and HOST_LITERAL in value:
                    problems.append(f"{relative}:{node.lineno}: {value}")
                if "roots.dataset_root(" not in value:
                    problems.append(f"{relative}:{node.lineno}: DATASET_ROOT is not a "
                                    f"helper call: {value}")
        # A `dst` in downloads(), a `ROOT` in a helper, or any other executable literal:
        # the only place the operator's path may appear is roots.dataset_root(..., legacy).
        for node, parent in _code_constants(path):
            if not _called_by_data_root(node, parent):
                problems.append(f"{relative}:{node.lineno}: host path outside the helper: "
                                f"{node.value!r}")
    assert not problems, "\n".join(problems)


def _provisioned():
    """`gsm8k` when it is on this machine, otherwise the first dataset that is."""
    order = ["gsm8k"] + [name for name in registry.published() if name != "gsm8k"]
    for name in order:
        module = importlib.import_module(registry.module_name(name))
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
