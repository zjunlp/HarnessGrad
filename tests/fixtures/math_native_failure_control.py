"""Real Math-Verify failure boundaries using authored records, never benchmark rows."""
import importlib.util
import json
import os
from pathlib import Path
import sys


def main(repo, store, output):
    sys.path.insert(0, str(repo))
    assert importlib.util.find_spec('math_verify') is None, 'control platform must be stdlib-only'
    os.environ.update(HG_DATASET_RUNTIME_ROOT=str(store), HG_REVIEWED_ENVIRONMENT_ONLY='1')
    from data.scaffolds.native_qa import CATALOG, Dataset
    from data.scaffolds.native_qa.prepare import prepare
    from eval.verification.verifier import _verify
    from harnessgrad import dataset_runtime
    runtime = dataset_runtime.resolve_execution('math')
    rows = []
    for name in ('math', 'math500'):
        local = []
        for split in CATALOG[name]['splits']:
            source = output / (name + '-' + split + '.jsonl')
            source.write_text(json.dumps({'unique_id': 'authored-' + split,
                'problem': 'Find half.', 'solution': r'PRIVATE AUTHORED REASONING \boxed{\frac{1}{2}}'}) + '\n')
            local.append(split + '=' + str(source))
        root = output / ('data-' + name)
        manifest = prepare(name, root, local)
        assert manifest['acquisition'].startswith('local-native-import')
        dataset = Dataset(name, root)
        tasks, _ = dataset.load()
        assert 'PRIVATE AUTHORED REASONING' not in json.dumps(tasks)
        assert 'PRIVATE AUTHORED REASONING' not in json.dumps(dataset.setup_map())
        record = dataset.records()[0]
        spec = dataset.verifier(record)
        assert spec['python_runtime']['runtime_id'] == runtime['runtime_id']
        work = output / ('work-' + name)
        work.mkdir()
        os.environ['HARNESSGRAD_WORK_ROOT'] = str(work)
        def evaluate(case, prediction, expected, selected=spec, timeout=30):
            (work / 'answer.txt').write_text(prediction)
            verdict = _verify(work, selected, '', sandbox=True, timeout_s=timeout,
                              env={'kind': 'files'}, task_id=record['task_id'])
            (output / (name + '-' + case + '-private-verdict.json')).write_text(json.dumps(verdict, indent=2))
            if expected == 'timeout':
                assert verdict.get('score') is None and verdict.get('outcome') == 'unmeasured', verdict
                assert 'timed out' in verdict.get('unmeasured', ''), verdict
            elif expected == 'unmeasured':
                assert verdict.get('score') is None and verdict.get('outcome') == 'unmeasured', verdict
            else:
                assert verdict.get('score') == expected, verdict
                assert verdict['evidence']['measured'] is True
                assert verdict['evidence']['scorer_input']['verified'] is True
            assert 'PRIVATE AUTHORED REASONING' not in json.dumps(verdict)
            rows.append({'dataset': name, 'control': case, 'score': verdict.get('score'),
                         'outcome': verdict.get('outcome'), 'expected': expected,
                         'deadline_exhausted': 'timed out' in verdict.get('unmeasured', '')})
        # Prior validation already established positive/negative scoring. This is
        # only the baseline for stale-reward and failure-classification controls.
        evaluate('baseline-correct', r'\boxed{0.5}', 1)
        evaluate('empty-prediction', '', 0)
        evaluate('malformed-final-box', r'\boxed{0.5} then \boxed{', 0)
        evaluate('last-box-overrides-correct', r'\boxed{0.5} then \boxed{3}', 0)
        evaluate('baseline-before-reference-failure', r'\boxed{0.5}', 1)
        invalid = {**record, 'gold': {'answer': '', 'private': 'PRIVATE AUTHORED REASONING'}}
        evaluate('unparseable-private-reference', r'\boxed{0.5}', 'unmeasured', dataset.verifier(invalid))
        evaluate('baseline-before-timeout', r'\boxed{0.5}', 1)
        evaluate('native-timeout-with-old-reward', r'\boxed{0.5}', 'timeout', timeout=0.001)
    assert importlib.util.find_spec('math_verify') is None
    report = {'scope': 'authored Math-Verify 0.9.0 native failure classification; not a released benchmark',
              'runtime_id': runtime['runtime_id'], 'platform_math_verify_present': False, 'controls': rows}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main(*(Path(value) for value in sys.argv[1:]))
