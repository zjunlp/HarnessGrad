"""Is this machine ready for the run described on the command line?

**The defect this module exists for: a new user could only find out by paying.** Every
prerequisite below -- an unprovisioned dataset, a `bwrap` that cannot build a namespace,
a harness whose `harness.json` names an entrypoint that is not on disk -- used to be
discovered *inside* the run, after round 0 had already spent model calls. `driver.py
--check-only` asks this module the same question the run will ask, from the same parsed
argv, and prints the answer before anything is created, staged or called.

Three rules the checks keep, each learned from the checks that came before them:

* **A check reports a fact; it does not act.** It runs no model, starts no harness and
  writes nothing. Subprocesses inspect tool versions/builds and the sandbox's own
  namespace probe (`true` under `bwrap`), so `--check-only` is
  safe on a machine where nothing has been configured yet -- which is exactly when it is
  worth running.
* **`required` is a property of the selected run, not of the check.** `bwrap` is
  required by every run that does not pass `--no-sandbox` and optional for one that does.
  Disk headroom is never a reason to refuse a run (a small task set fits in little space)
  but it is exactly the reason a run dies at task 37, so it warns instead.
* **A check never raises.** `plan_checks` turns an unexpected exception into a failed row
  naming the exception: a preflight that tracebacks tells the user nothing about what to
  fix, and this module is read by people whose platform is not working yet.

`render()` is a fixed-width table on purpose: the point of the feature is that one screen
answers whether to start. `all_required_ok()` is the driver's exit code, so the same table
is usable from a script -- `driver.py --check-only && driver.py ...`.

The console has its own launch-time preflight (`console/server.py:guard_launch`), and a
copy of that logic is not imported here: `data/`, `eval/`, `harnessgrad/` and `tools/`
never import `console/`, and the check that matters *before a run* belongs where the run
is launched, not in one front end's form validation.
"""
from __future__ import annotations

import json
import importlib.util
import os
import shlex
import shutil
import shlex
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import data.scaffolds.registry as datasets
from harnessgrad import PLATFORM_ROOT
from harnessgrad.config import current

#: The interpreter floor the platform declares (README, `pyproject`-free by design).
MIN_PYTHON = (3, 12)
#: A task set materialises working trees and, for some datasets, a runtime venv; under
#: this much free space a run is more likely to die at task N than to finish. Optional
#: because "how much is enough" is a property of the dataset, not of the platform.
FREE_DISK_BYTES = 5 * 1024 ** 3
#: A namespace probe that has not answered in five seconds is a broken bwrap, not a slow
#: one: the real probe inside `sandbox.self_check` uses the same order of magnitude.
PROBE_TIMEOUT = 5
#: The endpoint check must not make `--check-only` feel like a hang. `--check-net` is
#: opt-in for that reason: an unreachable endpoint is a fact worth having, but the probe
#: leaves the machine, so it never runs unless asked for.
ENDPOINT_TIMEOUT = 3
#: One line per check, whatever the probe found. A detail that wraps destroys the columns
#: that let a reader scan the table, and a stack trace pasted into a cell is worse.
DETAIL_WIDTH = 96


@dataclass(frozen=True)
class Check:
    """One prerequisite, as a fact rather than as a plan.

    `required` is part of the row because it is not a constant of the check: the selected
    run decides. `ok` for a skipped optional check is `True` -- nothing failed, there was
    simply nothing to do (`bwrap` under `--no-sandbox`) -- so a reader can tell "not
    needed" from "not working" by the detail.
    """

    name: str
    required: bool
    ok: bool
    detail: str


