"""Portable external OpenCode selections share one identity with formal Bun execution."""
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

from base_harness.opencode import opencode_runtime as runtime
from eval.containers import bun_runtime, sandbox
from tools.configure_opencode_harness import configure

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "base_harness/opencode"
BUN = os.environ.get("HG_TEST_BUN") or os.environ.get("HG_AGENT_OPENCODE_BUN") or shutil.which("bun")
needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is unavailable")


@pytest.fixture(autouse=True)
def clear_overrides(monkeypatch):
    for name in ("HG_AGENT_OPENCODE_SOURCE", "HG_AGENT_OPENCODE_BUN", "HG_BUN_BINARY", "HG_HARNESS_SOURCE", "HG_HARNESS_BUN"):
        monkeypatch.delenv(name, raising=False)


def source(root, marker):
    entry = root / "packages/opencode/src/index.ts"
    entry.parent.mkdir(parents=True)
    (root / "package.json").write_text('{"name":"opencode"}')
    (root / "bun.lock").write_text("synthetic installed lock")
    entry.parent.parent.joinpath("package.json").write_text('{"name":"opencode","version":"1.0.1"}')
    dependency = root / "node_modules/fixture-dependency"
    dependency.mkdir(parents=True)
    (dependency / "package.json").write_text('{"name":"fixture-dependency","version":"1"}')
    entry.write_text('''import fs from 'node:fs';
const args = process.argv.slice(2);
const version = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version;
const readable = p => {try {fs.readFileSync(p); return true;} catch {return false;}};
if(args.includes('--version')) {
    if (process.env.HG_AGENT_API_KEY || process.env.BUN_OPTIONS || process.env.NODE_OPTIONS) throw Error('probe inherited host settings');
    if(readable(MARKER)) { fs.writeFileSync(MARKER + '.executed', 'host execution'); throw Error('source ran on host'); }
    console.log(version);
    process.exit(0);
}
const tokens = {input: 2, output: 1, reasoning: 0, cache: {read: 0, write: 0}};
if(args.includes('export')) {
    console.log(JSON.stringify({info:{id:'s1',outcome:'succeeded'},messages:[{id:'m1',type:'assistant',finish:'stop',tokens,
        content:[{type:'text',text:'fixture final'}]}]}));
    process.exit(0);
}
await Bun.stdin.text();
let readonly = false;
try {fs.appendFileSync(new URL('../package.json', import.meta.url), ' ');} catch {readonly = true;}
fs.writeFileSync('answer.txt', JSON.stringify({version,readonly,
    readable:JSON.parse(process.env.HG_AGENT_OPENCODE_FIXTURE_PROBES || '[]').map(readable),
    leaked:['HG_METHOD_API_KEY','HG_ENV_API_KEY','HG_JUDGE_API_KEY','OPENAI_API_KEY','NODE_PATH'].filter(k=>process.env[k]!==undefined)}));
console.log(JSON.stringify({type:'step_finish',sessionID:'s1',part:{id:'f1',messageID:'m1',reason:'stop',tokens}}));
'''.replace('MARKER', json.dumps(str(marker))))
    return root


@pytest.fixture
def installed(tmp_path):
    if not BUN or not Path(BUN).is_file():
        pytest.skip("set HG_TEST_BUN or HG_AGENT_OPENCODE_BUN to an installed Bun")
    with tempfile.TemporaryDirectory(prefix="hg-opencode-installation-", dir="/var/tmp") as directory:
        base = Path(directory)
        harness = tmp_path / "harness"
        shutil.copytree(SOURCE, harness)
        marker = base / "synthetic-host-secret"
        marker.write_text("synthetic probe sentinel")
        tree = source(base / "prepared-source", marker)
        alias = base / "aliases/bun"
        alias.parent.mkdir()
        alias.symlink_to(Path(BUN).resolve())
        yield harness, tree, alias, base


def prepare(installed, **kwargs):
    harness, tree, bun, _ = installed
    return configure(harness, str(tree), bun=str(bun), env={"PATH": os.defpath}, **kwargs)


