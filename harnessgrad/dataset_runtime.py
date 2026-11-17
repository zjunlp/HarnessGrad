"""Transitional alias for canonical dataset runtime."""
import sys
from harnessgrad.datasets import runtime as implementation
sys.modules[__name__] = implementation
