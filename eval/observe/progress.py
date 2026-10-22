"""What a run is doing *right now*, as events a panel can render.

Why this exists
---------------
`events.jsonl` used to describe a run only at round granularity: a header, then one
line per finished round. That is enough to read a curve after the fact and useless
while it is being produced. Measured on `loop-terminal_bench-26452`: round 0's five
tasks took ~100 minutes, the driver was working the whole time, and the log held
**eleven** lines -- six notes, a header, a warn and one round. The console UI polls
that file every 1.5s and redraws only when it changes, so it showed the same screen
for over an hour. A watcher cannot tell that from a hung process, and neither can
the person who has to decide whether to kill it.

So progress is a first-class record, not a by-product of printing:

    task    a task started, or finished, in a round           (round, i/n, task_id)
    phase   one stage of that task started or finished        (phase, status, elapsed)
    beat    a phase that is still running                     (phase, elapsed)
    over    a phase that ran past the budget it declared      (phase, elapsed, budget)

Three properties, each of which cost a design decision:

* **Every event is durable when it happens.** `Console.emit` flushes per record, so a
  run killed mid-task has an honest record up to the kill. That is the run somebody
  needs to read.
* **Beats are cheap and boring.** One thread, one line per `BEAT_S`, carrying no
  output and no state -- a heartbeat that is expensive to produce becomes the thing
  that slows the run down, and a heartbeat that carries state becomes a second copy
  of the run to keep in step.
* **The phase names are shared, not invented per call site.** `PHASES` is the one
  list; `eval/run/runner.py` opens phases by name, and a panel that has never seen this
  module can still say what "agent" means. A free-form label would drift.

The reporter is reached through a `ContextVar`, not through parameters. That is a
deliberate exception to this codebase's habit of passing things explicitly, and it is
bounded by the dispatch it serves: `runner.evaluate` -> `runner.run_one` ->
`container.*` is one call chain, in one thread, and threading a reporter through it
would change eight signatures in `eval/containers/container.py` to move a *log line*. The
context is set by the driver for the duration of one evaluation and by the runner for
the duration of one task, so nothing outside an evaluation can emit progress and the
log stays a record of the run rather than of the platform.
"""
from __future__ import annotations

import contextlib
import os
import threading
import time
from contextvars import ContextVar

__all__ = ["PHASES", "TaskProgress", "Reporter", "current", "phases_for", "reporting",
           "task_of", "task_progress", "PHASE_BUDGET_S"]


#: One flat vocabulary for both environment kinds (`eval/run/runner.py` runs a task either
#: on the host or inside a container). "agent" is the phase that costs the money in
#: both; the rest name what the platform is doing around it.
#: Re-exported from `eval/observe/phases.py`, which owns the vocabulary. This module writes
#: phases, it does not decide what phases exist -- the same list is read by the panel, the
#: method-facing channel and the trace reader, and a second copy here is how a panel ends
#: up showing a raw identifier for a phase that was added on one side only.
from eval.observe.phases import PHASE_BUDGET_S, PHASES  # noqa: F401  (re-exported)

#: Read by tests, so a suite does not have to spend ten seconds proving that a beat
#: appears. Production never sets it.
#: Seconds between beats. A poll loop that redraws every 1.5s does not need more, and
#: a line per second would put ~10k records into a 3-hour run's event file.
BEAT_S = 10.0

#: Read by tests, so a suite does not have to spend ten seconds proving that a beat
#: appears. Production never sets it.
ENV_BEAT_S = "HARNESSGRAD_BEAT_S"


def _beat_interval() -> float:
    try:
        return max(0.05, float(os.environ.get(ENV_BEAT_S) or BEAT_S))
    except ValueError:
        return BEAT_S


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


_CONTEXT: ContextVar["Reporter | None"] = ContextVar("harnessgrad_progress",
                                                     default=None)
_TASK: ContextVar["TaskProgress | None"] = ContextVar("harnessgrad_task",
                                                      default=None)


def current() -> "Reporter | None":
    """The reporter in force, or None. Never raises: a caller outside a run -- a unit
    test, an import -- loses the log line, not the work."""
    return _CONTEXT.get()


def task_of() -> "TaskProgress | None":
    """The task currently being run in this thread, or None."""
    return _TASK.get()


class _NoPhases:
    """`phases_for`'s answer when no run is being reported.

    A context manager that does nothing, rather than `None`, so that a call site never
    has to ask whether anyone is watching. The branch belongs in one place; the twelve
    places that open a phase are library code that also runs under unit tests.
    """

    def __call__(self, _name: str):
        return contextlib.nullcontext()


_NO_PHASES = _NoPhases()


def phases_for(held: "TaskProgress | None"):
    """A `phase(name)` opener bound to `held`, or a no-op one when `held` is None."""
    reporter = _CONTEXT.get()
    if held is None or reporter is None:
        return _NO_PHASES
    return lambda name: reporter.phase(name, round_no=held.round_no,
                                       task_id=held.task_id)


@contextlib.contextmanager
def reporting(reporter: "Reporter | None"):
    """Make `reporter` current for the enclosed block. Nesting restores the outer one."""
    token = _CONTEXT.set(reporter)
    try:
        yield reporter
    finally:
        _CONTEXT.reset(token)


