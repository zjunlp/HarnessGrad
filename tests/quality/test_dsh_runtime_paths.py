"""Relocated DSH installs keep launch, facts, links and sandbox bindings together."""
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

import pytest

from tools.configure_dsh_harness import configure, _runtime_directory

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "base_harness/deepseek-harness"


@pytest.fixture
def agent(monkeypatch):
    spec = importlib.util.spec_from_file_location("_dsh_relocated_test", SOURCE / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("HG_AGENT_DSH", "HG_AGENT_DSH_ROOT", "HG_AGENT_NODE", "HG_AGENT_DSH_COMMAND"):
        monkeypatch.delenv(name, raising=False)
    return module


@pytest.fixture
def installation():
    # Runtime dependencies must live outside private /tmp. Keep harness/task
    # scratch on pytest's ordinary tmp_path so real sandbox tests exercise that
    # separation, and clean up every installation when the test ends.
    with tempfile.TemporaryDirectory(prefix="hg-dsh-runtime-", dir="/var/tmp") as directory:
        count = 0

        def create(tmp_path, *, full_farm=False):
            nonlocal count
            count += 1
            return _installation(tmp_path, full_farm=full_farm,
                                 runtime_base=Path(directory) / str(count))

        yield create


def _installation(tmp_path, *, full_farm=False, runtime_base=None):
    runtime_base = runtime_base or tmp_path
    harness = tmp_path / "candidate"
    shutil.copytree(SOURCE, harness)
    if not full_farm:
        (harness / "dsh_farm.tsv").write_text(
            "@example/one\tapps/cli/node_modules/one\n"
            "two\tapps/cli/node_modules/nested/node_modules/two\n")
    root = runtime_base / "another-user" / "relocated-dsh"
    cli = root / "apps/cli/lib/bin.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fixture CLI; the fake Node owns execution\n")
    for line in (harness / "dsh_farm.tsv").read_text().splitlines():
        (root / line.split("\t")[1]).mkdir(parents=True, exist_ok=True)
    node = runtime_base / "node-install" / "bin/node"
    node.parent.mkdir(parents=True)
    node.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "if '--input-type=module' in sys.argv:\n"
        " print(json.dumps({'resolved': True, 'loader': os.environ['HG_PROBE_LOADER']}))\n"
        "elif '--dump-config' in sys.argv:\n"
        " print('- id: relocated-fixture')\n"
        "else:\n"
        " print('RELOCATED_DSH_ANSWER')\n")
    node.chmod(0o755)
    return harness, root, cli, node


def _use_copy(agent, monkeypatch, harness):
    monkeypatch.setattr(agent, "__file__", str(harness / "agent.py"))
    monkeypatch.setattr(agent, "FARM_MANIFEST", harness / "dsh_farm.tsv")


def test_preparation_records_paths_used_by_the_sandbox_and_all_farm_links(agent, monkeypatch, tmp_path, installation):
    harness, root, cli, node = installation(tmp_path, full_farm=True)
    receipt = configure(harness, root, str(node))
    assert receipt["farm_targets"] == 277
    manifest = json.loads((harness / "harness.json").read_text())
    assert manifest["runtime_paths"] == [str(root), str(node), str(node.parent.parent)]
    _use_copy(agent, monkeypatch, harness)
    # No runtime overrides: the prepared declaration must be enough after staging.
    selected = agent.runtime({"PATH": ""})
    assert selected["argv"] == [str(node), str(cli)]
    home = tmp_path / "scratch-home"
    assert agent._install_profile(home, selected["root"]) == 277
    farm = home / ".dsh/profiles/node_modules"
    for name, target in agent.farm_targets(root):
        assert (farm / name).is_symlink()
        assert (farm / name).readlink() == target
        assert (farm / name).resolve().is_relative_to(root)
    assert agent._install_profile(home, root) == -2


def test_explicit_cli_and_node_override_the_prepared_installation(agent, monkeypatch, tmp_path, installation):
    first = tmp_path / "first"
    first.mkdir()
    harness, first_root, _, node = installation(first)
    configure(harness, first_root, str(node))
    second = tmp_path / "second"
    second.mkdir()
    _, root, cli, other_node = installation(second)
    _use_copy(agent, monkeypatch, harness)
    selected = agent.runtime({"HG_AGENT_DSH": str(cli), "HG_AGENT_NODE": str(other_node)})
    assert selected["root"] == root
    assert selected["argv"] == [str(other_node), str(cli)]
    home = tmp_path / "home"
    agent._install_profile(home, selected["root"])
    assert (home / ".dsh/profiles/node_modules/@example/one").readlink().is_relative_to(root)


