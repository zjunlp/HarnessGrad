"""Real CostBench service/scorer lifecycle on explicitly author-owned tiny inputs.

Offline only: requires a published execution artifact and original-pin source
bundle. Never reads released tasks, manifests, queries or search databases.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'data/datasets/costbench'
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def build(request):
    """Called only inside the exact admitted artifact interpreter."""
    from harnessgrad.dataset_runtime import admit_binding
    admit_binding(request['binding'], 'service', python=sys.executable)
    controller = runpy.run_path(str(ASSETS / 'prepare_controller.py'))
    if controller['source_hash']() != request['source_sha256']:
        raise ValueError('lifecycle control source changed before build')
    bundle, root = Path(request['bundle']), Path(request['root'])
    sys.meta_path.insert(0, controller['SourceGuard'](bundle))
    sys.path.insert(0, str(ASSETS / 'lib'))
    from bootstrap import bootstrap, REVISION
    bootstrap(bundle)
    from env.domains.travel import enums
    from env.domains.travel.atomic_tools import get_all_dynamic_atomic_tools
    from env.utils.solver import solve_single_query_gt
    preferences = {key: next(iter(getattr(enums, 'Location' + ''.join(part.capitalize() for part in key.split('_'))))).value
                   for key in ('category', 'tier', 'style', 'feature_package')}
    write(root / 'search.json', {'locations': {
        str(tuple(preferences[key] for key in sorted(preferences))): '<LocationPreference00001>'},
        'location_information': {}})
    bootstrap(bundle, search_space=root / 'search.json', config_path=root / 'build.yaml')
    tools = {name: tool for name, tool in get_all_dynamic_atomic_tools(2).items() if 'Location' in name}
    for tool in tools.values():
        tool.cost = 20
    query = solve_single_query_gt({
        'query_id': 'AuthorOwnedLifecycle-1', 'task': 'location', 'goal_type': 'TravelLocation',
        'preferences': preferences, 'TimeInfo': '<TimeInfo00001>',
        'groundtruth': '<LocationPreference00001>',
        'query': {'input': 'Author-owned one-location lifecycle control.'},
        'user_requirements': 'Author-owned control, not a released query.'}, tools)
    static = runpy.run_path(str(ASSETS / 'prepare_worker.py'))
    config = {'task_id': 'costbench/test/AuthorOwnedLifecycle', 'revision': REVISION, 'split': 'test',
              'query': query, 'protocol': dict(static['PROTOCOL']), 'tool_seed': 42,
              'tools': {name: {'name': name, 'type': 'atomic', 'input_types': tool.input_types,
                        'output_type': tool.output_type, 'cost': tool.cost, 'description': tool.description}
                        for name, tool in tools.items()},
              'search_space': 'search.json', 'search_space_sha256': sha(root / 'search.json')}
    public = {'query': query['query']['input'],
              'system_prompt': 'Author-owned native engine control; not a released benchmark task.',
              'tools': [tool.generate_interface(provide_composite_concept=True) for tool in tools.values()]}
    write(root / 'public.json', public)
    result, _ = static['gold_episode'](config, bundle)
    if not result['passed']:
        raise RuntimeError('author-owned static native oracle failed')
    write(root / 'static-private.json', config)
    write(root / 'static-script.json', {'actions': result['actions'], 'answer': result['answer']})
    write(root / 'author-queries.json', [query])
    dynamic = runpy.run_path(str(ASSETS.parent / 'costbench_dynamic/prepare_worker.py'))
    dynamic_config = dynamic['freeze'](config, 'cost_change', root / 'author-queries.json')
    result = dynamic['oracle'](dynamic_config, public)
    if not result['passed']:
        raise RuntimeError('author-owned dynamic native oracle failed')
    write(root / 'dynamic-private.json', dynamic_config)
    write(root / 'dynamic-script.json', {'actions': result['actions'], 'answer': result['answer']})
    admit_binding(request['binding'], 'service', python=sys.executable)
    if controller['source_hash']() != request['source_sha256']:
        raise ValueError('lifecycle control source changed during build')


def declarations(root, bundle, mode, service, verifier):
    case, public = root / (mode + '-private.json'), root / 'public.json'
    task_id = json.loads(case.read_text())['task_id']
    transport = ROOT / 'eval/service/tool_transport.py'
    read_paths = [ASSETS, transport, root, bundle]
    for relative in ('eval/__init__.py', 'eval/service/__init__.py'):
        if (ROOT / relative).is_file():
            read_paths.append(ROOT / relative)
    setup = {'tool_relay': {'program': str(ASSETS / 'relay.py'), 'python': service['python'],
        'python_runtime': service, 'read_paths': list(map(str, read_paths)),
        'content_flag': '--case-spec', 'filename': 'case-spec.json',
        'content': json.dumps({'case': str(case), 'sha256': sha(case), 'public': str(public),
                              'public_sha256': sha(public), 'bundle': str(bundle), 'dataset_root': str(root)}),
        'record': {'path': 'trusted_episode', 'max_bytes': None},
        'max_calls': None, 'deadline_s': None, 'max_line_bytes': None}}
    verify = {'kind': 'command', 'python_runtime': verifier, 'runtime_paths': verifier['runtime_paths'],
        'argv': [verifier['python'], '-I', '-B', 'costbench_code/verify/check.py',
                 '--task-id', task_id, '--case-sha256', sha(case)],
        'inputs': [{'path': 'costbench_code', 'from': str(ASSETS), 'immutable': True},
                   {'path': 'costbench_private.json', 'from': str(case)},
                   {'path': 'costbench_bundle', 'from': str(bundle), 'immutable': True}],
        'reward_file': '/verifier/reward.txt', 'evidence_file': '/verifier/evidence.json',
        'result_contract': {'version': 1, 'score_range': [0, 1], 'require_exit_zero': True},
        'trial_evidence': {'version': 1}}
    return task_id, setup, verify


def run_arm(root, bundle, mode, arm, service, verifier):
    from harnessgrad.dataset_runtime import admit_binding
    from eval.run import tool_relay
    from eval.service.tool_transport import call
    from eval.verification.verifier import _verify
    task_id, setup, verify = declarations(root, bundle, mode, service, verifier)
    admit_binding(service, 'service')
    relay = tool_relay.start(setup, context={'task_id': task_id, 'run_id': 'author-owned-lifecycle',
                                            'trial_id': mode + '-' + arm, 'seed': '42'})
    result = {'mode': mode, 'arm': arm, 'task_id': task_id}
    output = root / (mode + '-' + arm)
    output.mkdir()
    try:
        result['initial_status'] = call(relay.path, 'status', {})
        script = json.loads((root / (mode + '-script.json')).read_text())
        if mode == 'dynamic':
            result['initial_schema_count'] = len(call(relay.path, 'show', {})['tools'])
        if arm == 'oracle':
            for action in script['actions']:
                response = call(relay.path, 'turn', {'content': None, 'calls': [action]}, max_line_bytes=None)
                if any(row['content'].startswith(('Tool execution failed', 'Error:')) for row in response['results']):
                    raise RuntimeError('native authored action failed')
        elif arm == 'invalid':
            response = call(relay.path, 'turn', {'content': None, 'calls': [
                {'name': 'AuthorOwnedMissingTool', 'arguments': {}}]}, max_line_bytes=None)
            if not response['results'][0]['content'].startswith('[ERROR]'):
                raise RuntimeError('invalid tool was not rejected')
        result['final_status'] = call(relay.path, 'turn', {
            'content': '<answer> ' + (script['answer'] if arm == 'oracle' else '<LocationCandidate99999>') + ' </answer>',
            'calls': []}, max_line_bytes=None)
        relay.stop()
        if relay.fault():
            raise RuntimeError(relay.fault())
        recorded = relay.recording_input()
        sealed = Path(recorded['from']) / 'episode.json'
        episode = json.loads(sealed.read_text())
        write(output / 'sealed-episode.json', episode)
        result['block_count'] = episode['payload']['query_result']['block_stats']['block_count_agent']
        result['service_receipt'] = relay.receipt
        admit_binding(verifier, 'verifier')
        verify['inputs'].append(recorded)
        result['verdict'] = _verify(output, verify, '', sandbox=True, timeout_s=60,
                                    env={'kind': 'files', 'network': 'none'}, task_id=task_id)
        admit_binding(verifier, 'verifier')
        expected = 1.0 if arm == 'oracle' else 0.0
        if result['verdict'].get('score') != expected:
            raise RuntimeError('authored control did not measure expected score: ' + str(result['verdict']))
        if mode == 'dynamic' and arm == 'oracle' and result['block_count'] != 1:
            raise RuntimeError('dynamic oracle never applied its real intervention')
    finally:
        relay.cleanup()
        result['relay_report'] = relay.report()
        result['service_process_exited'] = relay.proc.poll() is not None
        result['relay_directory_removed'] = not relay.directory.exists()
        write(output / 'control.json', result)
    if not result['service_process_exited'] or not result['relay_directory_removed']:
        raise RuntimeError('relay cleanup incomplete')
    return result


def run(runtime_root, bundle, output):
    from harnessgrad.dataset_runtime import resolve_execution, bind, admit_binding
    controller = runpy.run_path(str(ASSETS / 'prepare_controller.py'))
    fingerprint = controller['source_hash']()
    runtime = resolve_execution('costbench', root=runtime_root)
    service, verifier = bind(runtime, 'service'), bind(runtime, 'verifier')
    # Native verifier runtimes cannot live under its private /tmp mount.
    if Path(runtime['path']).is_relative_to('/tmp'):
        raise ValueError('CostBench lifecycle verifier requires an explicitly prepared runtime outside /tmp')
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('lifecycle control output must be outside the installed code tree')
    output.mkdir(parents=True, exist_ok=False)
    request = {'binding': service, 'source_sha256': fingerprint, 'bundle': str(bundle), 'root': str(output)}
    request_path = output / 'build-request.json'
    write(request_path, request)
    admit_binding(service, 'service')
    environment = {'HOME': str(output), 'PYTHON_DOTENV_DISABLED': '1'}
    command = [service['python'], '-I', '-B', str(Path(__file__).resolve()), '--build-request', str(request_path)]
    with (output / 'build.log').open('w') as log:
        built = subprocess.run(command, env=environment, cwd=output, stdout=log, stderr=subprocess.STDOUT, timeout=60)
    if built.returncode:
        raise RuntimeError('author-owned native builder failed; see retained build.log')
    admit_binding(service, 'service')
    if controller['source_hash']() != fingerprint:
        raise ValueError('lifecycle source changed before launch')
    results = [run_arm(output, bundle, mode, arm, service, verifier)
               for mode in ('static', 'dynamic') for arm in ('oracle', 'noop', 'invalid')]
    admit_binding(service, 'service')
    admit_binding(verifier, 'verifier')
    if controller['source_hash']() != fingerprint:
        raise ValueError('lifecycle source changed before result consumption')
    receipt = {'schema': 'hg-costbench-author-owned-lifecycle-v1', 'author_owned_only': True,
               'released_cases_read': False, 'model_calls': 0, 'source_sha256': fingerprint,
               'service_binding': service, 'verifier_binding': verifier,
               'bundle_pins_sha256': sha(ASSETS / 'source-files.json'),
               'dynamic_modes_tested': ['cost_change'], 'results': results}
    write(output / 'lifecycle-receipt.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--build-request', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.build_request:
        build(json.loads(args.build_request.read_text()))
        return 0
    if not all((args.runtime_root, args.bundle, args.output)):
        parser.error('--runtime-root, --bundle and fresh --output are required')
    result = run(args.runtime_root, args.bundle, args.output)
    print(json.dumps({'schema': result['schema'], 'controls': len(result['results']),
                      'receipt': str(args.output / 'lifecycle-receipt.json')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