class Reporter:
    """Turns phases into events. Owns no state a reader would miss if it vanished.

    `emit` is injected (`Console.progress`) rather than imported, so this module has no
    opinion about where events are written and `eval/observe/console.py` stays the single
    writer of the run's record.
    """

    def __init__(self, emit, *, beat_s: float | None = None):
        self._emit = emit
        self._beat_s = _beat_interval() if beat_s is None else max(0.05, float(beat_s))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------- internals ---

    def _beat(self, kind: str, **fields) -> None:
        try:
            self._emit(kind, **fields)
        except Exception:
            # A progress line must never be able to fail a run. The alternative --
            # letting a full disk or a closed stream raise out of the reporter --
            # would make the logging the most dangerous part of the platform.
            pass

    def _start_beats(self, *, phase: str, round_no: int, task_id: str, t0: float,
                     budget: int | None) -> None:
        self._stop = threading.Event()
        stop = self._stop

        def loop():
            warned = False
            while not stop.wait(self._beat_s):
                elapsed = round(time.time() - t0, 1)
                self._beat("beat", phase=phase, round=round_no, task=task_id,
                           elapsed_s=elapsed, budget_s=budget)
                if budget and not warned and elapsed > budget:
                    warned = True
                    self._beat("over", phase=phase, round=round_no, task=task_id,
                               elapsed_s=elapsed, budget_s=budget)

        self._thread = threading.Thread(target=loop, daemon=True,
                                        name=f"hg-beat-{phase}")
        self._thread.start()

    def _stop_beats(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)

    # -------------------------------------------------------------- the API ---

    @contextlib.contextmanager
    def phase(self, name: str, *, round_no: int = 0, task_id: str = "",
              budget_s: int | None = None):
        """Announce `name` running, then its outcome and elapsed time.

        The `finally` is the point: a phase that raises -- or that a killed run never
        finishes -- still leaves its start and its ending on the record.
        """
        if name not in PHASES:
            # Named, not accepted silently: an invented phase is a panel showing a
            # word the panel's own legend cannot explain.
            raise KeyError(f"unknown phase {name!r}; PHASES has {sorted(PHASES)}")
        if budget_s is None:
            budget_s = PHASE_BUDGET_S.get(name)
        t0 = time.time()
        holder = _TASK.get()
        previous = None
        if holder is not None:
            previous, holder.phase = holder.phase, name
        self._beat("phase", phase=name, status="start", round=round_no,
                   task=task_id, budget_s=budget_s)
        self._start_beats(phase=name, round_no=round_no, task_id=task_id, t0=t0,
                          budget=budget_s)
        status, detail = "done", ""
        try:
            yield
        except BaseException as exc:
            status, detail = "failed", f"{type(exc).__name__}: {exc}"[:200]
            raise
        finally:
            self._stop_beats()
            elapsed = round(time.time() - t0, 1)
            if holder is not None:
                holder.phase = previous or ""
                holder.phase_s += elapsed
            self._beat("phase", phase=name, status=status, round=round_no,
                       task=task_id, elapsed_s=elapsed, budget_s=budget_s,
                       detail=detail)

    @contextlib.contextmanager
    def task(self, task_id: str, *, round_no: int = 0, index: int = 0,
             total: int = 0):
        """Announce a task starting and finishing, and scoping the phases inside it."""
        holder = TaskProgress(round_no=round_no, task_id=task_id, index=index,
                              total=total, started=time.time())
        token = _TASK.set(holder)
        self._beat("task", task=task_id, status="start", round=round_no,
                   index=index, total=total)
        try:
            yield holder
        finally:
            holder.finished = time.time()
            _TASK.reset(token)
            self._beat("task", task=task_id, status=holder.status,
                       round=round_no, index=index, total=total,
                       elapsed_s=holder.elapsed, score=holder.score,
                       detail=holder.detail[:200])


class TaskProgress:
    """The mutable facts about one task in flight. Handed to the code that runs it."""

    def __init__(self, *, round_no: int, task_id: str, index: int, total: int,
                 started: float):
        self.round_no = round_no
        self.task_id = task_id
        self.index = index
        self.total = total
        self.started = started
        self.finished: float | None = None
        self.phase = ""
        self.phase_s = 0.0
        self.status = "done"
        self.detail = ""
        self.score: float | None = None

    @property
    def elapsed(self) -> float:
        return round((self.finished or time.time()) - self.started, 1)

    def failed(self, detail: str) -> None:
        """Record a task the platform could not measure. Score stays None, because a
        task that did not run is not a task that scored zero."""
        self.status = "failed"
        self.detail = str(detail)


@contextlib.contextmanager
def task_progress(task_id: str, *, round_no: int = 0, index: int = 0, total: int = 0):
    """`task()` when a reporter exists, a no-op holder when it does not.

    Callers are library code (`eval/run/runner.py`) that also runs under unit tests with no
    run and no console attached; making every call site ask "is there a reporter?"
    would be the same branch written twelve times.
    """
    reporter = current()
    if reporter is None:
        yield TaskProgress(round_no=round_no, task_id=task_id, index=index,
                           total=total, started=time.time())
        return
    with reporter.task(task_id, round_no=round_no, index=index, total=total) as held:
        yield held