def agent(harness):
    spec = importlib.util.spec_from_file_location("_prepared_opencode_agent", harness / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unprepared_template_fails_with_a_preparation_trace(tmp_path, monkeypatch):
    harness = tmp_path / "harness"
    shutil.copytree(SOURCE, harness)
    assert json.loads((harness / "harness.json").read_text())["runtime_paths"] == []
    monkeypatch.setenv("HG_AGENT_MODEL", "fixture")
    work = tmp_path / "task"
    assert agent(harness).run({"goal": "Refuse an unprepared template"}, work) == 1
    rows = [json.loads(line) for line in (work / "trace.jsonl").read_text().splitlines()]
    assert "configure_opencode_harness.py" in next(row["error"] for row in rows if "error" in row)
    assert any("usage" in row for row in rows)


@needs_bwrap
def test_prepared_selection_is_fixed_and_platform_does_not_run_host_code(installed, monkeypatch):
    harness, tree, bun, base = installed
    receipt = prepare(installed)
    assert receipt["runtime_paths"] == [str(tree), str(Path(BUN).resolve())]
    assert not Path(str(base / "synthetic-host-secret") + ".executed").exists()
    monkeypatch.setenv("PATH", str(base / "empty"))
    monkeypatch.setattr(bun_runtime, "_run", lambda *a, **k: pytest.fail("prepared runtime invoked a host subprocess"))
    spec, selected, entry, external = bun_runtime.owned_source(harness)
    assert selected == tree and external
    admitted = bun_runtime.admit(harness, {})
    assert admitted == {"HG_HARNESS_SOURCE": str(tree), "HG_HARNESS_ENTRYPOINT": str(entry)}
    prepared = bun_runtime.prepare(harness)
    assert prepared["binary"] == str(Path(BUN).resolve())
    assert prepared["runtime_paths"] == receipt["runtime_paths"]
    assert bun_runtime.identity(harness) == prepared["identity"]
    # A candidate cannot shadow the selected installation by adding owned source.
    (harness / "opencode/packages/opencode/src").mkdir(parents=True)
    (harness / "opencode/packages/opencode/src/index.ts").write_text("throw Error('shadowed')")
    assert bun_runtime.owned_source(harness)[1] == tree


@needs_bwrap
def test_aliases_and_checkpoint_copy_retain_external_installation(installed, tmp_path):
    from eval.ckpt.source_tree import copy_harness
    harness, tree, bun, base = installed
    alias = base / "aliases/source"
    alias.symlink_to(tree, target_is_directory=True)
    configure(harness, str(alias), bun=str(bun), env={"PATH": os.defpath})
    child = tmp_path / "child"
    copy_harness(harness, child)
    assert not (child / "opencode").exists()
    alias.unlink()
    alias.symlink_to(base, target_is_directory=True)
    assert runtime.resolve(child, {})["source"] == str(tree)


@needs_bwrap
@pytest.mark.parametrize("changed", ["source", "package", "dependency", "link", "binding", "pin"])
def test_source_dependency_and_record_changes_cannot_reuse_formal_runtime(installed, changed):
    harness, tree, _, base = installed
    prepare(installed)
    if changed == "source":
        (tree / "packages/opencode/src/index.ts").write_text("changed source")
    elif changed == "package":
        (tree / "packages/opencode/package.json").write_text('{"name":"opencode","version":"1.0.2"}')
    elif changed == "dependency":
        (tree / "node_modules/fixture-dependency/package.json").write_text('{"name":"fixture-dependency","version":"2"}')
    elif changed == "link":
        (tree / "new-link").symlink_to(tree / "bun.lock")
    else:
        manifest = json.loads((harness / "harness.json").read_text())
        if changed == "binding":
            manifest["runtime_paths"] = []
        else:
            manifest[runtime.RECORD]["files"] = []
        (harness / "harness.json").write_text(json.dumps(manifest))
    with pytest.raises(bun_runtime.RuntimeUnavailable, match="changed|prepared|bindings"):
        bun_runtime.prepare(harness)


@needs_bwrap
def test_candidate_python_is_not_imported_during_preparation(installed):
    harness, _, _, base = installed
    marker = base / "candidate-python-on-host"
    for name in ("opencode_runtime.py", "agent.py"):
        (harness / name).write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('candidate')\n")
    prepare(installed)
    assert not marker.exists()


@pytest.mark.parametrize("path", ["/", "/tmp", "/var/tmp", "/usr", "/usr/local", "/usr/local/bin"])
def test_broad_and_hidden_runtime_roots_are_refused(path):
    with pytest.raises(ValueError, match="hidden|shared temporary|dedicated"):
        runtime.validate_binding(Path(path))


def test_physical_home_alias_is_not_a_dedicated_installation(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="hg-opencode-home-alias-", dir="/var/tmp") as directory:
        root = Path(directory)
        home = root / "physical-home"
        home.mkdir()
        alias = root / "home-alias"
        alias.symlink_to(home, target_is_directory=True)
        monkeypatch.setenv("HOME", str(alias))
        with pytest.raises(ValueError, match="dedicated installation"):
            runtime.validate_binding(home)


@needs_bwrap
def test_invalid_override_or_unsupported_version_leaves_manifest_unchanged(installed):
    harness, tree, _, base = installed
    before = (harness / "harness.json").read_bytes()
    with pytest.raises(ValueError, match="missing or not executable"):
        configure(harness, str(tree), bun=str(base / "absent"), env={"PATH": os.defpath})
    (tree / "packages/opencode/package.json").write_text('{"name":"opencode","version":"2.0.0"}')
    with pytest.raises(ValueError, match="OpenCode v1"):
        prepare(installed)
    assert (harness / "harness.json").read_bytes() == before


@needs_bwrap
def test_preparation_refuses_platform_overlap_and_escaped_dependency_links(installed):
    harness, tree, _, base = installed
    with pytest.raises(ValueError, match="overlaps"):
        runtime.validate_binding(tree, forbidden=(tree / "platform",))
    external = base / "external-dependency"
    external.mkdir()
    (tree / "node_modules/external").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="explicit --runtime"):
        prepare(installed)
    assert prepare(installed, runtime_paths=(external,))["source"] == str(tree)


