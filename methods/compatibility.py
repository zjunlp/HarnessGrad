"""Method-side compatibility import for callers that use ``methods`` directly."""
from harnessgrad.runtime.compat import LegacyCompatibilityWrapper, LegacyRun, run_legacy

__all__ = ["LegacyCompatibilityWrapper", "LegacyRun", "run_legacy"]
