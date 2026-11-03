"""Candidate storage settings cannot change a QA measurement."""
from __future__ import annotations

import json
import importlib.util
import marshal
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import pytest

from eval.verification import verifier


@pytest.mark.parametrize("answer,expected", [("True", 1), ("False", 0)])
@pytest.mark.parametrize("poison", [
    None, "pathlib.py", "contextvars.py", "__init__.py", "scoring.pyc",
])
def test_native_qa_uses_restored_sources_with_isolated_imports(
        tmp_path, monkeypatch, answer, expected, poison):
    from data.scaffolds.native_qa import Dataset

    record = {"task_id": "bbeh/test/authored-control", "gold": {"answer": "True"}}
    spec = Dataset("bbeh", tmp_path / "unprovisioned").verifier(record)
    package = tmp_path / "verifier/native_qa"
    package.mkdir(parents=True)
    marker = tmp_path / "verifier/candidate-native-module-entered"
    code = ('open("verifier/candidate-native-module-entered", "w").write("entered")\n'
            'raise RuntimeError("CANDIDATE_NATIVE_MODULE_EXECUTED")\n')
    if poison == "scoring.pyc":
        source = Path(next(item["from"] for item in spec["inputs"]
                           if item["path"] == "verifier/native_qa/scoring.py"))
        target = package / "scoring.py"
        cache = Path(importlib.util.cache_from_source(str(target)))
        cache.parent.mkdir()
        stat = source.stat()
        cache.write_bytes(importlib.util.MAGIC_NUMBER
                          + struct.pack("<III", 0, int(stat.st_mtime) & 0xffffffff, stat.st_size)
                          + marshal.dumps(compile(code, str(target), "exec")))
    elif poison:
        (package / poison).write_text(code)
    (tmp_path / "answer.txt").write_text(answer)
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    result = verifier._verify(tmp_path, spec, "", sandbox=True, timeout_s=20,
                              env={"kind": "files"}, task_id=record["task_id"])
    assert result["score"] == expected, result
    assert result["evidence"]["measured"] is True, result
    assert result["evidence"]["scorer_input"]["verified"] is True, result
    assert not marker.exists(), "the scorer executed a candidate module"


def _task(work: Path, dataset, poison: str) -> tuple[dict, str]:
    # Authored inputs reproduce the reported gold=4, answer=5 regression without
    # requiring any downloaded dataset or optional table reader.
    record = {**dataset._RECORDS[0], "gold": {"answer": "4"}}
    spec = dataset._SPEC.verifier(record)
    staged_root = work / "qa/lib"
    staged_root.mkdir(parents=True)
    if poison in {"individual", "global"}:
        key = "HG_DATA_GSM8K" if poison == "individual" else "HG_DATA_ROOT"
        (staged_root / ".env").write_text(f"{key}=.\n")
    elif poison == "worktree":
        primary = work / "candidate-checkout"
        git_dir = primary / ".git/worktrees/qa"
        git_dir.mkdir(parents=True)
        (git_dir / "commondir").write_text("../..\n")
        (staged_root / ".git").write_text(f"gitdir: {git_dir}\n")
        (primary / ".env").write_text("HG_DATA_GSM8K=.\n")
    return spec, record["task_id"]


@pytest.mark.parametrize("poison", ["none", "individual", "global", "worktree"])
@pytest.mark.parametrize("answer,expected", [("5", 0), ("4", 1)])
def test_candidate_dotenv_and_git_cannot_change_sandbox_score(
        tmp_path, monkeypatch, synthetic_gsm8k, poison, answer, expected):
    spec, task_id = _task(tmp_path, synthetic_gsm8k, poison)
    (tmp_path / "answer.txt").write_text(answer)
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    result = verifier._verify(tmp_path, spec, "", sandbox=True, timeout_s=20,
                              env={"kind": "files"}, task_id=task_id)
    assert result["score"] == expected, result
    assert result["evidence"]["measured"] is True, result
    assert result["evidence"]["scorer_input"]["verified"] is True, result


def test_staged_check_does_not_discover_config_files(tmp_path, synthetic_gsm8k):
    spec, _ = _task(tmp_path, synthetic_gsm8k, "worktree")
    (tmp_path / "qa/lib/.env").write_text("HG_DATA_ROOT=.\n")
    (tmp_path / "answer.txt").write_text("5")
    for entry in spec["inputs"]:
        target = tmp_path / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copyfile(entry["from"], target)
    script = '''\
import pathlib, runpy, sys
def audit(event, args):
    if event == "open" and isinstance(args[0], str):
        assert pathlib.Path(args[0]).name not in {".env", ".git", "commondir"}, args[0]
sys.addaudithook(audit)
sys.argv = ''' + repr(spec["argv"][2:]) + '''
runpy.run_path(sys.argv[0], run_name="__main__")
'''
    process = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path,
                             env=verifier._verifier_env(tmp_path),
                             capture_output=True, text=True, timeout=20)
    assert process.returncode == 1, process.stdout + process.stderr
    evidence = json.loads((tmp_path / "verifier/evidence.json").read_text())
    assert evidence["measured"] is True and evidence["reward"] == 0, evidence


