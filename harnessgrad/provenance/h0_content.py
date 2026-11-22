"""Content-attested round-zero measurements, independent of donor deployment."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from eval.containers import external_runtime
from harnessgrad.datasets import runtime as dataset_artifacts, runtime_identity as dataset_runtime
from harnessgrad.execution import candidate_runtime
from harnessgrad.provenance import measurement_components, scoring_identity, verification_context
from harnessgrad.provenance.runtime_content import canonical, digest

FILENAME = "h0-content-attestation.json"
SCHEMA = "harnessgrad.h0-content-attestation.v1"
AGENT_SCHEMA = "harnessgrad.h0-agent-content.v1"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TREE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_STUDY_LOOP = "7a40f6ddd0cef5c7210c1be3dc6529cc19c03775"
_LOOP_FIELDS = {
    "max_steps": ("HG_AGENT_MAX_STEPS", 32),
    "max_output_tokens": ("HG_AGENT_MAX_OUTPUT_TOKENS", 2048),
    "submit_retries": ("HG_AGENT_SUBMIT_RETRIES", 2),
    "parse_retries": ("HG_AGENT_PARSE_RETRIES", 2),
    "finish_nudge_steps": ("HG_AGENT_FINISH_NUDGE_STEPS", 4),
    "repeat_nudge_limit": ("HG_AGENT_REPEAT_NUDGE_LIMIT", 2),
    "output_limit": ("HG_AGENT_OUTPUT_LIMIT", 4000),
    "context_chars": ("HG_AGENT_CONTEXT_CHARS", 60000),
}
_SCORING_FILES = {
    "aggregation_policy": "data/scaffolds/aggregation.py",
    "scorer_input": "data/scaffolds/scorer_input.py",
    "trial_aggregation": "eval/run/aggregation.py",
    "metrics": "eval/run/metrics.py",
    "task_verification": "eval/run/task_verification.py",
    "task_outcomes": "eval/run/outcomes.py",
    "relay_execution": "eval/run/tool_relay.py",
    "scorer_result_contract": "eval/verification/result_contract.py",
    "scorer_isolation": "eval/verification/isolation.py",
    "public_native_execution": "eval/verification/public_native_runtime.py",
    "verifier_execution": "eval/verification/verifier.py",
}


def _fields(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ValueError(f"invalid {label} fields")


def _hex(value, pattern=_HASH):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _plain(value, label):
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label} evidence is missing")
    canonical(value)
    return value


def _semantic_agent(value):
    _fields(value, "weights_sha256 backend decoding harness_config", "agent content")
    if not _hex(value["weights_sha256"]) or not isinstance(value["backend"], str) or not value["backend"]:
        raise ValueError("agent content requires verified model weights and backend")
    decoding = _plain(value["decoding"], "agent decoding")
    # These fields determine the protocol even for a deterministic HARNESS.
    temperature = decoding.get("temperature")
    max_tokens = decoding.get("max_tokens")
    if (type(temperature) not in (int, float) or not math.isfinite(temperature) or temperature < 0
            or type(max_tokens) is not int or max_tokens <= 0):
        raise ValueError("agent decoding requires temperature and max_tokens evidence")
    _plain(value["harness_config"], "harness measurement configuration")
    return value


def read_agent(path, configured_agent):
    """Read a reviewed serving receipt for the recipient's actual job and route."""
    receipt = json.loads(path.read_bytes())
    _fields(receipt, "schema model endpoint_sha256 content", "agent serving receipt")
    if (receipt["schema"] != AGENT_SCHEMA or receipt["model"] != configured_agent.model
            or receipt["endpoint_sha256"] != hashlib.sha256(configured_agent.base_url.encode()).hexdigest()):
        raise ValueError("agent serving receipt is for a different configured model or endpoint")
    semantic = _semantic_agent(receipt["content"])
    if semantic["backend"] != configured_agent.backend:
        raise ValueError("agent serving receipt backend differs from the configured backend")
    return semantic


