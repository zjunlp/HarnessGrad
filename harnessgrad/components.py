"""Transitional alias for canonical component declarations."""
import sys
from harnessgrad.core import components as implementation
sys.modules[__name__] = implementation