def plan_checks(args) -> list[Check]:
    """Every prerequisite of the run `args` describes, one row each, in reading order.

    The order is the order a run meets them: the interpreter, the tool the round lineage
    is committed with, the sandbox boundary, the data, the harness that runs inside that
    boundary, the two writable roots, then disk -- which is long-lived rather than fatal.
    `endpoint` is appended last and only on request because it is the one check that leaves
    the machine (`--check-net`).

    `args` is an argparse namespace from the same parser a real run uses, so the answer
    is about *that* run: `--dataset`, `--harness`/`--harness-path`, `--runs-root`,
    `--work-root` and `--no-sandbox` all change what is checked, not just what is
    printed. A caller that has already resolved the harness directory (driver.py) puts it
    on `args.harness_dir`; see `_harness_dir`.
    """
    guards = [
        ("python", True, _python_check),
        ("git", True, _git_check),
        ("bwrap", bool(getattr(args, "sandbox", True)), _bwrap_check),
        ("dataset", True, _dataset_check),
        ("harness", True, _harness_check),
        ("writable", True, _writable_check),
        ("disk", False, _disk_check),
    ]
    if getattr(args, 'split', None) not in (None, 'declared'):
        guards.insert(4, ('split', True, _split_check))
    checks: list[Check] = []
    for name, required, probe in guards:
        try:
            checks.append(probe(args))
        except Exception as exc:                              # noqa: BLE001 - see module doc
            checks.append(Check(name, required, False,
                                f"probe raised {type(exc).__name__}: {exc}"))
    try:
        checks.extend(configuration_checks(args))
    except (OSError, ValueError, TypeError, AttributeError):
        checks.append(Check('models', True, False, 'model/method configuration is invalid; no task launched'))
    if any(check.name == 'dataset' and check.ok for check in checks):
        checks.extend(selected_model_checks(args))
    # `method_checks` is the pull request's narrower probe (entrypoint + declared editor
    # requirement). `configuration_checks` already answers the same question with the full
    # editor/endpoint plan, so this is a fallback for the cases that returned early -- not a
    # second row for the same thing.
    if not any(check.name == 'method' for check in checks):
        try:
            checks.extend(method_checks(args))
        except (OSError, ValueError, TypeError, AttributeError):
            checks.append(Check('method', True, False,
                                'invalid method configuration; no task launched'))
    if getattr(args, "check_net", False):
        try:
            checks.extend(endpoint_checks(args))
        except (OSError, ValueError, TypeError, AttributeError):
            checks.append(Check('endpoint', True, False, 'model channel configuration is invalid; no probe sent'))
    return checks


def _method_plan(args):
    """Inspect trusted declarations only; never import/run candidate or method code."""
    rounds = getattr(args, 'rounds', None)
    if rounds is None:
        rounds = 0 if getattr(args, 'sampling', '') in ('diagnostic', 'pilot_10pct') else 3
    if getattr(args, 'side', 'train') == 'eval' or rounds == 0:
        return None
    entry = getattr(args, 'method_entrypoint', None)
    if not entry:
        return {'error': 'improvement rounds need --method-entrypoint (or experiment method)'}
    from harnessgrad.methods import _check_method_entrypoint
    argv = shlex.split(entry) if isinstance(entry, str) else entry
    if not argv or not (Path(argv[0]).is_file() or shutil.which(argv[0])):
        return {'error': 'method executable is missing'}
    problem = _check_method_entrypoint(argv)
    if problem:
        return {'error': problem}
    if any(part.endswith('.py') and not Path(part).is_file() for part in argv):
        return {'error': 'method script is missing; use an existing absolute entrypoint'}
    method = None
    for arg in argv:
        path = Path(arg)
        if path.name == 'run.py' and path.parent.parent.resolve() == (PLATFORM_ROOT / 'methods').resolve():
            method = path.parent
            break
    if method is None:
        return {'custom': True}  # A self-contained external method may need no editor.
    manifest = json.loads((method / 'method.json').read_text())
    if manifest.get('kind') not in ('published', 'improver'):
        return {'custom': True}
    return {'name': method.name, 'requires': manifest.get('requires') or {},
            'endpoint_only': bool(manifest.get('requires', {}).get('editor_model'))
                or method.name == 'rrsi' or (method.name == 'meta_harness' and rounds > 1)}


def _editor_plan(args, method):
    cfg = current()
    direct = {'kind': 'openai-compatible', 'base_url': cfg.method.base_url,
              'model': cfg.method.model, 'key': cfg.method.api_key}
    requested = getattr(args, 'improver', None)
    if requested in ('none', 'noop', 'null'):
        return direct
    path = Path(cfg.improver.registry) if cfg.improver.registry else PLATFORM_ROOT/'harnessgrad/improvers/improvers.json'
    registry = json.loads(path.read_text())
    chosen = requested or registry.get('default')
    entry = (registry.get('improvers') or {}).get(chosen)
    if entry is None:
        if requested and (Path(requested).is_file() or shutil.which(requested)):
            return {'kind': 'cli', 'path': requested}
        return {'error': 'selected improver is not declared or executable; configure --improver local or HG_METHOD_*'}
    if entry.get('kind') == 'cli':
        executable = entry.get('path') or shutil.which(chosen)
        launcher = entry.get('launcher')
        if launcher:
            executable = str(PLATFORM_ROOT / launcher)
        return {'kind':'cli', 'path': executable}
    key = os.environ.get(entry['api_key_env']) if entry.get('api_key_env') else cfg.method.api_key
    selected = {'kind': 'openai-compatible', 'base_url': entry.get('base_url') or cfg.method.base_url,
            'model': getattr(args, 'improver_model', None) or entry.get('model') or cfg.method.model,
            'key': key}
    # Match the driver: an unavailable default improver does not override a
    # fully configured method-owned endpoint. An explicit named selection does.
    if requested is None and not all(selected.get(k) for k in ('base_url','model','key')):
        if all(direct.get(k) for k in ('base_url','model','key')):
            return direct
    return selected


