"""Native deployment manifests stay private while study grading inputs remain readable."""
import copy
import hashlib
import json

import pytest

from harnessgrad import channel


def declaration():
    prefix = "/private/RUNTIME_CANARY/venv"
    binding = {
        "schema": "harnessgrad.python-runtime-binding.v1", "dataset": "native_harbor",
        "profile": "execution", "role": "verifier", "runtime_id": "a" * 64,
        "path": prefix, "python": prefix + "/bin/python",
        "manifest_path": "/private/RUNTIME_CANARY/artifact.json", "runtime_paths": [prefix],
    }
    case = {
        "schema": "harnessgrad.native-harbor-case.v1", "dataset": "deepswe", "task_id": "study",
        "directory": "/private/TASK_DIRECTORY_CANARY", "source_sha256": "SOURCE_HASH_CANARY",
        "declaration": {"verifier": {"command": "run-check", "reference": "STUDY_REFERENCE"}},
        "collect": [{"command": "collect-public-artifact"}], "artifact_paths": ["/app/output"],
        "actor_timeout_s": 30, "verify_timeout_s": 20,
        "judge_identity": {"endpoint_sha256": "JUDGE_IDENTITY_CANARY"},
        "runtime": {"harbor_runtime": binding, "harbor_python": binding["python"],
            "pro_replay_source": "/private/PRO_TOOL_CANARY", "future": "FUTURE_PRIVATE_CANARY",
            "actor": {"kind": "exec", "image": "sha256:" + "b" * 64, "network": "none",
                      "workdir": "/app", "future_private": "ACTOR_EXTRA_CANARY"}},
    }
    text = json.dumps(case, sort_keys=True)
    digest = hashlib.sha256(text.encode()).hexdigest()
    spec = {"kind": "command", "python_runtime": binding,
        "argv": [binding["python"], "-I", "-B", "/private/WORKER_CANARY/harbor_entry.py"],
        "native_benchmark": {"dataset": "deepswe", "case_path": "native-case.json", "case_sha256": digest},
        "scorer_input": {"task_id": "study", "path": "native-case.json", "sha256": digest},
        "inputs": [{"path": "native-case.json", "content": text}]}
    return case, spec


@pytest.mark.parametrize("source", ["inline", "file", "directory"])
def test_actual_task_page_projects_only_native_deployment_input(tmp_path, source):
    case, spec = declaration()
    if source != "inline":
        folder = tmp_path / "checks"; folder.mkdir()
        path = folder / "native-case.json"; path.write_text(spec["inputs"][0]["content"])
        spec["inputs"] = [{"path": "native-case.json", "from": str(path)}] if source == "file" else [
            {"path": ".", "from": str(folder)}]
    spec["inputs"].append({"path": "gold.json", "content": '{"answer":"STUDY_GOLD"}'})
    before = copy.deepcopy(spec)
    channel._stage_task_context(tmp_path, [{"task_id": "study", "goal": "Public instruction"}], {},
                                {"study": spec}, {"per_task": {"study": 1}}, ["study"])
    page, = (tmp_path / "_harnessgrad/tasks").glob("*.json")
    raw = page.read_text(); result = json.loads(raw)
    assert "CANARY" not in raw
    projected = json.loads(result["verifier"]["checks"]["native-case.json"])
    assert projected["declaration"] == case["declaration"]
    assert projected["collect"] == case["collect"] and projected["artifact_paths"] == case["artifact_paths"]
    assert projected["runtime"]["actor"]["image"] == case["runtime"]["actor"]["image"]
    assert projected["runtime"]["harbor_runtime"] == channel._method_runtimes(spec["python_runtime"])
    assert result["verifier"]["checks"]["gold.json"] == '{"answer":"STUDY_GOLD"}'
    assert spec == before


@pytest.mark.parametrize("bad", ["digest", "schema", "task", "binding", "json", "oversize", "missing", "duplicate", "descriptor"])
def test_bad_native_case_is_unavailable_without_raw_echo(bad):
    _, spec = declaration()
    if bad == "digest": spec["scorer_input"]["sha256"] = "f" * 64
    elif bad == "binding": spec["python_runtime"] = {**spec["python_runtime"], "runtime_id": "f" * 64}
    elif bad == "json": spec["inputs"][0]["content"] = "{PRIVATE_CANARY"
    elif bad == "oversize": spec["inputs"][0]["content"] = "PRIVATE_CANARY" * channel.CONTEXT_FILE_LIMIT
    elif bad == "missing": spec["inputs"] = []
    elif bad == "duplicate": spec["inputs"] *= 2
    elif bad == "descriptor": spec["native_benchmark"].pop("case_path")
    else:
        case = json.loads(spec["inputs"][0]["content"])
        case["schema" if bad == "schema" else "task_id"] = "PRIVATE_CANARY"
        text = json.dumps(case); digest = hashlib.sha256(text.encode()).hexdigest()
        spec["inputs"][0]["content"] = text
        spec["native_benchmark"]["case_sha256"] = spec["scorer_input"]["sha256"] = digest
    checks = channel._resolve_checks(spec, [channel.CONTEXT_TOTAL_LIMIT])
    assert checks == {"native-case.json": channel._NATIVE_CASE_UNAVAILABLE}


def test_ordinary_study_json_is_not_reinterpreted_or_redacted():
    text = '{"schema":"harnessgrad.python-runtime-binding.v1","answer":"STUDY_GOLD"}'
    spec = {"kind": "command", "inputs": [{"path": "gold.json", "content": text}]}
    assert channel._resolve_checks(spec, [channel.CONTEXT_TOTAL_LIMIT]) == {"gold.json": text}


def test_file_hash_uses_original_utf8_crlf_bytes(tmp_path):
    case, spec = declaration()
    case["declaration"]["verifier"]["reference"] = "作者参考"
    text = json.dumps(case, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    spec["native_benchmark"]["case_sha256"] = spec["scorer_input"]["sha256"] = digest
    path = tmp_path / "case.json"; path.write_bytes(text.encode("utf-8"))
    spec["inputs"] = [{"path": "native-case.json", "from": str(path)}]
    before = path.read_bytes()
    projected = channel._resolve_checks(spec, [channel.CONTEXT_TOTAL_LIMIT])["native-case.json"]
    assert json.loads(projected)["declaration"] == case["declaration"]
    assert "CANARY" not in projected and path.read_bytes() == before
