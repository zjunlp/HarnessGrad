"""StrongREJECT opts in; real driver controls use mock actors and a local HTTP judge."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from data.datasets import strongreject as dataset
from data.scaffolds import registry
from harnessgrad import scoring_identity as scoring
from tools import prepare_h0
from test_strongreject_dataset import endpoint, prepared

ROOT = Path(__file__).resolve().parents[2]


def test_only_strongreject_has_opted_in():
    assert {p.parent.name for p in scoring.PROVIDERS.glob("*/scoring_profile.py")} == {"strongreject"}


def test_resolved_judge_endpoint_and_parameters_define_identity(monkeypatch):
    monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", "judge-a")
    monkeypatch.setenv("HG_JUDGE_BASE_URL", "http://127.0.0.1:1234/v1")
    first = scoring.resolve("strongreject")
    monkeypatch.setenv("HG_JUDGE_API_KEY", "rotated-secret-must-not-be-recorded")
    monkeypatch.setenv("HG_JUDGE_MODEL", "unused-global-model")
    monkeypatch.setenv("HG_JUDGE_BASE_URL", "http://127.0.0.1:1234/v1/")
    assert scoring.resolve("strongreject") == first
    assert "rotated-secret" not in json.dumps(first)
    monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", "judge-b")
    assert scoring.resolve("strongreject") != first
    monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", "judge-a")
    monkeypatch.setenv("HG_JUDGE_BASE_URL", "http://127.0.0.1:4321/v1")
    assert scoring.resolve("strongreject") != first
    monkeypatch.setenv("HG_JUDGE_BASE_URL", "http://127.0.0.1:1234/v1")
    original = dataset.judge_profile
    def different_parameters():
        profile = copy.deepcopy(original())
        profile["judge"]["parameters"]["temperature"] = .5
        return profile
    monkeypatch.setattr(dataset, "judge_profile", different_parameters)
    assert scoring.resolve("strongreject") != first


def test_h0_configuration_separates_judges(monkeypatch):
    from types import SimpleNamespace
    args = SimpleNamespace(dataset="strongreject", side="train", study_ids=None,
                           model="agent", endpoint="endpoint", trials=1)
    monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", "judge-a")
    first = prepare_h0.configuration(args)
    monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", "judge-b")
    assert prepare_h0.configuration(args) != first


@pytest.mark.parametrize("changed", ["adapter", "scorer", "templates", "transport"])
def test_scoring_dependencies_are_versioned_without_checkout_paths(tmp_path, monkeypatch, changed):
    import shutil
    original = scoring.resolve("strongreject")
    assets = tmp_path / "repo/data/datasets/strongreject"
    transport = assets.parents[2] / "eval/service"
    for name in ("verify/check.py", "lib/scoring.py", "scoring_profile.py", "specs/sources.json"):
        target = assets / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dataset.ASSETS / name, target)
    shutil.copyfile(dataset.ASSETS.with_suffix(".py"), assets.with_suffix(".py"))
    transport.mkdir(parents=True)
    for name in ("judge.py", "judge_request.py", "tool_transport.py"):
        shutil.copyfile(dataset.ASSETS.parents[2] / "eval/service" / name, transport / name)
    monkeypatch.setattr(dataset, "ASSETS", assets)
    assert scoring.resolve("strongreject") == original
    if changed == "templates":
        sources = copy.deepcopy(dataset.SOURCES)
        next(e for e in sources["files"] if e["path"] == "upstream-judge_templates.json")["sha256"] = "0" * 64
        monkeypatch.setattr(dataset, "SOURCES", sources)
    elif changed == "adapter":
        with assets.with_suffix(".py").open("a") as handle:
            handle.write("\n# synthetic adapter version change\n")
    else:
        path = assets / "verify/check.py" if changed == "scorer" else transport / "judge.py"
        with path.open("a") as handle:
            handle.write("\n# synthetic version change\n")
    assert scoring.resolve("strongreject") != original


def test_h0_preparation_loads_the_same_dotenv_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("HG_STRONGREJECT_JUDGE_MODEL", raising=False)
    monkeypatch.setattr(prepare_h0, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("HG_STRONGREJECT_JUDGE_MODEL=dotenv-judge\nHG_JUDGE_API_KEY=dotenv-secret\n")
    recorded = {}
    def lookup(config, root):
        recorded.update(config)
        return None
    monkeypatch.setattr(prepare_h0, "find_existing", lookup)
    assert prepare_h0.main(["--dataset", "strongreject", "--dry-run", "--runs-root", str(tmp_path / "runs")]) == 0
    assert recorded["scoring_identity"]["profile"]["judge"]["model"] == "dotenv-judge"
    assert "dotenv-secret" not in json.dumps(recorded)


@pytest.fixture
def run_driver(prepared, endpoint, monkeypatch, tmp_path):
    def run(run_id, *extra, model="judge-a", rounds=0, side="train", overrides=None,
            split="default", harness_path=None):
        monkeypatch.setenv("HG_STRONGREJECT_JUDGE_MODEL", model)
        tasks = [{"task_id": tid} for tid in prepared.rows()]
        if split == "default":
            resolved = registry.default_split(
                tasks, split_groups=registry.load_split_groups("strongreject", tasks))
        elif split == "official":
            resolved = registry.official_split("strongreject", tasks)
        else:
            resolved = {side: [task["task_id"] for task in tasks]}
        tid = resolved[side][0]
        env = {**os.environ, "HG_AGENT_BACKEND": "mock", "HG_AGENT_MAX_STEPS": "1"}
        env.update(overrides or {})
        harness_args = (["--harness-path", str(harness_path)] if harness_path is not None
                        else ["--harness", "loop_plain"])
        command = [sys.executable, str(ROOT / "driver.py"), *harness_args,
            "--dataset", "strongreject", "--side", side, "--tasks", tid, "--sampling", "all",
            "--rounds", str(rounds), "--jobs", "1", "--improver", "none",
            "--run-id", run_id, "--runs-root", str(tmp_path / "runs"),
            "--work-root", str(tmp_path / "work"),
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/echo_base/run.py'}"]
        if split is not None:
            command += ["--split", split]
        command += extra
        return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
    return run


def curve(tmp_path, run_id):
    return [json.loads(line) for line in (tmp_path / "runs" / run_id / "curve.jsonl").read_text().splitlines()]


def test_driver_records_profiles_and_guards_h0_reuse(run_driver, tmp_path):
    donor = run_driver("donor", rounds=1)
    assert donor.returncode == 0, donor.stdout + donor.stderr
    points = curve(tmp_path, "donor")
    assert len(points) == 2
    profile = points[0]["scoring_identity"]
    assert points[1]["scoring_identity"] == profile
    for filename in ("run_meta.json", "h0.json"):
        assert json.loads((tmp_path / "runs/donor" / filename).read_text())["scoring_identity"] == profile
    same = run_driver("same", "--h0-from", "donor")
    assert same.returncode == 0, same.stdout + same.stderr
    assert curve(tmp_path, "same")[0]["scoring_identity"] == profile
    different = run_driver("different", "--h0-from", "donor", model="judge-b")
    assert different.returncode == 2
    assert "scoring_identity" in different.stderr
    assert not (tmp_path / "runs/different/curve.jsonl").exists()
    path = tmp_path / "runs/donor/h0.json"
    payload = json.loads(path.read_text())
    del payload["scoring_identity"]
    path.write_text(json.dumps(payload))
    legacy = run_driver("legacy", "--h0-from", "donor")
    assert legacy.returncode == 2
    assert "scoring_identity is missing" in legacy.stderr


def test_driver_resume_rejects_changes_before_rewriting_records(run_driver, tmp_path):
    first = run_driver("train")
    assert first.returncode == 0, first.stdout + first.stderr
    run = tmp_path / "runs/train"
    before = {name: (run / name).read_bytes() for name in ("run_meta.json", "curve.jsonl", "h0.json")}
    for kwargs in ({"model": "judge-b"}, {"overrides": {"HG_JUDGE_BASE_URL": "http://127.0.0.1:9/v1"}}):
        result = run_driver("train", "--resume", rounds=1, **kwargs)
        assert result.returncode == 2
        assert "scoring_identity" in result.stderr
        assert {name: (run / name).read_bytes() for name in before} == before
    same = run_driver("train", "--resume", rounds=1)
    assert same.returncode == 0, same.stdout + same.stderr
    assert len(curve(tmp_path, "train")) == 2
    exam = run_driver("exam", "--from-run", "train", "--from-round", "0", side="eval")
    assert exam.returncode == 0, exam.stdout + exam.stderr
    assert curve(tmp_path, "exam")[0]["scoring_identity"] == curve(tmp_path, "train")[0]["scoring_identity"]
    meta = tmp_path / "runs/exam/run_meta.json"
    original = meta.read_bytes()
    again = run_driver("exam", "--resume", "--from-run", "train", "--from-round", "0", side="eval", model="judge-b")
    assert again.returncode == 2
    assert "scoring_identity" in again.stderr
    assert meta.read_bytes() == original


def test_driver_does_not_publish_gain_against_another_judge(run_driver, tmp_path):
    first = run_driver("reference")
    assert first.returncode == 0, first.stdout + first.stderr
    reference = tmp_path / "baseline.json"
    reference.write_text(json.dumps(curve(tmp_path, "reference")[0]))
    candidate = run_driver("candidate", "--baseline", str(reference), model="judge-b", rounds=1)
    assert candidate.returncode == 0, candidate.stdout + candidate.stderr
    point = curve(tmp_path, "candidate")[1]
    assert point["baseline"]["comparable"] is False
    assert any("scoring_identity" in reason for reason in point["baseline"]["problems"])
    assert "improvement" not in point and "capability_floor" not in point
