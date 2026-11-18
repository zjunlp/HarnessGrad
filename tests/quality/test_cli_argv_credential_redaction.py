"""Structured auth argv values are executed unchanged and archived only redacted."""
import shlex
import sys

import pytest
from methods import cli_improver


def test_narrow_auth_options_and_config_assignments_exclude_benign_names():
    options = ['--api-key','synthetic-api-key','--token=synthetic-token',
        '-c','provider.api_key="synthetic-config-key"',
        '--config=provider.client_secret=synthetic-client-secret',
        '--header','Authorization: Bearer synthetic-bearer',
        '--api-key-env','PROVIDER_KEY_VARIABLE','--token-limit','12345',
        '--access-token-env=ACCESS_TOKEN_VARIABLE','--config','model.max_tokens=9000',
        'ordinary prose api_key=not-an-option','provider.api_key = \"synthetic-spaced-config\"',
        '--token-limit=54321']
    got = cli_improver._argv_credentials(options)
    for value in ('synthetic-api-key','synthetic-token','synthetic-config-key',
                  'synthetic-client-secret','synthetic-bearer','synthetic-spaced-config'):
        assert value in got
    assert not any(value in got for value in
        ('PROVIDER_KEY_VARIABLE','12345','ACCESS_TOKEN_VARIABLE','9000','not-an-option','54321'))


@pytest.mark.parametrize('streaming',[True,False])
def test_fake_cli_echoed_auth_options_redacted_on_each_stream(tmp_path,monkeypatch,streaming):
    parent = tmp_path/'parent'; parent.mkdir()
    (parent/'harness.json').write_text('{}')
    workspace = tmp_path/'workspace'; workspace.mkdir()
    host = tmp_path/'host'; host.mkdir()
    fake = tmp_path/'fake_cli.py'
    secret_values = ['synthetic-argv-key','synthetic-argv-token','synthetic-argv-config']
    fake.write_text('#!'+sys.executable+'\n'+'''import os,sys,time
args=sys.argv[1:-1]
start=args.index('--api-key')
# Echo only the synthetic options: no task/prompt output or environment dump.
values=args[start:]
assert values==['--api-key','synthetic-argv-key','--token=synthetic-argv-token','-c','provider.api_key="synthetic-argv-config"','--token-limit','12345','--api-key-env','PROVIDER_KEY_VARIABLE']
stdout=' '.join(values)+'\\n'
stderr='separate stderr: '+values[1]+' '+values[2].split('=',1)[1]+' synthetic-argv-config\\n'
for fd,text in ((1,stdout),(2,stderr)):
 data=text.encode()
 for byte in data:
  os.write(fd,bytes([byte]))
  time.sleep(.001)
print('dsh-usage {"input_tokens":4,"output_tokens":2,"total_tokens":6}',flush=True)
''')
    fake.chmod(0o700)
    monkeypatch.delenv('HG_IMPROVER',raising=False)
    monkeypatch.setenv('HG_IMPROVER_ARGS',shlex.join([
        '--api-key',secret_values[0],'--token='+secret_values[1],'-c',
        'provider.api_key="'+secret_values[2]+'"','--token-limit','12345',
        '--api-key-env','PROVIDER_KEY_VARIABLE']))
    monkeypatch.setattr(cli_improver,'_stage_evidence',lambda *a:None)
    monkeypatch.setattr(cli_improver,'_seed_codex_home',lambda *a:None)
    req={'workspace':str(workspace),'improver':{'path':str(fake),'argv_style':'codex'}}
    if streaming:
        req['archive_directory']=str(host)
    result=cli_improver.propose(req,parent)
    assert result['exit_code']==0
    assert all(secret not in result['transcript'] for secret in secret_values)
    assert '--token-limit 12345 --api-key-env PROVIDER_KEY_VARIABLE' in result['transcript']
    assert 'dsh-usage ' in result['transcript']
    for directory in (host,workspace):
        for file in directory.glob('*.log'):
            assert all(secret.encode() not in file.read_bytes() for secret in secret_values)
    records=result['transcript_archive']
    assert records['raw_bytes'] > records['full']['bytes'] - 100
    if streaming:
        assert records['raw_sha256'] is None
        for name in ('stdout','stderr'):
            row=records['streams'][name]
            assert row['redacted']
            assert row['raw_sha256'] != row['full']['sha256']


def test_positional_prompt_is_not_scanned_for_auth_assignments(tmp_path,monkeypatch):
    parent=tmp_path/'parent';parent.mkdir();(parent/'harness.json').write_text('{}')
    workspace=tmp_path/'workspace';workspace.mkdir()
    observed=[]
    original=cli_improver._argv_credentials
    def extract(arguments):
        observed.append(list(arguments))
        return original(arguments)
    monkeypatch.setattr(cli_improver,'_argv_credentials',extract)
    monkeypatch.setattr(cli_improver,'_cli_argv',lambda *a:[sys.executable,'-c',"print('benign output')",'api_key=ordinary-prompt-text'])
    monkeypatch.setattr(cli_improver,'_stage_evidence',lambda *a:None)
    monkeypatch.setattr(cli_improver,'_seed_codex_home',lambda *a:None)
    result=cli_improver.propose({'workspace':str(workspace)},parent)
    assert result['exit_code']==0
    assert observed and all('api_key=ordinary-prompt-text' not in argv for argv in observed)
