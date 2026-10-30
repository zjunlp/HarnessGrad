"""harness 自己的依赖,从任务镜像之外提供。§2.5.3 与 §2.5.6 之间那个缺口。

那次故障的形状
--------------
`loop` + Terminal-Bench:镜像自带 `/usr/local/bin/python3`,平台因此**不**注入自己的
解释器和包(§2.5.3 是对的,它想保住"解释器是镜像的"),而镜像里没有 `openai`。于是

    base_harness/loop/agent.py:105  from openai import OpenAI
    ModuleNotFoundError      exit 1      trace 0 字节      score 0.000

而记录里唯一说出来的话是 `RRSI rule: no edit` —— 和一只决定不改的 harness 一模一样。

为什么不是"烤进镜像",也不是"换平台解释器"
------------------------------------------
烤进去是 `O(#任务镜像)`:Terminal-Bench 89 个,别人自带一个 harness 就得先付这个代价。
换平台解释器要改 §2.5.9,还会削弱唯一能抓"平台偷偷在宿主上跑了"的那个测试。

这个方案是 `O(#Python 版本)`,而版本数是**数据集**的属性、不是 harness 的:实测
Terminal-Bench 是 5(3.13×41、3.12×13、3.11×3、3.10×1、3.9×2,另有 26 个镜像没有解释器、
本来就不需要)。任务集变大,这个数字不动。

这里钉住五件事:
  * 键是 `(install 文件内容, Python 小版本)`,而且没配过时**一次探测都不做**
  * 记录的三种形态,第三种(声明了却没配)是过去完全不可见的那个
  * 导入名的推导来自 wheel 必写的 `RECORD`,不是只有 setuptools 才写的 `top_level.txt`
  * overlay 是 bind mount,`docker commit` 不会把它的内容带进被判分的快照
  * `files` 那半:键是 PATH 上的 `python`,探针**不带** site-packages(否则宿主的包会
    替一份坏 overlay 通过),而它遮住宿主的包、不排除它们,这一点记在点子上
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import eval.containers.harness_runtime as harness_runtime                   # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Every test gets its own overlay root and a cold in-process cache."""
    monkeypatch.setenv("HG_OVERLAY_ROOT", str(tmp_path / "overlays"))
    monkeypatch.setattr(harness_runtime, "_VERSION_CACHE", {}, raising=False)
    harness_runtime._VERSION_CACHE.clear()


