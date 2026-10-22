"""Shared runtime controls using authored files only; no dataset/model/scorer run."""
from __future__ import annotations

import errno
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from eval.containers import container, sandbox
from eval.containers.immutable_runtime import ImmutableRuntimeUnavailable, projections
from eval.observe.tracing import _materialize
from eval.run import runner
from eval.verification import verifier
from eval.verification.native_runtime import FrozenRuntime

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "sha256:" + "a" * 64  # Plan-only sentinel, never sent to Docker.


@pytest.fixture
def runtime(tmp_path):
    source = tmp_path / "shared-source"
    (source / "bin").mkdir(parents=True)
    (source / "bin/python").write_text("authored runtime marker\n")
    return source


def entry(source, path="dojo_runtime"):
    return {"path": path, "from": str(source), "immutable": True}


def digest(source):
    h = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        h.update(str(path.relative_to(source)).encode())
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


def mounts(plan):
    return [(plan[i], plan[i + 1], plan[i + 2])
            for i in range(len(plan) - 2) if plan[i] in ("--bind", "--ro-bind")]


def native_plan(work, source, **kw):
    work_root = work.parent / "work-root"
    work_root.mkdir(exist_ok=True)
    return sandbox.command(["/bin/true"], platform=ROOT, work_root=work_root,
                           task_dir=work, repo=work_root, env={},
                           immutable_runtimes=projections([entry(source)]), **kw)


@pytest.mark.parametrize("verification", [False, True])
def test_no_materialized_host_runtime_and_exact_backend_plans(tmp_path, runtime, monkeypatch, verification):
    work = tmp_path / "task"
    work.mkdir()

    def no_copy(*a, **kw):
        pytest.fail("immutable runtime was copied")

    monkeypatch.setattr(shutil, "copytree", no_copy)
    monkeypatch.setattr(shutil, "copy2", no_copy)
    assert _materialize(work, [entry(runtime)], ROOT) == []
    assert list(work.iterdir()) == []
    monkeypatch.setattr(sandbox, "_bwrap", lambda: "/synthetic/bwrap")
    plan = native_plan(work, runtime, task_dir_writable=not verification)
    events = mounts(plan)
    projection = ("--ro-bind", str(runtime), str(work / "dojo_runtime"))
    assert events.count(projection) == 1
    assert events.index(projection) > max(i for i, event in enumerate(events)
                                         if event == ("--bind", str(work), str(work)))
    if verification:
        assert plan.index("--remount-ro") > plan.index(str(work / "dojo_runtime"))
    docker = container.command(["/bin/true"], image=IMAGE, task_dir=work,
                               task_mount="/app", task_dir_writable=not verification,
                               immutable_runtimes=projections([entry(runtime)]))
    expected = f"type=bind,src={runtime},dst=/app/dojo_runtime,readonly"
    assert docker.count(expected) == 1
    assert docker.index(expected) > docker.index(
        f"type=bind,src={work},dst=/app" + (",readonly" if verification else ""))
    assert list(work.iterdir()) == []  # Building either plan creates no host target.


@pytest.mark.parametrize("place", ["setup", "verify"])
@pytest.mark.parametrize("fault", ["missing", "relative", "symlink", "file", "parent-symlink"])
@pytest.mark.parametrize("backend", ["files", "exec"])
def test_bad_source_is_invalid_before_any_sandbox(tmp_path, runtime, monkeypatch, place, fault, backend):
    if fault == "missing":
        source = tmp_path / "missing"
    elif fault == "relative":
        source = Path("shared-source")
    elif fault == "symlink":
        source = tmp_path / "link"
        source.symlink_to(runtime, target_is_directory=True)
    elif fault == "parent-symlink":
        parent = tmp_path / "linked-parent"
        parent.symlink_to(tmp_path, target_is_directory=True)
        source = parent / runtime.name
    else:
        source = runtime / "bin/python"

    def forbidden(*a, **kw):
        pytest.fail("invalid source reached a backend")

    monkeypatch.setattr(sandbox, "run", forbidden)
    monkeypatch.setattr(container, "run", forbidden)
    # Call the production core directly, without bypassing a policy gate in run_one.
    result = runner._run_one(tmp_path / "unopened-harness", {"task_id": "authored"},
                             setup={"files": [entry(source)]} if place == "setup" else None,
                             verifier={"kind": "command", "argv": ["/bin/true"],
                                       "inputs": [entry(source)]} if place == "verify" else None,
                             env={"kind": backend})
    assert result["invalid"]["stage"] == "immutable runtime admission"
    assert result["exit_code"] is None and "verdict" not in result


