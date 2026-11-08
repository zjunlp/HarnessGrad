"""Authored DS-1000 checks only; never open or execute a released task row.

Real scientific controls opt into an explicitly prepared CPU artifact and the
pinned official execution.py source. They use no benchmark prompts/tests/gold.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
from types import ModuleType

import pytest

from harnessgrad import dataset_runtime, dataset_runtime_identity

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "data/datasets/ds1000.py"
EXECUTION_SHA = "fab0beb2a5c3300f4540c2225ffc013881e5770fed388ad20d4620ae040cd311"
SCIENCE = {"numpy": "1.26.4", "pandas": "2.2.2", "scipy": "1.13.1",
           "scikit-learn": "1.8.0", "matplotlib": "3.8.4", "seaborn": "0.13.2", "torch": "2.5.1+cpu"}
CONTEXT = '''import numpy as np
import pandas as pd
import scipy.linalg
import sklearn.preprocessing
import matplotlib
import matplotlib.pyplot as plt
import seaborn as sns
import torch

def test_execution(code):
    scope = {}
    exec(code, globals(), scope)
    assert scope['value'] == 42
    assert np.__version__ == "1.26.4" and pd.__version__ == "2.2.2"
    assert scipy.__version__ == "1.13.1" and sklearn.__version__ == "1.8.0"
    assert matplotlib.__version__ == "3.8.4" and sns.__version__ == "0.13.2"
    assert torch.__version__ == "2.5.1+cpu"
    assert np.array([1, 2]).sum() == 3
    assert pd.Series([2, 4]).mean() == 3
    assert scipy.linalg.det([[2.]]) == 2
    assert sklearn.preprocessing.scale([-1., 1.]).sum() == 0
    fig, ax = plt.subplots()
    ax.plot([1, 2])
    plt.close(fig)
    assert len(sns.color_palette(n_colors=2)) == 2
    assert torch.matmul(torch.tensor([[6.]]), torch.tensor([[7.]])).item() == 42
    assert torch.version.cuda is None

def test_string(code):
    assert '# authored' in code
'''


def test_profile_covers_the_complete_scientific_stack_and_cpu_torch():
    declaration = dataset_runtime.execution_descriptor("ds1000")
    assert declaration["python"] == "3.12"
    assert SCIENCE.items() <= declaration["pins"].items()
    assert len(declaration["pins"]) == 29
    assert not any(name.startswith(("nvidia-", "tensorflow", "triton")) for name in declaration["pins"])
    assert dataset_runtime.descriptor("ds1000") is None


def test_source_and_historical_queue_pins_are_preserved_without_reading_rows():
    tree = ast.parse(ADAPTER.read_text())
    literals = {target.id: ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(node.value, (ast.Constant, ast.Dict))
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                if isinstance(target, ast.Name) and target.id in {
                    "DATA_SHA256", "SOURCE_COMMIT", "SOURCE_TARBALL_SHA256", "EXECUTION_SHA256",
                    "MEASURED_EXCLUSIONS", "MANAGED_EXECUTION_RUNTIME"}}
    assert literals["EXECUTION_SHA256"] == EXECUTION_SHA
    assert literals["DATA_SHA256"] == "26a492838b05f9d030b2f08a5715c6bafecafb601c157da912bf417fd8b0d363"
    assert literals["SOURCE_COMMIT"] == "b39aab71"
    assert literals["SOURCE_TARBALL_SHA256"] == "b14f1a2b0fb20ef55f0693019122f22c82b03ef6647faabf0e6e8ae1272a1dc0"
    assert sum(map(len, literals["MEASURED_EXCLUSIONS"].values())) == 27
    assert literals["MANAGED_EXECUTION_RUNTIME"] == "ds1000"


@pytest.fixture(scope="module")
def cold_platform(tmp_path_factory):
    root = tmp_path_factory.mktemp("cold-ds1000-platform")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root)], check=True,
                   capture_output=True, text=True)
    return root / "bin/python"


@pytest.fixture
def authored_adapter(tmp_path, monkeypatch):
    runtime_root = os.environ.get("HG_DS1000_AUTHORED_RUNTIME_ROOT")
    source = os.environ.get("HG_DS1000_OFFICIAL_EXECUTION_SOURCE")
    if not runtime_root or not source:
        pytest.skip("opt-in real CPU control: prepare ds1000 execution runtime and supply the pinned execution.py (see ds1000/README.md)")
    monkeypatch.setenv("HG_DATASET_RUNTIME_ROOT", runtime_root)
    monkeypatch.setenv("HG_DATA_DS1000", str(tmp_path / "authored-dataset"))
    monkeypatch.setenv("HG_REVIEWED_ENVIRONMENT_ONLY", "1")
    assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == EXECUTION_SHA
    # Avoid even opening test.jsonl. Only the eager record-derived map assignments
    # are omitted; original functions, constants and lazy VERIFY implementation run.
    tree = ast.parse(ADAPTER.read_text())
    tree.body = [node for node in tree.body if not (isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SETUP" for target in node.targets))]
    module = ModuleType("authored_ds1000_adapter")
    module.__file__ = str(ADAPTER)
    exec(compile(tree, str(ADAPTER), "exec"), module.__dict__)
    module._RAW = [{"prompt": "Authored arithmetic control", "code_context": CONTEXT,
                    "reference_code": "value = 42\n# authored"}]
    module._INCLUDED = [0]
    module.EXECUTION_FILE = tmp_path / "execution-source.py"
    shutil.copyfile(source, module.EXECUTION_FILE)
    module.SEABORN_DIR = tmp_path / "authored-csvs"
    module.SEABORN_DIR.mkdir()
    for name in module.SEABORN_CSVS:
        (module.SEABORN_DIR / name).write_text("authored,value\ncontrol,1\n")
    return module


def stage(spec, work):
    work.mkdir(parents=True, exist_ok=True)
    for entry in spec["inputs"]:
        target = work / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copyfile(entry["from"], target)


def direct(spec, work, python=None):
    argv = list(spec["argv"])
    if python:
        argv[0] = str(python)
    return subprocess.run(argv, cwd=work, env={**os.environ, "PYTHONPATH": str(work),
                          "MPLBACKEND": "Agg"}, capture_output=True, text=True, timeout=180)


def evidence(work):
    return json.loads((work / "verifier/evidence.json").read_text())


@pytest.mark.parametrize("solution,score", [("value = 42\n# authored", 1),
                                            ("value = 41\n# authored", 0),
                                            ("value = 42", 0),
                                            ("import authored_missing_dependency\nvalue = 42\n# authored", 0)])
def test_cold_platform_uses_bound_science_and_official_construction(authored_adapter, cold_platform, tmp_path, solution, score):
    spec = authored_adapter.VERIFY["ds1000/0"]
    assert spec["argv"][1:3] == ["-I", "-B"]
    assert spec["scorer_input"]["files"]["execution.py"] == EXECUTION_SHA
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    work = tmp_path / "work"
    work.mkdir()
    (work / "solution.py").write_text(solution)
    script = '''import importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
assert all(importlib.util.find_spec(name) is None for name in ('numpy','pandas','scipy','sklearn','matplotlib','seaborn','torch'))
sys.path.insert(0, sys.argv[1])
from data.scaffolds import command_control
from harnessgrad import dataset_runtime_identity
spec=json.loads(Path(sys.argv[2]).read_text())
identity=dataset_runtime_identity.create(tasks={'ds1000/0':{'verifier':spec['python_runtime']}})
reward,detail=command_control.run_check(SimpleNamespace(VERIFY={'ds1000/0':spec}),{'task_id':'ds1000/0'},Path(sys.argv[3]))
assert all(importlib.util.find_spec(name) is None for name in ('numpy','pandas','scipy','sklearn','matplotlib','seaborn','torch'))
print(json.dumps({'reward':reward,'detail':json.loads(detail),'identity':identity}))
'''
    proc = subprocess.run([str(cold_platform), "-I", "-B", "-c", script, str(ROOT), str(spec_path), str(work)],
                          env=os.environ, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    assert result["reward"] == score and result["detail"]["measured"] is True
    identity = dataset_runtime_identity.validate(result["identity"])
    assert identity["tasks"]["ds1000/0"].keys() == {"verifier"}
    assert evidence(work)["scorer_input"]["verified"] is True


def test_missing_scientific_dependencies_are_unmeasured_not_zero(authored_adapter, cold_platform, tmp_path):
    spec = authored_adapter.verify_for(0)
    work = tmp_path / "missing-deps"
    stage(spec, work)
    (work / "solution.py").write_text("value = 42\n# authored")
    (work / "verifier").mkdir()
    (work / "verifier/reward.txt").write_text("1")
    proc = direct(spec, work, cold_platform)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert evidence(work)["measured"] is False
    assert "scientific runtime unavailable" in evidence(work)["error"]
    assert not (work / "verifier/reward.txt").exists()
    from eval.verification.result_contract import apply
    assert apply({"score": None, "evidence": evidence(work)}, spec["result_contract"])["score"] is None


def test_trusted_context_can_create_a_module_before_importing_it(authored_adapter, tmp_path):
    # These statements must run in their original order inside the official
    # executor. Extracting only import statements would incorrectly refuse it.
    prefix = ("import os, sys\nfrom pathlib import Path\n"
              "Path('authored_context_helper.py').write_text('VALUE = 42\\n')\n"
              "sys.path.insert(0, os.getcwd())\n"
              "import authored_context_helper\n"
              "assert authored_context_helper.VALUE == 42\n")
    authored_adapter._RAW[0]["code_context"] = prefix + CONTEXT
    spec = authored_adapter.verify_for(0)
    work = tmp_path / "context-created-module"
    stage(spec, work)
    (work / "solution.py").write_text("value = 42\n# authored")
    proc = direct(spec, work)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert evidence(work)["measured"] is True
    assert (work / "verifier/reward.txt").read_text().strip() == "1"


def test_binding_cannot_silently_use_the_platform_interpreter(authored_adapter, cold_platform, tmp_path):
    from data.scaffolds.command_control import run_check
    from types import SimpleNamespace
    spec = authored_adapter.verify_for(0)
    spec["argv"][0] = str(cold_platform)
    work = tmp_path / "wrong-interpreter"
    work.mkdir()
    with pytest.raises(dataset_runtime.DatasetRuntimeUnavailable, match="launch executable differs"):
        run_check(SimpleNamespace(VERIFY={"ds1000/0": spec}), {"task_id": "ds1000/0"}, work)
    assert list(work.iterdir()) == []


@pytest.mark.parametrize("target", ["context.json", "execution.py"])
def test_private_scorer_input_tampering_is_unmeasured(authored_adapter, tmp_path, target):
    spec = authored_adapter.verify_for(0)
    work = tmp_path / "tampered"
    stage(spec, work)
    (work / target).write_text("not the authenticated source")
    proc = direct(spec, work)
    assert proc.returncode == 3
    assert evidence(work)["measured"] is False
    assert evidence(work)["scorer_input"]["verified"] is False
    assert not (work / "verifier/reward.txt").exists()


def test_changed_official_source_is_refused_before_runtime_selection(authored_adapter, monkeypatch):
    authored_adapter.EXECUTION_FILE.write_text("raise RuntimeError('changed scorer')")
    monkeypatch.setattr(dataset_runtime, "resolve_execution", lambda *a, **kw: pytest.fail("selected runtime before source refusal"))
    with pytest.raises(ValueError, match="pinned execution.py changed"):
        authored_adapter.verify_for(0)


def test_task_modules_packages_and_pyc_cannot_shadow_trusted_startup(authored_adapter, tmp_path):
    spec = authored_adapter.verify_for(0)
    work = tmp_path / "shadow"
    stage(spec, work)
    (work / "solution.py").write_text("value = 42\n# authored")
    marker = tmp_path / "shadow-executed"
    poison = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('task module imported')\n"
    for name in ("json.py", "numpy.py", "pandas.py", "torch.py", "sitecustomize.py",
                 "data/__init__.py", "data/scaffolds/__init__.py"):
        (work / name).write_text(poison)
    source = tmp_path / "poison.py"
    source.write_text(poison)
    py_compile.compile(str(source), cfile=str(work / "execution.pyc"), doraise=True)
    py_compile.compile(str(source), cfile=str(work / "data/scaffolds/scorer_input.pyc"), doraise=True)
    cache = work / "data/scaffolds/__pycache__"
    cache.mkdir()
    py_compile.compile(str(source), cfile=str(cache / f"scorer_input.cpython-{sys.version_info.major}{sys.version_info.minor}.pyc"), doraise=True)
    proc = direct(spec, work)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (work / "verifier/reward.txt").read_text().strip() == "1"
    assert not marker.exists()


@pytest.mark.parametrize("target", ["data/scaffolds/scorer_input.py", "data", "execution.py"])
def test_task_symlinks_cannot_supply_scorer_code(authored_adapter, tmp_path, target):
    spec = authored_adapter.verify_for(0)
    work = tmp_path / "symlink"
    stage(spec, work)
    marker = tmp_path / "symlink-executed"
    outside = tmp_path / "outside.py"
    outside.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    path = work / target
    if path.is_dir():
        outside_dir = tmp_path / "outside-package"
        shutil.copytree(path, outside_dir)
        shutil.rmtree(path)
        path.symlink_to(outside_dir, target_is_directory=True)
    else:
        path.unlink()
        path.symlink_to(outside)
    proc = direct(spec, work)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert evidence(work)["measured"] is False
    assert not (work / "verifier/reward.txt").exists()
    assert not marker.exists()


@pytest.mark.parametrize("transport", ["empty", "invalid-json", "list-envelope", "bad-result",
    "nonboolean", "nonzero", "timeout"])
def test_broken_result_transport_never_becomes_an_ordinary_zero(tmp_path, monkeypatch, transport):
    """Fault injection at the process boundary; no scientific/official work runs."""
    import importlib.util
    from types import SimpleNamespace
    path = ROOT / "data/datasets/ds1000/verify/check.py"
    definition = importlib.util.spec_from_file_location("authored_ds1000_checker", path)
    check = importlib.util.module_from_spec(definition)
    definition.loader.exec_module(check)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "context.json").write_text(json.dumps({"code_context": "def test_execution(code): pass"}))
    (tmp_path / "execution.py").write_text("# Authored transport fixture; never executed\n")
    (tmp_path / "solution.py").write_text("value = 42")
    (tmp_path / "verifier").mkdir()
    (tmp_path / "verifier/reward.txt").write_text("1")
    args = SimpleNamespace(reward_file="verifier/reward.txt", evidence_file="verifier/evidence.json")
    payload = {"available": True, "result": {"passed": True, "result": "passed"}}
    stdout, code = json.dumps(payload), 0
    if transport == "empty": stdout = ""
    if transport == "invalid-json": stdout = "not JSON"
    if transport == "list-envelope": stdout = "[]"
    if transport == "bad-result": stdout = json.dumps({"available": True, "result": []})
    if transport == "nonboolean": stdout = json.dumps({"available": True, "result": {"passed": 1, "result": "passed"}})
    if transport == "nonzero": code = 1
    def run(*argv, **kwargs):
        assert kwargs["timeout"] == 170
        if transport == "timeout": raise subprocess.TimeoutExpired("authored child", 170)
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="authored transport failure")
    monkeypatch.setattr(check.subprocess, "run", run)
    assert check._score(args, {"context.json": tmp_path / "context.json", "execution.py": tmp_path / "execution.py"}, {"verified": True}) == 3
    assert evidence(tmp_path)["measured"] is False
    assert not (tmp_path / "verifier/reward.txt").exists()
