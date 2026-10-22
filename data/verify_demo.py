"""A worked example of the task contract in INTERFACE.md §2.4: environment + verifier.

Why this dataset exists
-----------------------
The other two datasets (`demo`, `probe_set`) predate verifiers and declare only
`TASKS` + `SCORABLE`. That path is still supported and still correct, but it can only
express "answer a short question" — the task's environment is an empty directory and
the grade is a string comparison.

These tasks are the ones that contract could not express:

    v01  a program is broken; fix it so its own test passes
    v02  produce a file with specific contents
    v03  the two kinds side by side, so a dataset can mix them
    v04  a task whose grade is a directory's contents, not one file

Two things here are the whole point, and both are easy to get wrong:

1. **`SETUP` is what the harness receives; `VERIFY` is what it must not.** The task
   dict (`TASKS`) is written to `task.json` and handed to the harness verbatim, so
   nothing secret may live there. `data/scaffolds/registry.py` enforces that.
2. **`v01`'s verifier re-writes its own test file.** The harness is free to edit
   anything in its working directory, tests included — so a grade that reads the
   harness's copy of the test is not a grade. Re-materialising `inputs` at
   verification time is what makes the check meaningful, and `test_verifier.py`
   pins it by running a harness that edits the test file and asserting it still fails.

Nothing here needs the network, pytest, or Docker: the checks are `python3` scripts
that exit zero or non-zero, which is the smallest thing that demonstrates the shape.
"""
from __future__ import annotations

TASKS: list[dict] = [
    {"task_id": "v01", "goal":
        "In your --workdir there is `broken.py` and `test_broken.py`. "
        "`python3 test_broken.py` fails. Fix `broken.py` so that it exits 0. "
        "Do not change `test_broken.py` — it is the specification."},
    {"task_id": "v02", "goal":
        "In your --workdir, create `report.txt` containing exactly the line "
        "`harness ok` (no leading or trailing blank lines)."},
    {"task_id": "v03", "goal":
        "In your --workdir, run `python3 seed.py` once and answer with the number "
        "it prints."},
    {"task_id": "v04", "goal":
        "In your --workdir, create a directory `out/` containing exactly two files, "
        "`a.txt` and `b.txt`, whose contents are `alpha` and `beta` respectively."},
]

#: The task's initial state. Materialised before the harness runs, platform-side.
SETUP: dict[str, dict] = {
    "v01": {"files": [
        {"path": "broken.py", "content":
            "# Deliberately wrong: `add` subtracts.\n"
            "def add(a, b):\n"
            "    return a - b\n"},
        # Shipped *and* re-written at verification time. Shipping it is what lets the
        # harness run it for its own feedback; re-writing it is what stops the harness
        # from grading itself.
        {"path": "test_broken.py", "content":
            "from broken import add\n"
            "assert add(2, 3) == 5, 'add(2, 3) must be 5'\n"
            "assert add(-1, 1) == 0, 'add(-1, 1) must be 0'\n"
            "print('ok')\n"},
    ]},
    "v02": {"files": []},
    "v03": {"files": [
        {"path": "seed.py", "content": "print(6 * 7)\n"},
    ]},
    "v04": {"files": []},
}

#: The grade. Platform-side only, and re-materialised immediately before it runs.
VERIFY: dict[str, dict] = {
    "v01": {"kind": "command", "argv": ["python3", "test_broken.py"],
            "inputs": [
                {"path": "test_broken.py", "content":
                    "from broken import add\n"
                    "assert add(2, 3) == 5, 'add(2, 3) must be 5'\n"
                    "assert add(-1, 1) == 0, 'add(-1, 1) must be 0'\n"
                    "print('ok')\n"},
            ]},
    "v02": {"kind": "command", "argv": [
        "python3", "-c",
        "import pathlib,sys;"
        "p=pathlib.Path('report.txt');"
        "sys.exit(0 if p.exists() and p.read_text().strip()=='harness ok' else 1)"]},
    # The two kinds side by side: a dataset may mix them freely.
    "v03": {"kind": "answer", "expected": "42"},
    "v04": {"kind": "command", "argv": [
        "python3", "-c",
        "import pathlib,sys;"
        "o=pathlib.Path('out');"
        "ok=(sorted(p.name for p in o.iterdir())==['a.txt','b.txt']"
        " and o.joinpath('a.txt').read_text().strip()=='alpha'"
        " and o.joinpath('b.txt').read_text().strip()=='beta')"
        " if o.is_dir() else False;"
        "sys.exit(0 if ok else 1)"]},
}

#: Two and two. Not a statistical split -- this is a worked example, not a
#: benchmark -- but an eval side of one task makes the score move in whole
#: points and the confidence interval undefined, which reads as a defect.
SPLIT = {"train": ["v01", "v02"], "eval": ["v03", "v04"]}


def load():
    return TASKS, {"v01": "", "v02": "", "v03": "42", "v04": ""}