def _resource(root, relative):
    if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
            or '..' in Path(relative).parts):
        return False
    path = root / relative
    return path.resolve().is_relative_to(root.resolve()) and path.is_file()


def configuration_checks(args):
    """Cheap local prerequisites, shared by check-only and the real pre-H0 gate."""
    checks = []
    agent = current().agent
    if agent.backend != 'mock':
        missing = [label for label, value in [('HG_AGENT_MODEL',agent.model),
            ('HG_AGENT_BASE_URL',agent.base_url),('HG_AGENT_API_KEY',agent.api_key)] if not value]
        checks.append(Check('agent', True, not missing,
            'configure ' + ', '.join(missing) if missing else f'{agent.backend}: model configured; credential present'))
    try:
        method = _method_plan(args)
        if method is None or method.get('custom'):
            return checks
        if method.get('error'):
            return [*checks, Check('method', True, False, method['error'])]
        # Two rows, because they fail for different reasons and a reader acts on them
        # differently: `editor` is "the model that proposes edits is not configured",
        # `method` is "this method cannot run this experiment". Collapsing them made a
        # missing HG_METHOD_* look like a property of the method.
        editor = _editor_plan(args, method)
        editor_error = editor.get('error')
        if editor.get('kind') == 'cli':
            path = editor.get('path')
            if not path or not (Path(path).is_file() or shutil.which(path)):
                editor_error = 'selected CLI improver executable is missing'
            if method['endpoint_only']:
                direct = current().method
                if not all((direct.base_url, direct.model, direct.api_key)):
                    editor_error = 'this method has structured roles requiring HG_METHOD_BASE_URL / HG_METHOD_MODEL / HG_METHOD_API_KEY as well as its CLI editor'
        elif not editor_error:
            missing = [label for label in ('base_url','model','key') if not editor.get(label)]
            if missing:
                editor_error = 'method editor needs HG_METHOD_BASE_URL / HG_METHOD_MODEL / HG_METHOD_API_KEY (or an explicitly configured improver)'
        if editor_error:
            checks.append(Check('editor', True, False, editor_error))
        error = None
        requires = method['requires']
        minimum = requires.get('min_trials', 1)
        if getattr(args, 'trials', 1) < minimum:
            error = f"{method['name']} needs --trials >= {minimum}"
        root, root_error = _harness_dir(args)
        options = json.loads(os.environ.get('HG_POLICY_CONFIG', '{}'))
        if not isinstance(options, dict):
            raise ValueError('HG_POLICY_CONFIG must be a JSON object')
        if requires.get('instruction_file') and not root_error:
            instruction = options.get('instruction_path')
            choices = [p for p in ('prompts/system.md','prompt.md','CLAUDE.md','AGENTS.md') if _resource(root,p)]
            if not (instruction and _resource(root,instruction)) and not (instruction is None and len(choices)==1):
                error = 'declare a loaded instruction_path in HG_POLICY_CONFIG or use a prompt-carrying harness (e.g. miniagent)'
        if requires.get('modules'):
            minimum = max(minimum, options.get('num_rollouts', 2))
            if getattr(args, 'trials', 1) < minimum:
                error = f"ModularRSI needs --trials >= num_rollouts ({minimum})"
            modules = options.get('modules')
            if (not isinstance(modules,dict) or not modules or any(not isinstance(v,list) or not v for v in modules.values())):
                error = 'ModularRSI needs explicit HG_POLICY_CONFIG modules {name:[relative files]}'
            else:
                paths = [p for values in modules.values() for p in values]
                if len(paths)!=len(set(paths)) or any(not _resource(root,p) for p in paths):
                    error = 'ModularRSI modules need disjoint existing harness files'
        checks.append(Check('method', True, not error, error or f"{method['name']}: editor and local requirements configured"))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        checks.append(Check('method', True, False, f'method configuration is invalid ({type(exc).__name__})'))
    return checks


def _probe_endpoint(name, base, key, *, required=True):
    if not base:
        return Check(name, required, False, 'required endpoint base URL is not configured')
    started = time.monotonic()
    try:
        request = urllib.request.Request(str(base).rstrip('/') + '/models', method='GET',
            headers={'Accept':'application/json', **({'Authorization':'Bearer '+key} if key else {})})
        with urllib.request.urlopen(request, timeout=ENDPOINT_TIMEOUT) as response:
            response.read(1 << 20)
            status = response.status
        return Check(name, required, True, f'GET /models -> {status} in {time.monotonic()-started:.2f}s; no generation requested')
    except Exception as exc:
        # Provider errors can echo credentials or URLs with query secrets.
        return Check(name, required, False, f'GET /models failed ({type(exc).__name__}); check endpoint, authentication and model route')


