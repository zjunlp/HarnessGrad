#!/usr/bin/env python3
"""Fetch pinned CostBench, prepare static cases, and execute the full gold audit.

No model is called. Run with the isolated requirements.lock interpreter.
"""
from __future__ import annotations
import argparse
import ast
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ASSETS = Path(__file__).resolve().parent
sys.path.insert(0, str(ASSETS / 'lib'))
from bootstrap import bootstrap, REVISION
ARCHIVE_SHA256 = 'b61cc4923662e63b0756a309aa2988487350036cecea93d674e3defc62404844'
QUERY_FILES = {'env/data/runtime/queries/queries.json': '2d15f439a22d55077d58b3d211129d403a8dcba55cbf4f7971f9de17b27253bb', 'env/data/runtime/queries/filtered_queries_with_requirements.json': '68f8fd8a6a2e87e564d94b2ed0333251ffd8536ab27fd78b9fafd568683274c7', 'env/data/runtime/train_queries/train/queries.json': 'ecae5294bd0b116d58a9731eb6aef29edeb9cc9dc860aecd17649a3a264bac9e', 'env/data/runtime/train_queries/train/filtered_queries_with_requirements.json': 'f42ec2f514584ac8e4d0ca2c965bbe76390cc130a088509647f625a254adccc9'}
DEFAULT_ROOT = Path(os.environ.get('HG_COSTBENCH_DATA', Path(__file__).resolve().parents[4] / 'datasets/costbench'))
PROTOCOL = {'refinement_level': 2, 'ban_longest_tool': True, 'min_atomic_cost': 15,
            'max_atomic_cost': 25, 'noise_std': 0.1, 'max_tool_steps': 20,
            'tool_creation_seed': 42, 'use_blocker': False}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    return digest(path)


def fetch(root):
    archive = root / 'source.tar.gz'
    if not archive.exists():
        urllib.request.urlretrieve(f'https://codeload.github.com/JiayuJeff/CostBench/tar.gz/{REVISION}', archive)
    if digest(archive) != ARCHIVE_SHA256:
        raise ValueError('upstream archive digest mismatch')
    source = root / f'CostBench-{REVISION}'
    if not source.exists():
        with tarfile.open(archive) as stream:
            stream.extractall(root, filter='data')
    for relative, expected in QUERY_FILES.items():
        if digest(source / relative) != expected:
            raise ValueError('released query digest mismatch: ' + relative)
    pins = json.loads((ASSETS / 'source-files.json').read_text())
    bundle = root / 'bundle'
    for relative, expected in pins.items():
        original = source / relative
        if digest(original) != expected:
            raise ValueError('source digest mismatch: ' + relative)
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    write_json(bundle / 'manifest.json', {'revision': REVISION, 'archive_sha256': ARCHIVE_SHA256, 'files': pins})
    return source, bundle


