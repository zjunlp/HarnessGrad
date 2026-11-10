"""Kernel layouts are synthetic; no host cgroup writes or Docker are required."""
import json
from pathlib import Path

import pytest

from eval.containers import cgroups, observed
from eval.service import container_observation as co


def fixture(tmp_path, version=1, *, mount_root='/', hybrid=False):
    proc = tmp_path / 'proc'; (proc / '42').mkdir(parents=True); (proc / 'self').mkdir()
    lines, mounts, paths, roots = [], [], {}, {}
    if version == 1:
        for i, (key, controllers) in enumerate((('cpu','cpu,cpuacct'), ('memory','memory'), ('pids','pids')), 1):
            root = tmp_path / ('mount-' + key); path = root / 'container'; path.mkdir(parents=True)
            membership = mount_root.rstrip('/') + '/container'
            lines.append(f'{i}:{controllers}:{membership}')
            mounts.append(f'{i} 0 0:{i} {mount_root} {root} rw - cgroup cgroup rw,{controllers}')
            paths[key], roots[key] = path, root
        values = {'cpu': {'cpu.cfs_quota_us':'200000','cpu.cfs_period_us':'100000'},
                  'memory': {'memory.limit_in_bytes':str(512 << 20), 'memory.memsw.limit_in_bytes':str(512 << 20),
                             'memory.oom_control':'oom_kill_disable 0\nunder_oom 0\noom_kill 0'},
                  'pids': {'pids.max':'64'}}
        if hybrid:
            unified = tmp_path / 'unified'; unified.mkdir()
            lines.append('0::/container'); mounts.append(f'9 0 0:9 / {unified} rw - cgroup2 cgroup rw')
    else:
        root = tmp_path / 'unified'; path = root / 'container'; path.mkdir(parents=True)
        for key in ('cpu','memory','pids'): paths[key], roots[key] = path, root
        lines = ['0::/container']; mounts = [f'1 0 0:1 / {root} rw - cgroup2 cgroup rw']
        values = {'cpu': {'cpu.max':'200000 100000'},
                  'memory': {'memory.max':str(512 << 20), 'memory.swap.max':'0', 'memory.events':'oom 0\noom_kill 0'},
                  'pids': {'pids.max':'64'}}
    for key, files in values.items():
        for name, value in files.items(): (paths[key] / name).write_text(value)
    (proc / '42' / 'cgroup').write_text('\n'.join(lines))
    (proc / 'self' / 'mountinfo').write_text('\n'.join(mounts))
    return proc, paths, roots


@pytest.mark.parametrize('version', [1,2])
def test_both_versions_read_kernel_caps_and_oom(tmp_path, version):
    proc, paths, roots = fixture(tmp_path,version)
    layout = cgroups.resolve(42, proc=proc); got = cgroups.limits(layout)
    assert layout.backend == f'cgroup-v{version}' and layout.paths == paths
    assert got['cpu_millis'] == 2000 and got['memory_bytes'] == 512 << 20
    assert got['swap_bytes'] == 0 and got['pids'] == 64 and got['pids_observed']
    assert cgroups.oom_events(layout)['oom_kill'] == 0


def test_v1_subtree_mounts_and_hybrid_resolve_actual_controllers(tmp_path):
    proc, paths, roots = fixture(tmp_path, mount_root='/delegated', hybrid=True)
    layout = cgroups.resolve(42, proc=proc)
    assert layout.backend == 'cgroup-v1' and layout.paths == paths


@pytest.mark.parametrize('malformation', ['traversal', 'missing-controller', 'invisible-root'])
def test_unprovable_proc_layout_is_refused(tmp_path, malformation):
    proc, paths, roots = fixture(tmp_path)
    path = proc / '42' / 'cgroup'; text = path.read_text()
    if malformation == 'traversal': text = text.replace('/container','/../container')
    elif malformation == 'missing-controller': text = '\n'.join(x for x in text.splitlines() if ':memory:' not in x)
    else:
        mounts = proc / 'self' / 'mountinfo'; mounts.write_text(mounts.read_text().replace(' / ', ' /invisible '))
    path.write_text(text)
    with pytest.raises(ValueError): cgroups.resolve(42,proc=proc)


