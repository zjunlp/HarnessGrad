"""Task scheduling and global slots; running task cleanup always finishes before propagation."""
from __future__ import annotations

import concurrent.futures as futures

from harnessgrad import slots
from eval.run.outcomes import _harness_failure


def _held(measure, index: int, task: dict) -> dict:
    """One task, holding one global measurement slot while it runs.

    When no budget is configured (`slots.current()` is `None`) this is a bare call and the
    platform behaves exactly as before: `--jobs` is the only bound.
    """
    with slots.slot():
        return measure(index, task)


def _in_parallel(tasks: list[dict], measure, jobs: int) -> dict[str, dict]:
    """Measure every task, up to `jobs` at a time, and collect the outcomes by task id.

    `jobs=1` stays a plain sequential loop, because the platform's default record must not
    depend on a thread pool to be the record it has always been.

    Why this axis and not another: on a measured three-task run the agent phase was 87.8% of
    the wall clock (2578 s of 2936 s) and the platform sat idle for all of it -- the tasks are
    independent by construction (own container, own network, own model gateway, own workspace,
    own cache key), so this is the cheap parallelism. Splitting *inside* a task is not
    available: the harness is a process the platform does not control.

    The first exception is re-raised after cancelling what has not started. A task already
    running is left to finish: `run_one` tears its own containers down in a `finally`, and
    abandoning a thread mid-teardown is how a leaked network gets inherited by whatever runs
    next (§2.5.7).
    """
    if jobs <= 1:
        # Computed in task order, which is what makes the merge reproducible at one
        # `--jobs` setting as well as comparable across two of them.
        return {task["task_id"]: _held(measure, index, task)
                for index, task in enumerate(tasks, start=1)}
    pool = futures.ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="hg-task")
    pending = [pool.submit(_held, measure, index, task)
               for index, task in enumerate(tasks, start=1)]
    outcomes: dict[str, dict] = {}
    try:
        for done in futures.as_completed(pending):
            outcome = done.result()
            outcomes[outcome["task_id"]] = outcome
            # **Stop paying for a harness that cannot start.** See `_all_unstarted`: the
            # failure is task-independent, so `jobs` agreements are enough, and the rest
            # of the round can only return the same `0.000` at full price.
            if _all_unstarted(outcomes, jobs):
                for fut in pending:
                    fut.cancel()
                break
    except BaseException:
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    # **A cancelled task is not a task that scored nothing.** The merge indexes `outcomes`
    # by every task, so the cancelled ones are marked `not_run` and kept out of `per_task`
    # rather than given a zero the platform never measured (§2.5.7).
    for task in tasks:
        outcomes.setdefault(task["task_id"],
                            {"task_id": task["task_id"], "not_run": True})
    return outcomes


def _all_unstarted(outcomes: dict[str, dict], jobs: int) -> bool:
    """Has the harness failed to *start* on every task measured so far, `jobs` of them?

    This is the platform's own question (`_harness_failure`) asked about a round instead of
    a task. A harness whose entrypoint cannot be invoked fails identically on every task --
    that is the task-independent part of a run -- so `jobs` agreements are enough to stop
    the round. Measured (`codex-dgm`, 2026-10-05): the improver's edit dropped the
    `--task`/`--workdir` contract, all ten tasks produced `FileNotFoundError: '--task'`
    with no trace, and that happened in **two consecutive rounds** -- twenty
    task-measurements whose only possible contribution was `0.000` over and over.

    Deliberately not "any task failed to start": a task-specific failure (an image with no
    interpreter, say) is a fact about *that task*, and stopping the round on it would trade
    one wrong record for another.
    """
    if len(outcomes) < jobs:
        return False
    for outcome in outcomes.values():
        if outcome.get("not_run") or outcome.get("invalid") or "cached" in outcome:
            return False
        measured = outcome.get("measured") or []
        if not measured or not _harness_failure(measured[-1]):
            return False
    return True
