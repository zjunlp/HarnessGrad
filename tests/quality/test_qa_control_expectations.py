"""Adapter declarations own the shared QA control's do-nothing expectations."""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data.datasets import bfcl_v3
from data.scaffolds import registry  # noqa: E402
from data.scaffolds.qa import control  # noqa: E402


@pytest.mark.parametrize("path", ["local", "platform"])
@pytest.mark.parametrize(
    "declaration,gold,expected",
    [
        ("default", {"functions": ["lookup"]}, 0.0),
        ("default", {}, 0.0),
        ("constant", {}, 0.25),
        ("bfcl", {"functions": ["lookup"]}, 0.0),
        ("bfcl", {"functions": []}, 1.0),
        ("bfcl", {}, 1.0),
    ],
)
def test_noop_expected_declaration(path, declaration, gold, expected, tmp_path, monkeypatch):
    record = {"task_id": "synthetic/0", "gold": gold}
    # An unrelated module name proves that only the declaration determines the reward.
    adapter = SimpleNamespace(
        __name__="synthetic_adapter",
        control_task=lambda task_id: record,
        task_for=lambda record: {"task_id": record["task_id"]},
        setup_for=lambda record: {"files": []},
        verify_for=lambda record: {},
        DEFAULT_ENV={"kind": "files"},
    )
    if declaration == "constant":
        adapter.NOOP_EXPECTED = 0.25
    elif declaration == "bfcl":
        adapter.NOOP_EXPECTED = bfcl_v3.NOOP_EXPECTED

    measured = [expected]
    if path == "local":
        monkeypatch.setattr(control, "materialize", lambda *args, **kwargs: None)
        monkeypatch.setattr(control, "run_check", lambda *args: (measured[0], "{}"))
        run = control.run_local
    else:
        # Patch `run_one` on the module object instead of installing a fake module in
        # `sys.modules`. `control.run_platform` does `from <module> import run_one`
        # inside the call, so an attribute patch is seen either way -- and it keeps
        # working when the canonical location moves (`eval.runner` is a documented
        # compatibility alias for `eval.run.runner`), where an entry under the old
        # name would simply be ignored and the real platform would be invoked.
        import eval.runner as runner_mod
        monkeypatch.setattr(
            runner_mod, "run_one",
            lambda **kwargs: {"verdict": {"score": measured[0]}})
        monkeypatch.setattr(control.shutil, "copytree", lambda *args, **kwargs: None)
        monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
        run = control.run_platform

    assert run(adapter, record, oracle=False, workdir=tmp_path) == 0
    measured[0] = expected + 0.5
    assert run(adapter, record, oracle=False, workdir=tmp_path) == 1


def test_shared_control_has_no_adapter_module_literals():
    source = Path(control.__file__).read_text(encoding="utf-8")
    adapter_names = {f"data.datasets.{name}" for name in registry.published()}
    offenders = [
        (node.lineno, node.value)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in adapter_names
    ]
    assert not offenders, f"shared QA control names adapter modules: {offenders}"
