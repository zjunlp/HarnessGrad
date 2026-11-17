"""GEPA's scripted experiment port retains its pre-extraction behavior."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from native_helpers import fixture, runtime

REFERENCE = Path(__file__).resolve().parents[1] / 'fixtures/policy_ports/pre_refactor_gepa.json'


def test_pre_refactor_gepa_prompt_candidate_and_report(tmp_path, monkeypatch):
    _base, request = fixture(tmp_path)
    prompts, output = [], {}

    def ask(prompt, **_kwargs):
        prompts.append(prompt.replace(str(tmp_path), '<WORK>'))
        return {'instruction': 'Revised synthetic instruction\n'}

    def report(_request, **kwargs):
        output.update(kwargs)
        return 0

    def fail(_request, **kwargs):
        pytest.fail(f"policy failed: {kwargs['exc']}")

    monkeypatch.setenv('HG_POLICY_CONFIG', json.dumps({'reflection_minibatch_size': 1}))
    monkeypatch.setattr(runtime.editor, 'ask', ask)
    monkeypatch.setattr(runtime.editor, 'report_many', report)
    monkeypatch.setattr(runtime.editor, 'fail', fail)
    runtime.run('gepa', request)
    for step in output['steps']:
        candidate = Path(step.pop('harness_dir'))
        step['files_contents'] = {path: (candidate / path).read_text()
                                  for path in ('prompt.md', 'skills/main.md')}
    actual = json.loads(json.dumps({'prompts': prompts, 'report': output}, default=str)
                        .replace(str(tmp_path), '<WORK>'))
    assert actual == json.loads(REFERENCE.read_text())


@pytest.mark.parametrize('scenario', ['perfect', 'duplicate', 'empty', 'wrong_type'])
def test_pre_refactor_gepa_boundaries(scenario, tmp_path, monkeypatch):
    points = ([{'round': 0, 'score_kind': 'training', 'score': 1.0,
                'per_task': {'t1': 1.0, 't2': 1.0}, 'identity': {'harness_sha': 'root'}}]
              if scenario == 'perfect' else None)
    _base, request = fixture(tmp_path, points=points)
    prompts, output = [], {}

    def ask(prompt, **_kwargs):
        prompts.append(prompt.replace(str(tmp_path), '<WORK>'))
        return {'instruction': {'duplicate': 'Baseline instruction\n', 'empty': '',
                                'wrong_type': 42, 'perfect': 'unexpected'}[scenario]}

    def report(_request, **kwargs):
        output.update(kwargs)
        return 0

    def fail(_request, **kwargs):
        output.update(failure=str(kwargs['exc']), error_type=type(kwargs['exc']).__name__,
                      state=kwargs['state'])
        return 0

    monkeypatch.setenv('HG_POLICY_CONFIG', json.dumps({'reflection_minibatch_size': 1}))
    monkeypatch.setattr(runtime.editor, 'ask', ask)
    monkeypatch.setattr(runtime.editor, 'report_many', report)
    monkeypatch.setattr(runtime.editor, 'fail', fail)
    runtime.run('gepa', request)
    for step in output.get('steps', []):
        candidate = Path(step.pop('harness_dir'))
        step['files_contents'] = {path: (candidate / path).read_text()
                                  for path in ('prompt.md', 'skills/main.md')}
    actual = json.loads(json.dumps({'prompts': prompts, 'report': output}, default=str)
                        .replace(str(tmp_path), '<WORK>'))
    reference = REFERENCE.with_name('pre_refactor_gepa_boundaries.json')
    assert actual == json.loads(reference.read_text())[scenario]
