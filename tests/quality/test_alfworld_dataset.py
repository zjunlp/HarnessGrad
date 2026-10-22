"""The ALFWorld adapter's contract: what it returns, and what it refuses to leak.

The judgement lessons live in `docs/dataset-onboarding-skill.md`; this file is the part
a program can decide. The one that carries the most weight is
`test_the_check_re_materializes_its_inputs`: the dataset ships `check.py` to the agent
through `SETUP` so an agent can run it for feedback, and if verification used the
agent's copy the agent would be grading itself. It does not, and this proves it by
planting a rogue check that writes reward 1 and asserting the grade is still 0.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# The check imports `gym_env` from its own directory; in a real run they are copied into
# the workdir side by side. Here they live in `verify/` and `lib/`, so `lib/` is put on
# the path for the one test that imports the check as a module.
sys.path.insert(0, str(ROOT / "data" / "datasets" / "alfworld" / "lib"))

pytest.importorskip("textworld", reason="ALFWorld needs textworld")
pytest.importorskip("alfworld", reason="ALFWorld needs the alfworld package")


@pytest.fixture(scope="module")
def alfworld():
    import data.datasets.alfworld as module
    if not module._RECORDS:
        pytest.skip(f"no extracted ALFWorld data under {module.DATASET_ROOT}")
    return module


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_returns_the_benchmark_task_list(alfworld):
    """134 is ALFWorld's own count for `valid_unseen` (`Evaluating with 134 games`)."""
    tasks, scorable = alfworld.load()
    assert len(tasks) == 134
    ids = [t["task_id"] for t in tasks]
    assert len(set(ids)) == len(ids)
    assert all(t["goal"].strip() for t in tasks)
    assert set(alfworld.VERIFY) == set(ids)
    assert set(alfworld.SETUP) == set(ids)
    assert set(alfworld.ENV) == set(ids)
    assert set(scorable) == set(ids)


def test_the_default_pool_declares_no_division(alfworld):
    """`valid_unseen` is the benchmark's held-out set; there is no study side to declare."""
    tasks, _ = alfworld.load()
    assert alfworld.SPLIT is None and alfworld.SPLIT_SOURCE == "none"
    assert {t["task_id"] for t in tasks}


def test_the_goal_is_the_environment_s_own_word_for_word(alfworld):
    """The goal must be the templated one TextWorld checks, not a human paraphrase.

    ALFWorld evaluates with `goal_desc_human_anns_prob=0.0`, so the game's goal is the
    templated one in the grammar. The human annotation (`turk_annotations`) says
    something similar and different -- "Hold the clock and turn on the lamp." against
    "examine the alarmclock with the desklamp." -- and grading against a paraphrase the
    environment does not check is a task statement that lies.
    """
    record = alfworld._RECORDS[0]
    goal = alfworld._goal(record)
    assert record["goal"].startswith("Your task is to:")
    assert record["goal"] in goal
    assert json.loads(Path(record["game_path"]).read_text())["walkthrough"]  # sanity


def test_the_reference_solution_is_stripped_from_the_agent_s_game(alfworld):
    """`walkthrough` is ALFWorld's own solution to the game; the agent must not get it.

    The other three keys are asserted present on purpose: they are the initial state the
    agent *can* read, because the agent's own CLI has to build the TextWorld environment
    and TextWorld refuses a game file without them. The module docstring ("What the agent
    can read, and what that costs") records that cost; this test pins the shape as it is,
    not as one would wish it.
    """
    for record in alfworld._RECORDS[:5]:
        game = json.loads(record["stripped_game"])
        assert "walkthrough" not in game
        assert game["pddl_domain"] and game["grammar"] and game["pddl_problem"]
        raw = Path(record["game_path"]).read_text()
        assert "walkthrough" in raw, "the source game should have had one to strip"


def test_the_verifier_declares_the_contract_paths(alfworld):
    tid = alfworld._RECORDS[0]["task_id"]
    verifier = alfworld.VERIFY[tid]
    assert verifier["kind"] == "command"
    assert verifier["reward_file"] == alfworld.REWARD_FILE
    assert verifier["reward_file"].startswith("/")
    assert verifier["evidence_file"].startswith("/")
    assert verifier["timeout_s"] > 0
    # A host (`files`) environment materializes `path` inputs into the workdir; `dst` is
    # a container path and is not used here. The lint pins the `dst` rule for datasets
    # that do use one.
    assert all("path" in entry for entry in verifier["inputs"])
    assert any(entry["path"] == "check.py" for entry in verifier["inputs"])
    assert any(entry["path"] == alfworld.GAME_IN_WORKDIR for entry in verifier["inputs"])