def test_preparation_preserves_exact_node_after_symlink_resolution_and_in_sandbox_plan(agent, monkeypatch, tmp_path, installation):
    from eval.containers import sandbox

    harness, root, cli, node = installation(tmp_path)
    renamed = node.with_name("node-custom-version")
    node.rename(renamed)
    node.symlink_to(renamed)
    receipt = configure(harness, root, str(node))
    _use_copy(agent, monkeypatch, harness)
    selected = agent.runtime({"PATH": ""})
    assert selected["argv"] == [str(renamed), str(cli)]
    monkeypatch.setattr(sandbox, "_bwrap", lambda: "/fixture/bwrap")
    monkeypatch.setattr(sandbox, "_interpreter_mounts", lambda *args: ())
    work = tmp_path / "work"
    task = work / "task"
    task.mkdir(parents=True)
    platform = tmp_path / "platform"
    platform.mkdir()
    plan = sandbox.command(selected["argv"], platform=platform, work_root=work,
                           repo=harness, task_dir=task, env={},
                           runtime_paths=tuple(Path(p) for p in receipt["runtime_paths"]))
    for bound in (root, renamed, renamed.parent.parent):
        assert any(plan[index:index + 3] == ["--ro-bind", str(bound), str(bound)]
                   for index in range(len(plan) - 2))


@pytest.mark.parametrize("directory", ["/", "/usr", "/usr/local", "/usr/lib/nodejs", "/bin", "/lib64"])
def test_system_runtime_trees_cannot_replace_the_image(directory):
    with pytest.raises(ValueError, match="dedicated installation directory"):
        _runtime_directory(Path(directory), "Node installation")


@pytest.mark.parametrize("use_path", [False, True])
def test_system_node_alias_is_rejected_before_manifest_changes(tmp_path, monkeypatch, installation, use_path):
    harness, root, _, node = installation(tmp_path)
    configure(harness, root, str(node))
    original = (harness / "harness.json").read_bytes()
    # A shell stands in for a system Node: preparation only resolves an executable,
    # and the regression concerns its prefix, not which language it implements.
    system_binary = Path(shutil.which("sh")).resolve()
    alias = tmp_path / "bin/node"
    alias.parent.mkdir()
    alias.symlink_to(system_binary)
    monkeypatch.setenv("PATH", str(alias.parent))
    with pytest.raises(ValueError, match="Node installation.*outside system trees"):
        configure(harness, root, "node" if use_path else str(alias))
    assert (harness / "harness.json").read_bytes() == original
    assert not list(harness.glob(".dsh-manifest-*"))


