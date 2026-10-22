"""`_harnessgrad/states/`:方法必须拿得到历史状态,否则父代选择规则是装饰。

这一条是平台侧的改动,所以单独钉在这里。没有它的时候,方法只拿到当前 harness 的
文件,`history` 里只有分数 —— 于是 SICA 的置信下界、DGM 的归档、HyperAgents 的子代
惩罚、RRSI 的 frontier 全都退化成同一条无条件爬山,八个方法跑起来一模一样却各自
声称在做别的事。

用 `git archive` 而不是 checkout,是因为 mode A 每轮结束会 `reset_hard`,那些轮次的
提交是**游离对象**;`git archive` 直接读对象库,而且不会动它正在读的工作树。

这里不启动 driver 子进程:那会去比对平台哈希,而开发期间 `methods/` 随时在变,
会把一次正常的测试跑成 `PlatformTampered`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harnessgrad import channel as channel_mod                # noqa: E402
from harnessgrad.channel import _stage_for_method              # noqa: E402
from ckpt.git_state import (commit_state, init_repo, materialize,  # noqa: E402
                            reset_hard)


def _repo(tmp_path: Path, versions: list[str]) -> tuple[Path, list[str]]:
    """A harness git repo with one commit per version, oldest first."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "harness.json").write_text(json.dumps({"name": "probe", "version": "1", "entrypoint": "agent.py"}))
    (work / "agent.py").write_text(versions[0])
    init_repo(work)                                   # commits H0
    shas = [commit_state(work, "H0")]
    for i, body in enumerate(versions[1:], start=1):
        (work / "agent.py").write_text(body)
        shas.append(commit_state(work, f"round {i}"))
    return work, shas


def _history(shas: list[str]) -> list[dict]:
    return [{"round": i, "score": i / 10.0, "train_score": i / 10.0,
             "label": f"round {i}", "identity": {"harness_sha": sha}}
            for i, sha in enumerate(shas)]


def test_every_round_becomes_a_directory_a_method_can_build_on(tmp_path):
    """核心不变量:某一轮的状态必须是一个**完整的 harness 目录**,不是 diff 或 sha。"""
    work, shas = _repo(tmp_path, ["print('v0')\n", "print('v1')\n", "print('v2')\n"])
    history = _history(shas)
    _stage_for_method(work, history[-1], {}, history=history)

    states = work / "_harnessgrad" / "states"
    for i in range(3):
        d = states / f"round-{i}"
        assert (d / "agent.py").read_text() == f"print('v{i}')\n", f"round-{i} 的内容不对"
        assert (d / "harness.json").is_file(), "状态必须是完整 harness,不是若干文件"


def test_the_channel_is_not_copied_into_the_states(tmp_path):
    """`_harnessgrad/` 是通道不是 harness。被复制进去的话,它就成了一个会被提交的文件,
    而一个 no-op 轮次会因此铸出新的 sha,控制曲线看起来在动。"""
    work, shas = _repo(tmp_path, ["print('v0')\n", "print('v1')\n"])
    history = _history(shas)
    _stage_for_method(work, history[-1], {}, history=history)
    for i in range(2):
        assert not (work / "_harnessgrad" / "states" / f"round-{i}" / "_harnessgrad").exists()


def test_the_index_says_what_exists_and_where(tmp_path):
    """方法读索引,不猜路径 —— 索引里没有的状态就是没被 materialize 的状态。"""
    work, shas = _repo(tmp_path, ["print('v0')\n", "print('v1')\n"])
    history = _history(shas)
    _stage_for_method(work, history[-1], {}, history=history)

    index = json.loads(
        (work / "_harnessgrad" / "states" / "index.json").read_text())
    assert index["kept"] == ["round-0", "round-1"]
    assert index["available"] == 2 and index["window"] == channel_mod.STATES_KEPT
    entry = index["round-1"]
    assert entry["sha"] == shas[1] and entry["files"] >= 2
    assert Path(entry["harness_dir"]).is_dir()


def test_the_window_is_capped_and_says_so(tmp_path, monkeypatch):
    """长跑不能无限膨胀通道。窗口封顶,并且把"总共有多少、留下了哪些"写进索引。"""
    monkeypatch.setattr(channel_mod, "STATES_KEPT", 2)
    work, shas = _repo(tmp_path, [f"print('v{i}')\n" for i in range(5)])
    history = _history(shas)
    _stage_for_method(work, history[-1], {}, history=history)

    states = work / "_harnessgrad" / "states"
    index = json.loads((states / "index.json").read_text())
    assert index["available"] == 5 and index["window"] == 2
    assert index["kept"] == ["round-3", "round-4"], "留下的应当是最新的那两轮"
    assert not (states / "round-0").exists()


