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
        # 2026-10-22: the launch form asks for the task model explicitly, because the driver takes
        # it as `HG_AGENT_MODEL` on the process and the console used to inherit whatever the
        # server had. `mock` is always offered and needs no endpoint.
        "task_model": "mock",
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
    # The reading side degrades: health and the run list show no live driver rather than 500.
    assert module.live_drivers_by_run() == {}
    assert module.procfs_readable() is False


def test_launch_is_refused_when_procfs_cannot_be_read(tmp_path, monkeypatch):
    """**看不见 ≠ 没有。** /proc 读不到时必须拒绝起跑,而不是当作"没有 driver"放行。

    这条是合入时补的:`live_drivers_by_run()` 在 /proc 不可读时返回 `{}`,而守卫原先只看那个
    字典,于是同一个 `{}` 既表示"机器上没实验"也表示"我没法知道" —— 后者放行就等于在别人的
    实验旁边再起一个,正好是这份守卫存在的理由。
    """
    module = _console()
    budget = tmp_path / "budget"
    budget.mkdir()
    _prepare(module, monkeypatch, budget)

    def unavailable(self):
        raise FileNotFoundError("no procfs")

    monkeypatch.setattr(module.pathlib.Path, "iterdir", unavailable)
    code, body = module.guard_launch(_form())
    assert code == 503, body
    assert body["error"] == "liveness-unknown"


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


# ------------------------------------------- 设置：两个 endpoint，key 不出服务端 ---

def test_settings_round_trip_keeps_the_stored_key(tmp_path, monkeypatch):
    """表单留空 = 不修改。否则只改模型名也会把已存的 key 擦掉。"""
    module = _console()
    monkeypatch.setattr(module, "SETTINGS", tmp_path / "settings.json")

    module.save_settings({"task": {"base_url": "http://127.0.0.1:8002/v1",
                                   "model": "qwen3.5-9b-local", "api_key": "sk-task"}})
    # 第二次只改模型，key 留空
    module.save_settings({"task": {"model": "qwen3.5-9b-local-v2", "api_key": ""}})

    stored = module.load_settings()["task"]
    assert stored["api_key"] == "sk-task", "空白字段把 key 擦掉了"
    assert stored["model"] == "qwen3.5-9b-local-v2"
    assert stored["base_url"] == "http://127.0.0.1:8002/v1", "空白字段把 base_url 擦掉了"


def test_the_ui_view_never_carries_the_key(tmp_path, monkeypatch):
    """页面只该知道「有没有配」，不该知道值。"""
    module = _console()
    monkeypatch.setattr(module, "SETTINGS", tmp_path / "settings.json")
    module.save_settings({"chat": {"base_url": "https://example.invalid/v1",
                                   "model": "x", "api_key": "sk-secret-value"}})
    view = module.settings_for_ui()
    assert "api_key" not in view["chat"]
    assert view["chat"]["api_key_set"] is True
    assert "sk-secret-value" not in repr(view)


def test_the_task_model_list_follows_the_configured_endpoint(tmp_path, monkeypatch):
    module = _console()
    monkeypatch.setattr(module, "SETTINGS", tmp_path / "settings.json")
    module.save_settings({"task": {"model": "served-model-name"}})
    listed = module.task_models()
    assert "served-model-name" in listed
    assert "mock" in listed, "demo 不需要 endpoint，必须始终可选"
