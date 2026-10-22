"""AppWorld release, released oracle, log replay, and control arms."""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import data.datasets.appworld as dataset  # noqa: E402

BUNDLE = dataset.DATASET_ROOT / "_src" / "data-0.1.0.bundle"
CONTROL_TASK = "e85d92a_1"


def test_bundle_digest_and_release_count():
    assert hashlib.sha256(BUNDLE.read_bytes()).hexdigest() == dataset.downloads()[0]["sha256"]
    assert len(dataset.task_ids()) == dataset.TASKS_IN_RELEASE == 732


def test_declared_split_files_cover_every_task():
    """The release's four declared lists, and the two this run draws its sides from.

    The study side is the official **train** split alone (90): dev is the selection split
    and is scored with `HG_APPWORLD_POOL=dev`, so it is neither studied nor part of the
    default exam. An earlier default studied train+dev (147), which made dev both the
    material a method learned from and the split its checkpoints were selected on.
    """
    splits = dataset.split_ids()
    assert {key: len(value) for key, value in splits.items()} == dataset.SPLIT_SIZES
    union = set().union(*splits.values())
    assert len(union) == 732 == len(dataset.task_ids())
    assert union == set(dataset.task_ids())
    assert set(dataset.SPLIT["train"]) == set(splits["train"])
    assert set(dataset.SPLIT["eval"]) == set(splits["test_normal"] + splits["test_challenge"])
    # dev is not in this run's sides -- the default pool holds no dev task.
    assert not (set(dataset.SPLIT["train"]) | set(dataset.SPLIT["eval"])) & set(splits["dev"])


def test_the_paper_count_is_an_earlier_build():
    """Preserve the mainline fact correction about the release's own task count."""
    paper, release = dataset.PAPER_SPLITS, dataset.SPLIT_SIZES
    assert paper["test_normal"] == release["test_normal"] == 168
    assert paper["test_challenge"] == release["test_challenge"] == 417
    assert paper["train"] - release["train"] == 15
    assert paper["dev"] - release["dev"] == 3
    assert sum(paper.values()) - sum(release.values()) == 18
    assert (paper["train"] - release["train"]) + (paper["dev"] - release["dev"]) == 18
    assert 18 % 3 == 0 and 18 // 3 == 6
    assert "2407.18901" in (ROOT / "data/datasets/appworld.py").read_text()


def test_pool_contains_compact_metadata_and_db_digests():
    rows = dataset._pool()
    assert len(rows) == 732
    assert all({"task_id", "split", "difficulty", "num_api_calls", "num_apps", "instruction", "dbs"} <= set(row) for row in rows)
    assert len({row["dbs"] for row in rows}) > 1


def test_released_reference_coverage_is_computed():
    assert dataset.ORACLE_SOURCE == "released"
    assert "TestTracker" in dataset.ORACLE_VALIDATED_BY
    assert dataset.ORACLE_COVERAGE.startswith("147 of 732")
    assert "requester" in dataset.oracle_files({"task_id": CONTROL_TASK})["reference.py"]
    with pytest.raises(ValueError, match="no released reference"):
        dataset.oracle_files({"task_id": "92fe421_3"})


def test_released_coverage_is_derived_from_bundle_members():
    with dataset.bundle_zip() as archive:
        train_dev = set(dataset.split_ids()["train"] + dataset.split_ids()["dev"])
        released = {
            name.split("/", 3)[2]
            for name in archive.namelist()
            if name.startswith("data/tasks/") and name.endswith("/ground_truth/api_calls.json")
        }
        solutions = {
            name.split("/", 3)[2]
            for name in archive.namelist()
            if name.startswith("data/tasks/") and name.endswith("/ground_truth/solution.py")
        }
    assert solutions == released
    assert released == train_dev
    assert len(released) == 147
    assert not (released & set(dataset.split_ids()["test_normal"] + dataset.split_ids()["test_challenge"]))
    assert dataset.POOL_SHA256 == __import__("hashlib").sha256(dataset.POOL.read_bytes()).hexdigest()


def test_every_uncovered_task_rejects_a_reference():
    uncovered = set(dataset.SPLIT["eval"])
    assert len(uncovered) == 585
    for task_id in uncovered:
        with pytest.raises(ValueError, match="no released reference"):
            dataset.oracle_files({"task_id": task_id})


def test_every_released_reference_passes_its_official_evaluation(tmp_path):
    """Run each released API-call oracle in its own process.

    AppWorld keeps global database/app state, so a single interpreter cannot safely
    evaluate two task ids.  One subprocess per covered task makes this a replay of the
    release artifacts rather than a test of a shared process's leftovers.
    """
    python = "/mnt/20t/xubuqiang/venvs/appworld/bin/python"
    probe = tmp_path / "probe.py"
    for task_id in sorted(dataset._oracle_ids()):
        reference = dataset.oracle_files({"task_id": task_id})["reference.py"]
        probe.write_text(textwrap.dedent(f"""
            import json, os
            from appworld import AppWorld
            os.environ["APPWORLD_ROOT"] = {str(dataset.UNPACKED)!r}
            with AppWorld(task_id={task_id!r}, experiment_name="released-oracle",
                          raise_on_failure=False) as world:
                world.execute({reference!r})
                tracker = world.evaluate()
                print(json.dumps({{"success": bool(tracker.success),
                                  "num_tests": tracker.num_tests,
                                  "failures": tracker.failures}}))
        """), encoding="utf-8")
        proc = subprocess.run([python, str(probe)], cwd=tmp_path,
                              env={**os.environ, "APPWORLD_ROOT": str(dataset.UNPACKED)},
                              capture_output=True, text=True, timeout=180)
        assert proc.returncode == 0, f"{task_id}: {proc.stdout[-500:]}\\n{proc.stderr[-500:]}"
        report = json.loads(proc.stdout.strip().splitlines()[-1])
        assert report["success"] is True and report["num_tests"] > 0, (
            f"{task_id}: {report}")


