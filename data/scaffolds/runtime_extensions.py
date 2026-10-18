"""Validation for explicitly enabled runtime extensions; standard library only."""
from pathlib import Path
import math


def validate_record(spec) -> None:
    if spec is None:
        return
    if not isinstance(spec, dict) or set(spec) - {"path", "max_bytes"}:
        raise ValueError("tool_relay.record requires path and optional max_bytes")
    path = spec.get("path")
    if (not isinstance(path, str) or len(Path(path).parts) != 1
            or path in ("", ".", "..") or Path(path).is_absolute()):
        raise ValueError("tool_relay.record.path must be one relative directory name")
    limit = spec.get("max_bytes", 32 * 1024 * 1024)
    if type(limit) is not int or limit <= 0:
        raise ValueError("tool_relay.record.max_bytes must be a positive integer")


def validate_judge(spec, *, allow_trial_seed=False):
    fields = {'model', 'max_calls', 'max_output_tokens', 'timeout_s', 'parameters', 'tools', 'tool_choice'}
    if allow_trial_seed:
        fields.add('seed_source')
    if not isinstance(spec, dict) or set(spec) - fields:
        raise ValueError('judge must declare model, max_calls, max_output_tokens, timeout_s and optional parameters')
    if not isinstance(spec.get('model'), str) or not spec['model'].strip():
        raise ValueError('judge.model must be nonempty')
    for key in ('max_calls', 'max_output_tokens'):
        if type(spec.get(key)) is not int or spec[key] <= 0:
            raise ValueError(f'judge.{key} must be a positive integer')
    timeout = spec.get('timeout_s')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('judge.timeout_s must be positive and finite')
    tools = spec.get('tools', [])
    if not isinstance(tools, list) or any(not isinstance(t, dict) or t.get('type') != 'function'
            or not isinstance(t.get('function'), dict) or not isinstance(t['function'].get('name'), str)
            for t in tools):
        raise ValueError('model tools must be declared function schemas')
    if spec.get('tool_choice', 'auto') not in ('auto', 'none', 'required'):
        raise ValueError('tool_choice must be auto, none or required')
    if 'tool_choice' in spec and not tools:
        raise ValueError('tool_choice requires declared tools')
    params = spec.get('parameters', {})
    if not isinstance(params, dict) or set(params) - {'temperature', 'top_p', 'seed'}:
        raise ValueError('judge parameters support temperature, top_p and seed only')
    if 'seed_source' in spec:
        if spec['seed_source'] != 'trial' or 'seed' in params:
            raise ValueError('environment seed_source must be trial and cannot combine with parameters.seed')
    for key, value in params.items():
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('judge parameters must be finite numbers')
        if key == 'seed' and type(value) is not int:
            raise ValueError('judge seed must be an integer')
        if key == 'temperature' and not 0 <= value <= 2:
            raise ValueError('judge temperature must be between 0 and 2')
        if key == 'top_p' and not 0 < value <= 1:
            raise ValueError('judge top_p must be in (0,1]')


def validate_result(spec):
    if (not isinstance(spec, dict) or not {'version', 'score_range'} <= set(spec)
            or set(spec) - {'version', 'score_range', 'require_exit_zero'} or spec['version'] != 1):
        raise ValueError('result_contract requires version=1 and score_range=[min,max]')
    if 'require_exit_zero' in spec and type(spec['require_exit_zero']) is not bool:
        raise ValueError('result_contract.require_exit_zero must be boolean')
    bounds = spec['score_range']
    if (not isinstance(bounds, list) or len(bounds) != 2
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds)
            or bounds[0] >= bounds[1]):
        raise ValueError('result_contract.score_range must contain two increasing finite numbers')



def validate_trials(spec):
    if (not isinstance(spec, dict) or set(spec) != {'version','metric','trials','ks','seeds'}
            or spec['version'] != 1 or spec['metric'] != 'pass^k'):
        raise ValueError('trial_protocol requires version=1, metric=pass^k, trials, ks and seeds')
    n=spec['trials'];ks=spec['ks'];seeds=spec['seeds']
    if type(n) is not int or n < 1:
        raise ValueError('trial_protocol.trials must be positive')
    if not isinstance(ks,list) or not ks or any(type(k) is not int or not 1<=k<=n for k in ks) or len(set(ks))!=len(ks):
        raise ValueError('trial_protocol.ks must be unique positive integers <= trials')
    if not isinstance(seeds,list) or len(seeds)!=n or any(type(x) not in (str,int) for x in seeds):
        raise ValueError('trial_protocol.seeds must declare one string/integer seed per trial')