@pytest.mark.parametrize("rel", ["", ".", "../runtime", "/runtime"])
def test_invalid_destination_is_refused(runtime, rel):
    with pytest.raises(ImmutableRuntimeUnavailable):
        projections([entry(runtime, rel)])


@pytest.mark.parametrize("backend", ["files", "exec"])
def test_two_task_runs_share_source_and_restore_mutable_verifier_inputs(tmp_path, runtime, monkeypatch, backend):
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({"entrypoint": "authored.py"}))
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.delenv("HG_AGENT_BASE_URL", raising=False)
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    monkeypatch.setattr(runner.harness_runtime, "lookup", lambda *a, **kw: None)
    monkeypatch.setattr(container, "image_python", lambda _: "/usr/bin/python3")
    monkeypatch.setattr(sandbox, "_bwrap", lambda: "/synthetic/bwrap")
    plans, directories = [], []
    original_digest = digest(runtime)

    def synthetic_launch(argv, **kw):
        work = kw["task_dir"]
        if backend == "files":
            command = sandbox.command(argv, **{k: v for k, v in kw.items()
                                               if k not in ("timeout_s", "cwd")})
            assert ("--ro-bind", str(runtime), str(work / "dojo_runtime")) in mounts(command)
        else:
            command = container.command(argv, **{k: v for k, v in kw.items() if k != "timeout_s"})
            assert f"type=bind,src={runtime},dst=/app/dojo_runtime,readonly" in command
        plans.append(command)
        if kw.get("task_dir_writable", True):
            directories.append(work)
            assert (work / "tests.txt").read_text() == "trusted tests"
            assert not (work / "dojo_runtime").exists()
            assert not (work / "trusted_runtime").exists()
            (work / "tests.txt").write_text("harness tampering")
        else:
            assert (work / "tests.txt").read_text() == "trusted tests"
            assert not (work / "dojo_runtime").exists()
            assert not (work / "trusted_runtime").exists()
            assert kw["immutable_runtimes"] == ((runtime, "dojo_runtime"), (runtime, "trusted_runtime"))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(sandbox, "run", synthetic_launch)
    monkeypatch.setattr(container, "run", synthetic_launch)
    mutable = {"path": "tests.txt", "content": "trusted tests"}
    for number in range(2):
        result = runner._run_one(repo, {"task_id": f"authored-{number}"},
                                 setup={"files": [entry(runtime), mutable]},
                                 verifier={"kind": "command", "argv": ["/bin/true"],
                                           "inputs": [entry(runtime, "trusted_runtime"), mutable]},
                                 env={"kind": backend, "image": IMAGE})
        assert result["exit_code"] == 0 and result["verdict"]["passed"]
    assert len(plans) == 4 and directories[0] != directories[1]
    assert all(not p.exists() for p in directories)  # Runner removed task roots.
    assert digest(runtime) == original_digest


