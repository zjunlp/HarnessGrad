"""Trusted dataset loading through an explicitly prepared Python runtime.

The subprocess protocol is private platform control traffic. ``tasks`` are the
public actor inputs; ``scorable`` and resolved verifier/service declarations
remain private and must never be copied into an actor's task JSON.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from eval.containers.process import run
from harnessgrad import dataset_runtime
from harnessgrad.env_file import REVIEWED_ENVIRONMENT_ONLY
from harnessgrad.paths import DataRootNotConfigured, dataset_root, env_name, main_checkout


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "harnessgrad.dataset-provider.v1"
_BOOTSTRAP = ("import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
              "runpy.run_module('data.scaffolds.provider_worker',run_name='__main__')")


class DatasetProviderError(ValueError):
    """An explicitly selected provider could not complete its operation."""

    def __init__(self, dataset: str, operation: str, detail: str, *, error_type: str):
        self.dataset, self.operation, self.error_type = dataset, operation, error_type
        super().__init__(f"{dataset}: provider {operation}: {detail}")


def _selection(task_ids):
    if task_ids is None:
        return None
    if isinstance(task_ids, (str, bytes)):
        raise ValueError("selected task ids must be a sequence, not a string")
    values = list(task_ids)
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("selected task ids must be nonempty strings")
    if len(values) != len(set(values)):
        raise ValueError("selected task ids contain duplicates")
    return values


def _configuration(name: str, snapshot: dict | None) -> dict[str, str]:
    """Freeze only the QA provider's storage settings, never model credentials."""
    key = env_name(name)
    if snapshot is not None:
        if (not isinstance(snapshot, dict) or set(snapshot) - {key, "HG_DATA_ROOT"}
                or any(not isinstance(value, str) or not value for value in snapshot.values())):
            raise ValueError("QA provider configuration accepts only explicit dataset storage paths")
        if key in snapshot:
            root = Path(snapshot[key]).expanduser()
        elif "HG_DATA_ROOT" in snapshot:
            root = Path(snapshot["HG_DATA_ROOT"]).expanduser() / name
        else:
            root = Path(dataset_root(name))
        if not root.is_absolute():
            raise ValueError("provider configuration paths must be absolute")
    else:
        root = Path(dataset_root(name))
    root = root.resolve()
    if any(root.is_relative_to(platform) for platform in {ROOT.resolve(), main_checkout().resolve()}):
        raise ValueError("dataset storage must be outside the platform checkout")
    return {key: str(root)}


