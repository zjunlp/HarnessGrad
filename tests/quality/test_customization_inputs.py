"""Explicit customization inputs stay bounded, local and rooted in the selected tree."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harnessgrad.customization.inputs import MAX_FILE_BYTES, collect_inputs
from harnessgrad.customization.proposals import Feedback

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def harness(tmp_path):
    root = tmp_path / 'harness'
    (root / 'skills').mkdir(parents=True)
    (root / 'skills/main.md').write_text('Skill text.\n')
    (root / 'prompt.md').write_text('Instruction.\n')
    (root / 'secret.txt').write_text('UNSELECTED SECRET')
    return root


def test_collects_only_selected_files_without_writing(harness):
    before = {p: p.read_bytes() for p in harness.rglob('*') if p.is_file()}
    result = collect_inputs(harness, ['skills/main.md'], 'prompt.md',
                            [Feedback('f1', 'failure', 'Skipped a check.')])
    assert result == {'feedback': [{'id': 'f1', 'kind': 'failure', 'text': 'Skipped a check.'}],
                      'skills': {'skills/main.md': 'Skill text.\n'},
                      'instruction': {'path': 'prompt.md', 'text': 'Instruction.\n'}}
    assert 'UNSELECTED SECRET' not in json.dumps(result)
    assert before == {p: p.read_bytes() for p in harness.rglob('*') if p.is_file()}


@pytest.mark.parametrize('path', ['../secret.md', '/tmp/secret.md', 'skills/../secret.md',
                                  'skills//main.md', 'skills/./main.md', 'skills\\main.md'])
def test_rejects_noncanonical_paths(harness, path):
    with pytest.raises(ValueError):
        collect_inputs(harness, [path], None, [Feedback('f', 'failure', 'feedback')])


def test_symlink_file_or_directory_never_exposes_target(harness, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.md').write_text('DO NOT EXPOSE')
    (harness / 'skills/link.md').symlink_to(outside / 'secret.md')
    (harness / 'skills/linked').symlink_to(outside, target_is_directory=True)
    for path in ('skills/link.md', 'skills/linked/secret.md'):
        with pytest.raises(ValueError, match='unavailable or unsafe') as exc:
            collect_inputs(harness, [path], None, [Feedback('f', 'failure', 'feedback')])
        assert 'DO NOT EXPOSE' not in str(exc.value)


def test_rejects_hardlinks_oversized_files_and_bad_utf8(harness, tmp_path):
    target = tmp_path / 'shared.md'
    target.write_text('shared')
    os.link(target, harness / 'skills/hard.md')
    (harness / 'skills/large.md').write_bytes(b'x' * (MAX_FILE_BYTES + 1))
    (harness / 'skills/bad.md').write_bytes(b'\xff')
    for path in ('skills/hard.md', 'skills/large.md', 'skills/bad.md'):
        with pytest.raises(ValueError):
            collect_inputs(harness, [path], None, [Feedback('f', 'failure', 'feedback')])


def test_collection_cli_is_offline_and_outputs_a_proposal_request(harness, tmp_path):
    feedback = tmp_path / 'feedback.json'
    feedback.write_text('[{"id":"a","kind":"failure","text":"run tests"}]')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    proc = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'collect',
        '--root', str(harness), '--skill', 'skills/main.md', '--instruction', 'prompt.md',
        '--feedback', str(feedback)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    request = json.loads(proc.stdout)
    assert request['skills']['skills/main.md'] == 'Skill text.\n'
    assert request['instruction']['text'] == 'Instruction.\n'
    assert request['feedback'][0]['text'] == 'run tests'
    assert 'UNSELECTED SECRET' not in proc.stdout + proc.stderr


def test_collected_request_cannot_exceed_proposal_input_limit(harness, tmp_path):
    (harness / 'skills/main.md').write_text('x' * MAX_FILE_BYTES)
    feedback = tmp_path / 'feedback.json'
    feedback.write_text(json.dumps([{'id': 'a', 'kind': 'failure', 'text': 'x' * (1900 * 1024)}]))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HG_METHOD_', 'PYTHONPATH'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    proc = subprocess.run([sys.executable, '-m', 'harnessgrad.cli', 'customize', 'collect',
        '--root', str(harness), '--skill', 'skills/main.md', '--feedback', str(feedback)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 1 and not proc.stdout
    assert 'combined request exceeds 2 MiB' in proc.stderr
