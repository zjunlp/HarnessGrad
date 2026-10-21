#!/usr/bin/env python3
"""Freeze four one-event CostBench protocols and audit their official gold paths.

Requires the pinned static preparation. No model/API calls and no static files
are rewritten. --smoke writes a separate 48-case diagnostic, never the catalog.
"""
from __future__ import annotations
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import tempfile

ASSETS = Path(__file__).resolve().parent.parent / 'costbench'
sys.path.insert(0, str(ASSETS / 'lib'))
from bootstrap import bootstrap, REVISION
from dynamic_runtime import DynamicCostBenchRuntime

MODES = ('cost_change', 'steplen_change', 'ban_tool', 'preference_change')
STATIC_MANIFEST = '7bcc4ba0bc653229b5536f625d797217b3b48f31764314d9d2a397e2e8332587'
ARCHIVE_SHA256 = 'b61cc4923662e63b0756a309aa2988487350036cecea93d674e3defc62404844'
_spec = importlib.util.spec_from_file_location('costbench_static_prepare', ASSETS / 'prepare.py')
static = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(static)


def read_checked(path, expected):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('CostBench input digest mismatch: ' + str(path))
    return json.loads(raw)


def freeze(case, mode, queries_path):
    from env.core.base_types import create_tool_from_dict
    from env.settings import load_config
    from env.utils.blocker import create_blocker
    from env.utils.solver import GroundtruthSolver
    config = copy.deepcopy(case)
    config.pop('oracle_actions', None)
    config.pop('oracle_answer', None)
    config['task_id'] = 'costbench_dynamic/' + mode + '/' + '/'.join(case['task_id'].split('/')[1:])
    config['protocol'] = {**case['protocol'], 'use_blocker': True, 'block_mode': mode,
                          'block_num': 1, 'control_tool_length': False, 'max_tool_length': 8}
    query = config['query']
    query['tool_creation_seed'] = config['tool_seed']
    defaults = load_config()
    blocker = create_blocker(block_mode=mode, block_num=1,
                             seed_range=defaults.random.seed_range, queries_path=str(queries_path),
                             refinement_level=2, output_dir='.')
    tools = {name: create_tool_from_dict({**spec, 'name': name}) for name, spec in config['tools'].items()}
    blocker.set_solver(GroundtruthSolver(tools_with_costs=tools))
    blocker.filter_options = {name: config['protocol'][name] for name in (
        'control_tool_length', 'max_tool_length', 'ban_longest_tool',
        'min_atomic_cost', 'max_atomic_cost', 'noise_std')}
    config['query'] = blocker.initialize_blocking_plan(query)
    if mode == 'preference_change':
        params = config['query']['blocking_plan'][0]['parameters']
        # Upstream silently invents defaults when query loading fails. Freeze
        # only a real, same-split released replacement, never that fallback.
        pool = json.loads(Path(queries_path).read_text())
        candidates = [q for q in pool if q['task'] == query['task'] and q['query_id'] != query['query_id']]
        if not any(q['preferences'] == params['preferences']
                   and q['user_requirements'] == params['user_requirement']
                   and (query['task'] == 'location' or q['location_preference'] == params['location'])
                   for q in candidates):
            raise ValueError('preference replacement is not a same-split released query')
    return config


def oracle(config, public):
    """Control follows independent GT prefixes/suffixes; never exposed to actor."""
    from dynamic_scoring import score
    runtime = DynamicCostBenchRuntime({**config, 'seed': '42', 'case_sha256': 'preparation'}, public)
    query = copy.deepcopy(config['query'])
    known = {'TimeInfo': query['TimeInfo']}
    if query['task'] != 'location':
        known['LocationPreference'] = query['location_preference']
    cursor = 0
    actions = []
    for _ in range(config['protocol']['max_tool_steps'] - 1):
        if runtime.final_type in runtime.state:
            break
        scenario = runtime.query['groundtruth']['scenarios'][runtime.blocker.blocked_count]
        if cursor >= len(scenario['tools']):
            raise RuntimeError('oracle exhausted its reference suffix before reaching goal')
        name = next(iter(scenario['tools'][cursor]))
        tool = runtime.tools[name]
        arguments = static.gold_arguments(tool, query, known)
        action = {'name': name, 'arguments': arguments}
        before = runtime.blocker.blocked_count
        result = runtime.turn(calls=[action])
        actions.append(action)
        cursor += 1
        text = result['results'][0]['content']
        changed = runtime.blocker.blocked_count != before
        blocked_attempt = changed and config['protocol']['block_mode'] == 'ban_tool'
        if not blocked_attempt:
            if text.startswith(('[ERROR]', 'Tool execution failed', 'Error:')):
                raise RuntimeError('oracle tool execution failed: ' + text)
            tokens = re.findall(r'<[^>]+>', text)
            if not tokens:
                raise RuntimeError('oracle tool output lacks an identifier')
            known[tool.output_type] = tokens[0]
        if changed:
            cursor = 0
            if config['protocol']['block_mode'] == 'preference_change':
                params = runtime.query['block_stats']['metadata'][0]['parameters']
                query['preferences'] = params['preferences']
                known = {'TimeInfo': query['TimeInfo']}
                if query['task'] != 'location':
                    query['location_preference'] = params['location']
                    known['LocationPreference'] = params['location']
    if runtime.final_type not in runtime.state or runtime.blocker.blocked_count != 1:
        raise RuntimeError('oracle did not complete the dynamic episode')
    answer = known[runtime.final_type]
    runtime.turn(content='<answer> ' + answer + ' </answer>')
    payload = runtime.episode()
    measured = score(config, payload)
    report = measured['official']
    result = report['per_query'][0]
    correct = (report['train_compatibility']['per_query'][0]['correct'] if config['split'] == 'train'
               else result['final_answer_correct'])
    passed = (measured['reward'] == 1 and correct and not result['no_goal_state']
              and abs(result['agent_cost'] - result['gt_cost']) < 1e-8
              and result['normalized_edit_distance_single'] == 0)
    return {'task_id': config['task_id'], 'passed': bool(passed),
            'answer_correct': bool(correct), 'agent_cost': result['agent_cost'],
            'gt_cost': result['gt_cost'], 'path_exact_match': result['tool_path_exact_match'],
            'block_count': payload['query_result']['block_stats']['block_count_agent'],
            'actions': actions, 'answer': answer}