@pytest.mark.parametrize("component", ["DSH root", "Node installation"])
def test_tmp_installation_alias_is_rejected_before_manifest_changes(tmp_path, installation, component):
    harness, root, _, node = installation(tmp_path)
    configure(harness, root, str(node))
    original = (harness / "harness.json").read_bytes()
    # Use explicit /tmp even when pytest's --basetemp lives elsewhere. Alias from
    # a supported directory to ensure the physical target is what gets checked.
    with tempfile.TemporaryDirectory(prefix="hg-dsh-hidden-", dir="/tmp") as directory:
        hidden = Path(directory)
        if component == "DSH root":
            shutil.copytree(root, hidden / "dsh")
            alias = root.with_name("aliased-dsh")
            alias.symlink_to(hidden / "dsh", target_is_directory=True)
            root = alias
        else:
            shutil.copy2(node, hidden / "node")
            alias = node.with_name("aliased-node")
            alias.symlink_to(hidden / "node")
            node = alias
        with pytest.raises(ValueError, match=component + ".*under /tmp"):
            configure(harness, root, str(node))
    assert (harness / "harness.json").read_bytes() == original
    assert not list(harness.glob(".dsh-manifest-*"))


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is not installed")
def test_prepared_runtime_executes_read_only_in_actual_native_sandbox(tmp_path, installation):
    from eval.containers import sandbox

    harness, root, cli, node = installation(tmp_path)
    prepared = configure(harness, root, str(node))
    work = tmp_path / "work"
    work.mkdir()
    task = tmp_path / "task"
    task.mkdir()
    private = tmp_path / "other-task-secret"
    private.write_text("must remain hidden")
    script = f'''
import json, pathlib, subprocess
cli, node = pathlib.Path({str(cli)!r}), pathlib.Path({str(node)!r})
assert cli.is_file()
assert subprocess.check_output([str(node), str(cli)], text=True).strip() == 'RELOCATED_DSH_ANSWER'
for target in (cli, node):
    try:
        target.write_text('changed')
    except OSError:
        pass
    else:
        raise AssertionError('runtime is writable: ' + str(target))
assert not pathlib.Path({str(ROOT / 'driver.py')!r}).exists()
assert not pathlib.Path({str(private)!r}).exists()
pathlib.Path({str(task / 'result')!r}).write_text('ok')
print('runtime-visible-and-read-only')
'''
    command = sandbox.command([sys.executable, "-c", script], platform=ROOT,
                              work_root=work, task_dir=task, repo=harness,
                              env={"PATH": os.defpath},
                              runtime_paths=tuple(Path(p) for p in prepared["runtime_paths"]))
    ran = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "runtime-visible-and-read-only"
    assert (task / "result").read_text() == "ok"


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker is not installed")
def test_prepared_runtime_preserves_image_python_and_executes_in_container(tmp_path, installation):
    from eval.containers import container

    image = "python:3.12-slim"
    inspected = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=15)
    if inspected.returncode:
        pytest.skip(f"Docker unavailable or no cached {image}; this test never pulls images")
    harness, root, cli, node = installation(tmp_path)
    node.write_text('#!/bin/sh\ntest -r "$1" || exit 2\nprintf "DSH_FIXTURE_OK\\n"\n')
    prepared = configure(harness, root, str(node))
    task = tmp_path / "task"
    task.mkdir()
    script = f'''
import pathlib, subprocess, sys
assert sys.version_info[:2] == (3, 12)
assert pathlib.Path({str(cli)!r}).is_file()
assert subprocess.check_output([{str(node)!r}, {str(cli)!r}], text=True).strip() == 'DSH_FIXTURE_OK'
for target in ({str(cli)!r}, {str(node)!r}):
    try:
        pathlib.Path(target).write_text('changed')
    except OSError:
        pass
    else:
        raise AssertionError('runtime is writable: ' + target)
print('image-python-and-runtime-ok')
'''
    name = "hg-dsh-runtime-test-" + uuid.uuid4().hex[:12]
    command = container.command(["/usr/local/bin/python", "-c", script], image=image,
                                task_dir=task, task_mount="/task", harness_root=harness,
                                readonly_paths=tuple(Path(p) for p in prepared["runtime_paths"]),
                                limits={"memory": "128m", "cpus": 0.5, "pids": 64})
    command[2:2] = ["--pull=never", "--name", name]
    try:
        ran = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert ran.returncode == 0, ran.stderr
        assert ran.stdout.strip() == "image-python-and-runtime-ok"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)


def test_one_runtime_drives_launch_configuration_dump_and_loader_probe(agent, monkeypatch, tmp_path, installation):
    harness, root, cli, node = installation(tmp_path)
    configure(harness, root, str(node))
    _use_copy(agent, monkeypatch, harness)
    monkeypatch.setenv("HOME", str(tmp_path / "task-home"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "task-temp"))
    monkeypatch.setenv("HG_AGENT_MODEL", "fixture-model")
    monkeypatch.setenv("HG_AGENT_BASE_URL", "http://127.0.0.1:1/v1")
    work = tmp_path / "task"
    work.mkdir()
    assert agent.run({"task_id": "synthetic", "goal": "Return a fixture answer."}, work) == 0
    assert (work / "answer.txt").read_text().strip() == "RELOCATED_DSH_ANSWER"
    trace = [json.loads(line) for line in (work / "trace.jsonl").read_text().splitlines()]
    facts = next(row["harness_facts"] for row in trace if "harness_facts" in row)
    assert facts["dump_ok"] is True
    assert facts["runtime_node"] == str(node)
    assert facts["runtime_cli"] == str(cli)
    profile = next(row["profile"] for row in trace if "profile" in row)
    assert profile["links_created"] == 2
    assert profile["runtime_root"] == str(root)
    assert profile["loader"] == str(root / "vendor/loader/lib/index.js")


