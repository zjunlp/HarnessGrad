#!/usr/bin/env python3
"""场景工作区：把一段自然语言的任务说明，起草成一个可验证的 bundle。

设计见 `docs/scenario-workspace.md`。这里只做三件确定的事：

1. `--init`      写出一个空 bundle 的目录骨架（老师照着填，或直接把材料拖进去）；
2. `--validate`  严格校验：缺什么、错在哪一行，一次说全；
3. `--draft`     读一段自然语言的说明（+ 材料目录），让模型起草 `bundle.json`，
                 再把**起草结果交给 validate 自己审**；校验不过就把原因回传给模型重来。

一条硬约束：**模型不产出可执行检查器。** 它只能从模板菜单里挑一个并填参数
（`VERIFIER_TEMPLATES`），因为模板能逐条翻译成人话给老师看，也能被穷举测试；模型写
出来的检查器两样都做不到。

用法：

    python3 tools/scenario_builder.py --init fix-bug-py --out /tmp/authored/fix-bug-py
    python3 tools/scenario_builder.py --validate data/authored/example-fix-bug
    python3 tools/scenario_builder.py --draft /tmp/note.md --materials /tmp/mat \
        --out /tmp/authored/fix-bug-py

`--draft` 读的环境变量（只读，不写日志、不进 bundle）：

    HG_AUTHOR_BASE_URL   OpenAI 兼容端点，例如 http://127.0.0.1:8001/v1
    HG_AUTHOR_MODEL      served model name
    HG_AUTHOR_API_KEY    任何非空值（本地端点不校验）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import tempfile

SCHEMA_VERSION = 1

#: 封闭的检查器菜单。每个模板 = (必需参数, 中文渲染)，`{path}` 之类由参数填。
#: 新增模板要同时改 `docs/scenario-workspace.md` 的表，否则老师那一屏说不清。
VERIFIER_TEMPLATES: dict[str, tuple[tuple[str, ...], str]] = {
    "answer_equals": (("expected",), "答案必须正好等于 {expected}"),
    "answer_number_close": (("expected", "tolerance"),
                            "答案的数值与 {expected} 相差不超过 {tolerance}"),
    "file_equals": (("path", "expected"), "`{path}` 的内容必须正好是 {expected}"),
    "file_contains": (("path", "text"), "`{path}` 里必须出现 `{text}`"),
    "command_exit0": (("cmd",), "在任务目录里运行 `{cmd}` 必须退出 0"),
    "tests_pass_ratio": (("cmd", "reward_file"),
                         "运行 `{cmd}`，按写到 `{reward_file}` 的通过比例给分"),
}

#: `cmd` 只允许这些程序，且必须指向材料里本来就有的文件。模型不能凭空造逻辑。
CMD_PROGRAMS = ("python3", "python", "bash", "sh", "pytest", "pytest-3")

ENV_KINDS = ("files", "exec")
SOURCE_KINDS = ("task-text", "task-text+materials", "materials")
REQUIRED_TOP = ("schema_version", "slug", "title", "source", "env", "tasks")


class BundleInvalid(ValueError):
    """校验失败：带上每一个问题，而不是第一个。"""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _rel_path(value: object) -> bool:
    """A bundle-relative path: no absolute, no `..`, no backslash."""
    if not isinstance(value, str) or not value.strip():
        return False
    if "\\" in value or value.startswith("/") or ":" in value:
        return False
    return ".." not in pathlib.PurePosixPath(value).parts


def describe(task: dict) -> str:
    """The one sentence a teacher reads instead of the checker. Never raises."""
    spec = task.get("verifier") or {}
    template = spec.get("template")
    params = spec.get("params") or {}
    entry = VERIFIER_TEMPLATES.get(str(template))
    if not entry:
        return f"（未知检查器 {template!r}）"
    _required, template_text = entry
    try:
        return template_text.format(**params)
    except (KeyError, IndexError):
        return f"（`{template}` 缺少参数）"


def check_bundle(root: pathlib.Path) -> list[str]:
    """Every problem with `root/bundle.json`, as a list of human-readable strings."""
    problems: list[str] = []
    bundle_path = root / "bundle.json"
    if not bundle_path.is_file():
        return [f"{bundle_path} 不存在"]
    try:
        data = json.loads(bundle_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"bundle.json 不是合法 JSON：{exc}"]
    if not isinstance(data, dict):
        return ["bundle.json 必须是一个对象"]

    for key in REQUIRED_TOP:
        if key not in data:
            problems.append(f"缺少顶层字段 `{key}`")
    if problems:
        return problems
    if data["schema_version"] != SCHEMA_VERSION:
        problems.append(f"schema_version 必须是 {SCHEMA_VERSION}，现在是 {data['schema_version']!r}")
    if data["slug"] != root.name:
        problems.append(f"slug ({data['slug']!r}) 必须等于目录名 ({root.name!r})，"
                        "否则发布时同名 bundle 会互相覆盖")
    if not isinstance(data["title"], str) or not data["title"].strip():
        problems.append("title 必须是非空字符串")

    source = data["source"]
    if not isinstance(source, dict):
        problems.append("source 必须是对象 {kind, from, license}")
    else:
        if source.get("kind") not in SOURCE_KINDS:
            problems.append(f"source.kind 必须是 {list(SOURCE_KINDS)} 之一")
        for key in ("from", "license"):
            if not isinstance(source.get(key), str) or not str(source[key]).strip():
                problems.append(f"source.{key} 必须是非空字符串（来源与许可是发布的必要条件）")

    env = data["env"]
    if not isinstance(env, dict) or env.get("kind") not in ENV_KINDS:
        problems.append(f"env.kind 必须是 {list(ENV_KINDS)} 之一")
    elif env["kind"] == "exec" and not env.get("image"):
        problems.append("env.kind=exec 时必须给 env.image（固定镜像引用，否则两次测量不可比）")

    split = data.get("split")
    if split is not None:
        if not isinstance(split, dict) or split.get("by") not in ("sha256(task_id)", "declared"):
            problems.append("split.by 必须是 'sha256(task_id)' 或 'declared'")
        if split.get("by") == "sha256(task_id)" and not isinstance(split.get("eval_fraction"), (int, float)):
            problems.append("split.by=sha256(task_id) 时需要 eval_fraction（0-1）")

    if data.get("eval_visible_to_authors") not in (True, False):
        problems.append("eval_visible_to_authors 必须显式写 true 或 false（这是可比性声明，不能默认）")

    tasks = data["tasks"]
    if not isinstance(tasks, list) or not tasks:
        problems.append("tasks 必须是非空数组")
        return problems
    seen: set[str] = set()
    for i, task in enumerate(tasks):
        where = f"tasks[{i}]"
        if not isinstance(task, dict):
            problems.append(f"{where} 必须是对象")
            continue
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", str(task_id)):
            problems.append(f"{where}.task_id 必须是 1-64 位字母数字（.-_ 允许）")
            task_id = None
        elif task_id in seen:
            problems.append(f"{where}.task_id 重复：{task_id}")
        else:
            seen.add(str(task_id))
        if not isinstance(task.get("goal"), str) or not str(task["goal"]).strip():
            problems.append(f"{where}.goal 必须是非空字符串（模型只看这一段）")

        materials = task.get("materials")
        if materials is None:
            problems.append(f"{where}.materials 缺失：files 环境下每道题都必须有初始状态")
        elif not _rel_path(materials):
            problems.append(f"{where}.materials 必须是 bundle 内的相对路径")
        elif not (root / str(materials)).is_dir():
            problems.append(f"{where}.materials 指向的目录不存在：{materials}")

        oracle = task.get("oracle")
        if oracle is None:
            problems.append(f"{where}.oracle 缺失：没有参考解就跑不了第一道门（oracle 必须拿 1.0）")
        elif not _rel_path(oracle):
            problems.append(f"{where}.oracle 必须是 bundle 内的相对路径")
        elif not (root / str(oracle)).exists():
            problems.append(f"{where}.oracle 指向的路径不存在：{oracle}")

        spec = task.get("verifier")
        if not isinstance(spec, dict):
            problems.append(f"{where}.verifier 必须是对象")
            continue
        template = spec.get("template")
        if template not in VERIFIER_TEMPLATES:
            problems.append(f"{where}.verifier.template 必须是 {sorted(VERIFIER_TEMPLATES)} 之一，"
                            f"现在是 {template!r}")
            continue
        required, _text = VERIFIER_TEMPLATES[str(template)]
        params = spec.get("params")
        if not isinstance(params, dict):
            problems.append(f"{where}.verifier.params 必须是对象")
            continue
        missing = [name for name in required if name not in params]
        if missing:
            problems.append(f"{where}.verifier.params 缺少 {missing}")
        extra = [name for name in params if name not in required]
        if extra:
            problems.append(f"{where}.verifier.params 有未知参数 {extra}")
        if template in ("command_exit0", "tests_pass_ratio"):
            cmd = params.get("cmd")
            if not isinstance(cmd, str) or not cmd.strip():
                problems.append(f"{where}.verifier.params.cmd 必须是非空字符串")
            else:
                program = cmd.strip().split()[0]
                if program not in CMD_PROGRAMS:
                    problems.append(f"{where}.verifier.params.cmd 只允许 {list(CMD_PROGRAMS)}，"
                                    f"现在是 {program!r}")
                scripts = [word for word in cmd.split() if word.endswith((".py", ".sh"))]
                for script in scripts:
                    if not (root / str(task.get("materials") or "") / script).is_file():
                        problems.append(f"{where}: 检查器引用的 {script} 不在材料目录里"
                                        "（模型不能新写检查逻辑，只能用老师带来的文件）")
        if template in ("file_equals", "file_contains"):
            path = params.get("path")
            if not _rel_path(path):
                problems.append(f"{where}.verifier.params.path 必须是任务目录内的相对路径")
    return problems


def validate(root: pathlib.Path) -> dict:
    """`{"ok": bool, "problems": [...]}`; also returns the teacher-facing screen."""
    root = pathlib.Path(root)
    problems = check_bundle(root)
    result = {"ok": not problems, "problems": problems, "root": str(root)}
    if result["ok"]:
        data = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
        result["tasks"] = [{"task_id": task["task_id"],
                            "goal": task["goal"],
                            "checker": describe(task)} for task in data["tasks"]]
        result["eval_visible_to_authors"] = data["eval_visible_to_authors"]
    return result


SKELETON = {
    "schema_version": SCHEMA_VERSION,
    "slug": "{slug}",
    "title": "（一句话说明这道题考什么）",
    "source": {"kind": "task-text", "from": "（谁发布的）", "license": "（许可，例如 CC-BY-4.0）"},
    "env": {"kind": "files"},
    "split": {"by": "sha256(task_id)", "eval_fraction": 0.2},
    "eval_visible_to_authors": True,
    "tasks": [{
        "task_id": "t01",
        "goal": "（模型看到的任务说明；它不会看到 oracle，也不会看到 materials 以外的任何东西）",
        "materials": "materials/t01",
        "oracle": "oracle/t01.sh",
        "verifier": {"template": "answer_equals", "params": {"expected": "TODO"}},
    }],
}


def init(slug: str, out: pathlib.Path) -> pathlib.Path:
    root = pathlib.Path(out)
    if root.exists() and any(root.iterdir()):
        raise BundleInvalid([f"{root} 已存在且非空；换一个 --out，或先自己清空"])
    root.mkdir(parents=True, exist_ok=True)
    data = json.loads(json.dumps(SKELETON).replace("{slug}", slug))
    (root / "bundle.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                                      encoding="utf-8")
    (root / "materials" / "t01").mkdir(parents=True, exist_ok=True)
    (root / "oracle").mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        f"# {slug}\n\n由 `tools/scenario_builder.py --init` 生成。\n\n"
        "1. 把初始文件放进 `materials/<task_id>/`；\n"
        "2. 写一份参考解放进 `oracle/`（它必须让检查器判 1.0）；\n"
        "3. 改 `bundle.json` 里的 goal 与 verifier；\n"
        "4. `python3 tools/scenario_builder.py --validate .`。\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------- drafting ---

DRAFT_SYSTEM = """你在为一个 harness 评测平台起草一道可验证的编程/操作任务。

