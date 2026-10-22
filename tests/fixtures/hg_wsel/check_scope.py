#!/usr/bin/env python3
"""Reject any registry delta except the eight appended observed annotations."""
import copy
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
BASE = '4556a9a5'
NAMES = {'fever', 'hotpotqa', 'physicsquestions', 'reasoning_gym',
         'sportsunderstanding', 'spreadsheetbench', 'strategyqa', 'triviaqa'}
old = json.loads(subprocess.check_output(['git', 'show', BASE + ':registry/dataset-blockers.json'], cwd=ROOT))
new = json.loads((ROOT / 'registry/dataset-blockers.json').read_bytes())
stripped = copy.deepcopy(new)
changes = []
for before, after, reset in zip(old['datasets'], new['datasets'], stripped['datasets']):
    assert before['dataset'] == after['dataset']
    for b, a, r in zip(before['blockers'], after['blockers'], reset['blockers']):
        if a['observed'] != b['observed']:
            assert after['dataset'] in NAMES
            assert a['observed'].startswith(b['observed'] + ' ')
            assert ' - w-sel: ' in a['observed'][len(b['observed']):]
            changes.append(after['dataset'])
            r['observed'] = b['observed']
assert stripped == old, 'unexpected registry delta (status/next_action/history/shared/refusal/etc.)'
assert set(changes) == NAMES and len(changes) == 8
journal = (ROOT / 'docs/adapter-integration-journal.md').read_bytes()
old_journal = subprocess.check_output(['git', 'show', BASE + ':docs/adapter-integration-journal.md'], cwd=ROOT)
assert journal.startswith(old_journal), 'journal must be append-only'
assert ' - w-sel\n' in journal[len(old_journal):].decode()
print('PASS: exactly 8 observed append-only changes; next_actions/statuses/history/shared gates and four permanent refusals unchanged; journal append-only')
