"""Personal setup, read-only service controls and explicit B1 model fixtures."""
from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import time
from urllib.parse import urlsplit


def connection(path: Path) -> dict:
    from harnessgrad.personal.contracts import decode
    file = path / 'connection.json'
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 16384:
        raise ValueError('no ordinary owned service connection file')
    value = decode(file.read_bytes())
    url = urlsplit(value['url'])
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
            or url.username or url.password or url.path != '/' or url.query or url.fragment):
        raise ValueError('connection must identify the local personal service')
    return value


def contact(value: dict, *, stop=False) -> dict:
    client = http.client.HTTPConnection('127.0.0.1', urlsplit(value['url']).port, timeout=5)
    headers = {'Authorization': 'Bearer ' + value['token'], 'Content-Type': 'application/json'}
    try:
        client.request('GET', '/api/personal/health', headers=headers)
        response = client.getresponse()
        health = json.loads(response.read(1024 * 1024))
        if response.status != 200 or health.get('instance') != value['instance']:
            raise ValueError('service identity does not match connection file')
        if stop:
            client.request('POST', '/api/personal/shutdown',
                           body=json.dumps({'instance': value['instance']}), headers=headers)
            response = client.getresponse()
            result = json.loads(response.read(16384))
            if response.status != 200:
                raise ValueError('service refused shutdown')
            return result
        return health
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='harnessgrad personal', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('serve', 'status', 'stop', 'project', 'import', 'demo', 'inspect', 'desktop',
                 'model-config', 'model-demo'):
        sub = commands.add_parser(name)
        sub.add_argument('--state-root', type=Path, required=name != 'model-config', help='owned directory outside the checkout')
        if name in ('project', 'demo', 'model-demo'):
            sub.add_argument('--project', type=Path, required=True, help='explicit existing project; no directory scan')
        if name == 'import':
            sub.add_argument('--project-id', required=True)
            sub.add_argument('--input', type=Path, required=True, help='one synthetic import JSON file')
        if name in ('serve', 'inspect'):
            sub.add_argument('--codex', type=Path, help='explicit native executable; no PATH discovery')
            sub.add_argument('--managed-hooks', action='store_true')
        if name == 'serve':
            sub.add_argument('--background', action='store_true')
        if name == 'desktop':
            sub.add_argument('--executable', type=Path, required=True, help='built personal desktop shell')
            sub.add_argument('--ordinary', action='store_true', help='OS-managed ordinary window without floating placement')
        if name == 'model-config':
            sub.add_argument('--env-file', type=Path, help='explicit model environment file; process values take precedence')
    args = parser.parse_args(argv)
    try:
        from harnessgrad.personal.contracts import encode
        if args.command == 'model-config':
            from harnessgrad.personal.profile import load_model_settings
            result = load_model_settings(env_file=args.env_file).identity()
        elif args.command in ('status', 'stop', 'desktop'):
            value = connection(args.state_root)
            result = contact(value, stop=args.command == 'stop')
            if args.command == 'desktop':
                executable = args.executable.resolve(strict=True)
                desktop_argv = [str(executable), str((args.state_root / 'connection.json').resolve())]
                if args.ordinary:
                    desktop_argv.append('--ordinary')
                subprocess.Popen(desktop_argv,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                result = {'status': 'desktop_started', 'service_instance': value['instance']}
        elif args.command == 'serve' and args.background:
            from harnessgrad.personal.service import REPOSITORY, state_directory
            root = state_directory(args.state_root)
            if (root / 'connection.json').exists():
                raise ValueError('connection already exists; inspect the owned service before restarting')
            forwarded = ['personal', 'serve', '--state-root', str(root)]
            if args.codex:
                forwarded += ['--codex', str(args.codex.resolve())]
            if args.managed_hooks:
                forwarded += ['--managed-hooks']
            code = 'import sys,runpy;sys.path.insert(0,' + repr(str(REPOSITORY)) + ');runpy.run_module("harnessgrad.cli",run_name="__main__")'
            env = {key: os.environ[key] for key in
                   ('SystemRoot', 'WINDIR', 'PATH', 'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'TEMP', 'TMP')
                   if key in os.environ}
            proc = subprocess.Popen([sys.executable, '-X', 'utf8', '-I', '-B', '-c', code, *forwarded], cwd=root, env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + 8
            try:
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise RuntimeError('owned service exited during startup')
                    if (root / 'connection.json').is_file():
                        try:
                            descriptor = connection(root)
                            if descriptor.get('pid') != proc.pid:
                                raise RuntimeError('connection belongs to another service process')
                            result = contact(descriptor)
                            break
                        except (ValueError, OSError):
                            pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError('owned service did not become ready')
            except BaseException:
                proc.terminate()
                proc.wait(timeout=5)
                raise
        else:
            from harnessgrad.personal.service import PersonalService, read_import
            service = PersonalService(args.state_root, codex=getattr(args, 'codex', None),
                                      managed_hooks=getattr(args, 'managed_hooks', False))
            if args.command == 'serve':
                from harnessgrad.personal.api import serve
                serve(service, ready=lambda line: print(line, flush=True))
                return 0
            if args.command == 'project':
                result = service.add_project(args.project)
            elif args.command == 'demo':
                from harnessgrad.personal.fixtures import example
                project = service.add_project(args.project)
                result = service.import_session(project['id'], example())
            elif args.command == 'model-demo':
                from harnessgrad.personal.fixtures import model_demo
                result = model_demo(service, args.project)
            elif args.command == 'import':
                result = service.import_session(args.project_id, read_import(args.input))
            else:
                result = service.probe()
        print(encode(result))
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        print('personal operation failed: check selected paths, schema, database and owned service identity', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
