"""Synthetic credential stores only; optional real pinned Node/YAML parser, no providers."""
import json
import builtins
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from methods import cli_improver
from improver_credentials import seeded_credentials
from improver_archive import ArchiveFailure

ROOT = Path(__file__).resolve().parents[2]


def test_codex_generated_default_needs_no_toml_parser_but_other_config_fails_closed(tmp_path, monkeypatch):
    home = tmp_path / '.codex-home'; home.mkdir()
    config = home / 'config.toml'
    config.write_text('\nresponses_experimental_transport = "https"\n')
    real_import = builtins.__import__
    def without_tomllib(name, *args, **kwargs):
        if name == 'tomllib':
            raise ImportError('synthetic unavailable parser')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', without_tomllib)
    assert seeded_credentials(tmp_path, 'codex', {}) == []
    config.write_text('api_key = "synthetic-secret"\n')
    with pytest.raises(ArchiveFailure, match='Cannot safely interpret'):
        seeded_credentials(tmp_path, 'codex', {})
YAML_DOCUMENT = '''version: 1
refs:
  FIXTURE_REF: "synthetic-file-ref"
  FIXTURE_MULTI: |-
    synthetic-multiline-first
    synthetic-multiline-second
records:
  provider/fake:
    kind: api-key
    key: 'synthetic-file-api-key'
    env:
      FIXTURE_PROVIDER_SECRET: "synthetic-file-env-secret"
  grant/fake:
    kind: grant
    payload:
      access_token: "synthetic-file-grant-token"
      nested:
        refresh_token: "synthetic-file-refresh-token"
'''
YAML_VALUES = ['synthetic-file-ref','synthetic-multiline-first\nsynthetic-multiline-second',
    'synthetic-file-api-key','synthetic-file-env-secret','synthetic-file-grant-token',
    'synthetic-file-refresh-token']
CODEX_VALUES = ['synthetic-file-codex-api','synthetic-file-codex-id',
                'synthetic-file-codex-access','synthetic-file-codex-refresh']

YAML_CONFIG = """provider:
  model: fixture-model-benign
  base_url: http://fixture.invalid/v1
  apiKey: synthetic-settings-api
  headers:
    Authorization: Bearer synthetic-settings-bearer
  entries:
    - password: |-
        synthetic-settings-first
        synthetic-settings-second
token_limit: 512
api_key_env: FIXTURE_ENV_NAME
"""
YAML_CONFIG_VALUES = ['synthetic-settings-api','Bearer synthetic-settings-bearer',
    'synthetic-settings-bearer','synthetic-settings-first\nsynthetic-settings-second']
TOML_CONFIG = """model = "fixture-model-benign"
token_limit = 512
api_key_env = "FIXTURE_ENV_NAME"
[model_providers.fixture]
base_url = "http://fixture.invalid/v1"
api_key = "synthetic-toml-api"
authorization = "Bearer synthetic-toml-bearer"
[auth]
password = 'synthetic-toml-pass'
"""
TOML_CONFIG_VALUES = ['synthetic-toml-api','Bearer synthetic-toml-bearer',
    'synthetic-toml-bearer','synthetic-toml-pass']


@pytest.fixture
def node_yaml():
    node = shutil.which('node')
    override = os.environ.get('HG_TEST_DSH_CREDENTIALS_RUNTIME_ROOT')
    candidates = ([Path(override)] if override else []) + [
        ROOT.parents[1]/'deepseek-harness',
        ROOT.parent/'deepseek-harness-probe-stdio-20261115']
    if node:
        for root in candidates:
            package = root/'packages/credentials/credentials-local/package.json'
            if package.is_file():
                probe = subprocess.run([node,'-e',
                    "require('module').createRequire(process.argv[1]).resolve('yaml')",str(package)],
                    capture_output=True,timeout=10,env={'LANG':'C.UTF-8'})
                if probe.returncode == 0:
                    return {'node':node,'root':str(root)}
    pytest.skip('Pinned DSH Node/YAML package unavailable; set HG_TEST_DSH_CREDENTIALS_RUNTIME_ROOT')


def seed_fixture(tmp_path,style):
    source=tmp_path/'fixture-source';source.mkdir()
    if style=='dsh':
        (source/'.credentials.yaml').write_text(YAML_DOCUMENT)
        (source/'settings.yaml').write_text(YAML_CONFIG)
        return source,YAML_VALUES+YAML_CONFIG_VALUES
    document={'OPENAI_API_KEY':CODEX_VALUES[0], 'auth_mode':'chatgpt','tokens':{
        'id_token':CODEX_VALUES[1],'access_token':CODEX_VALUES[2],
        'refresh_token':CODEX_VALUES[3],'account_id':'fixture-account'}}
    (source/'auth.json').write_text(json.dumps(document))
    (source/'config.toml').write_text(TOML_CONFIG)
    return source,CODEX_VALUES+TOML_CONFIG_VALUES


