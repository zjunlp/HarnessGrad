"""Run a task's check, and read its verdict.

Split out of `eval/run/runner.py` (stage 4). This is the half of the runner that decides **what
a task's score is**, and it is deliberately the half that is hardest to change by accident:
it runs the check in the environment the task declares, copies out the report the check
wrote, parses it, and turns it into the verdict a curve point carries.

It is also where the platform's *second* signal lives -- `test_rate_of` reads the check's own
test tally, which the reward file collapses to a pass or a fail (stage 0, `d853a10`).

The seam: everything here takes a task, an environment and a finished harness run, and
returns a verdict. It does not decide *which* tasks a round measures, whether to retry, or
how per-task scores become a curve -- those stay in `eval/run/runner.py`.
"""
from __future__ import annotations

from contextlib import ExitStack

import json
import os
import subprocess
import tempfile
from pathlib import Path

import eval.containers.container as container_mod
import eval.containers.sandbox as sandbox_mod
import eval.service.services as services_mod
from eval.service.services import label
from eval.run.integrity import PLATFORM_ROOT
from eval.observe.tracing import _materialize
from eval.containers.immutable_runtime import ImmutableRuntimeUnavailable, projections, targets
from eval.policy.runtime_policy import phase_timeout
from eval.verification.isolation import declared_class, record_class

