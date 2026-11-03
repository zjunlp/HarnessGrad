#!/usr/bin/env python3
"""External-method template: replace propose() with your optimizer's edit rule.

The template returns an unchanged candidate and stops. It calls no model, runs
no benchmark evaluator and claims no improvement. Keep diagnostics on stderr.
"""
import json
from pathlib import Path
import shutil
import sys


def propose(candidate, study):
    """Edit candidate using only study/round.json, traces and past study history.

    Return True when your optimizer wants this candidate measured and to
    continue its search. Return False to report no change and finish normally.
    Do not change harness.json's name/version or call a benchmark scorer here.
    """
    return False


def main():
    request = json.load(sys.stdin)
    if request.get('platform_api_version') != '0.1.0':
        raise SystemExit('unsupported platform API; see methods/protocol.py')
    workspace = Path(request['workspace'])
    candidate = workspace / 'candidate'
    shutil.copytree(request['base_harness'], candidate)
    study = Path(request['base_harness']) / '_harnessgrad'
    changed = propose(candidate, study)
    Path(request['trajectory_out']).write_text(json.dumps({'steps': [{
        'harness_dir': str(candidate), 'label': 'external method template',
        'edit_kind': 'none' if not changed else 'code',
        'claimed_cost': {'generation_tokens': 0}, 'method_reported': {}}],
        'trajectory_shape': 'sequence', 'nominated': 0,
        'provenance': {'method': 'external-template (control)'}}))
    print(json.dumps({'steps': 1, 'changed': changed, 'generation_tokens': 0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
