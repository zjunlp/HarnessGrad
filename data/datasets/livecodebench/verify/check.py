#!/usr/bin/env python3
"""LiveCodeBench's official pass@1, run by the **pinned upstream executor**.

`testing_util.py` and `pass_k_utils.py` arrive as `VERIFY.inputs`, copied out of the pinned
source archive (`LiveCodeBench@28fef95e`), so the code that decides whether a solution is
correct is upstream's own file rather than a transcription of it. This script is the wrapper:
it hands one problem's released tests and the agent's `solution.py` to
`testing_util.run_test`, applies upstream's correctness rule, and writes the platform's
reward and evidence.

The verdict, and where it comes from
------------------------------------
`lcb_runner/evaluation/compute_code_generation_metrics.py::evaluate_generations_by_problem`
collects the per-test values `run_test` returns and keeps only those where
`np.all(curr_res)`; `pass_k_utils.py::compute_metrics_from_results` (lines 26-53, transcribed
verbatim in `data/datasets/livecodebench.py`) then counts a generation as correct iff
`np.all(gen > 0)`. Over one problem with one generation, `pass@1` is therefore exactly 1.0 or
0.0 -- which is the reward here, and why the metric is named `pass@1` in the evidence rather
than "accuracy".

Timeout budget and the per-task metric
--------------------------------------
Upstream kills the child after `(timeout + 1) * n_tests + 5` seconds. The former 600 s
cap bounded the check's child wait, but could reject a correct program before upstream's
budget expired. Use the upstream budget: for 103 tests at timeout=6 it is 726 s, within
the adapter's existing 900 s verifier bound (`livecodebench.py::_verify`), which still
bounds the entire check. Removing the inner cap therefore preserves that outer bound.

**One generation, one problem.** The platform grades one task per check, so pass@k for
k > 1 has no second sample to average over; k = 1 is the only well-defined value and the
evidence says so.

Why a short verdict list is safe
--------------------------------
`run_test` returns **fewer** per-test verdicts than there are tests when it stops early, and
that is upstream's design, not a bug: `grade_stdio` returns as soon as it has to, appending a
sentinel first -- `-2` for a wrong answer (mismatched output), `-3` for the time limit, `-4` for
a runtime error. Measured on `abc370_a` with `print('Yes')`: `results == [True, -2]` for four
tests. The reward rule survives truncation because **every truncation carries a non-positive
sentinel**, so `np.all(gen > 0)` can never be true for a cut-short run; the evidence records the
list as it came back, which is why `tests.passed` can be smaller than the test count for reasons
other than test failures.

`measured: false` is reserved for the environment: a missing or undecodable test payload, or
an executor that will not import. A solution that does not compile, loops, or fails a test is
a **measured zero** -- that is upstream's own verdict for those cases (`results = [-2]` on a
compile failure, `-1` on a timeout), and it must not look like a broken platform.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
from pathlib import Path

# Native staging supplies this dependency at the task root.
sys.path.insert(0, str(Path.cwd()))
from data.scaffolds.scorer_input import add_arguments, run_bound

#: Upstream's per-test budget (`testing_util.run_test(..., timeout=6)`).
PER_TEST_TIMEOUT = 6


def _workdir_on_path() -> None:
    """Put the workdir on `sys.path`, where the platform materialized the pinned files.

    This check lives in `verify/` (so `sys.path[0]` is `verify/`) while `testing_util.py` and
    `pass_k_utils.py` are `VERIFY.inputs` at the workdir root. DS-1000 has the same layout and
    solves it by launching its runner from a scratch directory that holds `execution.py`; here
    the two directories are one `sys.path` entry apart, which is cheaper. Measured, because the
    platform arm failed on exactly this: by hand the control sets `PYTHONPATH`, and under
    `eval.verification.verifier` it does not, so `import testing_util` raised `ModuleNotFoundError` only in
    the sandbox run.
    """
    work = str(Path.cwd())
    if work not in sys.path:
        sys.path.insert(0, work)


def _json_scalar(value):
    """Convert numpy booleans to JSON-native bools, retaining numeric sentinels."""
    item = getattr(value, "item", None)
    value = item() if callable(item) else value
    return bool(value) if isinstance(value, bool) else value


def _child(tests_text: str, solution: str, queue) -> None:
    """`run_test` in its own process: it mutates process-global state (`reliability_guard`
    nulls `os.system`, `os.remove`, `builtins.quit` and `os.environ` itself) and it executes
    the candidate, so nothing it does may reach the check's own interpreter. Upstream makes the
    same choice, for the same reason, in `compute_code_generation_metrics.py`."""
    _workdir_on_path()
    sys.set_int_max_str_digits(50000)          # upstream sets this for huge test integers
    try:
        from testing_util import run_test
    except Exception as exc:                   # noqa: BLE001 - reported as unmeasurable
        queue.put({"error": f"the pinned executor will not import: {type(exc).__name__}: {exc}"})
        return
    try:
        results, metadata = run_test({"input_output": tests_text}, test=solution, debug=False,
                                     timeout=PER_TEST_TIMEOUT)
        queue.put({"results": [_json_scalar(value) for value in results],
                   "metadata": str(metadata)[:2000]})
    except Exception as exc:                   # noqa: BLE001
        queue.put({"error": f"run_test raised {type(exc).__name__}: {exc}"})


def _positive(value) -> bool:
    """`np.all(gen > 0)`'s per-element question, without importing numpy to ask it.

    The values `run_test` returns are `True`/`False` plus the sentinels `-1` (timeout) and
    `-2` (compile failure); `> 0` is upstream's test and it is why a sentinel is a failure
    rather than a truthy non-zero.
    """
    if isinstance(value, bool):
        return value
    try:
        return bool(value > 0)
    except TypeError:
        return False


def _write(path: str, payload: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")


def _measure(args, reward: float, why: str, total: int, passed: int, results) -> int:
    _write(args.reward_file, f"{reward:g}\n")
    _write(args.evidence_file, json.dumps(
        {"scorer_input": args.scorer_input_receipt, "measured": True, "metric": "pass@1 (one generation, all released tests)",
         "reward": reward, "tests": {"total": total, "passed": passed},
         "per_test": results, "why": why}, sort_keys=True) + "\n")
    print(f"reward {reward:g}: {why}")
    return 0


def _cannot_measure(args, why: str) -> int:
    _write(args.evidence_file, json.dumps(
        {"scorer_input": args.scorer_input_receipt, "measured": False, "metric": "pass@1", "error": why}, sort_keys=True) + "\n")
    print(f"check cannot measure: {why}", file=sys.stderr)
    return 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", required=True, help="the problem's released tests")
    parser.add_argument("--solution", required=True)
    parser.add_argument("--reward-file", default="verifier/reward.txt")
    parser.add_argument("--evidence-file", default="verifier/evidence.json")
    add_arguments(parser)
    args = parser.parse_args()
    return run_bound(args, {args.tests: Path(args.tests)},
                     lambda inputs, receipt: _score(args, inputs, receipt))


def _score(args, inputs, receipt) -> int:
    args.scorer_input_receipt = receipt
    _workdir_on_path()

    tests_path = inputs[args.tests]
    if not tests_path.is_file():
        return _cannot_measure(args, f"no test payload at {tests_path}")
    tests_text = tests_path.read_text(encoding="utf-8")
    try:
        payload = json.loads(tests_text)
        inputs, outputs = payload["inputs"], payload["outputs"]
    except Exception as exc:                   # noqa: BLE001
        return _cannot_measure(args, f"the test payload is not usable: "
                                     f"{type(exc).__name__}: {exc}")
    total = len(inputs)
    if total == 0 or total != len(outputs):
        return _cannot_measure(args, f"{total} inputs against {len(outputs)} outputs")

    solution_path = Path(args.solution)
    solution = solution_path.read_text(encoding="utf-8") if solution_path.is_file() else ""
    if not solution.strip():
        return _measure(args, 0.0, "no answer (measured zero): solution.py is missing or empty",
                        total, 0, [])

    cap = (PER_TEST_TIMEOUT + 1) * total + 5
    queue: multiprocessing.Queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_child, args=(tests_text, solution, queue))
    process.start()
    process.join(cap)
    if process.is_alive():                     # upstream kills and marks every test failed
        process.kill()
        process.join()
        return _measure(args, 0.0, f"global timeout after {cap:g}s: the solution did not finish "
                                   f"on {total} tests", total, 0, [-1] * total)
    if queue.empty():
        return _cannot_measure(args, "the executor died without a verdict")
    outcome = queue.get()
    if "error" in outcome:
        return _cannot_measure(args, outcome["error"])

    results = outcome["results"]
    passed = sum(1 for value in results if _positive(value))
    # **The verdict is upstream's own aggregation**, not a re-implementation of it:
    # `compute_metrics_from_results` counts a generation as correct iff `np.all(gen > 0)` and
    # `pass@1` over one problem with one generation is 1.0 or 0.0. The per-test tally is
    # recorded beside it because a binary reward hides partial progress (the register's M4:
    # one task scored 0 in twelve measurements while passing four of five tests nine times).
    try:
        from pass_k_utils import compute_metrics_from_results
        reward = float(compute_metrics_from_results({"task": [results]}, k_list=[1])["pass@1"])
    except Exception as exc:                    # noqa: BLE001
        return _cannot_measure(args, f"cannot score the executor's verdict: "
                                     f"{type(exc).__name__}: {exc}")
    why = (f"all {total} released tests passed" if reward else
           f"{passed} of {total} released tests passed")
    if outcome.get("metadata") and not reward:
        why += f"; executor metadata {outcome['metadata'][:200]}"
    return _measure(args, reward, why, total, passed, results)


if __name__ == "__main__":
    raise SystemExit(main())
