"""实验启动弹窗必须在真正起跑前显示服务端守卫的结论。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


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
    monkeypatch.setattr(module, "BUDGET_CAPACITY", "3")
    monkeypatch.setattr(module, "DRIVER", driver)
    monkeypatch.setattr(module, "RUNS", runs)
    monkeypatch.setattr(module, "python_supported", lambda: True)
    monkeypatch.setattr(module, "sandbox_supported", lambda: SimpleNamespace(ok=True, detail="fixture"))
    monkeypatch.setattr(module, "live_drivers_by_run", lambda: {})
    monkeypatch.setattr(module, "adapters", lambda: [{"name": "demo"}])
    monkeypatch.setattr(module, "base_harnesses", lambda: ["loop"])
    monkeypatch.setattr(module, "improvers", lambda: [{"name": "noop", "kind": "cli", "model": ""}])


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
        "detail": "HG_CONSOLE_BUDGET_DIR must be an existing absolute writable directory.",
    }


def test_missing_or_in_checkout_budget_is_refused(tmp_path, monkeypatch):
    module = _console()
    budget = tmp_path / 'budget'
    budget.mkdir()
    _prepare(module, monkeypatch, budget)
    monkeypatch.setattr(module, 'BUDGET_DIR', None)
    assert module.guard_launch(_form())[1]['error'] == 'no-budget-dir'
    monkeypatch.setattr(module, 'BUDGET_DIR', module.PLATFORM)
    assert module.guard_launch(_form())[1]['error'] == 'no-budget-dir'


def test_shared_budget_capacity_is_independent_of_jobs(tmp_path, monkeypatch):
    module = _console()
    budget = tmp_path / 'budget'
    budget.mkdir()
    _prepare(module, monkeypatch, budget)
    form = _form(jobs=4)
    argv = module.launch_argv(form, 'synthetic')
    assert argv[argv.index('--jobs') + 1] == '4'
    assert argv[argv.index('--gpu-budget') + 1] == '3'
    (budget / 'capacity').write_text('2\n')
    assert module.guard_launch(form)[1]['error'] == 'budget-capacity'
    assert (budget / 'capacity').read_text() == '2\n'


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


def test_unrelated_driver_does_not_override_shared_capacity(tmp_path, monkeypatch):
    module = _console()
    budget = tmp_path / "budget"
    budget.mkdir()
    _prepare(module, monkeypatch, budget)
    monkeypatch.setattr(module, 'procfs_readable', lambda: False)
    monkeypatch.setattr(module, 'live_drivers_by_run', lambda: {'other': {'pid': 123}})
    assert module.guard_launch(_form()) == (200, {})
    monkeypatch.setattr(module, 'BUDGET_CAPACITY', '')
    assert module.guard_launch(_form())[1]['error'] == 'budget-capacity'


def test_preview_reports_an_unsupported_python_before_launch(tmp_path, monkeypatch):
    module = _console()
    budget = tmp_path / "budget"
    budget.mkdir()
    _prepare(module, monkeypatch, budget)
    monkeypatch.setattr(module, "python_supported", lambda: False)

    payload = module.launch_preview(_form())

    assert payload["preflight"]["ok"] is False
    assert payload["preflight"]["error"] == "unsupported-python"
    assert "Python 3.12+" in payload["preflight"]["detail"]


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
    # 这一侧在 2026-11-02 从 `chat` 改名成 `meta`（那一类其实是"Meta-Agent / 对话用的
    # 端点"）：写入仍然接受旧名字（`SETTINGS_ALIASES`），UI 只看新名字。先按旧名字写一份，
    # 证明别名落到了新分节上，再按新名字断言"页面只该知道有没有配，不该知道值"。
    module.save_settings({"chat": {"base_url": "https://example.invalid/v1",
                                   "model": "x", "api_key": "sk-secret-value"}})
    view = module.settings_for_ui()
    assert "chat" not in view
    assert "api_key" not in view["meta"]
    assert view["meta"]["api_key_set"] is True
    assert "sk-secret-value" not in repr(view)


def test_the_task_model_list_follows_the_configured_endpoint(tmp_path, monkeypatch):
    module = _console()
    monkeypatch.setattr(module, "SETTINGS", tmp_path / "settings.json")
    module.save_settings({"task": {"model": "served-model-name"}})
    listed = module.task_models()
    assert "served-model-name" in listed
    assert "mock" in listed, "demo 不需要 endpoint，必须始终可选"
