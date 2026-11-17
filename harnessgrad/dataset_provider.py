"""Transitional alias for canonical dataset provider."""
import sys
from harnessgrad.datasets import provider as implementation
sys.modules[__name__] = implementation
