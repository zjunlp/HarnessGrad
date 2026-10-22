"""方法和平台之间的共享编辑层。

这里钉的是两类缺陷,它们都不是"某个方法写错了",而是**每个方法都会犯**的:

  1. **方法必须能看到契约承诺给它的东西。** `INTERFACE.md` §4.7 把 `_harnessgrad/`
     放在 harness 目录里。第一版 `llm_improver` 从 `request["workspace"]`(方法自己的
     空 scratch 目录)读 traces,于是它build 出来的每一个 prompt 都写着
     `(no traces available)` —— 它只看着 harness 源码改代码,而它的 docstring 声称它
     读了 harness 的实际行为。这条测试就是那次测量的复现。

  2. **每一条出口都要写出 trajectory。** 这条规则已经被违反过两次:provider 报错和
     提案不可用都直接退出、没写文件,于是平台只能说"方法什么都没写",真正的原因
     (一个 503、一个格式坏掉的回复)被丢掉了。不可读的失败比被报告的失败更糟。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "methods"))


def _write_harness(root: Path, *, traces: dict[str, str] | None = None,
                   history: list[dict] | None = None) -> Path:
    """A base harness with the channel staged exactly where the contract puts it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "harness.json").write_text(
        json.dumps({"name": "probe", "version": "1.0", "entrypoint": "agent.py"}))
    (root / "agent.py").write_text("print('hello')\n")
    hg = root / "_harnessgrad"
    (hg / "traces").mkdir(parents=True, exist_ok=True)
    (hg / "history").mkdir(parents=True, exist_ok=True)
    for tid, body in (traces or {}).items():
        (hg / "traces" / f"{tid}.jsonl").write_text(body)
    for point in (history or []):
        (hg / "history" / f"round-{point.get('round', 0)}.json").write_text(
            json.dumps(point))
    (hg / "round.json").write_text(json.dumps(
        (history or [{}])[-1] if history else {"round": 0, "score": 0.0}))
    return root


