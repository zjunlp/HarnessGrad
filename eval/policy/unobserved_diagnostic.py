"""Explicit operator permission to attempt execution, never enforcement evidence.

This capability is platform-side only. It is not read from task metadata, model
output, environment variables or cache entries.
"""
from dataclasses import dataclass

REASON = "policy_enforcement_not_observed"


@dataclass(frozen=True)
class Authorization:
    run_id: str
    task_id: str
    operator_authorization: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (
                self.run_id, self.task_id, self.operator_authorization)):
            raise ValueError("unobserved diagnostic requires run, task and explicit operator authorization")

    def require_scope(self, run_id, task_ids, *, trials=1, jobs=1, round_no=0):
        if (run_id != self.run_id or task_ids != [self.task_id]
                or type(trials) is not int or trials != 1
                or type(jobs) is not int or jobs != 1
                or type(round_no) is not int or round_no != 0):
            raise ValueError("unobserved diagnostic authorization binds one run/task/trial/job at round 0")

    def record(self):
        return dict(stage="diagnostic", run_id=self.run_id, task_id=self.task_id,
                    operator_authorization=self.operator_authorization,
                    policy_enforcement_observed=False, measured=False,
                    production_authorized=False, comparable=False,
                    non_comparable_reason=REASON)


def validate_record(record, *, run_id, selected, stage="diagnostic"):
    if not isinstance(record, dict):
        raise ValueError("unobserved diagnostic record must be an object")
    auth = Authorization(record.get("run_id"), record.get("task_id"),
                         record.get("operator_authorization"))
    auth.require_scope(run_id, selected)
    if (stage != "diagnostic" or record != auth.record() or any(
            record.get(k) is not False for k in (
                "policy_enforcement_observed", "measured", "production_authorized", "comparable"))):
        raise ValueError("unobserved diagnostic cannot claim measurement, comparison or authorization")
