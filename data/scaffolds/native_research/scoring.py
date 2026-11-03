"""Native prompt/parsing/formula dispatch; the injected completion is verifier-only.

No actor-supplied correct/resolved/verifier events are accepted. Remote/runtime failures
raise; only a completed native evaluation can produce a numeric reward.
"""
import ast
import json
import math
import re
from types import SimpleNamespace


def score(name, case, prediction, complete):
    from native import activate
    activate()
    row = case['row']
    if not prediction.strip() and name not in ('workbench', 'widesearch'):
        return {'reward': 0.0, 'metrics': {}, 'answer_missing': True}
    if name == 'workbench':
        from pathlib import Path
        from workbench_process import Session
        sealed = json.loads(Path('trusted_episode/episode.json').read_text())
        payload = sealed['payload']
        if (sealed['schema'] != 'harnessgrad.interaction-record.v1'
                or sealed['binding']['task_id'] != case['task_id'] or payload.get('task_id') != case['task_id']
                or payload.get('world_sha256') != case['world_sha256'] or payload.get('failure')):
            raise ValueError('native WorkBench interaction record binding mismatch')
        session = Session(Path('workbench_world').resolve())
        try:
            for entry in payload['actions']:
                result = session.request({'operation': 'call', **entry['request']})
                if result != entry['response']:
                    raise ValueError('native WorkBench replay differs from observed tool result')
            result = session.request({'operation': 'grade', 'reference': ast.literal_eval(row['outcome'])})
            metrics = result['metrics']
            value = metrics['correct']
        finally:
            session.close()
    elif name == 'browsecomp':
        from upstream.benchmarks.browsecomp._official import GRADER_TEMPLATE, decrypt
        answer = decrypt(row['answer'], row['canary'])
        question = decrypt(row['problem'], row['canary'])
        text = complete([{'role': 'user', 'content': GRADER_TEMPLATE.format(
            question=question, correct_answer=answer, response=prediction)}])
        # Preserve upstream's documented group(1) repair, rather than upstream's
        # always-zero group(0) typo. This profile is explicitly recorded in docs.
        match = re.search(r'correct: (yes|no)', text)
        value = float(bool(match and match.group(1) == 'yes'))
        metrics = {'accuracy': value}
    elif name == 'deepsearchqa':
        from upstream.benchmarks.deepsearchqa._official import counts, metrics as rate, prompt
        text = complete([{'role': 'user', 'content': prompt(row, prediction)}])
        metrics = rate(counts(text))
        value = metrics['f1_score']
    elif name == 'hle':
        from upstream.integrations._hle_judge import JUDGE_PROMPT
        text = complete([{'role': 'user', 'content': JUDGE_PROMPT.format(
            question=row['question'], correct_answer=row['answer'], response=prediction)}])
        verdict = json.loads(text)
        if (set(verdict) != {'extracted_final_answer', 'reasoning', 'correct', 'confidence', 'strict'}
                or verdict['correct'] not in ('yes', 'no') or verdict['strict'] is not True
                or type(verdict['confidence']) is not int or not 0 <= verdict['confidence'] <= 100
                or not isinstance(verdict['extracted_final_answer'], str) or not isinstance(verdict['reasoning'], str)):
            raise ValueError('invalid official HLE structured verdict')
        value = float(verdict['correct'] == 'yes')
        metrics = {'accuracy': value, 'confidence': verdict['confidence']}
    elif name == 'livemathbench':
        from upstream.benchmarks.livemathbench._prompts import JUDGE_PROMPT_CN, JUDGE_PROMPT_EN
        from upstream.benchmarks.livemathbench._extract import math_postprocess_v2
        from upstream.benchmarks.livemathbench._label import extract_judge_label
        language = case['metadata'].get('language', 'en')
        template = JUDGE_PROMPT_CN if language == 'cn' else JUDGE_PROMPT_EN
        # Keys retain the native record's spelling; preparation validates them.
        question = row.get('question', row.get('problem'))
        answer = row.get('answer')
        if not question or not isinstance(answer, str):
            raise ValueError('malformed LiveMathBench native case')
        text = complete([{'role': 'user', 'content': template.format(
            question=question, gold_answer=answer, answer=math_postprocess_v2(prediction))}])
        value = float(extract_judge_label(text) == 'yes')
        metrics = {'accuracy': value}
    elif name == 'travelplanner':
        from pathlib import Path
        from upstream.integrations._travelplanner_grade import check_database, score as native_score
        from upstream.benchmarks.travelplanner._prompts import CONVERSION_PREFIX
        database = Path('travelplanner_database')
        if check_database(database) != case['database_checksum']:
            raise ValueError('native TravelPlanner database identity changed')
        if prediction == 'Max Token Length Exceeded.':
            plan = None
        else:
            text = complete([{'role': 'system', 'content': 'You are a helpful assistant.'},
                {'role': 'user', 'content': CONVERSION_PREFIX + 'Text:\n' + prediction + '\nJSON:\n'}])
            if '```json' in text:
                plan = ast.literal_eval('\t'.join(text.split('\n')).split('```json')[1].split('```')[0])
            else:
                plan = json.loads(text)
            required = {'current_city', 'transportation', 'breakfast', 'attraction', 'lunch', 'dinner', 'accommodation'}
            if not isinstance(plan, list) or any(not isinstance(day, dict) or required - day.keys()
                    or any(not isinstance(day[k], str) for k in required) for day in plan):
                raise ValueError('native converter returned malformed daily plan')
        metrics = native_score(row, plan, database)
        value = metrics['final_pass_rate']
    elif name == 'widesearch':
        import base64
        import hashlib
        import io
        import pandas as pd
        from upstream.benchmarks.widesearch._vendor._llm import completion
        from upstream.benchmarks.widesearch._vendor.data_loader import WideSearchQuery, WideSearchResponse
        from upstream.benchmarks.widesearch._vendor.evaluation import evaluate_single_query
        from upstream.benchmarks.widesearch._vendor.utils import norm_column
        from upstream.benchmarks.widesearch import METRICS, _scores
        q = case['wide_query']
        if q.get('schema') != 'harnessgrad.widesearch-query.csv.v1':
            raise ValueError('WideSearch requires original gold CSV v1; re-prepare legacy JSON cases')
        raw = base64.b64decode(q['gold_csv_base64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != row.get('_gold_sha256'):
            raise ValueError('WideSearch private native gold CSV digest mismatch')
        answer = pd.read_csv(io.BytesIO(raw))
        answer.columns = [norm_column(col.strip()) for col in answer.columns]
        answer = answer[q['evaluation']['required']]
        if answer.empty:
            raise ValueError('WideSearch empty native gold CSV')
        query = WideSearchQuery(q['instance_id'], q['query'], q['evaluation'],
                              answer, q['language'])
        if not prediction.strip():
            return {'reward': 0.0, 'metrics': {}, 'answer_missing': True}
        faults = []
        def call(messages):
            try:
                content = complete([{'role': 'user', 'content': messages}])
                from upstream.benchmarks.widesearch._vendor.metric_utils import parse_markdown_json
                parsed = parse_markdown_json(content)
                if not isinstance(parsed, dict):
                    raise ValueError('WideSearch judge returned malformed native JSON')
                if messages.startswith('Your task is to align two vocabularies.'):
                    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in parsed.items()):
                        raise ValueError('WideSearch native alignment contains non-text values')
                elif messages.startswith('You are an expert in grading answers.'):
                    native_rows = ast.literal_eval(messages.split('====== response-start ======\n', 1)[1]
                        .split('\n====== response-end ======', 1)[0])
                    if set(parsed) != set(native_rows) or any(type(v) is not int or v not in (0, 1) for v in parsed.values()):
                        raise ValueError('WideSearch judge omitted or malformed native row scores')
                return SimpleNamespace(content=content)
            except Exception as exc:
                faults.append(type(exc).__name__)
                raise
        token = completion.set(call)
        try:
            result = evaluate_single_query(query, WideSearchResponse(q['instance_id'], prediction))
        finally:
            completion.reset(token)
        if faults:
            # Native evaluator catches exceptions and returns zero; a provider
            # error is unmeasured in HG and must escape that broad fallback.
            raise ValueError('WideSearch judge request failed')
        metrics = _scores({k: float(getattr(result, k)) for k in METRICS})
        value = metrics['score']
    else:
        raise ValueError(f'no admitted native scorer for {name}')
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('native scorer returned a non-finite or out-of-range reward')
    return {'reward': value, 'metrics': metrics}