def endpoint_checks(args):
    checks = []
    agent = current().agent
    if agent.backend != 'mock':
        checks.append(_probe_endpoint('agent-endpoint', agent.base_url, agent.api_key))
    method = _method_plan(args)
    if method and not method.get('custom') and not method.get('error'):
        editor = _editor_plan(args, method)
        if editor.get('kind') == 'openai-compatible':
            checks.append(_probe_endpoint('method-endpoint', editor.get('base_url'), editor.get('key')))
        elif method.get('endpoint_only'):
            cfg = current().method
            checks.append(_probe_endpoint('method-endpoint', cfg.base_url, cfg.api_key))
    if not checks:
        checks.append(Check('endpoint', False, True, 'no live HTTP model channel selected; no network probe needed'))
    return checks


def _selected_declarations(args):
    """Resolve exactly the run's pre-H0 task selection without reading other values."""
    from data.scaffolds.custom_split import resolve
    from eval.run.metrics import select_task_set
    tasks, _, declared = datasets.load_split(args.dataset)
    division, _, _ = resolve(args.dataset, tasks, declared, getattr(args, 'split', None), side=args.side)
    ids = list(division[args.side]) if division else [t['task_id'] for t in tasks]
    requested = getattr(args, 'tasks', None)
    if requested:
        asked = {tid.strip() for tid in requested.split(',') if tid.strip()}
        if not asked <= set(ids):
            raise ValueError('requested tasks do not belong to selected split side')
        ids = [tid for tid in ids if tid in asked]
    sampling = getattr(args, 'sampling', 'all')
    if sampling in ('diagnostic', 'pilot_10pct') or sampling.startswith('random_'):
        ids = select_task_set({}, sampling, within=ids)
    # Score-based sampling needs every selected-side task for H0, before its
    # scores exist. It cannot reduce model prerequisites before that first pass.
    if not ids:
        raise ValueError('no tasks selected')
    _, setups, verifiers = datasets.load_tasks(args.dataset, task_ids=ids)
    return setups, verifiers


def selected_model_checks(args, *, verifiers=None, setups=None):
    """Validate ordinary selected verifier/environment channels without starting them.

    Judge.__init__ validates frozen specs and local configuration only. No socket,
    subprocess, filesystem output or model request is created until __enter__,
    which preflight never calls. Native dual-provider/container routes keep their
    own separate readiness contracts.
    """
    from eval.service.judge import Judge
    try:
        if verifiers is None or setups is None:
            setups, verifiers = _selected_declarations(args)
    except Exception as exc:
        return [Check('selected-models', True, False,
            f'selected dataset declarations could not be loaded ({type(exc).__name__}); check selected task data/runtime configuration')]
    specifications = {'judge': [], 'environment': []}
    for spec in verifiers.values():
        if spec.get('judge') is not None:
            specifications['judge'].append(spec['judge'])
    for spec in setups.values():
        model = (spec.get('tool_relay') or {}).get('model')
        if model is not None:
            specifications['environment'].append(model)
    checks = []
    for role, declarations in specifications.items():
        if not declarations:
            continue
        prefix = 'HG_JUDGE' if role == 'judge' else 'HG_ENV'
        try:
            cfg = getattr(current(), role)
            for declaration in declarations:
                # A trial seed is an execution context, not a provider setting.
                # Use a well-typed stand-in solely to validate the channel spec.
                Judge(declaration, role=role, trial_seed=0)
            checks.append(Check(role, True, True,
                f'{len(declarations)} selected {role} model declarations configured; no generation requested'))
            if getattr(args, 'check_net', False):
                checks.append(_probe_endpoint(role+'-endpoint', cfg.base_url, cfg.api_key))
        except Exception as exc:
            try:
                missing_base = not getattr(current(), role).base_url
            except Exception:
                missing_base = False
            detail = (f'{prefix}_BASE_URL must be configured for the selected {role} channel'
                if missing_base else f'selected {role} channel configuration invalid ({type(exc).__name__}); '
                    f'check {prefix}_BASE_URL and the frozen model/profile declaration')
            checks.append(Check(role, True, False, detail))
    return checks


