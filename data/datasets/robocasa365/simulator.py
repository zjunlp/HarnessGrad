"""Trusted RoboCasa Gym bridge. Import the pinned simulator only inside its runtime."""
from __future__ import annotations

import base64
import ctypes
import io
import math
import os
from pathlib import Path
import random
import sys

from protocol import (
    ACTION_SIZES, CAMERAS, FALLBACK_MEDIAN_SECONDS, GROUPS, LANGUAGE_KEY, MAX_CHUNK, REVISION, SCENE_SPLIT,
    STATE_KEYS,
)


def open_env(case, prepared, osmesa):
    os.environ.update(MUJOCO_GL='osmesa', PYOPENGL_PLATFORM='osmesa',
                      LP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
                      NUMBA_CACHE_DIR=os.path.join(os.environ['XDG_CACHE_HOME'], 'numba'))
    if osmesa:
        path = str(Path(osmesa).resolve(strict=True))
        ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
    sys.path[:0] = [str(Path(prepared) / source) for source in ('robocasa', 'robosuite')]

    import gymnasium as gym
    import numpy as np
    import robocasa  # Registers the official Gym environments.
    from robocasa.utils.dataset_registry import TARGET_TASKS
    from robocasa.utils.dataset_registry_utils import get_task_horizon

    try:
        upstream_horizon = get_task_horizon(case['task'])
    except ValueError:
        upstream_horizon = None
    expected_horizon = None if case['task'] in FALLBACK_MEDIAN_SECONDS else case['horizon']
    if (robocasa.__version__ != '1.0.1'
            or TARGET_TASKS != {key: list(tasks) for key, tasks in GROUPS.items()}
            or upstream_horizon != expected_horizon):
        raise ValueError(f'RoboCasa365 task registry differs from {REVISION}')

    # Upstream uses the environment RNG for the scenario and also draws some
    # fixture visualization colors from NumPy's legacy process RNG.
    random.seed(case['seed'])
    np.random.seed(case['seed'] % (2**32))
    env = gym.make(f"robocasa/{case['task']}", split=SCENE_SPLIT,
                   seed=case['seed'], disable_env_checker=True)
    try:
        observation, _ = env.reset(seed=case['seed'])
        return env, observation
    except BaseException:
        env.close()
        raise


def _png_frame(rgb):
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format='PNG', compress_level=1)
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def _bounded_vector(value, size):
    return (isinstance(value, list) and len(value) == size
            and all(type(item) in (int, float) and -1 <= item <= 1 for item in value))


class Episode:
    def __init__(self, env, observation, horizon):
        self.env = env
        self.observation = observation
        self.horizon = horizon
        self.steps = 0
        self.ended = False
        self.first_success_step = None

    def observe(self, cameras):
        images = {name: _png_frame(self.observation[name]) for name in cameras}
        state = {name: self.observation[name].tolist() for name in STATE_KEYS}
        if not all(math.isfinite(value) for vector in state.values() for value in vector):
            raise ValueError('non-finite RoboCasa365 policy observation')
        return {
            'instruction': self.observation[LANGUAGE_KEY],
            'step': self.steps, 'horizon': self.horizon, 'ended': self.ended,
            'state': state, 'images': images,
        }

    def handle(self, op, args):
        if not isinstance(args, dict):
            return {'error': 'arguments must be an object'}
        if op == 'observe':
            required = set()
        elif op == 'step':
            required = {'action'}
        elif op == 'step_many':
            required = {'actions'}
        else:
            return {'error': 'operation must be observe, step or step_many'}
        if not required <= args.keys() <= required | {'cameras'}:
            return {'error': 'unexpected or missing arguments'}
        cameras = args.get('cameras', list(CAMERAS))
        if (not isinstance(cameras, list) or any(type(name) is not str or name not in CAMERAS for name in cameras)
                or len(cameras) != len(set(cameras))):
            return {'error': 'cameras must be a list of distinct policy camera names'}
        if op == 'observe':
            return self.observe(cameras)
        actions = [args['action']] if op == 'step' else args['actions']
        if (not isinstance(actions, list) or not 1 <= len(actions) <= MAX_CHUNK
                or any(not isinstance(action, dict) or action.keys() != ACTION_SIZES.keys()
                       or any(not _bounded_vector(action[key], size) for key, size in ACTION_SIZES.items())
                       for action in actions)):
            return {'error': f'provide 1 to {MAX_CHUNK} valid RoboCasa actions'}
        if self.ended:
            return {'error': 'episode has ended'}
        return self.advance(actions, cameras)

    def advance(self, actions, cameras):
        import numpy as np

        for action in actions:
            values = {key: np.asarray(value, dtype=np.float64) for key, value in action.items()}
            self.observation, _, terminated, truncated, info = self.env.step(values)
            self.steps += 1
            if bool(info['success']) and self.first_success_step is None:
                self.first_success_step = self.steps
            if terminated or truncated or self.steps == self.horizon:
                self.ended = True
                break
        return self.observe(cameras)