def _verifier_env(workdir: Path) -> dict:
    """A minimal environment for the check.

    Deliberately **not** `harness_env()`: the verifier has no business holding the
    agent's API key, and a grade that can call a model is a grade that can vary
    between runs.
    """
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(workdir), "LANG": os.environ.get("LANG", "C.UTF-8"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    # Native Windows Python needs SystemRoot to initialize its random source.
    # Forward only this OS prerequisite, never the agent or improver credentials.
    if os.name == "nt" and os.environ.get("SystemRoot"):
        env["SystemRoot"] = os.environ["SystemRoot"]
    return env


def _verifier_env_container() -> dict:
    """The check's environment inside a container.

    No `PATH`: the image has its own and the host's names directories that do not
    exist inside. `HOME` and `TMPDIR` point at the container's tmpfs, not at the task
    mount, because the task mount is bound **read-only** for the check -- a check that
    writes into the tree it is grading is one whose verdict depends on its own
    leftovers. `PYTHONDONTWRITEBYTECODE` is kept for the same reason.

    And, as on the host: no agent credentials, because a grade that can call a model
    is a grade that can vary between runs.
    """
    return {"HOME": "/tmp", "TMPDIR": "/tmp",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PYTHONDONTWRITEBYTECODE": "1"}

#: How much of a check's own output goes into its verdict.
#:
#: 600 was the number, and it was measured to be too small for a reason worth writing
#: down: the real task set reaches its test runner by `apt-get install`-ing it, so the
#: last 600 characters of stdout can be package-manager noise printed *after* the test
#: report -- and the report is the only part that says which check failed and why. A
#: method reads this field to decide what to change; a verdict it cannot learn from is a
#: round it cannot use. Measured on `cancel-async-tasks`: the whole detail was
#: `Setting up libcurl4 ... Processing triggers for libc-bin`, and the improver's round-2
#: edit had to guess.
VERDICT_STDOUT_TAIL = 2000
VERDICT_STDERR_TAIL = 800
VERDICT_FAILED_TESTS = 8
VERDICT_DETAIL_LIMIT = 6000


#: The egress proxy the platform's **checks** may use, if the operator configured one.
#:
#: Not for the harness, and the difference matters. A harness reaches its model through the
#: platform -- the model gateway when the endpoint is host-local -- and pushing those calls
#: through an HTTP proxy breaks them. A *check* is different: it runs after the harness has
#: stopped, calls no model, and on this host 83 of 89 Terminal-Bench checks bootstrap their
#: own test runner with `curl https://astral.sh/uv/... | sh`. When that fetch cannot leave
#: the machine, the check never runs -- and a check that never ran was recorded as a task
#: the harness failed, which is the wrong attribution and a wasted round.
#:
#: Measured on this host: `astral.sh` is unreachable directly (403 in a container, TLS
#: handshake timeout from the shell) while `pypi.org` is fine; with
#: `HG_EGRESS_PROXY=http://172.17.0.1:17890` (see `docs/egress.md`) the task's own
#: `test.sh` runs to completion and writes its `ctrf.json`. Unset, nothing about a run
#: changes.
EGRESS_PROXY_ENV = "HG_EGRESS_PROXY"

def _check_env() -> dict[str, str]:
    """What the check's process is told about how to leave the machine, if anything.

    All four spellings, because the tools disagree: `apt` reads the lowercase ones, `curl`
    prefers the uppercase, and `uv` honours both. `NO_PROXY` keeps anything local (a
    service, a package mirror on the bridge) from being pushed through the proxy.
    """
    proxy = (os.environ.get(EGRESS_PROXY_ENV) or "").strip()
    if not proxy:
        return {}
    local = "localhost,127.0.0.1,::1"
    return {"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "http_proxy": proxy,
            "https_proxy": proxy, "NO_PROXY": local, "no_proxy": local}


def _read_json(path: Path) -> dict | None:
    """Parse a file another program wrote. `None` on anything unexpected."""
    try:
        loaded = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _clear_verdict_file(path: Path) -> None:
    """Remove a stale host verdict without following a subject-created symlink."""
    if path.is_symlink() or path.is_file():
        path.unlink()


def _verdict_parent_is_safe(path: Path, workdir: Path) -> bool:
    """Ensure a host verdict path cannot escape through a pre-existing symlink."""
    try:
        relative = path.relative_to(workdir)
    except ValueError:
        return False
    current = workdir
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            return False
    return True

def test_rate_of(verdict: dict | None) -> float | None:
    """How much of the check's own test suite the artifact satisfied, or `None`.

    **A second signal beside `score`, and the finer one.** A `command` check usually
    reports "4 passed 1 failed" and the reward file collapses that to 0. Measured on this
    platform: `large-scale-text-editing` passed 4 of 5 tests for a binary 0.0, and a harness
    edit took it to 3 of 5 -- also a binary 0.0. The curve said the edit changed nothing
    while the artifact got *worse*. Ten all-or-nothing tasks have a resolution of 0.1 and,
    measured here, a noise band of about +/-0.1: the instrument cannot see the effect it
    exists to measure, and this number was already parsed and thrown away
    (`_test_report_failures` has returned it since 2026-10-04; `_save_verdicts` dropped it).

    `None` is not zero. It means this check reports no per-test tally -- most `command`
    checks outside pytest, and every check without an `evidence_file`. Averaging those in
    as failures would invent a number the check never produced.

    The denominator is `passed + failed`: tests that *ran*. A skipped test was not
    measured, and counting it as a failure would blame the artifact for the check's own
    skip. `summary["tests"]` is deliberately not used -- it counts skipped and pending
    tests, so it answers a different question.
    """
    tests = ((verdict or {}).get("tests"))
    if not isinstance(tests, dict):
        return None
    passed, failed = tests.get("passed"), tests.get("failed")
    if not isinstance(passed, int) or not isinstance(failed, int):
        return None
    ran = passed + failed
    return (passed / ran) if ran > 0 else None
def _test_report_failures(report: dict | None) -> tuple[list[str], str, dict]:
    """Failing tests from a CTRF report, a one-line summary, and the counts as numbers.

    A check may write a machine-readable report beside its reward file (the real task
    set passes `--ctrf /logs/verifier/ctrf.json` to pytest). That report is the
    difference between `reward 0` and
    `test_out_html_bypasses_filter: the XSS bypass failed`, and only the second is
    something a method can act on.

    **The counts come back as numbers too, not only as a sentence.** Measured on the local
    model (2026-10-04): `large-scale-text-editing` failed `test_input_equiv_expected` in
    **12 of 12** measurements while passing the other four -- a permanent floor, visible
    only to someone who parses "4 passed 1 failed" out of a detail string. Under an
    all-or-nothing reward that task contributes a constant 0, so a task set containing it
    has one fewer usable task than it looks like it has. `verdict["tests"]` is that fact in
    a form the platform can act on (task selection) and a reader can check.

    Parsed defensively: it is another project's format, and a report this cannot read
    must not take the verdict down with it. An empty list is a fact too -- it means the
    report was there and nothing failed.
    """
    results = (report or {}).get("results")
    if not isinstance(results, dict):
        return [], "", {}
    summary = results.get("summary") if isinstance(results.get("summary"), dict) else {}
    counts = " ".join(
        f"{summary[key]} {name}" for key, name in
        (("passed", "passed"), ("failed", "failed"), ("skipped", "skipped"))
        if isinstance(summary.get(key), int) and summary[key])
    numbers = {key: summary[key]
               for key in ("tests", "passed", "failed", "skipped", "pending", "other")
               if isinstance(summary.get(key), int)}
    failures: list[str] = []
    failed_names: list[str] = []
    tests = results.get("tests")
    for test in tests if isinstance(tests, list) else []:
        if not isinstance(test, dict) or test.get("status") != "failed":
            continue
        name = str(test.get("name") or "?")
        failed_names.append(name)
        # The assertion, not the summary. `pytest-json-ctrf` fills `message` with
        # "The test failed in the call phase due to an assertion error" -- true, and
        # useless to a method deciding what to change. The assertion itself is in
        # `trace`, on the `E ` lines pytest prints.
        assertion = ""
        for line in str(test.get("trace") or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("E ") and len(stripped) > 2:
                assertion = " ".join(stripped[2:].split())
        if not assertion:
            lines = str(test.get("message") or "").splitlines()
            assertion = " ".join(lines[0].split()) if lines else ""
        failures.append(f"{name}: {assertion[:240]}" if assertion else name)
    if len(failures) > VERDICT_FAILED_TESTS:
        rest = len(failures) - VERDICT_FAILED_TESTS
        failures = failures[:VERDICT_FAILED_TESTS] + [f"... and {rest} more"]
    if failed_names:
        numbers["failed_names"] = failed_names
    return failures, counts, numbers
def _check_output_detail(out: str, err: str) -> str:
    """Both of the check's streams, labelled and bounded.

    `stderr` used to be dropped entirely, which is where a check that *could not run*
    says so: measured on `break-filter-js-from-html`, `uvx: command not found` went to
    stderr while stdout held apt's progress, so the verdict read as a failed task rather
    than a check that never tested anything.
    """
    parts = []
    if out and out.strip():
        parts.append(f"stdout {out.strip()[-VERDICT_STDOUT_TAIL:]}")
    if err and err.strip():
        parts.append(f"stderr {err.strip()[-VERDICT_STDERR_TAIL:]}")
    return " | ".join(parts)
def _command_exit_failure(spec, code, out, err):
    """Opt-in command completion check, before trusting any scorer output file."""
    if not (spec.get('result_contract') or {}).get('require_exit_zero') or code == 0:
        return None
    reason = f'verifier command exited {code}; result_contract requires exit 0'
    return {'kind': 'command', 'passed': False, 'score': None,
            'outcome': 'unmeasured', 'unmeasured': reason, 'exit_code': code,
            'detail': (reason + ' | ' + _check_output_detail(out, err))[:VERDICT_DETAIL_LIMIT]}


def _verify_container_state(snapshot: str, verifier, *, task_mount: str,
                            staging: Path, timeout_s: int, run_id: str,
                            task_id: str, network: str = "none",
                            declared_network: str = "none",
                            limits: dict | None = None,
                            expected_snapshot: str | None = None) -> dict:
    if "runtime_paths" in (verifier or {}):
        return record_class({"kind": "command", "passed": False, "score": None,
                             "outcome": "unmeasured",
                             "unmeasured": "verifier runtime_paths requires sandboxed files command verification"},
                            verifier, "unknown")
    if declared_class(verifier) not in ("unknown", "snapshot-service"):
        return record_class({"kind": "command", "passed": False, "score": None,
                             "unmeasured": "scorer declaration cannot run on actor snapshot"},
                            verifier, "unknown")
    try:
        container_mod.require_snapshot_identity(snapshot, expected_snapshot)
        verdict = _verify_container_state_inner(
            snapshot, verifier, task_mount=task_mount, staging=staging, timeout_s=timeout_s,
            run_id=run_id, task_id=task_id, network=network,
            declared_network=declared_network, limits=limits,
            expected_snapshot=expected_snapshot)
    except container_mod.SnapshotUnavailable as exc:
        verdict = {"kind": "command", "passed": False, "score": None,
                   "outcome": "unmeasured", "unmeasured": str(exc)}
    return record_class(verdict, verifier, "snapshot-service")


def _verify_container_state_inner(snapshot: str, verifier, *, task_mount: str,
                                  staging: Path, timeout_s: int, run_id: str,
                                  task_id: str, network: str = "none",
                                  declared_network: str = "none",
                                  limits: dict | None = None,
                                  expected_snapshot: str) -> dict:
    """Steps 5-9: a fresh container from the snapshot, the check's inputs copied in
    afterwards, and the score read from the file the check writes.

    The container is fresh, so nothing the harness left running is present. The inputs go
    in at step 6 and the reward directory at step 7, both after the harness stopped --
    which is why this is a re-materialization and not a snapshot of the harness's view.

    A missing reward file is unmeasured: the check produced no result. A present but
    unparseable value is a malformed result and keeps the ordinary failed verdict.
    """
    spec = verifier or {"kind": "answer", "expected": None}
    kind = spec.get("kind", "answer")
    timeout_s = phase_timeout(timeout_s, spec.get("timeout_s"))
    name = label(f"hg-verify-{run_id}-{task_id}")
    created = False
    startup = ExitStack()
    verdict_dir: Path | None = None
    try:
        reward_file = spec.get("reward_file")
        if reward_file:
            # The snapshot is harness-owned state.  Reward/evidence files must be
            # created in a fresh directory owned by the platform, then mounted only
            # for this check; otherwise a harness can pre-seed a passing verdict.
            verdict_dir = staging / "verdict"
            verdict_dir.mkdir(parents=True, exist_ok=True)
        extra_mounts = ()
        if verdict_dir is not None:
            extra_mounts = ("--mount",
                            f"type=bind,src={verdict_dir},dst={Path(reward_file).parent}")
        # The environment's network, not `none`. Measured: the real task set installs
        # its own test dependencies at verification time -- all 89 `test.sh` files
        # `apt-get install` or `pip install` before running anything -- so a check given
        # no network cannot run at all, and reports a reward of 0 that looks exactly like
        # a harness that failed the task. The oracle control is what found this.
        entrypoint, idle_mounts = startup.enter_context(container_mod.snapshot_startup())
        container_mod.create(name, image=snapshot, limits=limits or {},
                            network=network, extra_mounts=extra_mounts + idle_mounts,
                            startup_entrypoint=entrypoint,
                            run_id=run_id or "", task_id=task_id or "")
        created = True
        container_mod.require_snapshot_container(name, expected_snapshot)
        # Match the actor's declared-network attachment in
        # eval/containers/container_state.py:111-112: its internal model network alone
        # hid declared bridge egress from the verifier. Keep that network, adding bridge
        # only when declared (allow_internet maps to it in data/datasets/terminal_bench.py:35).
        if declared_network == "bridge" and network != "bridge":
            container_mod.network_connect(name, "bridge")

        _copy_verifier_inputs(name, spec, staging, task_mount)

        container_mod.start(name)

        # Step 7's directory already exists via the fresh bind. Do not exec an
        # actor-owned `mkdir` before the checker: it could mutate committed inputs.
        argv = [str(a) for a in spec.get("argv") or []]
        if not argv:
            return {"kind": kind, "passed": False,
                    "detail": "a command verifier with state='container' needs `argv`"}
        try:
            proc = container_mod.exec_in(name, argv,
                                         cwd=spec.get("cwd") or task_mount,
                                         env=_check_env() or None,
                                         timeout_s=timeout_s)
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            detail = f"the verifier timed out after {timeout_s}s"
            return {"kind": kind, "passed": False,
                    "outcome": "timeout", "unmeasured": detail, "detail": detail}

        failure = _command_exit_failure(spec, code, out, err)
        if failure is not None:
            return failure

        # Step 9. `reward_file` first: the real task set writes 1/0 into a file and exits
        # 0 either way, so the exit code is not a fallback, it is noise.
        if reward_file:
            got = staging / "reward.txt"
            if not container_mod.cp_out(name, reward_file, got):
                detail = (f"the check wrote no {reward_file}; exit {code}, "
                          + (_check_output_detail(out, err)
                             or "and printed nothing at all"))
                return {"kind": kind, "passed": False,
                        "unmeasured": f"{detail}; this task was not measured",
                        "detail": detail}
            raw = got.read_text().strip()
            try:
                score = float(raw)
            except ValueError:
                return {"kind": kind, "passed": False,
                        "detail": f"{reward_file} held {raw!r}, which is not a number"}
            # The check's own output goes in the detail, because with a `reward_file`
            # the exit code says nothing and a bare "reward 0" leaves a reader with no
            # way to tell a wrong answer from a check that could not run at all. It
            # cost a full debugging round to learn that: the oracle solved the task,
            # the reward was still 0, and the reason was only in this output.
            #
            # **The report comes first, and the streams after it.** A method reads the
            # detail to decide what to change, so the part that names the failing check
            # has to survive the truncation; raw output is evidence of last resort.
            detail = [f"reward {score:g} from {reward_file} (exit {code})"]
            #: Set when the platform can *prove* the check never tested anything: it was
            #: supposed to leave a machine-readable report and did not. The caller turns
            #: this into an `invalid` task rather than a zero -- a reward of 0 written by a
            #: check whose tests never ran is not a failed harness (measured: `uvx: command
            #: not found` inside the container, before this host had an egress proxy).
            unmeasured = ""
            #: The check's own tally, empty until a report is read. **Initialised, not
            #: conditional**: this is read after the branch below, so a check that declares
            #: no `evidence_file` -- most `command` verifiers, and every container-state
            #: task in the test set -- hit `UnboundLocalError` at the verdict instead of
            #: returning a score. Caught by the full suite, not by the file that exercises
            #: the path, which is the argument for running the whole suite.
            numbers: dict = {}
            report_path = spec.get("evidence_file")
            if report_path:
                got_report = staging / Path(str(report_path)).name
                if container_mod.cp_out(name, str(report_path), got_report):
                    failures, counts, numbers = _test_report_failures(
                        _read_json(got_report))
                    detail.append(f"check_report {report_path}"
                                  + (f" ({counts})" if counts else ""))
                    detail.extend(f"FAILED {line}" for line in failures)
                    if not failures:
                        detail.append("check_report lists no failing test")
                else:
                    unmeasured = (f"the check wrote no {report_path}: its tests did not "
                                  f"run, so this task was not measured")
                    detail.append(unmeasured)
            detail.append(_check_output_detail(out, err))
            verdict = {"kind": kind, "passed": bool(score), "score": score,
                       "detail": " | ".join(p for p in detail if p)[:VERDICT_DETAIL_LIMIT]}
            # The check's own tally, structured. `passed` is `bool(score)` and the score
            # is what the task is worth; this is *how much* of it the artifact satisfied,
            # which the reward file collapses. Present only when the check wrote a report
            # -- absent is "this check does not report", not "zero tests passed".
            if numbers:
                verdict["tests"] = numbers
            if unmeasured:
                verdict["unmeasured"] = unmeasured
            return verdict

        pass_when = spec.get("pass_when", "exit0")
        passed = code == 0
        detail = f"exit {code}"
        if pass_when == "exit0_stdout":
            want = str(spec.get("stdout", ""))
            passed = passed and out.strip() == want.strip()
            detail = f"exit {code}, stdout {out.strip()[:80]!r} vs {want[:80]!r}"
        if not passed and out.strip():
            detail += f" | {out.strip()[-200:]}"
        return {"kind": kind, "passed": passed, "detail": detail}
    finally:
        if created:
            container_mod.remove_container(name)
        startup.close()
def _input_source(entry: dict, staging: Path) -> Path:
    """The host path behind a verifier input, for `docker cp`."""
    if "content" in entry and entry["content"] is not None:
        written = staging / f"input-{abs(hash(entry.get('dst') or entry.get('path')))}"
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(str(entry["content"]))
        return written
    source = Path(str(entry["from"]))
    if not source.is_absolute():
        source = PLATFORM_ROOT / source
    if not source.exists():
        raise ValueError(f"verifier input source {source} does not exist")
    return source
def _copy_verifier_inputs(name: str, spec: dict, staging: Path, task_mount: str) -> None:
    """Step 6: put a check's inputs where the check expects them.

    `dst` is a container path; `path` remains the original relative-to-the-workdir form, so
    nothing that worked before changes. Split out of `_verify_container_state` so the
    *decision* below can be tested without a docker daemon -- the decision is what was wrong.
    """
    for entry in spec.get("inputs") or []:
        if entry.get("dst"):
            # **`src/.`, not `src`.** `docker cp <dir> <ctr>:<dst>` *nests* when `dst`
            # already exists, and whether it exists is not the platform's to assume: the
            # container is built from the snapshot, which is the **harness's** filesystem.
            # Measured (`break-filter-js-from-html`): the harness ran
            # `mkdir -p /tests && cp /app/filter.py /tests/filter.py` while probing the
            # task's filter -- its own business -- so the check's inputs landed at
            # `/tests/tests/test.sh`, the check died with
            # `bash: /tests/test.sh: No such file or directory`, and the platform recorded
            # that as a task the *harness* failed. In the same round a harness that
            # happened not to touch `/tests` verified normally, which is what made this
            # look like a flaky task rather than a staging bug.
            #
            # `src/.` copies the *contents* into the destination and is correct whether or
            # not it exists (measured both ways on a real container). A file input keeps
            # the plain form: `docker cp <file> <ctr>:<dst>` overwrites rather than nests.
            source = _input_source(entry, staging)
            container_mod.cp_in(name, source, str(entry["dst"]),
                                contents=source.is_dir())
        else:
            _copy_relative_input(name, entry, staging, task_mount)
def _copy_relative_input(name: str, entry: dict, staging: Path,
                         task_mount: str) -> None:
    """An input with `path` (not `dst`): materialize it into the container's workdir."""
    rel = str(entry["path"])
    target = staging / "rel"
    _materialize(target, [entry], PLATFORM_ROOT)
    container_mod.cp_in(name, target / rel, f"{task_mount}/{rel}")


def _verifier_runtime_paths(workdir: Path, spec: dict, options: dict, bindings) -> tuple[Path, ...]:
    """Admit explicit host scorer dependencies without changing legacy launches."""
    contract = spec.get("result_contract")
    if (not options.get("sandbox") or spec.get("kind") != "command"
            or (options.get("env") or {}).get("kind", "files") != "files"
            or options.get("running") is not None or options.get("service_specs")
            or options.get("native_runtime") is not None
            or "native_runtime" in spec or "container_runtime" in spec
            or declared_class(spec) not in ("unknown", "actor-workdir")
            or not isinstance(contract, dict) or contract.get("require_exit_zero") is not True):
        raise ValueError("requires sandboxed files command verification with require_exit_zero")
    raw = spec["runtime_paths"]
    if not isinstance(raw, list) or any(not isinstance(p, str) or not p for p in raw):
        raise ValueError("must be a list of absolute directory paths")
    work_root = Path(os.environ.get("HARNESSGRAD_WORK_ROOT")
                     or (workdir if bindings else workdir.parent.parent))
    hidden = sandbox_mod.hidden_trees(work_root, PLATFORM_ROOT)
    task = workdir.resolve()
    paths = []
    for value in raw:
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or path == Path("/"):
            raise ValueError(f"invalid runtime directory: {value}")
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_dir():
            raise ValueError(f"runtime must be an existing non-symlink directory: {value}")
        if path == task or task in path.parents or path in task.parents:
            raise ValueError(f"runtime overlaps writable task storage: {value}")
        if any(path == tree or tree in path.parents for tree in hidden):
            raise ValueError(f"runtime would be hidden by sandbox isolation: {value}")
        if path not in paths:
            paths.append(path)
    argv = spec.get("argv")
    if not isinstance(argv, (list, tuple)) or not argv or not isinstance(argv[0], str):
        raise ValueError("requires an explicit absolute executable")
    executable = Path(argv[0])
    if (not executable.is_absolute() or ".." in executable.parts
            or not executable.is_file() or not os.access(executable, os.X_OK)):
        raise ValueError(f"executable is absent, not executable or not absolute: {argv[0]}")
    visible = (*paths, *(Path(p) for p in sandbox_mod.RUNTIME))
    for target in (executable, executable.resolve(strict=True)):
        if (any(target == tree or tree in target.parents for tree in hidden)
                or not any(target == root or root in target.parents for root in visible)):
            raise ValueError(f"executable or symlink target is outside declared runtimes: {target}")
    return tuple(paths)


def _verify(workdir: Path, verifier: dict | None, answer: str, **options) -> dict:
    """Fail closed on runtime bind errors, then dispatch the existing scorer routes."""
    try:
        bindings = (*options.get("immutable_runtimes", ()),
                    *projections((verifier or {}).get("inputs")))
        if bindings:
            if ((options.get("env") or {}).get("kind", "files") != "exec"
                    and not options.get("sandbox")):
                raise ImmutableRuntimeUnavailable("immutable runtimes require a read-only sandbox bind")
            if declared_class(verifier or {}) == "pristine-offline":
                raise ImmutableRuntimeUnavailable("this verification boundary cannot express immutable runtime binds")
            targets(workdir, bindings)
        if "runtime_paths" in (verifier or {}):
            try:
                options["runtime_paths"] = _verifier_runtime_paths(workdir, verifier, options, bindings)
            except (OSError, ValueError, RuntimeError) as exc:
                raise ImmutableRuntimeUnavailable(f"verifier runtime_paths: {exc}") from exc
        from eval.verification.result_contract import apply, validate as validate_result
        contract = (verifier or {}).get("result_contract")
        judge_spec = (verifier or {}).get("judge")
        if contract is not None:
            try:
                validate_result(contract)
                if contract.get('require_exit_zero') and verifier.get('kind') != 'command':
                    raise ValueError('require_exit_zero requires command verification')
            except ValueError as exc:
                return {"kind": "command", "passed": False, "score": None,
                        "outcome": "unmeasured", "unmeasured": str(exc)}
        if judge_spec is None:
            return apply(_verify_dispatch(workdir, verifier, answer, **options), contract)
        from eval.service.judge import Judge, JudgeUnavailable
        try:
            if (not options.get("sandbox") or (options.get("env") or {}).get("kind", "files") != "files"
                    or verifier.get("kind") != "command" or verifier.get("native_runtime")
                    or declared_class(verifier) not in ("unknown", "actor-workdir") or contract is None):
                raise JudgeUnavailable("judge requires sandboxed files command verification and result_contract v1")
            with Judge(judge_spec) as judge:
                verdict = _verify_dispatch(workdir, verifier, answer, judge=judge, **options)
            report = judge.report()
            verdict["judge"] = report
            if report["faults"]:
                verdict.update(score=None, passed=False, outcome="unmeasured",
                               unmeasured="judge call or transport failed")
            return apply(verdict, contract)
        except (JudgeUnavailable, ValueError, OSError) as exc:
            return {"kind": "command", "passed": False, "score": None,
                    "outcome": "unmeasured", "unmeasured": str(exc)}
    except ImmutableRuntimeUnavailable as exc:
        return {"kind": "command", "passed": False, "score": None,
                "outcome": "unmeasured", "unmeasured": str(exc),
                "immutable_runtime_error": True}


def _verify_dispatch(workdir: Path, verifier: dict | None, answer: str, **options) -> dict:
    """Bind a supported host scorer's native pin to execution and verdict."""
    spec = verifier or {}
    if "container_runtime" in spec:
        from eval.verification.observed import verify
        return verify(workdir, spec, answer, **options)
    if "native_runtime" not in spec:
        return _verify_existing(workdir, verifier, answer, **options)
    from eval.verification.native_runtime import admit, NativeRuntimeUnavailable, SUPPORTED_DATASETS, CHECK_PATHS
    binding = spec["native_runtime"]
    receipt = {"verified": False, "sha256": binding.get("sha256") if isinstance(binding, dict) else None}
    try:
        if not options.get("sandbox"):
            raise NativeRuntimeUnavailable("native runtime requires sandbox=True")
        if not isinstance(binding, dict) or binding.get('dataset') not in SUPPORTED_DATASETS:
            raise NativeRuntimeUnavailable("native runtime dataset is outside supported host scope")
        if ((options.get("env") or {}).get("kind", "files") != "files"
                or options.get("service_specs") or options.get("running")
                or spec.get("kind") != "command"
                or declared_class(spec) not in ("unknown", "actor-workdir")
                or spec.get("cwd") not in (None, ".")
                or spec.get("reward_file") != "/verifier/reward.txt"
                or spec.get("evidence_file") != "/verifier/evidence.json"
                or spec.get("argv", [])[:2] != ["python3", CHECK_PATHS[binding['dataset']]]):
            raise NativeRuntimeUnavailable("native runtime binding requires the supported host check route")
        with admit(binding) as runtime:
            receipt = runtime.receipt
            verdict = _verify_existing(workdir, verifier, answer,
                                       native_runtime=runtime, **options)
            return {**verdict, "native_runtime": receipt}
    except NativeRuntimeUnavailable as exc:
        return record_class({"kind": "command", "passed": False, "score": None,
                             "outcome": "unmeasured", "unmeasured": str(exc),
                             "native_runtime": {**receipt, "verified": False}}, spec, "actor-workdir")


def _verify_existing(workdir: Path, verifier: dict | None, answer: str,
            *, sandbox: bool, timeout_s: int,
            env: dict | None = None,
            service_specs: list[dict] | None = None,
            running=None, task_id: str | None = None,
            run_id: str | None = None, trial_id: str | None = None,
            native_runtime=None, immutable_runtimes=(), judge=None, runtime_paths=()) -> dict:
    """Run a verifier with platform-owned output storage for exec reward checks."""
    spec = verifier or {"kind": "answer", "expected": None}
    env_spec = env or {"kind": "files"}
    timeout_s = phase_timeout(timeout_s, spec.get("timeout_s"), env_spec.get("verify_timeout_s"))
    declared = declared_class(spec)
    actual = ("in-process" if spec.get("kind", "answer") == "answer" else
              "snapshot-service" if running is not None or service_specs else "actor-workdir")
    if declared == "pristine-offline":
        if (spec.get('result_contract') or {}).get('require_exit_zero'):
            return record_class({'kind': 'command', 'passed': False, 'score': None,
                                 'outcome': 'unmeasured',
                                 'unmeasured': 'require_exit_zero is unsupported for pristine-offline'},
                                spec, 'unknown')
        if actual != "actor-workdir" or spec.get("reward_file") or spec.get("pass_when", "exit0") != "exit0":
            return record_class({"kind": spec.get("kind"), "passed": False, "score": None,
                                 "unmeasured": "pristine scorer requires artifact command with exit0 protocol"},
                                spec, "unknown")
        from eval.verification.pristine import verify, enforce_verdict_binding
        verdict = verify(workdir, spec, timeout_s=timeout_s, task_id=task_id,
                         run_id=run_id, trial_id=trial_id)
        return enforce_verdict_binding(verdict, task_id=task_id, run_id=run_id, trial_id=trial_id)
    if declared not in ("unknown", actual):
        return record_class({"kind": spec.get("kind"), "passed": False, "score": None,
                             "unmeasured": "scorer declaration incompatible with runtime route"},
                            spec, "unknown")
    binding = spec.get("scorer_input")
    if binding is not None and (not isinstance(binding, dict) or not task_id
                                or binding.get("task_id") != task_id):
        return record_class({"kind": spec.get("kind"), "passed": False, "score": None,
                             "unmeasured": "scorer input does not belong to selected task"},
                            spec, actual)
    if spec.get("kind") == "command" and spec.get("reward_file") \
            and env_spec.get("kind") == "exec":
        with tempfile.TemporaryDirectory(prefix="hg-verdict-",
                                          dir=str(workdir.parent)) as verdict_dir:
            verdict = _verify_inner(workdir, verifier, answer, sandbox=sandbox,
                                 timeout_s=timeout_s, env=env,
                                 service_specs=service_specs, running=running,
                                 verdict_dir=Path(verdict_dir), native_runtime=native_runtime,
                                 immutable_runtimes=immutable_runtimes, judge=judge)
            return record_class(_enforce_scorer_input(verdict, binding), spec, actual)
    verdict = _verify_inner(workdir, verifier, answer, sandbox=sandbox,
                         timeout_s=timeout_s, env=env,
                         service_specs=service_specs, running=running, native_runtime=native_runtime,
                         immutable_runtimes=immutable_runtimes, judge=judge,
                         **({"runtime_paths": runtime_paths} if "runtime_paths" in spec else {}))
    return record_class(_enforce_scorer_input(verdict, binding), spec, actual)


def _enforce_scorer_input(verdict: dict, binding: dict | None) -> dict:
    """Consume a check's input receipt only against the independently selected spec.

    These are consistency checks under trusted spec/check custody, not authentication
    of arbitrary scorer JSON. Other verifier routes retain their existing contracts.
    """
    if binding is None:
        return verdict
    evidence = verdict.get("evidence")
    receipt = evidence.get("scorer_input") if isinstance(evidence, dict) else None
    if (isinstance(receipt, dict) and receipt.get("verified") is True
            and all(receipt.get(k) == binding.get(k) for k in ("task_id", "path", "sha256"))
            and receipt.get("expected_sha256") == binding.get("sha256")
            and ("files" not in binding or receipt.get("files") == binding["files"])):
        return verdict
    return {**verdict, "passed": False, "score": None,
            "unmeasured": verdict.get("unmeasured") or "scorer input receipt mismatch or missing"}


def _verify_inner(workdir: Path, verifier: dict | None, answer: str,
            *, sandbox: bool, timeout_s: int,
            env: dict | None = None,
            service_specs: list[dict] | None = None,
            running=None, verdict_dir: Path | None = None, native_runtime=None,
            immutable_runtimes=(), judge=None, runtime_paths=()) -> dict:
    """Decide whether the task was completed. Returns {kind, passed, detail}.

    `answer` is the legacy path and stays byte-for-byte what it was: stripped,
    lowercased, compared to `expected`. A dataset that declares only `SCORABLE`
    reaches exactly this branch, which is what makes adding verifiers a change that
    did not touch any existing dataset's numbers.
    """
    spec = verifier or {"kind": "answer", "expected": None}
    kind = spec.get("kind", "answer")
    env_spec = env or {"kind": "files"}
    is_exec = env_spec.get("kind") == "exec"
    #: A `files` check that declares a `reward_file`. The file channels used to be
    #: reachable only from a container snapshot (`_verify_container_state`); this is the
    #: host spelling of the same contract, added so a no-Docker dataset can keep its
    #: evidence. See the block below the command run for what it does.
    host_reward = bool(spec.get("reward_file"))
    task_mount = env_spec.get("workdir") or container_mod.DEFAULT_WORKDIR
    service_specs = list(service_specs or [])

    if kind == "answer":
        expected = spec.get("expected")
        passed = bool(expected is not None
                      and answer.strip().lower() == str(expected).strip().lower())
        return {"kind": "answer", "passed": passed,
                "detail": f"answer {answer.strip()[:80]!r} vs {str(expected)[:80]!r}"}

    if kind != "command":
        raise ValueError(f"unknown verifier kind {kind!r}")

    # Re-materialize the check's inputs, overwriting whatever the harness left there.
    # Immutable runtimes are skipped by _materialize: the harness could never write
    # them, which is stronger anti-tamper protection than restoring mutable inputs.
    immutable_runtimes = (*immutable_runtimes, *projections(spec.get("inputs")))
    try:
        _materialize(workdir, spec.get("inputs"), PLATFORM_ROOT)
    except ImmutableRuntimeUnavailable:
        raise
    except (ValueError, OSError) as exc:
        return {"kind": "command", "passed": False,
                "detail": f"could not stage the verifier's inputs: {exc}"}

    argv = [str(a) for a in spec["argv"]]
    cwd = workdir / spec["cwd"] if spec.get("cwd") else workdir

    # A host check that declares a `reward_file` is given its verdict directory back
    # writable (`writable_paths` below), even though verification freezes the rest of the
    # task tree. Created here rather than by the check because bwrap binds a path that
    # must already exist, and because the platform -- not the subject -- decides where a
    # verdict may be written.
    reward_rel = str(spec["reward_file"]).lstrip("/") if host_reward else ""
    exec_verdict_dir = verdict_dir if (host_reward and is_exec) else None
    reward_dir = (exec_verdict_dir if is_exec else workdir / Path(reward_rel).parent) if host_reward else None
    host_reward_path = (workdir / reward_rel) if (host_reward and not is_exec) else None
    host_evidence_path = None
    if host_reward and not is_exec and spec.get("evidence_file"):
        host_evidence_path = workdir / str(spec["evidence_file"]).lstrip("/")
    if host_reward and not is_exec:
        if (not _verdict_parent_is_safe(host_reward_path, workdir)
                or (host_evidence_path is not None
                    and not _verdict_parent_is_safe(host_evidence_path, workdir))):
            return {"kind": kind, "passed": False, "score": 0.0,
                    "detail": "verdict path uses a subject-controlled symlink"}
        _clear_verdict_file(host_reward_path)
        if host_evidence_path is not None:
            _clear_verdict_file(host_evidence_path)
    if reward_dir is not None:
        reward_dir.mkdir(parents=True, exist_ok=True)

    try:
        if native_runtime is not None:
            proc = native_runtime.run(argv, workdir=workdir, reward_dir=reward_dir,
                                      timeout_s=timeout_s,
                                      **({"immutable_runtimes": immutable_runtimes} if immutable_runtimes else {}))
            if proc.returncode and not host_reward_path.is_file():
                from eval.verification.native_runtime import NativeRuntimeUnavailable
                raise NativeRuntimeUnavailable("native runtime check did not produce a reward: "
                                               + _check_output_detail(proc.stdout, proc.stderr))
        elif is_exec:
            # A **fresh** container from the same image, sharing only the task bind
            # mount -- see `eval/container.run` for why the check does not reuse the
            # harness's container. Its `cwd` and its `argv` are container paths, and
            # it is handed no host environment at all.
            #
            # With services, the check **joins the task's internal network and they are
            # still up** (INTERFACE.md §2.5.7 step 6): §2.2 already allows a check that
            # needs a database in a particular state, and a check that needs to ask the
            # mock API what it was sent has the same shape. `running` being None here
            # would be a caller bug -- a service task whose check silently could not
            # reach its service would fail the task for a reason nothing records.
            verifier_mounts = list(services_mod.mounts_for(
                    "verifier", service_specs,
                    running.recordings if running else {}))
            if exec_verdict_dir is not None:
                verifier_mounts += ["--mount",
                                    f"type=bind,src={exec_verdict_dir},dst={Path(spec['reward_file']).parent}"]
            proc = container_mod.run(
                argv, image=spec.get("image") or env_spec["image"], task_dir=workdir,
                task_mount=task_mount, cwd=container_mod.to_container(
                    cwd, workdir, task_mount),
                env=_verifier_env_container(),
                timeout_s=timeout_s,
                network=(running.network if running is not None
                         else env_spec.get("network", "none")),
                task_dir_writable=False,
                immutable_runtimes=immutable_runtimes,
                extra_mounts=tuple(verifier_mounts))
        elif sandbox:
            # A host check whose verdict is a file still runs sandboxed: `writable_paths`
            # re-opens exactly its verdict directory on top of the read-only task tree,
            # so the reward-file contract does not require switching the namespace off.
            # (An unsandboxed check runs with the harness's workdir as `sys.path[0]`,
            # which is a route from the subject into a process that can read the
            # platform -- the failure this module exists to prevent.)
            work_root = Path(os.environ.get("HARNESSGRAD_WORK_ROOT")
                             or (workdir if immutable_runtimes else workdir.parent.parent))
            # The namespace freezes the task tree and gives the check a private tmpfs at
            # `/tmp`, so HOME/TMPDIR point **there** and not at the workdir. Measured on
            # ALFWorld: a check whose toolchain writes `$HOME/.cache` against a read-only
            # workdir dies with `[Errno 30] Read-only file system` before it can grade
            # anything, and the failure would have been recorded as a harness failure.
            check_env = _verifier_env(workdir)
            check_env.update({"HOME": "/tmp", "TMPDIR": "/tmp"})
            judge_options = {}
            if judge is not None:
                check_env["HG_JUDGE_SOCKET"] = "/run/harnessgrad-tool/tool.sock"
                judge_options = {"network": "none", "tool_socket": judge.path}
            proc = sandbox_mod.run(argv, platform=PLATFORM_ROOT, work_root=work_root,
                                   task_dir=workdir, env=check_env,
                                   timeout_s=timeout_s,
                                   cwd=cwd, repo=workdir, task_dir_writable=False,
                                   immutable_runtimes=immutable_runtimes,
                                   writable_paths=(reward_dir,) if reward_dir else (), **judge_options,
                                   **({"runtime_paths": runtime_paths} if "runtime_paths" in spec else {}))
        else:
            proc = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                                  timeout=timeout_s, cwd=cwd,
                                  env=_verifier_env(workdir))
        code, out = proc.returncode, proc.stdout
    except FileNotFoundError as exc:
        return {"kind": "command", "passed": False,
                "detail": f"the verifier command does not exist: {exc}"}
    except subprocess.TimeoutExpired:
        detail = f"the verifier timed out after {timeout_s}s"
        return {"kind": "command", "passed": False,
                "outcome": "timeout", "unmeasured": detail, "detail": detail}

    failure = _command_exit_failure(spec, code, out, proc.stderr)
    if failure is not None:
        return failure

    # ---- a host (`files`) check's reward and evidence files ------------------
    #
    # The absolute paths in `reward_file`/`evidence_file` are the container contract.
    # A `files` environment has no container, so the path is resolved against the task
    # workdir -- which *is* that environment's root -- and the check's own `argv` is
    # given the same location in the relative spelling its working directory
    # understands. `workdir / "verifier/reward.txt"` is therefore where both sides meet.
    #
    # Before this, the host branch read only the exit code, so a dataset that could not
    # use Docker had no way to keep the per-item evidence the contract asks for: the
    # run record would carry a bare `exit 1` and nothing a method could learn from.
    # **No existing dataset changes**: no dataset declared `reward_file` on a non-exec
    # environment before ALFWorld (measured 2026-10-08).
    if host_reward:
        got = ((exec_verdict_dir / Path(reward_rel).name)
               if exec_verdict_dir is not None else host_reward_path)
        evidence_path = spec.get("evidence_file")
        evidence_file = ((exec_verdict_dir / Path(str(evidence_path)).name)
                         if exec_verdict_dir is not None and evidence_path
                         else (None if not evidence_path
                               else host_evidence_path))
        evidence = (None if evidence_file is None or evidence_file.is_symlink()
                    else _read_json(evidence_file))

        if got is None or got.is_symlink() or not got.is_file():
            # No reward file: the check did not reach a publishable verdict. **That is an
            # infrastructure outcome and never a zero.**
            #
            # This branch used to publish `score: 0.0` and add `unmeasured` only when the
            # evidence said `measured: false`, and its comment claimed it drew "the same
            # distinction the container path draws". Both were wrong. The container path
            # draws that line by returning a verdict with **no score at all**
            # (`_verify_container_state_inner`, the `cp_out` failure above), and a missing
            # reward file says nothing about the artifact: a real check can exit 0, print
            # `reward 1`, write evidence with `measured: true, score: 1.0`, and lose only
            # the reward file. Measured 2026-10-15 by a synthetic reward-transport fault
            # injection: that case produced `score: 0.0, passed: false`, no `unmeasured`,
            # and a verdict whose own bound evidence still carried `score: 1.0` -- a
            # published zero for a task that was measured successfully.
            why = f"the check wrote no {spec['reward_file']} (looked for {got}), exit {code}"
            reason = why
            if isinstance(evidence, dict) and evidence.get("measured") is False:
                # Keep the check's own, more specific reason when it says it could not
                # measure the task at all.
                reason = str(evidence.get("error") or why)
            verdict = {"kind": kind, "passed": False, "score": None,
                       "outcome": "unmeasured", "unmeasured": reason,
                       "detail": " | ".join(p for p in
                                            [why, _check_output_detail(out, proc.stderr)]
                                            if p)[:VERDICT_DETAIL_LIMIT]}
            if evidence is not None:
                verdict["evidence"] = evidence
            return verdict

        raw = got.read_text().strip()
        try:
            score = float(raw)
        except ValueError:
            return {"kind": kind, "passed": False, "score": 0.0,
                    "detail": f"{spec['reward_file']} held {raw!r}, which is not a number"}

        detail = [f"reward {score:g} from {spec['reward_file']} (exit {code})"]
        if evidence is not None and evidence.get("why"):
            detail.append(str(evidence["why"]))
        detail.append(_check_output_detail(out, proc.stderr))
        verdict = {"kind": kind, "passed": bool(score), "score": score,
                   "detail": " | ".join(p for p in detail if p)[:VERDICT_DETAIL_LIMIT]}
        if evidence is not None:
            verdict["evidence"] = evidence
        if evidence_path and evidence is None:
            verdict["unmeasured"] = (f"the check wrote no {evidence_path}: its tests did "
                                     f"not run, so this task was not measured")
        return verdict

    pass_when = spec.get("pass_when", "exit0")
    passed = code == 0
    detail = f"exit {code}"
    if pass_when == "exit0_stdout":
        want = str(spec.get("stdout", ""))
        passed = passed and out.strip() == want.strip()
        detail = f"exit {code}, stdout {out.strip()[:80]!r} vs {want[:80]!r}"
    if not passed and out.strip():
        detail += f" | {out.strip()[-200:]}"
    return {"kind": "command", "passed": passed, "detail": detail}
