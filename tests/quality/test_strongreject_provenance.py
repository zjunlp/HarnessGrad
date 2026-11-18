"""StrongREJECT provenance and authored local-judge controls; no benchmark data."""
import json
from pathlib import Path
import shutil

import pytest

from data.datasets import strongreject
from test_strongreject_dataset import endpoint  # noqa: F401
from harnessgrad.provenance import measurement_components as identities, scoring_identity

ROOT = Path(__file__).resolve().parents[2]


def copy_sources(root):
    paths = ('data/datasets/strongreject.py', 'data/datasets/strongreject/prepare.py',
             'data/datasets/strongreject/measurement_profile.py', 'data/datasets/strongreject/scoring_profile.py',
             'data/datasets/strongreject/specs/sources.json', 'data/datasets/strongreject/verify/check.py',
             'data/datasets/strongreject/lib/scoring.py', 'eval/service/judge.py',
             'eval/service/judge_request.py', 'eval/service/tool_transport.py')
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)


def test_old_profile_is_valid_but_separate_components_are_required(monkeypatch):
    old = json.loads((ROOT / 'tests/fixtures/strongreject_scoring_identity/before.json').read_text())
    assert scoring_identity.validate(old) == old
    current = scoring_identity.resolve('strongreject')
    components = identities.resolve('strongreject')
    assert scoring_identity.problems(old, current)
    assert identities.bundle_problems(None, components)
    identities.require_scoring_alignment(current, components)
    assert components['data_source']['details']['declared_artifact_sha256'] is None
    assert 'tasks/' not in json.dumps(components['data_source'])


def test_manifest_data_change_does_not_relabel_scorer(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    first = identities.resolve('strongreject')
    path = tmp_path / 'data/datasets/strongreject/specs/sources.json'
    source = json.loads(path.read_text())
    next(e for e in source['files'] if e['path'].startswith('tasks/'))['sha256'] = 'f' * 64
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match='loaded source declaration differs'):
        identities.resolve('strongreject')
    monkeypatch.setattr(strongreject, 'SOURCES', source)
    after = identities.resolve('strongreject')
    assert after['data_source'] != first['data_source']
    assert after['scorer'] == first['scorer'] and after['loader'] == first['loader']


def test_manifest_change_during_capture_is_rejected(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    capture = identities.capture_implementation
    changed = False
    def race(kind, root, files, **kwargs):
        nonlocal changed
        if kind == 'loader' and not changed:
            path = root / 'data/datasets/strongreject/specs/sources.json'
            path.write_bytes(path.read_bytes() + b'\n')
            changed = True
        return capture(kind, root, files, **kwargs)
    monkeypatch.setattr(identities, 'capture_implementation', race)
    with pytest.raises(ValueError, match='source changed during capture'):
        identities.resolve('strongreject')


def test_judge_model_changes_scoring_and_aligned_component(monkeypatch):
    monkeypatch.setenv('HG_STRONGREJECT_JUDGE_MODEL', 'synthetic-judge-a')
    first = scoring_identity.resolve('strongreject')
    component = identities.resolve('strongreject')
    identities.require_scoring_alignment(first, component)
    monkeypatch.setenv('HG_STRONGREJECT_JUDGE_MODEL', 'synthetic-judge-b')
    second = scoring_identity.resolve('strongreject')
    changed = identities.resolve('strongreject')
    assert scoring_identity.problems(first, second)
    assert component['data_source'] == changed['data_source']
    identities.require_scoring_alignment(second, changed)


def test_invalid_or_duplicate_manifest_is_refused(tmp_path, monkeypatch):
    copy_sources(tmp_path)
    monkeypatch.setattr(identities, 'PROVIDERS', tmp_path / 'data/datasets')
    path = tmp_path / 'data/datasets/strongreject/specs/sources.json'
    source = path.read_text()
    path.write_text(source.replace('"schema": 1,', '"schema": 1, "schema": 1,', 1))
    with pytest.raises(ValueError, match='provenance declaration'):
        identities.resolve('strongreject')
    data = json.loads(source)
    data['files'] = data['files'][:-1] + [data['files'][0]]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='provenance declaration'):
        identities.resolve('strongreject')


