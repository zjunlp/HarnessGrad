"""Import-only runtime migration preserves syntax and records identity boundaries."""
import ast
import hashlib
import json
from pathlib import Path

import pytest

from harnessgrad.datasets import runtime
from harnessgrad.provenance import measurement_components, scoring_identity

ROOT = Path(__file__).resolve().parents[2]
BEFORE = json.loads((ROOT / 'tests/fixtures/runtime_consumer_import_migration/before.json').read_text())


class NormalizeRuntimeImport(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        if node.module == 'harnessgrad' and any(alias.name == 'dataset_runtime' for alias in node.names):
            node.module = 'harnessgrad.datasets'
            for alias in node.names:
                if alias.name == 'dataset_runtime':
                    alias.name, alias.asname = 'runtime', 'dataset_runtime'
        if node.module == 'harnessgrad.dataset_runtime':
            node.module = 'harnessgrad.datasets.runtime'
        return node


def remove_reviewed_strict_decode(tree, function_name):
    """Normalize only a specific later annotation of existing strict behavior."""
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name)
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == 'run'
             and isinstance(n.func.value, ast.Name) and n.func.value.id == 'subprocess']
    assert len(calls) == 1
    errors = [k for k in calls[0].keywords if k.arg == 'errors']
    assert len(errors) == 1 and isinstance(errors[0].value, ast.Constant) and errors[0].value.value == 'strict'
    calls[0].keywords.remove(errors[0])


@pytest.mark.parametrize('path', sorted(BEFORE['normalized_ast']))
def test_runtime_import_and_reviewed_annotations_are_the_only_syntax_changes(path):
    tree = ast.parse((ROOT / path).read_bytes())
    if path == 'data/datasets/jobbench/prepare.py':
        remove_reviewed_strict_decode(tree, 'render')
    assert any(isinstance(n, ast.ImportFrom) and n.module in
               ('harnessgrad.datasets', 'harnessgrad.datasets.runtime') for n in ast.walk(tree))
    assert not any(isinstance(n, ast.ImportFrom) and
                   (n.module == 'harnessgrad.dataset_runtime' or
                    n.module == 'harnessgrad' and any(a.name == 'dataset_runtime' for a in n.names))
                   for n in ast.walk(tree))
    normalized = ast.dump(NormalizeRuntimeImport().visit(tree), include_attributes=False)
    assert hashlib.sha256(normalized.encode()).hexdigest() == BEFORE['normalized_ast'][path]


def test_chess_identity_distinguishes_data_from_scorer_code():
    current = measurement_components.resolve('chessbench_puzzles')
    old = BEFORE['chess_components']
    assert current['data_source'] == old['data_source']
    assert current['loader'] == old['loader']
    assert current['scorer'] != old['scorer']
    assert scoring_identity.resolve('chessbench_puzzles') != BEFORE['chess_scoring']
    assert measurement_components.bundle_problems(old, current)
    measurement_components.require_scoring_alignment(scoring_identity.resolve('chessbench_puzzles'), current)


def test_canonical_runtime_module_is_available():
    assert callable(runtime.resolve_execution)


TAU_BEFORE = json.loads((ROOT / 'tests/fixtures/runtime_consumer_import_migration/tau_science_before.json').read_text())


@pytest.mark.parametrize('path', sorted(TAU_BEFORE['normalized_ast']))
def test_preparation_and_control_only_have_reviewed_syntax_changes(path):
    tree = ast.parse((ROOT / path).read_bytes())
    assert not any(isinstance(n, ast.ImportFrom) and
                   (n.module == 'harnessgrad.dataset_runtime' or
                    n.module == 'harnessgrad' and any(a.name == 'dataset_runtime' for a in n.names))
                   for n in ast.walk(tree))
    if path == 'data/datasets/scienceworld/control.py':
        remove_reviewed_strict_decode(tree, '_gold')
    normalized = ast.dump(NormalizeRuntimeImport().visit(tree), include_attributes=False)
    assert hashlib.sha256(normalized.encode()).hexdigest() == TAU_BEFORE['normalized_ast'][path]


def test_tau_preparation_identity_rejects_prior_source_bytes():
    from tools import _tau_prepare
    old = TAU_BEFORE['tau_preparation_identity']
    current = _tau_prepare.source_identity()
    changed = {path for path in old if old[path] != current[path]}
    # The supervisor later made its existing strict decoding explicit.
    assert changed == {'data/datasets/tau2_bench/prepare.py', 'data/datasets/tau3_bench/prepare.py',
                       'data/datasets/tau2_bench.py', 'data/datasets/tau3_bench.py',
                       'tools/_tau_prepare.py'}
    assert _tau_prepare.verify_sources(current) == current
    with pytest.raises(_tau_prepare.PreparationSourceChanged):
        _tau_prepare.verify_sources(old)


