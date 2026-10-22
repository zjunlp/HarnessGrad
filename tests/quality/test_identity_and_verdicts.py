"""同一个 harness 必须只有一个名字;每一分必须带着它为什么是这一分。

两条不变量,都是被一次真实测量逼出来的(都发生在 `loop-terminal_bench-26452c`):

**身份。** 六轮的曲线报告了六个不同的 `identity.harness_sha`,而其中四轮的
`git rev-parse <sha>^{tree}` 完全相同、`diffs/round-N.patch` 全是 0 字节。原因是
身份用的是 **commit sha** —— 提交还带着作者时间戳和父提交,所以同样的内容每次
提交都换一个名字。代价不是"曲线不好看":缓存键是 `(harness_sha, task_id)`,
于是**缓存永远不命中**,同一份代码被实跑了四遍,agent 阶段在 98s 和 2841s 之间乱跳。

**归因。** 五道题全是 0.000,而 run 目录里没有任何一处能解释为什么。判分脚本的输出
(`verdict["detail"]`)和 harness 的 stderr 都已经算出来了,然后被丢掉。回答"为什么
0 分"只能靠人手工把题目自己的 test.sh 在平台外面重放一次。

这两条是同一类缺陷:**曲线报告了结论,却没有报告任何能解释结论的东西。**
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ckpt.git_state import commit_state, content_sha, init_repo, materialize  # noqa: E402


# ------------------------------------------------------------------ 身份 ---

def _repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "harness.json").write_text(json.dumps(
        {"name": "probe", "version": "1", "entrypoint": "agent.py"}))
    (work / "agent.py").write_text("print('v0')\n")
    init_repo(work)
    return work


def test_identical_content_gets_one_identity(tmp_path):
    """这条如果失败,缓存就不存在,曲线上的"移动"可能是时间戳。"""
    work = _repo(tmp_path)
    first = commit_state(work, "H0")
    # 内容没动,再提交一次(真实场景:一轮"no change"照样走 commit_state)。
    second = commit_state(work, "round 1: no change")
    third = commit_state(work, "round 2: no change")
    assert first == second == third, (
        f"同一棵树得到了不同的身份:{first[:8]} {second[:8]} {third[:8]}")
    assert len(first) == 40


def test_different_content_gets_a_different_identity(tmp_path):
    """反向的一半:改了内容而身份不变,那才是真的糟。"""
    work = _repo(tmp_path)
    before = commit_state(work, "H0")
    (work / "agent.py").write_text("print('v1')\n")
    after = commit_state(work, "round 1")
    assert before != after


def test_the_harness_s_own_scratch_cannot_change_its_identity(tmp_path):
    """`.state/` 是 harness 做题时的草稿,不是 harness。

    它要是进了身份,一个写了缓存文件的 harness 每轮都换 sha,而没有任何方法改过它。
    """
    work = _repo(tmp_path)
    before = commit_state(work, "H0")
    (work / ".state").mkdir(exist_ok=True)
    (work / ".state" / "cache.json").write_text("{}")
    (work / "__pycache__").mkdir(exist_ok=True)
    (work / "__pycache__" / "agent.cpython-312.pyc").write_bytes(b"\x00")
    after = commit_state(work, "round 1")
    assert before == after, "harness 自己写出来的草稿改变了它的身份"


def test_the_identity_can_still_be_materialized(tmp_path):
    """身份换了含义,但"用身份把那一轮的完整 harness 取回来"必须还能用。

    `_stage_for_method` 就是拿 `identity.harness_sha` 去 `git archive` 的,所以一个
    我们自己算的哈希(而不是对象库里的对象)会把方法的状态窗口直接弄坏。
    """
    work = _repo(tmp_path)
    sha = commit_state(work, "H0")
    dest = tmp_path / "out"
    count = materialize(work, sha, dest)
    assert count >= 2, count
    assert (dest / "harness.json").is_file()
    assert (dest / "agent.py").read_text() == "print('v0')\n"


def test_content_sha_is_stable_without_a_new_commit(tmp_path):
    work = _repo(tmp_path)
    commit_state(work, "H0")
    assert content_sha(work) == content_sha(work)


# --------------------------------------------------- 真实 run 里的归因 ---

def _run(tmp_path: Path, run_id: str = "verdict-check"):
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", run_id, "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    return runs / run_id, proc


def test_every_task_gets_a_verdict_file_saying_why(tmp_path):
    """`per_task: {id: 0.0}` 不能是记录里仅有的东西。"""
    run_dir, _ = _run(tmp_path)
    dest = run_dir / "verdicts" / "round-0"
    assert dest.is_dir(), f"没有归因目录:{list(run_dir.iterdir())}"
    files = sorted(p.name for p in dest.glob("*.json"))
    assert files, "归因目录是空的"
    for name in files:
        entry = json.loads((dest / name).read_text())
        assert entry["task_id"] == name[:-5]
        # 这四个字段就是"为什么"。分数本身没有解释力。
        for field in ("score", "kind", "passed", "detail"):
            assert field in entry, (name, sorted(entry))
        assert entry["score"] is not None
        assert entry.get("harness"), (
            f"{name}: harness 的输出没有被记录 —— 这正是 0 分无法解释的那个缺口")


def test_round_0_and_later_rounds_both_record_verdicts(tmp_path):
    """round 0 和 round N 走的是两条代码路径(driver.main 与 _run_mode_a)。"""
    run_dir, _ = _run(tmp_path, "verdict-rounds")
    assert (run_dir / "verdicts" / "round-0").is_dir()
    assert (run_dir / "verdicts" / "round-1").is_dir(), \
        "轮内测量没有写归因 —— 只有 round 0 修了"


def test_the_check_s_own_output_reaches_the_event_stream(tmp_path):
    """面板要能看见它,不然"为什么 0"还是要靠终端考古。"""
    run_dir, _ = _run(tmp_path, "verdict-events")
    events = [json.loads(l) for l in
              (run_dir / "events.jsonl").read_text().splitlines() if l.strip()]
    logs = [e for e in events if e.get("kind") == "log"]
    assert logs, "事件流里没有任何 log 事件"
    assert {e["source"] for e in logs} == {"check"}, {e["source"] for e in logs}
    for e in logs:
        assert e["task"], e
        assert "round" in e, e
        assert e.get("detail") is not None or e.get("body") is not None, e


def test_a_run_whose_harness_did_not_change_reports_one_identity(tmp_path):
    """端到端:方法什么都不改,六轮的 sha 不该有六个。

    这一条是这次 bug 的复现。`methods/noop` 每一轮都返回"没有改动",所以 round 1
    和 round 2 的 harness 是同一棵树 —— 曲线必须说同一个名字。
    """
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "3", "--run-id", "identity-check", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    points = [json.loads(l) for l in
              (runs / "identity-check" / "curve.jsonl").read_text().splitlines()
              if l.strip()]
    shas = [p["identity"]["harness_sha"] for p in points]
    assert len(shas) >= 2, shas
    assert len(set(shas)) == 1, (
        "noop 方法没有改动任何东西,曲线却报告了多个身份 —— "
        f"身份不是内容地址:{shas}")


def test_a_cached_round_still_explains_its_scores(tmp_path):
    """缓存命中的轮次也必须带着"为什么"。

    实测这个缺口的方式很直接:`loop-terminal_bench-41133` 的 round 5 全是缓存命中,
    五个 verdict 文件里 `kind=null`、`detail=""` —— 分数还在,解释没了。缓存里存的
    只有 score 和 trace,没有 verdict。
    """
    runs = tmp_path / "runs"
    argv = [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
            "--rounds", "2", "--run-id", "cache-verdict", "--dataset", "demo",
            "--sampling", "all", "--runs-root", str(runs),
            "--work-root", str(tmp_path / "work"),
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"]
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=300,
                          env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    # round 1 的 harness 与 round 0 相同(noop 不改),于是 round 1 是纯缓存命中。
    for rnd in (0, 1):
        d = runs / "cache-verdict" / "verdicts" / f"round-{rnd}"
        entries = [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]
        assert entries, f"round-{rnd} 没有 verdict 文件"
        for e in entries:
            assert e.get("kind"), f"round-{rnd} {e['task_id']}: 缓存命中后 kind 丢了"
            assert e.get("detail") is not None, (
                f"round-{rnd} {e['task_id']}: 缓存命中后 detail 丢了")


def test_the_point_names_the_skill_the_method_was_given(tmp_path, monkeypatch):
    """**同一个人,不同的说明书。** `improver` 说"用了哪个工具",说不出"工具被告知了什么"。

    实测:两次运行之间 `improvers/skill.md` 多了一条失败模式,方法名一样、improver 版本
    一样,编辑不一样,而两份记录里没有一个字解释这件事 —— 这正是身份块存在的意义。
    按调用读而不是 import 时缓存:平台可以在同一轮之内改自己的 skill。
    """
    import hashlib

    from harnessgrad import identity

    monkeypatch.setattr(identity, "PLATFORM_ROOT", tmp_path)
    (tmp_path / "improvers").mkdir()
    skill = tmp_path / "improvers" / "skill.md"
    skill.write_text("# v1\n")
    man = {"name": "probe", "version": "1"}
    first = identity._identity(man, "sha", "A")
    assert first["skill_sha"] == hashlib.sha256(b"# v1\n").hexdigest()

    skill.write_text("# v2\n")
    assert identity._identity(man, "sha", "A")["skill_sha"] != first["skill_sha"]

    skill.unlink()
    assert "skill_sha" not in identity._identity(man, "sha", "A"), \
        "没有 skill 就不该报一个空文件的哈希"


def test_a_method_that_carries_its_own_improver_records_none(tmp_path):
    """**记录不能点名一个没干过活的工具。**

    `methods/noop` 是"方法什么都没做"的对照:它不调用任何 improver,曲线就是起点
    harness 的地板。可 `improvers.json` 里有默认 improver,于是这条跑道的每个 curve
    point 都写着 `identity.improver: deepseek` —— 一次 deepseek 都没被调用过的运行,
    却自称用了它。这正是 improver 字段存在要防的那件事(见本文件开头那次
    `agent_model: mock`)。`--improver none` 是"这个方法自带(或根本不需要)改进器"。
    """
    runs = tmp_path / "runs"
    argv = [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
            "--rounds", "2", "--run-id", "no-improver", "--dataset", "demo",
            "--sampling", "all", "--improver", "none",
            "--runs-root", str(runs), "--work-root", str(tmp_path / "work"),
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"]
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=300,
                          env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    points = [json.loads(l) for l in
              (runs / "no-improver" / "curve.jsonl").read_text().splitlines()
              if l.strip()]
    assert points, "没有曲线点"
    for p in points:
        assert "improver" not in p["identity"], (
            "--improver none 的跑道仍然在点上点了名:"
            f"{p['identity'].get('improver')}")


def test_the_default_improver_is_still_resolved_when_none_is_not_asked_for(tmp_path):
    """反向的一半:不加 `--improver none` 时,默认改进器照旧要解析出来。

    否则这条修法就变成了"悄悄关掉记录 improver 的能力",而那会让真正用了改进器的
    跑道失去归因。
    """
    runs = tmp_path / "runs"
    argv = [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
            "--rounds", "1", "--run-id", "default-improver", "--dataset", "demo",
            "--sampling", "all",
            "--runs-root", str(runs), "--work-root", str(tmp_path / "work"),
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"]
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=300,
                          env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    points = [json.loads(l) for l in
              (runs / "default-improver" / "curve.jsonl").read_text().splitlines()
              if l.strip()]
    assert points and all("improver" in p["identity"] for p in points), (
        "默认改进器没有被记录:" + str([p["identity"].get("improver") for p in points]))


def test_the_point_names_the_method_that_produced_the_candidate(tmp_path):
    """曲线点必须能自己说出"这一轮是谁改的"。

    实测的缺口:点上有 `method_model`、有 improver 的名字,却没有**方法**的名字 ——
    它只出现在 `run_meta.method_entrypoint` 这个路径里。于是把曲线从 run 目录里拿出来
    (画图、比较、放进论文,都是这么用的)之后,round 1 是 `llm_improver` 还是 `rrsi`
    就答不上来,两个方法在同一张图里只能靠分数区分。
    """
    from harnessgrad.identity import _identity

    man = {"name": "loop", "version": "1"}
    named = _identity(man, "a" * 40, "A", method="rrsi")
    assert named["method"] == "rrsi"
    # 没有方法名时不留一个空字段:"没说"和"没有方法"是两件事
    assert "method" not in _identity(man, "a" * 40, "A")


def test_a_real_run_names_its_method_on_every_point(tmp_path):
    """端到端的一半:方法自报的名字(`editor.report(method=...)`)真的走到了点上。"""
    runs = tmp_path / "runs"
    argv = [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
            "--rounds", "2", "--run-id", "method-named", "--dataset", "demo",
            "--sampling", "all", "--improver", "none",
            "--runs-root", str(runs), "--work-root", str(tmp_path / "work"),
            "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"]
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=300,
                          env={**os.environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    points = [json.loads(l) for l in
              (runs / "method-named" / "curve.jsonl").read_text().splitlines()
              if l.strip()]
    assert points, "没有曲线点"
    # round 0 是 H0:平台测的起点,还没有任何方法跑过,所以它**不该**有方法名。
    assert points[0]["round"] == 0 and "method" not in points[0]["identity"], \
        points[0]["identity"]
    later = [p for p in points if p["round"] > 0]
    assert later and all((p["identity"].get("method") or "").startswith("noop")
                         for p in later), \
        [(p["round"], p["identity"].get("method")) for p in points]


# ------------------------------- 检查自己的测试计数:比 pass/fail 更细的信号 ---

def test_the_check_s_own_tally_becomes_a_rate():
    """`4 passed 1 failed` 必须变成一个数,而不是只活在 detail 字符串里。

    实测(`ds-dgm`):`large-scale-text-editing` 在 loop harness 下过 4/5,DeepSeek 的机制改动后
    过 3/5 —— 二值分数两次都是 0.0,曲线说"没变化",而产物**变差了**。10 道全对/全错的题分辨率
    是 0.1、噪声约 ±0.1:仪器看不见它自己要测的效应,而这个数从 2026-10-04 起就被解析出来了,
    只是 `_save_verdicts` 手挑六个字段把它丢了。
    """
    from eval.run.runner import test_rate_of

    assert test_rate_of({"tests": {"passed": 4, "failed": 1}}) == 0.8
    assert test_rate_of({"tests": {"passed": 0, "failed": 5}}) == 0.0
    assert test_rate_of({"tests": {"passed": 3, "failed": 0}}) == 1.0
    # `None` 不是 0:这个检查不报测试数,把它当失败是在编造一个检查从未产生的数
    assert test_rate_of({}) is None
    assert test_rate_of(None) is None
    assert test_rate_of({"tests": {"passed": 0, "failed": 0}}) is None
    # 只数跑过的:skipped 不是失败
    assert test_rate_of({"tests": {"passed": 4, "failed": 1, "skipped": 9,
                                   "tests": 14}}) == 0.8
    # 字段类型不对时不能崩(那是别人的格式)
    assert test_rate_of({"tests": {"passed": "4", "failed": 1}}) is None


def test_the_curve_point_always_says_whether_a_tally_existed(tmp_path):
    """`test_rate` 缺字段和 `test_rate: null` 必须是两件事。

    这个文件开头那条注释说的就是这件事:曲线报告了结论、没报告任何解释。一个**缺**字段的点
    和一个"该字段为假"的点读起来一样 —— 正是这个缺陷让一个功能上不可能有差别的候选读成了
    `+0.100` 的提升(`docs/experiment_noise.md`)。所以字段恒在:`null` 表示平台看过、没有检查
    报测试数,`test_rate_n_tasks` 说有几道题报了。
    """
    from eval.run.runner import test_rate_of
    from harnessgrad.records import _curve_point, test_rate_fields

    # 没有任何检查报测试数
    # No test tally does not mean no topology observation. These synthetic points
    # model a command scorer; w-obs refuses scores whose route is unknown.
    none_res = {"per_task": {"a": 0.0}, "verdicts": {},
                "per_task_scorer_isolation": {"a": "actor-workdir"}}
    point = _curve_point(run_id="r", round_index=0, identity={}, sampling={},
                         scores=[0.0], task_ids=["a"], cost={}, cumulative={},
                         **test_rate_fields(none_res))
    assert "test_rate" in point and point["test_rate"] is None
    assert point["test_rate_n_tasks"] == 0
    assert point["per_task_test_rate"] == {}
    assert point["tests_total"] == 0

    # 一个检查报 4/5
    some_res = {"per_task_test_rate": {"a": 0.8}, "test_rate": 0.8,
                "per_task_scorer_isolation": {"a": "actor-workdir"},
                "test_rate_n_tasks": 1, "tests_passed": 4, "tests_total": 5}
    point = _curve_point(run_id="r", round_index=0, identity={}, sampling={},
                         scores=[0.0], task_ids=["a"], cost={}, cumulative={},
                         **test_rate_fields(some_res))
    assert point["test_rate"] == 0.8
    assert point["test_rate_n_tasks"] == 1
    assert point["per_task_test_rate"] == {"a": 0.8}
    assert (point["tests_passed"], point["tests_total"]) == (4, 5)
    # 二值分数照旧,没被取代
    assert point["score"] == 0.0
    # 检查自己的 rate 函数与这里用的是同一个判据
    assert test_rate_of({"tests": {"passed": 4, "failed": 1}}) == point["test_rate"]


def test_a_verdict_file_carries_the_tally_not_only_the_sentence(tmp_path):
    """`_save_verdicts` 挑字段写盘,`tests` 被它丢掉了 —— 这是那个数字消失的**确切位置**。

    runner 早就在算它(`_test_report_failures` 返回 `numbers`),verdict 里也早就设了
    `verdict["tests"]`,而唯一写盘的函数手挑六个字段、把它漏了。所以"产物是变好了还是变坏了"
    只能靠人从 detail 字符串里正则出 "4 passed 1 failed"。
    """
    from harnessgrad.records import _save_verdicts

    run_dir = tmp_path / "run"
    res = {
        "per_task": {"t1": 0.0},
        "verdicts": {"t1": {"kind": "command", "passed": False, "detail": "reward 0",
                            "tests": {"tests": 5, "passed": 4, "failed": 1}}},
        "harness_output": {"t1": {"exit_code": 0, "stderr": ""}},
    }
    _save_verdicts(run_dir, 0, res, ["t1"])
    entry = json.loads((run_dir / "verdicts" / "round-0" / "t1.json").read_text())
    assert entry["tests"] == {"tests": 5, "passed": 4, "failed": 1}, entry
    # 老的六个字段照旧,现有读者不受影响
    for field in ("task_id", "round", "score", "kind", "passed", "detail"):
        assert field in entry, field


def test_self_referential_says_whether_the_method_is_the_harness_it_edits():
    """The paper's independent variable, recorded instead of re-derived by every reader.

    A HarnessX-style method edits the tree it is itself running in; `dsh` edits somebody
    else's. Both are `improver.name` + `harness_name` in the identity, and comparing them is
    the whole difference -- so the point says it.
    """
    from harnessgrad.identity import _identity

    man = {"name": "loop", "version": "0.2.0"}
    assert _identity(man, "s", "unified", improver={"name": "dsh"})["self_referential"] is False
    assert _identity(man, "s", "unified", improver={"name": "loop"})["self_referential"] is True
    # No improver at all (a reference method that edits as it runs) is not self-referential:
    # the field is a statement about an improver, and there is none to compare.
    assert _identity(man, "s", "unified")["self_referential"] is False


def test_harness_base_records_only_a_declared_lineage():
    from harnessgrad.identity import _identity

    man = {"name": "loop", "version": "0.2.0"}
    # A harness we wrote declares no base, and the field is then absent rather than null: "the
    # platform looked and found nothing" is a different statement from "written here".
    assert "harness_base" not in _identity(man, "s", "unified")

    declared = {**man, "base": {"repo": "https://example.invalid/upstream", "commit": "abc123",
                                "name": "upstream", "smuggled": ["a", "b"]}}
    base = _identity(declared, "s", "unified")["harness_base"]
    assert base == {"repo": "https://example.invalid/upstream", "commit": "abc123",
                    "name": "upstream"}