def test_authored_verifier_context_remains_private_and_changes_with_judge(monkeypatch):
    from harnessgrad.provenance import verification_context
    template = json.dumps({'strongreject_rubric': 'Judge {forbidden_prompt} / {response}',
                           'strongreject_rubric_system': 'Synthetic rubric'}).encode()
    monkeypatch.setattr(strongreject, 'checked_bytes', lambda entry: template)
    row = {'forbidden_prompt': 'SYNTHETIC_PRIVATE_REQUEST', 'source_sha256': 'a' * 64,
           'attack_unchanged': False, 'jailbreak_method': 'authored'}
    task = {'task_id': 'synthetic/1', 'goal': 'Public synthetic prompt'}
    monkeypatch.setenv('HG_STRONGREJECT_JUDGE_MODEL', 'synthetic-judge-a')
    first = strongreject._verify(task['task_id'], row)
    context = verification_context.capture([task], {}, verifiers={task['task_id']: first})
    assert 'SYNTHETIC_PRIVATE_REQUEST' not in json.dumps(context)
    monkeypatch.setenv('HG_STRONGREJECT_JUDGE_MODEL', 'synthetic-judge-b')
    second = strongreject._verify(task['task_id'], row)
    changed = verification_context.capture([task], {}, verifiers={task['task_id']: second})
    assert verification_context.problems(context, changed)


@pytest.mark.parametrize('reply, expected', [
    ('1.b 1\n2.b 1\n3.b 1', 1.0),
    ('1.b 0\n2.b 5\n3.b 5', 0.0),
    ('malformed rubric', None),
])
def test_authored_verifier_and_local_judge_bind_current_provenance(tmp_path, monkeypatch, endpoint, reply, expected):
    from eval.verification.verifier import _verify
    from harnessgrad.provenance import verification_context

    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work))
    monkeypatch.setenv('HG_STRONGREJECT_JUDGE_MODEL', 'authored-judge')
    endpoint['reply'] = reply
    template = json.dumps({'strongreject_rubric': 'Request: {forbidden_prompt}\nAnswer: {response}',
                           'strongreject_rubric_system': 'Authored test rubric'}).encode()
    monkeypatch.setattr(strongreject, 'checked_bytes', lambda entry: template)
    row = {'forbidden_prompt': 'SYNTHETIC_PRIVATE_REQUEST', 'source_sha256': 'a' * 64,
           'attack_unchanged': False, 'jailbreak_method': 'authored'}
    task = {'task_id': 'synthetic/1', 'goal': 'Public authored prompt'}
    verifier = strongreject._verify(task['task_id'], row)
    scoring = scoring_identity.resolve('strongreject')
    components = identities.resolve('strongreject')
    identities.require_scoring_alignment(scoring, components)
    context = verification_context.capture([task], {}, verifiers={task['task_id']: verifier})
    assert 'SYNTHETIC_PRIVATE_REQUEST' not in json.dumps([context, scoring, components])
    actor = tmp_path / 'actor'
    actor.mkdir()
    (actor / 'answer.txt').write_text('Authored response')
    result = _verify(actor, verifier, '', sandbox=True, timeout_s=15)
    assert result.get('score') == expected, result
    if expected is None:
        assert result.get('outcome') == 'unmeasured'
    else:
        assert result['evidence']['measured'] is True
    assert len(endpoint['requests']) == 1, json.dumps(result)
    assert endpoint['requests'][0]['model'] == 'authored-judge'
    assert 'SYNTHETIC_PRIVATE_REQUEST' not in json.dumps(result)
    assert 'synthetic-secret' not in json.dumps([result, scoring, components])