def test_escaped_mountpoint_is_decoded(tmp_path):
    proc, paths, roots = fixture(tmp_path)
    mount = proc / 'self' / 'mountinfo'
    root = roots['cpu']; renamed = root.with_name('cpu space'); root.rename(renamed)
    mount.write_text(mount.read_text().replace(str(root),str(renamed).replace(' ',r'\040')))
    assert cgroups.resolve(42,proc=proc).paths['cpu'] == renamed / 'container'


@pytest.mark.parametrize('name,value,key', [('cpu.cfs_quota_us','-1','cpu_millis'),
    ('cpu.cfs_period_us','0','cpu_millis'), ('memory.limit_in_bytes',str(2**63-4096),'memory_bytes'),
    ('memory.memsw.limit_in_bytes','invalid','swap_bytes')])
def test_unknown_or_unlimited_values_are_never_claimed_as_caps(tmp_path,name,value,key):
    proc, paths, roots = fixture(tmp_path)
    (paths['cpu' if name.startswith('cpu') else 'memory'] / name).write_text(value)
    assert cgroups.limits(cgroups.resolve(42,proc=proc))[key] is None


def test_v1_swap_is_memsw_minus_memory_and_missing_accounting_stays_unknown(tmp_path):
    proc, paths, roots = fixture(tmp_path)
    layout = cgroups.resolve(42,proc=proc)
    (paths['memory'] / 'memory.memsw.limit_in_bytes').write_text(str(768 << 20))
    assert cgroups.limits(layout)['swap_bytes'] == 256 << 20
    (paths['memory'] / 'memory.memsw.limit_in_bytes').unlink()
    assert cgroups.limits(layout)['swap_bytes'] is None


def test_parent_pid_limit_is_preserved_and_missing_child_is_unknown(tmp_path):
    proc, paths, roots = fixture(tmp_path)
    (paths['pids'] / 'pids.max').write_text('max'); (roots['pids'] / 'pids.max').write_text('32')
    layout = cgroups.resolve(42,proc=proc)
    assert cgroups.limits(layout)['pids'] == 32
    (paths['pids'] / 'pids.max').unlink()
    assert cgroups.limits(layout)['pids_observed'] is False


def test_v1_oom_counter_required_and_kill_retained(tmp_path):
    proc, paths, roots = fixture(tmp_path); layout = cgroups.resolve(42,proc=proc)
    (paths['memory'] / 'memory.oom_control').write_text('under_oom 0')
    with pytest.raises(ValueError,match='kill counter'): cgroups.oom_events(layout)
    (paths['memory'] / 'memory.oom_control').write_text('under_oom 0\noom_kill 1')
    assert cgroups.oom_events(layout)['oom_kill'] == 1


@pytest.mark.parametrize('version', [1,2])
def test_appworld_probe_observes_kernel_version_without_docker_inference(tmp_path,monkeypatch,version):
    from test_container_observation import _fake_runner
    proc, paths, roots = fixture(tmp_path,version)
    (paths['pids'] / 'pids.max').write_text('1024')
    monkeypatch.setattr(co,'_cgroup_dir',lambda pid: cgroups.resolve(42,proc=proc))
    runner,calls = _fake_runner(nano_cpus=2_000_000_000,memory=512 << 20,pids=1024)
    receipt = co.observe('fixture:image',{'cpus':2,'memory_mb':512},runner=runner).receipt()
    assert receipt['ok'] and receipt['observed']['resource_backend'] == f'cgroup-v{version}'
    assert receipt['observed']['cgroup_evidence']['paths'] == {k:str(v) for k,v in paths.items()}
    assert calls[-1][:3] == ['docker','rm','-f']


