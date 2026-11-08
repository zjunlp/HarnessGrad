"""Opt-in real QA provider controls with authored Parquet rows and no model calls.

Set HG_TEST_DATASET_RUNTIME_ROOT to an explicitly prepared GSM8K runtime store.
These tests never prepare packages or download benchmark data. The small task pool
is an instrument fixture, not a measurement of the released GSM8K benchmark.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import venv

import pytest


ROOT = Path(__file__).resolve().parents[2]
_CHOSEN = "gsm8k/train/0"
_PLATFORM_GUARD = '''\
import importlib.abc, importlib.util, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
assert importlib.util.find_spec('pip') is None, 'platform fixture was seeded'
assert importlib.util.find_spec('pyarrow') is None, 'platform fixture contains pyarrow'

class PlatformImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'data.datasets.gsm8k' or fullname.split('.')[0] == 'pyarrow':
            raise AssertionError('platform imported a dataset dependency: ' + fullname)

sys.meta_path.insert(0, PlatformImports())

def assert_platform_imports():
    assert 'data.datasets.gsm8k' not in sys.modules
    assert not any(name == 'pyarrow' or name.startswith('pyarrow.') for name in sys.modules)
'''


@pytest.fixture(scope="module")
def prepared_provider(tmp_path_factory):
    selected = os.environ.get("HG_TEST_DATASET_RUNTIME_ROOT")
    if not selected:
        pytest.skip("set HG_TEST_DATASET_RUNTIME_ROOT to a prepared GSM8K runtime store")
    runtime_root = Path(selected).expanduser().resolve(strict=True)
    assert runtime_root.is_dir(), "the explicit runtime store must be a directory"

    # Only the pytest instrument writer imports Arrow; the platform subprocesses
    # below have an empty venv and reject even attempted Arrow/adapter imports.
    import pyarrow as pa
    import pyarrow.parquet as pq

    directory = tmp_path_factory.mktemp("provider-integration")
    dataset = directory / "datasets/gsm8k/main"
    dataset.mkdir(parents=True)
    for split, answers in (("test", (5, 8)), ("train", (5, 11))):
        rows = [{"question": f"Authored {split} question {index}: compute {answer - 3} + 3.",
                 "answer": f"AUTHORED_PRIVATE_{split}_{index} derivation.\n#### {answer}"}
                for index, answer in enumerate(answers)]
        pq.write_table(pa.Table.from_pylist(rows), dataset / f"{split}-00000-of-00001.parquet")

    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    platform_root = Path(tempfile.mkdtemp(prefix="hg-provider-platform-", dir=cache))
    try:
        platform = platform_root / "venv"
        # Match the linked interpreter layout in the README's uv/Linux recipe.
        venv.EnvBuilder(with_pip=False, system_site_packages=False, symlinks=True).create(platform)
        python = platform / "bin/python"
        poison = directory / "poison"
        poison.mkdir()
        (poison / "pyarrow.py").write_text("raise AssertionError('ambient Arrow entered')\n")
        (poison / "sitecustomize.py").write_text("raise AssertionError('ambient hook entered')\n")
        # No inherited role configuration, dataset settings or local .env discovery.
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("HG_", "HARNESSGRAD_", "PYTHON"))}
        environment.update(
            PATH=str(platform / "bin") + os.pathsep + os.defpath,
            HG_REVIEWED_ENVIRONMENT_ONLY="1", HG_DATASET_RUNTIME_ROOT=str(runtime_root),
            HG_DATA_ROOT=str(directory / "datasets"), HG_AGENT_BACKEND="mock",
            PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(poison),
        )
        yield SimpleNamespace(directory=directory, python=python, env=environment)
    finally:
        shutil.rmtree(platform_root)


def _run(platform, program, *args, timeout=180):
    result = subprocess.run(
        [str(platform.python), "-I", "-B", "-c", _PLATFORM_GUARD + program,
         str(ROOT), *map(str, args)],
        cwd=platform.directory, env=platform.env, capture_output=True, text=True,
        errors="replace", timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    return result


def test_real_provider_reads_parquet_outside_the_empty_platform_environment(prepared_provider):
    result = _run(prepared_provider, '''\
from data.scaffolds import registry
provider = registry.provider('gsm8k')
assert provider is not None
catalog = provider.catalog()
tasks, scorable, split = registry.load_split('gsm8k')
expected = {'train': ['gsm8k/train/0', 'gsm8k/train/1'],
            'eval': ['gsm8k/test/0', 'gsm8k/test/1']}
assert split == expected
assert catalog['metadata']['SPLIT'] == expected
assert catalog['metadata']['OFFICIAL_EVAL'] == expected['eval']
assert registry.official_split('gsm8k', tasks) == expected
assert registry.oracle_source('gsm8k') == ('released', None)
assert [task['task_id'] for task in tasks] == expected['eval'] + expected['train']
assert catalog['tasks'] == tasks
assert catalog['scorable'] == scorable
assert scorable['gsm8k/train/0'] == '5'
assert 'AUTHORED_PRIVATE_' not in json.dumps(catalog['tasks'])
for task in tasks:
    assert not set(task).intersection({'gold', 'answer', 'expected', 'scorable',
                                       'setup', 'verify', 'verifier', 'env'})
chosen = 'gsm8k/train/0'
selected, setups, verifiers = registry.load_tasks('gsm8k', task_ids=[chosen])
envs = registry.load_envs('gsm8k', task_ids=[chosen])
assert [task['task_id'] for task in selected] == [chosen]
assert set(setups) == set(verifiers) == {chosen}
assert envs == {chosen: {'kind': 'files'}}
assert 'AUTHORED_PRIVATE_' not in json.dumps(setups)
gold = next(entry for entry in verifiers[chosen]['inputs'] if entry['path'] == 'gold.json')
assert json.loads(gold['content'])['answer'] == '5'
assert 'AUTHORED_PRIVATE_train_0' in gold['content']
assert Path(catalog['runtime']['python']) != Path(sys.executable)
assert_platform_imports()
print(json.dumps({'tasks': len(tasks), 'selected': list(verifiers),
                  'runtime_id': catalog['runtime']['runtime_id'],
                  'platform_python': sys.executable}))
''')
    observed = json.loads(result.stdout)
    assert observed["tasks"] == 4 and observed["selected"] == [_CHOSEN]
    assert observed["runtime_id"]
    assert observed["platform_python"] == str(prepared_provider.python)


def test_real_driver_scores_one_authored_task_without_platform_arrow(prepared_provider):
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is not provisioned")
    harness = prepared_provider.directory / "harness"
    harness.mkdir()
    (harness / "harness.json").write_text(json.dumps({
        "name": "provider-instrument", "version": "1", "entrypoint": "agent.py",
        "backend": "cli", "env_kinds": ["files"],
    }))
    (harness / "agent.py").write_text(
        "import argparse,importlib.util,json\nfrom pathlib import Path\n"
        "parser=argparse.ArgumentParser()\n"
        "parser.add_argument('--task');parser.add_argument('--workdir')\n"
        "args=parser.parse_args()\n"
        "assert importlib.util.find_spec('pyarrow') is None\n"
        "assert 'AUTHORED_PRIVATE_' not in Path(args.task).read_text()\n"
        "assert not Path(args.workdir,'gold.json').exists()\n"
        "Path(args.workdir,'answer.txt').write_text('5')\n"
        "Path(args.workdir,'trace.jsonl').write_text(json.dumps({"
        "'event':'authored_control_completed',"
        "'usage':{'input_tokens':0,'output_tokens':0,'calls':0}})+'\\n')\n"
    )
    runs = prepared_provider.directory / "runs"
    work = prepared_provider.directory / "work"
    _run(prepared_provider, '''\
import driver
sys.argv = [str(Path(sys.argv[1]) / 'driver.py'), *sys.argv[2:]]
status = driver.main()
assert_platform_imports()
raise SystemExit(status)
''', "--harness", harness, "--dataset", "gsm8k", "--split", "declared",
         "--side", "train", "--tasks", _CHOSEN, "--sampling", "all",
         "--rounds", "0", "--trials", "1", "--jobs", "1", "--improver", "none",
         "--fail-on-incomplete", "--run-id", "provider-authored-control",
         "--runs-root", runs, "--work-root", work)
    points = [json.loads(line) for line in
              (runs / "provider-authored-control/curve.jsonl").read_text().splitlines()]
    assert len(points) == 1
    assert points[0]["score"] == 1.0
    assert points[0]["per_task"] == {_CHOSEN: 1.0}
    assert points[0]["n_invalid"] == points[0]["n_harness_failed"] == 0
    assert points[0]["cost"]["harness_model_calls"] == 0
    _run(prepared_provider, "assert_platform_imports()\n")
