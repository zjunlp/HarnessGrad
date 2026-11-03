"""把 `data/authored/<slug>/bundle.json` 变成一个平台认的数据集。

场景工坊产出的是**声明**（goal + 材料 + 检查器模板 + 参考解），平台这边需要的是
`TASKS` / `SETUP` / `VERIFY` 三个字典。这个模块只做这一层翻译，不执行任何东西——
执行发生在 run 里（`eval/run/runner.py` 物化 `SETUP.files`、跑 `VERIFY`）。

三条刻意的限制，都是为了"可判定"不变成"看起来可判定"：

1. **材料只收文本文件。** 二进制（图片、sqlite）今天会被拒绝并点名，而不是悄悄变成一个
   空文件 —— 一个被吃掉的初始状态会让 oracle 也失败，读的人只会以为题出错了。
2. **检查器永远是模板生成的，不是作者写的。** 作者能提供的只有"材料里本来就有的脚本"
   （`command_exit0` 指向它）或模板参数（`file_equals` 等）。模板脚本由这里生成，随
   `VERIFY.inputs` 在 agent 结束后重新物化，所以 agent 改不动它。
3. **不支持的模板直接报错。** `tests_pass_ratio` 还没有实现：宁可拒绝发布，也不要产出一个
   永远 0 分的检查器。
"""
from __future__ import annotations

import json
import pathlib

__all__ = ["load", "BundleError"]

#: 检查器脚本：由模板生成，进 `VERIFY.inputs`，agent 结束后重新物化。
#: 判决写到这个文件，而不是用退出码当分数。数据集契约里 `command` 检查器的判据是
#: `reward_file`（INTERFACE.md「exit code is not the score」）：退出码还带着"脚本自己崩了"
#: 这类信息，混进分数就分不清"题没做对"和"检查器没跑起来"。
REWARD_REL = "verifier/reward.txt"

#: 检查器脚本：由模板生成，进 `VERIFY.inputs`，agent 结束后重新物化。
_CHECKER = '''\
import json, pathlib, sys

def report(score):
    out = pathlib.Path("verifier")
    out.mkdir(parents=True, exist_ok=True)
    (out / "reward.txt").write_text(f"{score:g}\\n", encoding="utf-8")

spec = json.loads(pathlib.Path(__file__).with_name("_hg_check.json").read_text(encoding="utf-8"))
target = pathlib.Path(spec["path"])
if not target.is_file():
    print("missing:", spec["path"]); report(0.0); sys.exit(0)
text = target.read_text(encoding="utf-8", errors="replace")
mode = spec["mode"]
if mode == "equals":
    ok = text.strip() == spec["value"].strip()
elif mode == "contains":
    ok = spec["value"] in text
elif mode == "number_close":
    try:
        ok = abs(float(text.strip()) - float(spec["value"])) <= float(spec["tolerance"])
    except ValueError:
        ok = False
else:
    ok = False
print("ok" if ok else f"{mode} failed for {spec['path']}")
report(1.0 if ok else 0.0)
'''

#: `command_exit0` 的包装：跑作者给的那条命令，把它自己的退出码折成 1/0 写进 reward。
#: 命令仍然是作者写的、仍然只用材料里的脚本，但分数走契约里的文件通道。
_EXIT0 = '''\
import json, pathlib, subprocess, sys

def report(score):
    out = pathlib.Path("verifier")
    out.mkdir(parents=True, exist_ok=True)
    (out / "reward.txt").write_text(f"{score:g}\\n", encoding="utf-8")

spec = json.loads(pathlib.Path(__file__).with_name("_hg_exit0.json").read_text(encoding="utf-8"))
try:
    proc = subprocess.run(spec["argv"], capture_output=True, text=True, errors="replace",
                          timeout=spec.get("timeout_s", 900))
except (OSError, subprocess.TimeoutExpired) as exc:
    print(f"check could not run: {exc}"); report(0.0); sys.exit(0)
sys.stdout.write(proc.stdout or "")
sys.stderr.write(proc.stderr or "")
report(1.0 if proc.returncode == 0 else 0.0)
sys.exit(0)
'''

TEXT_SUFFIXES = {".py", ".sh", ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml",
                 ".ini", ".cfg", ".c", ".h", ".js", ".ts", ".html", ".sql", ".ipynb"}


class BundleError(ValueError):
    pass


def _read_tree(root: pathlib.Path) -> list[dict]:
    """Every regular file under `root`, as `{"path", "content"}`, with binaries refused."""
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink():
            raise BundleError(f"材料里不允许符号链接：{path.relative_to(root)}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            raise BundleError(
                f"材料 {path.relative_to(root)} 不是文本文件（后缀 {path.suffix or '无'}）；"
                "场景工坊今天只收文本材料")
        files.append({"path": str(path.relative_to(root)), "content": path.read_text(encoding="utf-8")})
    return files


