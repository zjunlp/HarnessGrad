"""Private dataset provider protocol, launched only with a prepared interpreter.

This process owns dataset loading dependencies. Its output is trusted platform
control data: answer maps, relay configuration and verifier inputs are private,
even though the public task list travels in the same response envelope.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib
import io
import json
import os
from pathlib import Path
import sys

from data.scaffolds import registry
from data.scaffolds.registry import module_name
from harnessgrad import dataset_runtime
from harnessgrad.paths import DataRootNotConfigured, env_name


SCHEMA = "harnessgrad.dataset-provider.v1"
_METADATA = ("KIND", "DEFAULT_ENV", "SPLIT", "SPLIT_SOURCE", "SPLIT_GROUPS", "OFFICIAL_EVAL",
             "ORACLE_SOURCE", "ORACLE_VALIDATED_BY", "ORACLE_COVERAGE", "REFUSED")


def _request(raw: object) -> dict:
    required = {"schema", "dataset", "operation", "descriptor_sha256", "runtime_id",
                "runtime_path", "configuration", "task_ids"}
    if not isinstance(raw, dict) or set(raw) - {"execution_runtime"} != required or raw.get("schema") != SCHEMA:
        raise ValueError("invalid dataset provider request")
    name = raw["dataset"]
    declaration = dataset_runtime.descriptor(name)
    if declaration is None or declaration["provider"] not in {"qa", "adapter"}:
        raise ValueError("dataset does not declare a supported provider")
    if raw["operation"] not in {"catalog", "resolve", "check", "inventory", "control"}:
        raise ValueError("unsupported provider operation")
    if (raw["descriptor_sha256"] != declaration["descriptor_sha256"]
            or not isinstance(raw["runtime_id"], str) or not raw["runtime_id"]):
        raise ValueError("dataset provider descriptor identity mismatch")
    if Path(raw["runtime_path"]).resolve() != Path(sys.prefix).resolve():
        raise ValueError("provider interpreter does not belong to the selected runtime")
    configuration = raw["configuration"]
    if (not isinstance(configuration, dict) or set(configuration) != {env_name(name)}
            or any(not isinstance(value, str) or not Path(value).is_absolute()
                   for value in configuration.values())):
        raise ValueError("provider requires one explicit dataset directory")
    if any(os.environ.get(key) != value for key, value in configuration.items()):
        raise ValueError("provider environment differs from its configuration snapshot")
    selected = raw["task_ids"]
    if selected is not None and (not isinstance(selected, list)
            or any(not isinstance(tid, str) or not tid for tid in selected)
            or len(selected) != len(set(selected))):
        raise ValueError("invalid selected task ids")
    if raw['operation'] == 'control' and selected is not None and len(selected) != 1:
        raise ValueError('control requires exactly one selected task or the default task')
    if raw['operation'] == 'inventory' and selected is not None:
        raise ValueError('inventory does not accept a task selection')
    if "execution_runtime" in raw:
        if raw["operation"] not in {"resolve", "inventory", "control"}:
            raise ValueError("operation does not accept an execution artifact")
        dataset_runtime.validate_binding(raw["execution_runtime"], "service")
    return raw


def _public_task(task, name):
    """Keep adapter control/configuration fields out of actor task JSON."""
    declaration = dataset_runtime.descriptor(name)
    if declaration is not None and declaration["provider"] == "adapter":
        private = {"oracle_files", "walkthrough", "gold", "credential", "credentials",
                   "configuration", "config", "api_key", "apikey", "password",
                   "secret", "secrets", "token", "access_token", "authorization"}
        for key in task:
            normalized = key.lower().replace("-", "_") if isinstance(key, str) else ""
            if normalized in private or normalized.endswith(("_api_key", "_apikey", "_password", "_secret", "_token", "_credentials")):
                raise ValueError("adapter public task contains private control or configuration fields")
    registry._check_no_secrets(task, name)


def _metadata(module):
    return {key: getattr(module, key) for key in _METADATA if hasattr(module, key)}


def _inventory(name):
    module = importlib.import_module(module_name(name))
    metadata = _metadata(module)
    if metadata.get('REFUSED'):
        return {'metadata': metadata, 'tasks': 0, 'sample': None,
                'env_kinds': [], 'verifier_kinds': [], 'setup': {}}
    tasks, _, split = registry.load_split(name)
    for task in tasks:
        _public_task(task, name)
    if split is not None:
        metadata['SPLIT'] = split
    # Local registry routing reads only ENV here, not every task's SETUP/VERIFY.
    envs = registry.load_envs(name)
    sample = tasks[0] if tasks else None
    setups, verifiers = {}, {}
    if sample is not None:
        _, setups, verifiers = registry.load_tasks(name, task_ids=[sample['task_id']])
    declared = getattr(getattr(module, '_SPEC', None), 'VERIFIER_KIND', None)
    kinds = ([declared] if declared else sorted({v['kind'] for v in getattr(module, 'VERIFY', {}).values()}))
    return {'metadata': metadata, 'tasks': len(tasks), 'sample': sample,
            'env_kinds': sorted({env.get('kind', 'files') for env in (envs or {}).values()}),
            'verifier_kinds': kinds, 'setup': setups.get(sample['task_id'], {}) if sample else {}}


def _control(name, selected):
    from data.scaffolds.qa import control
    module = importlib.import_module(module_name(name))
    refused = getattr(module, 'REFUSED', None)
    if refused:
        return {'refused': refused}
    try:
        record = control._record(module, None if selected is None else selected[0])
    except SystemExit as exc:
        # The in-process CLI uses SystemExit for an unknown task. Preserve its
        # diagnostic in the provider envelope instead of terminating the worker.
        raise ValueError(str(exc)) from exc
    task, setup, verifier, env = control.task_specs(module, record)
    _public_task(task, name)
    return {'record': {'task_id': record['task_id'], 'gold': record['gold']},
            'task': task, 'setup': setup, 'verifier': verifier, 'env': env,
            'submission': control._oracle_submission(module, record),
            'prediction': control._oracle_prediction(module, record),
            'noop_expected': control._noop_expected(module, record)}


def _check(name, selected):
    try:
        tasks, _ = registry.load(name)
        chosen = registry._selected_tasks(name, tasks, selected)
    except Exception as exc:
        causes, cause = [], exc
        while cause is not None and all(cause is not previous for previous in causes):
            causes.append(cause)
            cause = cause.__cause__
        if any(isinstance(item, DataRootNotConfigured) for item in causes):
            status = "config_missing"
        elif any(isinstance(item, FileNotFoundError) for item in causes):
            status = "data_missing"
        else:
            # Only explicit missing prerequisites are unprovisioned. Adapter
            # defects must fail lint, including with --allow-unprovisioned.
            status = "invalid"
        return {"present": False, "status": status, "scope": "dataset-provider-load",
                "detail": f"{type(exc).__name__}: {exc}", "tasks": 0}
    return {"present": True, "status": "ready", "scope": "dataset-provider-load",
            "detail": "selected data is loadable in its declared runtime",
            "tasks": len(chosen)}


def dispatch(request: dict) -> dict:
    """Read trusted adapters locally while registry provider routing is disabled."""
    name, operation = request["dataset"], request["operation"]
    binding = request.get("execution_runtime")
    execution_name = registry.execution_runtime_name(name)
    if operation in {"resolve", "inventory", "control"} and execution_name is not None:
        if binding is None or binding.get("dataset") != execution_name:
            raise ValueError("provider requires the selected execution artifact")
    bindings = {} if binding is None else {binding["dataset"]: binding}
    with registry._local_provider(), dataset_runtime.execution_snapshot(bindings):
        if operation == 'inventory':
            return _inventory(name)
        if operation == 'control':
            declaration = dataset_runtime.descriptor(name)
            if declaration is not None and declaration["provider"] == "adapter":
                module = importlib.import_module(module_name(name))
                selected = request.get("task_ids")
                value = module.command_control(task_id=None if selected is None else selected[0])
                if not value.get("refused"):
                    _public_task(value["task"], name)
                return value
            return _control(name, request['task_ids'])
        if operation == "check":
            value = _check(name, request["task_ids"])
            declaration = dataset_runtime.descriptor(name)
            if declaration is not None and declaration["provider"] == "adapter" and not value["present"]:
                value["detail"] = "adapter data is unavailable or invalid"
            return value
        if operation == "resolve":
            tasks, setups, verifiers = registry.load_tasks(name, task_ids=request["task_ids"])
            envs = registry.load_envs(name, task_ids=request["task_ids"])
            for task in tasks:
                _public_task(task, name)
            return {"tasks": tasks, "setups": setups, "verifiers": verifiers, "envs": envs}
        tasks, scorable, split = registry.load_split(name)
        # catalog must not obtain SETUP/VERIFY/ENV: some adapters expose those
        # through properties that can prepare runtime-specific declarations.
        module = importlib.import_module(module_name(name))
        metadata = _metadata(module)
        if split is not None:
            metadata["SPLIT"] = split
        downloads = getattr(module, "downloads", None)
        if callable(downloads):
            metadata["DOWNLOADS"] = downloads()
        for task in tasks:
            _public_task(task, name)
        return {"tasks": tasks, "scorable": scorable, "metadata": metadata}


def main() -> int:
    raw = json.load(sys.stdin)
    binding = ({key: raw.get(key) for key in ("schema", "dataset", "operation",
                                               "descriptor_sha256", "runtime_id")}
               if isinstance(raw, dict) else {"schema": SCHEMA})
    if isinstance(raw, dict) and "execution_runtime" in raw:
        binding["execution_runtime"] = raw["execution_runtime"]
    response = {**binding, "interpreter": {"python": sys.executable, "prefix": sys.prefix}}
    try:
        if not sys.flags.isolated or not sys.dont_write_bytecode:
            raise ValueError("provider requires isolated, bytecode-free Python startup")
        request = _request(raw)
        with redirect_stdout(io.StringIO()):
            value = dispatch(request)
        # Validate serialization before emitting any bytes of a successful reply.
        json.dumps(value, allow_nan=False)
        response["result"] = value
        status = 0
    except Exception as exc:
        try:
            declaration = dataset_runtime.descriptor(raw.get("dataset")) if isinstance(raw, dict) else None
        except (ValueError, OSError, TypeError):
            declaration = None
        detail = "adapter operation failed" if declaration and declaration["provider"] == "adapter" else str(exc)
        response["error"] = {"type": type(exc).__name__, "detail": detail}
        status = 1
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