def test_agent_image_has_no_gold_and_service_rejects_direct_grade_access(tmp_path):
    """Exercise the image boundary and the service over HTTP, including hostile calls."""
    agent = dataset.IMAGE
    service = dataset.SERVICE_IMAGE
    probe = tmp_path / "probe.py"
    probe.write_text(textwrap.dedent("""
        import json, time, urllib.error, urllib.request
        from pathlib import Path
        for _ in range(100):
            try:
                urllib.request.urlopen("http://127.0.0.1:8000/", timeout=1)
                break
            except Exception: time.sleep(.1)
        else: raise AssertionError("service did not become healthy")
        def call(path, payload):
            req = urllib.request.Request("http://127.0.0.1:8000" + path,
                data=json.dumps(payload).encode(), headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as error: return error.code, error.read().decode()
        status, body = call("/initialize", {"task_id":"e85d92a_1", "load_ground_truth":True,
                                     "ground_truth_mode":"full",
                                     "experiment_name":"../../escape"})
        assert status == 200
        assert json.loads(body)["output"]["supervisor"]["email"]
        assert urllib.request.urlopen("http://127.0.0.1:8000/api_docs").status == 200
        assert call("/evaluate", {"task_id":"e85d92a_1"})[0] == 404
        assert call("/initialize", {"task_id":"92fe421_3"})[0] == 403
        assert call("/execute", {"task_id":"92fe421_3", "code":"print(1)"})[0] == 403
        def execute(code):
            status, body = call("/execute", {"task_id":"e85d92a_1", "code":code})
            assert status == 200
            return json.loads(body)["output"]
        for task in ("e85d92a_1", "92fe421_3"):
            output = execute(f"from pathlib import Path; print(Path('/appworld/data/tasks/{task}/ground_truth/answer.json').read_text())")
            assert "FileNotFoundError" in output, output
        output = execute("print(apis.supervisor.show_active_task())")
        assert "Velvet Echo" in output and "NOT_GIVEN" in output, output
    """), encoding="utf-8")
    image_probe = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", agent,
         "-c", "test -x /usr/local/bin/aw && ! find / -path '*/ground_truth/answer.json' -print -quit 2>/dev/null | grep -q ."],
        capture_output=True, text=True, timeout=120)
    assert image_probe.returncode == 0, image_probe.stdout + image_probe.stderr
    service_probe = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HG_ENV_TASK_ID=e85d92a_1",
         "-v", f"{probe}:/probe.py:ro", service, "sh", "-c",
         "appworld-server & server=$!; python /probe.py; rc=$?; kill $server 2>/dev/null || true; wait $server 2>/dev/null || true; exit $rc"],
        capture_output=True, text=True, timeout=180)
    assert service_probe.returncode == 0, service_probe.stdout + service_probe.stderr


def test_check_separates_measured_zero_from_unmeasured(tmp_path):
    check = ROOT / "data/datasets/appworld/verify/check.py"
    missing = tmp_path / "missing.jsonl"
    reward = tmp_path / "reward"
    evidence = tmp_path / "evidence"
    proc = subprocess.run([sys.executable, str(check), "--task-id", CONTROL_TASK, "--log", str(missing), "--reward-file", str(reward), "--evidence-file", str(evidence)], cwd=tmp_path)
    assert proc.returncode == 3 and not reward.exists()
    assert json.loads(evidence.read_text())["measured"] is False

    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    appworld_env = dict(__import__("os").environ, APPWORLD_ROOT=str(dataset.UNPACKED))
    proc = subprocess.run(["/mnt/20t/xubuqiang/venvs/appworld/bin/python", str(check), "--task-id", CONTROL_TASK, "--log", str(empty), "--reward-file", str(reward), "--evidence-file", str(evidence)], cwd=tmp_path, env=appworld_env)
    assert proc.returncode == 0
    assert reward.read_text().strip() == "0.0"
    assert json.loads(evidence.read_text())["measured"] is True


@pytest.mark.parametrize("arm,expected", [("--oracle", "1.0"), ("--noop", "0.0")])
def test_by_hand_control_arms(arm, expected):
    proc = subprocess.run([sys.executable, "data/datasets/appworld/control.py", arm, "--task-id", CONTROL_TASK], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"reward  {expected}" in proc.stdout
    assert '"measured": true' in proc.stdout
    assert '"num_tests": 2' in proc.stdout


@pytest.mark.parametrize("arm,expected", [("--oracle", "1.0"), ("--noop", "0.0")])
def test_platform_control_arms(arm, expected):
    proc = subprocess.run([sys.executable, "data/datasets/appworld/control.py", arm, "--task-id", CONTROL_TASK, "--platform", "--no-sandbox"], cwd=ROOT, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"score    {expected}" in proc.stdout
    assert "measured': True" in proc.stdout
    assert "'num_tests': 2" in proc.stdout
