"""Offline proposal consistency checks, without model or source writes."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harnessgrad.improvement.contracts import Feedback, text_diff
from harnessgrad.customization.review import input_digest, review_proposal

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def inputs():
    request = {'feedback': [Feedback('one', 'failure', 'Run tests')],
               'skills': {'skills/main.md': 'Original\n'}, 'instruction': None}
    files = {'skills/main.md': 'Original\nCheck tests\n'}
    document = {'status': 'unevaluated', 'input_sha256': input_digest(request),
                'proposal': {'files': files, 'diff': text_diff(request['skills'], files),
                             'feedback_ids': ['one'], 'stages': ['skillopt-fast']}}
    return request, document


def test_review_checks_selected_source_without_writes(inputs, tmp_path):
    request, document = inputs
    (tmp_path / 'skills').mkdir()
    source = tmp_path / 'skills/main.md'
    source.write_text('Original\n')
    assert review_proposal(request, document)['source_check'] == 'not_checked'
    result = review_proposal(request, document, root=tmp_path)
    assert result['source_check'] == 'selected_files_match'
    assert result['changed_paths'] == ['skills/main.md']
    assert source.read_text() == 'Original\n'
    source.write_text('Changed\n')
    with pytest.raises(ValueError, match='differ'):
        review_proposal(request, document, root=tmp_path)
    source.unlink()
    source.symlink_to(tmp_path / 'other')
    with pytest.raises((OSError, ValueError)):
        review_proposal(request, document, root=tmp_path)


@pytest.mark.parametrize('mutation', ['request', 'digest', 'status', 'file', 'diff', 'stage', 'feedback', 'path', 'missing'])
def test_review_rejects_inconsistent_documents(inputs, mutation):
    request, document = copy.deepcopy(inputs)
    proposal = document['proposal']
    if mutation == 'request': request['feedback'][0] = Feedback('one', 'failure', 'Changed')
    elif mutation == 'digest': document.pop('input_sha256')
    elif mutation == 'status': document['status'] = 'approved'
    elif mutation == 'file': proposal['files']['skills/main.md'] = 'Changed'
    elif mutation == 'diff': proposal['diff'] = 'Changed'
    elif mutation == 'stage': proposal['stages'] = ['other']
    elif mutation == 'feedback': proposal['feedback_ids'] = ['other']
    elif mutation == 'path': proposal['files']['outside.md'] = 'Changed'
    else: proposal['files'].clear()
    with pytest.raises(ValueError):
        review_proposal(request, document)


def test_review_cli_is_offline_and_rejects_invalid_json(inputs, tmp_path):
    request, document = inputs
    request_path, proposal_path = tmp_path / 'request.json', tmp_path / 'proposal.json'
    request_path.write_text(json.dumps({'feedback': [vars(f) for f in request['feedback']],
                                        'skills': request['skills']}))
    proposal_path.write_text(json.dumps(document))
    command = [sys.executable, '-m', 'harnessgrad.cli', 'customize', 'review',
               '--request', str(request_path), '--proposal', str(proposal_path)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['source_check'] == 'not_checked'
    proposal_path.write_text('{"proposal": {}, "proposal": {}}')
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1 and not result.stdout


def test_offline_commands_work_when_method_package_is_unavailable(inputs, tmp_path):
    request, document = inputs
    root = tmp_path / 'harness'
    (root / 'skills').mkdir(parents=True)
    (root / 'skills/main.md').write_text('Original\n')
    feedback = tmp_path / 'feedback.json'
    feedback.write_text(json.dumps([vars(item) for item in request['feedback']]))
    request_file = tmp_path / 'request.json'
    request_file.write_text(json.dumps({'feedback': [vars(item) for item in request['feedback']],
                                        'skills': request['skills']}))
    proposal_file = tmp_path / 'proposal.json'
    proposal_file.write_text(json.dumps(document))
    source = '''
import importlib.abc, sys
class NoMethods(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'methods' or fullname.startswith('methods.'):
            raise AssertionError('offline workflow imported methods: ' + fullname)
sys.meta_path.insert(0, NoMethods())
from harnessgrad.entrypoints.customization import main
sys.exit(main(sys.argv[1:]))
'''
    for args in (['collect', '--root', str(root), '--skill', 'skills/main.md', '--feedback', str(feedback)],
                 ['review', '--request', str(request_file), '--proposal', str(proposal_file), '--root', str(root)]):
        result = subprocess.run([sys.executable, '-c', source, *args], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)


def test_review_rejects_ambiguous_candidate_path_even_with_matching_diff(inputs):
    request, document = copy.deepcopy(inputs)
    files = document['proposal']['files']
    files['skills/./main.md'] = 'Alias of selected source'
    document['proposal']['diff'] = text_diff(request['skills'], files)
    with pytest.raises(ValueError, match='files are invalid'):
        review_proposal(request, document)


@pytest.mark.parametrize('memory', [
    {'task_id': 'unknown', 'lesson': 'x', 'trigger': 'x', 'evidence': 'x', 'scope_hint': 'x'},
    {'task_id': [], 'lesson': 'x', 'trigger': 'x', 'evidence': 'x', 'scope_hint': 'x'},
    {'task_id': 'one', 'lesson': '', 'trigger': 'x', 'evidence': 'x', 'scope_hint': 'x'},
])
def test_review_rejects_invalid_memory_provenance(inputs, memory):
    request, document = copy.deepcopy(inputs)
    document['proposal']['stages'].append('evo-harness-skill-curation')
    document['memories'] = [memory]
    with pytest.raises(ValueError, match='memory'):
        review_proposal(request, document)


def test_review_rejects_duplicate_memory_for_one_feedback(inputs):
    request, document = copy.deepcopy(inputs)
    document['proposal']['stages'].append('evo-harness-skill-curation')
    memory = {'task_id': 'one', 'lesson': 'x', 'trigger': 'x', 'evidence': 'x', 'scope_hint': 'x'}
    document['memories'] = [memory, copy.deepcopy(memory)]
    with pytest.raises(ValueError, match='memory'):
        review_proposal(request, document)


def test_review_refuses_refinement_without_required_inputs(inputs):
    request, document = copy.deepcopy(inputs)
    document['proposal']['stages'].append('gepa-reflect-propose')
    with pytest.raises(ValueError, match='requires an instruction and failure feedback'):
        review_proposal(request, document)
    request = {'feedback': [Feedback('one', 'success', 'Passed')], 'skills': None,
               'instruction': ('prompt.md', 'Original\n')}
    files = {'prompt.md': 'Candidate\n'}
    document = {'status': 'unevaluated', 'input_sha256': input_digest(request),
                'proposal': {'files': files, 'diff': text_diff({'prompt.md': 'Original\n'}, files),
                             'feedback_ids': ['one'],
                             'stages': ['ace-reflect-curate', 'gepa-reflect-propose']}}
    with pytest.raises(ValueError, match='requires an instruction and failure feedback'):
        review_proposal(request, document)


@pytest.mark.parametrize('stages', [
    ['gepa-reflect-propose', 'ace-reflect-curate'],
    ['ace-reflect-curate', 'gepa-reflect-propose', 'gepa-reflect-propose'],
])
def test_review_refuses_duplicate_or_out_of_order_refinement(stages):
    request = {'feedback': [Feedback('one', 'failure', 'Skipped tests')], 'skills': None,
               'instruction': ('prompt.md', 'Original\n')}
    files = {'prompt.md': 'Candidate\n'}
    document = {'status': 'unevaluated', 'input_sha256': input_digest(request),
                'proposal': {'files': files, 'diff': text_diff({'prompt.md': 'Original\n'}, files),
                             'feedback_ids': ['one'], 'stages': stages}}
    with pytest.raises(ValueError, match='stages do not match'):
        review_proposal(request, document)