def test_a_state_that_cannot_be_materialized_is_absent_from_the_index(tmp_path):
    """索引里列一个不存在的目录,方法会以为平台给了它一个空 harness,然后报一个
    其实是平台造成的失败。宁可索引里没有。"""
    work, shas = _repo(tmp_path, ["print('v0')\n", "print('v1')\n"])
    history = _history(shas)
    history.append({"round": 2, "score": 0.2,
                    "identity": {"harness_sha": "0" * 40}})   # 对象库里没有
    _stage_for_method(work, history[-1], {}, history=history)

    states = work / "_harnessgrad" / "states"
    index = json.loads((states / "index.json").read_text())
    assert "round-2" not in index["kept"]
    assert "round-2" not in index
    assert not (states / "round-2").exists()


def test_materialize_reads_dangling_commits(tmp_path):
    """mode A 每轮结束 `reset_hard`,于是轮次提交变成游离对象。`git archive` 仍然
    读得到它们,而 `git checkout` 那套会把这些状态弄丢。"""
    work, shas = _repo(tmp_path, ["print('v0')\n", "print('v1')\n"])
    # 用平台自己的 `reset_hard`,不用裸 `git reset --hard`:身份现在是 **tree** 而不是
    # commit,而 `git reset --hard <tree>` 会直接报 "is a tree, not a commit"。这条测试
    # 要断言的是"游离状态仍然能被 materialize",不是"某个 git 动词能用"。
    reset_hard(work, shas[0])

    dest = tmp_path / "extracted"
    count = materialize(work, shas[1], dest)
    assert count >= 2
    assert (dest / "agent.py").read_text() == "print('v1')\n"


def test_the_default_skill_travels_in_the_channel(tmp_path):
    """方法的沙箱把平台藏起来,所以它读不到 `improvers/skill.md`。

    自带 skill 的方法不受影响;**恰恰是没有自带 skill 的那个方法**需要这一份,而它
    以前只能去平台树里读 —— 在沙箱里那是"文件不存在",所以默认 skill 从来没到过
    改进器手里。通道是"方法可以读什么"的清单,所以它跟着通道走。
    """
    work, shas = _repo(tmp_path, ["print('v0')\n"])
    _stage_for_method(work, _history(shas)[-1], {}, history=_history(shas))
    staged = work / "_harnessgrad" / "SKILL.md"
    assert staged.is_file(), "默认 skill 没有进通道"
    platform_copy = ROOT / "improvers" / "skill.md"
    assert staged.read_text() == platform_copy.read_text()


def test_the_instruments_own_state_never_moves_the_harness_identity(tmp_path):
    """**钉原则,不是钉清单。** 一切非 harness 代码的文件都属于仪器自身,不得进入身份。

    `content_sha` 是一棵 git 树的哈希,所以任何被 commit 进去的文件都是 harness 的
    **一部分**。于是"一轮什么也没改"只要多出一个非 harness 文件,就会铸出一个新的
    `harness_sha`;而缓存键是 `(harness_sha, task_id)`,那一轮会被**重新测量**,曲线上
    出现没有任何方法做过的移动。

    实测(`v10-dgm`,2026-10-05):improver 在 3 行的 `agent.py` 编辑旁写了 `agent.py.bak`
    (635 行),备份被 commit,那一轮的 diff 与 sha 把两个文件都报了出来。旧的四条清单
    (`_harnessgrad/`、`__pycache__/`、`*.pyc`、`.state/`)是同一原则的两个实例,漏掉了
    这一类 —— 这条测试把整类钉住。

    行为测试而不是字符串断言:它真的建 repo、真的写这些文件、真的比较 sha。
    """
    from ckpt.git_state import IGNORED_HARNESS_STATE, content_sha  # noqa: PLC0415

    work, _ = _repo(tmp_path, ["print('v0')\n"])
    before = content_sha(work)

    # The improver's own bookkeeping: `agent.py.bak` is the measured instance.
    for junk in ("agent.py.bak", "agent.py.orig", "agent.py.rej", "agent.py~"):
        (work / junk).write_text("the improver's own copy of the file it edited\n")
    # The two the platform was first bitten by, kept in the same test so the class is one
    # rule rather than two lists that can drift apart.
    (work / "__pycache__").mkdir(exist_ok=True)
    (work / "__pycache__" / "agent.cpython-312.pyc").write_text("bytecode\n")
    (work / ".state").mkdir(exist_ok=True)
    (work / ".state" / "cache.json").write_text("{}\n")

    after = commit_state(work, "a round that changed nothing")
    assert after == before, (
        "the instrument's own state moved the harness's identity, so a no-op round would be "
        "re-measured and the curve would show movement no method made")

    # The negative control: harness *code* still moves it, so the rule cannot be satisfied by
    # ignoring everything.
    (work / "agent.py").write_text("print('v0')\n# a real edit\n")
    assert commit_state(work, "a round that changed the harness") != before

    # And every rule the constant names is really in the file git reads.
    written = (work / ".gitignore").read_text()
    missing = [rule for rule in IGNORED_HARNESS_STATE if rule not in written]
    assert not missing, f"rules named in the constant but not written to .gitignore: {missing}"
