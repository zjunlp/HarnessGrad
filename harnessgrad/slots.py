"""A machine-wide budget for **measurement** concurrency.

The platform already bounds how many tasks one run measures at once (`--jobs`). That bound is
per-run, and it is the wrong unit as soon as more than one run shares one model endpoint:
`--jobs` is a *reservation* each run makes for itself, so a run that is waiting on its
improver gives its reservation back to nobody, and a run that is measuring cannot use the
slots its peers are not using.

**Measured, nine methods on one local endpoint (2026-10-17).** Fit to two points — a lone run
at `jobs=3` took 21 s per task, at `jobs=40` it took 78 s — the endpoint's per-task latency is
`L(N) = (N+10)/40` minutes and its throughput `T(N) = 40N/(N+10)` tasks/minute, where `N` is
**total** in-flight tasks across every run. Because each method's rounds are serial (round
`N+1` needs round `N`'s score), all nine enter their improver phase together and all nine
leave it together, and the GPU sits idle for the whole improver phase. Simulated with the fit
above: nine simultaneous runs at a fixed `--jobs 6` finish in 3.36 h with **100 minutes** of
GPU idle. The same nine under a work-conserving global budget of 40 finish in **2.21 h** with
15-25 minutes idle — because a run whose improver is running holds no slots, and the runs that
are ready to measure take them immediately.

So the unit is a **slot**, the budget is **global**, and a run's `--jobs` becomes a ceiling
rather than a reservation. Admission is work-conserving: a slot is granted to whoever asks
first, and released the moment its task finishes.

**Why file locks rather than a scheduler process.** A lease server needs its own lifecycle,
and a crashed or restarted server has to reason about the leases it handed out. An `flock` on
one file per slot is released by the kernel when the holding process dies, so a killed run
cannot leak capacity, and there is nothing to supervise. The cost is a 50 ms poll while
waiting, which is negligible against a task that takes a minute.

**Not a rate limiter.** The budget does not slow anything down or queue work behind a
schedule; it only stops `N` from exceeding a capacity the endpoint was measured to have. A run
that is alone in the system still gets its `--jobs`.
"""
from __future__ import annotations

import errno
import fcntl
import os
import random
import sys
import time
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DIR = "hg-gpu-slots"
DEFAULT_CAPACITY = 40

#: Host-headroom gate. The capacity above bounds one *endpoint*; it says nothing about the
#: machine the tasks run on, which is a different ceiling. Measured 2026-10-18: 96 slots of
#: ALFWorld -- each task a `bwrap` sandbox plus a TextWorld process plus a relay -- put the
#: 15-minute load at 384 on 144 cores, the host stopped answering and the model server died
#: with it. Login and an editor lost that race, so the machine now keeps a reserve that work
#: may not touch:
#:
#:   * at least `DEFAULT_RESERVED_CORES` (and never less than a tenth of the machine) must be
#:     free, not merely "not saturated", so an SSH session and a shell command have somewhere
#:     to run;
#:   * `DEFAULT_MIN_FREE_MEMORY` of `MemTotal` and `DEFAULT_MIN_FREE_GB` of free memory,
#:     whichever is larger, stay unclaimed -- the box must be able to fork and to page in
#:     a login shell;
#:   * a task that cannot get a slot because of either reserve waits, and after
#:     `DEFAULT_GUARD_TIMEOUT` the run fails loudly instead of hanging forever.
DEFAULT_RESERVED_CORES = 8
DEFAULT_MIN_FREE_MEMORY = 0.12
DEFAULT_MIN_FREE_GB = 16.0
DEFAULT_GUARD_TIMEOUT = 1800.0


def _reserved_cores(cpus: int) -> int:
    """Cores work may not use: the absolute floor, and never less than a tenth of the box."""
    return max(DEFAULT_RESERVED_CORES, round(cpus * 0.1))


def _read_host() -> tuple[float, int, int]:
    """`(load1, MemAvailable, MemTotal)`, bytes for the last two, straight from `/proc`."""
    load1 = float(Path("/proc/loadavg").read_text(encoding="utf-8").split()[0])
    info: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if fields:
            info[key] = int(fields[0]) * 1024
    total = info.get("MemTotal") or 1
    available = info.get("MemAvailable", info.get("MemFree", 0))
    return load1, available, total


def host_pressure(reserved_cores: int | None = None,
                  min_free_memory: float = DEFAULT_MIN_FREE_MEMORY,
                  min_free_gb: float = DEFAULT_MIN_FREE_GB) -> dict:
    """Whether the host can take one more task, and why not when it cannot.

    A knob set to `0` disables that half of the check. Nothing here looks at the GPU: the
    capacity already bounded the endpoint, and the failure this guards against is the host
    answering a human. `reserved_cores=None` uses the machine's own reserve; `0` disables
    the CPU half, which is what an unguarded diagnostic uses.
    """
    load1, available, total = _read_host()
    cpus = os.cpu_count() or 1
    reserve = _reserved_cores(cpus) if reserved_cores is None else reserved_cores
    usable = max(cpus - reserve, 0)
    free_gb = available / 2**30
    free_fraction = available / total
    need_gb = max(total * min_free_memory, min_free_gb * 2**30) if (min_free_memory or
                                                                   min_free_gb) else 0.0
    too_busy = bool(reserve) and load1 > usable
    too_full = bool(need_gb) and available < need_gb
    if too_busy and too_full:
        reason = (f"load {load1:.0f} leaves no headroom on {cpus} cpus (reserve {reserve}) "
                  f"and only {free_gb:.0f} GiB free (reserve {need_gb / 2**30:.0f} GiB)")
    elif too_busy:
        reason = (f"load {load1:.0f} leaves no headroom on {cpus} cpus "
                  f"(reserve {reserve})")
    elif too_full:
        reason = (f"only {free_gb:.0f} GiB of {total / 2**30:.0f} GiB free "
                  f"(reserve {need_gb / 2**30:.0f} GiB)")
    else:
        reason = ""
    return {"ok": not (too_busy or too_full), "reason": reason, "load1": load1,
            "cpus": cpus, "reserved_cores": reserve, "usable_cores": usable,
            "free_gb": free_gb, "total_gb": total / 2**30, "free_fraction": free_fraction,
            "free_reserve_gb": need_gb / 2**30}

