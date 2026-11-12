"""A relocated DSH installation must survive resolution and the restricted CLI hop."""
import hashlib
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

import pytest

from harnessgrad import config
from harnessgrad.identity import _improver_identity
from harnessgrad.methods import _method_env, _method_sandbox_plan
from methods import cli_improver
from tools import improver


@pytest.fixture(autouse=True)
def clear_dsh_configuration(monkeypatch):
    for name in ('HG_DSH_ROOT', 'HG_DSH_NODE', 'HG_IMPROVER_DSH_HOME',
                 'HG_IMPROVER', 'HG_IMPROVER_CONFIG', 'HG_IMPROVER_ARGS'):
        monkeypatch.delenv(name, raising=False)
    config.reset()
    yield
    config.reset()


def installation(base, version='9.9.9'):
    root = base / 'dsh source'
    cli = root / 'apps/cli/lib/bin.js'
    cli.parent.mkdir(parents=True)
    cli.write_text(version)
    (root / 'REVISION').write_text(version.replace('.', '') + 'build\n')
    home = base / 'prepared home'
    (home / 'profiles').mkdir(parents=True)
    (home / 'profiles/selected.txt').write_text(version)
    node = base / 'node installation/bin/node'
    node.parent.mkdir(parents=True)
    node.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
cli = Path(sys.argv[1])
if sys.argv[2:] == ['--version']:
    print(cli.read_text())
else:
    print(json.dumps({{
        'cli': str(cli), 'node': __file__, 'root': os.environ.get('HG_DSH_ROOT'),
        'profile': sys.argv[sys.argv.index('--profile') + 1],
        'pin': Path(sys.argv[sys.argv.index('--patch') + 1]).read_text(),
        'agent_key_present': 'HG_AGENT_API_KEY' in os.environ,
        'other_key_present': 'OTHER_API_KEY' in os.environ,
        'declared_key_present': 'DSH_TEST_KEY' in os.environ,
        'home_marker': (Path(os.environ['DSH_HOME']) / 'profiles/selected.txt').read_text(),
    }}))
    Path('agent.py').write_text('# changed by selected fake DSH\\n')
''')
    node.chmod(0o755)
    return root, home, node


def fixture_git(base, monkeypatch):
    real_git = shutil.which('git')
    bindir = base / 'fixture-bin'
    bindir.mkdir()
    git = bindir / 'git'
    git.write_text(f'''#!{sys.executable}
import os, sys
from pathlib import Path
if len(sys.argv) > 2 and sys.argv[1] == '-C' and (Path(sys.argv[2]) / 'REVISION').is_file():
    print((Path(sys.argv[2]) / 'REVISION').read_text().strip())
else:
    os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])