规则（违反任何一条，这次起草就会被校验拒绝）：
1. 只输出一个 JSON 对象，字段与给定骨架完全一致，不要解释、不要 markdown 围栏。
2. 每道题必须有 `goal`（模型唯一能看到的话，必须自足：说明在哪个目录、要做出什么、怎么算完成），
   `materials`（相对路径，初始文件所在目录），`oracle`（相对路径，参考解脚本或产物），
   `verifier`。
3. `verifier.template` 只能取给定菜单里的名字，`params` 只能有该模板要求的键。
4. `command_exit0` / `tests_pass_ratio` 的 `cmd` 只能调用 python3/bash/pytest，且必须指向
   材料目录里本来就有的文件（`check.py`、`test_*.py`）。不允许你自己写检查逻辑。
5. `goal` 里不能出现答案，也不能提及 oracle。
6. 每道题还要给 `oracle_content`：一段 POSIX shell 脚本，在任务目录里执行后能让检查器通过
   （解题过程，不是检查逻辑）。它会作为参考解跑第一道门；跑不过就会被退回重写。
"""


#: What the model sees when drafting: the same shape as a bundle, except the reference
#: solution is *content* rather than a path -- a model cannot create a file, and a skeleton
#: that shows `"oracle": "oracle/t01.sh"` invites it to copy that string back verbatim.
DRAFT_SKELETON = json.loads(json.dumps(SKELETON))
DRAFT_SKELETON["tasks"][0].pop("oracle", None)
DRAFT_SKELETON["tasks"][0]["oracle_content"] = "#!/usr/bin/env bash\n# 解题脚本：在任务目录里执行后必须让检查器通过\n"


def draft_prompt(note: str, materials: list[str]) -> tuple[str, str]:
    bones = json.dumps(DRAFT_SKELETON, ensure_ascii=False, indent=1)
    menu = "\n".join(f"- {name}: 参数 {list(req)} —— {text}"
                     for name, (req, text) in VERIFIER_TEMPLATES.items())
    user = (f"## 老师给的说明\n{note.strip()}\n\n"
            f"## 材料清单（相对 materials/ 的文件名）\n"
            + ("\n".join(f"- {name}" for name in materials) if materials else "（没有材料，需要你生成初始文件）")
            + f"\n\n## 检查器菜单\n{menu}\n\n"
            f"## 输出骨架（字段与层级必须一致）\n{bones}")
    return DRAFT_SYSTEM, user


def _chat(base_url: str, model: str, api_key: str, system: str, user: str) -> str:
    body = json.dumps({"model": model, "temperature": 0, "stream": False,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}],
                       "response_format": {"type": "json_object"}},
                      ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    return str(payload["choices"][0]["message"]["content"])


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    return text.strip()


def draft(note: str, materials_dir: pathlib.Path | None, out: pathlib.Path,
          attempts: int = 3) -> dict:
    """Ask the model for a bundle, validate it, and retry with the reason it was refused."""
    base_url = os.environ.get("HG_AUTHOR_BASE_URL", "").strip()
    model = os.environ.get("HG_AUTHOR_MODEL", "").strip()
    api_key = os.environ.get("HG_AUTHOR_API_KEY", "").strip()
    missing = [name for name, value in (("HG_AUTHOR_BASE_URL", base_url),
                                        ("HG_AUTHOR_MODEL", model),
                                        ("HG_AUTHOR_API_KEY", api_key)) if not value]
    if missing:
        raise BundleInvalid([f"起草需要设置 {missing}（配置自己的 OpenAI 兼容 endpoint）"])
    names = sorted(p.name for p in materials_dir.iterdir()) if materials_dir else []
    system, user = draft_prompt(note, names)
    reason = ""
    for attempt in range(attempts):
        ask = system if not reason else f"{system}\n\n上一次的起草被校验拒绝了：{reason}\n请修正。"
        raw = _chat(base_url, model, api_key, ask, user)
        try:
            data = json.loads(_strip_fence(raw))
        except ValueError as exc:
            reason = f"不是合法 JSON（{exc}）"
            continue
        if not isinstance(data, dict):
            reason = "顶层必须是对象"
            continue
        data["schema_version"] = SCHEMA_VERSION
        # The directory is the identity: asking the model for a slug and then trusting it is
        # how two bundles end up claiming the same name.
        data["slug"] = pathlib.Path(out).name
        pathlib.Path(out).mkdir(parents=True, exist_ok=True)
        if materials_dir and materials_dir.is_dir():
            target = pathlib.Path(out) / "materials" / materials_dir.name
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(materials_dir, target)
        # A reference solution is code, but it is *the solution*, not the checker: it runs in
        # the sandbox during gate 1, and a wrong one is caught there rather than shipped.
        (pathlib.Path(out) / "oracle").mkdir(parents=True, exist_ok=True)
        for task in data.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            body = task.pop("oracle_content", None)
            if isinstance(body, str) and body.strip():
                name = re.sub(r"[^A-Za-z0-9._-]", "_", str(task.get("task_id") or "task"))
                script = pathlib.Path(out) / "oracle" / f"{name}.sh"
                script.write_text(body if body.startswith("#!") else "#!/usr/bin/env bash\n" + body,
                                  encoding="utf-8")
                script.chmod(0o755)
                # Override, not setdefault: the model often fills `oracle` with a path it
                # invented, and a path that does not exist is worse than no answer.
                task["oracle"] = f"oracle/{name}.sh"
            elif not (pathlib.Path(out) / str(task.get("oracle") or "")).exists():
                task.pop("oracle", None)
        (pathlib.Path(out) / "bundle.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        # 校验只证明"形状对"。它证明不了"这道题可判定" —— 实测(2026-11-02):模型起草的
        # 参考解把 bug 猜成 `return a + 1`(实际是 `return a - b`),sed 什么都没改就退出 0,
        # 校验全绿而 oracle 门是红的。所以门必须在这里跑,红的原因就是下一次起草的输入。
        result = validate(out)
        if result["ok"]:
            gate = gates(out)
            if gate["ok"]:
                result["attempts"] = attempt + 1
                result["gates"] = gate
                return result
            reason = "三道门未过：" + "；".join(
                f"{row['task_id']} oracle={'通过' if row['oracle_passes'] else '未通过'}"
                f"（{row['detail']['oracle'].strip()[:120]}）"
                f"，空手={'被挡住' if row['empty_fails'] else '也通过了'}"
                for row in gate.get("tasks") or [])
        else:
            reason = "；".join(result["problems"])
    raise BundleInvalid([f"起草 {attempts} 次仍未通过校验，最后一次：{reason}"])


def _run_verifier(task: dict, workdir: pathlib.Path) -> tuple[bool, str]:
    """`(passed, detail)` for one task's verifier, run the way the platform would."""
    spec = task["verifier"]
    template = spec["template"]
    params = spec.get("params") or {}
    if template == "answer_equals":
        answer = workdir / "answer.txt"
        got = answer.read_text(encoding="utf-8", errors="replace").strip() if answer.is_file() else ""
        ok = got == str(params["expected"]).strip()
        return ok, f"answer.txt={got!r} expected={params['expected']!r}"
    if template == "answer_number_close":
        answer = workdir / "answer.txt"
        got = answer.read_text(encoding="utf-8", errors="replace").strip() if answer.is_file() else ""
        try:
            ok = abs(float(got) - float(params["expected"])) <= float(params["tolerance"])
        except ValueError:
            return False, f"answer.txt={got!r} 不是数字"
        return ok, f"answer.txt={got!r} expected={params['expected']}±{params['tolerance']}"
    if template in ("file_equals", "file_contains"):
        path = workdir / str(params["path"])
        if not path.is_file():
            return False, f"{params['path']} 不存在"
        text = path.read_text(encoding="utf-8", errors="replace")
        if template == "file_equals":
            ok = text.strip() == str(params["expected"]).strip()
            return ok, f"{params['path']} 内容不匹配" if not ok else "匹配"
        ok = str(params["text"]) in text
        return ok, f"{params['path']} 里{'有' if ok else '没有'} {params['text']!r}"
    completed = subprocess.run(params["cmd"], shell=True, cwd=workdir, capture_output=True,
                               text=True, errors="replace", timeout=600)
    tail = (completed.stdout + completed.stderr)[-400:]
    if template == "command_exit0":
        return completed.returncode == 0, f"exit={completed.returncode} {tail}"
    reward = workdir / str(params["reward_file"])
    try:
        value = float(json.loads(reward.read_text(encoding="utf-8")).get("score")
                      if reward.suffix == ".json" else reward.read_text().strip())
    except (OSError, ValueError, AttributeError):
        return False, f"{params['reward_file']} 读不出分数；exit={completed.returncode} {tail}"
    return value >= 1.0, f"reward={value}"


