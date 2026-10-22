"""The harness runs in its own mount namespace, or the run does not start.

Every test here exists because of a specific way this could be wrong, and two of
them are ways it *was* wrong while being developed:

1. A value-shaped guess at "is this environment variable a path?" deleted
   `HG_AGENT_API_KEY` out of the sandbox -- because `Path("deepseek-flash").resolve()`
   is relative to the platform's cwd and lands inside the hidden tree. The symptom
   would have been "the harness cannot reach a model", which reads as a harness
   defect. The test that catches it checks the sandbox **from the inside**, not the
   command line the platform built.
2. The same guess reported that `pnpm_config_verify_deps_before_run=false` was
   "under the hidden tree".

Both were false positives from asking a question that has no general answer. The
tests below pin the fix: the platform checks only the paths it set itself, and it
checks them by observing the subject's view rather than its own intent.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import eval.containers.sandbox as sandbox  # noqa: E402

needs_bwrap = pytest.mark.skipif(
    shutil.which("bwrap") is None,
    reason="bubblewrap not installed; the platform refuses to run without it, so "
           "there is nothing here to test on this machine")

PLATFORM = ROOT
WORK_ROOT = ROOT.parent / "harnessgrad_work"


def inside(env: dict[str, str], work_dir: Path, script: str,
           repo: Path | None = None) -> dict:
    """Run `script` in the sandbox and return what it saw."""
    probe = work_dir / "probe.py"
    probe.write_text(script)
    proc = sandbox.run([sys.executable, str(probe)], platform=PLATFORM,
                       work_root=WORK_ROOT, task_dir=work_dir, env=env,
                       timeout_s=120, cwd=work_dir, repo=repo,
                       state_dirs=(repo / ".state",) if repo else ())
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture
def staged_harness(tmp_path):
    """A throwaway harness laid out the way a run actually lays one out.

    The layout matters and a `tmp_path` alone gets it wrong: a task directory and the
    harness must not sit under a common directory that the plan then binds, because
    binding that common parent re-materialises everything in it. In a real run the
    task directory is under `/tmp` and the harness is under the work root, so this
    fixture reproduces that separation rather than inheriting pytest's.
    """
    repo = tmp_path / "workroot" / "run-id" / "workspace"
    state = repo / ".state"
    state.mkdir(parents=True)
    (repo / "harness.json").write_text('{"name": "probe", "version": "1", '
                                       '"entrypoint": "agent.py"}')
    (repo / "agent.py").write_text("# the subject's source\n")
    return repo, state


@needs_bwrap
def test_the_platform_is_not_on_the_subjects_disk(tmp_path, staged_harness):
    """The whole point: not "permission denied", but absent.

    This is the measurement that motivated the module, and it is asserted from
    inside the namespace rather than inferred from the arguments handed to bwrap.
    """
    repo, _ = staged_harness
    seen = inside({}, tmp_path, (
        "import json, os, pathlib\n"
        f"p = pathlib.Path({str(PLATFORM)!r})\n"
        "print(json.dumps({\n"
        "  'env': (p / '.env').exists(),\n"
        "  'driver': (p / 'driver.py').exists(),\n"
        # The implementation moved out of `driver.py`; the rule is about the
        # platform's code, so the package that holds it is probed too. A sandbox
        # that hid the entrypoint and left the scorer readable would look fine.
        "  'impl': (p / 'harnessgrad').exists(),\n"
        "  'scorer': (p / 'eval' / 'run' / 'metrics.py').exists(),\n"
        "  'methods': (p / 'methods').exists(),\n"
        "  'listing': sorted(os.listdir(p)) if p.is_dir() else 'not-a-dir',\n"
        "}))\n"), repo=repo)
    # The directory *name* may survive as an empty mount point -- covering the
    # platform's parent as well would erase the name, but it would also erase the
    # work root's siblings, and a name is not a secret. The contents are what matter
    # and the contents are gone: an empty listing, not a hidden one.
    assert seen["listing"] == [], seen
    assert seen["env"] is False and seen["driver"] is False, seen
    assert seen["impl"] is False, seen
    assert seen["scorer"] is False and seen["methods"] is False, seen


@needs_bwrap
def test_the_subject_keeps_its_own_credentials(tmp_path):
    """A sandboxed harness with no API key is a broken instrument, not a result.

    Regression for the bug that deleted `HG_AGENT_API_KEY`: the platform passed
    `HG_AGENT_MODEL=deepseek-flash` through a path check, `Path("deepseek-flash")`
    resolved against the platform's cwd, and the value was judged to be inside the
    hidden tree.
    """
    env = {"HG_AGENT_MODEL": "deepseek-flash",
           "HG_AGENT_BASE_URL": "https://api.deepseek.com",
           "HG_AGENT_API_KEY": "sk-not-a-real-key"}
    seen = inside(env, tmp_path, (
        "import json, os\n"
        "print(json.dumps({k: os.environ.get(k) for k in [\n"
        "  'HG_AGENT_MODEL', 'HG_AGENT_BASE_URL', 'HG_AGENT_API_KEY']}))\n"))
    assert seen["HG_AGENT_MODEL"] == "deepseek-flash"
    assert seen["HG_AGENT_BASE_URL"] == "https://api.deepseek.com"
    assert seen["HG_AGENT_API_KEY"] == "sk-not-a-real-key", \
        "the subject was left without the credential it needs to run at all"


@needs_bwrap
def test_host_variables_are_not_mistaken_for_paths(tmp_path):
    """`false` is not a path. Neither is `0.1.5-rc.2`."""
    env = {"npm_package_version": "0.1.5-rc.2",
           "pnpm_config_verify_deps_before_run": "false"}
    seen = inside(env, tmp_path, (
        "import json, os\n"
        "print(json.dumps({k: os.environ.get(k) for k in [\n"
        "  'npm_package_version', 'pnpm_config_verify_deps_before_run']}))\n"))
    assert seen == env, seen


@needs_bwrap
def test_the_work_root_survives_and_the_platform_does_not(tmp_path, staged_harness):
    """The harness's tree is reachable; the platform's is not.

    These two are siblings in a real run -- `harnessgrad_work/` next to
    `HarnessGrad/` -- which makes this the case worth pinning: exposing one must not
    expose the other, and hiding one must not take the other with it. The plan covers
    the platform's own path rather than its parent precisely so the sibling survives.
    """
    repo, _ = staged_harness
    env = {"HARNESSGRAD_WORK_ROOT": str(repo.parent.parent)}
    seen = inside(env, tmp_path, (
        "import json, os, pathlib\n"
        "w = pathlib.Path(os.environ['HARNESSGRAD_WORK_ROOT'])\n"
        f"p = pathlib.Path({str(PLATFORM)!r})\n"
        "print(json.dumps({\n"
        "  'work_root': w.is_dir(),\n"
        "  'harness_readable': (w / 'run-id' / 'workspace' / 'agent.py').exists(),\n"
        "  'platform_readable': (p / 'driver.py').exists(),\n"
        "  'impl_readable': (p / 'harnessgrad').exists(),\n"
        "  'platform_listing': sorted(os.listdir(p)) if p.is_dir() else 'not-a-dir',\n"
        "}))\n"), repo=repo)
    assert seen["work_root"] is True, "the work root's tree disappeared"
    assert seen["harness_readable"] is True, "the harness lost its own source"
    assert seen["platform_readable"] is False, "the driver is still readable"
    assert seen["impl_readable"] is False, "the implementation package is still readable"
    assert seen["platform_listing"] == [], seen


@needs_bwrap
def test_a_harness_can_actually_work_in_its_workdir(tmp_path):
    """The sandbox must not break the thing a harness does all day: file I/O.

    Read-only applies to the harness's *own tree*, never to `--workdir`. The
    reference harness executes model-issued shell commands with `cwd=workdir`, so a
    read-only workdir would not be a stricter sandbox -- it would be a harness that
    cannot do any task at all, scoring zero for a reason that looks like a harness
    defect. Verified here in the shape the real one uses: nested mkdir, redirect,
    read back, and a subprocess.

    Confirmed end to end separately: three tasks whose answers are only obtainable by
    creating and re-reading a file scored 1.000/1.000 in the sandbox, with the
    commands visible in the collected traces.
    """
    script = (
        "import json, os, pathlib, subprocess, sys\n"
        "wd = pathlib.Path(os.environ['TD'])\n"
        "r = {}\n"
        "def step(label, fn):\n"
        "    try: r[label] = fn()\n"
        "    except OSError as e: r[label] = type(e).__name__\n"
        "step('mkdir', lambda: (wd / 'deep' / 'nested').mkdir(parents=True) or 'ok')\n"
        "step('write', lambda: (wd / 'deep' / 'nested' / 'v.txt').write_text('gamma') and 'ok')\n"
        "step('read', lambda: (wd / 'deep' / 'nested' / 'v.txt').read_text())\n"
        "step('shell', lambda: subprocess.run(\n"
        "    \"printf beta > notes.txt && cat notes.txt\", shell=True, cwd=wd,\n"
        "    capture_output=True, text=True).stdout)\n"
        "step('rmdir', lambda: (wd / 'deep').rename(wd / 'moved') and 'ok')\n"
        "print(json.dumps(r))\n")
    seen = inside({"TD": str(tmp_path)}, tmp_path, script)
    assert seen["mkdir"] == "ok", seen
    assert seen["write"] == "ok", seen
    assert seen["read"] == "gamma", seen
    assert seen["shell"] == "beta", seen
    assert seen["rmdir"] == "ok", seen


@needs_bwrap
def test_the_harness_tree_stays_read_only(tmp_path):
    """The other half: the subject's own source is not scratch space.

    One copy of the harness scores every task in a round, so a subject that rewrote
    its own `agent.py` while answering task 1 would be a different program by task 2.
    """
    seen = inside({}, tmp_path, (
        "import json, os, pathlib\n"
        "results = {}\n"
        "for label, victim in [\n"
        f"    ('write_source', pathlib.Path({str(WORK_ROOT)!r})),\n"
        "]:\n"
        "    try:\n"
        "        (victim / 'probe_write').write_text('x'); results[label] = 'wrote'\n"
        "    except OSError as e:\n"
        "        results[label] = type(e).__name__\n"
        "print(json.dumps(results))\n"))
    assert seen["write_source"] == "OSError", seen


@needs_bwrap
def test_the_harness_gets_a_writable_hole_in_a_read_only_tree(tmp_path, staged_harness):
    """The contract a harness actually needs: read its code, write its scratch.

    Read-only on the whole tree was too blunt. A harness legitimately wants to put a
    cache, a session file or an intermediate artefact next to itself, and refusing
    that would break working harnesses to prevent a failure that is better prevented
    precisely -- by making the *code* unwritable and one named directory writable.

    A tmpfs rather than a shared directory, so what it writes is per-task: a score
    must not depend on what an earlier task left behind.
    """
    repo, state = staged_harness
    seen = inside({"HARNESSGRAD_STATE": str(state), "TD": str(tmp_path)},
                 tmp_path,
        "import json, os, pathlib\nrepo = pathlib.Path(os.environ['HG_REPO'])\nstate = pathlib.Path(os.environ['HARNESSGRAD_STATE'])\ndef attempt(fn):\n    try:\n        fn(); return 'ok'\n    except OSError as exc:\n        return type(exc).__name__\nprint(json.dumps({\n  'read_source': attempt(lambda: repo.joinpath('agent.py').read_text() and 'ok'),\n  'write_source': attempt(lambda: repo.joinpath('agent.py').write_text('rewritten')),\n  'delete_manifest': attempt(lambda: repo.joinpath('harness.json').unlink()),\n  'write_state': attempt(lambda: state.joinpath('cache.json').write_text('{}')),\n  'mkdir_state': attempt(lambda: state.joinpath('nested').mkdir()),\n  'write_workdir': attempt(lambda: pathlib.Path(os.environ['TD']).joinpath('out.txt').write_text('x')),\n}))\n".replace("os.environ['HG_REPO']", repr(str(repo))),
        repo=repo)
    assert seen["read_source"] == "ok", "the harness cannot read its own code"
    assert seen["write_source"] == "OSError", seen
    assert seen["delete_manifest"] == "OSError", seen
    assert seen["write_state"] == "ok", "no writable scratch space"
    assert seen["mkdir_state"] == "ok", seen
    assert seen["write_workdir"] == "ok", seen


@needs_bwrap
def test_a_missing_state_directory_refuses_rather_than_degrades(tmp_path):
    """No mount target means no sandbox, and that must stop the run.

    The alternative -- proceeding without the writable hole -- would hand a harness a
    read-only tree and let it fail task by task, which reads as a harness defect.
    """
    with pytest.raises(sandbox.SandboxUnavailable) as exc:
        sandbox.command(["true"], platform=PLATFORM, work_root=WORK_ROOT,
                        task_dir=tmp_path, env={},
                        repo=tmp_path, state_dirs=(tmp_path / "nonexistent",))
    assert "state" in str(exc.value) or ".state" in str(exc.value), str(exc.value)


def test_an_overlapping_work_root_is_refused():
    """Hiding the platform must not hide the subject with it."""
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.hidden_trees(PLATFORM / "runs" / "x" / "workspace", PLATFORM)
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.hidden_trees(PLATFORM, PLATFORM)


def test_a_work_root_on_another_mount_is_supported():
    """No common ancestor is required, and that is a fix rather than a nicety.

    The first plan hid the shallowest directory containing both trees, so a work
    root on a different mount from the platform -- `/work`, or a `tmp_path` under
    `/tmp` in a test -- made the driver refuse to start. This asserts the property
    that replaced it: the work root's parents are never covered, so the two trees'
    relative position is irrelevant.
    """
    hidden = sandbox.hidden_trees(Path("/work"), PLATFORM)
    assert PLATFORM in hidden
    assert Path("/work") not in hidden
    for parent in Path("/work").parents:
        assert parent not in hidden, f"{parent} must not be covered"
    # A private /tmp is still installed when the work root is not under it.
    assert Path("/tmp") in hidden


def test_the_interpreter_is_never_hidden():
    """The bug that this test exists for: hiding /mnt erased the interpreter.

    This machine's Python is under `/mnt/8t/.../anaconda3`, so a plan that covered
    every parent of the work root left the subject unable to start -- and the
    symptom was "the harness is broken".
    """
    prefix = sandbox.interpreter_prefix()
    assert prefix is not None, "this test assumes a conda/venv interpreter"
    hidden = sandbox.hidden_trees(WORK_ROOT, PLATFORM)
    for tree in hidden:
        assert tree != prefix and tree not in prefix.parents, \
            f"hidden tree {tree} contains the interpreter {prefix}"


def test_missing_bwrap_refuses_rather_than_degrades(monkeypatch):
    """A sandbox that silently did not apply changes what every number means."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(sandbox.SandboxUnavailable) as exc:
        sandbox.command(["true"], platform=PLATFORM, work_root=WORK_ROOT,
                        task_dir=Path("/tmp/x"), env={})
    assert "no-sandbox" in str(exc.value), \
        "the refusal must name the way to opt out"

    report = sandbox.self_check(PLATFORM, WORK_ROOT)
    assert report["ok"] is False
    assert "no-sandbox" in report["detail"]


