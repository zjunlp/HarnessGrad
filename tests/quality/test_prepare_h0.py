"""The H0 pass: one baseline per configuration, and a refusal to publish a holey one.

`driver.py --rounds N` measures round 0 inside every run, so a batch that starts N runs has N
baselines unless someone shares them. `tools/prepare_h0.py` is the missing step: measure once,
hand the run id to every study run. These tests pin the two things that make it trustworthy --
what counts as "the same configuration", and what is allowed to be called an H0.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("prepare_h0", ROOT / "tools" / "prepare_h0.py")
prepare_h0 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_h0)


def _args(tmp_path: pathlib.Path, ids: list[str] | None = None, **over):
    ids_file = None
    if ids is not None:
        ids_file = tmp_path / "ids.txt"
        ids_file.write_text("\n".join(ids) + "\n")
    base = {"dataset": "appworld", "side": "train", "study_ids": ids_file,
            "model": "qwen3.5-9b-local", "endpoint": "http://127.0.0.1:8100/v1", "trials": 1,
            "sampling": "all"}
    base.update(over)
    return type("Args", (), base)


def test_the_same_ids_in_a_different_order_are_the_same_configuration(tmp_path):
    a = prepare_h0.configuration(_args(tmp_path, ["t2", "t1"]))
    b = prepare_h0.configuration(_args(tmp_path, ["t1", "t2"]))
    assert a == b, "the study set is a set: file order is not part of the measurement"


def test_a_different_study_set_or_model_is_a_different_h0(tmp_path):
    a = prepare_h0.configuration(_args(tmp_path, ["t1"]))
    b = prepare_h0.configuration(_args(tmp_path, ["t1", "t2"]))
    c = prepare_h0.configuration(_args(tmp_path, ["t1"], model="other-model"))
    assert len({json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True),
                json.dumps(c, sort_keys=True)}) == 3


def _write_run(root: pathlib.Path, name: str, *, per_task: dict, invalid=None, sha="abc",
               config=None) -> pathlib.Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "h0.json").write_text(json.dumps({
        "h0_sha": sha,
        "base": {"per_task": per_task, "invalid": invalid or {}, "score": 0.5,
                 "harness_failed": None}}))
    metadata = {"h0_configuration": config}
    if config and "agent_backend" in config:
        metadata["sampling_policy"] = config.get("sampling", "all")
        metadata["config"] = {"agent": {"model": config["agent_model"],
                                         "backend": config["agent_backend"],
                                         "base_url": config["endpoint"]},
                              "run": {"sampling": config.get("sampling", "all")}}
        (run / "curve.jsonl").write_text(json.dumps({"measured_by_platform": True,
            "sampling": {"policy": config.get("sampling", "all")}, "identity": {
            "agent_model": config["agent_model"], "agent_backend": config["agent_backend"]}}))
    (run / "run_meta.json").write_text(json.dumps(metadata))
    return run


def test_a_complete_baseline_is_accepted(tmp_path):
    run = _write_run(tmp_path, "h0-appworld-1", per_task={"t1": 0.0, "t2": 1.0})
    summary = prepare_h0.verify(run, None, 2)
    assert summary == {"score": 0.5, "n_tasks": 2, "h0_sha": "abc"}


def test_matching_task_count_with_different_ids_is_refused(tmp_path):
    run = _write_run(tmp_path, "h0-appworld-wrong-ids", per_task={"t1": 0.0, "t3": 1.0})
    with pytest.raises(SystemExit, match="1 missing, 1 unexpected"):
        prepare_h0.verify(run, None, 2, study_ids=["t1", "t2"])


@pytest.mark.parametrize("kwargs, needle", [
    ({"per_task": {"t1": 0.0}, "invalid": {"t2": {"stage": "admission"}}, "n": 2}, "invalid"),
    ({"per_task": {"t1": 0.0}, "n": 2}, "1 tasks measured, the study set has 2"),
    ({"per_task": {}, "n": 2}, "no per-task verdicts"),
    ({"per_task": {"t1": 0.0, "t2": 1.0}, "sha": "", "n": 2}, "no h0_sha recorded"),
])
def test_a_holey_baseline_is_refused_not_published(tmp_path, kwargs, needle):
    n = kwargs.pop("n")
    run = _write_run(tmp_path, "h0-appworld-2", **kwargs)
    with pytest.raises(SystemExit) as exc:
        prepare_h0.verify(run, None, n)
    assert needle in str(exc.value)


def test_an_existing_baseline_is_reused_for_the_same_configuration(tmp_path):
    config = prepare_h0.configuration(_args(tmp_path, ["t1", "t2"]))
    run = _write_run(tmp_path, "h0-appworld-3", per_task={"t1": 0.0, "t2": 1.0},
                     config=config)
    found = prepare_h0.find_existing(config, tmp_path)
    assert found is not None and found[0] == run


@pytest.mark.parametrize("measured", [["t1", "t3"], ["t1", "t2", "t3"]])
def test_existing_baseline_must_measure_the_actual_study_set(tmp_path, measured):
    config = prepare_h0.configuration(_args(tmp_path, ["t1", "t2"]))
    _write_run(tmp_path, "h0-appworld-wrong-set", per_task=dict.fromkeys(measured, 0.5),
               config=config)
    assert prepare_h0.find_existing(config, tmp_path) is None


def test_a_different_configuration_is_not_reused(tmp_path):
    _write_run(tmp_path, "h0-appworld-4", per_task={"t1": 0.0, "t2": 1.0},
               config={"dataset": "appworld", "study_ids_sha": "other"})
    assert prepare_h0.find_existing({"dataset": "appworld", "study_ids_sha": "x"}, tmp_path) is None


def test_a_missing_study_ids_file_is_refused(tmp_path):
    with pytest.raises(SystemExit) as exc:
        prepare_h0.main(["--dataset", "appworld", "--study-ids", str(tmp_path / "missing.txt"),
                         "--runs-root", str(tmp_path / "runs"), "--dry-run"])
    assert "no such study-ids file" in str(exc.value)


def test_dry_run_measures_the_baseline_and_nothing_else(tmp_path, capsys):
    ids = tmp_path / "ids.txt"
    ids.write_text("t1\nt2\n")
    code = prepare_h0.main(["--dataset", "appworld", "--study-ids", str(ids),
                            "--runs-root", str(tmp_path / "runs"), "--dry-run"])
    printed = capsys.readouterr().out
    assert code == 0
    assert "--rounds 0" in printed and "--improver none" in printed
    assert "--tasks t1,t2" in printed
    assert not (tmp_path / "runs").exists() or not list((tmp_path / "runs").glob("h0-*"))


@pytest.mark.parametrize("contents, message", [
    ("\n# no tasks\n", "no task ids"),
    ("t1,t2\nt1\n", "duplicate task ids"),
])
def test_invalid_study_file_is_refused_before_launch(tmp_path, monkeypatch, contents, message):
    ids = tmp_path / "ids.txt"
    ids.write_text(contents)
    def launch(*args, **kwargs):
        pytest.fail("an invalid study file must not launch the driver")
    monkeypatch.setattr(prepare_h0.subprocess, "run", launch)
    with pytest.raises(SystemExit, match=message):
        prepare_h0.main(["--dataset", "demo", "--study-ids", str(ids),
                         "--runs-root", str(tmp_path / "runs")])
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("dataset", ["demo", "appworld", "alfworld"])
def test_study_ids_are_forwarded_for_every_adapter(tmp_path, monkeypatch, dataset):
    ids = tmp_path / "ids.txt"
    ids.write_text("t1,t2\nt3\tt4\n")
    captured = {}
    def launch(command, **kwargs):
        captured.update(command=command, env=kwargs["env"])
        return prepare_h0.subprocess.CompletedProcess(command, 2)
    monkeypatch.setattr(prepare_h0.subprocess, "run", launch)
    with pytest.raises(SystemExit, match="H0 run failed"):
        prepare_h0.main(["--dataset", dataset, "--study-ids", str(ids),
                         "--runs-root", str(tmp_path / "runs")])
    command = captured["command"]
    assert command[command.index("--tasks") + 1] == "t1,t2,t3,t4"
    assert captured["env"]["HG_APPWORLD_STUDY_IDS"] == str(ids.resolve())
    assert captured["env"]["HG_ALFWORLD_STUDY_IDS"] == str(ids.resolve())


def test_without_study_ids_the_driver_keeps_its_side(tmp_path, capsys):
    assert prepare_h0.main(["--dataset", "demo", "--runs-root", str(tmp_path), "--dry-run"]) == 0
    assert "--tasks" not in capsys.readouterr().out


@pytest.mark.parametrize("split", ["default", "official"])
def test_explicit_split_is_forwarded_and_part_of_h0_identity(tmp_path, capsys, split):
    declared = prepare_h0.configuration(_args(tmp_path))
    selected = prepare_h0.configuration(_args(tmp_path, split=split))
    assert selected == {**declared, "split": split}
    assert prepare_h0.main(["--dataset", "demo", "--split", split,
                            "--runs-root", str(tmp_path), "--dry-run"]) == 0
    assert f"--split {split}" in capsys.readouterr().out


def test_split_file_identity_uses_contents_instead_of_path(tmp_path):
    split = tmp_path / "split.json"
    copied = tmp_path / "copied.json"
    split.write_text('{"train": ["t1"], "eval": ["t2"]}')
    copied.write_bytes(split.read_bytes())
    original = prepare_h0.configuration(_args(tmp_path, split=str(split)))
    assert prepare_h0.configuration(_args(tmp_path, split=str(copied))) == original
    split.write_text('{"train": ["t2"], "eval": ["t1"]}')
    assert prepare_h0.configuration(_args(tmp_path, split=str(split))) != original


def test_missing_split_file_is_refused_before_launch(tmp_path, monkeypatch):
    def launch(*args, **kwargs):
        pytest.fail("a missing split file must not launch the driver")
    monkeypatch.setattr(prepare_h0.subprocess, "run", launch)
    with pytest.raises(SystemExit, match="no such split file"):
        prepare_h0.main(["--dataset", "demo", "--split", str(tmp_path / "missing.json"),
                         "--runs-root", str(tmp_path / "runs")])


def test_model_override_reaches_the_child_environment(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setenv("HG_AGENT_MODEL", "inherited-model")
    def launch(command, **kwargs):
        captured.update(kwargs["env"])
        return prepare_h0.subprocess.CompletedProcess(command, 2)
    monkeypatch.setattr(prepare_h0.subprocess, "run", launch)
    with pytest.raises(SystemExit, match="H0 run failed"):
        prepare_h0.main(["--dataset", "demo", "--model", "requested-model",
                         "--runs-root", str(tmp_path / "runs")])
    assert captured["HG_AGENT_MODEL"] == "requested-model"


def test_configuration_resolves_dotenv_then_environment_then_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare_h0, "ROOT", tmp_path)
    for name in ("HG_AGENT_MODEL", "HG_AGENT_BACKEND", "HG_AGENT_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text("HG_AGENT_MODEL=file-model\nHG_AGENT_BACKEND=openai\n"
                                   "HG_AGENT_BASE_URL=http://file/v1\n")
    args = _args(tmp_path, model=None, endpoint=None)
    initial = prepare_h0.configuration(args)
    assert (initial["agent_model"], initial["agent_backend"], initial["endpoint"]) == (
        "file-model", "openai", "http://file/v1")
    monkeypatch.setenv("HG_AGENT_MODEL", "environment-model")
    monkeypatch.setenv("HG_AGENT_BASE_URL", "http://environment/v1")
    changed = prepare_h0.configuration(args)
    assert changed != initial
    assert changed["agent_model"] == "environment-model"
    assert changed["endpoint"] == "http://environment/v1"
    explicit = prepare_h0.configuration(_args(tmp_path, model="cli-model", endpoint="http://cli/v1"))
    assert explicit["agent_model"] == "cli-model" and explicit["endpoint"] == "http://cli/v1"
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    assert prepare_h0.configuration(args)["agent_backend"] == "mock"


@pytest.mark.parametrize("field,value", [("agent_model", "wrong-model"),
                                         ("agent_backend", "wrong-backend"),
                                         ("endpoint", "http://wrong/v1")])
def test_recorded_configuration_cannot_override_measured_identity(tmp_path, field, value):
    config = prepare_h0.configuration(_args(tmp_path, ["t1"]))
    run = _write_run(tmp_path, "h0-appworld-identity", per_task={"t1": .5}, config=config)
    actual = {**config, field: value}
    meta = json.loads((run / "run_meta.json").read_text())
    meta["config"] = {"agent": {"model": actual["agent_model"],
                                  "backend": actual.get("agent_backend", "mock"),
                                  "base_url": actual["endpoint"]}}
    (run / "run_meta.json").write_text(json.dumps(meta))
    (run / "curve.jsonl").write_text(json.dumps({"measured_by_platform": True, "identity": {
        "agent_model": actual["agent_model"], "agent_backend": actual.get("agent_backend", "mock")}}))
    assert prepare_h0.find_existing(config, tmp_path) is None
    with pytest.raises(SystemExit, match="identity"):
        prepare_h0.verify(run, None, 1, configuration=config)


def test_reuse_hint_includes_the_run_store(tmp_path, capsys, monkeypatch):
    config = prepare_h0.configuration(_args(tmp_path, ["t1"]))
    run = _write_run(tmp_path, "h0-appworld-hint", per_task={"t1": .5}, config=config)
    def forbidden_launch(*args, **kwargs):
        pytest.fail("a reusable H0 must not launch a new measurement")
    monkeypatch.setattr(prepare_h0.subprocess, "run", forbidden_launch)
    assert prepare_h0.main(["--dataset", "appworld", "--study-ids", str(tmp_path / "ids.txt"),
                            "--model", config["agent_model"], "--endpoint", config["endpoint"],
                            "--runs-root", str(tmp_path)]) == 0
    assert f'--runs-root "{tmp_path}" --h0-from {run.name}' in capsys.readouterr().out


def test_curve_identity_must_match_even_when_environment_metadata_matches(tmp_path):
    config = prepare_h0.configuration(_args(tmp_path, ["t1"]))
    run = _write_run(tmp_path, "h0-appworld-harness-declaration", per_task={"t1": .5}, config=config)
    (run / "curve.jsonl").write_text(json.dumps({"measured_by_platform": True, "identity": {
        "agent_model": "harness-used-another-model", "agent_backend": config["agent_backend"]}}))
    assert prepare_h0.find_existing(config, tmp_path) is None
    with pytest.raises(SystemExit, match="curve identity agent_model"):
        prepare_h0.verify(run, None, 1, configuration=config)


@pytest.mark.parametrize("measured", [False, None])
def test_unverified_curve_cannot_publish_or_reuse_h0(tmp_path, measured):
    config = prepare_h0.configuration(_args(tmp_path, ["t1"]))
    run = _write_run(tmp_path, "h0-appworld-unverified", per_task={"t1": .5}, config=config)
    point = json.loads((run / "curve.jsonl").read_text())
    point["measured_by_platform"] = measured
    (run / "curve.jsonl").write_text(json.dumps(point))
    assert prepare_h0.find_existing(config, tmp_path) is None
    with pytest.raises(SystemExit, match="platform measurement"):
        prepare_h0.verify(run, None, 1, configuration=config)


@pytest.mark.parametrize("inherited", [None, "environment-judge"])
def test_opted_scoring_provider_keeps_dotenv_judge_precedence(tmp_path, monkeypatch, inherited):
    monkeypatch.setattr(prepare_h0, "ROOT", tmp_path)
    monkeypatch.delenv("HG_JUDGE_MODEL", raising=False)
    if inherited is not None:
        monkeypatch.setenv("HG_JUDGE_MODEL", inherited)
    (tmp_path / ".env").write_text("HG_JUDGE_MODEL=file-judge\n", encoding="utf-8")
    provider = tmp_path / "scoring_profile.py"
    provider.touch()
    monkeypatch.setattr(prepare_h0.scoring_identity, "provider_path", lambda _: provider)
    observed = []
    def resolve(_):
        import os
        observed.append(os.environ["HG_JUDGE_MODEL"])
        return None
    monkeypatch.setattr(prepare_h0.scoring_identity, "resolve", resolve)
    assert prepare_h0.main(["--dataset", "synthetic", "--runs-root", str(tmp_path / "runs"),
                            "--dry-run"]) == 0
    assert observed == [inherited or "file-judge"]


def test_sampling_changes_h0_identity_without_an_explicit_study_set(tmp_path):
    all_tasks = prepare_h0.configuration(_args(tmp_path, sampling="all"))
    one_task = prepare_h0.configuration(_args(tmp_path, sampling="random_1"))
    assert all_tasks != one_task
    assert all_tasks["sampling"] == "all" and one_task["sampling"] == "random_1"
    _write_run(tmp_path, "h0-appworld-random", per_task={"t1": .5}, config=one_task)
    assert prepare_h0.find_existing(all_tasks, tmp_path) is None


@pytest.mark.parametrize("source", ["curve", "sampling_policy", "run_config", "missing"])
def test_h0_verify_checks_the_measured_sampling_policy(tmp_path, source):
    config = prepare_h0.configuration(_args(tmp_path))
    run = _write_run(tmp_path, "h0-appworld-sampling", per_task={"t1": .5}, config=config)
    point = json.loads((run / "curve.jsonl").read_text())
    meta = json.loads((run / "run_meta.json").read_text())
    if source == "curve":
        point["sampling"]["policy"] = "random_1"
    elif source == "sampling_policy":
        meta["sampling_policy"] = "random_1"
    elif source == "run_config":
        meta["config"]["run"]["sampling"] = "random_1"
    else:
        point.pop("sampling")
        meta.pop("sampling_policy")
        meta["config"].pop("run")
    (run / "curve.jsonl").write_text(json.dumps(point))
    (run / "run_meta.json").write_text(json.dumps(meta))
    with pytest.raises(SystemExit, match="sampling"):
        prepare_h0.verify(run, None, 0, configuration=config)
    assert prepare_h0.find_existing(config, tmp_path) is None


def test_relative_runs_root_is_resolved_before_launching_in_another_cwd(tmp_path, monkeypatch):
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    captured = {}
    def launch(command, **kwargs):
        captured.update(command=command, cwd=kwargs["cwd"])
        return prepare_h0.subprocess.CompletedProcess(command, 2)
    monkeypatch.setattr(prepare_h0.subprocess, "run", launch)
    with pytest.raises(SystemExit, match="H0 run failed"):
        prepare_h0.main(["--dataset", "demo", "--runs-root", "runs"])
    command = captured["command"]
    assert command[command.index("--runs-root") + 1] == str((caller / "runs").resolve())
    assert pathlib.Path(captured["cwd"]) == ROOT
