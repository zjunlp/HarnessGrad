"""Stdlib-only actor client; selected task and JVM remain in the service."""
import json
from pathlib import Path
STEP_BUDGET = 100
LOG_FILENAME = 'scw_actions.jsonl'
TASK_FILENAME = 'scw_task.json'
def action_log_records(path):
    rows=[]
    if Path(path).is_file():
        for line in Path(path).read_text(errors='replace').splitlines():
            try:
                row=json.loads(line)
                if isinstance(row,dict): rows.append(row)
            except ValueError: pass
    return rows
def actions_in(rows):
    return [r['action'] for r in rows if r.get('kind')=='action' and isinstance(r.get('action'),str)]
def append_action(path,action,step):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as out: out.write(json.dumps({'kind':'action','step':step,'action':action},ensure_ascii=False)+'\n')
def replay(actions,budget):
    if budget != STEP_BUDGET: raise ValueError('the selected episode has a fixed 100-step budget')
    remote = Remote(actions)
    result = remote.call('look')
    remote.room = result['text']
    return remote, result['last']
class Remote:
    def __init__(self,actions): self.actions=actions
    def call(self,operation):
        import sys
        sys.path.insert(0,'/run/harnessgrad-tool')
        from tool_transport import call
        return call('/run/harnessgrad-tool/tool.sock',operation,{'actions':self.actions},timeout_s=None,max_line_bytes=None)
    def look(self): return self.room
    def inventory(self): return self.call('inventory')['text']
    def get_task_description(self): return self.call('task')['text']
