"""State observation must use the real writable create policy and fail closed."""
import json
from types import SimpleNamespace

import pytest

from eval.containers import container as backend
from eval.service import container_observation as co, services
from eval.run import runner

IMAGE = 'sha256:' + 'a' * 64


def install(monkeypatch, tmp_path, *, failure='', networks=None, internal=True, defaults=None):
    calls = []
    defaults = defaults or {'User': '', 'WorkingDir': '', 'Entrypoint': None}
    for name, value in [('cpu.max', '100000 100000'), ('memory.max', str(512*2**20)), ('pids.max', '1024')]:
        (tmp_path/name).write_text(value)
    monkeypatch.setattr(co, '_cgroup_dir', lambda pid: co.cgroups.Layout('cgroup-v2',
            dict.fromkeys(('cpu', 'memory', 'pids'), tmp_path),
            dict.fromkeys(('cpu', 'memory', 'pids'), tmp_path)))
    if failure == 'cgroup':
        (tmp_path/'cpu.max').unlink()
    def create(name, **kwargs):
        calls.append(('create', name, kwargs))
        if failure == 'create':
            raise backend.ContainerUnavailable('authored create')
    def start(name):
        calls.append(('start', name))
        if failure == 'start':
            raise backend.ContainerUnavailable('authored start')
    monkeypatch.setattr(backend, 'create', create)
    monkeypatch.setattr(backend, 'start', start)
    monkeypatch.setattr(backend, 'network_connect', lambda name, net: calls.append(('attach', name, net)))
    monkeypatch.setattr(services, 'create_network', lambda *args: calls.append(('network-create', args)) or 'owned-id')
    monkeypatch.setattr(services, 'remove_network', lambda n: calls.append(('network-remove', n)) or [])
    def command(argv, stdin=None):
        calls.append(tuple(argv))
        if argv[:3] == ['docker', 'network', 'inspect']:
            data = [{'Internal': internal}]
        elif argv[:3] == ['docker', 'image', 'inspect']:
            data = [{'Config': defaults}]
        elif argv[:2] == ['docker', 'inspect']:
            if failure == 'inspect':
                return SimpleNamespace(returncode=1, stdout='', stderr='authored inspect')
            data = [{'HostConfig': {'NanoCpus': 10**9, 'Memory': 512*2**20, 'PidsLimit':1024,
                                   'NetworkMode':'owned-id', 'ReadonlyRootfs':False},
                     'Config':defaults, 'State':{'Pid':123}, 'Image':IMAGE,
                     'NetworkSettings':{'Networks':{name:{'NetworkID':name} for name in (networks or ['owned-id'])}}}]
        else:
            data = ''
        return SimpleNamespace(returncode=0, stdout=json.dumps(data) if data else '', stderr='')
    return calls, command


def test_state_observer_uses_canonical_create_defaults_and_internal_readback(monkeypatch, tmp_path):
    calls, command = install(monkeypatch, tmp_path, defaults={'User':'','WorkingDir':'/image','Entrypoint':['/entry']})
    observed = co.observe_for(IMAGE, {'cpus':1, 'memory_mb':512}, network='none', run_id='r',
                              read_only=False, container_state=True, runner=command).receipt()
    assert observed['ok'] and observed['launch_mode']=='container-state'
    assert observed['declared']['rootfs_readonly'] is False
    assert observed['declared']['network_policy']=='none'
    assert observed['observed']['network_internal'] is True
    create = next(c for c in calls if c[0]=='create')
    assert create[2]=={'image':IMAGE,'limits':{'cpus':1,'pids':1024,'memory':'512m'},
                       'network':'owned-id','run_id':'r','task_id':'container-observation'}
    assert not any(c[:2]==('docker','run') for c in calls)
    assert calls[-1]==('network-remove','owned-id')
    assert any(c[:3]==('docker','rm','-f') and c[-1]==create[1] for c in calls)


