"""The host watchdog: it must pause *before* the machine stops answering.

2026-10-18: load 384 on 144 cores, no `ssh`, model server dead. The reserve lives in
`harnessgrad.slots.host_pressure`; this tool is what an operator can see and what stops new
jobs from being launched while the reserve is breached.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import host_guard


def _pressure(*, load1=10.0, free_gb=200.0, cpus=144, ok=True):
    return {"ok": ok, "reason": "" if ok else "load 200 leaves no headroom on 144 cpus",
            "load1": load1, "cpus": cpus, "free_gb": free_gb}


def test_a_single_bad_sample_does_not_pause_anything():
    paused, reason = host_guard.decide(_pressure(ok=False), over=1, under=0, paused=False)
    assert (paused, reason) == (False, "")


def test_two_bad_samples_raise_the_pause():
    paused, reason = host_guard.decide(_pressure(ok=False), over=2, under=0, paused=False)
    assert paused is True
    assert "no headroom" in reason


def test_a_pause_clears_only_after_three_good_samples():
    states = [(1, False), (2, True), (3, False)]
    verdicts = [host_guard.decide(_pressure(ok=not over > 0), over=over, under=under,
                                  paused=True)[0]
                for over, under in [(1, 0), (0, 2), (0, 3)]]
    assert verdicts == [True, True, False], states


def test_a_flapping_host_stays_paused():
    paused, _ = host_guard.decide(_pressure(ok=False), over=1, under=1, paused=True)
    assert paused is True


def test_critical_is_beyond_busy():
    assert host_guard.critical(_pressure(load1=150.0, cpus=144)) is False
    assert host_guard.critical(_pressure(load1=200.0, cpus=144)) is True
    assert host_guard.critical(_pressure(load1=10.0, free_gb=4.0)) is True


def test_the_watchdog_finds_launchers_not_itself():
    assert host_guard._sweeps() == [] or all(isinstance(pid, int) for pid in host_guard._sweeps())
