"""Train-side `--resume`: continue the loop, never re-measure a round that exists.

The seam is easy to get wrong in one specific way: it rebuilds everything from the record
instead of from a measurement, so if the re-staged workspace does not reproduce the same
content address, the resumed loop would build on a tree the curve's scores do not belong
to. That invariant is asserted here, next to the refusals.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from driver import ResumeNothingToDo, ResumeRefused, _resume_train_state
from harnessgrad import verification_context


def _ctx():
    return verification_context.capture([{"task_id": "t1"}], {"t1": "synthetic"})


def _state(tmp_path: Path, name: str, body: str) -> Path:
    """A saved harness state, exactly as `records._save_round_state` writes one: no `.git`."""
    import shutil

    from eval.ckpt.git_state import stage_workspace

    source = tmp_path / f"src-{name}"
    source.mkdir()
    (source / "agent.py").write_text(body)
    state = tmp_path / name
    stage_workspace(source, state)          # writes `.gitignore`, `.state/`, a repo
    shutil.rmtree(state / ".git")           # the record keeps the tree, not the history
    return state


def _address(tmp_path: Path, name: str) -> str:
    """The content address of a saved state: restage it and read the tree hash."""
    import shutil

    from eval.ckpt.git_state import commit_state, stage_workspace

    probe = tmp_path / f"probe-{name}"
    stage_workspace(tmp_path / name, probe)
    try:
        return commit_state(probe, name)
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def _run(tmp_path: Path, *, rounds: tuple[int, ...] = (0, 1),
         with_state: bool = True, with_base_state: bool = True,
         split_sha: str = "split-1", platform: str | None = "platform-1",
         last_body: str = "print('round 1')\n") -> Path:
    """A run directory whose curve names **real** content addresses.

    They used to be made-up strings (`"sha-a"`, `"sha-0"`), which is how the invariant this
    file is about went unchecked: a fabricated address cannot be reproduced by re-staging, so
    the fixture could not tell a correct resume from a broken one. Every point now names the
    address of a state that really exists, and the last round's state is on disk the way
    `records._save_round_state` writes it.
    """
    run_dir = tmp_path / "runs" / "rsi10-test-train"
    run_dir.mkdir(parents=True)
    bodies = {r: f"print('round {r}')\n" for r in rounds}
    bodies[rounds[-1]] = last_body
    addresses = {}
    for r in rounds:
        _state(tmp_path, f"saved-{r}", bodies[r])
        addresses[r] = _address(tmp_path, f"saved-{r}")
        if with_state and (r == rounds[-1] or (r == 0 and with_base_state)):
            import shutil
            dest = run_dir / f"state_after_round{r}"
            shutil.copytree(tmp_path / f"saved-{r}", dest)
    points = [{"round": r, "score": 0.5,
               "identity": {"harness_sha": addresses[r]},
               "sampling": {"trials": 1, "task_ids": ["t1"]},
               "per_task": {"t1": 0.5}, "verification_context": _ctx()} for r in rounds]
    (run_dir / "curve.jsonl").write_text(
        "\n".join(json.dumps(p) for p in points) + "\n")
    meta = {"split_sha": split_sha, "verification_context": _ctx()}
    if platform:
        meta["platform_content_sha"] = platform
    (run_dir / "run_meta.json").write_text(json.dumps(meta))
    traces = run_dir / "round0_traces"
    traces.mkdir()
    (traces / "t1.jsonl").write_text(json.dumps({"step": 0}) + "\n")
    return run_dir


def _args(rounds: int = 3) -> SimpleNamespace:
    return SimpleNamespace(rounds=rounds, verification_context=_ctx())


def test_refuses_without_a_curve(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "empty"
    run_dir.mkdir(parents=True)
    with pytest.raises(ResumeRefused) as exc:
        _resume_train_state(_args(), run_dir, None, tmp_path / "work")
    assert "nothing to continue" in str(exc.value)


def test_refuses_when_the_split_moved(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, split_sha="split-OLD")
    class Split:
        pass
    import driver
    monkeypatched = driver.datasets.split_sha
    try:
        driver.datasets.split_sha = lambda _split: "split-NEW"
        with pytest.raises(ResumeRefused) as exc:
            _resume_train_state(_args(), run_dir, Split(), tmp_path / "work")
        assert "task set" in str(exc.value)
    finally:
        driver.datasets.split_sha = monkeypatched


def test_allows_a_reviewed_cross_platform_resume_and_records_it(tmp_path: Path) -> None:
    """The guard's default is "no"; a sentence from the operator is the exception, and it is
    kept beside the curve with both hashes -- a resume that crosses a platform change must be
    auditable later, not just permitted once."""
    run_dir = _run(tmp_path, platform="platform-OLD")
    import driver
    original = driver._platform_content_sha
    try:
        driver._platform_content_sha = lambda: "platform-NEW"
        args = _args(rounds=3)
        args.resume_across_platforms = "reviewed: only a sibling method's stop field moved"
        state = _resume_train_state(args, run_dir, None, tmp_path / "work")
        assert state is not None
    finally:
        driver._platform_content_sha = original
    seams = sorted((run_dir / "platform-seams").glob("*-approved.json"))
    assert len(seams) == 1
    record = json.loads(seams[0].read_text())
    assert record["platform_content_sha"] == {"before": "platform-OLD", "after": "platform-NEW"}
    assert "only a sibling method" in record["reason_reviewed"]


def test_an_empty_review_note_is_still_a_refusal(tmp_path: Path) -> None:
    """`--resume-across-platforms ""` is not a review: the flag carries a claim, and a blank
    one would let the seam through with nothing recorded to check."""
    run_dir = _run(tmp_path, platform="platform-OLD")
    import driver
    original = driver._platform_content_sha
    try:
        driver._platform_content_sha = lambda: "platform-NEW"
        args = _args(rounds=3)
        args.resume_across_platforms = "   "
        with pytest.raises(ResumeRefused) as exc:
            _resume_train_state(args, run_dir, None, tmp_path / "work")
        assert "content changed" in str(exc.value)
    finally:
        driver._platform_content_sha = original
    assert not (run_dir / "platform-seams").exists()


def test_refuses_when_the_platform_changed(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, platform="platform-OLD")
    import driver
    original = driver._platform_content_sha
    try:
        driver._platform_content_sha = lambda: "platform-NEW"
        with pytest.raises(ResumeRefused) as exc:
            _resume_train_state(_args(), run_dir, None, tmp_path / "work")
        assert "content changed" in str(exc.value)
    finally:
        driver._platform_content_sha = original


def test_refuses_without_the_last_state(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, with_state=False, platform=None)
    with pytest.raises(ResumeRefused) as exc:
        _resume_train_state(_args(), run_dir, None, tmp_path / "work")
    assert "state_after_round" in str(exc.value)


def test_nothing_to_do_when_the_budget_is_already_measured(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, rounds=(0, 1, 2), platform=None)
    with pytest.raises(ResumeNothingToDo) as exc:
        _resume_train_state(_args(rounds=2), run_dir, None, tmp_path / "work")
    assert "already measured" in str(exc.value)


def test_the_restaged_tree_keeps_the_recorded_content_address(tmp_path: Path) -> None:
    """Re-staging the saved state must reproduce the address the curve already names."""
    from eval.ckpt.git_state import commit_state, stage_workspace

    run_dir = tmp_path / "runs" / "rsi10-test-train"
    run_dir.mkdir(parents=True)
    # Build a real harness state, so its content address is the thing under test.
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('state')\n")
    state = run_dir / "state_after_round1"
    stage_workspace(source, state)
    sha = commit_state(state, "round 1")
    (run_dir / "curve.jsonl").write_text("\n".join(
        json.dumps({"round": r, "score": 0.5, "identity": {"harness_sha": sha},
                    "sampling": {"trials": 1, "task_ids": ["t1"]}, "per_task": {"t1": 0.5},
                    "verification_context": _ctx()})
        for r in (0, 1)) + "\n")
    (run_dir / "run_meta.json").write_text(json.dumps({"split_sha": None, "verification_context": _ctx()}))
    traces = run_dir / "round0_traces"
    traces.mkdir()
    (traces / "t1.jsonl").write_text("{}\n")

    got = _resume_train_state(_args(), run_dir, None, tmp_path / "work")
    assert got["last"] == 1
    assert got["recorded_sha"] == sha
    assert got["staged_sha"] == sha, (
        "re-staging the saved state must reproduce the content address the curve names")
    assert (tmp_path / "work" / "agent.py").read_text() == "print('state')\n"


def test_a_resume_restores_the_base_tree_a_shared_h0_left_behind(tmp_path: Path) -> None:
    """**修 D5**：`--resume` 曾在新仓库里拿另一仓库的 tree 地址去 `cat-file`，直接崩溃。

    实测（2026-10-18T12:50Z 与 12:52Z，A 线两个 run）：`ckpt/git_state.py` 的 `_git` 抛
    `CalledProcessError`，退出码 1。原因不是 `--resume` 本身，而是 `mode_a` 每轮结束都要
    `reset_hard(work, h0_sha)`，而 resume 只把 `state_after_round<last>` 塞进新仓库 —— H0 的
    tree 对象从来没进过那个对象库。修法是先重铺 round 0 的保存态：tree hash 是内容寻址的，
    同样的文件在这里重新提交就会得到同一个地址，`reset_hard` 也就能取到它。
    """
    from eval.ckpt.git_state import reset_hard

    run_dir = _run(tmp_path, platform=None)
    got = _resume_train_state(_args(), run_dir, None, tmp_path / "work")

    assert got["restored_base"] == got["h0_sha"], "重铺的 base 必须就是曲线发布的那个地址"
    # 原来抛 CalledProcessError 的那一行：现在必须能跑通。
    reset_hard(tmp_path / "work", got["h0_sha"])
    assert (tmp_path / "work" / "agent.py").read_text() == "print('round 0')\n"


def test_a_resume_refuses_when_the_saved_base_is_not_what_the_curve_scored(
        tmp_path: Path) -> None:
    """保存的 base 与曲线上的地址不一致 = 两个状态，不能接着往后记。"""
    run_dir = _run(tmp_path, platform=None)
    (run_dir / "state_after_round0" / "agent.py").write_text("print('not the base')\n")
    with pytest.raises(ResumeRefused) as exc:
        _resume_train_state(_args(), run_dir, None, tmp_path / "work")
    assert "not the state the curve published" in str(exc.value)


def test_a_resume_refuses_when_the_last_state_is_not_what_the_curve_scored(
        tmp_path: Path) -> None:
    """同样的一致性检查也要落在"要继续的那一轮"上 —— docstring 承诺过，代码此前没做。"""
    run_dir = _run(tmp_path, platform=None)
    (run_dir / "state_after_round1" / "agent.py").write_text("print('drifted')\n")
    with pytest.raises(ResumeRefused) as exc:
        _resume_train_state(_args(), run_dir, None, tmp_path / "work")
    assert "not the state that score belongs to" in str(exc.value)


def test_a_resume_refuses_before_measuring_when_the_base_object_is_gone(
        tmp_path: Path) -> None:
    """没有 base 保存态、对象库里也没有它：拒绝，而不是跑到最后一轮才崩。"""
    run_dir = _run(tmp_path, platform=None, with_base_state=False)
    with pytest.raises(ResumeRefused) as exc:
        _resume_train_state(_args(), run_dir, None, tmp_path / "work")
    assert "state_after_round0" in str(exc.value)
    assert "object store" in str(exc.value)


def test_run_meta_keeps_the_platform_the_curve_began_on(tmp_path: Path) -> None:
    """The field is the resume guard's evidence, so a later write must not move it.

    Measured 2026-10-20T12:10Z: `_write_run_meta` passed the *current* digest on every call,
    and the write happens before `--resume` reads the record -- so the guard compared the new
    value with itself and let a run whose recorded sha was `deadbeef` continue silently.
    """
    from harnessgrad.records import _write_run_meta

    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)
    _write_run_meta(run_dir, platform_content_sha="tree-A", run_id="r")
    _write_run_meta(run_dir, platform_content_sha="tree-B", rounds_measured=3)
    meta = json.loads((run_dir / "run_meta.json").read_text())
    assert meta["platform_content_sha"] == "tree-A"
    assert meta["platform_content_sha_started"] == "tree-A"
    assert meta["rounds_measured"] == 3
    # ...and the tree that is running now is still on the record, under `platform`.
    assert meta["platform"]["content_sha"]


def test_bad_saved_state_preserves_workspace_and_receipts(tmp_path, monkeypatch):
    import driver
    run_dir = _run(tmp_path, platform="old")
    monkeypatch.setattr(driver, "_platform_content_sha", lambda: "new")
    work = tmp_path / "work"
    work.mkdir()
    (work / "original").write_bytes(b"keep")
    before = {p.relative_to(run_dir): p.read_bytes() for p in run_dir.rglob("*") if p.is_file()}
    (run_dir / "state_after_round1" / "agent.py").write_text("tampered")
    before = {p.relative_to(run_dir): p.read_bytes() for p in run_dir.rglob("*") if p.is_file()}
    args = _args()
    args.resume_across_platforms = "reviewed"
    with pytest.raises(ResumeRefused):
        _resume_train_state(args, run_dir, None, work)
    assert (work / "original").read_bytes() == b"keep"
    assert before == {p.relative_to(run_dir): p.read_bytes() for p in run_dir.rglob("*") if p.is_file()}
    assert not list(tmp_path.glob(".resume-*"))


@pytest.mark.parametrize("failure", ["configuration", "sandbox"])
def test_driver_preflight_refusal_preserves_resume_receipts(tmp_path, monkeypatch, failure, capsys):
    import sys
    import driver
    from harnessgrad import config
    from eval.observe.console import CONSOLE

    run_dir = _run(tmp_path, platform=None, split_sha=None)
    work = tmp_path / "work" / run_dir.name / "workspace"
    work.mkdir(parents=True)
    (work / "sentinel").write_bytes(b"original workspace")
    (run_dir / "events.jsonl").write_bytes(b'{"seq": 1, "kind": "old"}\n')
    before_run = {p.relative_to(run_dir): p.read_bytes() for p in run_dir.rglob("*") if p.is_file()}
    before_work = {p.relative_to(work): p.read_bytes() for p in work.rglob("*") if p.is_file()}
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    synthetic_context = _ctx()
    monkeypatch.setattr(driver.verification_context, "capture", lambda *a, **k: synthetic_context)
    monkeypatch.setattr(driver, "load_env", lambda *_: {})
    monkeypatch.setattr(driver, "_probe_tool_calls", lambda *a, **k: pytest.fail("model call"))
    if failure == "configuration":
        check = SimpleNamespace(required=True, ok=False)
        monkeypatch.setattr(driver.readiness, "configuration_checks", lambda *_: [check])
        monkeypatch.setattr(driver.readiness, "render", lambda *_: "synthetic failure")
    else:
        monkeypatch.setattr(driver.readiness, "configuration_checks", lambda *_: [])
        monkeypatch.setattr(driver.sandbox, "self_check", lambda *_: {"ok": False, "detail": "synthetic failure"})
    monkeypatch.setattr(sys, "argv", ["driver.py", "--dataset", "demo", "--harness", "loop",
        "--sampling", "all", "--rounds", "3", "--improver", "none", "--resume",
        "--method-entrypoint", f"{sys.executable} {driver.PLATFORM_ROOT / 'methods/noop/run.py'}",
        "--run-id", run_dir.name, "--runs-root", str(tmp_path / "runs"),
        "--work-root", str(tmp_path / "work"), "--gpu-budget-dir", str(tmp_path / "slots")])
    CONSOLE.close()
    config.reset()
    try:
        assert driver.main() == 2
    finally:
        CONSOLE.close()
        config.reset()
    captured = capsys.readouterr()
    assert "synthetic failure" in captured.out + captured.err
    assert before_run == {p.relative_to(run_dir): p.read_bytes() for p in run_dir.rglob("*") if p.is_file()}
    assert before_work == {p.relative_to(work): p.read_bytes() for p in work.rglob("*") if p.is_file()}
    assert not list(work.parent.glob(".resume-*"))


def test_resume_events_append_without_overwriting(tmp_path):
    from eval.observe.console import Console
    path = tmp_path / 'events.jsonl'
    original = b'{"seq": 8, "kind": "old"}\n'
    path.write_bytes(original)
    console = Console()
    console.attach(path, append=True)
    console.emit('resume')
    console.close()
    content = path.read_bytes()
    assert content.startswith(original)
    assert json.loads(content[len(original):])['seq'] == 9


@pytest.mark.parametrize('mode', ['missing', 'changed'])
def test_resume_refuses_unproven_verification_context_without_side_effects(tmp_path, mode):
    run_dir = _run(tmp_path, platform=None)
    work = tmp_path / 'work'; work.mkdir(); (work / 'sentinel').write_bytes(b'original')
    meta_path = run_dir / 'run_meta.json'
    metadata = json.loads(meta_path.read_text())
    if mode == 'missing':
        metadata.pop('verification_context')
    else:
        metadata['verification_context'] = verification_context.capture([{'task_id':'t1'}], {'t1':'changed'})
    meta_path.write_text(json.dumps(metadata))
    before = {p.relative_to(run_dir): p.read_bytes() for p in run_dir.rglob('*') if p.is_file()}
    with pytest.raises(ResumeRefused, match='verification'):
        _resume_train_state(_args(), run_dir, None, work)
    assert (work / 'sentinel').read_bytes() == b'original'
    assert before == {p.relative_to(run_dir): p.read_bytes() for p in run_dir.rglob('*') if p.is_file()}
    assert not list(work.parent.glob('.resume-*'))
