"""A dataset that runs in a container: the `exec` environment kind (INTERFACE.md §2.5).

Why this dataset exists
-----------------------
`data/verify_demo.py` proves the *task* contract — environment + verifier — using the
host as the environment. This one proves the *environment* contract, and it exists
because a container path that is never exercised is a container path that does not
work. Every failure mode below is one that would otherwise be discovered by a user.

The four tasks are chosen so that each one is a *test*, not a demonstration:

    e01  a program is broken; fix it, and the image's own interpreter runs the check
    e02  report the running interpreter's version
    e03  a check that the harness is free to edit, and must not be able to
    e04  do nothing

`e02` is the load-bearing one. **The host interpreter here is 3.12 and the image's is
3.11**, so a harness that honestly reports `sys.version_info` writes `3.12` if the
platform quietly ran it on the host instead of in the container. That is the failure
this whole feature can have — an `exec` task that is really a `files` task, reporting
a container curve that was never taken — and no other task here would catch it. It
catches the *platform* rather than the harness, which is the right target: a harness
is free to guess, and the platform is not free to be wrong about where it ran.

`e03` is the other one that matters. §2.5.6 says the check's inputs are re-materialized
after the harness stops, and that guarantee has to hold in the container path too —
where "the harness's process tree" is a `docker exec` that has already returned. The
check is shipped in `SETUP` (so the harness can run it for feedback) *and* re-written
at verification time (so it cannot grade itself), and `test_container.py` pins this by
running a harness that rewrites the check and asserting it still fails.

The image is named as a tag and pinned to a digest by the driver at startup, which is
where §2.5.4 puts that step: the *dataset* names an image that exists, the *record*
carries the digest. Nothing here builds or pulls anything.
"""
from __future__ import annotations

import os

#: The image the tasks and the check run in. **It is built for the platform's own base
#: harness**, not chosen from a catalogue:
#:
#:     python tools/import_env.py --image docker.m.daocloud.io/library/python:3.11-slim \
#:         --harness base_harness/loop --tag harnessgrad-task-loop:demo
#:
#: That coupling is real and is recorded here rather than hidden: `loop` needs the
#: `openai` package, the bare `python` image does not have it, and the harness tree is
#: read-only during a run so it cannot install anything itself. `install` (§2.5.6) is
#: where a harness's dependencies go, and `tools/import_env.py --harness` is what bakes
#: them in. A dataset's image therefore has to satisfy whatever harness will run in it,
#: and the platform has no mechanism yet to reconcile "the dataset names an image" with
#: "the harness declares dependencies" — flagging it as an open design point rather than
#: pretending the demo resolved it.
IMAGE = os.environ.get("HG_EXEC_DEMO_IMAGE", "harnessgrad-task-loop:demo")

#: Overridable, and whatever it resolves to ends up on every curve point as
#: `env.image_digest` — so an overridden value is still reproducible from the record,
#: which is the property that matters.
#:
#: A tag rather than a digest on purpose, and it is not a contradiction of §2.5.4: the
#: driver resolves it before the run starts and **refuses to start** if it cannot, so no
#: `exec` curve can be recorded without a digest on it. Naming a digest here would only
#: move the same failure from "the run says so" to "the import is wrong".

TASKS: list[dict] = [
    {"task_id": "e01", "goal":
        "In your --workdir there is `broken.py` and `test_broken.py`. "
        "`python3 test_broken.py` fails. Fix `broken.py` so that it exits 0. "
        "Do not change `test_broken.py` — it is the specification."},
    {"task_id": "e02", "goal":
        "In your --workdir, write `answer.txt` containing exactly the version of the "
        "interpreter you are running under, as `<major>.<minor>` — for example "
        "`3.11`. Run `python3 -c \"import sys; print('%d.%d' % sys.version_info[:2])\"` "
        "and put what it prints in the file."},
    {"task_id": "e03", "goal":
        "In your --workdir there is `check.py`, which exits 0 only when `out.txt` "
        "exists and contains `correct`. Create `out.txt` so that `python3 check.py` "
        "exits 0. Do not change `check.py` — it is the specification."},
    {"task_id": "e04", "goal":
        "In your --workdir, create `done.txt` containing exactly the word `yes`."},
]

#: The task's initial state. Materialised before the harness runs, platform-side, into
#: the host directory that is bind-mounted at the environment's `workdir` — so the
#: paths below are relative to `/app` inside the container without being written as
#: `/app`, which is the same convention `verify_demo` uses for the host.
SETUP: dict[str, dict] = {
    "e01": {"files": [
        {"path": "broken.py", "content":
            "# Deliberately wrong: `add` subtracts.\n"
            "import sys\n"
            "def add(a, b):\n"
            "    return a - b\n"
            "if __name__ == '__main__':\n"
            "    print(sys.version.split()[0])\n"},
        {"path": "test_broken.py", "content":
            "from broken import add\n"
            "assert add(2, 3) == 5, 'add(2, 3) must be 5'\n"
            "assert add(-1, 1) == 0, 'add(-1, 1) must be 0'\n"
            "print('ok')\n"},
    ]},
    "e02": {"files": []},
    "e03": {"files": [
        {"path": "check.py", "content":
            "import pathlib, sys\n"
            "p = pathlib.Path('out.txt')\n"
            "ok = p.exists() and p.read_text().strip() == 'correct'\n"
            "sys.exit(0 if ok else 1)\n"},
    ]},
    "e04": {"files": []},
}

#: The grade. Platform-side only, and re-materialised immediately before it runs --
#: inside a **fresh** container from the same image, which shares only the task bind
#: mount (see `eval/container.run`).
VERIFY: dict[str, dict] = {
    "e01": {"kind": "command", "argv": ["python3", "test_broken.py"],
            "inputs": [
                {"path": "test_broken.py", "content":
                    "from broken import add\n"
                    "assert add(2, 3) == 5, 'add(2, 3) must be 5'\n"
                    "assert add(-1, 1) == 0, 'add(-1, 1) must be 0'\n"
                    "print('ok')\n"},
            ]},
    # The version the *image* provides, which is not the version the host provides.
    "e02": {"kind": "answer", "expected": "3.11"},
    "e03": {"kind": "command", "argv": ["python3", "check.py"],
            "inputs": [
                {"path": "check.py", "content":
                    "import pathlib, sys\n"
                    "p = pathlib.Path('out.txt')\n"
                    "ok = p.exists() and p.read_text().strip() == 'correct'\n"
                    "sys.exit(0 if ok else 1)\n"},
            ]},
    "e04": {"kind": "command", "argv": [
        "python3", "-c",
        "import pathlib,sys;"
        "p=pathlib.Path('done.txt');"
        "sys.exit(0 if p.exists() and p.read_text().strip()=='yes' else 1)"]},
}

#: Two and two, for the reason `verify_demo` gives: an eval side of one task makes the
#: score move in whole points and the confidence interval undefined.
SPLIT = {"train": ["e01", "e02"], "eval": ["e03", "e04"]}

#: Every task here runs in the container. Declared once rather than per task, which is
#: what `DEFAULT_ENV` is for.
DEFAULT_ENV: dict = {
    "kind": "exec",
    "image": IMAGE,
    "workdir": "/app",
    # No network. The image is on the local daemon and nothing here needs to fetch
    # anything, so the stricter setting is also the honest one.
    "network": "none",
    # What a CLI harness needs (INTERFACE.md §2.5.3). The cost -- the agent's key
    # enters the image -- is stated there and is why `placement` is on every curve
    # point rather than assumed.
    "placement": "inside",
}


def load():
    return TASKS, {"e01": "", "e02": "3.11", "e03": "", "e04": ""}