def method_checks(args) -> list[Check]:
    """Validate declared method requirements without executing a method or model."""
    if getattr(args, "side", "train") == "eval" or getattr(args, "rounds", 0) == 0:
        return [Check("method", False, True, "no improvement rounds requested")]
    entry = getattr(args, "method_entrypoint", None)
    if not entry:
        return [Check("method", True, False, "improvement rounds need --method-entrypoint")]
    try:
        argv = shlex.split(entry) if isinstance(entry, str) else list(entry)
        from harnessgrad.methods import _check_method_entrypoint
        problem = _check_method_entrypoint(argv)
        if problem:
            return [Check("method", True, False, problem)]
        checks = [Check("method", True, True, "entrypoint exists; execution is not probed")]
        script = next((Path(part) for part in argv if part.endswith(".py")), None)
        manifest = script.parent / "method.json" if script is not None else None
        requirements = (json.loads(manifest.read_text(encoding="utf-8")).get("requires") or {}
                        if manifest is not None and manifest.is_file() else {})
        if requirements.get("editor_model"):
            checks.append(_editor_check(args))
        return checks
    except Exception as exc:                                  # noqa: BLE001 - a check reports failures
        return [Check("method", True, False, f"invalid method configuration: {exc}")]


def _editor_check(args) -> Check:
    """Use the existing improver resolver; external/self-contained methods opt out."""
    from tools.improver import load_config, resolve

    config = current()
    if config.improver.selection:
        # methods.cli_improver gives the explicit command priority over registry names.
        command = shlex.split(config.improver.selection)
        if not command or shutil.which(command[0]) is None:
            executable = command[0] if command else "<empty>"
            return Check("editor", True, False, f"CLI improver is not executable: {executable}")
        return Check("editor", True, True, f"CLI improver: {command[0]}")
    requested = getattr(args, "improver", None) or config.improver.name or ""
    disabled = requested.strip().lower() in ("none", "noop", "null")
    direct = all((config.method.base_url, config.method.model, config.method.api_key))
    resolved = {} if disabled else resolve(
        load_config(), requested=requested,
        model_override=getattr(args, "improver_model", None) or "")
    if resolved.get("ok") and resolved.get("kind") == "cli":
        return Check("editor", True, True, f"CLI improver: {resolved.get('name')}")
    if requested and not disabled and not resolved.get("ok"):
        return Check("editor", True, False, resolved.get("reason") or "improver unavailable")
    if resolved.get("ok"):
        key_env = resolved.get("api_key_env")
        key = os.environ.get(key_env) if key_env else config.method.api_key
        configured = bool(resolved.get("base_url") and resolved.get("model") and key)
    else:
        configured = direct
    if not configured:
        return Check("editor", True, False,
                     "configure an improver or HG_METHOD_BASE_URL / HG_METHOD_MODEL / HG_METHOD_API_KEY")
    if importlib.util.find_spec("openai") is None:
        return Check("editor", True, False, "the method endpoint needs the openai package")
    return Check("editor", True, True, "endpoint configured; authentication is not probed")


def render(checks) -> str:
    """The table a human reads: a header and one aligned line per check.

    Every column but the detail is sized to its content, and the detail is collapsed to
    one line and cut at `DETAIL_WIDTH`, so the shape of the output does not depend on
    what the probes found. A probe that puts a kernel error or a task list in `detail`
    must not be able to make the table unreadable.
    """
    rows = [("check", "req", "result", "detail")]
    for check in checks:
        rows.append((check.name, "yes" if check.required else "no",
                     "ok" if check.ok else "FAIL", _one_line(check.detail)))
    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    return "\n".join(
        f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]:<{widths[2]}}  {row[3]}"
        for row in rows)


def all_required_ok(checks) -> bool:
    """True when nothing a run depends on failed. A failing optional row is a warning."""
    return all(check.ok for check in checks if check.required)


# ---------------------------------------------------------------- the checks ---

def _fixture_kind(name: str) -> bool:
    """Is `name` the platform's own scaffolding rather than a published dataset?

    Asked through the registry so this module still never imports an adapter itself. A
    module that cannot be imported is not a fixture: it is a name nothing answers to, and
    the caller reports it as one.
    """
    try:
        return datasets.kind_of(name) == "fixture"
    except Exception:                                         # noqa: BLE001
        return False


def _python_check(_args=None) -> Check:
    """The floor, checked against the interpreter that would actually run the platform.

    Not `python3 --version` from PATH: in a sandboxed or venv deployment the driver can
    run under an interpreter PATH does not name, and the version that matters is the one
    already executing.
    """
    version = ".".join(str(part) for part in sys.version_info[:3])
    ok = tuple(sys.version_info[:2]) >= MIN_PYTHON
    detail = f"python {version}"
    if not ok:
        detail += (f" — the platform needs >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}")
    return Check("python", True, ok, detail)


