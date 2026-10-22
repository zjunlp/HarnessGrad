"""A dataset whose environment cannot work, for testing that it is refused.

This is not an example. It exists so that the two refusals §2.5.7 requires can be
exercised **end to end** rather than asserted about in the abstract, in the same spirit
as `data/leaky.py`:

* a service that declares `needs_model` when no environment-side model is configured
  (`HG_ENV_MODEL`) must stop the run at the door, because a simulator with no model
  produces a task that fails for a reason nothing records;
* with a model configured, the service's health check still never passes, so **no task
  produces a scoreable result** and the run must refuse rather than write a curve point.

That second one is the reason this file exists. `mean([])` is `0.0`, so a run whose
tasks were all unmeasurable would otherwise record a score of zero -- which says "the
harness scored nothing" about a run in which the harness never ran. Getting that wrong
is silent, produces a plausible number, and is exactly the class of claim this platform
exists to stop.
"""


from __future__ import annotations


#: See `data/scaffolds/registry.py:KIND_DATASET`. A fixture is platform test material and is
#: deliberately not offered as a choice in the Launch form.
KIND = "fixture"

TASKS: list[dict] = [
    {"task_id": "b01", "goal": "This task can never be measured. See the module docstring."},
    {"task_id": "b02", "goal": "This task can never be measured either."},
]

SETUP: dict[str, dict] = {t["task_id"]: {"files": []} for t in TASKS}

VERIFY: dict[str, dict] = {
    t["task_id"]: {"kind": "answer", "expected": "never checked"} for t in TASKS}

SPLIT = {"train": ["b01"], "eval": ["b02"]}

DEFAULT_ENV: dict = {
    "kind": "exec",
    # Any image will do: the run never reaches it, and naming the demo one keeps this
    # dataset importable on a machine that has that image.
    "image": "harnessgrad-task-loop:demo",
    "workdir": "/app",
    "network": "services",
    "placement": "inside",
    "services": [
        {"name": "never-ready",
         "image": "harnessgrad-fake-service:demo",
         "port": 8080,
         # `false` never exits zero, so this service is never healthy however long the
         # platform waits. A 1-second budget keeps the test quick; the refusal is the
         # same one a real service that crashed on startup would produce.
         "health": {"argv": ["false"], "timeout_s": 1},
         "record": False,
         # Declared so that the *other* refusal can be tested from the same file.
         "needs_seed": False,
         "needs_model": True},
    ],
}


def load():
    return TASKS, {t["task_id"]: "" for t in TASKS}
