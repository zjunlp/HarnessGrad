"""upstream evo_harness/__init__.py:135-211: pre-refactor compatibility."""
import json
from pathlib import Path

import pytest
from native_helpers import fixture, runtime

REFERENCE = Path(__file__).resolve().parents[1] / 'fixtures/policy_ports/pre_refactor_evo_harness.json'


@pytest.mark.parametrize('scenario', ['topic_general', 'foreign_source'])
def test_pre_refactor_memory_and_skill_compilation(scenario, tmp_path, monkeypatch):
    rows = [{'round': 0, 'score_kind': 'training', 'score': .25,
             'per_task': {'t1': 0, 't2': 0, 't3': 0, 't4': 1}, 'identity': {'harness_sha': 'root'}}]
    _base, request = fixture(tmp_path, rows)
    prompts, output = [], {}

    def ask(prompt, **_kwargs):
        prompts.append(prompt.replace(str(tmp_path), '<WORK>'))
        if '[EvoHarness: reflect]' in prompt:
            return {'lesson': 'check exit status', 'trigger': 'shell', 'evidence': 'failure', 'scope_hint': 'execution'}
        if '[EvoHarness: compile topic]' in prompt:
            return {'skills': [{'path': 'skills/shell/SKILL.md', 'name': 'shell', 'description': 'shell execution',
                'source_task_ids': ['unknown'] if scenario == 'foreign_source' else ['t1'], 'content': 'Check exit status.'}]}
        if '[EvoHarness: compile general]' in prompt:
            return {'skills': [{'path': 'skills/general/SKILL.md', 'name': 'general', 'description': 'verify results',
                'source_task_ids': ['t1', 't2', 't3'], 'content': 'Verify observed results.'}]}
        raise AssertionError('unexpected prompt')

    def report(_request, **kwargs):
        output.update(kwargs)
        return 0

    def fail(_request, **kwargs):
        output['failure'] = str(kwargs['exc'])
        return 0

    monkeypatch.setenv('HG_POLICY_CONFIG', '{}')
    monkeypatch.setattr(runtime.editor, 'ask', ask)
    monkeypatch.setattr(runtime.editor, 'report_many', report)
    monkeypatch.setattr(runtime.editor, 'fail', fail)
    runtime.run('evo_harness', request)
    for entry in output.get('steps', []):
        folder = Path(entry.pop('harness_dir'))
        entry['files_contents'] = {str(p.relative_to(folder)): p.read_text()
                                   for p in sorted((folder / 'skills').rglob('*.md'))}
    actual = json.loads(json.dumps({'prompts': prompts, 'report': output}, default=str)
                        .replace(str(tmp_path), '<WORK>'))
    assert actual == json.loads(REFERENCE.read_text())[scenario]
