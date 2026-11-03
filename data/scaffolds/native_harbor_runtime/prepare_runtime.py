#!/usr/bin/env python3
"""Pin already-imported native images and optionally build a no-network actor derivative.

This command is explicit preparation, never part of load/measurement. A selected
actor source image must be local and private-test-free. Separate verifier images
are built from the author's native tests/Dockerfile in advance, then passed here.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from data.scaffolds.native_harbor import inventory


def inspect(value):
    data=json.loads(subprocess.check_output(['docker','image','inspect',value]))[0]
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',data['Id']):
        raise ValueError('Docker did not resolve a local immutable image identity')
    return data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',required=True,choices=('deepswe','jobbench','swe_bench_pro'))
    p.add_argument('--data-root',required=True,type=Path)
    p.add_argument('--task-id',required=True)
    p.add_argument('--actor-image',required=True)
    p.add_argument('--replay-actor-image',required=True)
    p.add_argument('--verifier-image',required=True)
    p.add_argument('--harbor-python',required=True,type=Path)
    p.add_argument('--manifest',required=True,type=Path)
    p.add_argument('--workdir',default='/app')
    p.add_argument('--workspace-bytes',required=True,type=int)
    p.add_argument('--workspace-inodes',required=True,type=int)
    p.add_argument('--memory-mb',required=True,type=int)
    p.add_argument('--cpus',type=float,default=2)
    p.add_argument('--pids',type=int,default=1024)
    p.add_argument('--build-actor-derivative',action='store_true')
    p.add_argument('--pro-tooling',type=Path)
    args=p.parse_args()
    row=next((r for r in inventory(args.data_root,args.dataset) if r.task_id==args.task_id),None)
    if row is None:raise ValueError('selected native task ID absent from inventory')
    if (args.workspace_bytes<=0 or args.workspace_bytes%4096 or args.workspace_inodes<=0 or args.memory_mb<=0):
        raise ValueError('positive page-aligned workspace resources required')
    original=inspect(args.actor_image);actor_image=original['Id']
    if original['Config'].get('Volumes'):raise ValueError('native actor image may not declare volumes')
    if args.build_actor_derivative:
        # Capture files native tools write outside the repo under the same
        # admitted writable world; no gold/scorer is copied to this image.
        with tempfile.TemporaryDirectory(prefix='hg-native-actor-build-') as tmp:
            recipe=(f'FROM {actor_image}\nUSER root\nRUN test ! -e /tests && mkdir -p {args.workdir}/.logs/artifacts '
                    f'{args.workdir}/.logs/agent && rm -rf /logs && ln -s {args.workdir}/.logs /logs\n'
                    f'WORKDIR {args.workdir}\n')
            if not re.fullmatch(r'/[A-Za-z0-9_-]+',args.workdir):raise ValueError('workdir must be a top-level directory')
            Path(tmp,'Dockerfile').write_text(recipe)
            actor_image=subprocess.check_output(['docker','build','--network=none','-q',tmp],text=True,
                                               errors='replace').strip()
            inspect(actor_image)
    actor={'kind':'exec','backend':'observed-v1','image':actor_image,
           'python':'/usr/local/bin/python3','workdir':args.workdir,'image_workspace':args.workdir,
           'network':'none','cpus':args.cpus,'memory_mb':args.memory_mb,'pids':args.pids,
           'filesystems':{path:{'bytes':args.workspace_bytes,'inodes':args.workspace_inodes}
                          for path in (args.workdir,'/tmp','/dev','/dev/shm')}}
    if (row.declaration.get('agent') or {}).get('network_mode')=='public':
        if not os.environ.get('HG_NATIVE_HARBOR_ALLOW_PUBLIC'):
            raise ValueError('public native source needs explicit HG_NATIVE_HARBOR_ALLOW_PUBLIC operator reason')
        actor.update(backend='native-public',network='public')
    entry={'source_sha256':row.source_sha256,'actor':actor,
           'replay_actor_image':inspect(args.replay_actor_image)['Id'],
           'verifier_image':inspect(args.verifier_image)['Id'],
           'harbor_python':str(args.harbor_python.resolve()),
           'source_actor_image':original['Id'],'artifact_max_bytes':64<<20}
    if args.dataset=='swe_bench_pro':
        if not args.pro_tooling:raise ValueError('SWE Pro requires fixed --pro-tooling v2/tooling')
        for key,file in [('pro_replay_source','patch_replay.py'),('pro_capture_source','locked_claude_code.py')]:
            path=args.pro_tooling/file
            entry[key]=str(path.resolve());entry[key+'_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest={'schema':'harnessgrad.native-harbor-runtime.v1','tasks':{}}
    if args.manifest.exists():manifest=json.loads(args.manifest.read_text())
    if manifest['schema']!='harnessgrad.native-harbor-runtime.v1':raise ValueError('runtime manifest schema differs')
    manifest['tasks'][row.task_id]=entry
    args.manifest.parent.mkdir(parents=True,exist_ok=True)
    args.manifest.write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'runtime_prepared':True,'task_id':row.task_id,'manifest':str(args.manifest),
                      'actor_image':actor_image,'production_authorized':False}))


if __name__=='__main__':main()
