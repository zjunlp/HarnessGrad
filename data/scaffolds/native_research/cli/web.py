#!/usr/bin/env python3
"""Public search/fetch CLI; endpoints and provider keys are private to the relay."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from web_transport import call
p = argparse.ArgumentParser()
p.add_argument('operation', choices=['call'])
p.add_argument('kind', choices=['search', 'fetch'])
p.add_argument('arguments', help='JSON object: search uses query/num, fetch uses url/query')
a = p.parse_args()
print(json.dumps(call('/run/harnessgrad-tool/tool.sock', a.kind, json.loads(a.arguments)), ensure_ascii=False))
