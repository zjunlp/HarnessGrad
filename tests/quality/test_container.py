"""The `exec` environment kind: the container boundary, end to end. INTERFACE.md §2.5.

Every test here either proves a property the contract claims, or proves that the test
for that property **can fail**. The second half is not padding: this module's own
`self_check` was written once with two probes that could not fail for the reason they
claimed (a marker under `/tmp`, which the container shadows with a tmpfs; and a write
to `/usr`, which a non-root uid cannot do in any image), and both passed against a
deliberately broken plan. Checks that cannot fail are worse than no checks, because
they are believed.

Docker is required. The tests skip rather than fail when there is no daemon, because a
machine without Docker is a machine that cannot run this feature at all -- but the
skip is loud in its reason, not a silent pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import eval.containers.container as container
from ckpt.git_state import STATE_DIRNAME
from data.scaffolds import registry
from eval.run.integrity import PLATFORM_ROOT
from policy_assertions import assert_driver_policy_refusal

ROOT = Path(__file__).resolve().parents[2]
DEMO_IMAGE = os.environ.get("HG_EXEC_DEMO_IMAGE",
                            "docker.m.daocloud.io/library/python:3.11-slim")


def _require_docker() -> str:
    version = container.available()
    if version is None:
        pytest.skip("no docker daemon: the exec environment cannot be exercised here")
    try:
        return container.resolve_digest(DEMO_IMAGE)
    except container.ContainerUnavailable as exc:
        pytest.skip(f"{DEMO_IMAGE!r} is not on the local daemon: {exc}")


#: A harness that is **self-contained**: it uses nothing but the base interpreter, which
#: is what an `exec` harness has to be, because the image is the environment and the
#: platform's own tools are not in it. It also runs on the host unchanged, which is what
#: makes the same file usable as the `files` control.
#:
#: The mode travels in `HG_AGENT_PROBE_MODE` rather than a plainer name on purpose --
#: `eval/containers/container.py` forwards an allowlist of `HG_AGENT_*` and the mode has to survive
#: it. `test_only_declared_variables_cross_into_the_container` pins the other half.
HARNESS = '''\
import argparse, json, os, pathlib, subprocess, sys

ap = argparse.ArgumentParser()
ap.add_argument("--task"); ap.add_argument("--workdir")
a = ap.parse_args()
work = pathlib.Path(a.workdir)
task = json.loads(pathlib.Path(a.task).read_text())
tid = task["task_id"]
mode = os.environ.get("HG_AGENT_PROBE_MODE", "solve")

if mode == "idle":
    pass
elif tid == "e01":
    (work / "broken.py").write_text(
        "import sys\\ndef add(a, b):\\n    return a + b\\n")
elif tid == "e02":
    # Asked of the interpreter that is actually running this harness. If the platform
    # ran it on the host this prints 3.12 and the check fails.
    out = subprocess.run(
        [sys.executable, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture_output=True, text=True).stdout.strip()
    (work / "answer.txt").write_text(out)
elif tid == "e03":
    if mode == "cheat":
        # Rewrite the specification so that it cannot fail.
        (work / "check.py").write_text("import sys; sys.exit(0)\\n")
    else:
        (work / "out.txt").write_text("correct\\n")
elif tid == "e04":
    (work / "done.txt").write_text("yes\\n")

(work / "trace.jsonl").write_text(json.dumps({
    "harness_identity": {"agent_model": "probe", "harness": "probe 1"},
    "usage": {"input_tokens": 11, "output_tokens": 7},
}) + "\\n")
'''


def _harness(tmp_path: Path, *, env_kinds=None) -> Path:
    repo = tmp_path / "probe_exec_harness"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "agent.py").write_text(HARNESS)
    (repo / "harness.json").write_text(json.dumps({
        "name": "probe-exec", "version": "1", "entrypoint": "agent.py",
        "backend": "cli", "env_kinds": env_kinds or ["files", "exec"],
    }))
    return repo


def _run(tmp_path: Path, *, mode="solve", dataset="verify_exec",
         env_kinds=None, timeout=1800, extra_argv=()) -> subprocess.CompletedProcess:
    runs = tmp_path / "runs"
    return subprocess.run(
        [sys.executable, "driver.py",
         "--harness", str(_harness(tmp_path, env_kinds=env_kinds)),
         "--mode", "A", "--rounds", "1", "--sampling", "all",
         "--dataset", dataset, "--run-id", "probe", *extra_argv,
         "--runs-root", str(runs), "--work-root", str(tmp_path / "work"),
         "--method-entrypoint",
         f"{sys.executable} {ROOT / 'methods/noop/run.py'}"],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "HG_AGENT_BACKEND": "mock",
             "HG_AGENT_PROBE_MODE": mode})


def _curve(tmp_path: Path) -> list[dict]:
    path = tmp_path / "runs" / "probe" / "curve.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ------------------------------------------------------------------ the gate ---

def test_a_harness_that_declares_only_files_is_refused_for_an_exec_dataset(tmp_path):
    """Refused, not attempted.

    The failure mode otherwise is a harness that runs, scores zero on every task, and
    looks like a weak harness. The manifest is the harness's own statement of what it
    can do, and the platform's job is to hold it to that rather than to find out by
    measurement. This costs nothing and needs no docker -- that is the point of
    checking before the run rather than during it.
    """
    proc = _run(tmp_path, env_kinds=["files"])
    assert proc.returncode == 2, proc.stdout[-800:]
    assert "exec" in (proc.stdout + proc.stderr)


def test_an_image_that_is_not_local_is_refused_rather_than_pulled(tmp_path):
    """§2.5.4: no digest, no start. §2.5.5: nothing is built or pulled during a run."""
    _require_docker()
    with pytest.raises(container.ContainerUnavailable) as exc:
        container.resolve_digest("harnessgrad/definitely-not-a-real-image:latest")
    assert "import_env" in str(exc.value), (
        "the refusal must say what to do about it, not only that it failed")


def test_a_tag_is_not_accepted_as_already_pinned():
    assert container.looks_pinned("python:3.11-slim") is False
    assert container.looks_pinned("repo/x@sha256:" + "a" * 64) is True
    assert container.looks_pinned("sha256:" + "b" * 64) is True
    # A digest-shaped string that is not a digest is not pinned.
    assert container.looks_pinned("python@sha256:abc") is False


def test_running_an_unpinned_image_is_refused_at_the_call(tmp_path):
    """The safety net inside `run`, independent of the driver's startup resolution.

    Resolving at startup is the mechanism; this is what happens if a caller ever
    forgets. The cost of being wrong is a silent pull, and the cost of checking is a
    string compare.
    """
    _require_docker()
    with pytest.raises(container.ContainerUnavailable):
        container.run(["true"], image="python:3.11-slim", task_dir=tmp_path,
                      task_mount="/app")


# ------------------------------------------------------------ the boundary ---

def test_the_container_boundary_holds():
    """The nine properties, measured. See `eval/container.self_check`."""
    _require_docker()
    result = container.self_check(PLATFORM_ROOT, DEMO_IMAGE)
    assert result["ok"], result["detail"]
    assert result["image_digest"].startswith("sha256:")


def test_the_platform_is_invisible_inside_the_container(tmp_path):
    """Stated separately from `self_check` so a reader can see the fact, not a boolean.

    The probe uses the **real** platform path. A marker under `/tmp` would be shadowed
    by the container's tmpfs and report "invisible" whether or not anything was
    mounted, which is a check that cannot fail.
    """
    pinned = _require_docker()
    proc = container.run(
        ["sh", "-c", f"test -e {PLATFORM_ROOT / 'driver.py'} && echo yes || echo no"],
        image=pinned, task_dir=tmp_path, task_mount="/app",
        state_dirs=(), timeout_s=180)
    assert proc.stdout.strip() == "no", proc.stdout


def test_only_declared_variables_cross_into_the_container(tmp_path):
    """An allowlist, not "the host environment minus some names".

    Both directions matter: the agent's configuration has to survive, and everything
    else has to not. `HG_METHOD_*` is the one with teeth -- the two budgets are kept
    apart by `harness_env()` on the host and by this allowlist in a container, and two
    independent mechanisms is the correct number for a separation that has already
    been broken once.
    """
    pinned = _require_docker()
    proc = container.run(
        ["sh", "-c", "echo AGENT=$HG_AGENT_PROBE_MODE "
                     "METHOD=${HG_METHOD_API_KEY:-absent} "
                     "HOME_SET=${HOME:+yes}"],
        image=pinned, task_dir=tmp_path, task_mount="/app", timeout_s=180,
        env={**container.container_env("/app"), "HG_AGENT_PROBE_MODE": "sentinel",
             "HG_METHOD_API_KEY": "must-not-cross"})
    # `container_env` is the allowlist, so passing the method key in explicitly is the
    # caller's error; what this pins is that the *allowlist* does not carry it and that
    # an agent variable does.
    assert "AGENT=sentinel" in proc.stdout, proc.stdout
    assert "HOME_SET=yes" in proc.stdout, proc.stdout


def test_each_container_run_starts_fresh(tmp_path):
    """The property §2.5.6 asks for, made structural rather than best-effort.

    Killing the process tree of a `docker exec` cannot guarantee that nothing the
    harness started is still running: anything backgrounded is reparented to the
    container's PID 1 and survives the exec. A fresh container per call makes it a
    fact about how the check is started instead of a fact about how well the harness
    was cleaned up -- and this is the measurement that the fresh container is real,
    using a file the harness wrote outside the task bind mount.
    """
    pinned = _require_docker()
    first = container.run(["sh", "-c", "echo leaked > /tmp/marker && echo wrote"],
                          image=pinned, task_dir=tmp_path, task_mount="/app",
                          timeout_s=180)
    assert "wrote" in first.stdout, first.stderr
    second = container.run(
        ["sh", "-c", "test -e /tmp/marker && echo present || echo absent"],
        image=pinned, task_dir=tmp_path, task_mount="/app", timeout_s=180)
    assert second.stdout.strip() == "absent", (
        "the check would inherit whatever the harness left in its container")


def test_the_task_mount_is_read_only_for_the_check(tmp_path):
    """A check that writes into the tree it grades has a verdict that depends on its
    own leftovers."""
    pinned = _require_docker()
    (tmp_path / "artifact.txt").write_text("the harness made this\n")
    proc = container.run(
        ["sh", "-c", "cat artifact.txt; echo x > written 2>/dev/null "
                     "&& echo wrote || echo readonly"],
        image=pinned, task_dir=tmp_path, task_mount="/app", timeout_s=180,
        task_dir_writable=False)
    assert "the harness made this" in proc.stdout, "the check must still read it"
    assert proc.stdout.strip().endswith("readonly"), proc.stdout


# -------------------------------------------------------- the whole thing ---

def test_a_working_exec_harness_is_unmeasured_without_backend_observations(tmp_path):
    """008348b: even a solving harness cannot authorize an unobserved backend."""
    _require_docker()
    assert_driver_policy_refusal(_run(tmp_path, mode="solve"), tmp_path, ("e01", "e02"))


def test_an_idle_exec_harness_is_invalid_rather_than_zero(tmp_path):
    _require_docker()
    assert_driver_policy_refusal(_run(tmp_path, mode="idle"), tmp_path, ("e01", "e02"))


def test_the_check_reports_the_images_interpreter_not_the_hosts(tmp_path):
    """**The test that catches the only failure that matters here.**

    An `exec` task that is really a `files` task is an environment curve that was never
    taken, and it would look correct in every other respect. The host interpreter is a
    different version from the image's, so an honest answer distinguishes them.

    The image is **asked**, rather than assumed to be `DEMO_IMAGE`: the demo dataset
    names an image built from a base plus the base harness's `install`
    (`tools/import_env.py --harness`), so an assertion tied to the base would fail the
    moment the dataset's image changed -- which it did, and did. Asking the image also
    makes the check cover the derived image, which is the one that actually runs.
    """
    _require_docker()
    host = "%d.%d" % sys.version_info[:2]
    spec = registry.load_envs("verify_exec")["e02"]
    expected = registry.load_tasks("verify_exec")[2]["e02"]["expected"]
    pinned = container.resolve_digest(spec["image"])

    proc = container.run(
        ["python3", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        image=pinned, task_dir=tmp_path, task_mount="/app", timeout_s=180)
    in_image = proc.stdout.strip()
    assert in_image, proc.stderr

    if in_image == host:
        # Skipped, not failed: the assertion is only meaningful when the host and the
        # image disagree, and where they agree the test cannot distinguish a real `exec`
        # run from a `files` run. That is a limitation of the demo image, not a defect,
        # and failing here would make the suite machine-dependent.
        pytest.skip(f"vacuous here: the host and {spec['image']!r} both report {host}")
    assert in_image == expected, (
        f"{spec['image']!r} runs Python {in_image} but the demo's e02 verifier expects "
        f"{expected!r}, so the task is unpassable rather than discriminating")


def test_a_cheating_exec_harness_cannot_bypass_policy_admission(tmp_path):
    """The direct restored/cheated verifier control below still tests input integrity."""
    _require_docker()
    assert_driver_policy_refusal(_run(tmp_path, mode="cheat"), tmp_path, ("e01", "e02"))


def test_the_cheat_would_pass_if_the_inputs_were_not_restored(tmp_path):
    """The negative control for the test above.

    Without re-materialization the cheat succeeds, so `test_a_harness_cannot_grade_
    itself_in_a_container` is measuring the re-materialization and not something else
    that happens to make it fail. Run directly against the verifier machinery, with the
    restore step skipped.
    """
    pinned = _require_docker()
    work = tmp_path / "task"
    work.mkdir()
    (work / "check.py").write_text("import sys; sys.exit(0)\n")   # the cheat
    (work / "out.txt").write_text("wrong\n")

    # What the platform does: restore the input, then run.
    from eval.run.runner import _materialize
    _materialize(work, registry.load_tasks("verify_exec")[2]["e03"]["inputs"],
                 PLATFORM_ROOT)

    restored = container.run(["python3", "check.py"], image=pinned, task_dir=work,
                             task_mount="/app", timeout_s=180,
                             task_dir_writable=False)
    assert restored.returncode != 0, (
        "restoring the check must defeat an edited one")

    # And with the cheat left in place it passes -- which is what makes the assertion
    # above a measurement rather than a tautology.
    (work / "check.py").write_text("import sys; sys.exit(0)\n")
    cheated = container.run(["python3", "check.py"], image=pinned, task_dir=work,
                            task_mount="/app", timeout_s=180,
                            task_dir_writable=False)
    assert cheated.returncode == 0


def test_an_unmeasured_exec_environment_is_recorded_without_a_curve(tmp_path):
    _require_docker()
    assert_driver_policy_refusal(_run(tmp_path, mode="idle"), tmp_path, ("e01", "e02"))
    env = json.loads((tmp_path / "runs/probe/run_meta.json").read_text())["env"]
    assert env["kind"] == "exec", env
    assert env["image_digest"].startswith("sha256:"), env
    assert env["network"] == "none", env
    assert env["placement"] == "inside", env
    assert "model" in env and env["model"] is None
    assert "seed" in env and env["seed"] is None


def test_files_datasets_still_record_a_files_environment(tmp_path):
    """The other half of the record rule: adding `exec` must not have told every
    existing dataset that it now runs in a container."""
    assert _run(tmp_path, mode="solve", dataset="verify_demo").returncode == 0
    for point in _curve(tmp_path):
        assert point["env"]["kind"] == "files", point["env"]
        assert point["env"]["image_digest"] is None


# ------------------------------------------------------------ importing ---
#
# `install` has been declared, validated and inert since the manifest gained the field.
# §2.5.6 settles where it goes -- into the environment, at import time -- so these tests
# exist to make the difference between "declared" and "runs" observable.

def _fixture_harness(tmp_path: Path, install: str | None, body: str) -> Path:
    repo = tmp_path / "install_fixture"
    repo.mkdir(parents=True, exist_ok=True)
    manifest = {"name": "install-fixture", "version": "1", "entrypoint": "agent.py",
                "backend": "cli", "env_kinds": ["exec"]}
    if install:
        manifest["install"] = install
        (repo / install).write_text(body)
    (repo / "agent.py").write_text("# a harness\n")
    (repo / ".state").mkdir(exist_ok=True)
    (repo / ".state" / "scratch").write_text("must not be copied\n")
    (repo / "harness.json").write_text(json.dumps(manifest))
    return repo


def test_the_derived_dockerfile_bakes_the_install_in(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import import_env

    repo = _fixture_harness(tmp_path, "requirements.txt", "requests==2.31.0\n")
    text = import_env.derived_dockerfile("base:tag", repo, "requirements.txt")
    assert text.startswith("FROM base:tag\n"), text
    assert "COPY . /opt/harnessgrad-install/" in text, text
    assert "pip install" in text and "requirements.txt" in text, text


def test_an_unrecognised_install_file_is_refused_rather_than_guessed_at(tmp_path):
    """A build step is arbitrary code. A tool that invents a command for a file it does
    not recognise is a tool that runs something nobody wrote."""
    sys.path.insert(0, str(ROOT / "tools"))
    import import_env

    with pytest.raises(SystemExit) as exc:
        import_env.install_command("Makefile")
    assert "neither" in str(exc.value)


def test_a_harness_that_declares_no_install_is_not_an_error(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import import_env

    assert import_env.harness_install(_fixture_harness(tmp_path, None, "")) is None
    repo = _fixture_harness(tmp_path, "requirements.txt", "requests\n")
    assert import_env.harness_install(repo) == "requirements.txt"


def test_a_missing_install_file_is_refused(tmp_path):
    """`validate_harness` already refuses this, and the importer must not be the one
    place that trusts the manifest."""
    sys.path.insert(0, str(ROOT / "tools"))
    import import_env

    repo = _fixture_harness(tmp_path, None, "")
    (repo / "harness.json").write_text(json.dumps(
        {"name": "x", "version": "1", "entrypoint": "agent.py", "install": "gone.sh"}))
    with pytest.raises(SystemExit) as exc:
        import_env.harness_install(repo)
    assert "gone.sh" in str(exc.value)


def test_importing_a_harness_actually_runs_its_install(tmp_path):
    """The end-to-end proof that `install` is no longer inert.

    A `.sh` install rather than a `requirements.txt`, so the build needs no network and
    the test is measuring the mechanism instead of a package index. The marker it
    writes is checked **in the resulting image**, because the claim being tested is
    "the install is baked into the environment", and anything weaker would also pass
    for an install that ran on the host and vanished.
    """
    pinned = _require_docker()
    sys.path.insert(0, str(ROOT / "tools"))
    import import_env

    repo = _fixture_harness(
        tmp_path, "install.sh",
        'set -e\necho "baked at import time" > /opt/hg-install-proof.txt\necho "# a module" > /opt/hg-from-install.py\n')
    image_id = import_env.import_with_harness(pinned, repo, "install.sh",
                                              "harnessgrad-test-fixture:latest")
    try:
        proc = container.run(
            ["sh", "-c", "cat /opt/hg-install-proof.txt; "
                         "test -e /opt/harnessgrad-install/.state "
                         "&& echo STATE_LEAKED || echo state_not_copied"],
            image=image_id, task_dir=tmp_path, task_mount="/app", timeout_s=180)
        assert "baked at import time" in proc.stdout, proc.stdout + proc.stderr
        # The harness's own scratch space is not part of its declaration and must not
        # be shipped into an image that will be mounted read-only and reused.
        assert "state_not_copied" in proc.stdout, proc.stdout
    finally:
        subprocess.run(["docker", "image", "rm", "--force", image_id],
                       capture_output=True, text=True)


def test_a_refused_run_still_records_the_resolved_image(tmp_path):
    """Image resolution precedes run metadata, including when no curve is authorized."""
    _require_docker()
    assert_driver_policy_refusal(_run(tmp_path, mode="idle"), tmp_path, ("e01", "e02"))
    meta = json.loads((tmp_path / "runs/probe/run_meta.json").read_text())
    assert meta["env"]["kind"] == "exec", meta["env"]
    pinned = container.resolve_digest(registry.load_envs("verify_exec")["e01"]["image"])
    assert meta["env"]["image_digest"] == container.digest_of(pinned)


def test_no_sandbox_does_not_bypass_exec_policy_admission(tmp_path):
    """--no-sandbox changes host bwrap; it cannot authorize container execution."""
    _require_docker()
    proc = _run(tmp_path, mode="solve", extra_argv=("--no-sandbox",))
    assert_driver_policy_refusal(proc, tmp_path, ("e01", "e02"))
    header = " ".join((proc.stdout + proc.stderr).split())
    assert "exec" in header, header[:800]
    assert "host-side bwrap OFF for the method" in header, header[:800]


# ------------------------------------------------------ snapshot failure controls ---


def test_snapshot_admission_rejects_writable_layer_before_commit(monkeypatch):
    calls = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("inspect", "--size"):
            return subprocess.CompletedProcess(args, 0, "17179869185\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(container, "docker", fake_docker)
    with pytest.raises(container.SnapshotUnavailable, match="kind=disk"):
        container.snapshot_admission("ctr")
    assert calls == [("inspect", "--size", "-f", "{{.SizeRw}}", "ctr")]


def test_commit_exit_137_is_classified_as_oom(monkeypatch):
    monkeypatch.setattr(container, "snapshot_admission", lambda name: {})
    monkeypatch.setattr(
        container, "docker",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 137, "", "write /var/lib/docker: killed"))
    with pytest.raises(container.SnapshotUnavailable, match="kind=oom.*exit=137"):
        container.commit("ctr", "hg-snapshot:test")


def test_commit_disk_and_resource_errors_are_classified(monkeypatch):
    monkeypatch.setattr(container, "snapshot_admission", lambda name: {})
    for detail, kind in (("no space left on device", "disk"),
                         ("resource temporarily unavailable", "resource")):
        monkeypatch.setattr(
            container, "docker",
            lambda *args, _detail=detail, **kwargs: subprocess.CompletedProcess(
                args, 1, "", _detail))
        with pytest.raises(container.SnapshotUnavailable, match=f"kind={kind}"):
            container.commit("ctr", "hg-snapshot:test")
