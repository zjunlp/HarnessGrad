"""The slot budget must refuse to *start* work on a host that has no room for it.

Measured 2026-10-18: 96 ALFWorld slots put 15-minute load at 384 on 144 cores, the host
stopped answering and the model server died with it. `ssh` and an editor lost the race, so
the machine keeps a reserve that work may not touch: some cores, and some memory. Capacity
bounds one endpoint; this gate bounds the machine, and it has to act before a task starts,
because a running task cannot give its sandbox back.
"""
from __future__ import annotations

import pytest

from harnessgrad import slots


def _fake_host(monkeypatch, *, load1: float, free_fraction: float, cpus: int = 100):
    total = 100 * 2**30
    monkeypatch.setattr(slots, "_read_host",
                        lambda: (load1, int(total * free_fraction), total))
    monkeypatch.setattr(slots.os, "cpu_count", lambda: cpus)


def test_pressure_reports_an_idle_host_as_usable(monkeypatch):
    _fake_host(monkeypatch, load1=10.0, free_fraction=0.5)
    pressure = slots.host_pressure()
    assert pressure["ok"] is True
    assert pressure["reason"] == ""
    assert pressure["usable_cores"] == 100 - slots._reserved_cores(100)


def test_pressure_refuses_an_overloaded_host(monkeypatch):
    _fake_host(monkeypatch, load1=384.0, free_fraction=0.5)
    pressure = slots.host_pressure()
    assert pressure["ok"] is False
    assert "no headroom" in pressure["reason"]


def test_pressure_refuses_a_host_without_memory(monkeypatch):
    _fake_host(monkeypatch, load1=1.0, free_fraction=0.02)
    pressure = slots.host_pressure()
    assert pressure["ok"] is False
    assert "GiB free" in pressure["reason"]


def test_a_tenth_of_the_machine_is_the_reserve_floor(monkeypatch):
    """A big box reserves more than 8 cores: a tenth, so a login stays schedulable."""
    assert slots._reserved_cores(144) == 14
    assert slots._reserved_cores(32) == 8


def test_work_still_runs_at_48_slots_on_144_cores(monkeypatch):
    """The reserve must not block the workload that was stable all day (48 slots)."""
    _fake_host(monkeypatch, load1=37.0, free_fraction=0.5, cpus=144)
    assert slots.host_pressure()["ok"] is True


def test_pressure_knobs_disable_each_half(monkeypatch):
    _fake_host(monkeypatch, load1=384.0, free_fraction=0.02)
    assert slots.host_pressure(0, 0, 0)["ok"] is True
    assert slots.host_pressure(0, 0.12, 16.0)["ok"] is False
    assert slots.host_pressure(slots._reserved_cores(100), 0, 0)["ok"] is False


def test_hold_waits_for_headroom_and_takes_no_slot_while_hot(tmp_path, monkeypatch):
    budget = slots.SlotBudget(tmp_path / "b", 2)
    budget.guard_timeout = 0            # no guard deadline: wait like a real measurement
    states = [False, False, True]       # hot, hot, then cool
    seen: list[bool] = []

    def pressure(*_args):
        state = states[min(len(seen), len(states) - 1)]
        seen.append(state)
        return {"ok": state, "reason": "" if state else "load 384.0 on 144 cpus"}

    monkeypatch.setattr(slots, "host_pressure", pressure)
    monkeypatch.setattr(slots.time, "sleep", lambda _seconds: None)

    with budget.hold(poll=0.0):
        pass                            # acquired once, after the host cooled
    assert seen == [False, False, True]


def test_hold_refuses_loudly_when_the_host_never_cools(tmp_path, monkeypatch):
    budget = slots.SlotBudget(tmp_path / "b", 2)
    budget.guard_timeout = 0.001
    monkeypatch.setattr(slots, "host_pressure",
                        lambda *_: {"ok": False, "reason": "load 384.0 on 144 cpus"})
    monkeypatch.setattr(slots.time, "sleep", lambda _seconds: None)

    with pytest.raises(slots.SlotBudgetError, match="no room for another task"):
        with budget.hold():
            pass
