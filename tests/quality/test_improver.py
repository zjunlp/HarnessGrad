"""改进器必须被找到、被验证、被记名,而且不能用 `PATH` 猜。

一个 method 是**一条决策规则 + 一个改进器**。在这次的解耦之前,平台只记录了规则用的
模型(`method_model`),于是一次"改进器升级"和一次"方法变强"在记录里长得一模一样。
这个文件测的是把改进器变成一等公民之后必须成立的四件事:

  * 机器上有好几个 codex 时,坏的那个**不能**被选中(`/usr/local/bin/codex` 实测
    0.125.0 缺平台二进制,`--version` 直接抛异常);
  * `latest` 是"别钉版本",不是"别记录"——解析出来的版本和 sha256 必须随曲线点走;
  * 改过的 codex 拿不到、也不该假装拿得到,但**必须能被声明**,而且声明了就要记下来;
  * 解析不了不能静默降级:一个自带改进器的方法照样能跑,而这个 run 记录里不能出现
    它并没有用过的改进器。
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

from tools import improver  # noqa: E402


# --------------------------------------------------------------- 解析 ---

def test_a_candidate_is_verified_by_running_it(tmp_path):
    """`--version` 跑不动的东西不能算候选 —— 那正是坏安装的失败形态。"""
    bad = tmp_path / "codex"
    bad.write_text("#!/bin/sh\necho 'Missing optional dependency' >&2\nexit 1\n")
    bad.chmod(0o755)
    ok, version, reason = improver._run_version(bad)
    assert not ok and not version
    assert "Missing optional dependency" in reason


def test_a_working_candidate_reports_its_version(tmp_path):
    good = tmp_path / "codex"
    good.write_text("#!/bin/sh\necho 'codex-cli 9.9.9'\n")
    good.chmod(0o755)
    ok, version, reason = improver._run_version(good)
    assert (ok, version, reason) == (True, "9.9.9", "")


def test_latest_picks_the_highest_verified_version_not_the_first_on_path(monkeypatch):
    """PATH 上恰好是坏的那份,所以"第一个找到的"是错的语义。"""
    made = [
        {"path": "/a/codex", "ok": True, "version": "0.125.0", "reason": "", "sha256": "x"},
        {"path": "/b/codex", "ok": False, "version": "", "reason": "broken", "sha256": None},
        {"path": "/c/codex", "ok": True, "version": "0.156.1", "reason": "", "sha256": "y"},
    ]

    class _C:
        """和 `Candidate` 同形状。**不要**把 sha256 放进 `__dict__`:那会盖住方法。"""

        def __init__(self, d):
            self.path = Path(d["path"])
            self.ok = d["ok"]
            self.version = d["version"]
            self.reason = d["reason"]

        def as_dict(self):
            return {"path": str(self.path), "ok": self.ok, "version": self.version,
                    "reason": self.reason}

        def sha256(self):
            return "deadbeef"

    monkeypatch.setattr(improver, "candidates", lambda extra=None: [_C(d) for d in made])
    got = improver.resolve({"default": "codex", "improvers": {"codex": {}}}, version="latest")
    assert got["ok"] and got["version"] == "0.156.1", got


def test_asking_for_a_version_that_is_not_here_is_a_refusal(monkeypatch):
    """静默换成另一个版本,就是让记录撒谎。"""
    class _C:
        def __init__(self, version):
            self.path = Path(f"/x/{version}/codex")
            self.ok, self.version, self.reason = True, version, ""

        def as_dict(self):
            return {"path": str(self.path), "ok": True, "version": self.version,
                    "reason": "", "sha256": "z"}

        def sha256(self):
            return "z"

    monkeypatch.setattr(improver, "candidates",
                        lambda extra=None: [_C("0.139.0")])
    got = improver.resolve({"default": "codex", "improvers": {"codex": {}}},
                           version="0.156.1")
    assert not got["ok"]
    assert "0.156.1" in got["reason"]
    assert got["candidates"], "拒绝时必须列出它到底找到了什么"


def test_no_working_candidate_at_all_is_a_refusal(monkeypatch):
    monkeypatch.setattr(improver, "candidates", lambda extra=None: [])
    got = improver.resolve({"default": "codex", "improvers": {"codex": {}}})
    assert not got["ok"] and "no working codex" in got["reason"]


# ----------------------------------------------------------- 记名 ---

def test_the_resolved_model_comes_from_the_improver_s_own_config(tmp_path, monkeypatch):
    """codex 从自己的 config.toml 读模型,所以 `HG_METHOD_MODEL` 说什么都不算数。"""
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text('model = "gpt-6-sol"\nmodel_provider = "OPENAI"\n')
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert improver.declared_model({"default": "codex", "improvers": {"codex": {}}}) == "gpt-6-sol"


def test_a_modified_improver_can_be_declared_and_is_visible(monkeypatch):
    """拿不到别人的二进制,但可以不让他匿名。

    平台对 harness 就是这个立场:它不能判断一个 harness 写得好不好,但它能说清楚
    跑的是哪一份字节。改过的 codex 同理 —— 声明 `modified: true`,解析结果里就得有
    这个事实,曲线点才会带着它。
    """
    class _C:
        def __init__(self):
            self.path = Path("/opt/my-codex/codex")
            self.ok, self.version, self.reason = True, "0.156.1", ""

        def as_dict(self):
            return {"path": str(self.path), "ok": True, "version": self.version,
                    "reason": "", "sha256": "abc"}

        def sha256(self):
            return "abc"

    monkeypatch.setattr(improver, "candidates", lambda extra=None: [_C()])
    monkeypatch.setattr(improver, "declared_model",
                        lambda config=None, source=None, entry=None: "my-model")
    cfg = {"default": "mine",
           "improvers": {"mine": {"name": "mine", "path": "/opt/my-codex/codex",
                                  "modified": True}}}
    got = improver.resolve(cfg)
    assert got["ok"] and got["modified"] is True, got
    assert got["name"] == "mine" and got["model"] == "my-model"


# --------------------------------------------- 真实 run 里的身份 ---

def test_a_real_run_records_the_improver_on_every_point(tmp_path):
    runs = tmp_path / "runs"
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "1", "--run-id", "improver-id", "--dataset", "demo",
         "--sampling", "all", "--runs-root", str(runs),
         "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "HG_AGENT_BACKEND": "mock"})
    assert proc.returncode == 0, f"{proc.stdout[-600:]}\n{proc.stderr[-600:]}"
    points = [json.loads(l) for l in
              (runs / "improver-id" / "curve.jsonl").read_text().splitlines() if l.strip()]
    assert points
    for p in points:
        imp = (p.get("identity") or {}).get("improver")
        if imp is None:
            # 这台机器上没有可用的改进器:那么一个 run 也不能声称自己用过谁。
            assert "no improver" in proc.stdout + proc.stderr, \
                "曲线上没有 improver,但日志里也没有说为什么"
            continue
        # `ok`/`reason` 是本次进程的簿记,不是记录该带的东西。
        assert "ok" not in imp and "reason" not in imp, imp
        assert imp.get("name"), imp
        # **身份因 kind 而异,这不是放宽断言。** 一个 CLI 改进器是磁盘上的一个程序,它的
        # 身份就是"哪个版本、哪份 sha256"——不记下来,"改进器升级了"和"方法变强了"分不开。
        # 一个端点型改进器不是程序:`resolve` 对它的定义是"端点配好了",版本与哈希本来就
        # 是空字符串,而它真正的身份是 **端点地址和模型名**(记录里不出现密钥)。
        # 默认改进器 2026-10-04 从 codex(CLI)改成 deepseek(端点)之后,旧断言要求 version
        # 非空,把一个正确的记录判成了失败。
        if imp.get("kind") == "cli":
            for field in ("version", "sha256", "path"):
                assert imp.get(field), (field, imp)
        else:
            for field in ("model", "path"):
                assert imp.get(field), (field, imp)
            assert imp.get("version") in ("", None) and imp.get("sha256") in ("", None), imp


# ------------------------------------------- 改进器的运行环境(不是身份) ---

def test_runtime_trees_names_the_package_and_not_the_launcher(tmp_path):
    """一个 npm CLI 是"包装脚本 + 旁边的原生二进制",所以绑文件不够。

    实测:`codex.js` 只负责转交给同一个包里 vendor/ 下面的原生二进制。只绑那个 .js
    文件,方法在沙箱里照样跑不起来 —— 报错还是 `FileNotFoundError`,看起来像方法坏了。
    """
    package = tmp_path / "lib" / "node_modules" / "@openai" / "codex" / "bin"
    package.mkdir(parents=True)
    wrapper = package / "codex.js"
    wrapper.write_text("#!/usr/bin/env node\n")
    trees = improver.runtime_trees(wrapper)
    assert str(tmp_path / "lib" / "node_modules") in trees, trees
    assert str(package) not in trees, "绑了 launcher 所在目录,旁边那个包还是读不到"


def test_runtime_trees_follows_the_shebang_to_the_interpreter(tmp_path, monkeypatch):
    """`#!/usr/bin/env node` 里的 node 在这台机器上不在 /usr 下面,而在 $HOME 里。

    沙箱绑了 `/usr`、`/bin`、`/lib` 和 Python 前缀,别的运行时必须被点名,而"需要什么
    运行时"只有脚本第一行知道。
    """
    node = tmp_path / "node-v22" / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    monkeypatch.setattr(improver.shutil, "which", lambda name: str(node) if name == "node" else None)
    script = tmp_path / "pkg" / "cli.js"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env node\n")
    trees = improver.runtime_trees(script)
    assert str(tmp_path / "node-v22") in trees, trees


def test_runtime_trees_includes_the_config_home_that_holds_the_credentials(
        tmp_path, monkeypatch):
    """没有 config.toml 和 auth.json,会话就没有 provider 也没有钥匙。

    方法会把这两份文件拷进自己的 CODEX_HOME,所以它们必须在方法的命名空间里可读;
    否则失败长得像"方法调不动模型"。
    """
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    script = tmp_path / "pkg" / "cli.js"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    assert str(home) in improver.runtime_trees(script)


def test_the_method_sandbox_binds_the_improver_but_never_the_platform(tmp_path):
    """`runtime` 是"方法必须能执行的工具",不是"把平台还回去"。

    两条护栏:平台自己的路径永远不进 `visible_paths`(那会让整个隐藏失效),`/usr`
    这类已经绑过的树不重复绑(计划的读者是人)。而工具那棵树必须真的被绑,否则
    `--improver codex` 只能在 `--no-sandbox` 下用 —— 那正是它之前的状态。
    """
    import harnessgrad.methods as methods
    tool = tmp_path / "node_modules" / "@openai" / "codex"
    tool.mkdir(parents=True)
    plan = methods._method_sandbox_plan(
        tmp_path / "work", [sys.executable, str(ROOT / "methods" / "llm_improver" / "run.py")],
        tmp_path / "scratch",
        runtime=(str(tool), str(ROOT / "tools"), "/usr", str(ROOT / "harnessgrad")))
    assert plan["runtime_paths"] == (tool,), plan["runtime_paths"]
    assert tool in plan["visible_paths"]
    assert ROOT / "tools" not in plan["visible_paths"]
    assert ROOT / "harnessgrad" not in plan["visible_paths"]


def test_a_named_endpoint_improver_declares_its_own_url_model_and_key(monkeypatch):
    """`--improver deepseek` 不能在记录里写 deepseek,而方法实际打的是 `.env` 里的本地端点。

    这条和 harness 误报自己模型是同一类 bug,修法也一样:平台把事实写在消费者读的地方。
    注册项声明端点/模型/钥匙变量,`.env` 只是通用兜底 —— 一个**具名**的改进器被 `.env`
    悄悄改道,正是"记录说 A、实际跑 B"的形态。
    """
    # `.env` 会被 driver 提前塞进 os.environ,所以在真实运行里这两个变量**是**设着的 ——
    # 第一版测试把它们删掉才通过,于是漏掉了实测到的那次错误:具名改进器的端点被 .env
    # 覆盖,记录写 deepseek、方法实际打本地 vLLM。
    monkeypatch.setenv("HG_METHOD_BASE_URL", "http://127.0.0.1:8001/v1")
    monkeypatch.setenv("HG_METHOD_MODEL", "qwen3.5-9b-local")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k" * 12)
    cfg = {"default": "deepseek", "improvers": {"deepseek": {
        "name": "deepseek", "kind": "openai-compatible", "model": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"}}}
    got = improver.resolve(cfg, requested="deepseek")
    assert got["ok"], got
    assert got["path"] == "https://api.deepseek.com/v1"
    assert got["model"] == "deepseek-v4-pro"
    assert got["api_key_env"] == "DEEPSEEK_API_KEY"


def test_an_endpoint_improver_without_its_credential_is_refused_by_name(monkeypatch):
    """没有钥匙要在**门口**拒绝,而不是花掉一轮之后在方法里报一个像方法坏了的错。"""
    monkeypatch.setenv("HG_METHOD_BASE_URL", "http://127.0.0.1:8001/v1")
    monkeypatch.setenv("HG_METHOD_MODEL", "qwen3.5-9b-local")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = {"default": "deepseek", "improvers": {"deepseek": {
        "name": "deepseek", "kind": "openai-compatible", "model": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"}}}
    got = improver.resolve(cfg, requested="deepseek")
    assert not got["ok"] and "DEEPSEEK_API_KEY" in got["reason"], got


def test_the_method_environment_is_pointed_at_the_resolved_endpoint(monkeypatch):
    """方法侧的客户端读 `HG_METHOD_*`,所以平台必须把解析出来的端点写进去。

    少了这一步,`HG_METHOD_*` 会来自 `.env`,而曲线点上是注册表里的名字 —— 记录和实际
    调用的端点就不是同一个东西了。钥匙从注册表点名的变量复制,不进记录。
    """
    import harnessgrad.methods as methods
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value")
    env = methods._method_env(Path("/tmp"), {
        "name": "deepseek", "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-pro", "api_key_env": "DEEPSEEK_API_KEY"})
    assert env["HG_METHOD_BASE_URL"] == "https://api.deepseek.com/v1"
    assert env["HG_METHOD_MODEL"] == "deepseek-v4-pro"
    assert env["HG_METHOD_API_KEY"] == "secret-value"
    # 没有端点(例如 codex 这种 CLI 改进器)时不能改动方法自己的配置
    plain = methods._method_env(Path("/tmp"), {"name": "codex", "path": "/usr/bin/codex"})
    assert "HG_METHOD_BASE_URL" not in plain or \
        plain["HG_METHOD_BASE_URL"] == os.environ.get("HG_METHOD_BASE_URL")


def test_a_model_the_project_does_not_use_is_refused_by_name(monkeypatch):
    """项目规定只用 `deepseek-flash`。这条不能是"大家记得",必须是一次拒绝。

    理由是可比的:一个悄悄用了另一个模型的 run,会在曲线上留一个谁也没法与之比较的数字,
    而记录里没有一个字说明这件事。
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k" * 12)
    cfg = {"default": "deepseek", "_forbidden_models": {"deepseek-v4-pro": "we use flash"},
           "improvers": {"deepseek": {"name": "deepseek", "kind": "openai-compatible",
                                      "model": "deepseek-flash",
                                      "base_url": "https://api.deepseek.com/v1",
                                      "api_key_env": "DEEPSEEK_API_KEY"}}}
    got = improver.resolve(cfg, requested="deepseek", model_override="deepseek-v4-pro")
    assert not got["ok"] and "not allowed" in got["reason"], got
    ok = improver.resolve(cfg, requested="deepseek")
    assert ok["ok"] and ok["model"] == "deepseek-flash", ok


