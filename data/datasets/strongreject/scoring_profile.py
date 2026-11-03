"""Opt in to scoring-identity checks without loading task data or a model."""
import hashlib

from data.datasets import strongreject
from harnessgrad.config import current


def profile():
    selected = strongreject.judge_profile()
    assets = strongreject.ASSETS
    transport = assets.parents[2] / "eval/service"
    endpoint = current().judge.base_url
    endpoint = endpoint.rstrip("/") + "/chat/completions" if endpoint else None
    return {
        "metric": selected["metric"],
        "judge": selected["judge"],
        "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest() if endpoint else None,
        "evaluator_revision": strongreject.SOURCES["evaluator_revision"],
        "task_revision": strongreject.SOURCES["task_revision"],
        "templates_sha256": next(e["sha256"] for e in strongreject.SOURCES["files"]
                                 if e["path"] == "upstream-judge_templates.json"),
        "adapter_sha256": hashlib.sha256(assets.with_suffix(".py").read_bytes()).hexdigest(),
        "scorer_sha256": {name: hashlib.sha256((assets / name).read_bytes()).hexdigest()
                          for name in ("verify/check.py", "lib/scoring.py", "scoring_profile.py",
                                       "specs/sources.json")},
        "transport_sha256": {name: hashlib.sha256((transport / name).read_bytes()).hexdigest()
                             for name in ("judge.py", "judge_request.py", "tool_transport.py")},
    }
