"""Negative admission cases and installed-code probes, without ROMs or models."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.containers import sandbox
from policy_assertions import assert_policy_refusal
from eval.run.runner import run_one
from eval.policy.runtime_policy import admit_native_network, stage_agent_runtime


def control_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("jericho_control_review", ROOT / "data/datasets/jericho/control.py")
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    return control


def test_lazy_missing_provisioning_retains_the_actionable_load_error(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("jericho_missing_review", ROOT / "data/datasets/jericho.py")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)

    def absent():
        raise FileNotFoundError("synthetic missing ROMs")

    monkeypatch.setattr(adapter, "_official_games", absent)
    assert adapter._RECORDS == []
    with pytest.raises(ValueError, match="synthetic missing ROMs"):
        adapter.load()


def test_live_backend_is_refused_before_staging_or_execution(tmp_path, monkeypatch):
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({"entrypoint": "agent.py"}))
    monkeypatch.setenv("HG_AGENT_BACKEND", "openai")

    def unexpected(*args, **kwargs):
        pytest.fail("admission must happen before allocating task storage")

    monkeypatch.setattr("eval.run.runner.tempfile.mkdtemp", unexpected)
    # The declaration has to be one this process does **not** apply. `network: "none"` used to
    # serve here, but a native files task's network is isolated by the platform itself
    # (`--unshare-net`), so since w-jerichoadm it is admitted instead of refused - see
    # `test_network_none_admits_controls_and_mock_but_leaves_ordinary_tasks_alone` below. A native
    # files task's `cpus` is still only a declaration: nothing here enforces it, so it must refuse
    # before any task storage is allocated. Naming a genuinely unapplied field is what keeps this
    # guard from passing for the wrong reason.
    result = run_one(repo, {"task_id": "synthetic", "goal": "synthetic"},
                     env={"kind": "files", "network": "none", "cpus": 2})
    assert_policy_refusal(result, ("env.cpus",))


def test_network_none_admits_controls_and_mock_but_leaves_ordinary_tasks_alone():
    admit_native_network({"network": "none"}, {"role": "control"}, "openai")
    admit_native_network({"network": "none"}, {}, "mock")
    admit_native_network({}, {}, "openai")
    with pytest.raises(ValueError, match="network policy"):
        admit_native_network({"network": "host"}, {}, "mock")


@pytest.mark.parametrize("bad", ["../escape.py", "/escape.py", "__pycache__/cached.pyc", "module.pyc"])
def test_projection_rejects_escaping_paths_and_cached_code(tmp_path, bad):
    package = tmp_path / "installed"
    package.mkdir()
    with pytest.raises(ValueError, match="unsafe|bytecode"):
        stage_agent_runtime({"package_dir": str(package), "files": [
            {"path": bad, "content": "secret"}]}, tmp_path / "projection")


def test_projection_rejects_bulk_copies_symlinks_and_leftover_data(tmp_path):
    package = tmp_path / "installed"
    package.mkdir()
    code = package / "__init__.py"
    code.write_text("PUBLIC = True\n")
    alias = package / "alias.py"
    alias.symlink_to(code)
    for source in (package, alias):
        with pytest.raises(ValueError, match="regular files"):
            stage_agent_runtime({"package_dir": str(package), "files": [
                {"path": "__init__.py", "from": str(source)}]}, tmp_path / "projection")
    projection = tmp_path / "projection"
    projection.mkdir()
    (projection / "game_info.py").write_text("SYNTHETIC_SECRET")
    with pytest.raises(ValueError, match="empty"):
        stage_agent_runtime({"package_dir": str(package), "files": [
            {"path": "__init__.py", "content": "PUBLIC = True\n"}]}, projection)


def test_control_resets_a_previous_log_symlink_without_touching_its_target(tmp_path, monkeypatch):
    import data.datasets.jericho as adapter
    control = control_module()
    record = {"task_id": "synthetic"}
    monkeypatch.setitem(adapter.__dict__, "SETUP", {"synthetic": {"files": [
        {"path": adapter.LOG_FILE, "content": ""}]}})
    old_log = tmp_path / "old-log"
    old_log.write_text("SYNTHETIC_PREVIOUS_ACTIONS")
    work = tmp_path / "task"
    work.mkdir()
    (work / adapter.LOG_FILE).symlink_to(old_log)
    control.materialize(record, work)
    assert old_log.read_text() == "SYNTHETIC_PREVIOUS_ACTIONS"
    assert not (work / adapter.LOG_FILE).is_symlink()
    assert (work / adapter.LOG_FILE).read_text() == ""


@pytest.mark.parametrize("verdict", [
    {"score": 0.0, "evidence": {"measured": False}},
    {"score": 0.0},
    {"score": None, "evidence": {"measured": True}},
])
def test_control_cannot_accept_an_unmeasured_zero(tmp_path, monkeypatch, verdict):
    from types import SimpleNamespace
    control = control_module()
    module = SimpleNamespace(VERIFY={"synthetic": {}}, VERIFY_TIMEOUT_S=300,
                             DEFAULT_ENV={"kind": "files", "network": "none"})
    monkeypatch.setattr("eval.verification.verifier._verify", lambda *args, **kwargs: verdict)
    reward, _ = control.run_check(module, {"task_id": "synthetic"}, tmp_path)
    assert reward == -1.0


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap unavailable")
def test_installed_jericho_projection_imports_without_reference_payload(tmp_path):
    pytest.importorskip("numpy")
    import importlib.util
    if importlib.util.find_spec("jericho") is None:
        pytest.skip("Jericho not installed")
    import data.datasets.jericho as adapter

    spec = adapter._env()["agent_runtime"]
    overlay = stage_agent_runtime(spec, tmp_path / "projection")
    platform = tmp_path / "platform"
    platform.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    task = tmp_path / "task"
    task.mkdir()
    probe = task / "probe.py"
    probe.write_text('''import importlib.util, json, pathlib
import jericho
from jericho import defines
from jericho.jericho import _load_bindings, _load_frotz_lib
assert defines.BINDINGS_DICT == {}
assert _load_bindings('synthetic') == {}
assert importlib.util.find_spec('jericho.game_info') is None
package = pathlib.Path(jericho.__file__).parent
assert not (package / '__pycache__').exists()
assert not (package / 'clubfloyd_verb_counts.json').exists()
try:
    jericho.FrotzEnv.__new__(jericho.FrotzEnv).get_walkthrough()
except RuntimeError:
    pass
else:
    raise AssertionError('walkthrough API is available')
lib = _load_frotz_lib()
assert lib.get_max_score
print('installed runtime imports; native library loads; reference payload absent')
''')
    proc = sandbox.run([sys.executable, str(probe)], platform=platform, work_root=work,
                       repo=work, task_dir=task, env={"PYTHONDONTWRITEBYTECODE": "1"},
                       timeout_s=15, cwd=task, network="none", package_overlays=(overlay,))
    assert proc.returncode == 0, proc.stderr
    assert "reference payload absent" in proc.stdout