def _git_check(_args=None) -> Check:
    """Round lineage is committed with git (`eval/ckpt/git_state.py`), so it is required.

    A run without it stages no checkpoints: the method's edits exist only in a directory
    the next round overwrites, and an eval side finds no `state_after_round<N>` to
    measure. That is discovered today at the end of round 0.
    """
    ok, text = _run(["git", "--version"])
    if not ok:
        return Check("git", True, False,
                     f"git --version failed: {text or 'no output'}")
    return Check("git", True, True, text)


def _bwrap_check(args) -> Check:
    """The binary exists **and** a real namespace builds -- the second half is the point.

    `shutil.which("bwrap")` proves nothing: a container host with user namespaces disabled
    has the binary and fails every mount. So the probe is the invocation the platform's
    own plan is built from (`eval/containers/sandbox.py:command`): the whole host bound
    read-only, `/dev` and `/proc` fresh, one `true` to run. `sandbox._bwrap()` supplies
    the path so `HG_BWRAP` is honoured -- the override exists so the installer and the
    platform check the same binary.

    Not `sandbox.self_check()` here even though it is the stronger probe: it needs a work
    root outside the platform and writes two throwaway trees, and `--check-only` must be
    safe to run before those roots exist. It remains the door check a real run performs,
    so a green row here is a necessary, not a sufficient, condition.
    """
    required = bool(getattr(args, "sandbox", True))
    if not required:
        return Check("bwrap", False, True, "not needed: --no-sandbox")
    try:
        from eval.containers.sandbox import SandboxUnavailable, _bwrap
    except Exception as exc:                                  # noqa: BLE001
        return Check("bwrap", True, False, f"cannot load the sandbox module: {exc}")
    try:
        exe = _bwrap()
    except SandboxUnavailable as exc:
        return Check("bwrap", True, False, str(exc))
    argv = [exe, "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/",
            "--dev", "/dev", "--proc", "/proc", "--", "true"]
    ok, text = _run(argv, timeout=PROBE_TIMEOUT)
    if not ok:
        return Check("bwrap", True, False, f"{exe}: a namespace did not build: {text}")
    version = _run([exe, "--version"])[1] or "version unknown"
    return Check("bwrap", True, True, f"{exe}: namespace builds ({version})")


def _dataset_check(args) -> Check:
    """Published, provisioned, and loadable -- three separate ways to fail, named apart.

    They are checked in that order because the fix differs: a name that is not published
    is a typo or a fixture (`data.demo`), a published adapter whose bytes are absent is a
    `tools/datasets.py --fetch`, and a `load()` that raises is the adapter's own refusal
    (a blocker it declares, a judge it cannot configure). Collapsing them into one
    "dataset unavailable" would send the user to the wrong fix.

    `provision_status()` is consulted only when the adapter has one: a module without it
    is assumed provisioned, which is the documented rule (`tools/dataset_lint.py`) and
    what `load()` decides anyway.
    """
    name = getattr(args, "dataset", None)
    if not name:
        return Check("dataset", True, False, "no --dataset selected")
    published = datasets.published()
    if name not in published:
        # A fixture is not a typo. `demo` is the dataset every zero-cost smoke in
        # `README.md` step 2 and 21 test modules use, and the driver runs it; refusing it
        # here made the preflight disagree with the run it is supposed to certify
        # (measured 2026-11-02: `driver.py --dataset demo ... --check-only` printed
        # "'demo' is not a published dataset" for a run that then scored 0.000 on 8 tasks).
        # The distinction that matters is "the driver will run this" versus "nothing does".
        if _fixture_kind(name):
            try:
                tasks, _scorable = datasets.load(name)
            except Exception as exc:                          # noqa: BLE001
                return Check("dataset", True, False,
                             f"{name}: fixture, load() raised {type(exc).__name__}: {exc}")
            if not tasks:
                return Check("dataset", True, False, f"{name}: fixture, load() returned no tasks")
            return Check("dataset", True, True,
                         f"{name}: {len(tasks)} tasks (test fixture, not a published dataset)")
        return Check("dataset", True, False,
                     f"{name!r} is not a published dataset; "
                     f"`python3 tools/datasets.py --list` prints the {len(published)} "
                     f"that are")
    # Do not call provision_status here: some adapters implement it by probing
    # every instance runtime, including tasks this run never selects. Public
    # inventory establishes source presence; selected SETUP/VERIFY values are
    # checked independently below. Full-pool status remains an explicit host tool.
    try:
        tasks, _scorable = datasets.load(name)
    except Exception as exc:                                  # noqa: BLE001
        return Check("dataset", True, False,
                     f"{name}: load() raised {type(exc).__name__}: {exc}")
    if not tasks:
        return Check("dataset", True, False, f"{name}: load() returned no tasks")
    detail = f"{name}: {len(tasks)} tasks; source loaded, checking selected declarations"
    return Check("dataset", True, True, detail)