def test_opaque_command_wrapper_owns_its_runtime_and_is_used_for_dump(agent, monkeypatch, tmp_path):
    wrapper = tmp_path / "custom wrapper.py"
    wrapper.write_text(
        "import sys\n"
        "print('- id: wrapper' if '--dump-config' in sys.argv else 'WRAPPER_ANSWER')\n")
    monkeypatch.setenv("HG_AGENT_DSH_COMMAND", shlex.join([sys.executable, str(wrapper), "--key=private-fixture"]))
    # An opaque wrapper must not depend on a Node override it does not use.
    monkeypatch.setenv("HG_AGENT_NODE", str(tmp_path / "unused-missing-node"))
    monkeypatch.setenv("HOME", str(tmp_path / "wrapper-home"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "wrapper-temp"))
    work = tmp_path / "wrapper-task"
    work.mkdir()
    assert agent.run({"goal": "Use the fixture wrapper."}, work) == 0
    assert (work / "answer.txt").read_text().strip() == "WRAPPER_ANSWER"
    trace = [json.loads(line) for line in (work / "trace.jsonl").read_text().splitlines()]
    facts = next(row["harness_facts"] for row in trace if "harness_facts" in row)
    assert facts["dump_ok"] is True
    assert facts["runtime_cli"] is None
    assert facts["runtime_command_sha"]
    assert "private-fixture" not in (work / "trace.jsonl").read_text()
    profile = next(row["profile"] for row in trace if "profile" in row)
    assert profile["links_created"] is None
    assert profile["probe_skipped"] == "custom command owns its runtime"
    assert not (tmp_path / "wrapper-home/.dsh/profiles/node_modules").exists()


def test_direct_node_command_uses_its_interpreter_for_launch_dump_and_loader(agent, monkeypatch, tmp_path, installation):
    harness, root, cli, node_b = installation(tmp_path)
    configure(harness, root, str(node_b))
    _use_copy(agent, monkeypatch, harness)
    node_a = tmp_path / "different-node-install/bin/node-a"
    node_a.parent.mkdir(parents=True)
    node_a.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "if '--input-type=module' in sys.argv:\n"
        " print(json.dumps({'node_fixture': 'A', 'loader': os.environ['HG_PROBE_LOADER']}))\n"
        "elif '--dump-config' in sys.argv:\n"
        " print('- id: node-a')\n"
        "else:\n"
        " print('NODE_A_ANSWER')\n")
    node_a.chmod(0o755)
    monkeypatch.setenv("HG_AGENT_DSH_COMMAND", shlex.join([str(node_a), str(cli), "--fixture-option"]))
    # Both other selection mechanisms point at B; neither may replace command A.
    monkeypatch.setenv("HG_AGENT_NODE", str(node_b))
    monkeypatch.setenv("HG_AGENT_DSH", str(tmp_path / "unused-install/apps/cli/lib/bin.js"))
    monkeypatch.setenv("HOME", str(tmp_path / "node-a-home"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "node-a-temp"))
    work = tmp_path / "node-a-task"
    work.mkdir()
    assert agent.run({"goal": "Use the selected interpreter."}, work) == 0
    assert (work / "answer.txt").read_text().strip() == "NODE_A_ANSWER"
    trace = [json.loads(line) for line in (work / "trace.jsonl").read_text().splitlines()]
    facts = next(row["harness_facts"] for row in trace if "harness_facts" in row)
    assert facts["runtime_node"] == str(node_a)
    assert facts["row_ids"] == ["node-a"]
    profile = next(row["profile"] for row in trace if "profile" in row)
    assert profile["node_fixture"] == "A"
    assert profile["runtime_root"] == str(root)


def test_opaque_wrapper_with_explicit_root_keeps_farm_but_does_not_guess_node(agent, monkeypatch, tmp_path, installation):
    harness, root, _, node_b = installation(tmp_path)
    configure(harness, root, str(node_b))
    _use_copy(agent, monkeypatch, harness)
    wrapper = tmp_path / "opaque.py"
    wrapper.write_text("print('OPAQUE_ANSWER')\n")
    monkeypatch.setenv("HG_AGENT_DSH_COMMAND", shlex.join([sys.executable, str(wrapper)]))
    monkeypatch.setenv("HG_AGENT_DSH_ROOT", str(root))
    monkeypatch.setenv("HG_AGENT_NODE", str(node_b))
    monkeypatch.setenv("HOME", str(tmp_path / "opaque-home"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "opaque-temp"))
    work = tmp_path / "opaque-task"
    work.mkdir()
    assert agent.run({"goal": "Use an opaque wrapper."}, work) == 0
    assert (work / "answer.txt").read_text().strip() == "OPAQUE_ANSWER"
    trace = [json.loads(line) for line in (work / "trace.jsonl").read_text().splitlines()]
    facts = next(row["harness_facts"] for row in trace if "harness_facts" in row)
    assert facts["runtime_node"] is None
    profile = next(row["profile"] for row in trace if "profile" in row)
    assert profile["links_created"] == 2
    assert profile["probe_skipped"] == "custom command owns its runtime"


