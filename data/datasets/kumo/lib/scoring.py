"""Reconstruct the official evaluator's tuple-keyed rules from prepared JSON."""
from engine import Environment

METRIC = "kumo.official.consistency_accuracy.v1"
PROTOCOL = "kumo.query50.v1"


def environment(case):
    mapping = {action: {"type": rule["type"],
                        "states": {(tuple(state) if isinstance(state, list) else state): excluded
                                   for state, excluded in rule["states"]}}
               for action, rule in case["rules"].items()}
    return Environment(case["truths"], case["actions"], mapping, case["observations"], case["seed"])
