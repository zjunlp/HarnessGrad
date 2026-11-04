"""A prepared Codex installation stays the same in host and task namespaces."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
import venv

import pytest

from base_harness.codex import codex_runtime
from tools.configure_codex_harness import configure


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "base_harness/codex"
needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is unavailable")


@pytest.fixture(autouse=True)
def clear_overrides(monkeypatch):
    monkeypatch.delenv("HG_AGENT_CODEX", raising=False)
    monkeypatch.delenv("HG_AGENT_CODEX_COMMAND", raising=False)


@pytest.fixture
def installed(tmp_path):
    # Installation data must survive the native sandbox's private /tmp.
    with tempfile.TemporaryDirectory(prefix="hg-codex-runtime-", dir="/var/tmp") as directory:
        harness = tmp_path / "candidate"
        shutil.copytree(SOURCE, harness)
        base = Path(directory)
        program = native(base / "installation")
        yield harness, program, base


def native(root, version="fixture-1"):
    program = root / "bin/codex"
    program.parent.mkdir(parents=True)
    program.with_name("codex-exec-server").write_text("fixture helper")
    program.write_text(f'''#!{Path(sys.executable).resolve()}
import json, os, sys
from pathlib import Path
assert Path(__file__).with_name('codex-exec-server').read_text() == 'fixture helper'
if '--version' in sys.argv:
    assert 'HG_AGENT_API_KEY' not in os.environ
    print('codex-cli {version}')
    raise SystemExit(0)
sys.stdin.read()
probes = json.loads(os.environ.get('HG_AGENT_CODEX_FIXTURE_PROBES', '[]'))
def readable(path):
    try:
        Path(path).read_text()
        return True
    except OSError:
        return False
payload = {{'version': '{version}', 'readable': [readable(p) for p in probes]}}
Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(payload))
print(json.dumps({{'type': 'item.completed', 'item': {{'type': 'agent_message', 'text': 'done'}}}}))
print(json.dumps({{'type': 'turn.completed', 'usage': {{'input_tokens': 1, 'output_tokens': 1}}}}))
''')
    program.chmod(0o755)
    return program


def agent(harness):
    spec = importlib.util.spec_from_file_location("_prepared_codex_agent", harness / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(harness, program=None, **kwargs):
    return configure(harness, str(program) if program else None, env={"PATH": os.defpath}, **kwargs)


def test_template_contains_no_local_installation_and_requires_preparation(tmp_path):
    harness = tmp_path / "candidate"
    shutil.copytree(SOURCE, harness)
    manifest = json.loads((harness / "harness.json").read_text())
    assert manifest["runtime_paths"] == []
    assert codex_runtime.RECORD not in manifest
    with pytest.raises(ValueError, match="not prepared"):
        agent(harness)._codex_argv()


@needs_bwrap
def test_path_discovery_is_fixed_before_path_changes(installed):
    harness, program, base = installed
    receipt = configure(harness, env={"PATH": str(program.parent) + os.pathsep + os.defpath})
    other = native(base / "other-installation", version="fixture-2")
    selected = codex_runtime.resolve(harness, {"PATH": str(other.parent), "HG_AGENT_CODEX": "codex"})
    assert selected["argv"] == [str(program)]
    assert selected["version"] == "codex-cli fixture-1"
    manifest = json.loads((harness / "harness.json").read_text())
    assert manifest[codex_runtime.RECORD] == {k: v for k, v in receipt.items() if k != "harness"}
    assert manifest["runtime_paths"] == selected["runtime_paths"]
    assert str(program.parent) in selected["runtime_paths"]
    assert str(program.with_name("codex-exec-server")) in {p["path"] for p in selected["files"]}


@needs_bwrap
def test_native_alias_does_not_bind_its_alias_directory(installed):
    harness, program, base = installed
    alias = base / "user-home/codex"
    alias.parent.mkdir()
    alias.symlink_to(program)
    selected = prepare(harness, alias)
    assert selected["argv"] == [str(program)]
    assert str(alias.parent) not in selected["runtime_paths"]


@needs_bwrap
def test_companion_alias_keeps_its_external_target_visible(installed):
    harness, program, base = installed
    helper = program.with_name("codex-exec-server")
    real = base / "companion/codex-exec-server"
    real.parent.mkdir()
    helper.rename(real)
    helper.symlink_to(real)
    selected = prepare(harness, program)
    assert str(real) in selected["runtime_paths"]
    assert codex_runtime.resolve(harness, {})["version"] == "codex-cli fixture-1"


@needs_bwrap
def test_preparation_does_not_import_candidate_code_on_host(installed):
    harness, program, base = installed
    marker = base / "candidate-executed-on-host"
    (harness / "codex_runtime.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
        "raise RuntimeError('candidate code executed during trusted preparation')\n")
    prepare(harness, program)
    assert not marker.exists()


@needs_bwrap
def test_a_binary_in_home_binds_only_the_file(installed):
    harness, _, base = installed
    home = base / "user-home"
    home.mkdir()
    program = home / "codex"
    program.write_text('#!/bin/sh\nprintf "codex-cli standalone-fixture\\n"\n')
    program.chmod(0o755)
    (home / "auth.json").write_text("synthetic secret")
    selected = prepare(harness, program)
    assert selected["runtime_paths"] == [str(program)]


@needs_bwrap
def test_companions_outside_a_dedicated_bin_use_file_bindings(installed):
    harness, program, base = installed
    home = base / "user-home"
    home.mkdir()
    relocated = home / "codex"
    program.rename(relocated)
    helper = home / "codex-exec-server"
    program.with_name("codex-exec-server").rename(helper)
    selected = prepare(harness, relocated)
    assert str(home) not in selected["runtime_paths"]
    assert {str(relocated), str(helper)} <= set(selected["runtime_paths"])


@needs_bwrap
@pytest.mark.parametrize("variable", ["HG_AGENT_CODEX", "HG_AGENT_CODEX_COMMAND"])
def test_invalid_explicit_choice_never_falls_back_to_path(installed, variable):
    harness, program, base = installed
    original = (harness / "harness.json").read_bytes()
    with pytest.raises(ValueError, match="missing or not executable"):
        configure(harness, env={"PATH": str(program.parent), variable: str(base / "missing")})
    assert (harness / "harness.json").read_bytes() == original


@needs_bwrap
def test_overrides_must_match_prepared_choice_and_command_keeps_precedence(installed):
    harness, program, base = installed
    prepare(harness, program)
    other = native(base / "other-installation", version="fixture-2")
    with pytest.raises(ValueError, match="differs from the prepared"):
        codex_runtime.resolve(harness, {"HG_AGENT_CODEX": str(other)})
    with pytest.raises(ValueError, match="missing or not executable"):
        codex_runtime.resolve(harness, {"HG_AGENT_CODEX": str(base / "missing")})
    selected = codex_runtime.resolve(harness, {"HG_AGENT_CODEX": str(other),
                                             "HG_AGENT_CODEX_COMMAND": shlex.quote(str(program))})
    assert selected["argv"] == [str(program)]


@needs_bwrap
@pytest.mark.parametrize("target", ["binary", "helper", "bindings", "identity"])
def test_runtime_changes_are_refused(installed, target):
    harness, program, _ = installed
    prepare(harness, program)
    if target in ("binary", "helper"):
        path = program if target == "binary" else program.with_name("codex-exec-server")
        path.write_text(path.read_text() + "\n# changed\n")
    else:
        manifest = json.loads((harness / "harness.json").read_text())
        if target == "bindings":
            manifest["runtime_paths"] = []
        else:
            manifest[codex_runtime.RECORD]["files"] = []
        (harness / "harness.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="changed|bindings differ|invalid prepared"):
        codex_runtime.resolve(harness, {})


@pytest.mark.parametrize("path", ["/", "/usr", "/usr/local", "/usr/local/bin", "/tmp", "/var/tmp"])
def test_wide_or_hidden_runtime_directories_are_refused(path):
    with pytest.raises(ValueError, match="dedicated|hidden /tmp|shared temporary"):
        codex_runtime.validate_binding(Path(path))


def test_home_platform_aliases_and_configured_temporary_root_are_refused(installed, monkeypatch):
    _, _, base = installed
    with pytest.raises(ValueError, match="dedicated"):
        codex_runtime.validate_binding(Path.home())
    platform = base / "platform"
    platform.mkdir()
    alias = base / "platform-alias"
    alias.symlink_to(platform, target_is_directory=True)
    with pytest.raises(ValueError, match="overlaps"):
        codex_runtime.validate_binding(alias, forbidden=(platform,))
    with pytest.raises(ValueError, match="overlaps"):
        codex_runtime.validate_binding(base, forbidden=(platform,))
    shared = base / "shared-tmp"
    shared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(shared))
    with pytest.raises(ValueError, match="shared temporary"):
        codex_runtime.validate_binding(shared)


@needs_bwrap
def test_failed_dependency_probe_leaves_previous_preparation_intact(installed):
    harness, program, base = installed
    prepare(harness, program)
    original = (harness / "harness.json").read_bytes()
    payload = base / "unbound/payload"
    payload.parent.mkdir()
    payload.write_text("dependency")
    wrapper = base / "wrapper"
    wrapper.write_text(f'#!/bin/sh\ncat {shlex.quote(str(payload))} >/dev/null || exit 3\nprintf "codex-cli wrapper\\n"\n')
    wrapper.chmod(0o755)
    with pytest.raises(ValueError, match="failed in native sandbox"):
        prepare(harness, wrapper)
    assert (harness / "harness.json").read_bytes() == original
    selected = prepare(harness, wrapper, runtime_paths=(payload,))
    assert selected["version"] == "codex-cli wrapper"


@needs_bwrap
@pytest.mark.parametrize("separator", [",", "\n", "\r"])
@pytest.mark.parametrize("alias", [False, True])
def test_unrepresentable_docker_paths_leave_manifest_intact(installed, separator, alias):
    harness, program, base = installed
    prepare(harness, program)
    original = (harness / "harness.json").read_bytes()
    bad = base / ("install" + separator + "invalid")
    bad.mkdir()
    extra = bad
    if alias:
        extra = base / "runtime-alias"
        extra.symlink_to(bad, target_is_directory=True)
    with pytest.raises(ValueError, match="Docker bind mount"):
        prepare(harness, program, runtime_paths=(extra,))
    assert (harness / "harness.json").read_bytes() == original


@needs_bwrap
@pytest.mark.parametrize("layout", ["nested", "hoisted"])
def test_npm_launcher_binds_node_and_native_package(installed, layout):
    harness, _, base = installed
    package = base / "npm/node_modules/@openai/codex"
    script = package / "bin/codex.js"
    script.parent.mkdir(parents=True)
    dependency = ((package if layout == "nested" else base / "npm") /
                  "node_modules/@openai/codex-linux-x64")
    backend = dependency / "vendor/fixture/bin/codex"
    backend.parent.mkdir(parents=True)
    backend.write_text("codex-cli npm-fixture")
    (dependency / "package.json").write_text('{"name":"@openai/codex-linux-x64"}')
    (package / "package.json").write_text(json.dumps({"name": "@openai/codex",
        "optionalDependencies": {"@openai/codex-linux-x64": "fixture"}}))
    script.write_text('#!/usr/bin/env node\n' + str(backend))
    script.chmod(0o755)
    node = base / "node/bin/node-custom"
    node.parent.mkdir(parents=True)
    node.write_text(f'''#!{Path(sys.executable).resolve()}
from pathlib import Path
import sys
backend = Path(sys.argv[1]).read_text().splitlines()[1]
print(Path(backend).read_text())
''')
    node.chmod(0o755)
    selected = prepare(harness, script, node=str(node))
    assert selected["argv"] == [str(node), str(script)]
    assert selected["version"] == "codex-cli npm-fixture"
    assert {str(node.parent.parent), str(package), str(dependency)} <= set(selected["runtime_paths"])
    backend.write_text("changed native backend")
    with pytest.raises(ValueError, match="runtime file changed"):
        codex_runtime.resolve(harness, {})


@needs_bwrap
@pytest.mark.parametrize("spelling", ["absolute", "relative", "path"])
def test_python_wrapper_preserves_venv_dependencies(installed, monkeypatch, spelling):
    harness, _, base = installed
    environment = base / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    python = environment / "bin/python"
    purelib = subprocess.check_output([str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
    Path(purelib, "codex_private_fixture.py").write_text("VERSION='codex-cli venv-fixture'\n")
    wrapper = environment / "wrapper.py"
    wrapper.write_text("import sys\nfrom codex_private_fixture import VERSION\nassert sys.prefix != sys.base_prefix\nprint(VERSION)\n")
    monkeypatch.chdir(base)
    selected_python = {"absolute": str(python), "relative": "venv/bin/python", "path": "python"}[spelling]
    raw = shlex.join([selected_python, str(wrapper)])
    selected = configure(harness, command=raw, env={"PATH": str(python.parent) + os.pathsep + os.defpath})
    assert selected["argv"][0] == str(python)
    assert str(environment) in selected["runtime_paths"]
    monkeypatch.chdir(harness)
    assert codex_runtime.resolve(harness, {"HG_AGENT_CODEX_COMMAND": raw})["version"] == "codex-cli venv-fixture"


@needs_bwrap
def test_nonvenv_python_alias_does_not_mount_the_alias_home(installed):
    harness, _, base = installed
    alias = base / "user-home/bin/python"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(Path(sys.executable).resolve())
    wrapper = base / "wrapper.py"
    wrapper.write_text("print('codex-cli python-fixture')\n")
    selected = prepare(harness, command=shlex.join([str(alias), str(wrapper)]))
    assert selected["argv"][0] == str(Path(sys.executable).resolve())
    assert str(alias.parent.parent) not in selected["runtime_paths"]


@needs_bwrap
def test_inline_wrapper_arguments_are_not_treated_as_file_names(installed):
    harness, _, _ = installed
    code = '#' + 'x' * 300 + "\nprint('codex-cli inline-fixture')"
    selected = prepare(harness, command=shlex.join([str(Path(sys.executable).resolve()), '-c', code]))
    assert selected['argv'][2] == code
    assert selected['version'] == 'codex-cli inline-fixture'


@needs_bwrap
def test_native_task_executes_fixed_cli_with_private_siblings_hidden(installed, tmp_path):
    from eval.containers import sandbox

    harness, program, base = installed
    selected = prepare(harness, program)
    secret = base / "user-home/.codex/auth.json"
    secret.parent.mkdir(parents=True)
    secret.write_text("synthetic credential")
    other_task = tmp_path / "other-task/answer.txt"
    other_task.parent.mkdir()
    other_task.write_text("synthetic other-task answer")
    platform = tmp_path / "platform"
    platform.mkdir()
    scorer = platform / "scorer.py"
    scorer.write_text("synthetic scorer")
    work = tmp_path / "work"
    work.mkdir()
    task = tmp_path / "task"
    task.mkdir()
    (task / "task.json").write_text('{"task_id":"fixture","goal":"Use the fixture CLI"}')
    env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1",
           "HG_AGENT_CODEX_FIXTURE_PROBES": json.dumps([str(secret), str(other_task), str(scorer)])}
    command = sandbox.command([str(Path(sys.executable).resolve()), str(harness / "agent.py"),
        "--task", str(task / "task.json"), "--workdir", str(task)],
        platform=platform, work_root=work, repo=harness, task_dir=task, env=env,
        network="none", runtime_paths=tuple(Path(p) for p in selected["runtime_paths"]))
    ran = subprocess.run(command, cwd=task, capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
    assert json.loads((task / "answer.txt").read_text()) == {"version": "fixture-1", "readable": [False]*3}
    trace = [json.loads(line) for line in (task / "trace.jsonl").read_text().splitlines()]
    assert trace[0]["harness_identity"]["codex_runtime"]["version"] == "codex-cli fixture-1"


@needs_bwrap
@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker is unavailable")
def test_exec_task_keeps_image_python_and_runtime_readonly(installed, tmp_path):
    from eval.containers import container

    image = "python:3.12-slim"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=15).returncode:
        pytest.skip("No usable cached python:3.12-slim; images are never pulled")
    harness, program, _ = installed
    program.write_text('''#!/bin/sh
if [ "$1" = --version ]; then printf 'codex-cli container-fixture\\n'; exit 0; fi
while [ "$#" -gt 0 ]; do
    if [ "$1" = -o ]; then shift; answer="$1"; fi
    shift
done
cat >/dev/null
printf 'container-fixture' >"$answer"
printf '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\\n'
printf '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\\n'
''')
    selected = prepare(harness, program)
    task = tmp_path / "container-task"
    task.mkdir()
    (task / "task.json").write_text('{"goal":"Use the fixture CLI"}')
    script = f'''
import pathlib, subprocess, sys
assert sys.version_info[:2] == (3,12)
try:
    pathlib.Path({str(program)!r}).write_text('changed')
except OSError:
    pass
else:
    raise AssertionError('Codex runtime is writable')
subprocess.check_call([sys.executable, {str(harness / 'agent.py')!r}, '--task', '/task/task.json', '--workdir', '/task'])
assert pathlib.Path('/task/answer.txt').read_text() == 'container-fixture'
print('image-python-and-codex-ok')
'''
    name = "hg-codex-runtime-test-" + uuid.uuid4().hex[:12]
    command = container.command(["/usr/local/bin/python", "-c", script], image=image,
        task_dir=task, task_mount="/task", harness_root=harness,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        readonly_paths=tuple(Path(p) for p in selected["runtime_paths"]),
        limits={"memory": "128m", "cpus": 0.5, "pids": 64})
    command[2:2] = ["--pull=never", "--name", name]
    try:
        ran = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert ran.returncode == 0, ran.stderr
        assert ran.stdout.strip() == "image-python-and-codex-ok"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