def test_the_registry_itself_ships_flash_and_names_the_banned_model():
    """注册表是这条规则的家:`--improver deepseek` 不指定模型时必须就是 flash。"""
    cfg = improver.load_config()
    assert cfg["improvers"]["deepseek"]["model"] == "deepseek-flash"
    assert "deepseek-v4-pro" in cfg.get("_forbidden_models", {})
    assert improver.forbidden_reason(cfg, "deepseek-flash") == ""
    assert improver.forbidden_reason(cfg, "deepseek-v4-pro") != ""


def test_a_resolved_improver_reports_its_own_model_not_the_default_s(tmp_path, monkeypatch):
    """**`resolve` 必须问"选中的那个"它用什么模型,不是问默认条目。**

    实测(2026-10-04):`resolve(requested="codex")` 返回 `model: deepseek-flash` ——
    那是 `default` 条目(一个端点改进器)的模型。于是曲线点上会写"改进器用的模型是
    deepseek-flash",而 codex 从头到尾没碰过 deepseek,它用的是自己
    `~/.codex/config.toml` 里那个。这和 `agent_model: mock` 而实际有真模型回答是同一类
    谎言,而且它正好卡在"要判断哪个模型得会输出标准 tool_calls"这件事上。
    """
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text('model = "probe-strong-model"\n')
    monkeypatch.setenv("CODEX_HOME", str(home))

    class _C:
        def __init__(self):
            self.path = Path("/x/codex")
            self.ok = True
            self.version = "9.9.9"
            self.reason = ""

        def as_dict(self):
            return {"path": str(self.path), "ok": True, "version": self.version,
                    "reason": ""}

        def sha256(self):
            return "deadbeef"

    monkeypatch.setattr(improver, "candidates", lambda extra=None: [_C()])
    config = {"default": "deepseek",
              "improvers": {"codex": {"kind": "cli", "version": "latest"},
                            "deepseek": {"kind": "openai-compatible",
                                         "model": "deepseek-flash"}}}
    got = improver.resolve(config, version="latest", requested="codex")
    assert got["ok"], got
    assert got["name"] == "codex" and got["version"] == "9.9.9"
    assert got["model"] == "probe-strong-model", (
        "codex 的模型被记成了默认改进器的模型:" + str(got["model"]))

    # 反向的一半:端点改进器自己的 `model` 仍然优先,而且默认条目照旧可用
    assert improver.resolve(config, version="", requested="deepseek")["model"] == "deepseek-flash"
    assert improver.declared_model(config) == "deepseek-flash"


