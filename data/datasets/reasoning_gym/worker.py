"""Private worker for generation and direct scoring in the prepared runtime."""
from contextlib import redirect_stdout
import json
from pathlib import Path
import runpy
import sys


if __name__ == "__main__":
    assets = Path(__file__).resolve().parent
    sys.path.insert(0, str(assets.parents[2]))
    from data.datasets import reasoning_gym

    operation, = sys.argv[1:]
    with redirect_stdout(sys.stderr):
        if operation == "pool":
            result = runpy.run_path(str(assets / "prepare.py"))["_render_pool"]()
        elif operation == "score":
            payload = json.load(sys.stdin)
            result = reasoning_gym.score(payload["prediction"], payload["gold"],
                                         mode=reasoning_gym.SCORE_MODE)
        else:
            raise ValueError("unknown Reasoning Gym worker operation")
    print(json.dumps(result, allow_nan=False))
