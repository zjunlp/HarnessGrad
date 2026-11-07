"""Offline wheel contract; never resolves dependencies or calls a model.

Build tooling (setuptools >=64 and pip) must already be available to the test
interpreter. The installed runtime uses a new venv without system site packages.
Sandbox execution is audited separately: pytest's /tmp venv is intentionally not
exposed by the sandbox and must not be mistaken for a supported installation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = (
    "base_harness/loop/agent.py",
    "base_harness/loop/harness.json",
    "base_harness/loop/requirements.txt",
    "base_harness/loop/requirements.lock",
    "base_harness/loop/requirements-py310.lock",
    "base_harness/loop/requirements-py39.lock",
    "base_harness/loop/README.md",
    "base_harness/miniswe/requirements.txt",
    "base_harness/miniswe/requirements.lock",
    "base_harness/miniswe/requirements-py310.lock",
    "base_harness/miniswe/requirements-py311.lock",
    "base_harness/miniswe/README.md",
    "base_harness/miniagent/requirements.lock",
    "base_harness/miniagent/PROVENANCE.json",
    "base_harness/openclaw/openclaw_runtime.py",
    "eval/containers/openclaw_runtime.py",
    "tools/configure_openclaw_harness.py",
    "base_harness/opencode/opencode_runtime.py",
    "tools/configure_opencode_harness.py",
    "scenarios/demo-smoke.json",
    "data/aggregation_policies.json",
    "data/scaffolds/qa/cli/qa",
    "data/scaffolds/qa/prediction.schema.json",
    "data/scaffolds/qa/verify/check.py",
    "data/scaffolds/qa/oracle_harness/harness.json",
    "data/datasets/gsm8k/runtime.json",
    "data/datasets/gsm8k/requirements-load.txt",
    "data/datasets/hotpotqa/runtime.json",
    "data/datasets/hotpotqa/requirements-load.txt",
    "data/datasets/triviaqa/runtime.json",
    "data/datasets/triviaqa/requirements-load.txt",
    "data/datasets/costbench/execution-runtime.json",
    "data/datasets/costbench/requirements.lock",
    "data/datasets/locomo/execution-runtime.json",
    "data/datasets/locomo/requirements.lock",
    "data/datasets/locomo/requirements-score.txt",
    "data/datasets/locomo/specs/source.json",
    "data/datasets/locomo/verify/check.py",
    "data/datasets/math/execution-runtime.json",
    "data/datasets/math/requirements.lock",
    "data/scaffolds/native_qa/math_execution.py",
    "data/scaffolds/native_qa/math_worker.py",
    "data/scaffolds/native_qa/math_grade.py",
    "data/scaffolds/native_qa/PROVENANCE.json",
    "harnessgrad/improvers/improvers.json",
    "harnessgrad/improvers/skill.md",
    "harnessgrad/runtime/eight_stage_protocol.schema.json",
    "harnessgrad/candidate_runtime.py",
    "harnessgrad/python_harness_runtime.py",
    "harnessgrad/python_artifacts.py",
    "methods/noop/method.json",
    "methods/dsh-improver",
)


def run(argv, cwd, env):
    result = subprocess.run(argv, cwd=cwd, env=env, text=True,
                            capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp("clean-install")
    source = root / "source"
    source.mkdir()
    # Copy tracked packaging inputs only, including current uncommitted fixes.
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    prefixes = ("harnessgrad/", "eval/", "data/", "methods/", "tools/",
                "scenarios/", "base_harness/")
    for name in tracked:
        if name in {"pyproject.toml", "LICENSE", "driver.py"} or name.startswith(prefixes):
            src = ROOT / name
            if src.is_file():
                dst = source / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    # Deliberately dirty build input: these untracked/ignored files must not
    # leak through package-data globs, even without Git in a release source tree.
    (source / ".gitignore").write_text("local-secret.*\n")
    sentinels = []
    for package in ("harnessgrad/improvers", "base_harness/loop", "data", "methods"):
        for suffix in ("json", "md", "yaml"):
            name = f"{package}/local-secret.{suffix}"
            (source / name).write_text("DO NOT DISTRIBUTE: synthetic secret\n")
            sentinels.append(name)
    # Discovery is still namespace-based: the data allowlist is NOT a
    # tracked-source security boundary. Document this known Python-module risk
    # without importing the sentinel or claiming arbitrary dirty builds safe.
    ignored_module = source / "data/local_ignored_module"
    ignored_module.mkdir()
    (ignored_module / "__init__.py").write_text("# synthetic untracked module\n")
    with (source / ".gitignore").open("a") as stream:
        stream.write("local_ignored_module/\n")
    home = root / "home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": os.defpath, "PYTHONNOUSERSITE": "1",
           "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INDEX": "1",
           "HG_AGENT_BACKEND": "mock"}
    wheels = root / "wheels"
    run([sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps",
         "--no-build-isolation", "--wheel-dir", str(wheels), str(source)], root, env)
    wheel, = wheels.glob("harnessgrad-*.whl")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert not names.intersection(sentinels)
        assert "data/local_ignored_module/__init__.py" in names
        launcher = archive.getinfo("methods/dsh-improver")
        assert (launcher.external_attr >> 16) & 0o111, "launcher lost executable mode"
    assert set(RESOURCES) <= names, sorted(set(RESOURCES) - names)
    # Only tracked, local source components are shipping obligations. Never
    # include runtime_paths (operator installations) or generated outputs.
    declared = set()
    for manifest in source.glob("base_harness/*/harness.json"):
        config = json.loads(manifest.read_text())
        components = [item for group in config.get("components", {}).values()
                      for item in group]
        components += [config.get("entrypoint"), config.get("install")]
        for item in components:
            if not item or Path(item).is_absolute() or ".." in Path(item).parts:
                continue
            relative = (manifest.parent / item).relative_to(source).as_posix()
            if relative in tracked:
                declared.add(relative)
    assert declared, "no tracked manifest components checked"
    assert declared <= names, sorted(declared - names)
    runtime = root / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(runtime)
    python = runtime / "bin/python"
    run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], root, env)
    # Remove source to prevent accidental source-tree fallback.
    shutil.rmtree(source)
    return root, env, runtime


def test_installed_help_catalog_and_scenario(installed):
    root, env, runtime = installed
    cli = str(runtime / "bin/harnessgrad")
    assert "usage:" in run([cli, "--help"], root, env)
    assert "--dataset" in run([cli, "run", "--help"], root, env)
    assert "0.1.0" in run([cli, "--version"], root, env)
    run([cli, "run", "--scenario", "demo-smoke", "--show-config"], root, env)
    code = """
