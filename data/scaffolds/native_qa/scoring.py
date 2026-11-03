"""Pinned upstream QA scoring; no task I/O, model calls or actor-controlled code."""
from __future__ import annotations

import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path

from .metrics import qa_metrics
from .bbeh_official import evaluate_correctness
from .office_reward import score_answer


def score(name: str, prediction: str, gold: dict) -> dict:
    if name in ('searchqa', 'nq'):
        metrics = qa_metrics(prediction, gold['answers'])
        reward = metrics['exact_match']
        protocol = ('native-context-short-answer-EM-F1-v1' if name == 'nq'
                    else 'native-text-EM-F1-v1')
    elif name == 'bbeh':
        reward = float(evaluate_correctness(prediction, gold['answer']))
        metrics, protocol = {'accuracy': reward}, 'bbeh-per-answer-correctness-80d12ca9'
    elif name == 'supergpqa':
        lines = prediction.strip().splitlines()
        final = lines[-1].strip() if lines else ''
        match = re.fullmatch(r'(?:Answer:\s*)?([A-J])\.?', final, re.IGNORECASE)
        answer = match.group(1).upper() if match else None
        reward = float(answer == gold['answer'])
        metrics, protocol = {'accuracy': reward}, 'native-supergpqa-final-letter-v1'
    elif name in ('math', 'math500'):
        if importlib.metadata.version('math-verify') != '0.9.0':
            raise ValueError('math-verify==0.9.0 is required; no string-match fallback')
        # The native Math-Verify rules run in their own bounded interpreter, as
        # upstream. A timeout/error is unmeasured, never an ordinary wrong answer.
        result = subprocess.run([sys.executable, '-I', '-X', 'utf8',
                                 str(Path(__file__).with_name('math_grade.py'))],
                                input=json.dumps({'gold': gold['answer'],
                                                  'prediction': prediction}).encode(),
                                capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError('Math-Verify refused the reference or failed to grade')
        value = json.loads(result.stdout)['correct']
        if type(value) is not bool:
            raise ValueError('invalid Math-Verify result')
        reward = float(value)
        metrics, protocol = {'accuracy': reward}, 'math-verify-0.9.0-last-boxed'
    elif name == 'officeqa_pro':
        reward = float(score_answer(gold['answer'], prediction, 0.0))
        metrics, protocol = {'accuracy': reward}, 'databricks-officeqa-7b9a3c15-tolerance-0'
    else:
        raise ValueError('unknown upstream QA adapter')
    return {'reward': reward, 'correct': reward == 1.0,
            'official': {'metric_identity': protocol, 'metrics': metrics},
            'why': f'{protocol}: reward {reward:g}'}