@pytest.mark.parametrize("verification", [False, True])
def test_live_bwrap_two_tasks_reject_writes_and_keep_digest(tmp_path, runtime, verification):
    if not shutil.which("bwrap"):
        pytest.skip("bwrap unavailable; exact plan is tested separately")
    original_digest = digest(runtime)
    for number in range(2):
        work = tmp_path / f"live-task-{number}"
        work.mkdir()
        verdict = work / "verdict"
        verdict.mkdir()
        bindings = projections([entry(runtime)])
        assert _materialize(work, [entry(runtime)], ROOT) == []
        assert not (work / "dojo_runtime").exists()
        script = (
            "import errno,json,pathlib; "
            f"p=pathlib.Path({str(work / 'dojo_runtime/bin/python')!r}); "
            "print(p.read_text().strip()); "
            "\ntry: p.write_text('tampered')"
            "\nexcept OSError as exc: print(json.dumps({'write_errno':exc.errno})); assert exc.errno==errno.EROFS"
            "\nelse: raise AssertionError('runtime is writable')"
        )
        if verification:
            script += (f"\ntry: pathlib.Path({str(work / 'tamper')!r}).write_text('x')"
                       "\nexcept OSError as exc: assert exc.errno==errno.EROFS"
                       "\nelse: raise AssertionError('task is writable')"
                       f"\npathlib.Path({str(verdict / 'reward')!r}).write_text('authored verdict')")
        plan = native_plan(work, runtime, task_dir_writable=not verification,
                           writable_paths=(verdict,) if verification else ())
        # Execute the exact same mount plan with an authored probe as its argv.
        plan[plan.index("--") + 1:] = ["/usr/bin/python3", "-c", script]
        proc = subprocess.run(plan, capture_output=True, text=True, timeout=20)
        if proc.returncode and ("Operation not permitted" in proc.stderr
                                or "No permissions" in proc.stderr):
            pytest.skip(f"bwrap namespace unavailable: {proc.stderr.strip()}")
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout.splitlines()[-1]) == {"write_errno": errno.EROFS}
        print(json.dumps({"task": number, "verification": verification,
                          "runtime_bind": [str(runtime), str(work / "dojo_runtime")],
                          "output": proc.stdout, "source_digest": digest(runtime)}))
        # bwrap creates an empty mount point, never a runtime copy.
        assert list((work / "dojo_runtime").iterdir()) == []
        assert digest(runtime) == original_digest
        if verification:
            assert not (work / "tamper").exists()
            assert (verdict / "reward").read_text() == "authored verdict"


def test_unavailable_bwrap_and_docker_mount_failure_fail_closed(tmp_path, runtime, monkeypatch):
    def unavailable():
        raise sandbox.SandboxUnavailable("authored unavailable namespace")
    monkeypatch.setattr(sandbox, "_bwrap", unavailable)
    with pytest.raises(ImmutableRuntimeUnavailable, match="unavailable namespace"):
        sandbox.run(["/bin/true"], platform=ROOT, work_root=tmp_path,
                    task_dir=tmp_path / "task", env={}, timeout_s=1,
                    immutable_runtimes=projections([entry(runtime)]))
    work = tmp_path / "task"
    work.mkdir()
    monkeypatch.setattr(container, "remove_container", lambda _: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
                        subprocess.CompletedProcess(cmd, 125, "", "authored bind failure"))
    with pytest.raises(ImmutableRuntimeUnavailable, match="bind failure"):
        container.run(["/bin/true"], image=IMAGE, task_dir=work, task_mount="/app",
                      immutable_runtimes=projections([entry(runtime)]))


def test_verification_target_symlink_is_unmeasured_without_launch(tmp_path, runtime, monkeypatch):
    work = tmp_path / "task"
    work.mkdir()
    (work / "dojo_runtime").symlink_to(runtime, target_is_directory=True)
    monkeypatch.setattr(sandbox, "run", lambda *a, **kw: pytest.fail("unsafe mount launched"))
    result = verifier._verify(work, {"kind": "command", "argv": ["/bin/true"],
                                    "inputs": [entry(runtime)]}, "", sandbox=True, timeout_s=1)
    assert result["score"] is None and result["immutable_runtime_error"]