def _split_check(args) -> Check:
    """Use the real resolver before a check-only launch can call a split valid."""
    from data.scaffolds.custom_split import resolve
    try:
        tasks, _, declared = datasets.load_split(args.dataset)
        split, _, protocol = resolve(args.dataset, tasks, declared, args.split, side=args.side)
        detail = f"{len(split['train'])} train / {len(split['eval'])} eval"
        if protocol:
            detail += f" / {protocol['counts']['excluded']} excluded; custom held-out"
        return Check('split', True, True, detail)
    except (OSError, ValueError, ImportError) as exc:
        return Check('split', True, False, str(exc))


def _split_check(args) -> Check:
    """Use the real resolver before a check-only launch can call a split valid."""
    from data.scaffolds.custom_split import resolve
    try:
        tasks, _, declared = datasets.load_split(args.dataset)
        split, _, protocol = resolve(args.dataset, tasks, declared, args.split, side=args.side)
        detail = f"{len(split['train'])} train / {len(split['eval'])} eval"
        if protocol:
            detail += f" / {protocol['counts']['excluded']} excluded; custom held-out"
        return Check('split', True, True, detail)
    except (OSError, ValueError, ImportError) as exc:
        return Check('split', True, False, str(exc))


def _harness_check(args) -> Check:
    """The tree that runs, and the four facts about it the rest of the platform assumes.

    `harness.json` is the contract (`tools/validate_harness.py` holds the full schema).
    The four checked here are the ones a run cannot proceed without: `entrypoint` names
    the file the sandbox executes, `backend` decides how the model is reached, and
    `env_kinds` is what the driver compares against the dataset's environments before it
    stages anything. The deeper checks (naming, declared files, path safety) stay in the
    validator a run still runs; duplicating them here would be a second schema.
    """
    path, problem = _harness_dir(args)
    if problem:
        return Check("harness", True, False, problem)
    manifest_path = path / "harness.json"
    if not manifest_path.is_file():
        return Check("harness", True, False, f"{path}: no harness.json")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        return Check("harness", True, False, f"{manifest_path}: {exc}")
    if not isinstance(manifest, dict):
        return Check("harness", True, False, f"{manifest_path}: not a JSON object")
    missing = [key for key in ("entrypoint", "backend", "env_kinds")
               if not manifest.get(key)]
    if missing:
        return Check("harness", True, False,
                     f"{manifest_path}: missing {', '.join(missing)}")
    entrypoint = path / str(manifest["entrypoint"])
    if not entrypoint.is_file():
        return Check("harness", True, False,
                     f"{manifest_path}: entrypoint {manifest['entrypoint']!r} "
                     f"is not a file")
    kinds = manifest["env_kinds"]
    shown = ", ".join(str(kind) for kind in kinds) if isinstance(kinds, list) else str(kinds)
    return Check("harness", True, True,
                 f"{path} ({manifest['backend']}, entrypoint {manifest['entrypoint']}, "
                 f"env_kinds {shown})")


def _writable_check(args) -> Check:
    """Both roots must be usable, and the work root must be outside the platform.

    The second half is not a nicety: the workspace is where the harness runs, and a work
    root inside the platform is a harness that can read `driver.py` and `.env` and write
    the scorer (`driver.py` refuses it at the door, `workspace_is_outside_platform`).
    Reporting it here means `--check-only` cannot say "ready" for a run the driver will
    refuse one line later.

    Writability is probed without creating anything -- the nearest existing ancestor is
    the one that has to be writable -- because `--check-only` must not leave a directory
    behind.
    """
    runs_root = Path(getattr(args, "runs_root", "runs"))
    work_root = Path(getattr(args, "work_root", "../harnessgrad_work"))
    # The refusal a run meets first goes first: the detail is cut at `DETAIL_WIDTH`, and a
    # truncated tail must never hide the one line that says what to fix.
    details: list[str] = []
    failed: list[str] = []
    resolved = work_root.expanduser().resolve()
    platform = PLATFORM_ROOT.resolve()
    if resolved == platform or platform in resolved.parents:
        details.append(f"work root {resolved} is inside the platform {platform}, so a "
                       f"harness there could read the driver and .env")
        failed.append("work root")
    for label, path in (("runs root", runs_root), ("work root", work_root)):
        ok, detail = _can_write(path)
        details.append(f"{label}: {detail}")
        if not ok and label not in failed:
            failed.append(label)
    return Check("writable", True, not failed, "; ".join(details))