@needs_bwrap
def test_explicit_file_dependency_changes_the_platform_runtime_identity(installed):
    harness, _, _, base = installed
    addon = base / "addon.node"
    addon.write_text("first native dependency")
    prepare(installed, runtime_paths=(addon,))
    first = bun_runtime.identity(harness)
    addon.write_text("second native dependency")
    with pytest.raises(bun_runtime.RuntimeUnavailable, match="changed"):
        bun_runtime.identity(harness)
    prepare(installed, runtime_paths=(addon,))
    second = bun_runtime.identity(harness)
    assert first != second
    assert str(addon) in second["prepared_installation"]["runtime_paths"]


@pytest.mark.parametrize("location", ["node_modules/fixture/index.js", "node_modules/fixture/data.json",
    "node_modules/fixture/addon.node", "node_modules/fixture/module.wasm", "node_modules/.cache/plugin.js",
    "__pycache__/plugin.pyc", ".state/plugin.js", ".git/runtime-data", "extra"])
def test_all_runtime_content_is_fingerprinted_even_with_preserved_stat_data(tmp_path, location):
    tree, extra = tmp_path / "source", tmp_path / "external-dependency"
    tree.mkdir(); extra.mkdir()
    path = extra / "plugin.js" if location == "extra" else tree / location
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"before")
    before = runtime.inventory(tree, [tree, extra])
    stamp = path.stat()
    path.write_bytes(b"after!")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    after = runtime.inventory(tree, [tree, extra])
    assert after["runtime_contents_sha256"] != before["runtime_contents_sha256"]
    assert after["package_metadata_sha256"] == before["package_metadata_sha256"]
    assert after["source_sha256"] == before["source_sha256"]
    assert path.stat().st_size == stamp.st_size and path.stat().st_mtime_ns == stamp.st_mtime_ns


@pytest.mark.parametrize("change", ["new-file", "empty-directory", "permissions", "removed-file"])
def test_runtime_layout_and_permissions_change_the_identity(tmp_path, change):
    dependency = tmp_path / "node_modules/fixture"
    dependency.mkdir(parents=True)
    path = dependency / "index.js"
    path.write_text("runtime code")
    before = runtime.inventory(tmp_path, [tmp_path])
    if change == "new-file":
        (dependency / "new.js").write_text("added code")
    elif change == "empty-directory":
        (dependency / "new-directory").mkdir()
    elif change == "permissions":
        path.chmod(path.stat().st_mode ^ 0o100)
    else:
        path.unlink()
    assert runtime.inventory(tmp_path, [tmp_path])["runtime_contents_sha256"] != before["runtime_contents_sha256"]


@needs_bwrap
@pytest.mark.parametrize("record_change", ["old-schema", "missing-content-digest"])
def test_metadata_only_records_require_updated_preparation(installed, record_change):
    harness, _, _, _ = installed
    prepare(installed)
    manifest = json.loads((harness / "harness.json").read_text())
    if record_change == "old-schema":
        manifest[runtime.RECORD]["schema"] = 1
    else:
        manifest[runtime.RECORD]["inventory"].pop("runtime_contents_sha256")
    (harness / "harness.json").write_text(json.dumps(manifest))
    with pytest.raises(bun_runtime.RuntimeUnavailable, match="preparation|prepared"):
        bun_runtime.prepare(harness)


