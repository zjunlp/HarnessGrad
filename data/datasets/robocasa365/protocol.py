"""Roster and episode identity shared by the loader, relay and verifier."""
from __future__ import annotations

from hashlib import sha256
import json
from math import ceil
from pathlib import Path


METRIC = 'robocasa365.multitask.task_success.v1'
SCENE_SPLIT = 'pretrain'
EPISODE_SCHEMA = 'harnessgrad.robocasa365.episode.v1'
CATALOG = json.loads(Path(__file__).with_name('tasks.json').read_text())
REVISION = CATALOG['revision']
GROUPS = CATALOG['groups']
LEADERBOARD_POOL = 'leaderboard'
LEADERBOARD_EPISODES_PER_CLASS = 50
CATALOG_POOL = 'catalog'
FALLBACK_MEDIAN_SECONDS = CATALOG['composite_fallback_median_seconds']
# The published composite horizons are about 2.25 times the median human
# rollout length. Round up to the same 150-step increment as the registry.
ADDITIONAL_GROUPS = {
    'atomic': CATALOG['additional']['atomic'],
    'composite': {
        **CATALOG['additional']['composite'],
        **{task: ceil(seconds * 20 * 2.25 / 150) * 150
           for task, seconds in FALLBACK_MEDIAN_SECONDS.items()},
    },
}
LANGUAGE_KEY = 'annotation.human.task_description'
CAMERAS = (
    'video.robot0_agentview_left',
    'video.robot0_agentview_right',
    'video.robot0_eye_in_hand',
)
STATE_KEYS = (
    'state.gripper_qpos', 'state.base_position', 'state.base_rotation',
    'state.end_effector_position_relative', 'state.end_effector_rotation_relative',
)
ACTION_SIZES = {
    'action.end_effector_position': 3,
    'action.end_effector_rotation': 3,
    'action.gripper_close': 1,
    'action.base_motion': 4,
    'action.control_mode': 1,
}
MAX_CHUNK = 32


def _case(group, task, horizon, pool, index):
    task_id = f'robocasa365/{pool}/{task}/{index:02}'
    return {
        'task_id': task_id, 'task': task, 'group': group, 'pool': pool,
        'seed': _seed(task_id), 'horizon': horizon,
    }


def _seed(task_id):
    return int.from_bytes(sha256(task_id.encode()).digest()[:8], 'big')


def cases():
    for group, tasks in GROUPS.items():
        for task, horizon in tasks.items():
            for index in range(LEADERBOARD_EPISODES_PER_CLASS):
                yield _case(group, task, horizon, LEADERBOARD_POOL, index)


def catalog_cases():
    """Expose one reproducible episode per class outside the leaderboard."""
    for group, tasks in ADDITIONAL_GROUPS.items():
        for task, horizon in tasks.items():
            yield _case(group, task, horizon, CATALOG_POOL, 0)


def encode_case(case):
    return json.dumps(case, sort_keys=True, separators=(',', ':'))


def validate_case(case):
    parts = case['task_id'].split('/')
    if len(parts) != 4:
        raise ValueError('invalid RoboCasa365 case ID')
    prefix, pool, task, index = parts
    groups = ADDITIONAL_GROUPS if pool == CATALOG_POOL else GROUPS
    limit = {CATALOG_POOL: 1, LEADERBOARD_POOL: LEADERBOARD_EPISODES_PER_CLASS}.get(pool, 0)
    if (prefix != 'robocasa365' or pool != case['pool']
            or task != case['task'] or len(index) != 2 or not index.isascii()
            or not index.isdecimal() or int(index) >= limit
            or type(case['horizon']) is not int
            or groups.get(case['group'], {}).get(task) != case['horizon']
            or _seed(case['task_id']) != case['seed']):
        raise ValueError('invalid RoboCasa365 case identity')
    return case