# ------------------------------------------- improver 的模型会不会调工具 ---

def test_a_cli_s_custom_provider_endpoint_is_derived_from_its_own_config(tmp_path, monkeypatch):
    """门要能拿到"codex 到底会连哪个 endpoint",而那个答案在 codex 自己的配置里。"""
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        'model = "qwen3.5-9b-local"\nmodel_provider = "local9b"\n\n'
        '[model_providers.local9b]\nname = "local9b"\n'
        'base_url = "http://127.0.0.1:8001/v1"\nwire_api = "responses"\n')
    monkeypatch.setenv("CODEX_HOME", str(home))
    from driver import _cli_provider_base_url
    assert _cli_provider_base_url({"name": "codex"}) == "http://127.0.0.1:8001/v1"

    # 内置 provider(没有 base_url 定义) -> 探测不到,于是"未探测",不是"不支持"
    (home / "config.toml").write_text('model = "x"\nmodel_provider = "OPENAI"\n')
    assert _cli_provider_base_url({"name": "codex"}) == ""


def test_the_probe_reports_a_dead_endpoint_instead_of_raising(monkeypatch):
    """门自己不能成为失败来源:连不上就返回 False + 原因,不抛异常。"""
    from driver import _probe_tool_calls
    ok, why = _probe_tool_calls("http://127.0.0.1:9/v1", "nope")
    assert ok is False and why, why


