"""Opt-in admission for complete method invocations during discounted hours."""
from datetime import datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
import time as clock
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')


def decision(now, timeout_s):
    if now.tzinfo is None:
        raise ValueError('training window requires a timezone-aware clock')
    reserve = float(timeout_s) + 60  # Include subprocess startup/cleanup margin.
    if not math.isfinite(reserve) or not 60 < reserve <= 10 * 3600:
        raise ValueError('method timeout cannot fit in the discounted training window')
    local = now.astimezone(ZONE)
    day = local.date()
    if local.hour >= 22:
        end = datetime.combine(day + timedelta(days=1), time(8), ZONE)
    elif local.hour < 8:
        end = datetime.combine(day, time(8), ZONE)
    else:
        end = None
    allowed = end is not None and local + timedelta(seconds=reserve) <= end
    opening = local if allowed else datetime.combine(day, time(22), ZONE)
    if not allowed and opening <= local:
        opening += timedelta(days=1)
    return {'allowed': allowed, 'checked_local': local.isoformat(),
            'next_open': opening.isoformat(), 'reserved_seconds': reserve,
            'window_end': end.isoformat() if end else None}


def wait_for_admission(timeout_s, receipt, *, now=None, sleep=None):
    """Wait outside the method's timeout; local evaluations in other runs proceed.

    The platform still enforces timeout_s for the launched process tree. Reserving
    that entire interval prevents its nested CLI/API retries crossing the window.
    This function never launches or resumes an experiment itself.
    """
    now = now or (lambda: datetime.now(timezone.utc))
    sleep = sleep or clock.sleep
    receipt = Path(receipt)
    previous = None
    while True:
        status = decision(now(), timeout_s)
        state = (status['allowed'], status['next_open'])
        if state != previous:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps(status, indent=2) + '\n')
            previous = state
        if status['allowed']:
            return status
        sleep(30)
