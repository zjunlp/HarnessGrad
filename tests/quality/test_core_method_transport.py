"""Legacy JSON wire contract remains reusable without supplying an executor."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harnessgrad.execution import method_runner as methods
from harnessgrad.core.method_transport import decode_response, encode_request


def previous_decode(process):
    """The pre-extraction interpretation, used only as a differential oracle."""
    if process.returncode != 0:
        return {"_error": f"exit {process.returncode}", "_stderr": process.stderr[-1500:]}
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        return {"_error": f"non-JSON stdout: {exc}",
                "_stdout": process.stdout[:500], "_stderr": process.stderr[-500:]}


@pytest.mark.parametrize("code,stdout,stderr", [
    (0, '{"changed":true,"steps":2}', ''),
    (0, '[1, null, "中文"]', ''),
    (0, 'false', ''),
    (0, 'null', ''),
    (0, '', 'details'),
    (0, 'not-json' * 100, 'e' * 1200),
    (5, '{"ignored":true}', 'e' * 2500),
])
def test_response_matches_existing_wire_behavior(code, stdout, stderr):
    process = subprocess.CompletedProcess(['fixture'], code, stdout, stderr)
    assert decode_response(process) == previous_decode(process)


def test_request_serialization_matches_legacy_and_does_not_mutate():
    request = {"task_ids": ["甲"], "score": None, "nested": {"changed": False}}
    expected = json.dumps(request)
    assert encode_request(request) == expected
    assert request == {"task_ids": ["甲"], "score": None, "nested": {"changed": False}}
    with pytest.raises(TypeError):
        encode_request({"path": Path('/not-json')})


def test_existing_method_caller_uses_same_exchange_without_a_model(tmp_path, monkeypatch):
    from eval.containers import bun_runtime

    work = tmp_path / 'work'
    work.mkdir()
    script = tmp_path / 'method.py'
    script.write_text('import json,sys\nrequest=json.load(sys.stdin)\n'
                      'print(json.dumps({"received":request["task_ids"],"changed":False}))\n')
    monkeypatch.setattr(bun_runtime, 'prepare', lambda path: None)
    request = {"base_harness": str(work), "task_ids": ["one", "二"]}
    result = methods._call_entrypoint(work, [sys.executable, str(script)], request,
                                      timeout_s=10, sandboxed=False, cwd=work)
    assert result == {"received": ["one", "二"], "changed": False}
    assert request == {"base_harness": str(work), "task_ids": ["one", "二"]}
