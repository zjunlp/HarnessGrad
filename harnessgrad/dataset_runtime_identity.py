"""Managed loading/service/scoring artifacts bound to a selected measurement.

Only artifact references and task-to-role associations belong here. Task contents,
answers and credentials remain in their existing private channels. These identities
cover the runtime manager's venv/base-executable inventory, not a sealed operating
system or all dataset dependencies.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

from harnessgrad import dataset_runtime

KEY = "dataset_runtime_identity"
_PROVIDER_FIELDS = {"dataset", "runtime_id", "manifest_path", "python", "path",
                    "descriptor_sha256", "requirements_sha256"}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def _provider(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != _PROVIDER_FIELDS
            or not isinstance(value["dataset"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_]*", value["dataset"])):
        raise ValueError("invalid provider artifact identity")
    for key in ("runtime_id", "descriptor_sha256", "requirements_sha256"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            raise ValueError("invalid provider artifact digest")
    for key in ("manifest_path", "python", "path"):
        path = value[key]
        if (not isinstance(path, str) or not path or "\x00" in path or "\\" in path
                or not PurePosixPath(path).is_absolute() or path.startswith("//")
                or ".." in PurePosixPath(path).parts or str(PurePosixPath(path)) != path):
            raise ValueError("invalid provider artifact path")
    venv = PurePosixPath(value["path"])
    if (venv == PurePosixPath("/") or PurePosixPath(value["python"]) != venv / "bin/python"
            or PurePosixPath(value["manifest_path"]) != venv.parent / "artifact.json"):
        raise ValueError("provider artifact paths do not name one runtime")
    return dict(value)


def create(provider=None, tasks=None):
    provider = _provider(provider)
    if tasks is None:
        tasks = {}
    if not isinstance(tasks, dict):
        raise ValueError("runtime task bindings must be an object")
    selected, artifacts = {}, {}
    for tid, roles in tasks.items():
        if (not isinstance(tid, str) or not tid or not isinstance(roles, dict)
                or not roles or set(roles) - {"actor", "service", "verifier"}):
            raise ValueError("invalid runtime task/role bindings")
        selected[tid] = {}
        for role, binding in roles.items():
            binding = dataset_runtime.validate_binding(binding, role)
            artifact = {key: value for key, value in binding.items() if key != "role"}
            digest = hashlib.sha256(_canonical(artifact).encode()).hexdigest()
            artifacts[digest] = artifact
            selected[tid][role] = digest
    if provider is None and not selected:
        return None
    payload = {"version": 1, "provider": provider, "artifacts": artifacts, "tasks": selected}
    encoded = _canonical(payload)
    return {**json.loads(encoded), "sha256": hashlib.sha256(encoded.encode()).hexdigest()}


def validate(value):
    if (not isinstance(value, dict) or set(value) != {"version", "provider", "artifacts", "tasks", "sha256"}
            or type(value["version"]) is not int or value["version"] != 1):
        raise ValueError("invalid dataset_runtime_identity envelope")
    expected = create(value["provider"], _expand(value))
    if expected is None or value != expected:
        raise ValueError("dataset_runtime_identity digest mismatch or empty identity")
    return expected


def _expand(value):
    artifacts, tasks = value["artifacts"], value["tasks"]
    if not isinstance(artifacts, dict) or not isinstance(tasks, dict):
        raise ValueError("invalid runtime artifact/role tables")
    expanded = {}
    for tid, roles in tasks.items():
        if not isinstance(roles, dict):
            raise ValueError("invalid runtime task role mapping")
        expanded[tid] = {}
        for role, digest in roles.items():
            if (not isinstance(digest, str) or digest not in artifacts
                    or not isinstance(artifacts[digest], dict) or "role" in artifacts[digest]):
                raise ValueError("runtime role names an unknown artifact")
            expanded[tid][role] = {**artifacts[digest], "role": role}
    return expanded


def public_summary(value):
    """Dependency-only facts, without task association, paths or plan digests."""
    value = validate(value)
    facts = {}
    provider = value['provider']
    if provider is not None:
        facts[(provider['dataset'], provider['runtime_id'])] = {'provider'}
    for roles in _expand(value).values():
        for role, binding in roles.items():
            facts.setdefault((binding['dataset'], binding['runtime_id']), set()).add(role)
    return {'schema': 'harnessgrad.dataset-runtime-public-summary.v1',
            'scope': 'dependency-artifacts-without-task-associations',
            'artifacts': [{'dataset': dataset, 'runtime_id': runtime_id, 'roles': sorted(roles)}
                          for (dataset, runtime_id), roles in sorted(facts.items())]}


def fields(value):
    return {} if value is None else {KEY: validate(value)}


def problems(reference, current):
    if reference is None and current is None:
        return []
    for label, value in (("reference", reference), ("current measurement", current)):
        if value is None:
            return [f"dataset_runtime_identity is missing from the {label}"]
        try:
            validate(value)
        except (TypeError, ValueError) as exc:
            return [f"invalid dataset_runtime_identity on the {label}: {exc}"]
    return [] if reference == current else ["dataset_runtime_identity changed: selected runtime artifacts or task roles differ"]


def require_same(reference, current):
    errors = problems(reference, current)
    if errors:
        raise ValueError("; ".join(errors))


def project(value, task_ids):
    if value is None:
        return None
    value = validate(value)
    keep = set(task_ids)
    return create(value["provider"], {tid: roles for tid, roles in _expand(value).items() if tid in keep})


def project_many(value, task_ids):
    """Build task projections in linear time after validating one run envelope."""
    if value is None:
        return {tid: None for tid in task_ids}
    value = validate(value)
    expanded = _expand(value)
    return {tid: create(value["provider"], {tid: expanded[tid]} if tid in expanded else {})
            for tid in task_ids}


def admit(value):
    """Check the exact recorded artifacts even when no process will be launched."""
    if value is None:
        return
    value = validate(value)
    try:
        if value["provider"] is not None:
            provider = value["provider"]
            dataset_runtime.admit_provider(provider["dataset"], provider)
        seen = set()
        for roles in _expand(value).values():
            for role, binding in roles.items():
                key = _canonical({key: item for key, item in binding.items() if key != "role"})
                if key not in seen:
                    dataset_runtime.admit_binding(binding, role)
                    seen.add(key)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError(f"dataset_runtime_identity artifact unavailable: {exc}") from exc


def _bindings(task_ids, setups, verifiers, envs=None):
    selected = {}
    for tid in task_ids:
        relay = ((setups or {}).get(tid) or {}).get("tool_relay") or {}
        verifier = (verifiers or {}).get(tid) or {}
        roles = {}
        native = verifier.get("native_benchmark")
        if isinstance(native, dict) and native.get("dataset") in {"deepswe", "jobbench", "swe_bench_pro"}:
            declared = verifier.get("python_runtime")
            if (not isinstance(declared, dict) or declared.get("dataset") != "native_harbor"
                    or declared.get("profile") != "execution" or declared.get("role") != "verifier"):
                raise ValueError("native_harbor execution verifier binding is required for built-in native tasks; "
                                 "run python -m data.scaffolds.native_harbor_runtime.prepare_python prepare, "
                                 "then reprepare the native task runtime manifest")
        if isinstance(native, dict) and native.get("dataset") == "jobbench":
            from harnessgrad.native_judge_identity import admit_identity
            admit_identity(verifier.get("native_judge_identity"))
        environment = (envs or {}).get(tid) or {}
        if "task_python" in environment:
            from harnessgrad.task_python import validate
            roles["actor"] = validate(environment, setup=(setups or {}).get(tid))
        if "python_runtime" in relay:
            binding = dataset_runtime.validate_binding(relay["python_runtime"], "service", relay.get("python"))
            if relay.get("python") != binding["python"]:
                raise ValueError("bound service requires its explicit Python interpreter")
            roles["service"] = binding
        if "python_runtime" in verifier:
            argv = verifier.get("argv")
            if not isinstance(argv, list) or len(argv) < 4 or argv[1:3] != ["-I", "-B"]:
                raise ValueError("bound verifier requires isolated Python startup")
            binding = dataset_runtime.validate_binding(verifier["python_runtime"], "verifier", argv[0])
            if verifier.get("runtime_paths") != binding["runtime_paths"]:
                raise ValueError("bound verifier runtime paths changed")
            roles["verifier"] = binding
        if roles:
            selected[tid] = roles
    return selected


def capture(dataset, task_ids, setups, verifiers, envs=None):
    """Describe the provider actually used and only the selected role bindings."""
    from data.scaffolds import registry
    runtime = registry.provider_runtime(dataset)
    if runtime is None and registry.provider(dataset) is not None:
        raise ValueError("provider identity must come from this run's loading session")
    provider = None if runtime is None else {"dataset": dataset, **{
        key: runtime[key] for key in _PROVIDER_FIELDS - {"dataset"}}}
    value = create(provider, _bindings(task_ids, setups, verifiers, envs))
    admit(value)
    return value


def for_evaluation(value, tasks, setups, verifiers, envs=None):
    """Validate supplied bindings, including callers that bypass driver.py."""
    task_ids = [task["task_id"] for task in tasks]
    bindings = _bindings(task_ids, setups, verifiers, envs)
    if value is None:
        selected = create(tasks=bindings)
    else:
        selected = project(value, task_ids)
        expected = create((selected or {}).get("provider"), bindings)
        if selected != expected:
            raise ValueError("dataset_runtime_identity does not match selected execution declarations; "
                             "new task actor bindings require a new run")
    admit(selected)
    return selected


def resolve_h0(dataset, *, side, task_ids, split="declared", sampling="all", env=None,
               managed_harness=False, managed_exec=False):
    """Preflight opted-in adapters under the driver's effective environment.

    A subprocess prevents adapter imports and configuration caches in this tool
    from freezing different paths than the eventual driver. It launches no actor,
    service, scorer or installer. Legacy tools keep their metadata-only discovery.
    """
    from data.scaffolds import registry
    if not managed_harness and not registry.managed_runtime(dataset):
        return None
    request = {"dataset": dataset, "side": side, "task_ids": task_ids,
               "split": split, "sampling": sampling}
    if managed_harness:
        request["managed_harness"] = True
    if managed_exec:
        request['managed_exec'] = True
    result = subprocess.run([sys.executable, "-B", "-m", __name__],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            input=_canonical(request), capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError("dataset runtime H0 preflight failed: " + result.stderr[-4000:])
    try:
        value = json.loads(result.stdout)
        return None if value is None else validate(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid dataset runtime H0 preflight response") from exc


def h0_selection(request):
    from data.scaffolds import registry
    from data.scaffolds.custom_split import resolve as resolve_split
    from eval.run import metrics
    dataset, side = request["dataset"], request["side"]
    tasks, _, split = registry.load_split(dataset)
    split, _, _ = resolve_split(dataset, tasks, split, request["split"], side=side)
    ids = list(split[side]) if split else [task["task_id"] for task in tasks]
    asked = request["task_ids"]
    if asked:
        if len(asked) != len(set(asked)) or set(asked) - set(ids):
            raise ValueError("H0 study tasks must be unique and on the requested side")
        ids = [tid for tid in ids if tid in set(asked)]
    policy = request["sampling"]
    if policy in {"diagnostic", "pilot_10pct"} or policy.startswith("random_"):
        ids = metrics.select_task_set({}, policy, within=ids)
    if not ids:
        raise ValueError("H0 has no selected tasks")
    _, setups, verifiers = registry.load_tasks(dataset, task_ids=ids)
    return ids, setups, verifiers, registry.load_envs(dataset, task_ids=ids)


def _h0_preflight(request):
    from data.scaffolds import registry
    with registry.provider_session():
        ids, setups, verifiers, envs = h0_selection(request)
        if request.get("managed_harness"):
            from harnessgrad import candidate_runtime
            candidate_runtime.validate_routes(envs, setups, True,
                **({'allow_exec': True} if request.get('managed_exec') else {}))
        return capture(request['dataset'], ids, setups, verifiers, envs)


if __name__ == "__main__":
    from contextlib import redirect_stdout
    try:
        request = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            identity = _h0_preflight(request)
        print(_canonical(identity))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