def test_docker_extra_mount_cannot_reopen_runtime(tmp_path, runtime):
    work = tmp_path / "task"
    work.mkdir()
    for mount in (f"type=bind,src={tmp_path},dst=/app/dojo_runtime/cache",
                  f"type=bind,src={runtime},dst=/writable-alias"):
        with pytest.raises(ImmutableRuntimeUnavailable):
            container.command(["true"], image=IMAGE, task_dir=work, task_mount="/app",
                              immutable_runtimes=projections([entry(runtime)]),
                              extra_mounts=("--mount", mount))


def test_source_inside_writable_mount_is_refused(tmp_path):
    work = tmp_path / "task"
    source = work / "source"
    source.mkdir(parents=True)
    with pytest.raises(ImmutableRuntimeUnavailable, match="writable storage"):
        container.command(["true"], image=IMAGE, task_dir=work, task_mount="/app",
                          immutable_runtimes=projections([entry(source)]))


@pytest.mark.parametrize("backend", ["files", "exec"])
def test_setup_verify_same_projection_is_bound_once(tmp_path, runtime, monkeypatch, backend):
    work = tmp_path / "task"
    work.mkdir()
    bindings = projections([entry(runtime), entry(runtime)])
    monkeypatch.setattr(sandbox, "_bwrap", lambda: "/synthetic/bwrap")
    if backend == "files":
        # Also test the same deduplication exercised by the verifier's merged list.
        plan = sandbox.command(["true"], platform=ROOT, work_root=tmp_path,
                               task_dir=work, repo=tmp_path, env={}, immutable_runtimes=bindings)
        assert mounts(plan).count(("--ro-bind", str(runtime), str(work / "dojo_runtime"))) == 1
    else:
        plan = container.command(["true"], image=IMAGE, task_dir=work,
                                 task_mount="/app", immutable_runtimes=bindings)
        assert plan.count(f"type=bind,src={runtime},dst=/app/dojo_runtime,readonly") == 1


def test_sealed_native_verifier_plan_projects_same_source(tmp_path, runtime, monkeypatch):
    work = tmp_path / "task"
    work.mkdir()
    reward = work / "verifier"
    reward.mkdir()
    manifest = {"dataset": "scienceworld", "identity": {}, "environment": {},
                "interpreter": "/sealed/python", "loader": "/sealed/ld.so",
                "library_path": [], "python_flags": []}
    frozen = FrozenRuntime(manifest, "authored-plan-only", {}, 999)
    calls = []

    def no_execution(plan, **kw):
        calls.append(plan)
        return subprocess.CompletedProcess(plan, 0, "", "")

    monkeypatch.setattr(subprocess, "run", no_execution)
    frozen.run(["python3", "check.py"], workdir=work, reward_dir=reward,
               timeout_s=1, immutable_runtimes=projections([entry(runtime)]))
    plan = calls[0]
    events = mounts(plan)
    assert ("--ro-bind", str(runtime), "/task/dojo_runtime") in events
    assert events.index(("--ro-bind", str(runtime), "/task/dojo_runtime")) > events.index(
        ("--bind", str(work), "/task"))
    assert plan.index("--remount-ro") > plan.index("/task/dojo_runtime")
    assert not (work / "dojo_runtime").exists()


@pytest.mark.parametrize("backend", ["files", "exec"])
@pytest.mark.parametrize("phase", ["agent", "verify"])
def test_backend_bind_failure_makes_task_invalid(tmp_path, runtime, monkeypatch, backend, phase):
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({"entrypoint": "authored.py"}))
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.delenv("HG_AGENT_BASE_URL", raising=False)
    monkeypatch.setattr(runner.harness_runtime, "lookup", lambda *a, **kw: None)
    monkeypatch.setattr(container, "image_python", lambda _: "/usr/bin/python3")

    def launch(argv, **kw):
        if phase == "agent" or not kw.get("task_dir_writable", True):
            raise ImmutableRuntimeUnavailable("authored bind failure")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(sandbox, "run", launch)
    monkeypatch.setattr(container, "run", launch)
    result = runner._run_one(repo, {"task_id": "authored"},
                             setup={"files": [entry(runtime)]},
                             verifier={"kind": "command", "argv": ["/bin/true"]},
                             env={"kind": backend, "image": IMAGE})
    assert result["invalid"]["stage"] == (
        "immutable runtime launch" if phase == "agent" else "immutable runtime verification")
    assert result["exit_code"] is None and "verdict" not in result


