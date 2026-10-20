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


def test_relocation_preserves_official_test_diff_filter(tmp_path):
    official = (ASSETS / 'vendor/harbor-test.sh').read_text()
    adapted = protocol.adapt_scorer(official)
    with pytest.raises(ValueError, match='pinned source'):
        protocol.adapt_scorer(official + '\n# unknown source\n')
    function = lambda source: 'filter_test_files() {\n' + source.split('filter_test_files() {\n', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
    assert function(adapted) == function(official)
    paths = ['tests/helpers.py', 'pkg/tests/utils.py', 'pkg/test/helper.py',
             'pkg/test_parser.py', 'pkg/parser_test.py', 'pkg/tests.py', 'pkg/core.py']
    patch = ''.join(f'diff --git a/{path} b/{path}\n+candidate change\n' for path in paths)
    outputs = []
    for i, source in enumerate((official, adapted)):
        target = tmp_path / f'patch-{i}'; target.write_text(patch)
        subprocess.run(['bash', '-c', function(source) + 'filter_test_files "$1"', 'filter', str(target)], check=True)
        outputs.append(target.read_text())
    assert outputs == ['diff --git a/pkg/core.py b/pkg/core.py\n+candidate change\n'] * 2


@pytest.mark.parametrize('group,pairs', [
    ('dspy_task/task8563', 15), ('react_hook_form_task/task85', 10),
])
def test_patch_repairs_preserve_released_tests_and_scorer(group, pairs):
    from data.datasets import cooperbench as dataset

    source = dataset.DATASET_ROOT / 'upstream/dataset' / group
    if not (source.exists() and (dataset.DATASET_ROOT / 'prepared-v1/manifest.json').exists()):
        pytest.skip('prepared CooperBench inputs and pinned source needed for patch regression')
    rows = [row for row in dataset.roster().values() if row['group'] == group]
    assert len(rows) == pairs
    scorer = protocol.adapt_scorer((ASSETS / 'vendor/harbor-test.sh').read_text())
    for row in rows:
        # Every pair must retain the released feature assertions. Appending the
        # obsolete Harbor fix conflicts with the import and format normalization
        # already supplied by features 2-6, before pytest can even run.
        assert 'tests/testfix.patch' not in row['files_sha256']
        assert dataset.task_file(row, 'test.sh') == scorer
        features = row['task_id'].rsplit('/f', 1)[1].split('-')
        for index, feature in enumerate(features, 1):
            assert dataset.task_file(row, f'tests/tests{index}.patch') == (
                source / f'feature{feature}/tests.patch').read_text()
        if group == 'dspy_task/task8563':
            assert dataset.task_file(row, 'solution/combined.patch') == (source / 'combined.patch').read_text()


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


@pytest.mark.parametrize('mode,expected', [('both', 1), ('one_wrong', 0), ('missing', 0), ('invalid', 0), ('test_only', 0), ('helper_injection', 1), ('union', 1)])
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
    if mode == 'helper_injection':
        # This deliberately unapplicable test hunk must disappear before Git
        # applies the valid implementation patch. Path relocation must preserve
        # the official '/tests/' filtering predicate.
        patch_a += ('diff --git a/tests/helpers.py b/tests/helpers.py\n'
                    '--- a/tests/helpers.py\n+++ b/tests/helpers.py\n'
                    '@@ -1 +1 @@\n-never-present\n+candidate-injection\n')
    if mode == 'one_wrong': patch_b = ''
    if mode == 'invalid': patch_a = 'invalid diff\n'
    if mode == 'test_only':
        (repo / 'tests/test_a.py').write_text('assert True\n'); patch_a = git(repo, 'diff'); git(repo, 'checkout', '--', '.')
    for name, patch in [('agent1', patch_a), ('agent2', patch_b)]:
        folder = tmp_path / 'shared' / name; folder.mkdir(parents=True)
        (folder / 'logs').mkdir(); (folder / 'logs/actor.log').write_text(name)
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
    for name in ('agent1', 'agent2'):
        assert (tmp_path / 'logs/agent' / name / 'actor.log').read_text() == name


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
        assert ('export NUM_WORKERS=1' in adapted) == (group == 'dspy_task/task8587')
        script = tmp_path / 'runner.sh'; script.write_text(adapted)
        subprocess.run(['bash', '-n', str(script)], check=True)
        dockerfile, install, _ = protocol.image_recipe('sha256:' + 'a'*64, source, group)
        script.write_text(install); subprocess.run(['bash', '-n', str(script)], check=True)
        for line in source.splitlines():
            if (' -m pytest' in line or 'go test ' in line or 'cargo build ' in line or 'cargo test ' in line):
                assert line in adapted, group
        assert 'CARGO_NET_OFFLINE=true' in dockerfile if recipe['family'] == 'rust' else True


@pytest.mark.parametrize('exit_code', [124, 137])
def test_nested_runner_timeout_is_unmeasured_even_if_harbor_writes_zero(tmp_path, monkeypatch, capsys, exit_code):
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
        (out / 'test-output.txt').write_text('verbose output\n' * 5000 + f'=== Feature 1 tests (exit={exit_code}) ===\n')
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(check.subprocess, 'run', scorer)
    assert check.main() == 3
    assert not (tmp_path / 'verifier/reward.txt').exists()
    assert json.loads((tmp_path / 'verifier/evidence.json').read_text())['measured'] is False
    stderr = capsys.readouterr().err
    assert len(stderr) < 4800
    # The platform retains only the final 800 stderr characters on this path.
    assert f'feature runner timeout/infrastructure failure: [{exit_code}]' in stderr[-800:]


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


def test_auxiliary_python_preserves_official_toolchain(monkeypatch):
    source = "#!/bin/bash\npnpm test tests/example.test.tsx\n"
    monkeypatch.setattr(protocol, 'recipe', lambda group: {
        'runner_sha256': protocol.sha(source.encode()), 'family': 'node',
        'mode': 'node', 'install_lines': None})
    dockerfile, install, runner = protocol.image_recipe('task-image', source, 'fixture', 'sha256:helper')
    assert dockerfile.startswith('FROM sha256:helper AS helper_python\nFROM task-image\n')
    assert 'COPY --from=helper_python /usr/local /opt/hg-python' in dockerfile
    assert 'ENV LD_LIBRARY_PATH=/opt/hg-python/lib' in dockerfile
    assert 'apt-get' not in install
    assert runner == source


def test_build_requirements_support_python310_and_nested_project(tmp_path, monkeypatch):
    import builtins
    import sys
    import tomllib

    source = '#!/bin/bash\npip install -e .\n'
    monkeypatch.setattr(protocol, 'recipe', lambda group: {
        'runner_sha256': protocol.sha(source.encode()), 'family': 'python',
        'mode': 'editable-venv', 'install_lines': [1, 2]})
    _, install, _ = protocol.image_recipe('base', source, 'fixture')
    helper = install.split("<<'BUILD_REQUIREMENTS'\n", 1)[1].split('\nBUILD_REQUIREMENTS', 1)[0]
    (tmp_path / 'pyproject.toml').write_text('[build-system]\nrequires = ["setuptools>=62.4"]\n')
    nested = tmp_path / 'llama-index-core'; nested.mkdir()
    (nested / 'pyproject.toml').write_text('[build-system]\nrequires = ["poetry-core==2.1.3"]\n')
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda argv, **kwargs: calls.append(argv))
    original_import = builtins.__import__

    def python310_import(name, *args, **kwargs):
        if name == 'tomllib':
            raise ModuleNotFoundError(name)
        if name == 'tomli':
            return tomllib
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', python310_import)
    exec(helper, {})
    assert calls == [[sys.executable, '-m', 'pip', 'install', dependency] for dependency in
                     ('tomli==2.2.1', 'setuptools>=62.4', 'poetry-core==2.1.3')]


