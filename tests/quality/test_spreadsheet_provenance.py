"""Source-only spreadsheet provenance controls; no benchmark or native runtime."""
import json
from pathlib import Path
import shutil

import pytest

from data.scaffolds import spreadsheet_provenance
from harnessgrad.provenance import measurement_components as identities, scoring_identity

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('spreadsheetbench', 'spreadsheetbench2')


def copy_sources(root, name):
    files = [f'data/datasets/{name}.py', f'data/datasets/{name}/scoring_profile.py',
             f'data/datasets/{name}/measurement_profile.py', f'data/datasets/{name}/verify/check.py',
             'data/scaffolds/scorer_input.py', 'data/scaffolds/spreadsheet_runtime/native.py']
    if name.endswith('2'):
        files += [f'data/datasets/{name}/{path}' for path in
                  ('PROVENANCE.json', 'prepare.py', 'verify/evaluation.py', 'verify/open_spreadsheet.py')]
        files += ['data/scaffolds/native_harbor.py']
    for relative in files:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)


@pytest.mark.parametrize('name', NAMES)
def test_old_profiles_validate_but_do_not_silently_match(name):
    old = json.loads((ROOT / 'tests/fixtures/spreadsheet_scoring_identity/before.json').read_text())[name]
    assert scoring_identity.validate(old) == old
    current = scoring_identity.resolve(name)
    components = identities.resolve(name)
    assert old['profile']['metric'] == current['profile']['metric']
    assert scoring_identity.problems(old, current)
    assert identities.bundle_problems(None, components)
    identities.require_scoring_alignment(current, components)
    assert components['data_source']['details']['declared_artifact_sha256'] == (
        None if name.endswith('2') else '10ef893dd29cb13ab97143ea787e68cdc9574a13873ab9a54e50b31dc03fc949')


@pytest.mark.parametrize('name', NAMES)
def test_distinct_loader_scorer_and_source_changes(tmp_path, monkeypatch, name):
    copy_sources(tmp_path, name)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    monkeypatch.setattr(scoring_identity, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve(name)
    scoring = scoring_identity.resolve(name)
    adapter = tmp_path / f'data/datasets/{name}.py'
    adapter.write_bytes(adapter.read_bytes() + b'\n# loader change\n')
    loader = identities.resolve(name)
    assert loader['loader'] != first['loader']
    assert loader['data_source'] == first['data_source'] and loader['scorer'] == first['scorer']
    assert scoring_identity.resolve(name) == scoring
    scorer = tmp_path / f'data/datasets/{name}/verify/check.py'
    scorer.write_bytes(scorer.read_bytes() + b'\n# scorer change\n')
    changed = identities.resolve(name)
    assert changed['scorer'] != loader['scorer'] and changed['loader'] == loader['loader']
    assert scoring_identity.problems(scoring, scoring_identity.resolve(name))
    old_revision = first['data_source']['details']['revision']
    adapter.write_text(adapter.read_text().replace(old_revision, 'f' * 40))
    if name.endswith('2'):
        for relative in ('prepare.py', 'PROVENANCE.json'):
            file = tmp_path / f'data/datasets/{name}/{relative}'
            file.write_text(file.read_text().replace(old_revision, 'f' * 40))
    after = identities.resolve(name)
    assert after['data_source'] != changed['data_source']
    assert after['scorer'] == changed['scorer']


@pytest.mark.parametrize('name,relative', [('spreadsheetbench', 'data/datasets/spreadsheetbench.py'),
    ('spreadsheetbench2', 'data/datasets/spreadsheetbench2.py'),
    ('spreadsheetbench2', 'data/datasets/spreadsheetbench2/prepare.py'),
    ('spreadsheetbench2', 'data/datasets/spreadsheetbench2/PROVENANCE.json')])
def test_declaration_race_is_rejected(tmp_path, monkeypatch, name, relative):
    copy_sources(tmp_path, name)
    capture = identities.capture_implementation
    altered = False
    def change(kind, root, components, **kwargs):
        nonlocal altered
        if kind == 'loader' and not altered:
            file = root / relative
            file.write_bytes(file.read_bytes() + b'\n')
            altered = True
        return capture(kind, root, components, **kwargs)
    monkeypatch.setattr(identities, 'capture_implementation', change)
    with pytest.raises(ValueError, match='changed during capture'):
        spreadsheet_provenance.profile(tmp_path, name)


def test_v2_inconsistent_and_duplicate_declarations_fail(tmp_path):
    copy_sources(tmp_path, 'spreadsheetbench2')
    file = tmp_path / 'data/datasets/spreadsheetbench2/PROVENANCE.json'
    original = file.read_text()
    file.write_text(original.replace('"source_revision":', '"source_revision": "bad", "source_revision":'))
    with pytest.raises(ValueError, match='provenance declaration'):
        spreadsheet_provenance.profile(tmp_path, 'spreadsheetbench2')
    file.write_text(original.replace('5a2215ed4121945ab09d8723df2995602090b042', 'f' * 40))
    with pytest.raises(ValueError, match='provenance declaration'):
        spreadsheet_provenance.profile(tmp_path, 'spreadsheetbench2')


def test_v2_upstream_evaluator_declaration_changes_only_scorer(tmp_path):
    copy_sources(tmp_path, 'spreadsheetbench2')
    before = spreadsheet_provenance.profile(tmp_path, 'spreadsheetbench2')
    path = tmp_path / 'data/datasets/spreadsheetbench2/PROVENANCE.json'
    manifest = json.loads(path.read_text())
    manifest['files']['evaluation.py'] = 'f' * 64
    path.write_text(json.dumps(manifest))
    after = spreadsheet_provenance.profile(tmp_path, 'spreadsheetbench2')
    assert before['data_source'] == after['data_source']
    assert before['loader'] == after['loader']
    assert before['scorer'] != after['scorer']


def test_v1_invalid_url_declaration_is_rejected(tmp_path):
    copy_sources(tmp_path, 'spreadsheetbench')
    adapter = tmp_path / 'data/datasets/spreadsheetbench.py'
    adapter.write_text(adapter.read_text().replace('hf-mirror.com/datasets/KAKA22', 'other.invalid/datasets/KAKA22'))
    with pytest.raises(ValueError, match='provenance declaration'):
        spreadsheet_provenance.profile(tmp_path, 'spreadsheetbench')