def official_prompt(bundle, task):
    # Read literal constants instead of importing the reference agent/provider SDK.
    names = {'QUERY_INSTRUCTION', 'COMPOSITE_CONCEPT_CONTENT', 'TASK_ATOMIC_TOOL_SEQUENCE_TEMPLATE', 'REFINEMENT_CONTENT_TEMPLATE'}
    literals = {}
    tree = ast.parse((bundle / 'env/run.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names:
            literals[node.targets[0].id] = ast.literal_eval(node.value)
    from env.utils.example_generator import example_for_refinement_level
    from env.domains.travel.refinement_steps_catalog import get_atomic_sequence, get_refinement_dimensions
    dimensions = get_refinement_dimensions(2)
    return literals['QUERY_INSTRUCTION'].format(
        task=task.capitalize(), tool_num_str=4,
        tool_types_str='Deciding Preference, Searching Candidates, Refining Options, Final Recommendation',
        refinement_content=literals['REFINEMENT_CONTENT_TEMPLATE'].format(task=task.capitalize(), num_dimensions=2,
                           refinement_dimensions_str=', '.join(dimensions[i] for i in range(1,3))),
        example_content=example_for_refinement_level(2),
        composite_concept_content=literals['COMPOSITE_CONCEPT_CONTENT'],
        task_atomic_tool_sequence_str=literals['TASK_ATOMIC_TOOL_SEQUENCE_TEMPLATE'].format(atomic_tool_sequence=get_atomic_sequence(2,task.capitalize())),
        need_loc_pref='LocationPreference, ' if task != 'location' else '')


def gold_arguments(tool, query, known):
    result = {}
    for typ in tool.input_types:
        if typ in known:
            result[typ] = known[typ]
        else:
            suffix = typ[len(query['task'].capitalize()):]
            key = {'Category':'category','Tier':'tier','Style':'style','FeaturePackage':'feature_package'}.get(suffix)
            if key is None:
                raise ValueError('oracle missing predecessor type: ' + typ)
            result[typ] = query['preferences'][key]
    return result


def gold_episode(config, bundle):
    from env.core.base_types import create_tool_from_dict
    from env.utils.eval import evaluate_single_query, eval as aggregate
    from scoring import train_answer
    tools = {n:create_tool_from_dict(d) for n,d in config['tools'].items() if config['query']['task'].capitalize() in n}
    query = copy.deepcopy(config['query'])
    known = {'TimeInfo':query['TimeInfo']}
    if query['task'] != 'location':
        known['LocationPreference'] = query['location_preference']
    history = [{'role':'system','content':official_prompt(bundle,query['task'])},
               {'role':'user','content':query['query']['input']}]
    actions = []
    for step in query['groundtruth']['scenarios'][0]['tools']:
        name = next(iter(step))
        tool = tools[name]
        arguments = gold_arguments(tool,query,known)
        tool_input = tool.process_input(json.dumps(arguments))
        output = tool.execute(input_data=tool_input,available_tools=tools)
        text = tool.process_output(output,refinement_level=2,available_tools=tools) + ' Cost: ' + str(tool.cost)
        if text.startswith(('[ERROR]','Tool execution failed','Error:')):
            return {'task_id':config['task_id'],'passed':False,'error':text}, None
        tokens = re.findall(r'<[^>]+>',text)
        if not tokens:
            raise ValueError('official output has no identifier')
        known[tool.output_type] = tokens[0]
        call_id = str(len(actions))
        history.extend([{'role':'assistant','content':None,'tool_calls':[{'id':call_id,'name':name,'arguments':json.dumps(arguments)}]},
                        {'role':'tool','tool_call_id':call_id,'name':name,'content':text}])
        actions.append({'name':name,'arguments':arguments})
    answer = known[query['goal_type']]
    history.append({'role':'assistant','content':'<answer> '+answer+' </answer>'})
    query.update(conversation_history=history,is_goal_state=True,block_stats={'expected_block_count':0,'block_count':0,'block_step':[]},run_args=PROTOCOL)
    score = evaluate_single_query(query)
    summary = aggregate([query])
    result = {'task_id':config['task_id'],'passed':bool(score['tool_path_exact_match']),
              'tcr':score['final_answer_correct'],
              'train_compat_tcr':(train_answer(query) == re.search(r'\d+',query['groundtruth']['scenarios'][0]['gt_id'])[0]) if config['split']=='train' else None,
              'agent_cost':score['agent_cost'],'gt_cost':score['gt_cost'],
              'normalized_edit_distance':score['normalized_edit_distance_single'],
              'cost_metrics':summary['cost_metrics'],'actions':actions,'answer':answer}
    return result, query




def audit_raw_training(source, bundle, root):
    """Execute every released train Decide action under unmodified test enums."""
    bootstrap(bundle,split='test')
    from env.domains.travel.atomic_tools import get_all_dynamic_atomic_tools
    from env.utils.eval import extract_candidate_id, extract_gt_id_number
    rows = json.loads((source / 'env/data/runtime/train_queries/train/queries.json').read_text())
    tools = get_all_dynamic_atomic_tools(2)
    results = []
    for query in rows:
        task = query['task'].capitalize()
        name = 'Decide_'+task+'_Preference'
        tool = tools[name]
        args = {task+''.join(part.capitalize() for part in key.split('_')):value for key,value in query['preferences'].items()}
        if task != 'Location':
            args['LocationPreference'] = query['location_preference']
        with contextlib.redirect_stdout(io.StringIO()):
            output = tool.execute(input_data=tool.process_input(json.dumps(args)),available_tools=tools)
        digits = extract_gt_id_number(query['groundtruth'])
        parsed = extract_candidate_id('<answer> <'+task+'Candidate'+digits+'> </answer>')
        results.append({'query_id':query['query_id'],'task':query['task'],'tool':name,
                        'error':output if isinstance(output,str) else None,'gt_digits':digits,
                        'scorer_extracted':parsed,'tcr_possible':parsed==digits})
    report = {'total':len(results),'runtime_failures':sum(bool(r['error']) for r in results),
              'tcr_possible':sum(r['tcr_possible'] for r in results),'results':results}
    write_json(root/'raw-train-audit.json',report)
    return {k:v for k,v in report.items() if k!='results'}

def shard_database(database):
    """Lossless physical partition of the official generator's lookup tables."""
    database = Path(database)
    index_path = database.parent / 'index.json'
    if index_path.exists():
        return index_path
    full = json.loads(database.read_text())
    index = {'schema':'costbench.search-index.v1','location_information':{}}
    def entry(name,value):
        return {'path':name,'sha256':write_json(database.parent/name,value)}
    index['locations'] = entry('locations.json',{'locations':full['locations'],'location_information':{}})
    index['empty'] = entry('empty.json',{'locations':{},'location_information':{}})
    for identifier, values in full['location_information'].items():
        name = 'shards/'+identifier.strip('<>')+'.json'
        index['location_information'][identifier] = entry(name,{'locations':{},'location_information':{identifier:values}})
    write_json(index_path,index)
    return index_path

def prepare(root, splits):
    source,bundle = fetch(root)
    bootstrap(bundle)
    import yaml
    from env.domains.travel.tool_registry import tools_ready_in_memory
    from env.core.base_types import create_tool_from_dict
    from env.utils.solver import solve_single_query_gt
    from env.domains.travel.generate_search_space import generate_search_space
    prepared = root / 'prepared-v1'
    manifest = {'revision':REVISION,'archive_sha256':ARCHIVE_SHA256,'protocol':PROTOCOL,'tasks':[], 'counts':{}}
    audit = []
    if 'train' in splits:
        manifest['raw_training_audit'] = audit_raw_training(source,bundle,root)
    for split in splits:
        database = root / 'search-spaces' / split / 'search_space.json'
        if not database.exists():
            previous = Path.cwd()
            os.chdir(source)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    generate_search_space(5000,5000,42,True,str(database.parent),
                        str(bundle / ('env/domains/travel/train_travel_config.yaml' if split=='train' else 'env/domains/travel/travel_config.yaml')),split)
            finally:
                os.chdir(previous)
        database = shard_database(database)
        database_sha = digest(database)
        config_path = prepared / f'{split}-runtime.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        bootstrap(bundle,split=split,search_space=database,config_path=config_path)
        relative = ('env/data/runtime/train_queries/train/queries.json' if split=='train' else 'env/data/runtime/queries/queries.json')
        queries = json.loads((source / relative).read_text())
        filtered_path = source / relative.replace('queries.json','filtered_queries_with_requirements.json')
        filtered = json.loads(filtered_path.read_text())
        if [{k:v for k,v in q.items() if k!='query'} for q in queries] != filtered:
            raise ValueError('execution queries differ from filtered release beyond query.input')
        if len(queries) != {'train':1912,'test':381}[split] or len({q['query_id'] for q in queries}) != len(queries):
            raise ValueError('released query inventory mismatch')
        manifest['counts'][split] = len(queries)
        manifest.setdefault('sources',{})[split] = {'path':relative,'sha256':digest(source/relative), 'search_space_sha256':database_sha, 'filtered_sha256':digest(filtered_path)}
        for index,query in enumerate(queries):
            task_id = 'costbench/'+split+'/'+query['query_id'].strip('<>')
            seed = int(hashlib.sha256(('42:'+query['query_id']).encode()).hexdigest(),16) % (2**31-1)
            snapshot = tools_ready_in_memory(refinement_level=2,min_atomic_cost=15,max_atomic_cost=25,noise_std=0.1,random_seed=seed,ban_longest_tool=True)
            tools = {n:create_tool_from_dict(d) for n,d in snapshot['tools'].items()}
            solved = solve_single_query_gt(copy.deepcopy(query),tools)
            private = {'task_id':task_id,'revision':REVISION,'split':split,'query':solved,'protocol':PROTOCOL,
                       'tool_seed':seed,'tools':snapshot['tools'],'search_space':str(database.relative_to(root)),'search_space_sha256':database_sha, 'filtered_sha256':digest(filtered_path)}
            public = {'query':query['query']['input'],'system_prompt':official_prompt(bundle,query['task']),
                      'tools':[t.generate_interface(provide_composite_concept=True) for t in tools.values() if query['task'].capitalize() in t.name]}
            result,episode = gold_episode(private,bundle)
            audit.append(result)
            private['oracle_actions'] = result.get('actions', [])
            private['oracle_answer'] = result.get('answer')
            path = 'tasks/'+split+'/'+query['query_id'].strip('<>')
            row = {'task_id':task_id,'path':path,'split':split,'domain':query['task'],
                   'public_sha256':write_json(prepared/path/'public.json',public),
                   'private_sha256':write_json(prepared/path/'private.json',private)}
            write_json(prepared/path/'oracle.json',result)
            manifest['tasks'].append(row)
            if index%50 == 0:
                print(json.dumps({'split':split,'completed':index+1,'total':len(queries),'passed':result['passed']}),flush=True)
    report = {'revision':REVISION,'protocol':PROTOCOL,'counts':manifest['counts'],'total':len(audit),
              'passed':sum(r['passed'] for r in audit),'tcr_passed':sum(r.get('tcr',False) for r in audit),'results':audit}
    write_json(root/'gold-audit.json',report)
    if (report['passed'] != report['total'] or any(
        not r.get('train_compat_tcr' if '/train/' in r['task_id'] else 'tcr',False)
        or abs(r.get('agent_cost',0)-r.get('gt_cost',1)) > 1e-8 for r in audit)):
        raise RuntimeError('gold audit failed; see '+str(root/'gold-audit.json'))
    manifest['gold_audit_sha256'] = digest(root/'gold-audit.json')
    write_json(prepared/'manifest.json',manifest)
    print(json.dumps({k:v for k,v in report.items() if k!='results'}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--splits',nargs='+',choices=['test','train'],default=['train','test'])
    parser.add_argument('--install',action='store_true',help='create isolated Python3.12 runtime with uv')
    args = parser.parse_args()
    if len(set(args.splits)) != len(args.splits):
        parser.error('--splits must not contain duplicates')
    args.root.mkdir(parents=True,exist_ok=True)
    if args.install:
        runtime = args.root/'runtime'
        if not (runtime/'bin/python').exists():
            subprocess.run(['uv','venv','--python','3.12',str(runtime)],check=True)
        subprocess.run(['uv','pip','install','--python',str(runtime/'bin/python'),'-r',str(ASSETS/'requirements.lock')],check=True)
        command = [str(runtime/'bin/python'),str(Path(__file__).resolve()),'--root',str(args.root),'--splits',*args.splits]
        return subprocess.call(command)
    prepare(args.root.resolve(),args.splits)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
