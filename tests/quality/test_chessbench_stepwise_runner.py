"""Live Unix-socket runner tests with fabricated puzzles and isolated actor processes.

No model calls or dataset reads. These establish the wiring, not measured accuracy
on the released ChessBench slice or production pilot authorization.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import chess
import pytest

import data.datasets.chessbench_puzzles as dataset
from eval.run import runner
from eval.run import chess_stepwise
from eval.service.unix_stepwise_relay import StepwiseRelay
from eval.verification import native_runtime
from harnessgrad.records import _curve_point, _save_verdicts
from tools import plot_curve


TASK_ID = "chessbench_puzzles/test/0"
PREAMBLE = '''import argparse, json, os, socket, signal, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--task')
p.add_argument('--workdir')
a = p.parse_args()
root = Path(a.workdir)
task = json.loads(Path(a.task).read_text())
def trace(value):
    with (root / 'trace.jsonl').open('a') as f:
        f.write(json.dumps(value) + '\\n')
def connect():
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(3)
    s.connect(os.environ['HG_STEPWISE_SOCKET'])
    return s, s.makefile('rb')
'''
PLAY = '''
s, f = connect()
observations = []
for move in ('e7e5', 'b8c6'):
    board = json.loads(f.readline())
    assert set(board) == {'contract', 'fen', 'solver_turn', 'status', 'verdict'}
    assert board['status'] == 'awaiting_move'
    assert board['solver_turn'] == len(observations)
    observations.append(board)
    s.sendall(json.dumps({'uci': move}).encode() + b'\\n')
terminal = json.loads(f.readline())
assert terminal['status'] == 'solved'
trace({'observations': observations, 'terminal': terminal,
       'socket': os.environ['HG_STEPWISE_SOCKET']})
# A contradictory offline artifact must not determine the interactive score.
(root / 'answer.txt').write_text('wrong offline answer')
'''


@pytest.fixture(autouse=True)
def forbid_dataset_reads(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("fabricated runner test attempted a dataset read")
    for name in ("_read_rows", "_rows", "_selected_records", "load", "load_selected"):
        monkeypatch.setattr(dataset, name, forbidden)
    monkeypatch.setattr(dataset._SPEC, "ensure", forbidden)


@pytest.fixture
def case(tmp_path, monkeypatch):
    # Test-only admitted artifact: the local interpreter already provides python-chess.
    # Production must pass an actual captured provider envelope.
    actual_session = chess_stepwise.session_for
    actual_validate = chess_stepwise.runtime_identity_mod.validate
    def validate(value):
        if value == {"synthetic": True}:
            return {"provider": {"dataset": "chessbench_puzzles"}}
        return actual_validate(value)
    monkeypatch.setattr(chess_stepwise.runtime_identity_mod, "validate", validate)
    monkeypatch.setattr(chess_stepwise.dataset_runtime, "admit_provider",
                        lambda name, provider: {"python": sys.executable})
    def session(task, verifier, **kwargs):
        kwargs["dataset_runtime_identity"] = kwargs.get("dataset_runtime_identity") or {"synthetic": True}
        return actual_session(task, verifier, **kwargs)
    monkeypatch.setattr(chess_stepwise, "session_for", session)
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({
        "name": "fabricated-stepwise-control", "version": "1", "entrypoint": "actor.py"}))
    gold = {"fen": chess.STARTING_FEN,
            "moves": ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
            "answer": "e7e5 b8c6", "puzzle_id": "private-fabricated-id"}
    task = {"task_id": TASK_ID, "goal": "offline goal", "inputs": [],
            "interaction": dataset.STEPWISE_CONTRACT}
    return repo, task, gold, dataset.verify_for({"task_id": task["task_id"], "gold": gold})


@pytest.fixture(scope="module")
def local_chess_runtime(tmp_path_factory):
    """Seal local Python plus the pinned scorer; no historical installation needed."""
    for tool in ("bwrap", "readelf", "ldd"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for the local sealed-runtime control")
    root = Path(__file__).resolve().parents[2]
    work = tmp_path_factory.mktemp("local-chess-runtime")
    path = work / "manifest.json"
    # The BIRD capture supplies a local Python/stdlib/ELF closure. Attach the
    # reviewed Chess scorer sources and the installed pure-Python chess package.
    proc = subprocess.run(
        [sys.executable, str(root / "tools/build_bird_native_runtime.py"),
         "--dataset", "bird_minidev", "--out", str(work / "capture"),
         "--manifest", str(path), "--store", str(work / "store")],
        cwd=root, env={"PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    manifest = json.loads(path.read_text())
    reviewed = json.loads((root / "data/runtimes/chessbench_puzzles-native.json").read_text())
    manifest["dataset"] = "chessbench_puzzles"
    # The native scorer intentionally retains the reviewed pre-provider QA base.
    # The current platform base has since changed; preserve the exact pinned
    # bytes rather than pointing a pinned digest at the new source.
    pinned_base = (root / 'tests/fixtures/chessbench_native_sources/qa_base.py').read_bytes()
    expected_base = reviewed['files']['/scorer/data/scaffolds/qa/base.py']
    assert hashlib.sha256(pinned_base).hexdigest() == expected_base['sha256']
    assert len(pinned_base) == expected_base['size']
    historical_base = work / 'pinned-qa-base.py'
    historical_base.write_bytes(pinned_base)
    for target, entry in reviewed["files"].items():
        if target.startswith("/scorer/"):
            source = (root / "data/scaffolds/qa/verify/check.py" if target == "/scorer/check.py"
                      else historical_base if target == "/scorer/data/scaffolds/qa/base.py"
                      else root / target.removeprefix("/scorer/"))
            manifest["files"][target] = {**entry, "source": str(source)}
    package = Path(chess.__file__).resolve().parent
    for source in sorted(package.rglob("*.py")):
        raw = source.read_bytes()
        manifest["files"][str(source)] = {
            "source": str(source), "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw), "entry": "local Chess test dependency"}
    manifest["python_path"] = ["/scorer", str(package.parent)]
    raw = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return {"dataset": "chessbench_puzzles", "manifest": str(path),
            "sha256": hashlib.sha256(raw).hexdigest()}


def execute(case, script, **kwargs):
    repo, task, _, verifier = case
    (repo / "actor.py").write_text(PREAMBLE + script)
    return runner.run_one(repo, task, sandbox=True, verifier=verifier,
                          timeout_s=kwargs.pop("timeout_s", 5), **kwargs)


def assert_clean(receipt):
    assert receipt["cleanup"] == {
        "server_closed": True, "thread_joined": True, "socket_removed": True}


def assert_incomplete(result):
    v = result["verdict"]
    assert v["unmeasured"]
    assert v["scorer_isolation_class"] == "in-process"
    assert "score" not in v and "passed" not in v
    receipt = v["evidence"]
    assert receipt["status"] == "incomplete" and receipt["measured"] is False
    assert not {"score", "reward", "correct"} & receipt.keys()
    assert receipt["protocol"] == dataset.STEPWISE_CONTRACT
    assert_clean(receipt)
    return receipt


def test_live_runner_delivers_opponent_updates_and_scores_controller_receipt(case):
    result = execute(case, PLAY)
    assert result["exit_code"] == 0, result["stderr"]
    verdict = result["verdict"]
    assert verdict["score"] == 1.0 and verdict["passed"] is True
    assert verdict["scorer_isolation_class"] == "in-process"
    assert verdict["protocol"] == dataset.STEPWISE_CONTRACT
    receipt = verdict["evidence"]
    assert receipt["status"] == "complete" and receipt["measured"] is True
    assert receipt["production_authorized"] is False
    assert receipt["metric"] == "ChessBench puzzle accuracy"
    assert "not comparable" in receipt["comparability"]
    assert_clean(receipt)
    assert receipt["transport"]["submissions"] == 2
    observations = [json.loads(event["message"]) for event in receipt["transport"]["events"]
                    if event["kind"] == "observation"]
    board = chess.Board(chess.STARTING_FEN)
    board.push_uci("e2e4")
    assert observations[0]["fen"] == board.fen()
    board.push_uci("e7e5")
    board.push_uci("g1f3")
    assert observations[1]["fen"] == board.fen()
    assert all(set(o) == chess_stepwise.PUBLIC_FIELDS for o in observations)
    actor = json.loads(result["trace"])
    assert actor["observations"] == observations[:-1]
    assert not Path(actor["socket"]).parent.exists()
    assert result["answer"] == ""


@pytest.mark.parametrize("private_field", ["reference_line", "_moves"])
def test_actor_private_reads_are_refused_at_filesystem_and_wire(case, private_field):
    script = f'''
assert not (root / 'gold.json').exists()
assert not Path({str(dataset.__file__)!r}).exists()
assert 'moves' not in task and 'gold' not in task
assert 'answer.txt' not in task['goal'].split('This task')[0]
s, f = connect()
initial = json.loads(f.readline())
assert set(initial) == {{'contract', 'fen', 'solver_turn', 'status', 'verdict'}}
# The projected socket directory cannot be replaced or used to export private files.
try:
    Path(os.environ['HG_STEPWISE_SOCKET']).with_name('probe').write_text('x')
except OSError:
    pass
else:
    raise AssertionError('socket directory was writable')
s.sendall(json.dumps({{'read': {private_field!r}}}).encode() + b'\\n')
terminal = json.loads(f.readline())
assert terminal['status'] == 'failed' and terminal['verdict'] == 'invalid_answer'
assert set(terminal) == set(initial)
assert {private_field!r} not in terminal
assert not f.readline()
trace({{'initial': initial, 'terminal': terminal}})
'''
    result = execute(case, script)
    assert result["exit_code"] == 0, result["stderr"]
    assert result["verdict"]["score"] == 0.0
    assert result["verdict"]["scorer_isolation_class"] == "in-process"
    for board in json.loads(result["trace"]).values():
        wire = json.dumps(board)
        assert all(move not in wire for move in case[2]["moves"])
        assert case[2]["puzzle_id"] not in wire


@pytest.mark.parametrize("wire", ['{"uci":1e309}', '{"uci":"e7e5","extra":1e309}'])
def test_overflow_in_invalid_move_scores_zero(case, wire):
    script = f'''
s, f = connect()
initial = json.loads(f.readline())
assert initial['status'] == 'awaiting_move'
s.sendall({wire.encode()!r} + b'\\n')
terminal = json.loads(f.readline())
assert terminal['status'] == 'failed' and terminal['verdict'] == 'invalid_answer'
assert not f.readline()
'''
    result = execute(case, script)
    assert result["exit_code"] == 0, result["stderr"]
    assert result["verdict"]["score"] == 0.0
    events = result["verdict"]["evidence"]["transport"]["events"]
    assert [event["message"] for event in events if event["kind"] == "submission"] == [wire]


def test_unreviewed_public_field_is_never_delivered(case, monkeypatch):
    # A malformed worker response must be rejected before any actor is launched.
    def polluted(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "contract": dataset.STEPWISE_CONTRACT, "fen": "x", "solver_turn": 0,
            "status": "awaiting_move", "verdict": "awaiting_move",
            "private_reference": case[2]["moves"]}))
    monkeypatch.setattr(chess_stepwise, "run", polluted)
    result = execute(case, "raise AssertionError('actor must not start')\n")
    assert result["invalid"]["stage"] == "stepwise admission"
    assert case[2]["puzzle_id"] not in json.dumps(result)
    assert all(move not in json.dumps(result) for move in case[2]["moves"])


@pytest.mark.parametrize("ending", ["s.close(); f.close()", "os.kill(os.getpid(), signal.SIGKILL)"])
def test_cut_short_exchange_is_incomplete_even_with_forged_offline_answer(case, ending):
    result = execute(case, '''
s, f = connect()
assert json.loads(f.readline())['status'] == 'awaiting_move'
s.sendall(b'{"uci":"e7e5"}\\n')
assert json.loads(f.readline())['status'] == 'awaiting_move'
(root / 'answer.txt').write_text('e7e5 b8c6')
''' + ending + "\n")
    receipt = assert_incomplete(result)
    assert receipt["transport"]["submissions"] == 1


def test_deadline_interrupts_partial_message_and_joins_controller(case):
    started = time.monotonic()
    result = execute(case, '''
s, f = connect()
assert json.loads(f.readline())['status'] == 'awaiting_move'
s.sendall(b'{"uci":')
time.sleep(60)
''', timeout_s=0.5)
    assert time.monotonic() - started < 3
    assert result["exit_code"] == -1
    assert_incomplete(result)


def test_oversized_message_is_unmeasured_and_bounded(case):
    result = execute(case, '''
s, f = connect()
f.readline()
s.sendall(json.dumps({'uci': 'x' * 8192}).encode() + b'\\n')
assert not f.readline()
''')
    receipt = assert_incomplete(result)
    assert len(receipt["transport"]["events"]) == 1
    assert receipt["limits"]["max_message_bytes"] == 4096


def test_no_connection_and_launch_failure_cleanup(case, monkeypatch):
    """没连上：一条 incomplete 的 verdict（有 receipt、无分数）。

    启动就失败：**没有 verdict，而是一条 invalid**。两者都"不是分数"，但不是同一件事 ——
    没连上时中继真的跑过，它交得出 receipt（含清理证据）；启动失败时中继根本没起来，
    没有 receipt 可交。`_run_one` 对这种情况返回
    `_invalid(..., stage="harness launch")`，这段代码就是为了不再把启动失败伪装成
    `exit -1`（"被信号杀死"）而写的，所以这里断言的是那条 invalid 的形状，而不是
    一个不存在的中继记录。
    """
    assert_incomplete(execute(case, "pass\n"))

    def fail_launch(*args, **kwargs):
        raise OSError("fabricated launch failure")

    monkeypatch.setattr(runner.sandbox_mod, "run", fail_launch)
    failed = execute(case, "pass\n")
    assert "verdict" not in failed, failed
    assert failed["invalid"]["stage"] == "harness launch", failed["invalid"]
    assert "score" not in failed and failed.get("exit_code") is None


def test_invalid_trusted_reference_is_unmeasured(case):
    repo, task, gold, _ = case
    gold = {**gold, "moves": ["e2e5", "e7e5"]}
    result = execute((repo, task, gold, dataset.verify_for({"task_id": task["task_id"], "gold": gold})), '''
s, f = connect()
assert json.loads(f.readline())['verdict'] == 'invalid_reference'
''')
    assert assert_incomplete(result)["reason"] == "invalid_reference"


def test_existing_admission_and_isolation_refusals_still_apply(case):
    repo, task, _, verifier = case
    (repo / "actor.py").write_text("raise AssertionError('must not launch')\n")
    bare = runner.run_one(repo, task, sandbox=False, verifier=verifier)
    assert bare["invalid"]["stage"] == "stepwise admission"
    assert "sandbox" in bare["invalid"]["detail"]
    policy = runner.run_one(repo, task, sandbox=True, verifier=verifier,
                            env={"kind": "files", "memory_mb": 64})
    assert policy["invalid"]["stage"] == "admission"
    assert policy["invalid"]["receipt"]["reason"] == "backend_observation_unavailable"


def test_incomplete_evaluation_persists_receipt_without_score_or_cache(case, tmp_path):
    repo, task, _, verifier = case
    (repo / "actor.py").write_text(
        PREAMBLE + "s, f = connect()\nf.readline()\nraise RuntimeError('Connection error')\n")
    cache = {}
    res = runner.evaluate(repo, [task], {}, cache=cache, sandbox=True,
                          verifiers={TASK_ID: verifier}, harness_sha="same")
    assert res["per_task"] == {} and cache == {}
    receipt = res["invalid"][TASK_ID]["receipt"]
    assert receipt["status"] == "incomplete" and receipt["measured"] is False
    _save_verdicts(tmp_path, 0, res, [TASK_ID])
    saved = json.loads((tmp_path / "verdicts/round-0" / f"{TASK_ID}.json").read_text())
    assert saved["score"] is None
    assert saved["invalid"]["receipt"] == receipt


@pytest.mark.parametrize("trials", [1, 2])
@pytest.mark.parametrize("offline_available", [False, True])
def test_interactive_cache_uses_controller_without_offline_native_runtime(
        case, monkeypatch, trials, offline_available):
    repo, task, _, verifier = case
    (repo / "actor.py").write_text(PREAMBLE + PLAY)
    admissions, executions = [], []

    @contextmanager
    def offline_admit(binding):
        admissions.append(binding)
        if not offline_available:
            raise native_runtime.NativeRuntimeUnavailable("authored unavailable offline runtime")
        yield SimpleNamespace(receipt={"verified": True, "dataset": binding["dataset"],
                                       "sha256": binding["sha256"]})

    original_run = runner.run_one

    def run(*args, **kwargs):
        executions.append(True)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(native_runtime, "admit", offline_admit)
    monkeypatch.setattr(runner, "run_one", run)
    cache = {}

    original_native = verifier['native_runtime']
    unused_manifest = repo.parent / 'unavailable-offline-runtime.json'
    unused_manifest.write_text('this declaration is never an executed native runtime')
    # Static checkout files may be read by the platform integrity snapshot.
    # This external poison file isolates dereferencing the unused declaration.
    original_open = Path.open
    def no_unused_manifest_read(path, *args, **kwargs):
        if path.absolute() == unused_manifest:
            pytest.fail('stepwise scoring opened the unused native manifest')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', no_unused_manifest_read)
    def no_unused_native_route(*args, **kwargs):
        pytest.fail('stepwise scoring selected the offline native execution route')
    monkeypatch.setattr(native_runtime, 'validate_route', no_unused_native_route)

    def measure():
        return runner.evaluate(repo, [task], {}, cache=cache, sandbox=True, trials=trials,
                               verifiers={TASK_ID: verifier}, harness_sha="same")

    first = measure()
    assert first["per_task"] == {TASK_ID: 1.0} and not first["invalid"], first
    assert not first["cached_tasks"] and len(executions) == trials
    assert first["scorer_isolation_class"] == "in-process"
    assert first["verdicts"][TASK_ID]["protocol"] == chess_stepwise.STEPWISE
    assert "native_runtime" not in first["verdicts"][TASK_ID]
    # INTERFACE's (harness, task, trials) tuple is necessary, not a promise to
    # ignore changed measurement declarations. verification_context commits the
    # complete verifier, while native admission identities describe execution.
    original_keys = set(cache)
    original_context = first['verification_context']
    reused = measure()
    assert reused['cached_tasks'] == [TASK_ID] and reused['per_task'] == {TASK_ID: 1.0}
    assert len(executions) == trials and admissions == [] and set(cache) == original_keys
    assert reused['verification_context'] == original_context

    verifier['native_runtime'] = {'dataset': 'chessbench_puzzles',
                                 'manifest': str(unused_manifest), 'sha256': '0' * 64}
    remeasured = measure()
    assert not remeasured['cached_tasks'] and remeasured['per_task'] == {TASK_ID: 1.0}
    assert not remeasured['invalid']
    assert remeasured['verification_context'] != original_context
    assert len(executions) == 2 * trials and admissions == [] and len(cache) == 2
    assert original_keys < set(cache)  # A declaration change does not overwrite old evidence.
    assert remeasured['scorer_isolation_class'] == 'in-process'

    changed_reused = measure()
    assert changed_reused['cached_tasks'] == [TASK_ID]
    assert changed_reused['verification_context'] == remeasured['verification_context']
    assert len(executions) == 2 * trials and admissions == []

    verifier['native_runtime'] = original_native
    restored = measure()
    assert restored['cached_tasks'] == [TASK_ID]
    assert restored['verification_context'] == original_context
    assert len(executions) == 2 * trials and admissions == [] and len(cache) == 2

    # Neither stricter cache invalidation nor a cache hit can manufacture an
    # executed-native identity for the in-process controller (including trials).
    for result in (first, reused, remeasured, changed_reused, restored):
        verdicts = result.get('trial_verdicts', {}).get(TASK_ID) or [result['verdicts'][TASK_ID]]
        assert result['verdicts'][TASK_ID]['protocol'] == chess_stepwise.STEPWISE
        assert 'native_runtime' not in result['verdicts'][TASK_ID]
        assert all(v['protocol'] == chess_stepwise.STEPWISE and 'native_runtime' not in v
                   for v in verdicts)
        if trials > 1:
            assert len(verdicts) == trials
    assert all(':native:' not in key for key in cache)


@pytest.mark.parametrize("failure", ["sandbox", "backend", "gold"])
def test_interactive_cache_still_requires_session_admission(case, monkeypatch, failure):
    repo, task, _, verifier = case
    (repo / "actor.py").write_text(PREAMBLE + PLAY)

    def no_native(*args):
        pytest.fail("interactive scoring reached offline native admission")

    monkeypatch.setattr(native_runtime, "admit", no_native)
    cache = {}
    first = runner.evaluate(repo, [task], {}, cache=cache, sandbox=True,
                            verifiers={TASK_ID: verifier}, harness_sha="same")
    assert first["per_task"] == {TASK_ID: 1.0}

    class NoRead(dict):
        def get(self, *args):
            pytest.fail("invalid session read the measurement cache")

    def no_actor(*args, **kwargs):
        pytest.fail("invalid session launched an actor")

    monkeypatch.setattr(runner, "run_one", no_actor)
    if failure == "gold":
        verifier["inputs"] = []
    refused = runner.evaluate(repo, [task], {}, cache=NoRead(cache),
                              sandbox=failure != "sandbox",
                              envs={TASK_ID: {"kind": "exec" if failure == "backend" else "files"}},
                              verifiers={TASK_ID: verifier}, harness_sha="same")
    assert not refused["per_task"] and not refused["cached_tasks"]
    assert refused["invalid"][TASK_ID]["stage"] == (
        "admission" if failure == "backend" else "stepwise admission")


def test_offline_chess_cannot_reuse_interactive_cache_or_skip_native_admission(case, monkeypatch):
    repo, task, _, verifier = case
    (repo / "actor.py").write_text(PREAMBLE + PLAY)
    cache = {}
    first = runner.evaluate(repo, [task], {}, cache=cache, sandbox=True,
                            verifiers={TASK_ID: verifier}, harness_sha="same")
    assert first["per_task"] == {TASK_ID: 1.0}
    cached = dict(cache)
    admissions = []

    @contextmanager
    def unavailable(binding):
        admissions.append(binding)
        raise native_runtime.NativeRuntimeUnavailable("authored unavailable offline runtime")
        yield  # contextmanager protocol

    def no_actor(*args, **kwargs):
        pytest.fail("unavailable offline scorer launched an actor")

    monkeypatch.setattr(native_runtime, "admit", unavailable)
    monkeypatch.setattr(runner, "run_one", no_actor)
    offline = {k: v for k, v in task.items() if k != "interaction"}
    refused = runner.evaluate(repo, [offline], {}, cache=cache, sandbox=True,
                              verifiers={TASK_ID: verifier}, harness_sha="same")
    assert not refused["per_task"] and not refused["cached_tasks"]
    assert refused["invalid"][TASK_ID]["stage"] == "native runtime preparation"
    assert admissions == [verifier["native_runtime"]] and cache == cached


def test_offline_protocol_reward_and_cache_remain_distinct_from_interactive(
        case, tmp_path, local_chess_runtime):
    repo, task, gold, verifier = case
    verifier["native_runtime"] = local_chess_runtime
    # One harness, task ID and SHA, with both protocols exercised against one cache.
    (repo / "actor.py").write_text(PREAMBLE + '''
if 'interaction' not in task:
    assert 'HG_STEPWISE_SOCKET' not in os.environ
    (root / 'answer.txt').write_text('e7e5 b8c6')
    trace({'offline': True})
else:
''' + "\n".join("    " + line for line in PLAY.splitlines()) + "\n")
    offline = {k: v for k, v in task.items() if k != "interaction"}
    cache = {}
    def measure(t):
        return runner.evaluate(repo, [t], {}, cache=cache, sandbox=True,
                               verifiers={TASK_ID: verifier}, harness_sha="same")
    batch = measure(offline)
    assert batch["per_task"] == {TASK_ID: 1.0}, json.dumps(batch.get("invalid"), indent=2)
    native = batch["verdicts"][TASK_ID]["native_runtime"]
    assert native["verified"] and native["sha256"] == local_chess_runtime["sha256"]
    assert batch["verdicts"][TASK_ID]["protocol"] == chess_stepwise.OFFLINE
    official = batch["verdicts"][TASK_ID]["evidence"]["official"]
    assert official["protocol"] == chess_stepwise.OFFLINE
    assert official["strict"]["reward"] == dataset.score(gold["answer"], gold)["reward"]
    live = measure(task)
    assert live["cached_tasks"] == [] and live["per_task"] == {TASK_ID: 1.0}
    assert live["verdicts"][TASK_ID]["protocol"] == dataset.STEPWISE_CONTRACT
    assert measure(task)["cached_tasks"] == [TASK_ID]
    assert measure(offline)["evaluation_protocols"] == {TASK_ID: chess_stepwise.OFFLINE}
    _save_verdicts(tmp_path, 0, live, [TASK_ID])
    saved = json.loads((tmp_path / "verdicts/round-0" / f"{TASK_ID}.json").read_text())
    assert saved["protocol"] == saved["evidence"]["protocol"] == dataset.STEPWISE_CONTRACT
    assert live["scorer_isolation_class"] == saved["scorer_isolation_class"] == "in-process"
    refused = runner.evaluate(repo, [task], {}, cache=cache, sandbox=False,
                              verifiers={TASK_ID: verifier}, harness_sha="same")
    assert refused["cached_tasks"] == [] and refused["per_task"] == {}
    assert refused["invalid"][TASK_ID]["stage"] == "stepwise admission"


def point(protocol):
    return _curve_point(
        run_id="fabricated", round_index=0, identity={},
        sampling={"dataset": "chessbench_puzzles", "policy": "all", "task_ids": [TASK_ID]},
        scores=[1.0], task_ids=[TASK_ID], cost={}, cumulative={"evaluation_trials": 1},
        evaluation_protocols={TASK_ID: protocol},
        # These synthetic points model the two known executor routes. Missing
        # observations must now refuse before the protocol comparison is reached.
        per_task_scorer_isolation={TASK_ID: (
            "in-process" if protocol == dataset.STEPWISE_CONTRACT else "actor-workdir")})


def test_curve_records_protocol_and_refuses_cross_protocol_comparison(tmp_path, monkeypatch, capsys):
    paths = []
    for label, protocol in (("offline", chess_stepwise.OFFLINE), ("live", dataset.STEPWISE_CONTRACT)):
        p = point(protocol)
        assert p["evaluation_protocols"] == {TASK_ID: protocol}
        assert "not comparable" in p["protocol_comparability"]
        path = tmp_path / label
        path.mkdir()
        (path / "curve.jsonl").write_text(json.dumps(p) + "\n")
        paths.append(str(path))
    monkeypatch.setattr(sys, "argv", ["plot_curve", *paths])
    assert plot_curve.main() == 1
    assert "not comparable" in capsys.readouterr().err
    # Changes halfway through a single curve cannot evade the comparison check.
    mixed = Path(paths[0]) / "curve.jsonl"
    with mixed.open("a") as f:
        f.write(json.dumps(point(dataset.STEPWISE_CONTRACT)) + "\n")
    monkeypatch.setattr(sys, "argv", ["plot_curve", str(mixed)])
    assert plot_curve.main() == 1
    assert "mixed evaluation protocols" in capsys.readouterr().err


def test_mixed_protocol_aggregate_refused_before_execution(case):
    repo, task, _, _ = case
    offline = {"task_id": "chessbench_puzzles/test/1", "goal": "offline", "inputs": []}
    with pytest.raises(ValueError, match="cannot aggregate"):
        runner.evaluate(repo, [task, offline], {})


@pytest.fixture
def short_relay_root():
    # Keep Unix IPC independent of the potentially deep per-file pytest root.
    with tempfile.TemporaryDirectory(prefix="hg-stepwise-", dir="/tmp") as directory:
        yield Path(directory)


def test_reconnect_cannot_reset_session_allowance(short_relay_root):
    relay = StepwiseRelay(short_relay_root, dataset.StepwisePuzzle({
        "fen": chess.STARTING_FEN, "moves": ["e2e4", "e7e5"]}),
        contract=dataset.STEPWISE_CONTRACT, public_fields=chess_stepwise.PUBLIC_FIELDS,
        timeout_s=1).start()
    try:
        with socket.socket(socket.AF_UNIX) as first:
            first.settimeout(1)
            first.connect(str(relay.path))
            with first.makefile("rb") as f:
                assert json.loads(f.readline())["status"] == "awaiting_move"
                first.sendall(b'{"uci":"e7e5"}\n')
                assert json.loads(f.readline())["status"] == "solved"
        with socket.socket(socket.AF_UNIX) as second:
            second.settimeout(1)
            second.connect(str(relay.path))
            assert second.recv(1) == b""
    finally:
        relay.stop()
    receipt = relay.receipt()
    assert receipt["connections"] == 1 and receipt["rejections"] == ["connection_budget"]
    assert_clean(receipt)


def test_disconnect_after_initial_board_cannot_reconnect_or_score(case):
    # A short-lived shell client consumes the connection even without an answer.
    # Retrying from a new process must not reopen or reset the trusted session.
    result = execute(case, '''
s, f = connect()
initial = json.loads(f.readline())
assert initial['status'] == 'awaiting_move' and initial['solver_turn'] == 0
f.close()
s.close()
s, f = connect()
assert f.readline() == b''
''')
    assert result["exit_code"] == 0, result["stderr"]
    receipt = assert_incomplete(result)
    assert receipt["connections"] == 1
    assert receipt["rejected_connections"] == 1
    assert receipt["rejections"] == ["connection_budget"]
    transport = receipt["transport"]
    assert transport["reason"] == "answer_receive_failed"
    assert transport["submissions"] == 0
    assert [event["kind"] for event in transport["events"]] == ["observation"]


def test_trickled_partial_frame_cannot_extend_absolute_socket_deadline(short_relay_root):
    relay = StepwiseRelay(short_relay_root, dataset.StepwisePuzzle({
        "fen": chess.STARTING_FEN, "moves": ["e2e4", "e7e5"]}),
        contract=dataset.STEPWISE_CONTRACT, public_fields=chess_stepwise.PUBLIC_FIELDS,
        timeout_s=0.2).start()
    stopped = threading.Event()
    writer = None
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(1)
            connection.connect(str(relay.path))
            with connection.makefile("rb") as f:
                assert json.loads(f.readline())["status"] == "awaiting_move"

                def trickle():
                    while not stopped.wait(0.02):
                        try:
                            connection.sendall(b" ")
                        except OSError:
                            return

                writer = threading.Thread(target=trickle)
                writer.start()
                # A per-read inactivity timeout would hang until our client timeout.
                assert f.readline() == b""
    finally:
        stopped.set()
        if writer:
            writer.join()
        relay.stop()
    receipt = relay.receipt()
    assert receipt["status"] == "incomplete"
    assert receipt["transport"]["reason"] == "answer_receive_failed"
    assert_clean(receipt)
