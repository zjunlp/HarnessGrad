"""Scoped negative reachability evidence plus an UNWIRED decoder preflight.

The static check inspects the current platform sources for known decoder
references. The saved graph describes a historical review. Computed loaders
remain unresolved, and these controls never authorize official scoring.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "tests/fixtures/hg_bfclguard"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


analysis = load("bfclguard_reachability", HERE / "reachability.py")


def test_retained_eval_branches_are_pinned_and_excluded_by_gate():
    path = (ROOT / "tests/fixtures/bfcl_ast_source/candidate-codex195/files/"
            "berkeley-function-call-leaderboard/bfcl_eval/model_handler/utils.py")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "153bb33a9515ea2d6ee6a1387faaa464c7be4fe9c6a9f88cc17ad23a1cc306d2"
    tree = ast.parse(raw)
    resolver = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "resolve_ast_by_type")
    parents = {child: node for node in ast.walk(resolver) for child in ast.iter_child_nodes(node)}
    branches = []
    for node in ast.walk(resolver):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "eval":
            cursor = node
            while not isinstance(cursor, ast.If):
                cursor = parents[cursor]
            branches.append(ast.unparse(cursor.test))
    assert sorted(branches) == ["isinstance(value, ast.BinOp)", "isinstance(value, ast.Lambda)"]
    gate = load("bfclguard_gate", HERE / "gate.py")
    assert gate.preflight("fabricated_echo(value=1+2)")["reason"] == "bfclguard_binop_eval"
    assert gate.preflight("fabricated_echo(value=lambda: 1)")["reason"] == "bfclguard_nonliteral_argument"


def test_current_platform_graph_has_no_known_decoder_or_candidate_imports():
    result = analysis.analyze()
    assert len(result["closures"]) == len(result["sources"])
    assert {r["entry"] for r in result["closures"]} == {r["id"] for r in result["sources"]}
    assert not analysis.candidate_import_edges(result)
    assert not analysis.target_edges(result)
    assert not [r for r in result["mechanisms"] if r["target_mentions"]]
    # Decoder identifiers would be executable references; currently only two data strings exist.
    assert all(r["kind"] == "Constant" and r["path"] == "data/datasets/bfcl_v3.py"
               for r in result["references"])


def test_analyzer_follows_relative_imports_initializers_and_literal_dynamic_aliases(tmp_path):
    files = {
        "driver.py": "from eval import entry\n",
        "eval/__init__.py": "from . import boot\n",
        "eval/boot.py": "pass\n",
        "eval/entry.py": "import importlib as il\nload = il.import_module\nload('data.leaf')\n",
        "data/leaf.py": "from bfcl_eval.model_handler.utils import resolve_ast_call\n",
        "methods/cli": "#!/usr/bin/env python3\nimport driver\n",
        "tools/tool.py": "pass\n",
        "harnessgrad/helper.py": "pass\n",
        "ckpt/loader.py": "pass\n",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    candidate = tmp_path / "data/candidates/local/agent.py"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("from bfcl_eval.model_handler.utils import resolve_ast_call\n")
    result = analysis.analyze(tmp_path)
    ids = {r["path"]: r["id"] for r in result["sources"]}
    assert ids.keys() == files.keys()
    closure = next(r["reachable"] for r in result["closures"] if r["entry"] == ids["driver.py"])
    assert {ids[p] for p in ("driver.py", "eval/__init__.py", "eval/boot.py", "eval/entry.py", "data/leaf.py")} <= set(closure)
    assert analysis.target_edges(result)
    entry_closure = next(r["reachable"] for r in result["closures"] if r["entry"] == ids["eval/entry.py"])
    assert ids["eval/__init__.py"] in entry_closure and ids["eval/boot.py"] in entry_closure


@pytest.mark.parametrize("candidate_present", [False, True])
@pytest.mark.parametrize("statement", ["import data.candidates.probe", "from data import candidates"])
def test_candidate_import_is_detected_even_when_artifact_is_absent(tmp_path, candidate_present, statement):
    (tmp_path / "driver.py").write_text(statement + "\nimport data.candidates_extra\n")
    if candidate_present:
        candidate = tmp_path / "data/candidates/probe.py"
        candidate.parent.mkdir(parents=True)
        candidate.write_text("from bfcl_eval.model_handler.utils import resolve_ast_call\n")
    result = analysis.analyze(tmp_path)
    assert [row["path"] for row in result["sources"]] == ["driver.py"]
    assert [edge["statement"] for edge in analysis.candidate_import_edges(result)] == [statement]


@pytest.mark.parametrize("statement,category", [
    ("import importlib as il; il.import_module('bfcl_eval.model_handler.utils')", "dynamic_import"),
    ("from importlib.util import spec_from_file_location as load; load('alias', '/bfcl_eval/model_handler/utils.py')", "file_loader"),
    ("import runpy as r; r.run_module('bfcl_eval.model_handler.utils')", "runpy"),
    ("exec('from bfcl_eval.model_handler import utils')", "code_execution"),
    ("import subprocess as sp; sp.run(['python', '-m', 'bfcl_eval.model_handler.utils'])", "process"),
    ("resolve_ast_call(value)", None),
])
def test_named_target_witnesses_are_detected(tmp_path, statement, category):
    (tmp_path / "driver.py").write_text(statement)
    result = analysis.analyze(tmp_path)
    if category:
        assert any(r["category"] == category and r["target_mentions"] for r in result["mechanisms"])
    else:
        assert any(r["kind"] == "Name" and r["value"] == "resolve_ast_call" for r in result["references"])


@pytest.mark.parametrize("statement", [
    "import bfcl_eval.model_handler.utils as decoder",
    "from bfcl_eval.model_handler.utils import default_decode_ast_prompting",
])
def test_direct_target_import_witnesses_are_detected(tmp_path, statement):
    (tmp_path / "driver.py").write_text(statement)
    assert analysis.target_edges(analysis.analyze(tmp_path))


def test_existing_import_direction_policy_would_not_catch_tests_decoder_import():
    # Correct the premise explicitly without weakening/editing that guard.
    policy = load("bfclguard_existing_policy", ROOT / "tests/quality/test_import_direction.py")
    statement = "from tests.fixtures.bfcl_ast_source import model_handler"
    edges = policy._imports("data/datasets/bfcl_v3.py", statement, set())
    assert edges and edges[0].target == "tests.fixtures.bfcl_ast_source"
    assert policy._policy_errors(edges, {}, {}) == []
    direct = policy._imports("data/datasets/bfcl_v3.py", "import bfcl_eval.model_handler.utils", set())
    assert direct and policy._policy_errors(direct, {}, {}) == []
    # Also test a syntactically valid import-by-string of the exact retained path.
    statement = ("__import__('tests.fixtures.bfcl_ast_source.candidate-codex195.files.'"
                 " 'berkeley-function-call-leaderboard.bfcl_eval.model_handler.utils')")
    assert policy._imports("data/datasets/bfcl_v3.py", statement, set()) == []


@pytest.mark.parametrize("account", ["first-account", "another-account"])
@pytest.mark.parametrize("as_bytes", [False, True])
def test_dataset_tripwire_uses_the_supplied_root(tmp_path, monkeypatch, account, as_bytes):
    monkeypatch.syspath_prepend(str(HERE))
    controls = load("bfclguard_path_controls", HERE / "controls.py")
    dataset = tmp_path / account / "corpus"
    tripwire = controls.DecoderTripwire(dataset)
    filename = str(dataset / "row.json")
    argument = os.fsencode(filename) if as_bytes else filename
    with pytest.raises(RuntimeError, match="forbidden_open"):
        tripwire.audit("open", (argument, "r", 0))
    assert tripwire.events == [{"kind": "forbidden_open", "value": filename}]
    # A directory whose name merely shares the prefix is outside the corpus.
    tripwire.audit("open", (str(dataset.with_name("corpus-output") / "answer.txt"), "w", 0))
    assert len(tripwire.events) == 1


def test_dataset_tripwire_checks_relative_paths(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(HERE))
    monkeypatch.chdir(tmp_path)
    controls = load("bfclguard_relative_path_controls", HERE / "controls.py")
    tripwire = controls.DecoderTripwire(tmp_path / "corpus")
    with pytest.raises(RuntimeError, match="forbidden_open"):
        tripwire.audit("open", ("corpus/nested/../row.json", "r", 0))


def test_dynamic_real_platform_adapter_and_shared_check_never_enter_decoder(tmp_path):
    proc = subprocess.run([sys.executable, "-I", "-S", "-B", str(HERE / "controls.py"),
                           "--dynamic", "--scratch", str(tmp_path)],
                          text=True, capture_output=True, timeout=30, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    actual = json.loads(proc.stdout)
    expected = json.loads((HERE / "dynamic-observation.json").read_text())
    assert actual == expected
    assert actual["retained_sys_modules"] == [] and actual["sink_events"] == []


def test_gate_refuses_before_spy_decoder_and_bypass_control_enters_spy():
    proc = subprocess.run([sys.executable, "-I", "-S", "-B", str(HERE / "controls.py")],
                          text=True, capture_output=True, timeout=10, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    actual = [json.loads(line) for line in proc.stdout.splitlines()]
    expected = [json.loads(line) for line in (HERE / "gate-observations.jsonl").read_text().splitlines()]
    assert actual == expected
