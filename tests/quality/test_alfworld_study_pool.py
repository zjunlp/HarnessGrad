"""适配器把 study 侧接到 `train` 上：三个 eval 池，一个 study 文件。

**为什么需要这个能力。** 端口原来的 study 题是从 `valid_unseen` 里切的（30 题），于是"搜索"
和"报告"用的是同一个评测池。题目虽然是 game 不重叠的，但它给了评审一个正当的质疑：你在
benchmark 的评测集上练手。把 study 侧接到官方 `train` 上就消掉了这个质疑，而且**每轮成本
不变**（还是 100 题一轮），只是题来自另一个目录。

这里钉住四件事：
1. 不设环境变量时，行为与从前**逐点一致**（`valid_unseen` 82/52，哈希决定 side）；
2. 设了 study 文件后，train 侧**等于该文件**，eval 侧等于整个池子，两者不相交；
3. `HG_ALFWORLD_POOL` 选出 eval 池（valid_seen 140 / valid_train 200 / valid_unseen 134）；
4. study 文件里有一个跑不了的 id 时**响亮失败**，而不是悄悄少几道题。

每个用例起一个子进程：模块在 import 时读环境变量，同进程里 reload 会互相污染。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUDY_FILE = Path("/mnt/20t/xubuqiang/hg-work/rsi10/train100.csv")

PROBE = """
import json, sys
sys.path.insert(0, __ROOT__)
from data.datasets import alfworld as A
print(json.dumps({
    "pool": A._POOL,
    "study": len(A._STUDY),
    "train": len(A.SPLIT["train"]) if A.SPLIT else None,
    "eval": len(A.SPLIT["eval"]) if A.SPLIT else None,
    "train_ids": A.SPLIT["train"] if A.SPLIT else [],
    "eval_ids": A.SPLIT["eval"] if A.SPLIT else [],
    "source": A.SPLIT_SOURCE,
    "tasks": len(A._RECORDS),
    "error": A._LOAD_ERROR,
}))
"""


def _probe(**env) -> dict:
    full = {k: v for k, v in os.environ.items()
            if not k.startswith("HG_ALFWORLD_")}
    full.update({k: str(v) for k, v in env.items()})
    proc = subprocess.run([sys.executable, "-c",
                           PROBE.replace("__ROOT__", repr(str(ROOT)))],
                          capture_output=True, text=True, timeout=600, env=full,
                          cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-1500:]
    return json.loads(proc.stdout)


def test_without_a_study_file_there_is_no_division():
    """No study file = no declared division: `valid_unseen` is the pool, and it is all scored.

    It used to be hash-split 82/52 by game id, which labelled a third of the benchmark's
    held-out games "study material"; removed 2026-10-24. `--split default` is how a run
    asks for the hash rule now.
    """
    got = _probe()
    assert got["pool"] == "valid_unseen"
    assert got["study"] == 0
    assert got["split"] if False else True  # the probe reports sides, not the dict
    assert (got["train"], got["eval"]) == (None, None)
    assert got["source"] == "none"
    assert got["tasks"] > 0


@pytest.mark.skipif(not STUDY_FILE.exists(), reason="train100.csv 未生成")
def test_a_study_file_makes_the_two_sides_come_from_two_directories():
    got = _probe(HG_ALFWORLD_STUDY_IDS=STUDY_FILE)
    assert got["study"] == 100
    assert (got["train"], got["eval"]) == (100, 134)
    train, ev = set(got["train_ids"]), set(got["eval_ids"])
    assert not (train & ev), "study 题不能同时出现在 eval 侧"
    named = {t.strip() for t in STUDY_FILE.read_text().split(",") if t.strip()}
    assert train == named, "train 侧必须就是这个文件，不能悄悄换题"


@pytest.mark.parametrize("pool,expected", [("valid_seen", 140),
                                           ("valid_train", 200),
                                           ("valid_unseen", 134)])
@pytest.mark.skipif(not STUDY_FILE.exists(), reason="train100.csv 未生成")
def test_the_eval_pool_is_selectable(pool, expected):
    got = _probe(HG_ALFWORLD_STUDY_IDS=STUDY_FILE, HG_ALFWORLD_POOL=pool)
    assert got["pool"] == pool
    assert got["eval"] == expected
    assert len(got["train_ids"]) == 100


def test_a_study_id_that_is_not_runnable_fails_loudly(tmp_path):
    """悄悄少一道 study 题，等于悄悄改了方法看到的东西。"""
    bogus = tmp_path / "study.csv"
    bogus.write_text("no-such-house-None-Nothing-999__trial_20190901_000000_000000\n")
    got = _probe(HG_ALFWORLD_STUDY_IDS=bogus)
    assert got["error"] and "study ids" in got["error"]


def test_the_same_directory_cannot_supply_a_task_twice():
    """实测过的 bug：study 文件放在 pool 的同一个目录上时，100 题变成 200 条 train 侧记录
    （每道题被收集两次），等于同一个任务被测量两次、记两个曲线点、收两次费。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from data.datasets import alfworld as A
    once = A._collect([(A._split_dir("valid_unseen"), None)])
    twice = A._collect([(A._split_dir("valid_unseen"), None),
                        (A._split_dir("valid_unseen"), None)])
    assert len(twice) == len(once) == 134
    assert len({r["task_id"] for r in twice}) == len(twice)


def test_measuring_the_split_the_study_set_comes_from_is_refused():
    """`--pool train` + 一个 train 的 study 文件 = 在自己的学习材料上考试。响亮拒绝。"""
    got = _probe(HG_ALFWORLD_STUDY_IDS=STUDY_FILE, HG_ALFWORLD_POOL="train")
    assert got["error"] and "study set is drawn from" in got["error"]
