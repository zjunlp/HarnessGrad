"""Offline wheel recipes and explicitly reviewed runtime assets."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def test_installed_presets_and_reviewed_assets_without_checkout(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    prefixes = ('console/', 'harnessgrad/', 'eval/', 'data/', 'methods/', 'tools/',
                'scenarios/', 'base_harness/', 'experiments/')
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    extras = ['experiments/__init__.py', 'data/datasets/livecodebench/execution-runtime.json',
              'data/datasets/livecodebench/requirements.lock', 'data/datasets/livecodebench/LOADER_RUNTIME.md']
    for name in set(tracked + extras):
        src = ROOT / name
        if (name in {'pyproject.toml', 'README.md', 'LICENSE', 'driver.py'} or name.startswith(prefixes)) and src.is_file():
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    (source / 'experiments/private-secret.json').write_text('{"secret":"not included"}')
    (source / 'data/scaffolds/native_research/private-secret.json').write_text('{"secret":"not included"}')
    home = tmp_path / 'home'
    home.mkdir()
    env = {'HOME': str(home), 'PATH': os.defpath, 'PYTHONNOUSERSITE': '1',
           'PIP_CONFIG_FILE': os.devnull, 'PIP_NO_INDEX': '1', 'HG_REVIEWED_ENVIRONMENT_ONLY': '1',
           'HG_AGENT_BACKEND': 'mock'}
    def run(argv, expected=0):
        result = subprocess.run(argv, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
        assert result.returncode == expected, result.stdout + result.stderr
        return result
    wheels = tmp_path / 'wheels'
    run([sys.executable, '-m', 'pip', 'wheel', '--no-index', '--no-deps', '--no-build-isolation',
         '--wheel-dir', str(wheels), str(source)])
    wheel, = wheels.glob('*.whl')
    expected_recipes = {f'experiments/{path.name}' for path in (ROOT / 'experiments').glob('*.json')}
    expected = {'data/scaffolds/native_research/SWE_EVALUATOR_SOURCE.json',
                'data/scaffolds/native_research/HARVEY_NATIVE_SOURCE.json',
                'data/scaffolds/native_research/PROVENANCE.json',
                'data/scaffolds/native_research/LICENSE.upstream',
                'data/scaffolds/native_harbor_runtime/PROVENANCE.json',
                'data/scaffolds/native_qa/PROVENANCE.json',
                'data/scaffolds/native_qa/LICENSE.upstream',
                'data/scaffolds/command_oracle_harness/harness.json',
                'data/datasets/livecodebench/cli/lcb',
                'data/datasets/livecodebench/execution-runtime.json',
                'data/datasets/livecodebench/requirements.lock',
                'data/datasets/livecodebench/LOADER_RUNTIME.md',
                'data/datasets/chessbench_puzzles/runtime.json',
                'data/datasets/chessbench_puzzles/requirements-load.txt',
                'data/datasets/chessbench_puzzles/scoring_profile.py',
                'data/datasets/appworld/execution-runtime.json',
                'data/datasets/appworld/requirements.lock',
                'data/datasets/appworld/decrypt_bundle.py',
                'data/datasets/appworld/PREPARATION.md',
                'data/runtimes/chessbench_puzzles-native.json',
                'data/runtime-support.json',
                'data/datasets/locomo/requirements-score.txt',
                'data/datasets/locomo/execution-runtime.json',
                'data/datasets/locomo/requirements.lock',
                'data/datasets/locomo/scoring_profile.py',
                'data/datasets/locomo/specs/source.json',
                'data/datasets/locomo/LICENSE.harbor',
                'data/datasets/math/execution-runtime.json',
                'data/datasets/math/requirements.lock',
                'data/datasets/math/scoring_profile.py',
                'data/datasets/math500/scoring_profile.py',
                'methods/endpoint_client.py',
                'methods/ENDPOINT_TRANSPORT.md',
                'data/datasets/memoryagentbench/requirements-prepare.txt',
                'data/datasets/spreadsheetbench2/requirements.txt',
                'data/datasets/tau2_bench/requirements.lock',
                'data/datasets/tau3_bench/requirements.lock',
                'eval/diagnostic_receipt.schema.json'}
    expected |= {f'data/scaffolds/native_research/vendor/flashrsi/benchmarks/{name}/upstream.json'
                 for name in ('harvey_lab', 'livemathbench', 'travelplanner', 'widesearch')}
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert expected_recipes <= names
        assert expected <= names, sorted(expected - names)
        assert not any('private-secret' in name for name in names)
    installed = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(installed)
    python, cli = installed / 'bin/python', installed / 'bin/harnessgrad'
    run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)])
    shutil.rmtree(source)
    assert 'quickstart' in run([str(cli), '--help']).stdout
    path = Path(run([str(cli), 'preset', 'path', 'quickstart']).stdout.strip())
    assert path.is_file() and path.is_relative_to(installed)
    run([str(cli), 'run', '--preset', 'quickstart', '--show-config'])
    run([str(python), '-I', '-B', '-m', 'tools.prepare_appworld', '--help'])
    missing = run([str(python), '-I', '-B', '-m', 'tools.prepare_appworld'], expected=1)
    assert 'AppWorld preparation failed:' in missing.stderr and 'Traceback' not in missing.stderr
    run([str(python), '-I', '-B', '-c',
         'from harnessgrad.scoring_identity import resolve; assert resolve("chessbench_puzzles")["sha256"]'])
    run([str(python), '-I', '-B', '-c',
         'from harnessgrad.scoring_identity import resolve; assert all(resolve(name)["sha256"] for name in ("math", "math500", "locomo"))'])
    custom = tmp_path / 'custom.json'
    custom.write_text('{"dataset":"demo","harness":"loop","sampling":"all"}')
    run([str(cli), 'run', '--config', str(custom), '--show-config'])
    run([str(cli), 'run', '--preset', '../quickstart', '--show-config'], expected=2)
    run([str(cli), 'run', '--preset', 'quickstart', '--config', str(custom)], expected=2)
    code = '''from pathlib import Path
from data.scaffolds.native_research import swe_official
from data.scaffolds.command_control import ORACLE_HARNESS
assert (ORACLE_HARNESS / 'harness.json').is_file()
assert (swe_official.HERE / 'SWE_EVALUATOR_SOURCE.json').is_file()
'''
    run([str(python), '-I', '-B', '-c', code])
    script = Path(run([str(python), '-I', '-B', '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))']).stdout.strip()) / 'data/datasets/livecodebench/cli/lcb'
    run([str(python), '-I', '-B', str(script), 'submit', 'synthetic solution'])
    assert (tmp_path / 'solution.py').read_text() == 'synthetic solution\n'
