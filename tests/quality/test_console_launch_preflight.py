"""实验启动弹窗必须在真正起跑前显示服务端守卫的结论。"""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _console():
    spec = importlib.util.spec_from_file_location(
        "console_launch_server", ROOT / "console" / "server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _form(**overrides):
    form = {
        "dataset": "demo",
        "harness": "loop",
        "rounds": 2,
        "jobs": 1,
        "trials": 1,
        "sampling": "all",
        "improver": "noop",
        "improver_model": "",
    }
    form.update(overrides)
    return form


def _prepare(module, monkeypatch, budget_dir: Path):
    root = budget_dir.parent
    driver = root / "driver.py"
    driver.write_text("# fixture\n", encoding="utf-8")
    runs = root / "runs"
    runs.mkdir(exist_ok=True)
    monkeypatch.setattr(module, "BUDGET_DIR", budget_dir)
    monkeypatch.setattr(module, "DRIVER", driver)
    monkeypatch.setattr(module, "RUNS", runs)
    monkeypatch.setattr(module, "python_supported", lambda: True)
    monkeypatch.setattr(module, "live_drivers_by_run", lambda: {})
    monkeypatch.setattr(module, "adapters", lambda: [{"name": "demo"}])
    monkeypatch.setattr(module, "base_harnesses", lambda: ["loop"])


def test_preview_surfaces_environment_guard_without_launching(tmp_path, monkeypatch):
    module = _console()
    missing_budget = tmp_path / "missing-budget"
    _prepare(module, monkeypatch, missing_budget)

    payload = module.launch_preview(_form())

    assert payload["argv"], "有效表单仍应展示命令预览"
    assert payload["preflight"] == {
        "ok": False,
        "status": 400,
        "error": "no-budget-dir",
        "detail": f"GPU 预算目录不存在：{missing_budget}（要先排窗口）",
    }


def test_preview_is_ready_only_when_the_real_guard_passes(tmp_path, monkeypatch):
    module = _console()
    budget = tmp_path / "budget"
    budget.mkdir()
    _prepare(module, monkeypatch, budget)

    payload = module.launch_preview(_form())

    assert payload["preflight"] == {"ok": True, "status": 200}
    assert "--dataset" in payload["argv"]
    assert "demo" in payload["argv"]


def test_preview_rejects_invalid_numbers_without_a_broken_argv(tmp_path, monkeypatch):
    module = _console()
    budget = tmp_path / "budget"
    budget.mkdir()
    _prepare(module, monkeypatch, budget)

    payload = module.launch_preview(_form(rounds="not-a-number"))

    assert payload["argv"] == []
    assert payload["preflight"]["ok"] is False
    assert payload["preflight"]["error"] == "bad-value"
    assert "rounds 必须是整数" in payload["preflight"]["detail"]


def test_driver_probe_is_empty_when_proc_is_unavailable(monkeypatch):
    module = _console()

    def unavailable(_path):
        raise FileNotFoundError("no procfs")

    monkeypatch.setattr(module.pathlib.Path, "iterdir", unavailable)
    assert module.live_drivers_by_run() == {}


def test_preview_reports_an_unsupported_python_before_launch(tmp_path, monkeypatch):
    module = _console()
    budget = tmp_path / "budget"
    budget.mkdir()
    _prepare(module, monkeypatch, budget)
    monkeypatch.setattr(module, "python_supported", lambda: False)

    payload = module.launch_preview(_form())

    assert payload["preflight"]["ok"] is False
    assert payload["preflight"]["error"] == "unsupported-python"
    assert "Python 3.10+" in payload["preflight"]["detail"]
