"""OpenClaw preparation, fixed installation identities and real native sandbox controls."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

from base_harness.openclaw import openclaw_runtime as runtime
from tools import configure_openclaw_harness as preparation
from tools import install_harness


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "base_harness/openclaw"
needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is unavailable")


@pytest.fixture(autouse=True)
def clear_overrides(monkeypatch):
    monkeypatch.delenv("HG_AGENT_OPENCLAW", raising=False)
    monkeypatch.delenv("HG_OPENCLAW_NODE", raising=False)
    monkeypatch.delenv("HG_OPENCLAW_RUNTIME_PATHS", raising=False)


def package(root, version="2026.3.13"):
    root.mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"name": "openclaw", "version": version}))
    entry = root / "openclaw.mjs"
    entry.write_text('''#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const args = process.argv.slice(2);
const metadata = JSON.parse(fs.readFileSync(new URL('./package.json', import.meta.url)));
if (args.includes('--version')) {
    for (const name of ['HG_AGENT_API_KEY', 'OPENAI_API_KEY', 'NODE_PATH', 'NODE_OPTIONS']) {
        assert.equal(process.env[name], undefined);
    }
    assert.equal(fs.readFileSync(process.env.OPENCLAW_CONFIG_PATH, 'utf8').trim(), '{}');
    console.log(metadata.version);
    process.exit(0);
}
assert.equal(args[0], 'agent');
assert(args.includes('--local') && args.includes('--json'));
const config = JSON.parse(fs.readFileSync(process.env.OPENCLAW_CONFIG_PATH));
assert.deepEqual(Object.keys(config.models.providers), ['harnessgrad']);
assert.equal(config.agents.defaults.workspace, process.cwd());
const readable = p => { try { fs.readFileSync(p); return true; } catch { return false; } };
let readonly = false;
try { fs.appendFileSync(new URL('./package.json', import.meta.url), ' '); } catch { readonly = true; }
const payload = {
    version: metadata.version,
    readable: JSON.parse(process.env.HG_AGENT_OPENCLAW_FIXTURE_PROBES || '[]').map(readable),
    readonly,
    state: process.env.OPENCLAW_STATE_DIR,
    home: process.env.HOME,
    leaked: ['NODE_PATH', 'NODE_OPTIONS', 'HG_METHOD_API_KEY', 'HG_ENV_API_KEY',
             'HG_JUDGE_API_KEY', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_HOME']
             .filter(k => process.env[k] !== undefined)
};
fs.writeFileSync('answer.txt', JSON.stringify(payload));
const usage = {input: 2, output: 1, cacheRead: 0, cacheWrite: 0};
const rows = [
    {message: {role: 'assistant', usage, content: [
        {type: 'toolCall', id: 'write', name: 'exec', arguments: {command: 'fixture write answer.txt'}}]}},
    {message: {role: 'toolResult', toolCallId: 'write', details: {exitCode: 0}, content: 'fixture wrote answer.txt'}},
    {message: {role: 'assistant', usage, content: [{type: 'text', text: 'fixture final text'}]}}
];
const session = args[args.indexOf('--session-id') + 1];
const sessions = path.join(process.env.OPENCLAW_STATE_DIR, 'agents/main/sessions');
fs.mkdirSync(sessions, {recursive: true});
fs.writeFileSync(path.join(sessions, session + '.jsonl'), rows.map(r => JSON.stringify(r)).join('\\n'));
console.log(JSON.stringify({status: 'ok'}));
''')
    entry.chmod(0o755)
    return entry


def npm_package(root, name, version="1.0.0", **fields):
    root.mkdir(parents=True, exist_ok=True)
    metadata = {"name": name, "version": version, "exports": "./index.mjs", **fields}
    (root / "package.json").write_text(json.dumps(metadata))
    (root / "index.mjs").write_text("export const value=" + json.dumps(version) + ";\n")
    return root


def declare(entry, **fields):
    path = entry.with_name("package.json")
    metadata = json.loads(path.read_text())
    metadata.update(fields)
    path.write_text(json.dumps(metadata))


def import_check(entry, code):
    text = entry.read_text()
    entry.write_text(text.replace("\n", "\n" + code + "\n", 1))


@pytest.fixture
def installed(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for the synthetic CLI")
    node = Path(node).resolve()
    version = subprocess.check_output([str(node), "--version"], env={"PATH": os.defpath}, text=True)
    try:
        runtime.node_version(version)
    except ValueError:
        pytest.skip("Node >=22.12.0 is unavailable for the synthetic CLI")
    with tempfile.TemporaryDirectory(prefix="hg-openclaw-runtime-", dir="/var/tmp") as directory:
        base = Path(directory)
        harness = tmp_path / "candidate"
        shutil.copytree(SOURCE, harness)
        entry = package(base / "packages/openclaw")
        launcher = base / "bin/openclaw"
        launcher.parent.mkdir()
        launcher.symlink_to(entry)
        alias = launcher.with_name("node")
        alias.symlink_to(node)
        yield harness, launcher, node, base


def prepare(installed, **kwargs):
    harness, launcher, node, _ = installed
    return preparation.configure(harness, str(launcher), node=str(node), env={"PATH": os.defpath}, **kwargs)


class NoMeasurementCache(dict):
    def get(self, *args, **kwargs):
        pytest.fail("OpenClaw read the measurement cache without complete dependency contents")
    def __setitem__(self, *args, **kwargs):
        pytest.fail("OpenClaw populated the measurement cache without complete dependency contents")


@needs_bwrap
def test_trusted_admission_blocks_changed_launcher_before_cached_scoring(installed, monkeypatch):
    from eval.run import runner
    from eval.containers import openclaw_runtime as platform_runtime
    harness, _, _, _ = installed
    prepared = prepare(installed)
    entry = Path(prepared["entrypoint"])
    calls = []
    def execute(repo, task, *args, **kwargs):
        changed = "changed-runtime" in entry.read_text()
        calls.append(changed)
        return {"task_id": task["task_id"], "answer": str(changed), "trace": "", "exit_code": 0,
                "stderr": "", "verdict": {"kind": "command", "passed": not changed},
                "harness_runtime": {"source": "platform", "python": None}}
    monkeypatch.setattr(runner, "_run_one", execute)
    monkeypatch.setattr(runner, "snapshot", lambda root: {})
    tasks = [{"task_id": "fixture", "prompt": "synthetic cached-runtime control"}]
    cache = NoMeasurementCache({"old score": {"score": 1.0}})
    def evaluate():
        return runner._evaluate(harness, tasks, {}, cache=cache, harness_sha="unchanged-harness")
    first = evaluate()
    assert first["per_task"] == {"fixture": 1.0} and not first["cached_tasks"]
    assert first["harness_runtime"][0]["openclaw"]["cacheable"] is False
    assert evaluate()["per_task"] == {"fixture": 1.0}
    assert calls == [False, False]
    entry.write_text(entry.read_text() + "\n// changed-runtime\n")
    with pytest.raises(platform_runtime.RuntimeUnavailable, match="file changed"):
        platform_runtime.identity(harness, {})
    refused = evaluate()
    assert refused["invalid"] and not refused["per_task"] and not refused["cached_tasks"]
    assert calls == [False, False]
    direct = runner.run_one(harness, tasks[0])
    assert direct["invalid"]["stage"] == "harness runtime preparation" and calls == [False, False]
    prepare(installed)
    fresh = evaluate()
    assert fresh["per_task"] == {"fixture": 0.0} and not fresh["cached_tasks"]
    assert calls == [False, False, True]


@needs_bwrap
def test_unpinned_dependency_changes_are_always_measured_fresh(installed, monkeypatch):
    from eval.run import runner
    from eval.containers import openclaw_runtime as platform_runtime
    harness, launcher, _, _ = installed
    dependency = launcher.resolve().with_name("untracked-dependency.js")
    dependency.write_text("before")
    prepare(installed)
    initial = platform_runtime.identity(harness, {})
    calls = []
    def execute(repo, task, *args, **kwargs):
        value = dependency.read_text()
        calls.append(value)
        return {"task_id": task["task_id"], "answer": value, "trace": "", "exit_code": 0,
                "stderr": "", "verdict": {"kind": "command", "passed": value == "before"},
                "harness_runtime": {"source": "platform", "python": None}}
    monkeypatch.setattr(runner, "_run_one", execute)
    monkeypatch.setattr(runner, "snapshot", lambda root: {})
    cache = NoMeasurementCache({"old score": {"score": 1.0}})
    tasks = [{"task_id": "fixture", "prompt": "untracked dependency control"}]
    first = runner._evaluate(harness, tasks, {}, cache=cache, harness_sha="same-harness")
    assert first["per_task"] == {"fixture": 1.0}
    dependency.write_text("after")
    assert platform_runtime.identity(harness, {}) == initial
    second = runner._evaluate(harness, tasks, {}, cache=cache, harness_sha="same-harness")
    assert second["per_task"] == {"fixture": 0.0} and not second["cached_tasks"]
    assert calls == ["before", "after"]


@needs_bwrap
def test_platform_admission_never_imports_candidate_python_or_executes_a_cli(installed, monkeypatch):
    from eval.containers import openclaw_runtime as platform_runtime
    harness, _, _, base = installed
    prepare(installed)
    marker = base / "candidate-executed-on-host"
    for name in ("agent.py", "openclaw_runtime.py"):
        (harness / name).write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('candidate')\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("admission executed a host subprocess"))
    assert platform_runtime.identity(harness, {})["cacheable"] is False
    assert not marker.exists()


@needs_bwrap
def test_cooperative_dispatch_preserves_and_checks_the_trusted_identity(installed, monkeypatch):
    from eval.run import cooperative, runner
    harness, _, _, _ = installed
    prepared = prepare(installed)
    launches = []
    def dispatch(*args, **kwargs):
        launches.append(True)
        return {"task_id": "fixture", "harness_runtime": {"source": "workers"}}
    monkeypatch.setattr(cooperative, "run", dispatch)
    first = runner.run_one(harness, {"task_id": "fixture", "prompt": "synthetic cooperative admission"},
                           setup={"workers": {}})
    assert first["harness_runtime"]["source"] == "workers"
    assert first["harness_runtime"]["openclaw"]["cacheable"] is False
    entry = Path(prepared["entrypoint"])
    entry.write_text(entry.read_text() + "\n// changed runtime\n")
    second = runner.run_one(harness, {"task_id": "fixture", "prompt": "synthetic cooperative admission"},
                            setup={"workers": {}})
    assert second["invalid"]["stage"] == "harness runtime preparation"
    assert launches == [True]


@needs_bwrap
def test_platform_rejects_its_checkout_before_hashing_forged_bindings(installed, monkeypatch):
    from eval.containers import openclaw_runtime as platform_runtime
    harness, _, _, base = installed
    prepare(installed)
    primary = base / "synthetic-primary-checkout"
    primary.mkdir()
    (primary / "private-config").write_text("synthetic-private-content")
    monkeypatch.setattr(platform_runtime, "main_checkout", lambda root: primary)
    manifest = json.loads((harness / "harness.json").read_text())
    manifest["runtime_paths"].append(str(primary))
    record = manifest[runtime.RECORD]
    record["runtime_paths"].append(str(primary))
    record["binding_identities"].append({"path": str(primary), "realpath": str(primary), "kind": "directory"})
    (harness / "harness.json").write_text(json.dumps(manifest))
    open_file, walk = Path.open, os.walk
    def guard_open(path, *args, **kwargs):
        if path.resolve().is_relative_to(primary):
            pytest.fail("platform admission read its checkout")
        return open_file(path, *args, **kwargs)
    def guard_walk(path, *args, **kwargs):
        if Path(path).resolve().is_relative_to(primary):
            pytest.fail("platform admission traversed its checkout")
        return walk(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guard_open)
    monkeypatch.setattr(os, "walk", guard_walk)
    with pytest.raises(platform_runtime.RuntimeUnavailable, match="overlaps the platform"):
        platform_runtime.identity(harness, {})


@needs_bwrap
def test_platform_does_not_read_dependencies_outside_the_declared_projection(installed, monkeypatch):
    from eval.containers import openclaw_runtime as platform_runtime
    harness, _, _, base = installed
    prepared = prepare(installed)
    entry = Path(prepared["entrypoint"])
    outside = npm_package(base / "packages/node_modules/unbound-dependency", "unbound-dependency")
    declare(entry, dependencies={"unbound-dependency": "1.0.0"})
    manifest = json.loads((harness / "harness.json").read_text())
    for row in manifest[runtime.RECORD]["files"]:
        if row["path"] == str(entry.with_name("package.json")):
            row["sha256"] = runtime.digest(entry.with_name("package.json"))
    (harness / "harness.json").write_text(json.dumps(manifest))
    open_file = Path.open
    def guard_open(path, *args, **kwargs):
        if path.resolve().is_relative_to(outside):
            pytest.fail("admission read unbound npm metadata")
        return open_file(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guard_open)
    with pytest.raises(platform_runtime.RuntimeUnavailable, match="outside its runtime bindings"):
        platform_runtime.identity(harness, {})


def agent(harness):
    spec = importlib.util.spec_from_file_location("_prepared_openclaw_agent", harness / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_portable_template_requires_preparation(tmp_path):
    harness = tmp_path / "candidate"
    shutil.copytree(SOURCE, harness)
    manifest = json.loads((harness / "harness.json").read_text())
    assert manifest["runtime_paths"] == []
    assert manifest["env_kinds"] == ["files"]
    assert runtime.RECORD not in manifest
    assert runtime.identity(harness)["version"] == "2026.3.13"
    with pytest.raises(ValueError, match="not prepared.*configure_openclaw_harness"):
        agent(harness).runtime_identity({})


@needs_bwrap
def test_path_discovery_aliases_and_atomic_record(installed):
    harness, launcher, node, base = installed
    env = {"PATH": str(launcher.parent) + os.pathsep + os.defpath,
           "HG_AGENT_API_KEY": "must-not-reach-probe", "NODE_OPTIONS": "--require=/host/unavailable.js",
           "NODE_PATH": "/host/private"}
    receipt = preparation.configure(harness, env=env)
    other = package(base / "other/openclaw")
    launcher.unlink()
    launcher.symlink_to(other)
    selected = runtime.resolve(harness, {"PATH": str(base / "missing"), "HG_AGENT_OPENCLAW": "openclaw",
                                         "HG_OPENCLAW_NODE": "node"})
    assert selected["argv"] == [str(node), str(base / "packages/openclaw/openclaw.mjs")]
    assert set(selected["runtime_paths"]) == {str(node), str(base / "packages/openclaw")}
    manifest = json.loads((harness / "harness.json").read_text())
    assert manifest["runtime_paths"] == selected["runtime_paths"]
    assert manifest[runtime.RECORD] == {k: v for k, v in receipt.items() if k != "harness"}
    assert set(runtime.identity(harness)) == {"source", "version", "upstream_commit", "editable"}


@needs_bwrap
def test_relative_choices_remain_fixed_after_cwd_changes(installed, monkeypatch):
    harness, launcher, _, base = installed
    monkeypatch.chdir(base)
    preparation.configure(harness, "bin/openclaw", node="bin/node", env={"PATH": os.defpath})
    monkeypatch.chdir(harness)
    assert runtime.resolve(harness, {"HG_AGENT_OPENCLAW": "bin/openclaw", "HG_OPENCLAW_NODE": "bin/node"})["version"] == "2026.3.13"


@needs_bwrap
def test_package_and_node_alias_directories_are_not_exposed(installed):
    harness, launcher, node, base = installed
    alias = base / "user-home/package-alias"
    alias.parent.mkdir()
    alias.symlink_to(launcher.resolve().parent, target_is_directory=True)
    node_alias = alias.parent / "node"
    node_alias.symlink_to(node)
    selected = preparation.configure(harness, str(alias / "openclaw.mjs"), node=str(node_alias), env={"PATH": os.defpath})
    assert selected["runtime_paths"] == [str(node), str(launcher.resolve().parent)]
    assert str(alias.parent) not in selected["runtime_paths"]


@needs_bwrap
def test_candidate_code_is_not_imported_during_preparation(installed):
    harness, _, _, base = installed
    marker = base / "host-code-execution"
    for name in ("agent.py", "openclaw_runtime.py"):
        (harness / name).write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('candidate executed')\n")
    prepare(installed)
    assert not marker.exists()


@needs_bwrap
@pytest.mark.parametrize("variable", ["HG_AGENT_OPENCLAW", "HG_OPENCLAW_NODE"])
def test_invalid_explicit_selection_never_falls_back(installed, variable):
    harness, launcher, _, base = installed
    before = (harness / "harness.json").read_bytes()
    with pytest.raises(ValueError, match="missing or not executable"):
        preparation.configure(harness, env={"PATH": str(launcher.parent) + os.pathsep + os.defpath,
                                           variable: str(base / "missing")})
    assert (harness / "harness.json").read_bytes() == before


@needs_bwrap
def test_explicit_arguments_override_environment(installed):
    harness, launcher, node, base = installed
    selected = preparation.configure(harness, str(launcher), node=str(node), env={"PATH": os.defpath,
        "HG_AGENT_OPENCLAW": str(base / "missing"), "HG_OPENCLAW_NODE": str(base / "missing-node")})
    assert selected["argv"] == [str(node), str(launcher.resolve())]


@needs_bwrap
def test_additional_dependencies_use_shared_settings_with_explicit_precedence(installed):
    harness, launcher, node, base = installed
    dependency = base / "nonstandard-dependency"
    dependency.mkdir()
    env = {"PATH": os.defpath, "HG_OPENCLAW_RUNTIME_PATHS": json.dumps([str(dependency)])}
    selected = preparation.configure(harness, str(launcher), node=str(node), env=env)
    assert str(dependency) in selected["runtime_paths"]
    env["HG_OPENCLAW_RUNTIME_PATHS"] = "invalid JSON overridden by the explicit argument"
    selected = preparation.configure(harness, str(launcher), node=str(node), runtime_paths=(), env=env)
    assert str(dependency) not in selected["runtime_paths"]


@pytest.mark.parametrize("value", ["not json", '"/a/path"', '[1]', '[""]'])
def test_invalid_additional_runtime_settings_are_refused(value):
    with pytest.raises(ValueError, match="HG_OPENCLAW_RUNTIME_PATHS"):
        preparation._additional_paths({"HG_OPENCLAW_RUNTIME_PATHS": value})


@needs_bwrap
@pytest.mark.parametrize("layout", ["nested", "hoisted", "linked"])
def test_declared_dependencies_survive_native_preparation(installed, layout):
    harness, _, node, base = installed
    modules = base / "npm/node_modules"
    entry = package(modules / "openclaw")
    declare(entry, dependencies={"fixture-dependency": "1.0.0"})
    if layout == "nested":
        dependency = npm_package(entry.parent / "node_modules/fixture-dependency", "fixture-dependency")
    elif layout == "hoisted":
        dependency = npm_package(modules / "fixture-dependency", "fixture-dependency")
    else:
        dependency = npm_package(base / "linked-dependency", "fixture-dependency")
        link = entry.parent / "node_modules/fixture-dependency"
        link.parent.mkdir()
        link.symlink_to(dependency, target_is_directory=True)
    import_check(entry, "import {value} from 'fixture-dependency'; if(value!=='1.0.0') throw Error('wrong dependency');")
    selected = preparation.configure(harness, str(entry), node=str(node), env={"PATH": os.defpath})
    assert str(dependency) in selected["packages"]
    assert str(dependency / "package.json") in {row["path"] for row in selected["files"]}
    assert str(modules) not in selected["runtime_paths"]
    assert runtime.resolve(harness, {})["argv"] == selected["argv"]


@needs_bwrap
def test_hoisted_and_nested_versions_are_distinct_and_all_metadata_is_pinned(installed):
    harness, _, node, base = installed
    modules = base / "npm/node_modules"
    entry = package(modules / "openclaw")
    declare(entry, dependencies={"fixture-parent": "1.0.0", "fixture-shared": "1.0.0"})
    parent = npm_package(modules / "fixture-parent", "fixture-parent", dependencies={"fixture-shared": "2.0.0"})
    shared = npm_package(modules / "fixture-shared", "fixture-shared", "1.0.0")
    nested = npm_package(parent / "node_modules/fixture-shared", "fixture-shared", "2.0.0")
    (parent / "index.mjs").write_text("import {value as child} from 'fixture-shared'; export const value='parent-'+child;\n")
    import_check(entry, "import {value as first} from 'fixture-shared'; import {value as second} from 'fixture-parent'; "
                       "if(first!=='1.0.0'||second!=='parent-2.0.0') throw Error('wrong nested version');")
    selected = preparation.configure(harness, str(entry), node=str(node), env={"PATH": os.defpath})
    assert {str(shared), str(nested)} <= set(selected["packages"])
    assert str(nested) not in selected["runtime_paths"], "parent already supplies its nested package"
    assert str(nested / "package.json") in {row["path"] for row in selected["files"]}
    (shared / "package.json").write_text((shared / "package.json").read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        runtime.resolve(harness, {})


@needs_bwrap
def test_scoped_alias_optional_peer_and_cyclic_npm_dependencies(installed):
    harness, _, node, base = installed
    modules = base / "npm/node_modules"
    entry = package(modules / "openclaw")
    declare(entry, dependencies={"@fixture/scoped": "1.0.0", "fixture-alias": "npm:real-name@1.0.0", "absent-optional": "1"},
            optionalDependencies={"absent-optional": "2"},
            peerDependencies={"absent-peer": "1", "fixture-peer": "1"})
    scoped = npm_package(modules / "@fixture/scoped", "@fixture/scoped", dependencies={"fixture-alias": "1"})
    alias = npm_package(modules / "fixture-alias", "real-name", dependencies={"@fixture/scoped": "1"})
    peer = npm_package(modules / "fixture-peer", "fixture-peer")
    import_check(entry, "import {value as first} from '@fixture/scoped'; import {value as second} from 'fixture-alias'; "
                       "if(first!=='1.0.0'||second!=='1.0.0') throw Error('wrong scoped/alias package');")
    selected = preparation.configure(harness, str(entry), node=str(node), env={"PATH": os.defpath})
    assert set(selected["packages"]) == {str(entry.parent), str(scoped), str(alias), str(peer)}
    assert str(modules) not in selected["runtime_paths"]


@pytest.mark.parametrize("name", ["../escape", "@scope/../escape", "a/b", "/absolute", ".."])
def test_npm_dependency_names_cannot_escape_lookup_directories(installed, name):
    _, launcher, node, _ = installed
    declare(launcher.resolve(), dependencies={name: "1"})
    with pytest.raises(ValueError, match="invalid npm dependency name"):
        runtime.installation({"PATH": os.defpath}, openclaw=str(launcher), node=str(node))


@pytest.mark.parametrize("broken_nearest", [False, True])
def test_missing_or_broken_required_dependency_never_falls_back(installed, broken_nearest):
    _, launcher, node, base = installed
    entry = launcher.resolve()
    declare(entry, dependencies={"fixture-required": "1"})
    if broken_nearest:
        (entry.parent / "node_modules/fixture-required").mkdir(parents=True)
        npm_package(entry.parent.parent / "node_modules/fixture-required", "fixture-required")
    with pytest.raises(ValueError, match="missing installed npm dependency|own package.json"):
        runtime.installation({"PATH": os.defpath}, openclaw=str(launcher), node=str(node))


@needs_bwrap
def test_hoisted_package_metadata_pin_is_required(installed):
    harness, launcher, _, base = installed
    declare(launcher.resolve(), dependencies={"fixture-hoisted": "1"})
    dependency = npm_package(base / "packages/node_modules/fixture-hoisted", "fixture-hoisted")
    prepare(installed)
    manifest = json.loads((harness / "harness.json").read_text())
    manifest[runtime.RECORD]["files"] = [row for row in manifest[runtime.RECORD]["files"]
                                         if row["path"] != str(dependency / "package.json")]
    (harness / "harness.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="invalid prepared"):
        runtime.resolve(harness, {})


@needs_bwrap
def test_external_npm_alias_lookup_requires_an_explicit_link_directory(installed):
    harness, launcher, _, base = installed
    declare(launcher.resolve(), dependencies={"fixture-linked": "1"})
    dependency = npm_package(base / "linked-package", "fixture-linked")
    modules = base / "packages/node_modules"
    modules.mkdir()
    (modules / "fixture-linked").symlink_to(dependency, target_is_directory=True)
    import_check(launcher.resolve(), "import {value} from 'fixture-linked'; if(value!=='1.0.0') throw Error('wrong linked package');")
    before = (harness / "harness.json").read_bytes()
    with pytest.raises(ValueError, match="npm dependency lookup needs an explicit --runtime"):
        prepare(installed)
    assert (harness / "harness.json").read_bytes() == before
    selected = prepare(installed, runtime_paths=(modules,))
    assert str(dependency) in selected["packages"]


def test_missing_dist_error_explains_possible_missing_dependency(installed):
    harness, launcher, _, _ = installed
    launcher.resolve().write_text("#!/usr/bin/env node\nthrow Error('openclaw: missing dist/entry.(m)js (build output).');\n")
    before = (harness / "harness.json").read_bytes()
    with pytest.raises(ValueError, match="missing dependency"):
        prepare(installed)
    assert (harness / "harness.json").read_bytes() == before


def test_preparation_environment_layers_main_worktree_and_shell(installed, monkeypatch):
    _, _, _, base = installed
    primary = base / "main"
    monkeypatch.setattr(preparation, "main_checkout", lambda _: primary)
    def settings(path):
        if path == primary / ".env":
            return {"HG_AGENT_OPENCLAW": "main-cli", "HG_OPENCLAW_NODE": "main-node"}
        assert path == preparation.ROOT / ".env"
        return {"HG_AGENT_OPENCLAW": "worktree-cli", "HG_OPENCLAW_NODE": "worktree-node"}
    monkeypatch.setattr(preparation, "read_env_file", settings)
    monkeypatch.setenv("HG_OPENCLAW_NODE", "shell-node")
    selected = preparation._environment()
    assert selected["HG_AGENT_OPENCLAW"] == "worktree-cli"
    assert selected["HG_OPENCLAW_NODE"] == "shell-node"


@needs_bwrap
@pytest.mark.parametrize("name", ["node", "entry", "metadata", "bindings", "missing-file-pin", "missing-package-bind", "source"])
def test_changed_or_incomplete_runtime_is_refused(installed, name):
    harness, launcher, node, base = installed
    if name == "node":
        copied = base / "dedicated-node/bin/node"
        copied.parent.mkdir(parents=True)
        shutil.copy2(node, copied)
        node = copied
    preparation.configure(harness, str(launcher), node=str(node), env={"PATH": os.defpath})
    manifest = json.loads((harness / "harness.json").read_text())
    if name in ("node", "entry", "metadata"):
        path = {"node": node, "entry": launcher.resolve(), "metadata": launcher.resolve().with_name("package.json")}[name]
        with path.open("ab") as stream:
            stream.write(b"\n ")
    elif name == "bindings":
        manifest["runtime_paths"] = []
    elif name == "missing-file-pin":
        manifest[runtime.RECORD]["files"] = manifest[runtime.RECORD]["files"][:-1]
    elif name == "missing-package-bind":
        record = manifest[runtime.RECORD]
        record["runtime_paths"].remove(record["package_root"])
        record["binding_identities"] = [row for row in record["binding_identities"] if row["path"] != record["package_root"]]
        manifest["runtime_paths"] = record["runtime_paths"]
    else:
        identity = json.loads((harness / "runtime-identity.json").read_text())
        identity["upstream_commit"] = "changed"
        (harness / "runtime-identity.json").write_text(json.dumps(identity))
    (harness / "harness.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="changed|bindings differ|invalid prepared"):
        runtime.resolve(harness, {})


@needs_bwrap
def test_override_cannot_switch_installations(installed):
    harness, _, node, base = installed
    prepare(installed)
    other = package(base / "other/openclaw")
    with pytest.raises(ValueError, match="differs from the prepared"):
        runtime.resolve(harness, {"HG_AGENT_OPENCLAW": str(other)})
    assert runtime.resolve(harness, {"HG_OPENCLAW_NODE": str(node)})["node"] == str(node)


@needs_bwrap
def test_explicit_unverified_version_binds_with_a_warning(installed, monkeypatch, capsys):
    harness, launcher, node, _ = installed
    version = "2026.11.3"
    declare(launcher.resolve(), version=version)
    probe = preparation._probe

    def sandbox_only(selected, *, native):
        assert native
        return probe(selected, native=native)

    monkeypatch.setattr(preparation, "_probe", sandbox_only)
    record = preparation.configure(harness, str(launcher), node=str(node),
                                   env={"PATH": os.defpath}, version=version)
    assert f"Warning: OpenClaw {version} is unverified" in capsys.readouterr().err
    assert record["source_identity"]["verified"] is False
    assert record["source_identity"]["upstream_commit"] is None
    assert record["source_identity"]["adapter_profile"] == "entries"
    assert runtime.resolve(harness, {})["version"] == version


@needs_bwrap
def test_install_and_reuse_explicit_unverified_version(installed, monkeypatch, capsys):
    _, _, node, root = installed
    version = "2026.11.3"
    integrity = preparation.RELEASES["2026.11.2"].integrity
    monkeypatch.setattr(install_harness, "_toolchain", lambda _root: node.parent.parent)

    def install_package(instance, _node_root, requested, release):
        assert requested == version and release is None
        project = instance / "package"
        package(project / "node_modules/openclaw", version)
        lockfile = project / "package-lock.json"
        lockfile.write_text(json.dumps({"packages": {"node_modules/openclaw": {
            "version": version, "integrity": integrity,
            "resolved": f"https://registry.npmjs.org/openclaw/-/openclaw-{version}.tgz",
        }}}))
        return lockfile

    monkeypatch.setattr(install_harness, "_install_package", install_package)
    args = ["install", "openclaw", "--version", version, "--install-root", str(root / "store")]
    assert install_harness.main(args) == 0
    first = capsys.readouterr()
    assert first.err.count(f"Warning: OpenClaw {version} is unverified") == 1
    assert runtime.resolve(Path(first.out.strip()), {})["source_identity"]["verified"] is False
    assert install_harness.main(args) == 0
    reused = capsys.readouterr()
    assert reused.out == first.out
    assert reused.err == first.err


@needs_bwrap
@pytest.mark.parametrize("failure", ["package-version", "cli-version", "old-node", "sandbox-failure", "probe-mutation"])
def test_failed_admission_leaves_manifest_unchanged(installed, monkeypatch, failure):
    harness, launcher, _, _ = installed
    before = (harness / "harness.json").read_bytes()
    if failure == "package-version":
        launcher.resolve().with_name("package.json").write_text('{"name":"openclaw","version":"2026.3.14"}')
    else:
        probe = preparation._probe
        def fail(selected, *, native):
            if failure == "cli-version":
                return "v22.22.0", "2026.3.14"
            if failure == "old-node":
                return "v20.12.0", "2026.3.13"
            if native and failure == "sandbox-failure":
                raise ValueError("synthetic sandbox-failure")
            result = probe(selected, native=native)
            if native and failure == "probe-mutation":
                with launcher.resolve().open("a") as stream:
                    stream.write("\n// changed during admission\n")
            return result
        monkeypatch.setattr(preparation, "_probe", fail)
    with pytest.raises(ValueError, match="version mismatch|Node >=|sandbox-failure|changed"):
        prepare(installed)
    assert (harness / "harness.json").read_bytes() == before
    assert not list(harness.glob(".openclaw-manifest-*"))


@pytest.mark.parametrize("path", ["/", "/usr", "/usr/local", "/usr/local/bin", "/tmp", "/var/tmp"])
def test_shared_runtime_roots_are_refused(path):
    with pytest.raises(ValueError, match="dedicated|hidden /tmp|shared temporary"):
        runtime.validate_binding(Path(path))


def test_home_platform_and_shared_temporary_aliases_are_refused(installed, monkeypatch):
    harness, _, _, base = installed
    with pytest.raises(ValueError, match="dedicated"):
        runtime.validate_binding(Path.home())
    alias = base / "platform-alias"
    alias.symlink_to(harness, target_is_directory=True)
    with pytest.raises(ValueError, match="overlaps|hidden /tmp"):
        runtime.validate_binding(alias, forbidden=(harness,))
    with pytest.raises(ValueError, match="overlaps"):
        runtime.validate_binding(base, forbidden=(base / "platform",))
    monkeypatch.setattr(tempfile, "tempdir", str(base))
    with pytest.raises(ValueError, match="shared temporary"):
        runtime.validate_binding(base)


@pytest.mark.parametrize("leaf", ["openclaw.mjs", "package.json"])
def test_package_leaf_symlinks_cannot_escape(installed, leaf):
    harness, launcher, node, base = installed
    original = launcher.resolve().parent / leaf
    outside = base / ("escaped-" + leaf)
    original.rename(outside)
    original.symlink_to(outside)
    with pytest.raises(ValueError, match="belong|selected package"):
        preparation.configure(harness, str(launcher), node=str(node), env={"PATH": os.defpath})


@needs_bwrap
def test_external_linked_dependency_requires_explicit_binding(installed):
    _, launcher, _, base = installed
    dependency = base / "linked-dependency"
    dependency.mkdir()
    (dependency / "index.mjs").write_text("export const fixture = 1;\n")
    link = launcher.resolve().parent / "node_modules/fixture"
    link.parent.mkdir()
    link.symlink_to(dependency, target_is_directory=True)
    with pytest.raises(ValueError, match="explicit --runtime"):
        prepare(installed)
    selected = prepare(installed, runtime_paths=(dependency,))
    assert str(dependency) in selected["runtime_paths"]


@needs_bwrap
@pytest.mark.parametrize("change", ["package-symlink", "missing-link-pin", "missing-extra-pin"])
def test_linked_dependencies_cannot_be_retargeted_or_lose_file_pins(installed, change):
    harness, launcher, _, base = installed
    first, second = base / "first-dependency", base / "second-dependency"
    first.mkdir()
    second.mkdir()
    (first / "index.mjs").write_text("export const fixture=1;")
    (second / "index.mjs").write_text("export const fixture=2;")
    alias = base / "dependency-alias"
    alias.symlink_to(first, target_is_directory=True)
    link = launcher.resolve().parent / "linked-dependency"
    link.symlink_to(first, target_is_directory=True)
    extra = base / "payload.js"
    extra.write_text("original dependency")
    prepare(installed, runtime_paths=(alias, second, extra))
    if change in ("package-symlink", "missing-link-pin"):
        link.unlink()
        link.symlink_to(second, target_is_directory=True)
        if change == "missing-link-pin":
            manifest = json.loads((harness / "harness.json").read_text())
            manifest[runtime.RECORD]["symlinks"] = []
            (harness / "harness.json").write_text(json.dumps(manifest))
    else:
        manifest = json.loads((harness / "harness.json").read_text())
        manifest[runtime.RECORD]["files"] = [row for row in manifest[runtime.RECORD]["files"] if row["path"] != str(extra)]
        (harness / "harness.json").write_text(json.dumps(manifest))
        extra.write_text("changed dependency")
    with pytest.raises(ValueError, match="changed|invalid prepared"):
        runtime.resolve(harness, {})


@needs_bwrap
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_additional_runtime_aliases_select_physical_dependencies(installed, tmp_path, kind):
    from eval.containers import sandbox

    harness, _, _, base = installed
    original, other = base / "original", base / "other"
    if kind == "file":
        original.write_text("original dependency")
        other.write_text("other dependency")
    else:
        original.mkdir()
        other.mkdir()
    alias = base / "dependency-alias"
    alias.symlink_to(original, target_is_directory=(kind == "directory"))
    selected = prepare(installed, runtime_paths=(alias,))
    assert str(original) in selected["runtime_paths"] and str(alias) not in selected["runtime_paths"]
    alias.unlink()
    alias.symlink_to(other, target_is_directory=(kind == "directory"))
    runtime.resolve(harness, {})
    work, task = tmp_path / "work", tmp_path / "task"
    work.mkdir()
    task.mkdir()
    code = "import runpy,sys; from pathlib import Path; runpy.run_path(sys.argv[1])['runtime_identity']({})"
    argv = sandbox.command([str(Path(sys.executable).resolve()), "-c", code, str(harness / "agent.py")],
                           platform=ROOT, work_root=work, task_dir=task, repo=harness,
                           env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, network="none",
                           runtime_paths=tuple(Path(p) for p in selected["runtime_paths"]))
    ran = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0, ran.stderr


@needs_bwrap
def test_unprojected_external_alias_chain_is_refused_before_commit(installed):
    harness, launcher, _, base = installed
    dependency = base / "physical-dependency"
    dependency.mkdir()
    alias = base / "external-alias"
    alias.symlink_to(dependency, target_is_directory=True)
    (launcher.resolve().parent / "linked-dependency").symlink_to(alias, target_is_directory=True)
    before = (harness / "harness.json").read_bytes()
    with pytest.raises(ValueError, match="projection failed.*physical dependency"):
        prepare(installed, runtime_paths=(dependency,))
    assert (harness / "harness.json").read_bytes() == before


def test_additional_dependency_links_require_their_own_targets(installed):
    harness, launcher, node, base = installed
    dependency = base / "dependency"
    dependency.mkdir()
    nested = base / "nested-dependency"
    nested.mkdir()
    (dependency / "nested").symlink_to(nested, target_is_directory=True)
    with pytest.raises(ValueError, match="explicit --runtime"):
        runtime.installation({"PATH": os.defpath}, openclaw=str(launcher), node=str(node), extra=(dependency,))


def test_parent_traversal_in_extra_paths_obeys_symlink_semantics(installed):
    _, launcher, node, base = installed
    logical, physical = base / "logical", base / "physical"
    logical.mkdir()
    (physical / "inner").mkdir(parents=True)
    (logical / "link").symlink_to(physical / "inner", target_is_directory=True)
    (physical / "payload.js").write_text("selected dependency")
    (logical / "payload.js").write_text("wrong dependency")
    selected = runtime.installation({"PATH": os.defpath}, openclaw=str(launcher), node=str(node),
                                    extra=(logical / "link/../payload.js",))
    assert str(physical / "payload.js") in selected["runtime_paths"]
    assert str(logical / "payload.js") not in selected["runtime_paths"]


@needs_bwrap
@pytest.mark.parametrize("name", ["node", "entry", "metadata"])
def test_symlink_loops_produce_startup_error_trace(installed, tmp_path, monkeypatch, name):
    harness, launcher, node, base = installed
    if name == "node":
        copied = base / "dedicated-node/bin/node"
        copied.parent.mkdir(parents=True)
        shutil.copy2(node, copied)
        node = copied
    preparation.configure(harness, str(launcher), node=str(node), env={"PATH": os.defpath})
    path = {"node": node, "entry": launcher.resolve(), "metadata": launcher.resolve().with_name("package.json")}[name]
    path.unlink()
    path.symlink_to(path.name)
    monkeypatch.setenv("HG_AGENT_BACKEND", "openai")
    with pytest.raises(ValueError, match="configure_openclaw_harness"):
        runtime.resolve(harness, {})
    task = tmp_path / "failed-task"
    assert agent(harness).run({"task_id": "loop", "goal": "Refuse a changed installation"}, task) == 1
    rows = [json.loads(line) for line in (task / "trace.jsonl").read_text().splitlines()]
    assert rows[0]["harness_identity"]["harness"] == "openclaw"
    assert "configure_openclaw_harness" in next(row["error"] for row in rows if "error" in row)
    assert rows[-1]["usage"]["source"] == "adapter_failure"


@pytest.mark.parametrize("character", [",", "\n", "\r"])
@pytest.mark.parametrize("alias", [False, True])
def test_unrepresentable_mount_paths_are_refused(installed, character, alias):
    _, _, _, base = installed
    bad = base / ("dependency" + character + "name")
    bad.mkdir()
    path = base / "alias" if alias else bad
    if alias:
        path.symlink_to(bad, target_is_directory=True)
    with pytest.raises(ValueError, match="Docker bind mount"):
        runtime.validate_binding(path)


@needs_bwrap
@pytest.mark.parametrize("files_submission", [False, True])
def test_native_task_uses_fixed_runtime_private_state_and_readonly_bindings(installed, tmp_path, files_submission):
    from eval.containers import sandbox

    harness, launcher, node, base = installed
    declare(launcher.resolve(), dependencies={"fixture-hoisted": "1"})
    modules = base / "packages/node_modules"
    npm_package(modules / "fixture-hoisted", "fixture-hoisted")
    import_check(launcher.resolve(), "import {value} from 'fixture-hoisted'; if(value!=='1.0.0') throw Error('wrong dependency');")
    unrelated = modules / "unrelated-package/private.txt"
    unrelated.parent.mkdir()
    unrelated.write_text("synthetic unrelated package secret")
    if files_submission:
        copied = base / "dedicated-node/bin/node"
        copied.parent.mkdir(parents=True)
        shutil.copy2(node, copied)
        node = copied
    selected = preparation.configure(harness, str(launcher), node=str(node), env={"PATH": os.defpath})
    credential = base / "user-home/.openclaw/auth.json"
    credential.parent.mkdir(parents=True)
    credential.write_text("synthetic host credential")
    sibling = launcher.parent / "private-sibling.txt"
    sibling.write_text("synthetic sibling secret")
    node_sibling = base / "dedicated-node/bin/private-node-sibling.txt"
    node_sibling.parent.mkdir(parents=True, exist_ok=True)
    node_sibling.write_text("synthetic Node sibling secret")
    other_task = tmp_path / "other-task/answer.txt"
    other_task.parent.mkdir()
    other_task.write_text("synthetic other-task answer")
    platform = tmp_path / "platform"
    platform.mkdir()
    scorer = platform / "scorer.py"
    scorer.write_text("synthetic scorer")
    work, task = tmp_path / "work", tmp_path / "task"
    work.mkdir()
    task.mkdir()
    spec = {"task_id": "fixture", "goal": "Use the synthetic CLI"}
    if files_submission:
        spec["submission"] = {"kind": "files", "paths": ["answer.txt"]}
    (task / "task.json").write_text(json.dumps(spec))
    env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "HG_AGENT_BACKEND": "openai",
           "HG_AGENT_MODEL": "fixture", "HG_AGENT_BASE_URL": "http://127.0.0.1:9/v1",
           "HG_AGENT_API_KEY": "synthetic-task-key", "HG_METHOD_API_KEY": "synthetic-method-key",
           "HG_ENV_API_KEY": "synthetic-env-key", "HG_JUDGE_API_KEY": "synthetic-judge-key",
           "NODE_PATH": "/host/private-node", "NODE_OPTIONS": "--require=/host/absent.js",
           "OPENAI_API_KEY": "synthetic-daily-key", "OPENAI_BASE_URL": "daily-provider", "CODEX_HOME": "/host/codex",
           "HG_AGENT_OPENCLAW_FIXTURE_PROBES": json.dumps([str(credential), str(sibling), str(node_sibling), str(unrelated), str(other_task), str(scorer)])}
    command = sandbox.command([str(Path(sys.executable).resolve()), str(harness / "agent.py"),
        "--task", str(task / "task.json"), "--workdir", str(task)],
        platform=platform, work_root=work, repo=harness, task_dir=task, env=env,
        network="none", runtime_paths=tuple(Path(p) for p in selected["runtime_paths"]))
    ran = subprocess.run(command, cwd=task, capture_output=True, text=True, timeout=30)
    assert ran.returncode == 0, ran.stderr
    answer = json.loads((task / "answer.txt").read_text())
    assert answer["readable"] == [False] * 6
    assert str(modules) not in selected["runtime_paths"]
    assert answer["readonly"] is True
    assert answer["leaked"] == []
    assert not Path(answer["state"]).exists() and not Path(answer["home"]).exists()
    trace = [json.loads(line) for line in (task / "trace.jsonl").read_text().splitlines()]
    assert trace[0]["harness_identity"]["config"]["runtime"]["argv"] == selected["argv"]
    assert trace[-1]["answer_source"] == ("files" if files_submission else "artifact")
    assert trace[-2]["usage"]["calls"] == 2
    assert next(row for row in trace if "command" in row)["observation"]["returncode"] == 0
    assert "synthetic-task-key" not in (task / "trace.jsonl").read_text()


def test_configure_version_probe_rejects_non_utf8_output(tmp_path):
    node = tmp_path / 'node'
    node.write_text(f"#!{sys.executable}\nimport os\nos.write(1, b'\\xff')\n", encoding='utf-8')
    node.chmod(0o755)
    with pytest.raises(UnicodeDecodeError):
        preparation._probe({'node': str(node), 'argv': [str(node)]}, native=False)
