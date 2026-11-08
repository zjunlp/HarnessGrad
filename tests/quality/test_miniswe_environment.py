"""Private mini state must preserve dependencies supplied for the executing Python."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("_miniswe_private_environment", ROOT / "base_harness/miniswe/agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


@pytest.mark.parametrize("overlay", [None, "/platform/dependencies with spaces:/other/declared-dependencies"])
def test_private_state_preserves_the_platform_overlay_and_removes_daily_settings(tmp_path, overlay):
    daily = tmp_path / "daily"
    daily.mkdir()
    parent = {"PATH": os.defpath, "HOME": str(daily), "HG_AGENT_API_KEY": "synthetic-task-key",
              "MSWEA_GLOBAL_CONFIG_DIR": str(daily), "MSWEA_MINI_CONFIG_PATH": str(daily / "mini.yaml"),
              "LITELLM_MODEL_REGISTRY_PATH": str(daily / "models.json"), "CODEX_HOME": str(daily),
              "OPENAI_API_KEY": "synthetic-daily-key", "ANTHROPIC_API_KEY": "synthetic-other-key",
              "HG_METHOD_API_KEY": "synthetic-method", "HG_ENV_API_KEY": "synthetic-env",
              "HG_JUDGE_API_KEY": "synthetic-judge", "PYTHONHOME": "/invalid/host/python",
              "PYTHONSTARTUP": str(daily / "startup.py")}
    if overlay is not None:
        parent["PYTHONPATH"] = overlay
    original = dict(parent)
    states = []
    for name in ("first", "second"):
        scratch = tmp_path / name
        scratch.mkdir()
        child = agent.child_environment(parent, scratch)
        assert child.get("PYTHONPATH") == overlay
        assert child["HG_AGENT_API_KEY"] == "synthetic-task-key"
        assert not any(key in child for key in ("PYTHONHOME", "PYTHONSTARTUP", "CODEX_HOME", "OPENAI_API_KEY",
                                                "ANTHROPIC_API_KEY", "MSWEA_MINI_CONFIG_PATH",
                                                "LITELLM_MODEL_REGISTRY_PATH", "HG_METHOD_API_KEY",
                                                "HG_ENV_API_KEY", "HG_JUDGE_API_KEY"))
        paths = [Path(child[key]) for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                 "XDG_CACHE_HOME", "XDG_STATE_HOME", "MSWEA_GLOBAL_CONFIG_DIR", "TMPDIR")]
        assert len(set(paths)) == len(paths)
        assert all(path.is_relative_to(scratch) and path.is_dir() and not list(path.iterdir()) for path in paths)
        states.append(set(paths))
    assert states[0].isdisjoint(states[1])
    assert parent == original and not list(daily.iterdir())


def test_child_python_imports_its_supplied_overlay_with_private_state(tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "overlay_dependency.py").write_text("VALUE = 'platform-overlay'\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    child = agent.child_environment({"PATH": os.defpath, "PYTHONPATH": str(overlay),
                                     "PYTHONHOME": "/unavailable/daily/python"}, scratch)
    code = ("import json, os, sys, overlay_dependency; print(json.dumps({"
            "'value':overlay_dependency.VALUE, 'python':sys.executable, "
            "'home':os.environ['HOME'], 'config':os.environ['MSWEA_GLOBAL_CONFIG_DIR']}))")
    result = subprocess.run([sys.executable, "-c", code], env=child, cwd=tmp_path,
                            capture_output=True, text=True, check=True, timeout=20)
    observed = json.loads(result.stdout)
    assert observed == {"value": "platform-overlay", "python": sys.executable,
                        "home": str(scratch / "home"), "config": str(scratch / "mini-config")}


def test_missing_dependencies_do_not_guess_a_user_site(monkeypatch):
    # Preparation belongs to the shared overlay tool, not a guessed HOME directory.
    monkeypatch.setattr(agent, "_importable", lambda name: False)
    monkeypatch.setattr(agent, "_manifest", lambda: {"runtime_paths": []})
    import site
    monkeypatch.setattr(site, "getusersitepackages", lambda: pytest.fail("guessed a user-site directory"))
    with pytest.raises(SystemExit, match="tools/configure_harness.py"):
        agent._site_for_child()


def test_managed_cli_preserves_task_python_configuration_and_private_state(tmp_path):
    parent={'PATH':'/image/venv/bin:/image/tools:/usr/bin','VIRTUAL_ENV':'/image/venv',
            'CONDA_PREFIX':'/image/conda','PYTHONPATH':'/image/modules',
            'PYTHONHOME':'/image/python','PYTHONUSERBASE':'/image/users',
            'PYTHONNOUSERSITE':'','PYTHONDONTWRITEBYTECODE':'',
            'MSWEA_MINI_CONFIG_PATH':'/daily/mini.yaml','HG_METHOD_API_KEY':'daily-secret'}
    child=agent.child_environment(parent,tmp_path,managed=True)
    for name,value in parent.items():
        if name.startswith('PYTHON') or name in ('PATH','VIRTUAL_ENV','CONDA_PREFIX'):
            assert child[name]==value
    assert 'MSWEA_MINI_CONFIG_PATH' not in child and 'HG_METHOD_API_KEY' not in child
    assert Path(child['MSWEA_GLOBAL_CONFIG_DIR']).is_relative_to(tmp_path)


def test_managed_missing_dependency_refuses_legacy_package_fallback(monkeypatch,tmp_path):
    (tmp_path/'minisweagent').mkdir()
    (tmp_path/'minisweagent/__init__.py').write_text('raise AssertionError("legacy fallback")')
    monkeypatch.setattr(agent,'_importable',lambda _:False)
    monkeypatch.setattr(agent,'_manifest',lambda:{'runtime':{'kind':'python'},'runtime_paths':[str(tmp_path)]})
    with pytest.raises(SystemExit,match='bound Python environment'):
        agent._site_for_child()
