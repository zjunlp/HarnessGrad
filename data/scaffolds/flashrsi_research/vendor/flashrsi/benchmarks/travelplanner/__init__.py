"""Native TravelPlanner sole-planning tasks and official constraint pass rates.

Upstream: OSU-NLP-Group/TravelPlanner at e52c87f4ac348a3410c46dc3553c519db5ec5e23.
"""

import ast
import copy
import hashlib
from dataclasses import replace
from types import MappingProxyType

from flashrsi.benchmarks._native import NativeBenchmark
from flashrsi.benchmarks.travelplanner._prompts import PLANNER_INSTRUCTION

REVISION = "e52c87f4ac348a3410c46dc3553c519db5ec5e23"
METRICS = (
    "final_pass_rate",
    "delivery_rate",
    "commonsense_pass_rate",
    "hard_pass_rate",
)


def _scoring_row(row):
    row = copy.deepcopy(dict(row))
    required = {
        "query",
        "reference_information",
        "org",
        "dest",
        "days",
        "visiting_city_number",
        "date",
        "people_number",
        "local_constraint",
        "budget",
        "level",
    }
    if required - row.keys():
        raise ValueError(
            "TravelPlanner local scoring requires native labeled fields; official test is submission-only (missing: "
            + ", ".join(sorted(required - row.keys()))
            + ")"
        )
    for key in ("query", "reference_information", "org", "dest"):
        if not isinstance(row[key], str) or not row[key].strip():
            raise ValueError(f"TravelPlanner requires a nonempty native {key}")
    for key in ("days", "visiting_city_number", "people_number", "budget"):
        value = row[key]
        if isinstance(value, str):
            value = ast.literal_eval(value)
        if type(value) is not int or value < (0 if key == "budget" else 1):
            raise ValueError(f"TravelPlanner requires a native integer {key}")
        row[key] = value
    for key in ("date", "local_constraint"):
        if isinstance(row[key], str):
            row[key] = ast.literal_eval(row[key])
    if (
        row["days"] not in (3, 5, 7)
        or row["level"] not in ("easy", "medium", "hard")
        or not isinstance(row["date"], list)
        or len(row["date"]) != row["days"]
        or any(not isinstance(v, str) for v in row["date"])
        or not isinstance(row["local_constraint"], dict)
        or {"house rule", "cuisine", "room type", "transportation"}
        - row["local_constraint"].keys()
    ):
        raise ValueError(
            "TravelPlanner requires native dates, level and local_constraint"
        )
    return row


class TravelPlanner(NativeBenchmark):
    name = "travelplanner"
    description = (
        "TravelPlanner sole-planning with native reference information and constraints."
    )
    primary_metric = "final_pass_rate"
    metadata = MappingProxyType(
        {
            "source": "osunlp/TravelPlanner",
            "revision": "8736504ecfc31b7f8b7e40122873c337e83fff7c",
            "scorer_revision": REVISION,
            "native_splits": ("train", "validation", "test"),
            "mode": "sole-planning",
            "execution": "native-harness-local-workspace",
            "scoring": "official-constraint-functions-macro",
            "task_id_scheme": "sha256(query), or explicit native id",
        }
    )

    def __init__(self, config):
        super().__init__(config)
        if self.config.get("mode", "sole-planning") != "sole-planning":
            raise ValueError(
                "TravelPlanner currently supports mode: sole-planning only"
            )
        subsets = self.config.get("source_splits", ["train", "validation"])
        if (
            not isinstance(subsets, list)
            or not subsets
            or any(s not in ("train", "validation", "test") for s in subsets)
            or len(set(subsets)) != len(subsets)
        ):
            raise ValueError(
                "TravelPlanner source_splits must be unique native train/validation/test names"
            )
        self.metadata = MappingProxyType({**self.metadata, "configs": tuple(subsets)})
        self._database_checksum = None

    def _iter_tasks(self):
        for split, raw in self._source_rows():
            row = _scoring_row(raw)
            task_id = row.get("id") or hashlib.sha256(row["query"].encode()).hexdigest()
            yield self._register(
                row,
                split,
                task_id,
                PLANNER_INSTRUCTION.format(
                    text=row["reference_information"], query=row["query"]
                ),
                env="travelplanner",
                days=row["days"],
                level=row["level"],
            )

    def grade(self, trajs):
        graded = []
        for trajectory in trajs:
            self._row_for(trajectory)
            metrics = dict.fromkeys(METRICS, 0.0)
            if trajectory.status == "finished":
                event = trajectory.messages[-1] if trajectory.messages else {}
                if (
                    event.get("type") != "TravelPlannerVerifier"
                    or event.get("task_id") != trajectory.task.task_id
                    or event.get("task_checksum")
                    != trajectory.task.metadata["task_checksum"]
                    or event.get("scorer_revision") != REVISION
                    or not self._database_checksum
                    or event.get("database_checksum") != self._database_checksum
                ):
                    raise ValueError(
                        "TravelPlanner requires a final host verifier event"
                    )
                rates = event.get("metrics", {})
                if (
                    set(rates) != set(METRICS)
                    or any(
                        type(v) not in (int, float) or v not in (0, 1)
                        for v in rates.values()
                    )
                    or rates["final_pass_rate"]
                    != rates["commonsense_pass_rate"] * rates["hard_pass_rate"]
                    or any(rates[k] > rates["delivery_rate"] for k in METRICS)
                ):
                    raise ValueError("Invalid TravelPlanner official macro scores")
                metrics = dict(rates)
            graded.append(
                replace(trajectory, metrics={**trajectory.metrics, **metrics})
            )
        return tuple(graded)


__all__ = ["TravelPlanner"]
