#!/usr/bin/env python3
"""upstream ace policy port; see methods/policy_ports/README.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _siblings

# All proposal calls are routed through the shared editor in policy_ports/runtime.py.
bridge = _siblings.load("policy_ports/runtime")


def main():
    return bridge.run("ace")


if __name__ == "__main__":
    raise SystemExit(main())
