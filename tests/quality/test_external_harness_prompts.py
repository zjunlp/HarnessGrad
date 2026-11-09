"""Public task delivery must work for text, files and stateful environments."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def adapter(name):
    path = ROOT / 'base_harness' / name / 'agent.py'
    spec = importlib.util.spec_from_file_location('_prompt_' + name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codex_prompt_keeps_environment_submission_and_inputs():
    prompt = adapter('codex').task_prompt({'goal': 'Use the service and submit its state.',
                                           'inputs': {'path': 'fixture.json'}})
    assert 'Use the service and submit its state.' in prompt
    assert 'fixture.json' in prompt
    assert 'modify the actual task files' not in prompt
    assert 'answer.txt' not in prompt


def test_dsh_prompt_separates_preamble_and_preserves_state_task():
    module = adapter('deepseek-harness')
    knobs = json.loads((ROOT / 'base_harness/deepseek-harness/knobs.json').read_text())
    prompt = module.task_prompt({'goal': 'Use the service and submit its state.',
                                 'inputs': {'path': 'fixture.json'}}, knobs)
    assert '\n\nUse the service and submit its state.' in prompt
    assert 'fixture.json' in prompt
    assert 'work is the files' not in prompt
    assert 'answer.txt' not in prompt
    assert module.task_prompt({'goal': 'GOAL'}, {'prompt': {'preamble': 'Custom.'}}) == 'Custom.\n\nGOAL'
