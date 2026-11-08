"""The outcome classifiers have one owner across runner import paths."""
import eval.runner as legacy_runner
import eval.run.runner as runner
from eval.run import outcomes


def test_runner_paths_share_the_canonical_classifiers():
    assert legacy_runner is runner
    for name in ("_PROVIDER_FAILURES", "_provider_failure", "_stopped_by_signals",
                 "_invalid", "timeout_verdict", "_spread", "_harness_failure"):
        assert getattr(runner, name) is getattr(outcomes, name)


def test_signal_classifier_uses_its_canonical_observation_port(monkeypatch):
    observed = []

    def classify(code, stderr, *, wrapper):
        observed.append((code, stderr, wrapper))
        return "stopped_by_signal"

    monkeypatch.setattr(outcomes, "process_outcome", classify)
    reason = runner._stopped_by_signals([
        {"exit_code": -15, "stderr": "terminated", "sandbox": True}])
    assert observed == [(-15, "terminated", True)]
    assert reason["outcome"] == "stopped_by_signal"