def launch_fixture(tmp_path,monkeypatch,style,runtime):
    source,values=seed_fixture(tmp_path,style)
    parent=tmp_path/'parent';parent.mkdir();(parent/'harness.json').write_text('{}')
    workspace=tmp_path/'workspace';workspace.mkdir()
    host=tmp_path/'host';host.mkdir()
    marker=tmp_path/'fake-provider-started'
    fake=tmp_path/'fake_cli.py'
    fake.write_text('#!'+sys.executable+'\n'+'''import hashlib,json,os,subprocess,sys,tomllib
from pathlib import Path
Path(MARKER).write_text('started')
if STYLE=='codex':
 document=json.loads((Path(os.environ['CODEX_HOME'])/'auth.json').read_text())
 values=[document['OPENAI_API_KEY'],*[document['tokens'][key] for key in ('id_token','access_token','refresh_token')]]
 config=tomllib.loads((Path(os.environ['CODEX_HOME'])/'config.toml').read_text())
 p=config['model_providers']['fixture']
 values += [p['api_key'],p['authorization'],p['authorization'].split(' ',1)[1],config['auth']['password'],config['model'],p['base_url'],str(config['token_limit']),config['api_key_env']]
else:
 package=Path(os.environ['HG_DSH_ROOT'])/'packages/credentials/credentials-local/package.json'
 code="const y=require('module').createRequire(process.argv[1])('yaml');const f=require('fs');const docs=JSON.parse(f.readFileSync(0,'utf8'));const d=y.parse(docs[0]);const c=y.parse(docs[1]);let a=[];function visit(x){if(typeof x==='string')a.push(x);else if(x&&typeof x==='object')Object.values(x).forEach(visit)};visit(d.refs);Object.values(d.records).forEach(r=>{if(r.kind==='api-key'){if(r.key)a.push(r.key);if(r.env)visit(r.env)}else visit(r.payload)});a.push(c.provider.apiKey,c.provider.headers.Authorization,c.provider.headers.Authorization.split(' ')[1],c.provider.entries[0].password,c.provider.model,c.provider.base_url,String(c.token_limit),c.api_key_env);process.stdout.write(JSON.stringify(a))"
 parsed=subprocess.run([os.environ['HG_DSH_NODE'],'-e',code,str(package)],input=json.dumps([(Path(os.environ['DSH_HOME'])/'.credentials.yaml').read_text(),(Path(os.environ['DSH_HOME'])/'settings.yaml').read_text()]).encode(),capture_output=True,check=True)
 values=json.loads(parsed.stdout)
body='\\n'.join(values).encode()
for fd,chunk in ((1,body[:len(body)//2]),(2,body[len(body)//2:])):
 for byte in chunk:os.write(fd,bytes([byte]))
root=Path(os.environ['HG_IMPROVER_ARTIFACT_ROOT'])
(root/'body.txt').write_bytes(body)
Path(os.environ['HG_IMPROVER_ARTIFACT_MANIFEST']).write_text(json.dumps({'version':1,'session_id':'fixture','artifacts':[{'path':'body.txt','sha256':hashlib.sha256(body).hexdigest()}]}))
'''.replace('MARKER',repr(str(marker))).replace('STYLE',repr(style)))
    fake.chmod(0o700)
    monkeypatch.delenv('HG_IMPROVER',raising=False)
    monkeypatch.delenv('HG_IMPROVER_ARGS',raising=False)
    monkeypatch.setenv('HG_CODEX_HOME_SOURCE',str(source))
    monkeypatch.setenv('HG_IMPROVER_DSH_HOME',str(source))
    monkeypatch.setattr(cli_improver,'_stage_evidence',lambda *a:None)
    improver={'path':str(fake),'argv_style':style}
    if style=='dsh':
        improver.update(model='fixture',provider='fixture',profile='fixture',dsh_runtime=runtime)
    result=cli_improver.propose({'workspace':str(workspace),'archive_directory':str(host),
                                'improver':improver},parent)
    assert marker.read_text()=='started'
    assert result['exit_code']==0
    for value in values:
        assert value not in os.environ.values()
        assert value not in result['transcript']
        assert all(value.encode() not in file.read_bytes() for file in host.glob('*.log'))
    assert 'fixture-model-benign' in result['transcript']
    assert 'http://fixture.invalid/v1' in result['transcript']
    assert 'FIXTURE_ENV_NAME' in result['transcript']
    artifacts=result['artifact_archive']['artifacts']
    assert artifacts and all(row['redacted'] for row in artifacts)
    assert '[REDACTED]' in Path(artifacts[0]['output']['path']).read_text()
    return result


def test_codex_file_only_keys_tokens_redact_full_tail_and_body(tmp_path,monkeypatch):
    launch_fixture(tmp_path,monkeypatch,'codex',{})


