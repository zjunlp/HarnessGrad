"""Translate one sealed RoboCasa365 success observation into a score."""
import argparse
from hashlib import sha256
import json
from pathlib import Path


def evaluate(config_bytes, sealed, expected_digest):
    if sha256(config_bytes).hexdigest() != expected_digest:
        raise ValueError('RoboCasa365 verifier input checksum mismatch')
    config = json.loads(config_bytes)
    case = config['case']
    case_bytes = json.dumps(case, sort_keys=True, separators=(',', ':')).encode()
    binding = sealed['binding']
    episode = sealed['payload']
    if (sealed['schema'] != 'harnessgrad.interaction-record.v1'
            or binding['task_id'] != case['task_id'] or episode['task_id'] != case['task_id']
            or episode['schema'] != config['episode_schema']
            or episode['input_sha256'] != binding['input_sha256']
            or episode['case_sha256'] != sha256(case_bytes).hexdigest()
            or episode['scene_split'] != config['scene_split'] or episode['revision'] != config['revision']
            or episode['bundle'] != config['bundle'] or episode['python_version'][:2] != [3, 11]):
        raise ValueError('RoboCasa365 trusted episode identity mismatch')
    steps = episode['steps']
    success_step = episode['first_success_step']
    if (type(steps) is not int or not 0 <= steps <= case['horizon']
            or type(episode['terminal']) is not bool
            or (steps == case['horizon'] and not episode['terminal'])
            or (success_step is not None and (type(success_step) is not int or not 1 <= success_step <= steps))):
        raise ValueError('RoboCasa365 trusted episode has invalid step counts')
    return {
        'binding': binding, 'bundle': config['bundle'], 'metric': config['metric'],
        'revision': config['revision'], 'scene_split': config['scene_split'],
        'task': case['task'], 'group': case['group'], 'pool': case['pool'],
        'steps': steps, 'horizon': case['horizon'], 'success': success_step is not None,
        'first_success_step': success_step, 'python_version': episode['python_version'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-sha256', required=True)
    args = parser.parse_args()
    out = Path('verifier')
    out.mkdir(exist_ok=True)
    reward = out / 'reward.txt'
    reward.unlink(missing_ok=True)
    evidence = {'measured': False}
    try:
        case = Path('robocasa365_case.json').read_bytes()
        sealed = json.loads(Path('trusted_episode/episode.json').read_text())
        result = evaluate(case, sealed, args.config_sha256)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        evidence['error'] = type(exc).__name__
        (out / 'evidence.json').write_text(json.dumps(evidence))
        return 3
    reward.write_text(f"{int(result['success'])}\n")
    evidence.update(measured=True, task_id=result['binding']['task_id'], metric=result['metric'], official=result)
    (out / 'evidence.json').write_text(json.dumps(evidence))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
