"""Launch validation, invocation construction, preview and one-shot detached start.

The HTTP composition supplies settings/catalogue and invocation ports. This
module owns only files/handles it creates, never a global driver lifecycle. It
neither retries spawn nor cancels a detached process when a response is lost.
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse
if __package__:
    from .record_reader import read_json
    from .settings import display_base_url
else:
    from record_reader import read_json
    from settings import display_base_url


@dataclass(frozen=True)
class LaunchPaths:
    driver: pathlib.Path
    platform: pathlib.Path
    methods_root: pathlib.Path
    runs: pathlib.Path
    state_root: pathlib.Path
    launch_dir: pathlib.Path
    launch_log: pathlib.Path
    budget_dir: pathlib.Path | None
    external_state: bool
    console_parent: pathlib.Path


@dataclass(frozen=True)
class InvocationPort:
    validate: Callable
    argv: Callable
    environment: Callable

def parse_capacity(value) -> int:
    if not re.fullmatch(r'[1-9][0-9]{0,3}', str(value)):
        raise ValueError('Set HG_CONSOLE_BUDGET_CAPACITY to a positive integer (1..9999).')
    return int(value)


def python_supported() -> bool:
    return sys.version_info >= (3, 12)


def procfs_readable() -> bool:
    """能不能读 /proc —— 与"读到几个 driver"是两个问题。

    `live_drivers_by_run()` 在 /proc 不可读时返回空字典(读接口应该降级而不是 500),但**启动守卫
    不能把"看不见"当成"没有"**:那正好是这份守卫存在的理由。所以这里单独问一次,守卫拿它 fail
    closed。
    """
    try:
        next(iter(pathlib.Path("/proc").iterdir()))
    except (OSError, StopIteration):
        return False
    return True


def live_drivers_by_run() -> dict[str, dict]:
    """在跑的 driver：读 /proc，不 grep ps（`ps` 会截断 cmdline）。"""
    found: dict[str, dict] = {}
    try:
        entries = list(pathlib.Path("/proc").iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            argv = [a.decode("utf-8", "replace") for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        # **A driver, not a process that mentions one.** `bwrap` re-executes the driver's argv
        # inside the namespace with `/proc/self/fd/N` as its argv[0], and those children were
        # being reported as extra drivers -- measured: one 4-cell sweep produced 80 "live drivers"
        # and a preflight that read like a wall of run ids.
        if not any(a.endswith("driver.py") for a in argv):
            continue
        if not any(a in ("--dataset", "--run-id", "--from-run") for a in argv):
            continue
        run_id = ""
        if "--run-id" in argv:
            i = argv.index("--run-id")
            run_id = argv[i + 1] if i + 1 < len(argv) else ""
        found[run_id or f"pid-{entry.name}"] = {"pid": int(entry.name), "argv": argv[:6]}
    return found


def describe(catalogue, *, load_settings, pools) -> dict:
    """选择器的取值全部来自平台自己，不写死。"""
    names = catalogue.adapters()
    improver_rows = catalogue.improvers()
    settings = load_settings()
    task = settings["task"]
    meta = settings["meta"]
    try:
        from harnessgrad.config import current
        agent = current().agent
        task_model = task.get("model") or agent.model or ""
        task_url = task.get("base_url") or agent.base_url or ""
        # Only advertise keys launch_env can actually use; do not claim an
        # ambient driver key is saved while the launcher intentionally clears it.
        task_key_set = bool(task.get("api_key"))
        backend = agent.backend if agent.backend in ("mock", "openai") else "mock"
    except Exception:  # noqa: BLE001 - no configured platform model is not a default
        task_model, task_url, task_key_set, backend = task.get("model", ""), task.get("base_url", ""), bool(task.get("api_key")), "mock"
    if task.get("model") and task.get("model") != "mock":
        backend = "openai"
    return {"datasets": names, "harnesses": catalogue.base_harnesses(),
            "task_models": catalogue.task_models(),
            # Editor harness and model are separate choices in the driver invocation.
            "improvers": improver_rows,
            "meta_models": sorted({row["model"] for row in improver_rows if row["model"]}),
            # Improvement method and editor harness are separate catalogue choices.
            "methods": catalogue.method_catalogue(),
            "pools": list(pools),
            "defaults": {"dataset": "alfworld", "harness": "loop",
                         "task_backend": backend, "task_model": task_model if backend == "openai" else "mock",
                         "task_base_url": display_base_url(task_url), "task_base_url_set": bool(task_url),
                         "task_base_url_redacted": bool(task_url and not display_base_url(task_url)),
                         "task_base_url_reenter": bool(task_url and not display_base_url(task_url) and not task.get("base_url")),
                         "task_api_key_set": task_key_set,
                         # Legacy meta settings are retained on disk, not silently applied
                         # to a CLI improver that owns its own provider configuration.
                         "meta_base_url": "", "improver_model": "",
                         "meta_api_key_set": bool(meta.get("api_key")),
                         "rounds": 10, "jobs": 12, "trials": 1, "sampling": "all"}}


def _single_measurement(form: dict) -> bool:
    return str(form.get("rounds")) == "0" or str(form.get("method") or "").strip() == "none"


def build_argv(form: dict, run_id: str, *, paths, budget_capacity) -> list[str]:
    """预览与真正起跑**共用这一个函数** —— 否则"预览的命令"和"实际跑的命令"迟早分叉。"""
    jobs = int(form["jobs"])
    single = _single_measurement(form)
    improver = "none" if single else str(form["improver"])
    improver_model = str(form.get("improver_model") or "")
    return [sys.executable, str(paths.driver),
            "--harness", str(form["harness"]),
            "--dataset", str(form["dataset"]),
            "--split", "declared",
            "--side", "train",
            "--sampling", str(form.get("sampling") or "all"),
            "--rounds", "0" if single else str(int(form["rounds"])),
            "--trials", str(int(form["trials"])),
            "--jobs", str(jobs),
            *(["--method-entrypoint",
               str(paths.methods_root / str(form["method"]) / "run.py")]
              if str(form.get("method") or "").strip() not in ("", "none") else []),
            "--improver", improver,
            *(["--improver-model", improver_model] if improver_model and not single else []),
            "--run-id", run_id,
            "--runs-root", str(paths.runs),
            *(["--work-root", str(paths.state_root / "work")] if paths.external_state else []),
            "--gpu-budget", str(budget_capacity()),
            "--gpu-budget-dir", str(paths.budget_dir)]


def build_environment(form: dict, *, load_settings) -> dict:
    """Build explicit solver/method overrides for the child driver environment."""
    settings = load_settings()
    task, meta = settings["task"], settings["meta"]
    backend = str(form.get("task_backend") or ("mock" if form.get("task_model") == "mock" else "openai"))
    env = {"HG_AGENT_BACKEND": backend,
           "HG_AGENT_MODEL": str(form.get("task_model") or ""),
           "HG_AGENT_BASE_URL": "", "HG_AGENT_API_KEY": ""}
    if backend == "openai":
        env["HG_AGENT_BASE_URL"] = str(form.get("task_base_url") or task.get("base_url") or "")
        env["HG_AGENT_API_KEY"] = str(form.get("task_api_key") or
            (task.get("api_key") if env["HG_AGENT_BASE_URL"] == str(task.get("base_url") or "") else "") or "")
    # Method-owned editor clients consume HG_METHOD_*. Endpoint-backed improvers use
    # a per-launch registry override; CLI improvers retain their own transport.
    if form.get('meta_base_url'):
        env['HG_METHOD_BASE_URL'] = str(form['meta_base_url'])
        env['HG_METHOD_MODEL'] = str(form.get('improver_model') or '')
        env['HG_METHOD_API_KEY'] = str(form.get('meta_api_key') or
            (meta.get('api_key') if env['HG_METHOD_BASE_URL'] == str(meta.get('base_url') or '') else '') or '')
    return env


def new_run_id(form: dict) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = re.sub(r"[^A-Za-z0-9]+", "_", str(form["dataset"])) or "run"
    import uuid
    actor = "measurement" if _single_measurement(form) else str(form["improver"])
    return f"console-{stamp}-{tag}-{actor}-{uuid.uuid4().hex[:8]}"


def _field_types(form):
    for field in ('dataset', 'harness', 'sampling', 'improver', 'improver_model', 'method',
                  'task_backend', 'task_model', 'task_base_url', 'meta_base_url',
                  'task_api_key', 'meta_api_key'):
        if field in form and not isinstance(form[field], str):
            return 400, {"error": "bad-value", "detail": "Invalid launch field type"}
    return None


def _host_capabilities(form, paths, catalogue, python_supported, sandbox_supported):
    # Parallel drivers are coordinated through the shared lease; they are not
    # globally rejected merely because another deployment is running.
    if not python_supported():
        return 400, {"error": "unsupported-python",
                     "detail": (f"当前 Python 是 {sys.version_info.major}.{sys.version_info.minor}，"
                                "HarnessGrad 需要 Python 3.12+")}
    if not paths.driver.is_file():
        return 400, {"error": "no-driver", "detail": f"找不到 driver.py：{paths.driver}"}
    sandbox = sandbox_supported()
    if not sandbox.ok:
        return 400, {"error": "sandbox-unavailable", "detail": sandbox.detail}
    method = str(form.get("method") or "").strip()
    if method not in ("", "none"):
        entry = paths.methods_root / method / "run.py"
        if not entry.is_file():
            return 400, {"error": "unknown-method",
                         "detail": f"methods/{method}/run.py 不存在；可选："
                                   + ", ".join(row["name"] for row in catalogue.method_catalogue())}
    return None


def _storage_and_budget(paths, budget_capacity):
    if not paths.runs.is_dir() or not os.access(paths.runs, os.W_OK):
        return 400, {"error": "runs-root-not-writable",
                     "detail": f"runs 根目录不存在或不可写：{paths.runs}"}
    if paths.budget_dir is None:
        return 400, {"error": "no-budget-dir",
                     "detail": "Set HG_CONSOLE_BUDGET_DIR to a shared writable directory outside the platform before starting the console."}
    if not paths.budget_dir.is_absolute() or not paths.budget_dir.is_dir() or not os.access(paths.budget_dir, os.W_OK):
        return 400, {"error": "no-budget-dir",
                     "detail": "HG_CONSOLE_BUDGET_DIR must be an existing absolute writable directory."}
    if paths.budget_dir.resolve().is_relative_to(paths.platform.resolve()):
        return 400, {"error": "no-budget-dir", "detail": "HG_CONSOLE_BUDGET_DIR must be outside the platform checkout."}
    try:
        capacity = budget_capacity()
    except ValueError as exc:
        return 400, {"error": "budget-capacity", "detail": str(exc)}
    # Requested jobs is per-run parallelism; capacity is a shared deployment fact.
    if str(paths.console_parent) not in sys.path:
        sys.path.insert(0, str(paths.console_parent))
    from harnessgrad.slots import inspect_capacity, SlotBudgetError
    try:
        inspect_capacity(paths.budget_dir, capacity)
    except SlotBudgetError as exc:
        return 400, {"error": "budget-capacity", "detail": str(exc)}
    return None


def _catalogue_selection(form, catalogue):
    known = {a["name"] for a in catalogue.adapters()}
    if form.get("dataset") not in known:
        return 400, {"error": "unknown-dataset",
                     "detail": f"{form.get('dataset')!r} 不是 KIND=dataset 的适配器"}
    if form.get("harness") not in set(catalogue.base_harnesses()):
        return 400, {"error": "unknown-harness",
                     "detail": f"task-harness {form.get('harness')!r} 不在可启动清单里"}
    return None


def _solver_credentials(form, env, backend, model, preview_key_pending):
    if backend == "openai" and not env["HG_AGENT_BASE_URL"]:
        return 400, {"error": "no-task-url", "detail": "实时 task 模型需要填写 endpoint URL"}
    for field in ("task_base_url", "meta_base_url"):
        value = str((env["HG_AGENT_BASE_URL"] if field == "task_base_url" and backend == "openai" else form.get(field)) or "")
        if value:
            try:
                if any(ord(c) < 32 or ord(c) == 127 for c in value):
                    raise ValueError('unsafe endpoint')
                parsed = urlparse(value)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError('unsafe endpoint')
            except ValueError:
                return 400, {"error": "bad-endpoint", "detail": f"{field} 必须是无凭据、无查询参数的 HTTP(S) endpoint URL"}
    if any(ord(c) < 32 or ord(c) == 127 for c in model):
        return 400, {"error": "bad-task-model", "detail": "task-model 不能包含控制字符"}
    if backend == "openai" and not env["HG_AGENT_API_KEY"] and not preview_key_pending:
        return 400, {"error": "no-task-key", "detail": "实时 task 模型需要 API key；本地服务也请填写其要求的占位 key"}
    return None


def _editor_credentials(form, catalogue, env, preview_meta_key_pending):
    if _single_measurement(form):
        return None
    if not form.get("improver"):
        return 400, {"error": "no-improver", "detail": "train 侧必须指定 meta-harness（改进器）"}
    selected = next((row for row in catalogue.improvers() if row['name'] == form.get('improver')), None)
    if not selected:
        return 400, {"error": "unknown-improver", "detail": "请选择可用的 meta-harness"}
    if (form.get('meta_api_key') or preview_meta_key_pending) and not form.get('meta_base_url'):
        return 400, {"error": "no-meta-url", "detail": "填写 meta API key 时也必须填写 endpoint URL"}
    if form.get('meta_base_url'):
        if not env.get('HG_METHOD_MODEL'):
            return 400, {"error": "incomplete-meta-endpoint", "detail": "覆盖 meta endpoint 时必须填写模型名"}
        if not env.get('HG_METHOD_API_KEY') and not preview_meta_key_pending:
            return 400, {"error": "no-meta-key", "detail": "覆盖 meta endpoint 时必须填写 API key"}
    # Model names are supplied by the operator; the registry is a suggestion, not an
    # exhaustive list. Endpoint credentials are checked again on POST, never in a URL.
    if any(ord(c) < 32 or ord(c) == 127 for c in str(form.get("improver_model") or "")):
        return 400, {"error": "bad-meta-model", "detail": "meta-model 不能包含控制字符"}
    return None


def _count_bounds(form):
    for key, lo, hi in (("rounds", 0, 20), ("jobs", 1, 64), ("trials", 1, 3)):
        raw = form.get(key)
        if type(raw) is not int and not (isinstance(raw, str) and re.fullmatch(r'[0-9]{1,9}', raw)):
            return 400, {"error": "bad-value", "detail": f"{key} 必须是整数"}
        value = int(raw)
        if not lo <= value <= hi:
            return 400, {"error": "bad-value", "detail": f"{key} 必须在 {lo}..{hi}"}
    return None


def validate_launch_request(form: dict, *, paths, catalogue, python_supported, sandbox_supported,
                            budget_capacity, launch_env, preview_key_pending=False,
                            preview_meta_key_pending=False) -> tuple[int, dict]:
    """Validate in the historical fail-closed order, without starting anything."""
    problem = _field_types(form)
    if problem:
        return problem
    problem = _count_bounds(form)
    if problem:
        return problem
    problem = _host_capabilities(form, paths, catalogue, python_supported, sandbox_supported)
    if problem:
        return problem
    problem = _storage_and_budget(paths, budget_capacity)
    if problem:
        return problem
    problem = _catalogue_selection(form, catalogue)
    if problem:
        return problem
    backend = str(form.get("task_backend") or ("mock" if form.get("task_model") == "mock" else "openai"))
    if backend not in ("mock", "openai"):
        return 400, {"error": "bad-task-backend", "detail": "task backend 只能是 mock 或 openai"}
    model = str(form.get("task_model") or "").strip()
    if backend == "openai" and (not model or model == "mock"):
        return 400, {"error": "no-task-model", "detail": "实时 task 模型需要填写实际模型名（不能是 mock）"}
    if backend == "mock" and model not in ("", "mock"):
        return 400, {"error": "task-backend-mismatch", "detail": "mock backend 只能使用 mock 模型"}
    env = launch_env(form)
    problem = _solver_credentials(form, env, backend, model, preview_key_pending)
    if problem:
        return problem
    problem = _editor_credentials(form, catalogue, env, preview_meta_key_pending)
    if problem:
        return problem
    return 200, {}


def _write_improver_override(paths, selected, form, environment, run_id):
    if selected and selected.get('kind') != 'cli' and form.get('meta_base_url'):
        # Preserve registry identity while making the selected endpoint authoritative.
        # No key enters this file; the selected entry points to the child's environment.
        registry = read_json(paths.platform / 'harnessgrad/improvers/improvers.json')
        spec = registry['improvers'][selected['name']]
        spec.update(base_url=environment['HG_METHOD_BASE_URL'], model=environment['HG_METHOD_MODEL'],
                    api_key_env='HG_METHOD_API_KEY')
        paths.launch_dir.mkdir(parents=True, exist_ok=True)
        registry_path = paths.launch_dir / f'{run_id}.improvers.json'
        with registry_path.open('x', encoding='utf-8') as stream:
            json.dump(registry, stream, ensure_ascii=False)
        registry_path.chmod(0o600)
        environment['HG_IMPROVER_CONFIG'] = str(registry_path.resolve())


def start(form: dict, *, paths, catalogue, plan, make_run_id, spawn, base_environment) -> tuple[int, dict]:
    code, err = plan.validate(form)
    if code != 200:
        return code, err
    run_id = make_run_id(form)
    argv = plan.argv(form, run_id)
    environment = {**base_environment, **plan.environment(form)}
    selected = next((row for row in catalogue.improvers() if row['name'] == form.get('improver')), None)
    _write_improver_override(paths, selected, form, environment, run_id)
    paths.launch_dir.mkdir(parents=True, exist_ok=True)
    log_path = paths.launch_dir / f"{run_id}.log"
    with log_path.open("ab") as handle:
        spawn(argv, stdout=handle, stderr=subprocess.STDOUT,
                         start_new_session=True, cwd=str(paths.platform), env=environment)
    with paths.launch_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "run_id": run_id, "argv": argv, "by": "console /launch.html"},
                                ensure_ascii=False) + "\n")
    return 202, {"run_id": run_id, "argv": argv, "log": str(log_path.relative_to(paths.state_root))}


def scope_preview(form: dict, effective_env: dict, *, catalogue) -> dict:
    """Describe requested scope without loading datasets or contacting model endpoints."""
    single = _single_measurement(form)
    selected = {} if single else next((row for row in catalogue.improvers() if row.get('name') == form.get('improver')), {})
    kind = selected.get('kind') or None
    model = None if single else (effective_env.get('HG_METHOD_MODEL') or form.get('improver_model')
                                 or selected.get('model') or None)
    return {
        "dataset": form.get("dataset"), "side": "train", "split": "declared",
        "sampling": form.get("sampling") or "all", "task_count": None,
        "rounds": 0 if single else form.get("rounds"), "trials_per_task": form.get("trials"),
        "jobs": form.get("jobs"),
        "solver": {"backend": effective_env.get("HG_AGENT_BACKEND"),
                   "model": effective_env.get("HG_AGENT_MODEL") or None},
        "editor": {"method": form.get("method") or "none", "improver": "none" if single else form.get("improver"),
                   "model": model, "kind": kind,
                   "active": False if single else None},
        # Dataset-specific model use and CLI improver connections are resolved at runtime.
        "environment": None, "judge": None,
    }


def preview(form: dict, *, plan, catalogue) -> dict:
    """Preview without receiving credentials in the URL; POST revalidates actual keys."""
    form = {k: v for k, v in form.items() if k not in ("task_api_key", "meta_api_key")}
    code, err = plan.validate(form,
        preview_key_pending=form.get("task_key_present") in (True, "true", "1"),
        preview_meta_key_pending=form.get("meta_key_present") in (True, "true", "1"))
    try:
        argv = plan.argv(form, "rsi10-<UTC>-<dataset>-<improver>-train")
    except (KeyError, TypeError, ValueError, OverflowError):
        argv = []
        if code == 200:
            code = 400
            err = {"error": "bad-value", "detail": "运行参数不完整或格式不正确"}
    effective_env = plan.environment(form)
    shown_env = {key: ("***" if "KEY" in key else display_base_url(value) if key.endswith("BASE_URL") else value)
                 for key, value in effective_env.items()}
    form = dict(form)
    for field in ("task_base_url", "meta_base_url"):
        if field in form:
            value = str(form[field] or "")
            form[field] = display_base_url(value)
            form[field + "_set"] = bool(value)
            form[field + "_redacted"] = bool(value and not form[field])
    return {
        "argv": argv,
        "env": shown_env,
        "form": form,
        "preflight": {"ok": code == 200, "status": code, **err},
        "scope": scope_preview(form, effective_env, catalogue=catalogue),
    }
