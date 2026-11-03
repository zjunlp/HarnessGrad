#!/usr/bin/env python3
"""Execute a shell command in the selected native repository."""
import json
import sys
from swe_transport import call

if len(sys.argv) != 3 or sys.argv[1] != 'exec':
    raise SystemExit('usage: python3 swe.py exec "SHELL COMMAND"')
result = call('/run/harnessgrad-tool/tool.sock', 'exec', {'command': sys.argv[2]},
              timeout_s=None, max_line_bytes=None)
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(result.get('exit_code', 1))
