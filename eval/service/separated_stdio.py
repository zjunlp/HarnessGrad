"""Trusted stdio scoring with separate verifier and candidate namespaces.

The fixed scorer runs at /trusted/scorer.py with network none. Each candidate
receives only frozen solution.py and one stdin input in a fresh network-none
namespace. Tests, comparison code, relay, and writable verdict remain outside it.
This is an external-process variant, not the upstream wrapped-function executor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

PYTHON, BWRAP = "/usr/bin/python3", "/usr/bin/bwrap"
SCORER_FILE = Path(__file__).resolve()
UPSTREAM_COMMIT = "28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24"
UPSTREAM_EXECUTOR_SHA256 = "b7cb6a8a69807bb868150a61742e25d7bb5328bbe01d514471b6ec43c9fa9ed2"
MAX_BYTES, MAX_CASES = 1048576, 16
METRIC = "pass@1 scoped external-process stdio; upstream text/decimal comparison"


# Verbatim helpers from the public, pinned testing_util.py at UPSTREAM_COMMIT.
def convert_line_to_decimals(line: str) -> tuple[bool, list[Decimal]]:
    try:
        decimal_line = [Decimal(elem) for elem in line.split()]
    except:
        return False, []
    return True, decimal_line


def get_stripped_lines(val: str):
    ## you don't want empty lines to add empty list after splitlines!
    val = val.strip()

    return [val_line.strip() for val_line in val.split("\n")]


def compare(prediction: str, expected: str) -> bool:
    left, right = get_stripped_lines(prediction), get_stripped_lines(expected)
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if a == b:
            continue
        ok_a, numbers_a = convert_line_to_decimals(a)
        ok_b, numbers_b = convert_line_to_decimals(b)
        try:
            if not ok_a or not ok_b or numbers_a != numbers_b:
                return False
        except InvalidOperation:
            return False
    return True


def read_regular(path: Path) -> bytes:
    """Reject links, FIFOs, devices and large submissions before reading bytes."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise ValueError("bounded regular single-link file required")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            content = handle.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("oversized submission")
        return content
    finally:
        os.close(fd)


def namespace_command(task_dir: Path, argv: list[str], *, writable=False,
                      relay_dir: Path | None = None, tests_path: Path | None = None,
                      verdict_dir: Path | None = None) -> list[str]:
    """Explicit mount allowlist from an empty root; no host/SDK fallback."""
    cmd = [BWRAP, "--die-with-parent", "--new-session", "--unshare-pid",
           "--unshare-net", "--unshare-ipc", "--unshare-uts", "--cap-drop", "ALL",
           "--clearenv"]
    for runtime in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(runtime).exists():
            cmd += ["--ro-bind", runtime, runtime]
    cmd += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--bind" if writable else "--ro-bind", str(task_dir), "/work"]
    if relay_dir is not None:
        cmd += ["--ro-bind", str(relay_dir), "/relay"]
    if tests_path is not None:
        if relay_dir is not None or verdict_dir is None or writable:
            raise ValueError("verifier must be frozen and relay-free")
        cmd += ["--ro-bind", str(SCORER_FILE), "/trusted/scorer.py",
                "--ro-bind", str(tests_path), "/tests.json",
                "--bind", str(verdict_dir), "/verdict"]
    elif verdict_dir is not None:
        raise ValueError("verdict mount is verifier-only")
    cmd += ["--chdir", "/work", "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    if relay_dir is not None:
        cmd += ["--setenv", "HG_AGENT_UNIX_SOCKET", "/relay/model.sock"]
    return cmd + ["--"] + argv


def run_bounded(argv, *, capture_dir, input_bytes=b"", timeout=6, cpu=3, memory_mib=512):
    # prlimit avoids preexec_fn in the relay's multithreaded agent supervisor.
    argv = ["/usr/bin/prlimit", f"--cpu={cpu}", f"--as={memory_mib * 1024 * 1024}",
            f"--fsize={MAX_BYTES}", "--", *argv]
    with tempfile.TemporaryFile(dir=capture_dir) as out, tempfile.TemporaryFile(dir=capture_dir) as err:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                env={}, start_new_session=True)
        timed_out = False
        try:
            proc.communicate(input_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=3)
        out.seek(0)
        err.seek(0)
        return proc.returncode, out.read(MAX_BYTES + 1), err.read(MAX_BYTES + 1), timed_out


