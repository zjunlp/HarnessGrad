"""Source-checked patch export stays offline and does not write the harness."""
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from harnessgrad.customization.patch import export_patch
from harnessgrad.customization.review import input_digest
from harnessgrad.improvement.contracts import Feedback, text_diff

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / 'harness'
    (root / 'skills').mkdir(parents=True)
    (root / 'skills/main.md').write_text('Original without newline')
    (root / 'prompt.md').write_text('Baseline\n')
    request = {'feedback': [Feedback('one', 'failure', 'Tests were skipped')],
               'skills': {'skills/main.md': 'Original without newline'},
               'instruction': ('prompt.md', 'Baseline\n')}
    original = {'skills/main.md': 'Original without newline', 'prompt.md': 'Baseline\n'}
    files = {'skills/main.md': 'Revised\n', 'prompt.md': 'Baseline\nCheck.\n',
             'skills/verify/SKILL.md': 'Run tests.\n'}
    document = {'status': 'unevaluated', 'input_sha256': input_digest(request),
                'proposal': {'files': files, 'diff': text_diff(original, files),
                             'feedback_ids': ['one'], 'stages': ['skillopt-fast', 'ace-reflect-curate']}}
    return root, request, document


def test_exported_patch_applies_to_a_synthetic_tree(candidate):
    if not shutil.which('git'):
        pytest.skip('git is required for this patch compatibility control')
    root, request, document = candidate
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    patch = export_patch(request, document, root=root)
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    checked = subprocess.run(['git', 'apply', '--check', '-'], cwd=root, input=patch,
                             text=True, capture_output=True, timeout=20)
    assert checked.returncode == 0, checked.stderr
    applied = subprocess.run(['git', 'apply', '-'], cwd=root, input=patch,
                             text=True, capture_output=True, timeout=20)
    assert applied.returncode == 0, applied.stderr
    assert (root / 'skills/main.md').read_text() == 'Revised\n'
    assert (root / 'prompt.md').read_text() == 'Baseline\nCheck.\n'
    assert (root / 'skills/verify/SKILL.md').read_text() == 'Run tests.\n'


def test_export_refuses_stale_source_and_existing_new_file(candidate):
    root, request, document = candidate
    (root / 'skills/main.md').write_text('changed')
    with pytest.raises(ValueError, match='differ'):
        export_patch(request, document, root=root)
    (root / 'skills/main.md').write_text('Original without newline')
    (root / 'skills/verify').mkdir()
    (root / 'skills/verify/SKILL.md').write_text('unselected')
    with pytest.raises(ValueError, match='already exists'):
        export_patch(request, document, root=root)


def test_export_refuses_symlinked_new_path(candidate, tmp_path):
    root, request, document = candidate
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'skills/verify').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='unsafe'):
        export_patch(request, document, root=root)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('path,text,problem', [('skills/empty.md', '', 'empty file'),
                                                ('skills/tab\tname.md', 'x\n', 'control characters')])
def test_export_refuses_unrepresentable_paths_and_new_empty_files(candidate, path, text, problem):
    root, request, document = candidate
    document = copy.deepcopy(document)
    files = document['proposal']['files']
    files[path] = text
    before = {**request['skills'], request['instruction'][0]: request['instruction'][1]}
    document['proposal']['diff'] = text_diff(before, files)
    with pytest.raises(ValueError, match=problem):
        export_patch(request, document, root=root)


def test_patch_cli_prints_only_patch_without_endpoint(candidate, tmp_path):
    root, request, document = candidate
    request_path, proposal_path = tmp_path / 'request.json', tmp_path / 'proposal.json'
    request_path.write_text(json.dumps({'feedback': [vars(f) for f in request['feedback']],
                                        'skills': request['skills'],
                                        'instruction': {'path': request['instruction'][0],
                                                        'text': request['instruction'][1]}}))
    proposal_path.write_text(json.dumps(document))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    proc = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'patch',
        '--request', str(request_path), '--proposal', str(proposal_path), '--root', str(root)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0 and not proc.stderr
    assert proc.stdout == document['proposal']['diff']
    assert (root / 'skills/main.md').read_text() == 'Original without newline'
    assert not (root / 'skills/verify').exists()


@pytest.mark.parametrize('text', ['one\rtwo', 'one\u2028two', 'one\x00two'])
def test_export_refuses_text_formats_git_diff_cannot_reliably_reproduce(candidate, text):
    root, request, document = candidate
    document = copy.deepcopy(document)
    files = document['proposal']['files']
    files['skills/main.md'] = text
    before = {**request['skills'], request['instruction'][0]: request['instruction'][1]}
    document['proposal']['diff'] = text_diff(before, files)
    with pytest.raises(ValueError, match='line format'):
        export_patch(request, document, root=root)


def test_memory_proposal_can_export_patch(candidate):
    root, request, document = candidate
    document['proposal']['stages'].append('evo-harness-skill-curation')
    document['memories'] = [{'task_id': 'one', 'lesson': 'x', 'trigger': 'x', 'evidence': 'x', 'scope_hint': 'x'}]
    assert export_patch(request, document, root=root) == document['proposal']['diff']


def test_exported_patch_reproduces_common_text_variants(tmp_path):
    """Git applies each candidate exactly, including CRLF and missing final newlines."""
    if not shutil.which('git'):
        pytest.skip('git is required for this patch compatibility control')
    values = ('', '\n', 'a', 'a\n', 'a\nb', 'a\nb\n', 'a\r\nb\r\n', 'a  \n')
    feedback = [Feedback('one', 'failure', 'synthetic observation')]
    for index, (before, after) in enumerate((a, b) for a in values for b in values if a != b):
        root = tmp_path / str(index)
        (root / 'skills').mkdir(parents=True)
        target = root / 'skills/with space.md'
        target.write_bytes(before.encode('utf-8'))
        request = {'feedback': feedback, 'skills': {'skills/with space.md': before}, 'instruction': None}
        files = {'skills/with space.md': after}
        document = {'status': 'unevaluated', 'input_sha256': input_digest(request),
                    'proposal': {'files': files, 'diff': text_diff(request['skills'], files),
                                 'feedback_ids': ['one'], 'stages': ['skillopt-fast']}}
        patch = export_patch(request, document, root=root)
        env = {key: value for key, value in os.environ.items()
               if key not in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        result = subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'], cwd=root,
                                input=patch.encode('utf-8'), capture_output=True, env=env, timeout=20)
        assert result.returncode == 0, (repr(before), repr(after), result.stderr)
        assert target.read_bytes() == after.encode('utf-8'), (repr(before), repr(after))