def gates(root: pathlib.Path) -> dict:
    """The three gates, run locally on a throwaway copy of each task's materials.

    Gate 1 (oracle) and gate 2 (empty hand) are the two that make "verifiable" mean
    something; gate 3 runs the oracle twice and compares the verdict. Publishing still
    re-runs them in the platform's verifier sandbox -- this is the author's own check.
    """
    root = pathlib.Path(root)
    result = validate(root)
    if not result["ok"]:
        return {"ok": False, "problems": result["problems"]}
    data = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    rows = []
    for task in data["tasks"]:
        materials = root / str(task["materials"])
        oracle = root / str(task["oracle"])
        verdicts = {}
        with tempfile.TemporaryDirectory(prefix="hg-gate-") as tmp:
            tmp = pathlib.Path(tmp)
            for label in ("empty", "oracle", "oracle-again"):
                work = tmp / label
                shutil.copytree(materials, work)
                if label != "empty":
                    ran = subprocess.run(["bash", str(oracle.resolve())], cwd=work,
                                         capture_output=True, text=True, errors="replace",
                                         timeout=900)
                    if ran.returncode != 0:
                        verdicts[label] = (False, f"参考解自己退出了 {ran.returncode}: "
                                                  f"{(ran.stdout + ran.stderr)[-300:]}")
                        continue
                verdicts[label] = _run_verifier(task, work)
        empty_ok, empty_why = verdicts.get("empty", (False, "没跑"))
        oracle_ok, oracle_why = verdicts.get("oracle", (False, "没跑"))
        again_ok = verdicts.get("oracle-again", (False, "没跑"))[0]
        row = {"task_id": task["task_id"],
               "oracle_passes": oracle_ok, "empty_fails": not empty_ok,
               "deterministic": oracle_ok == again_ok,
               "detail": {"oracle": oracle_why, "empty": empty_why}}
        row["ok"] = row["oracle_passes"] and row["empty_fails"] and row["deterministic"]
        rows.append(row)
    return {"ok": all(row["ok"] for row in rows), "tasks": rows}


