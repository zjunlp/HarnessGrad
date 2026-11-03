"""Fixed host scorer: sealed native artifacts -> fresh original Harbor verifier."""
from __future__ import annotations
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import signal
from urllib.parse import urlsplit

from data.scaffolds.native_harbor import _tree_identity


def _rewrite_images(text,actor_image,verifier_image):
    # Only deployment identities change; original task instructions, tests,
    # collect commands, phase policies and resources are otherwise untouched.
    lines=[];seen=set();section=''
    values={'environment':actor_image,'verifier.environment':verifier_image}
    for line in text.splitlines():
        stripped=line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            section=stripped[1:-1]
            lines.append(line)
            if section in values:
                lines.append('docker_image = '+json.dumps(values[section]));seen.add(section)
            continue
        if section in values and stripped.startswith('docker_image') and '=' in stripped:
            continue
        lines.append(line)
    for section in values.keys()-seen:
        lines+=['['+section+']','docker_image = '+json.dumps(values[section])]
    return '\n'.join(lines)+'\n'


def sealed_artifacts(case,root,*,task_id,run_id,trial_id):
    envelope=json.loads((root/'native_episode/episode.json').read_text())
    binding=envelope.get('binding',{})
    if any(binding.get(k)!=v for k,v in {'task_id':task_id,'run_id':run_id,'trial_id':trial_id}.items()):
        raise ValueError('sealed native episode belongs to another execution')
    payload=envelope['payload']
    case_sha=hashlib.sha256(json.dumps(case,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if (payload.get('task_id')!=task_id or payload.get('dataset')!=case['dataset']
            or payload.get('case_sha256')!=case_sha
            or payload.get('source_sha256')!=case['source_sha256'] or not payload.get('finished')
            or payload.get('failure') is not None
            or not payload.get('actor_runtime',{}).get('cleanup',{}).get('complete')):
        raise ValueError('native actor episode is incomplete or mismatched')
    if _tree_identity(Path(case['directory']))!=case['source_sha256']:
        raise ValueError('native task source changed after actor execution')
    return payload


def export_artifacts(payload,root):
    files={};size=0
    for name,record in payload['artifacts'].items():
        relative=PurePosixPath(name.lstrip('/'))
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError('native artifact export path invalid')
        data=base64.b64decode(record['base64'],validate=True)
        if record['bytes']!=len(data) or hashlib.sha256(data).hexdigest()!=record['sha256']:
            raise ValueError('native artifact bytes changed')
        size+=len(data)
        if size>256<<20:
            raise ValueError('native replay artifact aggregate exceeds 256MiB')
        destination=root/'files'/str(relative);destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(data)
        files[str(relative)]={'bytes':len(data),'sha256':record['sha256']}
    (root/'manifest.json').write_text(json.dumps({'files':files}))
    return files


def run(spec,artifact_root,*,task_id,run_id,trial_id,timeout_s):
    root=Path(artifact_root);config=spec['native_benchmark'];gateway=None
    try:
        raw=(root/config['case_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=config['case_sha256']:
            raise ValueError('selected native case digest differs')
        case=json.loads(raw)
        if case['task_id']!=task_id or case['dataset']!=config['dataset']:
            raise ValueError('selected native case identity differs')
        payload=sealed_artifacts(case,root,task_id=task_id,run_id=run_id,trial_id=trial_id)
        with tempfile.TemporaryDirectory(prefix='hg-native-harbor-verify-') as tmp:
            scratch=Path(tmp);exports=scratch/'exports';exports.mkdir()
            if case['dataset']=='swe_bench_pro':
                # Read once, authenticate and own the tooling bytes before the
                # selected Harbor interpreter executes a source module.
                for key in ('pro_replay_source','pro_capture_source'):
                    source=Path(case['runtime'][key])
                    if source.is_symlink():raise ValueError('linked native Pro tooling')
                    raw_source=source.read_bytes()
                    if hashlib.sha256(raw_source).hexdigest()!=case['runtime'][key+'_sha256']:
                        raise ValueError('native Pro tooling changed after preparation')
                    owned=scratch/(key+'.py');owned.write_bytes(raw_source)
                    case['runtime'][key]=str(owned)
            files=export_artifacts(payload,exports)
            projection=scratch/'task'/Path(case['directory']).name
            shutil.copytree(case['directory'],projection,symlinks=False)
            if _tree_identity(projection)!=case['source_sha256']:
                raise ValueError('native task source changed while copying private verifier projection')
            toml=projection/'task.toml'
            toml.write_text(_rewrite_images(toml.read_text(),case['runtime']['replay_actor_image'],
                                            case['runtime']['verifier_image']))
            source_job=scratch/'source-job';captured=None
            if case['dataset']=='swe_bench_pro':
                source=source_job/'instance_native/agent';source.mkdir(parents=True)
                captured=source/'model.patch';shutil.copyfile(exports/'files/logs/agent/model.patch',captured)
                (source.parent/'result.json').write_text(json.dumps({'task_name':Path(case['directory']).name}))
            verifier_env={}
            if case['dataset']=='jobbench':
                # Only an explicitly configured loopback provider is accepted here.
                # Never inherit an actor's endpoint/model/credentials.
                base=os.environ['HG_JUDGE_HARBOR_BASE_URL'];parsed=urlsplit(base)
                if parsed.scheme!='http' or not ipaddress.ip_address(parsed.hostname or '').is_loopback:
                    raise ValueError('JobBench judge must use an explicit local HTTP endpoint')
                from eval.service import modelgate
                bridge=json.loads(subprocess.check_output(['docker','network','inspect','bridge'],text=True,
                                                                   errors='replace'))[0]['IPAM']['Config'][0]['Gateway']
                gateway=modelgate.publish(base,bind_host='0.0.0.0')
                verifier_env={'JUDGE_MODEL':os.environ['HG_JUDGE_HARBOR_MODEL'],
                              'JUDGE_API_KEY':os.environ.get('HG_JUDGE_HARBOR_API_KEY','local'),
                              'JUDGE_API_BASE':gateway.url_for(bridge)}
            request={'case':case,'artifact_root':str(exports),'task_projection':str(projection),
                     'source_job':str(source_job),'captured_patch':str(captured) if captured else None,
                     'trial_root':str(scratch/'trials'),'verifier_env':verifier_env}
            request_path=scratch/'request.json';request_path.write_text(json.dumps(request));request_path.chmod(0o600)
            response=scratch/'response.json'
            env={k:v for k,v in os.environ.items() if not k.startswith(('HG_AGENT_','HG_METHOD_','HG_ENV_','HG_JUDGE_'))
                 and not any(token in k.upper() for token in ('API_KEY','SECRET','PASSWORD','ACCESS_TOKEN'))}
            env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='',HOME=str(scratch/'home'))
            Path(env['HOME']).mkdir()
            process=subprocess.Popen([case['runtime']['harbor_python'],'-I','-B',str(Path(__file__).with_name('harbor_entry.py')),
                                      str(request_path),str(response)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                      text=True,errors='replace',start_new_session=True)
            try:
                stdout,stderr=process.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                # Signal the supervisor itself first so its fixed cleanup handler
                # can remove exact trial containers; kill its process group only
                # after the bounded cleanup grace.
                process.terminate()
                try: process.communicate(timeout=35)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.communicate()
                raise TimeoutError('native Harbor verifier deadline reached')
            process.stderr=stderr
            if process.returncode or not response.is_file():
                raise RuntimeError('native Harbor verifier process failed: '+process.stderr[-1000:])
            result=json.loads(response.read_text());score=result['reward']
            if case['dataset']=='jobbench':
                result.update(judge_model=verifier_env['JUDGE_MODEL'],judge_profile='custom-local',
                              judge_endpoint_sha256=hashlib.sha256(os.environ['HG_JUDGE_HARBOR_BASE_URL'].encode()).hexdigest())
            return {'kind':'command','passed':score==1,'score':score,'outcome':'measured',
                    'detail':'Fresh native Harbor verifier completed',
                    'scorer_isolation_class':'unknown','native_harbor':result,
                    'evidence':{'measured':True,'dataset':case['dataset'],
                                'native_rewards':result['native_rewards'],
                                'official_evaluation_performed':True},
                    'scorer_input':{'task_id':task_id,'path':config['case_path'],
                                    'sha256':config['case_sha256'],'expected_sha256':config['case_sha256'],'verified':True}}
    except Exception as exc:
        return {'kind':'command','passed':False,'score':None,'outcome':'unmeasured',
                'unmeasured':'native Harbor verifier unavailable ('+type(exc).__name__+')',
                'evidence':{'measured':False,'error':type(exc).__name__},
                'scorer_isolation_class':'unknown'}
    finally:
        if gateway is not None:
            gateway.stop()
