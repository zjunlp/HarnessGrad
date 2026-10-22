"""Ordinary entrypoint contracts with authored data and no external execution.

These tests establish wiring and refusal behavior, not live GPU, sandbox, quota,
backend enforcement or production/H0 readiness.
"""
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

import driver
from eval.run import runner
from tools import serve_ui


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("Task 242 forbids process, network, model and H0 execution")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(driver, "evaluate", refuse)
    monkeypatch.setattr(driver, "_run_mode_a", refuse)
    monkeypatch.setattr(driver, "_run_eval_side", refuse)
    monkeypatch.setattr(driver, "_probe_tool_calls", refuse)
    monkeypatch.setattr(driver, "bind_budget", refuse)
    monkeypatch.setattr(driver, "stage_workspace", refuse)
    monkeypatch.setattr(driver.container, "available", refuse)
    monkeypatch.setattr(driver.container, "self_check", refuse)
    monkeypatch.setattr(runner.sandbox_mod, "run", refuse)


def test_help_explains_scope_without_loading_or_running(monkeypatch, capsys):
    monkeypatch.setattr(driver, "load_env", lambda *a: pytest.fail("help loaded .env"))
    monkeypatch.setattr(driver.datasets, "load_split", lambda *a: pytest.fail("help loaded data"))
    monkeypatch.setattr("sys.argv", ["driver.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        driver.main()
    assert exc.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "no universal filesystem-quota or backend-observer prerequisite" in help_text
    assert "Existing declared-policy refusals still apply" in help_text
    assert "required for diagnostic/pilot_10pct sampling" in help_text
    assert "never an automatic fallback" in help_text
    assert "Exec tasks retain their container boundary" in help_text
    assert "Synthetic checks do not authorize production diagnostics or H0" in help_text


@pytest.fixture
def ordinary_driver(monkeypatch, tmp_path):
    run_id = "synthetic-entrypoint-242"
    monkeypatch.setattr("sys.argv", [
        "driver.py", "--sampling", "all", "--rounds", "0", "--improver", "none",
        "--run-id", run_id, "--runs-root", str(tmp_path / "runs"),
        "--work-root", str(tmp_path / "work"),
    ])
    monkeypatch.setattr(driver, "load_env", lambda *a: {})
    config = driver.platform_config.load({
        "HG_AGENT_BACKEND": "openai", "HG_AGENT_MODEL": "synthetic-model",
        "HG_AGENT_BASE_URL": "http://127.0.0.1:8001/v1",
    })
    monkeypatch.setattr(driver.platform_config, "current", lambda: config)
    # This is authored model identity, never an endpoint readiness observation.
    monkeypatch.setattr(driver, "describe_model", lambda: dict(
        model="synthetic-model", backend="openai", live=True, credentials=True))
    monkeypatch.setattr(driver, "load_improvers", lambda: {})
    monkeypatch.setattr(driver, "forbidden_reason", lambda *a: None)
    monkeypatch.setattr(driver.datasets, "load_split", lambda *a: (
        [dict(task_id="synthetic-task-242")], {}, None))
    monkeypatch.setattr(driver.datasets, "oracle_source", lambda *a: ("authored", 0))
    monkeypatch.setattr(driver.datasets, "load_tasks", lambda *a, **kwargs: ([], {}, {}))
    monkeypatch.setattr(driver.datasets, "load_envs", lambda *a, **kwargs: {})
    monkeypatch.setattr(driver, "_warn_missing_overlay", lambda *a: None)
    monkeypatch.setattr(driver, "manifest", lambda *a: dict(name="authored", version="test"))
    # Keep every scratch write inside the supplied worktree's pytest basetemp.
    monkeypatch.setattr(driver, "workspace_is_outside_platform", lambda *a: True)
    monkeypatch.setattr(driver.console, "attach", lambda *a: None)
    notes = []
    monkeypatch.setattr(driver.console, "note", lambda message, **k: notes.append(message))
    monkeypatch.setattr(driver.services_mod, "reap", lambda *a: [])
    for key in ("HG_IMPROVER_NAME", "HARNESSGRAD_WORK_ROOT", "HARNESSGRAD_SEED"):
        monkeypatch.delenv(key, raising=False)
    return notes, tmp_path / "runs" / run_id


class BeforeExecution(Exception):
    """Stop at metadata construction, before workspace staging or evaluation."""


@pytest.mark.parametrize("sandbox_requested", [True, False])
def test_ordinary_model_identity_reaches_metadata_without_strict_backend_prerequisite(
        monkeypatch, ordinary_driver, sandbox_requested):
    import sys

    notes, run_dir = ordinary_driver
    checks = []
    def self_check(*args):
        checks.append(args)
        return dict(ok=True, detail="Authored self-check; no namespace executed")
    monkeypatch.setattr(driver.sandbox, "self_check", self_check)
    if not sandbox_requested:
        monkeypatch.setattr(sys, "argv", [*sys.argv, "--no-sandbox"])
    recorded = {}
    def stop(run_dir, **fields):
        recorded.update(fields)
        raise BeforeExecution
    monkeypatch.setattr(driver, "_write_run_meta", stop)
    with pytest.raises(BeforeExecution):
        driver.main()
    assert len(checks) == int(sandbox_requested)
    assert recorded["sandbox"]["requested"] is sandbox_requested
    assert recorded["sandbox"]["active"] is sandbox_requested
    assert recorded["config"]["run"]["sandbox"] is sandbox_requested
    assert recorded["diagnostic_policy"] is None and recorded["budget"] is None
    assert not (run_dir / "staged_plan.json").exists()
    assert not (run_dir / "curve.jsonl").exists()
    if not sandbox_requested:
        assert any("running with --no-sandbox" in note for note in notes)


def test_requested_sandbox_failure_refuses_before_metadata_or_execution(
        monkeypatch, ordinary_driver):
    notes, run_dir = ordinary_driver
    monkeypatch.setattr(driver.sandbox, "self_check", lambda *a: dict(
        ok=False, detail="Authored sandbox unavailable"))
    monkeypatch.setattr(driver, "_write_run_meta", lambda *a, **k: pytest.fail(
        "failed sandbox must not publish active metadata"))
    assert driver.main() == 2
    assert "refusing to run: Authored sandbox unavailable" in notes
    assert not any("running with --no-sandbox" in note for note in notes)
    assert not (run_dir / "run_meta.json").exists()
    assert not (run_dir / "curve.jsonl").exists()


@pytest.mark.parametrize("no_sandbox", [False, True])
def test_ui_preserves_ordinary_sampling_and_explicit_sandbox_choice(
        monkeypatch, tmp_path, no_sandbox):
    monkeypatch.setattr(serve_ui, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(serve_ui, "JOBS", {})
    monkeypatch.setattr(serve_ui, "model_presets", lambda: {"authored": {
        "HG_AGENT_BACKEND": "openai", "HG_AGENT_MODEL": "synthetic-model",
        "HG_AGENT_BASE_URL": "http://127.0.0.1:8001/v1",
    }})
    captured = {}
    def capture(argv, **kwargs):
        captured.update(argv=argv, env=kwargs["env"])
        kwargs["stdout"].close()
        return SimpleNamespace(pid=242)
    # Capture the process request; this stub never executes the driver.
    monkeypatch.setattr(serve_ui.subprocess, "Popen", capture)
    assert serve_ui.start_job(dict(run_id="synthetic-ui-242", model="authored",
                                   no_sandbox=no_sandbox)) == {"started": "synthetic-ui-242"}
    argv = captured["argv"]
    assert argv[argv.index("--sampling") + 1] == "all"
    assert ("--no-sandbox" in argv) is no_sandbox
    assert "--policy-evidence" not in argv and "--budget-profile" not in argv
    launch = json.loads((tmp_path / "runs/synthetic-ui-242/launch.json").read_text())
    assert launch["sandbox"] is (not no_sandbox)
    assert launch["argv"] == argv