def _disk_check(args) -> Check:
    """Free space on the device the run record lands on, as a warning rather than a stop.

    The runs root's own device, not `/`: a run record on an NFS mount or a dedicated
    volume has a capacity `/` says nothing about. The threshold is a warning because the
    honest answer depends on the dataset -- an AgentDojo task materialises a 217 MB
    runtime venv, `gsm8k` materialises nothing -- and refusing a run that would fit is
    worse than starting one that may not.
    """
    target = Path(getattr(args, "runs_root", "runs")).expanduser()
    probe, problem = _existing_ancestor(target)
    if problem:
        return Check("disk", False, False, problem)
    try:
        free = shutil.disk_usage(probe).free
    except OSError as exc:
        return Check("disk", False, False, f"{probe}: {exc}")
    return Check("disk", False, free >= FREE_DISK_BYTES,
                 f"{probe}: {free / 2 ** 30:.1f} GB free "
                 f"(a run needs about {FREE_DISK_BYTES / 2 ** 30:.0f} GB)")


# ------------------------------------------------------------------ helpers ---

def _run(argv: list[str], *, timeout: int = PROBE_TIMEOUT) -> tuple[bool, str]:
    """(ok, last line of output) for one short probe. Never raises.

    `stdin=DEVNULL` because a probe that reads a terminal would hang `--check-only` in a
    CI job, and `errors="replace"` because a locale-mangled message is still a message.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{' '.join(argv)}: {exc}"
    lines = [line for line in (proc.stdout or proc.stderr or "").splitlines()
             if line.strip()]
    last = lines[-1].strip() if lines else ""
    return proc.returncode == 0, last


def _one_line(detail: str) -> str:
    """Collapse whitespace and cut at a fixed width, so the table keeps its shape."""
    text = " ".join((detail or "").split())
    if len(text) <= DETAIL_WIDTH:
        return text
    return text[:DETAIL_WIDTH - 3].rstrip() + "..."


def _without(text: str, secret: str) -> str:
    """A credential never reaches a rendered row, even if an exception quotes it."""
    return text.replace(secret, "***") if secret else text


def _existing_ancestor(path: Path) -> tuple[Path, str]:
    """The nearest path that exists, or a problem naming the one that never will."""
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return probe, f"{path} has no existing ancestor to create it under"
        probe = probe.parent
    return probe, ""


def _can_write(path: Path) -> tuple[bool, str]:
    """Can this directory be created/used, judged without creating anything.

    `os.access()` on the nearest existing ancestor is the honest test available without a
    write: it follows the same permission bits the `mkdir` would, and it leaves the tree
    exactly as it was found.
    """
    probe, problem = _existing_ancestor(path)
    if problem:
        return False, problem
    if not probe.is_dir():
        return False, f"{probe} exists and is not a directory"
    if not os.access(probe, os.W_OK | os.X_OK):
        return False, f"{probe} is not writable by this user"
    if probe == path:
        return True, f"{path} exists and is writable"
    return True, f"{path} can be created"


def _harness_roots() -> tuple[Path, ...]:
    """Where a harness may live, in the order the driver looks.

    The same tuple as `driver.py:_harness_roots`. It is repeated here rather than
    imported because `driver.py` is the entrypoint and importing it from the layer below
    would invert the dependency; `plan_checks` prefers `args.harness_dir`, which the
    driver fills with the directory it resolved, so the repeat is only the fallback a
    direct caller (a test, a tool) gets.
    """
    return (PLATFORM_ROOT / "base_harness",
            PLATFORM_ROOT / "tests" / "fixtures" / "base_harness")


def _harness_dir(args) -> tuple[Path, str]:
    """(directory, "") or (placeholder, problem) for the harness the run would stage.

    `harness_dir` set to `None` by the caller means "this run resolves to no harness" --
    an eval run whose `--from-run` record is not there, say -- and must not fall back to
    a name search: that would report a *different* harness as ready.
    """
    name = getattr(args, "harness", None) or "loop"
    explicit = (getattr(args, "harness_path", None)
                or getattr(args, "harness_dir", None))
    if explicit:
        path = Path(explicit)
        if not path.is_dir():
            return path, f"{path}: no such directory"
        return path, ""
    if hasattr(args, "harness_dir"):
        problem = getattr(args, "harness_problem", "") or (
            f"{name!r}: no harness resolved (searched "
            + ", ".join(str(base) for base in _harness_roots()) + ")")
        return PLATFORM_ROOT, problem
    for base in _harness_roots():
        if (base / name).is_dir():
            return base / name, ""
    searched = ", ".join(str(base) for base in _harness_roots())
    return PLATFORM_ROOT, f"{name!r}: no such harness (searched {searched})"


__all__ = ["Check", "plan_checks", "render", "all_required_ok"]