#: The process-wide budget. `None` means "no budget", which is exactly the old behaviour:
#: every run measures up to its own `--jobs` and nothing is shared.
_configured: "SlotBudget | None" = None


def _env_float(name: str, default: float) -> float:
    """An operator knob; unset or unparseable keeps the measured default."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class SlotBudgetError(RuntimeError):
    """A configuration that would silently hand out the wrong capacity."""


class SlotBudget:
    """`capacity` locks in `directory`; holding one lock is holding one slot."""

    def __init__(self, directory: os.PathLike | str, capacity: int):
        if capacity < 1:
            raise SlotBudgetError(f"capacity must be >= 1, got {capacity!r}")
        self.directory = Path(directory)
        self.capacity = int(capacity)
        self.reserved_cores = _env_float("HG_SLOT_RESERVED_CORES", -1.0)
        self.reserved_cores = None if self.reserved_cores < 0 else self.reserved_cores
        self.min_free_memory = _env_float("HG_SLOT_MIN_FREE_MEMORY",
                                          DEFAULT_MIN_FREE_MEMORY)
        self.min_free_gb = _env_float("HG_SLOT_MIN_FREE_GB", DEFAULT_MIN_FREE_GB)
        self.guard_timeout = _env_float("HG_SLOT_GUARD_TIMEOUT", DEFAULT_GUARD_TIMEOUT)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._assert_capacity_agrees()

    # -- the capacity is a fact about the endpoint, not about one run -------------------
    def _assert_capacity_agrees(self) -> None:
        """Refuse two runs that think the budget is two different numbers.

        Silently honouring whichever process asked last would make `N` drift without a
        record, and `N` is the whole point of the mechanism.
        """
        stamp = self.directory / "capacity"
        mine = str(self.capacity)
        try:
            fd = os.open(stamp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            recorded = stamp.read_text(encoding="utf-8").strip()
            if recorded and recorded != mine:
                raise SlotBudgetError(
                    f"{self.directory} already has capacity {recorded}, not {mine}; "
                    "one endpoint has one capacity, so give this run the same number or a "
                    "different directory") from None
            return
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(mine + "\n")

    def _slot_path(self, index: int) -> Path:
        return self.directory / f"slot-{index:03d}"

    def _try_acquire(self) -> int | None:
        """One pass over the slots, in a rotated order so nobody always wins slot 0."""
        order = list(range(self.capacity))
        if self.capacity > 1:
            offset = random.randrange(self.capacity)
            order = order[offset:] + order[:offset]
        for index in order:
            path = self._slot_path(index)
            try:
                fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
            except OSError:                      # pragma: no cover - unreadable directory
                continue
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    continue
                raise
            return fd                            # the fd *is* the lease
        return None

    @contextmanager
    def hold(self, *, timeout: float | None = None, poll: float = 0.05):
        """Hold one slot for the duration of the block.

        `timeout=None` waits indefinitely, which is correct for a measurement: the task is
        not optional, it is only waiting its turn. A timeout exists so a caller can fail
        loudly instead of hanging when a budget is misconfigured.

        Host headroom is a **second** gate, checked before the slot: capacity bounds the
        endpoint, and this bounds the machine. A task keeps its slot while it runs, so the
        gate only ever stops work from *starting*, which is what lets a hot host cool down
        instead of accumulating another sandbox every time a slot frees up.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        guard_deadline = time.monotonic() + (self.guard_timeout or 0) if self.guard_timeout \
            else None
        last_note = 0.0
        while True:
            pressure = host_pressure(self.reserved_cores, self.min_free_memory,
                                     self.min_free_gb)
            if not pressure["ok"]:
                if guard_deadline is not None and time.monotonic() >= guard_deadline:
                    raise SlotBudgetError(
                        f"host still has no room for another task after {self.guard_timeout}s: "
                        f"{pressure['reason']}; refusing rather than piling on")
                now = time.monotonic()
                if now - last_note >= 60.0:
                    last_note = now
                    print(f"[slots] waiting for host headroom: {pressure['reason']}",
                          file=sys.stderr, flush=True)
                time.sleep(max(poll, 1.0))
                continue
            fd = self._try_acquire()
            if fd is not None:
                break
            if deadline is not None and time.monotonic() >= deadline:
                raise SlotBudgetError(
                    f"no free slot in {self.directory} (capacity {self.capacity}) "
                    f"within {timeout}s")
            time.sleep(poll)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def configure(capacity: int | None, directory: os.PathLike | str | None = None) -> None:
    """Set the process-wide budget. `capacity=None` clears it (the old behaviour)."""
    global _configured
    if capacity is None:
        _configured = None
        return
    _configured = SlotBudget(directory or os.path.join(_base_dir(), DEFAULT_DIR), capacity)


def _base_dir() -> str:
    return os.environ.get("HG_GPU_BUDGET_DIR") or os.environ.get("TMPDIR") or "/tmp"


def current() -> "SlotBudget | None":
    return _configured


@contextmanager
def slot(timeout: float | None = None):
    """Hold one slot if a budget is configured; a no-op otherwise."""
    budget = _configured
    if budget is None:
        yield
        return
    with budget.hold(timeout=timeout):
        yield