def check_live_settings(source_sha, semantic):
    """Check effective overrides for the pinned study Loop without executing it."""
    if source_sha != _STUDY_LOOP:
        return
    configuration = semantic["harness_config"]
    for name, (variable, default) in _LOOP_FIELDS.items():
        if configuration.get(name) != int(os.environ.get(variable, default)):
            raise ValueError(f"effective {variable} differs from the reviewed Harness configuration")
    decoding = semantic["decoding"]
    if (decoding["temperature"] != 0 or decoding["max_tokens"] != configuration["max_output_tokens"]
            or decoding.get("wire") != "chat"):
        raise ValueError("decoding differs from the pinned Loop study request")
    for setting, variable, default, convert in (
        ("request_timeout_s", "HG_AGENT_TIMEOUT_S", 180, float),
        ("request_retries", "HG_AGENT_RETRIES", 3, int),
    ):
        if decoding.get(setting) != convert(os.environ.get(variable, default)):
            raise ValueError(f"effective {variable} differs from the reviewed request configuration")


def deployment(agent, *, dataset, candidate, external):
    """Record selected deployment identifiers and hashes without credentials."""
    dataset_ids = []
    if dataset is not None:
        records = list(dataset["artifacts"].values())
        if dataset["provider"]:
            records.append(dataset["provider"])
        dataset_ids = sorted({record["runtime_id"] for record in records})
    return {
        "agent_model": agent.model, "agent_backend": agent.backend,
        "endpoint_sha256": hashlib.sha256(agent.base_url.encode()).hexdigest(),
        "dataset_runtime_ids": dataset_ids,
        "candidate_binding_sha256": None if candidate is None else candidate["sha256"],
        "external_installations": {key: row.get("installation_sha256")
                                   for key, row in external.items()},
    }


def capture(work, source_sha, identities, options, *, agent, tasks, scorable, setups, verifiers, envs):
    if not _hex(source_sha, _TREE):
        raise ValueError("content H0 requires a full source tree hash")
    local_actor = candidate_runtime.admit(
        work, identities.initial_actor, setups=setups, envs=envs, sandbox=options.sandbox)
    binding_keys = {}
    runtime = dataset_runtime.content_key(identities.dataset, binding_keys=binding_keys)
    public_bindings = {}
    with dataset_artifacts.execution_declarations():
        for tid, verifier in verifiers.items():
            native = (verifier or {}).get("native_runtime")
            if isinstance(native, dict) and native.get("profile") == "public-native-local-v1":
                artifact = dataset_artifacts.resolve_execution(native["dataset"])
                public_bindings[tid] = {"verifier": dataset_artifacts.bind(artifact, "verifier")}
    public_runtime = dataset_runtime.create(tasks=public_bindings)
    context = verification_context.capture_content(
        tasks, scorable, setups, verifiers, envs, sandbox=options.sandbox, runtimes=binding_keys)
    external = external_runtime.identities(work, content=True)
    from data.scaffolds import registry
    from data.scaffolds.aggregation import aggregation_policy
    root = Path(__file__).resolve().parents[2]
    scoring_files = {**_SCORING_FILES,
                     "selected_adapter": registry.module_name(options.dataset).replace(".", "/") + ".py"}
    scoring_code = measurement_components.capture_implementation("scorer", root, scoring_files)
    context_record = {
        "base_tree": source_sha,
        "dataset": options.dataset,
        "dataset_runtime": {
            "declared": runtime,
            "public_scorers": dataset_runtime.content_key(public_runtime, binding_keys=binding_keys),
        },
        "verification": context,
        "scoring": {
            "profile": scoring_identity.content_key(identities.scoring),
            "aggregation": aggregation_policy(options.dataset, "run_reward"),
            "implementation": scoring_code["sha256"],
        },
        "measurement_components": identities.measurement,
        "candidate_runtime": candidate_runtime.content_key(local_actor),
        "external_runtime": external_runtime.content_key(external),
    }
    live = deployment(agent, dataset=identities.dataset, candidate=local_actor, external=external)
    from harnessgrad.experiment.records import _platform_record
    live["platform"] = _platform_record()
    exact_external = {kind: {k: v for k, v in value.items() if k != "content_sha256"}
                      for kind, value in external.items()}
    return context_record, live, exact_external, public_bindings


