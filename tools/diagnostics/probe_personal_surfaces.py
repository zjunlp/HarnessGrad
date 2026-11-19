#!/usr/bin/env python3
"""Measure AGENTS.md and standalone Skill loading in an owned Codex app-server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from tools.diagnostics import probe_personal_codex as host
from harnessgrad.personal.adapters.codex import CodexAdapter
from harnessgrad.personal.adapters.contracts import HostBinding, TargetSelection
from harnessgrad.personal.contracts import SurfaceRef


def probe(executable: Path, output: Path) -> dict:
    executable = executable.resolve(strict=True)
    output = output.resolve()
    if output.is_relative_to(host.ROOT):
        raise ValueError('probe output must be outside the checkout')
    output.mkdir(parents=True, exist_ok=False)
    home, temp, project = (output / name for name in ('home', 'tmp', 'project'))
    for path in (home, temp, project):
        path.mkdir()
    adapter = CodexAdapter()
    version = adapter.probe(executable).version
    if version is None:
        raise ValueError('expected an explicit Codex executable with a measured version')
    identity = host.digest(executable)
    env = host.child_environment(home, temp, executable)
    binding = HostBinding('probe', 'project', 'project', project, version, sys.platform, 'controlled')
    (project / 'manifest.json').write_text('{"files":[]}', encoding='utf-8')
    revisions = ('v1', 'v2')
    fixture_calls = [{'name': host.TOOL_NAME, 'arguments': {'revision': rev}} for rev in revisions]
    cases = []
    with host.ScriptedResponses(fixture_calls) as fixture:
        host.fixture_config(home, fixture.base_url)
        server = host.AppServer(host.app_argv(executable), cwd=project, env=env,
                                deadline=time.monotonic() + 90)
        try:
            initialization = server.initialize()
            if Path(initialization['codexHome']).resolve() != home:
                raise RuntimeError('app-server selected a different home')
            for rev in revisions:
                target = host.bundle(project, rev)
                path = target / 'skills' / host.SKILL_NAME
                instruction = project / 'AGENTS.md'
                instruction.write_text(f'HG_NATIVE_AGENTS_{rev}: Synthetic instruction.\n', encoding='utf-8')
                selected = (
                    TargetSelection('instruction', 'instruction', SurfaceRef('project', 'AGENTS.md')),
                    TargetSelection('skill', 'skill', SurfaceRef('project', path.relative_to(project).as_posix())),
                )
                frozen = adapter.collect_selected(binding, selected)
                first = len(fixture.requests)
                observed = host.observe_case(server, fixture, target=target, project=project,
                                             env=env, label=rev, expected_missing=[])
                model_prompt = host.message_text(fixture.requests[first]['payload'])
                other = next(value for value in revisions if value != rev)
                adapter.verify_selected(binding, frozen)
                cases.append({'revision': rev, 'thread_id': observed['thread_id'],
                              'turn_status': observed['turn_status'], 'snapshot_sha256': frozen.snapshot_sha256,
                              'instruction_loaded': f'HG_NATIVE_AGENTS_{rev}' in model_prompt,
                              'skill_loaded': observed['skill_body_loaded'],
                              'other_revision_absent': observed['old_version_absent'] and
                                  f'HG_NATIVE_AGENTS_{other}' not in model_prompt})
        finally:
            server.close()
            (output / 'events.jsonl').write_bytes(server.stdout)
            (output / 'app-server.stderr').write_bytes(server.stderr)
            (output / 'requests.json').write_text(json.dumps(fixture.requests, indent=2), encoding='utf-8')
    complete = (len(cases) == 2 and len(fixture.requests) == 4 and not fixture.errors
                and not server.stdout_truncated and not server.stderr_truncated
                and host.digest(executable) == identity
                and all(row['turn_status'] == 'completed' and row['instruction_loaded']
                        and row['skill_loaded'] and row['other_revision_absent'] for row in cases))
    report = {'status': 'measured' if complete else 'invalid', 'host_version': version,
              'executable_sha256': identity, 'platform': sys.platform, 'mode': 'owned app-server / fresh thread',
              'real_model_calls': 0, 'scripted_requests': len(fixture.requests), 'cases': cases}
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = probe(args.codex, args.output)
    except (OSError, RuntimeError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        print(f'Codex surface probe failed: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'measured' else 1


if __name__ == '__main__':
    raise SystemExit(main())