class DatasetProvider:
    """One statically discovered QA provider; constructing it performs no build."""

    def __init__(self, name: str, *, timeout_s: float | None = None, freeze_runtime=False):
        declaration = dataset_runtime.descriptor(name)
        if declaration is None:
            raise ValueError(f"{name}: no dataset runtime descriptor")
        if declaration["provider"] != "qa":
            raise ValueError(f"{name}: unsupported dataset provider")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("provider timeout must be positive")
        self.name, self.declaration, self.timeout_s = name, declaration, timeout_s
        self.freeze_runtime = freeze_runtime
        self.runtime = None

    def _request(self, operation: str, *, task_ids=None, config_snapshot=None) -> dict:
        configuration = _configuration(self.name, config_snapshot)
        runtime = (dataset_runtime.admit_provider(self.name, self.runtime)
                   if self.freeze_runtime and self.runtime is not None
                   else dataset_runtime.resolve(self.name))
        descriptor_sha = self.declaration["descriptor_sha256"]
        if runtime["descriptor_sha256"] != descriptor_sha:
            raise DatasetProviderError(self.name, operation, "descriptor changed after discovery",
                                       error_type="IdentityMismatch")
        if self.freeze_runtime:
            self.runtime = dict(runtime)
        request = {"schema": SCHEMA, "dataset": self.name, "operation": operation,
                   "descriptor_sha256": descriptor_sha, "runtime_id": runtime["runtime_id"],
                   "runtime_path": runtime["path"], "configuration": configuration,
                   "task_ids": _selection(task_ids)}
        with tempfile.TemporaryDirectory(prefix="hg-dataset-provider-") as directory:
            private = Path(directory)
            (private / "home").mkdir()
            environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                           "HOME": str(private / "home"), "TMPDIR": str(private),
                           "XDG_CACHE_HOME": str(private / "cache"),
                           "PYTHONDONTWRITEBYTECODE": "1", REVIEWED_ENVIRONMENT_ONLY: "1",
                           **configuration}
            try:
                result = run([runtime["python"], "-I", "-B", "-c", _BOOTSTRAP, str(ROOT)],
                             timeout_s=self.timeout_s, cwd=private, env=environment,
                             input_text=json.dumps(request, allow_nan=False))
            except subprocess.TimeoutExpired as exc:
                raise DatasetProviderError(self.name, operation, "worker exceeded the declared timeout",
                                           error_type="TimeoutExpired") from exc
            except OSError as exc:
                raise DatasetProviderError(self.name, operation, str(exc),
                                           error_type=type(exc).__name__) from exc
        try:
            response = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise DatasetProviderError(self.name, operation,
                                       f"worker returned no valid response (exit {result.returncode})",
                                       error_type="ProtocolError") from exc
        expected = {key: request[key] for key in ("schema", "dataset", "operation",
                                                  "descriptor_sha256", "runtime_id")}
        if not isinstance(response, dict) or any(response.get(key) != value for key, value in expected.items()):
            raise DatasetProviderError(self.name, operation, "worker response identity mismatch",
                                       error_type="IdentityMismatch")
        observed = response.get("interpreter")
        if (not isinstance(observed, dict) or observed.get("python") != runtime["python"]
                or Path(observed.get("prefix", "")).resolve() != Path(runtime["path"]).resolve()):
            raise DatasetProviderError(self.name, operation, "worker used a different Python environment",
                                       error_type="IdentityMismatch")
        if response.get("error") is not None:
            error = response["error"]
            if not isinstance(error, dict) or not isinstance(error.get("type"), str):
                raise DatasetProviderError(self.name, operation, "malformed worker error",
                                           error_type="ProtocolError")
            raise DatasetProviderError(self.name, operation, str(error.get("detail", "worker failed")),
                                       error_type=error["type"])
        value = response.get("result")
        if result.returncode or not isinstance(value, dict):
            raise DatasetProviderError(self.name, operation, "worker result is absent or unsuccessful",
                                       error_type="ProtocolError")
        return {**value, "runtime": runtime}

    def catalog(self, *, config_snapshot: dict | None = None) -> dict:
        """Return public tasks separately from the trusted fallback answer map."""
        result = self._request("catalog", config_snapshot=config_snapshot)
        if (not isinstance(result.get("tasks"), list) or not isinstance(result.get("scorable"), dict)
                or not isinstance(result.get("metadata"), dict)):
            raise DatasetProviderError(self.name, "catalog", "malformed catalog", error_type="ProtocolError")
        return result

    def resolve(self, task_ids=None, config_snapshot: dict | None = None) -> dict:
        """Resolve only selected execution declarations; ``None`` means all."""
        result = self._request("resolve", task_ids=task_ids, config_snapshot=config_snapshot)
        if (not isinstance(result.get("tasks"), list)
                or any(not isinstance(result.get(key), dict) for key in ("setups", "verifiers", "envs"))):
            raise DatasetProviderError(self.name, "resolve", "malformed declarations", error_type="ProtocolError")
        return result

    def inventory(self, *, config_snapshot: dict | None = None) -> dict:
        """Read host counts, metadata and one public setup without all verifiers."""
        value = self._request('inventory', config_snapshot=config_snapshot)
        if (not isinstance(value.get('metadata'), dict) or type(value.get('tasks')) is not int
                or value['tasks'] < 0 or not isinstance(value.get('setup'), dict)
                or (value.get('sample') is not None and not isinstance(value['sample'], dict))
                or bool(value['tasks']) != (value.get('sample') is not None)
                or any(not isinstance(value.get(key), list)
                       or any(not isinstance(item, str) for item in value[key])
                       for key in ('env_kinds', 'verifier_kinds'))):
            raise DatasetProviderError(self.name, 'inventory', 'malformed inventory', error_type='ProtocolError')
        return value

    def control(self, task_id=None, *, config_snapshot: dict | None = None) -> dict:
        """Prepare one trusted oracle/noop snapshot; reference data stays private."""
        value = self._request('control', task_ids=None if task_id is None else [task_id],
                              config_snapshot=config_snapshot)
        if value.get('refused'):
            if isinstance(value['refused'], str):
                return value
        elif (all(isinstance(value.get(key), dict) for key in ('record', 'task', 'setup', 'verifier', 'env'))
                and isinstance(value['record'].get('gold'), dict)
                and isinstance(value['record'].get('task_id'), str)
                and bool(value['record']['task_id'])
                and value['record']['task_id'] == value['task'].get('task_id')
                and (task_id is None or task_id == value['record']['task_id'])
                and all(key in value and (value[key] is None or isinstance(value[key], dict))
                        for key in ('submission', 'prediction'))
                and type(value.get('noop_expected')) in (int, float)
                and math.isfinite(value['noop_expected'])):
            return value
        raise DatasetProviderError(self.name, 'control', 'malformed control snapshot', error_type='ProtocolError')

    def check(self, task_ids=None, config_snapshot: dict | None = None) -> dict:
        """Inspect the selected runtime and data; never prepare missing packages."""
        runtime_status = dataset_runtime.check(self.name)
        if not runtime_status["present"]:
            return {**runtime_status, "scope": "dataset-provider-runtime", "tasks": 0}
        try:
            result = self._request("check", task_ids=task_ids, config_snapshot=config_snapshot)
        except DataRootNotConfigured as exc:
            return {"present": False, "status": "config_missing", "scope": "dataset-provider-load",
                    "detail": str(exc), "tasks": 0}
        except dataset_runtime.DatasetRuntimeUnavailable as exc:
            return {"present": False, "status": exc.status, "scope": "dataset-provider-runtime",
                    "detail": str(exc), "tasks": 0}
        except DatasetProviderError as exc:
            # A failed exchange is not evidence of an unprepared dataset. Keep
            # infrastructure failures distinct from rejected identity/protocol.
            unavailable = exc.error_type == "TimeoutExpired" or isinstance(exc.__cause__, OSError)
            return {"present": False, "status": "unavailable" if unavailable else "invalid",
                    "scope": "dataset-provider-load", "error_type": exc.error_type,
                    "detail": str(exc), "tasks": 0}
        except OSError as exc:
            return {"present": False, "status": "unavailable", "scope": "dataset-provider-load",
                    "error_type": type(exc).__name__, "detail": str(exc), "tasks": 0}
        return result