def require_public_runtime_receipts(bindings, result):
    """Check the implicit public scoring runtime against its actual verdict receipts."""
    for tid, roles in bindings.items():
        expected = roles["verifier"]
        trials = (result.get("trial_verdicts") or {}).get(tid)
        verdicts = trials or [(result.get("verdicts") or {}).get(tid)]
        if len(verdicts) != (result.get("trials") or 1):
            raise ValueError(f"H0 public scorer trial receipts are missing for {tid}")
        for verdict in verdicts:
            observed = (verdict or {}).get("native_runtime") or {}
            if (observed.get("verified") is not True
                    or (observed.get("identity") or {}).get("python_runtime") != expected):
                raise ValueError(f"H0 public scorer runtime receipt differs for {tid}")


def safe_id(task_id):
    if not isinstance(task_id, str) or not task_id or "\\" in task_id or "\x00" in task_id:
        return False
    path = PurePosixPath(task_id)
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts and str(path) == task_id


def _measurement(point, run_id, content, agent):
    from eval.run.metrics import mean
    if not isinstance(point, dict) or point.get("shared_from") is not None or point.get("reused_from") is not None:
        raise ValueError("specify the original H0 donor; nested reuse is not admissible")
    if (point.get("run_id") != run_id or type(point.get("round")) is not int or point["round"] != 0
            or point.get("evaluated_from") or point.get("side", "train") != "train"):
        raise ValueError("attestation does not identify an original training round zero")
    identity = point.get("identity") or {}
    if identity.get("harness_sha") != content["base_tree"]:
        raise ValueError("measured base tree differs from the attestation")
    if canonical(identity.get("harness_config")) != canonical(agent["harness_config"]):
        raise ValueError("reported Harness measurement configuration differs from the attestation")
    if point.get("unmeasured") or point.get("invalid") or point.get("n_invalid"):
        raise ValueError("donor H0 includes invalid or unmeasured outcomes")
    interval = point.get("score_ci95")
    if interval is not None and (not isinstance(interval, list) or len(interval) != 2
            or any(value is not None and (type(value) not in (int, float) or not math.isfinite(value))
                   for value in interval)):
        raise ValueError("invalid round-zero confidence interval")
    task_context = verification_context.validate(content["verification"])
    sampling = point.get("sampling") or {}
    selected = sampling.get("task_ids")
    per_task = point.get("per_task")
    if (not isinstance(selected, list) or not selected or not all(map(safe_id, selected))
            or len(set(selected)) != len(selected) or not isinstance(per_task, dict)
            or set(per_task) != set(selected) or set(task_context["tasks"]) != set(selected)
            or sampling.get("dataset") != content["dataset"]):
        raise ValueError("attestation, donor and selected task cohort differ")
    values = list(per_task.values())
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
            or type(point.get("n_scored")) is not int or point["n_scored"] != len(values)
            or type(point.get("n_passed")) is not int
            or point["n_passed"] != sum(value >= 1 for value in values)
            or type(point.get("score")) not in (int, float)
            or not math.isclose(mean(values, dataset=content["dataset"]), point["score"],
                                rel_tol=0, abs_tol=1e-9)):
        raise ValueError("donor score or counts cannot be recomputed from complete per-task evidence")
    return selected


def point_json(point):
    # Legacy curve writers use NaN for the undefined confidence interval at n=1.
    interval = point.get("score_ci95")
    if not isinstance(interval, list):
        return point
    return {**point, "score_ci95": [None if isinstance(x, float) and not math.isfinite(x) else x
                                     for x in interval]}


def _trace_files(directory, selected):
    root = directory / "round0_traces"
    if not root.exists() and not root.is_symlink():
        return {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError("round-zero trace directory is unsafe")
    files = {}
    for task in selected:
        path = Path(task)
        parent = root / path.parent
        if not parent.is_dir():
            continue
        for item in parent.iterdir():
            name = item.name
            if name != path.name + ".jsonl" and not re.fullmatch(
                    re.escape(path.name) + r"\.trial[1-9][0-9]*\.jsonl", name):
                continue
            if item.is_symlink() or not item.is_file() or item.resolve(strict=True) != item.absolute():
                raise ValueError("round-zero trace is outside its original evidence directory")
            files[item.relative_to(root).as_posix()] = item.read_bytes()
    return files


def _trace_digests(files):
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}


