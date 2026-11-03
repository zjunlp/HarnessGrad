"""场景工作区：自然语言起草出来的 bundle，必须过得了结构校验与三道门。

这里不调用模型（起草那一步要端点，测不了就不测），测的是**模型之后**的所有判断：
形状对不对、路径会不会越界、检查器模板用没用错，以及最关键的那道门——参考解真的能
解题、空手真的拿不到分。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import scenario_builder as builder                                      # noqa: E402

EXAMPLE = ROOT / "data" / "authored" / "example-fix-bug"


@pytest.fixture
def bundle(tmp_path):
    """A copy of the built-in example that a test can break in one specific way."""
    target = tmp_path / "example-fix-bug"
    shutil.copytree(EXAMPLE, target)
    return target


def edit(root: Path, change) -> Path:
    path = root / "bundle.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root


def test_the_built_in_example_is_valid_and_passes_its_gates():
    assert builder.validate(EXAMPLE)["ok"]
    result = builder.gates(EXAMPLE)
    assert result["ok"], result
    row = result["tasks"][0]
    assert row["oracle_passes"] and row["empty_fails"] and row["deterministic"]


def test_the_teacher_screen_reads_as_a_sentence(bundle):
    screen = builder.validate(bundle)
    assert screen["tasks"][0]["checker"] == "在任务目录里运行 `python3 test_broken.py` 必须退出 0"


def test_a_missing_required_field_is_named(bundle):
    edit(bundle, lambda d: d.pop("source"))
    problems = builder.check_bundle(bundle)
    assert any("source" in problem for problem in problems), problems


def test_a_duplicate_task_id_is_refused(bundle):
    edit(bundle, lambda d: d["tasks"].append(dict(d["tasks"][0])))
    assert any("重复" in problem for problem in builder.check_bundle(bundle))


def test_an_unknown_verifier_template_is_refused(bundle):
    edit(bundle, lambda d: d["tasks"][0]["verifier"].update(template="run_my_code"))
    assert any("template" in problem for problem in builder.check_bundle(bundle))


def test_a_missing_verifier_parameter_is_refused(bundle):
    edit(bundle, lambda d: d["tasks"][0]["verifier"].update(params={}))
    assert any("缺少" in problem for problem in builder.check_bundle(bundle))


def test_a_verifier_script_that_is_not_in_the_materials_is_refused(bundle):
    edit(bundle, lambda d: d["tasks"][0]["verifier"]["params"].update(cmd="python3 hidden.py"))
    assert any("不在材料目录里" in problem for problem in builder.check_bundle(bundle))


def test_a_verifier_may_not_shell_out_to_anything(bundle):
    edit(bundle, lambda d: d["tasks"][0]["verifier"]["params"].update(cmd="curl http://x"))
    assert any("只允许" in problem for problem in builder.check_bundle(bundle))


def test_a_materials_path_cannot_escape_the_bundle(bundle):
    edit(bundle, lambda d: d["tasks"][0].update(materials="../../etc"))
    assert any("相对路径" in problem for problem in builder.check_bundle(bundle))


def test_a_missing_oracle_is_refused(bundle):
    edit(bundle, lambda d: d["tasks"][0].pop("oracle"))
    assert any("oracle" in problem for problem in builder.check_bundle(bundle))


def test_exec_needs_a_pinned_image(bundle):
    edit(bundle, lambda d: d.update(env={"kind": "exec"}))
    assert any("image" in problem for problem in builder.check_bundle(bundle))


def test_eval_visibility_must_be_declared(bundle):
    edit(bundle, lambda d: d.pop("eval_visible_to_authors"))
    assert any("eval_visible_to_authors" in problem for problem in builder.check_bundle(bundle))


def test_the_slug_must_match_the_directory(bundle):
    edit(bundle, lambda d: d.update(slug="something-else"))
    assert any("目录名" in problem for problem in builder.check_bundle(bundle))


def test_init_writes_a_skeleton_that_says_what_is_missing(tmp_path):
    root = builder.init("new-scenario", tmp_path / "new-scenario")
    assert (root / "bundle.json").is_file()
    problems = builder.check_bundle(root)
    # A skeleton is never publishable as-is: that is the point of validating it.
    assert any("oracle" in problem for problem in problems), problems


def test_a_wrong_oracle_fails_gate_one(bundle):
    """The gate that matters: a reference solution that does not solve the task."""
    (bundle / "oracle" / "t01.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    result = builder.gates(bundle)
    assert result["ok"] is False
    assert result["tasks"][0]["oracle_passes"] is False
