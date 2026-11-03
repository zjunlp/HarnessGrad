"""Fixed artifact replayer for native Harbor trials; never calls a model."""
from __future__ import annotations
import json
from pathlib import Path
import shlex
from harbor.agents.base import BaseAgent


class NativeArtifactReplay(BaseAgent):
    def __init__(self,*args,artifact_root,mode,workdir,**kwargs):
        super().__init__(*args,**kwargs)
        self.artifact_root=Path(artifact_root)
        self.mode=mode
        self.workdir=workdir

    @staticmethod
    def name():
        return 'harnessgrad-native-artifact-replay'

    def version(self):
        return '1.0.0'

    async def setup(self,environment):
        pass

    async def run(self,instruction,environment,context):
        manifest=json.loads((self.artifact_root/'manifest.json').read_text())
        self.logs_dir.mkdir(parents=True,exist_ok=True)
        if self.mode=='deepswe':
            # The source collector exports committed changes against native BASE.
            # Commit the captured patch in a fresh baseline so the same collect
            # command runs before Harbor's separate native verifier.
            patch=self.artifact_root/'files/logs/artifacts/model.patch'
            if not patch.is_file():
                raise ValueError('native committed patch export is missing')
            await environment.upload_file(source_path=patch,target_path='/tmp/hg-native-replay.patch')
            git='git -c '+shlex.quote('safe.directory='+self.workdir)
            command=('cd '+shlex.quote(self.workdir)+'; '
                     'if [ -s /tmp/hg-native-replay.patch ]; then '
                     # Image extraction changes stat metadata; refresh only the
                     # index cache before --index verifies the baseline bytes.
                     +git+' update-index --refresh && '
                     +git+' apply --index /tmp/hg-native-replay.patch && '
                     +git+' -c user.name=HarnessGrad -c user.email=harnessgrad@local '
                     'commit --no-verify -m native-artifact-replay; fi')
            result=await environment.exec(command=command,timeout_sec=300)
            (self.logs_dir/'replay.json').write_text(json.dumps({'apply_rc':result.return_code,'mode':self.mode,
                'stdout':result.stdout,'stderr':result.stderr}))
            if result.return_code:
                raise RuntimeError('sealed committed patch could not be replayed on selected native baseline')
        elif self.mode=='jobbench':
            for relative in manifest['files']:
                target='/'+relative
                if not target.startswith('/workspace/output/'):
                    raise ValueError('JobBench only accepts final output artifacts')
                local=self.artifact_root/'files'/relative
                await environment.exec(command='mkdir -p '+shlex.quote(str(Path(target).parent)),timeout_sec=30)
                await environment.upload_file(source_path=local,target_path=target)
            (self.logs_dir/'replay.json').write_text(json.dumps({'mode':self.mode,'files':len(manifest['files'])}))
        else:
            raise ValueError('unexpected native artifact replay mode')
