#!/bin/sh
set -eu

# The service process is task-scoped.  The image is assembled from the release for
# convenience, but an agent must see only the task selected by the platform. Remove
# every sibling before importing AppWorld. This
# also means arbitrary action code cannot inspect another task's database through the
# ordinary filesystem.
python - <<'PY'
import os
import re
import shutil
from pathlib import Path

task_id = os.environ.get("HG_ENV_TASK_ID", "")
if not re.fullmatch(r"[A-Za-z0-9]+_[0-9]+", task_id):
    raise SystemExit("HG_ENV_TASK_ID is missing or malformed")
tasks = Path("/appworld/data/tasks")
if not (tasks / task_id).is_dir():
    raise SystemExit("HG_ENV_TASK_ID is not a released task")
for task in tasks.iterdir():
    if task.name != task_id:
        shutil.rmtree(task)
PY

mkdir -p /recordings/appworld/experiments
rm -rf /appworld/experiments
ln -s /recordings/appworld/experiments /appworld/experiments
export APPWORLD_ROOT=/appworld
exec python -m uvicorn appworld_service:app --host 0.0.0.0 --port 8000