def test_missing_target_refuses_before_profile_or_manifest_changes(agent, monkeypatch, tmp_path, installation):
    harness, root, _, node = installation(tmp_path)
    _use_copy(agent, monkeypatch, harness)
    (root / "apps/cli/node_modules/one").rmdir()
    old_manifest = (harness / "harness.json").read_bytes()
    home = tmp_path / "no-partial-home"
    with pytest.raises(ValueError, match="dependency is missing"):
        agent._install_profile(home, root)
    assert not home.exists()
    with pytest.raises(ValueError, match="dependency is missing"):
        configure(harness, root, str(node))
    assert (harness / "harness.json").read_bytes() == old_manifest
    assert not list(harness.glob(".dsh-manifest-*"))


@pytest.mark.parametrize("row", [
    "../escape\tapps/cli/node_modules/one", "@scope/../../escape\tapps/cli/node_modules/one",
    "/absolute\tapps/cli/node_modules/one", "one\t/etc", "one\t../outside",
    "one\tapps//cli/node_modules/one", "one\tapps/cli/node_modules/one\textra",
    "one\\bad\tapps/cli/node_modules/one",
])
def test_unsafe_farm_rows_are_rejected_before_writing(agent, monkeypatch, tmp_path, row):
    harness, root, _, _ = _installation(tmp_path)
    _use_copy(agent, monkeypatch, harness)
    (harness / "dsh_farm.tsv").write_text(row + "\n")
    home = tmp_path / "no-partial-home"
    with pytest.raises(ValueError, match="(unsafe|invalid) DSH farm row"):
        agent._install_profile(home, root)
    assert not home.exists()


def test_dependency_symlink_outside_root_is_rejected(agent, monkeypatch, tmp_path):
    harness, root, _, _ = _installation(tmp_path)
    _use_copy(agent, monkeypatch, harness)
    target = root / "apps/cli/node_modules/one"
    target.rmdir()
    outside = tmp_path / "external-package"
    outside.mkdir()
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes its installation"):
        agent._install_profile(tmp_path / "home", root)


def test_dependency_symlink_loop_is_an_actionable_validation_error(agent, monkeypatch, tmp_path):
    harness, root, _, _ = _installation(tmp_path)
    _use_copy(agent, monkeypatch, harness)
    target = root / "apps/cli/node_modules/one"
    target.rmdir()
    target.symlink_to(target)
    home = tmp_path / "no-partial-loop-home"
    with pytest.raises(ValueError, match="DSH dependency cannot be resolved"):
        agent._install_profile(home, root)
    assert not home.exists()


def test_an_existing_farm_cannot_silently_keep_links_to_the_previous_install(agent, monkeypatch, tmp_path):
    harness, root, _, _ = _installation(tmp_path)
    _use_copy(agent, monkeypatch, harness)
    home = tmp_path / "home"
    agent._install_profile(home, root)
    other = tmp_path / "other"
    other.mkdir()
    _, new_root, _, _ = _installation(other)
    with pytest.raises(ValueError, match="stale or missing link"):
        agent._install_profile(home, new_root)


def test_mismatched_explicit_root_and_cli_are_refused(agent, tmp_path):
    _, root, cli, node = _installation(tmp_path)
    with pytest.raises(ValueError, match="different installations"):
        agent.runtime({"HG_AGENT_DSH_ROOT": str(root / "another"),
                       "HG_AGENT_DSH": str(cli), "HG_AGENT_NODE": str(node)})


def test_unprepared_harness_explains_required_configuration(agent):
    with pytest.raises(ValueError, match="configure_dsh_harness.py"):
        agent.runtime({"PATH": ""})


def test_loader_diagnostic_records_missing_configuration_without_raising(agent, tmp_path):
    result = agent._loader_probe(tmp_path, {"PATH": ""})
    assert "DSH is not configured" in result["probe_error"]
