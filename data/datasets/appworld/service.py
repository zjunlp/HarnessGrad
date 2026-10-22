"""Task-bound AppWorld service.

The upstream environment server is intentionally not exposed: its initialize endpoint
accepts ``load_ground_truth`` and ``ground_truth_mode`` and it also exposes evaluation,
task, state, and log-management endpoints.  This wrapper accepts only the action surface
needed by a harness and fixes the task to ``HG_ENV_TASK_ID`` supplied by the platform.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from munch import unmunchify
from appworld import AppWorld

TASK_ID = os.environ.get("HG_ENV_TASK_ID", "")
if not TASK_ID:
    raise RuntimeError("HG_ENV_TASK_ID is required")

app = FastAPI(title="AppWorld task action service")
world: AppWorld | None = None


def _check(task_id: str) -> None:
    if task_id != TASK_ID:
        raise HTTPException(status_code=403, detail="task id is not this service's task")
    if world is None:
        raise HTTPException(status_code=409, detail="task is not initialized")


@app.get("/")
async def health() -> dict[str, str]:
    return {"status": "ok", "task_id": TASK_ID}


@app.post("/initialize")
async def initialize(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    global world
    task_id = payload.get("task_id")
    if task_id != TASK_ID:
        raise HTTPException(status_code=403, detail="task id is not this service's task")
    # Ground truth is never loaded in the agent-facing process.  The verifier creates a
    # separate AppWorld with the official evaluator after the recording is complete.
    # The client cannot choose an output path.  AppWorld joins this value to its
    # recording root, so accepting an absolute or traversal name would let action code
    # redirect logs and state outside the platform recording mount.
    world = AppWorld(task_id=TASK_ID, experiment_name="harness",
                     load_ground_truth=False, ground_truth_mode="minimal",
                     raise_on_failure=False)
    task = world.task
    supervisor = task.supervisor
    return {"output": {"task_id": task.id, "instruction": task.instruction,
                        "supervisor": {k: supervisor[k] for k in
                                       ("first_name", "last_name", "email", "phone_number")},
                        "datetime": task.datetime}}


@app.post("/execute")
async def execute(task_id: str = Body(...), code: str = Body(...)) -> dict[str, Any]:
    _check(task_id)
    return {"output": world.execute(code)}


@app.get("/api_docs")
async def api_docs() -> dict[str, Any]:
    if world is None:
        raise HTTPException(status_code=409, detail="task is not initialized")
    return unmunchify(world.task.api_docs.copy())