def _load(name: str):
    path = ROOT / "methods" / name / "run.py"
    spec = importlib.util.spec_from_file_location(f"m_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _invoke(mod, request: dict, monkeypatch, reply=None, raises=None) -> dict:
    """Run a method's main() with a stubbed model, returning its stdout object."""
    import editor

    captured: dict = {}

    def fake_ask(prompt, system=editor.DEFAULT_SYSTEM, base=None, skill=True, parent=None, endpoint_only=False):
        captured["prompt"] = prompt
        captured["system"] = system
        if raises is not None:
            raise raises
        return reply if reply is not None else {"no_change": "stub"}

    monkeypatch.setattr(editor, "ask", fake_ask)
    monkeypatch.setattr(mod.editor, "ask", fake_ask, raising=False)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(sys, "stdout", out)
    try:
        mod.main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise
    return {"stdout": json.loads(out.getvalue() or "{}"), **captured}


def _request(base: Path, work: Path, traj: Path) -> dict:
    return {"platform_api_version": "0.1.0", "mode": "A", "base_harness": str(base),
            "workspace": str(work), "round_index": 1, "incumbent_score": 0.0,
            "task_ids": ["t01"], "train_task_ids": ["t01"],
            "trajectory_out": str(traj)}


# ------------------------------------------- 方法必须看到承诺给它看的东西 ---

def test_the_editor_is_given_the_traces_the_contract_promises(tmp_path, monkeypatch):
    """回归测试:traces 在 harness 目录里,不在方法的 workspace 里。

    这条测试是那次测量的直接复现 —— 旧代码在这种目录布局下报告
    `(no traces available)`,因为它在 `workspace/_harnessgrad/traces` 找文件,而那里
    永远是空的。
    """
    base = _write_harness(tmp_path / "base", traces={
        "t01": json.dumps({"command": "ls -a", "exit": 1}) + "\n"
               + json.dumps({"reply": "I cannot do that"}) + "\n"})
    work = tmp_path / "ws"; work.mkdir()          # 方法的 scratch 目录:空的
    mod = _load("llm_improver")

    got = _invoke(mod, _request(base, work, tmp_path / "traj.json"), monkeypatch)
    prompt = got["prompt"]

    # **新契约:内容不再编译进 prompt。** 平台把通道 stage 成 `evidence/`,CLI 层拿到的是
    # **路径**(codex 自己读),聊天端点层(没有文件系统)才拿内容。以前两边都靠把渲染好的
    # 轨迹和源码塞进 prompt —— 实测 58,160 字符里 89.7% 是证据,按真实规模(51 题)约 230 KB,
    # 而整条指令是一个 argv 参数,Linux 上限 128 KB,工具根本起不来。
    import editor
    assert "ran: ls -a" not in prompt, "轨迹又被编译进方法的 prompt 了"
    assert "## Current harness sources" not in prompt, "源码又被编译进方法的 prompt 了"

    # 端点层拿内容(它没有文件系统)
    content = editor.evidence_content(base)
    assert "ran: ls -a" in content and "said: I cannot do that" in content, content[:300]

    # CLI 层拿路径,而且路径要指名 traces 和索引
    pointer = editor.evidence_pointer(base)
    assert "evidence/traces/" in pointer and "index.json" in pointer, pointer
    assert "ran: ls -a" not in pointer, "指针块里不该有内容"


def test_history_is_read_from_the_contract_path(tmp_path, monkeypatch):
    """`history/` 同样在 harness 目录里,而且它是选择类规则的唯一输入。"""
    base = _write_harness(tmp_path / "base", traces={"t01": "{}\n"}, history=[
        {"round": 0, "score": 0.0, "score_ci95": [0.0, 0.1]},
        {"round": 1, "score": 0.5, "score_ci95": [0.4, 0.6]},
    ])
    work = tmp_path / "ws"; work.mkdir()
    mod = _load("sica_ci")

    got = _invoke(mod, _request(base, work, tmp_path / "traj.json"), monkeypatch,
                  reply={"files": [{"path": "agent.py", "content": "x=1\n"}],
                         "hypothesis": "h"})
    rule = got["stdout"]["method_reported"]["sica_rule"]
    assert rule["rounds_seen"] == [0, 1], f"SICA 的规则没看到历史:{rule}"
    assert rule["selected_base_round"] == 1


# ---------------------------------------------- 每条出口都要写 trajectory ---

def test_a_provider_error_still_produces_a_trajectory(tmp_path, monkeypatch):
    """不可读的失败比被报告的失败更糟:它看起来像方法什么都没做。"""
    base = _write_harness(tmp_path / "base", traces={"t01": "{}\n"})
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"
    mod = _load("llm_improver")

    got = _invoke(mod, _request(base, work, traj), monkeypatch,
                  raises=RuntimeError("503 no available channel"))

    assert traj.exists(), "provider 报错时没有写出 trajectory"
    step = json.loads(traj.read_text())["steps"][0]
    assert "503" in json.dumps(step["method_reported"]), \
        "真正的原因必须进记录,否则只剩「方法什么都没写」"
    assert got["stdout"]["changed"] is False


def test_a_no_change_reply_still_produces_a_trajectory(tmp_path, monkeypatch):
    base = _write_harness(tmp_path / "base", traces={"t01": "{}\n"})
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"
    mod = _load("llm_improver")

    _invoke(mod, _request(base, work, traj), monkeypatch,
            reply={"no_change": "the evidence supports nothing"})

    assert traj.exists()
    assert json.loads(traj.read_text())["steps"][0]["harness_dir"]


def test_an_unusable_proposal_is_reported_not_swallowed(tmp_path, monkeypatch):
    """被拒绝的编辑和「方法选择不动」是两件事,记录里必须分得开。"""
    base = _write_harness(tmp_path / "base", traces={"t01": "{}\n"})
    work = tmp_path / "ws"; work.mkdir()
    traj = tmp_path / "traj.json"
    mod = _load("llm_improver")

    got = _invoke(mod, _request(base, work, traj), monkeypatch,
                  reply={"files": [{"path": "nope.py"}]})     # 没有 content

    assert traj.exists()
    assert got["stdout"]["changed"] is False
    note = json.loads(traj.read_text())["steps"][0]["method_reported"]
    assert "rejected" in note, note


# ------------------------------------------------- 编辑不能跑到 harness 外 ---

def test_an_edit_outside_the_harness_is_rejected_not_clamped(tmp_path):
    """`../agent.py` 被悄悄夹成 `agent.py`,等于测了一个模型没要求的东西。"""
    import editor

    base = _write_harness(tmp_path / "base")
    dest = tmp_path / "candidate"

    changed, written, note = editor.apply_edits(
        base, dest, [{"path": "../escaped.py", "content": "x"}])
    assert changed is False and not written
    assert "outside the harness" in note
    assert not (tmp_path / "escaped.py").exists()


def test_the_manifest_cannot_be_rewritten(tmp_path):
    """`harness.json` 命名了这个 harness;能改它的方法能改自己被测量成什么。"""
    import editor

    base = _write_harness(tmp_path / "base")
    dest = tmp_path / "candidate"
    changed, written, note = editor.apply_edits(
        base, dest, [{"path": "harness.json", "content": "{}"}])
    assert changed is False
    assert "protected" in note


def test_a_legitimate_multi_file_edit_is_applied(tmp_path):
    """方法可能要新增一个模块 —— 只允许改 agent.py 会把这类改进挡在门外。"""
    import editor

    base = _write_harness(tmp_path / "base")
    dest = tmp_path / "candidate"
    changed, written, _ = editor.apply_edits(base, dest, [
        {"path": "agent.py", "content": "print('better')\n"},
        {"path": "tools/helper.py", "content": "VALUE = 1\n"},
    ])
    assert changed and sorted(written) == ["agent.py", "tools/helper.py"]
    assert (dest / "tools" / "helper.py").read_text() == "VALUE = 1\n"
    assert (dest / "harness.json").read_text() == (base / "harness.json").read_text()
    assert not (dest / "_harnessgrad").exists(), \
        "channel 不该被复制进候选,否则它会成为一个被提交的文件"


# ------------------------------------------------ 读不懂 ≠ 决定不改 ---

def test_a_reply_missing_its_trailing_brackets_is_repaired():
    """小模型把整个文件写进 JSON 字符串,然后忘了结尾的 `]}`。

    这是实测的形状,不是构造的:一次真实运行里,9B 模型返回了完整的 8.8 KB 编辑,
    `content` 在 8864 字符处正确闭合、`hypothesis` 也正常,就是没写最后的 `]}`。
    `json.loads` 报 "Expecting ',' delimiter at EOF";旧的容错回退取
    `text[第一个{ : 最后一个}]`,拿到的仍然没有闭合,于是放弃 —— 然后方法报
    `no_change`。六轮下来读作"方法拒绝了六次"。
    """
    import editor

    real = ('{"files": [{"path": "agent.py", "content": "print(1)\\n", '
            '"hypothesis": "h"}')
    got = editor.loads_tolerant(real)
    assert got and got["files"][0]["path"] == "agent.py", got


def test_a_reply_with_raw_newlines_inside_a_string_is_repaired():
    """同一种失败的另一半:整文件内容里的**裸换行**。

    裸换行让 JSON 非法,任何"补括号"都救不了它,所以修复必须在扫描时做。
    """
    import editor

    got = editor.loads_tolerant('{"files": [{"path": "a.py", "content": "l1\nl2"}]}')
    assert got and got["files"][0]["content"] == "l1\nl2", got
    got = editor.loads_tolerant('{"files": [{"path": "a.py", "content": "a\tb"}]}')
    assert got and got["files"][0]["content"] == "a\tb", got


def test_a_reply_with_a_bracket_too_many_is_also_repaired():
    """反方向的同一种错:多一个结尾括号。

    实测的形状:让 `base_harness/loop` 失败的那条回复就是 `{...}}` —— 尾部多了一个
    `}`。`json.loads` 报 "Extra data",而 harness 自己的回退取
    `reply[find("{") : rfind("}")]`,最后一个 `}` 恰恰就是多余的那个,所以它同样失败
    (`base_harness/loop/agent.py:143`)。补括号救不了这种情况,只有**在第一个平衡点
    截断**才行 —— 两个方向需要相反的修法,所以两件都做。
    """
    import editor

    assert editor.loads_tolerant('{"tool": "bash", "command": "x"}}') == \
        {"tool": "bash", "command": "x"}
    assert editor.loads_tolerant('{"a": 1}}}') == {"a": 1}
    # 尾部有散文也是同一类:JSON 在前,后面还有话
    assert editor.loads_tolerant('{"a": 1}\n\nHope that helps!') == {"a": 1}


#: 每一种都是**实测**遇到过的坏法,不是假设。左边是模型怎么坏的,右边是修好之后的值。
#: 这些以前全都被静默地报成 `no_change` —— 那才是真正的危害:一次读不懂变成了一个决定。
MALFORMED = {
    "正常": ('{"a": 1}', {"a": 1}),
    "少一个结尾括号": ('{"files": [{"path": "a.py", "content": "x"}',
                       {"files": [{"path": "a.py", "content": "x"}]}),
    "多一个结尾括号": ('{"a": 1}}', {"a": 1}),
    # 这条是最难的一个:类型配错了对,`}` 关的是 `[`。既不是缺括号也不是多括号,
    # 而"按类型匹配"的扫描器会**悄悄忽略**它,于是文档看起来是平衡的。
    # 实测来自一条 11 KB 的回复:结尾 `...inputs."}]}`,那个 `}` 关的是 340 字符前
    # 打开的列表。json.loads 直接拒绝,扫描器却什么都不报。
    "括号类型配错": ('{"a": [1, 2}', {"a": [1, 2]}),
    "类型错且多余": ('{"a": [1}]}', {"a": [1]}),
    "字符串里的裸换行": ('{"a": "x\ny"}', {"a": "x\ny"}),
    "字符串里的裸控制字符": ('{"a": "x\x0by"}', {"a": "x\x0by"}),
    # 模型写代码/正则时几乎必然出现:`\d`、`\s`、`C:\data` 都是非法 JSON 转义
    "非法转义 \\d": ('{"a": "\\d+"}', {"a": "\\d+"}),
    "Windows 路径": ('{"a": "C:\\data\\x"}', {"a": "C:\\data\\x"}),
    "未转义的引号": ('{"a": "he said "hi" loudly", "b": 1}',
                     {"a": 'he said "hi" loudly', "b": 1}),
    "末尾多余逗号": ('{"a": 1,}', {"a": 1}),
    "数组末尾逗号": ('{"a": [1, 2,]}', {"a": [1, 2]}),
    "JSON 后面还有散文": ('{"a": 1} hope this helps', {"a": 1}),
    "围栏包着": ('```json\n{"a": 1}\n```', {"a": 1}),
    "前面有散文": ('Sure! Here you go:\n{"a": 1}', {"a": 1}),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_every_malformed_shape_a_small_model_produces_is_repaired(name):
    """读不懂一条回复,代价是整个方法看起来"没有 diff"。

    这张表的每一行都来自这台机器上真实发生过的一次失败,而不是一份"LLM 可能怎么写坏
    JSON"的清单。修不好它们的时候,方法报的是 `no_change` —— 于是六轮下来读作
    "这个方法拒绝了六次",而真相是平台没能读懂它。
    """
    import editor

    raw, expect = MALFORMED[name]
    assert editor.loads_tolerant(raw) == expect


def test_the_parser_leaves_valid_json_alone():
    """修复不能吃掉合法的内容:字符串里的 `,}`、合法的 `\n` 和嵌套都必须原样保留。"""
    import editor

    assert editor.loads_tolerant('{"a": "x,}y"}') == {"a": "x,}y"}
    assert editor.loads_tolerant('{"a": "x\\ny"}') == {"a": "x\ny"}
    assert editor.loads_tolerant('{"a": {"b": [1, {"c": 2}]}}') == \
        {"a": {"b": [1, {"c": 2}]}}
    # 真的读不懂时必须**放弃**,而不是硬凑一个出来
    assert editor.loads_tolerant("not json at all") is None
    assert editor.loads_tolerant('{"a": ') is None


def test_the_parser_only_adds_what_the_structure_says_is_missing():
    """保守:它只补文本自身结构表明缺了的括号,别的错仍然失败。"""
    import editor

    assert editor.loads_tolerant('{"a": [1, 2') == {"a": [1, 2]}
    assert editor.loads_tolerant('{"a": 1}') == {"a": 1}
    assert editor.loads_tolerant("这不是 JSON") is None
    assert editor.loads_tolerant('{"a": ') is None


def test_an_unreadable_reply_raises_instead_of_reporting_no_change(monkeypatch):
    """**这条是重点。** 读不懂不能变成"方法决定不改"。

    两者是完全不同的事件,把第二个折进第一个,等于在曲线上放了一个没人做过的决定。
    现在它抛 `UnparseableReply`,调用方的 `except Exception` 会走 `editor.fail`,
    记录成一次方法错误,原因留在 trajectory 里。
    """
    import editor

    monkeypatch.setenv("HG_METHOD_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("HG_METHOD_API_KEY", "x")
    monkeypatch.setenv("HG_METHOD_MODEL", "m")
    monkeypatch.setenv("HG_METHOD_PARSE_RETRIES", "1")

    class _Msg:
        content = "I refuse to answer in JSON."

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kw):
            return _Resp()

    class _Client:
        def __init__(self, **kw):
            self.chat = type("C", (), {"completions": _Completions()})()

    import openai
    monkeypatch.setattr(openai, "OpenAI", _Client)
    with pytest.raises(editor.UnparseableReply) as exc:
        editor.ask("prompt")
    assert "refuse" in str(exc.value), exc.value


def _cli_harness(tmp_path: Path, monkeypatch) -> Path:
    """A harness whose `round.json` resolved a CLI improver, so `ask` dispatches to it.

    **No `HG_METHOD_*` is set, and that is the point of this helper now.** It used to set all
    three, because `ask` called `model_settings()` *before* it dispatched
    (`methods/editor.py:1069`), so a CLI improver was asked for endpoint credentials it does
    not use -- the same conflation of the two improver layers that the `no_change` collapse
    below is. The call moved below the dispatch, so the CLI branch no longer touches an
    endpoint; leaving the variables unset is what keeps that true, and every test using this
    helper fails with `SystemExit: a method needs HG_METHOD_BASE_URL ...` if it comes back.
    """
    base = _write_harness(tmp_path / "h")
    (base / "_harnessgrad" / "round.json").write_text(json.dumps(
        {"identity": {"improver": {"name": "codex", "kind": "cli", "path": "/x/codex.js"}}}))
    for name in ("HG_METHOD_BASE_URL", "HG_METHOD_API_KEY", "HG_METHOD_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return base


def _stub_cli(monkeypatch, *, exit_code: int, note: str, files: list | None = None,
              tmp_path: Path | None = None) -> None:
    import cli_improver

    def stub(*_a, **_k):
        return {"candidate": (tmp_path or Path("/nonexistent")), "changed": files or [],
                "files": files or [], "protected": [], "exit_code": exit_code,
                "seconds": 0.1, "note": note, "reported": {}, "transcript": "…"}

    monkeypatch.setattr(cli_improver, "propose", stub)


def test_a_crashed_cli_improver_raises_instead_of_reporting_no_change(tmp_path, monkeypatch):
    """**CLI 那条路漏了同一条规则,而上面那条测试只守住了 endpoint 那条。**

    `UnparseableReply` 的 docstring 自己写着两者的区别:*"collapsing the second into the
    first puts a decision in the curve that nobody made."* `_ask_cli` 正是那个 collapse:
    improver 退出码非 0、一个文件没改,它却把 `result["note"]`(improver 打印的一段 prose)
    包成 `{"no_change": <prose>}` 返回。

    实测(`v12-dgm`,2026-10-05):improver 是 codex CLI,三轮都只打印 prose、没改文件,
    于是 `"no_change" in reply` 成立、`edits=[]`,曲线上写的是 "DGM: no child produced"。
    同仓库的 `cli_improver.run` 对**同一情形**记的是 error(`if code != 0 and not changed`),
    两条路不一致 —— 这条测试钉住它们一致。
    """
    import editor
    monkeypatch.delenv("HG_IMPROVER", raising=False)
    base = _cli_harness(tmp_path, monkeypatch)
    _stub_cli(monkeypatch, exit_code=1, note="Let me check the index files differently:",
              tmp_path=tmp_path)
    with pytest.raises(editor.UnparseableReply) as exc:
        editor.ask("prompt", base=base, parent=base)
    assert "exited 1" in str(exc.value), exc.value
    assert "index files" in str(exc.value), "原因必须留在异常里,不能丢掉"


def test_a_cli_improver_that_declines_is_still_a_no_change(tmp_path, monkeypatch):
    """**不要修过头。** 干净退出、没改文件 = improver 真的决定不改,这是合法答案。

    `cli_improver` 的 prompt 要求 `no_change` 必须点名机制,所以那段 prose 就是理由本身。
    把这一条也抛掉,等于把"方法决定不改"变成"调用坏了",是反方向的同一个错误。
    """
    import editor
    monkeypatch.delenv("HG_IMPROVER", raising=False)
    base = _cli_harness(tmp_path, monkeypatch)
    _stub_cli(monkeypatch, exit_code=0, note="no_change: the retry cap is already 3",
              tmp_path=tmp_path)
    reply = editor.ask("prompt", base=base, parent=base)
    assert "no_change" in reply, reply
    assert "retry cap" in str(reply["no_change"]), reply


def test_a_file_the_cli_improver_deleted_is_carried_as_a_deletion(tmp_path, monkeypatch):
    """**删除是编辑,不是读取失败。** `_changed_files` 会遍历 pristine 侧、点名候选里已经不在
    的文件,所以删除会送到 `_ask_cli` —— 而它曾用 `except OSError: continue` 把删除吞掉。
    后果是静默的:一轮里删一个文件又改了另一个,被测的树里那个删掉的文件**还在**。

    这个 stub 用 candidate 里不存在的路径来表示"被删掉"`——`正是 `_changed_files` 报出来的
    形状。
    """
    import editor
    monkeypatch.delenv("HG_IMPROVER", raising=False)
    base = _cli_harness(tmp_path, monkeypatch)
    candidate = tmp_path / "cand"
    candidate.mkdir()
    (candidate / "agent.py").write_text("x = 1\n", encoding="utf-8")
    _stub_cli(monkeypatch, exit_code=0, note="removed the dead module",
              files=["agent.py", "dead.py"], tmp_path=candidate)

    reply = editor._ask_cli("prompt", base, base,
                            {"name": "dsh", "argv_style": "dsh"})

    by_path = {edit["path"]: edit for edit in reply["files"]}
    assert by_path["dead.py"] == {"path": "dead.py", "delete": True}, reply
    assert by_path["agent.py"]["content"] == "x = 1\n", reply


def test_a_deletion_only_round_is_not_recorded_as_no_change(tmp_path, monkeypatch):
    """只删了一个文件的轮次曾经变成 `{"no_change": ...}`。

    那是 `UnparseableReply` 的 docstring 禁止的那个 collapse,低一层再犯一次:一次真的做出来
    的改动被记成"improver 决定不改",而记录里分不出它和一个真的什么都没找到的轮次。
    """
    import editor
    monkeypatch.delenv("HG_IMPROVER", raising=False)
    base = _cli_harness(tmp_path, monkeypatch)
    candidate = tmp_path / "cand"
    candidate.mkdir()
    _stub_cli(monkeypatch, exit_code=0, note="deleted the dead module",
              files=["dead.py"], tmp_path=candidate)

    reply = editor._ask_cli("prompt", base, base,
                            {"name": "dsh", "argv_style": "dsh"})

    assert "no_change" not in reply, reply
    assert reply["files"] == [{"path": "dead.py", "delete": True}], reply


def _improver_workspace_stub(transcript: str = "I read the round-0 traces and changed agent.py.",
                             pin: str = "model: deepseek-flash\n"):
    """A `propose` that behaves like the real one about the two files it leaves behind."""
    def stub(req, *_a, **_k):
        workspace = Path(req["workspace"])
        (workspace / "agent.py").write_text("x = 1\n", encoding="utf-8")
        if pin:
            (workspace / ".dsh-model-pin.yml").write_text(pin, encoding="utf-8")
        return {"candidate": workspace, "changed": ["agent.py"], "files": ["agent.py"],
                "protected": [], "exit_code": 0, "seconds": 1.0, "note": "edited the parser",
                "reported": {}, "transcript": transcript}
    return stub


def test_the_improver_transcript_lands_where_the_platform_keeps_logs(tmp_path, monkeypatch):
    """**transcript 必须落在平台会去拷的地方,否则它等于没写。**

    `_keep_method_logs` 扫的是**方法 workspace** 根目录的 `*.log`;而 improver 的 transcript
    和 model pin 都写在 `_ask_cli` 自己 `mkdtemp` 出来的目录里 —— 那个目录在
    `eval/containers/sandbox.py` 挂的**空 tmpfs** `/tmp` 上("a private /tmp, so two runs
    cannot read each other's task directories"),随 namespace 一起消失。实测:宿主上活下来的
    12 个 `hg-cli-ask-*` 目录**全是空的**,而且全来自测试运行(测试把 `propose` 打了桩,所以
    留下目录留不下文件);真跑的轮次连目录都不剩。

    代价是"improver 到底读了 trace 还是猜的"这个问题**无法回答** —— 而比较这些*方法*,问的
    主要就是它。
    """
    import cli_improver
    import editor
    monkeypatch.delenv("HG_IMPROVER", raising=False)
    base = _cli_harness(tmp_path, monkeypatch)
    method_ws = tmp_path / "method_ws"
    method_ws.mkdir()
    monkeypatch.setattr(editor, "_method_workspace", lambda: method_ws)
    monkeypatch.setattr(cli_improver, "propose", _improver_workspace_stub())

    editor._ask_cli("prompt", base, base, {"name": "dsh", "argv_style": "dsh"})

    logs = sorted(p.name for p in method_ws.glob("*.log"))
    assert len(logs) == 2, logs
    transcript = next(p for p in method_ws.glob("*.log") if "model-pin" not in p.name)
    assert "read the round-0 traces" in transcript.read_text()
    pin = next(p for p in method_ws.glob("*.log") if "model-pin" in p.name)
    assert "deepseek-flash" in pin.read_text()


def test_what_the_improver_left_behind_survives_the_round(tmp_path, monkeypatch):
    """和真正的消费者对起来:`_keep_method_logs` 必须真的把这些文件拷进 record。

    只断言"文件写出来了"是不够的 —— 这个缺陷的形状恰恰就是"写出来了,但没人从那里拷"。
    """
    import cli_improver
    import editor
    from harnessgrad.records import _keep_method_logs
    monkeypatch.delenv("HG_IMPROVER", raising=False)
    base = _cli_harness(tmp_path, monkeypatch)
    method_ws = tmp_path / "method_ws"
    method_ws.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(editor, "_method_workspace", lambda: method_ws)
    monkeypatch.setattr(cli_improver, "propose", _improver_workspace_stub())

    editor._ask_cli("prompt", base, base, {"name": "dsh", "argv_style": "dsh"})
    _keep_method_logs(method_ws, run_dir, 1)

    kept = sorted(p.name for p in (run_dir / "method_logs" / "round-1").iterdir())
    assert len(kept) == 2, kept
    text = "\n".join((run_dir / "method_logs" / "round-1" / n).read_text() for n in kept)
    assert "read the round-0 traces" in text
    assert "deepseek-flash" in text


def test_the_hypothesis_is_found_wherever_the_model_put_it():
    """实测:要的是顶层 `hypothesis`,模型塞进了 file 对象里,于是标签是空的 ——
    一次真实的 8 KB 编辑被记成了"没有理由"。两处都读。"""
    import editor

    top = {"files": [{"path": "a.py", "content": "x"}], "hypothesis": "顶层"}
    nested = {"files": [{"path": "a.py", "content": "x", "hypothesis": "嵌套"}]}
    assert editor.hypothesis_of(top) == "顶层"
    assert editor.hypothesis_of(nested) == "嵌套"
    assert editor.hypothesis_of({"files": []}) == ""


# ------------------------------------------- 不再要求模型转义整个文件 ---

def test_a_fenced_file_block_is_the_preferred_reply_format():
    """把整个文件塞进 JSON 字符串,是所有解析失败的共同来源。

    模型要在 8 KB 的程序里转义每一个引号、换行和反斜杠,而 9B 模型做不到。本机实测过的
    每一种坏法 —— 缺括号、多括号、括号类型错、裸控制字符、非法 `\\d` 转义、未转义的引号
    —— 归根到底都是同一个问题:文件自己的引号必须活过第二层编码。

    围栏块没有第二层编码:文件原样写出来。JSON 仍然接受,所以没有东西被破坏。
    """
    import editor

    assert editor.parse_reply("```file:a.py\nprint(1)\n```\nHYPOTHESIS: h") == {
        "files": [{"path": "a.py", "content": "print(1)\n"}], "hypothesis": "h"}
    # 前面有散文也要认
    assert editor.parse_reply("Sure:\n```file:a.py\nx = 1\n```\nHYPOTHESIS: y") == {
        "files": [{"path": "a.py", "content": "x = 1\n"}], "hypothesis": "y"}
    # 多个文件
    two = editor.parse_reply("```file:a.py\nA\n```\n```file:b.py\nB\n```\nHYPOTHESIS: t")
    assert [f["path"] for f in two["files"]] == ["a.py", "b.py"]
    # 显式拒绝
    assert editor.parse_reply("NO_CHANGE: nothing to do") == {
        "no_change": "nothing to do"}


def test_the_fenced_format_survives_what_json_could_not():
    """这条是它存在的全部理由:一段真实的 Python,里面全是引号、反斜杠和正则。

    同一段内容如果要求模型写成 JSON 字符串,本机实测会坏;写在围栏里则逐字保留。
    """
    import editor

    source = ('import re\n'
              'PAT = re.compile(r"\\\\d+\\\\s*")\n'
              'PATH = "C:\\\\data\\\\x"\n'
              'MSG = \'he said "hi"\'\n'
              'if __name__ == "__main__":\n'
              '    print(f"{PAT.pattern} {PATH} {MSG}")\n')
    reply = f"```file:agent.py\n{source}```\nHYPOTHESIS: use a regex"
    got = editor.parse_reply(reply)
    assert got["files"][0]["content"] == source
    assert got["hypothesis"] == "use a regex"


def test_json_is_still_accepted_so_nothing_that_worked_breaks():
    import editor

    assert editor.parse_reply('{"files":[{"path":"a.py","content":"x"}]}') == {
        "files": [{"path": "a.py", "content": "x"}]}
    # 一个都不是的时候必须返回空 —— 让调用方报成失败,而不是编一个决定出来
    assert editor.parse_reply("I refuse.") == {}


# ------------------------------------------- 改进器要看到"为什么失败" ---

def test_the_prompt_carries_why_each_task_failed(tmp_path):
    """traces 说"它做了什么",任务页说"为什么没通过" —— 少了后者只能猜。

    实测:平台把判分报告和 harness 自己的 stderr 都写进了任务页,而 `llm_improver` 的
    prompt 只有 traces + 源码。结果是模型删掉了 147 行注释、没改行为(`headless-terminal`
    那一轮)。它不是偷懒:那份输入里没有任何一句"哪条测试失败、断言是什么"。
    """
    mod = _load("llm_improver")
    root = _write_harness(tmp_path / "base")
    tasks = root / "_harnessgrad" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / "t01.json").write_text(json.dumps({
        "task_id": "t01", "score": 0.0,
        "verdict": {"kind": "command", "passed": False, "score": 0.0,
                    "detail": "reward 0 | check_report /logs/verifier/ctrf.json "
                              "(1 passed 6 failed) | FAILED test_outputs.py::test_import: "
                              "ModuleNotFoundError: No module named 'headless_terminal'"},
        "harness": {"exit_code": 1,
                    "stderr": "Traceback...\nRuntimeError: 3 attempts failed, last: "
                              "Connection error."},
        "loop": {"ended": "crashed"}}))
    prompt = mod.build_prompt(root, {"round_index": 1, "incumbent_score": 0.0})
    assert "FAILED test_outputs.py::test_import" in prompt
    assert "No module named 'headless_terminal'" in prompt
    assert "Connection error" in prompt


def _capture_messages(monkeypatch, reply='{"no_change": "stub"}'):
    """装一个假的 openai client,把**真正发出去的 messages** 抓下来。"""
    sent: list[list[dict]] = []

    class _Msg:
        content = reply

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        model = "stub"
        usage = None

    class _Completions:
        def create(self, **kw):
            sent.append(kw.get("messages") or [])
            return _Resp()

    class _Client:
        def __init__(self, **kw):
            self.chat = type("C", (), {"completions": _Completions()})()

    import openai
    monkeypatch.setattr(openai, "OpenAI", _Client)
    return sent


#: The header `editor.skill_block` puts in front of whatever the skill says. Counted rather
#: than the phrase "How to improve a harness", because a skill's own first line is often
#: exactly that phrase and then a correct injection looks like a double injection.
SKILL_HEADER = "## How to improve a harness (the platform's skill)"


def _staged_skill(tmp_path) -> Path:
    root = _write_harness(tmp_path / "base")
    (root / "_harnessgrad" / "SKILL.md").write_text("# XYZ skill\nBODY-MARKER\n")
    return root


def test_the_editor_injects_the_platform_skill_by_default(tmp_path, monkeypatch):
    """**默认继承,而不是每个方法自己记得读。**

    skill 由平台放进通道(`harnessgrad/channel.py`),而曲线点上的 `identity.skill_sha`
    说的是"这次给了方法哪份说明书"。在这条路修好之前,12 个方法里只有 4 个真的把它读进
    prompt —— 于是那个字段在另外 8 个方法上是在替一份**没有被用到的** skill 打包票。
    现在注入发生在 `editor.ask` 里,一次,所有方法都继承。
    """
    import editor

    root = _staged_skill(tmp_path)
    monkeypatch.setenv("HG_METHOD_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("HG_METHOD_API_KEY", "x")
    monkeypatch.setenv("HG_METHOD_MODEL", "m")
    sent = _capture_messages(monkeypatch)

    editor.ask("do the thing", base=root)
    blob = "\n".join(m["content"] for m in sent[-1])
    assert SKILL_HEADER in blob and "BODY-MARKER" in blob
    assert "do the thing" in blob


def test_the_skill_is_injected_once_even_though_methods_compose_prompts(tmp_path,
                                                                       monkeypatch):
    """方法自己**不该**再贴一份:两份会让 prompt 和点上的 `skill_sha` 互相打脸。"""
    import editor

    mod = _load("llm_improver")
    root = _staged_skill(tmp_path)
    own_prompt = mod.build_prompt(root, {"round_index": 2, "incumbent_score": 0.0})
    assert SKILL_HEADER not in own_prompt, "方法又把 skill 贴回来了"
    assert "BODY-MARKER" not in own_prompt, "方法又把 skill 正文贴回来了"

    monkeypatch.setenv("HG_METHOD_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("HG_METHOD_API_KEY", "x")
    monkeypatch.setenv("HG_METHOD_MODEL", "m")
    sent = _capture_messages(monkeypatch)
    editor.ask(own_prompt, base=root)
    blob = "\n".join(m["content"] for m in sent[-1])
    assert blob.count(SKILL_HEADER) == 1, blob.count(SKILL_HEADER)
    assert blob.count("BODY-MARKER") == 1, blob.count("BODY-MARKER")


def test_a_method_can_decline_the_skill_explicitly(tmp_path, monkeypatch):
    """不读要**显式声明** —— 这是这次改动的另一半:默认继承,退出是一个写出来的参数。"""
    import editor

    root = _staged_skill(tmp_path)
    monkeypatch.setenv("HG_METHOD_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("HG_METHOD_API_KEY", "x")
    monkeypatch.setenv("HG_METHOD_MODEL", "m")
    sent = _capture_messages(monkeypatch)

    editor.ask("do the thing", base=root, skill=False)
    blob = "\n".join(m["content"] for m in sent[-1])
    assert SKILL_HEADER not in blob and "BODY-MARKER" not in blob
    assert "do the thing" in blob


def test_no_staged_skill_means_no_block(tmp_path):
    """没有 skill 就不注入空壳 —— 和点上省略 `skill_sha` 是同一条规则。"""
    import editor

    root = _write_harness(tmp_path / "bare")
    assert editor.skill_block(root) == ""
    assert editor.skill_block(None) == ""


def test_an_oversized_skill_is_refused_not_truncated(tmp_path):
    """超长的 skill 现在是**拒绝**,不是截断(9681385e,2026-10-20)。

    截断保护的是 argv 上限,伤的却是这一轮最需要的那一节:实测那份 skill 8407 字符而当时上限
    6000,而关键一节正好在 5595。被截一半的说明书交给一个没法自己翻盘的读者,比没有说明书更糟,
    所以改成抛 `EvidenceTooLarge` —— 这条测试改成断言那个拒绝,而不是断言提示串。
    """
    import pytest

    import editor

    root = _write_harness(tmp_path / "long")
    (root / "_harnessgrad" / "SKILL.md").write_text("A" * (editor.SKILL_LIMIT + 500))
    with pytest.raises(editor.EvidenceTooLarge) as caught:
        editor.skill_block(root)
    assert str(editor.SKILL_LIMIT) in str(caught.value)

    # 恰好等于上限的 skill 仍然原样通过:拒绝的是"看不全",不是"长"。
    (root / "_harnessgrad" / "SKILL.md").write_text("B" * editor.SKILL_LIMIT)
    assert editor.skill_block(root).count("B") == editor.SKILL_LIMIT


def test_a_prompt_without_pages_still_builds(tmp_path):
    """老通道(没有 tasks/ 也没有 SKILL.md)不能把方法搞崩。"""
    mod = _load("llm_improver")
    root = _write_harness(tmp_path / "base")
    prompt = mod.build_prompt(root, {"round_index": 1, "incumbent_score": 0.0})
    # 方法自己的 prompt 现在只讲它的规则;要求(含 no_change 的代价)由平台统一加,
    # 所以这里断言的是一个**不再重复**的最小 prompt,而不是它自己拼出来的证据节。
    assert "## Current harness sources" not in prompt
    import cli_improver
    full = cli_improver.instruction(prompt, facts="FACTS", evidence="EVIDENCE")
    assert "no_change" in full and "FACTS" in full and "EVIDENCE" in full


# --------------------------------------------- 方法自己的 token 进 cost ---

def test_the_methods_own_tokens_reach_the_cost_record(tmp_path, monkeypatch):
    """**实测过的 0。** 方法自己调模型的 token 以前永远是 0。

    平台早就有 `cost.method_generation_tokens` 这个字段,README 里也写着成本是三个账本
    (harness / method / env)。但它一直是 0 —— 不是"方法没花钱",而是**没人把数传下来**:
    `editor.ask` 拿到的 `usage` 在函数返回时就没了,`report(tokens=)` 默认 0,而每个方法
    都得自己记得传。一个读常数的成本规则允许一切,所以这不是美化字段,是修一条规则。

    这条测试同时钉住"观测"和"声明"两件事:重试那次失败的调用也要算钱。
    """
    import editor

    monkeypatch.setenv("HG_METHOD_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("HG_METHOD_API_KEY", "x")
    monkeypatch.setenv("HG_METHOD_MODEL", "m")
    monkeypatch.setenv("HG_METHOD_PARSE_RETRIES", "2")

    class _Usage:
        prompt_tokens, completion_tokens, model = 900, 100, "m-real"

    class _Resp:
        model = "m-real"
        usage = _Usage()

        def __init__(self, text):
            self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]

    # 第一次读不懂,第二次成功 —— 两次都花了钱。
    replies = ["嗯…这个", '{"no_change": "算了"}']

    class _Completions:
        def create(self, **kw):
            return _Resp(replies.pop(0))

    class _Client:
        def __init__(self, **kw):
            self.chat = type("C", (), {"completions": _Completions()})()

    import openai
    monkeypatch.setattr(openai, "OpenAI", _Client)

    editor.reset_spent()
    reply = editor.ask("prompt")
    assert reply == {"no_change": "算了"}
    assert replies == [], "第二次调用必须真的发生,否则这条测试没在测重试"
    assert editor.spent() == {"input": 1800, "output": 200, "calls": 2,
                              "model": "m-real"}

    emitted = []
    monkeypatch.setattr(editor, "emit", emitted.append)
    req = {"workspace": str(tmp_path), "trajectory_out": str(tmp_path / "traj.json")}
    editor.report(req, method="probe", harness_dir=tmp_path, changed=False,
                  hypothesis="算了")
    assert emitted[-1]["generation_tokens"] == 2000, emitted[-1]
    assert emitted[-1]["method_usage"]["calls"] == 2
    traj = json.loads((tmp_path / "traj.json").read_text())
    claimed = traj["steps"][0]["claimed_cost"]
    assert claimed["generation_tokens"] == 2000
    assert (claimed["method_input_tokens"], claimed["method_output_tokens"]) == (1800, 200)
    assert claimed["method_model_calls"] == 2
    assert claimed["method_model"] == "m-real"


def test_a_method_that_made_no_call_reports_a_real_zero(tmp_path, monkeypatch):
    """显式传 0 仍然是 0 —— 参考方法 `echo_base` 不改 harness、不调模型,这是事实不是缺省。"""
    import editor

    emitted = []
    monkeypatch.setattr(editor, "emit", emitted.append)
    editor.reset_spent()
    editor.report({"workspace": str(tmp_path)}, method="echo_base", harness_dir=tmp_path,
                  changed=False, hypothesis="no change", tokens=0)
    assert emitted[-1]["generation_tokens"] == 0
    assert emitted[-1]["method_usage"]["calls"] == 0


def test_the_staged_improver_says_whether_it_is_a_cli():
    """`identity.improver.kind` 必须出现在 `round.json` 里。

    实测(`v2-dgm`,2026-10-05):`kind` 不在 `IMPROVER_IDENTITY_FIELDS` 里,于是方法从
    `round.json` 读到的 improver 块永远没有这个键,`cli_improver_identity` 永远回答"不是
    CLI",每个方法都退回到 chat endpoint —— codex 被解析了、被探测了、**从未被启动**。记录
    里少一个字段,挡掉的是整个 CLI 层,而曲线上只看得出"这个方法改得很粗糙"。
    """
    from harnessgrad.identity import _improver_identity

    got = _improver_identity({"name": "codex", "kind": "cli", "path": "/x/codex.js",
                              "ok": True, "reason": ""})
    assert got["kind"] == "cli", "kind 没进记录,方法就分不出 CLI 和 endpoint"
    assert "ok" not in got and "reason" not in got, "bookkeeping 不该进记录"


def test_a_cli_improver_is_dispatched_from_the_staged_record(tmp_path):
    """按记录里的形状判断 CLI:有 `path`、没有 `base_url`。

    这条是**旧记录**的兼容:已经写完的 `round.json` 不会有 `kind`。
    """
    import editor

    root = _write_harness(tmp_path / "h")
    (root / "_harnessgrad" / "round.json").write_text(json.dumps(
        {"identity": {"improver": {"name": "codex", "path": "/x/codex.js"}}}))
    assert editor.cli_improver_identity(root) is not None, "旧记录(没有 kind)也要认出来"


def test_an_endpoint_improver_is_not_dispatched_as_a_cli(tmp_path):
    """endpoint 的解析里 `path` 和 `base_url` **都有**(`tools/improver.py:resolve`)。

    把它当 CLI 的后果不是"没效果",是拿一个 URL 去 exec。
    """
    import editor

    # **用平台真正写记录的那个函数来构造**,而不是手搓一个 dict。上一版这条测试手写了
    # `base_url`,而平台从来不写它(`IMPROVER_IDENTITY_FIELDS` 里没有),于是一条通过的测试
    # 掩盖了真实形状会走到 CLI 分支:`--improver deepseek` 去 `Path("https://api.deepseek.com/v1")`,
    # 整轮以 "the platform named an improver at ... which does not exist inside this sandbox" 死掉。
    from harnessgrad.identity import _improver_identity

    root = _write_harness(tmp_path / "h")
    recorded = _improver_identity({"name": "deepseek", "kind": "openai-compatible",
                                   "path": "https://api/v1", "base_url": "https://api/v1",
                                   "model": "deepseek-flash"})
    assert recorded.get("base_url") == "https://api/v1", "base_url 没进记录,端点和 CLI 就分不开"
    (root / "_harnessgrad" / "round.json").write_text(json.dumps(
        {"identity": {"improver": recorded}}))
    assert editor.cli_improver_identity(root) is None


def test_the_parents_sources_are_readable_from_inside_the_channel(tmp_path):
    """父状态在 `_harnessgrad/states/round-N/` 里,`load_sources` 必须还能读到它的源码。

    实测(`v3-dgm`,2026-10-05):这里用**绝对路径的 parts** 判断 `_harnessgrad`,而 staged
    state 的绝对路径**必然**含这个词 —— 于是每个文件都被跳过,给改进器的 prompt 里
    "## The parent's sources" 一节永远是**空的**(20,313 字符的指令里那一节只有 43 字节)。
    改进器被要求改一个它没看过的 harness,后果两种都实测到了:

    * 聊天模型凭想象写出整个新 harness:635 行删成 179 行,连 `--task`/`--workdir` 约定
      都丢了,两轮十道题全部启动即崩;
    * codex 什么都不改(它甚至看不到有什么可改)。

    同一类错误之前已经在 traces 上犯过一次(那次是 `load_traces(parent)`,parent 也在
    通道里)。判据必须是**相对** root 的路径。
    """
    import editor

    root = _write_harness(tmp_path / "h")                  # harness 树 + 通道
    state = root / "_harnessgrad" / "states" / "round-0"   # 平台 stage 父状态的位置
    state.mkdir(parents=True)
    (state / "agent.py").write_text("print('parent')\n")
    (state / "requirements.txt").write_text("openai\n")
    (state / "_harnessgrad").mkdir()                       # 嵌套的通道仍然不许泄漏
    (state / "_harnessgrad" / "secret.json").write_text("{}")

    got = editor.load_sources(state)
    assert "agent.py" in got, "staged state 的源码读不到 —— 给改进器的 sources 一节会是空的"
    assert "requirements.txt" in got
    assert not any("_harnessgrad" in k for k in got), "通道里的文件泄进 prompt"

    top = editor.load_sources(root)
    assert "agent.py" in top and not any("_harnessgrad" in k for k in top)


def test_the_entrypoint_reaches_the_prompt_whole():
    """**参考 harness 的入口必须完整地进 prompt。**

    这条数字错过两次,错过的方式一样:先是 12000(模型交回来的文件连 `def main()` 都没了),
    提到 24000 时理由是"能放下整个参考 harness" —— 而到 2026-10-05 它是 28942 **字符**
    (34699 字节,注释是中文,所以字节数掩盖了它),于是 24000 把文件切在第 533/636 行,
    而文件里**所有做决定的代码**都在被切掉的那一半:重复提醒(583)、收尾提醒(595)、
    交卷被拒(551)、`_trim_history` 调用(602)。

    实测八轮(`v4`-`v10`):**8 次改动里 7 次落在前 533 行,其中 6 次是改常量**;两次掉分都是
    常量改动;唯一一次落在被切区域里的改动是唯一一次非常量改动,也是唯一一次无害的。
    改进器改在 prompt 给它看的地方,而 prompt 给它看的是旋钮。
    """
    import editor

    src = editor.load_sources(ROOT / "base_harness" / "loop")
    assert "TRUNCATED" not in src["agent.py"], "入口又被截断了"
    original = (ROOT / "base_harness" / "loop" / "agent.py").read_text()
    assert src["agent.py"] == original, "入口给模型看的内容必须和磁盘上逐字节一致"
    # 被切掉的那一半里的机制,现在必须在
    for needle in ("cmd == last_cmd", "finish_nudge", "submit_rejected", "dropped = _trim_history"):
        assert needle in src["agent.py"], f"入口里的 {needle!r} 没进 prompt"


def test_the_total_cap_trims_other_files_before_the_entrypoint(tmp_path):
    """总长上限必须先削**别的**文件,入口放到最后 —— 半个入口正是历次跑偏的原因。

    `strict=True`(默认)在总量超限时直接拒绝(9681385e:看不全就不要请人重写)。这个"先削别人、
    不削入口"的兜底仍然存在于 `strict=False` 那条路上 —— 它就是给"必须给点东西"的场景留的,
    算法本身没有被删掉,所以这里用 `strict=False` 测算法。
    """
    import editor

    root = tmp_path / "h"
    root.mkdir()
    (root / "harness.json").write_text(json.dumps(
        {"name": "probe", "version": "1", "entrypoint": "agent.py"}))
    entry = "print('the entrypoint')\n" * 40          # 约 880 字符,完整放得下
    (root / "agent.py").write_text(entry)
    for name in ("big_one.py", "big_two.py"):
        (root / name).write_text("# padding\n" * 4000)  # 每个约 40000 字符

    src = editor.load_sources(root, total=3000, strict=False)
    assert src["agent.py"] == entry, "入口被削了 —— 总长上限削错了对象"
    assert "TRUNCATED" in src["big_one.py"] or "TRUNCATED" in src["big_two.py"], (
        "总长超了却什么都没削,会把整条指令顶出 argv 上限")
    assert sum(len(v) for v in src.values()) < 3 * 3000

    # 默认严格模式对同一棵树是拒绝,而不是悄悄给一半。
    import pytest
    with pytest.raises(editor.EvidenceTooLarge):
        editor.load_sources(root, total=3000)


def test_the_edits_land_on_the_parent_the_rule_chose(tmp_path, monkeypatch):
    """**`parent` 是编辑落地的树,不是 `base`。**

    `ask_for_json(..., parent=parent)` 已经把 **parent** 交给 CLI improver 去改,所以把返回的
    编辑序列应用到别处,产出的候选就不是规则要的那一棵 —— 而是 `incumbent + parent 的文件`。
    今天没有调用方传非 incumbent 的 parent(所以是潜伏的),`methods/base.py` 直接自己
    `apply_edits(rnd.parent, ...)` 绕开了这里;但下一个会选旧状态的规则正是这个参数存在的理由。
    """
    import editor

    base = tmp_path / "base"; base.mkdir()
    (base / "harness.json").write_text(json.dumps(
        {"name": "h", "version": "1", "entrypoint": "agent.py"}))
    (base / "agent.py").write_text("OLD = 1\n")
    parent = tmp_path / "parent"; parent.mkdir()
    (parent / "harness.json").write_text(json.dumps(
        {"name": "h", "version": "1", "entrypoint": "agent.py"}))
    (parent / "agent.py").write_text("OLD = 2\n")
    (parent / "extra.py").write_text("PARENT_ONLY = True\n")

    req = {"workspace": str(tmp_path / "ws"), "base_harness": str(base),
           "candidate": str(tmp_path / "ws" / "candidate")}

    def fake_ask_for_json(prompt_for, **kw):
        # 模拟 improver 只改 agent.py:应用必须落在 parent 上
        reply = {"files": [{"path": "agent.py", "content": "NEW = 3\n"}]}
        problems = kw["accept"](reply)
        return reply, problems

    monkeypatch.setattr(editor, "ask_for_json", fake_ask_for_json)
    _, dest, problems = editor.propose_and_apply(
        base, req, "system", lambda p: "prompt", parent=parent)
    assert not problems, problems
    # parent 独有的文件必须跟着过来 —— 这正是"落在 base 上"会丢掉的东西
    assert (dest / "extra.py").is_file(), sorted(p.name for p in dest.iterdir())
    assert (dest / "agent.py").read_text() == "NEW = 3\n"
