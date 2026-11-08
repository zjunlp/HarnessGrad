"""Trusted verifier: replay action.json, not subject-supplied state or observations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reward-file', default='verifier/reward.txt')
    ap.add_argument('--evidence-file', default='verifier/evidence.json')
    args = ap.parse_args()
    work = Path.cwd()
    reward_file = work / args.reward_file
    evidence_file = work / args.evidence_file
    reward_file.parent.mkdir(parents=True, exist_ok=True)
    evidence_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        # -I does not add the script directory. Admit the owned bootstrap/bridge
        # explicitly, and validate the separately projected source before imports.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from bootstrap import bootstrap
        source_id = bootstrap(work / 'agentdojo_source')
        import bridge
        case = json.loads((work / 'verify/case.json').read_text())
        action = json.loads((work / 'action.json').read_text()) if (work / 'action.json').is_file() else {'calls': [], 'output': ''}
        result = bridge.evaluate(case, action)
        result['source_manifest_sha256'] = source_id
    except Exception as exc:
        # Case/task and tool output may contain private inputs; diagnostics carry
        # only the exception class, never those bytes or an upstream traceback.
        evidence_file.write_text(json.dumps({'measured': False, 'error': type(exc).__name__}))
        reward_file.unlink(missing_ok=True)
        return 3
    reward_file.write_text(str(result['reward']) + '\n')
    result.update(measured=True, why=f"utility={result['utility']}, attack_success={result['attack_success']}",
                  official={'metric': 'AgentDojo utility under fixed injection',
                            'attack_success': result['attack_success'], 'version': result['suite_version']})
    evidence_file.write_text(json.dumps(result))
    print(f"reward {result['reward']:g}: {result['why']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