@needs_bwrap
@pytest.mark.parametrize("escape", ["absolute", "parent-traversal"])
def test_identity_rows_cannot_make_platform_read_unbound_host_files(installed, monkeypatch, escape):
    harness, tree, _, base = installed
    prepare(installed)
    private = base / "synthetic-private-file"
    private.write_text("synthetic-private-content")
    manifest = json.loads((harness / "harness.json").read_text())
    spelling = private if escape == "absolute" else tree / ".." / private.name
    manifest[runtime.RECORD]["files"].append({"path": str(spelling), "realpath": str(private), "sha256": "0" * 64})
    (harness / "harness.json").write_text(json.dumps(manifest))
    open_file = Path.open
    def guarded_open(path, *args, **kwargs):
        if path.resolve() == private:
            pytest.fail("the platform read an unbound identity file")
        return open_file(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(bun_runtime.RuntimeUnavailable, match="outside its bindings"):
        bun_runtime.identity(harness)


@needs_bwrap
def test_platform_rejects_forged_bindings_before_reading_main_checkout(installed, monkeypatch):
    harness, _, _, base = installed
    prepare(installed)
    primary = base / "synthetic-primary-checkout"
    primary.mkdir()
    (primary / "private-config").write_text("synthetic private configuration")
    monkeypatch.setattr(bun_runtime, "main_checkout", lambda root: primary)
    manifest = json.loads((harness / "harness.json").read_text())
    manifest["runtime_paths"].append(str(primary))
    manifest[runtime.RECORD]["runtime_paths"].append(str(primary))
    (harness / "harness.json").write_text(json.dumps(manifest))
    open_file = Path.open
    walk = os.walk
    def guarded_open(path, *args, **kwargs):
        if path.resolve().is_relative_to(primary):
            pytest.fail("the platform read its own checkout through a forged binding")
        return open_file(path, *args, **kwargs)
    def guarded_walk(path, *args, **kwargs):
        if Path(path).resolve().is_relative_to(primary):
            pytest.fail("the platform traversed its own checkout through a forged binding")
        return walk(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(os, "walk", guarded_walk)
    with pytest.raises(bun_runtime.RuntimeUnavailable, match="overlaps the platform"):
        bun_runtime.identity(harness)


@needs_bwrap
@pytest.mark.parametrize("location", ["node_modules", "extra-runtime"])
def test_dependency_code_change_refuses_old_score_then_reprepare_gets_a_fresh_score(installed, tmp_path, monkeypatch, location):
    from eval.run import runner
    harness, tree, _, base = installed
    dependency = tree / "node_modules/fixture-dependency" if location == "node_modules" else base / "external-dependency"
    dependency.mkdir(exist_ok=True)
    (dependency / "package.json").write_text('{"name":"fixture-dependency","version":"1","type":"module","main":"index.js"}')
    body = dependency / "index.js"
    body.write_text('export const answer = "before";')
    target = "fixture-dependency" if location == "node_modules" else str(body)
    entry = tree / "packages/opencode/src/index.ts"
    entry.write_text('import { answer } from ' + json.dumps(target) +
                     '; console.log(process.argv.includes("--version") ? "1.0.1" : answer);')
    extra = () if location == "node_modules" else (dependency,)
    receipt = prepare(installed, runtime_paths=extra)
    before = bun_runtime.identity(harness)
    work, task = tmp_path / "work", tmp_path / "task"
    work.mkdir(); task.mkdir()
    def execute():
        env = {"PATH": os.defpath, "HOME": str(task)}
        argv = sandbox.command([receipt["binary"], "--no-env-file", "--no-install", str(entry)],
            platform=ROOT, work_root=work, task_dir=task, repo=harness, env=env, network="none",
            runtime_paths=tuple(Path(p) for p in receipt["runtime_paths"]))
        return subprocess.check_output(argv, cwd=task, env=env, text=True, timeout=30).strip()
    calls = []
    def run(repo, item, *args, runtime_receipt, **kwargs):
        answer = execute()
        calls.append(answer)
        runtime_receipt["bun"] = bun_runtime.prepare(repo)
        return {"task_id": item["task_id"], "answer": answer, "trace": "", "exit_code": 0, "stderr": "",
                "verdict": {"kind": "command", "passed": answer == "before"},
                "harness_runtime": {"source": "platform", "python": None}}
    monkeypatch.setattr(runner, "_run_one", run)
    monkeypatch.setattr(runner, "snapshot", lambda root: {})
    cache = {}
    tasks = [{"task_id": "fixture", "prompt": "synthetic dependency/cache control"}]
    def evaluate():
        return runner._evaluate(harness, tasks, {}, cache=cache, harness_sha="same-source")
    assert evaluate()["per_task"] == {"fixture": 1.0}
    assert evaluate()["cached_tasks"] == ["fixture"]
    stamp = body.stat()
    body.write_text('export const answer = "after!";')
    os.utime(body, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert execute() == "after!"
    refused = evaluate()
    assert refused["invalid"] and refused["per_task"] == {} and refused["cached_tasks"] == []
    assert calls == ["before"]
    prepare(installed, runtime_paths=extra)
    assert bun_runtime.identity(harness) != before
    fresh = evaluate()
    assert fresh["cached_tasks"] == [] and fresh["per_task"] == {"fixture": 0.0}
    assert calls == ["before", "after!"]
    assert evaluate()["cached_tasks"] == ["fixture"]


def test_standalone_external_diagnostic_is_explicit_and_formal_admission_refuses_it(installed, tmp_path, monkeypatch):
    harness, tree, bun, _ = installed
    env = {"HG_AGENT_OPENCODE_EXTERNAL_SOURCE": "1", "HG_AGENT_OPENCODE_SOURCE": str(tree),
           "HG_AGENT_OPENCODE_BUN": str(bun), "HG_AGENT_MODEL": "fixture", "HG_AGENT_API_KEY": "synthetic",
           "HG_AGENT_BASE_URL": "http://127.0.0.1:9/v1"}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(bun_runtime.RuntimeUnavailable, match="diagnostic"):
        bun_runtime.admit(harness, env)
    task = tmp_path / "diagnostic-task"
    assert agent(harness).run({"goal": "Use the synthetic standalone diagnostic"}, task) == 0
    rows = [json.loads(line) for line in (task / "trace.jsonl").read_text().splitlines()]
    assert rows[0]["harness_identity"]["external_source_diagnostic"] is True


@needs_bwrap
def test_native_task_runs_the_selected_bun_and_keeps_private_siblings_hidden(installed, tmp_path):
    harness, tree, bun, base = installed
    receipt = prepare(installed)
    credential = base / "home/.config/opencode/auth.json"
    credential.parent.mkdir(parents=True)
    credential.write_text("synthetic credential")
    unrelated = bun.parent / "private-sibling.txt"
    unrelated.write_text("synthetic sibling")
    platform = tmp_path / "platform"
    platform.mkdir()
    scorer = platform / "scorer.py"
    scorer.write_text("synthetic scorer")
    other = tmp_path / "other-task/answer.txt"
    other.parent.mkdir()
    other.write_text("synthetic other answer")
    work, task = tmp_path / "work", tmp_path / "task"
    work.mkdir(); task.mkdir()
    (task / "task.json").write_text('{"task_id":"fixture","goal":"Use the synthetic CLI"}')
    env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "HG_AGENT_MODEL": "fixture",
           "HG_AGENT_BASE_URL": "http://127.0.0.1:9/v1", "HG_AGENT_API_KEY": "synthetic-task-key",
           "HG_METHOD_API_KEY": "synthetic-method", "HG_ENV_API_KEY": "synthetic-env", "HG_JUDGE_API_KEY": "synthetic-judge",
           "OPENAI_API_KEY": "synthetic-daily", "NODE_PATH": "/host/unavailable",
           "HG_AGENT_OPENCODE_FIXTURE_PROBES": json.dumps([str(credential), str(unrelated), str(scorer), str(other)])}
    argv = sandbox.command([str(Path(sys.executable).resolve()), str(harness / "agent.py"),
        "--task", str(task / "task.json"), "--workdir", str(task)],
        platform=platform, work_root=work, task_dir=task, repo=harness, env=env, network="none",
        runtime_paths=tuple(Path(p) for p in receipt["runtime_paths"]))
    ran = subprocess.run(argv, cwd=task, capture_output=True, text=True, timeout=30)
    assert ran.returncode == 0, ran.stderr
    answer = json.loads((task / "answer.txt").read_text())
    assert answer == {"version": "1.0.1", "readonly": True, "readable": [False] * 4, "leaked": []}
    rows = [json.loads(line) for line in (task / "trace.jsonl").read_text().splitlines()]
    assert rows[0]["harness_identity"]["source_in_candidate"] is False
    assert rows[-2]["answer_source"] == "artifact"
    assert rows[-1]["usage"]["input_tokens"] == 2
