#!/usr/bin/env python3
"""Trusted post-exit judge/scorer; raw gold and judge messages never enter evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

# Both paths are frozen platform projections; isolated Python omits actor cwd.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path('judge_transport').resolve())]
from scoring import score
from tool_transport import call


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--case-sha256', required=True)
    args = parser.parse_args()
    out = Path('verifier')
    out.mkdir(exist_ok=True)
    reward = out / 'reward.txt'
    reward.unlink(missing_ok=True)
    evidence = {'measured': False, 'dataset': args.dataset, 'task_id': args.task_id}
    code = 3
    try:
        raw = Path('flashrsi_case.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.case_sha256:
            raise ValueError('selected private case digest mismatch')
        evidence['scorer_input'] = {'task_id': args.task_id, 'path': 'flashrsi_case.json',
            'sha256': hashlib.sha256(raw).hexdigest(), 'expected_sha256': args.case_sha256, 'verified': True}
        case = json.loads(raw)
        if case['task_id'] != args.task_id:
            raise ValueError('selected case task ID mismatch')
        path = Path('answer.txt')
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('answer is not a regular single-link file')
            prediction = path.read_text(encoding='utf-8')
        else:
            prediction = ''
        def complete(messages):
            result = call(os.environ['HG_JUDGE_SOCKET'], 'complete', {'messages': messages},
                          timeout_s=None, max_line_bytes=None)
            if result.get('finish_reason') != 'stop' or not isinstance(result.get('content'), str):
                raise ValueError('native judge returned an incomplete generation')
            return result['content']
        result = score(args.dataset, case, prediction, complete)
        evidence.update(measured=True, metrics=result['metrics'], answer_missing=result.get('answer_missing', False),
                        case_sha256=args.case_sha256, flashrsi_commit=case['flashrsi_commit'])
        reward.write_text(str(result['reward']))
        code = 0
    except Exception as exc:
        # Exceptions from native evaluators can contain private gold. Report only
        # the type; the trusted runtime receipt retains provider fault attribution.
        evidence.update(error=type(exc).__name__)
        print('native verifier failed: ' + type(exc).__name__, file=sys.stderr)
    (out / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
