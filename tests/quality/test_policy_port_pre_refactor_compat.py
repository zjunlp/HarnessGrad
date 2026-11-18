"""Offline behavior controls captured before extracting reusable policy stages."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from native_helpers import fixture, runtime

REFERENCE = Path(__file__).resolve().parents[1] / 'fixtures/policy_ports/pre_refactor_ace_skillopt.json'


@pytest.mark.parametrize('name', ['ace', 'skillopt'])
def test_pre_refactor_prompt_candidate_and_report(name, tmp_path, monkeypatch):
    base, request = fixture(tmp_path)
    prompts, output = [], {}

    def ask(prompt, **_kwargs):
        prompts.append(prompt.replace(str(tmp_path), '<WORK>'))
        if '[ACE: reflect]' in prompt:
            return {'key_insight': 'Verify tests', 'bullet_tags': []}
        if '[ACE: curate]' in prompt:
            return {'operations': [{'type': 'ADD', 'section': 'general', 'content': 'Run tests.'}]}
        if '[SkillOpt: rank]' in prompt:
            return {'selected_indices': [0]}
        return {'edits': [{'path': 'skills/main.md', 'op': 'append', 'content': 'Run checks.'}]}

    def report(_request, **kwargs):
        output.update(kwargs)
        return 0

    def fail(_request, **kwargs):
        pytest.fail(f"policy failed: {kwargs['exc']}")

    config = {'reflection_minibatch_size': 1, 'merge_batch_size': 2} if name == 'skillopt' else {}
    monkeypatch.setenv('HG_POLICY_CONFIG', json.dumps(config))
    monkeypatch.setattr(runtime.editor, 'ask', ask)
    monkeypatch.setattr(runtime.editor, 'report_many', report)
    monkeypatch.setattr(runtime.editor, 'fail', fail)
    runtime.run(name, request)
    for step in output['steps']:
        candidate = Path(step.pop('harness_dir'))
        step['files_contents'] = {path: (candidate / path).read_text()
                                  for path in ('prompt.md', 'skills/main.md')}
    actual = json.loads(json.dumps({'prompts': prompts, 'report': output}, default=str)
                        .replace(str(tmp_path), '<WORK>'))
    assert actual == json.loads(REFERENCE.read_text())[name]