def test_the_gate_refuses_a_cli_improver_whose_model_cannot_emit_tool_calls(tmp_path):
    """端到端:endpoint 不支持标准 tool_calls 时,**在花钱之前**拒绝。

    实测(2026-10-04):codex 指向本地 9B 时连上了、花了 8,699 tokens、一步都没执行 ——
    模型的调用以文本形式返回,而 codex 在 `wire_api = "responses"` 上只认结构化调用。
    """
    import shutil
    import subprocess

    if not improver.resolve({"default": "codex", "improvers": {"codex": {}}},
                            version="latest").get("ok"):
        pytest.skip("no usable codex on this machine")

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        'model = "probe"\nmodel_provider = "dead"\n\n'
        '[model_providers.dead]\nname = "dead"\n'
        'base_url = "http://127.0.0.1:9/v1"\nwire_api = "responses"\n')
    runs = tmp_path / "runs"
    env = {**os.environ, "HG_AGENT_BACKEND": "mock",
           "CODEX_HOME": str(home),
           # 沙箱会把 `tools/` 藏起来,而门在平台侧,所以关掉沙箱跑这条
           }
    proc = subprocess.run(
        [sys.executable, "driver.py", "--harness", "loop_plain", "--mode", "A",
         "--rounds", "0", "--run-id", "gate-refuses", "--dataset", "demo",
         "--sampling", "all", "--improver", "codex", "--no-sandbox",
         "--runs-root", str(runs), "--work-root", str(tmp_path / "work"),
         "--method-entrypoint", f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=600, env=env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out[-900:]
    assert "structured tool_calls" in out, out[-900:]
    assert "HG_IMPROVER_SKIP_TOOL_GATE" in out, out[-900:]


# ------------------------------------------- 第二个 CLI:声明而不是搜索 ---

def _stub_launcher(tmp_path: Path, body: str = "#!/bin/sh\necho 'dsh 9.9.9'\n") -> Path:
    path = tmp_path / "dsh-improver"
    path.write_text(body)
    path.chmod(0o755)
    return path


def test_a_declared_launcher_is_resolved_without_searching_for_codex(tmp_path, monkeypatch):
    """`launcher` 的条目按声明解析,一次都不去搜。

    codex 那套 globs 对 dsh 是错的形状:它得在 source tree 和 build 产物之间二选一,而
    猜错了不会报错,只会跑起另一个东西。这条同时钉住"没走 `candidates()`"—— 把它换成
    会抛异常的桩,解析仍然必须成功。
    """
    def boom(extra=None):
        raise AssertionError("a declared launcher must not go through the codex search")

    monkeypatch.setattr(improver, "candidates", boom)
    script = _stub_launcher(tmp_path)
    config = {"default": "dsh", "improvers": {"dsh": {
        "name": "dsh", "kind": "cli", "argv_style": "dsh", "launcher": str(script),
        "model": "deepseek-flash"}}}
    resolved = improver.resolve(config, requested="dsh")
    assert resolved["ok"] and resolved["path"] == str(script)
    # 这两件是 `methods/cli_improver.py` 拼 argv 时要用的,而路径推不出来
    assert resolved["argv_style"] == "dsh"
    assert resolved["model"] == "deepseek-flash"
    assert resolved["sha256"], "launcher 的字节也要记"


def test_the_declared_runtime_trees_are_what_the_method_sandbox_will_bind(tmp_path):
    """绑定树不能从 launcher 路径推出来,所以条目必须自己说。

    `runtime_trees(launcher)` 只会给出 `improvers/`(在平台内,会被 plan 自己的守卫丢掉)
    和 `~/.codex`。dsh 需要的是它的 checkout 和 `~/.dsh`;少了这些,round 1 会死于
    module-not-found,和 `harnessgrad/methods.py` 里记过的那次一模一样。
    """
    checkout = tmp_path / "deepseek-harness"
    checkout.mkdir()
    home = tmp_path / "dsh-home"
    home.mkdir()
    script = _stub_launcher(tmp_path)
    config = {"default": "dsh", "improvers": {"dsh": {
        "name": "dsh", "kind": "cli", "launcher": str(script),
        "runtime": [str(checkout), str(home), str(tmp_path / "not-here")]}}}
    resolved = improver.resolve(config, requested="dsh")
    assert str(checkout) in resolved["runtime"]
    assert str(home) in resolved["runtime"]
    # 声明了一个不存在的树不能被绑:一个失败的 bind 会让整个 sandbox plan 起不来
    assert not [t for t in resolved["runtime"] if "not-here" in t]


def test_a_declared_interpreter_supplies_the_tree_a_shebang_cannot(tmp_path, monkeypatch):
    """bash 的 launcher 不会透露 node 在哪 —— 而 node 装在 `$HOME` 下的一个 nvm 里。

    `runtime_trees` 靠 shebang 找解释器,所以 codex 的 `codex.js` 白拿 node 的安装树;
    dsh 的 launcher 是 bash,而 `sh`/`bash`/`env` 被**故意**跳过(它们在 `/usr`,namespace
    本来就绑)。少了这一条,沙箱里会有 CLI 的文件,却没有能跑它的程序 —— 而且失败会写成
    "improver 坏了",不是"少绑了一棵树"。
    """
    install = tmp_path / "nvm" / "versions" / "node" / "v22.19.0"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "node").write_text("")
    monkeypatch.setattr(improver.shutil, "which",
                        lambda name: str(install / "bin" / "node") if name == "node" else None)
    script = _stub_launcher(tmp_path)
    config = {"default": "dsh", "improvers": {"dsh": {
        "name": "dsh", "kind": "cli", "launcher": str(script), "interpreter": "node"}}}
    resolved = improver.resolve(config, requested="dsh")
    assert str(install) in resolved["runtime"], resolved["runtime"]


def test_a_build_command_is_what_identifies_the_improver(tmp_path):
    """launcher 的 sha256 是那 2KB 脚本,而那**不是** improver 的身份。

    codex 的 sha256 是它的二进制,二进制就是工具。这里的工具是一个 checkout:launcher 很
    稳定,底下的 `apps/cli/lib/bin.js` 却会在任何人动那个仓库时被重新构建 —— 那个仓库是
    活的。没有这一条,曲线会记下一个永远不会变的身份,而它背后的 improver 在变。
    """
    script = _stub_launcher(tmp_path)
    stamp = tmp_path / "revision"
    stamp.write_text("c291e7961a\n")
    config = {"default": "dsh", "improvers": {"dsh": {
        "name": "dsh", "kind": "cli", "launcher": str(script),
        "build_command": ["cat", str(stamp)]}}}
    resolved = improver.resolve(config, requested="dsh")
    assert resolved["version"] == "9.9.9+c291e7961a", resolved["version"]


def test_a_build_command_that_cannot_run_says_so_instead_of_dropping_the_field(tmp_path):
    """问不出 build 就写成 `unidentified`,而不是悄悄少一个字段。

    `0.1.5+unidentified(FileNotFoundError)` 和另一个一样的字符串仍然可比;空字符串会
    让"这次没测到"和"这次没这个字段"长得一样。
    """
    script = _stub_launcher(tmp_path)
    config = {"default": "dsh", "improvers": {"dsh": {
        "name": "dsh", "kind": "cli", "launcher": str(script),
        "build_command": [str(tmp_path / "no-such-program")]}}}
    resolved = improver.resolve(config, requested="dsh")
    assert resolved["version"].startswith("9.9.9+unidentified("), resolved["version"]


def test_a_missing_launcher_is_a_refusal_not_a_fallback_to_codex(tmp_path, monkeypatch):
    """"我要 dsh 却拿到 codex" 正是让两个实验不可比的那种替换。"""
    monkeypatch.setattr(improver, "candidates",
                        lambda extra=None: (_ for _ in ()).throw(
                            AssertionError("no fallback may be attempted")))
    config = {"default": "dsh", "improvers": {"dsh": {
        "name": "dsh", "kind": "cli", "launcher": str(tmp_path / "gone")}}}
    resolved = improver.resolve(config, requested="dsh")
    assert resolved["ok"] is False
    assert "missing" in resolved["reason"], resolved


def test_the_shipped_dsh_entry_declares_what_the_code_branches_on():
    """注册表条目是**代码分支的依据**,所以它自己的形状要有一条测试。

    四个字段各有各的失败方式,而且都不在 `resolve()` 里报错:`argv_style` 拼错会在 method
    里变成一次 argv 拒绝,`launcher` 写错会在 round 1 变成 "launcher is missing",
    `runtime` 忘了声明会变成 module-not-found,`build_command` 忘了声明会让曲线记下一个
    假的稳定身份。所以在这里一次把四个都钉住。
    """
    config = improver.load_config()
    entry = config["improvers"]["dsh"]
    assert entry["kind"] == "cli"
    assert entry["argv_style"] == "dsh"
    launcher = ROOT / entry["launcher"]
    assert launcher.is_file(), f"launcher 不存在: {launcher}"
    assert launcher.stat().st_mode & 0o111, "launcher 必须可执行"
    assert entry["runtime"], "必须声明 method sandbox 要绑的树"
    assert entry["build_command"], "必须声明怎么识别 checkout"
    assert entry["model"], "必须声明模型,否则记录里说不出它用的是什么"
    assert improver.forbidden_reason(config, entry["model"]) == ""


def test_the_launcher_lives_where_the_method_sandbox_already_looks():
    """**这条是实测出来的**:launcher 放在 `improvers/` 下时,round 1 直接死。

    `_method_sandbox_plan` 把 `PLATFORM_ROOT/methods` 绑进方法的 namespace —— 方法自己的
    程序在那儿 —— 而它**故意**丢掉每一个位于平台内部的 `runtime` 树,那正是"平台对方法不
    可见"这条性质的守卫。所以一个放在 `improvers/` 下的 launcher,对必须执行它的方法是
    不可见的:

        round 1: method entrypoint failed: exit 1 -- the platform named an improver at
        '.../improvers/dsh-improver', which does not exist inside this sandbox.

    放宽那个守卫的代价是一个操作者可写的注册表能点名平台下的**任意**目录,而 `data/` 里
    放着每道题的期望答案。所以 launcher 和 `cli_improver.py` 放在一起 —— `methods/` 本来
    就是为这个绑定的 —— 而这条测试把那个位置约束写下来,免得以后有人"整理"回 `improvers/`。
    """
    from harnessgrad.methods import PLATFORM_ROOT as HG_ROOT

    config = improver.load_config()
    launcher = (ROOT / config["improvers"]["dsh"]["launcher"]).resolve()
    bound = (HG_ROOT / "methods").resolve()
    assert launcher.parent == bound, (
        f"launcher 必须待在 {bound}(它被只读绑进每个方法的 namespace),而不是 "
        f"{launcher.parent} —— 后者在平台内部,sandbox plan 会把它丢掉")
    assert launcher.is_file()

