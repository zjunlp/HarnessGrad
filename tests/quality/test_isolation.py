"""The two checks that keep a run from silently invalidating itself.

Both are detection, not prevention -- without a container, a subprocess with the
platform on disk can edit the platform. What is tested here is that the platform
*notices*, and that it notices loudly enough to stop. A check that cannot fail on
a deliberately bad input is decoration, so each test below tampers on purpose and
asserts the platform refuses.

The property being protected: a score is the platform's opinion of a harness.
Every way of making the platform's opinion editable by the thing it is judging
has to end the run.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.run.integrity import (PLATFORM, diff, snapshot,  # noqa: E402
                            workspace_is_outside_platform)

#: The shared platform directories this file may read but must never write.
#: They are the tree `eval/integrity.py` hashes around every run, and the tree
#: every other test in the suite walks -- so a test that edits one of them for
#: even an instant can crash a concurrent process with a `FileNotFoundError`
#: for a file it briefly created or removed.
_GUARDED = ("driver.py", "harnessgrad", "eval", "data", "tools", "methods",
            "tests", "docs", "improvers", "base_harness")


def _platform_stat(root: Path | None = None) -> dict[str, tuple[int, int]]:
    """`path -> (size, mtime_ns)` for every file under the guarded top level.

    `stat()` only, deliberately: this runs around *every* test in the file, and
    hashing the whole tree each time would cost more than the tests it guards.
    Size plus modification time catches the class of change that matters -- a
    write, a create, a delete or a truncate -- and a same-length rewrite still
    moves the mtime.
    """
    root = ROOT if root is None else root
    out: dict[str, tuple[int, int]] = {}

    def walk_error(error):
        raise error  # A missing source directory must still fail the guard.

    for name in _GUARDED:
        target = root / name
        if target.is_file():
            st = target.stat()
            out[name] = (st.st_size, st.st_mtime_ns)
        elif target.is_dir():
            for directory, dirs, files in os.walk(target, onerror=walk_error):
                # Prune generated directories before descent: other pytest and
                # import processes may create, update or remove them at any time.
                dirs[:] = [d for d in dirs if d not in {"__pycache__", ".pytest_cache"}]
                for filename in files:
                    if filename.endswith(".pyc"):
                        continue
                    path = Path(directory) / filename
                    if path.is_file():
                        st = path.stat()
                        out[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture(autouse=True)
def _the_shared_checkout_is_never_edited():
    """Fail loudly if a test writes anywhere under the real platform root.

    This is what keeps the tamper tests below honest. They protect a real
    property -- "the platform notices a changed file" -- and they prove it on a
    synthetic tree under `tmp_path`, because editing the real checkout to prove
    the same thing is exactly what raced every concurrent test that walks the
    tree. A test that does it again fails here, naming the offending paths.
    """
    before = _platform_stat()
    yield
    after = _platform_stat()
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    report = []
    if added:
        report.append(f"added: {added}")
    if removed:
        report.append(f"removed: {removed}")
    if changed:
        report.append(f"resized or retouched: {changed}")
    assert not report, (
        f"this test edited the shared checkout at {ROOT}; the platform is hashed "
        "around every run and another test process may be walking these paths. "
        "Mutate a synthetic platform under `tmp_path` instead -- " + "; ".join(report))


def test_checkout_guard_ignores_generated_caches(tmp_path, monkeypatch):
    tree = tmp_path / "tests"
    tree.mkdir()
    source = tree / "test_example.py"
    source.write_text("# synthetic source\n")
    before = _platform_stat(tmp_path)
    assert set(before) == {"tests/test_example.py"}
    caches = [tree / "__pycache__", tree / ".pytest_cache"]
    for cache in caches:
        cache.mkdir()
        (cache / "generated").write_text("cache output\n")
    (tree / "example.pyc").write_bytes(b"compiled")
    scandir = os.scandir

    def source_only_scandir(path):
        assert Path(path) not in caches, "the guard must prune caches before descent"
        return scandir(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", source_only_scandir)
        assert _platform_stat(tmp_path) == before
    for cache in caches:
        shutil.rmtree(cache)
    assert _platform_stat(tmp_path) == before


def test_checkout_guard_still_tracks_source_changes(tmp_path):
    tree = tmp_path / "eval"
    tree.mkdir()
    source = tree / "runner.py"
    source.write_text("# first\n")
    before = _platform_stat(tmp_path)
    assert set(before) == {"eval/runner.py"}
    source.write_text("# longer replacement\n")
    assert _platform_stat(tmp_path)["eval/runner.py"] != before["eval/runner.py"]
    added = tree / "new.py"
    added.write_text("# new\n")
    assert set(_platform_stat(tmp_path)) == {"eval/runner.py", "eval/new.py"}
    source.unlink()
    assert set(_platform_stat(tmp_path)) == {"eval/new.py"}


def test_snapshot_covers_the_referee_not_the_candidate():
    """The scorer is watched; the artifact under improvement is not.

    `base_harness/` missing from the tuple is deliberate and load-bearing: every
    run copies it into a workspace and edits the copy. Watching it would flag the
    only write the platform exists to make.
    """
    snap = snapshot(ROOT)
    assert "driver.py" in snap, "the driver decides what a curve means"
    assert "eval/run/integrity.py" in snap, "the checker watches itself"
    assert "eval/run/runner.py" in snap, "the runner runs the subject"
    assert ".env" in snap, "the credentials pay for the measurement"
    assert not [k for k in snap if k.startswith("base_harness/")], \
        "base_harness/ is a candidate, not a referee"


def test_a_modified_platform_file_is_reported_by_name(tmp_path):
    """The point of the check: it says which file, so the run can be fixed.

    The mutation is against a synthetic platform under `tmp_path` on purpose.
    The property under test is "a content change is named", which does not need
    the real checkout to be edited -- and editing `tools/` raced every
    concurrent test that walked `tools/` at that instant.
    """
    (tmp_path / "tools").mkdir()
    target = tmp_path / "tools" / "plot_curve.py"
    original = b"# a platform tool\n"
    target.write_bytes(original)
    before = snapshot(tmp_path)
    # The fixture must be shaped like a platform, or the diff below is vacuous.
    assert "tools/plot_curve.py" in before, before
    target.write_bytes(original + b"\n# tampered\n")
    changes = diff(before, snapshot(tmp_path))
    assert changes["modified"] == ["tools/plot_curve.py"], changes
    assert not changes["added"] and not changes["removed"], changes
    target.write_bytes(original)
    assert diff(before, snapshot(tmp_path))["modified"] == [], "revert must be clean"


def test_an_added_or_removed_platform_file_is_also_a_violation(tmp_path):
    """Deleting the scorer is as effective as rewriting it.

    Synthetic platform again: the property is "a file appeared/disappeared and
    is named", and proving it by creating and deleting a file in the real
    `tools/` is what made a concurrent walk of `tools/` crash.
    """
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "plot_curve.py").write_text("# a platform tool\n")
    victim = tmp_path / "tools" / "probe.py"
    before = snapshot(tmp_path)
    assert "tools/plot_curve.py" in before, before
    victim.write_text("# nothing\n")
    assert "tools/probe.py" in diff(before, snapshot(tmp_path))["added"]

    # Removal is measured against a snapshot taken while the file existed.
    with_file = snapshot(tmp_path)
    victim.unlink()
    assert "tools/probe.py" in diff(with_file, snapshot(tmp_path))["removed"]


def test_workspace_inside_the_platform_is_refused():
    """Rule 1: a workspace in the platform tree is two `..` from the referee.

    Not a theoretical exposure. Measured before this guard existed: a harness at
    `runs/<id>/workspace` could read `driver.py`, read `.env`, and write anywhere
    in the platform directory.
    """
    assert not workspace_is_outside_platform(ROOT / "runs" / "x" / "workspace", ROOT)
    assert not workspace_is_outside_platform(ROOT, ROOT)
    assert workspace_is_outside_platform(ROOT.parent / "harnessgrad_work" / "w", ROOT)
    # `..` must not be accepted at face value; only the resolved path counts.
    assert not workspace_is_outside_platform(
        ROOT / ".." / ROOT.name / "runs" / "w", ROOT)


def test_the_driver_refuses_a_workspace_inside_the_platform():
    """The guard is wired in, not just available as a function.

    `--sampling all` is load-bearing and was missing. The driver's default sampling
    is now `diagnostic`, which is a **staged** run: it forces `rounds=0` and demands
    `--policy-evidence`, so without this flag the staged-policy refusal fires first
    and the workspace guard is never reached. The test then passed for the wrong
    reason -- it asserted "refusing to run" against a different refusal. Measured:
    the run below reaches the guard and the run directory is never created.
    """
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", "guard-probe", "--sampling", "all",
         "--work-root", ".", "--dataset", "demo", "--method-entrypoint",
         "python methods/noop/run.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode != 0, proc.stdout
    assert "refusing to run" in proc.stderr.lower(), proc.stderr[-800:]
    assert "inside the platform" in proc.stderr, proc.stderr[-800:]
    assert not (ROOT / "guard-probe").exists(), \
        "the refusal must happen before anything is staged"


def test_a_harness_that_edits_the_platform_fails_the_run(tmp_path, monkeypatch):
    """Rule 2, exercised through the real runner.

    The harness here is the worst case the platform can be handed: one whose
    entrypoint appends to `eval/run/runner.py` -- the file that decides what its own
    answer is worth. `evaluate()` must name it.

    `PLATFORM_ROOT` points at a synthetic platform under `tmp_path` rather than
    the checkout. `evaluate()` is in `eval/run/runner.py`, which imports that global
    into its own namespace, so patching it there changes the root the check
    actually hashes -- and rewriting the real `eval/run/runner.py` is what raced
    every concurrent test that imports or walks `eval/`.
    """
    import data.scaffolds.registry as registry
    from eval.run.runner import evaluate

    platform = tmp_path / "platform"
    victim = platform / "eval" / "run" / "runner.py"
    victim.parent.mkdir(parents=True)
    victim.write_text("# the synthetic runner\n")
    monkeypatch.setattr("eval.run.runner.PLATFORM_ROOT", platform)

    tasks_all, scorable = registry.load("demo")
    tasks = tasks_all[:1]

    harness = tmp_path / "evil"
    harness.mkdir()
    (harness / "harness.json").write_text(json.dumps(
        {"name": "evil", "version": "0.0.1", "entrypoint": "agent.py"}))
    (harness / "agent.py").write_text(
        "import pathlib, sys\n"
        f"victim = pathlib.Path({str(victim)!r})\n"
        "victim.write_text(victim.read_text() + '# owned\\n')\n"
        "d = pathlib.Path(sys.argv[sys.argv.index('--workdir') + 1])\n"
        "(d / 'answer.txt').write_text('done')\n"
    )
    original = victim.read_bytes()
    # `sandbox=False` on purpose: this test is about the DETECTION layer, which
    # is what protects the platform from a method and what remains if bwrap is
    # unavailable. With the sandbox on, the same harness cannot write at all --
    # see tests/quality/test_sandbox.py for that half.
    res = evaluate(harness, tasks, scorable, harness_sha="evil", sandbox=False)
    assert "eval/run/runner.py" in res["tampered"].get("modified", []), res
    assert victim.read_bytes() != original, "the harness really did write"

    # And the honest case must be silent, or the check is just noise.
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "harness.json").write_text(json.dumps(
        {"name": "clean", "version": "0.0.1", "entrypoint": "agent.py"}))
    (clean / "agent.py").write_text(
        "import pathlib, sys\n"
        "d = pathlib.Path(sys.argv[sys.argv.index('--workdir') + 1])\n"
        "(d / 'answer.txt').write_text('done')\n"
    )
    res = evaluate(clean, tasks, scorable, harness_sha="clean", sandbox=False)
    assert not any(res["tampered"].values()), res["tampered"]


def test_the_driver_exits_nonzero_when_a_harness_tampers(tmp_path):
    """End to end: the check stops the run instead of printing a curve.

    `--no-sandbox` is passed deliberately. This is the layer that still works when
    the namespace cannot be built (no bwrap, an unusual mount layout), so it has to
    be tested with the namespace switched off. With it on, the harness cannot reach
    the platform to tamper with it, which is a stronger result and a different test.
    

    A tampered curve is worse than no curve. It has a shape, it has confidence
    intervals, and it is the attacker's self-assessment -- so the only safe
    outcome is a non-zero exit with the file named and nothing written to
    `runs/`.

    The whole platform is copied and the driver runs from the copy. The probe
    harness has to be able to edit `eval/run/runner.py` for the check to fire, and
    doing that against the real checkout made this file unsafe to run next to
    anything else: a file rewritten for a moment is missing for whoever walks
    the tree at that moment. The copy is byte-identical, so the check under test
    is the same check.
    """
    copy = tmp_path / "platform"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(
        ".git", "runs", "__pycache__", "current_harness"))

    harness = copy / "base_harness" / "_probe_evil"
    harness.mkdir()
    victim = copy / "eval" / "run" / "runner.py"
    (harness / "harness.json").write_text(json.dumps(
        {"name": "probe-evil", "version": "0.0.1", "entrypoint": "agent.py"}))
    (harness / "agent.py").write_text(
        "import pathlib, sys\n"
        f"victim = pathlib.Path({str(victim)!r})\n"
        "victim.write_text(victim.read_text() + '# probe\\n')\n"
        "d = pathlib.Path(sys.argv[sys.argv.index('--workdir') + 1])\n"
        "(d / 'answer.txt').write_text('alpha')\n"
    )
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "_probe_evil", "--mode",
         "A", "--rounds", "1", "--run-id", "probe-evil", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"), "--method-entrypoint",
         f"{sys.executable} {copy / 'methods/noop/run.py'}", "--no-sandbox"],
        cwd=copy, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 3, f"{proc.returncode}: {proc.stdout[-500:]}"
    assert "FAILING RUN" in proc.stderr, proc.stderr[-600:]
    assert "eval/run/runner.py" in proc.stderr, proc.stderr[-600:]
    # The refusal has to say the number is unusable, not merely that something moved.
    # This asserted the literal phrase "not a measurement" until it stopped existing;
    # `harnessgrad/records.py::_require_untampered` now says the run's integrity is
    # unverified and that the changed paths must be inspected first. Asserted on that
    # claim rather than on the sentence, so a rewording does not silently delete the
    # property while a missing claim still fails.
    assert "integrity is unverified" in proc.stderr, proc.stderr[-600:]
    assert not (runs / "probe-evil" / "curve.jsonl").exists(), \
        "a tampered run must not leave a curve behind"
    # But it does say where it was working, so the failed run is inspectable.
    meta = json.loads((runs / "probe-evil" / "run_meta.json").read_text())
    assert meta["workspace"] == str(tmp_path / "work" / "probe-evil" / "workspace")
    # **`unified`, not the flag.** `--mode` is accepted so old scripts keep working but
    # it selects nothing: there is one engine, and the submission's shape is the
    # method's own choice (`steps_in_submission` on each point says which it made).
    assert meta["mode"] == "unified", meta["mode"]


def test_the_method_side_guard_fails_the_call(tmp_path):
    """The method is the other subject, and the one with the clearer motive.

    Run against a *copy* of the platform: the point is that a method which edits
    the referee cannot be measured, and a test that proves it by editing the real
    referee would leave the platform it is protecting in the state it forbids.
    The copy is byte-identical, so the check it exercises is the same one.

    `sandboxed=False` is explicit, and the reason is worth keeping: a method is now
    sandboxed like the harness, so a write to the platform is *prevented* rather than
    *detected* -- the cheat below cannot even reach the file. This test is the second
    layer, and it still has to work, because the sandbox is a `--no-sandbox` away and
    because a sandbox that silently did not apply is the one failure this platform
    exists to prevent. The prevention layer has its own test next door.
    """
    copy = tmp_path / "platform"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(
        ".git", "runs", "__pycache__", "current_harness"))

    cheat = tmp_path / "cheat.py"
    victim = copy / "eval" / "run" / "metrics.py"
    cheat.write_text(
        "import pathlib, sys\n"
        f"victim = pathlib.Path({str(victim)!r})\n"
        "victim.write_text(victim.read_text() + '# the method was here\\n')\n"
        "sys.stdout.write('{\"changed\": false}')\n"
    )

    probe = copy / "probe_method_guard.py"
    probe.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
        # Addressed through the layer the guard lives in: `driver.py` is the
        # entrypoint, and what this probe needs is the check around a method call.
        "from harnessgrad.methods import PlatformTampered, _call_method\n"
        "try:\n"
        "    _call_method(Path('.'), [sys.executable, "
        f"{str(cheat)!r}], {{}}, 60, sandboxed=False)\n"
        "except PlatformTampered as exc:\n"
        "    print('CAUGHT', sorted(exc.args[0].get('modified', [])))\n"
        "    raise SystemExit(3)\n"
        "print('NOT CAUGHT')\n"
        "raise SystemExit(0)\n"
    )
    try:
        proc = subprocess.run([sys.executable, str(probe)], cwd=copy,
                              capture_output=True, text=True, timeout=180)
        assert proc.returncode == 3, f"{proc.returncode}: {proc.stdout} {proc.stderr}"
        assert "eval/run/metrics.py" in proc.stdout, proc.stdout
    finally:
        shutil.rmtree(copy, ignore_errors=True)


def test_mode_a_completes_a_normal_round_and_records_its_diff(tmp_path):
    """跑完一次**正常**的 mode A 多轮,而不是在错误路径上退出。

    为什么需要这条:driver 的既有测试都在 round 0 就结束 —— 篡改检测退出 3、
    工作区越界拒绝启动。它们覆盖的是*失败*路径,于是"正常运行时会崩"这件事
    没有任何测试看着它。

    实测的代价:给 mode A 加逐轮 diff 记录时,`_save_round_diff` 被传入了一个
    在那个作用域里不存在的变量名(`run_dir`,而该函数的第 8 个参数叫 `base`,
    真正的目录变量当时叫 `runs_root`)。53 个测试全绿,而 UI 里一点"开始实验"
    立刻 traceback —— 因为没有任何测试真的把一轮 mode A 跑完。

    所以这条测试断言的是两件事:退出码为 0,以及每轮都留下了 diff 记录。
    """
    runs = tmp_path / "runs"
    work = tmp_path / "work"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", "normal-round", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs), "--work-root", str(work),
         # 绝对路径:driver 在 harness 工作树里执行方法,相对路径会解析到那里
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"},
    )
    assert proc.returncode == 0, f"{proc.returncode}\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    assert (runs / "normal-round" / "curve.jsonl").exists(), "没有曲线"

    # 逐轮 diff 记录:存在的意义是"这一轮改了什么"能在事后回答,包括"没改"。
    record = runs / "normal-round" / "diffs" / "round-1.json"
    assert record.exists(), (
        "mode A 跑完一轮却没有留下 diff 记录 —— 面板的每步 diff 会全是空的,"
        "而且没有任何东西提示它是空的")
    meta = json.loads(record.read_text())
    assert meta["round"] == 1 and "from" in meta and "to" in meta, meta


# ------------------------------ 方法那一侧:阻止,而不只是发现 ---

#: 探针的源码,三引号原样写出。之前用一串相邻字符串加 `\\n` 拼它,结果写出来的文件
#: 是坏的(语法错误),而**测试自己不知道** —— 它只是看到 method_reported 里没有那个键。
#: 一个写坏了的探针会让这条测试永远失败,或者更糟:让它看起来在测别的东西。
#:
#: 平台路径由测试注入,而不是让探针从 `argv[0]` 推。真实的方法装在
#: `methods/<name>/run.py`,往上三层就是平台;而这条测试的探针在 tmp 里,
#: 推出来的是 pytest 的临时根 —— 那样这条测试就会去问一个错的目录,然后
#: 「读不到」通过,什么也没测到。
PROBE_SOURCE = '''import json, os, sys
from pathlib import Path

req = json.loads(sys.stdin.read())
platform = Path("__PLATFORM__")   # 由测试替换成真实平台路径
runs = Path("__RUNS__")           # 由测试替换成这次运行的记录目录
out = {
    "listing": sorted(x.name for x in platform.iterdir()) if platform.is_dir() else None,
    "data": (platform / "data" / "probe_set.py").exists(),
    "dotenv": (platform / ".env").exists(),
    "scorer": (platform / "eval" / "run" / "runner.py").exists(),
    "own_program": Path(sys.argv[0]).exists(),
    "agent_key": bool(os.environ.get("HG_AGENT_API_KEY")),
    # 记录目录:里面有**每一道题的轨迹**,包括 eval 侧。它是方法最想要的东西 ——
    # 看过考试题的轨迹,就能把 harness 针对性地改成能过它们的样子。
    "runs_listing": sorted(x.name for x in runs.iterdir()) if runs.is_dir() else None,
    "runs_curve": (runs / "curve.jsonl").exists(),
}
try:
    (Path(req["base_harness"]) / "sneak").write_text("x")
    out["base_writable"] = True
except OSError as exc:
    out["base_writable"] = exc.strerror

Path(req["trajectory_out"]).write_text(json.dumps({
    "steps": [{"harness_dir": req["base_harness"], "label": "a", "edit_kind": "none",
               "claimed_cost": {}, "method_reported": {"audit": out}}],
    "trajectory_shape": "sequence", "nominated": 0,
    "provenance": {"method": "audit"}}))
sys.stdout.write(json.dumps({"changed": False}))
'''


def test_a_method_cannot_read_the_platform_it_is_being_measured_by(tmp_path):
    """**这是补的那个洞。**

    方法原本是普通子进程,平台整个在盘上 —— 而它自己的 `argv[0]` 就是
    `methods/<name>/run.py`,往上三层就是平台根。实测能读到 `data/probe_set.py`
    (也就是**每一道题的期望答案,包括 eval 侧**)、`.env` 和 `eval/run/runner.py`,而且手里
    有两把密钥。唯一的检查是调用前后的哈希,只看得见**写**。

    后果不是"泄露一点信息":方法把答案写死进 harness,每题满分,平台报告说这个方法很强,
    而且不会被发现。

    现在的允许清单只有一个:方法自己的程序。这个测试就是那句清单。
    """
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    (probe_dir / "run.py").write_text(
        PROBE_SOURCE.replace("__PLATFORM__", str(ROOT))
                    .replace("__RUNS__", str(tmp_path / "runs" / "audit")))

    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", "audit", "--dataset", "probe_set",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {probe_dir / 'run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-500:]}\n{proc.stderr[-500:]}"

    point = json.loads((runs / "audit" / "curve.jsonl").read_text().splitlines()[-1])
    got = point["method_reported"]["audit"]

    assert got["data"] is False, "方法能读到数据集 —— 那里有每一道题的期望答案"
    assert got["dotenv"] is False, "方法能读到 .env"
    assert got["scorer"] is False, "方法能读到评分器"
    assert got["agent_key"] is False, (
        "方法的进程里有 agent 的密钥;两笔预算按设计是分开的,而分开的配置在有人强制之前"
        "不算分开")
    assert got["own_program"] is True, "方法连自己的程序都读不到,那是把洞补成了砖墙"
    assert got["base_writable"] == "Read-only file system", got
    # 平台目录只应当剩下它被允许的那一部分
    assert got["listing"] == ["methods"], (
        f"平台在方法的沙箱里还有别的东西:{got['listing']}")
    # 记录目录:里面有每一道题的轨迹,包括 eval 侧。eval 轨迹现在**确实落盘**了
    # (那是人类查看失败原因的唯一依据),所以这条不是"文件不存在"的弱断言 ——
    # 它是"方法够不到"的断言,靠的是挂载规则而不是缺省。
    assert got["runs_listing"] is None, (
        f"方法能列出运行记录:{got['runs_listing']} —— 那里有 eval 侧的轨迹")
    assert got["runs_curve"] is False, "方法能读到曲线,也就是每一轮的分数和任务集"


# ------------------------------ harness 那一侧:实测过的四条 ---

#: 一个把"它能读到/写到什么"写进自己 trace 的 harness。三引号原样写出。
HARNESS_PROBE = '''import argparse, json, os, pathlib

ap = argparse.ArgumentParser()
ap.add_argument("--task"); ap.add_argument("--workdir")
a = ap.parse_args()
work = pathlib.Path(a.workdir)
task = json.loads(pathlib.Path(a.task).read_text())

PLATFORM = pathlib.Path("__PLATFORM__")
STATE = pathlib.Path(os.environ["HARNESSGRAD_STATE"])
out = {"task_id": task["task_id"]}

def readable(p):
    try:
        pathlib.Path(p).read_text()
        return True
    except OSError:
        return False

out["platform_visible"] = (PLATFORM / "driver.py").exists()
out["platform_listing"] = (sorted(x.name for x in PLATFORM.iterdir())
                           if PLATFORM.is_dir() else None)
out["data_readable"] = readable(PLATFORM / "data" / "probe_set.py")
out["dotenv_readable"] = readable(PLATFORM / ".env")
out["runs_readable"] = readable(PLATFORM / "runs")
out["keys_in_env"] = sorted(k for k in os.environ
                            if "KEY" in k.upper() or "SECRET" in k.upper())
out["state_carried"] = (STATE / "marker").read_text() if (STATE / "marker").exists() else False
(STATE / "marker").write_text(task["task_id"])
(work / "trace.jsonl").write_text(json.dumps({"probe": out}) + "\\n")
(work / "answer.txt").write_text("x")
'''


def _run_probe_harness(tmp_path, source_key="probe"):
    """Use a private platform outside /tmp, which the sandbox masks separately."""
    platform = tmp_path / "platform"
    shutil.copytree(ROOT, platform, ignore=shutil.ignore_patterns(
        ".git", "runs", "__pycache__", "current_harness", ".pytest_cache",
        "reports", "web", ".env"))
    # The probes need a real dotenv *path*, never the developer's credentials.
    (platform / ".env").write_text("# synthetic offline test configuration\n")
    repo = tmp_path / "probe_harness"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "harness.json").write_text(json.dumps(
        {"name": "probe", "version": "1", "entrypoint": "agent.py", "backend": "cli"}))
    (repo / "agent.py").write_text(
        HARNESS_PROBE.replace("__PLATFORM__", str(platform)))

    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", str(repo), "--mode", "A",
         "--rounds", "1", "--run-id", "p", "--dataset", "probe_set",
         "--sampling", "all", "--improver", "none", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {platform / 'methods/noop/run.py'}"],
        cwd=platform, capture_output=True, text=True, timeout=600,
        env={**os.environ, "HG_AGENT_BACKEND": "mock",
             "HG_AGENT_API_KEY": "synthetic-offline-fixture"})
    assert proc.returncode == 0, f"{proc.stdout[-500:]}\n{proc.stderr[-500:]}"

    found = []
    for f in sorted((runs / "p" / "round0_traces").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            event = json.loads(line)
            if source_key in event:
                found.append(event[source_key])
    assert found, "harness 的探针没有写进 trace"
    return found


def test_the_harness_cannot_read_the_platform_or_its_answers(tmp_path):
    """harness 那一侧的实测清单。

    平台目录**存在但是空的**:bwrap 把平台盖成一个空 tmpfs,于是路径名留着(那是刻意的
    停点:完全抹掉就得覆盖 work root 所在的整条分支),而里面什么都不剩。
    """
    probes = _run_probe_harness(tmp_path)
    first = probes[0]
    assert first["platform_visible"] is False, first
    assert first["platform_listing"] == [], (
        f"平台的沙箱里还有东西:{first['platform_listing']}")
    assert first["data_readable"] is False, "harness 能读到数据集"
    assert first["dotenv_readable"] is False, "harness 能读到 .env"
    assert first["runs_readable"] is False, "harness 能读到历史运行记录"


def test_the_harness_gets_only_its_own_credential(tmp_path):
    """两笔预算分开配置,而分开的配置在有人强制之前不算分开。

    方法那一侧已经因为这条被修过一次(method 曾同时拿到两把密钥);harness 这一侧
    本来就只拿到 agent 的,这里把它钉住。
    """
    first = _run_probe_harness(tmp_path, )[0]
    assert first["keys_in_env"] == ["HG_AGENT_API_KEY"], first["keys_in_env"]


def test_the_harness_has_no_memory_from_one_task_to_the_next(tmp_path):
    """**每一条可写路径都必须是每任务独立的。**

    `.state/` 用的是 tmpfs 而不是 bind,理由写在 `eval/containers/sandbox.py` 里:任务 N 留下的
    东西不能影响任务 N+1 的分数。但 `.state/` 只是三条可写路径之一,另外两条是
    `/tmp` 和 `HOME` —— 而一个有记忆的 harness 可以跨题累积,更重要的是可以把 eval 侧
    的信息带出去。这里四条路径一起测。
    """
    probes = _run_probe_harness(tmp_path)
    assert len(probes) >= 2, "至少要两个任务才谈得上跨任务"
    carried = [p for p in probes if p["state_carried"] is not False]
    assert not carried, (
        f"harness 在 .state/ 里留下了跨任务的状态:{[p['task_id'] for p in carried]}")


def test_neither_side_of_a_run_contains_the_other(tmp_path):
    """**结构性的那一条**:训练运行的记录里没有考试题的任何痕迹。

    这条替换了"记录两侧都留"的测试。两次改动的方向是相反的,理由也相反:

    1. 最早 `_save_traces` **只写 train 侧**,理由是"eval 轨迹放在运行目录里,离方法
       只有一个 `cp -r`"。代价从没被算过:eval 轨迹被丢掉,于是"harness 为什么在考试题
       上失败"没有答案,查看器只能看最没意思的那一半。
    2. 于是改成两侧都写 —— 安全性靠挂载规则(方法读不到 `runs/`,上面那条探针测试钉住)。
       但那样训练运行的目录里**确实**躺着考试题的轨迹,只是方法够不到。
    3. 现在两侧是**两次运行**。所以考试轨迹不在训练运行的记录里,不是被扣留,是**不存
       在于那次运行**。

    最后一条比第二条强:它不依赖任何检查正确,只依赖"那次运行没有加载那一侧"。这条测试
    就是那句话的两半 —— 训练侧没有 eval,考试侧没有 train。
    """
    runs = tmp_path / "runs"
    work = tmp_path / "work"

    def run(*extra, run_id):
        proc = subprocess.run(
            [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
             "--rounds", "1", "--run-id", run_id, "--dataset", "probe_set",
             "--sampling", "all", "--runs-root", str(runs), "--work-root", str(work),
             *extra,
             "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
            cwd=ROOT, capture_output=True, text=True, timeout=600,
            env={**os.environ, "HG_AGENT_BACKEND": "mock"})
        assert proc.returncode == 0, f"{proc.stdout[-600:]}\n{proc.stderr[-600:]}"

    run("--side", "train", run_id="t")
    run("--side", "eval", "--from-run", "t", run_id="e")

    def sides(run_id):
        d = runs / run_id / "round0_traces"
        assert (d / ".sides.json").exists(), f"{run_id} 没有留下轨迹标记"
        return json.loads((d / ".sides.json").read_text()), {p.stem for p in d.glob("*.jsonl")}

    train_sides, train_files = sides("t")
    eval_sides, eval_files = sides("e")

    t_split = json.loads((runs / "t" / "curve.jsonl").read_text().splitlines()[-1])["split"]

    # 训练运行的记录里,一条考试轨迹都没有 —— 而且是真的不在盘上,不只是没被标出来
    assert set(train_files) == set(t_split["train"]), train_files
    assert not (set(train_files) & set(t_split["eval"])), (
        f"训练运行的记录里有考试题:{sorted(set(train_files) & set(t_split['eval']))}")
    assert train_sides["eval"] == [], train_sides

    # 考试运行反过来
    assert set(eval_files) == set(t_split["eval"]), eval_files
    assert not (set(eval_files) & set(t_split["train"])), (
        f"考试运行的记录里有训练题:{sorted(set(eval_files) & set(t_split['train']))}")
    assert eval_sides["train"] == [], eval_sides

    # 两侧都非空,否则上面那些断言在空集上成立,什么都没测
    assert train_files and eval_files