import importlib.util
import site
from pathlib import Path
import harnessgrad
from data.scaffolds import registry
from data.scaffolds.aggregation import aggregation_policy
from data.datasets import costbench, costbench_dynamic
from harnessgrad.dataset_runtime import execution_descriptor
assert not site.ENABLE_USER_SITE
assert registry.published()
assert len(registry.load_tasks('demo')[0]) == 8
assert aggregation_policy('demo', 'run_reward')
assert registry.provider('costbench') is None
assert execution_descriptor('costbench')['python'] == '3.12'
assert costbench._execution() is costbench_dynamic._execution()
assert callable(costbench._execution().binding)
assert execution_descriptor('locomo')['pins']['nltk'] == '3.10.4'
from data.datasets import locomo
assert locomo._load_asset('report.py').aggregate_scores([None]) == {
    'num_failed_trials': 1, 'num_questions_scored': 0}
assert importlib.util.find_spec('nltk') is None
assert execution_descriptor('math')['pins']['math-verify'] == '0.9.0'
assert registry.managed_runtime('math') and registry.managed_runtime('math500')
assert importlib.util.find_spec('math_verify') is None
from data.datasets import math as math_dataset, math500
assert math_dataset.MANAGED_EXECUTION_RUNTIME == math500.MANAGED_EXECUTION_RUNTIME == 'math'
root = Path(harnessgrad.__file__).resolve().parent.parent
""" + "\n".join(f"assert (root / {name!r}).is_file()" for name in RESOURCES)
    run([str(runtime / "bin/python"), "-c", code], root, env)


def test_installed_miniagent_declares_its_own_complete_lock(installed):
    root, env, runtime = installed
    code = '''
import importlib.util,json
from pathlib import Path
import harnessgrad
from harnessgrad.python_harness_runtime import descriptor
source = Path(harnessgrad.__file__).resolve().parent.parent / 'base_harness/miniagent'
manifest = json.loads((source / 'harness.json').read_text())
assert manifest['install'] == 'requirements.lock'
assert manifest['runtime'] == {'kind':'python','python':'3.12','requirements':'requirements.lock'}
assert manifest['env_kinds'] == ['files'] and manifest['runtime_paths'] == []
pins = descriptor(source)['pins']
assert len(pins) == 20
assert pins['openai'] == '2.21.0' and pins['pyyaml'] == '6.0.3' and pins['requests'] == '2.33.0'
assert all(importlib.util.find_spec(name) is None for name in ('openai','httpx','yaml','requests'))
'''
    run([str(runtime / 'bin/python'), '-I', '-B', '-c', code], root, env)


def test_installed_openclaw_template_and_preparation_tool(installed):
    root, env, runtime = installed
    python = str(runtime / "bin/python")
    assert "--openclaw" in run([python, "-m", "tools.configure_openclaw_harness", "--help"], root, env)
    code = '''
import json
from pathlib import Path
from base_harness.openclaw import openclaw_runtime
root = Path(openclaw_runtime.__file__).parent
assert json.loads((root / "harness.json").read_text())["runtime_paths"] == []
assert openclaw_runtime.identity(root)["version"] == "2026.3.13"
try:
    openclaw_runtime.resolve(root, {})
except ValueError as exc:
    assert "configure_openclaw_harness.py" in str(exc)
else:
    raise AssertionError("unprepared installed template was accepted")
'''
    run([python, "-c", code], root, env)


def test_installed_opencode_template_and_preparation_tool(installed):
    root, env, runtime = installed
    python = str(runtime / "bin/python")
    assert "--source" in run([python, "-m", "tools.configure_opencode_harness", "--help"], root, env)
    code = '''
import json
from pathlib import Path
from base_harness.opencode import opencode_runtime
root = Path(opencode_runtime.__file__).parent
assert json.loads((root / "harness.json").read_text())["runtime_paths"] == []
try:
    opencode_runtime.resolve(root, {})
except ValueError as exc:
    assert "configure_opencode_harness.py" in str(exc)
else:
    raise AssertionError("unprepared installed OpenCode template was accepted")
'''
    run([python, "-c", code], root, env)


def test_installed_miniswe_template_and_managed_requirements(installed):
    root, env, runtime = installed
    python = str(runtime / "bin/python")
    assert "--harness" in run([python, "-m", "tools.configure_harness", "--help"], root, env)
    code = '''
import json
from pathlib import Path
import base_harness.miniswe.agent as agent
from eval.ckpt.source_tree import python_spec
root = Path(agent.__file__).parent
manifest = json.loads((root / "harness.json").read_text())
assert manifest["runtime_paths"] == []
assert manifest["install"] == "requirements.lock"
assert manifest["env_kinds"] == ["files", "exec"]
assert python_spec(manifest['runtime'],manifest['install'])['kind']=='python'
assert manifest['runtime']['exec']['requirements']=={'3.10':'requirements-py310.lock',
    '3.11':'requirements-py311.lock','3.12':'requirements.lock','3.13':'requirements.lock'}
assert all((root/name).is_file() for name in ('requirements.lock','requirements-py310.lock','requirements-py311.lock'))
assert (root / "requirements.txt").read_text().splitlines() == ["mini-swe-agent==2.4.6", "litellm==1.75.9"]
'''
    run([python, "-c", code], root, env)


@pytest.mark.parametrize('dataset', ['gsm8k', 'triviaqa', 'hotpotqa'])
def test_installed_parquet_qa_synthetic_setup_and_adapter_scorer(installed, dataset):
    """Exercise installed adapter SETUP and isolated verifier without released tasks.

    Patching only the adapter's table reader gives it one synthetic record per split;
    the production CLI, scorer, input projection and gold checks are not mocked.
    """
    root, env, runtime = installed
    code = r'''
import importlib, importlib.util, json, os, shutil, subprocess, sys
from pathlib import Path
from data.scaffolds.qa import base
name = sys.argv[1]
from harnessgrad.dataset_runtime import descriptor
assert descriptor(name)['pins'] == {'pyarrow': '23.0.0'}
assert importlib.util.find_spec('pyarrow') is None
row = {'question':'Synthetic: two plus three?', 'answer': '#### 5'}
if name == 'triviaqa':
    row['answer'] = {'value':'5', 'normalized_aliases':['5', 'five']}
elif name == 'hotpotqa':
    row.update(answer='5', supporting_facts={'title':['Authored private evidence'], 'sent_id':[0]})
    # This adapter checks file presence before calling the patched table reader.
    path = Path(os.environ['HG_DATA_HOTPOTQA']) / 'fullwiki/validation-00000-of-00001.parquet'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'authored reader placeholder; no released data')
base.parquet_rows = lambda path, **kwargs: [row]
adapter = importlib.import_module('data.datasets.' + name)
records, _ = adapter.load()
task = records[0]
work = Path('installed-' + name + '-test')
work.mkdir()
for item in adapter.SETUP[task["task_id"]]["files"] + adapter.VERIFY[task["task_id"]]["inputs"]:
    destination = work / item["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if "content" in item:
        destination.write_text(item["content"])
    else:
        shutil.copy2(item["from"], destination)
subprocess.run([sys.executable, "qa/bin/qa", "submit", "5"], cwd=work, check=True)
verifier = adapter.VERIFY[task["task_id"]]
check = subprocess.run([sys.executable, *verifier["argv"][1:]], cwd=work,
                       capture_output=True, text=True)
assert check.returncode == 0, check.stdout + check.stderr
assert float((work / "verifier/reward.txt").read_text()) == 1.0
print('installed synthetic ' + name + ' SETUP and scorer: ok')
'''
    configured = {**env, 'HG_DATA_' + dataset.upper(): str(root / ('synthetic-' + dataset))}
    output = run([str(runtime / "bin/python"), "-c", code, dataset], root, configured)
    assert 'installed synthetic ' + dataset + ' SETUP and scorer: ok' in output


def test_installer_python_floor_diagnostic():
    text = (ROOT / "install.sh").read_text()
    assert "no python3 >= 3.12 on PATH" in text
    assert "no python3 >= 3.10" not in text
