"""Bounded receipts tests: synthetic pytest files and fake selector runner only."""
import json
import sys
import subprocess
from pathlib import Path

import pytest

from tools import run_tests as runner
from tools import run_selected as selector


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    tests = root / "tests" / "quality"
    tests.mkdir(parents=True)
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(selector, "ROOT", root)
    monkeypatch.setattr(runner, "load_costs", lambda: {})
    return root, tests, tmp_path / "receipt.json", tmp_path / "temps"


def invoke(monkeypatch, output, temps, *args):
    monkeypatch.setattr(sys, "argv", ["run_tests.py", "--output-json", str(output),
                                     "--basetemp-root", str(temps), "--jobs", "1", *args])
    code = runner.main()
    return code, json.loads(output.read_text())


def test_real_subprocess_pass_fail_skip_and_exact_argv(sandbox, monkeypatch):
    root, tests, output, temps = sandbox
    (tests / "test_pass.py").write_text("def test_ok(): assert True\n")
    (tests / "test_fail.py").write_text("def test_bad(): assert False\n")
    (tests / "test_omit.py").write_text("raise AssertionError('must not run')\n")
    monkeypatch.setenv("SYNTHETIC_SECRET", "receipt-must-not-contain-this")
    code, data = invoke(monkeypatch, output, temps, "--file", "test_pass.py", "--file", "test_fail.py", "--no-rerun")
    assert code == data["returncode"] == 1
    assert data["passed"] == ["tests/quality/test_pass.py"]
    assert data["failed"] == ["tests/quality/test_fail.py"]
    assert data["skipped"] == ["tests/quality/test_omit.py"]
    assert not data["unexecuted"] and not data["full_coverage"]
    assert all(a["argv"][:4] == ["<redacted>", "-m", "pytest", "-q"] for a in data["attempts"])
    assert "receipt-must-not-contain-this" not in output.read_text()


def test_all_skipped_file_is_not_test_case_coverage(sandbox, monkeypatch):
    _, tests, output, temps = sandbox
    (tests / "test_skip.py").write_text("import pytest\n@pytest.mark.skip(reason='synthetic')\ndef test_skip(): pass\n")
    code, data = invoke(monkeypatch, output, temps)
    assert code == 0
    assert data["all_skipped"] == ["tests/quality/test_skip.py"]
    assert data["attempts"][0]["test_case_counts"] == {"skipped": 1}
    assert not data["attempts"][0]["test_case_counts_unknown"]
    assert data["coverage_scope"] == "file_invocations_not_test_cases_or_capabilities"


@pytest.mark.parametrize("allowed,expected", [(False, 1), (True, 0)])
def test_rerun_does_not_erase_failure(sandbox, monkeypatch, allowed, expected):
    root, tests, output, temps = sandbox
    marker = root / "first_attempt"
    (tests / "test_flaky.py").write_text(
        f"from pathlib import Path\ndef test_flaky():\n p = Path({str(marker)!r})\n"
        " existed = p.exists()\n p.touch()\n assert existed\n")
    code, data = invoke(monkeypatch, output, temps, *(["--allow-load-sensitive"] if allowed else []))
    assert code == expected
    assert [a["returncode"] for a in data["attempts"]] == [1, 0]
    assert data["failed"] == data["load_sensitive"] == ["tests/quality/test_flaky.py"]
    assert data["passed"] == []
    assert data["full_coverage"]  # Coverage is independent of success.


@pytest.mark.parametrize("args,status,code", [(["--file", "absent"], "empty", 2),
                                                (["--list"], "dry_run", 0),
                                                (["--jobs", "0"], "invalid", 2)])
def test_nonexecution(sandbox, monkeypatch, args, status, code):
    _, tests, output, temps = sandbox
    (tests / "test_one.py").write_text("raise AssertionError('must not run')\n")
    result, data = invoke(monkeypatch, output, temps, *args)
    assert result == code and data["status"] == status
    assert data["executed"] == [] and not data["full_coverage"]


