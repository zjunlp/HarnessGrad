"""Optional component attribution uses declared paths without scoring candidates."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harnessgrad.customization.review import input_digest, review_proposal
from harnessgrad.improvement.contracts import Feedback, text_diff

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / 'harness'
    (root / 'skills').mkdir(parents=True)
    (root / 'skills/main.md').write_text('Original\n')
    manifest = {'name': 'example', 'components': {'context': ['skills/main.md'],
                 'verification': ['skills/main.md']}}
    (root / 'harness.json').write_text(json.dumps(manifest))
    request = {'feedback': [Feedback('f', 'failure', 'Check')],
               'skills': {'skills/main.md': 'Original\n'}, 'instruction': None}
    files = {'skills/main.md': 'Updated\n', 'skills/new.md': 'New\n'}
    document = {'status': 'unevaluated', 'input_sha256': input_digest(request),
                'proposal': {'files': files, 'diff': text_diff(request['skills'], files),
                             'feedback_ids': ['f'], 'stages': ['skillopt-fast']}}
    return root, request, document


def test_declared_and_unattributed_paths_are_kept_distinct(candidate):
    root, request, document = candidate
    assert 'component_attribution' not in review_proposal(request, document, root=root)
    result = review_proposal(request, document, root=root, include_components=True)
    assert result['status'] == 'unevaluated' and result['source_check'] == 'selected_files_match'
    assert result['component_attribution'] == {
        'components': ['context', 'verification'], 'unattributed': ['skills/new.md'],
        'by_path': {'skills/main.md': {'components': ['context', 'verification'], 'unattributed': []},
                    'skills/new.md': {'components': [], 'unattributed': ['skills/new.md']}}}
    assert (root / 'skills/main.md').read_text() == 'Original\n'


def test_missing_component_map_does_not_invent_attribution(candidate):
    root, request, document = candidate
    (root / 'harness.json').write_text('{"name":"example"}')
    value = review_proposal(request, document, root=root, include_components=True)['component_attribution']
    assert value['components'] == [] and value['unattributed'] == ['skills/main.md', 'skills/new.md']


@pytest.mark.parametrize('kind', ['missing', 'symlink', 'hardlink', 'duplicate', 'invalid', 'oversized', 'utf8'])
def test_manifest_must_be_bounded_unlinked_valid_json(candidate, tmp_path, kind):
    root, request, document = candidate
    manifest = root / 'harness.json'
    if kind in ('missing', 'symlink', 'hardlink'):
        manifest.unlink()
        if kind != 'missing':
            target = tmp_path / 'other.json'
            target.write_text('{}')
            if kind == 'symlink': manifest.symlink_to(target)
            else: os.link(target, manifest)
    elif kind == 'duplicate':
        manifest.write_text('{"components": {}, "components": {}}')
    elif kind == 'oversized':
        manifest.write_bytes(b' ' * (256 * 1024 + 1))
    elif kind == 'utf8':
        manifest.write_bytes(b'\xff')
    else:
        manifest.write_text('{"components": {"invented": ["skills/main.md"]}}')
    with pytest.raises(ValueError):
        review_proposal(request, document, root=root, include_components=True)


def test_components_cli_requires_root_and_emits_attribution(candidate, tmp_path):
    root, request, document = candidate
    request_path, proposal_path = tmp_path / 'request.json', tmp_path / 'proposal.json'
    request_path.write_text(json.dumps({'feedback': [vars(f) for f in request['feedback']], 'skills': request['skills']}))
    proposal_path.write_text(json.dumps(document))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    command = [sys.executable, '-m', 'harnessgrad.cli', 'customize', 'review',
               '--request', str(request_path), '--proposal', str(proposal_path), '--components']
    without = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert without.returncode == 1 and not without.stdout and 'requires a harness root' in without.stderr
    with_root = subprocess.run([*command, '--root', str(root)], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=20)
    assert with_root.returncode == 0, with_root.stderr
    assert json.loads(with_root.stdout)['component_attribution']['components'] == ['context', 'verification']


def test_inspect_cli_reads_only_declared_components(candidate, tmp_path):
    root, _, _ = candidate
    (root / 'private.txt').write_text('UNSELECTED PRIVATE VALUE')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    result = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'inspect',
                             '--root', str(root)], cwd=tmp_path, env={**env, 'PYTHONPATH': str(ROOT)},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['name'] == 'example'
    assert value['components'] == {'context': ['skills/main.md'], 'verification': ['skills/main.md']}
    assert value['env_kinds'] == []
    assert 'UNSELECTED PRIVATE VALUE' not in result.stdout + result.stderr


def test_inspect_cli_refuses_unsafe_manifest(candidate, tmp_path):
    root, _, _ = candidate
    (root / 'harness.json').unlink()
    (root / 'harness.json').symlink_to(tmp_path / 'outside.json')
    (tmp_path / 'outside.json').write_text('{}')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    result = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'inspect',
                             '--root', str(root)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 1 and not result.stdout
    assert 'unavailable or unsafe' in result.stderr
