"""训练集与测试集必须分开,否则"比较两个方法"是假的。

为什么这是平台的核心而不是一个选项
----------------------------------
允许方法在它被评分的同一批任务上看 trace,等于允许它针对考试题改 harness。分数会
涨,而涨的原因不是方法变好了,是它把题背下来了。平台上没有任何东西能区分这两种
上涨 —— 除非评测用的任务根本不是它看过的那批。

所以这条规则不是"给方法更多限制",它是"让分数还说明点东西":
    train  方法看得到 trace,用来诊断     —— 分数记录,但不作为结论
    eval   方法看不到 trace,只用来评分   —— 曲线上的点

测试分两层写:一层是"机制正确"(split 被解析、两边都被评测、方法只拿到 train),
一层是"不静默降级"(数据集没声明 split 时,平台必须说出来,而不是假装一切正常)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad.channel import _stage_for_method                    # noqa: E402
from harnessgrad.environments import (_narrow_to_run,                # noqa: E402
                                      _required_env_kinds, _resolve_envs)


# ------------------------------------------------------------------ 机制 ---

def test_registry_reports_the_split_when_a_dataset_declares_one():
    """数据集可以声明 split;读取它不该要求 driver 参与。"""
    import data.scaffolds.registry as registry

    tasks, scorable, split = registry.load_split("demo")
    assert tasks and scorable
    assert split is not None, "demo 现在应该带 SPLIT"
    assert set(split["train"]).isdisjoint(split["eval"]), \
        "train 和 eval 不能重叠 —— 重叠就等于没分"
    # 每一题都必须落在某一边,否则它既不被诊断也不被评分,是个无声的黑洞
    covered = set(split["train"]) | set(split["eval"])
    assert covered == {t["task_id"] for t in tasks}, \
        f"有任务不在任何一边:{covered ^ {t['task_id'] for t in tasks}}"


def test_a_dataset_without_a_split_is_reported_not_assumed(monkeypatch):
    """没有 split 的数据集:平台说清楚,而不是假装分过了。

    这是刻意的返回 `None` 而不是"全部当 train"或"全部当 eval":两种猜测都会让
    一次过拟合的实验看起来完全正常。

    这一段以前只断言了 `demo` **有** split —— 那验证的是反面。真正的 `None` 路径要
    把一个模块的声明拿掉才走得到,而平台上每个 fixture 都声明了 split(6 个数据集
    没有,但它们 `load()` 就拒绝,走不到这里),所以端到端跑不出来,只能在单元层
    把声明摘掉。
    """
    import data.demo as demo
    import data.scaffolds.registry as registry

    assert registry.load_split("demo")[2] is not None

    monkeypatch.delattr(demo, "SPLIT")
    tasks, scorable, split = registry.load_split("demo")
    assert split is None, "摘掉声明之后必须返回 None,而不是猜一个"
    assert tasks, "拿掉 split 不影响任务本身还能被读到"
    assert scorable is not None


# --------------------------------------------------------------- 端到端 ---

def _run(tmp_path: Path, harness: str = "loop_plain", *, side: str = "train",
         run_id: str = "split-check", extra: tuple = ()) -> tuple[dict, Path]:
    """One run, on one side. `extra` carries `--from-run` for an eval run."""
    runs = tmp_path / "runs"
    work = tmp_path / "work"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", harness, "--mode", "A",
         "--rounds", "1", "--run-id", run_id, "--dataset", "demo",
         "--sampling", "all", "--side", side,
         "--runs-root", str(runs), "--work-root", str(work), *extra,
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
        env={**os.environ, "HG_AGENT_BACKEND": "mock"},
    )
    assert proc.returncode == 0, f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    points = [json.loads(l) for l in
              (runs / run_id / "curve.jsonl").read_text().splitlines() if l.strip()]
    return {"points": points, "runs": runs, "stdout": proc.stdout,
            "stderr": proc.stderr}, work


def test_every_run_records_which_division_it_used(tmp_path):
    """`split_sha` 是"两点是否在同一根轴上"的判据。

    "同一个数据集"不足以说明两次运行评了同一批任务 —— 一次实验的 exam 可以是另一次
    实验的 study material。所以每次运行都记下用的是哪份划分,以及那份划分的 sha。
    这条测试拿真正跑出来的 `run_meta.json` 验,而不是验一个字段名存在。
    """
    import data.scaffolds.registry as registry

    declared, _ = _run(tmp_path, run_id="src-declared")
    meta = json.loads((declared["runs"] / "src-declared" / "run_meta.json").read_text())
    tasks, _, split = registry.load_split("demo")
    assert meta["split_source"] == "declared"
    assert meta["split_sha"] == registry.split_sha(split)

    platform, _ = _run(tmp_path, run_id="src-default",
                       extra=("--split", "default"))
    meta2 = json.loads((platform["runs"] / "src-default" / "run_meta.json").read_text())
    assert meta2["split_source"] == "default"
    assert meta2["split_sha"] == registry.split_sha(registry.default_split(tasks))
    # 同一个数据集、同样是 train 侧,但两份划分不是一根轴 —— 这正是要记 sha 的原因。
    assert meta2["split_sha"] != meta["split_sha"]

    # 同一份记录也带着 oracle 的来源,理由一样:gate 1 是 oracle 控制臂,而
    # "benchmark 自己的参考解得 1 分" 与 "我们自撰的参考解得 1 分" 是对检查可达性的两种
    # 不同陈述,曲线本身说不出是哪一种。`ORACLE_COVERAGE` 只在自撰时才有值,所以这里读
    # 到的是 `None`(写记录时 `None` 不落盘)。
    assert meta["oracle_source"] == "released"
    assert meta.get("oracle_coverage") is None


def test_a_run_covers_exactly_one_side_and_the_pair_comes_from_two_runs(tmp_path):
    """**一次运行只测一侧**;train 与 eval 的配对由两次运行经 `harness_sha` 连接。

    这条替换了原来那条"曲线点必须同时带 eval 和 train 分数"的测试 —— 旧契约要求一个
    运行同时装两侧,而正是那个要求迫使 eval 的轨迹出现在训练运行里。

    新契约下两侧是**两次运行**:
      · train run 的 `per_task` 就是 train 侧,`score_kind == "training"`
      · eval run 的 `per_task` 是 eval 侧,`score_kind == "exam"`,并带 `evaluated_from`
      · 两者靠 `identity.harness_sha` 连接

    "train 涨了 eval 没动"仍然看得见 —— 它现在是两次运行之间的比较,而不是一个点上
    的两列。丢的是每轮的分辨率,不是那个诊断。
    """
    train, _ = _run(tmp_path, side="train", run_id="t-side")
    evalr, _ = _run(tmp_path, side="eval", run_id="e-side",
                    extra=("--from-run", "t-side"))

    tp, ep = train["points"][-1], evalr["points"][-1]
    split = tp["split"]

    # 各占一侧,而且互不越界
    assert set(tp["per_task"]) == set(split["train"]), tp["per_task"]
    assert set(ep["per_task"]) == set(split["eval"]), ep["per_task"]
    assert tp["side"] == "train" and ep["side"] == "eval"
    assert tp["score_kind"] == "training", (
        "训练分数必须自称训练分数:它必然被拟合抬高,而这个字段就是防止它被当成绩引用")
    assert ep["score_kind"] == "exam"

    # 训练侧不该有第二个任务集 —— 那会是 `per_task` 的副本,而重复字段会让人以为测了两组
    assert tp.get("train_per_task") == {} and tp.get("train_score") is None, tp
    assert ep.get("train_per_task") == {} and ep.get("train_score") is None, ep

    # 配对:两次运行指向同一个 harness 状态
    assert ep["evaluated_from"]["run_id"] == "t-side"
    assert ep["evaluated_from"]["round"] == 1
    assert ep["evaluated_from"]["harness_sha"] == tp["identity"]["harness_sha"], (
        "eval 点必须能追到它所测的那个训练状态;`harness_sha` 是连接键")


def runs_channel(result) -> Path:
    """`runs/<id>/method_channel/round<N>/` —— 方法实际读到的那份通道。

    不复用 `work/_harnessgrad`:那个目录在候选换进来的时候按设计被删掉了,正是为了让
    「方法读到的东西」永远不会落进被评测的工作区。
    """
    kept = sorted((result["runs"] / "split-check" / "method_channel").glob("round*"))
    assert kept, "运行记录里没有留下通道副本,「方法看到了什么」就无法回答了"
    return kept[-1]


def test_the_evaluated_tree_does_not_contain_the_channel(tmp_path):
    """候选换进来之后,被评测的树里不能有 `_harnessgrad`。

    实测:方法失败时 `editor.fail` 把「那一步」指向 `round1_base`,而它带着通道 ——
    于是题面和金标被拷回工作区,并进了 `state_after_round<N>`(eval 运行正是从那里起步)。
    """
    result, work = _run(tmp_path)
    for name in ("workspace",):
        assert not (work / "split-check" / name / "_harnessgrad").exists(), \
            "活的通道留在了工作区里"
    for state in sorted((result["runs"] / "split-check").glob("state_after_round*")):
        assert not (state / "_harnessgrad").exists(), \
            f"{state.name} 里带着通道 —— 那会跟着 eval 运行一起被带进去"


def test_the_method_only_sees_the_train_traces(tmp_path):
    """交给方法的 traces 目录里不能有 eval 任务的痕迹。

    这是整件事的落点。前面几条都是记录,这一条才是隔离:如果方法能读到 eval 的
    trace,它就能针对那些题改 harness,而分数会照常上涨。
    """
    result, _ = _run(tmp_path)
    # 读记录里的那份通道副本 —— 活的通道在候选换进来时被删掉了(见上一条测试)
    trace_dir = runs_channel(result) / "traces"
    assert trace_dir.is_dir(), f"方法应该拿到 traces 目录:{trace_dir}"

    seen = {p.stem for p in trace_dir.glob("*.jsonl")}
    import data.scaffolds.registry as registry
    _, _, split = registry.load_split("demo")

    assert seen == set(split["train"]), (
        f"方法看到的应是 train({sorted(split['train'])}),实际 {sorted(seen)}")
    leaked = seen & set(split["eval"])
    assert not leaked, f"eval 的 trace 泄漏给了方法:{sorted(leaked)}"


def test_the_method_never_sees_the_eval_side(tmp_path):
    """一次训练运行的通道里,不能有考试侧的**任何**东西。

    这条测试以前写的是「`per_task` 不进通道」,理由是「`per_task` 是考试的逐题分数」。
    那是 §2.6 之前的形状 —— 一个点同时带两侧,`per_task` 是考试,`train_per_task` 是诊断。
    §2.6 让一次运行只关于一侧之后,训练点上的 `per_task` **就是**训练侧的逐题分数,也正是
    §2.3 说方法应当拿到的东西;于是这条断言退化成在检查一个字段名,而不再检查泄漏。
    实测的代价:通道把契约承诺的逐题分数扣掉了,`protocol.studied_per_task()` 在每一个真实
    训练运行上返回 `{}`。

    现在它检查真正要紧的那件事 —— 考试侧根本不在这次运行里:

      * 分数里没有考试题
      * 轨迹里没有考试题
      * 任务上下文里没有考试题
      * 聚合分还在(那是方法的回报信号,扣掉它方法就成了随机游走)

    去掉扣留之所以安全,靠的就是这个:方法只可能看到训练点,而 `_for_method` 自己会拒绝
    一个 exam 点(见 `test_method_channel.py`)。
    """
    result, work = _run(tmp_path)
    # 活的通道是临时的(候选交换会删掉它,免得它落进被评测的树),所以读记录里的那份
    channel = runs_channel(result)
    point = result["points"][-1]
    train_ids = set(point["split"]["train"])
    eval_ids = set(point["split"]["eval"])
    assert eval_ids and train_ids, "demo 声明了 split,这个测试才有意义"

    for path in [channel / "round.json", *sorted((channel / "history").glob("*.json"))]:
        seen = json.loads(path.read_text())
        assert seen.get("side") == "train", f"{path.name} 是一个考试点"
        assert "score" in seen, f"{path.name} 没有了聚合分,方法就失去回报信号"
        keys = set(seen.get("per_task") or {})
        assert keys, f"{path.name} 没有任何逐题分数,归因类方法就没有数据可用"
        assert not (keys & eval_ids), \
            f"{path.name} 的逐题分数里有考试题:{sorted(keys & eval_ids)}"

    staged_traces = {p.stem for p in (channel / "traces").glob("*.jsonl")}
    assert staged_traces, "通道里没有轨迹,方法等于在盲改"
    assert not (staged_traces & eval_ids), \
        f"通道里出现了考试题的轨迹:{sorted(staged_traces & eval_ids)}"

    staged_tasks = {p.stem for p in (channel / "tasks").glob("*.json")}
    assert staged_tasks, "通道里没有任务上下文"
    assert not (staged_tasks & eval_ids), \
        f"通道里出现了考试题的任务上下文:{sorted(staged_tasks & eval_ids)}"

    # 平台自己的记录不能被一起削掉 —— 扣的只是发给方法的那一份
    assert "per_task" in point and point["per_task"], \
        "curve.jsonl 丢了逐题:平台自己的记录必须完整"


# --------------------------------------------------- 适配器读的是同一份划分 ---

def test_the_rrsi_domain_reads_the_platforms_split_instead_of_assuming_none():
    """适配器以前无条件返回「没有留出集」。

    那句话对数据集是真的(六道题确实没什么可留),对接口是假的:留出集正是 RRSI
    用来防止自己的搜索挑到被计分的那些题上的机制。平台现在带着这份划分,适配器就
    该读它,而不是声明它不存在。
    """
    import importlib.util

    import data.scaffolds.registry as registry
    # 适配器平时是被 RRSI 加载的,`rrsi` 由加载方放进 sys.path。单独 import 它就得
    # 自己补上这一步 —— 否则测的就不是适配器的行为,而是测试环境的缺件。
    #
    # 这里还要把 `rrsi` 从 sys.modules 里清掉:RRSI 的检出根目录下同时有 `rrsi.py`
    # 和 `rrsi/` 包,谁赢取决于哪一个先被 import 并被缓存。单独跑这个文件时包赢,
    # 全套跑时前面的测试先缓存了那个模块,于是这里拿到的是 `rrsi.py`,报
    # "'rrsi' is not a package"。这是检出本身的歧义,不是适配器的问题;测试能做的是
    # 不去猜它,而是明确要求拿到包。
    rrsi_root = ROOT.parent / "rrsi-run"
    if not (rrsi_root / "rrsi" / "domain.py").exists():
        pytest.skip(f"没有 RRSI 的检出:{rrsi_root}")
    for name in [m for m in sys.modules if m == "rrsi" or m.startswith("rrsi.")]:
        del sys.modules[name]
    if str(rrsi_root) in sys.path:
        sys.path.remove(str(rrsi_root))
    sys.path.insert(0, str(rrsi_root))

    import rrsi
    assert hasattr(rrsi, "__path__"), (
        f"`rrsi` 解析成了模块而不是包({rrsi.__file__});"
        f"检出目录里同时有 rrsi.py 和 rrsi/,sys.path 的顺序决定了拿到哪个")

    path = ROOT / "legacy" / "adapters" / "harnessgrad_domain" / "adapter.py"
    spec = importlib.util.spec_from_file_location("hg_domain_adapter", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                                  # noqa: BLE001
        pytest.skip(f"适配器加载不了:{exc}")

    _, _, split = registry.load_split("demo")
    domain = mod.HarnessGradDomain({"dataset": "demo"})
    evolve, heldout = sorted(domain.evolve_ids()), sorted(domain.heldout_ids())

    assert evolve == sorted(split["train"]), \
        "搜索只能在 train 上迭代,否则它会读到 eval 的 trace"
    assert heldout == sorted(split["eval"])
    assert not (set(evolve) & set(heldout)), "两边重叠等于没分"


def test_every_past_rounds_train_traces_are_staged(tmp_path):
    """方法必须能看到**历史**轨迹,不只是当前这一轮的。

    这一条是补的:通道里的 `traces/` 每轮被覆盖成现任的那一份,历史 trace 从来没被
    stage 过 —— 而代码注释里却写着"想要某一轮 trace 的方法可以点名它是哪一轮",那句话
    是假的。实测:一个方法能看到过去每一轮的**分数**和每一个**harness 目录**
    (`states/`),却看不到任何一条过去的 trace。而好几个已发表方法正是靠**跨轮对比**
    来诊断的,所以这是平台悄悄限制了自己能承载哪些方法。

    仍然只给 **train 侧**:eval 侧的行为正是方法不该去拟合的那部分。

    直接调 `_stage_for_method`,不起 driver:方法的输入现在是平台之外的一个临时目录,
    跑完之后就没了,从 run 目录里看不到。测通道的布局就该测那个函数本身。
    """
    from ckpt.git_state import commit_state, init_repo

    work = tmp_path / "work"
    work.mkdir()
    (work / "harness.json").write_text(json.dumps({"name": "p", "version": "1", "entrypoint": "agent.py"}))
    (work / "agent.py").write_text("print('v0')\n")
    init_repo(work)
    shas = [commit_state(work, "H0")]
    (work / "agent.py").write_text("print('v1')\n")
    shas.append(commit_state(work, "round 1"))

    import data.scaffolds.registry as registry
    _, _, split = registry.load_split("demo")
    history = [{"round": 0, "score": 0.0, "train_score": 0.0,
                "identity": {"harness_sha": shas[0], "harness_name": "p",
                             "harness_version": "1"}},
               {"round": 1, "score": 0.0, "train_score": 0.0,
                "identity": {"harness_sha": shas[1], "harness_name": "p",
                             "harness_version": "1"}}]

    # 每一轮的 train trace 由 driver 存在 run 目录里,通道再从那里复制回来
    run_dir = tmp_path / "runs" / "r"
    for r in range(2):
        d = run_dir / f"round{r}_traces"
        d.mkdir(parents=True)
        for tid in split["train"]:
            (d / f"{tid}.jsonl").write_text(json.dumps({"command": "ls"}) + "\n")

    _stage_for_method(work, history[-1], {}, history=history,
                             task_ids=split["train"], run_dir=run_dir)

    staged = work / "_harnessgrad" / "history" / "traces"
    assert staged.is_dir(), f"通道里没有 history/traces:{staged}"
    assert (staged / "round-0").is_dir(), "第 1 轮应当能看到第 0 轮的 trace"
    seen = sorted(p.stem for p in (staged / "round-0").glob("*.jsonl"))
    assert seen == sorted(split["train"]), seen
    # 只给 train 侧,而且通道里任何地方都不该有 eval 的 trace
    assert not (set(seen) & set(split["eval"]))
    for path in (work / "_harnessgrad").rglob("*.jsonl"):
        assert path.stem not in split["eval"], f"{path} 是 eval 侧的 trace"


# ------------------------------------------------- 运行之外的题不能拒掉这次运行 ---

def test_a_task_outside_the_run_cannot_refuse_it():
    """`--tasks` 之外的题不得参与这次运行的门禁。

    一个真实故障:`--side train --tasks break-filter-js-from-html` 被
    `alexgshaw/mteb-leaderboard:20251031` 的缺失拒掉了 —— 那是一道 **eval 侧**的题,
    这次运行根本不会碰它。用户看到的不是"少了一个镜像",而是"我明明只选了一道题,
    它却去跑了别的题",这比拒绝本身更糟。

    原因是收窄只作用在任务清单上:`load_envs` / `load_tasks` 读的是整个数据集,
    `envs`、`setups`、`verifiers` 三个表里都有另一侧的条目,而镜像预检遍历的是
    `envs` 的全部值。
    """
    import eval.containers.container as container

    class _Spec:                                     # 只够 _resolve_envs 用
        kind = "exec"

    def fake_resolve(image):
        if image.startswith("ghost/"):
            raise container.ContainerUnavailable(
                f"image {image!r} is not on the local daemon")
        return f"{image}@sha256:" + "0" * 64

    monkey = pytest.MonkeyPatch()
    monkey.setattr(container, "resolve_digest", fake_resolve)
    try:
        # 一道 train 侧的 files 题,一道 eval 侧的容器题,容器题的镜像拉不下来
        tasks = [{"task_id": "t01"}, {"task_id": "e01"}]
        setups = {"t01": {"files": []}, "e01": {"files": []}}
        verifiers = {"t01": {"kind": "answer", "expected": "x"},
                     "e01": {"kind": "command", "argv": ["true"]}}
        envs = {"t01": {"kind": "files"},
                "e01": {"kind": "exec", "image": "ghost/never-pulled:1"}}

        # 未收窄时,预检确实会去解析那道 eval 题的镜像,于是整次运行被拒 ——
        # 这正是用户遇到的那条拒绝
        _, problem = _resolve_envs(envs)
        assert problem and "e01" in problem, problem
        assert _required_env_kinds(envs) == {"files", "exec"}

        # 收窄到这次真正要跑的 t01 之后,同一份数据集不再能拒掉它
        _, _, envs_run = _narrow_to_run(setups, verifiers, envs, ["t01"])
        assert set(envs_run) == {"t01"}
        assert _required_env_kinds(envs_run) == {"files"}
        resolved, problem = _resolve_envs(envs_run)
        assert problem is None, problem
        assert resolved["t01"] == {"kind": "files"}

        # setups / verifiers 也必须一起收窄,否则另一侧的题仍然留在运行的表里
        setups_run, verifiers_run, _ = _narrow_to_run(
            setups, verifiers, envs, ["t01"])
        assert set(setups_run) == {"t01"} and set(verifiers_run) == {"t01"}
    finally:
        monkey.undo()


def test_the_narrowing_happens_before_the_image_preflight():
    """收窄必须在镜像预检**之前**。顺序反了,收窄就只是装饰。

    这条按源码顺序查,因为这个顺序不是风格问题:预检是那条拒绝的发出者,它看到的
    表是哪一份,决定了运行被不被拒。
    """
    src = (ROOT / "driver.py").read_text(encoding="utf-8")
    narrow = src.index("_narrow_to_run(setups, verifiers, envs, side_ids)")
    gate = src.index("_check_env_kinds(man_at_door, _required_env_kinds(envs))")
    preflight = src.index("envs, env_problem = _resolve_envs(envs)")
    assert narrow < gate < preflight, (
        "收窄必须排在能力门禁和镜像预检之前;现在的顺序是 "
        f"narrow@{narrow} gate@{gate} preflight@{preflight}")
