"""Cold stdlib platform, authored loader bytes, real selected sandbox controls.

These are transport/metric controls, not released benchmark measurements. Small
count pins are injected only in the child test process; production pins stay intact.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import shutil
import venv

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('aime', 'bbeh', 'fever', 'musique', 'physicsquestions',
         'sportsunderstanding', 'strategyqa', 'twowikimultihopqa',
         'bfcl_v3', 'bfcl_v3_irrelevance', 'fever_evidence', 'nq', 'searchqa', 'supergpqa', 'authored_example_fix_bug')


@pytest.fixture(scope='module')
def cold_stdlib(tmp_path_factory):
    root = Path(tempfile.mkdtemp(prefix='hg-stdlib-platform-', dir=Path.home() / '.cache'))
    try:
        venv.EnvBuilder(with_pip=False, system_site_packages=False).create(root / 'venv')
        yield root / 'venv/bin/python'
    finally:
        shutil.rmtree(root)


@pytest.mark.parametrize('name', NAMES)
def test_selected_loader_and_private_native_sandbox_controls(cold_stdlib, tmp_path, name):
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(('HG_', 'HARNESSGRAD_', 'PYTHON'))}
    environment.update(HG_REVIEWED_ENVIRONMENT_ONLY='1', HG_DATA_ROOT=str(tmp_path / 'payload'),
                       HARNESSGRAD_WORK_ROOT=str(tmp_path / 'work'), HG_AGENT_BACKEND='mock',
                       PATH=str(cold_stdlib.parent) + os.pathsep + os.defpath,
                       PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([str(cold_stdlib), '-I', '-B', '-c', CONTROL,
                             str(ROOT), name, str(tmp_path)], env=environment,
                            cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'measured controls: 1.0, 0.0' in result.stdout


CONTROL = r'''
import copy, csv, hashlib, importlib, importlib.util, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
scenario, directory = sys.argv[2], Path(sys.argv[3])
name={'bfcl_v3_irrelevance':'bfcl_v3','fever_evidence':'fever'}.get(scenario,scenario)
for dependency in ('pip','pytest','pyarrow','datasets','numpy','pandas','requests','yaml','httpx','openai'):
    assert importlib.util.find_spec(dependency) is None, dependency
from data.scaffolds import registry
from eval.ckpt.git_state import stage_workspace
from eval.run.runner import run_one

def write(path, value, lines=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(('\n'.join(json.dumps(r) for r in value)+'\n') if lines else json.dumps(value))

module = importlib.import_module(registry.module_name(name))
root = Path(module.DATASET_ROOT) if name != 'authored_example_fix_bug' else None
if name in ('bbeh','nq','searchqa','supergpqa'):
    from data.scaffolds.native_qa import CATALOG
    from data.scaffolds.native_qa.prepare import prepare
    rows = {
      'bbeh': {'task':'boolean_expressions','input':'Authored boolean request.','target':'True'},
      'searchqa': {'id':'authored','question':'Authored color request.','context':'Public context.','answer':'VIOLET_PRIVATE'},
      'nq': {'id':'authored','question':{'text':'Authored yes/no request.'},'document':{'tokens':{'token':['Public'],'is_html':[False]}},'annotations':{'short_answers':[{'start_token':[],'end_token':[]}],'yes_no_answer':[1]}},
      'supergpqa': {'uuid':'authored','question':'Authored choice request.','options':['Public first','Public second'],'answer_letter':'A','answer':'Public first'}}
    local=[]
    for split in CATALOG[name]['splits']:
        path=directory/(split+'.jsonl');write(path,[rows[name]],True);local.append(split+'='+str(path))
    prepare(name, root, local)
    answer={'bbeh':'True','searchqa':'VIOLET_PRIVATE','nq':'yes','supergpqa':'A'}[name]
elif name == 'aime':
    module.N_TOTAL=3;module.N_BY_MEMBER={Path(p).name:1 for p in module.JSONL_MEMBERS}
    for p in module.JSONL_MEMBERS: write(root/module.EXTRACT_PREFIX/p,[{'problem':'Authored integer request.','answer':'683'}],True)
    answer='683'
elif name in ('physicsquestions','sportsunderstanding','strategyqa'):
    module.N_TOTAL=1
    answer={'physicsquestions':'violet_private','sportsunderstanding':'plausible','strategyqa':'Yes'}[name]
    path=root/module.CSV_PATH;path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['input','target']);writer.writeheader();writer.writerow({'input':'Authored request.','target':('Can be one of [\"violet_private\"]' if name=='physicsquestions' else answer)})
elif name == 'fever':
    write(root/module.VALID,[{'claim':'Authored claim.','label':'NOT ENOUGH INFO','evidence':[]}],True)
    answer='NOT ENOUGH INFO'
    if scenario=='fever_evidence':
        write(root/module.VALID,[{'claim':'Authored claim.','label':'SUPPORTS','evidence':[['Public page',3,'Public sentence']]}],True)
        answer=json.dumps({'schema_version':1,'answer':'SUPPORTS','predicted_evidence':[['Public page',3]]})
elif name == 'musique':
    # This loader's count is deliberately literal: exercise it with authored rows.
    rows=[{'id':str(i),'question':'Authored question.','answerable':True,'answer':'VIOLET_PRIVATE','answer_aliases':['PRIVATE_ALIAS'],
           'paragraphs':[{'idx':7,'title':'Public title','paragraph_text':'Public context.','is_supporting':True}]} for i in range(2417)]
    write(root/module.DATA_FILE,rows,True)
    answer=json.dumps({'schema_version':1,'answer':'VIOLET_PRIVATE','predicted_evidence':[['Public title',7]]})
elif name == 'twowikimultihopqa':
    module.EXPECTED_ROWS=1
    write(root/module.DATA_FILE,[{'_id':'authored','question':'Authored question.','answer':'VIOLET_PRIVATE','context':[['Public title',['Public context.']]],
          'answer_id':'Q1','supporting_facts':[['Public title',0]],'evidences':[['Subject','relation','Object']],
          'evidences_id':[['Q2','relation','Q3']],'type':'comparison'}])
    write(root/module.ALIAS_FILE,[{'Q_id':'Q1','aliases':['PRIVATE_ALIAS'],'demonyms':[]}],True)
    answer=json.dumps({'schema_version':1,'answer':'VIOLET_PRIVATE','supporting_facts':[['Public title',0]],'evidence':[['Subject','relation','Object']]})
elif name == 'bfcl_v3':
    module.N_SIMPLE=module.N_IRRELEVANCE=1
    row={'id':'authored','question':[[{'role':'user','content':'Authored lookup request.'}]],
         'function':[{'name':'lookup','description':'Public function.','parameters':{'type':'dict','properties':{'x':{'type':'string'}},'required':['x']}}]}
    write(root/module.SIMPLE,[row],True)
    write(root/module.SIMPLE_ANSWERS,[{'id':'authored','ground_truth':[{'lookup':{'x':['PRIVATE_ARGUMENT']}}]}],True)
    write(root/module.IRRELEVANCE,[{**row,'id':'authored-irrelevant'}],True)
    # Preserve the adapter's declared name-set proxy, not upstream AST scoring.
    answer='lookup'
else:
    answer=None

# Fixture construction follows metadata import; discard its missing-payload cache.
if name in ('aime','fever','physicsquestions','sportsunderstanding','strategyqa','twowikimultihopqa'):
    module._SPEC.load_error=None;module._SPEC._load_cause=None
    built=module._SPEC.build()
    for key in ('ENV','SPLIT','OFFICIAL_EVAL','SPLIT_SOURCE'): setattr(module,key,built[key])

# Selected registry declarations are the same interfaces used by normal runs.
if name == 'bfcl_v3':
    selected='irrelevance/0' if scenario=='bfcl_v3_irrelevance' else 'simple/0'
    if scenario=='bfcl_v3_irrelevance': answer='NONE'
else:
    tasks, _ = registry.load(name);selected=tasks[0]['task_id']
tasks, setups, verifiers=registry.load_tasks(name,task_ids=[selected])
assert len(tasks)==1 and set(setups)==set(verifiers)=={selected}
assert set(registry.load_envs(name,task_ids=[selected]))=={selected}
public=json.dumps(tasks)+json.dumps(setups)
assert 'PRIVATE_' not in public and 'VIOLET_PRIVATE' not in public
spec=verifiers[selected]
from harnessgrad import verification_context
context=verification_context.capture(tasks, {}, verifiers=verifiers)
assert context==verification_context.capture(tasks, {}, verifiers=verifiers)
assert 'PRIVATE_' not in json.dumps(context)
# Changes to the private grader declaration must invalidate cached measurement.
changed=copy.deepcopy(spec)
changed.setdefault('inputs',[]).append({'path':'synthetic-source.py','content':'# different authored scorer source\n'})
other=verification_context.capture(tasks, {}, verifiers={selected:changed})
assert verification_context.problems(context,other)
from harnessgrad.loops.mode_a import _identical_prior
point={'identity':{'harness_sha':'authored'},'sampling':{'trials':1},'verification_context':context}
assert _identical_prior([point],'authored',1,verification_context=context) is point
assert _identical_prior([point],'authored',1,verification_context=other) is None
if name != 'authored_example_fix_bug':
    gold=next(item['content'] for item in spec['inputs'] if item['path']=='gold.json')
    assert hashlib.sha256(gold.encode()).hexdigest()==spec['scorer_input']['sha256']

for correct in (True,False):
    source=directory/('source-'+str(correct));source.mkdir()
    (source/'harness.json').write_text(json.dumps({'name':'stdlib-control','version':'1','entrypoint':'agent.py','env_kinds':['files']}))
    body="""import argparse,json,importlib.util
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args();w=Path(a.workdir)
assert not (w/'gold.json').exists()
assert 'PRIVATE_' not in Path(a.task).read_text()
for package in ('pyarrow','numpy','datasets','openai'): assert importlib.util.find_spec(package) is None
"""
    if name == 'authored_example_fix_bug':
        if correct: body+="(w/'broken.py').write_text('def add(a, b): return a + b\\n')\n"
    else:
        prediction=answer if correct else 'DELIBERATELY_WRONG_CONTROL'
        target='prediction.json' if (name=='musique' or scenario=='fever_evidence') and correct else 'answer.txt'
        body+=f"(w/{target!r}).write_text({prediction!r})\n"
        body+="(w/'gold.json').write_text('{\"answer\":\"TAMPERED\"}')\n"
    body+="(w/'trace.jsonl').write_text(json.dumps({'usage':{'calls':0,'input_tokens':0,'output_tokens':0}})+'\\n')\n"
    (source/'agent.py').write_text(body)
    repo=directory/'work'/('control-'+str(correct))/'workspace';stage_workspace(source,repo)
    result=run_one(repo,tasks[0],sandbox=True,setup=setups[selected],verifier=spec,timeout_s=30,verify_timeout_s=30,run_id='stdlib-control')
    assert not result.get('invalid'),result
    assert result['exit_code']==0,result
    verdict=result['verdict'];assert verdict['score']==float(correct),verdict
    assert not verdict.get('unmeasured'),verdict
    if name != 'authored_example_fix_bug':
        assert verdict['evidence']['measured'] is True,verdict
        assert 'TAMPERED' not in json.dumps(verdict['evidence'])
print('measured controls: 1.0, 0.0')
'''
