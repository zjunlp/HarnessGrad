"""Private short-lived ChessBench controller; stdin contains trusted gold.

This repository source is part of the platform content identity; the invoking
provider artifact is independently bound in the dataset runtime identity.
"""
import json
import sys


def main():
    try:
        request = json.load(sys.stdin)
        from data.datasets.chessbench_puzzles import STEPWISE_CONTRACT, StepwisePuzzle
        if STEPWISE_CONTRACT != 'chessbench_puzzles.stepwise.v1':
            raise ValueError('contract mismatch')
        session = StepwisePuzzle(request['gold'])
        observation = session.public_task()
        for answer in request['history']:
            observation = session.submit(answer)
        print(json.dumps(observation, allow_nan=False))
    except Exception:
        # Neither exceptions nor private input may reach the caller's public records.
        print('{}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
