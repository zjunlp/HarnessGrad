"""Execute the pinned Harbor scorer on real Git branches without model calls.

The fixture runner tests the produced merged tree. Real released Click tests
are exercised separately by the observed-image oracle/noop controls.
"""
import importlib.util
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'data/datasets/cooperbench'
spec = importlib.util.spec_from_file_location('cooperbench_protocol_test', ASSETS / 'lib/protocol.py')
protocol = importlib.util.module_from_spec(spec); spec.loader.exec_module(protocol)


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo, text=True)


def test_vendored_harbor_scorer_is_exact_pinned_source():
    protocol.read_checked(ASSETS / 'vendor/harbor-test.sh', protocol.HARBOR_SCORER_SHA256)


def test_offline_conversion_preserves_editable_builds_and_test_commands(monkeypatch):
    source = '#!/bin/bash\nset -e\npip install -e .\npip install pytest\npython -m pytest tests/test_a.py -v\n'
    monkeypatch.setattr(protocol, 'recipe', lambda group: {
        'runner_sha256': protocol.sha(source.encode()), 'family': 'python',
        'mode': 'editable-venv', 'install_lines': [2, 4]})
    block, actual = protocol.prepare_recipe(source, 'fixture')
    assert block == 'pip install -e .\npip install pytest\n'
    assert actual.endswith('python -m pytest tests/test_a.py -v\n')
    assert '--no-index --no-deps --no-build-isolation -e .' in actual
    assert 'venv --system-site-packages' in actual
    with pytest.raises(ValueError, match='reviewed source'):
        protocol.prepare_recipe(source + '# changed', 'fixture')


@pytest.mark.parametrize('mode,expected', [('both', 1), ('one_wrong', 0), ('missing', 0), ('invalid', 0), ('test_only', 0), ('union', 1)])
def test_official_filter_merge_and_both_feature_rule(tmp_path, mode, expected):
    repo = tmp_path / 'repo'; repo.mkdir()
    git(repo, 'init', '-q'); git(repo, 'config', 'user.email', 'control@example.test'); git(repo, 'config', 'user.name', 'control')
    (repo / 'a.py').write_text('A=0\n'); (repo / 'b.py').write_text('B=0\n')
    (repo / 'tests').mkdir(); (repo / 'tests/test_a.py').write_text('assert False\n')
    git(repo, 'add', '.'); git(repo, 'commit', '-qm', 'base')
    (repo / 'a.py').write_text('A=1\n'); patch_a = git(repo, 'diff'); git(repo, 'checkout', '--', '.')
    (repo / 'b.py').write_text('B=1\n'); patch_b = git(repo, 'diff'); git(repo, 'checkout', '--', '.')
    if mode == 'union':
        (repo / 'a.py').write_text('B=1\n'); patch_b = git(repo, 'diff'); git(repo, 'checkout', '--', '.')
    if mode == 'one_wrong': patch_b = ''
    if mode == 'invalid': patch_a = 'invalid diff\n'
    if mode == 'test_only':
        (repo / 'tests/test_a.py').write_text('assert True\n'); patch_a = git(repo, 'diff'); git(repo, 'checkout', '--', '.')
    for name, patch in [('agent1', patch_a), ('agent2', patch_b)]:
        folder = tmp_path / 'shared' / name; folder.mkdir(parents=True)
        (folder / 'agent.done').touch()
        if mode != 'missing': (folder / 'agent.patch').write_text(patch)
    tests = tmp_path / 'private/tests'; tests.mkdir(parents=True)
    (tests / 'tests1.patch').write_text('a'); (tests / 'tests2.patch').write_text('b')
    (tmp_path / 'private/runner.sh').write_text('''#!/bin/bash
set -e
git apply "PATCHROOT/$2"
if [ "$1" = tests1.patch ]; then grep -q 'A=1' a.py; else grep -q 'B=1' b.py; fi
'''.replace('PATCHROOT', str(tmp_path / 'patches')).replace('b.py', 'a.py' if mode == 'union' else 'b.py'))
    official = (ASSETS / 'vendor/harbor-test.sh').read_text()
    adapted = protocol.adapt_scorer(official).replace('/workspace', str(tmp_path))
    script = tmp_path / 'test.sh'; script.write_text(adapted)
    proc = subprocess.run(['bash', str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert int((tmp_path / 'logs/verifier/reward.txt').read_text()) == expected


def test_all_30_pinned_recipes_preserve_shell_and_test_commands(tmp_path):
    import json, os
    root = Path(os.environ.get('HG_COOPERBENCH_DATA', ROOT.parent / 'datasets/cooperbench')) / 'upstream/dataset'
    if not root.exists():
        pytest.skip('pinned CooperBench source needed for full recipe drift check')
    recipes = json.loads((ASSETS / 'recipes.json').read_text())
    assert len(recipes) == 30
    for group, recipe in recipes.items():
        source = (root / group / 'runner.sh').read_text()
        block, adapted = protocol.prepare_recipe(source, group)
        script = tmp_path / 'runner.sh'; script.write_text(adapted)
        subprocess.run(['bash', '-n', str(script)], check=True)
        dockerfile, install, _ = protocol.image_recipe('sha256:' + 'a'*64, source, group)
        script.write_text(install); subprocess.run(['bash', '-n', str(script)], check=True)
        for line in source.splitlines():
            if (' -m pytest' in line or 'go test ' in line or 'cargo build ' in line or 'cargo test ' in line):
                assert line in adapted, group
        assert 'CARGO_NET_OFFLINE=true' in dockerfile if recipe['family'] == 'rust' else True


def test_nested_runner_timeout_is_unmeasured_even_if_harbor_writes_zero(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location('cooperbench_verify_test', ASSETS / 'verify/check.py')
    check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)
    (tmp_path / 'private').mkdir()
    (tmp_path / 'private/case.json').write_text(json.dumps({'task_id': 'fixture', 'base_commit': 'base',
        'private_sha256': {}, 'provenance': {}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(check.subprocess, 'check_output', lambda *args, **kwargs: 'base\n')
    def scorer(*args, **kwargs):
        out = tmp_path / 'logs/verifier'; out.mkdir(parents=True)
        (out / 'reward.txt').write_text('0')
        (out / 'reward.json').write_text('{"reward":0}')
        (out / 'test-output.txt').write_text('=== Feature 1 tests (exit=124) ===\n')
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(check.subprocess, 'run', scorer)
    assert check.main() == 3
    assert not (tmp_path / 'verifier/reward.txt').exists()
    assert json.loads((tmp_path / 'verifier/evidence.json').read_text())['measured'] is False
