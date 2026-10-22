"""Local-model tool defaults must stay within staged endpoint policy; no requests."""
from eval.policy.diagnostic_policy import ENDPOINTS
from tools.check_local_model import DEFAULT_BASE_URL


def test_local_model_check_default_is_admitted_by_platform_policy():
    assert DEFAULT_BASE_URL in ENDPOINTS