def test_preparation_resolves_dynamic_editable_backend_dependencies(tmp_path):
    """Real backend hooks run in each baseline project, before offline builds."""
    import json
    import os
    import sys
    source = '#!/bin/bash\n'
    from unittest.mock import patch
    with patch.object(protocol, 'prepare_recipe', return_value=('', source)), patch.object(protocol, 'recipe', return_value={'family': 'python'}):
        _, install, _ = protocol.image_recipe('sha256:' + 'a' * 64, source, 'fixture')
    code = install.split("python3 - <<'BUILD_REQUIREMENTS'\n", 1)[1].split('\nBUILD_REQUIREMENTS', 1)[0]
    fake_modules = tmp_path / 'fake_modules'; fake_modules.mkdir()
    # Capture dependency installation without any package index or download.
    (fake_modules / 'pip.py').write_text("import json,os,sys\nwith open(os.environ['PIP_CAPTURE'],'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n")
    for name, required in [('.', 'root-editable==1.2.3'), ('llama-index-core', 'core-editable==4.5.6')]:
        project = tmp_path / name; (project / 'backend').mkdir(parents=True)
        (project / 'pyproject.toml').write_text('[build-system]\nrequires=["static-backend==7.8.9"]\nbuild-backend="local_backend"\nbackend-path=["backend"]\n')
        (project / 'backend/local_backend.py').write_text(
            'from pathlib import Path\ndef get_requires_for_build_editable(settings):\n'
            '    assert settings is None\n'
            f'    assert Path.cwd() == Path({str(project)!r})\n'
            f'    return [{required!r}]\n')
    capture = tmp_path / 'installs.jsonl'
    subprocess.run([sys.executable, '-c', code], cwd=tmp_path, check=True,
                   env={**os.environ, 'PYTHONPATH': str(fake_modules), 'PIP_CAPTURE': str(capture)})
    assert [json.loads(line) for line in capture.read_text().splitlines()] == [
        ['install', 'static-backend==7.8.9'], ['install', 'root-editable==1.2.3'],
        ['install', 'static-backend==7.8.9'], ['install', 'core-editable==4.5.6']]


