"""WorkBench Revisited: native tool actions scored by their database outcomes.

Upstream: olly-styles/WorkBench at 49c7dfd00c03d384ec59ea57374f50b766aa5613.
"""

import ast
import csv
import hashlib
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from flashrsi.benchmarks._git import _download_git
from flashrsi.benchmarks._native import NativeBenchmark

REVISION = "49c7dfd00c03d384ec59ea57374f50b766aa5613"
DOMAINS = (
    "analytics",
    "calendar",
    "customer_relationship_manager",
    "email",
    "multi_domain",
    "project_management",
)
DATABASE_FILES = (
    "processed/analytics_data.csv",
    "processed/calendar_events.csv",
    "processed/customer_relationship_manager_data.csv",
    "processed/emails.csv",
    "processed/project_tasks.csv",
    "raw/email_addresses.csv",
)
METRICS = ("correct", "exact_match", "unwanted_side_effects")


def _upstream():
    return _download_git(
        "https://github.com/olly-styles/WorkBench.git",
        REVISION,
        ["src", "data/processed", "data/raw"],
    )


class WorkBench(NativeBenchmark):
    name = "workbench"
    description = "WorkBench workplace tools with native outcome-based evaluation."
    primary_metric = "correct"
    metadata = MappingProxyType(
        {
            "source": "https://github.com/olly-styles/WorkBench",
            "revision": REVISION,
            "ground_truth_version": "v2",
            "execution": "native-harness-local-tools",
            "scoring": "native-database-state",
            "native_splits": ("unassigned",),
        }
    )

    def _iter_tasks(self):
        self._upstream_root = (
            Path(self.config["_prepared_root"])
            if self.config.get("_prepared_root")
            else _upstream()
        )
        # A local override is the complete native data/ directory, not rewritten
        # question/answer records. Tools and grader read the same initial world.
        self._data_root = Path(
            self.config.get("data_path") or self._upstream_root / "data"
        )
        domains = self.config.get("domains", list(DOMAINS))
        if (
            not isinstance(domains, list)
            or not domains
            or len(set(domains)) != len(domains)
            or any(d not in DOMAINS for d in domains)
        ):
            raise ValueError("WorkBench domains must be unique native domain names")
        files = [
            *DATABASE_FILES,
            *(
                f"processed/tasks_and_outcomes/{d}_tasks_and_outcomes.csv"
                for d in domains
            ),
        ]
        digest = hashlib.sha256()
        self._file_checksums = {}
        for name in sorted(files):
            path = self._data_root / name
            if not path.is_file():
                raise ValueError(f"WorkBench requires native data/{name}")
            digest.update(name.encode())
            checksum = hashlib.sha256(path.read_bytes())
            self._file_checksums[name] = checksum.hexdigest()
            digest.update(checksum.digest())
        self._data_checksum = digest.hexdigest()
        self.metadata = MappingProxyType(
            {
                **self.metadata,
                "data_path": str(self._data_root),
                "data_checksum": self._data_checksum,
                "data_mode": "local-native"
                if self.config.get("data_path")
                else "pinned-upstream",
                "domains": tuple(domains),
            }
        )
        for domain in domains:
            path = (
                self._data_root
                / f"processed/tasks_and_outcomes/{domain}_tasks_and_outcomes.csv"
            )
            with path.open(encoding="utf-8", newline="") as stream:
                for row in csv.DictReader(stream):
                    actions = ast.literal_eval(row["outcome"])
                    if not isinstance(actions, list) or any(
                        not isinstance(a, str) for a in actions
                    ):
                        raise ValueError(
                            "WorkBench requires native outcome action lists"
                        )
                    yield self._register(
                        row,
                        "unassigned",
                        hashlib.sha256(row["task"].encode()).hexdigest(),
                        row["task"],
                        env=self.name,
                        domain=domain,
                        data_checksum=self._data_checksum,
                    )

    def grade(self, trajs):
        graded = []
        for trajectory in trajs:
            self._row_for(trajectory)
            event = trajectory.messages[-1] if trajectory.messages else {}
            metrics = dict.fromkeys(METRICS, 0.0)
            if trajectory.status == "finished":
                if (
                    event.get("type") != "WorkBenchVerifier"
                    or event.get("task_id") != trajectory.task.task_id
                    or event.get("task_checksum")
                    != trajectory.task.metadata["task_checksum"]
                    or event.get("data_checksum") != self._data_checksum
                    or event.get("revision") != REVISION
                    or event.get("ground_truth_version") != "v2"
                ):
                    raise ValueError(
                        "WorkBench requires a final native host verifier event"
                    )
                metrics = event.get("metrics", {})
                if (
                    set(metrics) != set(METRICS)
                    or any(
                        type(v) not in (int, float) or v not in (0, 1)
                        for v in metrics.values()
                    )
                    or metrics["correct"] + metrics["unwanted_side_effects"] > 1
                ):
                    raise ValueError("Invalid WorkBench native metrics")
            graded.append(
                replace(trajectory, metrics={**trajectory.metrics, **metrics})
            )
        return tuple(graded)