def prepare(root, smoke=False):
    manifest = read_checked(root / 'prepared-v1/manifest.json', STATIC_MANIFEST)
    if manifest['counts'] != {'train': 1912, 'test': 381}:
        raise ValueError('static inventory changed')
    output = root / ('dynamic-smoke-v1' if smoke else 'dynamic-prepared-v1')
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'manifest.json').exists():
        raise ValueError('prepared output already exists; preserve it and choose a fresh root')
    tasks = []
    audit = []
    sources = {}
    with tempfile.TemporaryDirectory(prefix='hg-costbench-dynamic-prepare-') as scratch:
        for split in ('train', 'test'):
            source = root / ('CostBench-' + REVISION) / manifest['sources'][split]['path']
            read_checked(source, manifest['sources'][split]['sha256'])
            sources[split] = copy.deepcopy(manifest['sources'][split])
            rows = [row for row in manifest['tasks'] if row['split'] == split]
            if smoke:
                rows = [next(row for row in rows if row['domain'] == domain) for domain in
                        ('location', 'transportation', 'accommodation', 'attraction', 'dining', 'shopping')]
            search = root / 'search-spaces' / split / 'index.json'
            read_checked(search, manifest['sources'][split]['search_space_sha256'])
            bootstrap(root / 'bundle', split=split, search_space=search,
                      config_path=Path(scratch) / (split + '.yaml'))
            for mode in MODES:
                for index, row in enumerate(rows):
                    folder = root / 'prepared-v1' / row['path']
                    case = read_checked(folder / 'private.json', row['private_sha256'])
                    public = read_checked(folder / 'public.json', row['public_sha256'])
                    with contextlib.redirect_stdout(io.StringIO()):
                        config = freeze(case, mode, source)
                        result = oracle(config, public)
                    audit.append(result)
                    if not result['passed']:
                        static.write_json(output / 'failed-oracle.json', result)
                        raise RuntimeError('dynamic oracle failed: ' + result['task_id'])
                    config['oracle_actions'] = result['actions']
                    config['oracle_answer'] = result['answer']
                    relative = 'tasks/' + mode + '/' + '/'.join(case['task_id'].split('/')[1:])
                    tasks.append({'task_id': config['task_id'], 'path': relative, 'mode': mode,
                                  'split': split, 'domain': row['domain'],
                                  'public_sha256': static.write_json(output / relative / 'public.json', public),
                                  'private_sha256': static.write_json(output / relative / 'private.json', config)})
                    static.write_json(output / relative / 'oracle.json', result)
                    if index % 50 == 0:
                        print(json.dumps({'split': split, 'mode': mode, 'completed': index + 1,
                                          'total': len(rows)}), flush=True)
    report = {'revision': REVISION, 'smoke': smoke, 'total': len(audit),
              'passed': sum(r['passed'] for r in audit), 'results': audit}
    audit_digest = static.write_json(output / 'gold-audit.json', report)
    final = {'revision': REVISION, 'archive_sha256': ARCHIVE_SHA256, 'modes': list(MODES),
             'static_manifest_sha256': STATIC_MANIFEST, 'sources': sources,
             'counts': {'train': 6 if smoke else 1912, 'test': 6 if smoke else 381},
             'gold_audit_sha256': audit_digest, 'tasks': tasks}
    manifest_digest = static.write_json(output / 'manifest.json', final)
    print(json.dumps({'total': len(tasks), 'passed': report['passed'], 'manifest_sha256': manifest_digest}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    prepare(args.root.resolve(), args.smoke)


if __name__ == '__main__':
    main()
