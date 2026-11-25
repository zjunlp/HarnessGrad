"""Call the RoboCasa365 episode socket or save the current RGB views as PNGs."""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

TOOL_ROOT = Path('/run/harnessgrad-tool')
sys.path.insert(0, str(TOOL_ROOT))
from tool_transport import call as _call


CAMERAS = {
    'left': 'video.robot0_agentview_left',
    'right': 'video.robot0_agentview_right',
    'wrist': 'video.robot0_eye_in_hand',
}


def call(op, **args):
    return _call(TOOL_ROOT / 'tool.sock', op, args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['observe', 'step', 'step_many'])
    parser.add_argument('action_file', nargs='?', type=Path,
                        help='JSON action object (step) or array of action objects (step_many)')
    parser.add_argument('--image-dir', type=Path,
                        help='save requested PNGs here and print their paths')
    parser.add_argument('--views', nargs='*', choices=CAMERAS,
                        help='select camera views, or omit all values for state only')
    args = parser.parse_args()
    takes_action = args.operation != 'observe'
    if takes_action != (args.action_file is not None):
        parser.error('step and step_many require one action file, and observe takes none')
    payload = {}
    if takes_action:
        key = 'action' if args.operation == 'step' else 'actions'
        payload[key] = json.loads(args.action_file.read_text())
    views = args.views if args.views is not None else (list(CAMERAS) if args.image_dir else [])
    payload['cameras'] = [CAMERAS[name] for name in views]
    if views and args.image_dir is None:
        parser.error('--views with camera names requires --image-dir')
    response = call(args.operation, **payload)
    if response.get('images'):
        args.image_dir.mkdir(parents=True, exist_ok=True)
        for short, name in CAMERAS.items():
            if name in response['images']:
                path = args.image_dir / f'{short}.png'
                path.write_bytes(base64.b64decode(response['images'][name], validate=True))
                response['images'][name] = str(path)
    print(json.dumps(response, ensure_ascii=False))
    return int('error' in response)


if __name__ == '__main__':
    raise SystemExit(main())