@needs_bwrap
def test_self_check_notices_a_plan_that_leaks(tmp_path):
    """The self-check has to be able to fail, or it is decoration.

    A plan that actually exposes the platform must be caught. Note what does *not*
    qualify: deleting every `--tmpfs` from the plan still leaves the platform
    invisible, because bwrap starts from an empty root rather than inheriting the
    host's. The first version of this test mutated the plan that way, saw the check
    still pass, and would have been written off as a bug in the check -- it was a bug
    in the mutation. The property under test is "can the subject see the platform",
    so the mutation has to be one that makes the subject able to.

    The leaking plan binds the host root read-only, which is the realistic mistake:
    it is the reflex fix for "my harness cannot find its interpreter".
    """
    assert sandbox.self_check(PLATFORM, WORK_ROOT)["ok"] is True

    real_command = sandbox.command

    def leaky(argv, **kwargs):
        # `--bind / /` makes the host root visible, and the platform with it. The
        # real platform's `.env` is never read: the check only asks whether the
        # path exists, and the assertion below runs against a stand-in.
        return real_command(argv, **kwargs)[:-len(argv) - 1] + [
            "--bind", "/", "/", "--"] + argv

    sandbox.command = leaky
    try:
        report = sandbox.self_check(PLATFORM, WORK_ROOT)
    finally:
        sandbox.command = real_command
    assert report["ok"] is False, "a plan that exposes the host root passed"
    assert "still visible" in report["detail"], report["detail"]