''')
    git.chmod(0o755)
    monkeypatch.setenv('PATH', str(bindir) + os.pathsep + os.environ['PATH'])


def bundled_entry(root='', home=''):
    return {
        'name': 'dsh', 'kind': 'cli', 'argv_style': 'dsh',
        'launcher': 'methods/dsh-improver', 'interpreter': 'node',
        'root': str(root), 'home': str(home),
        'runtime': ['{root}', '{home}'],
        'build_command': ['git', '-C', '{root}', 'rev-parse', '--short', 'HEAD'],
        'model': 'test-model', 'provider': 'fixture-provider', 'profile': 'fixture-profile', 'api_key_env': 'DSH_TEST_KEY',
    }


def resolve(entry):
    return improver.resolve({'default': 'dsh', 'improvers': {'dsh': entry}})


def test_selected_installation_survives_identity_staging_and_restricted_execution(tmp_path, monkeypatch):
    old_root, old_home, old_node = installation(tmp_path / 'old user', '8.8.8')
    root, home, node = installation(tmp_path / 'new user')
    fixture_git(tmp_path, monkeypatch)
    monkeypatch.setenv('HG_DSH_ROOT', str(root))
    monkeypatch.setenv('HG_DSH_NODE', str(node))
    monkeypatch.setenv('HG_IMPROVER_DSH_HOME', str(home))
    monkeypatch.setattr(improver, 'runtime_trees', lambda path: pytest.fail(
        'DSH must not borrow the Codex credential home through runtime_trees'))
    selected = resolve(bundled_entry(old_root, old_home))
    assert selected['ok'], selected
    assert selected['version'] == '9.9.9+999build'
    assert {str(root), str(home), str(node.parent.parent)} <= set(selected['runtime'])
    assert str(old_root) not in selected['runtime'] and str(old_home) not in selected['runtime']
    identity = _improver_identity(selected)
    assert identity['dsh_runtime'] == selected['dsh_runtime']
    assert identity['dsh_runtime']['sha256'] == hashlib.sha256(
        (root / 'apps/cli/lib/bin.js').read_bytes()).hexdigest()
    assert identity['dsh_runtime']['node_sha256'] == hashlib.sha256(node.read_bytes()).hexdigest()

    parent = tmp_path / 'parent'
    parent.mkdir()
    (parent / 'harness.json').write_text(json.dumps({'entrypoint': 'agent.py'}))
    (parent / 'agent.py').write_text('# original\n')
    workspace = tmp_path / 'round'
    workspace.mkdir()
    monkeypatch.setenv('HG_AGENT_API_KEY', 'unused-agent-test-key')
    monkeypatch.setenv('OTHER_API_KEY', 'unused-other-test-key')
    monkeypatch.setenv('DSH_TEST_KEY', 'unused-declared-test-key')
    for field in ('MODEL', 'PROVIDER', 'PROFILE'):
        monkeypatch.setenv('HG_IMPROVER_' + field, 'ambient-wrong')
    env = _method_env(workspace, selected)
    assert {field: env['HG_IMPROVER_' + field.upper()] for field in ('model', 'provider', 'profile')} == {field: selected[field] for field in ('model', 'provider', 'profile')}
    assert env['HG_DSH_ROOT'] == str(root) and env['HG_DSH_NODE'] == str(node)
    assert env['HG_IMPROVER_DSH_HOME'] == str(home)
    # Mutating ambient settings after the probe must not select another installation.
    monkeypatch.setenv('HG_DSH_ROOT', str(old_root))
    monkeypatch.setenv('HG_DSH_NODE', str(old_node))
    monkeypatch.setenv('HG_IMPROVER_DSH_HOME', env['HG_IMPROVER_DSH_HOME'])
    result = cli_improver.propose({'workspace': str(workspace), 'improver': identity}, parent,
                                 prompt='Change the fixture.')
    assert result['exit_code'] == 0, result['transcript']
    executed = json.loads(result['transcript'].splitlines()[0])
    assert executed == {'cli': str(root / 'apps/cli/lib/bin.js'), 'node': str(node),
                        'root': str(root), 'home_marker': '9.9.9',
                        'profile': 'fixture-profile',
                        'pin': '- id: agent-default-model\n  config:\n    provider: \'fixture-provider\'\n    model: \'test-model\'\n',
                        'agent_key_present': False, 'other_key_present': False,
                        'declared_key_present': True}
    assert result['changed'] == ['agent.py']
    assert {'HG_AGENT_API_KEY', 'OTHER_API_KEY'} <= set(result['reported']['env_withheld'])
    assert not {'HG_DSH_ROOT', 'HG_DSH_NODE'} & set(result['reported']['env_withheld'])
    plan = _method_sandbox_plan(workspace, [sys.executable, str(improver.ROOT / 'methods/a/run.py')],
                                workspace, runtime=tuple(selected['runtime']))
    assert {root, home, node.parent.parent} <= set(plan['runtime_paths'])


def test_registry_installation_is_used_without_environment_overrides(tmp_path, monkeypatch):
    root, home, node = installation(tmp_path)
    fixture_git(tmp_path, monkeypatch)
    entry = bundled_entry(root, home)
    entry['interpreter'] = str(node)
    selected = resolve(entry)
    assert selected['ok'], selected
    assert selected['dsh_runtime']['root'] == str(root)
    assert selected['dsh_runtime']['node'] == str(node)
    assert selected['version'] == '9.9.9+999build'


def test_non_bin_node_layout_is_included_in_runtime_bindings(tmp_path, monkeypatch):
    root, home, node = installation(tmp_path)
    fixture_git(tmp_path, monkeypatch)
    standalone = tmp_path / 'standalone-runtime/custom-node'
    standalone.parent.mkdir()
    shutil.copy2(node, standalone)
    entry = bundled_entry(root, home)
    entry['interpreter'] = str(standalone)
    selected = resolve(entry)
    assert selected['ok'], selected
    assert str(standalone.parent) in selected['runtime']
    assert selected['dsh_runtime']['node'] == str(standalone)


@pytest.mark.skipif(shutil.which('bwrap') is None, reason='bubblewrap is not installed')
def test_external_dsh_launcher_runs_with_its_program_dependencies_in_sandbox(tmp_path, monkeypatch):
    from eval.containers import sandbox

    root, home, node = installation(tmp_path / 'installation')
    codex_home = tmp_path / 'unrelated-codex-home'
    codex_home.mkdir()
    credential = codex_home / 'auth.json'
    credential.write_text('fixture credential; must stay hidden')
    monkeypatch.setattr(improver, 'config_home', lambda: codex_home)

    # Neither the launcher nor its shebang interpreter is inside a declared DSH
    # tree or the work root. A parent bind must not accidentally make them visible.
    interpreter = tmp_path / 'launcher-interpreter/bin/fixture-python'
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text('#!/bin/sh\nexec ' + shlex.quote(str(Path(sys.executable).resolve()))
                           + ' "$@"\n')
    interpreter.chmod(0o755)
    launcher = tmp_path / 'external-launcher/dsh'
    launcher.parent.mkdir()
    payload = launcher.with_name('payload.txt')
    payload.write_text('external launcher payload')
    launcher.write_text(f'''#!{interpreter}
