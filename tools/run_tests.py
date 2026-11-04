#!/usr/bin/env python3
"""Run the quality suite in parallel, with a lane for tests that need exclusivity.

Why this exists
---------------
The suite is ~3,500 tests across ~139 files and takes **about an hour** when run
serially, because most of the cost is real subprocess and container work rather
than sleeps. The host has many cores, so almost all of that hour is idle waiting.
This runner splits the suite by file and runs the files concurrently.

Two lanes, because not every test can share a machine
-----------------------------------------------------
Most tests are isolated: they use `tmp_path` and never touch a fixed resource.
A minority bind a **fixed port**, start a long-lived server, or exercise a
process-wide service registry. Running two of those at once makes them fail for
reasons that have nothing to do with the code under test -- measured here as a
batch of false failures that vanished when run one at a time. Those files go in
the **exclusive lane**, which runs at concurrency 1 while the parallel lane runs
beside it. The exclusive lane is not "slower"; it is the only correct way to run
those files, and it still overlaps everything else.

Every file gets its own pytest process and its own `--basetemp` **outside the
platform tree**, which is required: the driver refuses a harness work root inside
the platform, so an in-tree basetemp turns ordinary tests into confusing failures.

What this does not do
---------------------
It does not weaken, skip or delete a test. A file that fails fails the run, and
the exit status is non-zero. It also does not claim the suite is green: some
failures are environment-dependent (Docker, missing external checkouts) and are
reported, not hidden.

Usage
-----
    python3 tools/run_tests.py                 # everything, jobs = min(cpus, 32)
    python3 tools/run_tests.py --jobs 8
    python3 tools/run_tests.py --fast          # skip the measured-slow heavy files
    python3 tools/run_tests.py --file test_dataset_blockers.py
    python3 tools/run_tests.py --list          # show the plan, run nothing
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import os
import subprocess
import sys
import tempfile
import time
import platform
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COSTS = ROOT / "tools" / "test_costs.json"

sys.path.insert(0, str(ROOT / "tools"))
import test_scope  # noqa: E402  (sibling helper; no third-party dependency)

#: Files that bind a fixed host port, start a shared server, share a container or
#: network name, write into the checkout, or mutate a process-wide registry. Each
#: entry was re-derived from the source against the signal vocabulary in
#: `docs/test-lanes.md` (section "The signals"), and each entry that could not be
#: settled from the source alone was measured under concurrency against a serial
#: run. Files whose reason had disappeared were removed on that evidence -- see
#: `docs/test-lanes.md` section "Measurements" for the runs themselves.
#: They run at concurrency 1 while the parallel lane runs beside them.
#: `tests/quality/test_test_lanes.py` fails if an entry here exists without
#: tripping one of those signals, so an entry whose reason has gone is caught
#: rather than kept out of habit.
EXCLUSIVE = (
    "test_services.py",
    "test_service_commands.py",
    "test_web_gateway.py",
    "test_appworld_official_scorer.py",
    "test_container.py",
    "test_container_state.py",
    "test_harness_runtime.py",
)

#: Costly files skipped by --fast. These are not broken; they are simply the ones
#: that spend minutes in real work, so a contributor iterating on one module does
#: not need them. `--fast` is a convenience, never the default and never a claim
#: of coverage.
SLOW = (
    "test_appworld_official_scorer.py",
    "test_dataset_classes.py",
    "test_dataset_controls.py",
    "test_qa_dataset_contract.py",
    "test_dgm_rule.py",
    "test_isolation.py",
    "test_livecodebench_official_scorer.py",
)


#: Use Python's standard temporary directory selection (including TMPDIR).
#: HG_TEST_TMPDIR explicitly overrides it; all roots are checked below to keep
#: pytest's harness work directories outside the platform checkout.
def default_basetemp_parent() -> str:
    return os.environ.get("HG_TEST_TMPDIR") or tempfile.gettempdir()


def free_gb(path: Path) -> float:
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / 1e9
    except OSError:
        return float("nan")


def discover(patterns: list[str]) -> list[Path]:
    """Match ANY filter: qualified repository paths exactly, bare terms by substring.

    Qualified paths keep explicit CI plans from also selecting a nested file whose
    path merely contains the requested path. Bare terms retain the interactive
    substring convenience (for example, `--file dataset`).

    A list rather than one string because a reviewer checking a change usually has
    several files in mind, and running them one invocation at a time meant paying
    the per-run collection cost once per file and re-deriving the lane split each
    time. An empty list means "no filter".
    """
    found = sorted((ROOT / "tests").rglob("test_*.py"))
    if patterns:
        found = [p for p in found
                 if any(p.relative_to(ROOT).as_posix() == pattern if "/" in pattern
                        else pattern in p.relative_to(ROOT).as_posix()
                        for pattern in patterns)]
    # A `conftest.py` is collected by pytest, not run as a test module; and the
    # shared fixture helpers are imported, not executed.
    return [p for p in found if not p.name.startswith("conftest")]


def load_costs() -> dict[str, float]:
    try:
        data = json.loads(COSTS.read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in data.get("files", {}).items()}
    except (OSError, ValueError, TypeError):
        return {}


def cost_of(path: Path, costs: dict[str, float]) -> float:
    return costs.get(path.name, 8.0)


def receipt_argv(argv: list[str]) -> list[str]:
    """Allow only fixed runner vocabulary; never retain arbitrary CLI values."""
    fixed = {"-m", "pytest", "-q", "-p", "no:cacheprovider", "--jobs", "--fast",
             "--file", "--dataset", "--registry-only", "--with-registry", "--list",
             "--basetemp-root", "--allow-load-sensitive", "--keep-basetemp",
             "--no-rerun", "--pytest-arg", "--output-json", "--plan", "--full",
             "--full-if-empty", "--dry-run", "<redacted>", "<redacted-pytest-arg>"}
    return [arg if arg in fixed else "<redacted>" for arg in argv]


class QualityReceipt:
    """Atomic, opt-in file-level evidence; exit zero is not a coverage claim.

    No environment dump or subprocess output is persisted. argv retains only
    fixed runner vocabulary; arbitrary values and executable paths are redacted.
    An unfinished receipt remains running,
    with completed attempts preserved even after SIGKILL (no cleanup guarantee).
    'passed' means pytest returned zero, not that every collected test ran.
    'skipped' describes runner exclusions, not individual pytest skip markers.
    'attempted' includes spawn errors; 'executed' requires successful Popen.
    'unexecuted' is planned files with no confirmed launch. A fatal signal in the
    tiny spawn/checkpoint window conservatively leaves launch unconfirmed.
    Receipt destinations are replaced per invocation; use unique paths to retain
    historical runs. Invalid CLI syntax and unwritable destinations cannot be
    receipted. Fatal signals leave a running snapshot, never a success claim.
    """

    def __init__(self, output: str, discovered: list[str], planned: list[str],
                 *, state: str = "selected", skipped: list[str] | None = None,
                 deferred: list[str] | None = None):
        self.path = Path(output).resolve() if output else None
        self.lock = threading.Lock()
        self.data = {
            "schema_version": 1, "kind": "quality-file-receipt",
            "state": state, "status": "planned", "returncode": None,
            "argv": receipt_argv([sys.executable, *sys.argv]),
            "argv_policy": "fixed_vocabulary_only_all_arbitrary_values_redacted",
            "attempted": [],
            "attempted_semantics": "launch_intent_only_starting_outcome_unknown_until_confirmed",
            "identity": {}, "started_at": time.time(),
            "discovered": discovered, "planned": planned, "executed": [],
            "passed": [], "failed": [], "skipped": skipped or [],
            "deferred": deferred or [], "unexecuted": list(planned),
            "attempts": [], "full_coverage": False,
            "coverage_scope": "file_invocations_not_test_cases_or_capabilities",
            "all_skipped": [],
            "passed_semantics": "pytest_exit_zero_including_all_skipped_and_unknown_counts",
        }
        if self.path:
            def git(*args: str) -> str | None:
                try:
                    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                       text=True, timeout=5)
                    return p.stdout.strip() if p.returncode == 0 else None
                except (OSError, subprocess.TimeoutExpired):
                    return None
            dirty = git("status", "--porcelain", "--untracked-files=no")
            self.data["identity"] = {
                "commit": git("rev-parse", "HEAD"),
                "tracked_dirty": None if dirty is None else bool(dirty),
                "python": platform.python_version(),
                "platform": platform.system(), "machine": platform.machine(),
                "pytest_environment": {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                                       "PYTHONDONTWRITEBYTECODE": "1"},
            }
        selector_argv = os.environ.get("HG_QUALITY_SELECTOR_ARGV_JSON")
        if selector_argv:
            self.data["selector_argv"] = receipt_argv(json.loads(selector_argv))
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        for key in ("argv", "selector_argv", "runner_argv"):
            if key in self.data:
                self.data[key] = receipt_argv(self.data[key])
        for attempt in self.data["attempts"]:
            attempt["argv"] = receipt_argv(attempt["argv"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.data, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def start(self, rel: str, argv: list[str]) -> int:
        with self.lock:
            index = len(self.data["attempts"])
            self.data["attempts"].append({"file": rel, "argv": receipt_argv(argv),
                                          "status": "starting", "returncode": None})
            self.data["status"] = "running"
            if rel not in self.data["attempted"]:
                self.data["attempted"].append(rel)
            self.save()
            return index

    def launched(self, index: int) -> None:
        with self.lock:
            attempt = self.data["attempts"][index]
            attempt["status"] = "running"
            rel = attempt["file"]
            if rel not in self.data["executed"]:
                self.data["executed"].append(rel)
                self.data["unexecuted"].remove(rel)
            self.save()

    def end(self, index: int, code: int | None, seconds: float,
            error: str | None = None, counts: dict | None = None) -> None:
        with self.lock:
            attempt = self.data["attempts"][index]
            attempt.update(status="completed" if code is not None else "error",
                           returncode=code, seconds=seconds)
            if error:
                attempt["error_type"] = error
            attempt["test_case_counts"] = counts
            attempt["test_case_counts_unknown"] = counts is None
            rel = attempt["file"]
            if counts and counts.get("skipped", 0) and not any(
                    counts.get(k, 0) for k in ("passed", "failed", "error", "errors", "xfailed", "xpassed")):
                if rel not in self.data["all_skipped"]:
                    self.data["all_skipped"].append(rel)
            # Once failed, always failed in this invocation, even if a rerun passes.
            target = "passed" if code == 0 else "failed"
            if target == "failed" and rel in self.data["passed"]:
                self.data["passed"].remove(rel)
            if rel not in self.data[target] and not (target == "passed" and rel in self.data["failed"]):
                self.data[target].append(rel)
            self.save()

    def finish(self, code: int, status: str = "completed") -> int:
        with self.lock:
            self.data.update(returncode=code, status=status, finished_at=time.time())
            self.data["full_coverage"] = bool(self.data["discovered"]) and (
                status == "completed" and not self.data["unexecuted"]
                and set(self.data["planned"]) == set(self.data["discovered"])
                and set(self.data["executed"]) == set(self.data["planned"])
                and bool(self.data["attempts"])
                and all(a["status"] == "completed" for a in self.data["attempts"]))
            self.save()
        return code


def run_one(path: Path, basetemp_root: Path, extra: list[str],
            keep: bool = False, receipt: QualityReceipt | None = None) -> dict:
    rel = path.relative_to(ROOT)
    tmp = basetemp_root / rel.with_suffix("")
    tmp.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           f"--basetemp={tmp}", str(rel), *extra]
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.time()
    safe_cmd = cmd[:8] + ["<redacted-pytest-arg>" for _ in extra]
    attempt = receipt.start(str(rel), safe_cmd) if receipt else None
    try:
        with subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, errors="replace", env=env) as child:
            if receipt:
                receipt.launched(attempt)
            stdout, stderr = child.communicate()
            proc = subprocess.CompletedProcess(cmd, child.returncode, stdout, stderr)
    except BaseException as exc:
        if receipt:
            receipt.end(attempt, None, time.time() - started, type(exc).__name__)
        raise
    else:
        if receipt:
            # Only parse pytest's terminal summary, never arbitrary output lines.
            lines = [line.strip("= ") for line in proc.stdout.splitlines() if line.strip()]
            summary = lines[-1] if lines else ""
            counts = None
            if re.fullmatch(r"(?:\d+ (?:passed|failed|skipped|deselected|errors?|xfailed|xpassed|warnings?)(?:, )?)+ in .+", summary):
                counts = {key: int(value) for value, key in re.findall(
                    r"(\d+) (passed|failed|skipped|deselected|errors?|xfailed|xpassed|warnings?)", summary)}
            receipt.end(attempt, proc.returncode, time.time() - started, counts=counts)
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
    tail = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return {"file": path.name, "rel": str(rel), "code": proc.returncode,
            "seconds": round(time.time() - started, 1),
            "summary": tail[-1] if tail else "",
            "stderr_tail": "\n".join(proc.stderr.splitlines()[-6:])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=int, default=min(os.cpu_count() or 4, 32),
                    help="concurrent files in the parallel lane (default: min(cpus, 32))")
    ap.add_argument("--fast", action="store_true",
                    help="skip the measured-slow files (a convenience, not full coverage)")
    ap.add_argument("--file", action="append", default=[], metavar="PATH_OR_SUBSTRING",
                    help="repository-relative paths (containing /) match exactly; bare "
                         "terms match path substrings. Repeatable; "
                         "a file matching any of them is included. `--file a --file b` "
                         "runs both in one pass, which is the point: they still land in "
                         "their own lanes.")
    ap.add_argument("--dataset", action="append", default=[],
                    help="run only the tests for this dataset, plus the core platform "
                         "tests. Repeatable. Use --list to see the selection.")
    ap.add_argument("--registry-only", action="store_true",
                    help="run ONLY the cross-dataset contract tests, with no core tests")
    ap.add_argument("--with-registry", action="store_true",
                    help="also run the cross-dataset contract tests (slow: they check "
                         "all datasets, so they belong to dataset-layer work)")
    ap.add_argument("--list", action="store_true", help="print the plan and exit")
    ap.add_argument("--basetemp-root", default="",
                    help="parent for per-file pytest basetemps; default is a fresh "
                         "tree under HG_TEST_TMPDIR or tempfile.gettempdir() (TMPDIR). "
                         "It must be OUTSIDE the platform tree.")
    ap.add_argument("--allow-load-sensitive", action="store_true",
                    help="exit 0 when the only failures are files that pass when run "
                         "alone. Off by default: a test that only passes alone is a real "
                         "isolation defect, it is just not the failure it first looked like.")
    ap.add_argument("--keep-basetemp", action="store_true",
                    help="keep each file's pytest temporary directory (debugging only; "
                         "140 files otherwise leave 140 trees behind)")
    ap.add_argument("--no-rerun", action="store_true",
                    help="skip the run-alone recheck of failed files")
    ap.add_argument("--pytest-arg", action="append", default=[],
                    help="extra argument forwarded to every pytest invocation")
    ap.add_argument("--output-json", default="", help="atomic file-level receipt; CLI arguments must not contain secrets")
    args = ap.parse_args()
    discovered = [str(f.relative_to(ROOT)) for f in discover([])]
    receipt = QualityReceipt(args.output_json, discovered, [])
    if args.jobs < 1:
        print("--jobs must be positive", file=sys.stderr)
        return receipt.finish(2, "invalid")

    try:
        return execute(args, receipt, discovered)
    except (Exception, KeyboardInterrupt) as exc:
        receipt.data["error_type"] = type(exc).__name__
        return receipt.finish(130 if isinstance(exc, KeyboardInterrupt) else 1,
                              "interrupted" if isinstance(exc, KeyboardInterrupt) else "error")


def execute(args, receipt: QualityReceipt, discovered: list[str]) -> int:
    costs = load_costs()
    files = discover(args.file)
    selection_note = ""
    if args.registry_only:
        names = test_scope.published()
        wanted = set(test_scope.REGISTRY_WIDE)
        before = len(files)
        files = [f for f in files if f.name in wanted]
        selection_note = f"scope: registry-wide only; {len(files)}/{before} files"
    elif args.dataset:
        try:
            names = test_scope.published()
            wanted = set(test_scope.select(names, args.dataset,
                                           include_registry=args.with_registry))
        except KeyError as exc:
            print(f"unknown dataset: {exc}", file=sys.stderr)
            return receipt.finish(2, "invalid")
        before = len(files)
        files = [f for f in files if f.name in wanted]
        selection_note = (f"scope: datasets {', '.join(sorted(set(args.dataset)))} "
                          f"+ core"
                          + (" + registry-wide" if args.with_registry else "")
                          + f"; {len(files)}/{before} files")
    if args.fast:
        files = [f for f in files if f.name not in SLOW]
    planned = [str(f.relative_to(ROOT)) for f in files]
    receipt.data.update(planned=planned, unexecuted=list(planned),
                        state="full" if planned and set(planned) == set(discovered) else "selected",
                        skipped=[f for f in discovered if f not in planned])
    receipt.save()
    if not files:
        print("no test files matched", file=sys.stderr)
        return receipt.finish(2, "empty")

    exclusive = [f for f in files if f.name in EXCLUSIVE]
    parallel = [f for f in files if f.name not in EXCLUSIVE]
    # Longest first, so the two lanes finish together instead of one long file
    # holding up the tail.
    parallel.sort(key=lambda p: -cost_of(p, costs))
    exclusive.sort(key=lambda p: -cost_of(p, costs))

    root = Path(args.basetemp_root or default_basetemp_parent()).resolve()
    if root == ROOT or ROOT in root.parents:
        print(f"refusing: basetemp root {root} is inside the platform {ROOT}; "
              "the driver rejects a harness work root there", file=sys.stderr)
        return receipt.finish(2, "invalid")
    root.mkdir(parents=True, exist_ok=True)
    if args.basetemp_root:
        basetemp_root = root
    else:
        basetemp_root = Path(tempfile.mkdtemp(prefix="harnessgrad-pytest-", dir=root))

    plan_parallel = sum(cost_of(f, costs) for f in parallel)
    plan_exclusive = sum(cost_of(f, costs) for f in exclusive)
    if selection_note:
        print(selection_note)
    if args.list:
        print(f"basetemp: {basetemp_root}  ({free_gb(basetemp_root):.1f} GB free)")
        print(f"parallel lane: {len(parallel)} files, measured cost {plan_parallel:.0f}s, "
              f"{args.jobs} jobs -> ~{plan_parallel / max(args.jobs, 1):.0f}s")
        print(f"exclusive lane: {len(exclusive)} files, measured cost {plan_exclusive:.0f}s, "
              f"1 job")
        # A selection is bounded below by its single slowest file: with five files
        # and 32 jobs, the cost is one file, not the sum divided by 32.
        longest = max((cost_of(f, costs) for f in files), default=0.0)
        estimate = max(plan_parallel / max(args.jobs, 1), plan_exclusive, longest)
        print(f"estimated wall clock: ~{estimate:.0f}s "
              f"(bounded by the slowest single file, ~{longest:.0f}s)")
        for f in exclusive:
            print(f"  [exclusive] {f.relative_to(ROOT)}  ~{cost_of(f, costs):.0f}s")
        return receipt.finish(0, "dry_run")

    print(f"basetemp: {basetemp_root}")
    print(f"parallel lane: {len(parallel)} files at {args.jobs} jobs")
    print(f"exclusive lane: {len(exclusive)} files at 1 job")
    started = time.time()
    results: list[dict] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs + 1) as pool:
        futures = [pool.submit(run_one, f, basetemp_root, args.pytest_arg, args.keep_basetemp, receipt)
                   for f in parallel]
        future_excl = pool.submit(
            lambda: [run_one(f, basetemp_root, args.pytest_arg, args.keep_basetemp, receipt)
                            for f in exclusive])
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            results.append(r)
            mark = "ok  " if r["code"] == 0 else "FAIL"
            print(f"  {mark} {r['file']:<58} {r['seconds']:>7.1f}s  {r['summary']}",
                  flush=True)
        for r in future_excl.result():
            results.append(r)
            mark = "ok  " if r["code"] == 0 else "FAIL"
            print(f"  {mark} {r['file']:<58} {r['seconds']:>7.1f}s  {r['summary']}  [exclusive]",
                  flush=True)

    wall = time.time() - started
    failed = sorted((r for r in results if r["code"] != 0), key=lambda r: r["file"])
    print()
    print(f"{len(results)} files in {wall:.1f}s wall clock "
          f"(serial measured total {sum(cost_of(f, costs) for f in files):.0f}s)")
    if not failed:
        print("all files passed")
        return receipt.finish(0)

    # A failure under load and a failure in the code look identical in the summary, and
    # they need different responses. Re-run each failed file on its own: if it passes
    # alone, the earlier failure was contention between processes, not a broken test.
    # That distinction is the difference between "this suite is unreliable" and "this
    # test is not isolated", and only the second is worth chasing.
    by_rel = {str(f.relative_to(ROOT)): f for f in files}
    if args.no_rerun:
        load_sensitive, real = [], failed
    else:
        print()
        print(f"rechecking {len(failed)} failed file(s) one at a time to separate "
              "load contention from real failures...")
        load_sensitive, real = [], []
        for r in failed:
            alone = run_one(by_rel[r["rel"]], basetemp_root, args.pytest_arg,
                            args.keep_basetemp, receipt)
            if alone["code"] == 0:
                load_sensitive.append(r)
                print(f"  load-sensitive {r['file']} (passes alone: {alone['summary']})")
            else:
                real.append(r)
                print(f"  REAL failure   {r['file']} ({alone['summary']})")

    if real:
        print()
        print(f"{len(real)} file(s) failed even when run alone:")
        for r in real:
            print(f"  FAIL {r['file']} -> {r['summary']}")
            if r["stderr_tail"]:
                for ln in r["stderr_tail"].splitlines():
                    print(f"        {ln}")
    if load_sensitive:
        print()
        print(f"{len(load_sensitive)} file(s) pass alone but failed under {args.jobs} "
              "concurrent files:")
        for r in load_sensitive:
            print(f"  {r['file']} -> {r['summary']}")
        print("  These are isolation defects in the tests, not code regressions. Lower")
        print("  --jobs, or fix the shared resource; do not ignore them forever.")

    receipt.data["load_sensitive"] = [r["rel"] for r in load_sensitive]
    receipt.data["allow_load_sensitive"] = args.allow_load_sensitive
    if real:
        return receipt.finish(1)
    return receipt.finish(0 if args.allow_load_sensitive else 1)


if __name__ == "__main__":
    raise SystemExit(main())
