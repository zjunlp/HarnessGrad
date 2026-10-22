#!/usr/bin/env python3
"""Keep the machine usable for the human, not only for the measurement.

What went wrong
---------------
2026-10-18, 96 ALFWorld slots on this host: 15-minute load 384 on 144 cores, and the box
stopped answering -- no `ssh`, no editor, the model server's engine core died and had to be
restarted by hand. The slot budget already refuses to *start* a task without headroom
(`harnessgrad/slots.py`), but that is a per-acquisition check inside the runs: it cannot see
a host that is already committed, and it cannot tell an operator what is happening.

What this does
--------------
Samples the same signal the budget uses and publishes it, so the pressure is visible before
it becomes an outage:

    guard.json   one line of load / free memory / verdict, rewritten every interval
    pause        present while the host is over the reserve; a sweep must not start a job
    guard.log    transitions only, so `tail -f` is readable after the fact

It sheds nothing by default. `--shed` makes it stop *sweep* processes (the job launchers,
which is where new load comes from) after the host stays critical for several samples, and
never touches a run that is already measuring: killing those throws away work, which is the
operator's call, not a watchdog's.

Exit codes: 0 normally, 2 with `--once --strict` when the host is over its reserve.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harnessgrad import slots                                     # noqa: E402

#: Samples over the reserve before `pause` is raised, and under it before it is cleared.
PAUSE_AFTER = 2
CLEAR_AFTER = 3
#: Samples of *critical* pressure before `--shed` stops the sweeps that are adding work.
SHED_AFTER = 6


def critical(pressure: dict) -> bool:
    """Beyond "wait a moment": the host is heading for an outage, not just busy."""
    load1 = pressure.get("load1") or 0.0
    cpus = pressure.get("cpus") or 1
    free_gb = pressure.get("free_gb") or 0.0
    return load1 > cpus * 1.25 or free_gb < 6.0


def decide(pressure: dict, over: int, under: int, paused: bool) -> tuple[bool, str]:
    """`(should_be_paused, reason)` from the current run of over/under samples."""
    if paused:
        if over > 0:
            return True, pressure.get("reason") or "host is over its reserve"
        if under >= CLEAR_AFTER:
            return False, "host is back under its reserve"
        return True, f"recovering ({under}/{CLEAR_AFTER} clear samples)"
    if over >= PAUSE_AFTER:
        return True, pressure.get("reason") or "host is over its reserve"
    return False, ""


def _sweeps() -> list[int]:
    """PIDs of job launchers (`sweep-*.py`), the processes whose next step adds load.

    Matched by argument vector rather than by pattern, because a `pgrep -f` on this host has
    matched an unrelated 15 KiB command line before.
    """
    found = []
    me = {os.getpid(), os.getppid()}
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) in me:
            continue
        try:
            argv = [x for x in Path(f"/proc/{entry}/cmdline").read_bytes().split(b"\0") if x]
        except OSError:
            continue
        if len(argv) >= 2 and os.path.basename(argv[1]).startswith(b"sweep-") \
                and argv[1].endswith(b".py"):
            found.append(int(entry))
    return sorted(found)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--status-dir", default="/tmp/hg-rsi10-lb")
    parser.add_argument("--once", action="store_true", help="sample, report, exit")
    parser.add_argument("--strict", action="store_true", help="with --once: exit 2 when over")
    parser.add_argument("--shed", action="store_true",
                        help="stop sweep-*.py launchers after sustained critical pressure")
    args = parser.parse_args(argv)

    status = Path(args.status_dir)
    status.mkdir(parents=True, exist_ok=True)
    pause_path = status / "pause"
    log_path = status / "guard.log"
    over = under = critical_run = 0
    paused = pause_path.exists()
    shed_note = ""

    def note(message: str) -> None:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}\n")
        print(message, flush=True)

    while True:
        pressure = slots.host_pressure()
        over = over + 1 if not pressure["ok"] else 0
        under = under + 1 if pressure["ok"] else 0
        is_critical = critical(pressure)
        critical_run = critical_run + 1 if is_critical else 0

        wanted, reason = decide(pressure, over, under, paused)
        if wanted and not paused:
            pause_path.touch()
            note(f"PAUSE {reason}")
        elif not wanted and paused:
            try:
                pause_path.unlink()
            except FileNotFoundError:
                pass
            note(f"CLEAR {reason}")
        paused = wanted

        if args.shed and is_critical and critical_run >= SHED_AFTER:
            victims = _sweeps()
            for pid in victims:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    continue
            if victims:
                shed_note = f"stopped {len(victims)} sweep launcher(s): {victims}"
                note(f"SHED {shed_note}")
                critical_run = 0

        snapshot = {**pressure, "paused": paused, "critical": is_critical,
                    "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if shed_note:
            snapshot["shed"] = shed_note
        (status / "guard.json").write_text(json.dumps(snapshot) + "\n", encoding="utf-8")

        if args.once:
            print(json.dumps(snapshot, indent=1))
            return 2 if (args.strict and (paused or is_critical)) else 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
