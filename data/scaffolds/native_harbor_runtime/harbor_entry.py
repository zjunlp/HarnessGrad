#!/usr/bin/env python3
"""Pinned native Harbor verifier trial in a selected, isolated host interpreter."""
from __future__ import annotations
import asyncio
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import signal

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))


def stopped(_sig,_frame):
    from data.scaffolds.native_harbor_runtime.harbor_environment import cleanup_instances
    cleanup_instances()
    raise SystemExit(3)


async def run(request):
    if importlib.metadata.version('harbor')!='0.23.0':
        raise RuntimeError('native replay requires harbor==0.23.0')
    from harbor.models.trial.config import AgentConfig,EnvironmentConfig,TaskConfig,TrialConfig,VerifierConfig
    from harbor.trial.trial import Trial
    case=request['case']; artifacts=Path(request['artifact_root']); mode=case['dataset']
    if mode=='swe_bench_pro':
        source=Path(case['runtime']['pro_replay_source'])
        spec=importlib.util.spec_from_file_location('hg_pinned_pro_patch_replay',source)
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
        import_path=spec.name+':PatchReplayAgent'
        kwargs={'source_job':request['source_job']}
    else:
        import_path='data.scaffolds.native_harbor_runtime.replay_agent:NativeArtifactReplay'
        kwargs={'artifact_root':str(artifacts),'mode':mode,'workdir':case['runtime']['actor']['workdir']}
    verifier_env=request.get('verifier_env',{})
    trial=await Trial.create(TrialConfig(
        task=TaskConfig(path=Path(request['task_projection'])),trials_dir=Path(request['trial_root']),
        agent=AgentConfig(import_path=import_path,model_name='artifact-replay',kwargs=kwargs,
                          override_timeout_sec=300,override_setup_timeout_sec=300),
        environment=EnvironmentConfig(type='docker',import_path='data.scaffolds.native_harbor_runtime.harbor_environment:NativeReplayDocker',delete=True,force_build=False),
        verifier=VerifierConfig(env=verifier_env)))
    result=await trial.run()
    if result.exception_info is not None:
        raise RuntimeError('native Harbor trial failed: '+result.exception_info.exception_type)
    rewards=result.verifier_result.rewards if result.verifier_result else None
    if not isinstance(rewards,dict) or type(rewards.get('reward')) not in (int,float):
        raise RuntimeError('native verifier ended without a numeric reward')
    reward=rewards['reward']
    if not 0<=reward<=1 or (mode in ('deepswe','swe_bench_pro') and reward not in (0,1)):
        raise ValueError('native verifier reward outside benchmark range')
    replay_file=trial.paths.agent_dir/'replay.json'
    replay=json.loads(replay_file.read_text()) if replay_file.is_file() else {}
    if mode=='deepswe' and replay.get('apply_rc')!=0:
        raise RuntimeError('native DeepSWE artifact replay did not apply the sealed committed patch')
    if mode=='swe_bench_pro':
        captured=artifacts/'files/logs/agent/model.patch'
        if captured.stat().st_size and replay.get('patch')!=request['captured_patch']:
            raise RuntimeError('official Pro replay did not consume the sealed patch')
    from data.scaffolds.native_harbor_runtime.harbor_environment import NativeReplayDocker
    runtime_observations=[i.runtime_observation for i in NativeReplayDocker._instances]
    if not runtime_observations or any(not r.get('cleanup',{}).get('complete') for r in runtime_observations):
        raise RuntimeError('native Harbor runtime cleanup not confirmed')
    return {'runtime_observations':runtime_observations,'reward':reward,'native_rewards':rewards,'harbor_version':'0.23.0',
            'native_trial_id':str(result.id),'official_evaluation_performed':True,
            'replay':{k:replay[k] for k in ('apply_rc','mode','files') if k in replay},
            'task_checksum':trial.task.checksum,
            'runtime_profile':'native-harbor-replay; not an observed-v1/pristine-offline attestation',
            'production_authorized':False}


if __name__=='__main__':
    signal.signal(signal.SIGTERM,stopped)
    signal.signal(signal.SIGINT,stopped)
    request=json.loads(Path(sys.argv[1]).read_text())
    try:
        result=asyncio.run(run(request))
        Path(sys.argv[2]).write_text(json.dumps(result,allow_nan=False))
    except Exception as exc:
        print('native Harbor verifier failed ('+type(exc).__name__+'): '+str(exc)[-1500:],file=sys.stderr)
        raise SystemExit(3)
    finally:
        from data.scaffolds.native_harbor_runtime.harbor_environment import cleanup_instances
        cleanup_instances()