def build(directory, run_id, point, content, agent, deployment_record):
    """Make an envelope from contemporaneous evidence. Printing its hash conveys no trust."""
    directory = Path(directory).resolve(strict=True)
    point = point_json(point)
    selected = _measurement(point, run_id, content, _semantic_agent(agent))
    if point.get("measured_by_platform") is not True:
        raise ValueError("fresh capture requires an original platform measurement")
    return {
        "schema": SCHEMA, "run_id": run_id, "content": content, "agent": agent,
        "deployment": deployment_record,
        "measurement_evidence": {"origin": "platform-measurement", "source": run_id,
                                 "point": point, "traces": _trace_digests(_trace_files(directory, selected))},
    }


def publish(directory, envelope):
    """Publish a complete immutable attestation without replacing earlier evidence."""
    raw = canonical(envelope) + b"\n"
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".h0-attestation-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.link(temporary, directory / FILENAME)
        finally:
            temporary.unlink(missing_ok=True)
    return hashlib.sha256(raw).hexdigest()


def load(directory, run_id, pin):
    """Require an operator pin obtained from a trusted release/measurement authority."""
    if not _hex(pin):
        raise ValueError("a trusted H0 attestation SHA-256 pin is required")
    path = directory / FILENAME
    if not path.is_file() or path.is_symlink():
        raise ValueError("missing H0 content attestation; obtain original evidence or measure H0 again")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError("H0 content attestation differs from the trusted SHA-256 pin")
    value = json.loads(raw)
    _fields(value, "schema run_id content agent deployment measurement_evidence", "H0 attestation")
    if value["schema"] != SCHEMA or value["run_id"] != run_id:
        raise ValueError("H0 attestation schema or donor run ID differs")
    return value


def admit(value, actual, agent, actual_deployment, directory):
    _fields(actual, "base_tree dataset dataset_runtime verification scoring measurement_components "
                    "candidate_runtime external_runtime", "recipient H0 content")
    recorded = value["content"]
    _fields(recorded, "base_tree dataset dataset_runtime verification scoring measurement_components "
                      "candidate_runtime external_runtime", "donor H0 content")
    for key in actual:
        if canonical(actual[key]) != canonical(recorded[key]):
            raise ValueError(f"H0 {key} content differs or is unproven")
    measurement_components.require_same(recorded["measurement_components"], actual["measurement_components"])
    if canonical(_semantic_agent(value["agent"])) != canonical(agent):
        raise ValueError("H0 model weights, backend, decoding or Harness measurement configuration differs")
    evidence = value["measurement_evidence"]
    _fields(evidence, "origin source point traces", "H0 measurement evidence")
    if (evidence["origin"] not in ("platform-measurement", "published-measurement")
            or not isinstance(evidence["source"], str) or not evidence["source"]):
        raise ValueError("H0 measurement origin is unknown")
    directory = directory.resolve(strict=True)
    point = evidence["point"]
    selected = _measurement(point, value["run_id"], actual, agent)
    if evidence["origin"] == "platform-measurement" and point.get("measured_by_platform") is not True:
        raise ValueError("H0 measurement origin conflicts with the recorded platform flag")
    curve = directory / "curve.jsonl"
    if curve.is_file():
        if curve.is_symlink():
            raise ValueError("donor curve is unsafe")
        with curve.open(encoding="utf-8") as stream:
            row = next((line for line in stream if line.strip()), None)
        if row is None or canonical(point_json(json.loads(row))) != canonical(point):
            raise ValueError("donor curve point contradicts its attestation")
    files = _trace_files(directory, selected)
    if _trace_digests(files) != evidence["traces"]:
        raise ValueError("round-zero trace evidence is missing or has changed")
    if not isinstance(value["deployment"], dict):
        raise ValueError("invalid donor deployment record")
    return point, files, {
        key: {"donor": value["deployment"].get(key), "recipient": actual_deployment.get(key)}
        for key in sorted(actual_deployment)
        if value["deployment"].get(key) != actual_deployment.get(key)
    }
