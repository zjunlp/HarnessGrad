#!/usr/bin/env python3
"""检查一个方法是平台能调用、且输出能被平台测量的。

    python3 tools/validate_method.py methods/llm_improver
    python3 tools/validate_method.py methods/sica_ci --harness base_harness/loop
    python3 tools/validate_method.py methods/mine --smoke     # 只查结构,不调用

为什么需要它
------------
`validate_harness.py` 校验被测量的那一半;这是对称的另一半。方法是平台不写的代码,
而"我的方法合规吗"应该能用运行一个命令来回答,而不是靠读一遍 contract 文档。

对平台来说更实际的理由:**不合规的方法会在花钱之后才暴露**。一次真实实验要跑模型、
花几分钟到几十分钟,而一个把响应写在 stderr 上、或者 `harness_dir` 指向一个没有
`harness.json` 的目录的方法,会让整轮白跑 —— 而且失败长得很像"实验做完了但分数没动"。

检查是分层的,因为不同的问题在不同成本上暴露:

  1. **结构**(免费)  — 文件在不在、入口能不能被找到
  2. **调用**(便宜)  — 用假请求调一次,看它答不答、答的是不是 JSON
  3. **契约**(便宜)  — 响应字段、trajectory 格式、`harness_dir` 是否合规
  4. **冒烟**(较贵)  — `--smoke` 让平台真的测一次(会调模型,所以默认不做)

第 3 层是价值最大的:它把"方法说自己改了 harness"和"它真的产出了一个能被测量的
harness"区分开 —— 这正是平台上最贵的一类错误。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from methods.protocol import PLATFORM_API_VERSION        # noqa: E402

OK, WARN, FAIL = "ok", "warn", "fail"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, level: str, title: str, detail: str = "") -> None:
        self.rows.append((level, title, detail))

    @property
    def failed(self) -> bool:
        return any(l == FAIL for l, _, _ in self.rows)

    def print(self) -> None:
        mark = {OK: "\033[32m✓\033[0m", WARN: "\033[33m!\033[0m", FAIL: "\033[31m✗\033[0m"}
        for level, title, detail in self.rows:
            print(f"  {mark[level]} {title}")
            if detail:
                for line in str(detail).splitlines():
                    print(f"      {line}")


def build_request(harness: Path, work: Path, traj: Path) -> dict:
    """平台会发的那种请求。字段与 `methods/protocol.py` 的说明一致。"""
    tasks = []
    try:
        import data.scaffolds.registry as registry
        tasks, _ = registry.load("demo")
        tasks = [t["task_id"] for t in tasks[:2]]
    except Exception:                                       # noqa: BLE001
        tasks = ["t01"]
    return {
        "platform_api_version": PLATFORM_API_VERSION,
        "mode": "A",
        "base_harness": str(harness.resolve()),
        "workspace": str(work.resolve()),
        "round_index": 1,
        "incumbent_score": 0.0,
        "task_ids": tasks,
        "trajectory_out": str(traj.resolve()),
    }


def check_structure(method: Path, rep: Report) -> bool:
    entry = method / "run.py"
    if not method.is_dir():
        rep.add(FAIL, f"方法目录不存在:{method}")
        return False
    if not entry.exists():
        rep.add(FAIL, "缺少 run.py", "入口必须是 run.py —— 平台按这个名字调用")
        return False
    rep.add(OK, f"找到 {entry.relative_to(ROOT)}({len(entry.read_text().splitlines())} 行)")

    # 方法可以用平台的 protocol 助手,也可以自己解析 stdin;两种都合规。这里只提示
    # 用的是哪种,因为自己解析的那条路要自己保证"stdout 只有那一个 JSON"。
    #
    # `import editor` 同样算走过这条路:editor 是平台给方法用的共享层,它自己
    # `from protocol import emit, read_request, require_api`。七个移植方法都这么用,
    # 而这条检查以前只认字面量 `protocol`,于是把**推荐的**用法报成了可疑用法。
    text = entry.read_text()
    uses_helper = any(tok in text for tok in
                      ("from protocol import", "import protocol",
                       "from editor import", "import editor"))
    if uses_helper:
        rep.add(OK, "使用平台的 protocol / editor 助手(推荐)")
    else:
        rep.add(WARN, "没有用 methods/protocol.py",
                "合规,但你要自己保证 stdout 上只有那一个 JSON 对象;"
                "任何 print 都会让平台的解析失败")
    return True


def check_invocation(method: Path, harness: Path, rep: Report,
                     timeout: int) -> dict | None:
    """真的调一次。假请求,但走完整的进程边界。"""
    entry = method / "run.py"
    with tempfile.TemporaryDirectory(prefix="hg-validate-") as tmp:
        tmpdir = Path(tmp)
        work = tmpdir / "ws"
        work.mkdir()
        traj = tmpdir / "trajectory.json"
        request = build_request(harness, work, traj)

        proc = subprocess.run(
            [sys.executable, str(entry)], input=json.dumps(request),
            capture_output=True, text=True, errors="replace", timeout=timeout, cwd=str(ROOT))

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-600:]
            hint = ""
            if "HG_METHOD_" in detail:
                hint = ("\n这看起来是缺少方法自己的模型凭证。方法自带凭证是设计选择,"
                        "\n不是缺陷 —— 校验时可以用 HG_METHOD_BASE_URL/API_KEY/MODEL "
                        "提供,或只跑 --no-call 看结构。")
            rep.add(FAIL, f"调用失败(退出码 {proc.returncode})", detail + hint)
            return None
        rep.add(OK, "能被调用并正常退出")

        raw = proc.stdout.strip()
        if not raw:
            rep.add(FAIL, "stdout 是空的",
                    "平台从 stdout 读那一个 JSON 对象。把诊断写到 stderr。")
            return None
        try:
            reply = json.loads(raw)
        except json.JSONDecodeError as exc:
            rep.add(FAIL, f"stdout 不是合法 JSON:{exc}",
                    f"开头是:{raw[:200]!r}\n"
                    "常见原因:在 stdout 上 print 了日志,或者写了一个 JSON 数组")
            return None
        rep.add(OK, "stdout 是一个 JSON 对象")

        # 控制字段。`changed` 缺失不算错 —— 平台默认 True,但那意味着它会继续花钱,
        # 所以值得提醒。
        if "changed" not in reply:
            rep.add(WARN, "响应里没有 `changed`",
                    "平台会当作 true 继续下一轮。不打算改变时明确写 false,"
                    "否则会白跑一轮。")
        else:
            rep.add(OK, f"changed = {reply['changed']}")

        if "generation_tokens" not in reply:
            rep.add(WARN, "响应里没有 `generation_tokens`",
                    "方法自己的开销就记不下来了;曲线只剩 harness 那半本账。")

        # trajectory:mode A 里可选(平台用 stdout 的控制字段),但多数方法会写。
        if not traj.exists():
            rep.add(WARN, "没有写 trajectory 文件",
                    "mode A 可以不写,但写了才有逐步记录,面板的每步 diff 和解释"
                    "都依赖它。")
            return reply

        try:
            document = json.loads(traj.read_text())
        except json.JSONDecodeError as exc:
            rep.add(FAIL, f"trajectory 不是合法 JSON:{exc}")
            return reply

        steps = document.get("steps") or []
        rep.add(OK, f"trajectory 有 {len(steps)} 个 step")

        bad_dirs = []
        for i, step in enumerate(steps, 1):
            d = step.get("harness_dir")
            if not d:
                bad_dirs.append(f"step {i}: 没有 harness_dir(平台会跳过这一步)")
                continue
            p = Path(d)
            if not p.is_dir():
                bad_dirs.append(f"step {i}: {d} 不是一个目录")
            elif not (p / "harness.json").exists():
                bad_dirs.append(f"step {i}: {d} 里没有 harness.json —— "
                                f"平台无法测量它")
        if bad_dirs:
            rep.add(FAIL, "有 step 不能被测", "\n".join(bad_dirs[:6]))
        elif steps:
            rep.add(OK, "每个 step 都指向一个合规的 harness 目录")

        if "nominated" in document:
            n = document["nominated"]
            # `0` 和 `None` 是**契约里写明**的"没有提名"(INTERFACE.md §4.3),平台回退到
            # 最后一个 step 并说出来。这不是可疑行为,所以不该报成警告 —— 而且 mode A
            # 根本不读这个字段(plan 拥有调度,没有人提名任何一步)。以前把 `0` 报成
            # "不在 1..N 内",于是每个 mode A 方法都带着一条无意义的警告。
            if n in (0, None) or not isinstance(n, int) or not 1 <= n <= len(steps):
                if n in (0, None):
                    rep.add(OK, f"nominated = {n}(没有提名,平台取最后一个 step)")
                else:
                    rep.add(WARN, f"nominated={n!r} 不在 1..{len(steps)} 内,也不是 0",
                            "平台会退回到最后一个 step")
            else:
                rep.add(OK, f"nominated = {n}")
        return reply


def check_smoke(method: Path, harness: Path, rep: Report, timeout: int) -> None:
    """让平台真的跑一轮。会调模型,所以只在明确要求时做。"""
    import tempfile as tf
    run_id = f"validate-{method.name}"
    with tf.TemporaryDirectory(prefix="hg-smoke-") as tmp:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "driver.py"),
             "--harness", harness.name, "--mode", "A", "--rounds", "1",
             "--run-id", run_id, "--dataset", "demo", "--sampling", "below_0.5",
             "--method-entrypoint", f"{sys.executable} {method / 'run.py'}",
             "--runs-root", tmp, "--work-root", str(Path(tmp) / "work")],
            capture_output=True, text=True, errors="replace", timeout=timeout, cwd=str(ROOT))
        # 没跑出曲线通常是"没有合规候选",这本身是校验要看的信息,不是崩溃
        if proc.returncode == 0:
            rep.add(OK, "平台完整跑通了一轮(冒烟)")
        else:
            rep.add(FAIL, f"平台跑不通(退出码 {proc.returncode})",
                    (proc.stderr or proc.stdout).strip()[-500:])


def main() -> int:
    ap = argparse.ArgumentParser(description="校验一个方法是否合规")
    ap.add_argument("method", help="方法目录,例如 methods/llm_improver")
    ap.add_argument("--harness", default="base_harness/loop",
                    help="用哪个 harness 校验(默认最简的 loop)")
    ap.add_argument("--smoke", action="store_true",
                    help="让平台真的跑一轮(会调用模型,较慢)")
    ap.add_argument("--no-call", action="store_true",
                    help="只查结构,不调用方法")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    method = (ROOT / args.method) if not Path(args.method).is_absolute() else Path(args.method)
    harness = (ROOT / args.harness) if not Path(args.harness).is_absolute() else Path(args.harness)

    print(f"\n\033[1m校验方法 {method.name}\033[0m  (harness: {harness.name})")
    rep = Report()

    if not check_structure(method, rep):
        rep.print()
        return 1
    if not args.no_call:
        check_invocation(method, harness, rep, args.timeout)
    if args.smoke:
        check_smoke(method, harness, rep, args.timeout * 4)

    print()
    rep.print()

    failed = rep.failed
    warned = any(l == WARN for l, _, _ in rep.rows)
    print()
    if failed:
        print("\033[31m不合规。\033[0m修掉上面的 ✗ 之后再跑真实实验 —— "
              "这些问题在付费实验里会以\"分数没动\"的样子出现。")
        return 1
    if warned:
        print("\033[33m可用,但有值得看一眼的地方(上面的 !)。\033[0m")
        return 0
    print("\033[32m合规。\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
