"""Offline C1/C2 reproduction and same-scenario comparison fixtures."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import uuid

from harnessgrad.improvement.contracts import is_relative_path
from harnessgrad.personal.contracts import digest, encode, identifier

SCHEMA = 'harnessgrad.personal.evolution-check.v1'
MAX_LOG_BYTES = 64 * 1024
MAX_FILES = 50
MAX_FILE_BYTES = 64 * 1024


@dataclass(frozen=True)
class ReproductionScenario:
    scenario_id: str
    baseline_files: dict[str, str]
    candidate_files: dict[str, str]
    command: tuple[str, ...]
    timeout_seconds: float

    @classmethod
    def from_dict(cls, value: dict) -> ReproductionScenario:
        fields = {'schema', 'scenario_id', 'baseline_files', 'candidate_files', 'command', 'timeout_seconds'}
        if not isinstance(value, dict) or set(value) != fields or value['schema'] != SCHEMA:
            raise ValueError('invalid evolution-check fixture schema')
        for name in ('baseline_files', 'candidate_files'):
            files = value[name]
            if (not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES
                    or any(not isinstance(path, str) or not is_relative_path(path)
                           or not isinstance(text, str) or len(text.encode('utf-8')) > MAX_FILE_BYTES
                           for path, text in files.items())):
                raise ValueError('fixture files must be bounded relative text files')
        if set(value['baseline_files']) != set(value['candidate_files']):
            raise ValueError('baseline and candidate must select the same files')
        if value['baseline_files'] == value['candidate_files']:
            raise ValueError('candidate must differ from baseline')
        command = value['command']
        if (not isinstance(command, list) or not 1 <= len(command) <= 32
                or command[0] != '{python}'
                or any(not isinstance(part, str) or not part or len(part) > 1024 for part in command)):
            raise ValueError('fixture command must be a bounded Python argument array')
        timeout = value['timeout_seconds']
        if type(timeout) not in (int, float) or not 0.05 <= timeout <= 30:
            raise ValueError('fixture timeout must be between 0.05 and 30 seconds')
        return cls(identifier(value['scenario_id']), dict(value['baseline_files']),
                   dict(value['candidate_files']), tuple(command), float(timeout))


@dataclass(frozen=True)
class RunRecord:
    run_ref: str
    scenario_id: str
    phase: str
    command: tuple[str, ...]
    files_sha256: str
    exit_code: int | None
    timed_out: bool
    reason_code: str | None
    stdout: str
    stderr: str
    output_truncated: bool
    duration_ms: int
    source_unchanged: bool


@dataclass(frozen=True)
class ComparisonReport:
    comparison_ref: str
    scenario_id: str
    baseline: RunRecord
    candidate: RunRecord
    comparable: bool
    status: str
    reason_code: str | None
    apply_eligible: bool


def _render_command(command: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sys.executable if part == '{python}' else part for part in command)


def _write_files(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = root.joinpath(*relative.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8', newline='')


def _selected_digest(root: Path, files: dict[str, str]) -> str:
    selected = {}
    for relative in sorted(files):
        try:
            selected[relative] = root.joinpath(*relative.split('/')).read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            selected[relative] = {'unreadable': True}
    return digest(selected)


def _bounded_output(value: bytes) -> tuple[str, bool]:
    truncated = len(value) > MAX_LOG_BYTES
    return value[:MAX_LOG_BYTES].decode('utf-8', errors='replace'), truncated


def run_scenario(scenario: ReproductionScenario, state_root: Path, *, phase: str) -> RunRecord:
    if phase not in ('baseline', 'candidate'):
        raise ValueError('run phase must be baseline or candidate')
    files = scenario.baseline_files if phase == 'baseline' else scenario.candidate_files
    run_ref = 'run_' + uuid.uuid4().hex
    run_root = state_root / 'evolution-runs' / run_ref
    run_root.mkdir(parents=True, exist_ok=False)
    _write_files(run_root, files)
    before = _selected_digest(run_root, files)
    command = _render_command(scenario.command)
    env = {'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8'}
    for name in ('PATH', 'SystemRoot', 'WINDIR'):
        if name in os.environ:
            env[name] = os.environ[name]
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            result = subprocess.run(command, cwd=run_root, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stdout_file, stderr=stderr_file,
                                    timeout=scenario.timeout_seconds, check=False)
            exit_code, timed_out, reason_code = result.returncode, False, None
        except subprocess.TimeoutExpired:
            exit_code, timed_out, reason_code = None, True, 'execution_timeout'
        except OSError:
            exit_code, timed_out, reason_code = None, False, 'runner_unavailable'
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout_bytes = stdout_file.read(MAX_LOG_BYTES + 1)
        stderr_bytes = stderr_file.read(MAX_LOG_BYTES + 1)
    duration = max(0, round((time.monotonic() - started) * 1000))
    stdout, stdout_truncated = _bounded_output(stdout_bytes)
    stderr, stderr_truncated = _bounded_output(stderr_bytes)
    after = _selected_digest(run_root, files)
    record = RunRecord(run_ref, scenario.scenario_id, phase, command, before, exit_code, timed_out,
                       reason_code, stdout, stderr, stdout_truncated or stderr_truncated, duration, before == after)
    (run_root / 'run-record.json').write_text(encode(asdict(record)) + '\n', encoding='utf-8')
    return record


def compare_scenario(scenario: ReproductionScenario, state_root: Path) -> ComparisonReport:
    baseline = run_scenario(scenario, state_root, phase='baseline')
    candidate = run_scenario(scenario, state_root, phase='candidate')
    comparable = (baseline.command == candidate.command and baseline.reason_code is None
                  and candidate.reason_code is None and baseline.source_unchanged and candidate.source_unchanged)
    if not comparable:
        status = 'not_comparable'
        reason = baseline.reason_code or candidate.reason_code or 'scenario_mismatch'
    elif baseline.exit_code == 0:
        status, reason = 'not_reproduced', 'baseline_passed'
    elif candidate.exit_code == 0:
        status, reason = 'retest_passed', None
    else:
        status, reason = 'retest_failed', 'candidate_failed'
    report = ComparisonReport('comparison_' + uuid.uuid4().hex, scenario.scenario_id, baseline, candidate,
                              comparable, status, reason, status == 'retest_passed')
    report_root = state_root / 'evolution-reports'
    report_root.mkdir(parents=True, exist_ok=True)
    (report_root / f'{report.comparison_ref}.json').write_text(
        encode(asdict(report)) + '\n', encoding='utf-8',
    )
    return report