import json, sys
from pathlib import Path
if sys.argv[1:] == ['--version']:
    print('dsh 1.2.3')
else:
    print(json.dumps({{
        'payload': Path(__file__).with_name('payload.txt').read_text(),
        'cli': Path({str(root / 'apps/cli/lib/bin.js')!r}).read_text(),
        'credential_visible': Path({str(credential)!r}).exists(),
    }}))
''')
    launcher.chmod(0o755)
    entry = bundled_entry(root, home)
    entry.update(launcher=str(launcher), interpreter=str(node), build_command=[])
    selected = resolve(entry)
    assert selected['ok'], selected
    assert str(codex_home) not in selected['runtime']

    work_root = tmp_path / 'work'
    workspace = work_root / 'round/workspace'
    workspace.mkdir(parents=True)
    parent = work_root / 'parent'
    parent.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work_root))
    plan = _method_sandbox_plan(
        workspace, [sys.executable, str(improver.ROOT / 'methods/cli_improver.py')],
        workspace.parent, readonly=(parent,), runtime=tuple(selected['runtime']))
    command = sandbox.command([selected['path'], '--fixture-probe'],
                              env={'PATH': os.defpath}, **plan)
    ran = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=15)
    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout) == {
        'payload': 'external launcher payload', 'cli': '9.9.9',
        'credential_visible': False,
    }


@pytest.mark.skipif(shutil.which('bwrap') is None, reason='bubblewrap is not installed')
@pytest.mark.parametrize('alias_kind', ['file', 'directory'])
def test_bundled_launcher_alias_uses_visible_canonical_path(tmp_path, monkeypatch, alias_kind):
    from eval.containers import sandbox

    root, home, node = installation(tmp_path / 'installation')
    bundled = (improver.ROOT / 'methods/dsh-improver').resolve()
    aliases = tmp_path / 'aliases'
    aliases.mkdir()
    if alias_kind == 'file':
        launcher = aliases / 'dsh'
        launcher.symlink_to(bundled)
    else:
        (aliases / 'methods').symlink_to(bundled.parent, target_is_directory=True)
        launcher = aliases / 'methods/dsh-improver'
    credential = aliases / 'auth.json'
    credential.write_text('fixture credential; must remain hidden')
    monkeypatch.setattr(improver, 'config_home', lambda: aliases)
    probed = []
    run_version = improver._run_version

    def record_probe(path, **kwargs):
        probed.append(path)
        return run_version(path, **kwargs)

    monkeypatch.setattr(improver, '_run_version', record_probe)
    entry = bundled_entry(root, home)
    entry.update(launcher=str(launcher), interpreter=str(node), build_command=[])
    selected = resolve(entry)
    assert selected['ok'], selected
    identity = _improver_identity(selected)

    # The alias is outside every runtime/work tree. Execute the recorded path in
    # the actual namespace, so host-side probing cannot mask a missing binding.
    work_root = tmp_path / 'work'
    workspace = work_root / 'round/workspace'
    workspace.mkdir(parents=True)
    parent = work_root / 'parent'
    parent.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(work_root))
    plan = _method_sandbox_plan(
        workspace, [sys.executable, str(improver.ROOT / 'methods/cli_improver.py')],
        workspace.parent, readonly=(parent,), runtime=tuple(selected['runtime']))
    script = f'''