def test_actual_yaml_parser_multiline_quoted_refs_records_grants(tmp_path,monkeypatch,node_yaml):
    result=launch_fixture(tmp_path,monkeypatch,'dsh',node_yaml)
    workspace=tmp_path/'workspace'
    assert set(seeded_credentials(workspace,'dsh',node_yaml))==set(YAML_VALUES+YAML_CONFIG_VALUES)
    assert result['transcript_archive']['coverage']=='cli_stdout_then_stderr'


@pytest.mark.parametrize('kind',['codex-malformed','dsh-absent-node','dsh-unavailable-parser'])
def test_unreadable_seeded_store_stops_before_fake_provider(tmp_path,monkeypatch,kind):
    style='codex' if kind.startswith('codex') else 'dsh'
    parent=tmp_path/'parent';parent.mkdir();(parent/'harness.json').write_text('{}')
    workspace=tmp_path/'workspace';workspace.mkdir()
    host=tmp_path/'host';host.mkdir()
    source=tmp_path/'source';source.mkdir()
    raw='{"OPENAI_API_KEY":"synthetic-malformed-private-value",' if style=='codex' else YAML_DOCUMENT
    (source/('auth.json' if style=='codex' else '.credentials.yaml')).write_text(raw)
    marker=tmp_path/'provider-started'
    fake=tmp_path/'fake.py';fake.write_text('#!'+sys.executable+'\nfrom pathlib import Path\nPath('+repr(str(marker))+').touch()\n');fake.chmod(0o700)
    monkeypatch.delenv('HG_IMPROVER',raising=False);monkeypatch.delenv('HG_IMPROVER_ARGS',raising=False)
    monkeypatch.setenv('HG_CODEX_HOME_SOURCE',str(source));monkeypatch.setenv('HG_IMPROVER_DSH_HOME',str(source))
    monkeypatch.setattr(cli_improver,'_stage_evidence',lambda *a:None)
    runtime={'node':'/missing/pinned-node','root':str(tmp_path/'runtime')}
    package=Path(runtime['root'])/'packages/credentials/credentials-local/package.json'
    package.parent.mkdir(parents=True);package.write_text('{}')
    if kind=='dsh-unavailable-parser':
        runtime['node']=shutil.which('node') or '/missing/pinned-node'
    improver={'path':str(fake),'argv_style':style,'model':'fixture','provider':'fixture','profile':'fixture','dsh_runtime':runtime}
    with pytest.raises(RuntimeError,match='Cannot safely interpret seeded improver credentials') as caught:
        cli_improver.propose({'workspace':str(workspace),'archive_directory':str(host),'improver':improver},parent)
    assert caught.value.terminal_stop['code']=='artifact_persistence_failed'
    assert not marker.exists()
    assert not list(host.glob('*.log'))
    assert 'synthetic-malformed-private-value' not in str(caught.value)


def test_malformed_yaml_never_emits_source_diagnostics(tmp_path,node_yaml):
    home=tmp_path/'.dsh-home';home.mkdir()
    (home/'.credentials.yaml').write_text('version: 1\nrefs: [synthetic-sensitive-bad-yaml\n')
    with pytest.raises(RuntimeError) as caught:
        seeded_credentials(tmp_path,'dsh',node_yaml)
    assert str(caught.value)=='Cannot safely interpret seeded improver credentials'


def test_malformed_copied_toml_config_fails_before_dispatch(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir()
    (source/'config.toml').write_text('api_key = "synthetic-config-private-value"\n[broken')
    parent=tmp_path/'parent';parent.mkdir();(parent/'harness.json').write_text('{}')
    workspace=tmp_path/'workspace';workspace.mkdir()
    marker=tmp_path/'provider-marker'
    monkeypatch.setenv('HG_CODEX_HOME_SOURCE',str(source))
    monkeypatch.delenv('HG_IMPROVER',raising=False)
    monkeypatch.delenv('HG_IMPROVER_ARGS',raising=False)
    monkeypatch.setattr(cli_improver,'_stage_evidence',lambda *a:None)
    monkeypatch.setattr(cli_improver,'_cli_argv',lambda *a:[sys.executable,'-c',
        'from pathlib import Path;Path('+repr(str(marker))+').touch()','task'])
    with pytest.raises(RuntimeError) as caught:
        cli_improver.propose({'workspace':str(workspace)},parent)
    assert not marker.exists()
    assert 'synthetic-config-private-value' not in str(caught.value)


def test_config_only_dsh_requires_real_yaml_parser_and_rejects_malformed(tmp_path,node_yaml):
    home=tmp_path/'.dsh-home';home.mkdir()
    (home/'settings.yaml').write_text(YAML_CONFIG)
    assert set(seeded_credentials(tmp_path,'dsh',node_yaml))==set(YAML_CONFIG_VALUES)
    (home/'settings.yaml').write_text('api_key: [synthetic-config-private-value\n')
    with pytest.raises(RuntimeError) as caught:
        seeded_credentials(tmp_path,'dsh',node_yaml)
    assert 'synthetic-config-private-value' not in str(caught.value)
