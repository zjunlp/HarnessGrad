"""Declared data source, loader and command scorer identity for MATH."""
from pathlib import Path
from data.scaffolds.native_qa_provenance import math_profile


def profile():
    return math_profile(Path(__file__).resolve().parents[3], 'math')