def test_four_dataset_flags_keep_runtime_paths():
    import ast

    for name in ("agentdojo", "scienceworld"):
        path = f"data/datasets/{name}.py"
        tree = ast.parse((ROOT / path).read_text())
        marked = [node for node in ast.walk(tree) if isinstance(node, ast.Dict)
                  and any(isinstance(key, ast.Constant) and key.value == "immutable"
                          and isinstance(value, ast.Constant) and value.value is True
                          for key, value in zip(node.keys, node.values))]
        assert len(marked) == 2
        namespace = {"DATASET_ROOT": Path("/authored/dataset"), "str": str,
                     "JRE_STAGED": Path("/authored/jre"),
                     "_LIB": type("AuthoredLibrary", (), {"JRE_RELDIR": "jre", "JRE_DIRNAME": "authored-jre"})}
        actual = [eval(compile(ast.Expression(node), path, "eval"), namespace) for node in marked]
        if name == "agentdojo":
            assert actual == [entry(Path("/authored/dataset/agent_runtime")),
                              entry(Path("/authored/dataset/runtime"), "trusted_runtime")]
        else:
            assert actual == [entry(Path("/authored/jre"), "jre/authored-jre")] * 2


def test_live_runner_keeps_mutable_antitamper_with_shared_runtimes(tmp_path, runtime, monkeypatch):
    if not shutil.which("bwrap"):
        pytest.skip("bwrap unavailable; synthetic runner and exact plans tested separately")
    repo = tmp_path / "harness"
    repo.mkdir()
    (repo / "harness.json").write_text(json.dumps({"entrypoint": "authored.py"}))
    (repo / "authored.py").write_text(
        "import argparse,errno,json,pathlib\n"
        "a=argparse.ArgumentParser(); a.add_argument('--task'); a.add_argument('--workdir'); p=a.parse_args()\n"
        "w=pathlib.Path(p.workdir); r=w/'dojo_runtime/bin/python'\n"
        "assert r.read_text()=='authored runtime marker\\n'\n"
        "assert not (w/'trusted_runtime').exists()\n"
        "try: r.write_text('tampered')\n"
        "except OSError as e: assert e.errno==errno.EROFS\n"
        "else: raise AssertionError('runtime writable')\n"
        "(w/'tests.txt').write_text('harness tampering')\n"
        "(w/'trace.jsonl').write_text(json.dumps({'runtime_readonly':True})+'\\n')\n")
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")
    monkeypatch.delenv("HG_AGENT_BASE_URL", raising=False)
    monkeypatch.setenv("HARNESSGRAD_WORK_ROOT", str(tmp_path))
    original = digest(runtime)
    check = (
        "from pathlib import Path; "
        "assert Path('tests.txt').read_text()=='trusted tests'; "
        "assert Path('dojo_runtime/bin/python').read_bytes()==Path('trusted_runtime/bin/python').read_bytes()"
    )
    mutable = {"path": "tests.txt", "content": "trusted tests"}
    for number in range(2):
        result = runner._run_one(repo, {"task_id": f"authored-live-{number}"},
                                 setup={"files": [entry(runtime), mutable]},
                                 verifier={"kind": "command", "argv": ["/usr/bin/python3", "-c", check],
                                           "inputs": [entry(runtime, "trusted_runtime"), mutable]})
        assert result["exit_code"] == 0, result
        assert json.loads(result["trace"])["runtime_readonly"] is True
        assert result["verdict"]["passed"], result
        assert digest(runtime) == original