@pytest.mark.parametrize("equals", [False, True])
def test_forwarded_secrets_redacted(sandbox, monkeypatch, equals):
    _, tests, output, temps = sandbox
    (tests / "test_one.py").write_text("def test_ok(): pass\n")
    secret = "--override-ini=cache_dir=secret-receipt-canary"
    args = ["--pytest-arg=" + secret] if equals else ["--pytest-arg", "cache_dir=secret-receipt-canary"]
    code, data = invoke(monkeypatch, output, temps, "--no-rerun", *args)
    assert "secret-receipt-canary" not in output.read_text()
    assert data["attempts"][0]["argv"][-1] == "<redacted-pytest-arg>"
    assert "redacted" in data["argv_policy"]


def test_arbitrary_paths_filters_and_attempt_args_redacted(sandbox, monkeypatch):
    _, tests, output, temps = sandbox
    (tests / "test_one.py").write_text("def test_ok(): pass\n")
    output = output.with_name("secret-output-canary.json")
    temps = temps.with_name("secret-temp-canary")
    code, data = invoke(monkeypatch, output, temps, "--file", "secret-filter-canary", "--file", "test_one.py")
    assert code == 0
    assert "canary" not in output.read_text()
    assert data["attempts"][0]["argv"][6] == "<redacted>"
    monkeypatch.setattr(selector, "load_plan", lambda _: {"state": "full", "suite": []})
    monkeypatch.setattr(sys, "argv", ["run_selected.py", "--plan", "secret-plan-canary", "--output-json", str(output), "--dry-run"])
    assert selector.main() == 0
    assert "canary" not in output.read_text()


@pytest.mark.parametrize("repeat_interrupt", [False, True])
def test_selector_reaps_writer_before_interrupted_receipt(sandbox, monkeypatch, repeat_interrupt):
    _, tests, output, _ = sandbox
    (tests / "test_one.py").touch()
    monkeypatch.setattr(selector, "load_plan", lambda _: {"state": "selected", "suite": ["test_one.py"]})
    monkeypatch.setattr(sys, "argv", ["run_selected.py", "--plan", "unused", "--output-json", str(output)])
    # Disable only optional git identity probes before replacing Popen.
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: type("Git", (), {"returncode": 1})())
    events = []
    class Child:
        def __init__(self, *args, **kwargs):
            events.append("spawn")
        def wait(self, timeout=None):
            if events == ["spawn"]:
                events.append("interrupt")
                raise KeyboardInterrupt
            events.append("reaped")
            return -15
        def terminate(self):
            events.append("terminate")
            assert json.loads(output.read_text())["status"] == "running"
            if repeat_interrupt:
                raise KeyboardInterrupt
    monkeypatch.setattr(selector.subprocess, "Popen", Child)
    if repeat_interrupt:
        with pytest.raises(KeyboardInterrupt):
            selector.main()
        assert json.loads(output.read_text())["status"] == "running"
        return
    assert selector.main() == 130
    assert events == ["spawn", "interrupt", "terminate", "reaped"]
    assert json.loads(output.read_text())["status"] == "interrupted"


def test_partial_receipt_survives_worker_failure(sandbox, monkeypatch):
    root, tests, output, temps = sandbox
    file = tests / "test_one.py"
    file.touch()
    receipt = runner.QualityReceipt(str(output), ["tests/quality/test_one.py"], ["tests/quality/test_one.py"])
    def fail(*args, **kwargs):
        raise OSError("secret details deliberately not persisted")
    monkeypatch.setattr(runner.subprocess, "Popen", fail)
    with pytest.raises(OSError):
        runner.run_one(file, temps, [], receipt=receipt)
    data = json.loads(output.read_text())
    assert data["status"] == "running" and data["returncode"] is None
    assert data["attempts"][0]["error_type"] == "OSError"
    assert data["failed"] == data["attempted"] == data["unexecuted"] == ["tests/quality/test_one.py"]
    assert data["executed"] == []
    assert "secret details" not in output.read_text()


def test_orchestration_error_finalizes_receipt(sandbox, monkeypatch):
    _, tests, output, temps = sandbox
    (tests / "test_one.py").touch()
    def fail(*args, **kwargs):
        raise OSError("private failure detail")
    monkeypatch.setattr(runner, "run_one", fail)
    code, data = invoke(monkeypatch, output, temps)
    assert code == 1 and data["status"] == "error"
    assert data["error_type"] == "OSError"
    assert data["unexecuted"] == data["planned"]
    assert "private failure detail" not in output.read_text()


