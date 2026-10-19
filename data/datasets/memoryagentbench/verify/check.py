#!/usr/bin/env python3
"""Score only the platform-sealed episode against verifier-only selected gold."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

# Upstream recommendation uses set iteration to break equal-distance ties.
# Start one isolated scoring process with a fixed hash seed. -I ignores the
# seed, so re-exec with equivalent safe-path/no-user-site flags and a cleared
# Python environment; no actor path, sitecustomize or PYTHONPATH is accepted.
if __name__ == '__main__' and sys.flags.ignore_environment:
    environment = {key: value for key, value in os.environ.items() if not key.startswith('PYTHON')}
    environment['PYTHONHASHSEED'] = '0'
    os.execve(sys.executable, [sys.executable, '-P', '-s', __file__, *sys.argv[1:]], environment)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.evaluation import score_episode
from lib.scoring import JUDGE_SOURCES, PRIMARY_METRICS, UPSTREAM_REVISION, UPSTREAM_SCORER_SHA256


def read_pinned(path, digest):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('verifier input digest mismatch')
    return json.loads(raw)


def verify(gold, record, *, task_id, input_sha256, relay_sha256, entity2id=None, judge_profile=None, complete=None):
    if (gold.get('schema') != 'hg-mab-gold-v1' or gold.get('task_id') != task_id
            or gold.get('episode_id') != task_id or gold.get('input_sha256') != input_sha256
            or gold.get('source') not in PRIMARY_METRICS):
        raise ValueError('gold identity or eligible source mismatch')
    binding = record.get('binding', {})
    payload = record.get('payload', {})
    if (record.get('schema') != 'harnessgrad.interaction-record.v1'
            or binding.get('task_id') != task_id or binding.get('input_sha256') != relay_sha256
            or not binding.get('run_id') or not binding.get('trial_id')
            or payload.get('schema') != 'hg-mab-service-seal-v1'
            or payload.get('task_id') != task_id or payload.get('input_sha256') != input_sha256):
        raise ValueError('trusted episode identity mismatch')
    result = score_episode(gold['source'], payload['seal'], gold['gold_by_id'],
                           expected_episode_id=task_id, entity2id=entity2id,
                           judge_metadata_by_id=gold.get('judge_metadata_by_id'),
                           judge_profile=judge_profile, complete=complete)
    evidence = {'schema': 'hg-mab-verifier-v1', 'task_id': task_id,
                'source': gold['source'], 'metric': PRIMARY_METRICS[gold['source']],
                'measured': result['status'] == 'measured', 'result': result,
                'binding': binding, 'artifacts': {'input_sha256': input_sha256,
                    'upstream_revision': UPSTREAM_REVISION,
                    'upstream_scorer_sha256': UPSTREAM_SCORER_SHA256,
                    'primary_only': True, 'python_hash_seed': os.environ.get('PYTHONHASHSEED')}}
    if 'judge' in result:
        evidence['judge'] = result['judge']
    if not evidence['measured']:
        evidence['reason'] = result['reason']
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('gold', 'task-id', 'input-sha256', 'relay-sha256', 'gold-sha256', 'entity2id-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--judge-model')
    parser.add_argument('--judge-max-output-tokens', type=int)
    parser.add_argument('--record', default='trusted_episode/episode.json')
    parser.add_argument('--reward-file', default='verifier/reward.txt')
    parser.add_argument('--evidence-file', default='verifier/evidence.json')
    args = parser.parse_args()
    reward, output = Path(args.reward_file), Path(args.evidence_file)
    reward.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    reward.unlink(missing_ok=True)
    try:
        gold = read_pinned(args.gold, args.gold_sha256)
        mapping = read_pinned(Path(args.gold).with_name('entity2id.json'), args.entity2id_sha256)
        record = json.loads(Path(args.record).read_text())
        selected = None
        complete = None
        if gold['source'] in JUDGE_SOURCES:
            from lib.judge import profile
            if not args.judge_model:
                raise ValueError('judge source requires its declared model')
            selected = profile(gold['source'], args.judge_model, max_output_tokens=args.judge_max_output_tokens)
            # Verifier-only projected transport; the candidate cannot import or
            # call this socket during its actor phase.
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                'mab_judge_transport', Path('mab_judge_transport.py').resolve())
            transport = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(transport)
            def complete(messages):
                return transport.call(os.environ['HG_JUDGE_SOCKET'], 'complete', {'messages': messages})['content']
        evidence = verify(gold, record, task_id=args.task_id, input_sha256=args.input_sha256,
                          relay_sha256=args.relay_sha256, entity2id=mapping,
                          judge_profile=selected, complete=complete)
        evidence['artifacts'].update(gold_sha256=args.gold_sha256,
                                     entity2id_sha256=args.entity2id_sha256)
    except Exception as exc:
        evidence = {'schema': 'hg-mab-verifier-v1', 'task_id': args.task_id,
                    'measured': False, 'reason': 'scorer_error', 'detail': f'{type(exc).__name__}: {exc}'}
    output.write_text(json.dumps(evidence, ensure_ascii=False) + '\n')
    if evidence['measured']:
        reward.write_text(str(evidence['result']['score']) + '\n')
        return 0
    # An incomplete episode is a completed adjudication with no numeric score.
    # Keep its coverage evidence available under the strict exit contract.
    if evidence.get('reason') == 'scorer_error':
        print(('scorer_error: ' + evidence['detail'])[:1000], file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