def validate_tests(tests):
    if not isinstance(tests, dict) or tests.get("fn_name") is not None:
        raise ValueError("stdin tests required")
    inputs, outputs = tests.get("inputs"), tests.get("outputs")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        raise ValueError("input/output lists required")
    if not 0 < len(inputs) <= MAX_CASES or len(inputs) != len(outputs):
        raise ValueError("nonempty complete bounded tests required")
    if any(not isinstance(x, str) or len(x.encode()) > MAX_BYTES for x in inputs + outputs):
        raise ValueError("bounded string cases required")
    return inputs, outputs


def _unmeasured(reason):
    return {"measured": False, "metric": METRIC, "error": reason}


def _score_in_namespace(tests):
    inputs, outputs = validate_tests(tests)
    verdicts = []
    for inp, expected in zip(inputs, outputs):
        argv = namespace_command(Path("/work"), [PYTHON, "-I", "-S", "solution.py"])
        code, prediction, err, timed_out = run_bounded(argv, capture_dir="/tmp", input_bytes=inp.encode())
        # bwrap setup failure is infrastructure, not a measured solution zero.
        if err.startswith(b"bwrap:"):
            return _unmeasured("candidate namespace failed")
        passed = (not timed_out and code == 0 and len(prediction) <= MAX_BYTES
                  and compare(prediction.decode(errors="replace"), expected))
        verdicts.append(bool(passed))
    return {"measured": True, "metric": METRIC, "reward": float(all(verdicts)),
            "tests": {"total": len(inputs), "passed": sum(verdicts)},
            "per_test": verdicts, "verifier_network": "none", "candidate_network": "none",
            "candidate_tests_mount": False, "candidate_verdict_mount": False,
            "candidate_relay_mount": False,
            "deviations": ["fresh external Python interpreter per input, no upstream import prelude",
                           "3s CPU / 6s wall / 512MiB address space / 1MiB output per case",
                           "malformed signaling decimal output fails instead of raising"]}


def score(solution: bytes, tests_bytes: bytes, work_root: Path) -> dict:
    """Freeze bytes; launch only the fixed trusted scorer; return redacted verdict."""
    try:
        tests = json.loads(tests_bytes)
        inputs, _ = validate_tests(tests)
        if not isinstance(solution, bytes) or len(solution) > MAX_BYTES:
            raise ValueError("bounded solution bytes required")
    except (ValueError, UnicodeError, TypeError):
        return _unmeasured("invalid scoring input")
    with tempfile.TemporaryDirectory(prefix="score-", dir=work_root) as tmp:
        staging = Path(tmp)
        candidate, verdict = staging / "candidate", staging / "verdict"
        candidate.mkdir()
        verdict.mkdir()
        (candidate / "solution.py").write_bytes(solution)
        tests_path = staging / "tests.json"
        tests_path.write_bytes(tests_bytes)
        argv = namespace_command(candidate, [PYTHON, "-I", "-S", "/trusted/scorer.py",
                                 "--score-in-namespace"], tests_path=tests_path,
                                 verdict_dir=verdict)
        code, _, _, timed_out = run_bounded(argv, capture_dir=staging,
                                           timeout=min(120, len(inputs) * 6 + 10),
                                           cpu=60, memory_mib=1024)
        if timed_out or code != 0:
            return _unmeasured("trusted verifier timeout or infrastructure failure")
        try:
            result = json.loads(read_regular(verdict / "result.json"))
        except (OSError, ValueError):
            return _unmeasured("missing or invalid trusted verdict")
        if result.get("measured") is True:
            values = result.get("per_test")
            if (not isinstance(values, list) or len(values) != len(inputs)
                    or any(type(x) is not bool for x in values)
                    or result.get("reward") != float(all(values))):
                return _unmeasured("incomplete or inconsistent trusted verdict")
        result["solution_sha256"] = hashlib.sha256(solution).hexdigest()
        result["scorer_sha256"] = hashlib.sha256(SCORER_FILE.read_bytes()).hexdigest()
        result["tests_sha256"] = hashlib.sha256(tests_bytes).hexdigest()
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--score-in-namespace", action="store_true", required=True)
    parser.parse_args()
    try:
        verdict = _score_in_namespace(json.loads(Path("/tests.json").read_bytes()))
    except (ValueError, OSError, TypeError):
        verdict = _unmeasured("verifier input or namespace failure")
    Path("/verdict/result.json").write_text(json.dumps(verdict, sort_keys=True) + "\n")