import pathlib, subprocess
version = subprocess.check_output([{identity['path']!r}, '--version'], text=True).strip()
assert version == '9.9.9', version
assert not pathlib.Path({str(credential)!r}).exists()
assert not pathlib.Path({str(aliases)!r}).exists()
assert not pathlib.Path({str(improver.ROOT / 'driver.py')!r}).exists()
print(version)
'''
    command = sandbox.command([sys.executable, '-c', script],
                              env={'PATH': os.defpath, 'HG_DSH_ROOT': str(root),
                                   'HG_DSH_NODE': str(node)}, **plan)
    ran = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=15)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == selected['version'] == '9.9.9'
    assert selected['path'] == identity['path'] == str(bundled)
    assert probed == [bundled]
    assert selected['sha256'] == hashlib.sha256(bundled.read_bytes()).hexdigest()
    assert str(aliases) not in selected['runtime']
    assert str(launcher.parent) not in selected['runtime']


@pytest.mark.parametrize('templates', [True, False])
def test_relocation_below_previous_root_is_applied_once(tmp_path, monkeypatch, templates):
    old_root, old_home, _ = installation(tmp_path / 'old')
    root, home, node = installation(old_root / 'relocated')
    fixture_git(tmp_path, monkeypatch)
    entry = bundled_entry(old_root, old_home)
    if not templates:
        entry['runtime'] = [str(old_root), str(old_home)]
        entry['build_command'][2] = str(old_root)
    monkeypatch.setenv('HG_DSH_ROOT', str(root))
    monkeypatch.setenv('HG_DSH_NODE', str(node))
    monkeypatch.setenv('HG_IMPROVER_DSH_HOME', str(home))
    selected = resolve(entry)
    assert selected['ok'], selected
    assert selected['version'] == '9.9.9+999build'
    assert set(selected['runtime']) == {str(root), str(home), str(node.parent.parent)}


def test_shipped_registry_prepares_without_host_specific_paths(tmp_path, monkeypatch):
    root, home, node = installation(tmp_path)
    fixture_git(tmp_path, monkeypatch)
    monkeypatch.setenv('HG_DSH_ROOT', str(root))
    monkeypatch.setenv('HG_DSH_NODE', str(node))
    monkeypatch.setenv('HG_IMPROVER_DSH_HOME', str(home))
    registry = improver.load_config(improver.DEFAULT_CONFIG)
    entry_before = json.dumps(registry['improvers']['dsh'], sort_keys=True)
    selected = improver.resolve(registry, requested='dsh')
    assert selected['ok'], selected
    assert selected['dsh_runtime']['root'] == str(root)
    assert {str(root), str(home)} <= set(selected['runtime'])
    assert json.dumps(registry['improvers']['dsh'], sort_keys=True) == entry_before


def test_legacy_registry_paths_are_rebased_with_explicit_overrides(tmp_path, monkeypatch):
    old_root, old_home, old_node = installation(tmp_path / 'old')
    root, home, node = installation(tmp_path / 'new')
    fixture_git(tmp_path, monkeypatch)
    entry = bundled_entry(old_root, old_home)
    entry.pop('root')
    # A separate home override must win even when its old path was below the old root.
    old_home = old_root / 'old-profile-home'
    entry['home'] = str(old_home)
    entry['interpreter'] = str(old_node)
    entry['runtime'] = [str(old_root), str(old_home)]
    entry['build_command'][2] = str(old_root)
    monkeypatch.setenv('HG_DSH_ROOT', str(root))
    monkeypatch.setenv('HG_DSH_NODE', str(node))
    monkeypatch.setenv('HG_IMPROVER_DSH_HOME', str(home))
    selected = resolve(entry)
    assert selected['ok'], selected
    assert {str(root), str(home)} <= set(selected['runtime'])
    assert not any(str(tmp_path / 'old') in path for path in selected['runtime'])
    assert selected['dsh_runtime']['root'] == str(root)
    assert selected['version'].endswith('+999build')


@pytest.mark.parametrize('missing', ['root', 'node', 'home'])
def test_missing_installation_configuration_is_refused_before_probing(tmp_path, monkeypatch, missing):
    root, home, node = installation(tmp_path)
    values = {'root': ('HG_DSH_ROOT', str(root)), 'node': ('HG_DSH_NODE', str(node)),
              'home': ('HG_IMPROVER_DSH_HOME', str(home))}
    for label, (name, value) in values.items():
        if label != missing:
            monkeypatch.setenv(name, value)
    if missing == 'node':
        monkeypatch.setenv('HG_DSH_NODE', str(tmp_path / 'no-node'))
    selected = resolve(bundled_entry())
    assert selected['ok'] is False
    assert values[missing][0] in selected['reason'], selected


@pytest.mark.parametrize('layout', ['standalone', 'bin-prefix'])
def test_node_installation_containing_platform_is_refused_before_probing(tmp_path, monkeypatch, layout):
    root, home, _ = installation(tmp_path / 'installation')
    shared = tmp_path / 'shared'
    platform = shared / 'platform'
    launcher = platform / 'methods/dsh-improver'
    launcher.parent.mkdir(parents=True)
    shutil.copy2(improver.ROOT / 'methods/dsh-improver', launcher)
    node = shared / ('node' if layout == 'standalone' else 'bin/node')
    node.parent.mkdir(exist_ok=True)
    node.write_text('#!/bin/sh\nprintf "dsh 1.2.3\\n"\n')
    node.chmod(0o755)
    assert (improver._interpreter_prefix(node) or node.parent) == shared
    monkeypatch.setattr(improver, 'ROOT', platform)
    probed = []

    def record_probe(path, **kwargs):
        probed.append(path)
        return True, '1.2.3', ''

    monkeypatch.setattr(improver, '_run_version', record_probe)
    entry = bundled_entry(root, home)
    entry.update(interpreter=str(node), build_command=[])
    selected = resolve(entry)
    assert selected['ok'] is False, selected
    assert 'must not contain or lie within the platform checkout' in selected['reason']
    assert str(shared) in selected['reason']
    assert probed == []


@pytest.mark.parametrize('location', ['tmp', 'var-tmp', 'configured', 'configured-alias',
                                      'configured-bin-prefix'])
def test_node_in_shared_temporary_root_is_refused_before_probing(tmp_path, monkeypatch, location):
    root, home, _ = installation(tmp_path / 'installation')
    with ExitStack() as cleanup:
        # Keep the platform outside both standard temporary roots: the existing
        # platform-overlap guard must not accidentally satisfy this regression.
        platform = Path(cleanup.enter_context(tempfile.TemporaryDirectory(
            prefix='hg-dsh-temp-platform-', dir=Path.home())))
        launcher = platform / 'methods/dsh-improver'
        launcher.parent.mkdir()
        shutil.copy2(improver.ROOT / 'methods/dsh-improver', launcher)
        monkeypatch.setattr(improver, 'ROOT', platform)
        if location in ('tmp', 'var-tmp'):
            shared = Path('/tmp' if location == 'tmp' else '/var/tmp')
        else:
            shared = tmp_path / 'shared-temporary-root'
            shared.mkdir()
            configured = shared
            if location == 'configured-alias':
                configured = tmp_path / 'tmp-alias'
                configured.symlink_to(shared, target_is_directory=True)
            monkeypatch.setenv('TMPDIR', str(configured))
            monkeypatch.setattr(tempfile, 'tempdir', None)
        bindir = shared / 'bin' if location == 'configured-bin-prefix' else shared
        if bindir != shared:
            bindir.mkdir()
        fd, filename = tempfile.mkstemp(prefix='hg-dsh-temp-node-', dir=bindir)
        os.close(fd)
        node = Path(filename)
        cleanup.callback(node.unlink)
        node.write_text('#!/bin/sh\nprintf "dsh 1.2.3\\n"\n')
        node.chmod(0o755)
        assert (improver._interpreter_prefix(node) or node.parent) == shared.resolve()
        probed = []

        def record_probe(path, **kwargs):
            probed.append(path)
            return True, '1.2.3', ''

        monkeypatch.setattr(improver, '_run_version', record_probe)
        monkeypatch.setenv('HG_DSH_NODE', str(node))
        entry = bundled_entry(root, home)
        entry['build_command'] = []
        selected = resolve(entry)
        assert selected['ok'] is False, selected
        assert 'shared temporary root' in selected['reason']
        assert str(shared.resolve()) in selected['reason']
        assert 'dedicated installation directory' in selected['reason']
        assert probed == []


def test_direct_launcher_has_no_personal_directory_fallback(tmp_path, monkeypatch):
    result = subprocess.run([str(improver.ROOT / 'methods/dsh-improver'), '--version'],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and 'set HG_DSH_ROOT' in result.stderr
    assert '/mnt/' not in result.stderr


def test_configuration_records_dsh_locations_without_credentials():
    cfg = config.load({'HG_DSH_ROOT': '/configured/dsh', 'HG_DSH_NODE': '/configured/node',
                       'HG_IMPROVER_DSH_HOME': '/configured/home', 'DEEPSEEK_API_KEY': 'private'})
    declared = config.dump(cfg)['improver']
    assert declared['dsh_root'] == '/configured/dsh'
    assert declared['dsh_node'] == '/configured/node'
    assert declared['dsh_home'] == '/configured/home'
    assert 'private' not in json.dumps(declared)


@pytest.mark.parametrize('field', ['model', 'provider', 'profile'])
def test_missing_resolved_identity_refuses_ambient_defaults(tmp_path, monkeypatch, field):
    identity = {'argv_style': 'dsh', 'model': 'fixture', 'provider': 'fixture', 'profile': 'fixture'}
    identity.pop(field)
    monkeypatch.setenv('HG_IMPROVER_' + field.upper(), 'ambient-wrong')
    with pytest.raises(ValueError, match='missing required field'):
        _method_env(tmp_path, identity)
    with pytest.raises(ValueError, match='missing required fields'):
        cli_improver._set_dsh_identity({}, identity)


@pytest.mark.skipif(shutil.which('bwrap') is None, reason='bubblewrap is not installed')
def test_resolved_identity_reaches_real_launcher_in_actual_method_sandbox(tmp_path, monkeypatch):
    from eval.containers import sandbox

    root, home, node = installation(tmp_path / 'installation')
    entry = bundled_entry(root, home)
    entry.update(interpreter=str(node), build_command=[])
    selected = resolve(entry)
    assert selected['ok'], selected
    identity = _improver_identity(selected)
    work = tmp_path / 'work'
    workspace = work / 'round/workspace'
    workspace.mkdir(parents=True)
    parent = work / 'parent'
    parent.mkdir()
    (parent / 'agent.py').write_text('# fixture\n')
    (parent / 'harness.json').write_text('{"entrypoint": "agent.py"}')
    private = tmp_path / 'unrelated-credential'
    private.write_text('fixture credential')
    protected_work = tmp_path / 'protected-work'
    protected_work.mkdir()
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(protected_work))
    monkeypatch.setenv('DSH_TEST_KEY', 'fake-declared-key')
    monkeypatch.setenv('OTHER_API_KEY', 'fake-other-key')
    monkeypatch.setenv('HG_AGENT_API_KEY', 'fake-agent-key')
    env = _method_env(workspace, selected)
    script = f'''
