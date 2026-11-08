"""Own only newly allocated task/runtime/stepwise directories and public task files."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile


class TaskWorkspace:
    def __init__(self):
        self.path = Path(tempfile.mkdtemp(prefix='hg-task-'))
        self.runtime_dir = None
        self.stepwise_dir = None

    def responses_directory(self, task_mount, *, image):
        (self.path / '.responses').mkdir()
        return str(Path(task_mount) / '.responses' if image else self.path / '.responses')

    def stage_runtime(self, declaration, *, stage):
        self.runtime_dir = Path(tempfile.mkdtemp(prefix='hg-agent-runtime-'))
        return stage(declaration, self.runtime_dir)

    def materialize(self, files, *, platform, materialize):
        materialize(self.path, files, platform)

    def write_public_task(self, task):
        path = self.path / 'task.json'
        path.write_text(json.dumps(task))
        return path

    def ensure_state(self, path):
        # A repo checkpoint's .state is retained state, NOT an owned temp dir.
        path.mkdir(exist_ok=True)

    def home(self):
        home = self.path / '.harnessgrad-home'
        (home / 'tmp').mkdir(parents=True, exist_ok=True)
        return home

    def allocate_stepwise(self):
        self.stepwise_dir = Path(tempfile.mkdtemp(prefix='hg-stepwise-'))
        return self.stepwise_dir

    def remove_stepwise(self):
        if self.stepwise_dir is not None:
            shutil.rmtree(self.stepwise_dir, ignore_errors=True)

    def remove_task(self):
        shutil.rmtree(self.path, ignore_errors=True)

    def remove_runtime(self):
        if self.runtime_dir is not None:
            shutil.rmtree(self.runtime_dir, ignore_errors=True)
