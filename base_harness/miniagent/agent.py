#!/usr/bin/env python3
"""Adapt the candidate-owned upstream MiniAgent runtime to HarnessGrad.

Native source/resources are pinned in PROVENANCE.json and remain editable. The
bridge records actual SDK requests and native actions; the platform owns scoring.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
SOURCE_COMMIT = 'dea1b1d0285e3328234a1e344562bb9e54ce14f7'


def submission_paths(task: dict) -> list[str] | None:
    if 'submission' not in task or task['submission'] == {'kind': 'text'}:
        return None
    spec = task['submission']
    if not isinstance(spec, dict) or set(spec) != {'kind', 'paths'} or spec['kind'] != 'files':
        raise ValueError('invalid public submission descriptor')
    paths = spec['paths']
    if not isinstance(paths, list) or not paths:
        raise ValueError('file submission paths must be a nonempty list')
    for name in paths:
        if (not isinstance(name, str) or not name.strip() or '\\' in name or ':' in name
                or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/'))):
            raise ValueError('submission paths must stay inside workdir')
    return paths


def task_text(task: dict) -> str:
    goal = task.get('goal') or task.get('instruction')
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError('task requires a non-empty goal or instruction')
    paths = submission_paths(task)
    delivery = ('Leave these files relative to the current working directory for scoring: '
                + json.dumps(paths) + '. A final message cannot replace those files.'
                if paths is not None else
                "Write every file the task asks for exactly as asked.")
    prompt = goal.strip() + '\n\n' + delivery
    if task.get('inputs') is not None:
        prompt += '\n\nTask inputs:\n' + json.dumps(task['inputs'], ensure_ascii=False)
    return prompt


def answer_stamp(work: Path):
    path = work / 'answer.txt'
    if path.is_symlink() or not path.resolve().is_relative_to(work):
        raise ValueError('answer.txt must be a regular file inside workdir')
    return (path.stat().st_mtime_ns, path.read_bytes()) if path.is_file() else None


def save_answer(task: dict, work: Path, before, final: str) -> tuple[str, str]:
    paths = submission_paths(task)
    if paths is not None:
        missing = [name for name in paths
                   if not (work / name).is_file()
                   or not (work / name).resolve().is_relative_to(work)]
        if missing:
            raise ValueError('missing submission files: ' + ', '.join(missing))
        return '', 'files'
    current = answer_stamp(work)
    if current is not None and current != before and current[1].strip():
        return current[1].decode('utf-8', 'replace'), 'artifact'
    if not final.strip() or final == '(no answer produced)':
        raise ValueError('native MiniAgent produced no final answer')
    path = work / 'answer.txt'
    if path.is_symlink() or not path.resolve().is_relative_to(work):
        raise ValueError('answer.txt must stay inside workdir')
    path.write_text(final, encoding='utf-8')
    return final, 'final_message'


def timeout_seconds(env: dict) -> float:
    value = float(env.get('HG_AGENT_MINIAGENT_TIMEOUT_S', '300'))
    if not math.isfinite(value) or value <= 0:
        raise ValueError('MiniAgent timeout must be positive and finite')
    if env.get('HG_AGENT_DEADLINE_S'):
        deadline = float(env['HG_AGENT_DEADLINE_S'])
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError('HG_AGENT_DEADLINE_S must be positive and finite')
        # This is a relative duration supplied by the platform, not wall time.
        value = min(value, max(0.05, deadline - min(5, deadline / 5)))
    return value


def child_environment(parent: dict, scratch: Path) -> dict:
    excluded = {'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'ANTHROPIC_API_KEY', 'DEEPSEEK_API_KEY',
                'PYTHONHOME', 'CODEX_HOME'}
    env = {k: v for k, v in parent.items()
           if not k.startswith(('HG_METHOD_', 'HG_ENV_')) and k not in excluded}
    # The platform injects its declared dependency overlay through PYTHONPATH.
    # Its sandbox already removes paths into the hidden platform tree. Dropping
    # this here would silently make the native worker depend on host packages.
    for key, directory in {'HOME': 'home', 'XDG_CONFIG_HOME': 'config',
                           'XDG_CACHE_HOME': 'cache', 'XDG_DATA_HOME': 'data',
                           'TMPDIR': 'tmp'}.items():
        path = scratch / directory
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    # A candidate can remain read-only: bytecode belongs outside its source tree.
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def _record(trace: Path, value: dict, secrets=()) -> None:
    text = json.dumps(value, ensure_ascii=False)
    for secret in secrets:
        if secret:
            text = text.replace(secret, '<redacted>')
    with trace.open('a', encoding='utf-8') as stream:
        stream.write(text + '\n')


def run(task: dict, work: Path) -> int:
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    prompt = task_text(task)
    env = dict(os.environ)
    backend = env.get('HG_AGENT_BACKEND', 'mock')
    model = (env.get('HG_AGENT_MODEL') or ('miniagent-mock' if backend == 'mock' else '')).strip()
    base = env.get('HG_AGENT_BASE_URL', '').strip()
    key = env.get('HG_AGENT_API_KEY') or 'local'
    if not model or (backend != 'mock' and not base):
        raise ValueError('HG_AGENT_MODEL and HG_AGENT_BASE_URL are required')
    timeout = timeout_seconds(env)
    turns = int(env.get('HG_AGENT_MAX_STEPS', '10'))
    tokens = int(env.get('HG_AGENT_MAX_OUTPUT_TOKENS', env.get('HG_AGENT_MAX_TOKENS', '2048')))
    if turns < 1 or tokens < 1:
        raise ValueError('model step and token budgets must be positive')
    context = int(env.get('HG_AGENT_CONTEXT_WINDOW', '32768'))
    if context < 1 or tokens >= context:
        raise ValueError('output token budget must be smaller than context window')
    trace = work / 'trace.jsonl'
    trace.write_text('', encoding='utf-8')
    secrets = sorted([v for k, v in env.items() if v and any(
        tag in k.upper() for tag in ('API_KEY', 'SECRET', 'PASSWORD', 'ACCESS_TOKEN'))],
        key=len, reverse=True)
    _record(trace, {'harness_identity': {
        'harness': 'miniagent', 'harness_version': '0.1.0', 'agent_model': model,
        'agent_backend': backend, 'upstream_commit': SOURCE_COMMIT,
        'config': {'base_url': base, 'step_limit': turns, 'max_tokens': tokens,
                   'context_window': context, 'runtime': 'candidate-owned Python'}},
        'task_id': task.get('task_id')}, secrets)
    before = answer_stamp(work)
    with tempfile.TemporaryDirectory(prefix='hg-miniagent-') as temporary:
        scratch = Path(temporary)
        result_path = scratch / 'result.json'
        request = {'prompt': prompt, 'workdir': str(work), 'root': str(ROOT),
                   'runs_root': str(scratch / 'runs'), 'trace': str(trace),
                   'result': str(result_path), 'backend': backend, 'model': model,
                   'base_url': base, 'api_key': key, 'max_turns': turns,
                   'max_tokens': tokens, 'context_window': context,
                   'request_timeout': min(timeout, float(env.get('HG_AGENT_TIMEOUT_S', '60'))),
                   'secrets': secrets}
        log_path = scratch / 'worker.log'
        with log_path.open('w', encoding='utf-8') as log:
            proc = subprocess.Popen([sys.executable, '-B', str(ROOT / 'bridge.py')],
                                    cwd=work, env=child_environment(env, scratch),
                                    stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace',
                                    start_new_session=True)
            timed_out = False
            try:
                proc.communicate(json.dumps(request), timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Native shell calls can leave a detached service. The bridge handles
                # SIGTERM and reaps its descendants; SIGKILL is the bounded fallback.
                if proc.poll() is None:
                    try: os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                    try: proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try: os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                        proc.wait()
        if timed_out or proc.returncode:
            detail = log_path.read_text(encoding='utf-8', errors='replace')[-2000:]
            reason = f'MiniAgent timed out after {timeout:g}s' if timed_out else f'MiniAgent exited {proc.returncode}'
            _record(trace, {'error': reason, 'stderr_tail': detail, 'exit_code': proc.returncode}, secrets)
            print(reason, file=sys.stderr)
            return 1
        try:
            result = json.loads(result_path.read_text(encoding='utf-8'))
            if result.get('stop_reason') != 'completed':
                raise ValueError(str(result.get('stop_reason') or 'native run did not complete'))
            answer, source = save_answer(task, work, before, str(result.get('answer') or ''))
        except (OSError, ValueError) as exc:
            _record(trace, {'error': str(exc)}, secrets)
            print(f'miniagent: {exc}', file=sys.stderr)
            return 1
        _record(trace, {'submitted': True, 'answer': answer, 'answer_source': source,
                        'native_stop_reason': result['stop_reason']}, secrets)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', required=True, type=Path)
    parser.add_argument('--workdir', required=True, type=Path)
    args = parser.parse_args()
    try:
        return run(json.loads(args.task.read_text(encoding='utf-8')), args.workdir)
    except (OSError, ValueError) as exc:
        print(f'miniagent: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