@pytest.mark.parametrize("kind,group", [("hf_mirror", "dottxt_ai_outlines_task/task1706"),
    ("https_asset_mirror", "llama_index_task/task18813")])
def test_mirror_derivation_preserves_base_runtime_and_records_verified_layer(tmp_path, monkeypatch, kind, group):
    import json
    from test_cooperbench_hf_mirror import fixture_assets
    spec = importlib.util.spec_from_file_location('cooperbench_prepare_test', ASSETS / 'prepare.py')
    preparation = importlib.util.module_from_spec(spec); spec.loader.exec_module(preparation)
    base = tmp_path / 'base'; base.mkdir()
    config = {'group': group, 'image': 'sha256:' + 'a' * 64, 'python': '/usr/local/bin/python3',
              'identity': {'base_commit': 'base', 'workspace_bytes': 1234, 'workspace_inodes': 5},
              'resources': {'workspace_bytes': 2 << 30, 'workspace_inodes': 200000, 'memory_mb': 4096}}
    for name, field in [('runner.sh', 'runner_sha256'), ('Dockerfile', 'dockerfile_sha256'),
                        ('requirements.freeze.txt', 'dependencies_sha256'),
                        ('prepare-dependencies.sh', 'preparation_sha256')]:
        (base / name).write_text('original ' + name)
        config[field] = protocol.sha((base / name).read_bytes())
    original = json.dumps(config).encode(); (base / 'runtime.json').write_bytes(original)
    root = tmp_path / 'derived'; (root / 'prepared-v1').mkdir(parents=True)
    (root / 'prepared-v1/manifest.json').write_text(json.dumps({'tasks': [{'group': group, 'base_commit': 'base'}]}))
    if kind == 'hf_mirror':
        assets = tmp_path / 'assets'; assets.mkdir(); fixture_assets(assets)
    else:
        import os
        path = os.environ.get('HG_COOPERBENCH_HTTPS_TEST_ASSETS')
        if not path:
            pytest.skip('requires explicitly prepared public HTTPS assets')
        assets = Path(path)
    monkeypatch.setattr(preparation, 'command', lambda *args: json.dumps([{'Id': config['image'] if args[-1] == config['image'] else 'sha256:' + 'b' * 64}]))
    builds = []
    monkeypatch.setattr(preparation.subprocess, 'run', lambda argv, **kwargs: builds.append(argv))
    preparation.prepare_mirror_runtime(root, group, base, assets, kind)
    result = json.loads((root / 'runtimes' / group.replace('/', '-') / 'runtime.json').read_text())
    assert (base / 'runtime.json').read_bytes() == original
    assert result['image'] == 'sha256:' + 'b' * 64 and result['python'] == '/opt/hg-hf/python'
    assert result['dependencies_sha256'] == config['dependencies_sha256']
    assert result[kind]['base_image'] == config['image']
    assert result[kind]['base_runtime_sha256'] == protocol.sha(original)
    assert len(builds) == 1 and '--network=none' in builds[0]
    if kind == 'https_asset_mirror':
        assert result['resources'] == config['resources']
        assert result[kind]['cache_bound_bytes'] == 0
        assert (root / 'runtimes' / group.replace('/', '-') / 'https-asset-mirror/assets/server.key').stat().st_mode & 0o777 == 0o400
    for existing in ('hf_mirror', 'https_asset_mirror'):
        (base / 'runtime.json').write_text(json.dumps({**config, existing: {'present': True}}))
        with pytest.raises(ValueError, match='mirror identity mismatch'):
            preparation.prepare_mirror_runtime(root, group, base, assets, kind)
    (base / 'runtime.json').write_bytes(original)
    with pytest.raises(ValueError, match='not reviewed'):
        preparation.prepare_mirror_runtime(root, 'unreviewed/task1', base, assets, kind)
    with pytest.raises(FileExistsError):
        preparation.prepare_mirror_runtime(root, group, base, assets, kind)
    (base / 'runner.sh').write_text('changed')
    with pytest.raises(ValueError, match='source digest mismatch'):
        preparation.prepare_mirror_runtime(root, group, base, assets, kind)