@pytest.mark.parametrize('failure', ['create','start','inspect','cgroup'])
def test_state_probe_failures_remove_only_owned_resources(monkeypatch,tmp_path,failure):
    calls, command=install(monkeypatch,tmp_path,failure=failure)
    if failure=='cgroup':
        result=co.observe_for(IMAGE,{'cpus':1,'memory_mb':512},network='none',
                             read_only=False,container_state=True,runner=command)
        assert not result.ok and any('cpu.max' in v for v in result.unobserved)
    else:
        with pytest.raises(co.ObservationError,match='authored'):
            co.observe_for(IMAGE,{'cpus':1,'memory_mb':512},network='none',
                           read_only=False,container_state=True,runner=command)
    assert calls[-1]==('network-remove','owned-id')
    assert any(c[:3]==('docker','rm','-f') for c in calls)


@pytest.mark.parametrize('policy,networks,expected', [
    ('bridge',['owned-id','bridge'],True),('bridge',['owned-id'],False),
    ('none',['owned-id','bridge'],False),('none',['owned-id','extra'],False)])
def test_state_network_attachments_are_observed_not_inferred(monkeypatch,tmp_path,policy,networks,expected):
    calls,command=install(monkeypatch,tmp_path,networks=networks)
    result=co.observe_for(IMAGE,{'cpus':1,'memory_mb':512},network=policy,
                          read_only=False,container_state=True,runner=command)
    assert result.ok is expected
    assert bool([c for c in calls if c[0]=='attach']) is (policy=='bridge')
    assert calls[-1]==('network-remove','owned-id')


def test_noninternal_primary_network_fails_before_start(monkeypatch,tmp_path):
    calls,command=install(monkeypatch,tmp_path,internal=False)
    with pytest.raises(co.ObservationError,match='not observed internal'):
        co.observe_for(IMAGE,{'cpus':1,'memory_mb':512},network='none',
                       read_only=False,container_state=True,runner=command)
    assert not any(c[0]=='create' for c in calls)
    assert calls[-1]==('network-remove','owned-id')


def test_unpinned_state_image_is_refused_before_any_acquisition(monkeypatch,tmp_path):
    calls,command=install(monkeypatch,tmp_path)
    with pytest.raises(co.ObservationError,match='pinned'):
        co.observe_for('unowned:latest',{},network='none',read_only=False,container_state=True,runner=command)
    assert calls==[]


def test_direct_public_run_one_observes_state_before_admission(monkeypatch,tmp_path):
    (tmp_path/'harness.json').write_text(json.dumps({'entrypoint':'actor.py','env_kinds':['exec']}))
    (tmp_path/'actor.py').write_text('')
    env={'kind':'exec','state':'container','image':IMAGE,'network':'none'}
    observed={'ok':True,'launch_mode':'container-state','image':IMAGE,
              'declared':{'rootfs_readonly':False,'network_policy':'none'},
              'observed':{'network_internal':True},'covers':list(co.COVERS)}
    calls=[]
    monkeypatch.setattr(runner,'_observations_for',lambda envs,tasks,**kw: calls.append('observe') or {'t':observed})
    monkeypatch.setattr(runner,'_run_one',lambda *args,**kw: calls.append('execute') or {'task_id':'t'})
    result=runner.run_one(tmp_path,{'task_id':'t'},env=env)
    assert calls==['observe','execute'] and result['env_observation'] is observed


def test_image_inspect_omitted_empty_defaults_are_equivalent(monkeypatch,tmp_path):
    calls,command=install(monkeypatch,tmp_path)
    def omit(argv,stdin=None):
        if argv[:3]==['docker','image','inspect']:
            return SimpleNamespace(returncode=0,stdout=json.dumps([{'Config':{}}]),stderr='')
        return command(argv,stdin)
    result=co.observe_for(IMAGE,{'cpus':1,'memory_mb':512},network='none',
                          read_only=False,container_state=True,runner=omit)
    assert result.ok


def test_inherited_entrypoint_mismatch_is_not_authorization(monkeypatch,tmp_path):
    calls,command=install(monkeypatch,tmp_path)
    def mismatch(argv,stdin=None):
        if argv[:3]==['docker','image','inspect']:
            return SimpleNamespace(returncode=0,stdout=json.dumps([{'Config':{
                'User':'','WorkingDir':'','Entrypoint':['/image-entry']}}]),stderr='')
        return command(argv,stdin)
    result=co.observe_for(IMAGE,{'cpus':1,'memory_mb':512},network='none',
                          read_only=False,container_state=True,runner=mismatch)
    assert not result.ok and any('entrypoint' in p for p in result.problems)
    assert calls[-1]==('network-remove','owned-id')
