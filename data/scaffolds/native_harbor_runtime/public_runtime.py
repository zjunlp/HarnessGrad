"""Explicit native-public Docker profile; never claims observed-v1 admission.

Some JobBench tasks require public research. This opt-in transport keeps the
native actor network capability, while exposing neither host files nor keys.
Its writable layer is Docker-native and is reported as unattested, not as an
HG observed filesystem quota. Production authorization is always false.
"""
from __future__ import annotations
import base64
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time
import uuid

from eval.containers.observed import checked, bounded


def validate_public_runtime(spec):
    """Validate this explicit native profile without claiming observed admission."""
    allowed = {'kind','backend','image','python','workdir','image_workspace','network',
               'cpus','memory_mb','pids','filesystems'}
    if (not isinstance(spec,dict) or set(spec)-allowed or spec.get('kind')!='exec'
            or spec.get('backend')!='native-public' or spec.get('network')!='public'):
        raise ValueError('native-public requires its explicit exec/public runtime profile')
    if not isinstance(spec.get('image'),str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',spec['image']):
        raise ValueError('native-public image must be an explicit local immutable image ID')
    for key in ('workdir','python'):
        value=spec.get(key)
        if (not isinstance(value,str) or not value.startswith('/') or value=='/'
                or str(PurePosixPath(value))!=value or '..' in PurePosixPath(value).parts):
            raise ValueError('native-public '+key+' must be a normalized absolute image path')
    cpu=spec.get('cpus')
    if type(cpu) not in (int,float) or not math.isfinite(cpu) or cpu<0.01 or cpu*1000!=int(cpu*1000):
        raise ValueError('native-public cpus must be positive whole millicores')
    for key in ('memory_mb','pids'):
        if type(spec.get(key)) is not int or spec[key]<=0:
            raise ValueError('native-public '+key+' must be positive')


class PublicRuntime:
    def __init__(self, spec, *, binding, timeout_s, env=None, **_unused):
        if not os.environ.get('HG_NATIVE_HARBOR_ALLOW_PUBLIC'):
            raise ValueError('native-public requires explicit HG_NATIVE_HARBOR_ALLOW_PUBLIC operator reason')
        validate_public_runtime(spec)
        self.spec = spec
        self.binding = binding
        self.timeout_s = timeout_s
        self.workdir = spec['workdir']
        self.python = spec['python']
        self.env = env or {}
        self.name = 'hg-native-public-' + uuid.uuid4().hex[:20]
        self.receipt = {'backend': 'native-docker-public', 'binding': binding,
                        'production_authorized': False, 'kernel_quota_admitted': False,
                        'operator_reason': os.environ['HG_NATIVE_HARBOR_ALLOW_PUBLIC'],
                        'filesystem_policy': 'native Docker writable layer; no observed-v1 quota claim'}
        self.created = False

    def __enter__(self):
        image = json.loads(checked('image', 'inspect', self.spec['image']))[0]
        if image.get('Id') != self.spec['image']:
            raise ValueError('native-public image identity differs from selected immutable image')
        if image['Config'].get('Volumes'):
            raise ValueError('native-public image may not create anonymous volumes')
        args = ['create', '--pull=never', '--network=bridge', '--name', self.name,
                '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--cpus', str(self.spec.get('cpus', 2)), '--memory', str(self.spec['memory_mb'])+'m',
                '--memory-swap', str(self.spec['memory_mb'])+'m', '--pids-limit', str(self.spec.get('pids') or -1),
                '--entrypoint', self.python, '-w', self.workdir,
                self.spec['image'], '-I', '-S', '-c', 'import time;time.sleep(2147483647)']
        self.created = True
        try:
            checked(*args)
            checked('start', self.name)
            info = json.loads(checked('inspect', self.name))[0]
            if info.get('Image') != self.spec['image']:
                raise ValueError('native-public actor image differs from selected immutable image')
            if info.get('Mounts'):
                raise ValueError('native-public actor received an unexpected host mount')
            self.receipt.update(container_id=info['Id'], image_id=info['Image'],
                                network=info['HostConfig']['NetworkMode'],
                                resources={k:info['HostConfig'].get(k) for k in
                                           ('Memory','MemorySwap','NanoCpus','PidsLimit')})
            return self
        except BaseException:
            self.close()
            raise

    def run(self, argv, *, cwd=None, env=None):
        args = ['docker', 'exec', '-w', cwd or self.workdir]
        for key,value in {**self.env, **(env or {})}.items():
            args += ['-e', key+'='+str(value)]
        args += [self.name, *argv]
        result = bounded(args, timeout=self.timeout_s, limit=8 << 20, truncate=True)
        return subprocess.CompletedProcess(argv, result.returncode,
            result.stdout.decode(errors='replace'), result.stderr.decode(errors='replace'))

    def collect(self, paths, *, max_bytes=64 << 20):
        # A fixed stdlib program reads only enumerated regular single-link files.
        code = '''import base64,hashlib,json,os,pathlib,stat,sys
root=pathlib.Path.cwd();out={};remaining=int(sys.argv[2])
for name in json.loads(sys.argv[1]):
 p=root/name
 if pathlib.Path(name).is_absolute() or '..' in pathlib.Path(name).parts:raise ValueError('export escape')
 if any(q.is_symlink() for q in (p,*p.parents)):raise ValueError('linked export')
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as f:
  s=os.fstat(f.fileno())
  if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>remaining:raise ValueError('unsafe export')
  b=f.read(remaining+1)
 if len(b)>remaining:raise ValueError('export bytes exceeded')
 remaining-=len(b);out[name]={'status':'submitted','bytes':len(b),'base64':base64.b64encode(b).decode(),'sha256':hashlib.sha256(b).hexdigest()}
print(json.dumps(out))'''
        argv=['docker','exec','-w',self.workdir,self.name,self.python,'-I','-S','-c',code,json.dumps(paths),str(max_bytes)]
        process=bounded(argv,timeout=self.timeout_s,limit=2*max_bytes+65536,truncate=False)
        if process.returncode:
            raise RuntimeError('native public output export failed')
        return json.loads(process.stdout)

    def close(self):
        errors=[]
        if self.created:
            try:
                checked('rm','--force','--volumes',self.name)
                absence=bounded(['docker','inspect',self.name],timeout=10)
                if absence.returncode==0 or b'no such' not in absence.stderr.lower():
                    errors.append('container absence not confirmed')
            except Exception as exc:
                errors.append(type(exc).__name__)
            self.created=False
        self.receipt['cleanup']={'complete':not errors,'errors':errors}

    def __exit__(self,*_args):
        self.close()
