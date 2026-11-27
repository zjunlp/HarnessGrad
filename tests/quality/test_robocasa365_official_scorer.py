"""Pin the RoboCasa365 catalogs, public tool surface and sealed binary verdict."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from data.datasets import robocasa365 as dataset
from data.scaffolds import custom_split, registry
from data.scaffolds.asset_module import load as load_asset
from harnessgrad.datasets.runtime import execution_descriptor


def test_leaderboard_is_the_default_test_set():
    tasks, scorable, declared = registry.load_split('robocasa365')
    official = registry.official_split('robocasa365', tasks)
    assert len(tasks) == len(scorable) == 2500
    assert set(scorable.values()) == {dataset.METRIC} == {dataset.PROTOCOL.METRIC}
    assert len({case['seed'] for case in dataset.CASES.values()}) == len(tasks)
    assert declared == official == {'train': [], 'eval': dataset.OFFICIAL_EVAL}
    assert {case['pool'] for case in dataset.CASES.values()} == {'leaderboard'}
    with pytest.raises(ValueError, match='declared split has an empty train side'):
        custom_split.resolve('robocasa365', tasks, declared, 'declared', side='train')
    counts = Counter(dataset.CASES[tid]['task'] for tid in official['eval'])
    assert len(counts) == 50 and set(counts.values()) == {50}
    assert {name: len(group) for name, group in dataset.PROTOCOL.GROUPS.items()} == {
        'atomic_seen': 18, 'composite_seen': 16, 'composite_unseen': 16,
    }
    assert execution_descriptor('robocasa365')['python'] == '3.11'


@pytest.mark.parametrize('pool', ['train', 'validation'])
def test_old_study_episode_ids_are_no_longer_valid(pool):
    protocol = dataset.PROTOCOL
    task_id = f'robocasa365/{pool}/OpenDrawer/00'
    case = {**dataset.CASES['robocasa365/leaderboard/OpenDrawer/00'],
            'task_id': task_id, 'pool': pool, 'seed': protocol._seed(task_id)}
    assert task_id not in dataset.CASES
    with pytest.raises(ValueError, match='invalid RoboCasa365 case identity'):
        protocol.validate_case(case)


def test_full_catalog_leaves_study_split_open_and_preserves_leaderboard(monkeypatch):
    monkeypatch.setenv('HG_ROBOCASA365_POOL', 'all')
    expanded = load_asset(Path(dataset.__file__))
    monkeypatch.setitem(sys.modules, dataset.__name__, expanded)
    tasks, scorable, declared = registry.load_split('robocasa365')
    assert declared == {'train': [], 'eval': dataset.OFFICIAL_EVAL}
    assert len(tasks) == len(scorable) == 2815
    assert len({case['seed'] for case in expanded.CASES.values()}) == len(tasks)
    assert len({case['task'] for case in expanded.CASES.values()}) == 365
    assert all(expanded.CASES[task_id] == case for task_id, case in dataset.CASES.items())
    with pytest.raises(ValueError, match='declared split has an empty train side'):
        custom_split.resolve('robocasa365', tasks, declared, 'declared', side='train')

    added = list(expanded.PROTOCOL.catalog_cases())
    assert registry.official_split('robocasa365', tasks) == declared
    assert custom_split.resolve('robocasa365', tasks, declared, 'official', side='eval')[0] == declared
    with pytest.raises(ValueError, match='split has an empty side'):
        custom_split.resolve('robocasa365', tasks, declared, 'official', side='train')
    assert Counter(case['group'] for case in added) == {'atomic': 47, 'composite': 268}
    assert len({case['task'] for case in added}) == 315
    assert not {case['task'] for case in added} & {case['task'] for case in dataset.CASES.values()}
    assert len(expanded.PROTOCOL.FALLBACK_MEDIAN_SECONDS) == 48
    for case in added:
        assert expanded.PROTOCOL.validate_case(case) == case
        assert case['task_id'] in expanded.SETUP and case['task_id'] in expanded.VERIFY
    assert expanded.CASES['robocasa365/catalog/CloseDrawer/00']['horizon'] == 450
    fallback = expanded.CASES['robocasa365/catalog/BoilEggs/00']
    assert fallback['horizon'] == 1050
    with pytest.raises(ValueError, match='invalid RoboCasa365 case identity'):
        expanded.PROTOCOL.validate_case({**fallback, 'horizon': 1000})
    with pytest.raises(ValueError, match='expected a measured leaderboard reward'):
        expanded.aggregate({fallback['task_id']: 1})


def test_frozen_catalog_study_and_official_test_agree_across_entries(monkeypatch, tmp_path):
    from console.submissions import _metadata
    from harnessgrad.orchestration.selected_dataset import SelectionOptions, resolve_catalog_partition
    from tools.serve_ui import task_options

    monkeypatch.setenv('HG_ROBOCASA365_POOL', 'all')
    expanded = load_asset(Path(dataset.__file__))
    monkeypatch.setitem(sys.modules, dataset.__name__, expanded)
    name = 'robocasa365'
    tasks, _, declared = registry.load_split(name)
    relative = 'experiments/splits/robocasa365/study.json'
    split, receipt = custom_split.read(name, tasks, dataset.PLATFORM / relative)
    assert receipt['counts'] == {'train': 252, 'eval': 63, 'excluded': 2500}
    assert receipt['official_eval_reused_for_train'] == 0
    assert set(receipt['manifest']['excluded']) == set(declared['eval'])
    strata = {
        tid: ('atomic' if case['group'] == 'atomic' else
              'composite_estimated' if case['task'] in expanded.PROTOCOL.FALLBACK_MEDIAN_SECONDS
              else 'composite_upstream')
        for tid, case in expanded.CASES.items() if case['pool'] == 'catalog'
    }
    assert set(split['train']) | set(split['eval']) == set(strata)
    quotas = {'atomic': 9, 'composite_upstream': 44, 'composite_estimated': 10}
    assert receipt['manifest']['recipe'] == {
        'algorithm': 'robocasa365-catalog-v1', 'seed': 0, 'eval_by_stratum': quotas}
    for stratum, quota in quotas.items():
        order = sorted((tid for tid, group in strata.items() if group == stratum),
                       key=lambda tid: (sha256(f'robocasa365-catalog-v1:0:{tid}'.encode()).digest(), tid))
        assert set(order[:quota]) == {tid for tid in split['eval'] if strata[tid] == stratum}

    donor = tmp_path / 'train-run'
    donor.mkdir()
    (donor / 'run_meta.json').write_text(json.dumps({
        'dataset': name, 'split_sha': registry.split_sha(split), 'split_protocol': receipt}))
    policy = SimpleNamespace(note=lambda *args, **kwargs: None)
    for choice, expected in ((str(dataset.PLATFORM / relative), split), ('official', declared)):
        options = SelectionOptions(name, choice, 'eval', None, 'all', 'eval-run', donor.name, True, False)
        partition = resolve_catalog_partition(options, registry, runs_root=tmp_path,
                                              scenario=None, reporter=policy)
        assert partition.split == expected
    with pytest.raises(ValueError, match='official test differs'):
        custom_split.check_eval_pair(donor, name, {'train': [], 'eval': declared['eval'][:-1]},
                                    None, official_tasks=tasks)
    with pytest.raises(ValueError, match='empty train side'):
        _metadata({'dataset': name, 'split': 'declared', 'side': 'train'})
    assert _metadata({'dataset': name, 'split': relative, 'side': 'train'})['tasks'] == 252
    assert _metadata({'dataset': name, 'split': relative, 'side': 'eval'})['tasks'] == 63
    assert _metadata({'dataset': name, 'split': 'official', 'side': 'eval'})['tasks'] == 2500
    assert Counter(row['side'] for row in task_options(name)['tasks']) == {'eval': 2500, 'unassigned': 315}


def test_balanced_study_selects_fixed_test_episodes_after_checkpoint_admission(monkeypatch, tmp_path):
    from harnessgrad.orchestration.selected_dataset import (
        SelectionOptions, resolve_catalog_partition, select_run_tasks,
    )

    monkeypatch.setenv('HG_ROBOCASA365_POOL', 'all')
    expanded = load_asset(Path(dataset.__file__))
    monkeypatch.setitem(sys.modules, dataset.__name__, expanded)
    name = 'robocasa365'
    tasks, _, declared = registry.load_split(name)
    directory = dataset.PLATFORM / 'experiments/splits/robocasa365'
    split, receipt = custom_split.read(name, tasks, directory / 'study-100.json')
    test = (directory / 'test-100.txt').read_text(encoding='utf-8').splitlines()
    assert receipt['counts'] == {'train': 100, 'eval': 100, 'excluded': 2615}
    assert set(declared['eval']) <= set(receipt['manifest']['excluded'])
    assert len(test) == 100 and len(set(test)) == 100 and set(test) <= set(declared['eval'])
    assert Counter(expanded.CASES[tid]['task'] for tid in test) == dict.fromkeys(
        (expanded.CASES[tid]['task'] for tid in declared['eval']), 2)
    assert Counter(expanded.CASES[tid]['group'] for tid in test) == {
        'atomic_seen': 36, 'composite_seen': 32, 'composite_unseen': 32}
    for members in split.values():
        cases = [expanded.CASES[tid] for tid in members]
        assert len({case['task'] for case in cases}) == 100
        assert Counter(case['group'] for case in cases) == {'atomic': 15, 'composite': 85}
        assert sum(case['task'] in expanded.PROTOCOL.FALLBACK_MEDIAN_SECONDS for case in cases) == 15
    assert len({expanded.CASES[tid]['task'] for tid in split['train'] + split['eval'] + test}) == 250

    manifest = receipt['manifest']
    official_id = test[0]
    swapped = {**manifest, 'split': {**split, 'train': [official_id, *split['train'][1:]]},
               'excluded': [split['train'][0], *(tid for tid in manifest['excluded'] if tid != official_id)]}
    with pytest.raises(ValueError, match='subset contains official'):
        custom_split.validate(name, tasks, swapped)

    donor = tmp_path / 'train'
    donor.mkdir()
    (donor / 'run_meta.json').write_text(json.dumps({
        'dataset': name, 'split_sha': registry.split_sha(split), 'split_protocol': receipt}))
    options = SelectionOptions(name, 'official', 'eval', ','.join(test), 'all',
                               'test', donor.name, True, False)
    partition = resolve_catalog_partition(options, registry, runs_root=tmp_path,
                                          scenario=None, reporter=SimpleNamespace(note=lambda *a, **kw: None))
    assert set(select_run_tasks(options, partition, None).ids) == set(test)


def test_aggregate_keeps_partial_coverage_visible():
    ids = dataset.OFFICIAL_EVAL
    scores = dict.fromkeys(ids, 0)
    scores[ids[0]] = scores[ids[1]] = 1
    result = dataset.aggregate(scores)
    assert result['complete']
    assert result['success_rate'] == pytest.approx(2 / 2500)
    assert result['groups']['atomic_seen']['success_rate'] == pytest.approx(2 / 900)
    partial = dataset.aggregate({ids[0]: 1, ids[50]: 0, ids[51]: 0})
    assert partial['success_rate'] == 0.5
    assert partial['rollouts_measured'] == 3 and not partial['complete']


@pytest.fixture
def protocol_modules(monkeypatch):
    monkeypatch.setitem(sys.modules, 'protocol', dataset.PROTOCOL)
    episode = load_asset(dataset.ASSETS / 'simulator.py').Episode
    check = load_asset(dataset.ASSETS / 'verify/check.py')
    return episode, check


def test_gym_success_is_latched_without_exposing_it(protocol_modules, monkeypatch):
    episode_type, _ = protocol_modules
    protocol = dataset.PROTOCOL
    state = SimpleNamespace(tolist=lambda: [0.0])
    observation = {protocol.LANGUAGE_KEY: 'Open the cabinet.'}
    observation.update(dict.fromkeys(protocol.STATE_KEYS, state))

    class Gym:
        def __init__(self):
            self.actions = []
        def step(self, action):
            self.actions.append(action)
            return observation, 0.0, False, False, {
                'success': len(self.actions) == 1, 'sim_state': 'private',
            }

    monkeypatch.setitem(sys.modules, 'numpy', SimpleNamespace(
        asarray=lambda values, **_: values, float64='float64',
    ))
    env = Gym()
    episode = episode_type(env, observation, 2)
    action = {key: [0] * size for key, size in protocol.ACTION_SIZES.items()}
    malformed = {**action, 'action.gripper_close': [float('nan')]}
    assert 'error' in episode.handle('step_many', {'actions': [action, malformed]})
    assert 'error' in episode.handle('reset', {})
    assert env.actions == []
    showed = episode.handle('step', {'action': action, 'cameras': []})
    assert set(showed) == {'instruction', 'step', 'horizon', 'ended', 'state', 'images'}
    assert not showed['ended'] and episode.first_success_step == 1
    ended = episode.handle('step_many', {'actions': [action, action], 'cameras': []})
    assert ended['ended'] and ended['step'] == 2
    assert len(env.actions) == 2 and episode.first_success_step == 1


@pytest.mark.parametrize('end', ['batch', 'horizon', 'terminated', 'truncated'])
def test_step_many_returns_the_same_last_frames_as_single_steps(protocol_modules, monkeypatch, end):
    episode_type, _ = protocol_modules
    protocol = dataset.PROTOCOL
    native_end = end in ('terminated', 'truncated')
    horizon = 3 if end == 'horizon' else 10

    class Gym:
        def __init__(self):
            self.step_no = 0
            self.frames = []
            self.env = SimpleNamespace(ignore_done=not native_end, viewer_get_obs=False,
                                       control_timestep=0.05, model_timestep=0.002,
                                       sim=SimpleNamespace(render=self.render))

        def render(self, *, camera_name, width, height, depth=False):
            self.frames.append((self.step_no, camera_name))
            return f'{camera_name} at step {self.step_no}'

        def observation(self):
            state = SimpleNamespace(tolist=lambda: [self.step_no])
            return {
                protocol.LANGUAGE_KEY: 'Open the cabinet.',
                **dict.fromkeys(protocol.STATE_KEYS, state),
                **{camera: self.env.sim.render(camera_name=camera, width=2, height=2)
                   for camera in protocol.CAMERAS},
            }

        def step(self, action):
            self.step_no += 1
            return (self.observation(), 0,
                    end == 'terminated' and self.step_no == 2,
                    end == 'truncated' and self.step_no == 2,
                    {'success': self.step_no == 1})

    class OrderEnforcing:
        def __init__(self, env):
            self.env = self.unwrapped = env

        def step(self, action):
            return self.env.step(action)

    monkeypatch.setitem(sys.modules, 'gymnasium.wrappers', SimpleNamespace(OrderEnforcing=OrderEnforcing))
    monkeypatch.setitem(sys.modules, 'robocasa.wrappers.gym_wrapper', SimpleNamespace(RoboCasaGymEnv=Gym))
    monkeypatch.setitem(sys.modules, 'numpy', SimpleNamespace(
        asarray=lambda values, **_: values, float64='float64', uint8='uint8',
        zeros=lambda shape, dtype: 'discarded frame',
    ))
    monkeypatch.setitem(episode_type.observe.__globals__, '_png_frame', lambda frame: frame)

    def start():
        env = Gym()
        episode = episode_type(OrderEnforcing(env), env.observation(), horizon)
        env.frames.clear()
        return episode, env

    action = {key: [0] * size for key, size in protocol.ACTION_SIZES.items()}
    sequential, baseline = start()
    for _ in range(4):
        expected = sequential.handle('step', {'action': action})
        if expected['ended']:
            break

    batched, env = start()
    actual = batched.handle('step_many', {'actions': [action] * 4})
    assert actual == expected
    assert batched.first_success_step == sequential.first_success_step == 1
    assert not native_end or env.step_no == 2
    assert env.frames == (baseline.frames if native_end else baseline.frames[-len(protocol.CAMERAS):])
    if not actual['ended']:
        # A following request can observe any camera and take another full step.
        assert batched.handle('observe', {}) == actual
        assert batched.handle('step', {'action': action}) == sequential.handle('step', {'action': action})


@pytest.mark.parametrize('success_step,expected', [(None, False), (1, True)])
def test_verifier_checks_sealed_native_episode(protocol_modules, success_step, expected):
    _, check = protocol_modules
    protocol = dataset.PROTOCOL
    task = dataset.OFFICIAL_EVAL[0]
    case = protocol.encode_case(dataset.CASES[task]).encode()
    config = json.dumps({
        'case': dataset.CASES[task], 'bundle': 'b' * 64, 'episode_schema': protocol.EPISODE_SCHEMA,
        'metric': protocol.METRIC, 'revision': protocol.REVISION, 'scene_split': protocol.SCENE_SPLIT,
    }).encode()
    digest = sha256(config).hexdigest()
    sealed = {
        'schema': 'harnessgrad.interaction-record.v1',
        'binding': {'task_id': task, 'input_sha256': 'a' * 64},
        'payload': {
            'schema': protocol.EPISODE_SCHEMA, 'task_id': task, 'revision': protocol.REVISION,
            'scene_split': 'pretrain', 'bundle': 'b' * 64, 'steps': 2,
            'input_sha256': 'a' * 64, 'case_sha256': sha256(case).hexdigest(),
            'first_success_step': success_step, 'terminal': False, 'python_version': [3, 11, 18],
        },
    }
    assert check.evaluate(config, sealed, digest)['success'] is expected
    with pytest.raises(ValueError, match='input checksum'):
        check.evaluate(config, sealed, 'c' * 64)
    sealed['payload']['bundle'] = 'c' * 64
    with pytest.raises(ValueError, match='episode identity'):
        check.evaluate(config, sealed, digest)
    sealed['payload']['bundle'] = 'b' * 64
    sealed['payload']['first_success_step'] = 3
    with pytest.raises(ValueError, match='invalid step counts'):
        check.evaluate(config, sealed, digest)


def test_packages_are_pinned_without_loading_dependencies():
    entries = dataset.downloads()
    assert len({entry['dst'] for entry in entries}) == len(entries)
    assert all(len(entry['sha256']) == 64 for entry in entries)
    assert all(not Path(entry['dst']).is_absolute() for entry in entries)
    pins = execution_descriptor('robocasa365')['pins']
    assert (pins['mujoco'], pins['numpy']) == ('3.3.1', '2.2.5')
