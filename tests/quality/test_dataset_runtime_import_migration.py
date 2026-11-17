"""Static and identity controls for source-only adapter import migration."""
import ast
import hashlib
import json
from pathlib import Path

import pytest

from harnessgrad.provenance import measurement_components, scoring_identity

ROOT = Path(__file__).resolve().parents[2]
BEFORE = json.loads((ROOT / 'tests/fixtures/dataset_runtime_import_migration/before.json').read_text())


@pytest.mark.parametrize('name', sorted(BEFORE))
def test_runtime_path_migration_preserves_algorithm_syntax_and_scorer(name):
    source = (ROOT / 'data/datasets' / (name + '.py')).read_bytes()
    tree = ast.parse(source)
    imports = [node for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module == 'harnessgrad.datasets.runtime']
    assert imports
    assert not any(isinstance(node, ast.ImportFrom) and node.module == 'harnessgrad.dataset_runtime'
                   for node in ast.walk(tree))
    assert hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest() == BEFORE[name]['normalized_adapter_ast_sha256']
    before = BEFORE[name]['measurement_components']
    current = measurement_components.resolve(name)
    assert current['data_source'] == before['data_source']
    assert current['scorer'] == before['scorer']
    assert current['loader'] != before['loader']
    assert scoring_identity.resolve(name) == BEFORE[name]['scoring_identity']
    measurement_components.require_scoring_alignment(scoring_identity.resolve(name), current)
    assert measurement_components.bundle_problems(before, current)