def test_the_step_budget_is_declared_once(alfworld):
    """One upstream limit, read by the adapter, the CLI and the check."""
    gym_env = _load_module("alfworld_gym_env_test",
                           ROOT / "data" / "datasets" / "alfworld" / "lib" / "gym_env.py")
    check = _load_module("alfworld_check_budget_test",
                         ROOT / "data" / "datasets" / "alfworld" / "verify" / "check.py")
    assert alfworld.STEP_BUDGET == gym_env.STEP_BUDGET == check.gym_env.STEP_BUDGET == 50
    # The CLI has no budget of its own either: its default comes from `gym_env`.
    cli = (ROOT / "data" / "datasets" / "alfworld" / "cli" / "alf").read_text()
    assert "gym_env.STEP_BUDGET" in cli


def test_the_check_re_materializes_its_inputs(alfworld, tmp_path):
    """Plant a check that always wins; the platform's re-materialization must ignore it.

    This is the whole reason `SETUP` and `VERIFY.inputs` both ship `check.py`: the agent
    gets a copy it can run for feedback, and verification overwrites that copy before it
    runs. Without the overwrite, an agent that writes `reward.txt` itself passes every
    task -- a capture channel the contract would not have.
    """
    from eval.verification.verifier import _verify

    record = alfworld._RECORDS[0]
    tid = record["task_id"]
    workdir = tmp_path / "task"
    workdir.mkdir()
    (workdir / alfworld.GAME_IN_WORKDIR).write_text(record["stripped_game"])
    (workdir / alfworld.LOG_IN_WORKDIR).write_text("")
    # The rogue copies the agent would leave behind:
    (workdir / "check.py").write_text(
        "import pathlib\n"
        "pathlib.Path('verifier').mkdir(exist_ok=True)\n"
        "pathlib.Path('verifier/reward.txt').write_text('1')\n"
        "print('agent wrote a win')\n")
    (workdir / "gym_env.py").write_text("raise SystemExit(1)\n")

    # This control targets generic input re-materialization. Keep the real inputs
    # and scorer-input binding, but isolate it from sealed-runtime admission;
    # test_native_runtime_hp3.py / test_native_runtime_hp4.py /
    # test_native_runtime_hostpin.py cover the sealed route.
    spec = copy.deepcopy(alfworld.VERIFY[tid])
    spec.pop("native_runtime")
    verdict = _verify(workdir, spec, "", task_id=tid,
                      sandbox=False, timeout_s=180, env=alfworld.ENV[tid])
    assert verdict["score"] == 0.0, verdict
    assert verdict["passed"] is False
    assert verdict.get("evidence"), "the verdict must carry the check's evidence"
    assert verdict["evidence"].get("why")
    assert "no actions" in verdict["evidence"]["why"]
    assert verdict["evidence"]["scorer_input"]["verified"] is True
    for name in ("check.py", "gym_env.py"):
        source = next(entry["from"] for entry in spec["inputs"]
                      if entry["path"] == name)
        assert (workdir / name).read_bytes() == Path(source).read_bytes()


def test_the_sandboxed_check_can_still_write_its_verdict(alfworld, tmp_path,
                                                         monkeypatch):
    """The reward channel works with the namespace **on**, and only its own directory is
    writable.

    This is the test for the platform change that made a no-Docker dataset keep the
    evidence channel. The first fix ran the check unsandboxed, which works but weakens
    isolation: an unsandboxed check runs with the harness's workdir as `sys.path[0]`.
    The version here re-opens exactly the verdict directory on top of the read-only task
    tree, so `verifier/reward.txt` can be written while everything the harness produced
    stays frozen.
    """
    from eval.containers import sandbox as sandbox_mod
    from eval.verification.verifier import _verify

    try:
        sandbox_mod._bwrap()
    except sandbox_mod.SandboxUnavailable:
        pytest.skip("bwrap is not available on this machine")

    record = alfworld._RECORDS[0]
    tid = record["task_id"]
    root = tmp_path / "work"
    workdir = root / "run" / "task"
    workdir.mkdir(parents=True)
    (workdir / alfworld.GAME_IN_WORKDIR).write_text(record["stripped_game"])
    (workdir / alfworld.LOG_IN_WORKDIR).write_text("")
    (workdir / "artifact.txt").write_text("the harness's work\n")
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(root))

    # Exercise the real sealed sandbox with its dataset inputs and selected task.
    spec = copy.deepcopy(alfworld.VERIFY[tid])
    verdict = _verify(workdir, spec, "", task_id=tid,
                      sandbox=True, timeout_s=300, env=alfworld.ENV[tid])
    print(json.dumps({"sandboxed_alfworld_verdict": verdict}, sort_keys=True))
    assert verdict["score"] == 0.0, verdict
    assert verdict["native_runtime"]["verified"] is True, verdict
    assert verdict["native_runtime"]["sha256"] == spec["native_runtime"]["sha256"]
    assert verdict.get("evidence"), verdict
    assert verdict["evidence"]["scorer_input"]["verified"] is True
    assert (workdir / alfworld.REWARD_REL).is_file()
    assert (workdir / alfworld.EVIDENCE_REL).is_file()
    # The one guarantee the read-only mount exists for: the check cannot edit the
    # harness's artifacts, even though it can write its verdict beside them.
    assert (workdir / "artifact.txt").read_text() == "the harness's work\n"