def _harness(root: Path, *, install: str | None = "requirements.txt",
             body: str = "openai>=1.0\n", extra: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"name": root.name, "version": "0.1.0", "path": ".",
                "entrypoint": "agent.py", "env_kinds": ["files", "exec"]}
    if install:
        manifest["install"] = install
        (root / install).write_text(body, encoding="utf-8")
    manifest.update(extra or {})
    (root / "harness.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "agent.py").write_text("print('hi')\n", encoding="utf-8")
    return root


def _fake_overlay(tmp_path: Path, repo: Path, version: str,
                  packages: dict | None = None, host: bool = False) -> Path:
    install = harness_runtime.install_spec(repo)
    path = harness_runtime.overlay_path(install, version)
    path.mkdir(parents=True, exist_ok=True)
    (path / "PROVENANCE.json").write_text(json.dumps({
        "harness": repo.name, "install": install[1], "install_sha256": install[0],
        "python": version, "built_from_image": None if host else "example/img:1",
        "host_site_packages_visible": host,
        "packages": packages if packages is not None else {"openai": {"version": "3.0"}},
    }), encoding="utf-8")
    return path


# ------------------------------------------------------------- 键与声明 ---

def test_a_harness_with_no_install_declares_nothing(tmp_path):
    assert harness_runtime.install_spec(_harness(tmp_path / "bare", install=None)) is None


def test_a_missing_manifest_is_not_an_error(tmp_path):
    (tmp_path / "empty").mkdir()
    assert harness_runtime.install_spec(tmp_path / "empty") is None


def test_a_shell_install_is_refused_rather_than_guessed_at(tmp_path):
    """`install` 也可以是一个 `.sh`。那是任意代码,烤进镜像;不能变成一个目录。"""
    repo = _harness(tmp_path / "shell", install="install.sh", body="pip install x\n")
    assert harness_runtime.install_spec(repo) is None


def test_the_key_is_the_file_contents_not_the_name(tmp_path):
    """改了 pin 就是另一个键 —— 否则会静默复用为旧 pin 建的那份。"""
    a = _harness(tmp_path / "a", body="openai>=1.0\n")
    b = _harness(tmp_path / "b", body="openai>=1.0\n")
    c = _harness(tmp_path / "c", body="openai>=2.0\n")
    assert harness_runtime.install_spec(a)[0] == harness_runtime.install_spec(b)[0], \
        "相同依赖应当共用一份 overlay"
    assert harness_runtime.install_spec(a)[0] != harness_runtime.install_spec(c)[0]


def test_the_key_does_not_depend_on_the_python_version(tmp_path):
    """版本是 overlay_path 的第二段,不是键的一部分:一份键对应多个版本。"""
    repo = _harness(tmp_path / "k")
    install = harness_runtime.install_spec(repo)
    assert harness_runtime.overlay_path(install, "3.13") \
        != harness_runtime.overlay_path(install, "3.12")


# --------------------------------------------- 没配过时一次探测都不做 ---

def test_lookup_costs_nothing_when_nothing_is_configured(tmp_path):
    """没人配过 overlay 的用户,不该为这个功能付出任何代价。

    这条是承重的:每次运行 89 个镜像各起一个容器只为问一句 Python 版本,是真实的
    开销。`lookup` 先看目录存不存在,存在才去问解释器 —— 所以传一个根本不存在的
    解释器路径也必须安静地返回 None,而不是抛。
    """
    repo = _harness(tmp_path / "h")
    assert harness_runtime.lookup(repo, "/nonexistent/python3") is None


def test_lookup_returns_none_for_a_version_that_was_not_built(tmp_path):
    repo = _harness(tmp_path / "h2")
    _fake_overlay(tmp_path, repo, "3.13")
    # 让探测直接给出 3.12,不真的起容器
    orig = harness_runtime.interpreter_version
    harness_runtime.interpreter_version = lambda *a, **k: "3.12"
    try:
        assert harness_runtime.lookup(repo, "python3") is None
    finally:
        harness_runtime.interpreter_version = orig


def test_lookup_finds_the_overlay_for_the_matching_version(tmp_path):
    repo = _harness(tmp_path / "h3")
    path = _fake_overlay(tmp_path, repo, "3.13")
    orig = harness_runtime.interpreter_version
    harness_runtime.interpreter_version = lambda *a, **k: "3.13"
    try:
        got = harness_runtime.lookup(repo, "python3")
    finally:
        harness_runtime.interpreter_version = orig
    assert got and got["path"] == path and got["python"] == "3.13"
    assert got["provenance"]["packages"] == {"openai": {"version": "3.0"}}


# --------------------------------------------------------- 环境与记录 ---

def test_env_for_prepends_to_an_existing_pythonpath(tmp_path, monkeypatch):
    """harness 自己设的 PYTHONPATH 不是平台能覆盖的。"""
    monkeypatch.setenv("PYTHONPATH", "/already/there")
    overlay = {"path": tmp_path / "ovl"}
    value = harness_runtime.env_for(overlay)["PYTHONPATH"]
    assert value.split(os.pathsep) == [str(tmp_path / "ovl"), "/already/there"]


def test_env_for_is_empty_without_an_overlay():
    assert harness_runtime.env_for(None) == {}


def test_extra_paths_is_the_bind_the_sandbox_and_container_both_use(tmp_path):
    assert harness_runtime.extra_paths(None) == ()
    assert harness_runtime.extra_paths({"path": tmp_path / "o"}) == (tmp_path / "o",)


def test_the_record_says_where_the_dependencies_came_from(tmp_path):
    """三种形态,第三种是过去完全不可见的那个。"""
    repo = _harness(tmp_path / "h4")
    _fake_overlay(tmp_path, repo, "3.13")
    orig = harness_runtime.interpreter_version
    harness_runtime.interpreter_version = lambda *a, **k: "3.13"
    try:
        overlay = harness_runtime.lookup(repo, "python3")
    finally:
        harness_runtime.interpreter_version = orig

    with_overlay = harness_runtime.record(overlay)
    assert with_overlay["source"] == "overlay"
    assert with_overlay["python"] == "3.13"
    assert with_overlay["built_from_image"] == "example/img:1"

    declared = harness_runtime.record(None)
    assert declared["source"] == "declared"
    assert "configure_harness.py" in declared["remedy"], \
        "说清楚缺什么,却不说怎么补,等于让人自己去猜 §2.5.6"

    # 「跑的是平台提供的解释器」与「兜底」不是一回事:只有镜像**没有**解释器时,平台
    # 才会注入自己的,也只有那时它的包才是依赖的来源。镜像自带解释器却没有 overlay 的
    # 情况**没有兜底** —— 那正是 `declared` 这一形态要指出的。
    platform = harness_runtime.record(None, platform_interpreter=True)
    assert platform["source"] == "platform"
    assert declared["source"] == "declared", "镜像自带解释器时不该说成 platform"


# ---------------------------------- 导入名来自 RECORD,不是 top_level.txt ---

def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "_configure_harness", ROOT / "tools" / "configure_harness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_import_names_come_from_record_not_top_level(tmp_path):
    """实测:一份真的 `openai` 安装里,**14 个发行版只有 3 个**写 `top_level.txt`。

    从 `top_level.txt` 推导,探针就只会导入 `anyio`、`h11`、`sniffio`,然后宣布这份
    overlay 是好的 —— 恰好跳过 `pydantic_core` 和 `jiter`,而它们是编译扩展,是唯一
    可能因为 ABI 不匹配而失败的东西。所以必须用 wheel 强制要求的 `RECORD`。
    """
    tool = _load_tool()
    overlay = tmp_path / "ovl"
    overlay.mkdir()

    modern = overlay / "somepkg-1.2.3.dist-info"
    modern.mkdir()
    (modern / "METADATA").write_text("Name: somepkg\nVersion: 1.2.3\n", encoding="utf-8")
    # 没有 top_level.txt —— 现代构建后端都不写
    (modern / "RECORD").write_text(
        "somepkg/__init__.py,sha256=x,10\n"
        "somepkg/_native.cpython-313-x86_64-linux-gnu.so,sha256=y,20\n"
        "somepkg-1.2.3.dist-info/METADATA,sha256=z,30\n"
        "somepkg-1.2.3.dist-info/RECORD,,\n"
        "../../bin/somepkg,sha256=w,5\n", encoding="utf-8")

    scripts_only = overlay / "justaclient-0.1.dist-info"
    scripts_only.mkdir()
    (scripts_only / "METADATA").write_text("Name: justaclient\nVersion: 0.1\n",
                                           encoding="utf-8")
    (scripts_only / "RECORD").write_text("../../../bin/justaclient,sha256=q,5\n",
                                         encoding="utf-8")

    found = tool._top_levels(overlay)
    assert found["somepkg"]["top_level"] == ["somepkg"], found
    assert found["somepkg"]["version"] == "1.2.3"
    assert found["justaclient"]["top_level"] == [], \
        "只有脚本的发行版没有可导入的模块,记成空列表而不是猜一个"


def test_a_single_module_distribution_is_detected(tmp_path):
    tool = _load_tool()
    overlay = tmp_path / "ovl2"
    (overlay / "single-1.0.dist-info").mkdir(parents=True)
    (overlay / "single-1.0.dist-info" / "METADATA").write_text(
        "Name: single\nVersion: 1.0\n", encoding="utf-8")
    (overlay / "single-1.0.dist-info" / "RECORD").write_text(
        "single.py,sha256=a,1\n", encoding="utf-8")
    assert tool._top_levels(overlay)["single"]["top_level"] == ["single"]


# ------------------------------------- overlay 不进被判分的快照(wire 层) ---

def _a_local_image() -> str | None:
    for candidate in ("harnessgrad-task-loop:demo",
                      "alexgshaw/break-filter-js-from-html:20251031"):
        proc = subprocess.run(["docker", "image", "inspect", candidate],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            return candidate
    return None


def test_the_overlay_cannot_reach_the_graded_snapshot(tmp_path):
    """overlay 是 bind mount,而 `docker commit` 不提交挂载的内容。

    这条不是风格问题。容器状态下,harness 停下来的那一刻整个容器被 commit 成快照,
    校验器从一个**全新容器**里跑 —— 如果挂载的内容进了快照,harness 的依赖就会出现在
    验证环境里,而验证器的解释器是镜像的。这是实测的,不是推理的。
    """
    image = _a_local_image()
    if not image:
        pytest.skip("no local image to build a container from")
    mount = tmp_path / "overlay"
    mount.mkdir()
    (mount / "proof.txt").write_text("sentinel\n", encoding="utf-8")
    name = "hg-test-overlay-commit"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
    subprocess.run(["docker", "rmi", "-f", "hg-test-overlay-snap"], capture_output=True,
                   timeout=60)
    try:
        subprocess.run(["docker", "create", "--name", name, "--network", "none",
                        "-v", f"{mount}:/overlay:ro", "--entrypoint", "sh", image,
                        "-c", "sleep 60"], capture_output=True, text=True, timeout=120,
                       check=True)
        subprocess.run(["docker", "start", name], capture_output=True, timeout=120,
                       check=True)
        subprocess.run(["docker", "commit", name, "hg-test-overlay-snap"],
                       capture_output=True, timeout=300, check=True)
        proc = subprocess.run(["docker", "run", "--rm", "--network", "none",
                               "--entrypoint", "sh", "hg-test-overlay-snap", "-c",
                               "[ -e /overlay/proof.txt ] && echo PRESENT || echo ABSENT"],
                              capture_output=True, text=True, timeout=120)
        assert "ABSENT" in proc.stdout, (
            "bind mount 的内容进了快照 —— harness 的依赖会出现在校验器运行的环境里")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=120)
        subprocess.run(["docker", "rmi", "-f", "hg-test-overlay-snap"],
                       capture_output=True, timeout=120)


# ------------------------------------------------ host overlays: the `files` 那半 ---
#
# `files` 运行没有镜像:`eval/run/runner.py:611` 启动的是 PATH 上的裸名 `python`,于是
# harness 的 import 落到**那个**解释器自己的 site-packages 上 —— 没钉住、没声明、没记录。
# 实测 `loop` 声明了 `openai>=1.0`,在 ALFWorld 上能跑只是因为宿主 anaconda 恰好有它。

def _host_version() -> str:
    return harness_runtime.interpreter_version(sys.executable) or "3.12"


def test_the_host_interpreter_is_the_one_the_run_launches():
    """键必须是 PATH 上 `python` 的版本,不是 `sys.executable` 的。

    两者通常相同,但不是必须:一个 activated venv 就会让它们不同,而键一旦不同,建出来
    的 overlay 永远不会被 `lookup` 查到 —— 花了 40 秒,换来一份没人用的目录。
    """
    tool = _load_tool()
    found = tool._host_interpreter("python")
    assert found is not None and found.is_absolute()
    assert found == Path(shutil.which("python")).absolute()


def test_a_venv_symlink_is_not_followed(tmp_path):
    """venv 的 `bin/python` 是符号链接,而它的 site-packages 在**链接旁边**,不在目标旁边。

    跟着链接走,版本键仍然是对的,但跑的 pip 是基础解释器的那个:装出来的东西和运行时
    看到的不是同一个环境,而这正是"看起来配好了"的那类错误。
    """
    tool = _load_tool()
    link = tmp_path / "venv" / "bin" / "python"
    link.parent.mkdir(parents=True)
    link.symlink_to(sys.executable)
    assert tool._host_interpreter(str(link)) == link


def test_a_wrong_version_is_refused_before_pip_runs(tmp_path):
    """键错了比没有 overlay 更糟:它能导入,直到某一天不能。所以先问解释器,再装。"""
    tool = _load_tool()
    repo = _harness(tmp_path / "hv")
    install = harness_runtime.install_spec(repo)
    with pytest.raises(SystemExit, match="key would be wrong"):
        tool.build_one_host(repo, install, "9.9", Path(sys.executable))
    assert not harness_runtime.overlay_path(install, "9.9").exists(), \
        "拒绝了版本,就不要留下一个会被读成『已建好』的空目录"


def test_the_host_probe_is_hermetic(tmp_path):
    """宿主本来就装了这些包,所以带上 site-packages 的探针会把坏的 overlay 判成好的。

    造一份声明了 `pytest` 而实际没有它的 overlay。宿主的 site-packages 里有 `pytest`,
    一个不隔离的探针会通过;隔离的那个必须失败 —— 这就是这条测试存在的全部理由。
    """
    tool = _load_tool()
    repo = _harness(tmp_path / "hh")
    install = harness_runtime.install_spec(repo)
    version = _host_version()
    overlay = harness_runtime.overlay_path(install, version)
    overlay.mkdir(parents=True)
    (overlay / "PROVENANCE.json").write_text(json.dumps({
        "install_sha256": install[0], "python": version,
        "host_site_packages_visible": True,
        "packages": {"pytest": {"version": "0", "top_level": ["pytest"]}},
    }), encoding="utf-8")
    assert importlib.util.find_spec("pytest") is not None, \
        "这条测试的前提是宿主真的装了 pytest"
    ok, note = tool.verify_one_host(repo, install, version, Path(sys.executable))
    assert not ok and "pytest" in note, note


def test_a_host_overlay_that_provides_its_module_passes_the_probe(tmp_path):
    """正向的一半:overlay 里真有的模块,探针要说它来自 overlay,而不只是"导入了"。"""
    tool = _load_tool()
    repo = _harness(tmp_path / "hp")
    install = harness_runtime.install_spec(repo)
    version = _host_version()
    overlay = harness_runtime.overlay_path(install, version)
    (overlay / "hgprobe_pkg").mkdir(parents=True)
    (overlay / "hgprobe_pkg" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (overlay / "PROVENANCE.json").write_text(json.dumps({
        "install_sha256": install[0], "python": version,
        "host_site_packages_visible": True,
        "packages": {"hgprobe-pkg": {"version": "0.1", "top_level": ["hgprobe_pkg"]}},
    }), encoding="utf-8")
    ok, note = tool.verify_one_host(repo, install, version, Path(sys.executable))
    assert ok and "import from the overlay" in note, note


def test_the_record_says_whether_the_hosts_packages_are_still_visible(tmp_path):
    """`files` 的 overlay **遮蔽**宿主的包,不**排除**它们。

    requirements 没点名的依赖还是从宿主来 —— 这一条必须记在点子上,否则两个人都以为
    "依赖已经钉住了",而实际上只钉住了其中一半。
    """
    # 两份不同的 requirements:键是**内容**,两份一样的会共用同一个 overlay 目录,
    # 于是第二次写 PROVENANCE 会盖掉第一次,测的就不再是两件事了。
    image_built = _harness(tmp_path / "hi", body="openai>=1.0\n")
    _fake_overlay(tmp_path, image_built, "3.13")
    host_built = _harness(tmp_path / "hb", body="openai>=2.0\n")
    _fake_overlay(tmp_path, host_built, "3.13", host=True)

    orig = harness_runtime.interpreter_version
    harness_runtime.interpreter_version = lambda *a, **k: "3.13"
    try:
        assert harness_runtime.record(
            harness_runtime.lookup(image_built, "python3"))["host_site_packages_visible"] \
            is False
        assert harness_runtime.record(
            harness_runtime.lookup(host_built, "python3"))["host_site_packages_visible"] \
            is True
    finally:
        harness_runtime.interpreter_version = orig


def test_a_platform_run_records_which_interpreter_it_used():
    """过去这里恒为 null,而它恰好是两个都是 `platform` 的点唯一不同的地方。"""
    assert harness_runtime.record(None, platform_interpreter=True)["python"] is None
    assert harness_runtime.record(None, platform_interpreter=True,
                                  interpreter="3.12")["python"] == "3.12"


def test_a_files_run_is_told_about_its_missing_overlay(tmp_path, capsys):
    """`files` 过去被直接跳过 —— 它通常能跑,所以没人发现依赖从来没被钉住过。

    两种环境的修法不同:`exec` 是 `--dataset`(按镜像的 Python 版本建),
    `files` 是那个解释器本身(默认就是宿主),所以两条消息不能共用一个命令。
    """
    import harnessgrad.environments as environments
    repo = _harness(tmp_path / "hw")
    environments._warn_missing_overlay(repo, {"t": {"kind": "files"}}, "alfworld")
    files_note = capsys.readouterr().out
    assert "configure_harness.py" in files_note and "--harness" in files_note
    assert "--dataset" not in files_note

    environments._warn_missing_overlay(
        repo, {"t": {"kind": "exec", "image": "example/img:1"}}, "terminal_bench")
    exec_note = capsys.readouterr().out
    assert "--dataset" in exec_note


def test_surfacing_follows_the_plan_not_the_path(tmp_path):
    """overlay 的根是**用户选择**(`HG_OVERLAY_ROOT`),而本平台自己的测试就指在 `/tmp`。

    `runtime_paths` 在 hides **之前**绑定 —— 这是对的:harness 不能靠声明把平台要回来。
    但 overlay 不是 harness 声明的,它是平台自己的目录,藏起来就没有意义了。所以问 plan
    它盖住了什么,而不是从路径形状猜。
    """
    import eval.containers.sandbox as sandbox
    work, platform = tmp_path / "work", tmp_path / "platform"
    work.mkdir()
    platform.mkdir()
    hidden = sandbox.hidden_trees(work, platform)
    inside = platform / "overlays" / "3.12"
    assert harness_runtime.surfaced_paths({"path": inside}, work_root=work,
                                          platform=platform) == (inside,)
    # 一个 plan 不盖住的目录:断言它确实不在这份 hidden 集合里,而不是假设。
    outside = Path("/usr/share/hg-overlay-not-here")
    assert not any(outside == tree or tree in outside.parents for tree in hidden)
    assert harness_runtime.surfaced_paths({"path": outside}, work_root=work,
                                          platform=platform) == ()


def test_the_tool_defaults_to_the_host_instead_of_demanding_an_image(tmp_path, capsys,
                                                                   monkeypatch):
    """默认分支过去要求 `--builder-image`,于是 `files` 的 harness 根本没法配置。

    现在默认就是"用宿主自己的 pip ":它是 `files` 运行真正会启动的解释器,也是改 harness
    的人手里有的那个。镜像仍然可以点名,那是 `exec` 的 overlay。
    """
    tool = _load_tool()
    repo = _harness(tmp_path / "hd")
    monkeypatch.delenv("HG_OVERLAY_BUILDER_IMAGE", raising=False)
    monkeypatch.setattr(tool, "_host_interpreter", lambda name: Path("/fake/python"))
    monkeypatch.setattr(tool, "_host_has_pip", lambda python: True)
    monkeypatch.setattr(tool.runtime, "interpreter_version", lambda *a, **k: "3.12")
    seen: dict = {}

    def fake_build(harness, install, version, python):
        seen.update(version=version, python=python)
        raise SystemExit("stop before pip")

    monkeypatch.setattr(tool, "build_one_host", fake_build)
    monkeypatch.setattr(sys, "argv", ["configure_harness.py", "--harness", str(repo)])
    assert tool.main() == 1, "建失败时退出码是 1,但**已经走到了**建这一步"
    assert seen == {"version": "3.12", "python": Path("/fake/python")}, seen
    assert "host interpreter /fake/python" in capsys.readouterr().out


def test_python_that_is_not_the_host_needs_an_image(tmp_path, capsys, monkeypatch):
    """`--python 3.11` 在 3.12 的宿主上不能靠猜:**键错了比没有 overlay 更糟**。"""
    tool = _load_tool()
    repo = _harness(tmp_path / "hm")
    monkeypatch.setattr(tool, "_host_interpreter", lambda name: Path("/fake/python"))
    monkeypatch.setattr(tool.runtime, "interpreter_version", lambda *a, **k: "3.12")
    monkeypatch.setattr(sys, "argv", ["configure_harness.py", "--harness", str(repo),
                                      "--python", "3.11"])
    assert tool.main() == 2
    assert "--builder-image" in capsys.readouterr().err


if __name__ == "__main__":                                       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))