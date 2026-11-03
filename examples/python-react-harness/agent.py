#!/usr/bin/env python3
"""Tiny OpenAI-compatible ReAct harness: model tool call -> shell observation -> answer."""
import argparse, json, os, subprocess, urllib.request
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--task',required=True); p.add_argument('--workdir',required=True); a=p.parse_args()
task=json.loads(Path(a.task).read_text()); work=Path(a.workdir).resolve(); work.mkdir(parents=True,exist_ok=True)
trace=work/'trace.jsonl'; calls=0; model=os.getenv('HG_AGENT_MODEL',''); backend=os.getenv('HG_AGENT_BACKEND','mock')
def log(row):
    with trace.open('a') as f: f.write(json.dumps(row)+'\n')
log({'harness_identity':{'harness':'python-react-example','agent_model':model or ('mock' if backend=='mock' else None),'agent_backend':backend}})
messages=[{'role':'system','content':'Solve the task in the workdir. Use shell tool if needed; then return a concise final answer.'},{'role':'user','content':task.get('goal','')}]
for step in range(8):
    calls+=1
    if backend=='mock':
        answer='EXAMPLE_MOCK_OK' if step else None
        tool=None if answer else "printf 'EXAMPLE_MOCK_OK\\n' > answer.txt"
    else:
        body=json.dumps({'model':model,'messages':messages,'tools':[{'type':'function','function':{'name':'shell','description':'Run a shell command in the task workdir','parameters':{'type':'object','properties':{'command':{'type':'string'}},'required':['command']}}}]}).encode()
        req=urllib.request.Request(os.environ['HG_AGENT_BASE_URL'].rstrip('/')+'/chat/completions',body,{'Authorization':'Bearer '+os.environ['HG_AGENT_API_KEY'],'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=float(os.getenv('HG_AGENT_TIMEOUT_S','60'))) as r: reply=json.load(r)['choices'][0]['message']
        log({'reply':reply.get('content') or ''})
        if reply.get('tool_calls'):
            tool=json.loads(reply['tool_calls'][0]['function']['arguments'])['command']; answer=None
        else: tool=None; answer=reply.get('content') or ''
    if tool:
        result=subprocess.run(tool,shell=True,cwd=work,capture_output=True,text=True,errors='replace',timeout=60)
        log({'command':tool,'observation':{'kind':'shell','returncode':result.returncode,'output':result.stdout+result.stderr}})
        if backend!='mock': messages += [reply,{'role':'tool','tool_call_id':reply['tool_calls'][0]['id'],'content':result.stdout+result.stderr}]
        continue
    if answer is not None:
        if not (work/'answer.txt').exists(): (work/'answer.txt').write_text(answer+'\n')
        log({'usage':{'calls':calls,'input_tokens':None,'output_tokens':None}}); log({'submitted':True,'answer':(work/'answer.txt').read_text(),'answer_source':'artifact'}); raise SystemExit(0)
raise SystemExit('step budget exhausted')
