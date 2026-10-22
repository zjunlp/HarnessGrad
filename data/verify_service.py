"""A dataset whose tasks run against a service: the `exec` + `services` contract.

INTERFACE.md §2.5.7. `verify_exec` proves a task can run *in* a container; this one
proves a task can run *against something*, which is the part that changes what the
platform has to do while the harness runs.

What each task is testing
-------------------------
    s01  the harness must call a service at all
    s02  the harness must call it with the right thing
    s03  the check must be able to ask the service what it was sent
    s04  do nothing

`s03` is the one that justifies the whole feature. Its grade is not read off the
filesystem — it is read out of the service's **recording**, which the harness cannot
see and therefore cannot write. A `files` task cannot express that: the only evidence
available to a check would be something the harness made, and something the harness
made is something the harness can make say anything.

`s02` exists because `s01` alone is too weak to be interesting: any HTTP request passes
it. Together they distinguish "called the service" from "called it correctly", and the
gap between those two is what makes the recording worth having.

The service itself is `tools/fake_service.py`, in the image by way of
`tools/import_env.py --dockerfile`. It is deliberately tiny and has no dependencies
beyond the standard library, so the environment is a stock `python` image plus one
file — which is also what makes the demo cheap enough to run in a test.
"""
from __future__ import annotations

import os

#: The image the tasks and the check run in. A tag, pinned to a digest by the driver at
#: startup, which refuses to start if it cannot be resolved — see `data/verify_exec.py`
#: for why a tag here is not a contradiction of §2.5.4.
IMAGE = os.environ.get("HG_EXEC_DEMO_IMAGE", "harnessgrad-task-loop:demo")

#: The service image, built once by `tools/import_env.py --dockerfile` and named by the
#: tag that build produced. Same rule, same place.
SERVICE_IMAGE = os.environ.get("HG_SERVICE_DEMO_IMAGE", "harnessgrad-fake-service:demo")

TASKS: list[dict] = [
    {"task_id": "s01", "goal":
        "A mock order service is running at $HG_SVC_ORDERS_URL. Send it any HTTP "
        "request. You can use `python3 -c` with urllib; no third-party packages are "
        "installed. Write nothing to disk unless you want to."},
    {"task_id": "s02", "goal":
        "A mock order service is running at $HG_SVC_ORDERS_URL. Place an order for "
        "exactly 3 widgets by POSTing JSON `{\"item\": \"widget\", \"qty\": 3}` to "
        "`$HG_SVC_ORDERS_URL/order`. `python3` and `urllib` are available."},
    {"task_id": "s03", "goal":
        "A mock order service is running at $HG_SVC_ORDERS_URL. The service records "
        "every request it receives. Place an order for exactly 2 gadgets — POST JSON "
        "`{\"item\": \"gadget\", \"qty\": 2}` to `$HG_SVC_ORDERS_URL/order` — and then "
        "record how many orders the service has taken, as a bare integer, in "
        "`order_count.txt` in your --workdir."},
    {"task_id": "s04", "goal":
        "Write `ready.txt` containing exactly `yes` in your --workdir."},
]

#: Nothing is pre-created: every task here is about reaching a service, and giving the
#: harness a starting state would make the task about editing a file instead.
SETUP: dict[str, dict] = {tid: {"files": []} for tid in ("s01", "s02", "s03", "s04")}

#: The grade. `s03`'s check asks the *service* — through the recording, which is mounted
#: read-only here and not at all into the harness.
VERIFY: dict[str, dict] = {
    "s01": {"kind": "command", "argv": [
        # Reads the recording rather than the filesystem: "did the harness call the
        # service" is a question only the service can answer.
        #
        # It asserts on `received`, **not** on a request count. The first version
        # counted requests, and the platform's own health check satisfied it -- so it
        # passed for a harness that never made a call, which is a check that cannot
        # fail. The health check is excluded from `received` for exactly this reason
        # (`tools/fake_service.py`).
        "python3", "-c",
        "import json,pathlib,sys;"
        "p=pathlib.Path('/recordings/orders/harnessgrad.json');"
        "d=json.loads(p.read_text()) if p.exists() else {};"
        "ok=any(r.get('path')=='/orders' for r in d.get('received',[]));"
        "sys.exit(0 if ok else 1)"]},
    "s02": {"kind": "command", "argv": [
        "python3", "-c",
        "import json,pathlib,sys;"
        "p=pathlib.Path('/recordings/orders/harnessgrad.json');"
        "d=json.loads(p.read_text()) if p.exists() else {};"
        "ok=any(r.get('path')=='/order' and r.get('body')=={'item':'widget','qty':3}"
        " for r in d.get('received',[]));"
        "sys.exit(0 if ok else 1)"]},
    # The harness reports a count; the check compares it against the service's own
    # tally. A harness that lies about the count fails, and one that never called the
    # service has nothing to count.
    "s03": {"kind": "command", "argv": [
        "python3", "-c",
        "import json,pathlib,sys;"
        "rec=pathlib.Path('/recordings/orders/harnessgrad.json');"
        "d=json.loads(rec.read_text()) if rec.exists() else {};"
        "truth=d.get('order_count');"
        "p=pathlib.Path('order_count.txt');"
        "said=int(p.read_text().strip()) if p.exists() and p.read_text().strip().isdigit() else None;"
        "sys.exit(0 if truth is not None and said==truth else 1)"]},
    "s04": {"kind": "command", "argv": [
        "python3", "-c",
        "import pathlib,sys;"
        "p=pathlib.Path('ready.txt');"
        "sys.exit(0 if p.exists() and p.read_text().strip()=='yes' else 1)"]},
}

SPLIT = {"train": ["s01", "s02"], "eval": ["s03", "s04"]}

DEFAULT_ENV: dict = {
    "kind": "exec",
    "image": IMAGE,
    "workdir": "/app",
    # Not `bridge`: the point of `services` is reachable-and-not-on-the-internet, and
    # the demo has no reason to want the internet.
    "network": "services",
    "placement": "inside",
    "services": [
        {"name": "orders",
         "image": SERVICE_IMAGE,
         "port": 8080,
         # Required, not optional (§2.5.7): a port that accepts a connection is not a
         # service that is ready, and `tools/fake_service.py` answers this only once it
         # is actually serving.
         "health": {"argv": ["python3", "-c",
                             "import urllib.request as u;"
                             "u.urlopen('http://localhost:8080/health', timeout=2).read()"],
                    "timeout_s": 60},
         # The recording is what makes `s01`-`s03` gradeable without trusting the
         # harness, and it is the only input in this platform the harness cannot read.
         "record": True,
         # Deterministic: the demo's service answers the same way every run, so no seed
         # is needed and `env.seed` stays null -- which is itself a fact worth recording
         # (§2.5.4).
         "needs_seed": False,
         "needs_model": False},
    ],
}


def load():
    return TASKS, {"s01": "", "s02": "", "s03": "", "s04": ""}
