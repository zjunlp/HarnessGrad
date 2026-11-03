#!/usr/bin/env python3
"""Public native WorkBench tools; no actor-accessible grader or reset."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workbench_transport import call
p = argparse.ArgumentParser()
p.add_argument('operation', choices=['describe', 'call'])
p.add_argument('name', nargs='?')
p.add_argument('arguments', nargs='?')
a = p.parse_args()
if a.operation == 'describe':
    if a.name is not None: p.error('describe accepts no arguments')
    result = call('/run/harnessgrad-tool/tool.sock', 'describe', {})
else:
    if not a.name or a.arguments is None: p.error('call requires TOOL_NAME JSON_OBJECT')
    result = call('/run/harnessgrad-tool/tool.sock', 'call', {'name': a.name, 'arguments': json.loads(a.arguments)})
print(json.dumps(result, ensure_ascii=False))