def control(root: pathlib.Path, arm: str) -> int:
    """数据集控制的两臂：`--oracle` 必须得 1，`--noop`（空手）必须得 0。

    为什么单独一个入口：平台对每个已发布数据集要求一对"参考解 1 / 什么都不做 0"的控制
    （`tests/quality/test_dataset_controls.py`），它们是"这个分数是可达的"唯一的凭据。
    authored bundle 的这两臂本来就在 `--gates` 里跑，但那条路打印的是一张表；这里把它
    折成控制协议要的那一个数字，读数的人不用自己解释表格。
    """
    root = pathlib.Path(root)
    gate = gates(root)
    rows = gate.get("tasks") or []
    if not rows:
        print("CONTROL UNMEASURED: 三道门没跑出任何题：" + "；".join(gate.get("problems") or []))
        return 2
    for row in rows:
        print(f"  · {row['task_id']} oracle={row['oracle_passes']} "
              f"empty_fails={row['empty_fails']} deterministic={row['deterministic']}")
    if arm == "oracle":
        ok = all(row["oracle_passes"] for row in rows)
    elif arm == "noop":
        # 第二臂**通过**意味着空手也得分了 —— 那是检查器坏了，不是题做出来了。
        ok = not all(row["empty_fails"] for row in rows)
    else:                                            # pragma: no cover - CLI 收口
        print(f"CONTROL UNMEASURED: 未知的一臂 {arm!r}")
        return 2
    print(f"reward {1 if ok else 0}")
    return 0