def load(slug: str, authored_root: pathlib.Path | None = None) -> tuple[list[dict], dict, dict]:
    """`(TASKS, SETUP, VERIFY)` for one authored bundle."""
    root = pathlib.Path(authored_root or pathlib.Path(__file__).resolve().parents[2]
                        / "data" / "authored") / slug
    bundle_path = root / "bundle.json"
    if not bundle_path.is_file():
        raise BundleError(f"{bundle_path} 不存在")
    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    kind = (data.get("env") or {}).get("kind")
    if kind != "files":
        raise BundleError(f"{slug}: 只有 env.kind=files 的 bundle 能被编译（现在是 {kind!r}）")
    tasks, setup, verify = [], {}, {}
    for task in data.get("tasks") or []:
        task_id = str(task["task_id"])
        materials = root / str(task["materials"])
        files = _read_tree(materials)
        tasks.append({"task_id": task_id, "goal": str(task["goal"])})
        setup[task_id] = {"files": files}
        spec = task["verifier"]
        template = spec["template"]
        params = spec.get("params") or {}
        if template == "answer_equals":
            verify[task_id] = {"kind": "answer", "expected": str(params["expected"])}
            continue
        if template in ("file_equals", "file_contains", "answer_number_close"):
            mode = {"file_equals": "equals", "file_contains": "contains",
                    "answer_number_close": "number_close"}[template]
            target = "answer.txt" if template == "answer_number_close" else str(params["path"])
            spec_json = {"mode": mode, "path": target}
            if template == "file_contains":
                spec_json["value"] = str(params["text"])
            else:
                spec_json["value"] = str(params["expected"])
            if template == "answer_number_close":
                spec_json["tolerance"] = str(params["tolerance"])
            verify[task_id] = {"kind": "command", "argv": ["python3", "_hg_check.py"],
                               "reward_file": "/" + REWARD_REL,
                               "inputs": [{"path": "_hg_check.py", "content": _CHECKER},
                                          {"path": "_hg_check.json",
                                           "content": json.dumps(spec_json, ensure_ascii=False)}]}
            continue
        if template == "command_exit0":
            cmd = str(params["cmd"]).split()
            # 检查器用到的文件跟着 VERIFY 走：agent 之后重新物化，改材料里的测试文件没用。
            referenced = [word for word in cmd if word.endswith((".py", ".sh"))]
            inputs = [{"path": name, "content": (materials / name).read_text(encoding="utf-8")}
                      for name in referenced if (materials / name).is_file()]
            spec_exit0 = {"argv": cmd, "timeout_s": int(params.get("timeout_s") or 900)}
            verify[task_id] = {"kind": "command", "argv": ["python3", "_hg_exit0.py"],
                               "reward_file": "/" + REWARD_REL,
                               "inputs": [{"path": "_hg_exit0.py", "content": _EXIT0},
                                          {"path": "_hg_exit0.json",
                                           "content": json.dumps(spec_exit0, ensure_ascii=False)}]
                                         + inputs}
            continue
        raise BundleError(f"{slug}/{task_id}: 检查器模板 {template!r} 还不能编译"
                          "（tests_pass_ratio 未实现；宁可拒绝，也不要发布一个永远 0 分的检查器）")
    return tasks, setup, verify


def module_source(slug: str) -> str:
    """The tiny adapter file `--publish` writes. It holds no data, only the reference."""
    return "".join([
        '"""由 `tools/scenario_builder.py --publish` 生成；改内容请改 '
        f'`data/authored/{slug}/bundle.json`，不要改这里。"""\n',
        "from data.scaffolds.authored import load as _load_authored\n\n",
        'KIND = "dataset"\n',
        'ORACLE_SOURCE = "authored"\n',
        'ORACLE_VALIDATED_BY = "tools/scenario_builder.py --gates (oracle / empty / deterministic)"\n',
        'ORACLE_COVERAGE = "all tasks"\n',
        'OFFICIAL_EVAL = None\n',
        'SPLIT_SOURCE = "authored bundle: every task in the bundle is scored"\n',
        f'_BUNDLE_NAME = "{slug}"\n',
        f'TASKS, SETUP, VERIFY = _load_authored("{slug}")\n',
        'def load():\n',
        '    """The registry protocol: (tasks, scorable). Baked-in answers would be a\n',
        '    second source of truth; an authored bundle grades by its verifier.\n',
        '    """\n',
        '    return TASKS, {}\n',
        'def downloads():\n',
        '    """一个 authored bundle 的数据就是它自己（`data/authored/<slug>`，随 git 走）。\n',
        '\n',
        '    不写 `downloads()` 是沉默，不是"没有数据"：这里声明来源、内容摘要与字节数，\n',
        '    于是 run 的记录能说清它量的是哪一份 bundle。\n',
        '    """\n',
        '    import hashlib\n',
        '    import pathlib\n',
        f'    root = pathlib.Path(__file__).resolve().parents[1] / "authored" / "{slug}"\n',
        '    digest = hashlib.sha256((root / "bundle.json").read_bytes()).hexdigest()\n',
        '    size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())\n',
        '    return [{"kind": "authored", "name": _BUNDLE_NAME, "version": digest[:16],\n',
        '             "size": f"{size} bytes",\n',
        '             "note": "authored scenario bundle: bundle.json + materials + oracle, "\n',
        '                     "versioned with this repository",\n',
        f'             "path": "data/authored/{slug}", "sha256": digest}}]\n',
        'DEFAULT_ENV = {"kind": "files"}\n',
        f'DEFAULT_SPLIT = {{"by": "sha256(task_id)", "eval_fraction": 0.2}}\n',
    ])
