"""Reviewed scenarios enter the existing driver, with their file identity intact."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _spec(**fields):
    spec = {"name": "unit", "dataset": "terminal_bench", "split": "declared",
            "side": "train", "sampling": "all", "rounds": 0, "trials": 1,
            "env_kinds": ["files"], "budget": {"jobs": 1},
            "model": {"backend": "mock"}}
    spec.update(fields)
    return spec


def _scenario(tmp_path, spec=None, raw=None):
    root = tmp_path / "scenarios"
    root.mkdir(exist_ok=True)
    path = root / "unit.json"
    path.write_bytes(raw if raw is not None else json.dumps(spec or _spec()).encode())
    return root, path


def _load(name, root):
    return importlib.import_module("scenarios").load(name, root)


def test_scenario_load_records_raw_file_identity(tmp_path):
    root, path = _scenario(tmp_path, raw=json.dumps(_spec(), indent=3).encode()+b"\n")
    spec, receipt = _load("unit", root)
    assert spec == _spec()
    assert receipt == {"name": "unit", "path": str(path.resolve()),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.mark.parametrize("name", ["", ".", "..", "../unit", "/unit", "a/unit", "a\\unit", "C:unit"])
def test_scenario_name_cannot_escape_root(tmp_path, name):
    root, _ = _scenario(tmp_path)
    with pytest.raises(ValueError, match="name"):
        _load(name, root)


def test_scenario_link_escape_is_refused_before_read(tmp_path, monkeypatch):
    root, path = _scenario(tmp_path)
    outside = tmp_path / "private.json"
    outside.write_text(json.dumps(_spec()))
    path.unlink()
    path.symlink_to(outside)
    original = Path.read_bytes

    def guarded_read(p):
        assert p.resolve() != outside, "an escaping target was read"
        return original(p)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    with pytest.raises(ValueError, match="outside"):
        _load("unit", root)


@pytest.mark.parametrize("raw", [b"{broken", b"[]", b"null", b"\xff"])
def test_scenario_invalid_json_is_named(tmp_path, raw):
    root, _ = _scenario(tmp_path, raw=raw)
    with pytest.raises(ValueError, match="scenario"):
        _load("unit", root)


@pytest.mark.parametrize("field", ["name", "dataset", "split", "side", "sampling", "rounds",
                                  "trials", "env_kinds", "budget", "model"])
def test_scenario_missing_fields_are_named(tmp_path, field):
    spec = _spec()
    del spec[field]
    root, _ = _scenario(tmp_path, spec)
    with pytest.raises(ValueError, match=field):
        _load("unit", root)


@pytest.mark.parametrize("field,value", [
    ("name", "other"), ("dataset", ""), ("split", []), ("split", "../outside.json"),
    ("side", "test"), ("sampling", 1), ("rounds", True), ("rounds", -1),
    ("trials", False), ("trials", 0), ("env_kinds", "files"), ("env_kinds", [1]),
    ("budget", []), ("budget", {"jobs": True}), ("budget", {"jobs": 2}),
    ("model", []), ("model", {"backend": "openai"}),
    ("model", {"backend": "mock", "required": "yes"}),
    ("limits", []), ("limits", {"agent_timeout_s": False}),
    ("limits", {"wall_clock_minutes": 0}), ("note", 1),
])
def test_scenario_schema_refuses_invalid_fields(tmp_path, field, value):
    root, _ = _scenario(tmp_path, _spec(**{field: value}))
    with pytest.raises(ValueError, match=field):
        _load("unit", root)


def test_eval_scenario_requires_pool(tmp_path):
    root, _ = _scenario(tmp_path, _spec(side="eval"))
    with pytest.raises(ValueError, match="pool"):
        _load("unit", root)


def test_scenario_keeps_extensions_and_does_not_import_adapters(tmp_path, monkeypatch):
    root, _ = _scenario(tmp_path, _spec(extension={"enabled": True}))
    module = importlib.import_module("scenarios")
    monkeypatch.setattr(importlib, "import_module", lambda *a, **kw: pytest.fail("adapter import"))
    spec, _ = module.load("unit", root)
    assert spec["extension"] == {"enabled": True}


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_scenario_must_be_regular_before_read(tmp_path, monkeypatch, kind):
    root, path = _scenario(tmp_path)
    path.unlink()
    if kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    monkeypatch.setattr(Path, "read_bytes", lambda p: pytest.fail("non-file read"))
    with pytest.raises(ValueError, match="regular file"):
        _load("unit", root)


def _args(**fields):
    values = dict(dataset="demo", split=None, side="train", pool=None,
                  sampling="diagnostic", rounds=None, trials=1, jobs=1)
    values.update(fields)
    return SimpleNamespace(**values)


def test_scenario_overrides_defaults_before_policy_gates():
    import driver
    spec = _spec()
    before = copy.deepcopy(spec)
    args = _args()
    driver._apply_scenario(args, spec, set())
    assert (args.dataset, args.sampling, args.rounds) == ("terminal_bench", "all", 0)
    assert spec == before


@pytest.mark.parametrize("field,value", [("dataset", "other"), ("split", "official"),
    ("side", "eval"), ("sampling", "random_1"), ("rounds", 2), ("trials", 3), ("jobs", 2)])
def test_scenario_rejects_conflicting_explicit_options(field, value):
    import driver
    with pytest.raises(ValueError, match=field):
        driver._apply_scenario(_args(**{field: value}), _spec(), {field})


def test_equal_explicit_options_are_allowed():
    import driver
    args = _args(sampling="all", rounds=0)
    driver._apply_scenario(args, _spec(), {"sampling", "rounds"})
    assert args.sampling == "all" and args.rounds == 0


def test_relative_scenario_split_is_checked_and_normalized(tmp_path, monkeypatch):
    import driver
    monkeypatch.setattr(driver, "PLATFORM_ROOT", tmp_path)
    path = tmp_path / "splits/demo.json"
    path.parent.mkdir()
    path.write_text('{"train": ["t01"], "eval": ["t02"]}')
    args = _args(split=str(path))
    driver._apply_scenario(args, _spec(split="splits/demo.json"), {"split"})
    assert args.split == str(path.resolve())


@pytest.mark.parametrize("dataset,pool,key", [("appworld", "dev", "HG_APPWORLD_POOL"),
                                            ("alfworld", "valid_seen", "HG_ALFWORLD_POOL")])
def test_scenario_pool_uses_existing_adapter_knob(dataset, pool, key):
    import driver
    assert driver._scenario_pool_env(dataset, pool) == {key: pool}


def test_unsupported_pool_is_refused():
    import driver
    with pytest.raises(ValueError, match="pool"):
        driver._scenario_pool_env("terminal_bench", "dev")


@pytest.mark.parametrize("fields,model,man,detail", [
    ({}, {"backend": "openai", "live": True}, {}, "model"),
    ({"model": {"backend": "live"}}, {"backend": "mock", "live": False}, {}, "model"),
    ({"env_kinds": ["exec"]}, {"backend": "mock"}, {}, "environment"),
    ({"limits": {"wall_clock_minutes": 1}}, {"backend": "mock"}, {}, "limits"),
])
def test_scenario_runtime_requirements_are_enforced(fields, model, man, detail):
    import driver
    with pytest.raises(ValueError, match=detail):
        driver._check_scenario_runtime(_spec(**fields), model, man, {})


@pytest.mark.parametrize("fields", [dict(name="other", dataset="demo"),
    dict(name="demo-smoke", dataset="demo", model={"backend": "live"}),
    dict(name="demo-smoke", dataset="probe_set"), dict(dataset="not_a_dataset")])
def test_only_approved_local_fixture_exception_is_allowed(monkeypatch, fields):
    import driver
    monkeypatch.setattr(driver.datasets, "published", lambda: ("terminal_bench",))
    with pytest.raises(ValueError, match="dataset"):
        driver._check_scenario_dataset(_spec(**fields))


def test_approved_local_demo_is_not_added_to_registry(monkeypatch):
    import driver
    monkeypatch.setattr(driver.datasets, "published", lambda: ())
    driver._check_scenario_dataset(_spec(name="demo-smoke", dataset="demo"))
    assert driver.datasets.published() == ()


def _external(tmp_path):
    root = tmp_path / "external"
    if not root.exists():
        import shutil
        shutil.copytree(ROOT / "base_harness/loop", root)
    return root


def _entry_args(tmp_path, *extra):
    return ["driver.py", "--scenario", "demo-smoke", "--harness-path", str(_external(tmp_path)),
            "--run-id", "scenario-check", "--runs-root", str(tmp_path/"runs"),
            "--work-root", str(tmp_path/"work"), *extra]


def test_scenario_unknown_dataset_is_refused_before_outputs(tmp_path, monkeypatch, capsys):
    import driver
    monkeypatch.setattr(driver, "load_scenario", lambda *a: (_spec(dataset="not_a_dataset"), {}), raising=False)
    monkeypatch.setattr(driver.datasets, "published", lambda: ())
    monkeypatch.setattr(driver, "evaluate", lambda *a, **kw: pytest.fail("runner started"))
    monkeypatch.setattr(sys, "argv", _entry_args(tmp_path))
    assert driver.main() == 2
    assert "unknown dataset" in capsys.readouterr().err
    assert not (tmp_path/"runs").exists()


def test_scenario_pool_is_bound_before_adapter_import(tmp_path, monkeypatch):
    import driver
    monkeypatch.setenv("HG_APPWORLD_POOL", "")
    monkeypatch.delitem(sys.modules, "data.datasets.appworld", raising=False)
    monkeypatch.setattr(driver, "load_scenario", lambda *a: (_spec(dataset="appworld", side="eval", pool="dev"), {}), raising=False)

    class ReachedRegistry(Exception):
        pass

    def published():
        assert os.environ["HG_APPWORLD_POOL"] == "dev"
        raise ReachedRegistry

    monkeypatch.setattr(driver.datasets, "published", published)
    # A2 keeps A1's direct-eval refusal. Train still binds the adapter's eval-pool knob.
    spec = _spec(dataset="appworld", pool="dev")
    monkeypatch.setattr(driver, "load_scenario", lambda *a: (spec, {}), raising=False)
    monkeypatch.setattr(sys, "argv", _entry_args(tmp_path))
    with pytest.raises(ReachedRegistry):
        driver.main()


def test_show_config_keeps_its_no_dataset_fast_path(monkeypatch, capsys):
    import driver
    monkeypatch.setattr(driver, "load_scenario", lambda *a: pytest.fail("scenario read"), raising=False)
    monkeypatch.setattr(sys, "argv", ["driver.py", "--show-config", "--scenario", "../bad"])
    assert driver.main() == 0
    assert json.loads(capsys.readouterr().out)["agent"]["backend"] == "mock"


def _run_scenario(tmp_path, run_id, *extra):
    argv = _entry_args(tmp_path, *extra)
    argv[argv.index("--run-id")+1] = run_id
    return subprocess.run([sys.executable, *argv], cwd=ROOT,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"}, capture_output=True, text=True, timeout=180)


@pytest.mark.parametrize("extra", [(), ("--sampling=all", "--rounds", "0")])
def test_scenario_run_records_raw_file_identity(tmp_path, extra):
    proc = _run_scenario(tmp_path, "metadata", *extra)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    meta = json.loads((tmp_path/"runs/metadata/run_meta.json").read_text())
    raw = (ROOT/"scenarios/demo-smoke.json").read_bytes()
    assert meta["scenario"] == {"name": "demo-smoke", "path": str((ROOT/"scenarios/demo-smoke.json").resolve()),
                                "sha256": hashlib.sha256(raw).hexdigest()}
    assert meta["harness_source"]["entries"] == 3
    points = [json.loads(line) for line in (tmp_path/"runs/metadata/curve.jsonl").read_text().splitlines()]
    assert len(points) == 1 and sorted(points[0]["per_task"]) == ["t01", "t02", "t03", "t04"]
    assert points[0]["measured_by_platform"] is True


def test_same_scenario_preserves_split_sha(tmp_path):
    for run_id in ("one", "two"):
        proc = _run_scenario(tmp_path, run_id)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    metas = [json.loads((tmp_path/f"runs/{name}/run_meta.json").read_text()) for name in ("one", "two")]
    assert metas[0]["split_sha"] == metas[1]["split_sha"]
    assert metas[0]["scenario"] == metas[1]["scenario"]


def test_explicit_empty_scenario_is_not_ignored(tmp_path):
    proc = _run_scenario(tmp_path, "empty", "--scenario", "", "--sampling", "all", "--rounds", "0")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "scenario name" in proc.stderr
    assert not (tmp_path/"runs").exists()


def test_changed_scenario_resume_cannot_rewrite_metadata(tmp_path, monkeypatch, capsys):
    import driver
    before = {"scenario": {"name": "demo-smoke", "sha256": "old"}, "original": True}
    path = tmp_path/"runs/scenario-check/run_meta.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(before))
    original = path.read_bytes()
    monkeypatch.setattr(driver, "load_scenario", lambda *a: (
        _spec(name="demo-smoke", dataset="demo"), {"name": "demo-smoke", "sha256": "new"}))
    monkeypatch.setattr(sys, "argv", _entry_args(tmp_path, "--resume"))
    assert driver.main() == 2
    assert path.read_bytes() == original, "a refused resume overwrote scenario identity"
    assert "scenario" in capsys.readouterr().err


@pytest.mark.parametrize("reason", ["environment", "already-imported"])
def test_pool_conflicts_are_refused_before_registry(tmp_path, monkeypatch, capsys, reason):
    import driver
    monkeypatch.setenv("HG_APPWORLD_POOL", "test" if reason == "environment" else "dev")
    if reason == "already-imported":
        monkeypatch.setitem(sys.modules, "data.datasets.appworld", SimpleNamespace())
    monkeypatch.setattr(driver, "load_scenario", lambda *a: (_spec(dataset="appworld", pool="dev"), {}))
    monkeypatch.setattr(driver.datasets, "published", lambda: pytest.fail("registry reached"))
    monkeypatch.setattr(sys, "argv", _entry_args(tmp_path))
    assert driver.main() == 2
    assert "pool" in capsys.readouterr().err
    assert not (tmp_path/"runs").exists()


@pytest.mark.parametrize("scenario", [None, {"name": "demo-smoke", "sha256": "a"*64}])
def test_unchanged_resume_admission_keeps_metadata(tmp_path, scenario):
    import driver
    path = tmp_path/"run/run_meta.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"scenario": scenario, "marker": "unchanged"}))
    before = path.read_bytes()
    args = SimpleNamespace(resume=True, run_id="run", runs_root=str(tmp_path))
    driver._check_scenario_resume(args, scenario)
    assert path.read_bytes() == before


@pytest.mark.parametrize("stage", ["load_split", "oracle_source", "load_tasks", "load_envs"])
def test_lazy_dataset_failures_are_named_before_execution(tmp_path, monkeypatch, capsys, stage):
    import driver
    tasks = [{"task_id": "t01", "goal": "train"}, {"task_id": "t02", "goal": "eval"}]
    monkeypatch.setattr(driver, "load_scenario", lambda *a: (_spec(), {}))
    monkeypatch.setattr(driver.datasets, "published", lambda: ("terminal_bench",))
    monkeypatch.setattr(driver.datasets, "load_split", lambda *a: (
        tasks, {"t01": "alpha", "t02": "beta"}, {"train": ["t01"], "eval": ["t02"]}))
    monkeypatch.setattr(driver.datasets, "oracle_source", lambda *a: ("released", None))
    monkeypatch.setattr(driver.datasets, "load_tasks", lambda *a, **kw: (tasks, {}, {}))
    monkeypatch.setattr(driver.datasets, "load_envs", lambda *a, **kw: {})

    def unavailable(*a, **kw):
        raise ValueError(f"terminal_bench: missing local declarations at {stage}")

    monkeypatch.setattr(driver.datasets, stage, unavailable)
    monkeypatch.setattr(driver, "evaluate", lambda *a, **kw: pytest.fail("runner started"))
    monkeypatch.setattr(sys, "argv", _entry_args(tmp_path))
    assert driver.main() == 2
    stderr = capsys.readouterr().err
    assert "terminal_bench" in stderr and stage in stderr and "Traceback" not in stderr
    assert not (tmp_path/"runs").exists()


def test_large_integer_limit_receives_named_refusal(tmp_path):
    import driver
    root, _ = _scenario(tmp_path, _spec(limits={"agent_timeout_s": 10**400}))
    with pytest.raises(ValueError, match="limits"):
        spec, _ = _load("unit", root)
        driver._check_scenario_runtime(spec, {"backend": "mock"}, {}, {})
