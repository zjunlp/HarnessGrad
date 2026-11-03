"""Native Harbor Docker environment with actual network switching and receipts.

Harbor's optional nftables sidecar needs a newer kernel on this host. For a
single-service no-network/public task, use Docker's network attachments directly.
Only fixed offline artifact replayers run in these environments; no actor code
or credentials are carried in from the solver world.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess

from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import NetworkMode


class NativeReplayDocker(DockerEnvironment):
    _instances=[]

    @staticmethod
    def _requires_egress_control(**_kwargs):
        return False

    @property
    def capabilities(self):
        return super().capabilities.model_copy(update={
            'disable_internet':True,'dynamic_network_policy':True,'mounted':False})

    def __init__(self,*args,**kwargs):
        self.runtime_observation={}
        self._network_file=None
        super().__init__(*args,**kwargs)
        if self._uses_compose:
            raise ValueError('native replay transport supports one native service only')
        self._instances.append(self)

    @property
    def _docker_compose_paths(self):
        paths=list(super()._docker_compose_paths)
        if self._network_file is not None:
            paths.append(self._network_file)
        return paths

    def _write_mounts_compose_file(self):
        # Avoid exposing host trial logs to a replayer or verifier; Harbor's
        # existing file-copy transport retrieves their log files instead.
        path=super()._write_mounts_compose_file()
        path.write_text(json.dumps({'services':{'main':{'volumes':[]}}}))
        return path

    async def start(self,force_build):
        # Fresh replay containers cannot inherit task-provided host mounts or
        # anonymous volumes. Task images were explicitly imported at preparation.
        image=json.loads(subprocess.check_output(['docker','image','inspect',self.task_env_config.docker_image]))[0]
        if image['Config'].get('Volumes'):
            raise ValueError('native replay image declares anonymous volumes')
        if self.network_policy.network_mode==NetworkMode.NO_NETWORK:
            self._network_file=self.trial_paths.trial_dir/('native-offline-'+self.session_id+'.json')
            self._network_file.write_text(json.dumps({'services':{'main':{'network_mode':'none'}}}))
        elif self.network_policy.network_mode!=NetworkMode.PUBLIC:
            raise ValueError('native replay only supports exact public/no-network policies')
        try:
            await super().start(force_build)
        except BaseException:
            # Compose may create containers before startup raises. Discover only
            # this exact project, never infer ownership from a broad name glob.
            self.cleanup_exact_project()
            raise
        result=await self._run_docker_compose_command(['ps','-q','main'])
        self._container_id=(result.stdout or '').strip()
        if not self._container_id:
            raise RuntimeError('native Harbor container identity missing')
        info=json.loads(subprocess.check_output(['docker','inspect',self._container_id]))[0]
        self.runtime_observation={'container_id':info['Id'],'image_id':info['Image'],
            'mounts':info.get('Mounts',[]),'network':info['HostConfig']['NetworkMode'],
            'resources':{key:info['HostConfig'].get(key) for key in ('Memory','MemorySwap','NanoCpus','PidsLimit')},
            'production_authorized':False}

    async def _apply_network_policy(self,policy):
        current=await self._run_docker_compose_command(['ps','-q','main'])
        identity=(current.stdout or '').strip()
        info=json.loads(subprocess.check_output(['docker','inspect',identity]))[0]
        networks=info['NetworkSettings'].get('Networks',{})
        if policy.network_mode==NetworkMode.NO_NETWORK:
            for network in networks:
                if network!='none':
                    subprocess.run(['docker','network','disconnect','--force',network,identity],check=True,
                                   capture_output=True)
        elif policy.network_mode==NetworkMode.PUBLIC:
            attached={n:v for n,v in networks.items() if n!='none'}
            if not attached:
                subprocess.run(['docker','network','connect','bridge',identity],check=True,capture_output=True)
        else:
            raise ValueError('native replay allowlist policy is not supported')
        after=json.loads(subprocess.check_output(['docker','inspect',identity]))[0]
        self.runtime_observation.setdefault('phase_networks',[]).append({
            'policy':policy.network_mode.value,'attachments':list(after['NetworkSettings'].get('Networks',{}))})

    async def stop(self,delete):
        try:
            await super().stop(delete)
        finally:
            identity=getattr(self,'_container_id',None)
            if identity:
                check=subprocess.run(['docker','inspect',identity],capture_output=True)
                self.runtime_observation['cleanup']={'complete':check.returncode!=0 and b'no such' in check.stderr.lower()}

    def cleanup_exact_project(self):
        from harbor.environments.docker.docker import _sanitize_docker_compose_project_name
        project=_sanitize_docker_compose_project_name(self.session_id)
        result=subprocess.run(['docker','ps','-a','--filter','label=com.docker.compose.project='+project,
                               '--format','{{.ID}}'],capture_output=True,text=True,errors='replace',timeout=10)
        ids=[value for value in result.stdout.splitlines() if value]
        for identity in ids:
            subprocess.run(['docker','rm','--force','--volumes',identity],capture_output=True,timeout=20)
        remains=subprocess.run(['docker','ps','-a','--filter','label=com.docker.compose.project='+project,
                                '--format','{{.ID}}'],capture_output=True,text=True,errors='replace',timeout=10)
        self.runtime_observation['startup_cleanup']={'complete':remains.returncode==0 and not remains.stdout.strip(),
                                                    'project':project,'containers':ids}


def cleanup_instances():
    receipts=[]
    for instance in NativeReplayDocker._instances:
        identity=getattr(instance,'_container_id',None)
        if identity:
            present=subprocess.run(['docker','inspect',identity],capture_output=True)
            if present.returncode==0:
                subprocess.run(['docker','rm','--force','--volumes',identity],capture_output=True,timeout=30)
            absence=subprocess.run(['docker','inspect',identity],capture_output=True)
            instance.runtime_observation['cleanup']={'complete':absence.returncode!=0 and b'no such' in absence.stderr.lower()}
        else:
            instance.cleanup_exact_project()
        receipts.append(instance.runtime_observation)
    return receipts