@pytest.mark.parametrize("directory", [".", "qa/lib", "qa/lib/verify"])
@pytest.mark.parametrize("module", ["contextvars", "_contextvars", "argparse", "pathlib", "copy", "sitecustomize"])
@pytest.mark.parametrize("answer,expected", [("5", 0), ("4", 1)])
def test_candidate_modules_cannot_enter_the_sandbox_scorer(
        tmp_path, monkeypatch, synthetic_gsm8k, directory, module, answer, expected):
    spec, task_id = _task(tmp_path, synthetic_gsm8k, "none")
    target = tmp_path / directory / f"{module}.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('open("verifier/candidate-module-entered", "w").write("entered")\n'
                      'raise RuntimeError("CANDIDATE_MODULE_EXECUTED")\n')
    (tmp_path / "answer.txt").write_text(answer)
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    result = verifier._verify(tmp_path, spec, "", sandbox=True, timeout_s=20,
                              env={"kind": "files"}, task_id=task_id)
    assert result["score"] == expected, result
    assert result["evidence"]["measured"] is True, result
    assert not (tmp_path / "verifier/candidate-module-entered").exists()
    assert "CANDIDATE_MODULE_EXECUTED" not in str(result)


def test_candidate_bytecode_cannot_replace_restored_source(tmp_path, monkeypatch, synthetic_gsm8k):
    spec, task_id = _task(tmp_path, synthetic_gsm8k, "none")
    relative = "qa/lib/harnessgrad/paths.py"
    source = Path(next(entry["from"] for entry in spec["inputs"] if entry["path"] == relative))
    cache = Path(importlib.util.cache_from_source(str(tmp_path / relative)))
    cache.parent.mkdir(parents=True)
    # Match the mtime and size restored by copy2, so the ordinary Python loader
    # would accept this candidate-created cache as current.
    stat = source.stat()
    code = compile('open("verifier/candidate-bytecode-entered", "w").write("entered")\n'
                   'raise RuntimeError("CANDIDATE_BYTECODE_EXECUTED")', str(tmp_path / relative), "exec")
    cache.write_bytes(importlib.util.MAGIC_NUMBER
                      + struct.pack("<III", 0, int(stat.st_mtime) & 0xffffffff, stat.st_size)
                      + marshal.dumps(code))
    (tmp_path / "answer.txt").write_text("5")
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    result = verifier._verify(tmp_path, spec, "", sandbox=True, timeout_s=20,
                              env={"kind": "files"}, task_id=task_id)
    assert result["score"] == 0, result
    assert result["evidence"]["measured"] is True, result
    assert not (tmp_path / "verifier/candidate-bytecode-entered").exists()
    assert "CANDIDATE_BYTECODE_EXECUTED" not in str(result)


@pytest.mark.parametrize("dependency", ["candidate_dependency", "data.scaffolds.qa.candidate_dependency"])
def test_missing_trusted_dependency_never_falls_back_to_candidate_code(
        tmp_path, monkeypatch, synthetic_gsm8k, dependency):
    spec, task_id = _task(tmp_path, synthetic_gsm8k, "none")
    adapter = next(entry for entry in spec["inputs"] if entry["path"] == "data/datasets/gsm8k.py")
    # An authored dependency probe in trusted verifier input exercises both
    # top-level fallback and unlisted siblings inside a restored package.
    adapter["content"] = Path(adapter.pop("from")).read_text() + f"\nimport {dependency}\n"
    candidate = tmp_path / (dependency.replace(".", "/") + ".py")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text('raise RuntimeError("CANDIDATE_DEPENDENCY_EXECUTED")\n')
    (tmp_path / "answer.txt").write_text("5")
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    result = verifier._verify(tmp_path, spec, "", sandbox=True, timeout_s=20,
                              env={"kind": "files"}, task_id=task_id)
    assert result["score"] is None and result["outcome"] == "unmeasured", result
    assert "ModuleNotFoundError" in result["unmeasured"], result
    assert "CANDIDATE_DEPENDENCY_EXECUTED" not in str(result)