import json, pathlib, sys
sys.path.insert(0, {str(improver.ROOT / 'methods')!r})
import cli_improver
assert not pathlib.Path({str(private)!r}).exists()
assert not pathlib.Path({str(improver.ROOT / 'driver.py')!r}).exists()
result = cli_improver.propose({{'workspace': {str(workspace)!r}, 'improver': {identity!r}}}, pathlib.Path({str(parent)!r}), prompt='Fixture only.')
assert result['exit_code'] == 0, result['transcript']
print(json.dumps({{'executed': json.loads(result['transcript']), 'reported': result['reported']}}))
'''
    plan = _method_sandbox_plan(workspace, [sys.executable, str(improver.ROOT / 'methods/cli_improver.py')],
                                workspace.parent, readonly=(parent,), runtime=tuple(selected['runtime']))
    command = sandbox.command([sys.executable, '-c', script], env=env, **plan)
    ran = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
    receipt = json.loads(ran.stdout)
    executed = receipt['executed']
    assert executed['profile'] == identity['profile']
    assert "provider: 'fixture-provider'" in executed['pin']
    assert "model: 'test-model'" in executed['pin']
    assert executed['cli'] == str(root / 'apps/cli/lib/bin.js')
    assert executed['node'] == str(node)
    assert executed['declared_key_present']
    assert not executed['agent_key_present'] and not executed['other_key_present']
    assert receipt['reported']['dsh_identity'] == {field: identity[field] for field in ('model', 'provider', 'profile')}
    assert 'fake-declared-key' not in ran.stdout


@pytest.mark.parametrize('field', ['MODEL', 'PROVIDER', 'PROFILE'])
def test_direct_launcher_refuses_missing_identity(tmp_path, field):
    root, _, node = installation(tmp_path / 'installation')
    candidate = tmp_path / 'workspace/candidate'
    candidate.mkdir(parents=True)
    env = {'PATH': os.defpath, 'HG_DSH_ROOT': str(root), 'HG_DSH_NODE': str(node),
           **{'HG_IMPROVER_' + name: 'fixture' for name in ('MODEL', 'PROVIDER', 'PROFILE')}}
    env.pop('HG_IMPROVER_' + field)
    ran = subprocess.run([str(improver.ROOT / 'methods/dsh-improver'), 'fixture'],
                         cwd=candidate, env=env, capture_output=True, text=True, timeout=5)
    assert ran.returncode == 2
    assert 'missing required resolved identity HG_IMPROVER_' + field in ran.stderr
    assert not (candidate.parent / '.dsh-model-pin.yml').exists()


def test_launcher_quotes_yaml_identity_without_changing_values(tmp_path):
    import yaml

    root, _, node = installation(tmp_path / 'installation')
    candidate = tmp_path / 'workspace/candidate'
    candidate.mkdir(parents=True)
    model, provider = "model: # 'quoted'", "provider: # 'quoted'"
    env = {'PATH': os.defpath, 'HG_DSH_ROOT': str(root), 'HG_DSH_NODE': str(node),
           'DSH_HOME': str(tmp_path / 'installation/prepared home'),
           'HG_IMPROVER_MODEL': model, 'HG_IMPROVER_PROVIDER': provider,
           'HG_IMPROVER_PROFILE': 'fixture-profile'}
    ran = subprocess.run([str(improver.ROOT / 'methods/dsh-improver'), 'fixture'],
                         cwd=candidate, env=env, capture_output=True, text=True, timeout=5)
    assert ran.returncode == 0, ran.stderr
    assert yaml.safe_load(json.loads(ran.stdout)['pin'])[0]['config'] == {'model': model, 'provider': provider}
