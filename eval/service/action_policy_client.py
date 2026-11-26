"""Delegate part of an episode to the configured local action policy."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['run'])
    parser.add_argument('--instruction', required=True)
    parser.add_argument('--steps', type=int, required=True)
    parser.add_argument('--image-dir', type=Path)
    args = parser.parse_args()
    root = Path('/run/harnessgrad-tool')
    sys.path.insert(0, str(root))
    from tool_transport import call

    # The actor has its own episode deadline. A multi-batch call can exceed the
    # transport's ordinary per-command timeout while the simulator is advancing.
    result = call(root / 'tool.sock', 'policy.run', {
        'instruction': args.instruction, 'steps': args.steps,
    }, timeout_s=None)
    images = result.pop('images', {})
    if args.image_dir is not None and images:
        args.image_dir.mkdir(parents=True, exist_ok=True)
        result['images'] = {}
        for index, (name, data) in enumerate(images.items()):
            path = args.image_dir / f'camera-{index}.png'
            path.write_bytes(base64.b64decode(data, validate=True))
            result['images'][name] = str(path)
    print(json.dumps(result, ensure_ascii=False))
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())
