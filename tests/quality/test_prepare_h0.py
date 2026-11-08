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
from unittest.mock import Mock

from test_dataset_runtime import environment
from test_dataset_execution_runtime import execution

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("prepare_h0", ROOT / "tools" / "prepare_h0.py")
prepare_h0 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_h0)


@pytest.fixture(autouse=True)
def authored_legacy_harness(monkeypatch):
    """Configuration unit tests use an unopted authored Harness; Loop has real controls."""
    original=prepare_h0.harness_source
    def source(args):
        if getattr(args,'harness_path',None) is None and getattr(args,'harness','loop')=='loop':
            return ROOT/'tests/fixtures/base_harness/loop_plain'
        return original(args)
    monkeypatch.setattr(prepare_h0,'harness_source',source)
    # Never load a real dataset in unit tests. The context is a synthetic task plan.
    from harnessgrad import verification_context
    monkeypatch.setattr(prepare_h0, '_verification_preflight',
                        lambda dataset, side, ids, split, sampling, environment:
                        verification_context.create({tid: 'a' * 64 for tid in (ids or ['t1', 't2'])}))



def _mock_driver(monkeypatch, launch):
    """Double measurements while leaving source-only Git preflight real."""
    real_run = prepare_h0.subprocess.run
    def dispatch(command, **kwargs):
        if command and str(command[0]) == "git":
            return real_run(command, **kwargs)
        return launch(command, **kwargs)
    monkeypatch.setattr(prepare_h0.subprocess, "run", dispatch)


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


def _write_run(root: pathlib.Path, name: str, *, per_task: dict, invalid=None, sha=None,
               config=None) -> pathlib.Path:
    if sha is None:
        sha = (config or {}).get("harness_sha", "abc")
    run = root / name
    run.mkdir(parents=True)
    verification = (config or {}).get("verification_context")
    (run / "h0.json").write_text(json.dumps({
        "h0_sha": sha, "verification_context": verification,
        "base": {"per_task": per_task, "invalid": invalid or {}, "score": 0.5,
                 "harness_failed": None, "verification_context": verification}}))
    metadata = {"h0_configuration": config, "verification_context": verification}
    if config and "agent_backend" in config:
        metadata["sampling_policy"] = config.get("sampling", "all")
        metadata["config"] = {"agent": {"model": config["agent_model"],
                                         "backend": config["agent_backend"],
                                         "base_url": config["endpoint"]},
                              "run": {"sampling": config.get("sampling", "all")}}
        (run / "curve.jsonl").write_text(json.dumps({"verification_context": verification, "measured_by_platform": True,
            "sampling": {"policy": config.get("sampling", "all")}, "identity": {
            "agent_model": config["agent_model"], "agent_backend": config["agent_backend"],
            "harness_sha": sha}}))
    (run / "run_meta.json").write_text(json.dumps(metadata))
    return run


def test_a_complete_baseline_is_accepted(tmp_path):
    config = prepare_h0.configuration(_args(tmp_path, ["t1", "t2"]))
    run = _write_run(tmp_path, "h0-appworld-1", per_task={"t1": 0.0, "t2": 1.0}, config=config)
    summary = prepare_h0.verify(run, None, 2, configuration=config)
    assert summary == {"score": 0.5, "n_tasks": 2, "h0_sha": config["harness_sha"]}


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
    _mock_driver(monkeypatch, launch)
    with pytest.raises(SystemExit, match=message):
        prepare_h0.main(["--dataset", "demo", "--study-ids", str(ids),
                         "--runs-root", str(tmp_path / "runs")])
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("dataset", ["demo", "appworld", "alfworld"])
def test_study_ids_are_forwarded_for_every_adapter(tmp_path, monkeypatch, dataset, execution):
    ids = tmp_path / "ids.txt"
    ids.write_text("t1,t2\nt3\tt4\n")
    # This configuration/CLI control must not load any named adapter's corpus.
    # Supply a typed preflight result from a genuinely admitted authored artifact;
    # do not let the fake driver process swallow a managed preflight subprocess.
    from harnessgrad import dataset_runtime, dataset_runtime_identity
    _, _, store, _ = execution
    artifact = dataset_runtime.prepare_execution('control', root=store)
    binding = dataset_runtime.bind(artifact, 'verifier')
    task_ids = ['t1', 't2', 't3', 't4']
    verifiers = {tid: {'python_runtime': binding,
                       'argv': [binding['python'], '-I', '-B', 'check.py'],
                       'runtime_paths': list(binding['runtime_paths'])} for tid in task_ids}
    selected = dataset_runtime_identity.for_evaluation(
        None, [{'task_id': tid} for tid in task_ids], {}, verifiers)
    preflight = Mock(return_value=selected)
    monkeypatch.setattr(prepare_h0.dataset_runtime_identity, 'resolve_h0', preflight)
    admission = Mock(wraps=dataset_runtime.admit_binding)
    monkeypatch.setattr(dataset_runtime, 'admit_binding', admission)
    captured = {}
    def launch(command, **kwargs):
        captured.update(command=command, env=kwargs["env"])
        return prepare_h0.subprocess.CompletedProcess(command, 2)
    _mock_driver(monkeypatch, launch)
    with pytest.raises(SystemExit, match="H0 run failed"):
        prepare_h0.main(["--dataset", dataset, "--study-ids", str(ids),
                         "--runs-root", str(tmp_path / "runs")])
    command = captured["command"]
    assert command[command.index("--tasks") + 1] == "t1,t2,t3,t4"
    assert captured["env"]["HG_APPWORLD_STUDY_IDS"] == str(ids.resolve())
    assert captured["env"]["HG_ALFWORLD_STUDY_IDS"] == str(ids.resolve())
    assert preflight.call_args.args == (dataset,)
    assert preflight.call_args.kwargs['task_ids'] == task_ids
    assert admission.call_count > 0, 'H0 lookup must re-admit the selected artifact before driver launch'


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
    _mock_driver(monkeypatch, launch)
    with pytest.raises(SystemExit, match="no such split file"):
        prepare_h0.main(["--dataset", "demo", "--split", str(tmp_path / "missing.json"),
                         "--runs-root", str(tmp_path / "runs")])


def test_model_override_reaches_the_child_environment(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setenv("HG_AGENT_MODEL", "inherited-model")
    def launch(command, **kwargs):
        captured.update(kwargs["env"])
        return prepare_h0.subprocess.CompletedProcess(command, 2)
    _mock_driver(monkeypatch, launch)
    with pytest.raises(SystemExit, match="H0 run failed"):
        prepare_h0.main(["--dataset", "demo", "--model", "requested-model",
                         "--runs-root", str(tmp_path / "runs")])
    assert captured["HG_AGENT_MODEL"] == "requested-model"


def test_configuration_resolves_dotenv_then_environment_then_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare_h0, "ROOT", tmp_path)
    monkeypatch.setattr(prepare_h0, "harness_source", lambda args: ROOT / "tests/fixtures/base_harness/loop_plain")
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
    _mock_driver(monkeypatch, forbidden_launch)
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
    monkeypatch.setattr(prepare_h0, "harness_source", lambda args: ROOT / "tests/fixtures/base_harness/loop_plain")
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
    _mock_driver(monkeypatch, launch)
    with pytest.raises(SystemExit, match="H0 run failed"):
        prepare_h0.main(["--dataset", "demo", "--runs-root", "runs"])
    command = captured["command"]
    assert command[command.index("--runs-root") + 1] == str((caller / "runs").resolve())
    assert pathlib.Path(captured["cwd"]) == ROOT