def test_selector_provenance_present_in_initial_child_checkpoint(tmp_path, monkeypatch):
    output = tmp_path / "child.json"
    argv = ["python", "run_selected.py", "--plan", "plan.json"]
    monkeypatch.setenv("HG_QUALITY_SELECTOR_ARGV_JSON", json.dumps(argv))
    runner.QualityReceipt(str(output), [], [])
    assert json.loads(output.read_text())["selector_argv"] == runner.receipt_argv(argv)


def test_hard_interruption_keeps_atomic_checkpoint(tmp_path):
    output = tmp_path / "interrupted.json"
    script = (
        "import os, signal\nfrom tools.run_tests import QualityReceipt\n"
        f"r = QualityReceipt({str(output)!r}, ['a', 'b'], ['a', 'b'])\n"
        "i = r.start('a', ['fake-pytest', 'a'])\nr.launched(i)\nr.end(i, 1, 0.1)\n"
        "r.start('b', ['fake-pytest', 'b'])\n"
        "os.kill(os.getpid(), signal.SIGKILL)\n")
    process = subprocess.run([sys.executable, "-c", script], timeout=10)
    assert process.returncode < 0
    data = json.loads(output.read_text())
    assert data["status"] == "running" and data["returncode"] is None
    assert data["failed"] == ['a'] and data["passed"] == []
    assert data["attempts"][1]["status"] == "starting"
    assert data["executed"] == ['a'] and data["unexecuted"] == ['b']
    assert not data["full_coverage"]


@pytest.mark.parametrize("state,suite,flags,status", [
    ("deferred", [], [], "deferred"), ("selected", [], [], "empty"),
    ("full", [], ["--dry-run"], "dry_run"),
    ("selected", ["test_one.py"], ["--dry-run"], "dry_run"),
    ("deferred", [], ["--full", "--dry-run"], "dry_run"),
])
def test_selector_nonexecution(sandbox, monkeypatch, state, suite, flags, status):
    _, tests, output, _ = sandbox
    (tests / "test_one.py").touch()
    monkeypatch.setattr(selector, "load_plan", lambda _: {"state": state, "suite": suite})
    monkeypatch.setattr(sys, "argv", ["run_selected.py", "--plan", "unused", "--output-json", str(output), *flags])
    assert selector.main() == 0
    data = json.loads(output.read_text())
    assert data["status"] == status and not data["executed"]
    assert not data["full_coverage"]
    if status == "deferred":
        assert data["deferred"] == data["discovered"]
    if "--full" in flags:
        assert data["state"] == "full" and "--file" not in data["runner_argv"]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_selector_fake_child_preserves_evidence(sandbox, monkeypatch, exit_code):
    root, tests, output, _ = sandbox
    (tests / "test_one.py").touch()
    fake = root / "fake_runner.py"
    fake.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "p = Path(sys.argv[sys.argv.index('--output-json') + 1])\n"
        "d = json.loads(p.read_text())\n"
        "d.update(status='completed', executed=d['planned'], unexecuted=[])\n"
        f"d['attempts'] = [dict(file=d['planned'][0], argv=sys.argv, status='completed', returncode={exit_code})]\n"
        f"d[{'failed' if exit_code else 'passed'!r}] = d['planned']\n"
        f"p.write_text(json.dumps(d))\nsys.exit({exit_code})\n")
    monkeypatch.setattr(selector, "RUNNER", fake)
    monkeypatch.setattr(selector, "load_plan", lambda _: {"state": "selected", "suite": ["test_one.py"]})
    monkeypatch.setattr(sys, "argv", ["run_selected.py", "--plan", "unused", "--output-json", str(output)])
    assert selector.main() == exit_code
    data = json.loads(output.read_text())
    assert data["returncode"] == exit_code and data["status"] == "completed"
    assert data["selector_argv"][1] == "<redacted>"
    assert data["runner_argv"][1] == "<redacted>"
    assert len(data["attempts"]) == 1


def test_selector_invalid_plan_receipt(sandbox, monkeypatch):
    _, _, output, _ = sandbox
    monkeypatch.setattr(sys, "argv", ["run_selected.py", "--plan", "missing", "--output-json", str(output)])
    with pytest.raises(SystemExit) as exc:
        selector.main()
    assert exc.value.code == 2
    assert json.loads(output.read_text())["status"] == "invalid"