def test_appworld_v1_missing_cpu_is_unobserved_even_with_matching_docker_flags(tmp_path,monkeypatch):
    from test_container_observation import _fake_runner
    proc, paths, roots = fixture(tmp_path)
    (paths['cpu'] / 'cpu.cfs_quota_us').unlink()
    monkeypatch.setattr(co,'_cgroup_dir',lambda pid:cgroups.resolve(42,proc=proc))
    runner,_ = _fake_runner(memory=512 << 20,pids=64)
    receipt = co.observe('fixture:image',{'cpus':2,'memory_mb':512},runner=runner).receipt()
    assert not receipt['ok'] and any('cpus' in row for row in receipt['unobserved'])


def test_strict_worker_supports_v1_and_refuses_missing_swap_evidence(tmp_path,monkeypatch):
    proc,paths,roots = fixture(tmp_path); layout=cgroups.resolve(42,proc=proc)
    monkeypatch.setattr(cgroups,'resolve',lambda pid:layout)
    binding={'adapter_id':'fixture','run_id':'r','trial_id':'t','manifest_sha256':'a'*64}
    policy={'network':'none','filesystems':{'/work':{'bytes':4096,'inodes':16}},
            'resources':{'cpu_millis':2000,'memory_bytes':512 << 20,'swap_bytes':0,'pids':64,'timeout_ms':1000}}
    kernel={'interfaces':['lo'],'mounts':[{'path':'/','writable':False},
        {'path':'/work','writable':True,'fstype':'tmpfs','character_device':False,'bytes':4096,'inodes':16,'device':'1'}]}
    info={'State':{'Pid':42},'Mounts':[],'Id':'a'*64,'Image':'sha256:'+'b'*64,
          'HostConfig':{'NetworkMode':'none'},'NetworkSettings':{'Networks':{}}}
    def command(*args): return json.dumps([info] if args[0]=='inspect' else kernel).encode()
    receipt=observed.observe('fixture',binding,policy,supervisor={'timeout_ms':1000},python='/python',expected_mounts={},command=command)
    assert receipt['admission']['status']=='passed'
    assert receipt['observed']['resource_backend']=='cgroup-v1'
    (paths['memory'] / 'memory.memsw.limit_in_bytes').unlink()
    with pytest.raises(RuntimeError,match='limits unavailable'):
        observed.observe('fixture',binding,policy,supervisor={'timeout_ms':1000},python='/python',expected_mounts={},command=command)


@pytest.mark.parametrize('profile', ['actor', 'worker'])
def test_native_existing_image_probe(profile):
    """Optional real-host control: no image pulls, models, datasets or shared services."""
    import os
    import subprocess
    image = os.environ.get('HG_CGROUP_TEST_IMAGE')
    if not image:
        pytest.skip('set HG_CGROUP_TEST_IMAGE to an existing local Python image')
    info = json.loads(subprocess.run(['docker', 'image', 'inspect', image],
                                    capture_output=True, text=True, check=True).stdout)[0]
    if profile == 'actor':
        result = co.observe(info['Id'], {'cpus': 1, 'memory_mb': 256}, network='none')
        assert result.ok, result.receipt()
        assert result.observed['resource_backend'] in ('cgroup-v1', 'cgroup-v2')
        return
    spec = {'kind': 'exec', 'backend': 'observed-v1', 'image': info['Id'],
            'workdir': '/workspace', 'cpus': 1, 'memory_mb': 256, 'pids': 64,
            'filesystems': {'/workspace': {'bytes': 16 << 20, 'inodes': 4096}}}
    binding = {'adapter_id': 'cgroup-compat-control', 'run_id': 'synthetic-local',
               'trial_id': '1', 'manifest_sha256': 'a' * 64}
    runtime = observed.Runtime(spec, binding=binding, timeout_s=10)
    with runtime:
        result = runtime.run(['/usr/local/bin/python3', '-c', 'print("CGROUP_COMPAT_OK")'])
        assert result.returncode == 0 and 'CGROUP_COMPAT_OK' in result.stdout
        assert runtime.receipt['memory_events']['oom_kill'] == 0
    assert runtime.receipt['cleanup']['complete']
