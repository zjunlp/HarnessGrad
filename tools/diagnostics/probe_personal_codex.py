#!/usr/bin/env python3
"""Probe one explicit Codex binary using fresh homes and a local scripted endpoint.

This stage-0 diagnostic measures controlled app-server sessions. It does not
install personal evolution, mutate daily configuration, or call a real model.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if __name__ == '__main__':
    sys.dont_write_bytecode = True

from base_harness.codex.codex_runtime import digest, require_clean_startup
from harnessgrad.core import snapshot
from harnessgrad.core.readiness import Readiness
from tools.diagnostics.personal_codex_fixture import (
    AppServer, MAX_MESSAGE_BYTES, MAX_TRANSCRIPT_BYTES, MODEL, ScriptedResponses,
    decode, message_text, tool_outputs,
)

SCHEMA = 'harnessgrad.personal.codex-probe.v1'
SKILL_NAME = 'hg-phase0-check'
TOOL_NAME = 'hg_phase0_check'
WORKFLOW = '''import json,sys
from pathlib import Path
project=Path(sys.argv[1])
manifest=json.loads((project/'manifest.json').read_text(encoding='utf-8'))
missing=[name for name in manifest['files'] if not (project/name).is_file()]
print(json.dumps({'revision':REVISION,'missing':missing,'marker':'HG_TOOL_'+REVISION}))
raise SystemExit(1 if missing else 0)
'''


def child_environment(home: Path, scratch: Path, codex: Path) -> dict[str, str]:
    """Allowlist startup variables; no model, proxy or daily-home inheritance."""
    env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ}
    search = [str(Path(sys.executable).parent), str(codex.parent)]
    if os.name == 'nt':
        system = Path(env['SystemRoot']) / 'System32'
        search.extend([str(system), str(system / 'WindowsPowerShell/v1.0')])
        env['COMSPEC'] = str(system / 'cmd.exe')
    else:
        search.extend(os.defpath.split(os.pathsep))
    env.update(PATH=os.pathsep.join(search), HOME=str(home), USERPROFILE=str(home),
               CODEX_HOME=str(home), APPDATA=str(home / 'appdata'),
               LOCALAPPDATA=str(home / 'localappdata'), TMP=str(scratch), TEMP=str(scratch),
               TMPDIR=str(scratch), XDG_CONFIG_HOME=str(home), XDG_CACHE_HOME=str(home),
               PYTHONDONTWRITEBYTECODE='1', LANG='C.UTF-8')
    require_clean_startup(env)
    return env


def fixture_config(home: Path, base_url: str) -> None:
    config = (f'model = "{MODEL}"\nmodel_provider = "phase0"\nweb_search = "disabled"\n'
              '[model_providers.phase0]\nname = "HarnessGrad scripted fixture"\n'
              f'base_url = "{base_url}"\nwire_api = "responses"\n'
              'requires_openai_auth = false\nrequest_max_retries = 0\n'
              'stream_max_retries = 0\nsupports_websockets = false\n'
              '[analytics]\nenabled = false\n[feedback]\nenabled = false\n')
    (home / 'config.toml').write_text(config, encoding='utf-8')


def bundle(root: Path, revision: str) -> Path:
    target = root / 'bundles' / revision
    skills = target / 'skills' / SKILL_NAME
    skills.mkdir(parents=True)
    (target / 'memory.md').write_text(f'HG_MEMORY_{revision}: Give the conclusion first.\n',
                                    encoding='utf-8')
    (skills / 'SKILL.md').write_text(
        f'---\nname: {SKILL_NAME}\ndescription: Synthetic delivery check.\n---\n'
        f'HG_SKILL_{revision}: Check the fixture manifest.\n', encoding='utf-8')
    (target / 'workflow.py').write_text(f'REVISION={revision!r}\n' + WORKFLOW, encoding='utf-8')
    return target


def run_workflow(target: Path, project: Path, env: dict[str, str], params: dict) -> dict:
    """Only the fixed synthetic component, never a command supplied by a tool."""
    revision = target.name
    if (params.get('tool') != TOOL_NAME or params.get('arguments') != {'revision': revision}):
        raise ValueError('dynamic tool arguments do not match the selected fixture revision')
    result = subprocess.run([sys.executable, '-S', str(target / 'workflow.py'), str(project)],
                            cwd=project, env=env, capture_output=True, timeout=5)
    if result.returncode not in (0, 1) or len(result.stdout) > 65536:
        raise RuntimeError('synthetic workflow failed')
    value = decode(result.stdout)
    if value.get('revision') != revision or value.get('marker') != f'HG_TOOL_{revision}':
        raise RuntimeError('synthetic workflow returned a different revision')
    if result.returncode != (1 if value.get('missing') else 0):
        raise RuntimeError('synthetic workflow exit code contradicts its result')
    value['exit_code'] = result.returncode
    return {'contentItems': [{'type': 'inputText', 'text': json.dumps(value)}], 'success': True}


def check(name: str, passed: bool, detail: str, evidence: dict) -> dict:
    return {**Readiness('ready' if passed else 'invalid', name, detail).as_dict(),
            'evidence': evidence}


def observe_case(server: AppServer, fixture: ScriptedResponses, *, target: Path,
                 project: Path, env: dict[str, str], label: str,
                 expected_missing: list[str]) -> dict:
    revision = target.name
    server.request('skills/extraRoots/set', {'extraRoots': [str(target / 'skills')]})
    found = server.request('skills/list', {'cwds': [str(project)], 'forceReload': True})
    matches = [skill for row in found['data'] for skill in row['skills']
               if skill['name'] == SKILL_NAME and skill.get('enabled')]
    if len(matches) != 1 or Path(matches[0]['path']).resolve() != target / 'skills' / SKILL_NAME / 'SKILL.md':
        raise RuntimeError('selected fixture skill was not uniquely discovered')
    start = server.request('thread/start', {
        'model': MODEL, 'modelProvider': 'phase0', 'cwd': str(project),
        'approvalPolicy': 'never', 'sandbox': 'read-only',
        'developerInstructions': (target / 'memory.md').read_text(encoding='utf-8'),
        'dynamicTools': [{'type': 'function', 'name': TOOL_NAME,
                          'description': 'Run the fixed synthetic delivery workflow.',
                          'inputSchema': {'type': 'object', 'properties': {
                              'revision': {'type': 'string'}}, 'required': ['revision'],
                              'additionalProperties': False}}]})
    thread_id = start['thread']['id']
    first = len(fixture.requests)
    calls: list[dict] = []
    turn = server.request('turn/start', {'threadId': thread_id, 'input': [
        {'type': 'text', 'text': f'Synthetic {label} task: use the selected delivery skill.'},
        {'type': 'skill', 'name': SKILL_NAME, 'path': matches[0]['path']}]})
    turn_id = turn['turn']['id']

    def execute(params):
        if params.get('threadId') != thread_id or params.get('turnId') != turn_id:
            raise RuntimeError('tool request belongs to a different probe turn')
        result = run_workflow(target, project, env, params)
        calls.append({'params': params, 'result': result})
        return result

    completion = server.wait_turn(thread_id, turn_id, execute)
    requests = fixture.requests[first:]
    initial = message_text(requests[0]['payload']) if requests else ''
    outputs = tool_outputs(requests[-1]['payload']) if requests else ''
    completed_calls = [event['params']['item'] for event in server.events
                       if event.get('method') == 'item/completed'
                       and event['params'].get('threadId') == thread_id
                       and event['params']['item'].get('type') == 'dynamicToolCall']
    results = [decode(call['result']['contentItems'][0]['text']) for call in calls]
    return {'label': label, 'revision': revision, 'thread_id': thread_id, 'turn_id': turn_id,
            'turn_status': completion['status'], 'skill_path': matches[0]['path'],
            'memory_loaded': f'HG_MEMORY_{revision}' in initial,
            'skill_body_loaded': f'HG_SKILL_{revision}' in initial,
            'old_version_absent': f'HG_MEMORY_{"v2" if revision == "v1" else "v1"}' not in initial
                                  and f'HG_SKILL_{"v2" if revision == "v1" else "v1"}' not in initial,
            'tool_result_returned': f'HG_TOOL_{revision}' in outputs,
            'tool_completed': len(completed_calls) == 1 and completed_calls[0].get('success') is True,
            'workflow_behavior_matches': len(results) == 1 and results[0]['missing'] == expected_missing,
            'workflow_results': results,
            'request_indices': list(range(first, len(fixture.requests)))}


def app_argv(codex: Path) -> list[str]:
    return [str(codex), 'app-server', '--listen', 'stdio://', '--disable', 'apps',
            '--disable', 'multi_agent', '--disable', 'enable_request_compression']


def probe(codex: Path, output: Path, *, timeout: float = 90) -> dict:
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError('timeout must be positive and at most 300 seconds')
    codex = codex.expanduser().resolve(strict=True)
    output = output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('probe output must be outside the checkout')
    output.mkdir(parents=True, exist_ok=False)
    home, scratch, project, other = [output / name for name in ('home', 'tmp', 'project', 'other-project')]
    for path in (home, scratch, project, other):
        path.mkdir()
    env = child_environment(home, scratch, codex)
    binary_digest = digest(codex)
    version = subprocess.run([str(codex), '--version'], cwd=project, env=env,
                             capture_output=True, text=True, errors='strict', timeout=10, check=True).stdout.strip()
    if not version.startswith('codex-cli '):
        raise ValueError('selected executable did not identify as Codex CLI')
    manifest = {'files': ['result.md', 'checks.json'], 'marker': 'HG_SANDBOX_FIXTURE'}
    (project / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (project / 'result.md').write_text('Synthetic output.\n', encoding='utf-8')
    bundles = {name: bundle(output, name) for name in ('v1', 'v2')}
    inventories = []
    (output / 'inventories').mkdir()
    for target in bundles.values():
        path = output / 'inventories' / f'{target.name}.json'
        value = {'schema': 'harnessgrad.personal.codex-fixture-inventory.v1',
                 'revision': target.name, 'files': snapshot.collect(target)}
        # Diagnostic evidence in a new owned directory, not a production snapshot
        # publisher: core.snapshot.write currently requires POSIX os.fchmod.
        with path.open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        inventories.append({'path': path.relative_to(output).as_posix(), 'sha256': digest(path)})
    hook_log = output / 'hook-ran.txt'
    hook_script = output / 'untrusted_hook.py'
    hook_script.write_text('from pathlib import Path\n'
                           f'Path({str(hook_log)!r}).write_text("ran")\nprint("{{}}")\n', encoding='utf-8')
    hook_argv = [sys.executable, '-S', str(hook_script)]
    hook_command = subprocess.list2cmdline(hook_argv) if os.name == 'nt' else shlex.join(hook_argv)
    (home / 'hooks.json').write_text(json.dumps({'hooks': {'Stop': [{'hooks': [{
        'type': 'command', 'command': hook_command, 'async': True, 'timeout': 1}]}]}}), encoding='utf-8')
    calls = [{'name': TOOL_NAME, 'arguments': {'revision': revision}}
             for revision in ('v1', 'v2', 'v1')]
    cases: list[dict] = []
    server = None
    fixture = None
    deadline = time.monotonic() + timeout
    try:
        with ScriptedResponses(calls) as fixture:
            fixture_config(home, fixture.base_url)
            server = AppServer(app_argv(codex), cwd=project, env=env, deadline=deadline)
            initialization = server.initialize()
            if Path(initialization['codexHome']).resolve() != home:
                raise RuntimeError('app-server selected a different home')
            hooks = server.request('hooks/list', {'cwds': [str(project)]})
            for label, revision in [('initial', 'v1'), ('updated', 'v2'), ('restored', 'v1')]:
                if revision == 'v2':
                    (project / 'checks.json').write_text('{}\n', encoding='utf-8')
                cases.append(observe_case(server, fixture, target=bundles[revision],
                                          project=project, env=env, label=label,
                                          expected_missing=['checks.json'] if label == 'initial' else []))
            shared = server.request('skills/list', {'cwds': [str(other)], 'forceReload': True})
            extra_root_visible_elsewhere = any(skill['name'] == SKILL_NAME
                                               for row in shared['data'] for skill in row['skills'])
            other_home = output / 'other-home'
            other_home.mkdir()
            other_server = AppServer(app_argv(codex), cwd=other,
                                     env=child_environment(other_home, scratch, codex), deadline=deadline)
            try:
                other_server.initialize()
                isolated = other_server.request('skills/list', {'cwds': [str(other)], 'forceReload': True})
                isolated_profile = not any(skill['name'] == SKILL_NAME
                                            for row in isolated['data'] for skill in row['skills'])
            finally:
                other_server.close()
                (output / 'other-profile.jsonl').write_bytes(other_server.stdout)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError,
            KeyError, IndexError, TypeError) as exc:
        (output / 'failure.json').write_text(json.dumps({
            'schema': SCHEMA, 'status': 'invalid', 'host_version': version,
            'failure_type': type(exc).__name__, 'cases_completed': len(cases),
            'detail': 'Native probe did not complete; inspect the retained protocol evidence.'},
            indent=2) + '\n', encoding='utf-8')
        raise RuntimeError('native probe did not complete; partial evidence retained') from exc
    finally:
        if server is not None:
            server.close()
            (output / 'events.jsonl').write_bytes(server.stdout)
            (output / 'app-server.stderr').write_bytes(server.stderr)
        if fixture is not None:
            (output / 'requests.json').write_text(json.dumps(fixture.requests, ensure_ascii=False,
                                                            indent=2), encoding='utf-8')
    all_loaded = all(case['memory_loaded'] and case['skill_body_loaded']
                     and case['old_version_absent'] for case in cases)
    all_tools = all(case['tool_completed'] and case['tool_result_returned'] and case['workflow_behavior_matches']
                    and len(case['workflow_results']) == 1 for case in cases)
    completed = len(cases) == 3 and all(case['turn_status'] == 'completed' for case in cases)
    bundle_unchanged = all(snapshot.collect(target) == decode((output / row['path']).read_bytes())['files']
                           for target, row in zip(bundles.values(), inventories))
    capture_complete = not server.failure and not other_server.failure
    checks = [
        check('codex.complete_protocol_capture', capture_complete,
              'Both owned processes closed without malformed or truncated protocol capture.', {}),
        check('codex.controlled_task_events', completed, 'Three actual native turns completed; IDs retained.',
              {'turns': [case['turn_id'] for case in cases]}),
        check('codex.controller_post_turn_step', completed,
              'The owned controller chose each next fixture only after turn completion; no background learner installed.',
              {'order': [case['label'] for case in cases]}),
        check('codex.managed_memory_and_skill_consumption', all_loaded,
              'Managed memory and canonical explicit Skill bodies reached the local model requests.', {}),
        check('codex.dynamic_tool_workflow', all_tools,
              'Experimental dynamic tool invoked a fixed external fixture script and returned its result.', {}),
        check('codex.next_thread_update_and_restore', all_loaded and bundle_unchanged,
              'New controlled threads selected v1, v2, v1; immutable bundles retained. Not atomic adoption.', {}),
        {**Readiness('refused' if extra_root_visible_elsewhere else 'unchecked',
                     'codex.extra_roots_project_isolation',
                     'Extra roots are process-wide; do not share a profile server across projects.').as_dict(),
         'evidence': {'visible_in_other_cwd': extra_root_visible_elsewhere}},
        check('codex.separate_profile_process', isolated_profile,
              'A separate fresh app-server process did not discover the selected profile skill.', {}),
        {**Readiness('refused' if any(hook.get('trustStatus') == 'untrusted'
                                     for row in hooks['data'] for hook in row['hooks']) else 'unchecked',
                     'codex.native_hook_execution',
                     'No hook trust was granted or bypassed; discovery does not prove execution.').as_dict(),
         'evidence': {'discovery': hooks, 'hook_ran': hook_log.exists()}},
        Readiness('unchecked', 'codex.production_component_isolation',
                  'The external fixture bridge is trusted diagnostic code, not a production isolation backend.').as_dict(),
        Readiness('unchecked', 'codex.native_plugin_installation',
                  'Dynamic tool registration was measured; native plugin marketplace installation was not.').as_dict(),
        Readiness('unchecked', 'codex.shared_configuration_atomic_adoption',
                  'No daily configuration was changed; concurrent adoption belongs to later stages.').as_dict(),
        Readiness('unchecked' if hasattr(os, 'fchmod') else 'unavailable',
                  'core.snapshot_posix_writer',
                  'Fixture inventories reuse snapshot.collect; the POSIX writer is not used or emulated.').as_dict(),
    ]
    if fixture.errors or len(fixture.requests) != 6 or digest(codex) != binary_digest:
        raise RuntimeError('fixture budget, request or binary identity changed')
    required = (completed and all_loaded and all_tools and bundle_unchanged and isolated_profile
                and capture_complete and not hook_log.exists())
    report = {'schema': SCHEMA, 'status': 'measured' if required else 'invalid',
              'scope': 'owned app-server sessions with experimental dynamicTools and synthetic Responses',
              'host': {'version': version, 'executable_sha256': binary_digest,
                       'identity_scope': 'selected executable only',
                       'platform_family': initialization.get('platformFamily'),
                       'platform_os': initialization.get('platformOs'),
                       'probe_python': sys.version.split()[0]},
              'limits': {'protocol_deadline_seconds': timeout, 'scripted_request_limit': 6,
                         'workflow_timeout_seconds': 5, 'protocol_message_limit_bytes': MAX_MESSAGE_BYTES,
                         'transcript_limit_bytes': MAX_TRANSCRIPT_BYTES,
                         'model_route': 'owned IPv4 loopback only',
                         'production_resource_limits': 'unmeasured'},
              'model': {'provider': 'loopback-scripted', 'model': MODEL,
                        'base_url': fixture.base_url, 'real_model_calls': 0,
                        'scripted_requests': len(fixture.requests), 'usage_is_synthetic': True},
              'cases': cases, 'checks': checks, 'bundle_inventories': inventories,
              'capture': {'stdout_truncated': server.stdout_truncated,
                          'stderr_truncated': server.stderr_truncated},
              'evidence': {name: digest(output / name) for name in
                           ('events.jsonl', 'requests.json', 'app-server.stderr', 'other-profile.jsonl')},
              'output': str(output)}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', type=Path, required=True, help='explicit native Codex executable')
    parser.add_argument('--output', type=Path, help='new directory outside the checkout; existing paths refused')
    parser.add_argument('--timeout', type=float, default=90, help='native protocol deadline, in seconds')
    args = parser.parse_args(argv)
    output = args.output or Path(tempfile.mkdtemp(prefix='hg-personal-codex-')) / 'probe'
    try:
        report = probe(args.codex, output, timeout=args.timeout)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'personal Codex probe failed: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'measured' else 1


if __name__ == '__main__':
    raise SystemExit(main())
