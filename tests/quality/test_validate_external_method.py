"""The external-method validator rejects submissions the unified engine cannot use."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import validate_method  # noqa: E402


def _method(tmp_path: Path, document: str | None, reply: str = "{}") -> Path:
    method = tmp_path / "external method"
    method.mkdir()
    source = ("import json, sys\nfrom pathlib import Path\n"
              "request = json.load(sys.stdin)\n")
    if document is not None:
        source += f"Path(request['trajectory_out']).write_text({document})\n"
    source += f"print({reply!r})\n"
    (method / "run.py").write_text(source, encoding="utf-8")
    return method


def _invoke(tmp_path: Path, document: str | None, reply: str = "{}"):
    harness = tmp_path / "base"
    harness.mkdir()
    (harness / "harness.json").write_text("{}")
    method = _method(tmp_path, document, reply)
    report = validate_method.Report()
    validate_method.check_invocation(method, harness, report, timeout=10)
    return report


def test_external_method_directory_validates_without_a_repository_relative_path(tmp_path):
    method = _method(tmp_path, "json.dumps({'steps': "
                     "[{'harness_dir': request['base_harness']}]})",
                     '{"changed": false, "generation_tokens": 0}')
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "validate_method.py"), str(method)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(method / "run.py") in result.stdout


@pytest.mark.parametrize("reply", ['{"changed": true}', '{"changed": false}'])
def test_json_response_still_requires_a_trajectory(tmp_path, reply):
    report = _invoke(tmp_path, None, reply)
    assert report.failed, report.rows


@pytest.mark.parametrize("document", [
    "not json", [], None, {}, {"steps": []}, {"steps": {}},
    {"steps": "invalid"}, {"steps": [None]}, {"steps": [{}]},
])
def test_invalid_or_empty_trajectory_fails_cleanly(tmp_path, document):
    encoded = repr(document if document == "not json" else json.dumps(document))
    report = _invoke(tmp_path, encoded, '{"changed": false}')
    assert report.failed, report.rows


def test_no_change_with_a_complete_harness_step_is_valid(tmp_path):
    report = _invoke(tmp_path, "json.dumps({'steps': "
                     "[{'harness_dir': request['base_harness'], "
                     "'decision': 'no_change'}]})",
                     '{"changed": false, "generation_tokens": 0}')
    assert not report.failed, report.rows


@pytest.mark.parametrize("reply", ["[]", "null", "1"])
def test_stdout_must_be_an_object(tmp_path, reply):
    report = _invoke(tmp_path, None, reply)
    assert report.failed, report.rows


def test_step_error_is_a_failed_submission_not_an_unchanged_candidate(tmp_path):
    report = _invoke(tmp_path, "json.dumps({'steps': "
                     "[{'harness_dir': request['base_harness'], "
                     "'method_reported': {'error': 'proposal failed'}}]})",
                     '{"changed": false}')
    assert report.failed, report.rows
