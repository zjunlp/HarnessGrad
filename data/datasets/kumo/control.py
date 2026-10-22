#!/usr/bin/env python3
"""Oracle/noop controls that exercise KUMO's real private relay and verifier."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.datasets import kumo
from data.scaffolds.command_control import materialize, solve, run_check
from eval.run import tool_relay
from eval.run.runner import run_one
from eval.service.tool_transport import call


def run(oracle, platform, task_id, work):
    task = kumo.control_task(task_id)
    setup = copy.deepcopy(kumo.SETUP[task_id])
    if platform:
        harness = work / 'harness'
        shutil.copytree(kumo.ASSETS / 'oracle_harness', harness)
        manifest = json.loads((harness / 'harness.json').read_text())
        manifest['runtime_paths'] = list(dict.fromkeys([sys.prefix, sys.base_prefix, str(Path(sys._base_executable).parent.parent)]))
        (harness / 'harness.json').write_text(json.dumps(manifest))
        if oracle:
            setup['files'] += [{'path': '.oracle/' + name, 'content': content}
                               for name, content in kumo.oracle_files(task).items()]
        os.environ['HARNESSGRAD_WORK_ROOT'] = str(work)
        result = run_one(repo=harness, task=task, setup=setup, verifier=kumo.VERIFY[task_id],
                         env=kumo.ENV[task_id], sandbox=True,
                         run_id='kumo-control-' + uuid.uuid4().hex[:10], run_seed='0')
        verdict = result.get('verdict') or {}
        score = (verdict.get('score') if result.get('exit_code') == 0 else None)
        detail = {'score': score, 'exit_code': result.get('exit_code'),
                  'unmeasured': verdict.get('unmeasured'), 'invalid': result.get('invalid'),
                  'detail': verdict.get('detail'), 'stderr': result.get('stderr')}
    else:
        materialize(kumo, task, work)
        relay = tool_relay.start(setup)
        try:
            public = json.loads((work / 'kumo_public.json').read_text())
            if oracle:
                call(relay.path, 'do', {'action': public['actions'][0]})
            relay.stop()
            if relay.fault():
                raise RuntimeError(relay.fault())
            if oracle:
                solve(kumo, task, work)
            score, detail = run_check(kumo, task, work)
        finally:
            relay.stop()
            relay.cleanup()
    print(json.dumps({'task_id': task_id, 'oracle': oracle, 'platform': platform,
                      'score': score, 'detail': detail}, ensure_ascii=False))
    print(f"reward {score:g}" if score is not None and score >= 0 else "CONTROL UNMEASURED")
    return 0 if score == float(oracle) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--oracle', action='store_true')
    mode.add_argument('--noop', action='store_true')
    parser.add_argument('--platform', action='store_true')
    parser.add_argument('--task-id', default='kumo/easy/AlgorithmEnv/0')
    args = parser.parse_args()
    # Controls never consume the user-configured model provider.
    os.environ['HG_AGENT_BACKEND'] = 'mock'
    with tempfile.TemporaryDirectory(prefix='kumo-control-') as directory:
        return run(args.oracle, args.platform, args.task_id, Path(directory))


if __name__ == '__main__':
    raise SystemExit(main())
