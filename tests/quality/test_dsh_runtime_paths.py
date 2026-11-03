"""Relocated DSH installs keep launch, facts, links and sandbox bindings together."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import sys

import pytest

from tools.configure_dsh_harness import configure

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


def _installation(tmp_path, *, full_farm=False):
    harness = tmp_path / "candidate"
    shutil.copytree(SOURCE, harness)
    if not full_farm:
        (harness / "dsh_farm.tsv").write_text(
            "@example/one\tapps/cli/node_modules/one\n"
            "two\tapps/cli/node_modules/nested/node_modules/two\n")
    root = tmp_path / "another-user" / "relocated-dsh"
    cli = root / "apps/cli/lib/bin.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fixture CLI; the fake Node owns execution\n")
    for line in (harness / "dsh_farm.tsv").read_text().splitlines():
        (root / line.split("\t")[1]).mkdir(parents=True, exist_ok=True)
    node = tmp_path / "node-install" / "bin/node"
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


def test_preparation_records_paths_used_by_the_sandbox_and_all_farm_links(agent, monkeypatch, tmp_path):
    harness, root, cli, node = _installation(tmp_path, full_farm=True)
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


def test_explicit_cli_and_node_override_the_prepared_installation(agent, monkeypatch, tmp_path):
    first = tmp_path / "first"
    first.mkdir()
    harness, _, _, node = _installation(first)
    configure(harness, first / "another-user/relocated-dsh", str(node))
    second = tmp_path / "second"
    second.mkdir()
    _, root, cli, other_node = _installation(second)
    _use_copy(agent, monkeypatch, harness)
    selected = agent.runtime({"HG_AGENT_DSH": str(cli), "HG_AGENT_NODE": str(other_node)})
    assert selected["root"] == root
    assert selected["argv"] == [str(other_node), str(cli)]
    home = tmp_path / "home"
    agent._install_profile(home, selected["root"])
    assert (home / ".dsh/profiles/node_modules/@example/one").readlink().is_relative_to(root)


def test_preparation_preserves_exact_node_after_symlink_resolution_and_in_sandbox_plan(agent, monkeypatch, tmp_path):
    from eval.containers import sandbox

    harness, root, cli, node = _installation(tmp_path)
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


def test_one_runtime_drives_launch_configuration_dump_and_loader_probe(agent, monkeypatch, tmp_path):
    harness, root, cli, node = _installation(tmp_path)
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


def test_direct_node_command_uses_its_interpreter_for_launch_dump_and_loader(agent, monkeypatch, tmp_path):
    harness, root, cli, node_b = _installation(tmp_path)
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


def test_opaque_wrapper_with_explicit_root_keeps_farm_but_does_not_guess_node(agent, monkeypatch, tmp_path):
    harness, root, _, node_b = _installation(tmp_path)
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


def test_missing_target_refuses_before_profile_or_manifest_changes(agent, monkeypatch, tmp_path):
    harness, root, _, node = _installation(tmp_path)
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
