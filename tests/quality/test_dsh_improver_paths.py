"""A relocated DSH installation must survive resolution and the restricted CLI hop."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

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
        'model': 'test-model', 'api_key_env': 'DSH_TEST_KEY',
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
    env = _method_env(workspace, selected)
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
                        'agent_key_present': False, 'other_key_present': False,
                        'declared_key_present': True}
    assert result['changed'] == ['agent.py']
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