def publish(root: pathlib.Path) -> dict:
    """把一个通过三道门的 bundle 登记成一个数据集适配器。

    写出的 `data/datasets/authored_<slug>.py` 不含数据，只有一句 `load(slug)`：内容的
    唯一来源永远是 `data/authored/<slug>/bundle.json`，所以改题不会出现两份真相。
    """
    root = pathlib.Path(root)
    gate = gates(root)
    if not gate.get("ok"):
        raise BundleInvalid(["三道门未通过，不能发布："
                             + "；".join(f"{r['task_id']} oracle={r['oracle_passes']} "
                                        f"empty_fails={r['empty_fails']}" for r in gate.get("tasks") or [])])
    slug = root.name
    module = f"authored_{re.sub(r'[^A-Za-z0-9_]', '_', slug)}"
    target = pathlib.Path(__file__).resolve().parents[1] / "data" / "datasets" / f"{module}.py"
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from data.scaffolds.authored import load, module_source          # noqa: PLC0415
    tasks, setup, verify = load(slug)                                 # 先证明它编译得动
    target.write_text(module_source(slug), encoding="utf-8")
    return {"module": target.stem, "dataset": module, "path": str(target),
            "tasks": len(tasks), "verifiers": sorted({v["kind"] for v in verify.values()})}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", metavar="SLUG")
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--validate", type=pathlib.Path)
    ap.add_argument("--draft", type=pathlib.Path, help="自然语言说明文件")
    ap.add_argument("--materials", type=pathlib.Path)
    ap.add_argument("--gates", type=pathlib.Path, help="跑三道门：oracle 必须 1.0、空手必须 0、两次一致")
    ap.add_argument("--publish", type=pathlib.Path,
                    help="登记成数据集：写 data/datasets/authored_<slug>.py")
    ap.add_argument("--control", type=pathlib.Path, metavar="BUNDLE",
                    help="数据集的 oracle/noop 两臂控制：配合 --oracle 或 --noop")
    arms = ap.add_mutually_exclusive_group()
    arms.add_argument("--oracle", action="store_true", help="参考解这一臂（必须得 1）")
    arms.add_argument("--noop", action="store_true", help="空手这一臂（必须得 0）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    try:
        if args.init:
            root = init(args.init, args.out or pathlib.Path("data/authored") / args.init)
            print(f"created {root}")
            return 0
        if args.control:
            arm = "oracle" if args.oracle else "noop" if args.noop else ""
            if not arm:
                print("CONTROL UNMEASURED: --control 需要一个 --oracle 或 --noop")
                return 2
            return control(args.control, arm)
        if args.validate:
            result = validate(args.validate)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=1))
            else:
                print(f"{result['root']}: {'OK' if result['ok'] else 'INVALID'}")
                for problem in result["problems"]:
                    print(f"  - {problem}")
                for task in result.get("tasks") or []:
                    print(f"  · {task['task_id']}: {task['checker']}")
                if result["ok"]:
                    print(f"  eval 对作者可见：{result['eval_visible_to_authors']}")
            return 0 if result["ok"] else 1
        if args.gates:
            result = gates(args.gates)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=1))
            else:
                print(f"{args.gates}: gates {'PASS' if result['ok'] else 'FAIL'}")
                for row in result.get("tasks") or []:
                    print(f"  · {row['task_id']}: "
                          f"oracle {'✓' if row['oracle_passes'] else '✗'}  "
                          f"空手 {'✓' if row['empty_fails'] else '✗'}  "
                          f"两次一致 {'✓' if row['deterministic'] else '✗'}")
                    for name, why in row["detail"].items():
                        print(f"      {name}: {why.strip()[:160]}")
                for problem in result.get("problems") or []:
                    print(f"  - {problem}")
            return 0 if result["ok"] else 1
        if args.publish:
            result = publish(args.publish)
            print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else
                  f"published {result['dataset']} -> {result['path']} "
                  f"({result['tasks']} tasks, {result['verifiers']})")
            return 0
        if args.draft:
            result = draft(args.draft.read_text(encoding="utf-8"), args.materials, args.out)
            print(json.dumps(result, ensure_ascii=False, indent=1) if args.json
                  else f"drafted {result['root']} in {result['attempts']} attempt(s)")
            return 0
    except BundleInvalid as exc:
        for problem in exc.problems:
            print(f"refused: {problem}", file=sys.stderr)
        return 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