def validate_workers(workers):
    import re
    if not isinstance(workers,list) or not 2<=len(workers)<=8:
        raise ValueError('workers must contain 2 to 8 fixed peers')
    names=set()
    for w in workers:
        if not isinstance(w,dict) or set(w)-{'id','goal','files','env','artifacts','timeout_s'}:
            raise ValueError('worker supports id, goal, files, env, artifacts, timeout_s')
        name=w.get('id')
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',name) or name in names:
            raise ValueError('workers require unique simple IDs')
        names.add(name)
        if not isinstance(w.get('goal'),str) or not w['goal']:
            raise ValueError('worker goal must be nonempty')
        if not isinstance(w.get('env', {}), dict):
            raise ValueError('worker env must be an object')
        if w.get('env', {}).get('backend') == 'observed-v1':
            validate_container_runtime(w['env'])
        if 'timeout_s' in w and (type(w['timeout_s']) not in (int, float) or not math.isfinite(w['timeout_s']) or w['timeout_s'] <= 0):
            raise ValueError('worker timeout must be positive and finite')
        paths=w.get('artifacts')
        if not isinstance(paths,list) or not paths or len(paths)>32:
            raise ValueError('workers require 1 to 32 artifact paths')
        for path in paths:
            if (not isinstance(path,str) or not Path(path).parts or Path(path).is_absolute()
                    or '..' in Path(path).parts or path=='.'):
                raise ValueError('worker artifacts must be safe relative paths')


def validate_container_runtime(spec):
    allowed = {'kind', 'backend', 'image', 'workdir', 'image_workspace', 'cpus',
               'memory_mb', 'pids', 'filesystems', 'network', 'agent_timeout_s', 'verify_timeout_s', 'python'}
    if (not isinstance(spec, dict) or set(spec) - allowed or spec.get('kind') != 'exec'
            or spec.get('backend') != 'observed-v1'):
        raise ValueError('observed worker requires kind=exec, backend=observed-v1 and supported fields')
    if spec.get('network', 'none') != 'none':
        raise ValueError('observed containers require network=none')
    import re
    from pathlib import PurePosixPath
    if not isinstance(spec.get('image'), str) or not re.fullmatch(r'(?:[^@]+@)?sha256:[a-fA-F0-9]{64}', spec['image']):
        raise ValueError('observed container image must be an explicit local digest')
    work = spec.get('workdir', '/workspace')
    if (not isinstance(work, str) or str(PurePosixPath(work)) != work
            or not work.startswith('/') or work.count('/') != 1 or work in
            ('/', '/tmp', '/dev', '/proc', '/sys', '/run', '/usr', '/bin', '/etc', '/lib', '/sbin')):
        raise ValueError('observed workdir must be a separate top-level directory, e.g. /workspace')
    python = spec.get('python', '/usr/local/bin/python3')
    if not isinstance(python, str) or not python.startswith('/') or '..' in PurePosixPath(python).parts:
        raise ValueError('container python must be an absolute image interpreter path')
    source = spec.get('image_workspace')
    if source is not None and (not isinstance(source, str) or not source.startswith('/')
                               or '..' in PurePosixPath(source).parts or source == '/'):
        raise ValueError('image_workspace must be an absolute image directory other than /')
    fs = spec.get('filesystems')
    if not isinstance(fs, dict) or set(fs) != {work}:
        raise ValueError('filesystems must declare bytes and inodes for workdir; system scratch is platform-owned')
    for row in fs.values():
        if (not isinstance(row, dict) or set(row) != {'bytes', 'inodes'}
                or any(type(v) is not int or v <= 0 for v in row.values())
                or row['bytes'] % 4096):
            raise ValueError('filesystem needs positive bytes (page aligned) and inodes')
    cpu = spec.get('cpus', 2)
    if type(cpu) not in (int, float) or not 0.01 <= cpu <= 112 or cpu * 1000 != int(cpu * 1000):
        raise ValueError('cpus must be positive in whole millicores')
    for key, default in [('memory_mb', 4096), ('pids', 1024)]:
        if type(spec.get(key, default)) is not int or spec.get(key, default) <= 0:
            raise ValueError(key + ' must be positive')
    for key in ('agent_timeout_s', 'verify_timeout_s'):
        if key in spec and (type(spec[key]) not in (int, float) or not math.isfinite(spec[key]) or spec[key] <= 0):
            raise ValueError(key + ' must be positive and finite')
    return work
