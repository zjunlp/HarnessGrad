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
        assert kwargs['env']['TMPDIR'] == str(tmp_path / '.test-tmp')
        assert (tmp_path / '.test-tmp').is_dir()
        out = tmp_path / 'logs/verifier'; out.mkdir(parents=True)
        (out / 'reward.txt').write_text('0')
        (out / 'reward.json').write_text('{"reward":0}')
        (out / 'test-output.txt').write_text('=== Feature 1 tests (exit=124) ===\n')
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(check.subprocess, 'run', scorer)
    assert check.main() == 3
    assert not (tmp_path / 'verifier/reward.txt').exists()
    assert json.loads((tmp_path / 'verifier/evidence.json').read_text())['measured'] is False


@pytest.mark.parametrize('failed_worker', [True, False])
def test_worker_completion_required_even_when_official_patch_score_passes(tmp_path, monkeypatch, failed_worker):
    """Real sandbox peers; pinned Harbor scorer on a synthetic two-feature Git repo.

    Only verifier placement is replaced: its official shell still executes. This
    exercises exit/artifact handling without needing a released Docker image.
    """
    import json
    import shutil
    from eval.run import cooperative, runner
    from test_shared_dataset_runtime import harness

    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    scoring = tmp_path / 'scoring'; scoring.mkdir()
    repo = scoring / 'repo'; repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.email', 'control@example.test')
    git(repo, 'config', 'user.name', 'control')
    for name in ('a', 'b'):
        (repo / (name + '.py')).write_text(name.upper() + '=0\n')
    git(repo, 'add', '.'); git(repo, 'commit', '-qm', 'base')
    patches = {}
    for name in ('a', 'b'):
        (repo / (name + '.py')).write_text(name.upper() + '=1\n')
        patches[name] = git(repo, 'diff') if failed_worker else ''
        git(repo, 'checkout', '--', '.')
    private = scoring / 'private'; (private / 'tests').mkdir(parents=True)
    for name in ('tests1.patch', 'tests2.patch'):
        (private / 'tests' / name).write_text('fixture')
    (private / 'runner.sh').write_text('''#!/bin/bash
set -e
git apply "PATCHROOT/$2"
if [ "$1" = tests1.patch ]; then grep -q 'A=1' a.py; else grep -q 'B=1' b.py; fi
'''.replace('PATCHROOT', str(scoring / 'patches')))
    source = protocol.read_checked(ASSETS / 'vendor/harbor-test.sh', protocol.HARBOR_SCORER_SHA256)
    script = scoring / 'test.sh'
    script.write_text(protocol.adapt_scorer(source).replace('/workspace', str(scoring)))
    official_scores = []

    def verify(work, *args, **kwargs):
        for name, agent in [('a', 'agent1'), ('b', 'agent2')]:
            target = scoring / 'shared' / agent; target.mkdir(parents=True)
            shutil.copyfile(work / 'workers' / name / 'agent.patch', target / 'agent.patch')
            (target / 'agent.done').touch()
        proc = subprocess.run(['bash', str(script)], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        score = int((scoring / 'logs/verifier/reward.txt').read_text())
        official_scores.append(score)
        return {'kind': 'command', 'score': score, 'passed': bool(score)}

    monkeypatch.setattr(cooperative, '_verify', verify)
    candidate = harness(tmp_path, f'''import json,sys
me=json.loads(Path(a.task).read_text())['worker_id']
(w/'agent.patch').write_text({patches!r}[me])
sys.exit(1 if {failed_worker!r} and me=='a' else 0)
''')
    workers = [{'id': name, 'goal': 'synthetic feature', 'artifacts': ['agent.patch'],
                'env': {'kind': 'files', 'network': 'none'}} for name in ('a', 'b')]
    result = runner.run_one(candidate, {'task_id': 'pair-exit-control'}, setup={'workers': workers},
                           verifier={'kind': 'command', 'argv': ['unused']},
                           recordings_root=tmp_path / 'archives')
    assert official_scores == [1 if failed_worker else 0]
    assert result['worker_group']['workers']['a']['exit_code'] == (1 if failed_worker else 0)
    assert result['worker_group']['workers']['b']['exit_code'] == 0
    verdict = result['verdict']
    if failed_worker:
        assert verdict['score'] is None and verdict['outcome'] == 'unmeasured'
        assert not verdict['passed'] and 'worker a failed: exit code 1' in verdict['unmeasured']
    else:
        assert verdict['score'] == 0 and not verdict['passed']
        assert 'unmeasured' not in verdict
    archive = Path(result['worker_group']['archive'])
    assert (archive / 'workers/a/agent.patch').read_text() == patches['a']
    assert result['runtime_services']['workers']['cleanup']['complete']

def test_go_preparation_accepts_no_external_modules(tmp_path, monkeypatch):
    source = "#!/bin/bash\ngo test ./...\n"
    monkeypatch.setattr(protocol, 'recipe', lambda group: {
        'runner_sha256': protocol.sha(source.encode()), 'family': 'go',
        'mode': 'go', 'install_lines': None})
    _, install, _ = protocol.image_recipe('base', source, 'fixture')
    workspace = tmp_path / 'workspace'
    (workspace / 'repo').mkdir(parents=True)
    binary = tmp_path / 'bin'; binary.mkdir()
    go = binary / 'go'; go.write_text('#!/bin/sh\nexit 0\n'); go.chmod(0o755)
    script = install.replace('/workspace', str(workspace)).replace('/go/pkg/mod', str(tmp_path / 'absent-cache'))
    import os
    subprocess.run(['bash', '-c', script], check=True, env={**os.environ, 'PATH': str(binary) + ':' + os.environ['PATH']})
    assert (workspace / '.gomodcache').is_dir()
