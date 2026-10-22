"""防篡改清单必须覆盖整棵树 —— 它是手写的,所以需要一条检查盯着它。

`PLATFORM` 是平台的核心主张:一轮运行里,平台"读什么来决定一个数字是什么意思"那些
文件,在被方法碰过之后必须能被发现。它是手写的元组,而手写的"重要的东西"清单会在树
长大的一刻过期。

这不是假设。`improvers/` 就是在这条检查被写出来的时候发现的缺口:它装着**发给改进器的
默认 skill** 和**决定跑哪个改进器的注册表** —— 都是裁判侧的东西,一个方法可以改掉它们
而 `FAILED RUN` 不会响。同一时刻 `eval/run/integrity.py` 里已经有一段长注释解释
`base_harness/` 为什么**故意**不在清单里,却没有一条机制保证"故意"是唯一的漏法。

所以这里测两件事:

  * 每个顶层条目要么被 hash,要么在 `NOT_PLATFORM` 里**带着理由**;
  * `base_harness/` 仍然**不在**清单里 —— 它是候选,每次运行会复制一份再改,把它 hash
    进去会让平台唯一该做的写入变成"篡改"。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import eval.run.integrity as integrity  # noqa: E402
from eval.run.integrity import NOT_PLATFORM, PLATFORM, PLATFORM_ROOT, snapshot  # noqa: E402

#: 运行产物与缓存:由运行本身生成,不是平台源码。和 `runs/` 同类,所以按前缀放行,
#: 不逐个登记 —— 它们的名字带日期/编号,登记不完。
_GENERATED_PREFIXES = ("runs", "__pycache__", ".git", ".pytest_cache",
                      ".scratch", ".ruff_cache", ".overleaf-mcp",
                      # `improver-hg-cli-ask-*.log`: the transcript an improver call dumps
                      # into the directory it was started from. Written *by* a run, named
                      # with a random suffix, and already ignored by `.gitignore`; without
                      # this prefix one improver call leaves the whole classification test
                      # red for whoever runs it next.
                      "improver-hg-cli-ask-")


def _top_level() -> list[str]:
    return sorted(p.name for p in PLATFORM_ROOT.iterdir()
                  if not p.name.startswith(_GENERATED_PREFIXES))


def test_every_top_level_entry_is_classified():
    """新加一个顶层目录/文件时,必须有人写下它属于哪一侧。

    这就是这条检查的全部作用:让"忘了"变成一次失败,而不是一个静默变大的洞。
    """
    unclassified = [name for name in _top_level()
                    if name not in set(PLATFORM) and name not in NOT_PLATFORM]
    assert not unclassified, (
        f"这些顶层条目既不在 PLATFORM 里、也没有理由说明它为什么不在: {unclassified}。"
        f"要么把它加进 PLATFORM(eval/run/integrity.py),要么在 NOT_PLATFORM 里写下理由。")


def test_every_exclusion_carries_a_reason():
    """`NOT_PLATFORM` 是"我决定不查它"的清单 —— 每一条都得说清为什么。"""
    for name, why in NOT_PLATFORM.items():
        # 长度不是重点,"说清了它是什么、以及为什么与测量无关"才是。11 个字符的
        # "the licence" 会被这条挡下 —— 它没告诉读者任何他不知道的事。
        assert len(why.strip()) >= 30, f"{name} 的排除理由太短,等于没写:{why!r}"


def test_the_default_improver_skill_is_protected():
    """**这个具体的缺口**:默认 skill 和"跑哪个改进器"的注册表都在 `improvers/` 下。

    一个方法如果能改掉自己即将收到的那份 skill,它就不是在被测量了。这条直接把
    文件名断言出来,而不是只依赖上面那条"分类"检查 —— 分类对了但内容没被 hash,
    是同一种病的两个阶段。
    """
    snap = snapshot(PLATFORM_ROOT)
    assert "improvers/skill.md" in snap, \
        "默认 skill 没有被 hash:方法可以改掉它即将收到的那份说明"
    assert "improvers/improvers.json" in snap, \
        "改进器注册表没有被 hash:方法可以改掉'跑哪个改进器'"


def test_base_harness_is_still_excluded_on_purpose():
    """它是**候选**。把它 hash 进去会让平台唯一该做的写入变成篡改。"""
    assert "base_harness" not in PLATFORM
    assert "base_harness" in NOT_PLATFORM
    snap = snapshot(PLATFORM_ROOT)
    assert not any(k.startswith("base_harness/") for k in snap), \
        "base_harness/ 里的文件出现在防篡改清单里了 —— 那会让每一次正常运行都判废"


def test_the_implementation_package_is_hashed():
    """**同一个缺口的下一步。** `driver.py` 是入口,规则住在 `harnessgrad/`。

    把实现拆出去的时候,`PLATFORM` 仍然只写着 `driver.py`,而它看起来是完整的 ——
    于是"一轮是什么""曲线点里有什么""哪些失败算平台自己的"这些代码落在了一个没人
    hash 的目录里,一个方法可以改掉裁判的源码而不触发 `FAILED RUN`。

    测试按文件名断言,不只是断言"目录在清单里":目录被 hash 而关键文件被 exclude 掉,
    是同一种病的第二个阶段。
    """
    assert "harnessgrad" in PLATFORM
    snap = snapshot(PLATFORM_ROOT)
    for expected in ("harnessgrad/identity.py", "harnessgrad/records.py",
                     # There is one loop now; `mode_b.py` was deleted when the two modes
                     # became one engine with two implementation shapes.
                     "harnessgrad/loops/mode_a.py"):
        assert expected in snap, \
            f"{expected} 没有被 hash:裁判侧的实现可以被方法改掉而检测不到"


def test_a_snapshot_reuses_the_digest_of_a_file_it_already_read(tmp_path, monkeypatch):
    """`snapshot()` runs around **every method call** and hashes the whole platform.

    Measured 2026-10-19: 20,817 files, 2.4 s per call, twice per call. Reusing a digest
    while the file's identity is unchanged is what makes that affordable; this asserts the
    reuse actually happens rather than that it would be nice if it did.
    """
    monkeypatch.setattr(integrity, "_CACHE_MIN_AGE_S", 0.0)
    (tmp_path / "driver.py").write_text("print(1)\n", encoding="utf-8")
    integrity.reset_cache()
    first = integrity.snapshot(tmp_path)
    misses_after_first = integrity.cache_stats()["miss"]
    second = integrity.snapshot(tmp_path)
    stats = integrity.cache_stats()
    assert first == second
    assert stats["miss"] == misses_after_first, "第二次 snapshot 又把文件重读了一遍"
    assert stats["hit"] >= 1, "缓存没有被用上"


def test_a_rewrite_that_restores_size_and_mtime_is_still_detected(tmp_path, monkeypatch):
    """The one way this cache could hide a change: put the stat fields back afterwards.

    `mtime` can be restored with `os.utime`; `ctime` cannot be set from user space, so the
    identity test still fails and the file is re-hashed. Without `st_ctime_ns` in the
    identity, this test passes the old digest through and a method could edit the scorer
    and re-date it.
    """
    monkeypatch.setattr(integrity, "_CACHE_MIN_AGE_S", 0.0)
    path = tmp_path / "driver.py"
    path.write_text("aaaa", encoding="utf-8")
    before = integrity.snapshot(tmp_path)["driver.py"]
    st = path.stat()
    time.sleep(0.05)                                             # past the coarse clock tick
    path.write_text("bbbb", encoding="utf-8")                    # same size, same path
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))          # put mtime back
    after = integrity.snapshot(tmp_path)["driver.py"]
    assert path.stat().st_size == st.st_size
    assert path.stat().st_mtime_ns == st.st_mtime_ns
    assert after != before, "重写 + 复原 mtime 的文件被缓存当成了没变"


def test_a_digest_taken_moments_ago_is_not_trusted(tmp_path):
    """The coarse-clock window: rewrite and re-date inside one tick, and the cache must miss.

    This is why the cache has a minimum age at all. Without it the previous test's scenario
    reuses a stale digest whenever `ctime` did not advance -- a few milliseconds is enough.
    """
    path = tmp_path / "driver.py"
    path.write_text("aaaa", encoding="utf-8")
    before = integrity.snapshot(tmp_path)["driver.py"]
    st = path.stat()
    path.write_text("bbbb", encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    after = integrity.snapshot(tmp_path)["driver.py"]
    assert after != before, "刚做过的摘要被直接信任了:粗时钟窗口里也能藏住一次改写"


def test_the_cache_never_changes_what_the_snapshot_says(tmp_path, monkeypatch):
    (tmp_path / "driver.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "metrics.py").write_text("y\n", encoding="utf-8")
    integrity.reset_cache()
    cached = integrity.snapshot(tmp_path)
    monkeypatch.setenv("HG_INTEGRITY_NO_CACHE", "1")
    fresh = integrity.snapshot(tmp_path)
    assert cached == fresh, "缓存给出了与无缓存路径不同的摘要"


def test_the_cache_can_be_switched_off(tmp_path, monkeypatch):
    (tmp_path / "driver.py").write_text("z\n", encoding="utf-8")
    integrity.reset_cache()
    integrity.snapshot(tmp_path)
    monkeypatch.setenv("HG_INTEGRITY_NO_CACHE", "1")
    before = integrity.cache_stats()["miss"]
    integrity.snapshot(tmp_path)
    assert integrity.cache_stats()["miss"] > before, "HG_INTEGRITY_NO_CACHE 没有绕开缓存"
