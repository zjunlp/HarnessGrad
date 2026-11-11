"""A task-owned OpenClaw gateway and bounded child-session collection."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import stat
import subprocess
import time
from typing import Literal, NamedTuple
from urllib.parse import quote

MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_CHILDREN = 64
SPAWN_REJECTION_STATUSES = ('error', 'rejected', 'denied', 'forbidden')


class ChildCompletion(NamedTuple):
    execution_status: Literal['finished', 'pending', 'unknown']
    outcomes: dict[str, dict | None]


def _configure_retention_plugin(config: dict) -> None:
    plugin_id = 'harnessgrad-retain-subagents'
    plugins = config.get('plugins', {})
    deny = plugins.get('deny', [])
    entries = plugins.get('entries', {})
    conflict = None
    if plugins.get('enabled') is False:
        conflict = 'plugins.enabled is false'
    elif isinstance(deny, list) and any(isinstance(name, str) and name.strip().lower() == plugin_id for name in deny):
        conflict = f'plugins.deny contains {plugin_id}'
    elif any(name.strip().lower() == plugin_id and isinstance(entry, dict) and entry.get('enabled') is False
             for name, entry in entries.items()):
        conflict = f'plugins.entries.{plugin_id}.enabled is false'
    if conflict:
        raise ValueError(f'OpenClaw requires {plugin_id} to verify native delegation. '
                         f'{conflict} in config.json. Remove that restriction to run this harness.')

    plugin_path = str(Path(__file__).with_name(plugin_id + '.mjs').resolve())
    paths = plugins.setdefault('load', {}).setdefault('paths', [])
    if plugin_path not in paths:
        paths.append(plugin_path)
    entries.setdefault(plugin_id, {})['enabled'] = True
    plugins['entries'] = entries
    if plugins.get('allow') and plugin_id not in plugins['allow']:
        plugins['allow'].append(plugin_id)
    config['plugins'] = plugins


def configure(config: dict, *, profile: str = 'list') -> int:
    """Replace gateway/network surfaces with one private task-scoped listener."""
    if profile == 'entries':
        _configure_retention_plugin(config)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    config['gateway'] = {'mode': 'local', 'bind': 'loopback', 'port': port,
                         'auth': {'mode': 'token', 'token': secrets.token_hex(32)},
                         'controlUi': {'enabled': False}}
    return port


@contextmanager
def running(argv, *, state: Path, work: Path, env: dict, port: int, deadline: float, stop):
    """Start only our foreground gateway; never discover, reuse or stop a shared one."""
    child = dict(env)
    with (state / 'gateway.log').open('wb') as log:
        proc = subprocess.Popen([*argv, 'gateway', 'run', '--port', str(port), '--bind', 'loopback'],
                                cwd=work, env=child, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            until = min(deadline, time.monotonic() + 45)
            while time.monotonic() < until:
                if proc.poll() is not None:
                    log.flush()
                    try:
                        detail = (_read_owned(state, ('gateway.log',)) or b'').decode('utf-8', 'replace')[-1200:]
                    except (OSError, ValueError):
                        detail = ''
                    raise ValueError('task-local OpenClaw gateway exited during startup: ' + detail)
                # A successful connection only indicates readiness, not model availability.
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise ValueError('task-local OpenClaw gateway startup timed out')
            yield
        finally:
            if proc.poll() is None:
                stop(proc)


def _read_owned(root: Path, parts: tuple[str, ...], limit: int = MAX_RECORD_BYTES) -> bytes | None:
    """Read a bounded ordinary file beneath task state without following links."""
    fds = []
    try:
        current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(current)
        for part in parts[:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            fds.append(current)
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        fds.append(file)
        info = os.fstat(file)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('unsafe or oversized task session record')
        data = bytearray()
        while len(data) <= limit:
            block = os.read(file, min(65536, limit + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        if len(data) > limit:
            raise ValueError('oversized task session record')
        return bytes(data)
    except FileNotFoundError:
        return None
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _mapping(root: Path, *parts: str) -> dict:
    raw = _read_owned(root, parts)
    if raw is None:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('invalid task session record')
    return value


def _registry(root: Path, profile: str) -> dict:
    if profile != 'entries':
        registry = _mapping(root, 'subagents', 'runs.json').get('runs', {})
        if not isinstance(registry, dict):
            raise ValueError('invalid subagent registry')
        return registry
    database = root / 'state' / 'openclaw.sqlite'
    if not database.exists():
        return {}
    if any(path.is_symlink() for path in (database, database.parent)) or not database.resolve().is_relative_to(root.resolve()):
        raise ValueError('unsafe subagent database')
    if not database.is_file() or database.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('invalid subagent database')
    uri = 'file:' + quote(str(database), safe='/') + '?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=.5) as db:
        rows = db.execute('SELECT run_id, payload_json FROM subagent_runs LIMIT ?',
                          (MAX_CHILDREN + 1,)).fetchall()
    if len(rows) > MAX_CHILDREN:
        raise ValueError('too many subagent runs')
    result = {}
    for run_id, payload in rows:
        if not isinstance(payload, str) or len(payload) > 1024 * 1024:
            raise ValueError('invalid subagent record')
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError('invalid subagent record')
        parent_completion = value.get('parentCompletion')
        if isinstance(parent_completion, dict) and parent_completion.get('completionTarget') == 'parent':
            value = parent_completion
        execution = value.get('execution')
        if isinstance(execution, dict):
            ended_at = execution.get('endedAt') if execution.get('status') == 'terminal' else None
            value = {**value, 'endedAt': ended_at, 'outcome': execution.get('outcome')}
        result[str(run_id)] = value
    return result


def _receipt(value, depth=0):
    if depth > 5:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict):
        if 'status' in value and ('childSessionKey' in value or value['status'] in SPAWN_REJECTION_STATUSES):
            return value
        for nested in value.values():
            found = _receipt(nested, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _receipt(nested, depth + 1)
            if found is not None:
                return found
    return None


def accepted_children(rows: list[dict]) -> tuple[set[str], bool]:
    keys, unresolved, pending = set(), False, set()
    for row in rows:
        message = row.get('message') if isinstance(row, dict) else None
        if not isinstance(message, dict):
            continue
        if message.get('role') == 'assistant':
            for part in message.get('content') or []:
                if not isinstance(part, dict) or part.get('type') != 'toolCall':
                    continue
                args = part.get('arguments') if isinstance(part.get('arguments'), dict) else {}
                action = str(args.get('id') or '')
                is_spawn = part.get('name') == 'sessions_spawn' or (
                    part.get('name') in ('dispatch_action', 'tool_call') and action.split(':')[-1] == 'sessions_spawn')
                if is_spawn and isinstance(part.get('id'), str):
                    pending.add(part['id'])
            continue
        call_id = message.get('toolCallId')
        if message.get('role') != 'toolResult' or (message.get('toolName') != 'sessions_spawn' and call_id not in pending):
            continue
        pending.discard(call_id)
        details = _receipt(message.get('details')) or _receipt(message.get('content')) or {}
        if details.get('status') != 'accepted':
            if details.get('status') not in SPAWN_REJECTION_STATUSES and not message.get('isError'):
                unresolved = True
            continue
        key = details.get('childSessionKey')
        if isinstance(key, str) and re.fullmatch(r'agent:[A-Za-z0-9_-]+:subagent:[A-Za-z0-9_-]+', key):
            keys.add(key)
        else:
            unresolved = True
    return keys, unresolved or bool(pending)


def _child_settled(item: dict, profile: str, settled_delivery: tuple[str, ...]) -> bool:
    if not item.get('endedAt'):
        return False
    if profile == 'entries':
        if not item.get('cleanupCompletedAt'):
            return False
        wake = item.get('requesterSettleWake')
        if isinstance(wake, dict) and wake.get('status') in ('pending', 'dispatching'):
            return False
    elif item.get('expectsCompletionMessage') and not item.get('cleanupCompletedAt'):
        return False
    delivery = item.get('delivery')
    return not isinstance(delivery, dict) or delivery.get('status') in settled_delivery


def wait_for_children(state: Path, parent_rows: list[dict], *, deadline: float,
                      profile: str = 'list') -> ChildCompletion:
    """Wait for child runs and their requester continuations to settle."""
    expected, unresolved = accepted_children(parent_rows)
    if unresolved:
        return ChildCompletion('unknown', {})
    if not expected:
        return ChildCompletion('finished', {})
    settled_delivery = ('delivered', 'failed', 'skipped')
    if profile == 'entries':
        settled_delivery += ('not_required',)
    execution_status = 'unknown'
    while time.monotonic() < deadline:
        try:
            registry = _registry(state, profile)
        except (OSError, ValueError, sqlite3.Error):
            execution_status = 'unknown'
            time.sleep(.1)
            continue
        if len(registry) > MAX_CHILDREN:
            return ChildCompletion('unknown', {})
        by_key = {item['childSessionKey']: item for item in registry.values()
                  if isinstance(item, dict) and isinstance(item.get('childSessionKey'), str)}
        # This gateway and state belong to one task, so every registry entry is
        # part of this task's delegation tree, including nested children.
        expected.update(by_key)
        if len(expected) > MAX_CHILDREN:
            return ChildCompletion('unknown', {})
        finished = {key: item.get('outcome') for key, item in by_key.items()
                    if _child_settled(item, profile, settled_delivery)}
        if expected <= finished.keys():
            return ChildCompletion('finished', finished)
        execution_status = 'pending' if by_key.keys() - finished.keys() else 'unknown'
        time.sleep(.1)
    return ChildCompletion(execution_status, {})


def collect_child_sessions(state: Path, outcomes: dict[str, dict | None], *, deadline: float,
                           profile: str = 'list', export_child=None) -> tuple[list[dict], bool]:
    """Export available traces for confirmed child sessions within the remaining budget."""
    try:
        sessions = _mapping(state, 'agents', 'main', 'sessions', 'sessions.json') if profile != 'entries' else {}
    except (OSError, ValueError):
        return [], False
    children = []
    for key in sorted(outcomes):
        if time.monotonic() >= deadline:
            break
        try:
            if profile == 'entries':
                rows = export_child(key) if export_child is not None else None
            else:
                info = sessions.get(key)
                session = info.get('sessionId') if isinstance(info, dict) else None
                if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session):
                    continue
                raw = _read_owned(state, ('agents', 'main', 'sessions', session + '.jsonl'))
                rows = [json.loads(line) for line in raw.splitlines() if line.strip()] if raw else None
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if isinstance(rows, list) and rows and all(isinstance(row, dict) for row in rows):
            children.append({'session_key': key, 'outcome': outcomes[key], 'rows': rows})
    return children, len(children) == len(outcomes)
