"""Registry maintenance tools keep their old CLI and module entrypoints."""
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('check_dataset_blockers', 'apply_blocker_proposal', 'merge_blocker_registry')


@pytest.mark.parametrize('name', NAMES)
def test_legacy_and_current_modules_share_identity(name):
    old = importlib.import_module('tools.' + name)
    new = importlib.import_module('tools.registry_ops.' + name)
    assert old is new
    assert Path(new.__file__).resolve() == ROOT / 'tools/registry_ops' / (name + '.py')
    if hasattr(new, 'ROOT'):
        assert new.ROOT == ROOT


@pytest.mark.parametrize('name', NAMES[:2])
def test_old_and_new_cli_help_match(name, tmp_path):
    results = [subprocess.run([sys.executable, str(ROOT / folder / (name + '.py')), '--help'],
                              cwd=tmp_path, capture_output=True, text=True, timeout=20)
               for folder in ('tools', 'tools/registry_ops')]
    assert all(result.returncode == 0 for result in results)
    # argparse includes the invoked filename, which is the same in both paths.
    assert results[0].stdout == results[1].stdout


def test_old_and_new_merge_commands_write_same_synthetic_result(tmp_path):
    base = {'review': {'name': 'base'}, 'datasets': [], 'shared_blockers': []}
    ours = {**base, 'review': {'name': 'ours'}}
    theirs = {**base, 'review': {'name': 'base', 'extra': 'theirs'}}
    inputs = []
    for name, value in (('base', base), ('ours', ours), ('theirs', theirs)):
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value))
        inputs.append(str(path))
    outputs = []
    for index, folder in enumerate(('tools', 'tools/registry_ops')):
        output = tmp_path / f'output-{index}.json'
        proc = subprocess.run([sys.executable, str(ROOT / folder / 'merge_blocker_registry.py'),
                               *inputs, str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=20)
        assert proc.returncode == 0, proc.stderr
        outputs.append(json.loads(output.read_text()))
    assert outputs[0] == outputs[1]
    assert outputs[0]['review'] == {'name': 'ours', 'extra': 'theirs'}


def test_taxonomy_reader_supports_historical_roots_and_prefers_current(tmp_path):
    from tools.registry_ops.check_dataset_blockers import taxonomy_declarations

    tools = tmp_path / 'tools'
    tools.mkdir()
    legacy = tools / 'dataset_classes.py'
    legacy.write_text("CAPABILITY = {'old': ('reasoning', 'text')}\nEXERCISES = {'old': ['context']}\n")
    assert set(taxonomy_declarations(tmp_path)['CAPABILITY']) == {'old'}
    current = tools / 'dataset_maintenance' / 'dataset_classes.py'
    current.parent.mkdir()
    current.write_text("CAPABILITY = {'new': ('reasoning', 'text')}\nEXERCISES = {'new': ['context']}\n")
    assert set(taxonomy_declarations(tmp_path)['CAPABILITY']) == {'new'}
    current.write_text('CAPABILITY = untrusted_call()\nEXERCISES = {}\n')
    with pytest.raises(ValueError, match='must be a literal'):
        taxonomy_declarations(tmp_path)
