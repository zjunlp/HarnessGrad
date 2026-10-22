"""Three promises of the `jer` interface, pinned so they cannot regress silently.

`jer look` must show the room the agent is *in*, `replay` must not let a crash look like a
partial score, and the CLI must not advertise a command that cannot work.

* **Defect 1, `jer look`.** `cmd_look` rebuilt the game from the log and then printed
  `last["observation"]` -- the *previous action's feedback*, truncated to 400 characters by
  `replay`. So after `take lamp` (feedback "Taken.") `jer look` printed "Taken." instead of the
  room, while the module docstring and `_goal` advertised it as "see where you are". The tests
  below assert look is the room, that it equals the room before intervening actions, that it
  reports the move count of the *log* (the game counts `look` itself as a move), and that it
  writes nothing: the log bytes are unchanged and the check's replay still sees exactly the
  logged actions.
* **Defect 2, a crashed replay.** `replay` swallowed every exception with `break`, so
  `verify/check.py` graded `steps[-1]["score"]` and reported `measured: true`. A raise is not a
  parser rejection -- measured here and in the CLI: an empty line, an unknown word, a
  300-character command and raw control bytes all come back as ordinary text -- so a raise is
  the interpreter/environment dying. The tests below assert `replay` records it (type, message,
  step, action) and that the check then writes no reward, says `measured: false`, and exits 3,
  while an action the parser simply does not understand keeps the replay going.
* **Defect 3, `jer valid`.** The agent runtime is a code-only projection whose `defines.py` has
  `BINDINGS_DICT = {}` (`data/datasets/jericho/runtime.py`), which is what hides the walkthrough.
  `FrotzEnv.load` builds its `TemplateActionGenerator` only when those bindings exist, so
  `get_valid_actions()` returns `[]` there. Restoring the table is not enough either: the
  generator's `_identify_interactive_objects` needs spaCy's `en_core_web_sm`, which is not
  installed and whose fallback is a **network download** (`jericho/util.py`), and this dataset's
  agent runtime runs with `network: none`. The command was removed rather than left advertised;
  these tests pin its removal.

The `live` fixture materializes the shortest eval-side game and drives the real CLI, because
the defect was in the interaction between the CLI and a real game; the replay/check tests use a
synthetic environment, because that is what a crash needs and no ROM should have to crash.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load_control(dataset: str):
    """The dataset's `control.py`, loaded by path under a unique name (see test_jericho_dataset)."""
    path = ROOT / "data" / "datasets" / dataset / "control.py"
    spec = importlib.util.spec_from_file_location(f"{dataset}_control_interface_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _jer_env():
    """`lib/jer_env.py` by path: the module the CLI and the check both import."""
    path = ROOT / "data" / "datasets" / "jericho" / "lib" / "jer_env.py"
    spec = importlib.util.spec_from_file_location("jer_env_interface_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_module():
    path = ROOT / "data" / "datasets" / "jericho" / "verify" / "check.py"
    spec = importlib.util.spec_from_file_location("jericho_check_interface_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def jericho():
    import data.datasets.jericho as module
    if not module._RECORDS:
        pytest.skip(f"no Jericho games under {module.games_dir()}; run "
                    f"`python3 tools/datasets.py --fetch --dataset jericho`")
    return module


@pytest.fixture(scope="module")
def live_record(jericho):
    """The shortest game a run would see: the whole pool, since there is no division."""
    return min(jericho._RECORDS, key=lambda r: len(r["walkthrough"]))


@pytest.fixture
def live(jericho, live_record, tmp_path):
    """A fresh materialized workdir per test: the CLI writes the log in it."""
    _load_control("jericho").materialize(live_record, tmp_path)
    return tmp_path, live_record


# ------------------------------------------------------------------ helpers ---


def _jer(workdir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "jer", *args], cwd=workdir,
                          capture_output=True, text=True)


def _observation(stdout: str) -> str:
    """`jer`'s stdout without the `[jer] move ...` trailer written after a blank line."""
    text = stdout.rstrip("\n")
    assert "\n\n[jer] " in text, f"no trailer in {stdout!r}"
    return text.rsplit("\n\n[jer] ", 1)[0]


def _look_moves(stdout: str) -> int:
    match = re.search(r"^\[jer\] move (\d+);", stdout, re.MULTILINE)
    assert match, f"no move counter in {stdout!r}"
    return int(match.group(1))


def _score_moves(stdout: str) -> int:
    match = re.search(r"; moves (\d+);", stdout)
    assert match, f"no move counter in {stdout!r}"
    return int(match.group(1))


class _Env:
    """The slice of `FrotzEnv` a replay uses; `crash_at` makes one step raise."""

    def __init__(self, *, crash_at: int | None = None, error: Exception | None = None):
        self.crash_at, self.error, self.calls = crash_at, error, 0

    def get_max_score(self) -> int:
        return 100

    def step(self, action: str):
        if self.calls == self.crash_at:
            raise self.error
        self.calls += 1
        return f"ok: {action}", 0, False, {"score": 0, "moves": self.calls}


# --------------------------------------------------------------------- look ---


def test_look_shows_the_current_room_not_the_previous_feedback(live):
    """The defect: after any action, `look` printed that action's feedback.

    Measured on the shortest eval-side game: the opening `look` prints the room, `inventory`
    answers "You are carrying nothing.", and the old `look` then printed that sentence. Looking
    again must print the room it printed before, which is also what the game answers.
    """
    workdir, _record = live
    opening = _jer(workdir, "look")
    assert opening.returncode == 0, opening.stderr
    room = _observation(opening.stdout)
    assert room.strip()

    action = _jer(workdir, "do", "inventory")
    assert action.returncode == 0, action.stderr
    feedback = _observation(action.stdout)
    assert feedback.strip() and feedback.strip() != room.strip(), (
        "the fixture needs an action whose feedback is distinguishable from the room")

    looked = _jer(workdir, "look")
    assert looked.returncode == 0, looked.stderr
    after = _observation(looked.stdout)
    assert after.strip() == room.strip(), (
        "`jer look` must print the room it is in, not the previous action's feedback:\n"
        f"room={room!r}\nfeedback={feedback!r}\nafter={after!r}")
    assert after.strip() != feedback.strip()


def test_look_before_any_action_still_opens_the_game(live):
    """The no-actions-yet path keeps working, and reports no spent move.

    The game's own counter has already moved once when it prints its opening, so the counter
    this command prints can only be the replayed log's. Nothing is in the log, so it is 0 --
    the same number `jer score` prints at this point.
    """
    workdir, _record = live
    log = workdir / "jer_actions.jsonl"
    assert log.read_text().strip() == "", "the fixture starts with no logged action"

    out = _jer(workdir, "look")
    assert out.returncode == 0, out.stderr
    assert _observation(out.stdout).strip(), "the opening room must be printed"
    assert _look_moves(out.stdout) == 0, out.stdout
    assert log.read_text().strip() == "", "`look` must not append to the action log"
    assert _score_moves(_jer(workdir, "score").stdout) == 0


def test_look_reports_the_move_count_of_the_log_not_of_its_own_step(live):
    """`look` is a move to the game, so the counter has to come from the replayed log.

    The ephemeral `step("look")` the command has to make returns `moves == logged + 1`; a
    reader of this trailer would take that to mean the agent spent a move it did not spend (and
    that the check does not replay).
    """
    workdir, _record = live
    assert _jer(workdir, "do", "wait").returncode == 0, "`wait` is a logged move"
    scored = _jer(workdir, "score")
    assert scored.returncode == 0, scored.stderr
    logged = _score_moves(scored.stdout)

    looked = _jer(workdir, "look")
    assert looked.returncode == 0, looked.stderr
    assert _look_moves(looked.stdout) == logged, (
        f"`jer look` reported move {_look_moves(looked.stdout)}, but the log's last step is "
        f"move {logged}: the number must come from the replayed log, not from the step `look` "
        f"itself makes")


def test_look_never_changes_what_is_scored(jericho, live):
    """Hard constraint: looks are invisible to the grade.

    The log is the whole episode -- the check replays it and reads nothing else -- so the two
    assertions are (a) its bytes do not move while `look` is used and (b) the check still sees
    and replays exactly the two logged actions.
    """
    workdir, record = live
    for action in ("inventory", "wait"):
        done = _jer(workdir, "do", action)
        assert done.returncode == 0, done.stderr
    log = workdir / jericho.LOG_FILE
    before = log.read_bytes()
    for _ in range(3):
        assert _jer(workdir, "look").returncode == 0
    assert log.read_bytes() == before, "`look` appended to the graded log"

    reward, evidence = _run_check(jericho, record, workdir)
    assert reward >= 0.0, evidence
    assert evidence["measured"] is True
    assert evidence["actions_in_log"] == 2
    assert evidence["actions_replayed"] == 2


# ------------------------------------------------------------------- replay ---


def test_a_crashed_replay_records_the_exception_instead_of_hiding_it():
    """A raise is a measurement failure, so it has to survive the return value."""
    lib = _jer_env()
    crash = RuntimeError("the interpreter wrote nothing")
    result = lib.replay(_Env(crash_at=1, error=crash), ["look", "take lamp", "north"])

    assert [step["action"] for step in result["steps"]] == ["look"]
    failure = result["failure"]
    assert failure is not None, "a crash must be reported, not dropped"
    assert failure["step"] == 1 and failure["action"] == "take lamp"
    assert failure["type"] == "RuntimeError"
    assert "wrote nothing" in failure["message"]
    assert failure["traceback"].strip()


def test_a_finished_replay_records_no_failure():
    """`done` and log exhaustion are the measured stops, and neither is a failure."""
    lib = _jer_env()
    result = lib.replay(_Env(), ["look"])
    assert result["failure"] is None
    assert [step["action"] for step in result["steps"]] == ["look"]


def test_the_check_refuses_to_score_a_crashed_replay(monkeypatch, tmp_path):
    """`measured: false`, no reward file, exit 3 -- not `steps[-1]["score"]`.

    Only `load_game` is stubbed (a crash is exactly what a synthetic environment is for); the
    real `replay` and the real `_score` decide.
    """
    lib = _jer_env()
    args, inputs, reward_file, evidence_file = _synthetic_episode(
        tmp_path, ["look", "take lamp", "north"])
    monkeypatch.setitem(sys.modules, "jer_env", lib)
    monkeypatch.setattr(lib, "load_game", lambda *a, **k: _Env(
        crash_at=1, error=RuntimeError("the interpreter wrote nothing")))

    code = _check_module()._score(args, inputs, {"verified": True})

    assert code == 3, "a crash is not a score"
    assert not reward_file.exists(), "an unmeasured episode must not write a reward"
    evidence = json.loads(evidence_file.read_text())
    assert evidence["measured"] is False
    assert "take lamp" in evidence["error"], evidence["error"]
    assert evidence["failure"]["type"] == "RuntimeError"
    assert "wrote nothing" in evidence["failure"]["message"]
    assert evidence["steps"][-1]["action"] == "look", "the accepted steps stay in evidence"
    assert "reward" not in evidence


def test_a_command_the_parser_rejects_does_not_stop_the_replay(jericho, live):
    """A deliberate rejection is not an exception, and the game goes on.

    Known text from the interpreter: an unknown word comes back as "I don't know the word ...",
    so the record for it is an ordinary step. The check must replay both actions and still
    measure.
    """
    workdir, record = live
    for action in ("florble glorp", "look"):
        done = _jer(workdir, "do", action)
        assert done.returncode == 0, done.stderr
    reward, evidence = _run_check(jericho, record, workdir)
    assert reward >= 0.0, evidence
    assert evidence["measured"] is True
    assert evidence["actions_replayed"] == 2, evidence
    assert evidence["actions_not_replayed"] == 0


# -------------------------------------------------------------------- valid ---


def test_the_cli_no_longer_ships_the_action_lister():
    """The command cannot work in the agent runtime, so the interface stops claiming it does."""
    source = (ROOT / "data" / "datasets" / "jericho" / "cli" / "jer").read_text()
    assert "jer valid" not in source, "the CLI still advertises `jer valid`"
    assert "cmd_valid" not in source, "the dead command is still implemented and dispatched"


def test_valid_is_reported_as_an_unknown_command(jericho, live):
    """A harness that tries it gets a clear refusal rather than a silent empty list."""
    workdir, _record = live
    out = _jer(workdir, "valid")
    assert out.returncode == 2, out.stdout
    assert "unknown command" in out.stderr and "valid" in out.stderr
    assert out.stdout.strip() == "", "nothing should be printed as if it were an answer"


def test_help_lists_only_the_commands_that_work(jericho, live):
    workdir, _record = live
    out = _jer(workdir, "help")
    assert out.returncode == 0, out.stderr
    for command in ("jer look", "jer do", "jer score", "jer inventory", "jer done"):
        assert command in out.stdout, (command, out.stdout)
    assert "jer valid" not in out.stdout


# ------------------------------------------------------------- check harness ---


def _synthetic_episode(tmp_path: Path, actions: list[str]):
    """A workdir shaped like the platform's, for `check._score` without a real game."""
    game = tmp_path / "jer_game.json"
    game.write_text(json.dumps({"task_id": "synthetic", "rom": "synthetic.z5", "seed": 0}))
    rom = tmp_path / "synthetic.z5"
    rom.write_bytes(b"never read: load_game is stubbed")
    log = tmp_path / "jer_actions.jsonl"
    log.write_text("".join(json.dumps({"kind": "action", "step": index, "action": action})
                           + "\n" for index, action in enumerate(actions)))
    args = SimpleNamespace(workdir=tmp_path, game=game, rom="synthetic.z5", log=log,
                           reward_file=tmp_path / "verifier" / "reward.txt",
                           evidence_file=tmp_path / "verifier" / "evidence.json")
    return args, {str(game): game, args.rom: rom}, args.reward_file, args.evidence_file


def _run_check(jericho, record: dict, workdir: Path) -> tuple[float, dict]:
    """Run the check the way the platform does: re-materialize its inputs, then run it.

    The same helper as `test_jericho_dataset.py`, kept local so this file can be run alone.
    """
    verifier = jericho.VERIFY[record["task_id"]]
    for entry in verifier.get("inputs") or []:
        target = workdir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content" in entry:
            target.write_text(entry["content"])
        else:
            shutil.copy2(entry["from"], target)
    proc = subprocess.run([sys.executable, *verifier["argv"][1:]], cwd=workdir,
                          capture_output=True, text=True)
    reward_path = workdir / jericho.REWARD_REL
    reward = float(reward_path.read_text().strip()) if reward_path.is_file() else -1.0
    evidence_path = workdir / jericho.EVIDENCE_REL
    evidence = json.loads(evidence_path.read_text()) if evidence_path.is_file() else {}
    return reward, {"stdout": proc.stdout, "stderr": proc.stderr, **evidence}