@pytest.mark.parametrize('tracked', [True, False])
def test_preparation_restores_tracked_lock_without_changing_resolved_dependencies(tmp_path, tracked):
    """A candidate's first Git diff must exclude preparation-only lock changes."""
    from unittest.mock import patch
    repo = tmp_path / 'repo'; repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'fixture@example.test'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Fixture'], check=True)
    (repo / 'uv.lock').write_text('committed lock\n')
    (repo / 'source.py').write_text('baseline\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'source.py', *(['uv.lock'] if tracked else [])], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'baseline'], check=True)
    # The dependency operation's effects must survive; only its source-tree
    # lock rewrite is restored. No package installation or network is needed.
    block = "printf 'resolved lock\\n' > uv.lock\nprintf 'installed dependency\\n' > installed.txt\n"
    with patch.object(protocol, 'prepare_recipe', return_value=(block, 'runner')), patch.object(protocol, 'recipe', return_value={'family': 'python'}):
        _, install, _ = protocol.image_recipe('sha256:' + 'a' * 64, 'runner', 'fixture')
    snapshots = tmp_path / 'snapshots'
    install = install.replace('/workspace/repo', str(repo)).replace('/opt/hg-cooperbench-locks', str(snapshots))
    subprocess.run(['bash', '-c', install], check=True)
    assert (repo / 'installed.txt').read_text() == 'installed dependency\n'
    assert subprocess.check_output(['git', '-C', str(repo), 'diff'], text=True) == ''
    if tracked:
        assert (repo / 'uv.lock').read_text() == 'committed lock\n'
        assert (snapshots / 'baseline.lock').read_text() == 'committed lock\n'
        assert (snapshots / 'input.lock').read_text() == 'committed lock\n'
        assert (snapshots / 'resolved.lock').read_text() == 'resolved lock\n'
    else:
        assert (repo / 'uv.lock').read_text() == 'resolved lock\n'
        assert not snapshots.exists()


def test_preparation_requires_explicit_capacity_before_docker(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('cooperbench_resource_prepare_test', ASSETS / 'prepare.py')
    preparation = importlib.util.module_from_spec(spec); spec.loader.exec_module(preparation)
    monkeypatch.setattr(preparation, 'command', lambda *a: pytest.fail('Docker must not run without allocation'))
    with pytest.raises(ValueError, match='workspace_bytes, workspace_inodes, memory_mb'):
        preparation.prepare_image(tmp_path, tmp_path, 'repo/task1', False)
    # Explicit allocations above all former hard envelopes are accepted unchanged.
    resources = {'workspace_bytes': 32 << 30, 'workspace_inodes': 3000000, 'memory_mb': 65536}
    base = tmp_path / 'base'; base.mkdir()
    import json
    (base / 'runtime.json').write_text(json.dumps({'resources': resources}))
    actual, source = preparation.selected_resources(base)
    assert actual == resources and source['base_runtime_sha256']
    seed = {'workspace_bytes': 30 << 30, 'workspace_inodes': 2500000}
    preparation.require_seed_capacity(actual, seed)
    with pytest.raises(ValueError, match='measured seed/cache'):
        preparation.require_seed_capacity(actual, seed, cache_bytes=3 << 30)
    resource_file = tmp_path / 'resources.json'
    resource_file.write_text(json.dumps({**resources, 'memory_mb': 131072}))
    actual, origin = preparation.selected_resources(base, resource_file)
    assert actual == {**resources, 'memory_mb': 131072}
    assert origin == {'resources_sha256': preparation.sha(resource_file.read_bytes())}
    # A file is a complete selection, never a partial override of another source.
    resource_file.write_text(json.dumps({'memory_mb': 131072}))
    with pytest.raises(ValueError, match='workspace_bytes, workspace_inodes'):
        preparation.selected_resources(base, resource_file)
    for invalid in ({**resources, 'workspace_bytes': 3}, {**resources, 'cpus': None},
                    {**resources, 'pids': -1}, {**resources, 'unexpected': 1}):
        resource_file.write_text(json.dumps(invalid))
        with pytest.raises(ValueError):
            preparation.selected_resources(resources_file=resource_file)