def test_reasoning_gym_import_migration_preserves_declared_source_and_guards_shared_code():
    before = json.loads((ROOT / 'tests/fixtures/runtime_consumer_import_migration/reasoning_before.json').read_text())
    tree = ast.parse((ROOT / 'data/datasets/reasoning_gym.py').read_bytes())
    assert any(isinstance(node, ast.ImportFrom) and node.module == 'harnessgrad.datasets.runtime'
               for node in ast.walk(tree))
    assert not any(isinstance(node, ast.ImportFrom) and node.module == 'harnessgrad.dataset_runtime'
                   for node in ast.walk(tree))
    normalized = ast.dump(NormalizeRuntimeImport().visit(tree), include_attributes=False)
    assert hashlib.sha256(normalized.encode()).hexdigest() == before['normalized_adapter_ast_sha256']
    old = before['components']
    current = measurement_components.resolve('reasoning_gym')
    assert current['data_source'] == old['data_source']
    assert current['loader'] != old['loader'] and current['scorer'] != old['scorer']
    assert scoring_identity.resolve('reasoning_gym') != before['scoring']
    assert measurement_components.bundle_problems(old, current)
    assert scoring_identity.problems(before['scoring'], scoring_identity.resolve('reasoning_gym'))
    measurement_components.require_scoring_alignment(scoring_identity.resolve('reasoning_gym'), current)


SERVICE_BEFORE = json.loads((ROOT / 'tests/fixtures/runtime_consumer_import_migration/service_adapters_before.json').read_text())


@pytest.mark.parametrize('name', sorted(SERVICE_BEFORE))
def test_service_adapter_imports_leave_declared_code_and_identity_opt_in_unchanged(name):
    before = SERVICE_BEFORE[name]
    tree = ast.parse((ROOT / 'data/datasets' / (name + '.py')).read_bytes())
    assert any(isinstance(node, ast.ImportFrom) and node.module == 'harnessgrad.datasets.runtime'
               for node in ast.walk(tree))
    assert not any(isinstance(node, ast.ImportFrom) and node.module == 'harnessgrad.dataset_runtime'
                   for node in ast.walk(tree))
    normalized = ast.dump(NormalizeRuntimeImport().visit(tree), include_attributes=False)
    assert hashlib.sha256(normalized.encode()).hexdigest() == before['normalized_ast_sha256']
    if name in ('agentdojo', 'alfworld', 'jericho', 'scienceworld'):
        assert before['scoring_identity'] is None and before['measurement_components'] is None
        current = measurement_components.resolve(name)
        measurement_components.require_scoring_alignment(scoring_identity.resolve(name), current)
    else:
        assert scoring_identity.resolve(name) == before['scoring_identity'] is None
        assert measurement_components.resolve(name) == before['measurement_components'] is None


FINAL_BEFORE = json.loads((ROOT / 'tests/fixtures/runtime_consumer_import_migration/final_adapters_before.json').read_text())


@pytest.mark.parametrize('name', sorted(FINAL_BEFORE['adapters']))
def test_final_adapter_imports_preserve_other_syntax_and_identity_opt_in(name):
    before = FINAL_BEFORE['adapters'][name]
    tree = ast.parse((ROOT / 'data/datasets' / (name + '.py')).read_bytes())
    assert not any(isinstance(n, ast.ImportFrom) and
                   (n.module == 'harnessgrad.dataset_runtime' or
                    n.module == 'harnessgrad' and any(a.name == 'dataset_runtime' for a in n.names))
                   for n in ast.walk(tree))
    normalized = ast.dump(NormalizeRuntimeImport().visit(tree), include_attributes=False)
    assert hashlib.sha256(normalized.encode()).hexdigest() == before['normalized_ast_sha256']
    assert scoring_identity.resolve(name) == before['scoring_identity'] is None
    assert measurement_components.resolve(name) == before['measurement_components'] is None


def test_tau_adapter_change_rejects_previous_preparation_identity():
    from tools import _tau_prepare
    old = FINAL_BEFORE['tau_preparation_identity']
    current = _tau_prepare.source_identity()
    # The supervisor later made its existing strict decoding explicit.
    assert {path for path in old if old[path] != current[path]} == {
        'data/datasets/tau2_bench.py', 'data/datasets/tau3_bench.py', 'tools/_tau_prepare.py'}
    assert _tau_prepare.verify_sources(current) == current
    with pytest.raises(_tau_prepare.PreparationSourceChanged):
        _tau_prepare.verify_sources(old)
