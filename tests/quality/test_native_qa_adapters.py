"""Native scorer fidelity and real private-verifier transport for upstream QA ports.

Fixtures below are authored protocol controls, not downloaded benchmark samples.
They do not authorize a benchmark H0 or establish dataset capability.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from data.scaffolds.native_qa import CATALOG, Dataset, parse_row
from data.scaffolds.native_qa.prepare import prepare
from data.scaffolds.native_qa.scoring import score
from data.scaffolds.native_qa import bbeh_official, metrics, office_reward
from eval.ckpt.git_state import stage_workspace
from eval.run.runner import run_one
from harnessgrad.config.areas import ConfigMissing


def source_row(name, split='test'):
    if name=='searchqa': return {'id':f'{split}-id','question':'Which token?','search_results':{'snippets':['The token is VIOLET.',None]},'answer':'VIOLET'}
    if name=='nq': return {'id':split+'-id','question':{'text':'Which two colors?'},'document':{'tokens':{'token':['<p>','red','and','blue','</p>'],'is_html':[True,False,False,False,True]}},'annotations':{'short_answers':[{'start_token':[3,1],'end_token':[4,2]}],'yes_no_answer':[-1]}}
    if name=='bbeh': return {'task':'boolean_expressions','input':'Return the truth value of True and True.','target':'True'}
    if name=='supergpqa': return {'uuid':'s-id','question':'Choose the violet option.','options':['VIOLET','AMBER'],'answer_letter':'A','answer':'VIOLET'}
    if name in ('math','math500'): return {'unique_id':split+'-id','problem':'Find one half.','solution':r'The answer is \boxed{\frac{1}{2}}.'}
    if name=='officeqa_pro': return {'uid':'o-id','question':'Which total is in the supplied document?','answer':'42','source_docs':['private-answer-pointer']}
    raise ValueError(name)


def fixture(name, root, monkeypatch=None):
    local=[]
    for split in CATALOG[name]['splits']:
        raw=root.parent/(root.name+'-'+split+'.jsonl')
        raw.write_text(json.dumps(source_row(name,split))+'\n',encoding='utf-8')
        local.append(split+'='+str(raw))
    prepare(name,root,local)
    if name=='officeqa_pro':
        (root/'corpus').mkdir()
        (root/'corpus/report.md').write_text('Public quarterly total is 42.\n')
    return Dataset(name,root)


def test_vendored_scorers_match_pinned_byte_inventory():
    root=ROOT/'data/scaffolds/native_qa'
    source=json.loads((root/'PROVENANCE.json').read_text())
    assert source['commit']=='e9c7667945f959d62da27204649a82f94d1b6705'
    for path,spec in source['vendored_files'].items():
        assert spec['modifications']==[]
        assert hashlib.sha256((root/path).read_bytes()).hexdigest()==spec['sha256']
    assert 'Apache License' in (root/'LICENSE.upstream').read_text()


@pytest.mark.parametrize('name',list(CATALOG))
def test_missing_native_source_refuses_without_import_network(name,tmp_path):
    adapter=Dataset(name,tmp_path/'unprepared')
    with pytest.raises(ConfigMissing,match='source manifest missing'):
        adapter.load()


@pytest.mark.parametrize('name',['searchqa','nq','bbeh','supergpqa','officeqa_pro'])
def test_private_sources_and_native_division(name,tmp_path):
    adapter=fixture(name,tmp_path/'data')
    tasks,scorable=adapter.load()
    private=[r['gold'] for r in adapter.records()]
    assert tasks and len(tasks)==len(scorable)==len(private)
    public=json.dumps(tasks,sort_keys=True)
    assert 'ground_truth' not in public and 'source_docs' not in public
    setup=json.dumps(adapter.setup_map())
    assert 'gold.json' not in setup and 'private-answer-pointer' not in setup
    if 'train' in CATALOG[name]['splits']:
        division=adapter.getattr('SPLIT')
        assert division['train'] and division['eval']
        assert all('/train/' in task for task in division['train'])
        assert all('/train/' not in task for task in division['eval'])
        assert adapter.getattr('OFFICIAL_EVAL')==division['eval']
    else:
        assert adapter.getattr('SPLIT') is None
        assert adapter.getattr('OFFICIAL_EVAL') is None
    for record in adapter.records():
        verify=adapter.verifier(record)
        assert any(item.get('path')=='gold.json' for item in verify['inputs'])
        assert verify['scorer_input']['sha256']==hashlib.sha256(json.dumps(record['gold'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize('prediction,answers,hit',[('The VIOLET.',['violet'],1),('', [''],0),('red blue',['red blue','green'],1),('red',['red blue'],0)])
def test_searchqa_em_f1_matches_native(prediction,answers,hit):
    result=score('searchqa',prediction,{'answers':answers})
    assert result['reward']==hit
    assert result['official']['metrics']==metrics.qa_metrics(prediction,answers)


@pytest.mark.parametrize('prediction,reference',[('The answer is: 1.','1'),('(a)','a'),('0.5','0.50'),("'yes'",'yes'),('first\nsecond','first'),('not true','true')])
def test_bbeh_correctness_preserves_native_edges(prediction,reference):
    assert score('bbeh',prediction,{'answer':reference})['reward']==float(bbeh_official.evaluate_correctness(prediction,reference))


@pytest.mark.parametrize('prediction,want',[('The options are A and B.\nB',0),('Reasoning\nA',1),('Answer: a.',1),('A B',0),('',0)])
def test_supergpqa_only_final_line_counts(prediction,want):
    assert score('supergpqa',prediction,{'answer':'A'})['reward']==want


def test_nq_multispan_order_filter_and_yes_no():
    row=source_row('nq')
    native_id,question,gold=parse_row('nq',row,'validation')
    assert gold['answers']==['red blue']
    assert '<p>' not in question
    row['annotations']={'short_answers':[{'start_token':[],'end_token':[]}],'yes_no_answer':[-1]}
    assert parse_row('nq',row,'validation') is None
    row['annotations']['yes_no_answer']=[1]
    assert parse_row('nq',row,'validation')[2]['answers']==['yes']
    row['annotations']['short_answers'][0]['start_token']=[100]
    row['annotations']['short_answers'][0]['end_token']=[101]
    with pytest.raises(ValueError):parse_row('nq',row,'validation')


def test_bbeh_duplicate_question_is_not_arbitrarily_retained(tmp_path):
    raw=tmp_path/'raw.jsonl';row=source_row('bbeh');raw.write_text('\n'.join(json.dumps(x) for x in [row,{**row,'target':'False'}]))
    prepare('bbeh',tmp_path/'data',['test='+str(raw)])
    with pytest.raises(ValueError,match='duplicate native'):
        Dataset('bbeh',tmp_path/'data').load()


def test_revision_checksum_and_missing_split_refuse(tmp_path):
    adapter=fixture('searchqa',tmp_path/'data')
    manifest=adapter.root/'source-manifest.json';data=json.loads(manifest.read_text())
    data['revision']='main';manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='revision mismatch'):adapter.load()
    data['revision']=CATALOG['searchqa']['revision'];manifest.write_text(json.dumps(data))
    first=adapter.root/data['files'][0]['path'];first.write_text(first.read_text()+'\n')
    with pytest.raises(ValueError,match='checksum mismatch'):adapter.load()


@pytest.mark.parametrize('gold,prediction',[('42','<FINAL_ANSWER>42</FINAL_ANSWER>'),('5%','<FINAL_ANSWER>5%</FINAL_ANSWER>'),('1,000','<FINAL_ANSWER>1000</FINAL_ANSWER>'),('Violet','AMBER')])
def test_office_reward_is_native(gold,prediction):
    assert score('officeqa_pro',prediction,{'answer':gold})['reward']==office_reward.score_answer(gold,prediction,0.0)


def test_office_corpus_is_public_and_separate(tmp_path):
    adapter=fixture('officeqa_pro',tmp_path/'data')
    setups=adapter.setup_map()
    assert all({'path':'corpus','from':str(adapter.root/'corpus')} in item['files'] for item in setups.values())
    (adapter.root/'corpus/answers.csv').write_text('private')
    with pytest.raises(ValueError,match='only ordinary public'):adapter.setup_map()


def test_math_never_falls_back_to_string_match(tmp_path,monkeypatch):
    adapter=fixture('math500',tmp_path/'data')
    real=importlib.metadata.version
    def absent(name):
        if name=='math-verify':raise importlib.metadata.PackageNotFoundError(name)
        return real(name)
    monkeypatch.setattr(importlib.metadata,'version',absent)
    with pytest.raises(ConfigMissing,match='exact-match is not a substitute'):adapter.load()
    with pytest.raises(importlib.metadata.PackageNotFoundError):score('math500','1/2',{'answer':r'\boxed{\frac{1}{2}}'})


@pytest.mark.parametrize('name,answer',[('searchqa','violet'),('nq','red blue'),('bbeh','The answer is: True.'),('supergpqa','A'),('officeqa_pro','<FINAL_ANSWER>42</FINAL_ANSWER>'),
    pytest.param('math500',r'\boxed{0.5}',marks=pytest.mark.skipif(
        importlib.util.find_spec('math_verify') is None,
        reason='exact Math-Verify dependency is not installed in this interpreter'))])
@pytest.mark.parametrize('oracle',[True,False])
def test_platform_independent_private_verifier(name,answer,oracle,tmp_path,monkeypatch):
    adapter=fixture(name,tmp_path/'data')
    task=adapter.load()[0][0]; record=adapter.records()[0]
    verifier=adapter.verifier(record)
    if name=='math500':
        private_reference=json.dumps(record['gold']['answer'])[1:-1]
        assert verifier['argv'][0]==sys.executable
        assert verifier['runtime_paths']==list(dict.fromkeys([sys.prefix,sys.base_prefix]))
        assert private_reference not in json.dumps(task)
        assert private_reference not in json.dumps(adapter.setup_map())
    source=tmp_path/'source';source.mkdir()
    (source/'harness.json').write_text(json.dumps({'name':'qa-control','version':'1','entrypoint':'agent.py','env_kinds':['files']}))
    predicted=answer if oracle else 'DELIBERATELY_WRONG_SENTINEL'
    body='''import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--task');p.add_argument('--workdir');a=p.parse_args();w=Path(a.workdir)
assert not (w/'gold.json').exists()
(w/'gold.json').write_text('{"answer":"TAMPERED"}')
(w/'answer.txt').write_text(PREDICTION)
(w/'trace.jsonl').write_text(json.dumps({'usage':{'calls':0,'input_tokens':0,'output_tokens':0}})+'\\n')
'''.replace('PREDICTION',repr(predicted))
    (source/'agent.py').write_text(body)
    work=tmp_path/'work';repo=work/'control/workspace';stage_workspace(source,repo)
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT',str(work))
    monkeypatch.setenv('HG_AGENT_BACKEND','mock')
    result=run_one(repo,task,sandbox=True,setup=adapter.setup_map()[task['task_id']],verifier=verifier,timeout_s=30,verify_timeout_s=30,run_id='qa-port-control')
    assert not result.get('invalid'),result
    assert result['exit_code']==0,result.get('stderr')
    assert result['verdict']['score']==float(oracle),result['verdict']
    assert not result['verdict'].get('unmeasured'),result['verdict']
    assert result['verdict']['evidence']['measured'] is True
    assert 'TAMPERED' not in json.dumps(result['verdict']['evidence'])
    if name=='math500':
        assert private_reference not in json.dumps(result['verdict']['evidence'])
        assert result['verdict']['evidence']['scorer_input']['verified'] is True


@pytest.mark.parametrize('name',['math','math500'])
def test_math_verifier_rejects_hidden_tmp_runtime(name,tmp_path,monkeypatch):
    monkeypatch.setattr(sys,'prefix',str(tmp_path))
    adapter=Dataset(name,tmp_path/'data')
    with pytest.raises(ConfigMissing,match='venv outside /tmp'):
        adapter.verifier({'task_id':name+'/test/control','gold':{'answer':r'\boxed{2}'}})


@pytest.mark.skipif(importlib.util.find_spec('math_verify') is None,
                    reason='exact Math-Verify dependency is not installed in this interpreter')
@pytest.mark.parametrize('gold,prediction,want',[
    (r'\boxed{\frac{1}{2}}',r'\boxed{0.5}',1),
    (r'\boxed{2}',r'\boxed{1+1}',1),
    (r'\boxed{2}',r'\boxed{3}',0),
    (r'\boxed{2}',r'\boxed{2} then \boxed{3}',0),
    (r'\boxed{2}',r'\boxed{2} then \boxed{',0),
])
def test_math_real_native_equivalence(gold,prediction,want):
    assert score('math500',prediction,{'answer':gold})['reward']==want
