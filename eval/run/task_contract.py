"""Adapter-declared, public runtime instructions shared by all harnesses."""
from __future__ import annotations


def runtime_task(task: dict, setup: dict | None) -> dict:
    """Render the tool contract into the existing goal without mutating the task.

    Only this explicitly public field crosses the setup boundary. Fixture source
    paths and verifier metadata remain platform-owned. A harness that reads only
    ``goal`` receives the instructions without needing an adapter-specific parser.
    """
    if not setup or "runtime_prompt" not in setup:
        return task
    prompt = setup["runtime_prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("SETUP.runtime_prompt must be a nonempty string")
    return {**task, "goal": str(task.get("goal") or "")
            + "\n\nRuntime tool instructions:\n" + prompt}
